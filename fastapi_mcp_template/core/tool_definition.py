from typing import Any, Dict, Callable, Optional, Union
from dataclasses import dataclass


@dataclass
class ToolDefinition:
    """Definition structure for a tool."""
    name: str
    description: str
    endpoint: str
    tool_type: str  # Dynamic string instead of enum
    version: str = "1.0.0"
    author: Optional[str] = None
    tags: Optional[list] = None
    
    def __post_init__(self):
        """Register the tool type when creating a definition."""
        ToolTypeRegistry.register_type(self.tool_type)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "name": self.name,
            "description": self.description,
            "endpoint": self.endpoint,
            "type": self.tool_type,  # Direct string value
            "version": self.version,
            "author": self.author,
            "tags": self.tags or []
        }
    
    @classmethod
    def create(cls, name: str, description: str, endpoint: str, 
               tool_type: str, **kwargs) -> 'ToolDefinition':
        """Create a tool definition with automatic type registration."""
        return cls(
            name=name,
            description=description,
            endpoint=endpoint,
            tool_type=tool_type,
            **kwargs
        )


@dataclass 
class ToolSchema:
    """Schema definition for tool parameters."""
    properties: Dict[str, Any]
    required: list = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON schema format."""
        return {
            "type": "object",
            "properties": self.properties,
            "required": self.required or []
        }


class ToolInterface:
    """Interface that tools must implement."""
    
    def get_definition(self) -> ToolDefinition:
        """Get tool definition."""
        raise NotImplementedError
    
    def get_schema(self) -> ToolSchema:
        """Get parameter schema."""
        raise NotImplementedError
    
    async def execute(self, **kwargs) -> Dict[str, Any]:
        """Execute the tool logic."""
        raise NotImplementedError


class ToolTypeRegistry:
    """Registry for dynamically discovered tool types."""
    
    _registered_types = set()
    
    @classmethod
    def register_type(cls, tool_type: str) -> None:
        """Register a new tool type."""
        cls._registered_types.add(tool_type.lower())
    
    @classmethod
    def get_registered_types(cls) -> list:
        """Get all registered tool types."""
        return sorted(list(cls._registered_types))
    
    @classmethod
    def is_registered(cls, tool_type: str) -> bool:
        """Check if a tool type is registered."""
        return tool_type.lower() in cls._registered_types
    
    @classmethod
    def clear(cls) -> None:
        """Clear all registered types (useful for testing)."""
        cls._registered_types.clear()


# Common tool type constants (optional convenience)
class CommonToolTypes:
    """Common tool type constants for convenience."""
    CONVERTER = "converter"
    PROCESSOR = "processor" 
    FETCHER = "fetcher"
    UTILITY = "utility"
    ANALYZER = "analyzer"
    GENERATOR = "generator"
    TRANSFORMER = "transformer"
    
    @classmethod
    def get_all(cls) -> list:
        """Get all common tool types."""
        return [
            cls.CONVERTER,
            cls.PROCESSOR,
            cls.FETCHER,
            cls.UTILITY,
            cls.ANALYZER,
            cls.GENERATOR,
            cls.TRANSFORMER
        ]
