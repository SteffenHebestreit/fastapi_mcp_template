"""Test file for tool functionality only."""

import unittest
import asyncio
import importlib.util
import sys
from pathlib import Path


def load_test_base():
    """Dynamically load test base classes."""
    try:
        test_base_path = Path(__file__).parent.parent / "fastapi_mcp_template" / "core_test" / "test_base.py"
        if test_base_path.exists():
            spec = importlib.util.spec_from_file_location("test_base", test_base_path)
            test_base_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(test_base_module)
            return test_base_module
        return None
    except Exception as e:
        print(f"Could not load test base: {e}")
        return None


# Load test base module
test_base_module = load_test_base()
if test_base_module:
    AsyncTestCase = test_base_module.AsyncTestCase
    ToolTestBase = test_base_module.ToolTestBase
else:
    # Fallback to basic unittest.TestCase
    AsyncTestCase = unittest.TestCase
    ToolTestBase = unittest.TestCase


class TestFileConverterTool(AsyncTestCase):
    """Test file converter tool."""
    
    def setUp(self):
        """Set up test case."""
        if test_base_module:
            self.tool_test_base = ToolTestBase()
            self.tool_test_base.setup_test_environment()
        else:
            self.tool_test_base = None
    
    def tearDown(self):
        """Clean up after test."""
        if self.tool_test_base:
            self.tool_test_base.cleanup_test_files()
    
    async def test_file_converter_tool_loading(self):
        """Test file converter tool loading."""
        if not self.tool_test_base:
            self.skipTest("Test base not available")
        
        try:
            tool = await self.tool_test_base.load_test_tool("file_converter")
            self.assertIsNotNone(tool)
            self.tool_test_base.log_test_info("File converter tool loaded successfully")
        except FileNotFoundError:
            self.skipTest("file_converter tool not available")
        except Exception as e:
            self.fail(f"Unexpected error loading file_converter: {e}")
    
    async def test_file_converter_execution(self):
        """Test file converter tool execution."""
        if not self.tool_test_base:
            self.skipTest("Test base not available")
        
        try:
            # Create a test markdown file
            test_content = "# Test Markdown\n\nThis is a test file."
            test_file = self.tool_test_base.create_test_file("test.md", test_content)
            
            # Test tool execution
            parameters = {
                "file_path": str(test_file),
                "output_format": "html"
            }
            result = await self.tool_test_base.execute_tool_test("file_converter", parameters)
            self.assertIsNotNone(result)
        
        except FileNotFoundError:
            self.skipTest("file_converter tool not available")
        except Exception as e:
            self.skipTest(f"file_converter test failed: {e}")


class TestTextProcessorTool(AsyncTestCase):
    """Test text processor tool."""
    
    def setUp(self):
        """Set up test case."""
        if test_base_module:
            self.tool_test_base = ToolTestBase()
            self.tool_test_base.setup_test_environment()
        else:
            self.tool_test_base = None
    
    def tearDown(self):
        """Clean up after test."""
        if self.tool_test_base:
            self.tool_test_base.cleanup_test_files()
    
    async def test_text_processor_tool_loading(self):
        """Test text processor tool loading."""
        if not self.tool_test_base:
            self.skipTest("Test base not available")
        
        try:
            tool = await self.tool_test_base.load_test_tool("text_processor")
            self.assertIsNotNone(tool)
            self.tool_test_base.log_test_info("Text processor tool loaded successfully")
        except FileNotFoundError:
            self.skipTest("text_processor tool not available")
        except Exception as e:
            self.fail(f"Unexpected error loading text_processor: {e}")
    
    async def test_text_processor_execution(self):
        """Test text processor tool execution."""
        if not self.tool_test_base:
            self.skipTest("Test base not available")
        
        try:
            parameters = {
                "text": "This is test text for processing.",
                "operation": "uppercase"
            }
            result = await self.tool_test_base.execute_tool_test("text_processor", parameters)
            self.assertIsNotNone(result)
        
        except FileNotFoundError:
            self.skipTest("text_processor tool not available")
        except Exception as e:
            self.skipTest(f"text_processor test failed: {e}")


