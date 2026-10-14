"""Public transfer savings must not change private responses or byte ranges."""

import pytest
from starlette.responses import Response
from starlette.testclient import TestClient

from a_stock_tracker.app import get_asgi_app
from a_stock_tracker.web_assets import CHINESE_FONT, PublicAssetCompression


@pytest.mark.parametrize(
    "path,compressed",
    [
        ("/main.dart.js", True),
        ("/main.dart.mjs", True),
        ("/main.dart.wasm", True),
        ("/flutter_bootstrap.js", True),
        ("/canvaskit/chromium/canvaskit.wasm", True),
        ("/assets/fonts/MaterialIcons-Regular.otf", True),
        ("/assets/packages/cupertino_icons/assets/CupertinoIcons.ttf", True),
        ("/fonts/NotoSansSC-Subset.woff2", False),
        ("/fonts/private.otf", False),
        ("/", False),
        ("/oauth_callback", False),
        ("/analysis/" + "a" * 32, False),
        ("/runs/" + "a" * 32, False),
        ("/private/report.js", False),
    ],
)
def test_only_public_assets_are_compressed(path, compressed):
    body = b"synthetic content " * 1000
    client = TestClient(PublicAssetCompression(Response(body)))
    response = client.get(path, headers={"Accept-Encoding": "gzip"})
    assert response.content == body
    assert (response.headers.get("content-encoding") == "gzip") is compressed
    if compressed:
        assert int(response.headers["content-length"]) < len(body) // 2
        assert "Accept-Encoding" in response.headers["vary"]
    # Only the content-stable Chinese font is long-cached; Flet assets keep names across upgrades.
    assert ("cache-control" in response.headers) is (path == "/" + CHINESE_FONT)


@pytest.mark.parametrize(
    "headers",
    [
        {"Accept-Encoding": "identity"},
        {"Range": "bytes=0-4095"},
        {"range": "bytes=0-4095"},
        {"RANGE": "bytes=0-4095"},
    ],
)
def test_uncompressed_clients_and_ranges_are_preserved(headers):
    body = b"x" * 4096
    status = 206 if "Range" in headers else 200
    client = TestClient(PublicAssetCompression(Response(body, status_code=status)))
    response = client.get("/main.dart.js", headers=headers)
    assert response.status_code == status
    assert response.content == body
    assert "content-encoding" not in response.headers


def test_real_wasm_compression_preserves_security_and_conditional_requests():
    client = TestClient(get_asgi_app(), base_url="http://127.0.0.1:8550")
    path = "/main.dart.wasm"
    response = client.get(path, headers={"Accept-Encoding": "gzip"})
    assert response.status_code == 200
    assert response.headers["content-encoding"] == "gzip"
    assert response.content.startswith(b"\x00asm")
    cached = client.get(path, headers={"If-None-Match": response.headers["etag"]})
    assert cached.status_code == 304 and not cached.content
    assert client.get(path, headers={"Host": "evil.example"}).status_code == 403


def test_html_shell_preloads_chinese_font_only():
    page = TestClient(PublicAssetCompression(Response("<html></html>", media_type="text/html")))
    link = page.get("/opportunity/600001.SH").headers["link"]
    assert link == f"</{CHINESE_FONT}>; rel=preload; as=fetch; crossorigin"
    asset = TestClient(PublicAssetCompression(Response(b"x" * 2048)))
    assert "link" not in asset.get("/main.dart.js").headers


def test_real_shell_preload_targets_served_font():
    client = TestClient(get_asgi_app(), base_url="http://127.0.0.1:8550")
    assert CHINESE_FONT in client.get("/").headers["link"]
    font = client.get("/" + CHINESE_FONT, headers={"Accept-Encoding": "gzip"})
    assert font.status_code == 200 and font.content.startswith(b"wOF2")
    assert "content-encoding" not in font.headers
    assert font.headers["cache-control"] == "public, max-age=2592000"
    assert "cache-control" not in client.get("/main.dart.wasm").headers


def test_startup_manifest_registers_chinese_and_preserves_bundled_families():
    import json
    from pathlib import Path
    from urllib.parse import urljoin

    from flet_web import get_package_web_dir

    client = TestClient(get_asgi_app(), base_url="http://127.0.0.1:8550")
    response = client.get("/assets/FontManifest.json")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"
    manifest = response.json()
    upstream = json.loads((Path(get_package_web_dir()) / "assets/FontManifest.json").read_text())
    assert all(
        entry in manifest for entry in upstream if entry["family"] not in {"Roboto", "NotoSansSC"}
    )
    chinese = [entry for entry in manifest if entry["family"] == "NotoSansSC"]
    assert chinese == [{"family": "NotoSansSC", "fonts": [{"asset": "../" + CHINESE_FONT}]}]
    assert [entry for entry in manifest if entry["family"] == "Roboto"] == [
        {"family": "Roboto", "fonts": [{"asset": "../" + CHINESE_FONT}]}
    ]
    # Flutter resolves manifest entries under /assets/. Reuse the original URL so
    # startup loading and the existing HTML preload share one cached download.
    path = urljoin("/assets/", chinese[0]["fonts"][0]["asset"])
    assert path == "/" + CHINESE_FONT
    font = client.get(path)
    assert font.status_code == 200 and font.content.startswith(b"wOF2")
    assert (
        client.get("/assets/FontManifest.json", headers={"Host": "evil.example"}).status_code == 403
    )
    # A stale conditional header cannot suppress the augmented startup manifest.
    assert (
        client.get("/assets/FontManifest.json", headers={"If-None-Match": '"old"'}).json()
        == manifest
    )
