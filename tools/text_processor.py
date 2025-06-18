"""
Text Processor Tool

This tool provides various text processing capabilities.
It's completely self-contained with no imports from the core service.
"""

from typing import Dict, Any
import re

class ToolDefinition:
    """Definition structure for a tool."""
    
    def __init__(self, name: str, description: str, endpoint: str, tool_type: str, version: str = "1.0.0"):
        self.name = name
        self.description = description
        self.endpoint = endpoint
        self.tool_type = tool_type  # Now accepts any string
        self.version = version
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "endpoint": self.endpoint,
            "type": self.tool_type,  # Direct string value
            "version": self.version
        }


class ToolSchema:
    """Schema definition for tool parameters."""
    
    def __init__(self, properties: Dict[str, Any], required: list = None):
        self.properties = properties
        self.required = required or []
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": self.properties,
            "required": self.required
        }


class ToolInterface:
    """Interface that tools must implement."""
    
    def get_definition(self) -> ToolDefinition:
        raise NotImplementedError
    
    def get_schema(self) -> ToolSchema:
        raise NotImplementedError
    
    async def execute(self, **kwargs) -> Dict[str, Any]:
        raise NotImplementedError


class TextProcessor(ToolInterface):
    """Process text with various operations."""
    
    def __init__(self, tool_base):
        self.tool_base = tool_base
    
    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="text_processor",
            description="Process text with various operations like cleaning, formatting, etc.",
            endpoint="/process",
            tool_type="processor",  # Direct string instead of enum
            version="1.0.0"
        )
    
    def get_schema(self) -> ToolSchema:
        return ToolSchema(
            properties={
                "text": {
                    "type": "string",
                    "description": "Input text to process"
                },
                "operation": {
                    "type": "string",
                    "enum": ["clean", "uppercase", "lowercase", "remove_html", "extract_emails"],
                    "description": "Type of processing to perform"
                }
            },
            required=["text", "operation"]
        )
    
    async def execute(self, **kwargs) -> Dict[str, Any]:
        text = kwargs.get("text", "")
        operation = kwargs.get("operation", "clean")
        
        try:
            if operation == "clean":
                result = re.sub(r'\s+', ' ', text.strip())
            elif operation == "uppercase":
                result = text.upper()
            elif operation == "lowercase":
                result = text.lower()
            elif operation == "remove_html":
                result = re.sub(r'<[^>]+>', '', text)
            elif operation == "extract_emails":
                emails = re.findall(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', text)
                result = "\n".join(emails)
            else:
                return {
                    "success": False,
                    "error": f"Unknown operation: {operation}"
                }
            
            if self.tool_base:
                self.tool_base.log_info(f"Text processing completed: {operation}")
            
            return {
                "success": True,
                "result": result,
                "operation": operation,
                "original_length": len(text),
                "processed_length": len(result)
            }
            
        except Exception as e:
            error_msg = f"Text processing failed: {str(e)}"
            if self.tool_base:
                self.tool_base.log_error(error_msg)
            return {
                "success": False,
                "error": error_msg
            }


def setup_tool(tool_base):
    """Setup function called by the core service."""
    return TextProcessor(tool_base)
