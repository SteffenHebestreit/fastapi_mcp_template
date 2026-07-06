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
- **OCR fallback for scanned PDFs and images** — traditional OCR (Tesseract) runs first and needs **no API key**; an optional LLM vision model is a second-level fallback for hard pages.
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
│   ├── api/                      # REST API endpoints
│   │   ├── __init__.py
│   │   └── routes.py             # API route definitions
│   ├── core/                     # MCP core functionality
│   │   ├── __init__.py
│   │   ├── tool_base.py          # Base tool infrastructure
│   │   ├── tool_definition.py    # Tool interface definitions
│   │   └── tool_manager.py       # Dynamic tool loading
│   └── core_test/                # Core functionality tests
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
├── tests/                        # Tool tests
│   ├── __init__.py
│   └── test_tools.py
├── data/                         # Data directory for file storage
├── docker-compose.yml            # Container orchestration
├── Dockerfile                    # Container definition with dynamic deps
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
- **Docker Support**: Complete containerization with multi-stage builds
- **Dynamic Dependencies**: Tool dependencies installed only when tools are enabled
- **Health Checks**: Built-in monitoring and health endpoints
- **Environment Configuration**: Flexible configuration through environment variables

### **Developer Experience**
- **FastAPI Foundation**: Modern Python web framework with automatic validation
- **Type Safety**: Full type hints and Pydantic models
- **Error Handling**: Comprehensive error handling and logging
- **Testing Ready**: Structure supports easy unit and integration testing

## 📋 MCP Protocol Compliance

This template implements the **Model Context Protocol (MCP) 2024-11-05** specification with full compliance to ensure seamless integration with MCP clients like Claude Desktop, Cline, and other LLM applications.

### **Protocol Standards Adherence**

#### **JSON-RPC 2.0 Foundation**
- ✅ **Full JSON-RPC 2.0 compliance** with proper message structure
- ✅ **Unique request IDs** that are never reused within a session
- ✅ **Proper error codes** following JSON-RPC standards (-32000 to -32099 range)
- ✅ **Structured error responses** with code, message, and optional data fields

#### **MCP Lifecycle Management**
- ✅ **Proper initialization handshake** with capability negotiation
- ✅ **Session management** with UUID-based session IDs
- ✅ **Protocol version negotiation** (supports 2024-11-05)
- ✅ **Graceful error handling** for unsupported protocol versions

#### **Core MCP Methods**
```json
{
  "initialize": "✅ Capability negotiation with tool listing support",
  "notifications/initialized": "✅ Post-initialization state management", 
  "tools/list": "✅ Dynamic tool discovery and enumeration",
  "tools/call": "✅ Tool execution with parameter validation",
  "ping": "✅ Connection liveness verification"
}
```

### **Capability Declaration**

The server correctly declares its capabilities during initialization:

```json
{
  "capabilities": {
    "tools": {
      "listChanged": true
    }
  },  
  "serverInfo": {
    "name": "fastapi_mcp_template",
    "version": "1.0.0"
  }
}
```

**Key Compliance Features:**
- **Tool Capability**: Declares support for tool listing and execution
- **Dynamic Updates**: Supports `listChanged` notifications for tool modifications
- **Server Identity**: Proper server information with name and version
- **Protocol Version**: Explicit protocol version support declaration

### **Tool Schema Compliance**

Tools follow MCP tool specification with proper JSON Schema validation:

```json
{
  "name": "file_to_markdown",
  "description": "Convert files to Markdown format",
  "inputSchema": {
    "type": "object",
    "properties": {
      "filename": {
        "type": "string",
        "description": "Name of the file to convert"
      },
      "base64_content": {
        "type": "string", 
        "description": "Base64 encoded file content"
      }
    },
    "required": ["filename", "base64_content"]
  }
}
```

### **Error Handling Standards**

#### **Protocol-Level Errors**
- **-32601**: Method not found (unsupported MCP methods)
- **-32602**: Invalid params (malformed requests, unknown tools)
- **-32603**: Internal error (server-side failures)
- **-32002**: Server not initialized (session/capability issues)

#### **Tool Execution Errors**
Tool errors are reported within successful JSON-RPC responses using the `isError` flag:

```json
{
  "jsonrpc": "2.0",
  "id": 123,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "Tool execution failed: File format not supported"
      }
    ],
    "isError": true
  }
}
```

### **Session Management**

#### **Session Lifecycle**
1. **Initialization**: Client sends `initialize` request
2. **Capability Negotiation**: Server responds with supported features
3. **Ready State**: Client sends `notifications/initialized`
4. **Active Session**: Tools can be listed and called
5. **Session Persistence**: Sessions maintain state across requests

