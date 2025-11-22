# Sora Suite — Electron + React shell

This repository now ships a frameless Electron application that drives the existing Python automation workers (`sora_suite/workers`). The Python code remains the execution engine (Playwright, FFmpeg, GenAI). Electron + React only provides the cyberpunk UI, streams logs, and passes environment variables to the Python scripts. The legacy `sora_suite/frontend` copy has been removed to avoid having two competing React folders; use the root-level `src/` + `electron/` only.

## Prerequisites
- Python 3.10+
- Node.js 20+
- Chrome/Chromium running with `--remote-debugging-port=9222` for the downloader

## Python setup
```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r sora_suite/requirements.txt
python -m playwright install chromium
```

## Node/Electron setup
```bash
npm install
```

### One-shot setup, build, and launch
Run everything (Python deps, Playwright browser, Node deps, build, and the packaged app) in one command:
```bash
./scripts/full_setup.sh
```
Set `PYTHON=/path/to/python` if you need a non-default interpreter.

### Development
Run Vite + Electron in watch mode (renders from the dev server and rebuilds the main/preload process automatically):
```bash
npm run dev
```
The renderer lives at <http://localhost:5173> and Electron will load it via `VITE_DEV_SERVER_URL`.

### Production build
```bash
npm run build   # outputs dist/ for the renderer and dist-electron/ for main/preload
npm run preview # optional, serve the built renderer only
```
Start the packaged app locally after a build:
```bash
npm run app:dist
```

## How it works
- Electron spawns `python -m sora_suite.bridge` through IPC (`runPython`).
- The `bridge.py` adapter turns renderer inputs into environment variables (e.g., `SORA_PROMPTS_FILE`, `SORA_CDP_ENDPOINT`, `DOWNLOAD_DIR`, `MAX_VIDEOS`, `GENAI_IMAGES_ONLY`).
- `bridge.py` calls the existing workers:
  - `sora_suite.workers.autogen.main.main()`
  - `sora_suite.workers.downloader.download_all.main()`
  - `sora_suite.workers.watermark_cleaner.restore.main()`
  - `sora_suite.workers.uploader.upload_queue.main()`
  - `sora_suite.workers.tiktok.upload_queue.main()`
- stdout/stderr from Python are streamed back to the React UI in real time.

## UI mapping to worker settings
- **Prompt textarea** → written to a temp file and passed as `SORA_PROMPTS_FILE`.
- **Explicit prompt/log paths** → `SORA_PROMPTS_FILE`, `SORA_SUBMITTED_LOG`, `SORA_FAILED_LOG`, `GENAI_IMAGE_PROMPTS_FILE`.
- **CDP endpoint** → `SORA_CDP_ENDPOINT` + `CDP_ENDPOINT`.
- **Downloads dir** → `DOWNLOAD_DIR`; **titles files** → `TITLES_FILE`, `TITLES_CURSOR_FILE`.
- **Max videos** → `MAX_VIDEOS` (0 = all drafts).
- **Open drafts first** → `OPEN_DRAFTS_FIRST`.
- **Generate images only** → `GENAI_IMAGES_ONLY=1`.
- **Attach GenAI output to Sora** → `GENAI_ATTACH_TO_SORA`.
- **Run downloader after autogen** → runs `download_all.py` after `autogen/main.py` completes.
- **Watermark cleaner** → `WMR_SOURCE_DIR`, `WMR_OUTPUT_DIR`, `WMR_TEMPLATE`.
- **YouTube/TikTok queues** → `APP_CONFIG_PATH`, `YOUTUBE_CHANNEL_NAME` + `YOUTUBE_SRC_DIR`, `TIKTOK_PROFILE_NAME` + `TIKTOK_SRC_DIR`.
- **Extra env textarea** → merges arbitrary `KEY=VALUE` pairs into the worker environment.

## Notes
- The legacy PyQt6 interface is removed from the dependency list; only the Python worker stack is required.
- Keep Playwright browsers installed (`python -m playwright install chromium`).
- You can override the Python binary via `PYTHON` or `PYTHON_PATH` when launching Electron.
