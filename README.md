# File Extraction Service

**Extract text and data from files (PDF, Office docs, images, URLs) as LLM-ready context — over both a REST API and the Model Context Protocol (MCP).**

This service turns documents into clean Markdown you can feed into LLM pipelines
(RAG, chat-with-your-docs, agent context). It handles the messy ingestion layer so
you don't have to: format detection, OCR for scanned pages, structured output with
per-page metadata and token counts, chunking, caching, and SSRF-guarded URL fetching.

It is built on a small dynamic-tool framework (drop a `.py` file in `tools/` and it's
exposed over both interfaces), so you can extend it with your own extractors.

### What it does

- **File → Markdown** via the [MarkItDown](https://github.com/microsoft/markitdown) library: PDF, DOCX, XLSX, PPTX, HTML, images, and more.
- **Scanned PDFs and images** — read by an LLM vision model when one is configured (best quality), with traditional OCR (Tesseract) as the fallback. Without an LLM, Tesseract does the job and needs **no API key**. The order is configurable.
- **LLM-ready structured output** — Markdown plus per-page text, a token estimate, an extraction-method label, and optional character-based chunks with overlap.
- **URL ingestion** — fetch a web page as Markdown, or fetch a remote document and extract it, with protection against requests to internal/private addresses (SSRF).
- **Caching** — identical files are extracted once and served from a content-hash cache (no repeated OCR / vision cost).
- **Dual interface** — the same tools are available as REST endpoints and as MCP tools for LLM clients.

## 🎯 Project Goal

**Provide a reliable ingestion boundary between untrusted documents and downstream LLM context.**

The service is organized as a set of self-contained tools under `tools/`:

- `file_converter` — the core file/URL → Markdown extractor (with OCR).
- `url_fetcher` — fetch a web page/file as Markdown, SSRF-guarded.
- `text_processor` — small text utilities (clean, case, strip HTML, extract emails).

Add your own extractor by dropping a `tools/your_tool.py` (see “Adding Your Own Tools”); it is discovered automatically and exposed on both interfaces.

## 🏗️ Architecture Overview

```
fastapi-mcp-template/
├── fastapi_mcp_template/          # Core MCP server package
│   ├── __about__.py              # Version information
│   ├── __init__.py               # Package initialization
│   ├── config.py                 # Configuration management
│   ├── main.py                   # Application entry point
│   ├── requirements.txt          # Core dependencies only
│   ├── api/                      # HTTP layer
│   │   ├── __init__.py
│   │   ├── routes.py             # MCP endpoint, REST API and job routes
│   │   └── security.py           # Origin allow-list and request size limit
│   ├── core/                     # MCP core functionality
│   │   ├── __init__.py
│   │   ├── jobs.py               # In-process background jobs
│   │   ├── metrics.py            # In-process metrics counters
│   │   ├── session_store.py      # MCP sessions (idle expiry, size cap)
│   │   ├── tool_base.py          # Base tool infrastructure
│   │   ├── tool_definition.py    # Tool interface definitions
│   │   └── tool_manager.py       # Dynamic tool loading
│   └── core_test/                # Core functionality tests + in-app test runner
│       ├── __init__.py
│       ├── requirements.txt      # Test dependencies
│       ├── test_base.py
│       ├── test_core.py
│       └── test_manager.py
├── tools/                        # Self-contained tools (your custom tools go here!)
│   ├── file_converter.py         # Example: File-to-Markdown converter with OCR
│   ├── text_processor.py         # Example: Text processing operations
│   ├── url_fetcher.py            # Example: URL content fetcher
│   └── requirements/             # Tool-specific dependencies
│       ├── file_converter.txt    # Dependencies for file_converter (markitdown, openai, PyMuPDF)
│       ├── text_processor.txt    # Dependencies for text_processor
│       └── url_fetcher.txt       # Dependencies for url_fetcher
├── tests/
│   ├── __init__.py
│   ├── test_api.py               # REST API, jobs, metrics
│   ├── test_mcp_http.py          # MCP protocol, sessions, CORS, API key, limits
│   ├── test_ocr_and_ssrf.py      # OCR engine selection, SSRF guard
│   └── test_tools.py             # Tool behaviour (extraction, chunking, caching)
├── data/                         # Data directory for file storage
├── docker-compose.yml            # Container orchestration
├── Dockerfile                    # Container definition with dynamic deps
├── pytest.ini                    # Test configuration
├── .env.example                  # Environment configuration template
└── README.md                     # This file
```

## 🚀 Key Features

### **Dynamic Tool Loading**
- **Zero Configuration**: Drop Python files in `tools/` and they're automatically discovered
- **Hot Reloading**: Development mode automatically reloads tools when changed
- **Dependency Isolation**: Each tool manages its own dependencies via `requirements/{toolname}.txt`
- **Self-Contained**: Tools have no dependencies on the core package
- **Dynamic Tool Types**: Tools can define any custom type without core modifications

### **Dual Interface Architecture**
- **MCP Protocol**: Native integration with LLM applications (Claude, etc.)
- **REST API**: Standard HTTP endpoints for web applications
- **Unified Tool Access**: Same tools available through both interfaces
- **Interactive Documentation**: Auto-generated API docs at `/docs`

### **Production-Ready Deployment**
- **Docker Support**: Complete containerization with cache-friendly image layers
- **Dynamic Dependencies**: Tool dependencies installed only when tools are enabled
- **Health Checks**: Built-in monitoring and health endpoints
- **Environment Configuration**: Flexible configuration through environment variables

### **Developer Experience**
- **FastAPI Foundation**: Modern Python web framework with automatic validation
- **Type Safety**: Full type hints and Pydantic models
- **Error Handling**: Comprehensive error handling and logging
- **Testing Ready**: Structure supports easy unit and integration testing

## 📋 MCP Protocol Support

The `/mcp` endpoint implements the **tools** part of the Model Context Protocol over a
single HTTP endpoint (Streamable HTTP with JSON responses).

| | |
|---|---|
| Protocol revisions | `2025-11-25`, `2025-06-18`, `2025-03-26`, `2024-11-05`. The client's requested revision is echoed when supported; otherwise the newest one is offered. |
| Methods | `initialize`, `notifications/*`, `tools/list`, `tools/call`, `ping` |
| JSON-RPC | Single messages and batches. Notifications are answered with `202` and no body. |
| Capabilities | `tools` with `listChanged: false` — there is no server→client stream to announce changes on. |
| Not implemented | The stateless `2026-07-28` revision (`server/discover`), SSE streams (`GET /mcp` returns `405`), resources, prompts, OAuth. |

Clients that prefer the stateless 2026-07-28 revision — the current official SDKs in
their default mode — probe with `server/discover`, are rejected, and fall back to the
`initialize` handshake, which works.

### Sessions

`initialize` returns a session ID in the `Mcp-Session-Id` response header; the client
sends it back on every later request.

- A session expires after `FTMD_SESSION_TTL_SECONDS` **without a request** (default 1 hour). A session in regular use does not expire.
- A request without the header gets HTTP `400`; an unknown or expired session gets HTTP `404`. On `404` MCP clients start a new session by themselves — this is also what happens after a server restart, because sessions are kept in memory.
- `DELETE /mcp` with the session header ends a session.
- At most `FTMD_MAX_SESSIONS` sessions are kept (least recently used are dropped first).

A session ID is not a credential. To restrict access, set `FTMD_API_KEY`.

### Tool results

A tool's return value is delivered as one text content block containing **JSON**
(UTF-8, non-ASCII characters are not escaped). `isError` is `true` when the tool
raised an exception **or** returned `{"success": false, ...}`:

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "content": [{"type": "text", "text": "{\"success\": false, \"error\": \"No text could be extracted\", ...}"}],
    "isError": true
  }
}
```

Missing required arguments are reported the same way, so the model can correct its call.

### Errors

| Code | HTTP | Meaning |
|---|---|---|
| `-32700` | 400 | Body is not valid JSON |
| `-32600` | 200 / 400 | Not a JSON-RPC 2.0 message; unsupported `MCP-Protocol-Version` header (400) |
| `-32601` | 200 | Unknown method |
| `-32602` | 200 | Malformed `tools/call` params, or unknown tool |
| `-32603` | 200 | Internal error |
| `-32001` | 200 | API key required (`FTMD_API_KEY` is set) and missing or wrong |
| `-32002` | 400 / 404 | No session header (400), unknown or expired session (404) |

### Browser clients

CORS allows the headers MCP clients send (`Content-Type`, `Accept`, `Authorization`,
`X-API-Key`, `Mcp-Session-Id`, `MCP-Protocol-Version`) and exposes `Mcp-Session-Id`.
Set `FTMD_CORS_ORIGINS` to the origins of your web clients to reject every other
browser origin (HTTP `403`).

### Trying it out

```bash
npx @modelcontextprotocol/inspector
# Transport: "Streamable HTTP", URL: http://localhost:8000/mcp
```

The protocol behaviour is covered by `tests/test_mcp_http.py` and was checked end to end
against the official MCP Python SDK client.

## 🛠️ Example Use Cases

This template can be adapted for any MCP server use case:

- **📄 Document Processing**: Convert, analyze, or transform documents with LLM-enhanced OCR
- **🔗 API Integrations**: Connect to external services and APIs  
- **📊 Data Analysis**: Process and analyze datasets
- **🎨 Content Generation**: Create or transform content with AI assistance
- **🔍 Information Retrieval**: Search and fetch information from various sources
- **🧮 Calculations**: Perform complex calculations or simulations
- **📈 Monitoring**: Gather metrics or monitor systems
- **🖼️ Image Analysis**: Extract text and descriptions from images using vision-capable LLMs

## 📦 Installation & Setup

### Option 1: Docker (Recommended)

**Quick Start - All tools enabled:**

```bash
# Clone the template
git clone https://github.com/SteffenHebestreit/fastapi-mcp-template.git
cd fastapi-mcp-template

