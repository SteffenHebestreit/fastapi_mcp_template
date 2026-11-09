"""API-level tests exercising the app end-to-end via TestClient.

Covers the newer surfaces: background jobs, metrics, and MCP protocol behavior
(batch requests, protocol-version negotiation, honest capabilities). These are
plain synchronous tests (TestClient is sync).
"""

import base64
import time

from fastapi.testclient import TestClient

from fastapi_mcp_template.main import app


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def test_health_and_tools():
    with TestClient(app) as c:
        assert c.get("/health").json()["status"] == "healthy"
        names = [t["name"] for t in c.get("/tools").json()["tools"]]
        assert "file_to_markdown" in names


def test_metrics_endpoint_counts_conversions():
    with TestClient(app) as c:
        assert c.get("/metrics").json()["enabled"] is True
        c.post("/mcp/tools/file_to_markdown",
               json={"filename": "m.html", "base64_content": b64(b"<p>metrics body text</p>")})
        counters = c.get("/metrics").json()["counters"]
        assert counters.get("conversion.success", 0) >= 1


def test_background_job_lifecycle():
    with TestClient(app) as c:
        r = c.post("/api/jobs/file_to_markdown",
                   files={"file": ("j.html", b"<p>job body text here</p>", "text/html")})
        assert r.status_code == 202
        job_id = r.json()["job_id"]

        job = {}
        for _ in range(100):
            job = c.get(f"/api/jobs/{job_id}").json()
            if job["status"] in ("completed", "failed"):
                break
            time.sleep(0.02)
        assert job["status"] == "completed"
        assert job["result"]["success"] is True
        assert c.get("/api/jobs/does-not-exist").status_code == 404


def test_mcp_initialize_negotiates_version_and_capabilities():
    with TestClient(app) as c:
        r = c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                 "params": {"protocolVersion": "2025-03-26"}})
        result = r.json()["result"]
        assert result["protocolVersion"] == "2025-03-26"           # echoed when supported
        assert result["capabilities"]["tools"]["listChanged"] is False  # honest: no push stream

        r2 = c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                  "params": {"protocolVersion": "1999-01-01"}})
        assert r2.json()["result"]["protocolVersion"] == "2025-11-25"  # newest supported when unsupported


def test_mcp_batch_request():
    with TestClient(app) as c:
        init = c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        sid = init.headers["Mcp-Session-Id"]
        batch = [
            {"jsonrpc": "2.0", "id": 10, "method": "tools/list", "params": {}},
            {"jsonrpc": "2.0", "id": 11, "method": "ping", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
        ]
        r = c.post("/mcp", json=batch, headers={"Mcp-Session-Id": sid})
        arr = r.json()
        assert isinstance(arr, list)
        assert sorted(x["id"] for x in arr) == [10, 11]  # notification produces no response


def test_mcp_get_not_supported():
    with TestClient(app) as c:
        r = c.get("/mcp")
        assert r.status_code == 405
        assert "POST" in r.headers.get("Allow", "")


def test_mcp_tool_call_returns_json_text():
    import json
    with TestClient(app) as c:
        init = c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        sid = init.headers["Mcp-Session-Id"]
        r = c.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                 "params": {"name": "text_processor",
                                            "arguments": {"text": "hi", "operation": "uppercase"}}},
                   headers={"Mcp-Session-Id": sid})
        text = r.json()["result"]["content"][0]["text"]
        parsed = json.loads(text)  # must be JSON, not a Python repr
        assert parsed["result"] == "HI"
