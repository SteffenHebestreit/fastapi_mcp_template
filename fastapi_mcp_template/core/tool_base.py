from typing import Any, Dict, Callable, Optional
from fastapi_mcp_template.core.tool_definition import ToolInterface, ToolDefinition, ToolSchema


class ToolBase:
    """Base class for injecting tool dependencies and managing execution context."""
    
    def __init__(self):
        self.config: Dict[str, Any] = {}
        self.logger = None
        self.metrics = None
    
    def set_config(self, config: Dict[str, Any]) -> None:
        """Set tool configuration."""
        self.config = config
    
    def set_logger(self, logger) -> None:
        """Set logger instance."""
        self.logger = logger
    
    def set_metrics(self, metrics) -> None:
        """Set metrics collector."""
        self.metrics = metrics
    
    def get_config(self, key: str, default: Any = None) -> Any:
        """Get configuration value."""
        return self.config.get(key, default)
    
    def log_info(self, message: str) -> None:
        """Log info message."""
        if self.logger:
            self.logger.info(message)
    
    def log_warning(self, message: str) -> None:
        """Log warning message."""
        if self.logger:
            self.logger.warning(message)
    
    def log_error(self, message: str) -> None:
        """Log error message."""
        if self.logger:
            self.logger.error(message)
    
    def record_metric(self, name: str, value: Any) -> None:
        """Record metric."""
        if self.metrics:
            self.metrics.record(name, value)
