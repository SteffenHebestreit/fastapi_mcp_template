"""Request guard: browser-origin allow-list and request body size limit.

API-key checks are done per endpoint (see ``routes.py``); this middleware covers
the two protections that have to apply before a route reads the request.
"""

from typing import Iterable

from fastapi import HTTPException
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RequestGuardMiddleware:
    """Rejects requests before they reach a route.

    * ``allowed_origins``: unless it contains ``"*"``, a request carrying an
      ``Origin`` header that is not listed gets 403. Requests without an
      ``Origin`` header (non-browser clients) are not affected. This is what
      protects a locally running server against DNS rebinding from web pages.
    * ``max_request_bytes``: bodies larger than this get 413, whether or not
      the client announced the size up front (0 disables the limit).

    ``OPTIONS`` requests and ``public_paths`` always pass.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        allowed_origins: Iterable[str] = ("*",),
        max_request_bytes: int = 0,
        public_paths: Iterable[str] = (),
    ) -> None:
        self.app = app
        origins = {o.rstrip("/") for o in allowed_origins}
        self.allowed_origins = None if "*" in origins else origins
        self.max_request_bytes = max(0, max_request_bytes)
        self.public_paths = frozenset(public_paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] == "OPTIONS"
            or scope["path"] in self.public_paths
        ):
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)

        origin = headers.get("origin")
        if (
            origin
            and self.allowed_origins is not None
            and origin.rstrip("/") not in self.allowed_origins
        ):
            await self._reject(scope, receive, send, 403, "Origin not allowed")
            return

        limit = self.max_request_bytes
        if limit:
            declared = headers.get("content-length", "")
            if declared.isdigit() and int(declared) > limit:
                await self._reject(scope, receive, send, 413, self._too_large_message())
                return
            # A client can omit or understate Content-Length, so the bytes
            # that actually arrive are counted as well.
            receive = self._limited(receive)

        await self.app(scope, receive, send)

    def _too_large_message(self) -> str:
        return f"Request body exceeds the limit of {self.max_request_bytes} bytes"

    def _limited(self, receive: Receive) -> Receive:
        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_request_bytes:
                    # Raised inside the route while it reads the body; the
                    # framework's exception handling turns it into a response.
                    raise HTTPException(status_code=413, detail=self._too_large_message())
            return message

        return limited_receive

    @staticmethod
    async def _reject(scope, receive, send, status_code, detail) -> None:
        response = JSONResponse({"detail": detail}, status_code=status_code)
        await response(scope, receive, send)
