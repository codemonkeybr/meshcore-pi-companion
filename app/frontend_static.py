import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)


def _resolve_request_origin(request: Request) -> str:
    """Resolve the external origin, honoring common reverse-proxy headers."""
    forwarded_proto = request.headers.get("x-forwarded-proto")
    forwarded_host = request.headers.get("x-forwarded-host")

    if forwarded_proto and forwarded_host:
        proto = forwarded_proto.split(",")[0].strip()
        host = forwarded_host.split(",")[0].strip()
        if proto and host:
            return f"{proto}://{host}"

    return str(request.base_url).rstrip("/")


def register_frontend_static_routes(app: FastAPI, frontend_dir: Path) -> bool:
    """Register frontend static file routes if a built frontend is available.

    Returns True when routes are registered, False when frontend files are
    missing/incomplete. Missing frontend files are logged but are not fatal.
    """
    frontend_dir = frontend_dir.resolve()
    index_file = frontend_dir / "index.html"
    assets_dir = frontend_dir / "assets"

    if not frontend_dir.exists():
        logger.warning(
            "Frontend build directory not found at %s. "
            "Run 'cd frontend && npm run build'. API will continue without frontend routes.",
            frontend_dir,
        )
        return False

    if not frontend_dir.is_dir():
        logger.error(
            "Frontend build path is not a directory: %s. "
            "API will continue without frontend routes.",
            frontend_dir,
        )
        return False

    if not index_file.exists():
        logger.error(
            "Frontend index file not found at %s. "
            "Run 'cd frontend && npm run build'. API will continue without frontend routes.",
            index_file,
        )
        return False

    if assets_dir.exists() and assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")
    else:
        logger.warning(
            "Frontend assets directory missing at %s; /assets files will not be served",
            assets_dir,
        )

    @app.get("/")
    async def serve_index():
        """Serve the frontend index.html."""
        return FileResponse(index_file)

    @app.get("/site.webmanifest")
    async def serve_webmanifest(request: Request):
        """Serve a dynamic web manifest using the active request origin."""
        origin = _resolve_request_origin(request)
        manifest = {
            "name": "RemoteTerm for MeshCore",
            "short_name": "RemoteTerm",
            "id": f"{origin}/",
            "start_url": f"{origin}/",
            "scope": f"{origin}/",
            "display": "standalone",
            "display_override": ["window-controls-overlay", "standalone", "fullscreen"],
            "theme_color": "#111419",
            "background_color": "#111419",
            "icons": [
                {
                    "src": f"{origin}/web-app-manifest-192x192.png",
                    "sizes": "192x192",
                    "type": "image/png",
                    "purpose": "maskable",
                },
                {
                    "src": f"{origin}/web-app-manifest-512x512.png",
                    "sizes": "512x512",
                    "type": "image/png",
                    "purpose": "maskable",
                },
            ],
        }
        return JSONResponse(
            manifest,
            media_type="application/manifest+json",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/{path:path}")
    async def serve_frontend(path: str):
        """Serve frontend files, falling back to index.html for SPA routing."""
        file_path = (frontend_dir / path).resolve()
        try:
            file_path.relative_to(frontend_dir)
        except ValueError:
            raise HTTPException(status_code=404, detail="Not found") from None

        if file_path.exists() and file_path.is_file():
            return FileResponse(file_path)

        return FileResponse(index_file)

    logger.info("Serving frontend from %s", frontend_dir)
    return True


def register_frontend_missing_fallback(app: FastAPI) -> None:
    """Register a fallback route that tells the user to build the frontend."""

    @app.get("/", include_in_schema=False)
    async def frontend_not_built():
        return JSONResponse(
            status_code=404,
            content={
                "detail": "Frontend not built. Run: cd frontend && npm install && npm run build"
            },
        )
