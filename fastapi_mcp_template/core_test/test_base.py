"""Base test class and utilities for MCP Template Server tests."""

import unittest
import asyncio
import logging
import importlib.util
import sys
from typing import Any, Dict, Optional
from pathlib import Path


class TestBase:
    """Base class for all tests with common utilities."""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.config = self._load_config()
        self.tool_base = self._load_tool_base()
        if self.tool_base:
            self.tool_base.set_logger(self.logger)
            self.tool_base.set_config(self.config)
    
    def _load_config(self) -> Dict[str, Any]:
        """Dynamically load configuration."""
        try:
            # Try to import config module
            config_path = Path(__file__).parent.parent / "config.py"
            if config_path.exists():
                spec = importlib.util.spec_from_file_location("config", config_path)
                config_module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(config_module)
                
                if hasattr(config_module, 'get_settings'):
                    return config_module.get_settings().dict()
              # Fallback to basic config
            return {
                "host": "0.0.0.0",
                "port": 8000,
                "debug": True,
                "tools_directory": "/app/tools"
            }
        except Exception as e:
            self.logger.warning(f"Could not load config: {e}")
            return {}
    
    def _load_tool_base(self):
        """Dynamically load ToolBase."""
        try:
            # Try to import ToolBase
            tool_base_path = Path(__file__).parent.parent / "core" / "tool_base.py"
            if tool_base_path.exists():
                spec = importlib.util.spec_from_file_location("tool_base", tool_base_path)
                tool_base_module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(tool_base_module)
                
                if hasattr(tool_base_module, 'ToolBase'):
                    return tool_base_module.ToolBase()
            
            return None
        except Exception as e:
            self.logger.warning(f"Could not load ToolBase: {e}")
            return None
    
    def setup_test_environment(self) -> None:
        """Setup test environment."""
        import tempfile
        
        # Setup logging for tests
        logging.basicConfig(level=logging.DEBUG)
        
        # Setup test directories in a temporary location with proper permissions
        temp_dir = tempfile.mkdtemp(prefix="mcp_test_")
        self.test_data_dir = Path(temp_dir) / "test_data"
        self.test_data_dir.mkdir(exist_ok=True)
        
        # Clear any existing test files
        self.cleanup_test_files()
    
    def cleanup_test_files(self) -> None:
        """Clean up test files."""
        if self.test_data_dir.exists():
            for file in self.test_data_dir.glob("*"):
                if file.is_file():
                    file.unlink()
    
    def create_test_file(self, filename: str, content: str) -> Path:
        """Create a test file with given content."""
        file_path = self.test_data_dir / filename
        file_path.write_text(content, encoding='utf-8')
        return file_path
    
    def log_test_info(self, message: str) -> None:
        """Log test information."""
        self.logger.info(f"[TEST] {message}")
    
    def log_test_error(self, message: str) -> None:
        """Log test error."""
        self.logger.error(f"[TEST ERROR] {message}")


class AsyncTestCase(unittest.TestCase):
    """Base async test case."""
    
    def setUp(self):
        """Set up test case."""
        self.test_base = TestBase()
        self.test_base.setup_test_environment()
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
    
    def tearDown(self):
        """Tear down test case."""
        self.test_base.cleanup_test_files()
        self.loop.close()
    
    def run_async(self, coro):
        """Run async coroutine in test."""
        return self.loop.run_until_complete(coro)


class ToolTestBase(TestBase):
    """Base class for tool testing."""
    
    def __init__(self):
        super().__init__()
        self.test_tools: Dict[str, Any] = {}
    
    async def load_test_tool(self, tool_name: str) -> Any:
        """Load a specific tool for testing."""
        try:
            # Import the tool module
            module_path = Path(__file__).parent.parent.parent / "tools" / f"{tool_name}.py"
            
            if not module_path.exists():
                raise FileNotFoundError(f"Tool {tool_name} not found at {module_path}")
            
            # Dynamic import
            import importlib.util
            spec = importlib.util.spec_from_file_location(tool_name, module_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            # Find the tool class
            for name, obj in inspect.getmembers(module):
                # Look for classes that implement ToolInterface (have get_definition, get_schema, execute methods)
                if (inspect.isclass(obj) and 
                    hasattr(obj, 'get_definition') and 
                    hasattr(obj, 'get_schema') and 
                    hasattr(obj, 'execute') and
                    name != 'ToolInterface'):  # Exclude the interface itself
                    
                    # Create tool instance with tool_base parameter
                    tool_instance = obj(self)
                    
                    self.test_tools[tool_name] = tool_instance
                    self.log_test_info(f"Loaded test tool: {tool_name}")
                    return tool_instance
            
            raise ValueError(f"No valid tool class found in {tool_name}")
            
        except Exception as e:
            self.log_test_error(f"Failed to load tool {tool_name}: {e}")
            raise
    
    async def execute_tool_test(self, tool_name: str, parameters: Dict[str, Any]) -> Any:
        """Execute a tool with test parameters."""
        if tool_name not in self.test_tools:
            await self.load_test_tool(tool_name)
        
        tool = self.test_tools[tool_name]
        self.log_test_info(f"Executing tool {tool_name} with parameters: {parameters}")
        
        try:
            # Pass parameters as keyword arguments
            if isinstance(parameters, dict):
                result = await tool.execute(**parameters)
            else:
                result = await tool.execute(parameters=parameters)
            self.log_test_info(f"Tool {tool_name} executed successfully")
            return result
        except Exception as e:
            self.log_test_error(f"Tool {tool_name} execution failed: {e}")
            raise
    
    def log_error(self, message: str) -> None:
        """Log error message - required by tool interface."""
        self.logger.error(f"[TOOL ERROR] {message}")
    
    def log_warning(self, message: str) -> None:
        """Log warning message - required by tool interface."""
        self.logger.warning(f"[TOOL WARNING] {message}")
    
    def log_info(self, message: str) -> None:
        """Log info message - required by tool interface."""
        self.logger.info(f"[TOOL INFO] {message}")


import inspect
