# Running GTA Clipper (setup for Muse)

Instructions for an agent (Muse) to install and run this clipper on an
**Ubuntu 24.04 x86_64 Linux VM, CPU only** (tested target: 1.5 CPUs, 6 GB RAM).
Run each step in order; stop and report the exact error if a step fails.

Expect it to be slow on this hardware: a 20-minute video can take an hour or
more. Start with a short video (under 10 minutes).

## 1. System packages

```bash
sudo apt-get update
sudo apt-get install -y python3.12 python3.12-venv python3-pip ffmpeg git curl \
  libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 \
  fontconfig fonts-liberation fonts-noto-core fonts-noto-color-emoji
```

No `sudo`? Report it to the user; ffmpeg and the libraries above are required.

## 2. Get the code

```bash
git clone -b local-setup https://github.com/inmydonger/gta-clipper.git ~/gta-clipper
cd ~/gta-clipper
```

Already cloned? Update instead: `cd ~/gta-clipper && git pull`.

## 3. Python environment

Install the CPU-only build of torch first (the default one pulls several GB of
GPU libraries this VM cannot use), then the rest:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install --index-url https://download.pytorch.org/whl/cpu torch==2.11.0 torchvision==0.26.0
pip install -r requirements.txt
```

## 4. Config

The moment picker needs a **Google Gemini API key** (free tier works:
https://aistudio.google.com/app/apikey). Ask the user for it. Never print it,
log it or commit it.

Create `.env` in the repo root:

```
LLM_PROVIDER=gemini
GEMINI_API_KEY=<key from the user>
FFMPEG_ENCODER=x264
WHISPER_MODEL=base
WHISPER_DEVICE=cpu
WHISPER_COMPUTE=int8
MAX_CONCURRENT_JOBS=1
```

`WHISPER_MODEL=base` keeps transcription inside 6 GB of RAM. Use `tiny` if a
job is killed for running out of memory.

## 5. Start the backend

Run in the background; it must keep running:

```bash
cd ~/gta-clipper && source .venv/bin/activate && uvicorn app:app --host 127.0.0.1 --port 8000
```

Check it is up (should print a JSON list of 8 tool names):

```bash
curl -s -X POST http://localhost:8000/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

## 6. Making clips

Drive the backend by POSTing JSON-RPC `tools/call` to `http://localhost:8000/mcp`:

```bash
curl -s -X POST http://localhost:8000/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"process_video","arguments":{"url":"https://www.youtube.com/watch?v=VIDEO_ID"}}}'
```

Use `tools/list` to see each tool's arguments. Typical loop:

1. `process_video` with a YouTube URL; it returns a job id.
2. `get_job_status` every ~60 s until it is done.
3. `list_clips` to get the clips, scores and hooks. Clip files land in `output/`.
4. Optional: `recut_clip`, `add_subtitles`.

**If the YouTube download fails with a bot check / "sign in to confirm"**:
YouTube often blocks downloads from cloud servers. Don't try to get around it.
Tell the user and ask them to send the video file. Then call `create_upload`,
PUT the file to the returned `upload_url` (`curl -T video.mp4 <upload_url>`),
and call `process_video` with the returned `upload_id`.

## Rules

- **Never call `publish_clip` without the user approving that specific clip.** It posts publicly to TikTok/Instagram/YouTube.
- Only process videos the user has permission to clip.
- One job at a time. Don't start a second `process_video` while one is running.
- Don't commit or push `.env`, `uploads/` or `output/`.
