import sys
import importlib.util
from typing import Dict, List, Any, Optional, Iterable
from pathlib import Path

from fastapi_mcp_template.core.tool_base import ToolBase
from fastapi_mcp_template.core.tool_definition import ToolInterface, ToolDefinition


class ToolManager:
    """Manages dynamic tool loading and registration."""

    def __init__(self, tools_directory: str = "/app/tools", enabled_tools: Optional[Iterable[str]] = None):
        self.tools_directory = Path(tools_directory)
        self.enabled_tools = self._normalize_enabled(enabled_tools)
        self.registered_tools: Dict[str, Dict[str, Any]] = {}
        self.tool_instances: Dict[str, Any] = {}
        self.tool_base = ToolBase()

    @staticmethod
    def _normalize_enabled(enabled_tools: Optional[Iterable[str]]) -> Optional[set]:
        """Turn a comma-separated string or iterable into a set, or None for 'all'."""
        if enabled_tools is None:
            return None
        if isinstance(enabled_tools, str):
            names = [t.strip() for t in enabled_tools.split(",")]
        else:
            names = [str(t).strip() for t in enabled_tools]
        names = [n for n in names if n]
        return set(names) if names else None

    def set_tool_base(self, tool_base: ToolBase) -> None:
        """Set the tool base instance with injected dependencies."""
        self.tool_base = tool_base

    def configure(self, tools_directory: Optional[str] = None, enabled_tools: Optional[Iterable[str]] = None) -> None:
        """Update loading configuration (call before discover_tools)."""
        if tools_directory:
            self.tools_directory = Path(tools_directory)
        if enabled_tools is not None:
            self.enabled_tools = self._normalize_enabled(enabled_tools)

    async def discover_tools(self) -> List[ToolDefinition]:
        """Discover all tools in the tools directory into a fresh registry."""
        if not self.tools_directory.exists():
            self.tool_base.log_error(f"Tools directory does not exist: {self.tools_directory}")
            return []

        self.tool_base.log_info(f"Searching for tools in: {self.tools_directory}")

        new_registry: Dict[str, Dict[str, Any]] = {}
        tools: List[ToolDefinition] = []

        for tool_file in sorted(self.tools_directory.glob("*.py")):
            if tool_file.name.startswith("__"):
                continue

            if self.enabled_tools is not None and tool_file.stem not in self.enabled_tools:
                self.tool_base.log_info(f"Skipping disabled tool: {tool_file.stem}")
                continue

            self.tool_base.log_info(f"Attempting to load tool: {tool_file}")
            try:
                definition = await self._load_tool(tool_file, new_registry)
                if definition:
                    tools.append(definition)
                    self.tool_base.log_info(f"Successfully loaded tool: {definition.name}")
                else:
                    self.tool_base.log_error(f"Failed to load tool: {tool_file} (returned None)")
            except Exception as e:
                self.tool_base.log_error(f"Failed to load tool {tool_file}: {e}")

        # Atomic swap so in-flight requests never see a half-empty registry.
        self.registered_tools = new_registry
        return tools

    async def _load_tool(self, tool_file: Path, registry: Dict[str, Dict[str, Any]]) -> Optional[ToolDefinition]:
        """Load a single tool from file into the given registry."""
        module_name = f"fastapi_mcp_tools.{tool_file.stem}"
        spec = importlib.util.spec_from_file_location(module_name, tool_file)

        if not spec or not spec.loader:
            return None

        module = importlib.util.module_from_spec(spec)
        # Register before exec so intra-module imports and dataclasses resolve,
        # and so repeated loads replace rather than leak the old module object.
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(module_name, None)
            raise

        setup_function = getattr(module, "setup_tool", None)
        if not setup_function or not callable(setup_function):
            return None

        tool_instance = setup_function(self.tool_base)

        if not all(hasattr(tool_instance, m) for m in ("get_definition", "get_schema", "execute")):
            return None

        definition = tool_instance.get_definition()

        if definition.name in registry:
            self.tool_base.log_error(
                f"Duplicate tool name '{definition.name}' from {tool_file.name}; keeping the first one loaded"
            )
            return None

        registry[definition.name] = {
            "definition": definition,
            "instance": tool_instance,
            "module": module,
        }
        return definition

    def _validate_arguments(self, tool_name: str, kwargs: Dict[str, Any]) -> None:
        """Check that required schema fields are present before executing."""
        try:
            schema = self.registered_tools[tool_name]["instance"].get_schema().to_dict()
        except Exception:
            return
        missing = [f for f in schema.get("required", []) if kwargs.get(f) in (None, "")]
        if missing:
            raise ValueError(f"Missing required argument(s) for '{tool_name}': {', '.join(missing)}")

    async def execute_tool(self, tool_name: str, /, **kwargs) -> Dict[str, Any]:
        """Execute a registered tool.

        ``tool_name`` is positional-only so a tool argument that happens to be
        called "tool_name" is passed through instead of clashing with it.
        """
        if tool_name not in self.registered_tools:
            raise ValueError(f"Tool '{tool_name}' not found")

        self._validate_arguments(tool_name, kwargs)
        tool_instance = self.registered_tools[tool_name]["instance"]

        try:
            result = await tool_instance.execute(**kwargs)
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
        return self.registered_tools[tool_name]["instance"].get_schema().to_dict()

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
                "schema": tool_info["instance"].get_schema().to_dict(),
            }
            for tool_info in self.registered_tools.values()
        ]

    def get_available_tools(self) -> List[ToolDefinition]:
        """Get all available tool definitions."""
        return [tool_info["definition"] for tool_info in self.registered_tools.values()]

    async def reload_tools(self) -> List[ToolDefinition]:
        """Reload all tools (for development). Atomic: old registry stays live until swap."""
        return await self.discover_tools()
