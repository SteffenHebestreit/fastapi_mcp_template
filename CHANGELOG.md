# Changelog

## 1.1.0 — 2026-11-09

### Changed

- **Scanned pages are read by the vision LLM first** when the LLM integration is
  enabled (`FTMD_OCR_STRATEGY=vision_first`, the new default). Tesseract takes over
  for a page when the model returns nothing, the request fails, or the per-request
  vision cap is reached. Without an LLM nothing changes: Tesseract reads the pages
  and needs no API key.
  - To get the previous order back (Tesseract first, LLM only for pages it cannot
    read), set `FTMD_OCR_STRATEGY=ocr_first`.
  - With a paid API every scanned page is now one vision call. Bound it with
    `FTMD_OCR_MAX_PAGES` / `FTMD_VISION_MAX_CALLS_PER_REQUEST`, or use `ocr_first`.
- **MCP sessions expire after an hour without requests**, not an hour after they
  were created. A session in regular use no longer dies mid-conversation.
- **Session errors use HTTP status codes**: `400` without an `Mcp-Session-Id` header,
  `404` for an unknown or expired session (both were `200` with a JSON-RPC error).
  MCP clients start a new session on `404`, so they now recover by themselves after
  a server restart.
- A client asking for an unsupported protocol revision is offered the newest
  supported one (`2025-11-25`) instead of the oldest.
- Calling a tool that does not exist (or is not enabled) is a JSON-RPC error
  `-32602` instead of a tool result.
- Tool results set `isError: true` when the tool returns `success: false`, and
  non-ASCII text (umlauts, CJK, ...) is no longer escaped as `\uXXXX`.
- The version reported to MCP clients and in the API docs comes from the package
  version.
- `GET /tests` (debug mode only) requires the API key when one is configured.

### Added

- MCP protocol revisions `2025-06-18` and `2025-11-25`, validation of the
  `MCP-Protocol-Version` header, and `DELETE /mcp` to end a session.
- Self-hosted OpenAI-compatible vision endpoints (LM Studio, Ollama, vLLM, ...)
  work without an API key: set `FTMD_OPENAI_BASE_URL` and `FTMD_OPENAI_MODEL`.
- The API key is also accepted as `Authorization: Bearer <key>`.
- Request body limit (`FTMD_MAX_REQUEST_BYTES`, by default derived from
  `FTMD_MAX_UPLOAD_BYTES`); larger requests get `413` before they are read into memory.
- With an explicit `FTMD_CORS_ORIGINS` list, requests from any other browser origin
  are rejected with `403` (protection against DNS rebinding).
- Settings `FTMD_OCR_STRATEGY`, `FTMD_SESSION_TTL_SECONDS`, `FTMD_MAX_SESSIONS`,
  `FTMD_MAX_REQUEST_BYTES`. Docker Compose passes the OCR and vision settings
  through from `.env`.
- `extraction_method` can be `vision` (every page read by the vision model).
- `python fastapi_mcp_template/main.py` starts the server without setting `PYTHONPATH`.

### Fixed

- Browser-based MCP clients were blocked by CORS: the `MCP-Protocol-Version` and
  `Authorization` headers and the `DELETE` method were not allowed.
- SSRF guard (`url_fetcher` and URL ingestion in `file_to_markdown`):
  - the carrier-grade NAT range `100.64.0.0/10`, also used by overlay VPNs, was
    not blocked;
  - the check could be bypassed with DNS rebinding, because the host name was
    resolved once for the check and again for the connection. Addresses are now
    validated at connect time.
- Notifications were answered with the body `null` instead of an empty `202`.
- The API key comparison is constant-time.
- Docker: editing application or tool code reinstalled every dependency on the
  next build; dependency layers are now reused (a rebuild takes seconds).
- Docker: `.dockerignore` was git-ignored and therefore missing, so `.git` and
  `.env` were sent to the build context.
- The in-app test runner could not load the API tests (`httpx` was missing from
  the test requirements).
- PyMuPDF printed a deprecation notice for `import fitz`.

### Not included

- The stateless MCP revision `2026-07-28` (`server/discover`, no sessions) is not
  implemented. Clients that prefer it fall back to the `initialize` handshake.
