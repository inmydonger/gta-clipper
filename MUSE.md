# Running GTA Clipper (setup for Muse)

Instructions for an agent (Muse) to install and run this clipper on a Mac
(Apple Silicon, macOS). Run each step in order; stop and report if a step fails.

## 1. Prerequisites

```bash
brew install python@3.12 ffmpeg node git
```

The moment picker calls the Claude Code CLI (`claude -p`). Check it is
installed and signed in; if not, ask the user to install it and sign in.

```bash
claude --version
```

## 2. Get the code

```bash
git clone -b local-setup https://github.com/inmydonger/gta-clipper.git ~/gta-clipper
cd ~/gta-clipper
```

Already cloned? Update instead: `cd ~/gta-clipper && git pull`.

## 3. Python environment

```bash
/opt/homebrew/opt/python@3.12/bin/python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 4. Config

Create `.env` in the repo root with exactly this (no secrets needed):

```
LLM_PROVIDER=claude
LLM_MODEL=sonnet
LLM_SCORE_BATCH=8
FFMPEG_ENCODER=videotoolbox
WHISPER_MODEL=small
WHISPER_DEVICE=cpu
WHISPER_COMPUTE=int8
MAX_CONCURRENT_JOBS=1
```

## 5. Start the backend

Run in the background, it must keep running:

```bash
cd ~/gta-clipper && source .venv/bin/activate && uvicorn app:app --host 127.0.0.1 --port 8000
```

Check it is up (should print a JSON list of 8 tool names):

```bash
curl -s -X POST http://localhost:8000/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

Optional dashboard (UI at http://localhost:5173):

```bash
cd ~/gta-clipper/dashboard && npm install && VITE_PROXY_TARGET=http://localhost:8000 npm run dev
```

## 6. Making clips

Drive the backend by POSTing JSON-RPC `tools/call` to `http://localhost:8000/mcp`:

```bash
curl -s -X POST http://localhost:8000/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"process_video","arguments":{"url":"https://www.youtube.com/watch?v=VIDEO_ID"}}}'
```

Use `tools/list` to see each tool's arguments. Typical loop:

1. `process_video` with a YouTube URL, which returns a job id.
2. `get_job_status` every ~30 s until it is done (a long video takes many minutes on this Mac).
3. `list_clips` to get the clips, scores and hooks. Clip files land in `output/`.
4. Optional: `recut_clip`, `add_subtitles`.

## Rules

- **Never call `publish_clip` without the user approving that specific clip.** It posts publicly to TikTok/Instagram/YouTube.
- Only process videos the user has permission to clip.
- One job at a time (8 GB RAM). Don't start a second `process_video` while one is running.
- Don't commit or push `.env`, `uploads/` or `output/`.
