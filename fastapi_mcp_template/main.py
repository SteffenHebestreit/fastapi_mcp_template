import sys
from pathlib import Path

if __package__ in (None, ""):
    # Started as a script (python fastapi_mcp_template/main.py): make the
    # package importable without having to set PYTHONPATH.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from fastapi_mcp_template.core.tool_manager import ToolManager
from fastapi_mcp_template.core.tool_base import ToolBase
from fastapi_mcp_template.core.metrics import MetricsCollector
from fastapi_mcp_template.core.jobs import JobManager
from fastapi_mcp_template.api.routes import (
    PROTOCOL_VERSION_HEADER,
    SESSION_HEADER,
    create_dynamic_routes,
)
from fastapi_mcp_template.api.security import RequestGuardMiddleware
from fastapi_mcp_template.config import Settings, get_settings

# Defense-in-depth: neutralize XML external-entity (XXE) attacks in any stdlib
# XML parsing triggered while extracting Office/HTML documents. Best-effort;
# lxml-based parsers (python-docx/openpyxl) are hardened upstream for OOXML.
try:
    from defusedxml import defuse_stdlib
    defuse_stdlib()
except Exception:  # noqa: BLE001 - defusedxml optional; don't block startup
    pass

# Test imports (only if tests are enabled)
try:
    from fastapi_mcp_template.core_test.test_manager import TestManager
    TEST_SUPPORT_AVAILABLE = True
except ImportError:
    TEST_SUPPORT_AVAILABLE = False

# Setup logging
logging.basicConfig(level=getattr(logging, get_settings().log_level.upper(), logging.INFO))
logger = logging.getLogger(__name__)

HEALTH_PATHS = ("/health", "/api/health")


def _register_core_routes(app: FastAPI, tool_manager: ToolManager) -> None:
    require_api_key = app.state.require_api_key

    @app.get("/health")
    async def health():
        """Health check endpoint."""
        return {"status": "healthy"}

    @app.get("/api/health")
    async def api_health():
        """API health check endpoint for Docker."""
        return {"status": "healthy"}

    @app.get("/tools")
    async def list_tools():
        """List all available tools."""
        return {
            "tools": tool_manager.list_tools()
        }

    @app.get("/metrics")
    @app.get("/api/metrics")
    async def metrics():
        """Expose in-process metrics counters (extractions, errors, vision usage, cost)."""
        collector = getattr(tool_manager.tool_base, "metrics", None)
        if not collector:
            return {"enabled": False, "message": "Metrics are disabled (FTMD_METRICS_ENABLED=false)."}
        return {"enabled": True, **collector.snapshot()}

    @app.post("/tools/reload", dependencies=[Depends(require_api_key)])
    async def reload_tools():
        """Reload all tools (admin endpoint; requires API key when one is configured)."""
        logger.info("Reloading tools...")
        tools = await tool_manager.reload_tools()
        return {
            "message": "Tools reloaded",
            "tools": [t.name for t in tools]
        }


