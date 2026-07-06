"""
URL Fetcher Tool

Fetches content from URLs for use as LLM context. Fetched HTML is converted to
Markdown by default. Includes SSRF protection: only http/https, and (by default)
requests to private, loopback, link-local, or reserved IP ranges are blocked —
including the cloud metadata endpoint (169.254.169.254).

Self-contained: no imports from the core service.
"""

import socket
import ipaddress
from typing import Dict, Any, List, Optional, Tuple
from urllib.parse import urlparse, urljoin

try:
    import aiohttp
except ImportError:
    aiohttp = None

try:
    from markitdown import MarkItDown
except ImportError:
    MarkItDown = None


SAFE_RESPONSE_HEADERS = {"content-type", "content-length", "last-modified", "etag"}


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


def _ip_is_blocked(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def validate_url(url: str, block_private: bool) -> Tuple[bool, Optional[str]]:
    """Validate scheme and (optionally) that the host does not resolve to an internal IP."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False, f"Unsupported URL scheme: {parsed.scheme or '(none)'} (only http/https allowed)"
    if not parsed.hostname:
        return False, "URL has no host"
    if not block_private:
        return True, None
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as e:
        return False, f"Could not resolve host: {e}"
    for info in infos:
        ip_str = info[4][0]
        if _ip_is_blocked(ip_str):
            return False, f"Blocked request to internal/reserved address ({ip_str})"
    return True, None


class URLFetcher(ToolInterface):
    """Fetch content from URLs, with SSRF protection and optional markdown conversion."""

    def __init__(self, tool_base):
        self.tool_base = tool_base
        self.config = getattr(tool_base, "config", {}) if tool_base else {}
        self._markitdown = MarkItDown(enable_plugins=False) if MarkItDown else None

    def _cfg(self, key: str, default: Any = None) -> Any:
        return self.config.get(key, default) if isinstance(self.config, dict) else default

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="url_fetcher",
            description="Fetch a web page or file from an http(s) URL and return it as LLM-ready "
                        "Markdown (or raw content). Blocks requests to internal/private addresses.",
            endpoint="/fetch",
            tool_type="fetcher",
            version="2.0.0",
        )

    def get_schema(self) -> ToolSchema:
        return ToolSchema(
            properties={
                "url": {"type": "string", "format": "uri", "description": "http(s) URL to fetch."},
                "convert_to_markdown": {
                    "type": "boolean",
                    "default": True,
                    "description": "Convert fetched HTML to Markdown (default true).",
                },
                "timeout": {"type": "integer", "default": 30, "description": "Request timeout in seconds."},
                "user_agent": {"type": "string", "default": "file-extraction-service/1.0"},
            },
            required=["url"],
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        if not aiohttp:
            return {"success": False, "error": "aiohttp library not available"}

        url = kwargs.get("url")
        if not url:
            return {"success": False, "error": "url is required"}

        block_private = bool(self._cfg("block_private_urls", True))
        max_redirects = int(self._cfg("url_max_redirects", 5) or 0)
        max_bytes = int(self._cfg("url_max_bytes", 10 * 1024 * 1024) or 0)
        timeout = kwargs.get("timeout", 30)
        user_agent = kwargs.get("user_agent", "file-extraction-service/1.0")
        convert = kwargs.get("convert_to_markdown", True)

        ok, err = validate_url(url, block_private)
        if not ok:
            self._log_error(err)
            return {"success": False, "error": err, "url": url}

        try:
            status, ctype, body, final_url, safe_headers = await self._fetch(
                url, timeout, user_agent, max_redirects, max_bytes, block_private
            )
        except _FetchError as e:
            self._log_error(str(e))
            return {"success": False, "error": str(e), "url": url}
        except Exception as e:  # noqa: BLE001
            self._log_error(f"Failed to fetch URL {url}: {e}")
            return {"success": False, "error": f"Failed to fetch URL: {e}", "url": url}

        if status >= 400:
            return {
                "success": False,
                "error": f"Upstream returned HTTP {status}",
                "url": final_url,
                "status_code": status,
            }

        result: Dict[str, Any] = {
            "success": True,
            "url": final_url,
            "status_code": status,
            "content_type": ctype,
            "content_length": len(body),
            "headers": safe_headers,
            "content_trust": "untrusted",
        }

        is_text = ctype.startswith("text/") or "html" in ctype or "xml" in ctype or "json" in ctype
        if convert and self._markitdown and ("html" in ctype or ctype.startswith("text/html")):
            try:
                import io

                ext = ".html"
                md = await _to_thread(
                    self._markitdown.convert_stream, io.BytesIO(body), file_extension=ext
                )
                result["markdown"] = (getattr(md, "markdown", None) or "").strip()
                result["format"] = "markdown"
            except Exception as e:  # noqa: BLE001
                self._log_error(f"Markdown conversion failed: {e}")
                result["content"] = _safe_decode(body)
                result["format"] = "text"
        elif is_text:
            result["content"] = _safe_decode(body)
            result["format"] = "text"
        else:
            result["format"] = "binary"
            result["note"] = "Binary content not decoded; use the file converter tool to extract text."

        self._log_info(f"Fetched {final_url} ({status}, {len(body)} bytes)")
        return result

    async def _fetch(self, url, timeout, user_agent, max_redirects, max_bytes, block_private):
        """Fetch with manual redirect handling so every hop is re-validated against SSRF rules."""
        timeout_obj = aiohttp.ClientTimeout(total=timeout)
        headers = {"User-Agent": user_agent}
        current = url
        async with aiohttp.ClientSession(timeout=timeout_obj) as session:
            for _ in range(max_redirects + 1):
                async with session.get(current, headers=headers, allow_redirects=False) as resp:
                    if resp.status in (301, 302, 303, 307, 308) and resp.headers.get("Location"):
                        nxt = urljoin(current, resp.headers["Location"])
                        ok, err = validate_url(nxt, block_private)
                        if not ok:
                            raise _FetchError(f"Blocked redirect: {err}")
                        current = nxt
                        continue

                    body = await _read_capped(resp, max_bytes)
                    ctype = resp.headers.get("content-type", "application/octet-stream").split(";")[0].strip().lower()
                    safe_headers = {k.lower(): v for k, v in resp.headers.items() if k.lower() in SAFE_RESPONSE_HEADERS}
                    return resp.status, ctype, body, str(resp.url), safe_headers
        raise _FetchError(f"Too many redirects (>{max_redirects})")

    def _log_info(self, msg: str) -> None:
        if self.tool_base:
            self.tool_base.log_info(msg)

    def _log_error(self, msg: str) -> None:
        if self.tool_base:
            self.tool_base.log_error(msg)


class _FetchError(Exception):
    pass


async def _read_capped(resp, max_bytes: int) -> bytes:
    if not max_bytes:
        return await resp.read()
    chunks: List[bytes] = []
    total = 0
    async for chunk in resp.content.iter_chunked(64 * 1024):
        total += len(chunk)
        if total > max_bytes:
            raise _FetchError(f"Response exceeds size limit of {max_bytes} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


def _safe_decode(body: bytes) -> str:
    for enc in ("utf-8", "latin-1"):
        try:
            return body.decode(enc)
        except UnicodeDecodeError:
            continue
    return body.decode("utf-8", errors="replace")


async def _to_thread(fn, *args, **kwargs):
    import asyncio

    return await asyncio.to_thread(fn, *args, **kwargs)


def setup_tool(tool_base):
    """Setup function called by the core service."""
    return URLFetcher(tool_base)
