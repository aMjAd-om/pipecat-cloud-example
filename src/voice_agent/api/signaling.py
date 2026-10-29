"""ICE configuration, TURN credentials, and offer validation."""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Literal
from fastapi import HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from pipecat.transports.network.webrtc_connection import IceServer


def configured_ice_servers():
    servers = json.loads(os.getenv("ICE_SERVERS", "[]"))
    if not isinstance(servers, list):
        raise ValueError("ICE_SERVERS must be a JSON array")
    # Browser needs these short-lived TURN credentials, never Azure credentials.
    if os.getenv("TURN_URLS"):
        secret = os.environ["TURN_SECRET"]
        username = f"{int(time.time()) + 3600}:{secrets.token_hex(8)}"
        credential = base64.b64encode(hmac.new(
            secret.encode(), username.encode(), hashlib.sha1).digest()).decode()
        servers.append({"urls": os.environ["TURN_URLS"].split(","),
                        "username": username, "credential": credential})
    for server in servers:
        IceServer(**server)  # Validate against the installed aiortc API.
    return servers


class Offer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sdp: str = Field(min_length=30, max_length=120000)
    type: Literal["offer"]
    pc_id: str | None = Field(default=None, max_length=100)
    restart_pc: bool = False


def register_signaling_routes(app, sessions):
    """Register the browser session and SDP endpoints."""
    @app.get("/api/health")
    async def health():
        return {"status": "ok", "sessions": len(sessions.items)}

    @app.post("/api/sessions")
    async def create(request: Request, response: Response):
        session = sessions.create()
        response.set_cookie("voice_session", session.token, httponly=True,
                            secure=request.url.scheme == "https", samesite="strict",
                            max_age=sessions.max_age, path=f"/api/sessions/{session.id}")
        return {"id": session.id, "iceServers": session.ice}

    @app.post("/api/sessions/{sid}/offer")
    async def offer(sid: str, payload: Offer, request: Request):
        session = sessions.owned(sid, request.cookies.get("voice_session"))
        try:
            return await sessions.offer(session, payload)
        except HTTPException:
            raise
        except Exception:
            await sessions.close(sid)
            raise HTTPException(400, "WebRTC negotiation failed. Check ICE/network configuration.")

    @app.delete("/api/sessions/{sid}")
    async def stop(sid: str, request: Request):
        # Peer-close cleanup can win the race with the browser's Stop request.
        if sid not in sessions.items or sessions.items[sid].closing:
            return Response(status_code=204)
        sessions.owned(sid, request.cookies.get("voice_session"))
        await sessions.close(sid)
        response = Response(status_code=204)
        response.delete_cookie("voice_session", path=f"/api/sessions/{sid}")
        return response