# Start with default configuration
docker-compose up --build
```

**Custom tool selection:**

```bash
# Copy environment template
cp .env.example .env

# Edit .env to specify which tools to enable
# ENABLED_TOOLS=file_converter,text_processor

# Build with custom tools
docker-compose up --build
```

**Advanced Docker usage:**

```bash
# Build with specific tools only
docker-compose build --build-arg ENABLED_TOOLS=file_converter,text_processor

# Run in background
docker-compose up -d

# View logs
docker-compose logs -f

# Stop services
docker-compose down
```

### Option 2: Direct Installation

**For development or custom deployments:**

```bash
# 1. Clone and navigate
git clone https://github.com/SteffenHebestreit/fastapi-mcp-template.git
cd fastapi-mcp-template

# 2. (recommended) create a virtualenv
python -m venv .venv && . .venv/Scripts/activate   # Windows
# python -m venv .venv && source .venv/bin/activate  # macOS/Linux

# 3. Install core dependencies
pip install -r fastapi_mcp_template/requirements.txt

# 4. Install tool dependencies (choose what you need)
pip install -r tools/requirements/file_converter.txt  # File conversion + OCR
pip install -r tools/requirements/url_fetcher.txt     # URL fetching
# text_processor has no additional dependencies

# 5. Additional OCR setup (for the traditional OCR fallback)
# Install the Tesseract OCR engine:
# - Windows: https://github.com/UB-Mannheim/tesseract/wiki
# - Ubuntu/Debian: sudo apt install tesseract-ocr
# - macOS: brew install tesseract

