"""Bridge between Electron IPC and Python worker entry points."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict


def _write_prompt_file(prompt_text: str) -> str:
    text = prompt_text.strip()
    if not text:
        return ""
    temp_dir = Path(tempfile.mkdtemp(prefix="sora_prompts_"))
    path = temp_dir / "prompts.txt"
    path.write_text(text, encoding="utf-8")
    return str(path)


def apply_env(payload: Dict[str, Any]) -> None:
    if prompt := str(payload.get("prompt_text", "")).strip():
        os.environ["SORA_PROMPTS_FILE"] = _write_prompt_file(prompt)

    if cdp := str(payload.get("cdp_endpoint", "")).strip():
        os.environ["SORA_CDP_ENDPOINT"] = cdp
        os.environ["CDP_ENDPOINT"] = cdp

    if downloads := str(payload.get("downloads_dir", "")).strip():
        os.environ["DOWNLOAD_DIR"] = downloads

    max_videos = payload.get("max_videos")
    if max_videos is not None:
        try:
            os.environ["MAX_VIDEOS"] = str(int(max_videos))
        except (TypeError, ValueError):
            pass

    if payload.get("open_drafts_first") is not None:
        os.environ["OPEN_DRAFTS_FIRST"] = "1" if bool(payload.get("open_drafts_first")) else "0"

    if payload.get("images_only"):
        os.environ["GENAI_IMAGES_ONLY"] = "1"
    else:
        os.environ.pop("GENAI_IMAGES_ONLY", None)

    if payload.get("attach_to_sora") is not None:
        os.environ["GENAI_ATTACH_TO_SORA"] = "1" if bool(payload.get("attach_to_sora")) else "0"


def run_autogen() -> None:
    from sora_suite.workers.autogen import main as autogen_main

    autogen_main.main()


def run_downloader() -> None:
    from sora_suite.workers.downloader import download_all

    download_all.main()


def run_pipeline(payload: Dict[str, Any]) -> None:
    print("[BRIDGE] -> starting autogen")
    run_autogen()
    if payload.get("run_downloader"):
        print("[BRIDGE] -> starting downloader")
        run_downloader()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sora Suite bridge entrypoint")
    parser.add_argument("--task", choices=["pipeline", "autogen", "downloader"], default="pipeline")
    parser.add_argument("--payload", default="{}", help="JSON payload passed from Electron")
    args = parser.parse_args(argv)

    try:
        payload = json.loads(args.payload) if args.payload else {}
        if not isinstance(payload, dict):
            raise ValueError("payload must be a JSON object")
    except Exception as exc:  # noqa: BLE001
        print(f"[BRIDGE] invalid payload: {exc}")
        return 1

    apply_env(payload)

    try:
        if args.task == "autogen":
            run_autogen()
        elif args.task == "downloader":
            run_downloader()
        else:
            run_pipeline(payload)
    except Exception as exc:  # noqa: BLE001
        print(f"[BRIDGE] fatal error: {exc}")
        return 1

    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
