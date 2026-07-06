import json
import uuid
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional, Tuple

from fastapi import HTTPException, UploadFile, File, Form, Request, Header
from fastapi.responses import JSONResponse

from fastapi_mcp_template.core.tool_manager import ToolManager
from fastapi_mcp_template.core.jobs import JobManager
from fastapi_mcp_template.config import get_settings

logger = logging.getLogger(__name__)

# In-process session store. For multi-worker/production deployments, back this
# with Redis or similar so sessions survive restarts and are shared across workers.
active_sessions: Dict[str, Dict[str, Any]] = {}
SESSION_TTL = timedelta(hours=1)
MAX_SESSIONS = 10_000

# MCP protocol versions this server can speak. The client's requested version is
# echoed back when supported; otherwise the default is returned.
SUPPORTED_PROTOCOL_VERSIONS = {"2024-11-05", "2025-03-26"}
DEFAULT_PROTOCOL_VERSION = "2024-11-05"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _prune_sessions() -> None:
    """Drop expired sessions and bound the store size."""
    cutoff = _now() - SESSION_TTL
    expired = [sid for sid, s in active_sessions.items() if s.get("created", cutoff) < cutoff]
    for sid in expired:
        active_sessions.pop(sid, None)
    if len(active_sessions) > MAX_SESSIONS:
        # Evict oldest first.
        for sid, _ in sorted(active_sessions.items(), key=lambda kv: kv[1].get("created", _now()))[
            : len(active_sessions) - MAX_SESSIONS
        ]:
            active_sessions.pop(sid, None)