# 6. Run the server from the repo root (config comes from FTMD_* env vars / .env — no CLI flags)
python fastapi_mcp_template/main.py

# Custom configuration is via environment variables, e.g.:
FTMD_PORT=8080 FTMD_DEBUG=true python fastapi_mcp_template/main.py
```

## 🔧 Configuration

### Environment Variables

Configure the server behavior through environment variables:

All runtime settings use the **`FTMD_`** prefix (see `.env.example` for the full
list). A stray unprefixed `PORT=...` is ignored — use `FTMD_PORT`.

```bash
# Core
FTMD_HOST=0.0.0.0               # Server host
FTMD_PORT=8000                  # Server port
FTMD_DEBUG=false                # Debug mode (also exposes /tests/* endpoints)
FTMD_RELOAD=false               # uvicorn auto-reload (dev only)
FTMD_LOG_LEVEL=INFO             # Logging level

# Tool loading
FTMD_ENABLED_TOOLS=file_converter,text_processor,url_fetcher  # empty = all
# FTMD_TOOLS_DIRECTORY=/app/tools    # auto-detected; override only if needed

# Security
FTMD_API_KEY=                   # if set, required on tool/admin endpoints (X-API-Key or Bearer token)
FTMD_CORS_ORIGINS=*             # "*" or comma-separated origins; a list also rejects other browser origins

# MCP sessions
FTMD_SESSION_TTL_SECONDS=3600   # idle time before a session expires (0 = never)

# Extraction limits
FTMD_MAX_UPLOAD_BYTES=52428800  # 50 MiB per file
FTMD_MAX_REQUEST_BYTES=0        # largest request body; 0 = derived from the upload limit
FTMD_OCR_MAX_PAGES=50           # 0 = no limit
FTMD_OCR_LANGUAGES=eng          # tesseract lang(s), e.g. deu+eng
FTMD_OCR_STRATEGY=vision_first  # or ocr_first; see "How scanned pages are read"

