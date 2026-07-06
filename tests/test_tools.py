"""Real tests for the extraction tools.

These exercise the actual tool interfaces (execute() with real inputs) rather
than loading the tool and asserting nothing. They run under pytest
(asyncio_mode=auto, see pytest.ini) and are also awaitable by the in-app
TestManager, which detects module-level ``test_*`` coroutine functions.

OCR-specific paths (scanned PDFs / images) require tesseract or PyMuPDF and are
skipped when those aren't installed; the pure-Python paths always run.
"""

import base64
import importlib.util
from pathlib import Path

import pytest

TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"


class StubToolBase:
    """Minimal tool_base with the logging/metrics/config surface tools expect."""

    def __init__(self, config=None):
        self.config = config or {"markitdown_enable_llm": False, "cache_enabled": True}

    def log_info(self, message): pass
    def log_warning(self, message): pass
    def log_error(self, message): pass
    def record_metric(self, name, value): pass


def load_tool(name: str):
    """Load a tool module by path and return a live instance via setup_tool."""
    path = TOOLS_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"test_tool_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, module.setup_tool(StubToolBase())


def _markitdown_available() -> bool:
    return importlib.util.find_spec("markitdown") is not None


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


# --------------------------------------------------------------- file_converter

@pytest.mark.skipif(not _markitdown_available(), reason="markitdown not installed")
async def test_file_converter_extracts_html_to_markdown():
    _, tool = load_tool("file_converter")
    html = b"<html><body><h1>Report</h1><p>Hello <b>world</b>.</p></body></html>"
    result = await tool.execute(filename="doc.html", base64_content=b64(html))
    assert result["success"] is True
    assert "world" in result["markdown"]
    assert result["metadata"]["extraction_method"] == "markitdown"
    assert result["metadata"]["token_estimate"] > 0
    assert result["metadata"]["content_trust"] == "untrusted"


@pytest.mark.skipif(not _markitdown_available(), reason="markitdown not installed")
async def test_file_converter_chunking():
    _, tool = load_tool("file_converter")
    text = ("A sentence. " * 40).encode()
    result = await tool.execute(filename="n.txt", base64_content=b64(text), chunk_size=120, chunk_overlap=10)
    assert result["success"] is True
    assert len(result["chunks"]) > 1
    assert all(c["token_estimate"] > 0 for c in result["chunks"])


@pytest.mark.skipif(not _markitdown_available(), reason="markitdown not installed")
async def test_file_converter_caches_repeated_input():
    _, tool = load_tool("file_converter")
    payload = b64(b"<p>cache me please, this is body text</p>")
    first = await tool.execute(filename="c.html", base64_content=payload)
    second = await tool.execute(filename="c.html", base64_content=payload)
    assert first.get("cached") is None
    assert second.get("cached") is True


async def test_file_converter_rejects_bad_base64():
    _, tool = load_tool("file_converter")
    result = await tool.execute(filename="x.pdf", base64_content="@@@not-base64@@@")
    assert result["success"] is False
    assert "base64" in result["error"].lower()


async def test_file_converter_requires_input():
    _, tool = load_tool("file_converter")
    result = await tool.execute(filename="x.pdf")
    assert result["success"] is False


async def test_file_converter_enforces_size_limit():
    _, tool = load_tool("file_converter")
    tool.config["max_upload_bytes"] = 16
    result = await tool.execute(filename="big.txt", base64_content=b64(b"x" * 100))
    assert result["success"] is False
    assert "too large" in result["error"].lower()


async def test_file_converter_blocks_ssrf_url():
    _, tool = load_tool("file_converter")
    result = await tool.execute(url="http://169.254.169.254/meta.pdf")
    assert result["success"] is False
    assert "internal" in result["error"].lower() or "reserved" in result["error"].lower()


# --------------------------------------------------------------- text_processor

async def test_text_processor_operations():
    _, tool = load_tool("text_processor")
    assert (await tool.execute(text="aBc", operation="uppercase"))["result"] == "ABC"
    assert (await tool.execute(text="aBc", operation="lowercase"))["result"] == "abc"
    cleaned = await tool.execute(text="  a   b  ", operation="clean")
    assert cleaned["result"] == "a b"
    emails = await tool.execute(text="reach me at a@b.com or c@d.org", operation="extract_emails")
    assert "a@b.com" in emails["result"] and "c@d.org" in emails["result"]


async def test_text_processor_unknown_operation():
    _, tool = load_tool("text_processor")
    result = await tool.execute(text="x", operation="nope")
    assert result["success"] is False


async def test_text_processor_remove_html_drops_scripts_and_entities():
    _, tool = load_tool("text_processor")
    html_text = "<style>.a{}</style><p>Hello&nbsp;<b>world</b></p><script>alert(1)</script>"
    result = await tool.execute(text=html_text, operation="remove_html")
    out = result["result"]
    assert "alert" not in out and ".a{}" not in out
    assert "Hello" in out and "world" in out
    assert "&nbsp;" not in out  # entity decoded


async def test_text_processor_extract_emails_precise():
    _, tool = load_tool("text_processor")
    result = await tool.execute(text="ok a@b.com, bad x@y (no tld)", operation="extract_emails")
    assert result["result"] == "a@b.com"


# --------------------------------------------------------------- url_fetcher

def test_url_fetcher_blocks_internal_hosts():
    module, _ = load_tool("url_fetcher")
    for url in ("http://127.0.0.1/", "http://169.254.169.254/", "http://10.0.0.1/", "http://localhost/"):
        ok, err = module.validate_url(url, block_private=True)
        assert ok is False, f"expected {url} to be blocked"


def test_url_fetcher_rejects_non_http_scheme():
    module, _ = load_tool("url_fetcher")
    ok, err = module.validate_url("file:///etc/passwd", block_private=False)
    assert ok is False
    assert "scheme" in err.lower()


# --------------------------------------------------------------- pure utilities

def test_chunk_and_token_utilities():
    module, _ = load_tool("file_converter")
    assert module.estimate_tokens("") == 0
    assert module.estimate_tokens("a" * 400) == 100
    # A single oversized paragraph must still be split.
    chunks = module.chunk_markdown("word " * 500, 200, 20)
    assert len(chunks) > 1
    assert module.chunk_markdown("", 100) == []
