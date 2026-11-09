"""OCR engine selection (vision LLM vs. traditional OCR) and the SSRF guard.

No real LLM, Tesseract or network is used: the LLM client and the OCR engine
are replaced by stubs, and DNS answers are simulated. Functions without
fixtures, so the in-app test runner can run them too.
"""

import base64
import importlib.util
import socket
import types
from pathlib import Path

import pytest

TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"

LONG_OCR_TEXT = "Traditional OCR text that is clearly longer than fifty characters in total."


def _available(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


needs_markitdown = pytest.mark.skipif(not _available("markitdown"), reason="markitdown not installed")
needs_pymupdf = pytest.mark.skipif(
    not (_available("markitdown") and _available("pymupdf")), reason="markitdown/PyMuPDF not installed"
)
needs_aiohttp = pytest.mark.skipif(not _available("aiohttp"), reason="aiohttp not installed")


class StubToolBase:
    def __init__(self, **config):
        self.config = {"markitdown_enable_llm": False, "cache_enabled": False, **config}
        self.metrics = {}

    def log_info(self, message): pass
    def log_warning(self, message): pass
    def log_error(self, message): pass

    def record_metric(self, name, value):
        self.metrics[name] = self.metrics.get(name, 0) + value


def load_module(name: str):
    spec = importlib.util.spec_from_file_location(f"test_engines_{name}", TOOLS_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeLLM:
    """Stands in for the OpenAI client; records the requests it receives."""

    def __init__(self, text="Text read by the vision model", fail=False):
        self.text = text
        self.fail = fail
        self.calls = []
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("503 Service Unavailable")
        message = types.SimpleNamespace(content=self.text)
        usage = types.SimpleNamespace(prompt_tokens=1000, completion_tokens=200)
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=message, finish_reason="stop")], usage=usage
        )


def make_converter(llm=None, ocr_text=LONG_OCR_TEXT, **config):
    """A file converter whose LLM client and traditional OCR engine are stubs."""
    module = load_module("file_converter")
    tool = module.setup_tool(StubToolBase(**config))
    tool.llm_client = llm
    tool.ocr_calls = []

    async def traditional_ocr(img_data, page_num):
        tool.ocr_calls.append(page_num)
        return ocr_text

    tool._traditional_ocr = traditional_ocr
    return tool


def new_stats():
    return {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0, "capped": False}


def scanned_pdf(pages: int) -> bytes:
    """A PDF whose pages hold only an image, like a scan."""
    import pymupdf

    source = pymupdf.open()
    page = source.new_page()
    page.insert_text((72, 72), "Scanned words")
    png = page.get_pixmap().tobytes("png")
    source.close()

    document = pymupdf.open()
    for _ in range(pages):
        page = document.new_page()
        page.insert_image(page.rect, stream=png)
    data = document.tobytes()
    document.close()
    return data


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


# ------------------------------------------------------------- OCR strategy