# URL fetching / SSRF
FTMD_BLOCK_PRIVATE_URLS=true    # block internal/private/metadata addresses
```

> With Docker Compose, a variable reaches the container only if it is listed under
> `environment:` in `docker-compose.yml`. The LLM, OCR and security settings are passed
> through from your `.env`; add a line there for anything else you want to change.

### OpenAI Integration Configuration

Enable OpenAI integration for enhanced image processing and content analysis:

#### Basic OpenAI Setup
```bash
# Enable LLM features for enhanced file conversion
FTMD_MARKITDOWN_ENABLE_LLM=true

# Your OpenAI API key
FTMD_OPENAI_API_KEY=sk-proj-your-api-key-here

# Model to use (default: gpt-4o)
FTMD_OPENAI_MODEL=gpt-4o
```

#### Azure OpenAI Setup
```bash
# Enable LLM features
FTMD_MARKITDOWN_ENABLE_LLM=true

# Azure OpenAI endpoint
FTMD_OPENAI_BASE_URL=https://your-resource.openai.azure.com/

# Azure API key
FTMD_OPENAI_API_KEY=your-azure-api-key

# Model deployment name
FTMD_OPENAI_MODEL=gpt-4o
```

#### Custom OpenAI Compatible Endpoints
```bash
# Enable LLM features
FTMD_MARKITDOWN_ENABLE_LLM=true

# Custom endpoint
FTMD_OPENAI_BASE_URL=https://your-custom-endpoint.com/v1

# API key for custom endpoint
FTMD_OPENAI_API_KEY=your-custom-api-key

# Model name
FTMD_OPENAI_MODEL=your-model-name
```

#### Supported Models
Common OpenAI models that work well with MarkItDown:
- `gpt-4o` (recommended)
- `gpt-4o-mini` (cost-effective)
- `gpt-4-turbo`
- `gpt-4`

#### Self-hosted OpenAI-compatible servers
For LM Studio, Ollama, vLLM and similar, set the endpoint and the model. No API key is needed:

```bash
FTMD_MARKITDOWN_ENABLE_LLM=true
FTMD_OPENAI_BASE_URL=http://host.docker.internal:1234/v1   # the host machine, seen from the container
FTMD_OPENAI_MODEL=your-vision-model
```

The model must accept images (a vision model).

### How scanned pages are read

A PDF that yields no text, and every image file, is read page by page. Two engines exist:

| Engine | Needs | Strengths |
|---|---|---|
| **LLM vision** | `FTMD_MARKITDOWN_ENABLE_LLM=true` and a vision model | Best quality: handwriting, tables, poor scans, complex layouts |
| **Traditional OCR** (Tesseract) | Nothing — bundled in the Docker image | Free, fast, offline; good for clean printed text |

`FTMD_OCR_STRATEGY` decides which one reads a page first:

- **`vision_first`** (default): the vision model reads the page. Tesseract takes over for a page when the model returns nothing, the request fails, or the per-request cap (`FTMD_VISION_MAX_CALLS_PER_REQUEST`) is reached.
- **`ocr_first`**: Tesseract reads the page. The vision model is only asked for pages where Tesseract finds 50 characters or fewer, and the richer of the two results is used. Choose this to keep paid API usage low.

**Without the LLM integration, Tesseract is used either way — OCR needs no API key.**

Related settings:

- `FTMD_OCR_LANGUAGES` — Tesseract language codes joined with `+` (default `eng`). The Docker image ships `eng`, `deu`, `fra` and `spa`; add `tesseract-ocr-<lang>` packages to the Dockerfile for others. For German documents: `FTMD_OCR_LANGUAGES=deu+eng`.
- `FTMD_OCR_MAX_PAGES` — pages read per document (default `50`, `0` = all). The response says so when pages were skipped (`metadata.truncated` and a warning).
- `FTMD_VISION_MAX_TOKENS` — output limit per page for the vision model (default `4000`).

The response tells you what happened: `metadata.extraction_method` is `markitdown`
(text was embedded in the file), `vision`, `ocr`, or `ocr+vision` (both engines were
used); `pages[]` lists the method per page; `metadata.vision` reports the number of
vision calls, token usage and an estimated cost.

**Cost:** with `vision_first`, every scanned page is one vision call. That is free with
a self-hosted model. With a paid API, bound it with `FTMD_OCR_MAX_PAGES` /
`FTMD_VISION_MAX_CALLS_PER_REQUEST`, or use `ocr_first`. Identical files are served
from the cache and are not billed twice.

### Docker Configuration

**docker-compose.yml** supports:
- Tool selection via build args
- Environment file mounting
- Volume mounting for development
- Port configuration
- Health checks

**Dockerfile** features:
- Dependencies are installed before the source is copied, so code changes rebuild in seconds
- Dynamic dependency installation based on enabled tools
- Container runs as a non-root user
- Health check endpoints

## 🎯 Adding Your Own Tools

### Dynamic Tool Type System

The template uses a flexible, dynamic tool type system that allows you to define any custom tool type without modifying core code.

#### How It Works

Tools use simple strings for types instead of hardcoded enums:

```python
# Define any tool type you want
tool_type = "converter"     # Built-in type
tool_type = "analyzer"      # Custom type
tool_type = "my_special_ai" # Your unique type
```

#### Available Built-in Types

For convenience, common tool types are available:

- `"converter"` - File format converters
- `"processor"` - Text/data processors  
- `"fetcher"` - Content/data fetchers
- `"utility"` - General utility tools

#### Custom Types

You can create any custom type:

```python
def get_definition(self):
    return ToolDefinition(
        name="sentiment_analyzer",
        description="AI-powered sentiment analysis",
        endpoint="/sentiment",
        tool_type="ai_analyzer",  # Custom type!
        version="1.0.0"
    )
