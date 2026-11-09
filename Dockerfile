# Use Python 3.11 slim image
FROM python:3.11-slim

# Build arguments for dynamic tool selection and testing
ARG ENABLED_TOOLS=file_converter,url_fetcher,text_processor
ARG ENABLE_TESTS=false

# Set environment variables.
# FTMD_ENABLED_TOOLS makes the server load exactly the tools whose dependencies
# were installed below; override it at runtime to load a subset.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    EXIFTOOL_PATH=/usr/bin/exiftool \
    FFMPEG_PATH=/usr/bin/ffmpeg \
    TOOLS_DIR=/app/tools \
    ENABLED_TOOLS=${ENABLED_TOOLS} \
    FTMD_ENABLED_TOOLS=${ENABLED_TOOLS} \
    ENABLE_TESTS=${ENABLE_TESTS} \
    PYTHONPATH=/app

# Install system dependencies for MarkItDown and other tools.
# For more OCR languages add tesseract-ocr-<lang> packages here and select them
# at runtime with FTMD_OCR_LANGUAGES (e.g. "deu+eng").
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    exiftool \
    libmagic1 \
    curl \
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-fra \
    tesseract-ocr-deu \
    tesseract-ocr-spa \
    && rm -rf /var/lib/apt/lists/*

# Set work directory
WORKDIR /app

# Dependencies are installed from the requirements files alone, before any
# source code is copied. Editing application or tool code then reuses these
# (large) layers instead of reinstalling everything.

# Core dependencies
COPY fastapi_mcp_template/requirements.txt ./fastapi_mcp_template/
RUN pip install --no-cache-dir -r fastapi_mcp_template/requirements.txt

# Tool dependencies, only for the tools named in ENABLED_TOOLS
COPY tools/requirements/ ./tools/requirements/
RUN for tool in $(echo $ENABLED_TOOLS | sed 's/,/ /g'); do \
        if [ -f "tools/requirements/${tool}.txt" ]; then \
            echo "Installing dependencies for tool: $tool"; \
            pip install --no-cache-dir -r "tools/requirements/${tool}.txt"; \
        fi; \
    done

# Test dependencies if testing is enabled
COPY fastapi_mcp_template/core_test/requirements.txt ./fastapi_mcp_template/core_test/
RUN if [ "$ENABLE_TESTS" = "true" ]; then \
        echo "Installing test dependencies"; \
        pip install --no-cache-dir -r fastapi_mcp_template/core_test/requirements.txt; \
    fi

# Copy application code
COPY fastapi_mcp_template/ ./fastapi_mcp_template/
COPY tools/ ./tools/
COPY tests/ ./tests/
COPY pytest.ini ./

# Create non-root user
RUN useradd --create-home --shell /bin/bash app
USER app

# Expose port
EXPOSE 8000

# Health check (start-period allows time for tool discovery on boot)
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

# Default command. Server config comes from FTMD_* env vars (see .env.example),
# so no CLI flags are passed here.
CMD ["python", "-m", "fastapi_mcp_template.main"]
