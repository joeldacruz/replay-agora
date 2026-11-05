from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import settings
from .logsafe import install_redaction
from .routers import agent, arena, arena_capacity, auth, ingest, media, platform, platform_capacity, user

CSP = ("default-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; style-src 'self' 'unsafe-inline'; "
       "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")


def create_app() -> FastAPI:
    install_redaction()
    app = FastAPI(title="Replay Agora API", version="0.1.0", docs_url="/api/docs", openapi_url="/api/openapi.json")
    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
                           allow_headers=["Authorization", "Content-Type"])

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.url.path.startswith("/api/agent/v1"):  # também em respostas de erro (503 do reserva, 409 de fencing)
            resp.headers["X-Central-Epoch"] = str(settings.central_epoch)
            resp.headers["X-Central-Role"] = settings.node_role
            resp.headers["X-Central-Node"] = settings.node_id
        if not request.url.path.startswith("/api/docs"):
            resp.headers.setdefault("Content-Security-Policy", CSP)
        return resp

    for r in (auth.router, user.router, arena.router, arena_capacity.router, platform.router, platform_capacity.router,
              ingest.router, agent.router, media.router):
        app.include_router(r)

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    # Três aplicações estáticas independentes + pacote compartilhado de UI (sem build).
    root = settings.frontend_dir
    for mount, folder in (("/shared", root / "packages" / "ui"), ("/portal", root / "apps" / "portal-dev"),
                          ("/arena", root / "apps" / "arena"), ("/", root / "apps" / "usuario")):
        if Path(folder).is_dir():
            app.mount(mount, StaticFiles(directory=folder, html=True), name=mount.strip("/") or "usuario")
    return app


app = create_app()
