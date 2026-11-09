"""MCP transport behaviour and HTTP-level protections.

Each test builds its own app with explicit settings (create_app(settings)), so
the outcome does not depend on the environment the suite runs in. Plain
synchronous functions without fixtures, so the in-app test runner can run
them too.
"""

import json
import tempfile
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient

from fastapi_mcp_template.api.routes import LATEST_PROTOCOL_VERSION, SUPPORTED_PROTOCOL_VERSIONS
from fastapi_mcp_template.config import Settings
from fastapi_mcp_template.main import create_app

ECHO_TOOL = '''
class _Definition:
    name = "echo"
    description = "Returns its arguments"

    def to_dict(self):
        return {"name": self.name, "description": self.description,
                "endpoint": "/echo", "type": "test", "version": "1.0.0"}


class _Schema:
    def to_dict(self):
        return {"type": "object", "properties": {}, "required": []}


class Echo:
    def __init__(self, tool_base):
        self.tool_base = tool_base

    def get_definition(self):
        return _Definition()

    def get_schema(self):
        return _Schema()

    async def execute(self, **kwargs):
        return {"success": True, "received": kwargs}


def setup_tool(tool_base):
    return Echo(tool_base)
'''


@contextmanager
def client(**overrides):
    """A TestClient for an isolated app; explicit values win over FTMD_* variables."""
    values = dict(
        api_key=None, cors_origins="*", debug=False, enabled_tools="text_processor",
        markitdown_enable_llm=False, max_request_bytes=0,
    )
    values.update(overrides)
    with TestClient(create_app(Settings(_env_file=None, **values))) as c:
        yield c


def initialize(c, version=LATEST_PROTOCOL_VERSION, headers=None):
    r = c.post("/mcp", headers=headers or {}, json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": version, "capabilities": {},
                   "clientInfo": {"name": "tests", "version": "1"}},
    })
    return r, {"Mcp-Session-Id": r.headers.get("mcp-session-id", "")}


def rpc(c, headers, method, params=None, request_id=2):
    message = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return c.post("/mcp", json=message, headers=headers)


def call_tool(c, headers, name, arguments):
    return rpc(c, headers, "tools/call", {"name": name, "arguments": arguments})


# ------------------------------------------------------------------ lifecycle

def test_protocol_version_negotiation():
    with client() as c:
        for version in SUPPORTED_PROTOCOL_VERSIONS:
            r, _ = initialize(c, version)
            assert r.json()["result"]["protocolVersion"] == version  # echoed when supported

        # Anything else is answered with the newest revision the server speaks,
        # including the stateless 2026-07-28 revision, which is not implemented.
        for version in ("2026-07-28", "1999-01-01", None):
            r, _ = initialize(c, version)
            assert r.json()["result"]["protocolVersion"] == LATEST_PROTOCOL_VERSION == "2025-11-25"


def test_server_info_reports_package_version():
    from fastapi_mcp_template.__about__ import __version__

    with client() as c:
        r, _ = initialize(c)
        assert r.json()["result"]["serverInfo"]["version"] == __version__
        assert c.get("/openapi.json").json()["info"]["version"] == __version__


def test_stateless_client_probe_falls_back_to_handshake():
    # Current SDK clients first probe "server/discover" without a session and
    # fall back to initialize when that is rejected.
    with client() as c:
        probe = rpc(c, {"MCP-Protocol-Version": "2026-07-28"}, "server/discover")
        assert probe.status_code == 400 and "error" in probe.json()

        r, headers = initialize(c)
        assert r.status_code == 200
        assert "tools" in rpc(c, headers, "tools/list").json()["result"]


def test_unsupported_protocol_version_header_is_rejected():
    with client() as c:
        _, headers = initialize(c)
        assert rpc(c, {**headers, "MCP-Protocol-Version": "1999-01-01"}, "ping").status_code == 400
        assert rpc(c, {**headers, "MCP-Protocol-Version": "2025-06-18"}, "ping").status_code == 200


def test_notifications_get_202_with_empty_body():
    with client() as c:
        _, headers = initialize(c)
        for method in ("notifications/initialized", "notifications/cancelled"):
            r = c.post("/mcp", json={"jsonrpc": "2.0", "method": method}, headers=headers)
            assert r.status_code == 202
            assert r.content == b""  # not the JSON text "null"


# ------------------------------------------------------------------- sessions

