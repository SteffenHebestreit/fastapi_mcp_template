"""
File to Markdown Converter Tool

This tool converts various file formats to Markdown using the MarkItDown library.
It's completely self-contained with no imports from the core service.
"""

import io
import base64
from typing import Dict, Any
from pathlib import Path

try:
    from markitdown import MarkItDown
except ImportError:
    MarkItDown = None


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


class FileToMarkdownConverter(ToolInterface):
    """Converts various file formats to Markdown."""
    
    def __init__(self, tool_base):
        self.tool_base = tool_base
        self.markitdown = None
        if MarkItDown:
            self._initialize_markitdown()
    
    def _initialize_markitdown(self):
        """Initialize MarkItDown with optional OpenAI integration."""
        try:
            # Get configuration from tool_base if available
            config = getattr(self.tool_base, 'config', {}) if self.tool_base else {}
            
            # Check if LLM features are enabled
            enable_llm = config.get('markitdown_enable_llm', False)
            openai_api_key = config.get('openai_api_key')
            openai_base_url = config.get('openai_base_url')
            openai_model = config.get('openai_model', 'gpt-4o')
            
            if enable_llm and openai_api_key:
                try:
                    from openai import OpenAI
                    
                    # Initialize OpenAI client
                    client_kwargs = {'api_key': openai_api_key}
                    if openai_base_url:
                        client_kwargs['base_url'] = openai_base_url
                    
                    client = OpenAI(**client_kwargs)
                    
                    # Initialize MarkItDown with LLM support
                    self.markitdown = MarkItDown(
                        llm_client=client,
                        llm_model=openai_model,
                        enable_plugins=False
                    )
                    
                    if self.tool_base and hasattr(self.tool_base, 'log_info'):
                        self.tool_base.log_info(f"MarkItDown initialized with OpenAI model: {openai_model}")
                    
                except ImportError:
                    # OpenAI not available, fall back to basic MarkItDown
                    self.markitdown = MarkItDown(enable_plugins=False)
                    if self.tool_base and hasattr(self.tool_base, 'log_warning'):
                        self.tool_base.log_warning("OpenAI library not available, using basic MarkItDown")
                except Exception as e:
                    # Error with OpenAI setup, fall back to basic MarkItDown
                    self.markitdown = MarkItDown(enable_plugins=False)
                    if self.tool_base and hasattr(self.tool_base, 'log_warning'):
                        self.tool_base.log_warning(f"OpenAI setup failed, using basic MarkItDown: {e}")
            else:
                # Use basic MarkItDown without LLM features
                self.markitdown = MarkItDown(enable_plugins=False)
                
        except Exception as e:
            # Fallback to basic MarkItDown
            self.markitdown = MarkItDown(enable_plugins=False)
            if self.tool_base and hasattr(self.tool_base, 'log_error'):
                self.tool_base.log_error(f"Error initializing MarkItDown: {e}")
    
    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="file_to_markdown",
            description="Convert various file formats (PDF, DOCX, images, etc.) to Markdown",
            endpoint="/convert",
            tool_type="converter",  # Direct string instead of enum
            version="1.0.0"
        )
    
    def get_schema(self) -> ToolSchema:
        return ToolSchema(
            properties={
                "file_content": {
                    "type": "string",
                    "format": "binary",
                    "description": "File content as bytes"
                },
                "filename": {
                    "type": "string",
                    "description": "Original filename with extension"
                },
                "content_type": {
                    "type": "string",
                    "description": "MIME type of the file"
                },
                "base64_content": {
                    "type": "string",
                    "description": "Base64 encoded file content (alternative to file_content)"
                }
            },
            required=["filename"]
        )
    
    async def execute(self, **kwargs) -> Dict[str, Any]:
        """Convert file to markdown."""
        if not self.markitdown:
            return {
                "success": False,
                "error": "MarkItDown library not available"
            }
        
        filename = kwargs.get("filename")
        if not filename:
            return {
                "success": False,
                "error": "filename is required"
            }
        
        # Get file content
        file_content = kwargs.get("file_content")
        base64_content = kwargs.get("base64_content")
        
        if base64_content:
            try:
                file_content = base64.b64decode(base64_content)
            except Exception as e:
                return {
                    "success": False,
                    "error": f"Invalid base64 content: {str(e)}"
                }
        
        if not file_content:
            return {
                "success": False,
                "error": "No file content provided"
            }
        
        try:
            # Create a BytesIO object from the content
            if isinstance(file_content, str):
                file_content = file_content.encode('utf-8')
            
            file_stream = io.BytesIO(file_content)
            
            # Convert using MarkItDown
            result = self.markitdown.convert_stream(
                file_stream,
                file_extension=Path(filename).suffix.lower(),
                filename=filename
            )
            
            # Log success
            if self.tool_base:
                self.tool_base.log_info(f"Successfully converted {filename} to markdown")
                self.tool_base.record_metric("conversion.success", 1)
            
            return {
                "success": True,
                "markdown": result.markdown,
                "title": result.title,
                "filename": filename,
                "content_type": kwargs.get("content_type"),
                "size": len(file_content)
            }
            
        except Exception as e:
            error_msg = f"Conversion failed: {str(e)}"
            if self.tool_base:
                self.tool_base.log_error(error_msg)
                self.tool_base.record_metric("conversion.error", 1)
            
            return {
                "success": False,
                "error": error_msg,
                "filename": filename
            }


def setup_tool(tool_base):
    """Setup function called by the core service."""
    return FileToMarkdownConverter(tool_base)
