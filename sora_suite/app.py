#!/usr/bin/env python3
"""
Launch the rewritten Sora Suite interface.

The old PyQt6 shell has been removed in favor of the new Electron/React UI.
This script only serves the built frontend from ``sora_suite/frontend/dist``
and opens it in the default browser. Build the UI once with:

    cd sora_suite/frontend
    npm install
    npm run build

Then start the app:

    python -m sora_suite.app

Use ``--dev`` to proxy to a running Vite dev server instead of serving the
static build.
"""
from __future__ import annotations

import argparse
import contextlib
import socket
import sys
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

APP_DIR = Path(__file__).parent.resolve()
FRONTEND_DIR = APP_DIR / "frontend"
DIST_DIR = FRONTEND_DIR / "dist"
DEFAULT_PORT = 5173


class _SilentHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args):  # noqa: A003 - matches base signature
        # Keep stdout clean; server status is printed manually.
        pass


def _find_open_port(preferred: int = DEFAULT_PORT) -> int:
    for candidate in (preferred, preferred + 1, 0):
        with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", candidate))
            except OSError:
                continue
            return sock.getsockname()[1]
    raise RuntimeError("Unable to allocate a port for the UI server")


def _ensure_build_exists() -> Path:
    index = DIST_DIR / "index.html"
    if index.exists():
        return DIST_DIR

    tip = (
        f"Собери новый интерфейс перед запуском: \n"
        f"  cd {FRONTEND_DIR}\n"
        "  npm install\n"
        "  npm run build\n"
    )
    raise SystemExit(
        "Новый интерфейс не найден. Ожидается сборка в frontend/dist.\n" + tip,
    )


def _serve_static(host: str, port: int, directory: Path) -> ThreadingHTTPServer:
    handler = partial(_SilentHandler, directory=str(directory))
    httpd = ThreadingHTTPServer((host, port), handler)
    return httpd


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Запуск нового интерфейса Sora Suite")
    parser.add_argument("--host", default="127.0.0.1", help="Хост для локального сервера (по умолчанию 127.0.0.1)")
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Порт для интерфейса (по умолчанию {DEFAULT_PORT}, автоматически меняется если занят)",
    )
    parser.add_argument(
        "--dev",
        action="store_true",
        help="Не поднимать сервер, а открыть запущенный Vite dev server на порту 5173",
    )
    parser.add_argument("--no-browser", action="store_true", help="Не открывать вкладку автоматически")
    args = parser.parse_args(argv)

    if args.dev:
        url = f"http://{args.host}:{DEFAULT_PORT}"
        print(f"Открываю dev-сервер Vite: {url}")
        if not args.no_browser:
            webbrowser.open(url)
        return 0

    dist_dir = _ensure_build_exists()
    port = _find_open_port(args.port)
    server = _serve_static(args.host, port, dist_dir)
    url = f"http://{args.host}:{port}"

    print(f"Новый интерфейс Sora Suite запущен: {url}")
    print(f"Статика: {dist_dir}")

    if not args.no_browser:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Остановлено пользователем")
    finally:
        server.server_close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
