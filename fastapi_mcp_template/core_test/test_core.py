"""Test file for core functionality of the MCP Template Server."""

import unittest
import asyncio
import importlib.util
import sys
from pathlib import Path

from .test_base import AsyncTestCase, TestBase


class TestCore(AsyncTestCase):
    """Test core functionality."""
    
    def test_tool_base_initialization(self):
        """Test that tool base initializes correctly."""
        self.assertIsNotNone(self.test_base.tool_base)
        self.assertIsNotNone(self.test_base.logger)
        self.assertIsNotNone(self.test_base.config)
    
    def test_test_environment_setup(self):
        """Test that test environment sets up correctly."""
        self.test_base.setup_test_environment()
        self.assertTrue(self.test_base.test_data_dir.exists())
    
    def test_create_test_file(self):
        """Test file creation in test environment."""
        content = "This is test content"
        file_path = self.test_base.create_test_file("test.txt", content)
        
        self.assertTrue(file_path.exists())
        self.assertEqual(file_path.read_text(), content)
    
    async def test_async_functionality(self):
        """Test async functionality works."""
        await asyncio.sleep(0.01)  # Simple async operation
        self.assertTrue(True)
    
    def test_async_functionality_sync(self):
        """Test async functionality from sync method."""
        self.run_async(self._async_helper())
    
    async def _async_helper(self):
        """Helper async method."""
        await asyncio.sleep(0.01)
        return True


class TestToolManager(AsyncTestCase):
    """Test tool manager functionality."""
    
    def _load_tool_manager(self):
        """Dynamically load tool manager."""
        try:
            tool_manager_path = Path(__file__).parent.parent / "core" / "tool_manager.py"
            spec = importlib.util.spec_from_file_location("tool_manager", tool_manager_path)
            tool_manager_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(tool_manager_module)
            return tool_manager_module.ToolManager
        except Exception as e:
            self.fail(f"Could not load ToolManager: {e}")
    
    async def test_tool_discovery(self):
        """Test tool discovery mechanism."""
        ToolManager = self._load_tool_manager()
        
        # Create tool manager with test directory
        tool_manager = ToolManager(tools_directory=str(self.test_base.test_data_dir.parent.parent / "tools"))
        tool_manager.set_tool_base(self.test_base.tool_base)
        
        # Discover tools
        tools = await tool_manager.discover_tools()
        
        # Should find some tools
        self.assertIsInstance(tools, list)
        # Note: Actual count depends on available tools
    
    def test_tool_manager_initialization(self):
        """Test tool manager initializes correctly."""
        ToolManager = self._load_tool_manager()
        
        tool_manager = ToolManager()
        self.assertIsNotNone(tool_manager.tools_directory)
        self.assertIsInstance(tool_manager.registered_tools, dict)


class TestConfig(unittest.TestCase):
    """Test configuration functionality."""
    
    def _load_config(self):
        """Dynamically load config."""
        try:
            config_path = Path(__file__).parent.parent / "config.py"
            spec = importlib.util.spec_from_file_location("config", config_path)
            config_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(config_module)
            return config_module
        except Exception as e:
            self.fail(f"Could not load config: {e}")
    
    def test_config_loading(self):
        """Test that configuration loads correctly."""
        config_module = self._load_config()
        
        # Test that get_settings function exists
        self.assertTrue(hasattr(config_module, 'get_settings'))
        
        # Test that settings can be retrieved
        settings = config_module.get_settings()
        self.assertIsNotNone(settings)


class TestApiRoutes(AsyncTestCase):
    """Test API routes functionality."""
    
    def _load_routes(self):
        """Dynamically load routes module."""
        try:
            routes_path = Path(__file__).parent.parent / "api" / "routes.py"
            spec = importlib.util.spec_from_file_location("routes", routes_path)
            routes_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(routes_module)
            return routes_module
        except Exception as e:
            self.fail(f"Could not load routes: {e}")
    
    def test_routes_module_loading(self):
        """Test that routes module loads correctly."""
        routes_module = self._load_routes()
        
        # Test that create_dynamic_routes function exists
        self.assertTrue(hasattr(routes_module, 'create_dynamic_routes'))


# Standalone test functions for core functionality
async def test_core_modules_loading():
    """Standalone test for core module loading."""
    test_base = TestBase()
    test_base.setup_test_environment()
    
    # Test loading core modules
    core_modules = ['tool_manager', 'tool_base', 'tool_definition']
    core_dir = Path(__file__).parent.parent / "core"
    
    for module_name in core_modules:
        module_path = core_dir / f"{module_name}.py"
        if module_path.exists():
            try:
                spec = importlib.util.spec_from_file_location(module_name, module_path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                test_base.log_test_info(f"Successfully loaded core module: {module_name}")
            except Exception as e:
                test_base.log_test_error(f"Failed to load core module {module_name}: {e}")
    
    test_base.cleanup_test_files()


def test_core_directory_structure():
    """Test that core directory has expected structure."""
    core_dir = Path(__file__).parent.parent / "core"
    api_dir = Path(__file__).parent.parent / "api"
    
    assert core_dir.exists(), "Core directory should exist"
    assert api_dir.exists(), "API directory should exist"
    
    # Check for expected core files
    expected_files = ['tool_manager.py', 'tool_base.py', 'tool_definition.py']
    for file_name in expected_files:
        file_path = core_dir / file_name
        assert file_path.exists(), f"Core file {file_name} should exist"


if __name__ == "__main__":
    unittest.main()