#### **Session Security**
- **UUID-based Session IDs**: Cryptographically secure session identifiers
- **Session Validation**: All requests validated against active sessions
- **Session Timeout**: Automatic cleanup of inactive sessions
- **Header-based Authentication**: Session ID passed via `Mcp-Session-Id` header

### **Transport Layer Compliance**

#### **HTTP Transport Support**
- ✅ **Content-Type**: `application/json` for all requests/responses
- ✅ **CORS Support**: Proper cross-origin headers for web clients
- ✅ **Session Headers**: `Mcp-Session-Id` header handling
- ✅ **Status Codes**: Appropriate HTTP status codes (200, 202, 400, 500)

#### **Endpoint Structure**
```bash
POST /mcp                    # Main MCP protocol endpoint
POST /                      # Fallback MCP endpoint
GET  /mcp/session           # Session creation helper
OPTIONS /mcp                # CORS preflight support
```

### **Testing & Validation**

#### **MCP Inspector Compatibility**
The server is fully compatible with the official MCP Inspector tool:

```bash
# Test with MCP Inspector
npx @modelcontextprotocol/inspector
# Connect to: http://localhost:8000/mcp
```

#### **Protocol Validation Tests**
- ✅ **Initialization Flow**: Complete handshake sequence
- ✅ **Tool Discovery**: List and validate all available tools
- ✅ **Tool Execution**: Parameter validation and result formatting
- ✅ **Error Scenarios**: Proper error code and message handling
- ✅ **Session Management**: Session creation, validation, and cleanup

### **Interoperability**

#### **Client Compatibility**
Tested and verified with:
- ✅ **Claude Desktop**: Full integration support
- ✅ **MCP Inspector**: Official testing tool compatibility
- ✅ **Custom Clients**: Java, TypeScript, Python client libraries
- ✅ **Web Clients**: Browser-based MCP applications

#### **Standards Compliance**
- 📋 **MCP Specification 2024-11-05**: Full compliance
- 📋 **JSON-RPC 2.0**: Complete implementation
- 📋 **JSON Schema**: Tool parameter validation
- 📋 **HTTP/1.1**: Standard web compatibility
- 📋 **CORS**: Cross-origin resource sharing support

This comprehensive compliance ensures that any MCP client can seamlessly connect to and interact with your server, providing a reliable foundation for LLM integrations.

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

# 6. Run the server (config comes from FTMD_* env vars / .env — no CLI flags)
#    PYTHONPATH must include the repo root so the package is importable.
PYTHONPATH=. python -m fastapi_mcp_template.main

# Custom configuration is via environment variables, e.g.:
FTMD_PORT=8080 FTMD_DEBUG=true PYTHONPATH=. python -m fastapi_mcp_template.main
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
FTMD_API_KEY=                   # if set, X-API-Key required on tool/admin endpoints
FTMD_CORS_ORIGINS=*             # "*" (no credentials) or comma-separated origins

# Extraction limits
FTMD_MAX_UPLOAD_BYTES=52428800  # 50 MiB
FTMD_OCR_MAX_PAGES=50           # 0 = no limit
FTMD_OCR_LANGUAGES=eng          # tesseract lang(s), e.g. eng+deu