@needs_markitdown
async def test_vision_model_reads_pages_first_by_default():
    llm = FakeLLM("Invoice 2024-117, total 1.234,56 EUR")
    tool = make_converter(llm=llm, openai_model="my-vision-model")

    text, method = await tool._ocr_image(b"png-bytes", 1, new_stats())

    assert (text, method) == ("Invoice 2024-117, total 1.234,56 EUR", "vision")
    assert tool.ocr_calls == []  # Tesseract was not needed
    (call,) = llm.calls
    assert call["model"] == "my-vision-model"
    assert call["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


@needs_markitdown
async def test_traditional_ocr_is_the_fallback_when_vision_yields_nothing():
    for llm in (FakeLLM(text=""), FakeLLM(text="   \n"), FakeLLM(fail=True)):
        tool = make_converter(llm=llm)

        text, method = await tool._ocr_image(b"png-bytes", 1, new_stats())

        assert (text, method) == (LONG_OCR_TEXT, "ocr")
        assert len(llm.calls) == 1 and tool.ocr_calls == [1]


@needs_markitdown
async def test_without_llm_traditional_ocr_reads_the_page():
    for strategy in ("vision_first", "ocr_first"):
        tool = make_converter(llm=None, ocr_strategy=strategy)

        text, method = await tool._ocr_image(b"png-bytes", 1, new_stats())

        assert (text, method) == (LONG_OCR_TEXT, "ocr")


@needs_markitdown
async def test_ocr_first_strategy_asks_vision_only_for_weak_pages():
    good = make_converter(llm=FakeLLM(), ocr_strategy="ocr_first")
    assert await good._ocr_image(b"png", 1, new_stats()) == (LONG_OCR_TEXT, "ocr")
    assert good.llm_client.calls == []  # OCR found enough text; no paid call

    weak = make_converter(llm=FakeLLM("Handwritten note, read by the model"), ocr_text="x1", ocr_strategy="ocr_first")
    assert await weak._ocr_image(b"png", 1, new_stats()) == ("Handwritten note, read by the model", "ocr+vision")
    assert len(weak.llm_client.calls) == 1


@needs_markitdown
async def test_vision_call_cap_switches_to_traditional_ocr():
    tool = make_converter(llm=FakeLLM(), vision_max_calls_per_request=1)
    stats = new_stats()

    first = await tool._ocr_image(b"png", 1, stats)
    second = await tool._ocr_image(b"png", 2, stats)

    assert first[1] == "vision" and second == (LONG_OCR_TEXT, "ocr")
    assert stats["calls"] == 1 and stats["capped"] is True


@needs_pymupdf
async def test_scanned_pdf_end_to_end_with_vision_first():
    llm = FakeLLM("Page text from the vision model")
    tool = make_converter(llm=llm)

    result = await tool.execute(filename="scan.pdf", base64_content=b64(scanned_pdf(2)))

    assert result["success"] is True
    assert result["markdown"].count("Page text from the vision model") == 2
    assert result["metadata"]["extraction_method"] == "vision"
    assert result["metadata"]["page_count"] == 2
    assert [p["method"] for p in result["pages"]] == ["vision", "vision"]
    assert result["metadata"]["vision"]["calls"] == 2
    assert tool.tool_base.metrics["vision.calls"] == 2


@needs_pymupdf
async def test_scanned_pdf_mixes_engines_when_the_cap_is_reached():
    tool = make_converter(llm=FakeLLM("Vision page"), vision_max_calls_per_request=1)

    result = await tool.execute(filename="scan.pdf", base64_content=b64(scanned_pdf(3)))

    assert [p["method"] for p in result["pages"]] == ["vision", "ocr", "ocr"]
    assert result["metadata"]["extraction_method"] == "ocr+vision"
    assert any("Vision-call cap reached" in w for w in result["warnings"])


@needs_pymupdf
async def test_scanned_pdf_without_llm_uses_ocr_and_needs_no_key():
    tool = make_converter(llm=None)

    result = await tool.execute(filename="scan.pdf", base64_content=b64(scanned_pdf(1)))

    assert result["success"] is True
    assert result["metadata"]["extraction_method"] == "ocr"
    assert LONG_OCR_TEXT in result["markdown"]


@needs_markitdown
def test_llm_client_setup():
    module = load_module("file_converter")

    assert module.setup_tool(StubToolBase(markitdown_enable_llm=False, openai_api_key="sk-x")).llm_client is None
    # Enabled, but nothing says where the model lives: continue without LLM.
    assert module.setup_tool(StubToolBase(markitdown_enable_llm=True)).llm_client is None

    if not _available("openai"):
        pytest.skip("openai not installed")

    hosted = module.setup_tool(StubToolBase(markitdown_enable_llm=True, openai_api_key="sk-test"))
    assert hosted.llm_client is not None and hosted.llm_client.api_key == "sk-test"

    # Self-hosted OpenAI-compatible servers need an endpoint but no API key.
    local = module.setup_tool(StubToolBase(
        markitdown_enable_llm=True, openai_base_url="http://localhost:1234/v1", openai_model="local-vision",
    ))
    assert local.llm_client is not None
    assert str(local.llm_client.base_url).startswith("http://localhost:1234/v1")
    assert local._vision_first() is True

    ocr_first = module.setup_tool(StubToolBase(
        markitdown_enable_llm=True, openai_api_key="sk-test", ocr_strategy="ocr_first",
    ))
    assert ocr_first._vision_first() is False


# -------------------------------------------------------------- SSRF guard

BLOCKED_ADDRESSES = (
    "127.0.0.1", "10.0.0.5", "172.16.0.1", "192.168.1.10", "0.0.0.0",
    "169.254.169.254",        # cloud metadata endpoint
    "100.64.0.1",             # carrier-grade NAT, also used by overlay VPNs
    "224.0.0.1", "255.255.255.255",
    "::1", "::", "fe80::1", "fd12:3456::1",
    "::ffff:127.0.0.1", "::ffff:10.0.0.1",   # IPv4-mapped
    "2002:7f00:1::",                         # 6to4 wrapping 127.0.0.1
    "not-an-address",
)
PUBLIC_ADDRESSES = ("93.184.216.34", "8.8.8.8", "1.1.1.1", "2606:4700:4700::1111")


def test_address_classification():
    file_converter = load_module("file_converter")
    url_fetcher = load_module("url_fetcher")
    for check in (file_converter.is_blocked_address, url_fetcher._ip_is_blocked):
        for address in BLOCKED_ADDRESSES:
            assert check(address) is True, f"{address} must be blocked"
        for address in PUBLIC_ADDRESSES:
            assert check(address) is False, f"{address} must be allowed"


def test_url_validation_blocks_address_literals():
    url_fetcher = load_module("url_fetcher")
    for url in ("http://100.64.0.1/", "http://[::ffff:127.0.0.1]/", "http://[fd00::1]/", "http://0.0.0.0/"):
        ok, err = url_fetcher.validate_url(url, block_private=True)
        assert ok is False and "internal" in err, url


class _Rebinding:
    """Simulates DNS rebinding: 'rebind.test' is public on the first lookup
    (the one the URL check makes) and 127.0.0.1 on every later one (the one
    the HTTP client makes when it connects)."""

    def __init__(self):
        self.lookups = 0
        self._real = socket.getaddrinfo

    def __enter__(self):
        def fake(host, port, *args, **kwargs):
            if host != "rebind.test":
                return self._real(host, port, *args, **kwargs)
            self.lookups += 1
            ip = "93.184.216.34" if self.lookups == 1 else "127.0.0.1"
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

        socket.getaddrinfo = fake
        return self

    def __exit__(self, *exc):
        socket.getaddrinfo = self._real


async def _local_server():
    """An HTTP server on 127.0.0.1 that counts the requests reaching it."""
    from aiohttp import web
    from aiohttp.test_utils import TestServer

    hits = []

    async def page(request):
        hits.append(request.path)
        return web.Response(text="<html><body><p>internal admin page</p></body></html>", content_type="text/html")

    app = web.Application()
    app.add_routes([web.get("/{tail:.*}", page)])
    server = TestServer(app)
    await server.start_server()
    return server, hits


@needs_aiohttp
async def test_url_fetcher_is_not_fooled_by_dns_rebinding():
    url_fetcher = load_module("url_fetcher")
    tool = url_fetcher.setup_tool(StubToolBase(block_private_urls=True))
    server, hits = await _local_server()
    try:
        with _Rebinding() as dns:
            result = await tool.execute(url=f"http://rebind.test:{server.port}/admin", timeout=5)
        assert dns.lookups >= 2  # checked once, then resolved again to connect
        assert result["success"] is False
        assert "internal" in result["error"]
        assert hits == []  # the request never reached the internal server
    finally:
        await server.close()


@needs_aiohttp
@needs_markitdown
async def test_file_converter_url_ingest_is_not_fooled_by_dns_rebinding():
    tool = make_converter(block_private_urls=True)
    server, hits = await _local_server()
    try:
        with _Rebinding() as dns:
            result = await tool.execute(url=f"http://rebind.test:{server.port}/secret.html")
        assert dns.lookups >= 2
        assert result["success"] is False
        assert "internal" in result["error"]
        assert hits == []
    finally:
        await server.close()


@needs_aiohttp
async def test_private_addresses_can_be_allowed_explicitly():
    url_fetcher = load_module("url_fetcher")
    tool = url_fetcher.setup_tool(StubToolBase(block_private_urls=False))
    server, hits = await _local_server()
    try:
        result = await tool.execute(url=f"http://127.0.0.1:{server.port}/page", convert_to_markdown=False)
        assert result["success"] is True
        assert "internal admin page" in result["content"]
        assert hits == ["/page"]
    finally:
        await server.close()
