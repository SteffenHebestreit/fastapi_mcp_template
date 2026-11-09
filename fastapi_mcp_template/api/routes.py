import hmac
import json
import logging
from typing import Any, Dict, Optional

from fastapi import Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from fastapi.security import APIKeyHeader

from fastapi_mcp_template.config import Settings, get_settings
from fastapi_mcp_template.core.jobs import JobManager
from fastapi_mcp_template.core.session_store import SessionStore
from fastapi_mcp_template.core.tool_manager import ToolManager

logger = logging.getLogger(__name__)

# MCP protocol revisions this server can speak (the handshake-era ones), newest
# first. A client asking for one of these gets it back; anything else is
# answered with the newest, as the MCP lifecycle rules require.
#
# Not implemented: the stateless 2026-07-28 revision ("server/discover", no
# initialize, no sessions). Clients that prefer it probe with server/discover,
# are rejected, and fall back to the initialize handshake.
SUPPORTED_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
LATEST_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]

SESSION_HEADER = "Mcp-Session-Id"
PROTOCOL_VERSION_HEADER = "MCP-Protocol-Version"

# JSON-RPC 2.0 error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
# Implementation-defined server errors
UNAUTHORIZED = -32001
NOT_INITIALIZED = -32002

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def api_key_matches(configured: Optional[str], headers) -> bool:
    """True when no key is configured, or the request presents the right one.

    The key is accepted in ``X-API-Key`` or as ``Authorization: Bearer <key>``.
    """
    if not configured:
        return True
    provided = headers.get("x-api-key")
    if not provided:
        authorization = headers.get("authorization", "")
        if authorization[:7].lower() == "bearer ":
            provided = authorization[7:].strip()
    return bool(provided) and hmac.compare_digest(provided.encode(), configured.encode())


def tool_failed(result: Any) -> bool:
    """Whether a tool reported failure the conventional way (``success: False``)."""
    return isinstance(result, dict) and result.get("success") is False


def format_tool_result(result: Any) -> Dict[str, Any]:
    """Convert a tool's return value into an MCP ``tools/call`` result."""
    if isinstance(result, str):
        text = result
    else:
        # ensure_ascii=False keeps non-ASCII text (umlauts, CJK, ...) readable
        # for the model instead of turning it into \uXXXX escapes.
        text = json.dumps(result, default=str, ensure_ascii=False)
    return {"content": [{"type": "text", "text": text}], "isError": tool_failed(result)}


