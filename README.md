# pa-elevenlabs-mcp

A self-hosted HTTP MCP server wrapping the ElevenLabs speech-to-text API.
Designed to be used with Claude Cowork plugins where the sandbox blocks direct outbound API calls.
The MCP server runs on the host machine (outside the sandbox), so the API call goes through cleanly.

## Tools exposed

| Tool | Description |
|------|-------------|
| `speech_to_text` | Transcribe an audio file using ElevenLabs Scribe v2 with speaker diarization |
| `list_models` | List available ElevenLabs models |

---

## Local development with Docker Compose + ngrok

### 1. Set up environment variables

```bash
cp .env.example .env
```

Edit `.env` and fill in:
```
ELEVENLABS_API_KEY=sk_...
NGROK_AUTHTOKEN=your_ngrok_authtoken   # get free token at https://ngrok.com
```

### 2. Start the server

```bash
docker compose up --build
```

This starts:
- `mcp-server` on port `8000`
- `ngrok` tunnel on port `4040` (inspect UI)

### 3. Get your ngrok public URL

```bash
curl -s http://localhost:4040/api/tunnels | python3 -m json.tool | grep public_url
```

Or open http://localhost:4040 in your browser.

You'll get a URL like: `https://abc123.ngrok-free.app`

### 4. Test the health endpoint

```bash
curl https://abc123.ngrok-free.app/health
# {"status":"ok"}
```

---

## Connect to a Cowork plugin

Add the following to your plugin's `.mcp.json` (or to `claude_desktop_config.json` for Claude Code):

```json
{
  "mcpServers": {
    "elevenlabs-mcp": {
      "type": "http",
      "url": "https://abc123.ngrok-free.app/mcp"
    }
  }
}
```

Replace `abc123.ngrok-free.app` with your actual ngrok URL.

In your skill, call the tool like:
```
Use the elevenlabs-mcp speech_to_text tool with:
- file_path: /absolute/path/to/audio.mp3
- model_id: scribe_v2
- diarize: true
```

---

## EC2 production deployment

For production, use the same `docker-compose.yml` but:

1. Remove the `ngrok` service entirely
2. Point your domain to port `8000` using nginx or Caddy as a reverse proxy
3. Use HTTPS (Let's Encrypt via Caddy is the easiest option)

**Minimal Caddyfile:**
```
your-domain.com {
    reverse_proxy localhost:8000
}
```

Update `.mcp.json` to use your real domain:
```json
{
  "mcpServers": {
    "elevenlabs-mcp": {
      "type": "http",
      "url": "https://your-domain.com/mcp"
    }
  }
}
```
