import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.frontend_static import (
    ASSET_CACHE_CONTROL,
    INDEX_CACHE_CONTROL,
    STATIC_FILE_CACHE_CONTROL,
    register_frontend_missing_fallback,
    register_frontend_static_routes,
)


def test_missing_dist_logs_warning_and_keeps_app_running(tmp_path, caplog):
    app = FastAPI()
    missing_dist = tmp_path / "frontend" / "dist"

    with caplog.at_level(logging.WARNING):
        registered = register_frontend_static_routes(app, missing_dist)

    assert registered is False
    assert "Frontend build directory not found" in caplog.text

    # Register the fallback like main.py does
    register_frontend_missing_fallback(app)

    with TestClient(app) as client:
        resp = client.get("/")
        assert resp.status_code == 404
        assert "npm ci" in resp.json()["detail"]
        assert "npm run build" in resp.json()["detail"]


def test_missing_index_logs_error_and_skips_frontend_routes(tmp_path, caplog):
    app = FastAPI()
    dist_dir = tmp_path / "frontend" / "dist"
    dist_dir.mkdir(parents=True)

    with caplog.at_level(logging.ERROR):
        registered = register_frontend_static_routes(app, dist_dir)

    assert registered is False
    assert "Frontend index file not found" in caplog.text


def test_valid_dist_serves_static_and_spa_fallback(tmp_path):
    app = FastAPI()
    dist_dir = tmp_path / "frontend" / "dist"
    assets_dir = dist_dir / "assets"
    dist_dir.mkdir(parents=True)
    assets_dir.mkdir(parents=True)

    index_file = dist_dir / "index.html"
    index_file.write_text("<html><body>index page</body></html>")
    (dist_dir / "robots.txt").write_text("User-agent: *")
    (assets_dir / "app.js").write_text("console.log('ok');")

    registered = register_frontend_static_routes(app, dist_dir)
    assert registered is True

    with TestClient(app) as client:
        root_response = client.get("/")
        assert root_response.status_code == 200
        assert "index page" in root_response.text
        assert root_response.headers["cache-control"] == INDEX_CACHE_CONTROL

        manifest_response = client.get("/site.webmanifest")
        assert manifest_response.status_code == 200
        assert manifest_response.headers["content-type"].startswith("application/manifest+json")
        assert manifest_response.headers["cache-control"] == "no-store"
        manifest = manifest_response.json()
        assert manifest["start_url"] == "http://testserver/"
        assert manifest["scope"] == "http://testserver/"
        assert manifest["id"] == "http://testserver/"
        assert manifest["display"] == "standalone"
        assert manifest["icons"][0]["src"] == "http://testserver/web-app-manifest-192x192.png"

        file_response = client.get("/robots.txt")
        assert file_response.status_code == 200
        assert file_response.text == "User-agent: *"
        assert file_response.headers["cache-control"] == STATIC_FILE_CACHE_CONTROL

        explicit_index_response = client.get("/index.html")
        assert explicit_index_response.status_code == 200
        assert "index page" in explicit_index_response.text
        assert explicit_index_response.headers["cache-control"] == INDEX_CACHE_CONTROL

        missing_response = client.get("/channel/some-route")
        assert missing_response.status_code == 200
        assert "index page" in missing_response.text
        assert missing_response.headers["cache-control"] == INDEX_CACHE_CONTROL

        asset_response = client.get("/assets/app.js")
        assert asset_response.status_code == 200
        assert "console.log('ok');" in asset_response.text
        assert asset_response.headers["cache-control"] == ASSET_CACHE_CONTROL


def test_webmanifest_uses_forwarded_origin_headers(tmp_path):
    app = FastAPI()
    dist_dir = tmp_path / "frontend" / "dist"
    dist_dir.mkdir(parents=True)
    (dist_dir / "index.html").write_text("<html><body>index page</body></html>")

    registered = register_frontend_static_routes(app, dist_dir)
    assert registered is True

    with TestClient(app) as client:
        response = client.get(
            "/site.webmanifest",
            headers={
                "x-forwarded-proto": "https",
                "x-forwarded-host": "mesh.example.com:8443",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["start_url"] == "https://mesh.example.com:8443/"
        assert data["scope"] == "https://mesh.example.com:8443/"
        assert data["id"] == "https://mesh.example.com:8443/"
