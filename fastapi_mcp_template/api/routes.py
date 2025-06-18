from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import JSONResponse
from typing import Dict, Any, Optional, List
import json
import uuid
from datetime import datetime

from fastapi_mcp_template.core.tool_manager import ToolManager

# Session storage (in production, use Redis or similar)
active_sessions = {}

def create_dynamic_routes(app, tool_manager: ToolManager) -> None:
    """Create dynamic routes for all registered tools."""
    
    def get_or_create_session(request: Request, method: str = None) -> tuple:
        """Get or create a session ID for the client."""
        # For initialize requests, always create new session
        if method == "initialize":
            session_id = str(uuid.uuid4())
            active_sessions[session_id] = {
                "created": datetime.now(),
                "initialized": False,
                "protocol_version": "2024-11-05"
            }
            return session_id, True
          # For other requests, get from Mcp-Session-Id header
        session_id = request.headers.get("mcp-session-id")
        
        if not session_id:
            return None, False
            
        if session_id not in active_sessions:
            return session_id, False
            
        return session_id, True

    # MCP Protocol endpoints
    @app.post("/mcp")
    async def mcp_endpoint(request: Request, payload: Dict[str, Any]):
        """Main MCP protocol endpoint."""
        import logging
        logger = logging.getLogger(__name__)
        
        try:
            method = payload.get("method")
            params = payload.get("params", {})
            request_id = payload.get("id")
            
            logger.info(f"MCP endpoint called - Method: {method}, Request ID: {request_id}")
            logger.info(f"Request payload: {payload}")
            
            session_id, session_exists = get_or_create_session(request, method)
            logger.info(f"Session ID: {session_id}, Session exists: {session_exists}")
            
            if method == "initialize":
                logger.info(f"Processing initialize request for session: {session_id}")
                # Create new session and mark as initialized immediately
                active_sessions[session_id]["initialized"] = True  # Mark as initialized right away for compatibility
                logger.info(f"Session {session_id} marked as initialized")
                
                # Get available tools from tool manager
                tool_definitions = []
                for tool_name, tool_info in tool_manager.registered_tools.items():
                    tool_definition = tool_info["definition"]
                    tool_instance = tool_info["instance"]
                    schema = tool_instance.get_schema()
                    
                    tool_definitions.append({
                        "name": tool_definition.name,
                        "description": tool_definition.description,
                        "inputSchema": schema.to_dict() if hasattr(schema, 'to_dict') else schema
                    })
                
                # Response with proper tool capabilities declaration and available tools
                response_data = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {
                            "tools": {
                                "listChanged": True
                            }
                        },
                        "serverInfo": {
                            "name": "fastapi_mcp_template",
                            "version": "1.0.0"
                        },
                        "tools": tool_definitions
                    }
                }
                
                return JSONResponse(
                    content=response_data,
                    headers={
                        "Mcp-Session-Id": session_id,
                        "Content-Type": "application/json"
                    }
                )
            
            elif method == "notifications/initialized":
                # Handle initialized notification (no response expected for notifications)
                if session_id and session_id in active_sessions:
                    active_sessions[session_id]["initialized"] = True
                    # Notifications should return 202 Accepted with no content (per MCP spec)
                    return JSONResponse(content=None, status_code=202)
                else:
                    # Even for failed notifications, return 202 (per MCP spec)
                    return JSONResponse(content=None, status_code=202)
            
            # For all other methods, check session and initialization
            if not session_id or not session_exists:
                response_data = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32002,
                        "message": "Server not initialized - session not found"
                    }
                }
            elif not active_sessions.get(session_id, {}).get("initialized", False):
                response_data = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32002,
                        "message": "Server not initialized"
                    }
                }
            elif method == "tools/list":
                # Get tool info from the tool manager's registered tools
                tool_definitions = []
                for tool_name, tool_info in tool_manager.registered_tools.items():
                    tool_definition = tool_info["definition"]
                    tool_instance = tool_info["instance"]
                    schema = tool_instance.get_schema()
                    
                    tool_definitions.append({
                        "name": tool_definition.name,
                        "description": tool_definition.description,
                        "inputSchema": schema.to_dict() if hasattr(schema, 'to_dict') else schema
                    })
                
                response_data = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "tools": tool_definitions
                    }
                }
            
            elif method == "tools/call":
                tool_name = params.get("name")
                arguments = params.get("arguments", {})
                
                result = await tool_manager.execute_tool(tool_name, **arguments)
                
                # Format result as MCP content
                result_text = str(result) if not isinstance(result, str) else result
                
                response_data = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": result_text
                            }
                        ]
                    }
                }
                
            elif method == "ping":
                response_data = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {}
                }
            
            else:
                response_data = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32601,
                        "message": f"Method not found: {method}"
                    }
                }
            
            # Return response with session ID header
            return JSONResponse(
                content=response_data,
                headers={
                    "Mcp-Session-Id": session_id if session_id else "",
                    "content-type": "application/json"
                }
            )
                
        except Exception as e:
            return JSONResponse(
                content={
                    "jsonrpc": "2.0",
                    "id": payload.get("id"),
                    "error": {
                        "code": -32603,
                        "message": "Internal error",
                        "data": str(e)
                    }
                },
                headers={"content-type": "application/json"}
            )
    
    @app.post("/")
    async def root_mcp_endpoint(request: Request, payload: Dict[str, Any]):
        """Root MCP endpoint (fallback)."""
        return await mcp_endpoint(request, payload)
    
    @app.post("/mcp/tools/{tool_name}")
    async def execute_mcp_tool(tool_name: str, payload: Dict[str, Any]):
        """Execute MCP tool endpoint."""
        try:
            result = await tool_manager.execute_tool(tool_name, **payload)
            return {
                "success": True,
                "result": result
            }
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.get("/api/tools/{tool_name}/schema")
    async def get_tool_schema(tool_name: str):
        """Get tool parameter schema."""
        try:
            schema = tool_manager.get_tool_schema(tool_name)
            return schema
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
    
    @app.post("/api/tools/{tool_name}")
    async def execute_rest_tool(
        tool_name: str,
        file: Optional[UploadFile] = File(None),
        params: Optional[str] = Form(None)
    ):
        """Execute tool via REST API."""
        try:
            # Parse parameters
            kwargs = {}
            if params:
                kwargs = json.loads(params)
            
            # Handle file upload
            if file:
                file_content = await file.read()
                kwargs['file_content'] = file_content
                kwargs['filename'] = file.filename
                kwargs['content_type'] = file.content_type
            
            result = await tool_manager.execute_tool(tool_name, **kwargs)
            return {
                "success": True,
                "result": result
            }
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON in params")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.get("/mcp/session")
    async def get_session(request: Request):
        """Get or create a session for MCP protocol."""
        session_id, _ = get_or_create_session(request, "initialize")
        return JSONResponse(
            content={"session_id": session_id},
            headers={"Mcp-Session-Id": session_id}
        )
    
    @app.options("/mcp")
    async def mcp_options():
        """Handle CORS preflight for MCP endpoint."""
        return JSONResponse(
            content={},
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Mcp-Session-Id",
                "Access-Control-Expose-Headers": "Mcp-Session-Id",
            }
        )
    
    @app.options("/")
    async def root_options():
        """Handle CORS preflight for root endpoint."""
        return JSONResponse(
            content={},
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Mcp-Session-Id",
                "Access-Control-Expose-Headers": "Mcp-Session-Id",
            }
        )