```

The system automatically discovers and tracks all tool types as tools are loaded.

### Step 1: Create a Tool File

Create a new Python file in the `tools/` directory:

```python
# tools/my_custom_tool.py
from typing import Dict, Any

class ToolInterface:
    """Interface that tools must implement."""
    def get_definition(self): raise NotImplementedError
    def get_schema(self): raise NotImplementedError
    async def execute(self, **kwargs) -> Dict[str, Any]: raise NotImplementedError

class ToolDefinition:
    def __init__(self, name: str, description: str, endpoint: str, tool_type: str, version: str = "1.0.0"):
        self.name = name
        self.description = description
        self.endpoint = endpoint
        self.tool_type = tool_type
        self.version = version

class ToolSchema:
    def __init__(self, properties: Dict[str, Any], required: list = None):
        self.properties = properties
        self.required = required or []

class MyCustomTool(ToolInterface):
    def __init__(self, tool_base):
        self.tool_base = tool_base
    
    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="my_custom_tool",
            description="Description of what my tool does",
            endpoint="/my-endpoint",
            tool_type="my_analyzer",  # Any string you want!
            version="1.0.0"
        )
    
    def get_schema(self) -> ToolSchema:
        return ToolSchema(
            properties={
                "input_param": {
                    "type": "string",
                    "description": "Description of the parameter"
                }
            },
            required=["input_param"]
        )
    
    async def execute(self, **kwargs) -> Dict[str, Any]:
        input_param = kwargs.get("input_param")
        
        try:
            # Your tool logic here
            result = f"Processed: {input_param}"
            
            self.tool_base.log_info(f"Tool executed successfully")
            return {
                "success": True,
                "result": result
            }
        except Exception as e:
            self.tool_base.log_error(f"Tool execution failed: {str(e)}")
            return {
                "success": False,
                "error": str(e)
            }

def setup_tool(tool_base):
    """Setup function called by the core service."""
    return MyCustomTool(tool_base)
```

### Step 2: Add Dependencies (if needed)

If your tool requires additional packages:

```bash
# Create tools/requirements/my_custom_tool.txt
mkdir -p tools/requirements
echo "requests>=2.28.0" > tools/requirements/my_custom_tool.txt
echo "beautifulsoup4>=4.11.0" >> tools/requirements/my_custom_tool.txt
```

### Step 3: Enable Your Tool

Add your tool to the enabled tools list:

```bash
# In .env file or environment
ENABLED_TOOLS=file_converter,text_processor,url_fetcher,my_custom_tool
```

### Step 4: Deploy

```bash
# Rebuild and restart
docker-compose up --build
```

That's it! Your tool is now available through both MCP and REST API interfaces.

## 🌐 API Endpoints

Once running, the server provides multiple interfaces:

### MCP Protocol Endpoints
- `POST /mcp` - Main MCP protocol endpoint (handles all MCP methods)
- `DELETE /mcp` - End the session named in the `Mcp-Session-Id` header
- `GET /mcp/session` - Session creation helper endpoint

### REST API Endpoints
- `GET /health`, `GET /api/health` - Health checks
- `GET /tools` - List all available tools with their schemas
- `GET /api/tools/{tool_name}/schema` - Get a tool's parameter schema
- `POST /api/tools/{tool_name}` - Execute a tool via **multipart** (`file` upload + `params` JSON string)
- `POST /mcp/tools/{tool_name}` - Execute a tool via a **JSON body** (arguments as top-level keys)
- `GET /metrics`, `GET /api/metrics` - In-process counters (extractions, errors, vision calls + estimated cost)
- `GET /docs` - Interactive API documentation

### Background jobs (for slow OCR)

Long OCR/vision extractions can be run as background jobs so the request returns
immediately. Jobs are in-process (not durable across restarts).

- `POST /api/jobs/{tool_name}` - Submit a job (same multipart inputs as `/api/tools/{name}`); returns `202` with a `job_id`
- `GET /api/jobs/{job_id}` - Poll status (`pending`/`running`/`completed`/`failed`) and fetch the result
- `GET /api/jobs` - List recent jobs

```bash
# Submit, then poll
JID=$(curl -s -X POST http://localhost:8000/api/jobs/file_to_markdown \
  -F "file=@big-scan.pdf" | python -c "import sys,json;print(json.load(sys.stdin)['job_id'])")
