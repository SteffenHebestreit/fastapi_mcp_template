"""
URL Fetcher Tool

This tool fetches content from URLs and can convert web pages to markdown.
It's completely self-contained with no imports from the core service.
"""

from typing import Dict, Any
import asyncio
try:
    import aiohttp
except ImportError:
    aiohttp = None


class ToolDefinition:
    """Definition structure for a tool."""
    
    def __init__(self, name: str, description: str, endpoint: str, tool_type: str, version: str = "1.0.0"):
        self.name = name
        self.description = description
        self.endpoint = endpoint
        self.tool_type = tool_type  # Now accepts any string
        self.version = version
    
    def to_dict(self) -> Dict[str, Any]:        return {
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


class URLFetcher(ToolInterface):
    """Fetch content from URLs."""
    
    def __init__(self, tool_base):
        self.tool_base = tool_base
    
    def get_definition(self) -> ToolDefinition:        return ToolDefinition(
            name="url_fetcher",
            description="Fetch content from URLs and optionally convert to markdown",
            endpoint="/fetch",
            tool_type="fetcher",  # Direct string instead of enum
            version="1.0.0"
        )
    
    def get_schema(self) -> ToolSchema:
        return ToolSchema(
            properties={
                "url": {
                    "type": "string",
                    "format": "uri",
                    "description": "URL to fetch content from"
                },
                "timeout": {
                    "type": "integer",
                    "default": 30,
                    "description": "Request timeout in seconds"
                },
                "user_agent": {
                    "type": "string",
                    "default": "MCP-URL-Fetcher/1.0",
                    "description": "User agent string"
                }
            },
            required=["url"]
        )
    
    async def execute(self, **kwargs) -> Dict[str, Any]:
        if not aiohttp:
            return {
                "success": False,
                "error": "aiohttp library not available"
            }
        
        url = kwargs.get("url")
        if not url:
            return {
                "success": False,
                "error": "url is required"
            }
        
        timeout = kwargs.get("timeout", 30)
        user_agent = kwargs.get("user_agent", "MCP-URL-Fetcher/1.0")
        
        try:
            timeout_obj = aiohttp.ClientTimeout(total=timeout)
            headers = {"User-Agent": user_agent}
            
            async with aiohttp.ClientSession(timeout=timeout_obj) as session:
                async with session.get(url, headers=headers) as response:
                    content = await response.text()
                    
                    if self.tool_base:
                        self.tool_base.log_info(f"Successfully fetched URL: {url}")
                    
                    return {
                        "success": True,
                        "url": url,
                        "status_code": response.status,
                        "content_type": response.headers.get("content-type", "unknown"),
                        "content": content,
                        "content_length": len(content),
                        "headers": dict(response.headers)
                    }
                    
        except asyncio.TimeoutError:
            error_msg = f"Timeout fetching URL: {url}"
            if self.tool_base:
                self.tool_base.log_error(error_msg)
            return {
                "success": False,
                "error": error_msg,
                "url": url
            }
        except Exception as e:
            error_msg = f"Failed to fetch URL {url}: {str(e)}"
            if self.tool_base:
                self.tool_base.log_error(error_msg)
            return {
                "success": False,
                "error": error_msg,
                "url": url
            }


def setup_tool(tool_base):
    """Setup function called by the core service."""
    return URLFetcher(tool_base)
