"""Compress public Flutter assets, never private pages or research responses."""

from starlette.middleware.gzip import GZipMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send


class PublicAssetCompression:
    def __init__(self, app: ASGIApp):
        self.app = app
        self.compressed = GZipMiddleware(app, minimum_size=1024, compresslevel=4)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        public = path in {
            "/main.dart.js",
            "/main.dart.mjs",
            "/main.dart.wasm",
            "/flutter.js",
            "/flutter_bootstrap.js",
        } or (path.startswith("/canvaskit/") and path.endswith((".js", ".wasm")))
        # Preserve byte ranges; GZipMiddleware handles negotiation and Vary.
        compress = (
            scope["type"] == "http"
            and scope["method"] == "GET"
            and public
            and not any(name.lower() == b"range" for name, _ in scope.get("headers", ()))
        )
        await (self.compressed if compress else self.app)(scope, receive, send)
