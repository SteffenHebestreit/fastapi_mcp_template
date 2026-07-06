"""In-process background job manager for long-running extractions.

Submitting a job returns immediately with a job id; the work runs in an asyncio
task bounded by a concurrency semaphore. Clients poll for status/result.

Limitations (documented, by design):
  * Jobs live in memory and do NOT survive a restart or a second worker process.
    For durable/multi-worker deployments, back this with a real queue (Redis/RQ,
    Celery, arq, ...). The public API here is intentionally small so that swap is
    localized.
"""

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Job:
    __slots__ = ("id", "tool", "status", "submitted_at", "started_at", "finished_at", "result", "error")

    def __init__(self, job_id: str, tool: str):
        self.id = job_id
        self.tool = tool
        self.status = "pending"  # pending | running | completed | failed
        self.submitted_at = _now()
        self.started_at: Optional[datetime] = None
        self.finished_at: Optional[datetime] = None
        self.result: Optional[Any] = None
        self.error: Optional[str] = None

    def to_dict(self, include_result: bool = True) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "id": self.id,
            "tool": self.tool,
            "status": self.status,
            "submitted_at": self.submitted_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }
        if include_result:
            data["result"] = self.result
            data["error"] = self.error
        return data


class JobManager:
    """Bounded in-memory job store with a concurrency limit and TTL pruning."""

    def __init__(self, max_concurrent: int = 4, max_entries: int = 1000, ttl_seconds: int = 3600):
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self._jobs: Dict[str, Job] = {}
        self._sem = asyncio.Semaphore(max(1, max_concurrent))
        self._tasks: set = set()

    def configure(self, max_concurrent: int = None, max_entries: int = None, ttl_seconds: int = None) -> None:
        """Apply settings at startup (before any jobs are submitted)."""
        if max_entries is not None:
            self.max_entries = max_entries
        if ttl_seconds is not None:
            self.ttl_seconds = ttl_seconds
        if max_concurrent is not None:
            self._sem = asyncio.Semaphore(max(1, max_concurrent))

    def submit(self, tool: str, coro_factory: Callable[[], Awaitable[Any]]) -> Job:
        """Register a job and schedule it. Returns the Job immediately (status=pending)."""
        self._prune()
        job = Job(str(uuid.uuid4()), tool)
        self._jobs[job.id] = job
        task = asyncio.create_task(self._run(job, coro_factory))
        # Keep a strong reference so the task isn't garbage-collected mid-flight.
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    async def _run(self, job: Job, coro_factory: Callable[[], Awaitable[Any]]) -> None:
        async with self._sem:
            job.status = "running"
            job.started_at = _now()
            try:
                job.result = await coro_factory()
                job.status = "completed"
            except Exception as e:  # noqa: BLE001
                job.error = str(e)
                job.status = "failed"
            finally:
                job.finished_at = _now()

    def get(self, job_id: str) -> Optional[Job]:
        self._prune()
        return self._jobs.get(job_id)

    def list(self) -> List[Dict[str, Any]]:
        self._prune()
        return [j.to_dict(include_result=False) for j in sorted(
            self._jobs.values(), key=lambda j: j.submitted_at, reverse=True
        )]

    def _prune(self) -> None:
        """Drop finished jobs older than the TTL, and bound total size."""
        now = _now()
        for jid in [
            jid for jid, j in self._jobs.items()
            if j.finished_at and (now - j.finished_at).total_seconds() > self.ttl_seconds
        ]:
            self._jobs.pop(jid, None)
        if len(self._jobs) > self.max_entries:
            # Evict oldest finished jobs first, then oldest overall.
            evictable = sorted(
                self._jobs.values(),
                key=lambda j: (j.finished_at is None, j.submitted_at),
            )
            for j in evictable[: len(self._jobs) - self.max_entries]:
                self._jobs.pop(j.id, None)
