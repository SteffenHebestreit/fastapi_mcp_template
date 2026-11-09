"""
File to Markdown Converter Tool

Converts various file formats (PDF, Office docs, images, HTML, ...) to
LLM-ready Markdown using the MarkItDown library, with an OCR fallback for
scanned PDFs and image files.

Extraction pipeline for scanned/image content. Two engines are available:
  * Traditional OCR (pytesseract, then easyocr) — fast, free, no LLM needed.
  * LLM vision — an OpenAI-compatible vision model reads the page image; the
    best quality for handwriting, tables, poor scans and complex layouts.

Which one reads a page first is set by ``ocr_strategy``:
  * ``vision_first`` (default): the vision model, when the LLM integration is
    enabled; traditional OCR is the fallback if the model returns nothing,
    fails, or the per-request vision-call cap is reached.
  * ``ocr_first``: traditional OCR; the vision model is only asked for pages
    where OCR yields too little text, and the richer of the two results wins.
Without an LLM, traditional OCR is used either way.

The tool is self-contained (no imports from the core service) and reads its
configuration from ``tool_base.config``.
"""

import io
import re
import errno
import base64
import socket
import asyncio
import hashlib
import binascii
import ipaddress
from collections import OrderedDict
from urllib.parse import urlparse, unquote
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path

try:
    from markitdown import MarkItDown
except ImportError:
    MarkItDown = None


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp"}
# Below this many characters, MarkItDown output for a PDF/image is treated as a
# failed extraction and the OCR fallback is attempted.
MIN_USEFUL_CHARS = 16

# Approximate LLM vision pricing, USD per 1M tokens (prompt, completion).
VISION_PRICING = {
    "gpt-4o": (2.5, 10.0),
    "gpt-4o-mini": (0.15, 0.6),
    "gpt-4-turbo": (10.0, 30.0),
}
DEFAULT_VISION_PRICING = (2.5, 10.0)


class ToolDefinition:
    """Definition structure for a tool."""

    def __init__(self, name: str, description: str, endpoint: str, tool_type: str, version: str = "1.0.0"):
        self.name = name
        self.description = description
        self.endpoint = endpoint
        self.tool_type = tool_type
        self.version = version

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "endpoint": self.endpoint,
            "type": self.tool_type,
            "version": self.version,
        }


class ToolSchema:
    """Schema definition for tool parameters."""

    def __init__(self, properties: Dict[str, Any], required: list = None):
        self.properties = properties
        self.required = required or []

    def to_dict(self) -> Dict[str, Any]:
        return {"type": "object", "properties": self.properties, "required": self.required}


class ToolInterface:
    """Interface that tools must implement."""

    def get_definition(self) -> ToolDefinition:
        raise NotImplementedError

    def get_schema(self) -> ToolSchema:
        raise NotImplementedError

    async def execute(self, **kwargs) -> Dict[str, Any]:
        raise NotImplementedError


def is_blocked_address(ip_str: str) -> bool:
    """True for any address that is not a public, globally routable unicast address.

    Covers loopback, private, link-local (cloud metadata), carrier-grade NAT
    (100.64.0.0/10, also used by overlay VPNs), multicast and reserved ranges,
    including IPv6 addresses that merely wrap such an IPv4 address.
    """
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True
    if ip.version == 6:
        embedded = ip.ipv4_mapped or ip.sixtofour
        if embedded is not None:
            ip = embedded
    return (
        not ip.is_global
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_unspecified
    )


