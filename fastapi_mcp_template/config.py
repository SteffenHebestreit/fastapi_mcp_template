from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    """Application settings."""
    
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = True
    tools_directory: str = "/app/tools"
    
    # MCP settings
    mcp_server_name: str = "fastapi_mcp_template"
    mcp_server_version: str = "1.0.0"
    
    # OpenAI settings for MarkItDown
    openai_api_key: Optional[str] = None
    openai_base_url: Optional[str] = None  # For custom endpoints like Azure OpenAI
    openai_model: str = "gpt-4o"  # Default model for MarkItDown
    markitdown_enable_llm: bool = False  # Enable LLM features for image descriptions
    
    # Logging
    log_level: str = "INFO"
    
    class Config:
        env_prefix = "FTMD_"
        env_file = ".env"


_settings = None


def get_settings() -> Settings:
    """Get application settings (singleton)."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
