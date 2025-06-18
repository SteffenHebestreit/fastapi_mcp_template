import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from fastapi_mcp_template.core.tool_manager import ToolManager
from fastapi_mcp_template.core.tool_base import ToolBase
from fastapi_mcp_template.api.routes import create_dynamic_routes
from fastapi_mcp_template.config import get_settings

# Test imports (only if tests are enabled)
try:
    from fastapi_mcp_template.core_test.test_manager import TestManager
    TEST_SUPPORT_AVAILABLE = True
except ImportError:
    TEST_SUPPORT_AVAILABLE = False

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global tool manager
tool_manager = ToolManager()

# Global test manager (if available)
test_manager = TestManager() if TEST_SUPPORT_AVAILABLE else None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Setup tool base with dependencies
    tool_base = ToolBase()
    tool_base.set_logger(logger)
    tool_base.set_config(get_settings().dict())
    
    tool_manager.set_tool_base(tool_base)
    
    # Discover and load tools
    logger.info("Discovering tools...")
    tools = await tool_manager.discover_tools()
    logger.info(f"Loaded {len(tools)} tools: {[t.name for t in tools]}")
    
    # Create dynamic routes
    create_dynamic_routes(app, tool_manager)
    
    yield
    
    # Cleanup
    logger.info("Shutting down...")


def create_app() -> FastAPI:
    """Create FastAPI application."""
    app = FastAPI(
        title="MCP Template Server",
        description="Dynamic MCP server with FastAPI for tool mounting and file conversion",
        version="1.0.0",
        lifespan=lifespan
    )
    
    # Add CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
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


@app.post("/tools/reload")
async def reload_tools():
    """Reload all tools (development endpoint)."""
    logger.info("Reloading tools...")
    tools = await tool_manager.reload_tools()
    return {
        "message": "Tools reloaded",
        "tools": [t.name for t in tools]
    }


# Test endpoints (only if test support is available)
if TEST_SUPPORT_AVAILABLE and test_manager:
    
    @app.get("/tests")
    async def list_tests():
        """List all available tests."""
        tests = await test_manager.discover_tests()
        return {
            "tests": test_manager.list_tests(),
            "total": len(tests)
        }
    
    @app.post("/tests/run/{test_name}")
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
    
    @app.post("/tests/run-all")
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
        reload=settings.debug,
        log_level="info"
    )


if __name__ == "__main__":
    main()
