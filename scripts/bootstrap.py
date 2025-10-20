#!/usr/bin/env python3
"""Установка зависимостей Sora Suite и Playwright."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQ = ROOT / "sora_suite" / "requirements.txt"


def run(cmd: list[str]) -> int:
    proc = subprocess.run(cmd, cwd=ROOT)
    return proc.returncode


def main() -> int:
    python = sys.executable
    if not REQ.exists():
        print(f"requirements.txt не найден: {REQ}", file=sys.stderr)
        return 1
    print("[bootstrap] Устанавливаем Python-зависимости…", flush=True)
    rc = run([python, "-m", "pip", "install", "-r", str(REQ)])
    if rc != 0:
        return rc
    print("[bootstrap] Устанавливаем браузер Playwright (chromium)…", flush=True)
    rc = run([python, "-m", "playwright", "install", "chromium"])
    if rc != 0:
        return rc
    print("[bootstrap] Готово", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
