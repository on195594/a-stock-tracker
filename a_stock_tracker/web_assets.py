"""Compress public Flutter assets, never private pages or research responses."""

import json
from pathlib import Path

from flet_web import get_package_web_dir  # type: ignore[import-untyped]
from starlette.middleware.gzip import GZipMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

CHINESE_FONT = "fonts/NotoSansSC-Subset.woff2"
# Flutter requests page fonts only after its runtime boots; start the largest download at once.
# as=fetch + crossorigin matches the client's same-origin fetch so the bytes are reused.
FONT_PRELOAD = (b"link", f"</{CHINESE_FONT}>; rel=preload; as=fetch; crossorigin".encode())
FONT_CACHE = (b"cache-control", b"public, max-age=2592000")


class PublicAssetCompression:
    def __init__(self, app: ASGIApp):
        self.app = app
        # Flet loads page.fonts asynchronously while building the first page. Register
        # Chinese with Flutter's startup fonts instead, before any Chinese layout can
        # trigger its unbundled no-CDN fallback shards. Keep the installed icon/math
        # families rather than copying a version-specific manifest into our assets.
        manifest = json.loads(
            (Path(get_package_web_dir()) / "assets/FontManifest.json").read_text()
        )
        # Flutter also uses Roboto for initial/default text before Flet's remote
        # theme has reached every frame. Give that default family the same complete
        # app subset, not a second same-weight face (Skia would keep selecting the
        # Latin-only face). Latin default text now intentionally matches the app's
        # Noto typography too; icon/math families remain untouched.
        manifest = [entry for entry in manifest if entry["family"] not in {"Roboto", "NotoSansSC"}]
        manifest.extend(
            {"family": family, "fonts": [{"asset": "../" + CHINESE_FONT}]}
            for family in ("Roboto", "NotoSansSC")
        )
        self.font_manifest = JSONResponse(manifest, headers={"Cache-Control": "no-cache"})
        self.compressed = GZipMiddleware(app, minimum_size=1024, compresslevel=4)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if (
            scope["type"] == "http"
            and scope["method"] in {"GET", "HEAD"}
            and path == "/assets/FontManifest.json"
        ):
            await self.font_manifest(scope, receive, send)
            return
        public = (
            path
            in {
                "/main.dart.js",
                "/main.dart.mjs",
                "/main.dart.wasm",
                "/flutter.js",
                "/flutter_bootstrap.js",
            }
            or (path.startswith("/canvaskit/") and path.endswith((".js", ".wasm")))
            # Flet's bundled icon/math fonts are uncompressed OTF/TTF; WOFF2 is already compressed.
            or (path.startswith("/assets/") and path.endswith((".otf", ".ttf")))
        )
        # Preserve byte ranges; GZipMiddleware handles negotiation and Vary.
        compress = (
            scope["type"] == "http"
            and scope["method"] == "GET"
            and public
            and not any(name.lower() == b"range" for name, _ in scope.get("headers", ()))
        )
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_preload(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = message.get("headers", [])
                if path == "/" + CHINESE_FONT and message["status"] == 200:
                    # The client's font fetch must reuse the preloaded bytes, and daily visits
                    # must not redownload 6MB. Rename the file if its content ever changes.
                    message = {**message, "headers": [*headers, FONT_CACHE]}
                elif any(
                    name == b"content-type" and value.startswith(b"text/html")
                    for name, value in headers
                ):
                    message = {**message, "headers": [*headers, FONT_PRELOAD]}
            await send(message)

        await (self.compressed if compress else self.app)(scope, receive, send_with_preload)