def make_guarded_resolver(aiohttp):
    """A DNS resolver for aiohttp that refuses names resolving to internal addresses.

    Validating a URL before the request is not enough on its own: the HTTP
    client resolves the host name again when it connects, and a hostile DNS
    server can answer the two lookups differently (DNS rebinding). Checking
    inside the resolver validates the very addresses the connection will use.
    """
    from aiohttp.abc import AbstractResolver

    class GuardedResolver(AbstractResolver):
        def __init__(self):
            self._inner = aiohttp.ThreadedResolver()

        async def resolve(self, host, port=0, family=socket.AF_INET):
            results = await self._inner.resolve(host, port, family)
            for result in results:
                if is_blocked_address(result["host"]):
                    raise OSError(
                        errno.EACCES,
                        f"Blocked request to internal/reserved address ({result['host']})",
                    )
            return results

        async def close(self):
            await self._inner.close()

    return GuardedResolver()


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token) without pulling in a tokenizer dep."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def chunk_markdown(text: str, chunk_size: int, overlap: int = 0) -> List[str]:
    """Split text into ~chunk_size-character chunks on paragraph boundaries.

    Overlap is applied by carrying the tail of the previous chunk forward, which
    helps retrieval keep context across chunk edges.
    """
    if chunk_size <= 0 or len(text) <= chunk_size:
        return [text] if text else []

    # Split into paragraph-sized units, further hard-splitting any single
    # paragraph that is itself larger than chunk_size.
    units: List[str] = []
    for para in re.split(r"\n\s*\n", text):
        if len(para) <= chunk_size:
            units.append(para)
        else:
            for i in range(0, len(para), chunk_size):
                units.append(para[i : i + chunk_size])

    chunks: List[str] = []
    current = ""
    for unit in units:
        if current and len(current) + len(unit) + 2 > chunk_size:
            chunks.append(current.strip())
            current = (current[-overlap:] if overlap else "") + unit
        else:
            current = f"{current}\n\n{unit}" if current else unit
    if current.strip():
        chunks.append(current.strip())
    return chunks