curl http://localhost:8000/api/jobs/$JID
```

### MCP protocol notes

See [MCP Protocol Support](#-mcp-protocol-support) for protocol revisions, sessions,
error codes and CORS.

> **Auth:** if `FTMD_API_KEY` is set, tool-execution and admin endpoints require it,
> sent as `X-API-Key: <key>` or `Authorization: Bearer <key>`. If it is unset (default),
> auth is disabled — do not expose the service publicly without setting a key.
>
> **Request size:** bodies larger than `FTMD_MAX_REQUEST_BYTES` are rejected with `413`
> (by default derived from `FTMD_MAX_UPLOAD_BYTES`).

### Available Tools
- `file_to_markdown` - Extract files/URLs to Markdown with OCR fallback, chunking, and caching
- `text_processor` - Process and transform text content
- `url_fetcher` - Fetch web pages/files as Markdown (SSRF-guarded)

### Example Usage

**Via REST — JSON body (base64), using the `/mcp/tools/{name}` endpoint:**
```bash
# List available tools
curl http://localhost:8000/tools

# Extract a file from base64 content
curl -X POST http://localhost:8000/mcp/tools/file_to_markdown \
  -H "Content-Type: application/json" \
  -d '{"filename": "document.pdf", "base64_content": "JVBERi0xLjQK..."}'

# Extract a remote document by URL (fetched with SSRF protection)
curl -X POST http://localhost:8000/mcp/tools/file_to_markdown \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/report.pdf", "chunk_size": 2000}'

# Process text
curl -X POST http://localhost:8000/mcp/tools/text_processor \
  -H "Content-Type: application/json" \
  -d '{"text": "Hello World", "operation": "uppercase"}'

# Fetch a URL as Markdown
curl -X POST http://localhost:8000/mcp/tools/url_fetcher \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com"}'
```

**Via REST — multipart file upload, using `/api/tools/{name}`:**
```bash
# 'file' is the upload; 'params' is a JSON string of extra arguments
curl -X POST http://localhost:8000/api/tools/file_to_markdown \
  -F "file=@document.pdf" \
  -F 'params={"chunk_size": 2000}'
```

> If `FTMD_API_KEY` is set, add `-H "X-API-Key: <your-key>"` to the calls above.

**Via MCP Protocol:**
```bash
# Initialize MCP session
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
      "protocolVersion": "2024-11-05",
      "capabilities": {},
      "clientInfo": {"name": "test-client", "version": "1.0.0"}
    }
  }'

# List tools (after initialization)
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Mcp-Session-Id: <session-id-from-initialize>" \
  -d '{
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/list",
    "params": {}
  }'

# Call tool
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Mcp-Session-Id: <session-id>" \
  -d '{
    "jsonrpc": "2.0",
    "id": 3,
    "method": "tools/call",
    "params": {
      "name": "file_to_markdown",
      "arguments": {"filename": "test.pdf", "base64_content": "..."}
    }
  }'
```

## 🔍 Monitoring & Health

- **Health Check**: `GET /api/health`
- **Metrics**: Built-in logging and metrics collection
- **Docker Health**: Container health checks configured
- **API Documentation**: Interactive docs at `/docs`

## 🧪 Development & Testing

### Development Mode

```bash
# Run with hot reloading
FTMD_RELOAD=true FTMD_DEBUG=true python fastapi_mcp_template/main.py

