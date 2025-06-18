import os
import importlib.util
import inspect
from typing import Dict, List, Callable, Any, Optional
from pathlib import Path

from fastapi_mcp_template.core.tool_base import ToolBase
from fastapi_mcp_template.core.tool_definition import ToolInterface, ToolDefinition


class ToolManager:
    """Manages dynamic tool loading and registration."""
    
    def __init__(self, tools_directory: str = "/app/tools"):
        self.tools_directory = Path(tools_directory)
        self.registered_tools: Dict[str, Dict[str, Any]] = {}
        self.tool_instances: Dict[str, Any] = {}
        self.tool_base = ToolBase()
    
    def set_tool_base(self, tool_base: ToolBase) -> None:
        """Set the tool base instance with injected dependencies."""
        self.tool_base = tool_base
        
    async def discover_tools(self) -> List[ToolDefinition]:
        """Discover all tools in the tools directory."""
        tools = []
        
        if not self.tools_directory.exists():
            if self.tool_base.logger:
                self.tool_base.log_error(f"Tools directory does not exist: {self.tools_directory}")
            return tools
        
        if self.tool_base.logger:
            self.tool_base.log_info(f"Searching for tools in: {self.tools_directory}")
        
        for tool_file in self.tools_directory.glob("*.py"):
            if tool_file.name.startswith("__"):
                continue
                
            if self.tool_base.logger:
                self.tool_base.log_info(f"Attempting to load tool: {tool_file}")
                
            try:
                tool_definition = await self._load_tool(tool_file)
                if tool_definition:
                    tools.append(tool_definition)
                    if self.tool_base.logger:
                        self.tool_base.log_info(f"Successfully loaded tool: {tool_definition.name}")
                else:
                    if self.tool_base.logger:
                        self.tool_base.log_error(f"Failed to load tool: {tool_file} (returned None)")
            except Exception as e:
                if self.tool_base.logger:
                    self.tool_base.log_error(f"Failed to load tool {tool_file}: {e}")
        
        return tools
    
    async def _load_tool(self, tool_file: Path) -> Optional[ToolDefinition]:
        """Load a single tool from file."""
        module_name = tool_file.stem
        spec = importlib.util.spec_from_file_location(module_name, tool_file)
        
        if not spec or not spec.loader:
            return None
        
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)        
        # Look for the setup function
        setup_function = getattr(module, 'setup_tool', None)
        if not setup_function or not callable(setup_function):
            return None
        
        # Call setup function with tool_base
        tool_instance = setup_function(self.tool_base)
        
        # Check if tool_instance has required methods (duck typing)
        if not all(hasattr(tool_instance, method) for method in ['get_definition', 'get_schema', 'execute']):
            return None
        
        definition = tool_instance.get_definition()
        
        # Register the tool
        self.registered_tools[definition.name] = {
            "definition": definition,
            "instance": tool_instance,
            "module": module
        }
        
        return definition
    
    async def execute_tool(self, tool_name: str, **kwargs) -> Dict[str, Any]:
        """Execute a registered tool."""
        if tool_name not in self.registered_tools:
            raise ValueError(f"Tool '{tool_name}' not found")
        
        tool_info = self.registered_tools[tool_name]
        tool_instance = tool_info["instance"]
        
        try:
            result = await tool_instance.execute(**kwargs)
            
            # Record metrics
            self.tool_base.record_metric(f"tool.{tool_name}.execution", 1)
            
            return result
        except Exception as e:
            self.tool_base.log_error(f"Tool execution failed for {tool_name}: {e}")
            self.tool_base.record_metric(f"tool.{tool_name}.error", 1)
            raise
    
    def get_tool_schema(self, tool_name: str) -> Dict[str, Any]:
        """Get schema for a tool."""
        if tool_name not in self.registered_tools:
            raise ValueError(f"Tool '{tool_name}' not found")
        
        tool_info = self.registered_tools[tool_name]
        tool_instance = tool_info["instance"]
        
        return tool_instance.get_schema().to_dict()
    
    def get_tool_definition(self, tool_name: str) -> ToolDefinition:
        """Get definition for a tool."""
        if tool_name not in self.registered_tools:
            raise ValueError(f"Tool '{tool_name}' not found")
        
        return self.registered_tools[tool_name]["definition"]
    
    def list_tools(self) -> List[Dict[str, Any]]:
        """List all registered tools."""
        return [
            {
                **tool_info["definition"].to_dict(),
                "schema": tool_info["instance"].get_schema().to_dict()
            }
            for tool_info in self.registered_tools.values()
        ]
    
    def get_available_tools(self) -> List[ToolDefinition]:
        """Get all available tool definitions."""
        return [tool_info["definition"] for tool_info in self.registered_tools.values()]
    
    async def reload_tools(self) -> List[ToolDefinition]:
        """Reload all tools (for development)."""
        self.registered_tools.clear()
        return await self.discover_tools()
