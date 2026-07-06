from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_tools_directory() -> str:
    """Resolve the tools directory relative to the repo root.

    Works both in Docker (/app/tools) and for local runs from the repo root,
    regardless of the current working directory.
    """
    return str(Path(__file__).resolve().parent.parent / "tools")


class Settings(BaseSettings):
    """Application settings.

    All settings are read from the environment with the ``FTMD_`` prefix
    (e.g. ``FTMD_PORT``, ``FTMD_DEBUG``) or from a ``.env`` file.
    """

    model_config = SettingsConfigDict(
        env_prefix="FTMD_",
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
    )

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False
    reload: bool = False

    # Tool loading
    tools_directory: str = _default_tools_directory()
    # Comma-separated allow-list of tool module names to load at runtime.
    # Empty/None means "load every tool found in tools_directory".
    enabled_tools: Optional[str] = None

    # MCP settings
    mcp_server_name: str = "file-extraction-service"
    mcp_server_version: str = "1.0.0"

    # OpenAI settings for MarkItDown / vision OCR
    openai_api_key: Optional[str] = None
    openai_base_url: Optional[str] = None  # For custom endpoints like Azure OpenAI
    openai_model: str = "gpt-4o"
    markitdown_enable_llm: bool = False  # Enable LLM features for image descriptions

    # Security
    # If set, requests to tool-execution and admin endpoints must present this
    # value in the ``X-API-Key`` header. If unset, auth is disabled (dev mode).
    api_key: Optional[str] = None
    # Comma-separated list of allowed CORS origins, or "*" for any.
    cors_origins: str = "*"
    # Credentials + wildcard origin is an invalid combination; only honored when
    # cors_origins is not "*".
    cors_allow_credentials: bool = False

    # Extraction limits
    max_upload_bytes: int = 50 * 1024 * 1024  # 50 MiB
    # Max PDF pages to OCR per request (0 = no limit).
    ocr_max_pages: int = 50
    ocr_zoom: float = 2.0
    # Tesseract language(s), e.g. "eng" or "eng+deu". EasyOCR uses the first 2-letter code.
    ocr_languages: str = "eng"
    # Cap on vision tokens per page.
    vision_max_tokens: int = 4000
    # Max paid LLM-vision calls per extraction request (0 = no cap; still bounded by ocr_max_pages).
    vision_max_calls_per_request: int = 0

    # Metrics
    metrics_enabled: bool = True

    # Background jobs (in-process; not durable across restarts)
    job_max_concurrent: int = 4
    job_max_entries: int = 1000
    job_ttl_seconds: int = 3600

    # URL fetching / SSRF protection
    block_private_urls: bool = True
    url_max_redirects: int = 5
    url_max_bytes: int = 10 * 1024 * 1024  # 10 MiB

    # Result caching (content-hash keyed)
    cache_enabled: bool = True
    cache_max_entries: int = 128

    # Logging
    log_level: str = "INFO"
    # Emit verbose per-request debug logs (may include document content). Off by default.
    verbose_debug: bool = False


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """Get application settings (singleton)."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
