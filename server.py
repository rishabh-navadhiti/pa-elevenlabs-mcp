"""
ElevenLabs MCP Server

A self-hosted HTTP MCP server wrapping the ElevenLabs speech-to-text API.
Exposes a /mcp endpoint (MCP streamable HTTP transport) and /health.

Environment variables:
    ELEVENLABS_API_KEY  — required, your ElevenLabs API key
"""

import os
import requests
from contextlib import asynccontextmanager
from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP

ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
ELEVEN_STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"
ELEVEN_MODELS_URL = "https://api.elevenlabs.io/v1/models"


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

    # Build speaker segments from the words array
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

    # Format to markdown
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

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with mcp.server.run_lifespan(app):
        yield


app = FastAPI(title="ElevenLabs MCP Server", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok"}


# Mount MCP at /mcp
app.mount("/mcp", mcp.streamable_http_app())


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False)
