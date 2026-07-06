import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Header
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from fastapi_mcp_template.core.tool_manager import ToolManager
from fastapi_mcp_template.core.tool_base import ToolBase
from fastapi_mcp_template.core.metrics import MetricsCollector
from fastapi_mcp_template.core.jobs import JobManager
from fastapi_mcp_template.api.routes import create_dynamic_routes, _check_api_key
from fastapi_mcp_template.config import get_settings

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

# Global tool manager
tool_manager = ToolManager()

# Global background-job manager (in-process; not durable across restarts)
job_manager = JobManager()

# Global test manager (if available)
test_manager = TestManager() if TEST_SUPPORT_AVAILABLE else None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    settings = get_settings()

    # Setup tool base with dependencies
    tool_base = ToolBase()
    tool_base.set_logger(logger)
    tool_base.set_config(settings.model_dump())
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


def create_app() -> FastAPI:
    """Create FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="File Extraction Service",
        description="Extract text/data from files (PDF, Office, images, URLs) as LLM-ready context, "
                    "exposed over both a REST API and the Model Context Protocol.",
        version=settings.mcp_server_version,
        lifespan=lifespan,
    )

    # CORS: wildcard origins and credentials are an invalid combination, so only
    # enable credentials when explicit origins are configured.
    if settings.cors_origins.strip() == "*":
        allow_origins = ["*"]
        allow_credentials = False
    else:
        allow_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
        allow_credentials = settings.cors_allow_credentials

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=allow_credentials,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Mcp-Session-Id", "X-API-Key"],
        expose_headers=["Mcp-Session-Id"],
    )

    # Generic tool routes don't depend on which tools are loaded, so register
    # them at creation time rather than mutating the app after startup.
    create_dynamic_routes(app, tool_manager, job_manager)

    return app


app = create_app()


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


@app.post("/tools/reload")
async def reload_tools(x_api_key: Optional[str] = Header(None)):
    """Reload all tools (admin endpoint; requires API key when one is configured)."""
    _check_api_key(x_api_key)
    logger.info("Reloading tools...")
    tools = await tool_manager.reload_tools()
    return {
        "message": "Tools reloaded",
        "tools": [t.name for t in tools]
    }


# Test endpoints: only exposed in debug mode, and gated by the API key when set.
# Running discovered test code over HTTP is a privileged operation.
if TEST_SUPPORT_AVAILABLE and test_manager and get_settings().debug:
    
    @app.get("/tests")
    async def list_tests():
        """List all available tests."""
        tests = await test_manager.discover_tests()
        return {
            "tests": test_manager.list_tests(),
            "total": len(tests)
        }
    
    @app.post("/tests/run/{test_name}")
    async def run_test(test_name: str, specific_test: str = None, x_api_key: Optional[str] = Header(None)):
        """Run a specific test or all tests in a test file."""
        _check_api_key(x_api_key)
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

    @app.post("/tests/run-all")
    async def run_all_tests(x_api_key: Optional[str] = Header(None)):
        """Run all discovered tests."""
        _check_api_key(x_api_key)
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

else:
    @app.get("/tests/status")
    async def test_status():
        """Get test system status when tests are not available."""
        return {
            "test_support_available": False,
            "message": "Test support not available. Install test dependencies to enable testing."
        }


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