# Or with Docker: the dev profile mounts the source, reloads on change and
# enables the test endpoints (published on port 8001)
docker compose --profile dev up file-extraction-service-dev
```

### Testing the MCP Server

#### Using MCP Inspector (Recommended)

1. Make sure the fastapi-mcp-template server is running:
   ```bash
   docker-compose up -d
   ```

2. Run the MCP Inspector to test the server:
   ```bash
   npx @modelcontextprotocol/inspector
   ```

3. In the MCP Inspector UI:
   - Set Transport to "streamable-http"
   - Set Server URL to "http://localhost:8000/mcp"
   - Click "Connect"

#### Manual Testing with curl

Test the initialization endpoint:
```bash
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
      "protocolVersion": "2024-11-05",
      "capabilities": {},
      "clientInfo": {
        "name": "test-client",
        "version": "1.0.0"
      }
    }
  }'
```

Test the tools/list endpoint (after initialization):
```bash
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Mcp-Session-Id: <session-id-from-initialize>" \
  -d '{
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/list",
    "params": {}
  }'
```

#### Expected Behavior

1. Initialize should return a session ID (`Mcp-Session-Id` response header; add `-i` to curl to see it) and server info
2. The initialized notification should be sent automatically 
3. Tools/list should return available tools after proper initialization
4. A request with an unknown or expired session ID gets HTTP `404`; the client then initializes again (this happens after every server restart)

#### Debugging

- Check Docker logs: `docker compose logs file-extraction-service`
- Check if port 8000 is accessible: `curl http://localhost:8000/health`
- Use MCP Inspector's debug features to see the full request/response flow

#### Testing OCR / scanned-document extraction