# URL fetching / SSRF
FTMD_BLOCK_PRIVATE_URLS=true    # block internal/private/metadata addresses
```

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

#### Features Enhanced by LLM Integration
When LLM integration is enabled, the file converter provides:
1. **Enhanced Image Processing**: Generate detailed descriptions of images, charts, and diagrams
2. **OCR for Scanned PDFs**: Automatically converts PDF pages to images and extracts text using vision-capable LLMs
3. **Better Content Analysis**: Improved understanding of document structure and content
4. **Smart Formatting**: More intelligent Markdown formatting decisions

**Enhanced OCR Fallback Process:**
The file converter now uses a sophisticated three-level fallback system for scanned PDFs:

1. **Traditional OCR (Primary)**: Fast and cost-effective text extraction using pytesseract or easyocr
   - Processes document images using traditional OCR algorithms
   - Ideal for clear, standard fonts and simple layouts
   - No API costs, works offline

2. **LLM Vision Analysis (Secondary)**: Advanced analysis for complex content
   - Used when traditional OCR yields insufficient results (<50 characters)
   - Handles handwriting, complex layouts, and poor-quality scans
   - Uses vision-capable LLMs for superior accuracy

3. **Intelligent Selection (Tertiary)**: Combines best results
   - Automatically selects the most comprehensive output
   - Prefers traditional OCR when sufficient, LLM when needed
   - Optimizes for both cost and accuracy

**Process Details:**
- Automatically detects scanned PDFs (empty text content)
- Converts PDF pages to high-resolution images (2x zoom)
- Processes up to 5 pages per document to manage costs
- Maintains original formatting and structure in Markdown output

### Docker Configuration

**docker-compose.yml** supports:
- Tool selection via build args
- Environment file mounting
- Volume mounting for development
- Port configuration
- Health checks

**Dockerfile** features:
- Multi-stage builds for optimization
- Dynamic dependency installation based on enabled tools
- Security-hardened container (non-root user)
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

The `/mcp` endpoint speaks JSON-RPC 2.0 and supports **batch requests** (send an
array), negotiates `protocolVersion` (echoes the client's when supported: `2024-11-05`
or `2025-03-26`), and declares `tools.listChanged: false` (there is no server→client
push stream). `GET /mcp` returns `405` (SSE streaming is not implemented; use POST).

> **Auth:** if `FTMD_API_KEY` is set, tool-execution and admin endpoints require an
> `X-API-Key` header. If it is unset (default), auth is disabled — do not expose the
> service publicly without setting a key.

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
python fastapi_mcp_template/main.py --reload

# Or with Docker in development mode
docker-compose -f docker-compose.yml -f docker-compose.dev.yml up
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

1. Initialize should return a session ID and server info
2. The initialized notification should be sent automatically 
3. Tools/list should return available tools after proper initialization
4. The server should not return "Server not initialized" errors

#### Debugging

- Check Docker logs: `docker compose logs file-extraction-service`
- Check if port 8000 is accessible: `curl http://localhost:8000/health`
- Use MCP Inspector's debug features to see the full request/response flow

#### Testing OCR / scanned-document extraction

Traditional OCR (Tesseract) runs **without any API key**. The LLM vision model is
only a second-level fallback, enabled with `FTMD_MARKITDOWN_ENABLE_LLM=true` and an
OpenAI key.

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
- Scanned PDFs and images: Tesseract OCR (free); the response includes `pages[]` and an `extraction_method` of `ocr` (or `ocr+vision` when the LLM fallback was used).
- The response reports `success: false` with a warning if no text could be extracted — it never reports success on empty output.

**LLM Integration Troubleshooting:**

- **PyMuPDF Not Available**: Install with `pip install PyMuPDF>=1.24.0` for OCR functionality
- **OpenAI Library Not Available**: Install dependencies with `pip install openai>=1.0.0`
- **API Key Issues**: 
  - Ensure your API key is valid and has sufficient credits
  - Check that the base URL is correct for Azure OpenAI or custom endpoints
  - Verify environment variable names use the `FTMD_` prefix
- **Model Not Found**: 
  - Ensure the model name is correct for your OpenAI setup
  - For Azure OpenAI, use your deployment name, not the base model name
  - Verify the model supports vision capabilities for OCR features
- **OCR Not Working**: 
  - Check that `FTMD_MARKITDOWN_ENABLE_LLM=true` is set
  - Verify the PDF actually contains no extractable text (scanned documents)
  - Look for "Attempting enhanced OCR fallback" messages in logs
- **Traditional OCR Issues**:
  - **Pytesseract**: Ensure Tesseract OCR engine is installed on system
  - **EasyOCR**: May require additional GPU setup for optimal performance
  - **Low Quality Results**: Tool will automatically fall back to LLM vision analysis
- **OCR Performance**:
  - Traditional OCR is preferred for cost efficiency (no API costs)
  - LLM vision analysis activates when OCR yields <50 characters
  - Processing limited to 5 pages per PDF to manage costs

**Security Considerations:**
- Never commit API keys to version control
- Use environment variables or secure secret management
- Rotate API keys regularly
- Monitor API usage for unexpected activity

**Cost Considerations:**
- LLM integration incurs API costs based on usage
- OCR processing (scanned PDFs) uses more tokens than standard text extraction
- Image processing typically uses more tokens than text-only operations
- Consider using `gpt-4o-mini` for cost-effective processing
- OCR processing is limited to 5 pages per PDF to manage costs
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
pip install pytest pytest-asyncio
# pytest.ini sets asyncio_mode=auto and the test paths.
PYTHONPATH=. pytest
```
This runs the tool tests in `tests/` (real extraction, chunking, caching, SSRF)
and the framework tests in `fastapi_mcp_template/core_test/`.

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

When tests are enabled (`ENABLE_TESTS=true`), the following endpoints are available:

- `GET /tests/status` - Check if testing is enabled
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
