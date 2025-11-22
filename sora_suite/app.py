#!/usr/bin/env python3
"""
Launch the new Electron + React shell for Sora Suite.

The legacy PyQt window has been removed. This entry point now starts the
Electron main process so the modern UI opens as a desktop application instead
of a browser tab.

Build the UI once and install dependencies:

    cd sora_suite/frontend
    npm install
    npm run build

Then start the desktop shell:

    python -m sora_suite.app

Use ``--dev`` to point Electron at a running Vite dev server instead of the
static production build.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

APP_DIR = Path(__file__).parent.resolve()
FRONTEND_DIR = APP_DIR / "frontend"
DIST_DIR = FRONTEND_DIR / "dist"
DEFAULT_PORT = 5173


def _electron_binary() -> Path | None:
    candidate = FRONTEND_DIR / "node_modules" / ".bin" / ("electron.cmd" if os.name == "nt" else "electron")
    if candidate.exists():
        return candidate
    fallback = shutil.which("electron")
    return Path(fallback) if fallback else None


def _ensure_build_exists() -> Path:
    if DIST_DIR.exists() and (DIST_DIR / "index.html").exists():
        return DIST_DIR

    tip = (
        "Новый интерфейс не найден. Ожидается сборка в frontend/dist.\n"
        "Соберите UI командой:\n"
        "  cd sora_suite/frontend\n"
        "  npm install\n"
        "  npm run build\n"
    )
    raise SystemExit("Новый интерфейс не найден. Ожидается сборка в frontend/dist.\n" + tip)


def _ensure_electron_available() -> Path:
    electron = _electron_binary()
    if not electron:
        tip = (
            "Electron не найден. Установите зависимости UI: \n"
            "  cd sora_suite/frontend && npm install\n"
            "Это требуется для запуска нового десктопного интерфейса."
        )
        raise SystemExit(tip)
    return electron


def _launch_electron(electron: Path, env: dict[str, str]) -> int:
    completed = subprocess.run([str(electron), str(FRONTEND_DIR / "electron-main.js")], cwd=FRONTEND_DIR, env=env)
    return completed.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Запуск нового интерфейса Sora Suite")
    parser.add_argument("--host", default="127.0.0.1", help="Хост для локального сервера (по умолчанию 127.0.0.1)")
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Порт для интерфейса (по умолчанию {DEFAULT_PORT})",
    )
    parser.add_argument("--dev", action="store_true", help="Подключить Electron к dev-серверу Vite вместо сборки")
    args = parser.parse_args(argv)

    env = os.environ.copy()
    electron = _ensure_electron_available()

    if args.dev:
        env["ELECTRON_START_URL"] = f"http://{args.host}:{args.port}"
        print(f"Запуск Electron и подключение к dev-серверу Vite: {env['ELECTRON_START_URL']}")
        return _launch_electron(electron, env)

    dist_dir = _ensure_build_exists()
    env["ELECTRON_DIST_DIR"] = str(dist_dir)
    print(f"Запуск десктопного интерфейса из сборки: {dist_dir}")
    return _launch_electron(electron, env)


if __name__ == "__main__":
    sys.exit(main())
