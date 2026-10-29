"""Same-origin API, application lifecycle, and static assets."""
import asyncio
import os
from contextlib import asynccontextmanager, suppress
import uvicorn
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from voice_agent.api.sessions import Sessions
from voice_agent.api.signaling import configured_ice_servers, register_signaling_routes
from voice_agent.observability.logging import configure_logging
from voice_agent.paths import VOICE_ASSETS


def create_app(manager=None, *, validate_settings=True):
    sessions = manager or Sessions()
    origins = [s.strip() for s in os.getenv("FRONTEND_ORIGINS",
               "http://localhost:7860,http://127.0.0.1:7860").split(",") if s.strip()]

    @asynccontextmanager
    async def lifespan(app):
        if validate_settings:
            configure_logging()
            configured_ice_servers()
        janitor = asyncio.create_task(sessions.reap())
        try:
            from voice_agent.api.telephony import telephone_listener
            async with telephone_listener():
                yield
        finally:
            janitor.cancel()
            with suppress(asyncio.CancelledError):
                await janitor
            await sessions.shutdown()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.sessions = sessions
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True,
                       allow_methods=["GET", "POST", "DELETE"], allow_headers=["Content-Type"])

    @app.middleware("http")
    async def guard(request, call_next):
        if request.url.path.startswith("/api/"):
            origin = request.headers.get("origin")
            if origin and origin not in origins:
                return JSONResponse({"detail": "Origin not allowed"}, status_code=403)
            if request.method == "POST":
                length = request.headers.get("content-length", "")
                if not length.isdigit() or int(length) > 128000:
                    return JSONResponse({"detail": "Request body missing or too large"}, status_code=413)
                if request.headers.get("content-type", "").split(";")[0] != "application/json":
                    return JSONResponse({"detail": "Expected JSON"}, status_code=415)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid(_request, _exc):
        return JSONResponse({"detail": "Invalid signaling request"}, status_code=422)

    register_signaling_routes(app, sessions)

    static = VOICE_ASSETS
    if static.is_dir():
        app.mount("/", StaticFiles(directory=static, html=True), name="frontend")
    return app


def run():
    # Load .env before app construction so origins/limits/ICE settings apply.
    from voice_agent.config import load_settings
    load_settings()
    uvicorn.run(create_app(), host=os.getenv("HOST", "127.0.0.1"),
                port=int(os.getenv("PORT", "7860")), workers=1, access_log=False,
                timeout_graceful_shutdown=25)