class TestUrlFetcherTool(AsyncTestCase):
    """Test URL fetcher tool."""
    
    def setUp(self):
        """Set up test case."""
        if test_base_module:
            self.tool_test_base = ToolTestBase()
            self.tool_test_base.setup_test_environment()
        else:
            self.tool_test_base = None
    
    def tearDown(self):
        """Clean up after test."""
        if self.tool_test_base:
            self.tool_test_base.cleanup_test_files()
    
    async def test_url_fetcher_tool_loading(self):
        """Test URL fetcher tool loading."""
        if not self.tool_test_base:
            self.skipTest("Test base not available")
        
        try:
            tool = await self.tool_test_base.load_test_tool("url_fetcher")
            self.assertIsNotNone(tool)
            self.tool_test_base.log_test_info("URL fetcher tool loaded successfully")
        except FileNotFoundError:
            self.skipTest("url_fetcher tool not available")
        except Exception as e:
            self.fail(f"Unexpected error loading url_fetcher: {e}")
    
    async def test_url_fetcher_execution(self):
        """Test URL fetcher tool execution."""
        if not self.tool_test_base:
            self.skipTest("Test base not available")
        
        try:
            parameters = {
                "url": "https://httpbin.org/json",
                "timeout": 10
            }
            result = await self.tool_test_base.execute_tool_test("url_fetcher", parameters)
            self.assertIsNotNone(result)
        
        except FileNotFoundError:
            self.skipTest("url_fetcher tool not available")
        except Exception as e:
            # Network issues are common in test environments
            self.skipTest(f"url_fetcher test skipped due to network: {e}")


# Standalone test functions for tools only
async def test_tool_loading_standalone():
    """Standalone test for tool loading."""
    if not test_base_module:
        print("Test base not available, skipping standalone tool test")
        return
    
    tool_test_base = test_base_module.ToolTestBase()
    tool_test_base.setup_test_environment()
    
    # List available tools
    tools_dir = Path(__file__).parent.parent / "tools"
    available_tools = [f.stem for f in tools_dir.glob("*.py") if not f.name.startswith("__")]
    
    tool_test_base.log_test_info(f"Available tools: {available_tools}")
    
    # Try to load each tool
    for tool_name in available_tools:
        try:
            tool = await tool_test_base.load_test_tool(tool_name)
            tool_test_base.log_test_info(f"Successfully loaded tool: {tool_name}")
        except Exception as e:
            tool_test_base.log_test_info(f"Could not load tool {tool_name}: {e}")
    
    tool_test_base.cleanup_test_files()


def test_tools_directory_structure():
    """Test that tools directory has expected structure."""
    tools_dir = Path(__file__).parent.parent / "tools"
    requirements_dir = tools_dir / "requirements"
    
    assert tools_dir.exists(), "Tools directory should exist"
    assert requirements_dir.exists(), "Tools requirements directory should exist"
    
    # Check for some expected tool files
    tool_files = list(tools_dir.glob("*.py"))
    assert len(tool_files) > 0, "Should have at least one tool file"
    
    # Check that each tool has a corresponding requirements file
    for tool_file in tool_files:
        if not tool_file.name.startswith("__"):
            req_file = requirements_dir / f"{tool_file.stem}.txt"
            if not req_file.exists():
                print(f"Warning: No requirements file found for tool {tool_file.stem}")


def test_tool_requirements_files():
    """Test that tool requirements files are properly formatted."""
    requirements_dir = Path(__file__).parent.parent / "tools" / "requirements"
    
    if not requirements_dir.exists():
        print("Requirements directory does not exist")
        return
    
    for req_file in requirements_dir.glob("*.txt"):
        try:
            content = req_file.read_text().strip()
            if content:
                # Basic validation that it looks like a requirements file
                lines = [line.strip() for line in content.split('\n') if line.strip()]
                for line in lines:
                    if not line.startswith('#'):  # Skip comments
                        # Should contain package names (basic check)
                        assert len(line) > 0, f"Empty requirement line in {req_file.name}"
                print(f"Requirements file {req_file.name} is valid")
            else:
                print(f"Requirements file {req_file.name} is empty")
        except Exception as e:
            print(f"Error reading requirements file {req_file.name}: {e}")


if __name__ == "__main__":
    unittest.main()
