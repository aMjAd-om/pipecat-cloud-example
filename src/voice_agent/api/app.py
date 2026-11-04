"""Same-origin telephone operator page, call API, and private media listener."""
import base64
import binascii
import os
import secrets
from contextlib import asynccontextmanager
import uvicorn
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from voice_agent.api.dialer import Dialer, register_dialer_routes
from voice_agent.observability.logging import configure_logging
from voice_agent.paths import DIALER_ASSETS


def create_app(dialer=None, *, validate_settings=True):
    dialer = dialer or Dialer()
    username = os.getenv("DIALER_USERNAME", "operator")
    password = os.getenv("DIALER_PASSWORD", "")
    if password and len(password) < 16:
        raise ValueError("DIALER_PASSWORD must contain at least 16 characters")
    if dialer.enabled and (not password or os.getenv("TELEPHONY_ENABLED", "false").lower() != "true"
                           or len(os.getenv("ASTERISK_AMI_SECRET", "")) < 16):
        raise ValueError("Outbound calls require DIALER_PASSWORD, TELEPHONY_ENABLED=true and ASTERISK_AMI_SECRET")
    origins = {value.strip() for value in os.getenv("FRONTEND_ORIGINS", "").split(",") if value.strip()}

    @asynccontextmanager
    async def lifespan(app):
        if validate_settings:
            configure_logging()
        from voice_agent.api.telephony import telephone_listener
        await dialer.start()
        try:
            async with telephone_listener(dialer=dialer):
                yield
        finally:
            await dialer.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.dialer = dialer

    @app.middleware("http")
    async def guard(request, call_next):
        # Protect both call controls and transcripts. Use TLS or an SSH tunnel.
        if password:
            auth = request.headers.get("authorization", "")
            valid = False
            try:
                scheme, encoded = auth.split(" ", 1)
                user, supplied = base64.b64decode(encoded, validate=True).decode("utf-8").split(":", 1)
                valid = (scheme.lower() == "basic"
                         and secrets.compare_digest(user.encode(), username.encode())
                         and secrets.compare_digest(supplied.encode(), password.encode()))
            except (ValueError, UnicodeError, binascii.Error):
                pass
            if not valid:
                return JSONResponse({"detail": "Operator login required"}, status_code=401,
                                    headers={"WWW-Authenticate": 'Basic realm="Telephone operator", charset="UTF-8"'})
        elif request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "Set DIALER_PASSWORD on the server to enable operator access"}, status_code=503)
        if request.url.path.startswith("/api/"):
            origin = request.headers.get("origin")
            same_origin = str(request.base_url).rstrip("/")
            if ((origin and origin not in origins | {same_origin})
                    or request.headers.get("sec-fetch-site") == "cross-site"):
                return JSONResponse({"detail": "Origin not allowed"}, status_code=403)
            if request.method == "POST":
                length = request.headers.get("content-length", "")
                if not length.isdigit() or int(length) > 4096:
                    return JSONResponse({"detail": "Request body missing or too large"}, status_code=413)
                if request.headers.get("content-type", "").split(";")[0] != "application/json":
                    return JSONResponse({"detail": "Expected JSON"}, status_code=415)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; "
            "img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid(_request, _exc):
        return JSONResponse({"detail": "Enter a valid phone number and call request"}, status_code=422)

    register_dialer_routes(app, dialer)

    static = DIALER_ASSETS
    if static.is_dir():
        app.mount("/", StaticFiles(directory=static, html=True), name="frontend")
    return app


def run():
    # Load .env before constructing the operator and telephone services.
    from voice_agent.config import load_settings
    load_settings()
    uvicorn.run(create_app(), host=os.getenv("HOST", "127.0.0.1"),
                port=int(os.getenv("PORT", "7860")), workers=1, access_log=False,
                timeout_graceful_shutdown=25)