def _register_test_routes(app: FastAPI, test_manager) -> None:
    """Test endpoints: only exposed in debug mode, and gated by the API key when set.

    Discovering and running test code over HTTP is a privileged operation.
    """
    if test_manager is None:
        @app.get("/tests/status")
        async def test_status_unavailable():
            """Get test system status when tests are not available."""
            return {
                "test_support_available": False,
                "message": "Test endpoints are only available in debug mode (FTMD_DEBUG=true)."
            }

        return

    protected = [Depends(app.state.require_api_key)]

    @app.get("/tests", dependencies=protected)
    async def list_tests():
        """List all available tests (this imports the test modules)."""
        tests = await test_manager.discover_tests()
        return {
            "tests": test_manager.list_tests(),
            "total": len(tests)
        }

    @app.post("/tests/run/{test_name}", dependencies=protected)
    async def run_test(test_name: str, specific_test: str = None):
        """Run a specific test or all tests in a test file."""
        try:
            result = await test_manager.run_test(test_name, specific_test)
            return {
                "success": True,
                "result": result
            }
        except ValueError as e:
            return {
                "success": False,
                "error": str(e)
            }
        except Exception as e:
            return {
                "success": False,
                "error": f"Test execution failed: {str(e)}"
            }

    @app.post("/tests/run-all", dependencies=protected)
    async def run_all_tests():
        """Run all discovered tests."""
        try:
            result = await test_manager.run_all_tests()
            return {
                "success": True,
                "result": result
            }
        except Exception as e:
            return {
                "success": False,
                "error": f"Test execution failed: {str(e)}"
            }

    @app.get("/tests/status")
    async def test_status():
        """Get test system status."""
        return {
            "test_support_available": True,
            "tests_directory": str(test_manager.tests_directory),
            "registered_tests": len(test_manager.registered_tests)
        }


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    """Create FastAPI application.

    Each app gets its own tool manager, job manager and session store, so
    several apps (e.g. in tests) can coexist with different settings.
    """
    settings = settings or get_settings()

    tool_manager = ToolManager()
    # In-process background jobs; not durable across restarts.
    job_manager = JobManager()
    test_manager = TestManager() if TEST_SUPPORT_AVAILABLE and settings.debug else None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Application lifespan manager."""
        # Setup tool base with dependencies
        tool_base = ToolBase()
        tool_base.set_logger(logger)
        tool_base.set_config(settings.model_dump(exclude={"api_key"}))
        tool_base.set_metrics(MetricsCollector() if settings.metrics_enabled else None)

        tool_manager.set_tool_base(tool_base)
        tool_manager.configure(
            tools_directory=settings.tools_directory,
            enabled_tools=settings.enabled_tools,
        )
        job_manager.configure(
            max_concurrent=settings.job_max_concurrent,
            max_entries=settings.job_max_entries,
            ttl_seconds=settings.job_ttl_seconds,
        )

        # Discover and load tools
        logger.info("Discovering tools...")
        tools = await tool_manager.discover_tools()
        logger.info(f"Loaded {len(tools)} tools: {[t.name for t in tools]}")

        yield

        # Cleanup
        logger.info("Shutting down...")

    app = FastAPI(
        title="File Extraction Service",
        description="Extract text/data from files (PDF, Office, images, URLs) as LLM-ready context, "
                    "exposed over both a REST API and the Model Context Protocol.",
        version=settings.mcp_server_version,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.tool_manager = tool_manager
    app.state.job_manager = job_manager

    # CORS: wildcard origins and credentials are an invalid combination, so only
    # enable credentials when explicit origins are configured.
    allow_origins = settings.cors_origin_list
    allow_credentials = settings.cors_allow_credentials if allow_origins != ["*"] else False

    # Middleware added later wraps middleware added earlier, so CORS ends up
    # outermost and also decorates the guard's 403/413 responses.
    app.add_middleware(
        RequestGuardMiddleware,
        allowed_origins=allow_origins,
        max_request_bytes=settings.effective_max_request_bytes,
        public_paths=HEALTH_PATHS,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=allow_credentials,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        # MCP clients send MCP-Protocol-Version on every request after the
        # handshake; browsers refuse the request if it is not allowed here.
        allow_headers=[
            "Content-Type", "Accept", "Authorization", "X-API-Key",
            SESSION_HEADER, PROTOCOL_VERSION_HEADER, "Last-Event-ID",
        ],
        expose_headers=[SESSION_HEADER],
    )

    # Generic tool routes don't depend on which tools are loaded, so register
    # them at creation time rather than mutating the app after startup.
    create_dynamic_routes(app, tool_manager, job_manager, settings)
    _register_core_routes(app, tool_manager)
    _register_test_routes(app, test_manager)

    return app


app = create_app()

# The default app's managers, kept as module attributes for convenience.
tool_manager = app.state.tool_manager
job_manager = app.state.job_manager


def main():
    """Main entry point."""
    settings = get_settings()
    uvicorn.run(
        "fastapi_mcp_template.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
