"""
ElevenLabs MCP Server

A self-hosted HTTP MCP server wrapping the ElevenLabs speech-to-text API.
Exposes a /mcp endpoint (MCP streamable HTTP transport) and /health.

Environment variables:
    ELEVENLABS_API_KEY  — required, your ElevenLabs API key
    MCP_TOKEN           — shared secret; clients must obtain via OAuth before calling /mcp
"""

import os
import secrets
import requests
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from mcp.server.fastmcp import FastMCP

ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
MCP_TOKEN = os.environ.get("MCP_TOKEN", "")
ELEVEN_STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"
ELEVEN_MODELS_URL = "https://api.elevenlabs.io/v1/models"

# In-memory one-time-use auth codes
_auth_codes: dict[str, str] = {}


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

mcp = FastMCP("elevenlabs-mcp")


@mcp.tool()
def speech_to_text(
    file_path: str,
    model_id: str = "scribe_v2",
    diarize: bool = True,
) -> str:
    """
    Transcribe an audio file using ElevenLabs Scribe.

    Args:
        file_path: Absolute path to the audio file on the host machine.
        model_id:  ElevenLabs STT model to use (default: scribe_v2).
        diarize:   Whether to enable speaker diarization (default: True).

    Returns:
        Formatted markdown transcript with speaker labels, e.g.:
            speaker_1: Hello, how are you?

            speaker_2: I'm doing well, thanks.
    """
    if not ELEVENLABS_API_KEY:
        raise RuntimeError(
            "ELEVENLABS_API_KEY is not set. "
            "Add it to your .env file and restart the server."
        )

    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Audio file not found: {file_path}")

    with open(file_path, "rb") as audio_file:
        response = requests.post(
            ELEVEN_STT_URL,
            headers={"xi-api-key": ELEVENLABS_API_KEY},
            files={"file": (os.path.basename(file_path), audio_file)},
            data={"model_id": model_id, "diarize": str(diarize).lower()},
            timeout=300,
        )

    response.raise_for_status()
    data = response.json()

    words = data.get("words", [])
    speaker_map: dict[str, str] = {}
    speaker_count = 0
    segments: list[dict] = []
    current_speaker: str | None = None
    current_text = ""

    for word_data in words:
        if word_data.get("type") != "word":
            continue

        raw_speaker = word_data["speaker_id"]
        if raw_speaker not in speaker_map:
            speaker_count += 1
            speaker_map[raw_speaker] = f"speaker_{speaker_count}"
        speaker = speaker_map[raw_speaker]

        if speaker != current_speaker:
            if current_text and current_speaker:
                segments.append({"speaker": current_speaker, "text": current_text.strip()})
            current_speaker = speaker
            current_text = word_data["text"] + " "
        else:
            current_text += word_data["text"] + " "

    if current_text and current_speaker:
        segments.append({"speaker": current_speaker, "text": current_text.strip()})

    lines = ["## Transcript", ""]
    for seg in segments:
        lines.append(f"{seg['speaker']}: {seg['text']}")
        lines.append("")

    return "\n".join(lines)


@mcp.tool()
def list_models() -> list:
    """
    List available ElevenLabs models.

    Returns:
        List of dicts with model_id and name.
    """
    if not ELEVENLABS_API_KEY:
        raise RuntimeError("ELEVENLABS_API_KEY is not set.")

    response = requests.get(
        ELEVEN_MODELS_URL,
        headers={"xi-api-key": ELEVENLABS_API_KEY},
        timeout=30,
    )
    response.raise_for_status()
    models = response.json()
    return [{"model_id": m.get("model_id"), "name": m.get("name")} for m in models]


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

from fastapi.middleware.httpsredirect import HTTPSRedirectMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

app = FastAPI(title="ElevenLabs MCP Server")
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["*"])

# Trust X-Forwarded-Proto from nginx so base_url resolves as https://
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")


# -- Auth middleware: protect /mcp with Bearer token -------------------------

@app.middleware("http")
async def bearer_auth(request: Request, call_next):
    if request.url.path.startswith("/mcp"):
        if MCP_TOKEN:
            auth = request.headers.get("Authorization", "")
            if not auth.startswith("Bearer ") or auth[7:] != MCP_TOKEN:
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
    return await call_next(request)


# -- OAuth 2.0 endpoints -----------------------------------------------------

@app.get("/.well-known/oauth-authorization-server")
async def oauth_metadata(request: Request):
    base = str(request.base_url).rstrip("/")
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/authorize",
        "token_endpoint": f"{base}/token",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "code_challenge_methods_supported": ["S256"],
    }


@app.get("/authorize")
@app.post("/authorize")
async def authorize(request: Request):
    params = dict(request.query_params)
    redirect_uri = params.get("redirect_uri", "")
    state = params.get("state", "")

    if not redirect_uri:
        return JSONResponse({"error": "missing redirect_uri"}, status_code=400)

    code = secrets.token_urlsafe(24)
    _auth_codes[code] = state

    sep = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(
        f"{redirect_uri}{sep}code={code}&state={state}",
        status_code=302,
    )


@app.post("/token")
async def token(request: Request):
    # Accept both JSON and form-encoded bodies
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        body = await request.json()
    else:
        form = await request.form()
        body = dict(form)

    code = body.get("code", "")
    grant_type = body.get("grant_type", "")

    if grant_type != "authorization_code":
        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)

    if code not in _auth_codes:
        return JSONResponse({"error": "invalid_grant"}, status_code=400)

    del _auth_codes[code]

    return {
        "access_token": MCP_TOKEN,
        "token_type": "bearer",
        "expires_in": 86400,
    }


# -- Standard endpoints ------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok"}


# Mount MCP at /mcp
app.mount("/mcp", mcp.streamable_http_app())


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False)