def _check_api_key(provided: Optional[str]) -> None:
    """Enforce the API key when one is configured. No-op in dev (key unset)."""
    configured = get_settings().api_key
    if configured and provided != configured:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def create_dynamic_routes(app, tool_manager: ToolManager, job_manager: JobManager = None) -> None:
    """Create dynamic routes for all registered tools."""

    def get_or_create_session(request: Request, method: str = None) -> Tuple[Optional[str], bool]:
        """Get or create a session ID for the client."""
        _prune_sessions()
        if method == "initialize":
            session_id = str(uuid.uuid4())
            active_sessions[session_id] = {
                "created": _now(),
                "initialized": False,
                "protocol_version": "2024-11-05",
            }
            return session_id, True

        session_id = request.headers.get("mcp-session-id")
        if not session_id:
            return None, False
        if session_id not in active_sessions:
            return session_id, False
        return session_id, True

    # ------------------------------------------------------------- MCP endpoint

    async def _dispatch(message: Dict[str, Any], request: Request):
        """Handle one JSON-RPC message. Returns (response_dict_or_None, session_id).

        A None response indicates a notification (no reply is emitted).
        """
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or "method" not in message:
            return _err_obj(message.get("id") if isinstance(message, dict) else None,
                            -32600, "Invalid Request"), None

        method = message.get("method")
        params = message.get("params") or {}
        request_id = message.get("id")

        # Auth (when configured) applies to everything except the initial handshake.
        if method not in ("initialize", "notifications/initialized"):
            if get_settings().api_key and request.headers.get("x-api-key") != get_settings().api_key:
                return _err_obj(request_id, -32001, "Unauthorized"), None

        session_id, session_exists = get_or_create_session(request, method)

        if method == "initialize":
            requested = params.get("protocolVersion")
            negotiated = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else DEFAULT_PROTOCOL_VERSION
            active_sessions[session_id]["initialized"] = True
            active_sessions[session_id]["protocol_version"] = negotiated
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": negotiated,
                    # listChanged is false: without a server->client stream we cannot
                    # push notifications, so advertising it would be dishonest.
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {
                        "name": get_settings().mcp_server_name,
                        "version": get_settings().mcp_server_version,
                    },
                },
            }, session_id

        if method and method.startswith("notifications/"):
            if session_id and session_id in active_sessions:
                active_sessions[session_id]["initialized"] = True
            return None, session_id  # notifications get no response

        if not session_id or not session_exists:
            return _err_obj(request_id, -32002, "Server not initialized - session not found"), session_id
        if not active_sessions.get(session_id, {}).get("initialized", False):
            return _err_obj(request_id, -32002, "Server not initialized"), session_id

        if method == "tools/list":
            tools = []
            for info in tool_manager.registered_tools.values():
                schema = info["instance"].get_schema()
                tools.append({
                    "name": info["definition"].name,
                    "description": info["definition"].description,
                    "inputSchema": schema.to_dict() if hasattr(schema, "to_dict") else schema,
                })
            return _res_obj(request_id, {"tools": tools}), session_id

        if method == "tools/call":
            tool_name = params.get("name")
            arguments = params.get("arguments", {})
            try:
                result = await tool_manager.execute_tool(tool_name, **arguments)
                text = result if isinstance(result, str) else json.dumps(result, default=str)
                return _res_obj(request_id, {"content": [{"type": "text", "text": text}]}), session_id
            except Exception as e:  # noqa: BLE001 - report as MCP tool error, not JSON-RPC error
                logger.warning("Tool '%s' execution failed: %s", tool_name, e)
                return _res_obj(
                    request_id,
                    {"content": [{"type": "text", "text": f"Tool execution failed: {e}"}], "isError": True},
                ), session_id

        if method == "ping":
            return _res_obj(request_id, {}), session_id

        return _err_obj(request_id, -32601, f"Method not found: {method}"), session_id

    async def _handle_mcp(request: Request):
        """Parse the body (single object or JSON-RPC batch array) and dispatch."""
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return _jsonrpc_error(None, -32700, "Parse error")

        try:
            if isinstance(body, list):
                if not body:
                    return _jsonrpc_error(None, -32600, "Invalid Request")
                responses = []
                session_id = ""
                for msg in body:
                    resp, sid = await _dispatch(msg, request)
                    session_id = sid or session_id
                    if resp is not None:
                        responses.append(resp)
                if not responses:  # all notifications
                    return JSONResponse(content=None, status_code=202)
                headers = {"content-type": "application/json"}
                if session_id:
                    headers["Mcp-Session-Id"] = session_id
                return JSONResponse(content=responses, headers=headers)

            resp, session_id = await _dispatch(body, request)
            if resp is None:
                return JSONResponse(content=None, status_code=202)
            headers = {"content-type": "application/json"}
            if session_id:
                headers["Mcp-Session-Id"] = session_id
            return JSONResponse(content=resp, headers=headers)
        except Exception as e:  # noqa: BLE001
            logger.exception("MCP endpoint error")
            return _jsonrpc_error(None, -32603, "Internal error", data=str(e))

    @app.post("/mcp")
    async def mcp_endpoint(request: Request):
        """Main MCP protocol endpoint (JSON-RPC 2.0; supports batch)."""
        return await _handle_mcp(request)

    @app.get("/mcp")
    async def mcp_get():
        """Server->client SSE streaming is not implemented; clients should POST."""
        return JSONResponse(
            content={"detail": "GET (SSE stream) not supported; use POST for JSON-RPC."},
            status_code=405,
            headers={"Allow": "POST, OPTIONS"},
        )

    @app.post("/")
    async def root_mcp_endpoint(request: Request):
        """Root MCP endpoint (fallback)."""
        return await _handle_mcp(request)

    # ------------------------------------------------------------- REST endpoints

    @app.get("/api/tools/{tool_name}/schema")
    async def get_tool_schema(tool_name: str):
        """Get tool parameter schema."""
        try:
            return tool_manager.get_tool_schema(tool_name)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.post("/api/tools/{tool_name}")
    async def execute_rest_tool(
        tool_name: str,
        file: Optional[UploadFile] = File(None),
        params: Optional[str] = Form(None),
        x_api_key: Optional[str] = Header(None),
    ):
        """Execute a tool via the REST API (multipart upload + JSON params)."""
        _check_api_key(x_api_key)

        if tool_name not in tool_manager.registered_tools:
            raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not found")

        kwargs: Dict[str, Any] = {}
        if params:
            try:
                kwargs = json.loads(params)
            except json.JSONDecodeError:
                raise HTTPException(status_code=400, detail="Invalid JSON in params")

        if file is not None:
            max_bytes = int(get_settings().max_upload_bytes or 0)
            content = await file.read()
            if max_bytes and len(content) > max_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"Uploaded file exceeds size limit of {max_bytes} bytes",
                )
            kwargs["file_content"] = content
            kwargs["filename"] = file.filename
            kwargs["content_type"] = file.content_type

        try:
            result = await tool_manager.execute_tool(tool_name, **kwargs)
            return {"success": True, "result": result}
        except ValueError as e:
            # Argument validation error from the tool manager.
            raise HTTPException(status_code=422, detail=str(e))
        except Exception as e:  # noqa: BLE001
            logger.exception("Tool '%s' failed", tool_name)
            raise HTTPException(status_code=500, detail=f"Tool execution failed: {e}")

    @app.post("/mcp/tools/{tool_name}")
    async def execute_mcp_tool(
        tool_name: str, payload: Dict[str, Any], x_api_key: Optional[str] = Header(None)
    ):
        """Execute a tool via a simple JSON endpoint."""
        _check_api_key(x_api_key)
        if tool_name not in tool_manager.registered_tools:
            raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not found")
        try:
            result = await tool_manager.execute_tool(tool_name, **payload)
            return {"success": True, "result": result}
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        except Exception as e:  # noqa: BLE001
            logger.exception("Tool '%s' failed", tool_name)
            raise HTTPException(status_code=500, detail=f"Tool execution failed: {e}")

    # ---------------------------------------------------- background jobs (async)

    if job_manager is not None:

        @app.post("/api/jobs/{tool_name}", status_code=202)
        async def submit_job(
            tool_name: str,
            file: Optional[UploadFile] = File(None),
            params: Optional[str] = Form(None),
            x_api_key: Optional[str] = Header(None),
        ):
            """Submit a tool execution as a background job. Returns a job id immediately.

            Accepts the same inputs as POST /api/tools/{tool_name} (multipart upload
            plus a JSON `params` string). Poll GET /api/jobs/{job_id} for the result.
            """
            _check_api_key(x_api_key)
            if tool_name not in tool_manager.registered_tools:
                raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not found")

            kwargs: Dict[str, Any] = {}
            if params:
                try:
                    kwargs = json.loads(params)
                except json.JSONDecodeError:
                    raise HTTPException(status_code=400, detail="Invalid JSON in params")
            if file is not None:
                max_bytes = int(get_settings().max_upload_bytes or 0)
                content = await file.read()
                if max_bytes and len(content) > max_bytes:
                    raise HTTPException(status_code=413, detail=f"Uploaded file exceeds size limit of {max_bytes} bytes")
                kwargs["file_content"] = content
                kwargs["filename"] = file.filename
                kwargs["content_type"] = file.content_type

            job = job_manager.submit(tool_name, lambda: tool_manager.execute_tool(tool_name, **kwargs))
            return {"job_id": job.id, "status": job.status, "tool": tool_name}

        @app.get("/api/jobs")
        async def list_jobs(x_api_key: Optional[str] = Header(None)):
            """List recent jobs (without results)."""
            _check_api_key(x_api_key)
            return {"jobs": job_manager.list()}

        @app.get("/api/jobs/{job_id}")
        async def get_job(job_id: str, x_api_key: Optional[str] = Header(None)):
            """Get a job's status and (when finished) its result."""
            _check_api_key(x_api_key)
            job = job_manager.get(job_id)
            if job is None:
                raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
            return job.to_dict()

    @app.get("/mcp/session")
    async def get_session(request: Request):
        """Create a session for MCP protocol."""
        session_id, _ = get_or_create_session(request, "initialize")
        return JSONResponse(content={"session_id": session_id}, headers={"Mcp-Session-Id": session_id})

    @app.options("/mcp")
    async def mcp_options():
        return JSONResponse(content={})

    @app.options("/")
    async def root_options():
        return JSONResponse(content={})


# --------------------------------------------------------------------- helpers


def _res_obj(request_id, result: Dict[str, Any]) -> Dict[str, Any]:
    """A JSON-RPC result object (plain dict, for single or batch responses)."""
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _err_obj(request_id, code: int, message: str, data: str = None) -> Dict[str, Any]:
    """A JSON-RPC error object (plain dict, for single or batch responses)."""
    error: Dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _jsonrpc_error(request_id, code: int, message: str, data: str = None, session_id: str = "") -> JSONResponse:
    error: Dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    headers = {"content-type": "application/json"}
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "error": error}, headers=headers)
