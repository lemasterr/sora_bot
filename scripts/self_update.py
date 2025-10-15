#!/usr/bin/env python3
"""CLI утилита для обновления Sora Suite через git."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_git(args: list[str]) -> subprocess.CompletedProcess[str]:
    git = shutil.which("git")
    if not git:
        raise RuntimeError("git не найден в PATH")
    return subprocess.run([git, *args], cwd=ROOT, capture_output=True, text=True)


def main() -> int:
    if not (ROOT / ".git").exists():
        print("Этот каталог не является git-репозиторием", file=sys.stderr)
        return 1
    try:
        fetch = run_git(["fetch", "--all", "--tags"])
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1
    if fetch.returncode != 0:
        print(fetch.stderr or fetch.stdout, file=sys.stderr)
        return fetch.returncode
    branch = run_git(["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip() or "main"
    pull = run_git(["pull", "--ff-only"])
    if pull.returncode == 0:
        print(f"Обновление {branch}: {pull.stdout.strip() or 'up to date'}")
    else:
        print(pull.stderr or pull.stdout, file=sys.stderr)
    return pull.returncode


if __name__ == "__main__":
    raise SystemExit(main())
