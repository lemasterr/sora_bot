# -*- coding: utf-8 -*-
"""
portable_config.py — кроссплатформенная автоконфигурация:
- Поиск Chrome/Chromium/Edge;
- Определение профиля и user-data-dir;
- Создание папок проекта;
- Поднятие/проверка CDP (ensure_cdp_endpoint).
"""
from __future__ import annotations
import os, sys, platform, socket, time, subprocess, shutil
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

import yaml  # PyYAML
from platformdirs import user_documents_dir

DEFAULT_APP_NAME = "sora_suite"

def _env(name: str, default: Optional[str]=None) -> Optional[str]:
    v = os.environ.get(name)
    if v is None or v.strip() == "":
        return default
    return v

def _is_port_open(host: str, port: int, timeout=0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False

def _find_binary(candidates: List[str]) -> Optional[str]:
    env_bin = _env("CHROME_BINARY")
    if env_bin and Path(env_bin).exists():
        return env_bin
    for c in candidates:
        if Path(c).exists():
            return c
        hit = shutil.which(c)
        if hit:
            return hit
    return None

def _chrome_candidates() -> List[str]:
    system = platform.system().lower()
    cands: List[str] = []
    if system == "darwin":
        cands += [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "google-chrome", "chromium", "chrome", "Microsoft Edge"
        ]
    elif system == "windows":
        local = os.environ.get("LOCALAPPDATA", r"C:\Users\%USERNAME%\AppData\Local")
        prog = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        progx86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
        cands += [
            str(Path(local) / "Google/Chrome/Application/chrome.exe"),
            str(Path(prog) / "Google/Chrome/Application/chrome.exe"),
            str(Path(progx86) / "Google/Chrome/Application/chrome.exe"),
            str(Path(local) / "Microsoft/Edge/Application/msedge.exe"),
            "chrome", "msedge", "chromium"
        ]
    else:  # linux
        cands += [
            "/usr/bin/google-chrome-stable", "/usr/bin/google-chrome",
            "/snap/bin/chromium", "/usr/bin/chromium", "/usr/bin/chromium-browser",
            "google-chrome-stable", "google-chrome", "chromium", "chromium-browser",
            "microsoft-edge", "microsoft-edge-stable"
        ]
    return cands

def _default_profile_dirs(browser_name: str="Chrome") -> Tuple[Path, str]:
    system = platform.system().lower()
    if system == "darwin":
        base = Path.home() / "Library/Application Support/Google/Chrome"
        return base, "Default"
    elif system == "windows":
        base = Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/User Data"
        return base, "Default"
    else:  # linux
        base = Path.home() / ".config/google-chrome"
        return base, "Default"

@dataclass
class Paths:
    project_root: Path
    downloads_dir: Path
    blurred_dir: Path
    merged_dir: Path
    history_file: Path
    titles_file: Path

@dataclass
class ChromeConfig:
    cdp_host: str = "127.0.0.1"
    cdp_port: int = 9222
    binary: Optional[str] = None
    user_data_dir: Optional[Path] = None
    profile_directory: Optional[str] = None

@dataclass
class SuiteConfig:
    sora_url: str = "https://sora.chatgpt.com/drafts"
    paths: Paths = None  # type: ignore
    chrome: ChromeConfig = ChromeConfig()
    dom_timeout_ms: int = 12000
    poll_interval_ms: int = 1500
    start_confirmation_timeout_ms: int = 8000
    human_typing_delay_ms: int = 12
    debug: bool = True

def load_config(project_root: Optional[Path]=None) -> SuiteConfig:
    project_root = project_root or Path(_env("SORA_SUITE_ROOT") or Path(user_documents_dir()) / DEFAULT_APP_NAME)
    project_root = project_root.expanduser().resolve()
    app_config_file = Path("app_config.yaml")
    app_yml = yaml.safe_load(app_config_file.read_text(encoding="utf-8")) if app_config_file.exists() else {}

    docs = Path(user_documents_dir())
    default_root = Path(_env("SORA_SUITE_ROOT") or (docs / "sora_suite"))
    downloads_dir = Path(_env("DOWNLOADS_DIR") or app_yml.get("downloads_dir") or (default_root / "downloads"))
    blurred_dir = Path(_env("BLURRED_DIR") or app_yml.get("blurred_dir") or (default_root / "blurred"))
    merged_dir = Path(_env("MERGED_DIR") or app_yml.get("merged_dir") or (default_root / "merged"))
    history_file = Path(_env("HISTORY_FILE") or app_yml.get("history_file") or (default_root / "history.jsonl"))
    titles_file = Path(_env("TITLES_FILE") or app_yml.get("titles_file") or (default_root / "titles.txt"))
    for p in [project_root, default_root, downloads_dir, blurred_dir, merged_dir, history_file.parent]:
        p.mkdir(parents=True, exist_ok=True)

    cdp_port = int(_env("CDP_PORT", str(app_yml.get("chrome", {}).get("cdp_port", 9222))))
    binary = _env("CHROME_BINARY", app_yml.get("chrome", {}).get("binary"))
    if not binary:
        binary = _find_binary(_chrome_candidates())
    user_data_dir_guess, profile_guess = _default_profile_dirs()
    user_data_dir = Path(_env("CHROME_USER_DATA_DIR", app_yml.get("chrome", {}).get("user_data_dir", str(user_data_dir_guess)))).expanduser()
    profile_directory = _env("CHROME_PROFILE", app_yml.get("chrome", {}).get("profile_directory", profile_guess))

    chrome = ChromeConfig(
        cdp_host="127.0.0.1",
        cdp_port=cdp_port,
        binary=binary,
        user_data_dir=user_data_dir,
        profile_directory=profile_directory
    )

    return SuiteConfig(
        paths=Paths(
            project_root=project_root,
            downloads_dir=downloads_dir,
            blurred_dir=blurred_dir,
            merged_dir=merged_dir,
            history_file=history_file,
            titles_file=titles_file
        ),
        chrome=chrome
    )

def ensure_cdp_endpoint(cfg: SuiteConfig) -> str:
    host = cfg.chrome.cdp_host
    port = cfg.chrome.cdp_port
    endpoint = f"http://{host}:{port}"
    if _is_port_open(host, port):
        return endpoint

    binary = cfg.chrome.binary or _find_binary(_chrome_candidates())
    if not binary:
        raise RuntimeError("Chrome/Chromium binary not found. Set CHROME_BINARY env or install Chrome.")

    udir = cfg.chrome.user_data_dir or _default_profile_dirs()[0]
    profile_dir = cfg.chrome.profile_directory or "Default"
    Path(udir).mkdir(parents=True, exist_ok=True)

    args = [
        binary,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={str(udir)}",
        f"--profile-directory={profile_dir}",
        "--disable-features=AutomationControlled",
        "--no-first-run",
        "--no-default-browser-check",
        "--start-maximized"
    ]

    try:
        if platform.system().lower() == "windows":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS  # type: ignore
            subprocess.Popen(args, creationflags=creationflags)  # noqa
        else:
            subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # noqa
    except Exception as e:
        raise RuntimeError(f"Failed to launch Chrome: {e}")

    deadline = time.time() + 15
    while time.time() < deadline:
        if _is_port_open(host, port):
            return endpoint
        time.sleep(0.25)
    raise RuntimeError("Chrome failed to expose CDP in time.")