def test_missing_and_unknown_sessions_use_http_status_codes():
    with client() as c:
        missing = rpc(c, {}, "tools/list")
        assert missing.status_code == 400
        assert missing.json()["error"]["code"] == -32002

        # 404 is the signal on which MCP clients start a new session. A JSON-RPC
        # error on a 200 left them stuck after every server restart.
        unknown = rpc(c, {"Mcp-Session-Id": "does-not-exist"}, "tools/list")
        assert unknown.status_code == 404
        assert unknown.json()["error"]["code"] == -32002


def test_session_stays_alive_while_used_and_expires_when_idle():
    with client(session_ttl_seconds=3600) as c:
        _, headers = initialize(c)
        store = c.app.state.sessions
        now = [1000.0]
        store._clock = lambda: now[0]
        assert rpc(c, headers, "ping").status_code == 200  # stamps last use with the fake clock

        # Three hours of regular use: the session used to die one hour after creation.
        for _ in range(6):
            now[0] += 1800
            assert rpc(c, headers, "ping").status_code == 200

        now[0] += 3601  # ... but an hour without any request ends it
        assert rpc(c, headers, "ping").status_code == 404


def test_delete_terminates_session():
    with client() as c:
        _, headers = initialize(c)
        assert c.delete("/mcp", headers=headers).status_code == 200
        assert rpc(c, headers, "ping").status_code == 404
        assert c.delete("/mcp", headers=headers).status_code == 404
        assert c.delete("/mcp").status_code == 400


def test_session_count_is_bounded():
    with client(max_sessions=5) as c:
        for _ in range(30):
            c.get("/mcp/session")
        assert len(c.app.state.sessions) == 5


# ----------------------------------------------------------------- tool calls

def test_failed_tool_result_sets_is_error():
    with client() as c:
        _, headers = initialize(c)
        ok = call_tool(c, headers, "text_processor", {"text": "x", "operation": "uppercase"})
        assert ok.json()["result"]["isError"] is False

        failed = call_tool(c, headers, "text_processor", {"text": "x", "operation": "bogus"})
        result = failed.json()["result"]
        assert result["isError"] is True
        assert json.loads(result["content"][0]["text"])["success"] is False

        # Missing required arguments are reported to the model the same way.
        missing = call_tool(c, headers, "text_processor", {})
        assert missing.json()["result"]["isError"] is True


def test_tool_result_keeps_non_ascii_text_readable():
    with client() as c:
        _, headers = initialize(c)
        r = call_tool(c, headers, "text_processor", {"text": "Grüße aus Köln", "operation": "uppercase"})
        text = r.json()["result"]["content"][0]["text"]
        assert "GRÜSSE AUS KÖLN" in text  # not "GR\\u00dcSSE ..."
        assert json.loads(text)["result"] == "GRÜSSE AUS KÖLN"


def test_unknown_tool_is_a_protocol_error():
    with client() as c:
        _, headers = initialize(c)
        r = call_tool(c, headers, "no_such_tool", {})
        assert r.json()["error"] == {"code": -32602, "message": "Unknown tool: no_such_tool"}

        # A tool that exists on disk but is not enabled is just as unknown.
        r = call_tool(c, headers, "url_fetcher", {"url": "https://example.com"})
        assert r.json()["error"]["code"] == -32602

        for params in ({"arguments": {}}, {"name": "text_processor", "arguments": [1, 2]}):
            assert rpc(c, headers, "tools/call", params).json()["error"]["code"] == -32602


def test_tool_arguments_are_passed_through_verbatim():
    with tempfile.TemporaryDirectory() as tools_dir:
        Path(tools_dir, "echo.py").write_text(ECHO_TOOL, encoding="utf-8")
        with client(tools_directory=tools_dir, enabled_tools=None) as c:
            _, headers = initialize(c)
            arguments = {"tool_name": "not-the-tool", "nested": {"a": [1, 2]}, "flag": True}
            r = call_tool(c, headers, "echo", arguments)
            assert json.loads(r.json()["result"]["content"][0]["text"])["received"] == arguments


# ----------------------------------------------------------------------- CORS

def test_cors_preflight_allows_what_mcp_clients_send():
    with client() as c:
        preflight = c.options("/mcp", headers={
            "Origin": "http://localhost:6274",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers":
                "content-type, accept, authorization, mcp-session-id, mcp-protocol-version, x-api-key",
        })
        assert preflight.status_code == 200

        delete = c.options("/mcp", headers={
            "Origin": "http://localhost:6274", "Access-Control-Request-Method": "DELETE",
        })
        assert delete.status_code == 200

        r, _ = initialize(c, headers={"Origin": "http://localhost:6274"})
        assert "mcp-session-id" in r.headers["access-control-expose-headers"].lower()


