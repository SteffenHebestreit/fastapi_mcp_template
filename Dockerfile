# Use Python 3.11 slim image
FROM python:3.11-slim

# Build arguments for dynamic tool selection and testing
ARG ENABLED_TOOLS=file_converter,url_fetcher,text_processor
ARG ENABLE_TESTS=false

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    EXIFTOOL_PATH=/usr/bin/exiftool \
    FFMPEG_PATH=/usr/bin/ffmpeg \
    TOOLS_DIR=/app/tools \
    ENABLED_TOOLS=${ENABLED_TOOLS} \
    ENABLE_TESTS=${ENABLE_TESTS}

# Install system dependencies for MarkItDown and other tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    exiftool \
    libmagic1 \
    curl \
    bash \
    && rm -rf /var/lib/apt/lists/*

# Set work directory
WORKDIR /app

# Copy and install core dependencies first
COPY fastapi_mcp_template/requirements.txt ./fastapi_mcp_template/
RUN pip install --no-cache-dir -r fastapi_mcp_template/requirements.txt

# Copy application code
COPY fastapi_mcp_template/ ./fastapi_mcp_template/
COPY tools/ ./tools/
COPY tests/ ./tests/

# Install tool dependencies based on ENABLED_TOOLS environment variable
RUN for tool in $(echo $ENABLED_TOOLS | sed 's/,/ /g'); do \
        if [ -f "tools/requirements/${tool}.txt" ]; then \
            echo "Installing dependencies for tool: $tool"; \
            pip install --no-cache-dir -r "tools/requirements/${tool}.txt"; \
        fi; \
    done

# Install test dependencies if testing is enabled
RUN if [ "$ENABLE_TESTS" = "true" ]; then \
        echo "Installing test dependencies"; \
        pip install --no-cache-dir -r fastapi_mcp_template/core_test/requirements.txt; \
    fi

# Set PYTHONPATH to include the main package directory
ENV PYTHONPATH=/app

# Create non-root user
RUN useradd --create-home --shell /bin/bash app
USER app

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

# Default command
CMD ["python", "fastapi_mcp_template/main.py", "--host", "0.0.0.0", "--port", "8000"]