class FileToMarkdownConverter(ToolInterface):
    """Converts various file formats to Markdown, with OCR fallback."""

    def __init__(self, tool_base):
        self.tool_base = tool_base
        self.config = getattr(tool_base, "config", {}) if tool_base else {}
        self.markitdown = None
        self.llm_client = None
        self._easyocr_reader = None  # lazily constructed and reused across requests
        # Content-hash keyed LRU cache so identical files aren't re-OCR'd / re-billed.
        self._cache: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        if MarkItDown:
            self._initialize_markitdown()

    # ------------------------------------------------------------------ setup

    def _cfg(self, key: str, default: Any = None) -> Any:
        return self.config.get(key, default) if isinstance(self.config, dict) else default

    def _log(self, level: str, message: str) -> None:
        if not self.tool_base:
            return
        fn = getattr(self.tool_base, f"log_{level}", None)
        if fn:
            fn(message)

    def _debug(self, message: str) -> None:
        if self._cfg("verbose_debug", False):
            self._log("info", message)

    def _initialize_markitdown(self):
        """Initialize MarkItDown, wiring an OpenAI client only if configured."""
        enable_llm = self._cfg("markitdown_enable_llm", False)
        api_key = self._cfg("openai_api_key")
        base_url = self._cfg("openai_base_url")

        # OpenAI itself needs a key. Self-hosted OpenAI-compatible servers
        # (LM Studio, Ollama, vLLM, ...) are selected by base_url and usually
        # need none, but the client refuses to start without some value.
        if enable_llm and (api_key or base_url):
            try:
                from openai import OpenAI

                client_kwargs = {"api_key": api_key or "not-needed"}
                if base_url:
                    client_kwargs["base_url"] = base_url
                self.llm_client = OpenAI(**client_kwargs)
                self.markitdown = MarkItDown(
                    llm_client=self.llm_client,
                    llm_model=self._cfg("openai_model", "gpt-4o"),
                    enable_plugins=True,
                )
                self._log(
                    "info",
                    f"MarkItDown initialized with LLM vision support "
                    f"(model {self._cfg('openai_model', 'gpt-4o')}, "
                    f"scanned pages: {'vision first' if self._vision_first() else 'OCR first'})",
                )
                return
            except Exception as e:  # noqa: BLE001 - fall back to basic mode
                self.llm_client = None
                self._log("warning", f"LLM setup failed, using basic MarkItDown: {e}")
        elif enable_llm:
            self._log(
                "warning",
                "LLM features are enabled but neither FTMD_OPENAI_API_KEY nor "
                "FTMD_OPENAI_BASE_URL is set; continuing without LLM",
            )

        self.markitdown = MarkItDown(enable_plugins=False)

    # --------------------------------------------------------------- schema

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="file_to_markdown",
            description=(
                "Extract text from a file (PDF, DOCX, XLSX, PPTX, images, HTML, ...) as "
                "LLM-ready Markdown. Provide the file as base64 via 'base64_content' (for "
                "MCP/JSON clients), as a 'url' to fetch, or as a multipart upload via the "
                "REST endpoint. Scanned PDFs and images are read by OCR / a vision model "
                "automatically. Returns markdown plus per-page text, a token estimate, "
                "and optional chunks."
            ),
            endpoint="/convert",
            tool_type="converter",
            version="2.0.0",
        )

    def get_schema(self) -> ToolSchema:
        return ToolSchema(
            properties={
                "filename": {
                    "type": "string",
                    "description": "Original filename with extension (used to pick the converter). "
                                   "Optional when 'url' is given.",
                },
                "url": {
                    "type": "string",
                    "format": "uri",
                    "description": "http(s) URL of a remote document to fetch and extract. "
                                   "Internal/private addresses are blocked.",
                },
                "base64_content": {
                    "type": "string",
                    "description": "Base64-encoded file content. Preferred input for MCP/JSON clients.",
                },
                "content_type": {
                    "type": "string",
                    "description": "Optional MIME type of the file.",
                },
                "chunk_size": {
                    "type": "integer",
                    "description": "If > 0, also return the markdown split into chunks of about this many characters.",
                },
                "chunk_overlap": {
                    "type": "integer",
                    "description": "Character overlap between chunks (default 0).",
                },
            },
            # One of base64_content / url (or a multipart file_content on the REST
            # path) must be supplied; filename is derived from the URL when absent.
            required=[],
        )

    # -------------------------------------------------------------- execute

    async def execute(self, **kwargs) -> Dict[str, Any]:
        if not self.markitdown:
            return self._error(kwargs.get("filename"), "MarkItDown library not available")

        filename = kwargs.get("filename")
        url = kwargs.get("url")
        max_bytes = int(self._cfg("max_upload_bytes", 50 * 1024 * 1024) or 0)

        # Resolve raw bytes from multipart file_content, base64_content, or a URL.
        source = "upload"
        file_content = kwargs.get("file_content")
        base64_content = kwargs.get("base64_content")
        if file_content is None and base64_content:
            source = "base64"
            decoded, err = self._decode_base64(base64_content)
            if err:
                return self._error(filename, err)
            file_content = decoded
        elif file_content is None and not base64_content and url:
            source = "url"
            if not filename:
                filename = self._filename_from_url(url)
            fetched, err = await self._fetch_url(url, max_bytes)
            if err:
                return self._error(filename, err)
            file_content = fetched

        if not filename:
            return self._error(None, "Provide 'filename' (with base64_content/upload) or 'url'")

        if isinstance(file_content, str):
            file_content = file_content.encode("utf-8")
        if not file_content:
            return self._error(filename, "No file content provided (expected base64_content, upload, or url)")

        if max_bytes and len(file_content) > max_bytes:
            return self._error(
                filename,
                f"File too large: {len(file_content)} bytes exceeds limit of {max_bytes} bytes",
            )

        cache_key = self._cache_key(filename, file_content, kwargs)
        cached = self._cache_get(cache_key)
        if cached is not None:
            self._debug(f"Cache hit for {filename}")
            return {**cached, "cached": True}

        try:
            result = await self._convert(filename, file_content, kwargs.get("content_type"), source, kwargs)
            if result.get("success"):
                self._cache_put(cache_key, result)
            return result
        except Exception as e:  # noqa: BLE001
            self._log("error", f"Conversion failed for {filename}: {e}")
            self.tool_base and self.tool_base.record_metric("conversion.error", 1)
            return self._error(filename, f"Conversion failed: {e}")

    def _decode_base64(self, data: str) -> Tuple[Optional[bytes], Optional[str]]:
        """Decode base64, tolerating line wraps and data: URIs but rejecting corruption."""
        cleaned = data.strip()
        if cleaned.startswith("data:") and "," in cleaned:
            cleaned = cleaned.split(",", 1)[1]
        cleaned = re.sub(r"\s+", "", cleaned)
        # Pad to a multiple of 4 (some clients strip trailing '='); validate=True
        # still rejects any non-alphabet characters, so genuine corruption fails.
        missing = len(cleaned) % 4
        if missing:
            cleaned += "=" * (4 - missing)
        try:
            return base64.b64decode(cleaned, validate=True), None
        except (binascii.Error, ValueError) as e:
            return None, f"Invalid base64 content: {e}"

    async def _convert(
        self,
        filename: str,
        file_content: bytes,
        content_type: Optional[str],
        source: str,
        kwargs: Dict[str, Any],
    ) -> Dict[str, Any]:
        ext = Path(filename).suffix.lower()
        warnings: List[str] = []
        pages: List[Dict[str, Any]] = []
        method = "markitdown"
        page_count: Optional[int] = None
        pages_processed: Optional[int] = None
        truncated = False

        # Per-request accounting for paid LLM-vision calls.
        vision_stats = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0, "capped": False}

        # Primary path: MarkItDown (runs off the event loop; it is synchronous).
        markdown = ""
        title = None
        if ext in IMAGE_EXTENSIONS:
            # Images: go straight to OCR; MarkItDown only describes images when an
            # LLM is configured, whereas OCR extracts actual text for free.
            self._debug(f"Image file {filename}: using OCR pipeline")
        else:
            result = await asyncio.to_thread(
                self.markitdown.convert_stream,
                io.BytesIO(file_content),
                file_extension=ext,
                filename=filename,
            )
            markdown = (getattr(result, "markdown", None) or getattr(result, "text_content", None) or "")
            title = getattr(result, "title", None)

        # OCR fallback for scanned PDFs and images, regardless of LLM availability.
        if len((markdown or "").strip()) < MIN_USEFUL_CHARS:
            if ext == ".pdf":
                self._log("info", f"{filename}: MarkItDown yielded little text; running OCR fallback")
                ocr_md, pages, page_count, pages_processed, truncated, method = await self._ocr_pdf(
                    file_content, vision_stats
                )
                if ocr_md.strip():
                    markdown = ocr_md
            elif ext in IMAGE_EXTENSIONS:
                text, used = await self._ocr_image(file_content, 1, vision_stats)
                if text.strip():
                    markdown = text
                    method = used
                    pages = [{"page": 1, "text": text, "method": used}]
                    page_count = pages_processed = 1

        self._record_vision_metrics(vision_stats)
        if vision_stats["capped"]:
            warnings.append(
                f"Vision-call cap reached (vision_max_calls_per_request="
                f"{self._cfg('vision_max_calls_per_request', 0)}); some pages used traditional OCR only."
            )

        markdown = (markdown or "").strip()

        if not markdown:
            warnings.append("No text could be extracted from the document.")
            self.tool_base and self.tool_base.record_metric("conversion.empty", 1)
            return {
                "success": False,
                "error": "No text could be extracted",
                "filename": filename,
                "content_type": content_type,
                "size": len(file_content),
                "warnings": warnings,
                "metadata": self._metadata(method, page_count, pages_processed, truncated, source, 0),
            }

        if truncated:
            warnings.append(
                f"Only the first {pages_processed} of {page_count} pages were OCR-processed "
                f"(limit ocr_max_pages={self._cfg('ocr_max_pages', 50)})."
            )

        metadata = self._metadata(
            method, page_count, pages_processed, truncated, source, estimate_tokens(markdown)
        )
        if vision_stats["calls"]:
            metadata["vision"] = {
                "calls": vision_stats["calls"],
                "prompt_tokens": vision_stats["prompt_tokens"],
                "completion_tokens": vision_stats["completion_tokens"],
                "estimated_cost_usd": round(vision_stats["cost_usd"], 6),
            }

        response: Dict[str, Any] = {
            "success": True,
            "filename": filename,
            "content_type": content_type,
            "size": len(file_content),
            "title": title,
            "markdown": markdown,
            "metadata": metadata,
        }
        if pages:
            response["pages"] = pages
        if warnings:
            response["warnings"] = warnings

        chunk_size = int(kwargs.get("chunk_size") or 0)
        if chunk_size > 0:
            overlap = int(kwargs.get("chunk_overlap") or 0)
            chunks = chunk_markdown(markdown, chunk_size, overlap)
            response["chunks"] = [
                {"index": i, "text": c, "token_estimate": estimate_tokens(c)} for i, c in enumerate(chunks)
            ]

        self.tool_base and self.tool_base.record_metric("conversion.success", 1)
        self._log("info", f"Extracted {len(markdown)} chars from {filename} via {method}")
        return response

    def _metadata(self, method, page_count, pages_processed, truncated, source, tokens) -> Dict[str, Any]:
        return {
            "extraction_method": method,
            "page_count": page_count,
            "pages_processed": pages_processed,
            "truncated": truncated,
            "source": source,
            "token_estimate": tokens,
            # Provenance marker: extracted content is untrusted and may contain
            # instructions aimed at a downstream LLM. Callers should not treat it
            # as trusted/system content.
            "content_trust": "untrusted",
        }

    @staticmethod
    def _error(filename: Optional[str], message: str) -> Dict[str, Any]:
        return {"success": False, "error": message, "filename": filename}

    # ------------------------------------------------------------ remote URL

    @staticmethod
    def _filename_from_url(url: str) -> str:
        path = urlparse(url).path
        name = unquote(Path(path).name) if path else ""
        return name or "download"

    def _host_ok(self, url: str) -> Optional[str]:
        """Return None if the URL is safe to fetch, else an error string (SSRF guard)."""
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return f"Unsupported URL scheme: {parsed.scheme or '(none)'}"
        if not parsed.hostname:
            return "URL has no host"
        if not bool(self._cfg("block_private_urls", True)):
            return None
        try:
            infos = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
        except socket.gaierror as e:
            return f"Could not resolve host: {e}"
        for info in infos:
            ip = info[4][0]
            if is_blocked_address(ip):
                return f"Blocked request to internal/reserved address ({ip})"
        return None

    async def _fetch_url(self, url: str, max_bytes: int) -> Tuple[Optional[bytes], Optional[str]]:
        """Fetch a remote document with SSRF protection, re-validating each redirect hop.

        Kept inline (rather than importing url_fetcher) to preserve the
        self-contained-tool convention.
        """
        try:
            import aiohttp
        except ImportError:
            return None, "aiohttp not available; cannot fetch remote URL"

        from urllib.parse import urljoin

        err = self._host_ok(url)
        if err:
            return None, err

        max_redirects = int(self._cfg("url_max_redirects", 5) or 0)
        limit = max_bytes or 0
        current = url
        try:
            timeout = aiohttp.ClientTimeout(total=int(self._cfg("url_timeout", 30) or 30))
            connector = None
            if bool(self._cfg("block_private_urls", True)):
                # Re-checks the addresses at connect time (DNS rebinding).
                connector = aiohttp.TCPConnector(resolver=make_guarded_resolver(aiohttp))
            async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
                for _ in range(max_redirects + 1):
                    async with session.get(
                        current, headers={"User-Agent": "file-extraction-service/1.0"}, allow_redirects=False
                    ) as resp:
                        if resp.status in (301, 302, 303, 307, 308) and resp.headers.get("Location"):
                            nxt = urljoin(current, resp.headers["Location"])
                            err = self._host_ok(nxt)
                            if err:
                                return None, f"Blocked redirect: {err}"
                            current = nxt
                            continue
                        if resp.status >= 400:
                            return None, f"Upstream returned HTTP {resp.status}"
                        chunks, total = [], 0
                        async for chunk in resp.content.iter_chunked(64 * 1024):
                            total += len(chunk)
                            if limit and total > limit:
                                return None, f"Remote file exceeds size limit of {limit} bytes"
                            chunks.append(chunk)
                        return b"".join(chunks), None
            return None, f"Too many redirects (>{max_redirects})"
        except Exception as e:  # noqa: BLE001
            return None, f"Failed to fetch URL: {e}"

    # ---------------------------------------------------------------- cache

    def _cache_key(self, filename: str, content: bytes, kwargs: Dict[str, Any]) -> str:
        h = hashlib.sha256()
        h.update(content)
        h.update(Path(filename).suffix.lower().encode())
        h.update(str(kwargs.get("chunk_size") or 0).encode())
        h.update(str(kwargs.get("chunk_overlap") or 0).encode())
        return h.hexdigest()

    def _cache_get(self, key: str) -> Optional[Dict[str, Any]]:
        if not self._cfg("cache_enabled", True):
            return None
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        return None

    def _cache_put(self, key: str, value: Dict[str, Any]) -> None:
        if not self._cfg("cache_enabled", True):
            return
        max_entries = int(self._cfg("cache_max_entries", 128) or 0)
        self._cache[key] = value
        self._cache.move_to_end(key)
        while max_entries and len(self._cache) > max_entries:
            self._cache.popitem(last=False)

    # ------------------------------------------------------------------ OCR

    async def _ocr_pdf(self, pdf_content: bytes, vision_stats: Dict[str, Any]):
        """OCR a PDF page by page. Returns (markdown, pages, page_count, processed, truncated, method)."""
        try:
            import pymupdf as fitz  # PyMuPDF >= 1.24.3
        except ImportError:
            try:
                import fitz  # the module name in older PyMuPDF releases
            except ImportError:
                self._log("error", "PyMuPDF not available - cannot OCR scanned PDF")
                return "", [], None, None, False, "none"

        zoom = float(self._cfg("ocr_zoom", 2.0) or 2.0)
        max_pages_cfg = int(self._cfg("ocr_max_pages", 50) or 0)

        pdf_doc = fitz.open(stream=pdf_content, filetype="pdf")
        try:
            page_count = pdf_doc.page_count
            limit = page_count if max_pages_cfg <= 0 else min(max_pages_cfg, page_count)
            truncated = limit < page_count

            pages: List[Dict[str, Any]] = []
            parts: List[str] = []
            methods = set()
            for page_num in range(limit):
                page = pdf_doc[page_num]
                pix = await asyncio.to_thread(page.get_pixmap, matrix=fitz.Matrix(zoom, zoom))
                img_data = pix.tobytes("png")
                text, method = await self._ocr_image(img_data, page_num + 1, vision_stats)
                if text.strip():
                    methods.add(method)
                    parts.append(f"# Page {page_num + 1}\n\n{text.strip()}\n")
                    pages.append({"page": page_num + 1, "text": text.strip(), "method": method})
                else:
                    self._log("warning", f"No text extracted from page {page_num + 1}")
        finally:
            pdf_doc.close()

        # "vision" / "ocr" when every page was read by one engine, else "ocr+vision".
        overall = methods.pop() if len(methods) == 1 else ("ocr+vision" if methods else "ocr")
        return "\n".join(parts), pages, page_count, limit, truncated, overall

    def _vision_first(self) -> bool:
        """Whether scanned pages go to the vision LLM before traditional OCR."""
        strategy = str(self._cfg("ocr_strategy", "vision_first") or "vision_first").strip().lower()
        return bool(self.llm_client) and strategy != "ocr_first"

    async def _vision_within_cap(self, img_data: bytes, page_num: int, vision_stats: Dict[str, Any]) -> str:
        """Run vision OCR unless the per-request vision-call cap is exhausted."""
        max_calls = int(self._cfg("vision_max_calls_per_request", 0) or 0)
        if max_calls and vision_stats["calls"] >= max_calls:
            vision_stats["capped"] = True
            return ""
        return await self._vision_ocr(img_data, page_num, vision_stats)

    async def _ocr_image(self, img_data: bytes, page_num: int, vision_stats: Dict[str, Any]) -> Tuple[str, str]:
        """Extract text from a single page image. Returns (text, method).

        See the module docstring for the two strategies.
        """
        if self._vision_first():
            vision_text = (await self._vision_within_cap(img_data, page_num, vision_stats)).strip()
            if vision_text:
                return vision_text, "vision"
            # The model returned nothing, failed, or the cap was reached.
            return (await self._traditional_ocr(img_data, page_num)).strip(), "ocr"

        # ocr_first (or no LLM configured): traditional OCR reads the page.
        ocr_text = await self._traditional_ocr(img_data, page_num)
        if len(ocr_text.strip()) > 50:
            return ocr_text.strip(), "ocr"

        # Too little text: ask the vision model, if there is one.
        vision_text = ""
        if self.llm_client:
            vision_text = await self._vision_within_cap(img_data, page_num, vision_stats)

        # Pick the richer result.
        if vision_text.strip() and ocr_text.strip():
            if len(vision_text.strip()) > len(ocr_text.strip()):
                return vision_text.strip(), "ocr+vision"
            return ocr_text.strip(), "ocr+vision"
        if vision_text.strip():
            return vision_text.strip(), "vision"
        return ocr_text.strip(), "ocr"

    async def _traditional_ocr(self, img_data: bytes, page_num: int) -> str:
        """pytesseract, falling back to easyocr. Runs off the event loop."""
        lang = str(self._cfg("ocr_languages", "eng") or "eng")
        try:
            import pytesseract
            from PIL import Image

            def _run():
                image = Image.open(io.BytesIO(img_data))
                return pytesseract.image_to_string(image, lang=lang, config="--psm 6")

            return await asyncio.to_thread(_run)
        except ImportError:
            pass
        except Exception as e:  # noqa: BLE001
            self._log("error", f"pytesseract failed on page {page_num}: {e}")

        try:
            import numpy as np
            from PIL import Image

            def _run_easy():
                reader = self._get_easyocr_reader(lang)
                if reader is None:
                    return ""
                image = np.array(Image.open(io.BytesIO(img_data)))
                results = reader.readtext(image)
                return "\n".join(r[1] for r in results)

            return await asyncio.to_thread(_run_easy)
        except ImportError:
            self._log("warning", "Neither pytesseract nor easyocr available for OCR")
            return ""
        except Exception as e:  # noqa: BLE001
            self._log("error", f"easyocr failed on page {page_num}: {e}")
            return ""

    # Map common tesseract 3-letter codes to EasyOCR 2-letter codes.
    _EASYOCR_LANG_MAP = {"eng": "en", "deu": "de", "fra": "fr", "spa": "es", "ita": "it", "por": "pt"}

    def _get_easyocr_reader(self, lang: str):
        """Construct the EasyOCR reader once and reuse it (model load is expensive)."""
        if self._easyocr_reader is not None:
            return self._easyocr_reader
        try:
            import easyocr

            codes = []
            for token in re.split(r"[+,\s]+", lang):
                if not token:
                    continue
                codes.append(self._EASYOCR_LANG_MAP.get(token, token[:2]))
            self._easyocr_reader = easyocr.Reader(codes or ["en"])
        except Exception as e:  # noqa: BLE001
            self._log("error", f"Failed to initialize EasyOCR: {e}")
            self._easyocr_reader = None
        return self._easyocr_reader

    async def _vision_ocr(self, img_data: bytes, page_num: int, vision_stats: Dict[str, Any]) -> str:
        """Extract text via an LLM vision model. Runs off the event loop."""
        try:
            img_b64 = base64.b64encode(img_data).decode()
            model = self._cfg("openai_model", "gpt-4o")
            max_tokens = int(self._cfg("vision_max_tokens", 4000) or 4000)

            def _run():
                return self.llm_client.chat.completions.create(
                    model=model,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "Extract all text from this image. Return only the text "
                                            "content, preserving reading order and structure.",
                                },
                                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                            ],
                        }
                    ],
                    max_tokens=max_tokens,
                )

            response = await asyncio.to_thread(_run)
            self._account_vision(response, model, vision_stats)
            choice = response.choices[0]
            if getattr(choice, "finish_reason", None) == "length":
                self._log("warning", f"Vision output truncated at {max_tokens} tokens on page {page_num}")
            return choice.message.content or ""
        except Exception as e:  # noqa: BLE001
            self._log("error", f"Vision OCR failed on page {page_num}: {e}")
            return ""

    @staticmethod
    def _account_vision(response, model: str, vision_stats: Dict[str, Any]) -> None:
        """Update per-request vision usage/cost from an OpenAI response."""
        usage = getattr(response, "usage", None)
        prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion = int(getattr(usage, "completion_tokens", 0) or 0)
        in_price, out_price = VISION_PRICING.get(model, DEFAULT_VISION_PRICING)
        vision_stats["calls"] += 1
        vision_stats["prompt_tokens"] += prompt
        vision_stats["completion_tokens"] += completion
        vision_stats["cost_usd"] += (prompt / 1_000_000) * in_price + (completion / 1_000_000) * out_price

    def _record_vision_metrics(self, vision_stats: Dict[str, Any]) -> None:
        """Push per-request vision usage into the global metrics collector."""
        if not self.tool_base or not vision_stats.get("calls"):
            return
        self.tool_base.record_metric("vision.calls", vision_stats["calls"])
        self.tool_base.record_metric("vision.prompt_tokens", vision_stats["prompt_tokens"])
        self.tool_base.record_metric("vision.completion_tokens", vision_stats["completion_tokens"])
        self.tool_base.record_metric("vision.cost_usd", vision_stats["cost_usd"])


def setup_tool(tool_base):
    """Setup function called by the core service."""
    return FileToMarkdownConverter(tool_base)