def test_origin_allow_list_blocks_other_origins():
    with client(cors_origins="http://localhost:3000") as c:
        assert c.get("/tools", headers={"Origin": "http://localhost:3000"}).status_code == 200
        assert c.get("/tools", headers={"Origin": "http://evil.example"}).status_code == 403
        assert initialize(c, headers={"Origin": "http://evil.example"})[0].status_code == 403
        assert c.get("/tools").status_code == 200  # non-browser clients send no Origin
        assert c.get("/health", headers={"Origin": "http://evil.example"}).status_code == 200

    with client(cors_origins="*") as c:
        assert c.get("/tools", headers={"Origin": "http://anything.example"}).status_code == 200


# ------------------------------------------------------------------ body size

def test_request_body_limit():
    payload = {"text": "a" * 5000, "operation": "uppercase"}
    with client(max_request_bytes=1000) as c:
        assert c.post("/mcp/tools/text_processor", json={"text": "a", "operation": "uppercase"}).status_code == 200
        assert c.post("/mcp/tools/text_processor", json=payload).status_code == 413
        assert c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "pad": "x" * 5000}).status_code == 413
        assert c.post("/api/tools/text_processor", files={"file": ("big.bin", b"x" * 5000)}).status_code == 413

        def chunks():  # no Content-Length: the limit must still hold
            yield b'{"text": "'
            for _ in range(50):
                yield b"a" * 100
            yield b'", "operation": "uppercase"}'

        r = c.post("/mcp/tools/text_processor", content=chunks(), headers={"Content-Type": "application/json"})
        assert r.status_code == 413


def test_request_body_limit_is_derived_from_upload_limit():
    settings = Settings(_env_file=None, max_upload_bytes=3_000_000, max_request_bytes=0)
    assert settings.effective_max_request_bytes == 4_000_000 + 1024 * 1024  # base64 + envelope
    assert Settings(_env_file=None, max_upload_bytes=0).effective_max_request_bytes == 0
    assert Settings(_env_file=None, max_request_bytes=123).effective_max_request_bytes == 123


# -------------------------------------------------------------------- API key

def test_api_key_is_accepted_as_header_or_bearer_token():
    with client(api_key="s3cret") as c:
        body = {"text": "a", "operation": "uppercase"}
        assert c.post("/mcp/tools/text_processor", json=body).status_code == 401
        assert c.post("/mcp/tools/text_processor", json=body, headers={"X-API-Key": "wrong"}).status_code == 401
        assert c.post("/mcp/tools/text_processor", json=body, headers={"X-API-Key": "s3cret"}).status_code == 200
        assert c.post("/mcp/tools/text_processor", json=body,
                      headers={"Authorization": "Bearer s3cret"}).status_code == 200
        assert c.post("/tools/reload").status_code == 401
        assert c.get("/api/jobs").status_code == 401
        assert c.get("/health").status_code == 200  # container health checks carry no credentials

        # MCP: the handshake is open, everything after needs the key.
        _, headers = initialize(c)
        assert rpc(c, headers, "tools/list").json()["error"]["code"] == -32001
        assert "result" in rpc(c, {**headers, "Authorization": "Bearer s3cret"}, "tools/list").json()
        assert "result" in rpc(c, {**headers, "X-API-Key": "s3cret"}, "tools/list").json()


# ------------------------------------------------------------- app isolation

def test_test_endpoints_exist_only_in_debug_mode():
    with client(debug=False) as c:
        assert c.get("/tests/status").json()["test_support_available"] is False
        assert c.get("/tests").status_code == 404
        assert c.post("/tests/run-all").status_code == 404

    with client(debug=True, api_key="s3cret") as c:
        assert c.get("/tests/status").json()["test_support_available"] is True
        assert c.get("/tests").status_code == 401  # listing imports test modules
        assert c.post("/tests/run-all").status_code == 401


def test_apps_do_not_share_tools_or_sessions():
    with client(enabled_tools="text_processor") as one, client(enabled_tools="url_fetcher") as two:
        assert [t["name"] for t in one.get("/tools").json()["tools"]] == ["text_processor"]
        assert [t["name"] for t in two.get("/tools").json()["tools"]] == ["url_fetcher"]

        _, headers = initialize(one)
        assert rpc(one, headers, "ping").status_code == 200
        assert rpc(two, headers, "ping").status_code == 404