class _TransportError(Exception):
    """A failure the transport reports through the HTTP status code."""

    def __init__(self, status_code: int, request_id: Any, code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.request_id = request_id
        self.code = code
        self.message = message


def create_dynamic_routes(
    app,
    tool_manager: ToolManager,
    job_manager: JobManager = None,
    settings: Optional[Settings] = None,
    sessions: Optional[SessionStore] = None,
) -> None:
    """Create the MCP, REST and job routes for the tools held by ``tool_manager``."""
    settings = settings or get_settings()
    if sessions is None:
        sessions = SessionStore(
            ttl_seconds=settings.session_ttl_seconds,
            max_sessions=settings.max_sessions,
        )
    app.state.sessions = sessions

    async def require_api_key(request: Request, _key: Optional[str] = Depends(_api_key_header)) -> None:
        """Enforce the API key when one is configured. No-op in dev (key unset)."""
        if not api_key_matches(settings.api_key, request.headers):
            raise HTTPException(status_code=401, detail="Invalid or missing API key")

    # Shared with the admin/test routes registered in main.py.
    app.state.require_api_key = require_api_key
    protected = [Depends(require_api_key)]

    # ------------------------------------------------------------- MCP endpoint

    async def _dispatch(message: Any, request: Request, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Handle one JSON-RPC message.

        Returns the response object, or None for notifications (no reply is emitted).
        """
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or "method" not in message:
            return _err_obj(message.get("id") if isinstance(message, dict) else None,
                            INVALID_REQUEST, "Invalid Request")

        method = message.get("method")
        request_id = message.get("id")
        if not isinstance(method, str):
            return _err_obj(request_id, INVALID_REQUEST, "Invalid Request")
        is_notification = "id" not in message or method.startswith("notifications/")

        params = message.get("params") or {}
        if not isinstance(params, dict):
            return None if is_notification else _err_obj(
                request_id, INVALID_PARAMS, "Invalid params: params must be an object")

        # Auth (when configured) applies to everything except the initial handshake.
        if method not in ("initialize", "notifications/initialized"):
            if not api_key_matches(settings.api_key, request.headers):
                return None if is_notification else _err_obj(request_id, UNAUTHORIZED, "Unauthorized")

        if method == "initialize":
            requested = params.get("protocolVersion")
            negotiated = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else LATEST_PROTOCOL_VERSION
            # Marked initialized right away: several clients send their first
            # request without waiting for "notifications/initialized".
            ctx["session_id"] = sessions.create(protocol_version=negotiated, initialized=True)
            ctx["session"] = sessions.get(ctx["session_id"])
            return _res_obj(request_id, {
                "protocolVersion": negotiated,
                # listChanged is false: without a server->client stream we cannot
                # push notifications, so advertising it would be dishonest.
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": settings.mcp_server_name,
                    "version": settings.mcp_server_version,
                },
            })

        if is_notification:
            if ctx["session"] is not None:
                ctx["session"]["initialized"] = True
            return None  # notifications get no response

        if ctx["session"] is None:
            if not ctx["session_id"]:
                raise _TransportError(
                    400, request_id, NOT_INITIALIZED,
                    f"Server not initialized - {SESSION_HEADER} header is required",
                )
            # 404 (not a JSON-RPC error on a 200) is what makes a client start a
            # new session, e.g. after this server restarted or the session expired.
            raise _TransportError(
                404, request_id, NOT_INITIALIZED, "Server not initialized - session not found")
        if not ctx["session"].get("initialized", False):
            return _err_obj(request_id, NOT_INITIALIZED, "Server not initialized")

        if method == "tools/list":
            tools = []
            for info in tool_manager.registered_tools.values():
                schema = info["instance"].get_schema()
                tools.append({
                    "name": info["definition"].name,
                    "description": info["definition"].description,
                    "inputSchema": schema.to_dict() if hasattr(schema, "to_dict") else schema,
                })
            return _res_obj(request_id, {"tools": tools})

        if method == "tools/call":
            tool_name = params.get("name")
            arguments = params.get("arguments")
            if arguments is None:
                arguments = {}
            if not isinstance(tool_name, str) or not tool_name:
                return _err_obj(request_id, INVALID_PARAMS, "Invalid params: tool name is required")
            if not isinstance(arguments, dict):
                return _err_obj(request_id, INVALID_PARAMS, "Invalid params: arguments must be an object")
            if tool_name not in tool_manager.registered_tools:
                return _err_obj(request_id, INVALID_PARAMS, f"Unknown tool: {tool_name}")
            try:
                result = await tool_manager.execute_tool(tool_name, **arguments)
            except Exception as e:  # noqa: BLE001 - report as MCP tool error, not JSON-RPC error
                logger.warning("Tool '%s' execution failed: %s", tool_name, e)
                return _res_obj(
                    request_id,
                    {"content": [{"type": "text", "text": f"Tool execution failed: {e}"}], "isError": True},
                )
            return _res_obj(request_id, format_tool_result(result))

        if method == "ping":
            return _res_obj(request_id, {})

        return _err_obj(request_id, METHOD_NOT_FOUND, f"Method not found: {method}")

    async def _handle_mcp(request: Request):
        """Parse the body (single object or JSON-RPC batch array) and dispatch."""
        # Read outside the catch-all below so a 413 from the size limit gets through.
        raw = await request.body()
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            return _jsonrpc_error(None, PARSE_ERROR, "Parse error", status_code=400)

        if isinstance(body, dict):
            messages, is_batch = [body], False
        elif isinstance(body, list) and body:
            messages, is_batch = body, True
        else:
            return _jsonrpc_error(None, INVALID_REQUEST, "Invalid Request", status_code=400)

        has_initialize = any(isinstance(m, dict) and m.get("method") == "initialize" for m in messages)
        header_version = request.headers.get(PROTOCOL_VERSION_HEADER)
        if header_version and not has_initialize and header_version not in SUPPORTED_PROTOCOL_VERSIONS:
            return _jsonrpc_error(
                None, INVALID_REQUEST,
                f"Unsupported protocol version '{header_version}' "
                f"(supported: {', '.join(SUPPORTED_PROTOCOL_VERSIONS)})",
                status_code=400,
            )

        session_id = request.headers.get(SESSION_HEADER)
        ctx: Dict[str, Any] = {"session_id": session_id, "session": sessions.get(session_id)}

        responses = []
        try:
            for message in messages:
                try:
                    response = await _dispatch(message, request, ctx)
                except _TransportError:
                    raise
                except Exception as e:  # noqa: BLE001
                    logger.exception("MCP endpoint error")
                    request_id = message.get("id") if isinstance(message, dict) else None
                    response = _err_obj(request_id, INTERNAL_ERROR, "Internal error", data=str(e))
                if response is not None:
                    responses.append(response)
        except _TransportError as e:
            return _jsonrpc_error(e.request_id, e.code, e.message, status_code=e.status_code)

        headers = {SESSION_HEADER: ctx["session_id"]} if ctx["session"] is not None else {}
        if not responses:
            # Only notifications were received: 202 Accepted with no body.
            return Response(status_code=202, headers=headers)
        return JSONResponse(content=responses if is_batch else responses[0], headers=headers)

    @app.post("/mcp")
    async def mcp_endpoint(request: Request):
        """Main MCP protocol endpoint (JSON-RPC 2.0; supports batch)."""
        return await _handle_mcp(request)

    @app.get("/mcp")
    async def mcp_get():
        """Server->client SSE streaming is not implemented; clients should POST."""
        return JSONResponse(
            content={"detail": "GET (SSE stream) not supported; use POST for JSON-RPC."},
            status_code=405,
            headers={"Allow": "POST, DELETE, OPTIONS"},
        )

    @app.delete("/mcp")
    async def mcp_delete(request: Request):
        """Terminate the MCP session named in the Mcp-Session-Id header."""
        session_id = request.headers.get(SESSION_HEADER)
        if not session_id:
            raise HTTPException(status_code=400, detail=f"{SESSION_HEADER} header is required")
        if not sessions.delete(session_id):
            raise HTTPException(status_code=404, detail="Session not found")
        return Response(status_code=200)

    @app.post("/")
    async def root_mcp_endpoint(request: Request):
        """Root MCP endpoint (fallback)."""
        return await _handle_mcp(request)

    # ------------------------------------------------------------- REST endpoints

    def _require_tool(tool_name: str) -> None:
        if tool_name not in tool_manager.registered_tools:
            raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not found")

    async def _multipart_arguments(file: Optional[UploadFile], params: Optional[str]) -> Dict[str, Any]:
        """Tool arguments from a multipart request (``params`` JSON plus optional upload)."""
        kwargs: Dict[str, Any] = {}
        if params:
            try:
                kwargs = json.loads(params)
            except json.JSONDecodeError:
                raise HTTPException(status_code=400, detail="Invalid JSON in params")
            if not isinstance(kwargs, dict):
                raise HTTPException(status_code=400, detail="params must be a JSON object")

        if file is not None:
            max_bytes = int(settings.max_upload_bytes or 0)
            content = await file.read()
            if max_bytes and len(content) > max_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"Uploaded file exceeds size limit of {max_bytes} bytes",
                )
            kwargs["file_content"] = content
            kwargs["filename"] = file.filename
            kwargs["content_type"] = file.content_type
        return kwargs

    async def _run_tool(tool_name: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        try:
            result = await tool_manager.execute_tool(tool_name, **kwargs)
            return {"success": True, "result": result}
        except ValueError as e:
            # Argument validation error from the tool manager.
            raise HTTPException(status_code=422, detail=str(e))
        except Exception as e:  # noqa: BLE001
            logger.exception("Tool '%s' failed", tool_name)
            raise HTTPException(status_code=500, detail=f"Tool execution failed: {e}")

    @app.get("/api/tools/{tool_name}/schema")
    async def get_tool_schema(tool_name: str):
        """Get tool parameter schema."""
        try:
            return tool_manager.get_tool_schema(tool_name)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.post("/api/tools/{tool_name}", dependencies=protected)
    async def execute_rest_tool(
        tool_name: str,
        file: Optional[UploadFile] = File(None),
        params: Optional[str] = Form(None),
    ):
        """Execute a tool via the REST API (multipart upload + JSON params)."""
        _require_tool(tool_name)
        return await _run_tool(tool_name, await _multipart_arguments(file, params))

    @app.post("/mcp/tools/{tool_name}", dependencies=protected)
    async def execute_mcp_tool(tool_name: str, payload: Dict[str, Any]):
        """Execute a tool via a simple JSON endpoint."""
        _require_tool(tool_name)
        return await _run_tool(tool_name, payload)

    # ---------------------------------------------------- background jobs (async)

    if job_manager is not None:

        @app.post("/api/jobs/{tool_name}", status_code=202, dependencies=protected)
        async def submit_job(
            tool_name: str,
            file: Optional[UploadFile] = File(None),
            params: Optional[str] = Form(None),
        ):
            """Submit a tool execution as a background job. Returns a job id immediately.

            Accepts the same inputs as POST /api/tools/{tool_name} (multipart upload
            plus a JSON `params` string). Poll GET /api/jobs/{job_id} for the result.
            """
            _require_tool(tool_name)
            kwargs = await _multipart_arguments(file, params)
            job = job_manager.submit(tool_name, lambda: tool_manager.execute_tool(tool_name, **kwargs))
            return {"job_id": job.id, "status": job.status, "tool": tool_name}

        @app.get("/api/jobs", dependencies=protected)
        async def list_jobs():
            """List recent jobs (without results)."""
            return {"jobs": job_manager.list()}

        @app.get("/api/jobs/{job_id}", dependencies=protected)
        async def get_job(job_id: str):
            """Get a job's status and (when finished) its result."""
            job = job_manager.get(job_id)
            if job is None:
                raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
            return job.to_dict()

    @app.get("/mcp/session")
    async def get_session():
        """Create a session for MCP protocol (helper; skips the initialize handshake)."""
        session_id = sessions.create(protocol_version=LATEST_PROTOCOL_VERSION)
        return JSONResponse(content={"session_id": session_id}, headers={SESSION_HEADER: session_id})

    @app.options("/mcp")
    async def mcp_options():
        return JSONResponse(content={})

    @app.options("/")
    async def root_options():
        return JSONResponse(content={})


# --------------------------------------------------------------------- helpers


def _res_obj(request_id, result: Dict[str, Any]) -> Dict[str, Any]:
    """A JSON-RPC result object (plain dict, for single or batch responses)."""
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _err_obj(request_id, code: int, message: str, data: str = None) -> Dict[str, Any]:
    """A JSON-RPC error object (plain dict, for single or batch responses)."""
    error: Dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _jsonrpc_error(request_id, code: int, message: str, data: str = None, status_code: int = 200) -> JSONResponse:
    return JSONResponse(content=_err_obj(request_id, code, message, data), status_code=status_code)