Traditional OCR (Tesseract) runs **without any API key**. With
`FTMD_MARKITDOWN_ENABLE_LLM=true` the vision model reads scanned pages first and
Tesseract is the fallback (see [How scanned pages are read](#how-scanned-pages-are-read)).

**Extract a scanned image (OCR):**
```bash
curl -X POST "http://localhost:8000/mcp/tools/file_to_markdown" \
  -H "Content-Type: application/json" \
  -d '{
    "filename": "chart.png",
    "base64_content": "iVBORw0KGgoAAAANSUhEUgAA..."
  }'
```

**Extract a scanned PDF (OCR fallback, per-page output):**
```bash
curl -X POST "http://localhost:8000/mcp/tools/file_to_markdown" \
  -H "Content-Type: application/json" \
  -d '{
    "filename": "scanned_document.pdf",
    "base64_content": "JVBERi0xLjYNJeLjz9MN..."
  }'
```

**Extraction behavior:**
- Text PDFs / Office docs: direct MarkItDown extraction.
- Scanned PDFs and images: read by the vision model and/or Tesseract; the response includes `pages[]` and an `extraction_method` of `vision`, `ocr`, or `ocr+vision` when both engines were used.
- The response reports `success: false` with a warning if no text could be extracted — it never reports success on empty output.

**LLM Integration Troubleshooting:**

- **PyMuPDF Not Available**: Install with `pip install PyMuPDF>=1.24.0` for OCR functionality
- **OpenAI Library Not Available**: Install dependencies with `pip install openai>=1.0.0`
- **API Key Issues**: 
  - Ensure your API key is valid and has sufficient credits
  - Check that the base URL is correct for Azure OpenAI or custom endpoints
  - Verify environment variable names use the `FTMD_` prefix
- **Self-hosted model not reached from Docker**:
  - `localhost` inside the container is the container itself; use `host.docker.internal` (or the host's LAN address) in `FTMD_OPENAI_BASE_URL`
  - When the model cannot be reached, pages are read by Tesseract instead; the logs show `Vision OCR failed on page N`
- **Model Not Found**: 
  - Ensure the model name is correct for your OpenAI setup
  - For Azure OpenAI, use your deployment name, not the base model name
  - Verify the model supports vision capabilities for OCR features
- **OCR Not Working**: 
  - Check `metadata.extraction_method`, `pages[].method` and `warnings` in the response: they say which engine read each page and whether pages were skipped
  - Verify the PDF actually contains no extractable text (OCR only runs for PDFs that yield no text, and for images)
  - Look for "running OCR fallback" in the logs
- **Traditional OCR Issues**:
  - **Pytesseract**: Ensure Tesseract OCR engine is installed on system (it is in the Docker image)
  - **Wrong or garbled characters**: Set `FTMD_OCR_LANGUAGES` to the document's language(s), e.g. `deu+eng`
  - **EasyOCR**: Optional alternative; not installed by default because it pulls in PyTorch
  - **Low Quality Results**: Enable the LLM integration so the vision model reads the pages
- **OCR Performance**:
  - A vision call takes seconds per page; use the background jobs API for long documents
  - `FTMD_OCR_STRATEGY=ocr_first` keeps the vision model for pages Tesseract cannot read (≤50 characters)
  - Processing is limited to `FTMD_OCR_MAX_PAGES` pages per document (default 50)

**Security Considerations:**
- Never commit API keys to version control
- Use environment variables or secure secret management
- Rotate API keys regularly
- Monitor API usage for unexpected activity

**Cost Considerations:**
- A self-hosted vision model costs nothing per page; a paid API bills every vision call
- With the default `vision_first`, each scanned page is one vision call — set `FTMD_OCR_STRATEGY=ocr_first`, `FTMD_VISION_MAX_CALLS_PER_REQUEST` or `FTMD_OCR_MAX_PAGES` to bound paid usage
- Text PDFs and Office documents never use the vision model
- Consider using `gpt-4o-mini` for cost-effective processing
- `GET /metrics` and `metadata.vision` in each response report vision calls, tokens and an estimated cost
- Monitor your usage through the OpenAI dashboard

### Testing Structure

The project uses a separated testing approach that mirrors the separation between core functionality and tools.

#### Test Organization

**1. Core Tests (`fastapi_mcp_template/core_test/`)**
- **Purpose**: Tests for the core MCP Template Server functionality
- **Location**: Inside the template service package
- **Covers**: Tool manager, configuration loading, API routes, core application logic

**2. Tool Tests (`tests/`)**
- **Purpose**: Tests specifically for individual tools
- **Location**: Root-level tests directory
- **Covers**: Individual tool loading and execution, tool-specific functionality, tool requirements validation

#### Running Tests

**Recommended: run the suite with pytest (from the repo root).**
```bash
pip install -r fastapi_mcp_template/core_test/requirements.txt
# pytest.ini sets asyncio_mode=auto and the test paths.
PYTHONPATH=. pytest
```
This runs the tests in `tests/` (extraction, chunking, caching, OCR engine selection,
SSRF, MCP protocol, sessions, CORS, API key, limits) and the framework tests in
`fastapi_mcp_template/core_test/`. No network, Tesseract or LLM is needed; tests for
a tool are skipped when that tool's dependencies are not installed.

**Via the in-app test runner (dev containers only).** Test endpoints are exposed
only when `FTMD_DEBUG=true`, and require `X-API-Key` when a key is configured:
```bash
# Start the dev service (debug + tests enabled)
docker compose --profile dev up file-extraction-service-dev   # dev is on :8001

curl http://localhost:8001/tests/status
curl -X POST http://localhost:8001/tests/run-all
curl -X POST http://localhost:8001/tests/run/test_tools
```

#### Test API Endpoints

`GET /tests/status` always exists. In debug mode (`FTMD_DEBUG=true`, with test
dependencies installed via the `ENABLE_TESTS=true` build argument) these are available
as well; they require the API key when one is configured:

- `GET /tests` - List all available tests
- `POST /tests/run/{test_name}` - Run specific test file
- `POST /tests/run-all` - Run all tests

#### Adding New Tests

**For Core Functionality:** Add test files to `fastapi_mcp_template/core_test/` following the pattern `test_*.py`

**For Tools:** Add test files to `tests/` following the pattern `test_*.py`

Both types use the same base testing classes for consistency.

### Tool Development Tips

1. **Keep Tools Self-Contained**: No imports from the core package
2. **Handle Errors Gracefully**: Always return structured error responses
3. **Use Type Hints**: Leverage Python's type system for better development experience
4. **Log Operations**: Use `tool_base.log_info()` and `tool_base.log_error()`
5. **Validate Inputs**: Implement proper input validation in your tools

## 📝 License

This work is licensed under the **Creative Commons Attribution-NonCommercial 4.0 International License**.

**What this means:**
- ✅ **Attribution Required**: You must give appropriate credit to the original creator
- ✅ **Non-Commercial Use**: You can use, modify, and distribute for non-commercial purposes
- ✅ **Remix & Adapt**: You can build upon and modify the material
- ❌ **Commercial Use**: Commercial use requires separate licensing agreement

**Copyright (c) 2025 Steffen Hebestreit**

For the full license text visit:
https://creativecommons.org/licenses/by-nc/4.0/

**For commercial licensing**, please contact the author.

## 🚀 Getting Started

Ready to build your MCP server? 

1. **Fork this template**
2. **Clone your fork**
   ```bash
   git clone https://github.com/yourusername/fastapi-mcp-template.git
   cd fastapi-mcp-template
   ```
3. **Add your custom tools** to the `tools/` directory
4. **Configure** via `.env` file
5. **Deploy** with `docker-compose up --build`

Your MCP server will be ready to integrate with LLM applications and provide both MCP and REST API access to your tools!

---

**Happy building! 🛠️**
