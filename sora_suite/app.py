#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os  # FIX: нужен в load_cfg/_open_chrome
import re  # FIX: используется в _slot_log, _natural_key
import math
import sys
import json
import uuid
import webbrowser
try:
    import yaml
except ModuleNotFoundError as exc:
    tip = (
        "PyYAML не найден. Установи зависимости командой \n"
        "    python -m pip install -r sora_suite/requirements.txt\n"
        "и запусти приложение повторно."
    )
    raise SystemExit(f"{tip}\nИсходная ошибка: {exc}") from exc
import time
import threading
import subprocess
import socket
import shutil
from pathlib import Path
from functools import partial
from urllib.request import urlopen, Request
from collections import deque
from typing import Optional, List, Union, Tuple, Dict, Callable, Any, Set, Iterable

from PyQt6 import QtCore, QtGui, QtWidgets

from concurrent.futures import ThreadPoolExecutor
from threading import Lock

# ---------- базовые пути ----------
PROMPTS_DEFAULT_KEY = "__general__"

APP_DIR = Path(__file__).parent.resolve()
CFG_PATH = APP_DIR / "app_config.yaml"
PROJECT_ROOT = APP_DIR  # корень хранения по умолчанию совпадает с директорией приложения
WORKERS_DIR = PROJECT_ROOT / "workers"
DL_DIR = PROJECT_ROOT / "downloads"
BLUR_DIR = PROJECT_ROOT / "blurred"
MERG_DIR = PROJECT_ROOT / "merged"
IMAGES_DIR = PROJECT_ROOT / "generated_images"
HIST_FILE = PROJECT_ROOT / "history.jsonl"   # JSONL по-умолчанию (с обратн. совместимостью)
TITLES_FILE = PROJECT_ROOT / "titles.txt"


# ---------- утилиты ----------


def _coerce_int(value) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, str):
        clean = value.strip()
        if not clean:
            return None
        # поддержка значений вроде "128px" или "45.6%"
        match = re.search(r"-?\d+(?:\.\d+)?", clean)
        if match:
            clean = match.group(0)
        value = clean
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):  # noqa: PERF203
        return None


def _pick_first(zone: Dict, keys: Tuple[str, ...]) -> Optional[int]:
    for key in keys:
        if key in zone:
            val = _coerce_int(zone.get(key))
            if val is not None:
                return val
    return None


def _as_zone_sequence(body: object) -> List[Dict]:
    """Вернёт список зон из произвольных старых структур конфигурации."""
    if body is None:
        return []
    if isinstance(body, list):
        return list(body)
    if isinstance(body, tuple):
        return list(body)
    if isinstance(body, dict):
        # классический формат {"zones": [...]}
        if "zones" in body:
            zones = body.get("zones")
            if isinstance(zones, dict):
                # YAML мог сохранить список как отображение
                try:
                    # сортируем по ключу, чтобы сохранялся порядок
                    return [zones[k] for k in sorted(zones.keys(), key=str)]
                except Exception:
                    return list(zones.values())
            if isinstance(zones, (list, tuple)):
                return list(zones)
        # формат, где сама запись является зоной
        keys = set(k.lower() for k in body.keys())
        if {"x", "y"}.issubset(keys) and ({"w", "h"}.issubset(keys) or {"width", "height"}.issubset(keys)):
            return [body]
        # вложенные словари с координатами
        for candidate_key in ("rect", "zone", "coords", "geometry"):
            candidate = body.get(candidate_key)
            if isinstance(candidate, dict):
                return [candidate]
        return []
    return []


def normalize_zone(zone: object) -> Optional[Dict[str, int]]:
    if not isinstance(zone, dict):
        return None

    enabled = zone.get("enabled")
    if isinstance(enabled, str):
        if enabled.lower() in {"false", "0", "off", "no"}:
            return None
    elif enabled is False:
        return None

    x = _pick_first(zone, ("x", "left", "start_x", "sx"))
    y = _pick_first(zone, ("y", "top", "start_y", "sy"))
    w = _pick_first(zone, ("w", "width"))
    h = _pick_first(zone, ("h", "height"))
    right = _pick_first(zone, ("right", "x2", "end_x"))
    bottom = _pick_first(zone, ("bottom", "y2", "end_y"))

    if w is None and right is not None and x is not None:
        w = right - x
    if h is None and bottom is not None and y is not None:
        h = bottom - y

    x = max(0, x or 0)
    y = max(0, y or 0)
    w = w if w is not None else 0
    h = h if h is not None else 0

    if w <= 0 or h <= 0:
        return None

    return {"x": int(x), "y": int(y), "w": int(w), "h": int(h)}


def normalize_zone_list(zones: Optional[List[Dict]]) -> List[Dict[str, int]]:
    normalized: List[Dict[str, int]] = []
    if not zones:
        return normalized
    for zone in zones:
        norm = normalize_zone(zone)
        if norm:
            normalized.append(norm)
    return normalized


def normalize_custom_commands(raw: object) -> List[Dict[str, str]]:
    """Приводит кастомные команды к ожидаемому виду."""

    result: List[Dict[str, str]] = []
    if not isinstance(raw, list):
        return result
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("name") or entry.get("title") or "").strip()
        command = str(entry.get("command") or "").strip()
        description = str(entry.get("description") or entry.get("subtitle") or "").strip()
        if not title or not command:
            continue
        result.append({"name": title, "command": command, "description": description})
    return result


def load_cfg() -> dict:
    if not CFG_PATH.exists():
        raise FileNotFoundError(f"Создай конфиг {CFG_PATH}")
    with open(CFG_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    # --- project paths defaults ---
    data.setdefault("project_root", str(PROJECT_ROOT))
    data.setdefault("downloads_dir", str(DL_DIR))
    data.setdefault("blurred_dir", str(BLUR_DIR))
    data.setdefault("merged_dir", str(MERG_DIR))
    data.setdefault("history_file", str(HIST_FILE))
    data.setdefault("titles_file", str(TITLES_FILE))

    # источники по-умолчанию (новые ключи)
    data.setdefault("blur_src_dir", data.get("downloads_dir", str(DL_DIR)))   # откуда брать на BLUR
    data.setdefault("merge_src_dir", data.get("blurred_dir", str(BLUR_DIR)))  # откуда брать на MERGE

    # --- workers ---
    autogen = data.setdefault("autogen", {})
    autogen.setdefault("workdir", str(WORKERS_DIR / "autogen"))
    autogen.setdefault("entry", "main.py")
    autogen.setdefault("config_path", str(WORKERS_DIR / "autogen" / "config.yaml"))
    autogen.setdefault("submitted_log", str(WORKERS_DIR / "autogen" / "submitted.log"))
    autogen.setdefault("failed_log", str(WORKERS_DIR / "autogen" / "failed.log"))
    autogen.setdefault("instances", [])
    sessions = autogen.setdefault("sessions", [])
    if isinstance(sessions, dict):
        # старый формат, где сессии хранились как отображение
        autogen["sessions"] = list(sessions.values())
    elif not isinstance(sessions, list):
        autogen["sessions"] = []
    autogen.setdefault("active_prompts_profile", PROMPTS_DEFAULT_KEY)
    autogen.setdefault("image_prompts_file", str(WORKERS_DIR / "autogen" / "image_prompts.txt"))

    genai = data.setdefault("google_genai", {})
    genai.setdefault("enabled", False)
    genai.setdefault("api_key", "")
    genai.setdefault("model", "models/imagen-4.0-generate-001")
    genai.setdefault("aspect_ratio", "1:1")
    genai.setdefault("image_size", "1K")
    genai.setdefault("number_of_images", 1)
    genai.setdefault("output_dir", str(IMAGES_DIR))
    genai.setdefault("manifest_file", str(Path("generated_images") / "manifest.json"))
    genai.setdefault("rate_limit_per_minute", 0)
    genai.setdefault("max_retries", 3)
    genai.setdefault("person_generation", "")
    genai.setdefault("output_mime_type", "image/jpeg")
    genai.setdefault("attach_to_sora", True)
    genai.setdefault("seeds", "")
    genai.setdefault("consistent_character_design", False)
    genai.setdefault("lens_type", "")
    genai.setdefault("color_palette", "")
    genai.setdefault("style", "")
    genai.setdefault("reference_prompt", "")
    genai.setdefault("notifications_enabled", True)
    genai.setdefault("daily_quota", 0)
    genai.setdefault("quota_warning_prompts", 5)
    genai.setdefault("quota_enforce", False)
    genai.setdefault("usage_file", str(Path("generated_images") / "usage.json"))

    downloader = data.setdefault("downloader", {})
    downloader.setdefault("workdir", str(WORKERS_DIR / "downloader"))
    downloader.setdefault("entry", "download_all.py")
    downloader.setdefault("max_videos", 0)
    downloader.setdefault("open_drafts", True)

    automator = data.setdefault("automator", {})
    automator.setdefault("steps", [])

    # --- ffmpeg ---
    ff = data.setdefault("ffmpeg", {})
    ff.setdefault("binary", "ffmpeg")
    ff.setdefault("post_chain", "boxblur=1:1,noise=alls=2:allf=t,unsharp=3:3:0.5:3:3:0.0")
    ff.setdefault("vcodec", "auto_hw")
    ff.setdefault("crf", 18)
    ff.setdefault("preset", "veryfast")
    ff.setdefault("format", "mp4")
    ff.setdefault("copy_audio", True)
    ff.setdefault("blur_threads", 2)
    auto_wm = ff.setdefault("auto_watermark", {})
    auto_wm.setdefault("enabled", False)
    auto_wm.setdefault("template", "")
    auto_wm.setdefault("threshold", 0.75)
    auto_wm.setdefault("frames", 5)
    auto_wm.setdefault("downscale", 0)

    wm_probe = data.setdefault("watermark_probe", {})
    wm_probe.setdefault("source_dir", data.get("downloads_dir", str(DL_DIR)))
    wm_probe.setdefault("output_dir", str(PROJECT_ROOT / "restored"))
    wm_probe.setdefault("region", {"x": 0, "y": 0, "w": 320, "h": 120})
    wm_probe.setdefault("frames", 120)
    wm_probe.setdefault("brightness_threshold", 245)
    wm_probe.setdefault("coverage_ratio", 0.002)
    wm_probe.setdefault("method", "hybrid")
    wm_probe.setdefault("edge_ratio", 0.006)
    wm_probe.setdefault("min_hits", 1)
    wm_probe.setdefault("downscale", 2.0)
    wm_probe.setdefault("flip_when", "missing")
    wm_probe.setdefault("flip_direction", "left")
    auto_wm.setdefault("bbox_padding", 12)
    auto_wm.setdefault("bbox_padding_pct", 0.15)
    auto_wm.setdefault("bbox_min_size", 48)
    presets = ff.setdefault("presets", {})
    if isinstance(presets, list):
        migrated: Dict[str, dict] = {}
        for idx, entry in enumerate(presets):
            if isinstance(entry, dict):
                name = entry.get("name") or f"preset_{idx+1}"
                migrated[name] = entry
        presets = migrated
        ff["presets"] = presets

    presets.setdefault("portrait_9x16", {
        "zones": [
            {"x": 30,  "y": 105,  "w": 157, "h": 62},
            {"x": 515, "y": 610,  "w": 157, "h": 62},
            {"x": 30,  "y": 1110, "w": 157, "h": 62},
        ]
    })
    presets.setdefault("landscape_16x9", {
        "zones": [
            {"x": 40,  "y": 60,  "w": 175, "h": 65},
            {"x": 1060,"y": 320, "w": 175, "h": 65},
            {"x": 40,  "y": 580, "w": 175, "h": 65},
        ]
    })
    sanitized_presets: Dict[str, Dict[str, List[Dict[str, int]]]] = {}
    for name, body in list(presets.items()):
        raw_list = _as_zone_sequence(body)
        norm = normalize_zone_list(raw_list)
        display = norm or [{"x": 0, "y": 0, "w": 0, "h": 0}]
        sanitized_presets[name] = {"zones": [dict(zone) for zone in display]}
    ff["presets"] = sanitized_presets
    ff.setdefault("active_preset", "portrait_9x16")

    # --- merge ---
    data.setdefault("merge", {"group_size": 3, "pattern": "*.mp4"})

    # --- chrome + профили ---
    ch = data.setdefault("chrome", {})
    ch.setdefault("cdp_port", 9222)
    if sys.platform == "darwin":
        default_chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    elif sys.platform.startswith("win"):
        default_chrome = r"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
    else:
        default_chrome = "google-chrome"
    ch.setdefault("binary", default_chrome)
    if not ch.get("binary"):
        ch["binary"] = default_chrome
    else:
        ch["binary"] = os.path.expandvars(ch["binary"])  # поддержка Windows %LOCALAPPDATA%
    ch.setdefault("profiles", [])
    for prof in ch.get("profiles", []) or []:
        if not isinstance(prof, dict):
            continue
        port = _coerce_int(prof.get("cdp_port"))
        if port and port > 0:
            prof["cdp_port"] = int(port)
        else:
            prof.pop("cdp_port", None)
    ch.setdefault("active_profile", "")

    # Fallback: если профилей нет, но задан старый user_data_dir — поднимем Imported
    if not ch["profiles"] and ch.get("user_data_dir"):
        udd = ch.get("user_data_dir")
        prof_dir = "Default" if os.path.basename(udd) == "Chrome" else os.path.basename(udd)
        root = udd if prof_dir == "Default" else os.path.dirname(udd)
        ch["profiles"] = [{
            "name": "Imported",
            "user_data_dir": root,
            "profile_directory": prof_dir
        }]
        ch["active_profile"] = "Imported"

    youtube = data.setdefault("youtube", {})
    youtube.setdefault("workdir", str(WORKERS_DIR / "uploader"))
    youtube.setdefault("entry", "upload_queue.py")
    youtube.setdefault("channels", [])
    youtube.setdefault("active_channel", "")
    youtube.setdefault("upload_src_dir", data.get("merged_dir", str(MERG_DIR)))
    youtube.setdefault("schedule_minutes_from_now", 60)
    youtube.setdefault("draft_only", False)
    youtube.setdefault("archive_dir", str(PROJECT_ROOT / "uploaded"))
    youtube.setdefault("batch_step_minutes", 60)
    youtube.setdefault("batch_limit", 0)
    youtube.setdefault("last_publish_at", "")

    tiktok = data.setdefault("tiktok", {})
    tiktok.setdefault("workdir", str(WORKERS_DIR / "tiktok"))
    tiktok.setdefault("entry", "upload_queue.py")
    tiktok.setdefault("profiles", [])
    tiktok.setdefault("active_profile", "")
    tiktok.setdefault("upload_src_dir", data.get("merged_dir", str(MERG_DIR)))
    tiktok.setdefault("archive_dir", str(PROJECT_ROOT / "uploaded_tiktok"))
    tiktok.setdefault("schedule_minutes_from_now", 0)
    tiktok.setdefault("schedule_enabled", True)
    tiktok.setdefault("batch_step_minutes", 60)
    tiktok.setdefault("batch_limit", 0)
    tiktok.setdefault("draft_only", False)
    tiktok.setdefault("last_publish_at", "")
    tiktok.setdefault("github_workflow", ".github/workflows/tiktok-upload.yml")
    tiktok.setdefault("github_ref", "main")
    for prof in tiktok.get("profiles", []) or []:
        if isinstance(prof, dict):
            if prof.get("cookies_file") and not prof.get("credentials_file"):
                prof["credentials_file"] = prof.get("cookies_file")
            prof.pop("cookies_file", None)

    telegram = data.setdefault("telegram", {})
    telegram.setdefault("enabled", False)
    telegram.setdefault("bot_token", "")
    telegram.setdefault("chat_id", "")
    telegram.setdefault("templates", [])
    telegram.setdefault("last_template", "")
    telegram.setdefault("quick_delay_minutes", 0)

    maintenance = data.setdefault("maintenance", {})
    maintenance.setdefault("auto_cleanup_on_start", False)
    retention = maintenance.setdefault("retention_days", {})
    retention.setdefault("downloads", 7)
    retention.setdefault("blurred", 14)
    retention.setdefault("merged", 30)

    ui = data.setdefault("ui", {})
    ui.setdefault("show_activity", True)
    ui.setdefault("accent_kind", "info")
    ui.setdefault("activity_density", "compact")
    ui.setdefault("show_context", True)
    ui.setdefault("custom_commands", [])
    ui["custom_commands"] = normalize_custom_commands(ui.get("custom_commands"))

    return data


def slugify(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower())
    value = re.sub(r"-+", "-", value).strip("-")
    return value or "instance"


def normalize_session_list(raw_sessions: object) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    if not isinstance(raw_sessions, list):
        return normalized

    seen_ids: Set[str] = set()
    for idx, item in enumerate(raw_sessions, start=1):
        if not isinstance(item, dict):
            continue
        session = dict(item)
        sid = str(session.get("id") or "").strip()
        if not sid:
            sid = uuid.uuid4().hex[:8]
        while sid in seen_ids:
            sid = uuid.uuid4().hex[:8]
        session["id"] = sid
        seen_ids.add(sid)

        name = str(session.get("name") or session.get("title") or "").strip()
        if not name:
            name = f"Сессия {idx}"
        session["name"] = name

        session.setdefault("chrome_profile", "")
        session.setdefault("prompt_profile", PROMPTS_DEFAULT_KEY)
        session.setdefault("cdp_port", None)
        session.setdefault("prompts_file", "")
        session.setdefault("image_prompts_file", "")
        session.setdefault("submitted_log", "")
        session.setdefault("failed_log", "")
        session.setdefault("notes", "")
        session.setdefault("auto_launch_chrome", False)
        session.setdefault("auto_launch_autogen", "idle")
        session.setdefault("download_dir", "")
        session.setdefault("clean_dir", "")
        session.setdefault("titles_file", "")
        session.setdefault("cursor_file", "")
        session.setdefault("max_videos", 0)
        session.setdefault("open_drafts", True)
        session["max_videos"] = int(_coerce_int(session.get("max_videos")) or 0)

        normalized.append(session)

    return normalized


def normalize_automator_steps(raw_steps: object) -> List[Dict[str, Any]]:
    """Приводит шаги автоматизатора к ожидаемой структуре."""

    normalized: List[Dict[str, Any]] = []
    if not isinstance(raw_steps, list):
        return normalized

    valid_types = {
        "session_prompts",
        "session_images",
        "session_mix",
        "session_download",
        "session_watermark",
        "session_chrome",
        "global_blur",
        "global_merge",
        "global_watermark",
        "global_probe",
    }

    for item in raw_steps:
        if not isinstance(item, dict):
            continue
        step_type = str(item.get("type") or "").strip()
        if step_type not in valid_types:
            continue
        step: Dict[str, Any] = {"type": step_type}

        if step_type.startswith("session_"):
            sessions_raw = item.get("sessions")
            sessions: List[str] = []
            if isinstance(sessions_raw, (list, tuple)):
                for sid in sessions_raw:
                    if not sid:
                        continue
                    sid_str = str(sid).strip()
                    if not sid_str:
                        continue
                    if sid_str not in sessions:
                        sessions.append(sid_str)
            if not sessions:
                continue
            step["sessions"] = sessions
            if step_type == "session_download":
                limit = _coerce_int(item.get("limit")) or 0
                step["limit"] = max(0, limit)
        elif step_type == "global_merge":
            group = _coerce_int(item.get("group")) or 0
            if group < 0:
                group = 0
            step["group"] = group

        normalized.append(step)

    return normalized


def normalize_automator_presets(raw_presets: object) -> List[Dict[str, Any]]:
    """Нормализует список пресетов автоматизатора."""

    normalized: List[Dict[str, Any]] = []
    if not isinstance(raw_presets, list):
        return normalized

    seen_ids: Set[str] = set()
    for idx, item in enumerate(raw_presets, start=1):
        if not isinstance(item, dict):
            continue
        preset = dict(item)
        pid = str(preset.get("id") or "").strip() or uuid.uuid4().hex[:8]
        while pid in seen_ids:
            pid = uuid.uuid4().hex[:8]
        seen_ids.add(pid)
        preset["id"] = pid

        name = str(preset.get("name") or "").strip() or f"Пресет {idx}"
        preset["name"] = name

        steps = normalize_automator_steps(preset.get("steps"))
        preset["steps"] = steps
        normalized.append(preset)

    return normalized


ERROR_GUIDE: List[Tuple[str, str, str]] = [
    (
        "AUTOGEN_TIMEOUT",
        "Playwright не увидел поле ввода или кнопку Sora.",
        "Открой вкладку drafts, обнови селекторы в workers/autogen/selectors.yaml и перезапусти автоген.",
    ),
    (
        "AUTOGEN_REJECT",
        "Sora вернула ошибку очереди или лимита.",
        "Увеличь паузу backoff_seconds_on_reject в конфиге автогена или запусти генерацию позже.",
    ),
    (
        "DOWNLOAD_HTTP",
        "FFmpeg/yt-dlp не смогли скачать ролик.",
        "Проверь интернет, авторизацию в браузере и актуальность cookies профиля Chrome.",
    ),
    (
        "BLUR_CODEC",
        "FFmpeg не поддерживает исходный кодек или требуется перекодирование.",
        "Выбери preset libx264, включи перекодирование аудио и повтори обработку.",
    ),
    (
        "YOUTUBE_QUOTA",
        "YouTube API вернул ошибку квоты или авторизации.",
        "Проверь OAuth-ключи, refresh_token и лимиты API в Google Cloud Console.",
    ),
]


def save_cfg(cfg: dict):
    with open(CFG_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)


def _normalize_path(raw: Union[str, Path]) -> Path:
    return Path(os.path.expandvars(str(raw or ""))).expanduser()


def _project_path(raw: Union[str, Path]) -> Path:
    """Вернёт абсолютный путь в рамках проекта для относительных значений из конфига."""
    p = _normalize_path(raw)
    if p.is_absolute():
        try:
            return p.resolve()
        except Exception:
            return p
    try:
        return (PROJECT_ROOT / p).resolve()
    except Exception:
        return (PROJECT_ROOT / p)


def _same_path(a: Union[str, Path], b: Union[str, Path]) -> bool:
    try:
        pa = _normalize_path(a)
        pb = _normalize_path(b)
        return pa == pb
    except Exception:
        return str(a or "").strip() == str(b or "").strip()


def _human_size(num_bytes: int) -> str:
    step = 1024.0
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(max(num_bytes, 0))
    for unit in units:
        if size < step:
            return f"{size:.1f} {unit}"
        size /= step
    return f"{size:.1f} PB"


def _dir_size(path: Path) -> int:
    total = 0
    try:
        for root, dirs, files in os.walk(path):
            for name in files:
                try:
                    total += (Path(root) / name).stat().st_size
                except Exception:
                    continue
    except Exception:
        return total
    return total


def ensure_dirs(cfg: dict):
    root_path = _project_path(cfg.get("project_root", PROJECT_ROOT))
    root_path.mkdir(parents=True, exist_ok=True)
    cfg["project_root"] = str(root_path)

    def _ensure_dir(key: str, fallback: Union[str, Path]) -> Path:
        raw = cfg.get(key) or fallback
        path = _project_path(raw)
        path.mkdir(parents=True, exist_ok=True)
        cfg[key] = str(path)
        return path

    downloads_path = _ensure_dir("downloads_dir", DL_DIR)
    blurred_path = _ensure_dir("blurred_dir", BLUR_DIR)
    merged_path = _ensure_dir("merged_dir", MERG_DIR)

    # источники для пост-обработки — если пусто или каталог не существует, подтягиваем из основных
    blur_path = _project_path(cfg.get("blur_src_dir") or downloads_path)
    if not blur_path.exists():
        blur_path = downloads_path
    blur_path.mkdir(parents=True, exist_ok=True)
    cfg["blur_src_dir"] = str(blur_path)

    merge_path = _project_path(cfg.get("merge_src_dir") or blurred_path)
    if not merge_path.exists():
        merge_path = blurred_path
    merge_path.mkdir(parents=True, exist_ok=True)
    cfg["merge_src_dir"] = str(merge_path)

    probe_cfg = cfg.setdefault("watermark_probe", {})
    probe_src = _project_path(probe_cfg.get("source_dir") or downloads_path)
    if not probe_src.exists():
        probe_src = downloads_path
    probe_src.mkdir(parents=True, exist_ok=True)
    probe_out = _project_path(probe_cfg.get("output_dir") or PROJECT_ROOT / "restored")
    probe_out.mkdir(parents=True, exist_ok=True)
    probe_cfg["source_dir"] = str(probe_src)
    probe_cfg["output_dir"] = str(probe_out)

    genai_cfg = cfg.get("google_genai", {}) or {}
    output_raw = genai_cfg.get("output_dir") or IMAGES_DIR
    output_path = _project_path(output_raw)
    output_path.mkdir(parents=True, exist_ok=True)
    genai_cfg["output_dir"] = str(output_path)
    manifest_raw = genai_cfg.get("manifest_file") or (Path(output_path) / "manifest.json")
    manifest_path = _project_path(manifest_raw)
    genai_cfg["manifest_file"] = str(manifest_path)

    auto_cfg = cfg.get("autogen", {}) or {}
    img_prompts_raw = auto_cfg.get("image_prompts_file") or (WORKERS_DIR / "autogen" / "image_prompts.txt")
    img_prompts_path = _project_path(img_prompts_raw)
    img_prompts_path.parent.mkdir(parents=True, exist_ok=True)
    auto_cfg["image_prompts_file"] = str(img_prompts_path)

    yt = cfg.get("youtube", {}) or {}
    archive = yt.get("archive_dir")
    if archive:
        archive_path = _project_path(archive)
        archive_path.mkdir(parents=True, exist_ok=True)
        yt["archive_dir"] = str(archive_path)

    upload_src = yt.get("upload_src_dir")
    if upload_src:
        src_path = _project_path(upload_src)
        src_path.mkdir(parents=True, exist_ok=True)
        yt["upload_src_dir"] = str(src_path)

    tiktok = cfg.get("tiktok", {}) or {}
    secrets_dir = tiktok.get("secrets_dir")
    if secrets_dir:
        secrets_path = _project_path(secrets_dir)
        secrets_path.mkdir(parents=True, exist_ok=True)
        tiktok["secrets_dir"] = str(secrets_path)

    cfg["downloads_dir"] = str(downloads_path)
    cfg["blurred_dir"] = str(blurred_path)
    cfg["merged_dir"] = str(merged_path)

    tk = cfg.get("tiktok", {}) or {}
    tk_archive = tk.get("archive_dir")
    if tk_archive:
        tk_archive_path = _project_path(tk_archive)
        tk_archive_path.mkdir(parents=True, exist_ok=True)
        tk["archive_dir"] = str(tk_archive_path)

    tk_src = tk.get("upload_src_dir")
    if tk_src:
        tk_src_path = _project_path(tk_src)
        tk_src_path.mkdir(parents=True, exist_ok=True)
        tk["upload_src_dir"] = str(tk_src_path)


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        return s.connect_ex(("127.0.0.1", int(port))) == 0


def cdp_ready(port: int) -> bool:
    try:
        with urlopen(f"http://127.0.0.1:{int(port)}/json/version", timeout=1.0) as r:
            return r.status == 200
    except Exception:
        return False


# --- История: JSONL + ротация, с обратной совместимостью ---
def append_history(cfg: dict, record: dict):
    hist_path = _project_path(cfg.get("history_file", HIST_FILE))
    try:
        record["ts"] = int(time.time())
        line = json.dumps(record, ensure_ascii=False)
        rotate = hist_path.exists() and hist_path.stat().st_size > 10 * 1024 * 1024  # 10MB
        if rotate:
            backup = hist_path.with_suffix(hist_path.suffix + ".1")
            try:
                if backup.exists():
                    backup.unlink()
            except Exception:
                pass
            try:
                hist_path.rename(backup)
            except Exception:
                pass
        with open(hist_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def open_in_finder(path: Union[str, Path]):
    resolved = _project_path(path)
    if not resolved.exists():
        try:
            resolved.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
    target = str(resolved)
    if sys.platform == "darwin":
        subprocess.Popen(["open", target])
    elif sys.platform.startswith("win"):
        subprocess.Popen(["explorer", target])
    else:
        subprocess.Popen(["xdg-open", target])


def send_tg(cfg: dict, text: str, timeout: float = 5.0) -> bool:
    tg = cfg.get("telegram", {}) or {}
    if not tg.get("enabled"):
        return False
    token, chat = tg.get("bot_token"), tg.get("chat_id")
    if not token or not chat:
        return False
    try:
        import urllib.parse
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = urllib.parse.urlencode({"chat_id": chat, "text": text})
        req = Request(url, data=payload.encode("utf-8"), headers={
            "Content-Type": "application/x-www-form-urlencoded"
        })
        with urlopen(req, timeout=timeout) as resp:
            resp.read(1)
        return True
    except Exception as exc:
        print(f"[TG][ERR] {exc}", file=sys.stderr)
        return False


# --- Shadow profile helpers ---
def _copytree_filtered(src: Path, dst: Path):
    """
    Копируем профиль без тяжёлых кешей/мусора.
    Повторные запуски — дозаливаем изменения (по size+mtime),
    но не затираем более свежие файлы в тени, чтобы сохранялись авторизации.
    """
    exclude_dirs = {
        "Cache", "Code Cache", "GPUCache", "Service Worker",
        "CertificateTransparency", "Crashpad", "ShaderCache",
        "GrShaderCache", "OptimizationGuide", "SafetyTips",
        "Reporting and NEL", "File System",
    }
    exclude_files = {
        "LOCK", "LOCKFILE", "SingletonLock", "SingletonCookie",
        "SingletonSocket", "Network Persistent State"
    }

    src = Path(src); dst = Path(dst)
    dst.mkdir(parents=True, exist_ok=True)
    for root, dirs, files in os.walk(src):
        rel = Path(root).relative_to(src)
        dirs[:] = [d for d in dirs if d not in exclude_dirs]
        (dst / rel).mkdir(parents=True, exist_ok=True)
        for f in files:
            if f in exclude_files:
                continue
            s = Path(root) / f
            d = dst / rel / f
            try:
                if not d.exists():
                    shutil.copy2(s, d)
                else:
                    ss, ds = s.stat(), d.stat()
                    if ss.st_mtime > ds.st_mtime + 1:
                        shutil.copy2(s, d)
            except Exception:
                pass


def _prepare_shadow_profile(active_profile: dict, shadow_base: Path) -> Path:
    """
    Готовит корень теневого профиля (user-data-dir), в нём лежит папка профиля
    с таким же именем (например, 'Profile 1' или 'Default').
    """
    raw_root = active_profile.get("user_data_dir", "") or ""
    root = Path(os.path.expandvars(raw_root)).expanduser()
    active_profile["user_data_dir"] = str(root)
    prof_dir = active_profile.get("profile_directory", "Default")
    if not root or not (root / prof_dir).is_dir():
        raise RuntimeError("Неверно задан profile root/profile_directory")

    name = active_profile.get("name", prof_dir).replace("/", "_").replace("..", "_")
    shadow_root = shadow_base / name
    shadow_prof = shadow_root / prof_dir

    _copytree_filtered(root / prof_dir, shadow_prof)
    return shadow_root


def _ffconcat_escape(path: Path) -> str:
    # безопасное экранирование одинарных кавычек для ffconcat через stdin
    return str(path).replace("'", "'\\''")


# ---------- универсальный раннер FFmpeg с логами ----------
def _run_ffmpeg(cmd: List[str], log_prefix: str = "FFMPEG") -> Tuple[int, List[str]]:
    """
    Запускает FFmpeg, пишет stdout/stderr в логи через self.sig_log.
    self передаём через _run_ffmpeg._self из конструктора окна.
    """
    tail = deque(maxlen=50)
    try:
        p = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
        )
        assert p.stdout
        for ln in p.stdout:
            line = ln.rstrip()
            tail.append(line)
            self = getattr(_run_ffmpeg, "_self", None)
            if self:
                self.sig_log.emit(f"[{log_prefix}] {line}")
        rc = p.wait()
        return rc, list(tail)
    except FileNotFoundError:
        self = getattr(_run_ffmpeg, "_self", None)
        if self:
            self.sig_log.emit(f"[{log_prefix}] ffmpeg не найден. Проверь путь в Настройках → ffmpeg.")
        tail.append("ffmpeg не найден")
        return 127, list(tail)
    except Exception as e:
        self = getattr(_run_ffmpeg, "_self", None)
        if self:
            self.sig_log.emit(f"[{log_prefix}] ошибка запуска: {e}")
        tail.append(str(e))
        return 1, list(tail)


# ---------- процесс-раннер ----------
class ProcRunner(QtCore.QObject):
    line = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal(int, str)  # rc, tag
    notify = QtCore.pyqtSignal(str, str)    # title, message

    def __init__(self, tag: str, parent=None):
        super().__init__(parent)
        self.tag = tag
        self.proc: Optional[subprocess.Popen] = None
        self._stop = threading.Event()

    def run(self, cmd: List[str], cwd: Optional[str] = None, env: Optional[dict] = None):
        if self.proc and self.proc.poll() is None:
            self.line.emit("[!] Уже выполняется процесс. Сначала останови его.\n")
            return
        self._stop.clear()
        threading.Thread(target=self._worker, args=(cmd, cwd, env), daemon=True).start()

    def stop(self):
        self._stop.set()
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                time.sleep(1.0)
                if self.proc.poll() is None:
                    self.proc.kill()
            except Exception:
                pass
        self.line.emit("[i] Процесс остановлен пользователем.\n")

    def _worker(self, cmd, cwd, env):
        self.line.emit(f"[{self.tag}] > Запуск: {' '.join(cmd)}\n")
        self.notify.emit(self.tag, "Старт задачи")
        try:
            self.proc = subprocess.Popen(
                cmd, cwd=cwd or None, env=env or os.environ.copy(),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, universal_newlines=True
            )
            assert self.proc.stdout
            for ln in self.proc.stdout:
                if self._stop.is_set():
                    break
                self.line.emit(f"[{self.tag}] {ln.rstrip()}")
            rc = self.proc.wait()
            self.line.emit(f"[{self.tag}] ✓ Завершено с кодом {rc}.\n")
            self.notify.emit(self.tag, "Готово" if rc == 0 else "Завершено с ошибкой")
            self.finished.emit(rc, self.tag)
        except FileNotFoundError as e:
            self.line.emit(f"[{self.tag}] x Не найден файл/интерпретатор: {e}\n")
            self.notify.emit(self.tag, "Не найден файл/интерпретатор")
            self.finished.emit(127, self.tag)
        except Exception as e:
            self.line.emit(f"[{self.tag}] x Ошибка запуска: {e}\n")
            self.notify.emit(self.tag, "Ошибка запуска")
            self.finished.emit(1, self.tag)


# ---------- отдельное окно сессии ----------
class SessionWorkspaceWindow(QtWidgets.QDialog):
    """Компактное отдельное окно для управления конкретной сессией."""

    def __init__(self, main: "MainWindow", session_id: str):
        super().__init__(parent=main)
        self._main = main
        self.session_id = session_id
        self.setWindowTitle("Рабочее пространство Sora")
        self.setObjectName("sessionWorkspaceWindow")
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setModal(False)
        self.setWindowFlag(QtCore.Qt.WindowType.Window, True)
        self.setMinimumSize(560, 520)
        self.resize(720, 560)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        header = QtWidgets.QFrame()
        header_layout = QtWidgets.QVBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(4)
        self.lbl_title = QtWidgets.QLabel("Сессия")
        self.lbl_title.setObjectName("sessionWindowTitle")
        self.lbl_details = QtWidgets.QLabel("—")
        self.lbl_details.setObjectName("sessionWindowDetails")
        self.lbl_details.setWordWrap(True)
        header_layout.addWidget(self.lbl_title)
        header_layout.addWidget(self.lbl_details)
        layout.addWidget(header)

        actions_card = QtWidgets.QFrame()
        actions_card.setObjectName("sessionWindowActions")
        actions_layout = QtWidgets.QVBoxLayout(actions_card)
        actions_layout.setContentsMargins(12, 12, 12, 12)
        actions_layout.setSpacing(10)

        picker_row = QtWidgets.QHBoxLayout()
        picker_row.setSpacing(8)
        picker_row.setContentsMargins(0, 0, 0, 0)
        self.cmb_action = QtWidgets.QComboBox()
        self.cmb_action.setObjectName("sessionActionCombo")
        self.cmb_action.addItems([
            "Выбери действие",
            "Запуск Chrome",
            "Промпты Sora",
            "Генерация картинок",
            "Скачивание видео",
            "Замена водяного знака",
        ])
        self.cmb_action.setMinimumWidth(200)
        self.cmb_action.setEditable(False)
        self.btn_start_action = QtWidgets.QPushButton("Старт")
        self.btn_start_action.setObjectName("sessionActionStart")
        self.btn_start_action.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        self.btn_start_action.setMinimumHeight(34)
        picker_row.addWidget(self.cmb_action, 1)
        picker_row.addWidget(self.btn_start_action)
        actions_layout.addLayout(picker_row)

        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        grid.setContentsMargins(0, 0, 0, 0)
        self.btn_launch_chrome = QtWidgets.QPushButton("Открыть Chrome")
        self.btn_run_prompts = QtWidgets.QPushButton("Промпты Sora")
        self.btn_run_images = QtWidgets.QPushButton("Генерация")
        self.btn_run_download = QtWidgets.QPushButton("Скачивание")
        self.btn_run_watermark = QtWidgets.QPushButton("Замена ВЗ")
        self.btn_open_downloads = QtWidgets.QPushButton("Папка RAW")
        self.btn_stop = QtWidgets.QPushButton("Стоп")
        action_buttons = [
            self.btn_launch_chrome,
            self.btn_run_prompts,
            self.btn_run_images,
            self.btn_run_download,
            self.btn_run_watermark,
            self.btn_open_downloads,
            self.btn_stop,
        ]
        for idx, btn in enumerate(action_buttons):
            btn.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
            btn.setMinimumHeight(32)
            row, col = divmod(idx, 3)
            grid.addWidget(btn, row, col)
        actions_layout.addLayout(grid)
        layout.addWidget(actions_card)

        status_card = QtWidgets.QFrame()
        status_card.setObjectName("sessionWindowStatus")
        status_layout = QtWidgets.QVBoxLayout(status_card)
        status_layout.setContentsMargins(12, 12, 12, 12)
        status_layout.setSpacing(6)
        self.lbl_status = QtWidgets.QLabel("Статус: —")
        self.lbl_status.setObjectName("sessionWindowStatusLabel")
        self.lbl_status.setWordWrap(True)
        status_layout.addWidget(self.lbl_status)
        self.lbl_status_hint = QtWidgets.QLabel(
            "История запуска и лог выполнения обновляются автоматически при действиях в этой сессии."
        )
        self.lbl_status_hint.setObjectName("sessionWindowHint")
        self.lbl_status_hint.setWordWrap(True)
        status_layout.addWidget(self.lbl_status_hint)
        layout.addWidget(status_card)

        log_frame = QtWidgets.QFrame()
        log_frame.setObjectName("sessionWindowLog")
        log_layout = QtWidgets.QVBoxLayout(log_frame)
        log_layout.setContentsMargins(12, 12, 12, 12)
        log_layout.setSpacing(6)
        lbl_log = QtWidgets.QLabel("Журнал выполнения")
        lbl_log.setObjectName("sessionWindowLogTitle")
        log_layout.addWidget(lbl_log)
        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(500)
        self.log_view.setPlaceholderText("Здесь появятся логи процессов выбранной сессии…")
        font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont)
        font.setPointSize(max(font.pointSize() - 1, 9))
        self.log_view.setFont(font)
        self.log_view.setMinimumHeight(140)
        log_layout.addWidget(self.log_view, 1)
        layout.addWidget(log_frame, 1)

        self.btn_launch_chrome.clicked.connect(lambda: self._main._launch_session_chrome(self.session_id))
        self.btn_run_prompts.clicked.connect(lambda: self._main._run_session_autogen(self.session_id))
        self.btn_run_images.clicked.connect(lambda: self._main._run_session_images(self.session_id))
        self.btn_run_download.clicked.connect(lambda: self._main._run_session_download(self.session_id))
        self.btn_run_watermark.clicked.connect(lambda: self._main._run_session_watermark(self.session_id))
        self.btn_open_downloads.clicked.connect(lambda: self._main._open_session_download_dir(self.session_id))
        self.btn_stop.clicked.connect(lambda: self._main._stop_session_runner(self.session_id))
        self.btn_start_action.clicked.connect(self._run_selected_action)

    def _run_selected_action(self):
        mapping = {
            1: lambda: self._main._launch_session_chrome(self.session_id),
            2: lambda: self._main._run_session_autogen(self.session_id),
            3: lambda: self._main._run_session_images(self.session_id),
            4: lambda: self._main._run_session_download(self.session_id),
            5: lambda: self._main._run_session_watermark(self.session_id),
        }
        idx = self.cmb_action.currentIndex()
        if idx in mapping:
            mapping[idx]()

    def update_session(self, session: Dict[str, Any]):
        name = session.get("name", self.session_id)
        profile = session.get("prompt_profile") or PROMPTS_DEFAULT_KEY
        chrome = session.get("chrome_profile") or "—"
        port = _coerce_int(session.get("cdp_port")) or self._main._session_chrome_port(session)
        self.setWindowTitle(f"Sora — {name}")
        prompt_label = self._main._prompt_profile_label(profile)
        download_dir = str(self._main._session_download_dir(session))
        limit_label = self._main._session_download_limit_label(session)
        self.lbl_title.setText(name)
        self.lbl_details.setText(
            (
                f"Промпты: <b>{prompt_label}</b> · Chrome: <b>{chrome or 'по умолчанию'}</b> · CDP: <b>{port}</b>"
                f"<br>RAW: <b>{download_dir}</b> · Лимит: <b>{limit_label}</b>"
            )
        )

    def update_status(self, status: str, message: str, rc: Optional[int]):
        icon = self._main._session_status_icon(status)
        extra = f" (rc={rc})" if rc is not None else ""
        text = message or f"Статус: {status}"
        self.lbl_status.setText(f"{icon} {text}{extra}")

    def append_log(self, line: str):
        self.log_view.appendPlainText(line)

    def set_log(self, lines: Iterable[str]):
        self.log_view.blockSignals(True)
        self.log_view.clear()
        for line in lines:
            self.log_view.appendPlainText(line)
        self.log_view.moveCursor(QtGui.QTextCursor.MoveOperation.End)
        self.log_view.blockSignals(False)

    def closeEvent(self, event: QtGui.QCloseEvent):
        try:
            if self.session_id in self._main._session_windows:
                self._main._session_windows.pop(self.session_id, None)
        finally:
            super().closeEvent(event)


# ---------- командная палитра ----------
class CommandPaletteDialog(QtWidgets.QDialog):
    def __init__(self, parent: QtWidgets.QWidget, commands: List[Dict[str, Any]]):
        super().__init__(parent)
        self.setWindowTitle("Командная палитра")
        self.setObjectName("commandPalette")
        self.setModal(True)
        self.setWindowFlag(QtCore.Qt.WindowType.Tool, True)
        self.setWindowFlag(QtCore.Qt.WindowType.FramelessWindowHint, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.resize(520, 440)

        wrapper = QtWidgets.QFrame()
        wrapper.setObjectName("commandPaletteFrame")
        wrapper_layout = QtWidgets.QVBoxLayout(wrapper)
        wrapper_layout.setContentsMargins(16, 16, 16, 16)
        wrapper_layout.setSpacing(12)

        self.search = QtWidgets.QLineEdit()
        self.search.setObjectName("commandPaletteSearch")
        self.search.setPlaceholderText("Найди действие или раздел…")
        wrapper_layout.addWidget(self.search)

        self.list = QtWidgets.QListWidget()
        self.list.setObjectName("commandPaletteList")
        self.list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setAlternatingRowColors(False)
        self.list.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        wrapper_layout.addWidget(self.list, 1)

        hint = QtWidgets.QLabel("↵ — выполнить · Esc — закрыть · Tab — переключить разделы")
        hint.setObjectName("commandPaletteHint")
        hint.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        wrapper_layout.addWidget(hint)

        base_layout = QtWidgets.QVBoxLayout(self)
        base_layout.setContentsMargins(0, 0, 0, 0)
        base_layout.addWidget(wrapper)

        self._commands = list(commands)
        self._filtered: List[Dict[str, Any]] = []
        self._selected_id: Optional[str] = None

        self.search.textChanged.connect(self._apply_filter)
        self.list.itemActivated.connect(self._choose_current)
        self.list.itemSelectionChanged.connect(self._sync_focus_label)

        self._apply_filter()
        self.search.setFocus(QtCore.Qt.FocusReason.PopupFocusReason)

    def keyPressEvent(self, event: QtGui.QKeyEvent):
        if event.key() in {QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter}:
            self._choose_current(self.list.currentItem())
            event.accept()
            return
        if event.key() == QtCore.Qt.Key.Key_Tab:
            self._jump_to_next_category()
            event.accept()
            return
        if event.key() == QtCore.Qt.Key.Key_Escape:
            self.reject()
            event.accept()
            return
        super().keyPressEvent(event)

    def _jump_to_next_category(self):
        if not self._filtered:
            return
        current = self.list.currentRow()
        current_category = None
        if 0 <= current < self.list.count():
            item = self.list.item(current)
            current_category = item.data(QtCore.Qt.ItemDataRole.UserRole + 1)
        for idx in range(current + 1, self.list.count()):
            item = self.list.item(idx)
            if item and item.data(QtCore.Qt.ItemDataRole.UserRole + 1) != current_category:
                self.list.setCurrentRow(idx)
                return
        # если не нашли впереди — крутимся
        for idx in range(0, self.list.count()):
            item = self.list.item(idx)
            if item and item.data(QtCore.Qt.ItemDataRole.UserRole + 1) != current_category:
                self.list.setCurrentRow(idx)
                return

    def _apply_filter(self):
        query = self.search.text().strip().lower()
        tokens = [token for token in re.split(r"\s+", query) if token]
        self.list.blockSignals(True)
        self.list.clear()
        self._filtered.clear()

        for command in self._commands:
            haystack = " ".join(
                [
                    command.get("title", ""),
                    command.get("subtitle", ""),
                    command.get("category", ""),
                    " ".join(command.get("keywords", [])),
                ]
            ).lower()
            if all(token in haystack for token in tokens):
                item = QtWidgets.QListWidgetItem()
                title = command.get("title", "")
                subtitle = command.get("subtitle", "")
                if subtitle:
                    item.setText(f"{title}\n<small>{subtitle}</small>")
                else:
                    item.setText(title)
                item.setData(QtCore.Qt.ItemDataRole.UserRole, command.get("id"))
                item.setData(QtCore.Qt.ItemDataRole.UserRole + 1, command.get("category", ""))
                item.setToolTip(subtitle or title)
                self.list.addItem(item)
                self._filtered.append(command)

        self.list.blockSignals(False)
        if self.list.count():
            self.list.setCurrentRow(0)

    def _choose_current(self, item: Optional[QtWidgets.QListWidgetItem]):
        if not item:
            return
        command_id = item.data(QtCore.Qt.ItemDataRole.UserRole)
        if not command_id:
            return
        self._selected_id = str(command_id)
        self.accept()

    def _sync_focus_label(self):
        # Вставка HTML-меток требует обновления флагов отображения
        for idx in range(self.list.count()):
            item = self.list.item(idx)
            if not item:
                continue
            text = item.text()
            if "<small>" in text:
                item.setData(QtCore.Qt.ItemDataRole.DisplayRole, QtCore.QVariant())
                item.setData(QtCore.Qt.ItemDataRole.DisplayRole, text)

    def selected_command(self) -> Optional[str]:
        return self._selected_id


class CustomCommandDialog(QtWidgets.QDialog):
    def __init__(self, parent: QtWidgets.QWidget, data: Optional[Dict[str, str]] = None):
        super().__init__(parent)
        self.setWindowTitle("Быстрая команда")
        self.setObjectName("customCommandDialog")
        self.setModal(True)
        self.resize(420, 240)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        form = QtWidgets.QFormLayout()
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(10)

        self.ed_name = QtWidgets.QLineEdit()
        self.ed_name.setPlaceholderText("Например: Открыть RAW")
        self.ed_command = QtWidgets.QLineEdit()
        self.ed_command.setPlaceholderText("Команда или путь к скрипту")
        self.ed_description = QtWidgets.QLineEdit()
        self.ed_description.setPlaceholderText("Подсказка (необязательно)")

        if isinstance(data, dict):
            self.ed_name.setText(str(data.get("name", "")))
            self.ed_command.setText(str(data.get("command", "")))
            self.ed_description.setText(str(data.get("description", "")))

        form.addRow("Название:", self.ed_name)
        form.addRow("Команда:", self.ed_command)
        form.addRow("Описание:", self.ed_description)
        layout.addLayout(form)

        hint = QtWidgets.QLabel(
            "Команда запускается через оболочку. Можно указать python-скрипт, `start .` или любой batch/шаблон."
        )
        hint.setObjectName("customCommandHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Cancel | QtWidgets.QDialogButtonBox.StandardButton.Ok
        )
        layout.addWidget(buttons)

        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

    def accept(self) -> None:  # type: ignore[override]
        if not self.ed_name.text().strip() or not self.ed_command.text().strip():
            QtWidgets.QMessageBox.warning(
                self,
                "Заполните поля",
                "Нужно указать и название, и команду. Без этих полей сохранение невозможно.",
            )
            return
        super().accept()

    def get_data(self) -> Dict[str, str]:
        return {
            "name": self.ed_name.text().strip(),
            "command": self.ed_command.text().strip(),
            "description": self.ed_description.text().strip(),
        }


class AutomatorStepDialog(QtWidgets.QDialog):
    """Диалог добавления/редактирования шага автоматизации."""

    def __init__(self, main: "MainWindow", sessions: List[Tuple[str, str]], step: Optional[Dict[str, Any]] = None):
        super().__init__(main)
        self._sessions = sessions
        self.setWindowTitle("Шаг автоматизации")
        self.setModal(True)
        self.resize(420, 520)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        self.cmb_type = QtWidgets.QComboBox()
        self.cmb_type.addItem("✍️ Вставка промптов (сессии)", "session_prompts")
        self.cmb_type.addItem("🖼️ Генерация картинок (сессии)", "session_images")
        self.cmb_type.addItem("🪄 Промпты + картинки (сессии)", "session_mix")
        self.cmb_type.addItem("⬇️ Скачивание видео (сессии)", "session_download")
        self.cmb_type.addItem("🧼 Замена водяного знака (сессии)", "session_watermark")
        self.cmb_type.addItem("🚀 Открыть Chrome (сессии)", "session_chrome")
        self.cmb_type.addItem("🌫️ Блюр (глобально)", "global_blur")
        self.cmb_type.addItem("🧵 Склейка (глобально)", "global_merge")
        self.cmb_type.addItem("🧼 Замена водяного знака (глобально)", "global_watermark")
        self.cmb_type.addItem("🧐 Проверка ВЗ (глобально)", "global_probe")
        layout.addWidget(self.cmb_type)

        self.session_group = QtWidgets.QGroupBox("Выбор сессий")
        session_layout = QtWidgets.QVBoxLayout(self.session_group)
        session_layout.setContentsMargins(12, 12, 12, 12)
        session_layout.setSpacing(6)
        self.lst_sessions = QtWidgets.QListWidget()
        self.lst_sessions.setAlternatingRowColors(True)
        self.lst_sessions.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
        for sid, label in sessions:
            item = QtWidgets.QListWidgetItem(label)
            item.setData(QtCore.Qt.ItemDataRole.UserRole, sid)
            item.setFlags(item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(QtCore.Qt.CheckState.Unchecked)
            self.lst_sessions.addItem(item)
        session_layout.addWidget(self.lst_sessions)
        layout.addWidget(self.session_group)

        self.limit_widget = QtWidgets.QWidget()
        limit_layout = QtWidgets.QHBoxLayout(self.limit_widget)
        limit_layout.setContentsMargins(0, 0, 0, 0)
        limit_layout.setSpacing(8)
        limit_layout.addWidget(QtWidgets.QLabel("Скачать по:"))
        self.sb_limit = QtWidgets.QSpinBox()
        self.sb_limit.setRange(0, 10000)
        self.sb_limit.setValue(0)
        self.sb_limit.setSuffix(" видео")
        limit_layout.addWidget(self.sb_limit)
        limit_layout.addStretch(1)
        layout.addWidget(self.limit_widget)

        self.merge_widget = QtWidgets.QWidget()
        merge_layout = QtWidgets.QHBoxLayout(self.merge_widget)
        merge_layout.setContentsMargins(0, 0, 0, 0)
        merge_layout.setSpacing(8)
        merge_layout.addWidget(QtWidgets.QLabel("Склеивать по:"))
        self.sb_group = QtWidgets.QSpinBox()
        self.sb_group.setRange(1, 50)
        self.sb_group.setValue(3)
        self.sb_group.setSuffix(" клипа")
        merge_layout.addWidget(self.sb_group)
        merge_layout.addStretch(1)
        layout.addWidget(self.merge_widget)

        self.probe_widget = QtWidgets.QWidget()
        probe_layout = QtWidgets.QHBoxLayout(self.probe_widget)
        probe_layout.setContentsMargins(0, 0, 0, 0)
        probe_layout.setSpacing(8)
        self.cb_probe_flip_step = QtWidgets.QCheckBox("Флипать при срабатывании")
        self.cb_probe_flip_step.setChecked(True)
        self.cb_probe_flip_step.setToolTip("Запуск из автоматизатора будет использовать текущие настройки зоны/порогов")
        probe_layout.addWidget(self.cb_probe_flip_step)
        probe_layout.addStretch(1)
        layout.addWidget(self.probe_widget)

        layout.addStretch(1)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.cmb_type.currentIndexChanged.connect(self._update_visibility)
        self._update_visibility()

        if step:
            self._load_step(step)

    def _load_step(self, step: Dict[str, Any]):
        step_type = step.get("type")
        idx = self.cmb_type.findData(step_type)
        if idx >= 0:
            self.cmb_type.setCurrentIndex(idx)
        sessions = set(step.get("sessions") or [])
        for i in range(self.lst_sessions.count()):
            item = self.lst_sessions.item(i)
            sid = item.data(QtCore.Qt.ItemDataRole.UserRole)
            if sid in sessions:
                item.setCheckState(QtCore.Qt.CheckState.Checked)
        if step_type == "session_download":
            self.sb_limit.setValue(int(step.get("limit", 0) or 0))
        if step_type == "global_merge":
            group = int(step.get("group", 0) or 0)
            if group > 0:
                self.sb_group.setValue(group)
        if step_type == "global_probe":
            self.cb_probe_flip_step.setChecked(bool(step.get("flip", True)))

    def _update_visibility(self):
        step_type = self.cmb_type.currentData()
        is_session = isinstance(step_type, str) and step_type.startswith("session_")
        self.session_group.setVisible(is_session)
        self.limit_widget.setVisible(step_type == "session_download")
        self.merge_widget.setVisible(step_type == "global_merge")
        self.probe_widget.setVisible(step_type == "global_probe")

    def _selected_sessions(self) -> List[str]:
        selected: List[str] = []
        for i in range(self.lst_sessions.count()):
            item = self.lst_sessions.item(i)
            if item.checkState() == QtCore.Qt.CheckState.Checked:
                sid = item.data(QtCore.Qt.ItemDataRole.UserRole)
                if sid:
                    selected.append(str(sid))
        return selected

    def accept(self) -> None:  # type: ignore[override]
        step_type = self.cmb_type.currentData()
        if isinstance(step_type, str) and step_type.startswith("session_"):
            if not self._selected_sessions():
                QtWidgets.QMessageBox.warning(
                    self,
                    "Выбери сессии",
                    "Нужно отметить хотя бы одну сессию для выполнения шага.",
                )
                return
        super().accept()

    def get_data(self) -> Dict[str, Any]:
        step_type = self.cmb_type.currentData()
        step: Dict[str, Any] = {"type": step_type}
        if isinstance(step_type, str) and step_type.startswith("session_"):
            step["sessions"] = self._selected_sessions()
            if step_type == "session_download":
                step["limit"] = int(self.sb_limit.value())
        elif step_type == "global_merge":
            step["group"] = int(self.sb_group.value())
        elif step_type == "global_probe":
            step["flip"] = bool(self.cb_probe_flip_step.isChecked())
        return step


# ---------- главное окно ----------
class MainWindow(QtWidgets.QMainWindow):
    # сигналы для безопасных UI-апдейтов из потоков
    sig_set_status = QtCore.pyqtSignal(str, int, int, str)  # text, progress, total, state
    sig_log = QtCore.pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.cfg = load_cfg()
        ensure_dirs(self.cfg)

        auto_cfg = self.cfg.setdefault("autogen", {})
        key = auto_cfg.get("active_prompts_profile", PROMPTS_DEFAULT_KEY) or PROMPTS_DEFAULT_KEY
        self._current_prompt_profile_key = key
        self._ensure_all_profile_prompts()

        sessions_list = normalize_session_list(auto_cfg.get("sessions"))
        if not sessions_list:
            sessions_list.append(
                {
                    "id": uuid.uuid4().hex[:8],
                    "name": "Сессия 1",
                    "prompt_profile": PROMPTS_DEFAULT_KEY,
                    "chrome_profile": "",
                    "cdp_port": None,
                    "prompts_file": "",
                    "image_prompts_file": "",
                    "submitted_log": "",
                    "failed_log": "",
                    "notes": "",
                    "auto_launch_chrome": False,
                    "auto_launch_autogen": "idle",
                    "download_dir": "",
                    "titles_file": "",
                    "cursor_file": "",
                    "max_videos": 0,
                }
            )
        auto_cfg["sessions"] = sessions_list
        self._session_cache: Dict[str, Dict[str, Any]] = {session["id"]: session for session in sessions_list}
        self._session_order: List[str] = [session["id"] for session in sessions_list]
        self._session_runners: Dict[str, ProcRunner] = {}
        self._session_state: Dict[str, Dict[str, Any]] = {}
        self._session_windows: Dict[str, "SessionWorkspaceWindow"] = {}
        self._current_session_id: str = self._session_order[0] if self._session_order else ""

        automator_cfg = self.cfg.setdefault("automator", {})
        self._automator_steps: List[Dict[str, Any]] = normalize_automator_steps(automator_cfg.get("steps"))
        automator_cfg["steps"] = [dict(step) for step in self._automator_steps]
        self._automator_presets: List[Dict[str, Any]] = normalize_automator_presets(
            automator_cfg.get("presets")
        )
        automator_cfg["presets"] = [dict(preset) for preset in self._automator_presets]
        self._automator_queue: List[Dict[str, Any]] = []
        self._automator_total: int = 0
        self._automator_index: int = 0
        self._automator_ok_all: bool = True
        self._automator_running: bool = False
        self._automator_waiting: bool = False

        self._command_registry: Dict[str, Dict[str, Any]] = {}
        self._command_actions: Dict[str, QtGui.QAction] = {}
        self._pending_tg_jobs: List[Tuple[QtCore.QTimer, str]] = []
        self._telegram_templates: List[Dict[str, str]] = []
        self._icon_cache: Dict[Tuple[int, str, int], QtGui.QIcon] = {}
        self._custom_commands: List[Dict[str, str]] = normalize_custom_commands(
            self.cfg.get("ui", {}).get("custom_commands", [])
        )

        self._apply_theme()

        self.setWindowTitle("Sora Suite — Control Panel")
        self.resize(1500, 950)
        self.setMinimumSize(1024, 720)

        self._app_icon = self._load_app_icon()
        self.setWindowIcon(self._app_icon)

        # tray notifications
        self.tray = QtWidgets.QSystemTrayIcon(self)
        self.tray.setIcon(self._app_icon)
        self.tray.setToolTip("Sora Suite")
        self.tray.show()

        # трекинг активных подпроцессов (ffmpeg и т.п.)
        self._active_procs: set[subprocess.Popen] = set()
        self._procs_lock = Lock()

        self._current_step_started: Optional[float] = None
        self._current_step_state: str = "idle"
        self._current_step_timer = QtCore.QTimer(self)
        self._current_step_timer.setInterval(1000)
        self._current_step_timer.timeout.connect(self._tick_step_timer)

        self._scenario_waiters: Dict[str, threading.Event] = {}
        self._scenario_results: Dict[str, int] = {}
        self._scenario_wait_lock = Lock()
        self._session_waiters: Dict[str, List[Tuple[str, str]]] = {}
        self._session_wait_events: Dict[str, threading.Event] = {}
        self._session_wait_results: Dict[str, int] = {}
        self._stat_cache: Dict[Tuple[str, Tuple[str, ...]], Tuple[float, int, int]] = {}
        self._activity_filter_text: str = ""
        self._readme_loaded = False

        # кеши пресетов блюра должны существовать до построения UI,
        # иначе _load_zones_into_ui() перезапишет их, а позже мы бы обнулили значения
        self._preset_cache: Dict[str, List[Dict[str, int]]] = {}
        self._preset_tables: Dict[str, QtWidgets.QTableWidget] = {}

        self._section_index: Dict[str, int] = {}
        self._section_order: List[str] = []
        self._current_section_key: str = ""
        self._context_saved_size: int = 320
        self._nav_saved_size: int = 260

        self._build_ui()
        self._wire()
        self._refresh_automator_presets()
        self._refresh_automator_list()
        self._init_state()
        self._refresh_update_buttons()
        self._refresh_pipeline_context()

        QtCore.QTimer.singleShot(0, self._init_splitter_sizes)
        QtCore.QTimer.singleShot(0, self._perform_delayed_startup)

        # дать раннеру ffmpeg доступ к self для логов
        _run_ffmpeg._self = self  # type: ignore[attr-defined]

        self._settings_dirty = False
        self._settings_autosave_timer = QtCore.QTimer(self)
        self._settings_autosave_timer.setInterval(2000)
        self._settings_autosave_timer.setSingleShot(True)
        self._settings_autosave_timer.timeout.connect(self._autosave_settings)
        self._register_settings_autosave_sources()

        for session_id in list(self._session_order):
            self._ensure_session_state(session_id)

    # ----- helpers -----
    def _mono_icon(
        self,
        sp: QtWidgets.QStyle.StandardPixmap,
        color: str = "#f8fafc",
        *,
        size: int = 26,
    ) -> QtGui.QIcon:
        key = (int(sp), color, size)
        cached = self._icon_cache.get(key)
        if cached:
            return cached

        base_icon = self.style().standardIcon(sp)
        pixmap = base_icon.pixmap(size, size)
        if pixmap.isNull():
            self._icon_cache[key] = base_icon
            return base_icon

        image = pixmap.toImage().convertToFormat(QtGui.QImage.Format.Format_ARGB32)
        tint = QtGui.QColor(color)
        for y in range(image.height()):
            for x in range(image.width()):
                pixel = QtGui.QColor(image.pixel(x, y))
                alpha = pixel.alpha()
                if alpha == 0:
                    continue
                toned = QtGui.QColor(tint)
                toned.setAlpha(alpha)
                image.setPixelColor(x, y, toned)

        icon = QtGui.QIcon(QtGui.QPixmap.fromImage(image))
        self._icon_cache[key] = icon
        return icon

    def _load_app_icon(self) -> QtGui.QIcon:
        icon_path = APP_DIR / "app_icon.png"
        if icon_path.exists():
            return QtGui.QIcon(str(icon_path))

        size = 256
        pixmap = QtGui.QPixmap(size, size)
        pixmap.fill(QtCore.Qt.GlobalColor.transparent)

        painter = QtGui.QPainter(pixmap)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

        gradient = QtGui.QLinearGradient(0, 0, size, size)
        gradient.setColorAt(0.0, QtGui.QColor("#38bdf8"))
        gradient.setColorAt(1.0, QtGui.QColor("#6366f1"))
        painter.setBrush(QtGui.QBrush(gradient))
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.drawRoundedRect(0, 0, size, size, 56, 56)

        inner_rect = QtCore.QRectF(size * 0.2, size * 0.2, size * 0.6, size * 0.6)
        painter.setBrush(QtGui.QBrush(QtGui.QColor("#0f172a")))
        painter.drawRoundedRect(inner_rect, 42, 42)

        painter.setPen(QtGui.QPen(QtGui.QColor("#f8fafc")))
        font = QtGui.QFont("Inter", int(size * 0.46))
        if not font.family():
            font = QtGui.QFont("Segoe UI", int(size * 0.46))
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(QtCore.QRectF(0, 0, size, size), int(QtCore.Qt.AlignmentFlag.AlignCenter), "S")
        painter.end()

        return QtGui.QIcon(pixmap)

    def _ensure_path_exists(self, raw: Union[str, Path]) -> Path:
        """Create file/dir for path within project if missing and return Path."""

        if raw is None:
            return Path()

        try:
            path = raw if isinstance(raw, Path) else Path(str(raw).strip())
        except Exception:
            return Path()

        if not str(path):
            return Path()

        target = _project_path(path)

        try:
            if target.exists():
                return target

            if target.suffix:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.touch()
            else:
                target.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        return target

    def _perform_delayed_startup(self):
        self._refresh_stats()
        self._reload_history()
        self._auto_scan_profiles_at_start()
        self._refresh_prompt_profiles_ui()
        self._refresh_content_context()
        self._refresh_watermark_context()
        self._load_image_prompts()
        self._refresh_youtube_ui()
        self._refresh_tiktok_ui()
        self._refresh_autopost_context()
        self._load_autogen_cfg_ui()
        self._reload_used_prompts()
        self._refresh_telegram_context()
        self._refresh_overview_context()
        maint_cfg = self.cfg.get("maintenance", {}) or {}
        if maint_cfg.get("auto_cleanup_on_start"):
            QtCore.QTimer.singleShot(200, lambda: self._run_maintenance_cleanup(manual=False))
        QtCore.QTimer.singleShot(400, self._apply_session_autolaunches)

    def _ensure_all_profile_prompts(self):
        try:
            self._ensure_path_exists(str(self._default_profile_prompts(None)))
        except Exception:
            pass

        for profile in self.cfg.get("chrome", {}).get("profiles", []) or []:
            name = profile.get("name") or profile.get("profile_directory")
            if not name:
                continue
            try:
                self._ensure_path_exists(str(self._default_profile_prompts(name)))
            except Exception:
                continue

    def _ensure_profile_prompt_files(self, profile_name: Optional[str]):
        try:
            self._ensure_path_exists(str(self._default_profile_prompts(profile_name)))
        except Exception:
            pass

    # ----- sessions helpers -----
    def _ensure_session_state(self, session_id: str) -> Dict[str, Any]:
        state = self._session_state.setdefault(
            session_id,
            {
                "status": "idle",
                "running": False,
                "last_rc": None,
                "last_message": "",
                "log": deque(maxlen=500),
                "active_task": "",
                "last_task": "",
                "download_before": None,
                "download_dest": "",
                "clean_before": None,
                "clean_dest": "",
            },
        )
        return state

    def _append_session_log(self, session_id: str, line: str):
        state = self._ensure_session_state(session_id)
        state["log"].append(line)
        if session_id in self._session_windows:
            self._session_windows[session_id].append_log(line)
        if getattr(self, "_current_session_id", "") == session_id and hasattr(self, "te_session_log"):
            self.te_session_log.appendPlainText(line)

    def _set_session_status(self, session_id: str, status: str, message: str = "", rc: Optional[int] = None):
        state = self._ensure_session_state(session_id)
        state["status"] = status
        state["running"] = status == "running"
        state["last_message"] = message
        if rc is not None:
            state["last_rc"] = rc
        self._refresh_sessions_list()
        if getattr(self, "_current_session_id", None) == session_id:
            self._update_session_status_panel(session_id)
        self._refresh_sessions_context()
        window = self._session_windows.get(session_id)
        if window:
            window.update_status(status, message, rc)

    def _session_status_icon(self, status: str) -> str:
        mapping = {
            "running": "⏳",
            "ok": "✅",
            "error": "⚠️",
            "idle": "🟦",
        }
        return mapping.get(status, "🟦")

    def _session_display_label(self, session_id: str) -> str:
        session = self._session_cache.get(session_id)
        if not session:
            return session_id
        state = self._ensure_session_state(session_id)
        icon = self._session_status_icon(state.get("status", "idle"))
        return f"{icon} {session.get('name', session_id)}"

    def _refresh_sessions_list(self):
        if not hasattr(self, "lst_sessions"):
            return
        current_id = getattr(self, "_current_session_id", "")
        self.lst_sessions.blockSignals(True)
        self.lst_sessions.clear()
        target_row = 0
        for idx, session_id in enumerate(self._session_order):
            session = self._session_cache.get(session_id)
            if not session:
                continue
            item = QtWidgets.QListWidgetItem(self._session_display_label(session_id))
            item.setData(QtCore.Qt.ItemDataRole.UserRole, session_id)
            state = self._ensure_session_state(session_id)
            tooltip = session.get("notes", "").strip()
            if state.get("last_message"):
                tooltip = f"{state['last_message']}\n{tooltip}" if tooltip else state["last_message"]
            if tooltip:
                item.setToolTip(tooltip)
            self.lst_sessions.addItem(item)
            if session_id == current_id:
                target_row = idx
        if self.lst_sessions.count():
            self.lst_sessions.setCurrentRow(target_row)
        self.lst_sessions.blockSignals(False)
        self._refresh_command_palette_sessions()
        self._refresh_sessions_context()
        self._refresh_automator_presets()
        self._refresh_automator_list()
        self._refresh_session_log_panel()

    def _session_config_snapshot(self) -> List[Dict[str, Any]]:
        snapshot: List[Dict[str, Any]] = []
        for session_id in self._session_order:
            session = self._session_cache.get(session_id)
            if not session:
                continue
            entry = {
                "id": session_id,
                "name": session.get("name", ""),
                "chrome_profile": session.get("chrome_profile", ""),
                "prompt_profile": session.get("prompt_profile", PROMPTS_DEFAULT_KEY),
                "cdp_port": session.get("cdp_port"),
                "prompts_file": session.get("prompts_file", ""),
                "image_prompts_file": session.get("image_prompts_file", ""),
                "submitted_log": session.get("submitted_log", ""),
                "failed_log": session.get("failed_log", ""),
                "notes": session.get("notes", ""),
                "auto_launch_chrome": bool(session.get("auto_launch_chrome", False)),
                "auto_launch_autogen": session.get("auto_launch_autogen", "idle"),
                "download_dir": session.get("download_dir", ""),
                "clean_dir": session.get("clean_dir", ""),
                "titles_file": session.get("titles_file", ""),
                "cursor_file": session.get("cursor_file", ""),
                "max_videos": int(_coerce_int(session.get("max_videos")) or 0),
                "open_drafts": bool(session.get("open_drafts", True)),
            }
            snapshot.append(entry)
        return snapshot

    def _persist_sessions(self):
        auto_cfg = self.cfg.setdefault("autogen", {})
        auto_cfg["sessions"] = self._session_config_snapshot()
        self._mark_settings_dirty()

    def _refresh_sessions_choices(self):
        if not hasattr(self, "cmb_session_prompt_profile"):
            return
        profiles = [(PROMPTS_DEFAULT_KEY, self._prompt_profile_label(PROMPTS_DEFAULT_KEY))]
        for profile in self.cfg.get("chrome", {}).get("profiles", []) or []:
            name = profile.get("name") or profile.get("profile_directory") or ""
            if name:
                profiles.append((name, name))
        self.cmb_session_prompt_profile.blockSignals(True)
        self.cmb_session_prompt_profile.clear()
        for key, label in profiles:
            self.cmb_session_prompt_profile.addItem(label, key)
        self.cmb_session_prompt_profile.blockSignals(False)

        chrome_options = [("", "Активный из настроек")]
        for profile in self.cfg.get("chrome", {}).get("profiles", []) or []:
            label = profile.get("name") or profile.get("profile_directory") or ""
            if label:
                chrome_options.append((label, label))
        self.cmb_session_chrome_profile.blockSignals(True)
        self.cmb_session_chrome_profile.clear()
        for key, label in chrome_options:
            self.cmb_session_chrome_profile.addItem(label, key)
        self.cmb_session_chrome_profile.blockSignals(False)

    def _set_session_editor_enabled(self, enabled: bool):
        for widget in getattr(self, "_session_detail_widgets", []):
            if isinstance(widget, QtWidgets.QWidget):
                widget.setEnabled(enabled)

    def _clear_session_editor(self):
        if not hasattr(self, "ed_session_name"):
            return
        self._set_session_editor_enabled(False)
        for line in (
            self.ed_session_name,
            self.ed_session_prompts,
            self.ed_session_image_prompts,
            self.ed_session_submitted,
            self.ed_session_failed,
            self.ed_session_download_dir,
            self.ed_session_titles_file,
            self.ed_session_cursor_file,
        ):
            line.blockSignals(True)
            line.setText("")
            line.blockSignals(False)
        self.te_session_notes.blockSignals(True)
        self.te_session_notes.setPlainText("")
        self.te_session_notes.blockSignals(False)
        self.sb_session_port.blockSignals(True)
        self.sb_session_port.setValue(0)
        self.sb_session_port.blockSignals(False)
        self.sb_session_max_videos.blockSignals(True)
        self.sb_session_max_videos.setValue(0)
        self.sb_session_max_videos.blockSignals(False)
        self.chk_session_open_drafts.blockSignals(True)
        self.chk_session_open_drafts.setChecked(True)
        self.chk_session_open_drafts.blockSignals(False)
        self.chk_session_auto_chrome.blockSignals(True)
        self.chk_session_auto_chrome.setChecked(False)
        self.chk_session_auto_chrome.blockSignals(False)
        self.cmb_session_autogen_mode.blockSignals(True)
        self.cmb_session_autogen_mode.setCurrentIndex(0)
        self.cmb_session_autogen_mode.blockSignals(False)
        self.te_session_log.clear()
        self.lbl_session_status.setText("Выбери сессию слева, чтобы увидеть детали")

    def _load_session_into_editor(self, session_id: str):
        session = self._session_cache.get(session_id)
        if not session:
            self._clear_session_editor()
            return
        self._set_session_editor_enabled(True)
        name = session.get("name", "")
        self.ed_session_name.blockSignals(True)
        self.ed_session_name.setText(name)
        self.ed_session_name.blockSignals(False)

        prompt_key = session.get("prompt_profile") or PROMPTS_DEFAULT_KEY
        idx = self.cmb_session_prompt_profile.findData(prompt_key)
        self.cmb_session_prompt_profile.blockSignals(True)
        if idx >= 0:
            self.cmb_session_prompt_profile.setCurrentIndex(idx)
        else:
            self.cmb_session_prompt_profile.setCurrentIndex(0)
        self.cmb_session_prompt_profile.blockSignals(False)

        chrome_key = session.get("chrome_profile") or ""
        idx = self.cmb_session_chrome_profile.findData(chrome_key)
        self.cmb_session_chrome_profile.blockSignals(True)
        if idx >= 0:
            self.cmb_session_chrome_profile.setCurrentIndex(idx)
        else:
            self.cmb_session_chrome_profile.setCurrentIndex(0)
        self.cmb_session_chrome_profile.blockSignals(False)

        port_val = _coerce_int(session.get("cdp_port")) or 0
        self.sb_session_port.blockSignals(True)
        self.sb_session_port.setValue(port_val if port_val > 0 else 0)
        self.sb_session_port.blockSignals(False)

        for field, line in [
            ("prompts_file", self.ed_session_prompts),
            ("image_prompts_file", self.ed_session_image_prompts),
            ("submitted_log", self.ed_session_submitted),
            ("failed_log", self.ed_session_failed),
            ("download_dir", self.ed_session_download_dir),
            ("clean_dir", self.ed_session_clean_dir),
            ("titles_file", self.ed_session_titles_file),
            ("cursor_file", self.ed_session_cursor_file),
        ]:
            value = str(session.get(field, "") or "")
            line.blockSignals(True)
            line.setText(value)
            line.blockSignals(False)

        self.te_session_notes.blockSignals(True)
        self.te_session_notes.setPlainText(session.get("notes", ""))
        self.te_session_notes.blockSignals(False)

        limit = _coerce_int(session.get("max_videos")) or 0
        self.sb_session_max_videos.blockSignals(True)
        self.sb_session_max_videos.setValue(limit if limit > 0 else 0)
        self.sb_session_max_videos.blockSignals(False)

        self.chk_session_open_drafts.blockSignals(True)
        self.chk_session_open_drafts.setChecked(bool(session.get("open_drafts", True)))
        self.chk_session_open_drafts.blockSignals(False)

        self.chk_session_auto_chrome.blockSignals(True)
        self.chk_session_auto_chrome.setChecked(bool(session.get("auto_launch_chrome")))
        self.chk_session_auto_chrome.blockSignals(False)

        mode = session.get("auto_launch_autogen", "idle")
        idx = self.cmb_session_autogen_mode.findData(mode)
        self.cmb_session_autogen_mode.blockSignals(True)
        if idx >= 0:
            self.cmb_session_autogen_mode.setCurrentIndex(idx)
        else:
            self.cmb_session_autogen_mode.setCurrentIndex(0)
        self.cmb_session_autogen_mode.blockSignals(False)

        state = self._ensure_session_state(session_id)
        self.te_session_log.blockSignals(True)
        self.te_session_log.clear()
        for line in state.get("log", []):
            self.te_session_log.appendPlainText(line)
        self.te_session_log.blockSignals(False)
        self._update_session_status_panel(session_id)

    def _update_session_status_panel(self, session_id: str):
        state = self._ensure_session_state(session_id)
        status = state.get("status", "idle")
        message = state.get("last_message", "")
        icon = self._session_status_icon(status)
        if message:
            self.lbl_session_status.setText(f"{icon} {message}")
        else:
            self.lbl_session_status.setText(f"{icon} Статус: {status}")

    def _on_session_selection_changed(self):
        item = self.lst_sessions.currentItem() if hasattr(self, "lst_sessions") else None
        if not item:
            self._current_session_id = ""
            self._clear_session_editor()
            return
        session_id = item.data(QtCore.Qt.ItemDataRole.UserRole)
        if not session_id:
            self._current_session_id = ""
            self._clear_session_editor()
            return
        self._current_session_id = session_id
        self._load_session_into_editor(session_id)
        self._refresh_sessions_context()

    def _create_session(self, name: Optional[str] = None) -> Dict[str, Any]:
        session_id = uuid.uuid4().hex[:8]
        session = {
            "id": session_id,
            "name": name or f"Сессия {len(self._session_order) + 1}",
            "prompt_profile": PROMPTS_DEFAULT_KEY,
            "chrome_profile": "",
            "cdp_port": None,
            "prompts_file": "",
            "image_prompts_file": "",
            "submitted_log": "",
            "failed_log": "",
            "notes": "",
            "auto_launch_chrome": False,
            "auto_launch_autogen": "idle",
            "download_dir": "",
            "titles_file": "",
            "cursor_file": "",
            "max_videos": 0,
        }
        self._session_cache[session_id] = session
        self._session_order.append(session_id)
        self._ensure_session_state(session_id)
        return session

    def _on_session_add(self):
        session = self._create_session()
        self._persist_sessions()
        self._refresh_sessions_list()
        self._select_session(session["id"])

    def _select_session(self, session_id: str):
        if not hasattr(self, "lst_sessions"):
            return
        for row in range(self.lst_sessions.count()):
            item = self.lst_sessions.item(row)
            if item.data(QtCore.Qt.ItemDataRole.UserRole) == session_id:
                self.lst_sessions.setCurrentRow(row)
                break

    def _on_session_duplicate(self):
        current = getattr(self, "_current_session_id", "")
        if not current or current not in self._session_cache:
            return
        base = dict(self._session_cache[current])
        session = self._create_session(name=f"{base.get('name', 'Сессия')} (копия)")
        for key in (
            "prompt_profile",
            "chrome_profile",
            "cdp_port",
            "prompts_file",
            "image_prompts_file",
            "submitted_log",
            "failed_log",
            "notes",
            "auto_launch_chrome",
            "auto_launch_autogen",
            "download_dir",
            "titles_file",
            "cursor_file",
            "max_videos",
        ):
            session[key] = base.get(key)
        self._persist_sessions()
        self._refresh_sessions_list()
        self._select_session(session["id"])

    def _on_session_remove(self):
        current = getattr(self, "_current_session_id", "")
        if not current:
            return
        if len(self._session_order) <= 1:
            QtWidgets.QMessageBox.information(self, "Рабочие пространства", "Нельзя удалить последнюю сессию.")
            return
        if QtWidgets.QMessageBox.question(
            self,
            "Удалить сессию",
            "Удалить выбранную сессию?",
        ) != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        self._session_order = [sid for sid in self._session_order if sid != current]
        self._session_cache.pop(current, None)
        self._session_state.pop(current, None)
        window = self._session_windows.pop(current, None)
        if window:
            window.close()
        self._persist_sessions()
        self._refresh_sessions_list()
        if self._session_order:
            self._select_session(self._session_order[0])
        else:
            self._clear_session_editor()

    def _on_session_launch_chrome(self):
        session_id = getattr(self, "_current_session_id", "")
        if session_id:
            self._launch_session_chrome(session_id)

    def _on_session_run_prompts(self):
        session_id = getattr(self, "_current_session_id", "")
        if session_id:
            self._run_session_autogen(session_id)

    def _on_session_run_images(self):
        session_id = getattr(self, "_current_session_id", "")
        if session_id:
            self._run_session_images(session_id)

    def _on_session_run_download(self):
        session_id = getattr(self, "_current_session_id", "")
        if session_id:
            self._run_session_download(session_id)

    def _on_session_run_watermark(self):
        session_id = getattr(self, "_current_session_id", "")
        if session_id:
            self._run_session_watermark(session_id)

    def _on_session_stop(self):
        session_id = getattr(self, "_current_session_id", "")
        if session_id:
            self._stop_session_runner(session_id)

    def _on_session_open_downloads(self):
        session_id = getattr(self, "_current_session_id", "")
        if session_id:
            self._open_session_download_dir(session_id)

    def _on_session_open_window(self):
        session_id = getattr(self, "_current_session_id", "")
        if not session_id:
            return
        window = self._session_windows.get(session_id)
        if window is None:
            window = SessionWorkspaceWindow(self, session_id)
            self._session_windows[session_id] = window
        window.update_session(self._session_cache.get(session_id, {}))
        state = self._ensure_session_state(session_id)
        window.set_log(state.get("log", []))
        window.update_status(state.get("status", "idle"), state.get("last_message", ""), state.get("last_rc"))
        window.show()
        window.raise_()
        window.activateWindow()

    def _on_session_name_changed(self, text: str):
        session_id = getattr(self, "_current_session_id", "")
        if not session_id or session_id not in self._session_cache:
            return
        self._session_cache[session_id]["name"] = text.strip()
        self._persist_sessions()
        self._refresh_sessions_list()
        window = self._session_windows.get(session_id)
        if window:
            window.update_session(self._session_cache[session_id])

    def _on_session_prompt_profile_changed(self):
        session_id = getattr(self, "_current_session_id", "")
        if not session_id or session_id not in self._session_cache:
            return
        key = self.cmb_session_prompt_profile.currentData()
        self._session_cache[session_id]["prompt_profile"] = key or PROMPTS_DEFAULT_KEY
        self._persist_sessions()
        self._update_session_status_panel(session_id)

    def _on_session_chrome_profile_changed(self):
        session_id = getattr(self, "_current_session_id", "")
        if not session_id or session_id not in self._session_cache:
            return
        key = self.cmb_session_chrome_profile.currentData()
        self._session_cache[session_id]["chrome_profile"] = key or ""
        self._persist_sessions()

    def _on_session_port_changed(self, value: int):
        session_id = getattr(self, "_current_session_id", "")
        if not session_id or session_id not in self._session_cache:
            return
        self._session_cache[session_id]["cdp_port"] = int(value) if int(value) > 0 else None
        self._persist_sessions()

    def _on_session_path_changed(self, key: str, line: QtWidgets.QLineEdit):
        session_id = getattr(self, "_current_session_id", "")
        if not session_id or session_id not in self._session_cache:
            return
        self._session_cache[session_id][key] = line.text().strip()
        self._persist_sessions()

    def _on_session_notes_changed(self):
        session_id = getattr(self, "_current_session_id", "")
        if not session_id or session_id not in self._session_cache:
            return
        self._session_cache[session_id]["notes"] = self.te_session_notes.toPlainText().strip()
        self._persist_sessions()

    def _on_session_auto_chrome_changed(self, checked: bool):
        session_id = getattr(self, "_current_session_id", "")
        if not session_id or session_id not in self._session_cache:
            return
        self._session_cache[session_id]["auto_launch_chrome"] = bool(checked)
        self._persist_sessions()

    def _on_session_autogen_mode_changed(self):
        session_id = getattr(self, "_current_session_id", "")
        if not session_id or session_id not in self._session_cache:
            return
        mode = self.cmb_session_autogen_mode.currentData() or "idle"
        self._session_cache[session_id]["auto_launch_autogen"] = mode
        self._persist_sessions()

    def _on_session_max_videos_changed(self, value: int):
        session_id = getattr(self, "_current_session_id", "")
        if not session_id or session_id not in self._session_cache:
            return
        limit = int(value)
        self._session_cache[session_id]["max_videos"] = limit if limit > 0 else 0
        self._persist_sessions()

    def _on_session_open_drafts_changed(self, checked: bool):
        session_id = getattr(self, "_current_session_id", "")
        if not session_id or session_id not in self._session_cache:
            return
        self._session_cache[session_id]["open_drafts"] = bool(checked)
        self._persist_sessions()

    def _register_session_waiter(self, session_id: str, task: str) -> Tuple[str, threading.Event]:
        token = f"{session_id}:{uuid.uuid4().hex}"
        event = threading.Event()
        with self._scenario_wait_lock:
            queue = self._session_waiters.setdefault(session_id, [])
            queue.append((token, task or ""))
            self._session_wait_events[token] = event
        return token, event

    def _cancel_session_waiter(self, token: str):
        with self._scenario_wait_lock:
            self._session_wait_events.pop(token, None)
            self._session_wait_results.pop(token, None)
            for sid, entries in list(self._session_waiters.items()):
                filtered = [entry for entry in entries if entry[0] != token]
                if filtered:
                    self._session_waiters[sid] = filtered
                else:
                    self._session_waiters.pop(sid, None)

    def _cancel_all_session_waiters(self, rc: int = 1):
        with self._scenario_wait_lock:
            tokens = list(self._session_wait_events.keys())
            for token in tokens:
                self._session_wait_results[token] = rc
                event = self._session_wait_events.get(token)
                if event:
                    event.set()
            self._session_wait_events.clear()
            self._session_waiters.clear()

    def _wait_for_session(self, token: str, waiter: threading.Event, timeout: Optional[float] = None) -> int:
        deadline = time.monotonic() + timeout if timeout else None
        while True:
            remaining = 0 if deadline is None else max(0.0, deadline - time.monotonic())
            interval = 0.25 if deadline is None else min(0.25, remaining)
            if waiter.wait(interval):
                break
            if deadline is not None and time.monotonic() >= deadline:
                break
            with self._scenario_wait_lock:
                if token not in self._session_wait_events:
                    break
        with self._scenario_wait_lock:
            rc = self._session_wait_results.pop(token, 1)
            self._session_wait_events.pop(token, None)
            for sid, entries in list(self._session_waiters.items()):
                filtered = [entry for entry in entries if entry[0] != token]
                if filtered:
                    self._session_waiters[sid] = filtered
                else:
                    self._session_waiters.pop(sid, None)
        return rc

    def _notify_session_waiters(self, session_id: str, task: str, rc: int):
        with self._scenario_wait_lock:
            entries = self._session_waiters.get(session_id, [])
            remaining: List[Tuple[str, str]] = []
            for token, expected in entries:
                expected_clean = expected or ""
                if expected_clean and expected_clean not in {task, "*"}:
                    remaining.append((token, expected))
                    continue
                event = self._session_wait_events.pop(token, None)
                self._session_wait_results[token] = rc
                if event:
                    event.set()
            if remaining:
                self._session_waiters[session_id] = remaining
            else:
                self._session_waiters.pop(session_id, None)

    def _ensure_session_runner(self, session_id: str) -> ProcRunner:
        runner = self._session_runners.get(session_id)
        if runner:
            return runner
        tag = f"SESSION-{session_id}"
        runner = ProcRunner(tag, self)
        runner.line.connect(lambda text, sid=session_id: self._on_session_runner_line(sid, text))
        runner.finished.connect(lambda rc, _tag, sid=session_id: self._on_session_runner_finished(sid, rc))
        runner.notify.connect(lambda title, message, sid=session_id: self._on_session_runner_notify(sid, title, message))
        self._session_runners[session_id] = runner
        return runner

    def _on_session_runner_line(self, session_id: str, text: str):
        clean = text.rstrip("\n")
        self._append_session_log(session_id, clean)
        self.sig_log.emit(clean)

    def _on_session_runner_notify(self, session_id: str, title: str, message: str):
        session = self._session_cache.get(session_id) or {"name": session_id}
        label = session.get("name") or session_id
        self._notify(f"{title} — {label}", message)

    def _on_session_runner_finished(self, session_id: str, rc: int):
        session = self._session_cache.get(session_id)
        label = session.get("name") if session else session_id
        state = self._ensure_session_state(session_id)
        task = state.get("active_task", "")
        status = "ok" if rc == 0 else "error"
        message = "Завершено успешно" if rc == 0 else "Завершено с ошибкой"
        activity_kind = "success" if rc == 0 else "error"
        tg_message: Optional[str] = None

        if task == "download":
            dest_raw = state.get("download_dest") or ""
            dest_path = Path(dest_raw) if dest_raw else None
            before = state.get("download_before")
            try:
                after = len(self._iter_videos(dest_path)) if dest_path else None
            except Exception:
                after = None
            delta = 0
            if isinstance(after, int) and isinstance(before, int):
                delta = max(after - before, 0)
            base = "Скачивание"
            message = f"{base} завершено {'успешно' if rc == 0 else 'с ошибками'}"
            if isinstance(after, int):
                message += f" · +{delta} (итого {after})"
            if dest_path:
                message += f" → {dest_path}"
            if dest_path:
                status_word = "готово" if rc == 0 else "ошибки"
                total_text = after if isinstance(after, int) else "?"
                tg_message = f"⬇️ {label}: {status_word} · +{delta} файлов (итого {total_text}) → {dest_path}"
        elif task == "autogen_images":
            message = f"Autogen (картинки) {'завершён' if rc == 0 else 'с ошибками'}"
        elif task == "autogen_mix":
            message = f"Autogen (картинки + промпты) {'завершён' if rc == 0 else 'с ошибками'}"
        elif task == "autogen_prompts":
            message = f"Autogen (промпты) {'завершён' if rc == 0 else 'с ошибками'}"
        elif task == "watermark":
            dest_raw = state.get("clean_dest") or ""
            dest_path = Path(dest_raw) if dest_raw else None
            before = state.get("clean_before")
            try:
                after = len(self._iter_videos(dest_path)) if dest_path else None
            except Exception:
                after = None
            delta = None
            if isinstance(after, int) and isinstance(before, int):
                delta = max(after - before, 0)
            message = f"Замена водяного знака {'завершена' if rc == 0 else 'с ошибками'}"
            if isinstance(after, int):
                if delta is not None:
                    message += f" · +{delta} (итого {after})"
                else:
                    message += f" · итог {after}"
            if dest_path:
                message += f" → {dest_path}"
            status_word = "готово" if rc == 0 else "ошибки"
            total_text = after if isinstance(after, int) else "?"
            if dest_path:
                tg_message = f"🧼 {label}: {status_word} · {total_text} файлов → {dest_path}"
            else:
                tg_message = f"🧼 {label}: {status_word}"

        self._notify_session_waiters(session_id, task, rc)
        state["last_task"] = task
        state["active_task"] = ""
        state["download_before"] = None
        state["download_dest"] = ""
        state["clean_before"] = None
        state["clean_dest"] = ""

        if tg_message:
            self._send_tg(tg_message)

        self._append_activity(f"Сессия {label}: {message} (rc={rc})", kind=activity_kind, card_text=False)
        self._set_session_status(session_id, status, message, rc)

    def _stop_session_runner(self, session_id: str):
        runner = self._session_runners.get(session_id)
        if not runner:
            return
        runner.stop()
        state = self._ensure_session_state(session_id)
        state["active_task"] = ""
        state["download_before"] = None
        state["download_dest"] = ""
        state["clean_before"] = None
        state["clean_dest"] = ""
        self._set_session_status(session_id, "idle", "Остановлено пользователем")

    def _run_session_autogen(self, session_id: str, *, force_images: Optional[bool] = None, images_only: bool = False):
        session = self._session_cache.get(session_id)
        if not session:
            return
        runner = self._ensure_session_runner(session_id)
        if runner.proc and runner.proc.poll() is None:
            self._append_session_log(session_id, "[!] Уже выполняется задача")
            return
        workdir = self.cfg.get("autogen", {}).get("workdir", str(WORKERS_DIR / "autogen"))
        entry = self.cfg.get("autogen", {}).get("entry", "main.py")
        env = self._session_env(session_id, force_images=force_images, images_only=images_only)
        state = self._ensure_session_state(session_id)
        if images_only:
            mode = "images"
            state["active_task"] = "autogen_images"
            status_message = "Только генерация картинок…"
        elif force_images:
            mode = "images+prompts"
            state["active_task"] = "autogen_mix"
            status_message = "Генерация картинок и вставка промптов…"
        else:
            mode = "prompts"
            state["active_task"] = "autogen_prompts"
            status_message = "Вставка промптов…"
        state["download_before"] = None
        state["download_dest"] = ""
        label = session.get("name") or session_id
        self._append_activity(f"Сессия {label}: запуск autogen ({mode})", kind="running", card_text=False)
        self._append_session_log(session_id, f"[SESSION] Запуск autogen ({mode})")
        self._set_session_status(session_id, "running", status_message)
        runner.run([sys.executable, entry], cwd=workdir, env=env)

    def _run_session_images(self, session_id: str):
        self._run_session_autogen(session_id, force_images=True, images_only=True)

    def _run_session_download(
        self,
        session_id: str,
        *,
        override_limit: Optional[int] = None,
        open_drafts_override: Optional[bool] = None,
    ):
        session = self._session_cache.get(session_id)
        if not session:
            return
        runner = self._ensure_session_runner(session_id)
        if runner.proc and runner.proc.poll() is None:
            self._append_session_log(session_id, "[!] Уже выполняется задача")
            return
        dl_cfg = self.cfg.get("downloader", {}) or {}
        workdir = dl_cfg.get("workdir", str(WORKERS_DIR / "downloader"))
        entry = dl_cfg.get("entry", "download_all.py")
        dest_dir = self._session_download_dir(session, ensure=True)
        titles_path = self._session_titles_path(session)
        cursor_path = self._session_cursor_path(session)
        try:
            titles_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        try:
            cursor_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["DOWNLOAD_DIR"] = str(dest_dir)
        env["TITLES_FILE"] = str(titles_path)
        env["TITLES_CURSOR_FILE"] = str(cursor_path)
        open_drafts = (
            bool(open_drafts_override)
            if open_drafts_override is not None
            else self._session_open_drafts(session)
        )
        env["OPEN_DRAFTS_FIRST"] = "1" if open_drafts else "0"
        limit_value = (
            self._session_download_limit(session)
            if override_limit is None
            else max(0, int(override_limit))
        )
        env["MAX_VIDEOS"] = str(limit_value if limit_value > 0 else 0)
        port = self._session_chrome_port(session)
        env["CDP_ENDPOINT"] = f"http://127.0.0.1:{int(port)}"
        env["SORA_CDP_ENDPOINT"] = env["CDP_ENDPOINT"]
        python = sys.executable
        cmd = [python, entry]
        label = session.get("name") or session_id
        state = self._ensure_session_state(session_id)
        state["active_task"] = "download"
        state["download_dest"] = str(dest_dir)
        try:
            before = len(self._iter_videos(dest_dir))
        except Exception:
            before = 0
        state["download_before"] = before
        self._append_activity(
            f"Сессия {label}: запуск скачивания → {dest_dir}",
            kind="running",
            card_text=False,
        )
        limit_display = (
            f"{limit_value}"
            if limit_value > 0
            else self._session_download_limit_label(session)
        )
        self._append_session_log(
            session_id,
            f"[SESSION] Старт скачивания → {dest_dir} (лимит {limit_display})",
        )
        if override_limit is not None and override_limit > 0:
            limit_label = f"{override_limit}"
        else:
            limit_label = self._session_download_limit_label(session)
        self._set_session_status(session_id, "running", f"Скачивание видео… (лимит {limit_label})")
        self._send_tg(f"⬇️ {label}: скачивание запускается → {dest_dir}")
        runner.run(cmd, cwd=workdir, env=env)

    def _open_session_download_dir(self, session_id: str):
        session = self._session_cache.get(session_id)
        if not session:
            return
        path = self._session_download_dir(session, ensure=True)
        open_in_finder(path)

    def _gather_session_downloads(self) -> None:
        dest = _project_path(self.cfg.get("downloads_dir", str(DL_DIR)))
        dest.mkdir(parents=True, exist_ok=True)
        allowed_ext = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}

        def _slug(text: str) -> str:
            slug = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")
            return slug or "session"

        copied = 0
        skipped = 0
        for session_id in self._session_order:
            session = self._session_cache.get(session_id)
            if not session:
                continue
            src = self._session_download_dir(session)
            try:
                entries = sorted(src.iterdir())
            except FileNotFoundError:
                continue
            prefix = _slug(session.get("name") or session_id)
            for video in entries:
                if not video.is_file() or video.suffix.lower() not in allowed_ext:
                    continue
                target = dest / video.name
                if target.exists():
                    base = f"{video.stem}_{prefix}"
                    counter = 1
                    candidate = dest / f"{base}{video.suffix}"
                    while candidate.exists():
                        counter += 1
                        candidate = dest / f"{base}_{counter}{video.suffix}"
                    target = candidate
                    skipped += 1
                shutil.copy2(video, target)
                copied += 1

        msg = f"Готово: собрано {copied} файлов"
        if skipped:
            msg += f", дубликаты переименованы {skipped}"
        self._append_activity(msg, kind="success", card_text=False)
        self._post_status(msg, state="ok")

    def _run_session_watermark(self, session_id: str):
        session = self._session_cache.get(session_id)
        if not session:
            return
        runner = self._ensure_session_runner(session_id)
        if runner.proc and runner.proc.poll() is None:
            self._append_session_log(session_id, "[!] Уже выполняется задача")
            return

        cfg = self.cfg.get("watermark_cleaner", {}) or {}
        workdir = cfg.get("workdir", str(WORKERS_DIR / "watermark_cleaner"))
        entry = cfg.get("entry", "restore.py")
        source_dir = self._session_download_dir(session, ensure=True)
        output_dir = self._session_watermark_output_dir(session, ensure=True)
        template_path = _project_path(cfg.get("template", str(PROJECT_ROOT / "watermark.png")))

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["WMR_SOURCE_DIR"] = str(source_dir)
        env["WMR_OUTPUT_DIR"] = str(output_dir)
        env["WMR_TEMPLATE"] = str(template_path)
        env["WMR_MASK_THRESHOLD"] = str(int(cfg.get("mask_threshold", 8) or 0))
        env["WMR_THRESHOLD"] = str(float(cfg.get("threshold", 0.78) or 0.78))
        env["WMR_FRAMES"] = str(int(cfg.get("frames", 120) or 1))
        env["WMR_DOWNSCALE"] = str(int(cfg.get("downscale", 1080) or 0))
        env["WMR_SCALE_MIN"] = str(float(cfg.get("scale_min", 0.85) or 0.85))
        env["WMR_SCALE_MAX"] = str(float(cfg.get("scale_max", 1.2) or 1.2))
        env["WMR_SCALE_STEPS"] = str(int(cfg.get("scale_steps", 9) or 3))
        env["WMR_FULL_SCAN"] = "1" if bool(cfg.get("full_scan")) else "0"
        env["WMR_PADDING_PX"] = str(int(cfg.get("padding_px", 12) or 0))
        env["WMR_PADDING_PCT"] = str(float(cfg.get("padding_pct", 0.18) or 0.0))
        env["WMR_MIN_SIZE"] = str(int(cfg.get("min_size", 32) or 2))
        env["WMR_SEARCH_SPAN"] = str(int(cfg.get("search_span", 12) or 1))
        env["WMR_POOL"] = str(int(cfg.get("pool", 4) or 1))
        env["WMR_MAX_IOU"] = str(float(cfg.get("max_iou", 0.25) or 0.0))
        env["WMR_BLEND"] = str(cfg.get("blend", "normal") or "normal")
        env["WMR_INPAINT_RADIUS"] = str(int(cfg.get("inpaint_radius", 6) or 1))
        env["WMR_INPAINT_METHOD"] = str(cfg.get("inpaint_method", "telea") or "telea")

        python = sys.executable
        cmd = [python, entry]
        label = self._session_instance_label(session)
        total = len(self._iter_videos(source_dir)) if source_dir.exists() else 0
        state = self._ensure_session_state(session_id)
        state["active_task"] = "watermark"
        try:
            before = len(self._iter_videos(output_dir)) if output_dir.exists() else 0
        except Exception:
            before = 0
        state["clean_before"] = before
        state["clean_dest"] = str(output_dir)
        self._append_activity(
            f"Сессия {label}: замена водяного знака → {output_dir}",
            kind="running",
            card_text=False,
        )
        self._append_session_log(session_id, f"[SESSION] Старт замены водяного знака → {output_dir}")
        self._set_session_status(session_id, "running", "Замена водяного знака…")
        self._send_tg(f"🧼 {label}: замена водяного знака запускается ({total} файлов) → {output_dir}")
        runner.run(cmd, cwd=workdir, env=env)

    def _launch_session_chrome(self, session_id: str):
        session = self._session_cache.get(session_id)
        if not session:
            return
        self._open_chrome(session=session)

    def _apply_session_autolaunches(self):
        for session_id in self._session_order:
            session = self._session_cache.get(session_id)
            if not session:
                continue
            if session.get("auto_launch_chrome"):
                QtCore.QTimer.singleShot(0, lambda sid=session_id: self._launch_session_chrome(sid))
            mode = session.get("auto_launch_autogen", "idle")
            if mode == "prompts":
                QtCore.QTimer.singleShot(0, lambda sid=session_id: self._run_session_autogen(sid))
            elif mode == "images":
                QtCore.QTimer.singleShot(0, lambda sid=session_id: self._run_session_images(sid))

    def _refresh_update_buttons(self):
        available = bool(shutil.which("git")) and (PROJECT_ROOT / ".git").exists()
        tooltip_disabled = (
            "Кнопка доступна только при запуске из git-репозитория. "
            "См. раздел README → Обновления для альтернативного сценария."
        )
        buttons = [
            getattr(self, "btn_update_check", None),
            getattr(self, "btn_update_pull", None),
        ]
        for btn in buttons:
            if not btn:
                continue
            btn.setEnabled(available)
            if not available:
                btn.setToolTip(tooltip_disabled)

    def _default_profile_prompts(self, profile_name: Optional[str]) -> Path:
        if not profile_name:
            return WORKERS_DIR / "autogen" / "prompts.txt"
        slug = slugify(profile_name) or "profile"
        return WORKERS_DIR / "autogen" / f"prompts_{slug}.txt"

    def _apply_theme(self):
        app = QtWidgets.QApplication.instance()
        if not app:
            return

        app.setStyle("Fusion")

        palette = QtGui.QPalette()
        base = QtGui.QColor("#0f172a")
        panel = QtGui.QColor("#101a2f")
        field = QtGui.QColor("#111d32")
        text = QtGui.QColor("#f1f5f9")
        disabled = QtGui.QColor("#8a94a6")
        highlight = QtGui.QColor("#4c6ef5")

        roles = {
            QtGui.QPalette.ColorRole.Window: base,
            QtGui.QPalette.ColorRole.Base: field,
            QtGui.QPalette.ColorRole.AlternateBase: panel,
            QtGui.QPalette.ColorRole.WindowText: text,
            QtGui.QPalette.ColorRole.Text: text,
            QtGui.QPalette.ColorRole.Button: QtGui.QColor("#1f2d4a"),
            QtGui.QPalette.ColorRole.ButtonText: QtGui.QColor("#f8fafc"),
            QtGui.QPalette.ColorRole.Highlight: highlight,
            QtGui.QPalette.ColorRole.HighlightedText: QtGui.QColor("#0f172a"),
            QtGui.QPalette.ColorRole.BrightText: QtGui.QColor("#ffffff"),
            QtGui.QPalette.ColorRole.Link: QtGui.QColor("#93c5fd"),
        }
        for role, color in roles.items():
            palette.setColor(QtGui.QPalette.ColorGroup.Active, role, color)
            palette.setColor(QtGui.QPalette.ColorGroup.Inactive, role, color)
            palette.setColor(QtGui.QPalette.ColorGroup.Disabled, role, disabled)

        app.setPalette(palette)

        app.setStyleSheet(
            """
            QWidget { background-color: #0d1425; color: #e2e8f0; }
            QLabel { background: transparent; }
            QGroupBox { border: 1px solid #1f2a40; border-radius: 12px; margin-top: 16px; padding-top: 12px; background: rgba(12,18,31,0.9); }
            QGroupBox::title { subcontrol-origin: margin; left: 16px; padding: 0 6px; background: #0d1425; color: #94a3b8; }
            QToolButton#contextToggleButton {
                border: 1px solid #27364d;
                border-radius: 10px;
                padding: 6px 12px;
                background: rgba(15,23,42,0.6);
                color: #e2e8f0;
            }
            QToolButton#contextToggleButton:hover { border-color: #8ba8ff; }
            QToolButton#contextToggleButton:checked {
                background: rgba(99,102,241,0.25);
                border-color: #8ba8ff;
            }
            QSplitter#mainSplitter::handle {
                background: transparent;
                width: 10px;
                border: none;
                margin: 0 2px;
            }
            QSplitter#mainSplitter::handle:hover {
                background: rgba(129,140,248,0.22);
                border-radius: 6px;
            }
            QFrame#navFrame {
                background: rgba(15,23,42,0.82);
                border: 1px solid #1f2a40;
                border-radius: 18px;
                padding: 6px 0;
            }
            QFrame#sectionContainer {
                background: radial-gradient(circle at 12% 18%, rgba(76,110,245,0.08), transparent 42%),
                             radial-gradient(circle at 88% 30%, rgba(34,211,238,0.06), transparent 38%),
                             linear-gradient(145deg, rgba(10,16,28,0.92), rgba(12,18,31,0.95));
                border: 1px solid rgba(148,163,184,0.14);
                border-radius: 22px;
                padding: 8px;
            }
            QFrame#sectionSurface {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(14,21,36,0.96), stop:1 rgba(11,18,31,0.94));
                border: 1px solid rgba(148,163,184,0.22);
                border-radius: 20px;
                padding: 16px;
            }
            QScrollArea#sectionScrollArea {
                background: rgba(5,10,18,0.65);
                border: none;
            }
            QWidget#sectionScrollWidget {
                background: rgba(8,13,23,0.6);
            }
            QFrame#contextContainer {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 rgba(12,19,34,0.92), stop:1 rgba(10,15,28,0.9));
                border: 1px solid #1e293b;
                border-radius: 18px;
                padding: 12px 0;
            }
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #1d2b46, stop:1 #15213a);
                border: 1px solid #2c3d63;
                border-radius: 10px;
                padding: 6px 12px;
                color: #f8fafc;
                font-weight: 600;
                letter-spacing: 0.2px;
                min-height: 30px;
            }
            QPushButton:disabled { background: #131a2a; border-color: #1f2a3f; color: #475569; }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #23375b, stop:1 #1b2948);
                border-color: #8ba8ff;
            }
            QPushButton:pressed { background: #121d34; border-color: #3d4f78; }
            QPushButton:focus { outline: none; border-color: #9fb4ff; }
            QToolButton {
                background: rgba(12,19,34,0.85);
                border: 1px solid #27364d;
                border-radius: 10px;
                padding: 4px 8px;
                color: #e2e8f0;
            }
            QToolButton:hover { border-color: #8ba8ff; background: rgba(35,55,91,0.65); }
            QToolButton:pressed { background: rgba(23,35,63,0.8); }
            QLineEdit, QSpinBox, QDoubleSpinBox, QDateTimeEdit, QComboBox, QTextEdit, QPlainTextEdit {
                background-color: #0a1324; border: 1px solid #1f2a40; border-radius: 8px; padding: 4px 8px;
                selection-background-color: #6366f1; selection-color: #f8fafc;
            }
            QPlainTextEdit { padding: 8px; }
            QCheckBox { color: #e2e8f0; spacing: 8px; }
            QCheckBox::indicator {
                width: 18px; height: 18px; border-radius: 5px;
                border: 1px solid #334155; background: #0a1324;
            }
            QCheckBox::indicator:unchecked { image: none; }
            QCheckBox::indicator:checked {
                background: #6366f1; border: 1px solid #a5b4fc; image: none;
            }
            QCheckBox::indicator:disabled { background: #1e293b; border-color: #27364d; }
            QListWidget { border: none; background: transparent; color: #f1f5f9; }
            QListWidget#sectionNav { background: transparent; border: none; padding: 6px 4px; }
            QListWidget#sectionNav::item { margin: 4px 8px; padding: 8px 12px; border-radius: 12px; color: #cbd5f5; background: rgba(99,102,241,0.08); }
            QListWidget#sectionNav::item:!enabled { margin: 16px 8px 6px 8px; padding: 4px 14px; color: #64748b; font-weight:600; background: transparent; }
            QListWidget#sectionNav::item:selected { background: rgba(99,102,241,0.36); color: #ffffff; }
            QListWidget#sectionNav::item:hover { background: rgba(148,163,184,0.18); }
            QTabWidget::pane { border: 1px solid #1f2a40; border-radius: 12px; margin-top: -4px; background: rgba(13,20,37,0.6); }
            QTabBar::tab { background: rgba(15,23,42,0.6); border: 1px solid #1f2a40; padding: 6px 12px; margin-right: 4px;
                           border-top-left-radius: 6px; border-top-right-radius: 6px; }
            QTabBar::tab:selected { background: rgba(99,102,241,0.35); color: #f8fafc; }
            QTabBar::tab:hover { background: rgba(99,102,241,0.25); }
            QLabel#statusBanner { padding: 12px 18px; border-radius: 12px; border: 1px solid #1f2a40; background: rgba(148,163,184,0.1); }
            QLabel#dashboardTitle { font-size: 22px; font-weight: 700; color: #f8fafc; }
            QLabel#dashboardSubtitle { color: #cbd5f5; font-size: 13px; }
            QLabel#dashboardSectionTitle { font-size: 13px; font-weight: 600; letter-spacing: 0.4px; text-transform: uppercase; color: #9fb7ff; }
            QFrame#dashboardHeader { background: transparent; border: 1px solid #1f2a40; border-radius: 18px; }
            QFrame#dashboardQuickActions, QFrame#dashboardStats, QFrame#dashboardActivity, QFrame#dashboardQuickSettings { background: rgba(15,23,42,0.55); border: 1px solid #1f2a40; border-radius: 16px; }
            QFrame#contextContainer { background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 rgba(12,19,34,0.92), stop:1 rgba(10,15,28,0.9)); }
            QFrame#customCommandPanel { background: rgba(15,23,42,0.55); border: 1px solid rgba(148,163,184,0.22); border-radius: 16px; }
            QLabel#customCommandTitle { font-size: 13px; font-weight: 600; color: #cbd5f5; }
            QLabel#customCommandSubtitle { color: #94a3b8; font-size: 11px; }
            QPushButton#customCommandButton { padding: 8px 14px; text-align: left; }
            QPushButton#customCommandButton:hover { background: rgba(99,102,241,0.2); }
            QPushButton#customCommandButton:pressed { background: rgba(99,102,241,0.28); }
            QListWidget#customCommandList { background: rgba(8,17,32,0.6); border: 1px solid #1f2a40; border-radius: 10px; }
            QListWidget#customCommandList::item { padding: 6px 10px; border-radius: 6px; }
            QListWidget#customCommandList::item:selected { background: rgba(99,102,241,0.32); }
            QToolButton#customCommandMove {
                border: 1px solid #27364d;
                border-radius: 8px;
                padding: 6px;
                background: rgba(15,23,42,0.6);
                color: #e2e8f0;
            }
            QToolButton#customCommandMove:hover { border-color: #8ba8ff; }
            QToolButton#customCommandMove:disabled { border-color: #1f2a3f; color: #475569; }
            QDialog#customCommandDialog { background-color: #101a2f; border: 1px solid #1f2a40; border-radius: 14px; }
            QDialog#customCommandDialog QLabel { color: #cbd5f5; }
            QLabel#customCommandHint { color: #94a3b8; font-size: 11px; }
            QTextBrowser { background-color: #081120; border: 1px solid #1f2a40; border-radius: 10px; padding: 12px; }
            QScrollArea { border: none; }
            QFrame#sessionWindowActions, QFrame#sessionWindowStatus, QFrame#sessionWindowLog { background: rgba(15,23,42,0.45); border: 1px solid #1d2840; border-radius: 12px; }
            QLabel#sessionWindowTitle { font-size: 20px; font-weight: 600; }
            QLabel#sessionWindowDetails { color: #94a3b8; }
            QLabel#sessionWindowStatusLabel { font-size: 13px; font-weight: 600; color: #e2e8f0; }
            QLabel#sessionWindowHint { color: #94a3b8; font-size: 11px; }
            QLabel#sessionWindowLogTitle { font-size: 12px; font-weight: 600; color: #cbd5f5; }
            QComboBox#sessionActionCombo {
                background: #0f172a;
                border: 1px solid #27364d;
                padding: 6px 10px;
                border-radius: 10px;
                font-weight: 500;
            }
            QPushButton#sessionActionStart {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #2563eb, stop:1 #1d4ed8);
                border: 1px solid #7dd3fc;
                color: #e2e8f0;
                padding: 8px 16px;
                border-radius: 12px;
            }
            QPushButton#sessionActionStart:hover { border-color: #a5b4fc; }
            QFrame#contextCard { background: rgba(15,23,42,0.6); border:1px solid rgba(148,163,184,0.24); border-radius:16px; }
            QFrame#contextStatusCard { background: rgba(15,23,42,0.78); border:1px solid rgba(148,163,184,0.28); border-radius:16px; }
            QLabel#contextStatusTitle { color:#cbd5f5; font-weight:600; letter-spacing:0.3px; }
            QLabel#contextStatusText { color:#e2e8f0; font-size:12px; }
            QProgressBar#contextStatusProgress {
                background:#0f172a;
                border:1px solid rgba(148,163,184,0.16);
                border-radius:6px;
                height:12px;
            }
            QProgressBar#contextStatusProgress::chunk { background:#4c6ef5; border-radius:6px; }
            QLabel#contextTitle { font-size:14px; font-weight:600; color:#cbd5f5; }
            QLabel#contextSubtitle { color:#94a3b8; font-size:12px; }
            QDialog#commandPalette { background: transparent; }
            QFrame#commandPaletteFrame { background: rgba(9,13,25,0.96); border-radius:18px; border:1px solid rgba(99,102,241,0.35); }
            QLineEdit#commandPaletteSearch { background:#070d1a; border-radius:12px; border:1px solid #27364d; padding:10px 14px; font-size:14px; }
            QListWidget#commandPaletteList { background: transparent; border:none; }
            QListWidget#commandPaletteList::item { margin:4px 0; padding:10px 12px; border-radius:10px; color:#e2e8f0; }
            QListWidget#commandPaletteList::item:selected { background: rgba(99,102,241,0.35); }
            QLabel#commandPaletteHint { color:#64748b; font-size:11px; }
            """
        )

    def _notify(self, title: str, message: str):
        try:
            self.tray.showMessage(title, message, QtWidgets.QSystemTrayIcon.MessageIcon.Information, 5000)
        except Exception:
            pass

    def _send_tg(self, text: str) -> bool:
        tg_cfg = self.cfg.get("telegram", {}) or {}
        if not tg_cfg.get("enabled"):
            if not getattr(self, "_tg_disabled_warned", False):
                self._append_activity("Telegram выключен — уведомление пропущено", kind="info")
                self._tg_disabled_warned = True
            return False
        ok = send_tg(self.cfg, text)
        if ok:
            self._append_activity(f"Telegram ✓ {text}", kind="success")
            self._tg_disabled_warned = False
        else:
            self._append_activity("Telegram ✗ не удалось отправить сообщение", kind="error")
            self._tg_disabled_warned = False
        return ok

    def ui(self, fn):
        QtCore.QTimer.singleShot(0, fn)

    def _browse_dir(self, line: QtWidgets.QLineEdit, title: str):
        base = line.text().strip()
        dlg = QtWidgets.QFileDialog(self, title)
        dlg.setFileMode(QtWidgets.QFileDialog.FileMode.Directory)
        dlg.setOption(QtWidgets.QFileDialog.Option.ShowDirsOnly, True)
        if base and os.path.isdir(base):
            dlg.setDirectory(base)
        if dlg.exec():
            sel = dlg.selectedFiles()
            if sel:
                line.setText(sel[0])

    def _browse_file(self, line: QtWidgets.QLineEdit, title: str, filter_str: str = "Все файлы (*.*)"):
        base = line.text().strip()
        dlg = QtWidgets.QFileDialog(self, title)
        dlg.setFileMode(QtWidgets.QFileDialog.FileMode.ExistingFile)
        dlg.setNameFilter(filter_str)
        if base and os.path.isfile(base):
            dlg.selectFile(base)
        if dlg.exec():
            sel = dlg.selectedFiles()
            if sel:
                line.setText(sel[0])

    def _open_path_from_edit(self, line: QtWidgets.QLineEdit):
        if not isinstance(line, QtWidgets.QLineEdit):
            return
        target = line.text().strip()
        if not target:
            return
        open_in_finder(target)

    def _toggle_youtube_schedule(self):
        enable = self.cb_youtube_schedule.isChecked() and not self.cb_youtube_draft_only.isChecked()
        self.dt_youtube_publish.setEnabled(enable)
        self.sb_youtube_interval.setEnabled(enable)
        self._update_youtube_queue_label()

    def _sync_draft_checkbox(self):
        self.cb_youtube_draft_only.blockSignals(True)
        self.cb_youtube_draft_only.setChecked(self.cb_youtube_default_draft.isChecked())
        self.cb_youtube_draft_only.blockSignals(False)
        self._toggle_youtube_schedule()

    def _apply_default_delay(self):
        minutes = int(self.sb_youtube_default_delay.value())
        self.dt_youtube_publish.setDateTime(QtCore.QDateTime.currentDateTime().addSecs(minutes * 60))

    def _apply_tiktok_default_delay(self):
        if not hasattr(self, "dt_tiktok_publish"):
            return
        minutes = int(self.sb_tiktok_default_delay.value())
        self.dt_tiktok_publish.setDateTime(QtCore.QDateTime.currentDateTime().addSecs(minutes * 60))

    def _reflect_youtube_interval(self, value: int):
        try:
            val = int(value)
        except (TypeError, ValueError):
            val = 0
        if self.sb_youtube_interval_default.value() != val:
            self.sb_youtube_interval_default.blockSignals(True)
            self.sb_youtube_interval_default.setValue(val)
            self.sb_youtube_interval_default.blockSignals(False)
        self._update_youtube_queue_label()

    def _reflect_youtube_limit(self, value: int):
        try:
            val = int(value)
        except (TypeError, ValueError):
            val = 0
        if self.sb_youtube_limit_default.value() != val:
            self.sb_youtube_limit_default.blockSignals(True)
            self.sb_youtube_limit_default.setValue(val)
            self.sb_youtube_limit_default.blockSignals(False)
        self._update_youtube_queue_label()

    def _sync_delay_from_datetime(self):
        if not self.dt_youtube_publish.isEnabled() or self.cb_youtube_draft_only.isChecked():
            return
        target = self.dt_youtube_publish.dateTime()
        if not target.isValid():
            return
        now = QtCore.QDateTime.currentDateTime()
        minutes = max(0, now.secsTo(target) // 60)
        if self.sb_youtube_default_delay.value() != minutes:
            self.sb_youtube_default_delay.blockSignals(True)
            self.sb_youtube_default_delay.setValue(int(minutes))
            self.sb_youtube_default_delay.blockSignals(False)

    # ----- UI -----
    def _build_ui(self):
        central = QtWidgets.QWidget(self)
        central.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        self.setCentralWidget(central)
        v = QtWidgets.QVBoxLayout(central)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(12)

        banner = QtWidgets.QLabel("<b>Sora Suite</b>: выбери шаги и запусти сценарий. Уведомления появятся в системном трее.")
        banner.setObjectName("statusBanner")
        banner.setWordWrap(True)
        banner.setStyleSheet(
            "QLabel#statusBanner{padding:12px 18px;border-radius:12px;"
            "background:transparent;color:#f1f5f9;font-weight:600;letter-spacing:0.3px;border:1px solid #1a1f4a;}"
        )
        v.addWidget(banner)

        toolbar = QtWidgets.QFrame()
        toolbar.setObjectName("topToolbar")
        toolbar.setStyleSheet(
            "QFrame#topToolbar{background:rgba(15,23,42,0.92);border:1px solid #1e293b;"
            "border-radius:12px;}"
            "QToolButton#topFolderButton{padding:4px 12px;border-radius:10px;"
            "background:#1e293b;color:#e2e8f0;font-size:11px;font-weight:600;}"
            "QToolButton#topFolderButton::hover{background:#27364d;}"
            "QToolButton#topCommandButton{padding:6px 14px;border-radius:10px;background:rgba(99,102,241,0.15);"
            "color:#cbd5f5;font-weight:600;}"
            "QToolButton#topCommandButton::hover{background:rgba(129,140,248,0.25);}" 
            "QPushButton#topActionButton{padding:8px 22px;border-radius:12px;"
            "background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #2563eb, stop:1 #7c3aed);"
            "border:1px solid #3b82f6;font-weight:600;letter-spacing:0.3px;}"
            "QPushButton#topActionButton:hover{border-color:#a855f7;"
            "background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #1d4ed8, stop:1 #6d28d9);}"
            "QPushButton#topActionButton:disabled{background:#1e293b;border-color:#27364d;color:#64748b;}"
            "QPushButton#topActionButton[theme=\"danger\"]{background:#dc2626;border-color:#f87171;}"
            "QPushButton#topActionButton[theme=\"danger\"]:hover{background:#b91c1c;border-color:#fca5a5;}"
        )
        tb = QtWidgets.QHBoxLayout(toolbar)
        tb.setContentsMargins(12, 6, 12, 6)
        tb.setSpacing(6)

        folder_shortcuts = [
            ("📁", "PRJ"),
            ("🧾", "RAW"),
            ("🌫️", "BLR"),
            ("🎬", "MRG"),
            ("🖼️", "IMG"),
            ("🧼", "RST"),
        ]

        def make_folder_button(symbol: str, label: str) -> QtWidgets.QToolButton:
            btn = QtWidgets.QToolButton()
            btn.setObjectName("topFolderButton")
            btn.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextOnly)
            btn.setText(f"{symbol} {label}")
            btn.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
            return btn

        folders_frame = QtWidgets.QFrame()
        folders_frame.setObjectName("foldersTopFrame")
        folders_frame.setStyleSheet(
            "QFrame#foldersTopFrame{background:rgba(15,23,42,0.85);border-radius:10px;padding:2px 8px;}"
            "QLabel#foldersTopLabel{color:#cbd5f5;font-weight:600;}"
        )
        folders_block = QtWidgets.QHBoxLayout(folders_frame)
        folders_block.setContentsMargins(4, 0, 4, 0)
        folders_block.setSpacing(6)
        folders_block.setStretch(0, 0)
        lbl_folders = QtWidgets.QLabel("🗂️ Каталоги")
        lbl_folders.setObjectName("foldersTopLabel")
        folders_block.addWidget(lbl_folders)
        folders_block.addSpacing(4)
        self.btn_open_root = make_folder_button(*folder_shortcuts[0][:2])
        self.btn_open_raw = make_folder_button(*folder_shortcuts[1][:2])
        self.btn_open_blur = make_folder_button(*folder_shortcuts[2][:2])
        self.btn_open_merge = make_folder_button(*folder_shortcuts[3][:2])
        self.btn_open_images_top = make_folder_button(*folder_shortcuts[4][:2])
        self.btn_open_restored_top = make_folder_button(*folder_shortcuts[5][:2])
        self.btn_collect_raw = make_folder_button("⇆", "RAW → общая")
        for btn in (
            self.btn_open_root,
            self.btn_open_raw,
            self.btn_open_blur,
            self.btn_open_merge,
            self.btn_open_images_top,
            self.btn_open_restored_top,
            self.btn_collect_raw,
        ):
            btn.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Expanding,
                QtWidgets.QSizePolicy.Policy.Fixed,
            )
            folders_block.addWidget(btn, 1)
        folders_block.addStretch(1)
        tb.addWidget(folders_frame)

        self.btn_command_palette_toolbar = QtWidgets.QToolButton()
        self.btn_command_palette_toolbar.setObjectName("topCommandButton")
        self.btn_command_palette_toolbar.setText("🧭 Команды")
        self.btn_command_palette_toolbar.setToolButtonStyle(
            QtCore.Qt.ToolButtonStyle.ToolButtonTextOnly
        )
        self.btn_command_palette_toolbar.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        self.btn_command_palette_toolbar.clicked.connect(self._open_command_palette)
        tb.addWidget(self.btn_command_palette_toolbar)

        self.btn_toggle_commands = QtWidgets.QToolButton()
        self.btn_toggle_commands.setObjectName("contextToggleButton")
        self.btn_toggle_commands.setCheckable(True)
        self.btn_toggle_commands.setToolButtonStyle(
            QtCore.Qt.ToolButtonStyle.ToolButtonTextOnly
        )
        self.btn_toggle_commands.setText("🧩 Панель")
        self.btn_toggle_commands.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        tb.addWidget(self.btn_toggle_commands)

        tb.addStretch(1)

        self.btn_start_selected = QtWidgets.QPushButton("⚡ Старт выбранного")
        self.btn_start_selected.setObjectName("topActionButton")
        self.btn_start_selected.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        self.btn_stop_all = QtWidgets.QPushButton("⛔ Стоп все")
        self.btn_stop_all.setObjectName("topActionButton")
        self.btn_stop_all.setProperty("theme", "danger")
        self.btn_stop_all.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        for btn in (self.btn_start_selected, self.btn_stop_all):
            btn.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Expanding,
                QtWidgets.QSizePolicy.Policy.Fixed,
            )
        action_block = QtWidgets.QHBoxLayout()
        action_block.setContentsMargins(0, 0, 0, 0)
        action_block.setSpacing(6)
        action_block.addWidget(self.btn_start_selected, 1)
        action_block.addWidget(self.btn_stop_all, 1)
        tb.addLayout(action_block)

        for themed in (self.btn_stop_all,):
            themed.style().unpolish(themed)
            themed.style().polish(themed)

        v.addWidget(toolbar)

        body = QtWidgets.QFrame()
        body.setObjectName("mainBody")
        body_layout = QtWidgets.QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        self.body_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self.body_splitter.setObjectName("mainSplitter")
        self.body_splitter.setChildrenCollapsible(False)
        self.body_splitter.setHandleWidth(8)
        body_layout.addWidget(self.body_splitter, 1)

        nav_frame = QtWidgets.QFrame()
        nav_frame.setObjectName("navFrame")
        nav_layout = QtWidgets.QVBoxLayout(nav_frame)
        nav_layout.setContentsMargins(0, 0, 0, 0)
        nav_layout.setSpacing(0)

        self.section_nav = QtWidgets.QListWidget()
        self.section_nav.setObjectName("sectionNav")
        self.section_nav.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.section_nav.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.section_nav.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.section_nav.setSpacing(4)
        self.section_nav.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.section_nav.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.section_nav.setMinimumWidth(220)
        self.section_nav.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Preferred,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        self.section_nav.itemClicked.connect(self._on_nav_item_clicked)
        nav_layout.addWidget(self.section_nav)
        self.body_splitter.addWidget(nav_frame)

        self.section_stack_container = QtWidgets.QFrame()
        self.section_stack_container.setObjectName("sectionContainer")
        section_container_layout = QtWidgets.QVBoxLayout(self.section_stack_container)
        section_container_layout.setContentsMargins(0, 0, 0, 0)
        section_container_layout.setSpacing(0)

        self.section_stack = QtWidgets.QStackedWidget()
        self.section_stack.setObjectName("sectionStack")
        section_container_layout.addWidget(self.section_stack, 1)
        self.body_splitter.addWidget(self.section_stack_container)

        self.context_container = QtWidgets.QFrame()
        self.context_container.setObjectName("contextContainer")
        context_layout = QtWidgets.QVBoxLayout(self.context_container)
        context_layout.setContentsMargins(0, 0, 0, 0)
        context_layout.setSpacing(12)
        self.context_container.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Preferred,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )

        self.context_stack = QtWidgets.QStackedWidget()
        self.context_stack.setObjectName("contextStack")
        self.context_stack.setMinimumWidth(260)
        self.context_stack.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Preferred,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        context_layout.addWidget(self.context_stack, 1)

        self.context_status_card = QtWidgets.QFrame()
        self.context_status_card.setObjectName("contextStatusCard")
        status_card_layout = QtWidgets.QVBoxLayout(self.context_status_card)
        status_card_layout.setContentsMargins(18, 16, 18, 18)
        status_card_layout.setSpacing(10)
        self.lbl_context_status_heading = QtWidgets.QLabel("Текущий процесс")
        self.lbl_context_status_heading.setObjectName("contextStatusTitle")
        status_card_layout.addWidget(self.lbl_context_status_heading)
        status_body_row = QtWidgets.QHBoxLayout()
        status_body_row.setSpacing(8)
        self.lbl_context_status_icon = QtWidgets.QLabel("—")
        self.lbl_context_status_icon.setMinimumWidth(18)
        self.lbl_context_status_icon.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop)
        self.lbl_context_status_text = QtWidgets.QLabel("—")
        self.lbl_context_status_text.setObjectName("contextStatusText")
        self.lbl_context_status_text.setWordWrap(True)
        status_body_row.addWidget(self.lbl_context_status_icon, 0)
        status_body_row.addWidget(self.lbl_context_status_text, 1)
        status_card_layout.addLayout(status_body_row)
        self.pb_context_status = QtWidgets.QProgressBar()
        self.pb_context_status.setObjectName("contextStatusProgress")
        self.pb_context_status.setMinimum(0)
        self.pb_context_status.setMaximum(1)
        self.pb_context_status.setValue(1)
        self.pb_context_status.setFormat("—")
        self.pb_context_status.setTextVisible(False)
        status_card_layout.addWidget(self.pb_context_status)
        context_layout.addWidget(self.context_status_card)

        self.custom_command_panel = QtWidgets.QFrame()
        self.custom_command_panel.setObjectName("customCommandPanel")
        custom_panel_layout = QtWidgets.QVBoxLayout(self.custom_command_panel)
        custom_panel_layout.setContentsMargins(18, 18, 18, 18)
        custom_panel_layout.setSpacing(10)
        lbl_custom_title = QtWidgets.QLabel("Быстрые команды")
        lbl_custom_title.setObjectName("customCommandTitle")
        custom_panel_layout.addWidget(lbl_custom_title)
        self.custom_command_caption = QtWidgets.QLabel(
            "Настрой список в разделе «Настройки → Интерфейс», чтобы запускать любимые скрипты в один клик."
        )
        self.custom_command_caption.setObjectName("customCommandSubtitle")
        self.custom_command_caption.setWordWrap(True)
        custom_panel_layout.addWidget(self.custom_command_caption)
        self.custom_command_button_host = QtWidgets.QWidget()
        self.custom_command_button_layout = QtWidgets.QVBoxLayout(self.custom_command_button_host)
        self.custom_command_button_layout.setContentsMargins(0, 0, 0, 0)
        self.custom_command_button_layout.setSpacing(6)
        custom_panel_layout.addWidget(self.custom_command_button_host)
        context_layout.addWidget(self.custom_command_panel)

        self.body_splitter.addWidget(self.context_container)
        self.body_splitter.setStretchFactor(0, 0)
        self.body_splitter.setStretchFactor(1, 1)
        self.body_splitter.setStretchFactor(2, 0)

        v.addWidget(body, 1)

        self._section_index = {}
        self._section_order = []
        self._section_nav_items: Dict[str, QtWidgets.QListWidgetItem] = {}
        self._section_meta: Dict[str, Dict[str, Any]] = {}
        self._nav_group_rows: Dict[str, QtWidgets.QListWidgetItem] = {}
        self._nav_category_members: Dict[str, List[QtWidgets.QListWidgetItem]] = {}
        self._nav_category_collapsed: Dict[str, bool] = dict(self.cfg.get("ui", {}).get("nav_collapsed", {}))

        context_placeholder = QtWidgets.QWidget()
        placeholder_layout = QtWidgets.QVBoxLayout(context_placeholder)
        placeholder_layout.setContentsMargins(24, 24, 24, 24)
        placeholder_layout.addStretch(1)
        self._context_default_idx = self.context_stack.addWidget(context_placeholder)
        self._context_index: Dict[str, int] = {}

        def add_section(
            key: str,
            title: str,
            widget: QtWidgets.QWidget,
            *,
            icon: Optional[QtGui.QIcon] = None,
            scrollable: bool = False,
            category: str = "Приложение",
            description: str = "",
        ) -> QtWidgets.QWidget:
            container = widget
            if scrollable:
                area = QtWidgets.QScrollArea()
                area.setWidgetResizable(True)
                area.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
                area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
                area.setWidget(widget)
                container = area
            surface = QtWidgets.QFrame()
            surface.setObjectName("sectionSurface")
            surface_layout = QtWidgets.QVBoxLayout(surface)
            surface_layout.setContentsMargins(0, 0, 0, 0)
            surface_layout.setSpacing(0)
            surface_layout.addWidget(container)
            idx = self.section_stack.addWidget(surface)
            self._section_index[key] = idx
            self._section_order.append(key)
            if category not in self._nav_group_rows:
                group_item = QtWidgets.QListWidgetItem(category.upper())
                group_item.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)
                font = QtGui.QFont(self.section_nav.font())
                font.setBold(True)
                font.setPointSize(max(font.pointSize() - 1, 9))
                group_item.setFont(font)
                group_item.setForeground(QtGui.QColor("#64748b"))
                group_item.setBackground(QtGui.QColor("#16233f"))
                group_item.setSizeHint(QtCore.QSize(220, 30))
                group_item.setData(QtCore.Qt.ItemDataRole.UserRole, None)
                group_item.setData(QtCore.Qt.ItemDataRole.UserRole + 1, "header")
                group_item.setData(QtCore.Qt.ItemDataRole.UserRole + 2, category)
                self.section_nav.addItem(group_item)
                self._nav_group_rows[category] = group_item
                self._nav_category_members.setdefault(category, [])
                if self._nav_category_collapsed.get(category):
                    group_item.setText(f"▶ {category.upper()}")
                else:
                    group_item.setText(f"▼ {category.upper()}")
            item = QtWidgets.QListWidgetItem(icon, title) if icon else QtWidgets.QListWidgetItem(title)
            item.setData(QtCore.Qt.ItemDataRole.UserRole, key)
            item.setData(QtCore.Qt.ItemDataRole.UserRole + 1, category)
            item.setSizeHint(QtCore.QSize(220, 48))
            if description:
                item.setToolTip(description)
            self.section_nav.addItem(item)
            self._section_nav_items[key] = item
            self._section_meta[key] = {
                "title": title,
                "category": category,
                "description": description,
            }
            self._nav_category_members.setdefault(category, []).append(item)
            if self._nav_category_collapsed.get(category):
                item.setHidden(True)
            self._register_command(
                f"section:{key}",
                f"Раздел — {title}",
                lambda k=key: self._focus_section_from_command(k),
                category="Навигация",
                subtitle=description or f"Перейти к разделу «{title}»",
                keywords=[title, category],
            )
            return container

        def make_scroll_tab(margins=(12, 12, 12, 12), spacing=10):
            area = QtWidgets.QScrollArea()
            area.setObjectName("sectionScrollArea")
            area.setWidgetResizable(True)
            area.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
            area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            area.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            body_widget = QtWidgets.QWidget()
            body_widget.setObjectName("sectionScrollWidget")
            layout = QtWidgets.QVBoxLayout(body_widget)
            layout.setContentsMargins(*margins)
            layout.setSpacing(spacing)
            area.setWidget(body_widget)
            return area, layout

        def register_context(key: str, widget: QtWidgets.QWidget) -> None:
            idx = self.context_stack.addWidget(widget)
            self._context_index[key] = idx

        def make_context_card(title: str, subtitle: str = "") -> Tuple[QtWidgets.QWidget, QtWidgets.QVBoxLayout]:
            card = QtWidgets.QFrame()
            card.setObjectName("contextCard")
            layout = QtWidgets.QVBoxLayout(card)
            layout.setContentsMargins(18, 18, 18, 18)
            layout.setSpacing(12)
            lbl_title = QtWidgets.QLabel(title)
            lbl_title.setObjectName("contextTitle")
            layout.addWidget(lbl_title)
            if subtitle:
                lbl_sub = QtWidgets.QLabel(subtitle)
                lbl_sub.setObjectName("contextSubtitle")
                lbl_sub.setWordWrap(True)
                layout.addWidget(lbl_sub)
            return card, layout


        overview_root = QtWidgets.QWidget()
        overview_layout = QtWidgets.QVBoxLayout(overview_root)
        overview_layout.setContentsMargins(12, 12, 12, 12)
        overview_layout.setSpacing(18)

        hero = QtWidgets.QFrame()
        hero.setObjectName("overviewHero")
        hero_layout = QtWidgets.QVBoxLayout(hero)
        hero_layout.setContentsMargins(18, 18, 18, 18)
        hero_layout.setSpacing(6)
        lbl_dash_title = QtWidgets.QLabel("Sora Suite — рабочая студия")
        lbl_dash_title.setObjectName("dashboardTitle")
        lbl_dash_sub = QtWidgets.QLabel(
            "Открой приложение, выбери нужные процессы и управляй всем циклом — от генерации и скачки до очистки и публикации."
        )
        lbl_dash_sub.setObjectName("dashboardSubtitle")
        lbl_dash_sub.setWordWrap(True)
        hero_layout.addWidget(lbl_dash_title)
        hero_layout.addWidget(lbl_dash_sub)
        overview_layout.addWidget(hero)

        info_cards = QtWidgets.QFrame()
        info_cards.setObjectName("overviewInfo")
        info_layout = QtWidgets.QHBoxLayout(info_cards)
        info_layout.setContentsMargins(16, 12, 16, 12)
        info_layout.setSpacing(12)
        for emoji, title, text_body in [
            ("🗂️", "Рабочие пространства", "Параллельные сессии Chrome со своими портами, промптами и логами."),
            ("⚙️", "Автоматизация", "Собирай цепочки действий, скачки и вставки промптов в едином потоке."),
            ("🔔", "Уведомления", "Получай статус выполнения в командной панели и в системных уведомлениях."),
        ]:
            card = QtWidgets.QFrame()
            card.setObjectName("overviewInfoCard")
            card_layout = QtWidgets.QVBoxLayout(card)
            card_layout.setContentsMargins(16, 12, 16, 12)
            card_layout.setSpacing(4)
            lbl_icon = QtWidgets.QLabel(emoji)
            lbl_icon.setStyleSheet("font-size:18px")
            lbl_text = QtWidgets.QLabel(title)
            lbl_text.setStyleSheet("font-weight:600;color:#e2e8f0;")
            lbl_desc = QtWidgets.QLabel(text_body)
            lbl_desc.setStyleSheet("color:#94a3b8;")
            lbl_desc.setWordWrap(True)
            card_layout.addWidget(lbl_icon)
            card_layout.addWidget(lbl_text)
            card_layout.addWidget(lbl_desc)
            card_layout.addStretch(1)
            info_layout.addWidget(card, 1)
        overview_layout.addWidget(info_cards)

        stats_panel = QtWidgets.QFrame()
        stats_panel.setObjectName("dashboardStats")
        stats_layout = QtWidgets.QVBoxLayout(stats_panel)
        stats_layout.setContentsMargins(16, 16, 16, 16)
        stats_layout.setSpacing(12)
        lbl_stats_title = QtWidgets.QLabel("Мониторинг папок")
        lbl_stats_title.setObjectName("dashboardSectionTitle")
        stats_layout.addWidget(lbl_stats_title)
        stats_grid = QtWidgets.QGridLayout()
        stats_grid.setHorizontalSpacing(16)
        stats_grid.setVerticalSpacing(12)
        stats_layout.addLayout(stats_grid)
        self._dashboard_stat_values = {}
        self._dashboard_stat_desc = {}

        def add_dashboard_card(row: int, col: int, key: str, title: str, tooltip: str, accent: str):
            card = QtWidgets.QFrame()
            card.setObjectName(f"dashStat_{key}")
            card.setStyleSheet(
                (
                    "QFrame#dashStat_{key}{background:rgba(15,23,42,0.92);border-radius:16px;"
                    "border:1px solid rgba(148,163,184,0.22);}"                     "QLabel#dashStatTitle_{key}{color:#cbd5f5;font-size:11px;text-transform:uppercase;letter-spacing:0.6px;}"                     "QLabel#dashStatDesc_{key}{color:#94a3b8;font-size:11px;}"
                ).replace("{key}", key)
            )
            layout = QtWidgets.QVBoxLayout(card)
            layout.setContentsMargins(16, 14, 16, 14)
            layout.setSpacing(6)
            accent_bar = QtWidgets.QFrame()
            accent_bar.setFixedHeight(4)
            accent_bar.setStyleSheet(f"QFrame{{background:{accent};border-radius:2px;}}")
            layout.addWidget(accent_bar)
            title_lbl = QtWidgets.QLabel(title)
            title_lbl.setObjectName(f"dashStatTitle_{key}")
            value_lbl = QtWidgets.QLabel("0")
            value_lbl.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            value_lbl.setStyleSheet(
                f"QLabel{{font:700 26px 'JetBrains Mono','Menlo','Consolas';color:{accent};padding-top:4px;}}"
            )
            desc_lbl = QtWidgets.QLabel("—")
            desc_lbl.setObjectName(f"dashStatDesc_{key}")
            desc_lbl.setWordWrap(True)
            desc_lbl.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(title_lbl, 0, QtCore.Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(value_lbl)
            layout.addWidget(desc_lbl)
            card.setToolTip(tooltip)
            stats_grid.addWidget(card, row, col)
            self._dashboard_stat_values[key] = value_lbl
            self._dashboard_stat_desc[key] = desc_lbl

        add_dashboard_card(0, 0, "raw", "RAW", "Количество файлов в каталоге RAW", "#38bdf8")
        add_dashboard_card(0, 1, "blur", "BLURRED", "Готовые для блюра клипы", "#a855f7")
        add_dashboard_card(0, 2, "merge", "MERGED", "Склеенные ролики", "#f97316")
        add_dashboard_card(1, 0, "youtube", "YOUTUBE", "Очередь загрузки YouTube", "#4ade80")
        add_dashboard_card(1, 1, "tiktok", "TIKTOK", "Очередь загрузки TikTok", "#f472b6")
        add_dashboard_card(1, 2, "images", "IMAGES", "Сгенерированные изображения", "#60a5fa")
        overview_layout.addWidget(stats_panel)

        quick_links = QtWidgets.QFrame()
        quick_links.setObjectName("overviewLinks")
        link_layout = QtWidgets.QHBoxLayout(quick_links)
        link_layout.setContentsMargins(16, 12, 16, 12)
        link_layout.setSpacing(10)
        btn_to_workflow = QtWidgets.QPushButton("🧠 К пайплайну")
        btn_to_workflow.clicked.connect(lambda: self._select_section("pipeline"))
        btn_to_automator = QtWidgets.QPushButton("🤖 Открыть автоматизатор")
        btn_to_automator.clicked.connect(lambda: self._select_section("automator"))
        btn_to_logs = QtWidgets.QPushButton("📜 Журналы")
        btn_to_logs.clicked.connect(lambda: self._select_section("logs"))
        for btn in (btn_to_workflow, btn_to_automator, btn_to_logs):
            btn.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
            link_layout.addWidget(btn)
        link_layout.addStretch(1)
        overview_layout.addWidget(quick_links)

        overview_context, overview_ctx_layout = make_context_card(
            "Главная", "Краткое описание возможностей и быстрые ссылки на ключевые разделы."
        )
        overview_ctx_layout.addWidget(QtWidgets.QLabel("Используй рабочие пространства и пайплайн, чтобы запускать процессы батчами."))
        overview_ctx_layout.addStretch(1)
        register_context("overview", overview_context)

        add_section(
            "overview",
            "🏠 Главная",
            overview_root,
            scrollable=True,
            category="Главная",
            description="Стартовая панель, статистика и краткие ссылки на основные разделы",
        )

        self.tab_sessions = self._build_sessions_tab()
        sessions_context, sessions_ctx_layout = make_context_card(
            "Рабочие пространства",
            "Выбирай сессию, управляй портами и быстрыми действиями."
        )
        sessions_form = QtWidgets.QFormLayout()
        sessions_form.setHorizontalSpacing(8)
        sessions_form.setVerticalSpacing(6)
        self.lbl_context_session_name = QtWidgets.QLabel("—")
        self.lbl_context_session_profiles = QtWidgets.QLabel("—")
        self.lbl_context_session_profiles.setWordWrap(True)
        self.lbl_context_session_status = QtWidgets.QLabel("—")
        self.lbl_context_session_status.setWordWrap(True)
        sessions_form.addRow("Активная:", self.lbl_context_session_name)
        sessions_form.addRow("Ресурсы:", self.lbl_context_session_profiles)
        sessions_form.addRow("Статус:", self.lbl_context_session_status)
        sessions_ctx_layout.addLayout(sessions_form)
        sessions_buttons = QtWidgets.QHBoxLayout()
        sessions_buttons.setSpacing(6)
        self.btn_context_session_window = QtWidgets.QPushButton("🗔 Окно")
        self.btn_context_session_prompts = QtWidgets.QPushButton("✍️ Промпты")
        self.btn_context_session_images = QtWidgets.QPushButton("🖼️ Картинки")
        self.btn_context_session_download = QtWidgets.QPushButton("⬇️ Скачка")
        self.btn_context_session_watermark = QtWidgets.QPushButton("🧼 Очистка")
        self.btn_context_session_probe = QtWidgets.QPushButton("🧐 Проверка ВЗ")
        for btn in (
            self.btn_context_session_window,
            self.btn_context_session_prompts,
            self.btn_context_session_images,
            self.btn_context_session_download,
            self.btn_context_session_watermark,
            self.btn_context_session_probe,
        ):
            btn.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
            sessions_buttons.addWidget(btn)
        sessions_buttons.addStretch(1)
        sessions_ctx_layout.addLayout(sessions_buttons)
        sessions_ctx_layout.addStretch(1)
        register_context("sessions", sessions_context)
        add_section(
            "sessions",
            "🗂️ Рабочие пространства",
            self.tab_sessions,
            scrollable=True,
            category="Рабочие процессы",
            description="Настройки отдельных Chrome-сессий и связанных файлов",
        )

        pipeline_context, pipeline_ctx_layout = make_context_card(
            "Пайплайн",
            "Собери нужные этапы и контролируй лимиты перед запуском."
        )
        self.lbl_context_pipeline_profile = QtWidgets.QLabel("—")
        self.lbl_context_pipeline_profile.setWordWrap(True)
        self.lbl_context_pipeline_steps = QtWidgets.QLabel("—")
        self.lbl_context_pipeline_steps.setWordWrap(True)
        self.lbl_context_pipeline_limits = QtWidgets.QLabel("—")
        self.lbl_context_pipeline_limits.setWordWrap(True)
        pipeline_form = QtWidgets.QFormLayout()
        pipeline_form.setHorizontalSpacing(8)
        pipeline_form.setVerticalSpacing(6)
        pipeline_form.addRow("Chrome:", self.lbl_context_pipeline_profile)
        pipeline_form.addRow("Этапы:", self.lbl_context_pipeline_steps)
        pipeline_form.addRow("Лимиты:", self.lbl_context_pipeline_limits)
        pipeline_ctx_layout.addLayout(pipeline_form)
        pipeline_ctx_layout.addStretch(1)
        register_context("pipeline", pipeline_context)
        pipeline_root = self._build_pipeline_page()
        add_section(
            "pipeline",
            "🧠 Пайплайн",
            pipeline_root,
            scrollable=True,
            category="Рабочие процессы",
            description="Настройка последовательности действий и лимитов",
        )

        automator_root = self._build_automator_page()
        automator_context, automator_ctx_layout = make_context_card(
            "Автоматизация",
            "Сохраняй готовые последовательности шагов для повторного запуска."
        )
        automator_ctx_layout.addWidget(QtWidgets.QLabel("Собери цепочки под разные проекты и запускай их когда угодно."))
        automator_ctx_layout.addStretch(1)
        register_context("automator", automator_context)
        add_section(
            "automator",
            "🤖 Автоматизатор",
            automator_root,
            scrollable=True,
            category="Рабочие процессы",
            description="Конструктор цепочек действий по сессиям",
        )

        logs_root = self._build_global_log_page()
        logs_context, logs_ctx_layout = make_context_card(
            "Журнал",
            "Просматривай историю операций и очищай её при необходимости."
        )
        logs_ctx_layout.addWidget(QtWidgets.QLabel("Журнал синхронизируется с панелью статусов и уведомлениями."))
        logs_ctx_layout.addStretch(1)
        register_context("logs", logs_context)
        add_section(
            "logs",
            "📜 Журнал процессов",
            logs_root,
            scrollable=True,
            category="Мониторинг",
            description="Глобальные логи и состояние текущих задач",
        )

        session_logs_root = self._build_session_log_page()
        session_logs_context, session_logs_ctx_layout = make_context_card(
            "Логи сессий",
            "Анализируй индивидуальные журналы Chrome-сессий и проверяй ошибки."
        )
        session_logs_ctx_layout.addWidget(QtWidgets.QLabel("Доступ к submitted/failed логам и лентам скачки по каждой сессии."))
        session_logs_ctx_layout.addStretch(1)
        register_context("session_logs", session_logs_context)
        add_section(
            "session_logs",
            "🗒️ Логи сессий",
            session_logs_root,
            scrollable=True,
            category="Мониторинг",
            description="Подробные журналы по каждому рабочему пространству",
        )
        wm_cfg = self.cfg.get("watermark_cleaner", {}) or {}
        self.tab_watermark, wm_layout = make_scroll_tab()
        wm_intro = QtWidgets.QLabel(
            "Новый модуль восстанавливает кадры вместо блюра: шаблон водяного знака ищется на каждом кадре,"
            " подбираются чистые фрагменты и бесшовно подставляются вместо логотипа."
        )
        wm_intro.setWordWrap(True)
        wm_intro.setStyleSheet("QLabel{color:#94a3b8;font-size:11px;}")
        wm_layout.addWidget(wm_intro)

        def make_path_field() -> Tuple[QtWidgets.QLineEdit, QtWidgets.QToolButton, QtWidgets.QWidget]:
            container = QtWidgets.QWidget()
            hl = QtWidgets.QHBoxLayout(container)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(6)
            line = QtWidgets.QLineEdit()
            line.setClearButtonEnabled(True)
            btn = QtWidgets.QToolButton()
            btn.setText("…")
            btn.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
            hl.addWidget(line, 1)
            hl.addWidget(btn)
            return line, btn, container

        grp_io = QtWidgets.QGroupBox("Папки и шаблон")
        io_form = QtWidgets.QFormLayout(grp_io)
        io_form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        io_form.setHorizontalSpacing(8)
        io_form.setVerticalSpacing(8)
        self.ed_wmr_source, self.btn_wmr_source_browse, wmr_source_widget = make_path_field()
        self.ed_wmr_source.setText(wm_cfg.get("source_dir", self.cfg.get("downloads_dir", str(DL_DIR))))
        io_form.addRow("Исходники (RAW):", wmr_source_widget)
        self.ed_wmr_output, self.btn_wmr_output_browse, wmr_output_widget = make_path_field()
        self.ed_wmr_output.setText(wm_cfg.get("output_dir", str(PROJECT_ROOT / "restored")))
        io_form.addRow("Готовые клипы:", wmr_output_widget)
        self.ed_wmr_template, self.btn_wmr_template_browse, wmr_template_widget = make_path_field()
        self.ed_wmr_template.setText(wm_cfg.get("template", str(PROJECT_ROOT / "watermark.png")))
        io_form.addRow("Шаблон водяного знака:", wmr_template_widget)
        self.sb_wmr_mask_threshold = QtWidgets.QSpinBox()
        self.sb_wmr_mask_threshold.setRange(0, 255)
        self.sb_wmr_mask_threshold.setValue(int(wm_cfg.get("mask_threshold", 8) or 0))
        io_form.addRow("Порог маски (alpha):", self.sb_wmr_mask_threshold)
        wm_layout.addWidget(grp_io)

        grp_detect = QtWidgets.QGroupBox("Детекция логотипа")
        detect_form = QtWidgets.QGridLayout(grp_detect)
        detect_form.setHorizontalSpacing(8)
        detect_form.setVerticalSpacing(8)
        row = 0
        detect_form.addWidget(QtWidgets.QLabel("Порог совпадения:"), row, 0)
        self.dsb_wmr_threshold = QtWidgets.QDoubleSpinBox()
        self.dsb_wmr_threshold.setRange(0.0, 1.0)
        self.dsb_wmr_threshold.setDecimals(3)
        self.dsb_wmr_threshold.setSingleStep(0.01)
        self.dsb_wmr_threshold.setValue(float(wm_cfg.get("threshold", 0.78) or 0.78))
        detect_form.addWidget(self.dsb_wmr_threshold, row, 1)
        detect_form.addWidget(QtWidgets.QLabel("Кадров для анализа:"), row, 2)
        self.sb_wmr_frames = QtWidgets.QSpinBox()
        self.sb_wmr_frames.setRange(1, 5000)
        self.sb_wmr_frames.setValue(int(wm_cfg.get("frames", 120) or 120))
        detect_form.addWidget(self.sb_wmr_frames, row, 3)
        row += 1
        detect_form.addWidget(QtWidgets.QLabel("Макс. ширина кадра:"), row, 0)
        self.sb_wmr_downscale = QtWidgets.QSpinBox()
        self.sb_wmr_downscale.setRange(0, 4096)
        self.sb_wmr_downscale.setSuffix(" px")
        self.sb_wmr_downscale.setSpecialValueText("без изменений")
        self.sb_wmr_downscale.setValue(int(wm_cfg.get("downscale", 1080) or 0))
        detect_form.addWidget(self.sb_wmr_downscale, row, 1)
        detect_form.addWidget(QtWidgets.QLabel("Масштаб min/max:"), row, 2)
        scale_box = QtWidgets.QHBoxLayout()
        self.dsb_wmr_scale_min = QtWidgets.QDoubleSpinBox()
        self.dsb_wmr_scale_min.setRange(0.05, 3.0)
        self.dsb_wmr_scale_min.setDecimals(2)
        self.dsb_wmr_scale_min.setSingleStep(0.05)
        self.dsb_wmr_scale_min.setValue(float(wm_cfg.get("scale_min", 0.85) or 0.85))
        self.dsb_wmr_scale_max = QtWidgets.QDoubleSpinBox()
        self.dsb_wmr_scale_max.setRange(0.1, 4.0)
        self.dsb_wmr_scale_max.setDecimals(2)
        self.dsb_wmr_scale_max.setSingleStep(0.05)
        self.dsb_wmr_scale_max.setValue(float(wm_cfg.get("scale_max", 1.2) or 1.2))
        scale_box.addWidget(self.dsb_wmr_scale_min)
        scale_box.addWidget(QtWidgets.QLabel("→"))
        scale_box.addWidget(self.dsb_wmr_scale_max)
        detect_form.addLayout(scale_box, row, 3)
        row += 1
        detect_form.addWidget(QtWidgets.QLabel("Шагов масштаба:"), row, 0)
        self.sb_wmr_scale_steps = QtWidgets.QSpinBox()
        self.sb_wmr_scale_steps.setRange(3, 25)
        self.sb_wmr_scale_steps.setValue(int(wm_cfg.get("scale_steps", 9) or 9))
        detect_form.addWidget(self.sb_wmr_scale_steps, row, 1)
        self.cb_wmr_full_scan = QtWidgets.QCheckBox("Проверять каждый кадр (медленнее, но точнее)")
        self.cb_wmr_full_scan.setChecked(bool(wm_cfg.get("full_scan", False)))
        detect_form.addWidget(self.cb_wmr_full_scan, row, 2, 1, 2)
        wm_layout.addWidget(grp_detect)

        grp_replace = QtWidgets.QGroupBox("Замена")
        replace_form = QtWidgets.QGridLayout(grp_replace)
        replace_form.setHorizontalSpacing(8)
        replace_form.setVerticalSpacing(8)
        r = 0
        replace_form.addWidget(QtWidgets.QLabel("Запас по краям (px):"), r, 0)
        self.sb_wmr_padding_px = QtWidgets.QSpinBox()
        self.sb_wmr_padding_px.setRange(0, 512)
        self.sb_wmr_padding_px.setValue(int(wm_cfg.get("padding_px", 12) or 0))
        replace_form.addWidget(self.sb_wmr_padding_px, r, 1)
        replace_form.addWidget(QtWidgets.QLabel("Доп. ширина (%):"), r, 2)
        self.dsb_wmr_padding_pct = QtWidgets.QDoubleSpinBox()
        self.dsb_wmr_padding_pct.setRange(0.0, 100.0)
        self.dsb_wmr_padding_pct.setDecimals(1)
        self.dsb_wmr_padding_pct.setSingleStep(0.5)
        self.dsb_wmr_padding_pct.setSuffix(" %")
        self.dsb_wmr_padding_pct.setValue(float(wm_cfg.get("padding_pct", 0.18) or 0.0) * 100.0)
        replace_form.addWidget(self.dsb_wmr_padding_pct, r, 3)
        r += 1
        replace_form.addWidget(QtWidgets.QLabel("Мин. размер зоны:"), r, 0)
        self.sb_wmr_min_size = QtWidgets.QSpinBox()
        self.sb_wmr_min_size.setRange(2, 1920)
        self.sb_wmr_min_size.setValue(int(wm_cfg.get("min_size", 32) or 32))
        replace_form.addWidget(self.sb_wmr_min_size, r, 1)
        replace_form.addWidget(QtWidgets.QLabel("Радиус поиска кадров:"), r, 2)
        self.sb_wmr_search_span = QtWidgets.QSpinBox()
        self.sb_wmr_search_span.setRange(1, 240)
        self.sb_wmr_search_span.setValue(int(wm_cfg.get("search_span", 12) or 12))
        replace_form.addWidget(self.sb_wmr_search_span, r, 3)
        r += 1
        replace_form.addWidget(QtWidgets.QLabel("Сколько кадров в смеси:"), r, 0)
        self.sb_wmr_pool = QtWidgets.QSpinBox()
        self.sb_wmr_pool.setRange(1, 12)
        self.sb_wmr_pool.setValue(int(wm_cfg.get("pool", 4) or 4))
        replace_form.addWidget(self.sb_wmr_pool, r, 1)
        replace_form.addWidget(QtWidgets.QLabel("Макс. пересечение (IoU):"), r, 2)
        self.dsb_wmr_max_iou = QtWidgets.QDoubleSpinBox()
        self.dsb_wmr_max_iou.setRange(0.0, 1.0)
        self.dsb_wmr_max_iou.setDecimals(2)
        self.dsb_wmr_max_iou.setSingleStep(0.05)
        self.dsb_wmr_max_iou.setValue(float(wm_cfg.get("max_iou", 0.25) or 0.25))
        replace_form.addWidget(self.dsb_wmr_max_iou, r, 3)
        r += 1
        replace_form.addWidget(QtWidgets.QLabel("Режим смешивания:"), r, 0)
        self.cmb_wmr_blend = QtWidgets.QComboBox()
        self.cmb_wmr_blend.addItems(["normal", "mixed"])
        self.cmb_wmr_blend.setCurrentText(str(wm_cfg.get("blend", "normal") or "normal"))
        replace_form.addWidget(self.cmb_wmr_blend, r, 1)
        replace_form.addWidget(QtWidgets.QLabel("Радиус inpaint:"), r, 2)
        self.sb_wmr_inpaint_radius = QtWidgets.QSpinBox()
        self.sb_wmr_inpaint_radius.setRange(1, 64)
        self.sb_wmr_inpaint_radius.setValue(int(wm_cfg.get("inpaint_radius", 6) or 6))
        replace_form.addWidget(self.sb_wmr_inpaint_radius, r, 3)
        r += 1
        replace_form.addWidget(QtWidgets.QLabel("Резервный метод:"), r, 0)
        self.cmb_wmr_inpaint_method = QtWidgets.QComboBox()
        self.cmb_wmr_inpaint_method.addItems(["telea", "ns"])
        self.cmb_wmr_inpaint_method.setCurrentText(str(wm_cfg.get("inpaint_method", "telea") or "telea"))
        replace_form.addWidget(self.cmb_wmr_inpaint_method, r, 1)
        replace_form.addItem(QtWidgets.QSpacerItem(20, 20, QtWidgets.QSizePolicy.Policy.Minimum, QtWidgets.QSizePolicy.Policy.Minimum), r, 2, 1, 2)
        wm_layout.addWidget(grp_replace)

        wm_layout.addStretch(1)

        wm_context, wm_ctx_layout = make_context_card(
            "Замена водяного знака",
            "Следи за активной папкой и шаблоном, чтобы не запустить обработку не того проекта.",
        )
        self.lbl_context_wmr_source = QtWidgets.QLabel("—")
        self.lbl_context_wmr_output = QtWidgets.QLabel("—")
        self.lbl_context_wmr_template = QtWidgets.QLabel("—")
        wm_form = QtWidgets.QFormLayout()
        wm_form.setHorizontalSpacing(8)
        wm_form.setVerticalSpacing(6)
        wm_form.addRow("RAW:", self.lbl_context_wmr_source)
        wm_form.addRow("Output:", self.lbl_context_wmr_output)
        wm_form.addRow("Шаблон:", self.lbl_context_wmr_template)
        wm_ctx_layout.addLayout(wm_form)
        wm_ctx_layout.addStretch(1)
        register_context("watermark", wm_context)

        add_section(
            "watermark",
            "🧼 Водяной знак",
            self.tab_watermark,
            scrollable=True,
            category="Водяные знаки",
            description="Замена логотипа подбором чистых фрагментов видео",
        )

        wm_probe_cfg = self.cfg.get("watermark_probe", {}) or {}
        self.tab_watermark_probe, wm_probe_layout = make_scroll_tab()
        wm_probe_intro = QtWidgets.QLabel(
            "Проверяй, вспыхивает ли логотип в выбранной зоне, и автоматически отражай ролик при нужном условии."
        )
        wm_probe_intro.setWordWrap(True)
        wm_probe_intro.setStyleSheet("QLabel{color:#94a3b8;font-size:11px;}")
        wm_probe_layout.addWidget(wm_probe_intro)

        probe_files = QtWidgets.QGroupBox("Файлы")
        pf_form = QtWidgets.QFormLayout(probe_files)
        pf_form.setHorizontalSpacing(8)
        pf_form.setVerticalSpacing(8)
        self.ed_probe_source, self.btn_probe_source_browse, probe_src_widget = make_path_field()
        self.ed_probe_source.setText(wm_probe_cfg.get("source_dir", self.cfg.get("downloads_dir", str(DL_DIR))))
        pf_form.addRow("RAW папка:", probe_src_widget)
        self.ed_probe_output_dir, self.btn_probe_output_browse, probe_out_widget = make_path_field()
        self.ed_probe_output_dir.setText(wm_probe_cfg.get("output_dir", str(PROJECT_ROOT / "restored")))
        pf_form.addRow("Папка результата:", probe_out_widget)
        self.ed_probe_video, self.btn_probe_video_browse, probe_video_widget = make_path_field()
        self.ed_probe_video.setPlaceholderText("Выбери конкретный файл или оставь пустым, чтобы указать позже")
        pf_form.addRow("Видео:", probe_video_widget)
        wm_probe_layout.addWidget(probe_files)

        probe_zone = QtWidgets.QGroupBox("Зона проверки")
        pz_grid = QtWidgets.QGridLayout(probe_zone)
        pz_grid.setHorizontalSpacing(8)
        pz_grid.setVerticalSpacing(8)
        self.sb_probe_x = QtWidgets.QSpinBox(); self.sb_probe_x.setRange(0, 10000)
        self.sb_probe_y = QtWidgets.QSpinBox(); self.sb_probe_y.setRange(0, 10000)
        self.sb_probe_w = QtWidgets.QSpinBox(); self.sb_probe_w.setRange(1, 20000)
        self.sb_probe_h = QtWidgets.QSpinBox(); self.sb_probe_h.setRange(1, 20000)
        region = wm_probe_cfg.get("region", {}) or {}
        self.sb_probe_x.setValue(int(region.get("x", 0)))
        self.sb_probe_y.setValue(int(region.get("y", 0)))
        self.sb_probe_w.setValue(int(region.get("w", 320)))
        self.sb_probe_h.setValue(int(region.get("h", 120)))
        pz_grid.addWidget(QtWidgets.QLabel("x"), 0, 0); pz_grid.addWidget(self.sb_probe_x, 0, 1)
        pz_grid.addWidget(QtWidgets.QLabel("y"), 0, 2); pz_grid.addWidget(self.sb_probe_y, 0, 3)
        pz_grid.addWidget(QtWidgets.QLabel("w"), 1, 0); pz_grid.addWidget(self.sb_probe_w, 1, 1)
        pz_grid.addWidget(QtWidgets.QLabel("h"), 1, 2); pz_grid.addWidget(self.sb_probe_h, 1, 3)
        self.btn_probe_preview = QtWidgets.QPushButton("Предпросмотр и оверлей")
        pz_grid.addWidget(self.btn_probe_preview, 2, 0, 1, 4)
        wm_probe_layout.addWidget(probe_zone)

        probe_opts = QtWidgets.QGroupBox("Параметры")
        po_form = QtWidgets.QGridLayout(probe_opts)
        po_form.setHorizontalSpacing(8)
        po_form.setVerticalSpacing(8)
        self.sb_probe_frames = QtWidgets.QSpinBox(); self.sb_probe_frames.setRange(1, 5000)
        self.sb_probe_frames.setValue(int(wm_probe_cfg.get("frames", 120) or 120))
        self.sb_probe_brightness = QtWidgets.QSpinBox(); self.sb_probe_brightness.setRange(1, 255)
        self.sb_probe_brightness.setValue(int(wm_probe_cfg.get("brightness_threshold", 245) or 245))
        self.dsb_probe_coverage = QtWidgets.QDoubleSpinBox(); self.dsb_probe_coverage.setRange(0.0005, 1.0)
        self.dsb_probe_coverage.setDecimals(4)
        self.dsb_probe_coverage.setSingleStep(0.001)
        self.dsb_probe_coverage.setValue(float(wm_probe_cfg.get("coverage_ratio", 0.002) or 0.002))
        self.dsb_probe_edge_ratio = QtWidgets.QDoubleSpinBox(); self.dsb_probe_edge_ratio.setRange(0.0, 1.0)
        self.dsb_probe_edge_ratio.setDecimals(4)
        self.dsb_probe_edge_ratio.setSingleStep(0.001)
        self.dsb_probe_edge_ratio.setValue(float(wm_probe_cfg.get("edge_ratio", 0.006) or 0.006))
        self.dsb_probe_downscale = QtWidgets.QDoubleSpinBox(); self.dsb_probe_downscale.setRange(0.0, 8.0)
        self.dsb_probe_downscale.setDecimals(1)
        self.dsb_probe_downscale.setSingleStep(0.5)
        self.dsb_probe_downscale.setValue(float(wm_probe_cfg.get("downscale", 2.0) or 2.0))
        self.sb_probe_hits = QtWidgets.QSpinBox(); self.sb_probe_hits.setRange(1, 20)
        self.sb_probe_hits.setValue(int(wm_probe_cfg.get("min_hits", 1) or 1))
        self.cmb_probe_method = QtWidgets.QComboBox()
        self.cmb_probe_method.addItem("Гибрид: вспышка или границы", "hybrid")
        self.cmb_probe_method.addItem("Только вспышка", "flash")
        self.cmb_probe_method.addItem("Только границы", "edges")
        current_method = wm_probe_cfg.get("method", "hybrid")
        idx_method = max(0, self.cmb_probe_method.findData(current_method))
        self.cmb_probe_method.setCurrentIndex(idx_method)
        self.cmb_probe_flip_when = QtWidgets.QComboBox()
        self.cmb_probe_flip_when.addItem("Флипать, если знака НЕТ", "missing")
        self.cmb_probe_flip_when.addItem("Флипать, если знак ЕСТЬ", "present")
        current_flip_when = wm_probe_cfg.get("flip_when", "missing")
        idx_flip_when = max(0, self.cmb_probe_flip_when.findData(current_flip_when))
        self.cmb_probe_flip_when.setCurrentIndex(idx_flip_when)
        self.cmb_probe_direction = QtWidgets.QComboBox()
        self.cmb_probe_direction.addItem("Влево (горизонтальный flip)", "left")
        self.cmb_probe_direction.addItem("Вправо (вертикальный flip)", "right")
        idx_dir = max(0, self.cmb_probe_direction.findData(wm_probe_cfg.get("flip_direction", "left")))
        self.cmb_probe_direction.setCurrentIndex(idx_dir)
        po_form.addWidget(QtWidgets.QLabel("Кадров для проверки:"), 0, 0)
        po_form.addWidget(self.sb_probe_frames, 0, 1)
        po_form.addWidget(QtWidgets.QLabel("Метод поиска:"), 0, 2)
        po_form.addWidget(self.cmb_probe_method, 0, 3)
        po_form.addWidget(QtWidgets.QLabel("Порог яркости:"), 1, 0)
        po_form.addWidget(self.sb_probe_brightness, 1, 1)
        po_form.addWidget(QtWidgets.QLabel("Доля заполнения:"), 1, 2)
        po_form.addWidget(self.dsb_probe_coverage, 1, 3)
        po_form.addWidget(QtWidgets.QLabel("Границы (доля):"), 2, 0)
        po_form.addWidget(self.dsb_probe_edge_ratio, 2, 1)
        po_form.addWidget(QtWidgets.QLabel("Минимум срабатываний:"), 2, 2)
        po_form.addWidget(self.sb_probe_hits, 2, 3)
        po_form.addWidget(QtWidgets.QLabel("Даунскейл зоны:"), 3, 0)
        po_form.addWidget(self.dsb_probe_downscale, 3, 1)
        po_form.addWidget(QtWidgets.QLabel("Условие флипа:"), 3, 2)
        po_form.addWidget(self.cmb_probe_flip_when, 3, 3)
        po_form.addWidget(QtWidgets.QLabel("Направление flip:"), 4, 0)
        po_form.addWidget(self.cmb_probe_direction, 4, 1, 1, 3)
        wm_probe_layout.addWidget(probe_opts)

        probe_actions = QtWidgets.QGroupBox("Действия")
        pa_grid = QtWidgets.QGridLayout(probe_actions)
        pa_grid.setHorizontalSpacing(8)
        pa_grid.setVerticalSpacing(8)
        self.btn_probe_scan = QtWidgets.QPushButton("Проверить водяной знак")
        self.btn_probe_flip = QtWidgets.QPushButton("Проверить и флипнуть")
        self.btn_probe_batch_scan = QtWidgets.QPushButton("Папка: проверить")
        self.btn_probe_batch_flip = QtWidgets.QPushButton("Папка: проверить и флипнуть")
        self.cb_probe_autosave = QtWidgets.QCheckBox("Сохранять результат в папку")
        self.cb_probe_autosave.setChecked(True)
        self.lbl_probe_status = QtWidgets.QLabel("—")
        self.lbl_probe_status.setWordWrap(True)
        pa_grid.addWidget(self.btn_probe_scan, 0, 0)
        pa_grid.addWidget(self.btn_probe_flip, 0, 1)
        pa_grid.addWidget(self.cb_probe_autosave, 0, 2)
        pa_grid.addWidget(self.btn_probe_batch_scan, 1, 0)
        pa_grid.addWidget(self.btn_probe_batch_flip, 1, 1)
        pa_grid.addWidget(self.lbl_probe_status, 2, 0, 1, 3)
        wm_probe_layout.addWidget(probe_actions)

        wm_probe_layout.addStretch(1)

        probe_context, probe_ctx_layout = make_context_card(
            "Проверка водяного знака",
            "Выбери область экрана, при необходимости открой предпросмотр с оверлеем и реши, при каком условии нужно зеркалить клип.",
        )
        self.lbl_context_probe_source = QtWidgets.QLabel("—")
        self.lbl_context_probe_output = QtWidgets.QLabel("—")
        ctx_form = QtWidgets.QFormLayout()
        ctx_form.setHorizontalSpacing(8)
        ctx_form.setVerticalSpacing(6)
        ctx_form.addRow("RAW:", self.lbl_context_probe_source)
        ctx_form.addRow("Output:", self.lbl_context_probe_output)
        probe_ctx_layout.addLayout(ctx_form)
        probe_ctx_layout.addStretch(1)
        register_context("watermark_probe", probe_context)

        add_section(
            "watermark_probe",
            "🧐 Проверка ВЗ",
            self.tab_watermark_probe,
            scrollable=True,
            category="Водяные знаки",
            description="Проверка области на вспышку водяного знака и управляемый flip видео",
        )

        # TAB: YouTube uploader
        yt_cfg = self.cfg.get("youtube", {}) or {}
        self.tab_youtube, ty = make_scroll_tab()
        yt_intro = QtWidgets.QLabel(
            "Здесь настраивается отложенный постинг YouTube. Для авторизации скачай"
            " <a href=\"https://console.cloud.google.com/apis/credentials\">client_secret.json</a>"
            " в Google Cloud Console (OAuth 2.0) и разреши приложению доступ к каналу."
            " После первого запуска рядом появится credentials.json."
        )
        yt_intro.setWordWrap(True)
        yt_intro.setOpenExternalLinks(True)
        yt_intro.setStyleSheet("QLabel{color:#94a3b8;font-size:11px;}")
        ty.addWidget(yt_intro)

        grp_channels = QtWidgets.QGroupBox("Каналы и доступы")
        gc_layout = QtWidgets.QHBoxLayout(grp_channels)
        gc_layout.setSpacing(8)
        gc_layout.setContentsMargins(12, 12, 12, 12)

        self.lst_youtube_channels = QtWidgets.QListWidget()
        self.lst_youtube_channels.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        gc_layout.addWidget(self.lst_youtube_channels, 1)

        ch_form = QtWidgets.QFormLayout()
        ch_form.setVerticalSpacing(6)
        ch_form.setHorizontalSpacing(8)
        self.ed_yt_name = QtWidgets.QLineEdit()

        client_wrap = QtWidgets.QWidget(); client_l = QtWidgets.QHBoxLayout(client_wrap); client_l.setContentsMargins(0,0,0,0)
        self.ed_yt_client = QtWidgets.QLineEdit()
        self.btn_yt_client_browse = QtWidgets.QPushButton("…")
        client_l.addWidget(self.ed_yt_client, 1)
        client_l.addWidget(self.btn_yt_client_browse)

        cred_wrap = QtWidgets.QWidget(); cred_l = QtWidgets.QHBoxLayout(cred_wrap); cred_l.setContentsMargins(0,0,0,0)
        self.ed_yt_credentials = QtWidgets.QLineEdit()
        self.btn_yt_credentials_browse = QtWidgets.QPushButton("…")
        cred_l.addWidget(self.ed_yt_credentials, 1)
        cred_l.addWidget(self.btn_yt_credentials_browse)

        self.cmb_yt_privacy = QtWidgets.QComboBox(); self.cmb_yt_privacy.addItems(["private", "unlisted", "public"])

        ch_form.addRow("Имя канала:", self.ed_yt_name)
        ch_form.addRow("client_secret.json:", client_wrap)
        ch_form.addRow("credentials.json:", cred_wrap)
        ch_form.addRow("Приватность по умолчанию:", self.cmb_yt_privacy)

        btn_row = QtWidgets.QHBoxLayout()
        self.btn_yt_add = QtWidgets.QPushButton("Сохранить")
        self.btn_yt_delete = QtWidgets.QPushButton("Удалить")
        self.btn_yt_set_active = QtWidgets.QPushButton("Назначить активным")
        btn_row.addWidget(self.btn_yt_add)
        btn_row.addWidget(self.btn_yt_delete)
        btn_row.addWidget(self.btn_yt_set_active)
        ch_form.addRow(btn_row)

        self.lbl_yt_active = QtWidgets.QLabel("—")
        ch_form.addRow("Активный канал:", self.lbl_yt_active)

        gc_layout.addLayout(ch_form, 2)
        ty.addWidget(grp_channels)

        grp_run = QtWidgets.QGroupBox("Публикация и расписание")
        gr_form = QtWidgets.QGridLayout(grp_run)
        gr_form.setContentsMargins(12, 12, 12, 12)
        gr_form.setVerticalSpacing(6)
        gr_form.setHorizontalSpacing(8)
        row = 0

        self.cmb_youtube_channel = QtWidgets.QComboBox()
        gr_form.addWidget(QtWidgets.QLabel("Канал для загрузки:"), row, 0)
        gr_form.addWidget(self.cmb_youtube_channel, row, 1, 1, 2)
        row += 1

        self.cb_youtube_draft_only = QtWidgets.QCheckBox("Создавать приватные черновики")
        gr_form.addWidget(self.cb_youtube_draft_only, row, 0, 1, 3)
        row += 1

        self.cb_youtube_schedule = QtWidgets.QCheckBox("Запланировать публикации")
        self.cb_youtube_schedule.setChecked(True)
        gr_form.addWidget(self.cb_youtube_schedule, row, 0, 1, 3)
        row += 1

        self.dt_youtube_publish = QtWidgets.QDateTimeEdit(QtCore.QDateTime.currentDateTime())
        self.dt_youtube_publish.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.dt_youtube_publish.setCalendarPopup(True)
        gr_form.addWidget(QtWidgets.QLabel("Стартовая дата публикации:"), row, 0)
        gr_form.addWidget(self.dt_youtube_publish, row, 1, 1, 2)
        row += 1

        self.sb_youtube_interval = QtWidgets.QSpinBox()
        self.sb_youtube_interval.setRange(0, 7 * 24 * 60)
        self.sb_youtube_interval.setValue(int(yt_cfg.get("batch_step_minutes", 60)))
        gr_form.addWidget(QtWidgets.QLabel("Интервал между видео (мин):"), row, 0)
        gr_form.addWidget(self.sb_youtube_interval, row, 1)
        row += 1

        self.sb_youtube_batch_limit = QtWidgets.QSpinBox()
        self.sb_youtube_batch_limit.setRange(0, 999)
        self.sb_youtube_batch_limit.setSpecialValueText("без ограничений")
        self.sb_youtube_batch_limit.setValue(int(yt_cfg.get("batch_limit", 0)))
        gr_form.addWidget(QtWidgets.QLabel("Сколько видео за один запуск:"), row, 0)
        gr_form.addWidget(self.sb_youtube_batch_limit, row, 1)
        row += 1

        src_wrap = QtWidgets.QWidget(); src_l = QtWidgets.QHBoxLayout(src_wrap); src_l.setContentsMargins(0,0,0,0); src_l.setSpacing(4)
        self.ed_youtube_src = QtWidgets.QLineEdit(yt_cfg.get("upload_src_dir", self.cfg.get("merged_dir", str(MERG_DIR))))
        self.btn_youtube_src_browse = QtWidgets.QPushButton("…")
        self.btn_youtube_src_open = QtWidgets.QToolButton(); self.btn_youtube_src_open.setText("↗"); self.btn_youtube_src_open.setToolTip("Открыть папку загрузок YouTube")
        src_l.addWidget(self.ed_youtube_src, 1)
        src_l.addWidget(self.btn_youtube_src_browse)
        src_l.addWidget(self.btn_youtube_src_open)
        gr_form.addWidget(QtWidgets.QLabel("Папка с клипами:"), row, 0)
        gr_form.addWidget(src_wrap, row, 1, 1, 2)
        row += 1

        self.lbl_youtube_queue = QtWidgets.QLabel("Очередь: —")
        self.lbl_youtube_queue.setStyleSheet("QLabel{font-weight:600;}")
        gr_form.addWidget(self.lbl_youtube_queue, row, 0, 1, 3)
        row += 1

        btn_run_row = QtWidgets.QHBoxLayout()
        self.btn_youtube_refresh = QtWidgets.QPushButton("Показать очередь")
        self.btn_youtube_start = QtWidgets.QPushButton("Запустить загрузку")
        btn_run_row.addWidget(self.btn_youtube_refresh)
        btn_run_row.addWidget(self.btn_youtube_start)
        btn_run_row.addStretch(1)
        gr_form.addLayout(btn_run_row, row, 0, 1, 3)

        ty.addWidget(grp_run)
        ty.addStretch(1)
        tk_cfg = self.cfg.get("tiktok", {}) or {}
        self.tab_tiktok, tt = make_scroll_tab()
        tt_intro = QtWidgets.QLabel(
            "TikTok требует токен, который выдаёт <a href=\"https://developers.tiktok.com/\">TikTok for Developers</a>."
            " Загрузите JSON/YAML с client_key, client_secret, open_id и refresh_token и укажите путь ниже."
            " Расписание можно запускать локально или через GitHub Actions."
        )
        tt_intro.setWordWrap(True)
        tt_intro.setOpenExternalLinks(True)
        tt_intro.setStyleSheet("QLabel{color:#94a3b8;font-size:11px;}")
        tt.addWidget(tt_intro)

        grp_tt_profiles = QtWidgets.QGroupBox("Профили и авторизация")
        tp_layout = QtWidgets.QHBoxLayout(grp_tt_profiles)
        tp_layout.setSpacing(10)
        tp_layout.setContentsMargins(12, 12, 12, 12)

        self.lst_tiktok_profiles = QtWidgets.QListWidget()
        self.lst_tiktok_profiles.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.lst_tiktok_profiles.setUniformItemSizes(True)
        self.lst_tiktok_profiles.setMinimumWidth(180)
        tp_layout.addWidget(self.lst_tiktok_profiles, 1)

        profile_panel = QtWidgets.QWidget()
        profile_layout = QtWidgets.QVBoxLayout(profile_panel)
        profile_layout.setContentsMargins(0, 0, 0, 0)
        profile_layout.setSpacing(8)

        tt_hint = QtWidgets.QLabel("Укажи client_key, client_secret, open_id и refresh_token TikTok. Можно загрузить их из JSON/" "YAML файла и хранить вне конфига.")
        tt_hint.setWordWrap(True)
        tt_hint.setStyleSheet("QLabel{color:#94a3b8;font-size:11px;}")
        profile_layout.addWidget(tt_hint)

        tt_form = QtWidgets.QFormLayout()
        tt_form.setVerticalSpacing(6)
        tt_form.setHorizontalSpacing(8)
        tt_form.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        tt_form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)

        self.ed_tt_name = QtWidgets.QLineEdit()
        tt_form.addRow("Имя профиля:", self.ed_tt_name)

        secret_wrap = QtWidgets.QWidget()
        secret_layout = QtWidgets.QHBoxLayout(secret_wrap)
        secret_layout.setContentsMargins(0, 0, 0, 0)
        secret_layout.setSpacing(6)
        self.ed_tt_secret = QtWidgets.QLineEdit()
        self.ed_tt_secret.setPlaceholderText("./secrets/tiktok/profile.json")
        self.btn_tt_secret = QtWidgets.QPushButton("…")
        secret_layout.addWidget(self.ed_tt_secret, 1)
        secret_layout.addWidget(self.btn_tt_secret)
        tt_form.addRow("Файл секретов:", secret_wrap)

        self.btn_tt_secret_load = QtWidgets.QPushButton("Загрузить из файла")
        tt_form.addRow("", self.btn_tt_secret_load)

        self.ed_tt_client_key = QtWidgets.QLineEdit()
        self.ed_tt_client_key.setPlaceholderText("aw41xxx…")
        tt_form.addRow("Client key:", self.ed_tt_client_key)

        self.ed_tt_client_secret = QtWidgets.QLineEdit()
        self.ed_tt_client_secret.setPlaceholderText("секрет приложения")
        self.ed_tt_client_secret.setEchoMode(QtWidgets.QLineEdit.EchoMode.PasswordEchoOnEdit)
        tt_form.addRow("Client secret:", self.ed_tt_client_secret)

        self.ed_tt_open_id = QtWidgets.QLineEdit()
        self.ed_tt_open_id.setPlaceholderText("open_id пользователя")
        tt_form.addRow("Open ID:", self.ed_tt_open_id)

        self.ed_tt_refresh_token = QtWidgets.QLineEdit()
        self.ed_tt_refresh_token.setPlaceholderText("refresh_token")
        self.ed_tt_refresh_token.setEchoMode(QtWidgets.QLineEdit.EchoMode.PasswordEchoOnEdit)
        tt_form.addRow("Refresh token:", self.ed_tt_refresh_token)

        self.lbl_tt_token_status = QtWidgets.QLabel("Access token будет обновлён автоматически")
        self.lbl_tt_token_status.setStyleSheet("QLabel{color:#94a3b8;font-size:11px;}")
        tt_form.addRow("Access token:", self.lbl_tt_token_status)

        self.ed_tt_timezone = QtWidgets.QLineEdit()
        self.ed_tt_timezone.setPlaceholderText("Europe/Warsaw")
        tt_form.addRow("Часовой пояс:", self.ed_tt_timezone)

        self.sb_tt_offset = QtWidgets.QSpinBox()
        self.sb_tt_offset.setRange(-24 * 60, 24 * 60)
        self.sb_tt_offset.setSuffix(" мин")
        tt_form.addRow("Сдвиг расписания:", self.sb_tt_offset)

        self.ed_tt_hashtags = QtWidgets.QLineEdit()
        self.ed_tt_hashtags.setPlaceholderText("#sora #ai")
        tt_form.addRow("Хэштеги по умолчанию:", self.ed_tt_hashtags)

        self.txt_tt_caption = QtWidgets.QPlainTextEdit()
        self.txt_tt_caption.setPlaceholderText("Шаблон подписи: {title}\n{hashtags}")
        self.txt_tt_caption.setFixedHeight(110)
        tt_form.addRow("Шаблон подписи:", self.txt_tt_caption)

        btn_tt_row = QtWidgets.QHBoxLayout()
        btn_tt_row.setSpacing(6)
        self.btn_tt_add = QtWidgets.QPushButton("Сохранить")
        self.btn_tt_delete = QtWidgets.QPushButton("Удалить")
        self.btn_tt_set_active = QtWidgets.QPushButton("Сделать активным")
        btn_tt_row.addWidget(self.btn_tt_add)
        btn_tt_row.addWidget(self.btn_tt_delete)
        btn_tt_row.addWidget(self.btn_tt_set_active)

        tt_form.addRow("", btn_tt_row)

        self.lbl_tt_active = QtWidgets.QLabel("—")
        tt_form.addRow("Активный профиль:", self.lbl_tt_active)

        profile_layout.addLayout(tt_form)
        profile_layout.addStretch(1)
        tp_layout.addWidget(profile_panel, 2)
        tt.addWidget(grp_tt_profiles)

        grp_tt_run = QtWidgets.QGroupBox("Очередь и запуск")
        tr_layout = QtWidgets.QGridLayout(grp_tt_run)
        tr_layout.setContentsMargins(12, 12, 12, 12)
        tr_layout.setVerticalSpacing(6)
        tr_layout.setHorizontalSpacing(8)
        tr_layout.setColumnStretch(1, 1)
        row = 0

        self.cmb_tiktok_profile = QtWidgets.QComboBox()
        tr_layout.addWidget(QtWidgets.QLabel("Профиль:"), row, 0)
        tr_layout.addWidget(self.cmb_tiktok_profile, row, 1, 1, 2)
        row += 1

        self.cb_tiktok_draft = QtWidgets.QCheckBox("Сохранять как черновик")
        self.cb_tiktok_draft.setChecked(bool(tk_cfg.get("draft_only", False)))
        tr_layout.addWidget(self.cb_tiktok_draft, row, 0, 1, 3)
        row += 1

        self.cb_tiktok_schedule = QtWidgets.QCheckBox("Запланировать публикации")
        self.cb_tiktok_schedule.setChecked(bool(tk_cfg.get("schedule_enabled", True)))
        tr_layout.addWidget(self.cb_tiktok_schedule, row, 0, 1, 3)
        row += 1

        default_dt = QtCore.QDateTime.currentDateTime().addSecs(int(tk_cfg.get("schedule_minutes_from_now", 0)) * 60)
        self.dt_tiktok_publish = QtWidgets.QDateTimeEdit(default_dt)
        self.dt_tiktok_publish.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.dt_tiktok_publish.setCalendarPopup(True)
        tr_layout.addWidget(QtWidgets.QLabel("Стартовая дата публикации:"), row, 0)
        tr_layout.addWidget(self.dt_tiktok_publish, row, 1, 1, 2)
        row += 1

        self.sb_tiktok_interval = QtWidgets.QSpinBox()
        self.sb_tiktok_interval.setRange(0, 7 * 24 * 60)
        self.sb_tiktok_interval.setValue(int(tk_cfg.get("batch_step_minutes", 60)))
        tr_layout.addWidget(QtWidgets.QLabel("Интервал между видео (мин):"), row, 0)
        tr_layout.addWidget(self.sb_tiktok_interval, row, 1)
        row += 1

        self.sb_tiktok_batch_limit = QtWidgets.QSpinBox()
        self.sb_tiktok_batch_limit.setRange(0, 999)
        self.sb_tiktok_batch_limit.setSpecialValueText("без ограничений")
        self.sb_tiktok_batch_limit.setValue(int(tk_cfg.get("batch_limit", 0)))
        tr_layout.addWidget(QtWidgets.QLabel("Сколько видео за запуск:"), row, 0)
        tr_layout.addWidget(self.sb_tiktok_batch_limit, row, 1)
        row += 1

        src_tt_wrap = QtWidgets.QWidget()
        src_tt_layout = QtWidgets.QHBoxLayout(src_tt_wrap)
        src_tt_layout.setContentsMargins(0, 0, 0, 0)
        src_tt_layout.setSpacing(4)
        self.ed_tiktok_src = QtWidgets.QLineEdit(tk_cfg.get("upload_src_dir", self.cfg.get("merged_dir", str(MERG_DIR))))
        self.btn_tiktok_src_browse = QtWidgets.QPushButton("…")
        self.btn_tiktok_src_open = QtWidgets.QToolButton(); self.btn_tiktok_src_open.setText("↗"); self.btn_tiktok_src_open.setToolTip("Открыть папку загрузок TikTok")
        src_tt_layout.addWidget(self.ed_tiktok_src, 1)
        src_tt_layout.addWidget(self.btn_tiktok_src_browse)
        src_tt_layout.addWidget(self.btn_tiktok_src_open)
        tr_layout.addWidget(QtWidgets.QLabel("Папка с клипами:"), row, 0)
        tr_layout.addWidget(src_tt_wrap, row, 1, 1, 2)
        row += 1

        self.lbl_tiktok_queue = QtWidgets.QLabel("Очередь: —")
        self.lbl_tiktok_queue.setStyleSheet("QLabel{font-weight:600;}")
        tr_layout.addWidget(self.lbl_tiktok_queue, row, 0, 1, 3)
        row += 1

        gh_row = QtWidgets.QHBoxLayout()
        self.ed_tiktok_workflow = QtWidgets.QLineEdit(tk_cfg.get("github_workflow", ".github/workflows/tiktok-upload.yml"))
        self.ed_tiktok_ref = QtWidgets.QLineEdit(tk_cfg.get("github_ref", "main"))
        self.ed_tiktok_workflow.setPlaceholderText(".github/workflows/tiktok-upload.yml")
        self.ed_tiktok_ref.setPlaceholderText("main")
        gh_row.addWidget(QtWidgets.QLabel("Workflow:"))
        gh_row.addWidget(self.ed_tiktok_workflow, 1)
        gh_row.addWidget(QtWidgets.QLabel("Branch:"))
        gh_row.addWidget(self.ed_tiktok_ref, 1)
        tr_layout.addLayout(gh_row, row, 0, 1, 3)
        row += 1

        run_tt_row = QtWidgets.QHBoxLayout()
        self.btn_tiktok_refresh = QtWidgets.QPushButton("Показать очередь")
        self.btn_tiktok_start = QtWidgets.QPushButton("Запустить загрузку")
        self.btn_tiktok_dispatch = QtWidgets.QPushButton("GitHub Actions")
        run_tt_row.addWidget(self.btn_tiktok_refresh)
        run_tt_row.addWidget(self.btn_tiktok_start)
        run_tt_row.addWidget(self.btn_tiktok_dispatch)
        run_tt_row.addStretch(1)
        tr_layout.addLayout(run_tt_row, row, 0, 1, 3)

        tt.addWidget(grp_tt_run)
        tt.addSpacing(6)
        self._toggle_tiktok_schedule()

        # TAB: Промпты
        self.tab_prompts = QtWidgets.QWidget()
        pp = QtWidgets.QVBoxLayout(self.tab_prompts)
        pp.setContentsMargins(12, 12, 12, 12)
        pp.setSpacing(12)

        prompts_intro = QtWidgets.QLabel(
            "Менеджер промптов: выбери профиль Chrome слева, редактируй текст справа, а ниже смотри историю и управляй параллельными окнами."
        )
        prompts_intro.setWordWrap(True)
        prompts_intro.setStyleSheet("QLabel{color:#94a3b8;font-size:11px;}")
        pp.addWidget(prompts_intro)

        prompts_split = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        prompts_split.setHandleWidth(8)
        prompts_split.setChildrenCollapsible(False)
        prompts_stack = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        prompts_stack.setHandleWidth(8)
        prompts_stack.setChildrenCollapsible(False)
        prompts_stack.addWidget(prompts_split)
        self.prompts_split = prompts_split
        self.prompts_stack = prompts_stack
        pp.addWidget(prompts_stack, 1)

        profile_panel = QtWidgets.QFrame()
        profile_layout = QtWidgets.QVBoxLayout(profile_panel)
        profile_layout.setContentsMargins(0, 0, 0, 0)
        profile_layout.setSpacing(8)

        lbl_profiles = QtWidgets.QLabel("<b>Профили промптов</b>")
        profile_layout.addWidget(lbl_profiles)
        self.lbl_prompts_active = QtWidgets.QLabel("—")
        self.lbl_prompts_active.setStyleSheet("QLabel{color:#94a3b8;font-size:11px;}")
        profile_layout.addWidget(self.lbl_prompts_active)

        self.lst_prompt_profiles = QtWidgets.QListWidget()
        self.lst_prompt_profiles.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.lst_prompt_profiles.setUniformItemSizes(True)
        self.lst_prompt_profiles.setStyleSheet(
            "QListWidget{background:#101827;border:1px solid #23324b;border-radius:10px;padding:6px;}"
        )
        profile_layout.addWidget(self.lst_prompt_profiles, 1)

        profile_hint = QtWidgets.QLabel(
            "Каждый профиль получает свой файл `prompts_*.txt`. При выборе профиль сразу становится активным для сценария."
        )
        profile_hint.setWordWrap(True)
        profile_hint.setStyleSheet("QLabel{color:#94a3b8;font-size:11px;}")
        profile_layout.addWidget(profile_hint)
        profile_layout.addSpacing(6)

        editor_panel = QtWidgets.QFrame()
        editor_layout = QtWidgets.QVBoxLayout(editor_panel)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(8)

        editor_bar = QtWidgets.QHBoxLayout()
        self.btn_load_prompts = QtWidgets.QPushButton("Обновить файл")
        self.btn_save_prompts = QtWidgets.QPushButton("Сохранить изменения")
        self.btn_save_and_run_autogen = QtWidgets.QPushButton("Сохранить и запустить автоген (видео)")
        editor_bar.addWidget(self.btn_load_prompts)
        editor_bar.addWidget(self.btn_save_prompts)
        editor_bar.addStretch(1)
        editor_bar.addWidget(self.btn_save_and_run_autogen)
        editor_layout.addLayout(editor_bar)

        self.lbl_prompts_path = QtWidgets.QLabel("—")
        self.lbl_prompts_path.setStyleSheet("QLabel{color:#94a3b8;font-size:11px;}")
        self.lbl_prompts_path.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        editor_layout.addWidget(self.lbl_prompts_path)

        self.ed_prompts = QtWidgets.QPlainTextEdit()
        self.ed_prompts.setPlaceholderText("Один промпт — одна строка. Эти строки сохраняются для выбранного профиля.")
        self.ed_prompts.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
        mono = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont)
        mono.setPointSize(11)
        self.ed_prompts.setFont(mono)
        editor_layout.addWidget(self.ed_prompts, 1)

        prompts_split.addWidget(profile_panel)
        prompts_split.addWidget(editor_panel)
        prompts_split.setStretchFactor(0, 1)
        prompts_split.setStretchFactor(1, 2)

        grp_used = QtWidgets.QGroupBox("Использованные промпты")
        grp_used.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Preferred,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        used_layout = QtWidgets.QVBoxLayout(grp_used)
        used_layout.setSpacing(8)
        self.tbl_used_prompts = QtWidgets.QTableWidget(0, 3)
        self.tbl_used_prompts.setHorizontalHeaderLabels(["Когда", "Окно", "Текст"])
        self.tbl_used_prompts.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.tbl_used_prompts.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl_used_prompts.verticalHeader().setVisible(False)
        self.tbl_used_prompts.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_used_prompts.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_used_prompts.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.Stretch)
        used_layout.addWidget(self.tbl_used_prompts, 1)
        used_btns = QtWidgets.QHBoxLayout()
        self.btn_used_refresh = QtWidgets.QPushButton("Обновить")
        self.btn_used_clear = QtWidgets.QPushButton("Очистить журнал")
        used_btns.addWidget(self.btn_used_refresh)
        used_btns.addWidget(self.btn_used_clear)
        used_btns.addStretch(1)
        used_layout.addLayout(used_btns)
        prompts_stack.addWidget(grp_used)

        prompts_stack.setStretchFactor(0, 3)
        prompts_stack.setStretchFactor(1, 2)
        QtCore.QTimer.singleShot(0, lambda: prompts_stack.setSizes([360, 220]))
        # TAB: Промпты картинок
        self.tab_image_prompts = QtWidgets.QWidget()
        ip_layout = QtWidgets.QVBoxLayout(self.tab_image_prompts)
        ip_layout.setContentsMargins(12, 12, 12, 12)
        ip_layout.setSpacing(12)

        ip_intro = QtWidgets.QLabel(
            "Задай отдельные промпты для генерации изображений. Строки применяются последовательно к основным промптам."
        )
        ip_intro.setWordWrap(True)
        ip_intro.setStyleSheet("QLabel{color:#94a3b8;font-size:11px;}")
        ip_layout.addWidget(ip_intro)

        ip_bar = QtWidgets.QHBoxLayout()
        self.btn_load_image_prompts = QtWidgets.QPushButton("Обновить файл")
        self.btn_save_image_prompts = QtWidgets.QPushButton("Сохранить изменения")
        ip_bar.addWidget(self.btn_load_image_prompts)
        ip_bar.addWidget(self.btn_save_image_prompts)
        ip_bar.addStretch(1)
        ip_layout.addLayout(ip_bar)

        self.lbl_image_prompts_path = QtWidgets.QLabel("—")
        self.lbl_image_prompts_path.setStyleSheet("QLabel{color:#94a3b8;font-size:11px;}")
        self.lbl_image_prompts_path.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        ip_layout.addWidget(self.lbl_image_prompts_path)

        self.ed_image_prompts = QtWidgets.QPlainTextEdit()
        self.ed_image_prompts.setPlaceholderText(
            "Одна строка = один image prompt. Можно использовать JSON, чтобы задать поля `prompt`, `prompts` или `count`."
        )
        self.ed_image_prompts.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
        self.ed_image_prompts.setFont(mono)
        ip_layout.addWidget(self.ed_image_prompts, 1)

        ip_hint = QtWidgets.QLabel(
            "Пустые строки и комментарии (#) пропускаются. JSON-строки позволяют указать несколько image-промптов и количество кадров."
        )
        ip_hint.setWordWrap(True)
        ip_hint.setStyleSheet("QLabel{color:#94a3b8;font-size:11px;}")
        ip_layout.addWidget(ip_hint)

        # TAB: Названия
        self.tab_titles = QtWidgets.QWidget(); pt = QtWidgets.QVBoxLayout(self.tab_titles)
        titles_intro = QtWidgets.QLabel("Редактор имён для переименования скачанных роликов — каждое имя на новой строке.")
        titles_intro.setWordWrap(True)
        titles_intro.setStyleSheet("QLabel{color:#94a3b8;font-size:11px;}")
        pt.addWidget(titles_intro)
        bar2 = QtWidgets.QHBoxLayout()
        self.btn_load_titles = QtWidgets.QPushButton("Загрузить")
        self.btn_save_titles = QtWidgets.QPushButton("Сохранить")
        self.btn_reset_titles_cursor = QtWidgets.QPushButton("Сбросить прогресс имён")
        bar2.addWidget(self.btn_load_titles); bar2.addWidget(self.btn_save_titles); bar2.addStretch(1); bar2.addWidget(self.btn_reset_titles_cursor)
        pt.addLayout(bar2)
        self.ed_titles = QtWidgets.QPlainTextEdit()
        self.ed_titles.setPlaceholderText("Желаемые имена (по строке)…")
        pt.addWidget(self.ed_titles, 1)
        # TAB: Настройки
        self.tab_settings = QtWidgets.QScrollArea()
        self.tab_settings.setWidgetResizable(True)
        settings_body = QtWidgets.QWidget()
        settings_layout = QtWidgets.QVBoxLayout(settings_body)
        settings_layout.setContentsMargins(16, 16, 16, 16)
        settings_intro = QtWidgets.QLabel("Настройки сгруппированы по вкладкам: каталоги, Chrome, FFmpeg, YouTube и обслуживание. Раздел Telegram вынесен отдельно слева.")
        settings_intro.setWordWrap(True)
        settings_intro.setStyleSheet("QLabel{color:#94a3b8;font-size:11px;}")
        settings_layout.addWidget(settings_intro)

        self.settings_tabs = QtWidgets.QTabWidget()
        self.settings_tabs.setTabPosition(QtWidgets.QTabWidget.TabPosition.North)
        settings_layout.addWidget(self.settings_tabs, 1)

        # Справочники (перенесены в настройки)
        self.tab_errors = QtWidgets.QWidget()
        err_layout = QtWidgets.QVBoxLayout(self.tab_errors)
        err_layout.setContentsMargins(12, 12, 12, 12)
        err_layout.setSpacing(8)
        self.tbl_errors = QtWidgets.QTableWidget(len(ERROR_GUIDE), 3)
        self.tbl_errors.setHorizontalHeaderLabels(["Код", "Что означает", "Что сделать"])
        self.tbl_errors.verticalHeader().setVisible(False)
        self.tbl_errors.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl_errors.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
        self.tbl_errors.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_errors.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.tbl_errors.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.Stretch)
        for row, (code, meaning, action) in enumerate(ERROR_GUIDE):
            self.tbl_errors.setItem(row, 0, QtWidgets.QTableWidgetItem(code))
            self.tbl_errors.setItem(row, 1, QtWidgets.QTableWidgetItem(meaning))
            self.tbl_errors.setItem(row, 2, QtWidgets.QTableWidgetItem(action))
        err_layout.addWidget(self.tbl_errors)
        self.tab_history = QtWidgets.QWidget()
        h = QtWidgets.QVBoxLayout(self.tab_history)
        h.setContentsMargins(12, 12, 12, 12)
        h.setSpacing(8)
        self.txt_history = QtWidgets.QPlainTextEdit()
        self.txt_history.setReadOnly(True)
        h.addWidget(self.txt_history, 1)

        self._build_settings_pages()

        controls_row = QtWidgets.QHBoxLayout()
        self.lbl_settings_status = QtWidgets.QLabel("Изменения сохраняются автоматически")
        self.lbl_settings_status.setStyleSheet("color:#2c3e50;")
        self.btn_save_settings = QtWidgets.QPushButton("Применить настройки")
        controls_row.addWidget(self.lbl_settings_status)
        controls_row.addStretch(1)
        controls_row.addWidget(self.btn_save_settings)
        settings_layout.addLayout(controls_row)

        self.tab_settings.setWidget(settings_body)
        settings_context, settings_ctx_layout = make_context_card(
            "Конфигурация",
            "Следи за статусом сохранения и быстро открывай каталог проекта.",
        )
        settings_form = QtWidgets.QFormLayout()
        settings_form.setHorizontalSpacing(8)
        settings_form.setVerticalSpacing(6)
        self.lbl_context_settings_status = QtWidgets.QLabel("—")
        self.lbl_context_project_root = QtWidgets.QLabel(self.cfg.get("project_root", str(PROJECT_ROOT)))
        self.lbl_context_project_root.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        settings_form.addRow("Сохранение:", self.lbl_context_settings_status)
        settings_form.addRow("Проект:", self.lbl_context_project_root)
        settings_ctx_layout.addLayout(settings_form)
        self.btn_context_open_project = QtWidgets.QPushButton("Открыть папку проекта")
        settings_ctx_layout.addWidget(self.btn_context_open_project)
        settings_ctx_layout.addStretch(1)
        register_context("settings", settings_context)

        content_host = QtWidgets.QWidget()
        content_layout = QtWidgets.QVBoxLayout(content_host)
        content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_tabs = QtWidgets.QTabWidget()
        self.content_tabs.setTabPosition(QtWidgets.QTabWidget.TabPosition.North)
        self.content_tabs.addTab(self.tab_prompts, "Промпты Sora")
        self.content_tabs.addTab(self.tab_image_prompts, "Промпты картинок")
        self.content_tabs.addTab(self.tab_titles, "Названия")
        content_layout.addWidget(self.content_tabs)
        content_context, content_ctx_layout = make_context_card(
            "Материалы",
            "Текущие файлы с промптами, шаблонами изображений и названиями.",
        )
        content_form = QtWidgets.QFormLayout()
        content_form.setHorizontalSpacing(8)
        content_form.setVerticalSpacing(6)
        self.lbl_context_content_profile = QtWidgets.QLabel("—")
        self.lbl_context_prompts_path = QtWidgets.QLabel("—")
        self.lbl_context_image_prompts_path = QtWidgets.QLabel("—")
        self.lbl_context_titles_path = QtWidgets.QLabel("—")
        for label in (
            self.lbl_context_prompts_path,
            self.lbl_context_image_prompts_path,
            self.lbl_context_titles_path,
        ):
            label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        content_form.addRow("Профиль:", self.lbl_context_content_profile)
        content_form.addRow("Sora:", self.lbl_context_prompts_path)
        content_form.addRow("Картинки:", self.lbl_context_image_prompts_path)
        content_form.addRow("Названия:", self.lbl_context_titles_path)
        content_ctx_layout.addLayout(content_form)
        btn_row = QtWidgets.QHBoxLayout()
        self.btn_context_open_prompts = QtWidgets.QPushButton("Открыть промпты")
        self.btn_context_open_image_prompts = QtWidgets.QPushButton("Открыть картинки")
        self.btn_context_open_titles = QtWidgets.QPushButton("Открыть названия")
        btn_row.addWidget(self.btn_context_open_prompts)
        btn_row.addWidget(self.btn_context_open_image_prompts)
        btn_row.addWidget(self.btn_context_open_titles)
        content_ctx_layout.addLayout(btn_row)
        content_ctx_layout.addStretch(1)
        register_context("content", content_context)
        add_section(
            "content",
            "📝 Контент",
            content_host,
            category="Контент",
            description="Редакторы промптов, изображений и заголовков",
        )

        autopost_host = QtWidgets.QWidget()
        autopost_layout = QtWidgets.QVBoxLayout(autopost_host)
        autopost_layout.setContentsMargins(0, 0, 0, 0)
        self.autopost_tabs = QtWidgets.QTabWidget()
        self.autopost_tabs.setTabPosition(QtWidgets.QTabWidget.TabPosition.North)
        self.autopost_tabs.addTab(self.tab_youtube, "YouTube")
        self.autopost_tabs.addTab(self.tab_tiktok, "TikTok")
        autopost_layout.addWidget(self.autopost_tabs)
        autopost_context, autopost_ctx_layout = make_context_card(
            "Очереди",
            "Контролируй количество роликов в очереди публикаций и обновляй данные перед стартом.",
        )
        queue_form = QtWidgets.QFormLayout()
        queue_form.setHorizontalSpacing(8)
        queue_form.setVerticalSpacing(6)
        self.lbl_context_youtube_queue = QtWidgets.QLabel("—")
        self.lbl_context_tiktok_queue = QtWidgets.QLabel("—")
        queue_form.addRow("YouTube:", self.lbl_context_youtube_queue)
        queue_form.addRow("TikTok:", self.lbl_context_tiktok_queue)
        autopost_ctx_layout.addLayout(queue_form)
        self.btn_context_refresh_queues = QtWidgets.QPushButton("Обновить очереди")
        autopost_ctx_layout.addWidget(self.btn_context_refresh_queues)
        autopost_ctx_layout.addStretch(1)
        register_context("autopost", autopost_context)

        self.telegram_panel = self._build_telegram_panel()
        telegram_context, telegram_ctx_layout = make_context_card(
            "Telegram",
            "Статус подключения и быстрые проверки рассылки.",
        )
        tg_form = QtWidgets.QFormLayout()
        tg_form.setHorizontalSpacing(8)
        tg_form.setVerticalSpacing(6)
        self.lbl_context_tg_enabled = QtWidgets.QLabel("—")
        self.lbl_context_tg_template = QtWidgets.QLabel("—")
        tg_form.addRow("Уведомления:", self.lbl_context_tg_enabled)
        tg_form.addRow("Шаблон:", self.lbl_context_tg_template)
        telegram_ctx_layout.addLayout(tg_form)
        tg_btns = QtWidgets.QHBoxLayout()
        self.btn_context_tg_test = QtWidgets.QPushButton("Тест")
        self.btn_context_tg_open = QtWidgets.QPushButton("Открыть раздел")
        tg_btns.addWidget(self.btn_context_tg_test)
        tg_btns.addWidget(self.btn_context_tg_open)
        telegram_ctx_layout.addLayout(tg_btns)
        telegram_ctx_layout.addStretch(1)
        register_context("telegram", telegram_context)
        add_section(
            "telegram",
            "✈️ Telegram",
            self.telegram_panel,
            scrollable=True,
            category="Интеграции",
            description="Уведомления, шаблоны и моментальные сообщения в Telegram",
        )
        self._refresh_telegram_history()
        # Автопостинг скрыт в новой компоновке, чтобы навигация осталась компактной

        add_section(
            "settings",
            "⚙️ Настройки",
            self.tab_settings,
            category="Система",
            description="Каталоги, Chrome, ffmpeg, история и обслуживание",
        )

        self._load_zones_into_ui()
        self._toggle_youtube_schedule()
        self._refresh_settings_context()

        self.section_nav.currentRowChanged.connect(self._on_section_nav_changed)
        if self.section_nav.count():
            for row in range(self.section_nav.count()):
                item = self.section_nav.item(row)
                if item and item.data(QtCore.Qt.ItemDataRole.UserRole):
                    self.section_nav.setCurrentRow(row)
                    break
        else:
            self._current_section_key = "overview"
        self._set_context_visible(bool(self.cfg.get("ui", {}).get("show_context", True)), persist=False)
        self._rebuild_custom_command_panel()
        self._refresh_custom_command_registry()
        self._update_current_event("—", self.cfg.get("ui", {}).get("accent_kind", "info"), persist=False)
        self._apply_activity_visibility(self.chk_activity_visible.isChecked(), persist=False)

    # ----- command palette -----
    def _register_command(
        self,
        command_id: str,
        title: str,
        callback: Callable[[], None],
        *,
        category: str = "Приложение",
        subtitle: str = "",
        shortcut: Optional[str] = None,
        keywords: Optional[Iterable[str]] = None,
    ) -> None:
        if not callable(callback):
            return
        keywords = list(keywords or [])
        existing_action = self._command_actions.pop(command_id, None)
        if existing_action is not None:
            self.removeAction(existing_action)

        action = None
        if shortcut:
            action = QtGui.QAction(title, self)
            action.setShortcut(QtGui.QKeySequence(shortcut))
            action.setShortcutContext(QtCore.Qt.ShortcutContext.ApplicationShortcut)
            action.triggered.connect(callback)
            self.addAction(action)
            self._command_actions[command_id] = action

        self._command_registry[command_id] = {
            "id": command_id,
            "title": title,
            "subtitle": subtitle or "",
            "category": category,
            "keywords": keywords,
            "callback": callback,
            "shortcut": shortcut or "",
        }

    def _remove_command(self, command_id: str) -> None:
        if command_id in self._command_registry:
            self._command_registry.pop(command_id, None)
        action = self._command_actions.pop(command_id, None)
        if action:
            self.removeAction(action)

    def _prune_commands_with_prefix(self, prefix: str) -> None:
        to_remove = [cid for cid in self._command_registry if cid.startswith(prefix)]
        for cid in to_remove:
            self._remove_command(cid)

    def _open_command_palette(self) -> None:
        items: List[Dict[str, Any]] = []
        for meta in self._command_registry.values():
            items.append(
                {
                    "id": meta.get("id"),
                    "title": meta.get("title", ""),
                    "subtitle": meta.get("subtitle", ""),
                    "category": meta.get("category", ""),
                    "keywords": list(meta.get("keywords", [])),
                }
            )
        items.sort(key=lambda it: (it.get("category", ""), it.get("title", "")))
        dialog = CommandPaletteDialog(self, items)
        geo = self.geometry()
        dlg_geo = dialog.frameGeometry()
        dlg_geo.moveCenter(geo.center())
        dialog.move(dlg_geo.topLeft())
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            selected = dialog.selected_command()
            if selected and selected in self._command_registry:
                callback = self._command_registry[selected].get("callback")
                if callable(callback):
                    callback()

    def _focus_section_from_command(self, key: str) -> None:
        self._select_section(key)
        self.raise_()
        self.activateWindow()

    def _focus_session_from_command(self, session_id: str) -> None:
        self._focus_section_from_command("sessions")
        if not hasattr(self, "lst_sessions"):
            return
        for row in range(self.lst_sessions.count()):
            item = self.lst_sessions.item(row)
            if not item:
                continue
            if item.data(QtCore.Qt.ItemDataRole.UserRole) == session_id:
                self.lst_sessions.setCurrentRow(row)
                break

    def _refresh_command_palette_sessions(self) -> None:
        self._prune_commands_with_prefix("session:")
        for session_id in self._session_order:
            session = self._session_cache.get(session_id)
            if not session:
                continue
            name = session.get("name", session_id)
            chrome = session.get("chrome_profile", "") or "по умолчанию"
            prompt = session.get("prompt_profile", PROMPTS_DEFAULT_KEY)
            subtitle = f"Chrome: {chrome} · Промпты: {self._prompt_profile_label(prompt)}"
            keywords = [name, chrome, prompt]
            self._register_command(
                f"session:{session_id}",
                f"Рабочее пространство — {name}",
                lambda sid=session_id: self._focus_session_from_command(sid),
                category="Рабочие пространства",
                subtitle=subtitle,
                keywords=keywords,
            )

    def _refresh_custom_command_registry(self) -> None:
        self._prune_commands_with_prefix("custom:")
        for idx, entry in enumerate(self._custom_commands):
            payload = dict(entry)
            name = payload.get("name") or f"Команда {idx + 1}"
            subtitle = payload.get("description") or payload.get("command", "")
            keywords = [name]
            keywords.extend((payload.get("command") or "").split())
            self._register_command(
                f"custom:{idx}",
                f"Быстрая команда — {name}",
                lambda data=payload: self._run_custom_command(data),
                category="Пользовательские команды",
                subtitle=subtitle,
                keywords=keywords,
            )

    def _set_context_visible(self, visible: bool, *, persist: bool = True) -> None:
        if hasattr(self, "body_splitter") and hasattr(self, "context_container"):
            sizes = self.body_splitter.sizes()
            if not isinstance(sizes, list) or len(sizes) < 3:
                sizes = [self._nav_saved_size, self.width(), self._context_saved_size]
            total = sum(sizes) or max(self.width(), 1)
            nav = sizes[0] or self._nav_saved_size
            self._nav_saved_size = max(nav, 220)
            if visible:
                context_target = max(self._context_saved_size, 260)
                middle = max(total - nav - context_target, 320)
                self.body_splitter.blockSignals(True)
                self.body_splitter.setSizes([nav, middle, context_target])
                self.body_splitter.blockSignals(False)
                self.context_container.show()
            else:
                self._context_saved_size = sizes[2] or max(self.context_container.width(), 260)
                middle = max(total - nav, 320)
                self.body_splitter.blockSignals(True)
                self.body_splitter.setSizes([nav, middle, 0])
                self.body_splitter.blockSignals(False)
                self.context_container.hide()
        elif hasattr(self, "context_container"):
            self.context_container.setVisible(bool(visible))
        if hasattr(self, "btn_toggle_commands"):
            self.btn_toggle_commands.blockSignals(True)
            self.btn_toggle_commands.setChecked(bool(visible))
            self.btn_toggle_commands.setText(
                "🧩 Панель ⬅️" if visible else "🧩 Панель ➡️"
            )
            self.btn_toggle_commands.setToolTip(
                "Скрыть панель команд" if visible else "Показать панель команд"
            )
            self.btn_toggle_commands.blockSignals(False)
        if hasattr(self, "cb_ui_show_context"):
            self.cb_ui_show_context.blockSignals(True)
            self.cb_ui_show_context.setChecked(bool(visible))
            self.cb_ui_show_context.blockSignals(False)
        if persist:
            self.cfg.setdefault("ui", {})["show_context"] = bool(visible)
            self._mark_settings_dirty()

    def _init_splitter_sizes(self) -> None:
        if not hasattr(self, "body_splitter"):
            return
        sizes = self.body_splitter.sizes()
        if not sizes or sum(sizes) == 0:
            self.body_splitter.setSizes([
                max(self._nav_saved_size, 240),
                max(self.width() - self._nav_saved_size - self._context_saved_size, 480),
                max(self._context_saved_size, 280),
            ])

    def _rebuild_custom_command_panel(self) -> None:
        if not hasattr(self, "custom_command_button_layout"):
            return
        layout = self.custom_command_button_layout
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if not self._custom_commands:
            if hasattr(self, "custom_command_panel"):
                self.custom_command_panel.hide()
            if hasattr(self, "custom_command_caption"):
                self.custom_command_caption.setVisible(True)
            return
        if hasattr(self, "custom_command_panel"):
            self.custom_command_panel.show()
        if hasattr(self, "custom_command_caption"):
            self.custom_command_caption.setVisible(False)
        for entry in self._custom_commands:
            label = entry.get("name") or "Команда"
            btn = QtWidgets.QPushButton(label)
            btn.setObjectName("customCommandButton")
            btn.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
            tooltip = entry.get("description") or entry.get("command")
            if tooltip:
                btn.setToolTip(tooltip)
            payload = dict(entry)
            btn.clicked.connect(lambda _, data=payload: self._run_custom_command(data))
            layout.addWidget(btn)
        layout.addStretch(1)

    def _refresh_custom_command_list(self) -> None:
        if not hasattr(self, "lst_custom_commands"):
            return
        self.lst_custom_commands.blockSignals(True)
        self.lst_custom_commands.clear()
        for entry in self._custom_commands:
            name = entry.get("name") or "Команда"
            item = QtWidgets.QListWidgetItem(name)
            cmd = entry.get("command")
            if cmd:
                item.setToolTip(cmd)
            self.lst_custom_commands.addItem(item)
        self.lst_custom_commands.blockSignals(False)
        self._update_custom_command_buttons()

    def _selected_custom_command_index(self) -> int:
        if not hasattr(self, "lst_custom_commands"):
            return -1
        row = self.lst_custom_commands.currentRow()
        if 0 <= row < len(self._custom_commands):
            return row
        return -1

    def _update_custom_command_buttons(self) -> None:
        if not hasattr(self, "btn_custom_edit"):
            return
        idx = self._selected_custom_command_index()
        has_selection = idx >= 0
        total = len(self._custom_commands)
        self.btn_custom_edit.setEnabled(has_selection)
        self.btn_custom_delete.setEnabled(has_selection)
        self.btn_custom_up.setEnabled(has_selection and idx > 0)
        self.btn_custom_down.setEnabled(has_selection and idx >= 0 and idx < total - 1)

    def _on_custom_command_add(self) -> None:
        dialog = CustomCommandDialog(self)
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            data = dialog.get_data()
            self._custom_commands.append(data)
            self._refresh_custom_command_list()
            self.lst_custom_commands.setCurrentRow(len(self._custom_commands) - 1)
            self._rebuild_custom_command_panel()
            self._refresh_custom_command_registry()
            self._mark_settings_dirty()

    def _on_custom_command_edit(self) -> None:
        idx = self._selected_custom_command_index()
        if idx < 0:
            return
        dialog = CustomCommandDialog(self, self._custom_commands[idx])
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            self._custom_commands[idx] = dialog.get_data()
            self._refresh_custom_command_list()
            self.lst_custom_commands.setCurrentRow(idx)
            self._rebuild_custom_command_panel()
            self._refresh_custom_command_registry()
            self._mark_settings_dirty()

    def _on_custom_command_remove(self) -> None:
        idx = self._selected_custom_command_index()
        if idx < 0:
            return
        self._custom_commands.pop(idx)
        self._refresh_custom_command_list()
        if self._custom_commands:
            self.lst_custom_commands.setCurrentRow(min(idx, len(self._custom_commands) - 1))
        self._rebuild_custom_command_panel()
        self._refresh_custom_command_registry()
        self._mark_settings_dirty()

    def _on_custom_command_move(self, direction: int) -> None:
        idx = self._selected_custom_command_index()
        if idx < 0:
            return
        new_idx = idx + direction
        if not (0 <= new_idx < len(self._custom_commands)):
            return
        self._custom_commands[idx], self._custom_commands[new_idx] = (
            self._custom_commands[new_idx],
            self._custom_commands[idx],
        )
        self._refresh_custom_command_list()
        self.lst_custom_commands.setCurrentRow(new_idx)
        self._rebuild_custom_command_panel()
        self._refresh_custom_command_registry()
        self._mark_settings_dirty()

    def _run_custom_command(self, payload: Dict[str, str]) -> None:
        command = (payload.get("command") or "").strip()
        name = payload.get("name") or "Команда"
        if not command:
            self._post_status(f"Команда «{name}»: не задана команда", state="error")
            return

        self._append_activity(f"Команда «{name}» запускается…", kind="running")

        def worker() -> None:
            try:
                subprocess.Popen(
                    command,
                    shell=True,
                    cwd=self.cfg.get("project_root", str(PROJECT_ROOT)),
                )
            except Exception as exc:  # noqa: BLE001
                self.ui(
                    lambda: (
                        self._append_activity(
                            f"Команда «{name}» не запустилась: {exc}",
                            kind="error",
                        ),
                        self._post_status(f"Команда «{name}» не запустилась", state="error"),
                    )
                )
                return

            self.ui(
                lambda: (
                    self._append_activity(
                        f"Команда «{name}» выполнена", kind="success"
                    ),
                    self._post_status(f"Команда «{name}» запущена", state="ok"),
                )
            )

        threading.Thread(target=worker, daemon=True).start()

    def _on_toolbar_commands_toggle(self, checked: bool) -> None:
        self._set_context_visible(bool(checked))

    def _on_settings_show_context_changed(self, checked: bool) -> None:
        self._set_context_visible(bool(checked))

    def _update_context_panel_for_section(self, key: str) -> None:
        if not hasattr(self, "context_stack"):
            return
        idx = self._context_index.get(key, getattr(self, "_context_default_idx", 0))
        if self.context_stack.currentIndex() != idx:
            self.context_stack.setCurrentIndex(idx)
        refresher = {
            "overview": self._refresh_overview_context,
            "sessions": self._refresh_sessions_context,
            "pipeline": self._refresh_pipeline_context,
            "automator": self._refresh_automation_context,
            "logs": self._refresh_logs_context,
            "session_logs": self._refresh_session_logs_context,
            "watermark": self._refresh_watermark_context,
            "watermark_probe": self._refresh_watermark_probe_context,
            "content": self._refresh_content_context,
            "telegram": self._refresh_telegram_context,
            "autopost": self._refresh_autopost_context,
            "settings": self._refresh_settings_context,
        }.get(key)
        if callable(refresher):
            refresher()

    def _refresh_overview_context(self) -> None:
        visible = bool(self.cfg.get("ui", {}).get("show_activity", True))
        density = self.cfg.get("ui", {}).get("activity_density", "compact")
        if hasattr(self, "lbl_context_overview_activity"):
            self.lbl_context_overview_activity.setText("включён" if visible else "скрыт")
        if hasattr(self, "lbl_context_overview_density"):
            mapping = {"compact": "компактная", "cozy": "стандартная"}
            self.lbl_context_overview_density.setText(mapping.get(density, density))

    def _refresh_sessions_context(self) -> None:
        if not hasattr(self, "lbl_context_session_name"):
            return
        session = self._session_cache.get(self._current_session_id)
        if not session:
            self.lbl_context_session_name.setText("—")
            self.lbl_context_session_profiles.setText("—")
            self.lbl_context_session_status.setText("—")
            return
        name = session.get("name", self._current_session_id)
        chrome = session.get("chrome_profile", "") or "по умолчанию"
        prompt = session.get("prompt_profile", PROMPTS_DEFAULT_KEY)
        raw_dir = self._session_download_dir(session)
        raw_label = raw_dir.name if isinstance(raw_dir, Path) else Path(str(raw_dir)).name if raw_dir else "—"
        limit_label = self._session_download_limit_label(session)
        self.lbl_context_session_name.setText(name)
        self.lbl_context_session_profiles.setText(
            f"Chrome: {chrome} · Промпты: {self._prompt_profile_label(prompt)} · RAW: {raw_label} ({limit_label})"
        )
        state = self._ensure_session_state(self._current_session_id)
        status = state.get("status", "idle")
        message = state.get("last_message", "")
        icon = self._session_status_icon(status)
        self.lbl_context_session_status.setText(message or f"{icon} {status}")

    def _refresh_pipeline_context(self) -> None:
        if not hasattr(self, "lbl_context_pipeline_profile"):
            return
        profile = self.cmb_chrome_profile_top.currentText() if hasattr(self, "cmb_chrome_profile_top") else ""
        profile = profile or "по умолчанию"
        self.lbl_context_pipeline_profile.setText(profile)
        stages = []
        stage_labels = [
            (getattr(self, "cb_do_images", None), "Картинки"),
            (getattr(self, "cb_do_autogen", None), "Промпты"),
            (getattr(self, "cb_do_download", None), "Скачка"),
            (getattr(self, "cb_do_blur", None), "Блюр"),
            (getattr(self, "cb_do_watermark", None), "Очистка"),
            (getattr(self, "cb_do_merge", None), "Склейка"),
            (getattr(self, "cb_do_upload", None), "YouTube"),
            (getattr(self, "cb_do_tiktok", None), "TikTok"),
        ]
        for checkbox, label in stage_labels:
            if checkbox is not None and checkbox.isChecked():
                stages.append(label)
        self.lbl_context_pipeline_steps.setText(
            ", ".join(stages) if stages else "этапы не выбраны"
        )
        limit_parts = []
        if hasattr(self, "sb_max_videos"):
            max_videos = self.sb_max_videos.value()
            limit_parts.append("нет ограничения" if max_videos <= 0 else f"до {max_videos} видео")
        if hasattr(self, "sb_merge_group"):
            limit_parts.append(f"склейка по {self.sb_merge_group.value()} клипа")
        self.lbl_context_pipeline_limits.setText(
            ", ".join(limit_parts) if limit_parts else "—"
        )

    def _refresh_automation_context(self) -> None:
        # Контекстная панель основана на статических пресетах, динамических данных нет
        return

    def _refresh_content_context(self) -> None:
        if not hasattr(self, "lbl_context_content_profile"):
            return
        profile_label = self._prompt_profile_label(self._current_prompt_profile_key)
        self.lbl_context_content_profile.setText(profile_label)
        try:
            prompts_path = str(self._prompts_path())
        except Exception:
            prompts_path = "—"
        try:
            image_path = str(self._image_prompts_path())
        except Exception:
            image_path = "—"
        titles_path = self.cfg.get("titles_file", str(TITLES_FILE))
        self.lbl_context_prompts_path.setText(prompts_path)
        self.lbl_context_image_prompts_path.setText(image_path)
        self.lbl_context_titles_path.setText(str(titles_path))

    def _refresh_telegram_context(self) -> None:
        tg_cfg = self.cfg.get("telegram", {}) or {}
        enabled = tg_cfg.get("enabled", False)
        template = tg_cfg.get("last_template") or "—"
        if hasattr(self, "lbl_context_tg_enabled"):
            self.lbl_context_tg_enabled.setText("включены" if enabled else "выключены")
        if hasattr(self, "lbl_context_tg_template"):
            self.lbl_context_tg_template.setText(template)

    def _refresh_autopost_context(self) -> None:
        if hasattr(self, "lbl_context_youtube_queue") and hasattr(self, "lbl_youtube_queue"):
            self.lbl_context_youtube_queue.setText(self.lbl_youtube_queue.text())
        if hasattr(self, "lbl_context_tiktok_queue") and hasattr(self, "lbl_tiktok_queue"):
            self.lbl_context_tiktok_queue.setText(self.lbl_tiktok_queue.text())

    def _refresh_settings_context(self) -> None:
        if hasattr(self, "lbl_context_settings_status") and hasattr(self, "lbl_settings_status"):
            self.lbl_context_settings_status.setText(self.lbl_settings_status.text())
        if hasattr(self, "lbl_context_project_root"):
            self.lbl_context_project_root.setText(self.cfg.get("project_root", str(PROJECT_ROOT)))

    def _refresh_watermark_context(self) -> None:
        wm_cfg = self.cfg.get("watermark_cleaner", {}) or {}
        if hasattr(self, "lbl_context_wmr_source"):
            source = wm_cfg.get("source_dir", self.cfg.get("downloads_dir", str(DL_DIR)))
            if hasattr(self, "ed_wmr_source") and isinstance(self.ed_wmr_source, QtWidgets.QLineEdit):
                source = self.ed_wmr_source.text().strip() or source
            self.lbl_context_wmr_source.setText(source)
        if hasattr(self, "lbl_context_wmr_output"):
            output = wm_cfg.get("output_dir", str(PROJECT_ROOT / "restored"))
            if hasattr(self, "ed_wmr_output") and isinstance(self.ed_wmr_output, QtWidgets.QLineEdit):
                output = self.ed_wmr_output.text().strip() or output
            self.lbl_context_wmr_output.setText(output)
        if hasattr(self, "lbl_context_wmr_template"):
            template = wm_cfg.get("template", str(PROJECT_ROOT / "watermark.png"))
            if hasattr(self, "ed_wmr_template") and isinstance(self.ed_wmr_template, QtWidgets.QLineEdit):
                template = self.ed_wmr_template.text().strip() or template
            self.lbl_context_wmr_template.setText(template)

    def _refresh_watermark_probe_context(self) -> None:
        probe_cfg = self.cfg.get("watermark_probe", {}) or {}
        if hasattr(self, "lbl_context_probe_source"):
            source = probe_cfg.get("source_dir", self.cfg.get("downloads_dir", str(DL_DIR)))
            if hasattr(self, "ed_probe_source") and isinstance(self.ed_probe_source, QtWidgets.QLineEdit):
                source = self.ed_probe_source.text().strip() or source
            self.lbl_context_probe_source.setText(source)
        if hasattr(self, "lbl_context_probe_output"):
            output = probe_cfg.get("output_dir", str(PROJECT_ROOT / "restored"))
            if hasattr(self, "ed_probe_output_dir") and isinstance(self.ed_probe_output_dir, QtWidgets.QLineEdit):
                output = self.ed_probe_output_dir.text().strip() or output
            self.lbl_context_probe_output.setText(output)

    def _refresh_telegram_templates(self) -> None:
        if not hasattr(self, "cmb_tg_templates"):
            return
        self.cmb_tg_templates.blockSignals(True)
        self.cmb_tg_templates.clear()
        self.cmb_tg_templates.addItem("— Без шаблона —", None)
        for idx, template in enumerate(self._telegram_templates):
            name = template.get("name") or f"Шаблон {idx + 1}"
            self.cmb_tg_templates.addItem(name, idx)
        self.cmb_tg_templates.blockSignals(False)
        last = self.cfg.get("telegram", {}).get("last_template", "") or ""
        if last:
            self._select_telegram_template_by_name(last, apply_text=True)
        else:
            self.cmb_tg_templates.setCurrentIndex(0)
            if hasattr(self, "ed_tg_template_name"):
                self.ed_tg_template_name.clear()

    def _select_telegram_template_by_name(self, name: str, apply_text: bool = True) -> None:
        if not hasattr(self, "cmb_tg_templates"):
            return
        target_idx = None
        for idx, template in enumerate(self._telegram_templates):
            if template.get("name") == name:
                target_idx = idx
                break
        if target_idx is None:
            self.cmb_tg_templates.blockSignals(True)
            self.cmb_tg_templates.setCurrentIndex(0)
            self.cmb_tg_templates.blockSignals(False)
            if apply_text and hasattr(self, "ed_tg_template_name"):
                self.ed_tg_template_name.setText(name or "")
            return
        for row in range(self.cmb_tg_templates.count()):
            data = self.cmb_tg_templates.itemData(row)
            if data == target_idx:
                self.cmb_tg_templates.blockSignals(True)
                self.cmb_tg_templates.setCurrentIndex(row)
                self.cmb_tg_templates.blockSignals(False)
                if apply_text and 0 <= target_idx < len(self._telegram_templates):
                    template = self._telegram_templates[target_idx]
                    if hasattr(self, "ed_tg_template_name"):
                        self.ed_tg_template_name.setText(template.get("name", ""))
                    if hasattr(self, "ed_tg_quick_message"):
                        self.ed_tg_quick_message.setPlainText(template.get("text", ""))
                break

    def _on_tg_template_selected(self, index: int) -> None:
        if not hasattr(self, "cmb_tg_templates"):
            return
        data = self.cmb_tg_templates.itemData(index)
        if data is None:
            if hasattr(self, "ed_tg_template_name"):
                self.ed_tg_template_name.clear()
            return
        try:
            template = self._telegram_templates[int(data)]
        except (IndexError, ValueError, TypeError):
            return
        if hasattr(self, "ed_tg_template_name"):
            self.ed_tg_template_name.setText(template.get("name", ""))
        if hasattr(self, "ed_tg_quick_message"):
            self.ed_tg_quick_message.setPlainText(template.get("text", ""))
        tg_cfg = self.cfg.setdefault("telegram", {})
        tg_cfg["last_template"] = template.get("name", "")
        save_cfg(self.cfg)
        self._refresh_telegram_context()

    def _on_tg_template_save(self) -> None:
        name = self.ed_tg_template_name.text().strip() if hasattr(self, "ed_tg_template_name") else ""
        body = self.ed_tg_quick_message.toPlainText().strip() if hasattr(self, "ed_tg_quick_message") else ""
        if not name or not body:
            self.lbl_tg_status.setText("Укажи название и текст для шаблона")
            self.lbl_tg_status.setStyleSheet("QLabel{color:#facc15;}")
            return
        updated = False
        for template in self._telegram_templates:
            if template.get("name") == name:
                template["text"] = body
                updated = True
                break
        if not updated:
            self._telegram_templates.append({"name": name, "text": body})
        tg_cfg = self.cfg.setdefault("telegram", {})
        tg_cfg["templates"] = list(self._telegram_templates)
        tg_cfg["last_template"] = name
        save_cfg(self.cfg)
        self._refresh_telegram_templates()
        self._select_telegram_template_by_name(name, apply_text=False)
        self.lbl_tg_status.setText("Шаблон сохранён")
        self.lbl_tg_status.setStyleSheet("QLabel{color:#34d399;}")
        self._refresh_telegram_context()

    def _on_tg_template_delete(self) -> None:
        name = self.ed_tg_template_name.text().strip() if hasattr(self, "ed_tg_template_name") else ""
        if not name:
            self.lbl_tg_status.setText("Выбери шаблон для удаления")
            self.lbl_tg_status.setStyleSheet("QLabel{color:#facc15;}")
            return
        before = len(self._telegram_templates)
        self._telegram_templates = [tpl for tpl in self._telegram_templates if tpl.get("name") != name]
        if len(self._telegram_templates) == before:
            self.lbl_tg_status.setText("Шаблон не найден")
            self.lbl_tg_status.setStyleSheet("QLabel{color:#facc15;}")
            return
        tg_cfg = self.cfg.setdefault("telegram", {})
        tg_cfg["templates"] = list(self._telegram_templates)
        if tg_cfg.get("last_template") == name:
            tg_cfg["last_template"] = ""
        save_cfg(self.cfg)
        self._refresh_telegram_templates()
        self._select_telegram_template_by_name("", apply_text=False)
        if hasattr(self, "ed_tg_template_name"):
            self.ed_tg_template_name.clear()
        self.lbl_tg_status.setText("Шаблон удалён")
        self.lbl_tg_status.setStyleSheet("QLabel{color:#38bdf8;}")
        self._refresh_telegram_context()

    def _on_tg_delay_changed(self, value: int) -> None:
        tg_cfg = self.cfg.setdefault("telegram", {})
        tg_cfg["quick_delay_minutes"] = int(value)
        self._mark_settings_dirty()

    def _apply_task_preset(self, preset_name: str, steps: Iterable[str]) -> None:
        mapping = {
            "images": getattr(self, "cb_do_images", None),
            "prompts": getattr(self, "cb_do_autogen", None),
            "download": getattr(self, "cb_do_download", None),
            "blur": getattr(self, "cb_do_blur", None),
            "watermark": getattr(self, "cb_do_watermark", None),
            "merge": getattr(self, "cb_do_merge", None),
            "youtube": getattr(self, "cb_do_upload", None),
            "tiktok": getattr(self, "cb_do_tiktok", None),
        }
        selected = {step for step in steps}
        for key, checkbox in mapping.items():
            if not checkbox:
                continue
            checkbox.blockSignals(True)
            checkbox.setChecked(key in selected)
            checkbox.blockSignals(False)
        message = f"Пресет задач: {preset_name}"
        self._append_activity(message, kind="info")
        try:
            self._post_status(message, state="info")
        except Exception:
            pass

    def _on_section_nav_changed(self, row: int):
        item = self.section_nav.item(row) if row >= 0 else None
        if not item:
            return
        key = item.data(QtCore.Qt.ItemDataRole.UserRole)
        if not key:
            return
        idx = self._section_index.get(key)
        if idx is None:
            return
        self.section_stack.setCurrentIndex(idx)
        self._current_section_key = key
        if key == "telegram":
            self._refresh_telegram_history()
        self._update_context_panel_for_section(key)

    def _select_section(self, key: str):
        if not getattr(self, "section_nav", None):
            return
        item = self._section_nav_items.get(key)
        if not item:
            return
        row = self.section_nav.row(item)
        if row < 0:
            return
        self.section_nav.blockSignals(True)
        self.section_nav.setCurrentRow(row)
        self.section_nav.blockSignals(False)
        self._on_section_nav_changed(row)

    def _build_settings_pages(self):
        ch = self.cfg.get("chrome", {})
        yt_cfg = self.cfg.get("youtube", {})

        # --- Пути проекта ---
        page_paths = QtWidgets.QWidget()
        paths_layout = QtWidgets.QVBoxLayout(page_paths)
        paths_layout.setContentsMargins(12, 12, 12, 12)
        paths_layout.setSpacing(8)

        paths_hint = QtWidgets.QLabel(
            "Рабочие папки используются сценариями автогена, блюра и загрузчиков."
            " Убедись, что каталоги существуют или выбери другие через кнопку «…»."
            " Подробности смотри на вкладке «Документация → Каталоги» внутри приложения."
        )
        paths_hint.setWordWrap(True)
        paths_hint.setStyleSheet("QLabel{color:#94a3b8;font-size:11px;}")
        paths_layout.addWidget(paths_hint)

        grid_holder = QtWidgets.QWidget()
        grid_paths = QtWidgets.QGridLayout(grid_holder)
        grid_paths.setContentsMargins(0, 0, 0, 0)
        grid_paths.setColumnStretch(1, 1)
        grid_paths.setVerticalSpacing(4)
        grid_paths.setHorizontalSpacing(10)
        row = 0

        def add_path_row(label_text: str, line_attr: str, browse_attr: str, open_attr: str, value: str, tooltip: str = "Открыть папку"):
            nonlocal row
            label = QtWidgets.QLabel(label_text)
            wrap = QtWidgets.QWidget()
            wrap_layout = QtWidgets.QHBoxLayout(wrap)
            wrap_layout.setContentsMargins(0, 0, 0, 0)
            wrap_layout.setSpacing(4)
            line = QtWidgets.QLineEdit(value)
            setattr(self, line_attr, line)
            browse_btn = QtWidgets.QToolButton()
            browse_btn.setText("…")
            setattr(self, browse_attr, browse_btn)
            open_btn = QtWidgets.QToolButton()
            open_btn.setText("↗")
            open_btn.setToolTip(tooltip)
            setattr(self, open_attr, open_btn)
            wrap_layout.addWidget(line, 1)
            wrap_layout.addWidget(browse_btn)
            wrap_layout.addWidget(open_btn)
            grid_paths.addWidget(label, row, 0)
            grid_paths.addWidget(wrap, row, 1, 1, 2)
            row += 1
            return line

        self.ed_root = add_path_row(
            "Папка проекта:",
            "ed_root",
            "btn_browse_root",
            "btn_open_root_path",
            self.cfg.get("project_root", str(PROJECT_ROOT)),
            "Открыть корень проекта",
        )

        self.ed_downloads = add_path_row(
            "Папка RAW:",
            "ed_downloads",
            "btn_browse_downloads",
            "btn_open_downloads_path",
            self.cfg.get("downloads_dir", str(DL_DIR)),
        )

        self.ed_blurred = add_path_row(
            "Папка BLURRED:",
            "ed_blurred",
            "btn_browse_blurred",
            "btn_open_blurred_path",
            self.cfg.get("blurred_dir", str(BLUR_DIR)),
        )

        self.ed_merged = add_path_row(
            "Папка MERGED:",
            "ed_merged",
            "btn_browse_merged",
            "btn_open_merged_path",
            self.cfg.get("merged_dir", str(MERG_DIR)),
        )

        self.ed_blur_src = add_path_row(
            "Источник BLUR:",
            "ed_blur_src",
            "btn_browse_blur_src",
            "btn_open_blur_src_path",
            self.cfg.get("blur_src_dir", self.cfg.get("downloads_dir", str(DL_DIR))),
        )

        self.ed_merge_src = add_path_row(
            "Источник MERGE:",
            "ed_merge_src",
            "btn_browse_merge_src",
            "btn_open_merge_src_path",
            self.cfg.get("merge_src_dir", self.cfg.get("blurred_dir", str(BLUR_DIR))),
        )

        self.ed_history_path = add_path_row(
            "Файл истории:",
            "ed_history_path",
            "btn_browse_history_path",
            "btn_open_history_path",
            self.cfg.get("history_file", str(HIST_FILE)),
            "Открыть файл истории",
        )

        self.ed_titles_path = add_path_row(
            "Файл названий:",
            "ed_titles_path",
            "btn_browse_titles_path",
            "btn_open_titles_path",
            self.cfg.get("titles_file", str(TITLES_FILE)),
            "Открыть файл titles.txt",
        )

        paths_layout.addWidget(grid_holder)
        paths_layout.addStretch(1)

        self._blur_src_autofollow = _same_path(self.cfg.get("blur_src_dir"), self.cfg.get("downloads_dir"))
        self._merge_src_autofollow = _same_path(self.cfg.get("merge_src_dir"), self.cfg.get("blurred_dir"))
        self._upload_src_autofollow = _same_path(yt_cfg.get("upload_src_dir"), self.cfg.get("merged_dir"))

        self.ed_downloads.textEdited.connect(self._on_downloads_path_edited)
        self.ed_blur_src.textEdited.connect(self._on_blur_src_edited)
        self.ed_blurred.textEdited.connect(self._on_blurred_path_edited)
        self.ed_merge_src.textEdited.connect(self._on_merge_src_edited)
        self.ed_merged.textEdited.connect(self._on_merged_path_edited)
        self.ed_youtube_src.textEdited.connect(self._on_youtube_src_edited)

        self.settings_tabs.addTab(page_paths, "Каталоги")

        # --- Интерфейс ---
        page_ui = QtWidgets.QWidget()
        self.page_ui_settings = page_ui
        ui_layout = QtWidgets.QVBoxLayout(page_ui)
        ui_layout.setContentsMargins(12, 12, 12, 12)
        grp_ui = QtWidgets.QGroupBox("Отображение")
        ui_form = QtWidgets.QFormLayout(grp_ui)
        self.cb_ui_show_activity = QtWidgets.QCheckBox("Показывать историю событий в левой панели")
        self.cb_ui_show_activity.setChecked(bool(self.cfg.get("ui", {}).get("show_activity", True)))
        ui_form.addRow(self.cb_ui_show_activity)

        self.cb_ui_show_context = QtWidgets.QCheckBox("Показывать панель команд справа")
        self.cb_ui_show_context.setChecked(bool(self.cfg.get("ui", {}).get("show_context", True)))
        ui_form.addRow(self.cb_ui_show_context)

        self.cmb_ui_activity_density = QtWidgets.QComboBox()
        self.cmb_ui_activity_density.addItem("Компактная", "compact")
        self.cmb_ui_activity_density.addItem("Стандартная", "cozy")
        density_cur = self.cfg.get("ui", {}).get("activity_density", "compact")
        idx = self.cmb_ui_activity_density.findData(density_cur)
        if idx < 0:
            idx = 0
        self.cmb_ui_activity_density.setCurrentIndex(idx)
        ui_form.addRow("Вид истории событий:", self.cmb_ui_activity_density)

        ui_hint = QtWidgets.QLabel(
            "Историю и правую панель можно быстро скрыть через верхнюю панель — настройки сохранятся автоматически."
        )
        ui_hint.setWordWrap(True)
        ui_hint.setStyleSheet("QLabel{color:#94a3b8;font-size:11px;}")
        ui_form.addRow(ui_hint)
        ui_layout.addWidget(grp_ui)

        grp_commands = QtWidgets.QGroupBox("Пользовательские команды")
        cmd_layout = QtWidgets.QVBoxLayout(grp_commands)
        cmd_layout.setContentsMargins(12, 12, 12, 12)
        cmd_layout.setSpacing(8)
        self.lst_custom_commands = QtWidgets.QListWidget()
        self.lst_custom_commands.setObjectName("customCommandList")
        self.lst_custom_commands.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.lst_custom_commands.setMinimumHeight(160)
        cmd_layout.addWidget(self.lst_custom_commands)

        cmd_buttons = QtWidgets.QHBoxLayout()
        self.btn_custom_add = QtWidgets.QPushButton("Добавить")
        self.btn_custom_edit = QtWidgets.QPushButton("Изменить")
        self.btn_custom_delete = QtWidgets.QPushButton("Удалить")
        self.btn_custom_up = QtWidgets.QToolButton()
        self.btn_custom_up.setObjectName("customCommandMove")
        self.btn_custom_up.setText("⬆️")
        self.btn_custom_up.setToolTip("Переместить вверх")
        self.btn_custom_down = QtWidgets.QToolButton()
        self.btn_custom_down.setObjectName("customCommandMove")
        self.btn_custom_down.setText("⬇️")
        self.btn_custom_down.setToolTip("Переместить вниз")
        for btn in (
            self.btn_custom_add,
            self.btn_custom_edit,
            self.btn_custom_delete,
            self.btn_custom_up,
            self.btn_custom_down,
        ):
            cmd_buttons.addWidget(btn)
        self.btn_custom_edit.setEnabled(False)
        self.btn_custom_delete.setEnabled(False)
        self.btn_custom_up.setEnabled(False)
        self.btn_custom_down.setEnabled(False)
        cmd_buttons.addStretch(1)
        cmd_layout.addLayout(cmd_buttons)
        cmd_hint = QtWidgets.QLabel(
            "Команды появляются в правой панели и в командной палитре. Можно запускать скрипты, открывать папки и т.д."
        )
        cmd_hint.setObjectName("customCommandSubtitle")
        cmd_hint.setWordWrap(True)
        cmd_layout.addWidget(cmd_hint)
        ui_layout.addWidget(grp_commands)
        self._refresh_custom_command_list()
        ui_layout.addStretch(1)
        self.settings_tabs.addTab(page_ui, "Интерфейс")

        # --- Chrome ---
        page_chrome = QtWidgets.QWidget()
        chrome_layout = QtWidgets.QVBoxLayout(page_chrome)
        chrome_form = QtWidgets.QFormLayout()
        self.ed_cdp_port = QtWidgets.QLineEdit(str(ch.get("cdp_port", 9222)))
        self.ed_userdir = QtWidgets.QLineEdit(ch.get("user_data_dir", ""))
        self.ed_chrome_bin = QtWidgets.QLineEdit(ch.get("binary", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"))
        chrome_form.addRow("Chrome CDP порт:", self.ed_cdp_port)
        chrome_form.addRow("Chrome user data dir:", self.ed_userdir)
        chrome_form.addRow("Chrome binary:", self.ed_chrome_bin)
        chrome_layout.addLayout(chrome_form)

        grp_prof = QtWidgets.QGroupBox("Профили Chrome")
        vlp = QtWidgets.QVBoxLayout(grp_prof)
        top = QtWidgets.QHBoxLayout()
        self.lst_profiles = QtWidgets.QListWidget()
        top.addWidget(self.lst_profiles, 1)

        form = QtWidgets.QFormLayout()
        self.ed_prof_name = QtWidgets.QLineEdit()
        self.ed_prof_userdir = QtWidgets.QLineEdit()
        self.ed_prof_directory = QtWidgets.QLineEdit()
        self.sb_prof_port = QtWidgets.QSpinBox()
        self.sb_prof_port.setRange(0, 65535)
        self.sb_prof_port.setSpecialValueText("По умолчанию")
        self.sb_prof_port.setToolTip("0 — использовать общий порт CDP из настроек Chrome")
        self.ed_prof_root = self.ed_prof_userdir
        self.ed_prof_dir = self.ed_prof_directory
        form.addRow("Название:", self.ed_prof_name)
        form.addRow("user_data_dir:", self.ed_prof_userdir)
        form.addRow("profile_directory:", self.ed_prof_directory)
        form.addRow("CDP порт:", self.sb_prof_port)
        btns = QtWidgets.QHBoxLayout()
        self.btn_prof_add = QtWidgets.QPushButton("Добавить/обновить")
        self.btn_prof_del = QtWidgets.QPushButton("Удалить")
        self.btn_prof_set = QtWidgets.QPushButton("Сделать активным")
        self.btn_prof_scan = QtWidgets.QPushButton("Автонайти профили")
        btns.addWidget(self.btn_prof_add)
        btns.addWidget(self.btn_prof_del)
        btns.addWidget(self.btn_prof_set)
        btns.addWidget(self.btn_prof_scan)
        form.addRow(btns)
        top.addLayout(form, 2)
        vlp.addLayout(top)
        footer = QtWidgets.QHBoxLayout()
        footer.addWidget(QtWidgets.QLabel("Активный профиль:"))
        self.lbl_prof_active = QtWidgets.QLabel("—")
        footer.addWidget(self.lbl_prof_active)
        footer.addStretch(1)
        vlp.addLayout(footer)
        chrome_layout.addWidget(grp_prof)

        self.settings_tabs.addTab(page_chrome, "Chrome")

        # --- FFmpeg ---
        ff = self.cfg.get("ffmpeg", {})
        page_ff = QtWidgets.QWidget()
        ff_layout = QtWidgets.QVBoxLayout(page_ff)
        ff_form = QtWidgets.QFormLayout()
        self.ed_ff_bin = QtWidgets.QLineEdit(ff.get("binary", "ffmpeg"))
        self.ed_post = QtWidgets.QLineEdit(ff.get("post_chain", "boxblur=1:1,noise=alls=2:allf=t,unsharp=3:3:0.5:3:3:0.0"))
        self.cmb_vcodec = QtWidgets.QComboBox()
        self.cmb_vcodec.addItems(["auto_hw", "libx264", "copy"])
        self.cmb_vcodec.setCurrentText(ff.get("vcodec", "auto_hw"))
        self.ed_crf = QtWidgets.QSpinBox(); self.ed_crf.setRange(0, 51); self.ed_crf.setValue(int(ff.get("crf", 18)))
        self.cmb_preset = QtWidgets.QComboBox(); self.cmb_preset.addItems(["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow", "placebo"])
        self.cmb_preset.setCurrentText(ff.get("preset", "veryfast"))
        self.cmb_format = QtWidgets.QComboBox(); self.cmb_format.addItems(["mp4", "mov", "webm"]); self.cmb_format.setCurrentText(ff.get("format", "mp4"))
        self.cb_copy_audio = QtWidgets.QCheckBox("Копировать аудио"); self.cb_copy_audio.setChecked(bool(ff.get("copy_audio", True)))
        self.sb_blur_threads = QtWidgets.QSpinBox(); self.sb_blur_threads.setRange(1, 8); self.sb_blur_threads.setValue(int(ff.get("blur_threads", 2)))
        ff_form.addRow("ffmpeg:", self.ed_ff_bin)
        ff_form.addRow("POST (цепочка фильтров):", self.ed_post)
        ff_form.addRow("vcodec:", self.cmb_vcodec)
        ff_form.addRow("CRF:", self.ed_crf)
        ff_form.addRow("preset:", self.cmb_preset)
        ff_form.addRow("format:", self.cmb_format)
        ff_form.addRow("Потоки BLUR:", self.sb_blur_threads)
        ff_form.addRow("", self.cb_copy_audio)
        auto_cfg = ff.get("auto_watermark", {}) or {}
        grp_auto = QtWidgets.QGroupBox("Автодетект водяного знака")
        auto_form = QtWidgets.QFormLayout(grp_auto)
        auto_form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)
        self.cb_aw_enabled = QtWidgets.QCheckBox("Включить автодетект")
        self.cb_aw_enabled.setChecked(bool(auto_cfg.get("enabled")))
        auto_form.addRow("Автодетект:", self.cb_aw_enabled)
        aw_template_wrap = QtWidgets.QWidget()
        aw_template_layout = QtWidgets.QHBoxLayout(aw_template_wrap)
        aw_template_layout.setContentsMargins(0, 0, 0, 0)
        aw_template_layout.setSpacing(4)
        self.ed_aw_template = QtWidgets.QLineEdit(auto_cfg.get("template", ""))
        self.btn_aw_template = QtWidgets.QToolButton(); self.btn_aw_template.setText("…")
        aw_template_layout.addWidget(self.ed_aw_template, 1)
        aw_template_layout.addWidget(self.btn_aw_template)
        auto_form.addRow("Шаблон:", aw_template_wrap)
        self.dsb_aw_threshold = QtWidgets.QDoubleSpinBox(); self.dsb_aw_threshold.setRange(0.0, 1.0); self.dsb_aw_threshold.setSingleStep(0.01); self.dsb_aw_threshold.setDecimals(3); self.dsb_aw_threshold.setValue(float(auto_cfg.get("threshold", 0.75) or 0.75))
        auto_form.addRow("Порог совпадения:", self.dsb_aw_threshold)
        self.sb_aw_frames = QtWidgets.QSpinBox(); self.sb_aw_frames.setRange(1, 120); self.sb_aw_frames.setValue(int(auto_cfg.get("frames", 5) or 5))
        auto_form.addRow("Кадров для анализа:", self.sb_aw_frames)
        self.sb_aw_downscale = QtWidgets.QSpinBox(); self.sb_aw_downscale.setRange(0, 4096); self.sb_aw_downscale.setSpecialValueText("без изменений"); self.sb_aw_downscale.setSuffix(" px"); self.sb_aw_downscale.setValue(int(auto_cfg.get("downscale", 0) or 0))
        auto_form.addRow("Макс. ширина кадра:", self.sb_aw_downscale)
        self.sb_aw_bbox_pad = QtWidgets.QSpinBox()
        self.sb_aw_bbox_pad.setRange(0, 512)
        self.sb_aw_bbox_pad.setValue(int(auto_cfg.get("bbox_padding", 12) or 0))
        auto_form.addRow("Запас по краям (px):", self.sb_aw_bbox_pad)
        self.dsb_aw_bbox_pct = QtWidgets.QDoubleSpinBox()
        self.dsb_aw_bbox_pct.setRange(0.0, 100.0)
        self.dsb_aw_bbox_pct.setSingleStep(1.0)
        self.dsb_aw_bbox_pct.setDecimals(1)
        self.dsb_aw_bbox_pct.setSuffix(" %")
        try:
            bbox_pct_val = float(auto_cfg.get("bbox_padding_pct", 0.15) or 0.0) * 100.0
        except Exception:
            bbox_pct_val = 0.0
        self.dsb_aw_bbox_pct.setValue(max(0.0, min(100.0, bbox_pct_val)))
        auto_form.addRow("Доп. процент ширины:", self.dsb_aw_bbox_pct)
        self.sb_aw_bbox_min = QtWidgets.QSpinBox()
        self.sb_aw_bbox_min.setRange(2, 1920)
        self.sb_aw_bbox_min.setSingleStep(2)
        self.sb_aw_bbox_min.setValue(int(auto_cfg.get("bbox_min_size", 48) or 2))
        auto_form.addRow("Мин. размер зоны (px):", self.sb_aw_bbox_min)
        self.cmb_active_preset = QtWidgets.QComboBox()
        ff_form.addRow("Активный пресет:", self.cmb_active_preset)
        ff_layout.addLayout(ff_form)
        ff_layout.addWidget(grp_auto)

        preset_btns = QtWidgets.QHBoxLayout()
        self.btn_preset_add = QtWidgets.QPushButton("Добавить пресет")
        self.btn_preset_delete = QtWidgets.QPushButton("Удалить пресет")
        self.btn_preset_preview = QtWidgets.QPushButton("Предпросмотр и разметка…")
        preset_btns.addWidget(self.btn_preset_add)
        preset_btns.addWidget(self.btn_preset_delete)
        preset_btns.addWidget(self.btn_preset_preview)
        preset_btns.addStretch(1)
        ff_layout.addLayout(preset_btns)

        self.tab_presets = QtWidgets.QTabWidget()
        ff_layout.addWidget(self.tab_presets, 1)

        self.settings_tabs.addTab(page_ff, "FFmpeg")

        # --- YouTube дефолты ---
        page_yt = QtWidgets.QWidget()
        yt_layout = QtWidgets.QVBoxLayout(page_yt)
        yt_layout.setContentsMargins(12, 12, 12, 12)
        yt_layout.setSpacing(8)

        yt_intro = QtWidgets.QLabel(
            "Укажи значения по умолчанию для очередей YouTube. Подробные шаги есть во вкладке «Документация → Автопостинг YouTube»."
        )
        yt_intro.setWordWrap(True)
        yt_intro.setStyleSheet("QLabel{color:#94a3b8;font-size:11px;}")
        yt_layout.addWidget(yt_intro)

        grp_yt = QtWidgets.QGroupBox("Параметры очереди по умолчанию")
        grid_yt = QtWidgets.QGridLayout(grp_yt)
        grid_yt.setColumnStretch(1, 1)
        grid_yt.setHorizontalSpacing(8)
        grid_yt.setVerticalSpacing(6)

        self.sb_youtube_default_delay = QtWidgets.QSpinBox()
        self.sb_youtube_default_delay.setRange(0, 7 * 24 * 60)
        self.sb_youtube_default_delay.setValue(int(yt_cfg.get("schedule_minutes_from_now", 60)))
        grid_yt.addWidget(QtWidgets.QLabel("Отложить по умолчанию (мин):"), 0, 0)
        grid_yt.addWidget(self.sb_youtube_default_delay, 0, 1)

        self.cb_youtube_default_draft = QtWidgets.QCheckBox("По умолчанию только приватный черновик")
        self.cb_youtube_default_draft.setChecked(bool(yt_cfg.get("draft_only", False)))
        grid_yt.addWidget(self.cb_youtube_default_draft, 1, 0, 1, 2)

        archive_wrap = QtWidgets.QWidget()
        archive_l = QtWidgets.QHBoxLayout(archive_wrap)
        archive_l.setContentsMargins(0, 0, 0, 0)
        archive_l.setSpacing(4)
        self.ed_youtube_archive = QtWidgets.QLineEdit(yt_cfg.get("archive_dir", str(PROJECT_ROOT / "uploaded")))
        self.btn_youtube_archive_browse = QtWidgets.QPushButton("…")
        self.btn_youtube_archive_open = QtWidgets.QToolButton()
        self.btn_youtube_archive_open.setText("↗")
        self.btn_youtube_archive_open.setToolTip("Открыть папку архива YouTube")
        archive_l.addWidget(self.ed_youtube_archive, 1)
        archive_l.addWidget(self.btn_youtube_archive_browse)
        archive_l.addWidget(self.btn_youtube_archive_open)
        grid_yt.addWidget(QtWidgets.QLabel("Архив загруженных:"), 2, 0)
        grid_yt.addWidget(archive_wrap, 2, 1)

        grid_yt.addWidget(QtWidgets.QLabel("Интервал для пакетов (мин):"), 3, 0)
        self.sb_youtube_interval_default = QtWidgets.QSpinBox()
        self.sb_youtube_interval_default.setRange(0, 7 * 24 * 60)
        self.sb_youtube_interval_default.setValue(int(yt_cfg.get("batch_step_minutes", 60)))
        grid_yt.addWidget(self.sb_youtube_interval_default, 3, 1)

        grid_yt.addWidget(QtWidgets.QLabel("Ограничение пакета (0 = все):"), 4, 0)
        self.sb_youtube_limit_default = QtWidgets.QSpinBox()
        self.sb_youtube_limit_default.setRange(0, 999)
        self.sb_youtube_limit_default.setValue(int(yt_cfg.get("batch_limit", 0)))
        grid_yt.addWidget(self.sb_youtube_limit_default, 4, 1)

        yt_layout.addWidget(grp_yt)
        yt_layout.addStretch(1)

        self.settings_tabs.addTab(page_yt, "YouTube")

        page_tt = QtWidgets.QWidget()
        tt_layout = QtWidgets.QVBoxLayout(page_tt)
        tt_layout.setContentsMargins(12, 12, 12, 12)
        tt_layout.setSpacing(8)
        tk_defaults = self.cfg.get("tiktok", {}) or {}

        tt_intro = QtWidgets.QLabel(
            "Эти параметры используются при автопостинге TikTok. Дополнительные пояснения смотри во вкладке "
            "«Документация → Автопостинг TikTok»."
        )
        tt_intro.setWordWrap(True)
        tt_intro.setStyleSheet("QLabel{color:#94a3b8;font-size:11px;}")
        tt_layout.addWidget(tt_intro)

        grp_tt = QtWidgets.QGroupBox("Параметры очереди по умолчанию")
        grid_tt = QtWidgets.QGridLayout(grp_tt)
        grid_tt.setColumnStretch(1, 1)
        grid_tt.setHorizontalSpacing(8)
        grid_tt.setVerticalSpacing(6)

        self.sb_tiktok_default_delay = QtWidgets.QSpinBox()
        self.sb_tiktok_default_delay.setRange(0, 7 * 24 * 60)
        self.sb_tiktok_default_delay.setValue(int(tk_defaults.get("schedule_minutes_from_now", 0)))
        grid_tt.addWidget(QtWidgets.QLabel("Отложить по умолчанию (мин):"), 0, 0)
        grid_tt.addWidget(self.sb_tiktok_default_delay, 0, 1)

        self.cb_tiktok_default_draft = QtWidgets.QCheckBox("По умолчанию только черновики")
        self.cb_tiktok_default_draft.setChecked(bool(tk_defaults.get("draft_only", False)))
        grid_tt.addWidget(self.cb_tiktok_default_draft, 1, 0, 1, 2)

        archive_tt_wrap = QtWidgets.QWidget()
        archive_tt_layout = QtWidgets.QHBoxLayout(archive_tt_wrap)
        archive_tt_layout.setContentsMargins(0, 0, 0, 0)
        archive_tt_layout.setSpacing(4)
        self.ed_tiktok_archive = QtWidgets.QLineEdit(tk_defaults.get("archive_dir", str(PROJECT_ROOT / "uploaded_tiktok")))
        self.btn_tiktok_archive_browse = QtWidgets.QPushButton("…")
        self.btn_tiktok_archive_open = QtWidgets.QToolButton()
        self.btn_tiktok_archive_open.setText("↗")
        self.btn_tiktok_archive_open.setToolTip("Открыть архив TikTok")
        archive_tt_layout.addWidget(self.ed_tiktok_archive, 1)
        archive_tt_layout.addWidget(self.btn_tiktok_archive_browse)
        archive_tt_layout.addWidget(self.btn_tiktok_archive_open)
        grid_tt.addWidget(QtWidgets.QLabel("Архив загруженных:"), 2, 0)
        grid_tt.addWidget(archive_tt_wrap, 2, 1)

        grid_tt.addWidget(QtWidgets.QLabel("Интервал для пакетов (мин):"), 3, 0)
        self.sb_tiktok_interval_default = QtWidgets.QSpinBox()
        self.sb_tiktok_interval_default.setRange(0, 7 * 24 * 60)
        self.sb_tiktok_interval_default.setValue(int(tk_defaults.get("batch_step_minutes", 60)))
        grid_tt.addWidget(self.sb_tiktok_interval_default, 3, 1)

        grid_tt.addWidget(QtWidgets.QLabel("Ограничение пакета (0 = все):"), 4, 0)
        self.sb_tiktok_limit_default = QtWidgets.QSpinBox()
        self.sb_tiktok_limit_default.setRange(0, 999)
        self.sb_tiktok_limit_default.setValue(int(tk_defaults.get("batch_limit", 0)))
        grid_tt.addWidget(self.sb_tiktok_limit_default, 4, 1)

        workflow_tt_wrap = QtWidgets.QWidget()
        workflow_tt_layout = QtWidgets.QHBoxLayout(workflow_tt_wrap)
        workflow_tt_layout.setContentsMargins(0, 0, 0, 0)
        workflow_tt_layout.setSpacing(4)
        self.ed_tiktok_workflow_settings = QtWidgets.QLineEdit(tk_defaults.get("github_workflow", ".github/workflows/tiktok-upload.yml"))
        self.ed_tiktok_ref_settings = QtWidgets.QLineEdit(tk_defaults.get("github_ref", "main"))
        workflow_tt_layout.addWidget(self.ed_tiktok_workflow_settings, 1)
        workflow_tt_layout.addWidget(self.ed_tiktok_ref_settings, 1)
        grid_tt.addWidget(QtWidgets.QLabel("Workflow / Branch:"), 5, 0)
        grid_tt.addWidget(workflow_tt_wrap, 5, 1)

        tt_layout.addWidget(grp_tt)
        tt_layout.addStretch(1)

        self.settings_tabs.addTab(page_tt, "TikTok")

        # --- Maintenance ---
        maint_cfg = self.cfg.get("maintenance", {}) or {}
        retention_cfg = maint_cfg.get("retention_days", {}) or {}
        page_maint = QtWidgets.QWidget()
        maint_layout = QtWidgets.QVBoxLayout(page_maint)
        maint_hint = QtWidgets.QLabel(
            "Укажи, сколько дней хранить файлы в рабочих папках. 0 — ничего не удалять."
        )
        maint_hint.setWordWrap(True)
        maint_hint.setStyleSheet("QLabel{color:#94a3b8;font-size:11px;}")
        maint_layout.addWidget(maint_hint)

        grid_maint = QtWidgets.QGridLayout()
        grid_maint.setColumnStretch(1, 1)
        self.sb_maint_downloads = QtWidgets.QSpinBox()
        self.sb_maint_downloads.setRange(0, 365)
        self.sb_maint_downloads.setValue(int(retention_cfg.get("downloads", 7)))
        grid_maint.addWidget(QtWidgets.QLabel("RAW (downloads):"), 0, 0)
        grid_maint.addWidget(self.sb_maint_downloads, 0, 1)

        self.sb_maint_blurred = QtWidgets.QSpinBox()
        self.sb_maint_blurred.setRange(0, 365)
        self.sb_maint_blurred.setValue(int(retention_cfg.get("blurred", 14)))
        grid_maint.addWidget(QtWidgets.QLabel("BLURRED:"), 1, 0)
        grid_maint.addWidget(self.sb_maint_blurred, 1, 1)

        self.sb_maint_merged = QtWidgets.QSpinBox()
        self.sb_maint_merged.setRange(0, 365)
        self.sb_maint_merged.setValue(int(retention_cfg.get("merged", 30)))
        grid_maint.addWidget(QtWidgets.QLabel("MERGED:"), 2, 0)
        grid_maint.addWidget(self.sb_maint_merged, 2, 1)

        maint_layout.addLayout(grid_maint)

        self.cb_maintenance_auto = QtWidgets.QCheckBox("Очищать автоматически при запуске")
        self.cb_maintenance_auto.setChecked(bool(maint_cfg.get("auto_cleanup_on_start", False)))
        maint_layout.addWidget(self.cb_maintenance_auto)

        maint_buttons = QtWidgets.QHBoxLayout()
        self.btn_env_check = QtWidgets.QPushButton("Проверка окружения")
        maint_buttons.addWidget(self.btn_env_check)
        self.btn_update_check = QtWidgets.QPushButton("Проверить обновления")
        maint_buttons.addWidget(self.btn_update_check)
        self.btn_update_pull = QtWidgets.QPushButton("Обновить из GitHub")
        maint_buttons.addWidget(self.btn_update_pull)
        self.btn_maintenance_sizes = QtWidgets.QPushButton("Размеры папок")
        maint_buttons.addWidget(self.btn_maintenance_sizes)
        maint_buttons.addStretch(1)
        self.btn_maintenance_cleanup = QtWidgets.QPushButton("Очистить сейчас")
        maint_buttons.addWidget(self.btn_maintenance_cleanup)
        maint_layout.addLayout(maint_buttons)
        maint_layout.addStretch(1)

        self.settings_tabs.addTab(page_maint, "Обслуживание")

        # --- Автоген ---
        page_auto = QtWidgets.QWidget()
        auto_layout = QtWidgets.QVBoxLayout(page_auto)
        grp_auto = QtWidgets.QGroupBox("Автоген — паузы и лимиты (workers/autogen/config.yaml)")
        fa = QtWidgets.QFormLayout(grp_auto)
        self.sb_auto_success_every = QtWidgets.QSpinBox(); self.sb_auto_success_every.setRange(1, 999); self.sb_auto_success_every.setValue(2)
        self.sb_auto_success_pause = QtWidgets.QSpinBox(); self.sb_auto_success_pause.setRange(0, 3600); self.sb_auto_success_pause.setValue(180)
        self.btn_save_autogen_cfg = QtWidgets.QPushButton("Сохранить автоген конфиг")
        fa.addRow("Пауза после каждых N успешных:", self.sb_auto_success_every)
        fa.addRow("Длительность паузы, сек:", self.sb_auto_success_pause)
        fa.addRow(self.btn_save_autogen_cfg)
        auto_layout.addWidget(grp_auto)

        auto_layout.addStretch(1)
        self.settings_tabs.addTab(page_auto, "Автоген")

        # --- Google AI Studio ---
        page_genai = QtWidgets.QWidget()
        genai_layout = QtWidgets.QVBoxLayout(page_genai)
        genai_layout.setContentsMargins(12, 12, 12, 12)
        genai_layout.setSpacing(12)

        genai_cfg = self.cfg.get("google_genai", {}) or {}

        self.tabs_genai = QtWidgets.QTabWidget()
        genai_layout.addWidget(self.tabs_genai)

        tab_genai_main = QtWidgets.QWidget()
        fg_main = QtWidgets.QFormLayout(tab_genai_main)
        fg_main.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.cb_genai_enabled = QtWidgets.QCheckBox("Включить генерацию изображений перед отправкой промпта")
        self.cb_genai_enabled.setChecked(bool(genai_cfg.get("enabled", False)))
        fg_main.addRow(self.cb_genai_enabled)

        self.cb_genai_attach = QtWidgets.QCheckBox("Прикреплять сгенерированные изображения к заявке в Sora")
        self.cb_genai_attach.setChecked(bool(genai_cfg.get("attach_to_sora", True)))
        fg_main.addRow(self.cb_genai_attach)

        self.ed_genai_api_key = QtWidgets.QLineEdit(genai_cfg.get("api_key", ""))
        self.ed_genai_api_key.setPlaceholderText("AIza...")
        self.ed_genai_api_key.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        fg_main.addRow("API ключ:", self.ed_genai_api_key)

        self.cmb_genai_model = QtWidgets.QComboBox()
        self.cmb_genai_model.setEditable(True)
        self.cmb_genai_model.setInsertPolicy(QtWidgets.QComboBox.InsertPolicy.NoInsert)
        known_models = [
            "models/imagen-4.0-generate-001",
            "models/imagen-3.0-generate-001",
            "models/imagen-3.0-generate-002",
            "models/imagegeneration@006",
        ]
        for model_name in known_models:
            self.cmb_genai_model.addItem(model_name)
        current_model = genai_cfg.get("model", known_models[0] if known_models else "models/imagen-4.0-generate-001")
        if current_model and self.cmb_genai_model.findText(current_model) < 0:
            self.cmb_genai_model.addItem(current_model)
        self.cmb_genai_model.setCurrentText(current_model)
        fg_main.addRow("Модель:", self.cmb_genai_model)

        self.cmb_genai_person = QtWidgets.QComboBox()
        self.cmb_genai_person.setEditable(True)
        self.cmb_genai_person.setInsertPolicy(QtWidgets.QComboBox.InsertPolicy.NoInsert)
        self.cmb_genai_person.addItem("По умолчанию (без явного запрета)", "")
        self.cmb_genai_person.addItem("ALLOW_ALL (устар.)", "ALLOW_ALL")
        self.cmb_genai_person.addItem("BLOCK_ALL (устар.)", "BLOCK_ALL")
        person_val = str(genai_cfg.get("person_generation", "") or "")
        idx_person = self.cmb_genai_person.findData(person_val)
        if idx_person < 0:
            label = person_val or ""
            if label:
                self.cmb_genai_person.addItem(label, person_val)
                idx_person = self.cmb_genai_person.count() - 1
            else:
                idx_person = 0
        self.cmb_genai_person.setCurrentIndex(idx_person)
        self.cmb_genai_person.lineEdit().setPlaceholderText("оставь пустым, чтобы следовать политике модели")
        fg_main.addRow("Генерация людей:", self.cmb_genai_person)

        self.ed_genai_aspect = QtWidgets.QLineEdit(str(genai_cfg.get("aspect_ratio", "1:1")))
        fg_main.addRow("Соотношение сторон:", self.ed_genai_aspect)

        self.ed_genai_size = QtWidgets.QLineEdit(str(genai_cfg.get("image_size", "1K")))
        fg_main.addRow("Размер:", self.ed_genai_size)

        self.ed_genai_mime = QtWidgets.QLineEdit(str(genai_cfg.get("output_mime_type", "image/jpeg")))
        fg_main.addRow("MIME-тип:", self.ed_genai_mime)

        self.sb_genai_images = QtWidgets.QSpinBox()
        self.sb_genai_images.setRange(1, 8)
        self.sb_genai_images.setValue(int(genai_cfg.get("number_of_images", 1) or 1))
        fg_main.addRow("Картинок на промпт:", self.sb_genai_images)

        self.sb_genai_rpm = QtWidgets.QSpinBox()
        self.sb_genai_rpm.setRange(0, 120)
        self.sb_genai_rpm.setSpecialValueText("без ограничений")
        self.sb_genai_rpm.setValue(int(genai_cfg.get("rate_limit_per_minute", 0) or 0))
        fg_main.addRow("Лимит запросов в минуту:", self.sb_genai_rpm)

        self.sb_genai_retries = QtWidgets.QSpinBox()
        self.sb_genai_retries.setRange(0, 10)
        self.sb_genai_retries.setValue(int(genai_cfg.get("max_retries", 3) or 0))
        fg_main.addRow("Повторов при ошибке:", self.sb_genai_retries)

        output_dir = genai_cfg.get("output_dir", str(IMAGES_DIR))
        self.ed_genai_output_dir = QtWidgets.QLineEdit(str(output_dir))
        self.btn_genai_output_browse = QtWidgets.QPushButton("…")
        self.btn_genai_output_open = QtWidgets.QToolButton()
        self.btn_genai_output_open.setText("↗")
        self.btn_genai_output_open.setToolTip("Открыть папку вывода")
        row_widget = QtWidgets.QWidget()
        row_layout = QtWidgets.QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(6)
        row_layout.addWidget(self.ed_genai_output_dir, 1)
        row_layout.addWidget(self.btn_genai_output_browse, 0)
        row_layout.addWidget(self.btn_genai_output_open, 0)
        fg_main.addRow("Папка изображений:", row_widget)

        manifest_hint = QtWidgets.QLabel(
            "Manifest используется для повторного сопоставления промптов и файлов."
            " При необходимости его путь можно изменить вручную в конфиге."
        )
        manifest_hint.setWordWrap(True)
        manifest_hint.setStyleSheet("QLabel{color:#94a3b8;font-size:11px;}")
        fg_main.addRow(manifest_hint)

        self.tabs_genai.addTab(tab_genai_main, "Основные")

        tab_genai_style = QtWidgets.QWidget()
        fg_style = QtWidgets.QFormLayout(tab_genai_style)
        fg_style.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.ed_genai_seeds = QtWidgets.QLineEdit(str(genai_cfg.get("seeds", "")))
        self.ed_genai_seeds.setPlaceholderText("Например: 12345, 67890")
        fg_style.addRow("Сиды (через запятую):", self.ed_genai_seeds)

        self.cb_genai_consistent = QtWidgets.QCheckBox("consistent character design")
        self.cb_genai_consistent.setChecked(bool(genai_cfg.get("consistent_character_design", False)))
        fg_style.addRow(self.cb_genai_consistent)

        self.ed_genai_lens = QtWidgets.QLineEdit(str(genai_cfg.get("lens_type", "")))
        self.ed_genai_lens.setPlaceholderText("например, 35mm cinematic")
        fg_style.addRow("Тип объектива:", self.ed_genai_lens)

        self.ed_genai_palette = QtWidgets.QLineEdit(str(genai_cfg.get("color_palette", "")))
        self.ed_genai_palette.setPlaceholderText("например, cool blue and golden tones")
        fg_style.addRow("Цветовая палитра:", self.ed_genai_palette)

        self.ed_genai_style = QtWidgets.QLineEdit(str(genai_cfg.get("style", "")))
        self.ed_genai_style.setPlaceholderText("например, ultra-realistic fantasy film")
        fg_style.addRow("Стиль:", self.ed_genai_style)

        self.te_genai_reference = QtWidgets.QPlainTextEdit()
        self.te_genai_reference.setPlaceholderText("Дополнительные подсказки и референсы для промпта")
        self.te_genai_reference.setPlainText(str(genai_cfg.get("reference_prompt", "")))
        self.te_genai_reference.setTabChangesFocus(True)
        self.te_genai_reference.setMaximumHeight(120)
        fg_style.addRow("Доп. подсказка:", self.te_genai_reference)

        style_hint = QtWidgets.QLabel(
            "Эти поля автоматически добавляются к текстовому промпту, сохраняя единый стиль изображений."
        )
        style_hint.setWordWrap(True)
        style_hint.setStyleSheet("QLabel{color:#94a3b8;font-size:11px;}")
        fg_style.addRow(style_hint)

        self.tabs_genai.addTab(tab_genai_style, "Стиль")

        tab_genai_notify = QtWidgets.QWidget()
        fg_notify = QtWidgets.QFormLayout(tab_genai_notify)
        fg_notify.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.cb_genai_notifications = QtWidgets.QCheckBox("Включить уведомления о суточной квоте")
        self.cb_genai_notifications.setChecked(bool(genai_cfg.get("notifications_enabled", True)))
        fg_notify.addRow(self.cb_genai_notifications)

        self.sb_genai_daily_quota = QtWidgets.QSpinBox()
        self.sb_genai_daily_quota.setRange(0, 10000)
        self.sb_genai_daily_quota.setSpecialValueText("без лимита")
        self.sb_genai_daily_quota.setValue(int(genai_cfg.get("daily_quota", 0) or 0))
        fg_notify.addRow("Квота промптов в сутки:", self.sb_genai_daily_quota)

        self.sb_genai_quota_warning = QtWidgets.QSpinBox()
        self.sb_genai_quota_warning.setRange(0, 1000)
        self.sb_genai_quota_warning.setValue(int(genai_cfg.get("quota_warning_prompts", 5) or 0))
        fg_notify.addRow("Предупреждать за N промптов:", self.sb_genai_quota_warning)

        self.cb_genai_quota_enforce = QtWidgets.QCheckBox("Останавливать генерацию при исчерпании квоты")
        self.cb_genai_quota_enforce.setChecked(bool(genai_cfg.get("quota_enforce", False)))
        fg_notify.addRow(self.cb_genai_quota_enforce)

        usage_default = genai_cfg.get("usage_file") or str(Path(output_dir) / "usage.json")
        self.ed_genai_usage_file = QtWidgets.QLineEdit(str(usage_default))
        self.btn_genai_usage_browse = QtWidgets.QPushButton("…")
        self.btn_genai_usage_open = QtWidgets.QToolButton()
        self.btn_genai_usage_open.setText("↗")
        self.btn_genai_usage_open.setToolTip("Открыть файл статистики квот")
        usage_row = QtWidgets.QWidget()
        usage_layout = QtWidgets.QHBoxLayout(usage_row)
        usage_layout.setContentsMargins(0, 0, 0, 0)
        usage_layout.setSpacing(6)
        usage_layout.addWidget(self.ed_genai_usage_file, 1)
        usage_layout.addWidget(self.btn_genai_usage_browse, 0)
        usage_layout.addWidget(self.btn_genai_usage_open, 0)
        fg_notify.addRow("Файл учёта квоты:", usage_row)

        quota_hint = QtWidgets.QLabel(
            "Уведомления появляются в консоли workers/autogen. Порог предупреждения задаётся отдельно для каждой модели."
        )
        quota_hint.setWordWrap(True)
        quota_hint.setStyleSheet("QLabel{color:#94a3b8;font-size:11px;}")
        fg_notify.addRow(quota_hint)

        self.tabs_genai.addTab(tab_genai_notify, "Уведомления")

        genai_layout.addStretch(1)
        self.settings_tabs.addTab(page_genai, "Генерация картинок")

        self.settings_tabs.addTab(self.tab_history, "История")
        self.settings_tabs.addTab(self.tab_errors, "Ошибки")

        page_docs = QtWidgets.QWidget()
        docs_layout = QtWidgets.QVBoxLayout(page_docs)
        docs_layout.setContentsMargins(8, 8, 8, 8)
        self.txt_readme = QtWidgets.QTextBrowser()
        self.txt_readme.setOpenExternalLinks(True)
        self.txt_readme.setPlaceholderText("README.md не найден")
        docs_layout.addWidget(self.txt_readme, 1)
        docs_btn_row = QtWidgets.QHBoxLayout()
        docs_btn_row.addStretch(1)
        self.btn_reload_readme = QtWidgets.QPushButton("Обновить README")
        docs_btn_row.addWidget(self.btn_reload_readme)
        docs_layout.addLayout(docs_btn_row)
        self.tab_docs = page_docs
        self.idx_settings_docs = self.settings_tabs.addTab(page_docs, "Документация")

        page_sequence = [
            ("Каталоги", page_paths),
            ("Генерация картинок", page_genai),
            ("Автоген", page_auto),
            ("FFmpeg", page_ff),
            ("Chrome", page_chrome),
            ("YouTube", page_yt),
            ("TikTok", page_tt),
            ("Интерфейс", page_ui),
            ("Обслуживание", page_maint),
            ("Ошибки", self.tab_errors),
            ("Документация", page_docs),
            ("История", self.tab_history),
        ]
        tab_bar = self.settings_tabs.tabBar()
        for target, (_, widget) in enumerate(page_sequence):
            idx_current = self.settings_tabs.indexOf(widget)
            if idx_current >= 0 and idx_current != target:
                tab_bar.moveTab(idx_current, target)
        self.idx_settings_docs = self.settings_tabs.indexOf(page_docs)

        self._refresh_path_fields()
        self.cb_ui_show_activity.toggled.connect(self._on_settings_activity_toggle)
    def _refresh_path_fields(self):
        mapping = [
            (self.ed_root, self.cfg.get("project_root", str(PROJECT_ROOT))),
            (self.ed_downloads, self.cfg.get("downloads_dir", str(DL_DIR))),
            (self.ed_blurred, self.cfg.get("blurred_dir", str(BLUR_DIR))),
            (self.ed_merged, self.cfg.get("merged_dir", str(MERG_DIR))),
            (self.ed_blur_src, self.cfg.get("blur_src_dir", self.cfg.get("downloads_dir", str(DL_DIR)))),
            (self.ed_merge_src, self.cfg.get("merge_src_dir", self.cfg.get("blurred_dir", str(BLUR_DIR)))),
            (getattr(self, "ed_history_path", None), self.cfg.get("history_file", str(HIST_FILE))),
            (getattr(self, "ed_titles_path", None), self.cfg.get("titles_file", str(TITLES_FILE))),
            (getattr(self, "ed_tiktok_src", None), self.cfg.get("tiktok", {}).get("upload_src_dir", self.cfg.get("merged_dir", str(MERG_DIR))))
        ]
        if hasattr(self, "ed_genai_output_dir"):
            mapping.append((self.ed_genai_output_dir, self.cfg.get("google_genai", {}).get("output_dir", str(IMAGES_DIR))))
        wm_cfg = self.cfg.get("watermark_cleaner", {}) or {}
        mapping.extend(
            [
                (getattr(self, "ed_wmr_source", None), wm_cfg.get("source_dir", self.cfg.get("downloads_dir", str(DL_DIR)))) ,
                (getattr(self, "ed_wmr_output", None), wm_cfg.get("output_dir", str(PROJECT_ROOT / "restored"))),
                (getattr(self, "ed_wmr_template", None), wm_cfg.get("template", str(PROJECT_ROOT / "watermark.png"))),
            ]
        )
        for line, value in mapping:
            if not isinstance(line, QtWidgets.QLineEdit):
                continue
            line.blockSignals(True)
            line.setText(str(value))
            line.blockSignals(False)

    def _mark_settings_dirty(self, *args):
        self._settings_dirty = True
        self.lbl_settings_status.setStyleSheet("color:#8e44ad;")
        self.lbl_settings_status.setText("Есть несохранённые изменения — автосохранение через пару секунд…")
        if hasattr(self, "_settings_autosave_timer"):
            self._settings_autosave_timer.stop()
            self._settings_autosave_timer.start()
        self._refresh_settings_context()
        self._refresh_watermark_context()

    def _autosave_settings(self):
        if getattr(self, "_settings_dirty", False):
            self._save_settings_clicked(silent=True, from_autosave=True)

    def _register_settings_autosave_sources(self):
        watchers = [
            (self.ed_root, "textEdited"),
            (self.ed_downloads, "textEdited"),
            (self.ed_blurred, "textEdited"),
            (self.ed_merged, "textEdited"),
            (self.ed_blur_src, "textEdited"),
            (self.ed_merge_src, "textEdited"),
            (getattr(self, "ed_history_path", None), "textEdited"),
            (getattr(self, "ed_titles_path", None), "textEdited"),
            (self.sb_max_videos, "valueChanged"),
            (self.cb_ui_show_activity, "toggled"),
            (getattr(self, "cb_ui_show_context", None), "toggled"),
            (self.cmb_ui_activity_density, "currentIndexChanged"),
            (self.ed_cdp_port, "textEdited"),
            (self.ed_userdir, "textEdited"),
            (self.ed_chrome_bin, "textEdited"),
            (self.ed_ff_bin, "textEdited"),
            (self.ed_post, "textEdited"),
            (self.sb_merge_group, "valueChanged"),
            (self.cmb_vcodec, "currentIndexChanged"),
            (self.ed_crf, "valueChanged"),
            (self.cmb_preset, "currentIndexChanged"),
            (self.cmb_format, "currentIndexChanged"),
            (self.cb_copy_audio, "toggled"),
            (self.cb_aw_enabled, "toggled"),
            (self.ed_aw_template, "textEdited"),
            (self.dsb_aw_threshold, "valueChanged"),
            (self.sb_aw_frames, "valueChanged"),
            (self.sb_aw_downscale, "valueChanged"),
            (self.sb_aw_bbox_pad, "valueChanged"),
            (self.dsb_aw_bbox_pct, "valueChanged"),
            (self.sb_aw_bbox_min, "valueChanged"),
            (self.cmb_active_preset, "currentIndexChanged"),
            (self.sb_blur_threads, "valueChanged"),
            (self.sb_youtube_default_delay, "valueChanged"),
            (self.cb_youtube_default_draft, "toggled"),
            (self.ed_youtube_archive, "textEdited"),
            (self.sb_youtube_interval_default, "valueChanged"),
            (self.sb_youtube_limit_default, "valueChanged"),
            (self.cb_tg_enabled, "toggled"),
            (self.ed_tg_token, "textEdited"),
            (self.ed_tg_chat, "textEdited"),
            (self.cb_maintenance_auto, "toggled"),
            (self.sb_maint_downloads, "valueChanged"),
            (self.sb_maint_blurred, "valueChanged"),
            (self.sb_maint_merged, "valueChanged"),
            (self.dt_youtube_publish, "dateTimeChanged"),
            (self.cb_youtube_schedule, "toggled"),
            (self.cb_youtube_draft_only, "toggled"),
            (self.sb_youtube_interval, "valueChanged"),
            (self.sb_youtube_batch_limit, "valueChanged"),
            (self.ed_youtube_src, "textEdited"),
            (self.cb_genai_enabled, "toggled"),
            (self.cb_genai_attach, "toggled"),
            (self.ed_genai_api_key, "textEdited"),
            (getattr(self, "ed_wmr_source", None), "textEdited"),
            (getattr(self, "ed_wmr_output", None), "textEdited"),
            (getattr(self, "ed_wmr_template", None), "textEdited"),
            (getattr(self, "sb_wmr_mask_threshold", None), "valueChanged"),
            (getattr(self, "dsb_wmr_threshold", None), "valueChanged"),
            (getattr(self, "sb_wmr_frames", None), "valueChanged"),
            (getattr(self, "sb_wmr_downscale", None), "valueChanged"),
            (getattr(self, "dsb_wmr_scale_min", None), "valueChanged"),
            (getattr(self, "dsb_wmr_scale_max", None), "valueChanged"),
            (getattr(self, "sb_wmr_scale_steps", None), "valueChanged"),
            (getattr(self, "cb_wmr_full_scan", None), "toggled"),
            (getattr(self, "sb_wmr_padding_px", None), "valueChanged"),
            (getattr(self, "dsb_wmr_padding_pct", None), "valueChanged"),
            (getattr(self, "sb_wmr_min_size", None), "valueChanged"),
            (getattr(self, "sb_wmr_search_span", None), "valueChanged"),
            (getattr(self, "sb_wmr_pool", None), "valueChanged"),
            (getattr(self, "dsb_wmr_max_iou", None), "valueChanged"),
            (getattr(self, "cmb_wmr_blend", None), "currentIndexChanged"),
            (getattr(self, "sb_wmr_inpaint_radius", None), "valueChanged"),
            (getattr(self, "cmb_wmr_inpaint_method", None), "currentIndexChanged"),
            (getattr(self, "ed_probe_source", None), "textEdited"),
            (getattr(self, "ed_probe_output_dir", None), "textEdited"),
            (getattr(self, "ed_probe_video", None), "textEdited"),
            (getattr(self, "sb_probe_x", None), "valueChanged"),
            (getattr(self, "sb_probe_y", None), "valueChanged"),
            (getattr(self, "sb_probe_w", None), "valueChanged"),
            (getattr(self, "sb_probe_h", None), "valueChanged"),
            (getattr(self, "sb_probe_frames", None), "valueChanged"),
            (getattr(self, "sb_probe_brightness", None), "valueChanged"),
            (getattr(self, "dsb_probe_coverage", None), "valueChanged"),
            (getattr(self, "cmb_probe_flip_when", None), "currentIndexChanged"),
            (getattr(self, "cmb_probe_direction", None), "currentIndexChanged"),
            (self.cmb_genai_model, "currentIndexChanged"),
            (self.cmb_genai_model.lineEdit(), "textEdited"),
            (self.cmb_genai_person, "currentIndexChanged"),
            (self.cmb_genai_person.lineEdit(), "textEdited"),
            (self.ed_genai_aspect, "textEdited"),
            (self.ed_genai_size, "textEdited"),
            (self.ed_genai_mime, "textEdited"),
            (self.sb_genai_images, "valueChanged"),
            (self.sb_genai_rpm, "valueChanged"),
            (self.sb_genai_retries, "valueChanged"),
            (self.ed_genai_output_dir, "textEdited"),
            (self.ed_genai_seeds, "textEdited"),
            (self.cb_genai_consistent, "toggled"),
            (self.ed_genai_lens, "textEdited"),
            (self.ed_genai_palette, "textEdited"),
            (self.ed_genai_style, "textEdited"),
            (self.te_genai_reference, "textChanged"),
            (self.cb_genai_notifications, "toggled"),
            (self.sb_genai_daily_quota, "valueChanged"),
            (self.sb_genai_quota_warning, "valueChanged"),
            (self.cb_genai_quota_enforce, "toggled"),
            (self.ed_genai_usage_file, "textEdited"),
        ]
        for widget, signal_name in watchers:
            signal = getattr(widget, signal_name, None)
            if signal:
                signal.connect(self._mark_settings_dirty)

    def _on_settings_tab_changed(self, index: int):
        widget = self.settings_tabs.widget(index) if hasattr(self, "settings_tabs") else None
        if widget is self.tab_history:
            self._reload_history()
        if hasattr(self, "idx_settings_docs") and index == self.idx_settings_docs:
            self._load_readme_preview()

    def _on_downloads_path_edited(self, text: str):
        clean = text.strip()
        if getattr(self, "_blur_src_autofollow", False):
            self._blur_src_autofollow = True
            self.ed_blur_src.blockSignals(True)
            self.ed_blur_src.setText(clean)
            self.ed_blur_src.blockSignals(False)
            self._mark_settings_dirty()

    def _on_blur_src_edited(self, text: str):
        clean = text.strip()
        downloads = self.ed_downloads.text().strip()
        auto = not clean or clean == downloads
        if auto and not getattr(self, "_blur_src_autofollow", False):
            self._blur_src_autofollow = True
            self._on_downloads_path_edited(downloads)
        else:
            self._blur_src_autofollow = auto

    def _on_blurred_path_edited(self, text: str):
        clean = text.strip()
        if getattr(self, "_merge_src_autofollow", False):
            self._merge_src_autofollow = True
            self.ed_merge_src.blockSignals(True)
            self.ed_merge_src.setText(clean)
            self.ed_merge_src.blockSignals(False)
            self._mark_settings_dirty()

    def _on_merge_src_edited(self, text: str):
        clean = text.strip()
        blurred = self.ed_blurred.text().strip()
        auto = not clean or clean == blurred
        if auto and not getattr(self, "_merge_src_autofollow", False):
            self._merge_src_autofollow = True
            self._on_blurred_path_edited(blurred)
        else:
            self._merge_src_autofollow = auto

    def _on_merged_path_edited(self, text: str):
        clean = text.strip()
        if getattr(self, "_upload_src_autofollow", False):
            self._upload_src_autofollow = True
            self.ed_youtube_src.blockSignals(True)
            self.ed_youtube_src.setText(clean)
            self.ed_youtube_src.blockSignals(False)
            self._mark_settings_dirty()

    def _on_youtube_src_edited(self, text: str):
        clean = text.strip()
        merged = self.ed_merged.text().strip()
        auto = not clean or clean == merged
        if auto and not getattr(self, "_upload_src_autofollow", False):
            self._upload_src_autofollow = True
            self._on_merged_path_edited(merged)
        else:
            self._upload_src_autofollow = auto

    @staticmethod
    def _guess_ffprobe(ffmpeg_bin: str) -> str:
        cleaned = (ffmpeg_bin or "").strip().strip('"')
        if not cleaned:
            return "ffprobe.exe" if sys.platform.startswith("win") else "ffprobe"
        ff_path = Path(cleaned)
        suffix = ff_path.suffix if ff_path.suffix else (".exe" if sys.platform.startswith("win") else "")
        candidate = ff_path.with_name(f"ffprobe{suffix}")
        if candidate.exists():
            return str(candidate)
        return "ffprobe.exe" if sys.platform.startswith("win") else "ffprobe"

    def _load_zones_into_ui(self):
        ff = self.cfg.get("ffmpeg", {}) or {}
        presets_obj = ff.get("presets", {}) or {}

        if isinstance(presets_obj, list):
            # поддержка очень старых конфигов
            presets = {}
            for idx, entry in enumerate(presets_obj):
                if not isinstance(entry, dict):
                    continue
                key = entry.get("name") or f"preset_{idx+1}"
                presets[key] = entry
        else:
            presets = dict(presets_obj)

        self._preset_cache = {}
        self._preset_tables = {}
        self.tab_presets.clear()

        if not presets:
            presets = {
                "portrait_9x16": {
                    "zones": [
                        {"x": 30, "y": 105, "w": 157, "h": 62},
                        {"x": 515, "y": 610, "w": 157, "h": 62},
                        {"x": 30, "y": 1110, "w": 157, "h": 62},
                    ]
                }
            }

        canonical: Dict[str, List[Dict[str, int]]] = {}
        changed = False
        for name, body in presets.items():
            raw_list = _as_zone_sequence(body)
            normalized = normalize_zone_list(raw_list)
            if not normalized and raw_list:
                # сохраним исходные значения в таблицу, чтобы пользователь мог поправить вручную
                normalized = []
                for item in raw_list:
                    if isinstance(item, dict):
                        zone = {
                            "x": _coerce_int(item.get("x") or item.get("left") or item.get("start_x") or item.get("sx") or 0) or 0,
                            "y": _coerce_int(item.get("y") or item.get("top") or item.get("start_y") or item.get("sy") or 0) or 0,
                            "w": _coerce_int(item.get("w") or item.get("width") or item.get("right") or item.get("x2")) or 0,
                            "h": _coerce_int(item.get("h") or item.get("height") or item.get("bottom") or item.get("y2")) or 0,
                        }
                        normalized.append(zone)
            if not normalized:
                normalized = [{"x": 0, "y": 0, "w": 0, "h": 0}]
            canonical[name] = [dict(zone) for zone in normalized]
            if raw_list != canonical[name]:
                changed = True
            self._preset_cache[name] = [dict(zone) for zone in normalized]
            self._create_preset_tab(name)

        if changed:
            ff["presets"] = {name: {"zones": zones} for name, zones in canonical.items()}
            save_cfg(self.cfg)

        self.cmb_active_preset.blockSignals(True)
        self.cmb_active_preset.clear()
        for name in self._preset_cache.keys():
            self.cmb_active_preset.addItem(name)
        active = ff.get("active_preset") or next(iter(self._preset_cache.keys()))
        idx = self.cmb_active_preset.findText(active)
        if idx < 0:
            idx = 0
        self.cmb_active_preset.setCurrentIndex(idx)
        self.cmb_active_preset.blockSignals(False)
        self._select_preset_tab(self.cmb_active_preset.currentText())

    def _create_preset_tab(self, name: str):
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)
        table = QtWidgets.QTableWidget(0, 4)
        table.setHorizontalHeaderLabels(["x", "y", "w", "h"])
        header = table.horizontalHeader()
        header.setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        layout.addWidget(table, 1)
        btn_row = QtWidgets.QHBoxLayout()
        btn_add = QtWidgets.QPushButton("Добавить зону")
        btn_remove = QtWidgets.QPushButton("Удалить зону")
        btn_row.addWidget(btn_add)
        btn_row.addWidget(btn_remove)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)
        self.tab_presets.addTab(widget, name)
        self._preset_tables[name] = table
        btn_add.clicked.connect(partial(self._add_zone_to_preset, name))
        btn_remove.clicked.connect(partial(self._remove_zone_from_preset, name))
        table.itemChanged.connect(partial(self._on_preset_zone_changed, name))
        self._populate_preset_table(name)

    def _populate_preset_table(self, name: str):
        table = self._preset_tables.get(name)
        zones = self._preset_cache.get(name, [])
        if not table:
            return
        table.blockSignals(True)
        table.setRowCount(0)
        for zone in zones:
            row = table.rowCount()
            table.insertRow(row)
            for col, key in enumerate(["x", "y", "w", "h"]):
                item = QtWidgets.QTableWidgetItem(str(int(zone.get(key, 0))))
                item.setTextAlignment(int(QtCore.Qt.AlignmentFlag.AlignCenter))
                table.setItem(row, col, item)
        table.blockSignals(False)

    def _select_preset_tab(self, name: str):
        for i in range(self.tab_presets.count()):
            if self.tab_presets.tabText(i) == name:
                self.tab_presets.setCurrentIndex(i)
                break

    def _add_zone_to_preset(self, name: str):
        zones = self._preset_cache.setdefault(name, [])
        zones.append({"x": 0, "y": 0, "w": 0, "h": 0})
        self._populate_preset_table(name)
        self._mark_settings_dirty()

    def _remove_zone_from_preset(self, name: str):
        zones = self._preset_cache.setdefault(name, [])
        table = self._preset_tables.get(name)
        if not table or not zones:
            return
        row = table.currentRow()
        if row < 0 or row >= len(zones):
            row = len(zones) - 1
        if row < 0:
            return
        zones.pop(row)
        if not zones:
            zones.append({"x": 0, "y": 0, "w": 0, "h": 0})
        self._populate_preset_table(name)
        self._mark_settings_dirty()

    def _on_preset_zone_changed(self, name: str, item: QtWidgets.QTableWidgetItem):
        try:
            value = max(0, int(item.text()))
        except ValueError:
            value = 0
        item.setText(str(value))
        zones = self._preset_cache.setdefault(name, [])
        while len(zones) <= item.row():
            zones.append({"x": 0, "y": 0, "w": 0, "h": 0})
        key = ["x", "y", "w", "h"][item.column()]
        zones[item.row()][key] = value
        self._mark_settings_dirty()

    def _load_readme_preview(self, force: bool = False):
        if not hasattr(self, "txt_readme"):
            return
        if self._readme_loaded and not force:
            return

        for path in [APP_DIR / "README.md", PROJECT_ROOT / "README.md"]:
            if path.exists():
                try:
                    text = path.read_text(encoding="utf-8")
                    self.txt_readme.setMarkdown(text)
                except Exception:
                    self.txt_readme.setPlainText(path.read_text(encoding="utf-8", errors="ignore"))
                self.txt_readme.verticalScrollBar().setValue(0)
                if hasattr(self, "lst_activity"):
                    self._append_activity(f"README загружен: {path.name}", kind="info")
                self._readme_loaded = True
                return

        self.txt_readme.setPlainText("README.md не найден в папке приложения")
        if hasattr(self, "lst_activity"):
            self._append_activity("README.md не найден", kind="error")
        self._readme_loaded = True

    def _build_telegram_panel(self) -> QtWidgets.QWidget:
        tg_cfg = self.cfg.get("telegram", {}) or {}
        raw_templates = tg_cfg.get("templates") or []
        self._telegram_templates = [
            {"name": str(t.get("name", "")).strip(), "text": t.get("text", "")}
            for t in raw_templates
            if isinstance(t, dict)
        ]
        root = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(root)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        intro = QtWidgets.QLabel(
            "Настрой уведомления и контролируй отправку сообщений в Telegram прямо из Sora Suite."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("QLabel{color:#94a3b8;font-size:12px;}")
        layout.addWidget(intro)

        cfg_box = QtWidgets.QGroupBox("Параметры уведомлений")
        cfg_form = QtWidgets.QFormLayout(cfg_box)
        cfg_form.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.cb_tg_enabled = QtWidgets.QCheckBox("Включить уведомления")
        self.cb_tg_enabled.setChecked(bool(tg_cfg.get("enabled", False)))
        cfg_form.addRow(self.cb_tg_enabled)
        self.ed_tg_token = QtWidgets.QLineEdit(tg_cfg.get("bot_token", ""))
        self.ed_tg_token.setPlaceholderText("123456:ABCDEF...")
        cfg_form.addRow("Bot token:", self.ed_tg_token)
        self.ed_tg_chat = QtWidgets.QLineEdit(tg_cfg.get("chat_id", ""))
        self.ed_tg_chat.setPlaceholderText("@channel или chat id")
        cfg_form.addRow("Chat ID:", self.ed_tg_chat)
        btn_row = QtWidgets.QHBoxLayout()
        self.btn_tg_test = QtWidgets.QPushButton("Отправить тест")
        btn_row.addWidget(self.btn_tg_test)
        btn_row.addStretch(1)
        cfg_form.addRow(btn_row)
        hint = QtWidgets.QLabel("Уведомления отправляются после ключевых шагов сценария и сервисных операций.")
        hint.setWordWrap(True)
        hint.setStyleSheet("QLabel{color:#94a3b8;font-size:11px;}")
        cfg_form.addRow(hint)
        layout.addWidget(cfg_box)

        quick_box = QtWidgets.QGroupBox("Быстрая отправка")
        quick_layout = QtWidgets.QVBoxLayout(quick_box)
        quick_layout.setSpacing(8)
        self.ed_tg_quick_message = QtWidgets.QPlainTextEdit()
        self.ed_tg_quick_message.setPlaceholderText("Напиши сообщение для команды или канала…")
        self.ed_tg_quick_message.setMaximumBlockCount(200)
        quick_layout.addWidget(self.ed_tg_quick_message)
        schedule_row = QtWidgets.QHBoxLayout()
        schedule_row.addWidget(QtWidgets.QLabel("Отправить через:"))
        self.sb_tg_quick_delay = QtWidgets.QSpinBox()
        self.sb_tg_quick_delay.setRange(0, 1440)
        self.sb_tg_quick_delay.setSuffix(" мин")
        self.sb_tg_quick_delay.setValue(int(tg_cfg.get("quick_delay_minutes", 0)))
        schedule_row.addWidget(self.sb_tg_quick_delay)
        schedule_row.addStretch(1)
        quick_layout.addLayout(schedule_row)
        quick_buttons = QtWidgets.QHBoxLayout()
        self.btn_tg_quick_send = QtWidgets.QPushButton("Отправить сейчас")
        self.btn_tg_quick_clear = QtWidgets.QPushButton("Очистить")
        quick_buttons.addWidget(self.btn_tg_quick_send)
        quick_buttons.addWidget(self.btn_tg_quick_clear)
        quick_buttons.addStretch(1)
        quick_layout.addLayout(quick_buttons)
        self.lbl_tg_status = QtWidgets.QLabel("Готово к отправке")
        self.lbl_tg_status.setStyleSheet("QLabel{color:#94a3b8;}")
        quick_layout.addWidget(self.lbl_tg_status)
        layout.addWidget(quick_box)

        templates_box = QtWidgets.QGroupBox("Шаблоны сообщений")
        templates_form = QtWidgets.QFormLayout(templates_box)
        templates_form.setHorizontalSpacing(8)
        templates_form.setVerticalSpacing(6)
        self.cmb_tg_templates = QtWidgets.QComboBox()
        templates_form.addRow("Выбрать:", self.cmb_tg_templates)
        self.ed_tg_template_name = QtWidgets.QLineEdit()
        templates_form.addRow("Название:", self.ed_tg_template_name)
        template_buttons = QtWidgets.QHBoxLayout()
        self.btn_tg_template_save = QtWidgets.QPushButton("Сохранить")
        self.btn_tg_template_delete = QtWidgets.QPushButton("Удалить")
        template_buttons.addWidget(self.btn_tg_template_save)
        template_buttons.addWidget(self.btn_tg_template_delete)
        template_buttons.addStretch(1)
        templates_form.addRow(template_buttons)
        layout.addWidget(templates_box)

        history_box = QtWidgets.QGroupBox("Недавние сообщения")
        history_layout = QtWidgets.QVBoxLayout(history_box)
        history_layout.setSpacing(8)
        self.lst_tg_history = QtWidgets.QListWidget()
        self.lst_tg_history.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
        self.lst_tg_history.setWordWrap(True)
        self.lst_tg_history.setAlternatingRowColors(False)
        history_layout.addWidget(self.lst_tg_history, 1)
        history_buttons = QtWidgets.QHBoxLayout()
        self.btn_tg_history_refresh = QtWidgets.QPushButton("Обновить")
        self.btn_tg_history_clear = QtWidgets.QPushButton("Очистить журнал")
        history_buttons.addWidget(self.btn_tg_history_refresh)
        history_buttons.addWidget(self.btn_tg_history_clear)
        history_buttons.addStretch(1)
        history_layout.addLayout(history_buttons)
        layout.addWidget(history_box, 1)

        layout.addStretch(1)

        cache = getattr(self, "_telegram_activity_cache", None)
        if cache is None:
            self._telegram_activity_cache = deque(maxlen=200)

        self.btn_tg_quick_send.clicked.connect(self._send_quick_telegram)
        self.btn_tg_quick_clear.clicked.connect(self._clear_quick_telegram_message)
        self.btn_tg_history_refresh.clicked.connect(self._refresh_telegram_history)
        self.btn_tg_history_clear.clicked.connect(self._clear_telegram_history)
        self.cmb_tg_templates.currentIndexChanged.connect(self._on_tg_template_selected)
        self.btn_tg_template_save.clicked.connect(self._on_tg_template_save)
        self.btn_tg_template_delete.clicked.connect(self._on_tg_template_delete)
        self.sb_tg_quick_delay.valueChanged.connect(self._on_tg_delay_changed)

        self._refresh_telegram_templates()
        self._refresh_telegram_context()

        return root

    def _build_pipeline_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QFrame()
        panel.setObjectName("pipelinePanel")
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        header = QtWidgets.QHBoxLayout()
        header.setSpacing(8)
        lbl_title = QtWidgets.QLabel("Пайплайн")
        lbl_title.setObjectName("pipelineTitle")
        lbl_title.setStyleSheet("QLabel#pipelineTitle{font-size:16px;font-weight:600;color:#e2e8f0;}")
        header.addWidget(lbl_title)
        header.addStretch(1)

        chrome_box = QtWidgets.QFrame()
        chrome_layout = QtWidgets.QHBoxLayout(chrome_box)
        chrome_layout.setContentsMargins(0, 0, 0, 0)
        chrome_layout.setSpacing(6)
        chrome_label = QtWidgets.QLabel("🌐 Chrome:")
        chrome_layout.addWidget(chrome_label)
        self.cmb_chrome_profile_top = QtWidgets.QComboBox()
        self.cmb_chrome_profile_top.setObjectName("chromeProfileTop")
        self.cmb_chrome_profile_top.setPlaceholderText("Профиль…")
        self.cmb_chrome_profile_top.setSizeAdjustPolicy(
            QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        self.cmb_chrome_profile_top.setMaximumWidth(200)
        chrome_layout.addWidget(self.cmb_chrome_profile_top)
        self.btn_scan_profiles_top = QtWidgets.QToolButton()
        self.btn_scan_profiles_top.setObjectName("chromeScanBtn")
        self.btn_scan_profiles_top.setText("🔄")
        self.btn_scan_profiles_top.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        chrome_layout.addWidget(self.btn_scan_profiles_top)
        self.btn_open_chrome = QtWidgets.QPushButton("🚀 Chrome")
        self.btn_open_chrome.setObjectName("pipelineChromeBtn")
        self.btn_open_chrome.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        chrome_layout.addWidget(self.btn_open_chrome)
        header.addWidget(chrome_box)
        layout.addLayout(header)

        stages_group = QtWidgets.QGroupBox("Этапы")
        stages_layout = QtWidgets.QGridLayout(stages_group)
        stages_layout.setHorizontalSpacing(12)
        stages_layout.setVerticalSpacing(8)
        self.cb_do_images = QtWidgets.QCheckBox("🖼️ Генерация картинок (Google)")
        self.cb_do_autogen = QtWidgets.QCheckBox("✍️ Вставка промптов")
        self.cb_do_download = QtWidgets.QCheckBox("⬇️ Скачка видео")
        self.cb_do_blur = QtWidgets.QCheckBox("🌫️ Блюр водяного знака")
        self.cb_do_watermark = QtWidgets.QCheckBox("🧼 Замена водяного знака")
        self.cb_do_merge = QtWidgets.QCheckBox("🧵 Склейка лент")
        self.cb_do_upload = QtWidgets.QCheckBox("📤 YouTube загрузка")
        self.cb_do_tiktok = QtWidgets.QCheckBox("🎵 TikTok загрузка")
        stage_boxes = [
            self.cb_do_images,
            self.cb_do_autogen,
            self.cb_do_download,
            self.cb_do_blur,
            self.cb_do_watermark,
            self.cb_do_merge,
            self.cb_do_upload,
            self.cb_do_tiktok,
        ]
        for idx, box in enumerate(stage_boxes):
            box.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
            row = idx // 2
            col = idx % 2
            stages_layout.addWidget(box, row, col)
        for box in stage_boxes:
            box.stateChanged.connect(lambda *_: self._refresh_pipeline_context())
        layout.addWidget(stages_group)

        settings_group = QtWidgets.QGroupBox("Настройки этапов")
        settings_form = QtWidgets.QFormLayout(settings_group)
        settings_form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        settings_form.setHorizontalSpacing(10)
        settings_form.setVerticalSpacing(8)

        dl_row = QtWidgets.QHBoxLayout()
        dl_row.setSpacing(6)
        self.sb_max_videos = QtWidgets.QSpinBox()
        self.sb_max_videos.setRange(0, 10000)
        self.sb_max_videos.setValue(int(self.cfg.get("downloader", {}).get("max_videos", 0)))
        self.sb_max_videos.setSuffix(" видео")
        dl_row.addWidget(self.sb_max_videos)
        self.chk_open_drafts_global = QtWidgets.QCheckBox("Открывать drafts")
        self.chk_open_drafts_global.setChecked(bool(self.cfg.get("downloader", {}).get("open_drafts", True)))
        dl_row.addWidget(self.chk_open_drafts_global)
        self.btn_apply_dl = QtWidgets.QPushButton("Применить")
        self.btn_apply_dl.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        dl_row.addWidget(self.btn_apply_dl)
        dl_row.addStretch(1)
        settings_form.addRow("Лимит скачки:", dl_row)

        merge_row = QtWidgets.QHBoxLayout()
        merge_row.setSpacing(6)
        self.sb_merge_group = QtWidgets.QSpinBox()
        self.sb_merge_group.setRange(1, 1000)
        self.sb_merge_group.setValue(int(self.cfg.get("merge", {}).get("group_size", 3)))
        self.sb_merge_group.setSuffix(" клипа")
        merge_row.addWidget(self.sb_merge_group)
        self.btn_apply_merge = QtWidgets.QPushButton("Применить")
        self.btn_apply_merge.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        merge_row.addWidget(self.btn_apply_merge)
        merge_row.addStretch(1)
        settings_form.addRow("Склейка по:", merge_row)

        layout.addWidget(settings_group)

        run_group = QtWidgets.QGroupBox("Управление")
        run_layout = QtWidgets.QHBoxLayout(run_group)
        run_layout.setSpacing(8)
        self.btn_run_scenario = QtWidgets.QPushButton("▶️ Старт")
        self.btn_run_scenario.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        self.btn_run_autogen_images = QtWidgets.QPushButton("🖼️ Картинки")
        self.btn_run_autogen_images.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        self.btn_run_watermark = QtWidgets.QPushButton("🧼 Водяной знак")
        self.btn_run_watermark.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        self.btn_open_genai_output = QtWidgets.QPushButton("📁 Галерея")
        self.btn_open_genai_output.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        for btn in (
            self.btn_run_scenario,
            self.btn_run_autogen_images,
            self.btn_run_watermark,
            self.btn_open_genai_output,
        ):
            btn.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Expanding,
                QtWidgets.QSizePolicy.Policy.Fixed,
            )
            run_layout.addWidget(btn)
        layout.addWidget(run_group)

        status_group = QtWidgets.QGroupBox("Текущий прогресс")
        status_layout = QtWidgets.QVBoxLayout(status_group)
        status_layout.setSpacing(10)
        self.lbl_status = QtWidgets.QLabel("—")
        self.lbl_status.setObjectName("pipelineStatusLabel")
        self.pb_global = QtWidgets.QProgressBar()
        self.pb_global.setMinimum(0)
        self.pb_global.setMaximum(1)
        self.pb_global.setValue(1)
        self.pb_global.setTextVisible(False)
        self.pb_global.setFixedHeight(8)
        self.pb_global.setStyleSheet(
            "QProgressBar{background:#0f172a;border-radius:4px;}"
            "QProgressBar::chunk{background:#4c6ef5;border-radius:4px;}"
        )
        status_layout.addWidget(self.lbl_status)
        status_layout.addWidget(self.pb_global)
        layout.addWidget(status_group)

        rename_group = QtWidgets.QGroupBox("Переименование файлов")
        rename_layout = QtWidgets.QGridLayout(rename_group)
        rename_layout.setHorizontalSpacing(8)
        rename_layout.setVerticalSpacing(6)
        self.ed_ren_dir = QtWidgets.QLineEdit(self.cfg.get("downloads_dir", str(DL_DIR)))
        self.ed_ren_dir.setClearButtonEnabled(True)
        self.btn_ren_browse = QtWidgets.QPushButton("…")
        self.btn_ren_browse.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        self.rb_ren_from_titles = QtWidgets.QRadioButton("По списку titles.txt")
        self.rb_ren_from_titles.setChecked(True)
        self.rb_ren_sequential = QtWidgets.QRadioButton("Последовательно (1,2,3…)")
        self.ed_ren_prefix = QtWidgets.QLineEdit("")
        self.ed_ren_start = QtWidgets.QSpinBox()
        self.ed_ren_start.setRange(1, 1_000_000)
        self.ed_ren_start.setValue(1)
        self.btn_ren_run = QtWidgets.QPushButton("Переименовать")
        row = 0
        rename_layout.addWidget(QtWidgets.QLabel("Папка:"), row, 0)
        rename_layout.addWidget(self.ed_ren_dir, row, 1)
        rename_layout.addWidget(self.btn_ren_browse, row, 2)
        row += 1
        rename_layout.addWidget(self.rb_ren_from_titles, row, 0, 1, 3)
        row += 1
        rename_layout.addWidget(self.rb_ren_sequential, row, 0, 1, 3)
        row += 1
        rename_layout.addWidget(QtWidgets.QLabel("Префикс:"), row, 0)
        rename_layout.addWidget(self.ed_ren_prefix, row, 1, 1, 2)
        row += 1
        rename_layout.addWidget(QtWidgets.QLabel("Начать с №:"), row, 0)
        rename_layout.addWidget(self.ed_ren_start, row, 1)
        rename_layout.addWidget(self.btn_ren_run, row, 2)
        layout.addWidget(rename_group)

        layout.addStretch(1)
        return panel

    def _build_sessions_tab(self) -> QtWidgets.QWidget:
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        header = QtWidgets.QLabel(
            "Создавай отдельные рабочие пространства для параллельных окон Chrome: каждое хранит свои промпты, логи и порт."
        )
        header.setWordWrap(True)
        header.setStyleSheet("QLabel{color:#94a3b8;font-size:12px;}")
        layout.addWidget(header)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)

        left_panel = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        self.lst_sessions = QtWidgets.QListWidget()
        self.lst_sessions.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.lst_sessions.setAlternatingRowColors(True)
        self.lst_sessions.setUniformItemSizes(True)
        self.lst_sessions.setStyleSheet("QListWidget{border-radius:10px;border:1px solid #22314d;padding:6px;}")
        left_layout.addWidget(self.lst_sessions, 1)

        controls_row = QtWidgets.QHBoxLayout()
        self.btn_session_add = QtWidgets.QPushButton("Добавить")
        self.btn_session_duplicate = QtWidgets.QPushButton("Дублировать")
        self.btn_session_remove = QtWidgets.QPushButton("Удалить")
        for button in (self.btn_session_add, self.btn_session_duplicate, self.btn_session_remove):
            button.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
            controls_row.addWidget(button)
        left_layout.addLayout(controls_row)

        splitter.addWidget(left_panel)

        detail_area = QtWidgets.QScrollArea()
        detail_area.setWidgetResizable(True)
        detail_area.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        splitter.addWidget(detail_area)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

        detail_widget = QtWidgets.QWidget()
        detail_area.setWidget(detail_widget)
        detail_layout = QtWidgets.QVBoxLayout(detail_widget)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(10)

        form = QtWidgets.QFormLayout()
        form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        self.ed_session_name = QtWidgets.QLineEdit()
        self.ed_session_name.setPlaceholderText("Например: Профиль #1")
        form.addRow("Название:", self.ed_session_name)

        self.cmb_session_prompt_profile = QtWidgets.QComboBox()
        self.cmb_session_prompt_profile.setEditable(False)
        form.addRow("Промпты:", self.cmb_session_prompt_profile)

        self.cmb_session_chrome_profile = QtWidgets.QComboBox()
        self.cmb_session_chrome_profile.setEditable(False)
        form.addRow("Профиль Chrome:", self.cmb_session_chrome_profile)

        self.sb_session_port = QtWidgets.QSpinBox()
        self.sb_session_port.setRange(0, 65535)
        self.sb_session_port.setSpecialValueText("Авто")
        self.sb_session_port.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)
        form.addRow("CDP порт:", self.sb_session_port)

        def make_path_field() -> Tuple[QtWidgets.QLineEdit, QtWidgets.QToolButton, QtWidgets.QWidget]:
            container = QtWidgets.QWidget()
            hl = QtWidgets.QHBoxLayout(container)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(6)
            line = QtWidgets.QLineEdit()
            line.setClearButtonEnabled(True)
            btn = QtWidgets.QToolButton()
            btn.setText("…")
            btn.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
            hl.addWidget(line, 1)
            hl.addWidget(btn, 0)
            return line, btn, container

        self.ed_session_prompts, self.btn_session_prompts_browse, prompts_widget = make_path_field()
        self.ed_session_prompts.setPlaceholderText("По умолчанию берётся из профиля")
        form.addRow("Файл промптов:", prompts_widget)

        self.ed_session_image_prompts, self.btn_session_image_prompts_browse, img_widget = make_path_field()
        self.ed_session_image_prompts.setPlaceholderText("Наследуется из общих настроек")
        form.addRow("Image-промпты:", img_widget)

        self.ed_session_submitted, self.btn_session_submitted_browse, submitted_widget = make_path_field()
        form.addRow("submitted.log:", submitted_widget)

        self.ed_session_failed, self.btn_session_failed_browse, failed_widget = make_path_field()
        form.addRow("failed.log:", failed_widget)

        self.ed_session_download_dir, self.btn_session_download_dir_browse, download_widget = make_path_field()
        self.ed_session_download_dir.setPlaceholderText("По умолчанию — отдельная папка RAW для сессии")
        form.addRow("Папка RAW:", download_widget)

        self.ed_session_clean_dir, self.btn_session_clean_dir_browse, clean_widget = make_path_field()
        self.ed_session_clean_dir.setPlaceholderText("По умолчанию — глобальная папка очистки")
        form.addRow("Папка очистки:", clean_widget)

        self.ed_session_titles_file, self.btn_session_titles_browse, titles_widget = make_path_field()
        self.ed_session_titles_file.setPlaceholderText("Использовать общий список названий")
        form.addRow("Названия:", titles_widget)

        self.ed_session_cursor_file, self.btn_session_cursor_browse, cursor_widget = make_path_field()
        self.ed_session_cursor_file.setPlaceholderText("Файл cursor для этой сессии")
        form.addRow("Cursor:", cursor_widget)

        self.sb_session_max_videos = QtWidgets.QSpinBox()
        self.sb_session_max_videos.setRange(0, 999)
        self.sb_session_max_videos.setSpecialValueText("Как в настройках")
        form.addRow("Лимит скачки:", self.sb_session_max_videos)

        self.chk_session_open_drafts = QtWidgets.QCheckBox("Перед скачкой открывать drafts")
        form.addRow("Drafts:", self.chk_session_open_drafts)

        detail_layout.addLayout(form)

        self.chk_session_auto_chrome = QtWidgets.QCheckBox("Автоматически запускать Chrome при старте")
        detail_layout.addWidget(self.chk_session_auto_chrome)

        auto_row = QtWidgets.QHBoxLayout()
        auto_row.setSpacing(10)
        lbl_auto = QtWidgets.QLabel("Автозапуск автогена:")
        self.cmb_session_autogen_mode = QtWidgets.QComboBox()
        self.cmb_session_autogen_mode.addItem("Не запускать", "idle")
        self.cmb_session_autogen_mode.addItem("Вставка промптов", "prompts")
        self.cmb_session_autogen_mode.addItem("Только картинки", "images")
        auto_row.addWidget(lbl_auto)
        auto_row.addWidget(self.cmb_session_autogen_mode, 1)
        detail_layout.addLayout(auto_row)

        self.te_session_notes = QtWidgets.QPlainTextEdit()
        self.te_session_notes.setPlaceholderText("Свободные заметки, чтобы не перепутать сессию…")
        self.te_session_notes.setMaximumBlockCount(400)
        detail_layout.addWidget(self.te_session_notes)

        status_row = QtWidgets.QHBoxLayout()
        status_row.setSpacing(12)
        self.lbl_session_status = QtWidgets.QLabel("Выбери сессию слева, чтобы увидеть детали")
        self.lbl_session_status.setObjectName("sessionStatusLabel")
        status_row.addWidget(self.lbl_session_status, 1)
        self.btn_session_open_window = QtWidgets.QPushButton("Отдельное окно")
        self.btn_session_open_window.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        status_row.addWidget(self.btn_session_open_window)
        detail_layout.addLayout(status_row)

        actions_grid = QtWidgets.QGridLayout()
        actions_grid.setHorizontalSpacing(8)
        actions_grid.setVerticalSpacing(6)
        self.btn_session_launch_chrome = QtWidgets.QPushButton("Запустить Chrome")
        self.btn_session_run_prompts = QtWidgets.QPushButton("Autogen промптов")
        self.btn_session_run_images = QtWidgets.QPushButton("Только картинки")
        self.btn_session_run_download = QtWidgets.QPushButton("Скачать видео")
        self.btn_session_run_watermark = QtWidgets.QPushButton("Замена водяного знака")
        self.btn_session_open_downloads = QtWidgets.QPushButton("Открыть RAW")
        self.btn_session_stop_runner = QtWidgets.QPushButton("Остановить")
        action_buttons = [
            self.btn_session_launch_chrome,
            self.btn_session_run_prompts,
            self.btn_session_run_images,
            self.btn_session_run_download,
            self.btn_session_run_watermark,
            self.btn_session_open_downloads,
            self.btn_session_stop_runner,
        ]
        for idx, btn in enumerate(action_buttons):
            btn.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
            btn.setMinimumHeight(30)
            btn.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Expanding,
                QtWidgets.QSizePolicy.Policy.Fixed,
            )
            row = idx // 3
            col = idx % 3
            actions_grid.addWidget(btn, row, col)
        detail_layout.addLayout(actions_grid)

        self.te_session_log = QtWidgets.QPlainTextEdit()
        self.te_session_log.setReadOnly(True)
        self.te_session_log.setMaximumBlockCount(500)
        self.te_session_log.setPlaceholderText("Лог сессии появится здесь…")
        detail_layout.addWidget(self.te_session_log, 1)

        self._session_detail_widgets = [
            self.ed_session_name,
            self.cmb_session_prompt_profile,
            self.cmb_session_chrome_profile,
            self.sb_session_port,
            self.ed_session_prompts,
            self.btn_session_prompts_browse,
            self.ed_session_image_prompts,
            self.btn_session_image_prompts_browse,
            self.ed_session_submitted,
            self.btn_session_submitted_browse,
            self.ed_session_failed,
            self.btn_session_failed_browse,
            self.ed_session_download_dir,
            self.btn_session_download_dir_browse,
            self.ed_session_clean_dir,
            self.btn_session_clean_dir_browse,
            self.ed_session_titles_file,
            self.btn_session_titles_browse,
            self.ed_session_cursor_file,
            self.btn_session_cursor_browse,
            self.sb_session_max_videos,
            self.chk_session_open_drafts,
            self.chk_session_auto_chrome,
            self.cmb_session_autogen_mode,
            self.te_session_notes,
            self.btn_session_open_window,
            self.btn_session_launch_chrome,
            self.btn_session_run_prompts,
            self.btn_session_run_images,
            self.btn_session_run_download,
            self.btn_session_run_watermark,
            self.btn_session_open_downloads,
            self.btn_session_stop_runner,
            self.te_session_log,
        ]

        self.lst_sessions.itemSelectionChanged.connect(self._on_session_selection_changed)
        self.btn_session_add.clicked.connect(self._on_session_add)
        self.btn_session_duplicate.clicked.connect(self._on_session_duplicate)
        self.btn_session_remove.clicked.connect(self._on_session_remove)
        self.btn_session_prompts_browse.clicked.connect(lambda: self._browse_file(self.ed_session_prompts, "Выбери файл промптов"))
        self.btn_session_image_prompts_browse.clicked.connect(
            lambda: self._browse_file(self.ed_session_image_prompts, "Выбери файл image-промптов")
        )
        self.btn_session_submitted_browse.clicked.connect(
            lambda: self._browse_file(
                self.ed_session_submitted,
                "Выбери файл submitted.log",
                "Логи (*.log *.txt);;Все файлы (*.*)",
            )
        )
        self.btn_session_failed_browse.clicked.connect(
            lambda: self._browse_file(
                self.ed_session_failed,
                "Выбери файл failed.log",
                "Логи (*.log *.txt);;Все файлы (*.*)",
            )
        )
        self.btn_session_download_dir_browse.clicked.connect(
            lambda: self._browse_dir(self.ed_session_download_dir, "Выбери папку RAW для сессии")
        )
        self.btn_session_clean_dir_browse.clicked.connect(
            lambda: self._browse_dir(self.ed_session_clean_dir, "Выбери папку для очищенных видео")
        )
        self.btn_session_titles_browse.clicked.connect(
            lambda: self._browse_file(
                self.ed_session_titles_file,
                "Выбери файл названий",
                "Текстовые файлы (*.txt);;Все файлы (*.*)",
            )
        )
        self.btn_session_cursor_browse.clicked.connect(
            lambda: self._browse_file(
                self.ed_session_cursor_file,
                "Выбери файл cursor",
                "Cursor (*.cursor *.txt *.log);;Все файлы (*.*)",
            )
        )
        self.btn_session_launch_chrome.clicked.connect(self._on_session_launch_chrome)
        self.btn_session_run_prompts.clicked.connect(self._on_session_run_prompts)
        self.btn_session_run_images.clicked.connect(self._on_session_run_images)
        self.btn_session_run_download.clicked.connect(self._on_session_run_download)
        self.btn_session_run_watermark.clicked.connect(self._on_session_run_watermark)
        self.btn_session_open_downloads.clicked.connect(self._on_session_open_downloads)
        self.btn_session_stop_runner.clicked.connect(self._on_session_stop)
        self.btn_session_open_window.clicked.connect(self._on_session_open_window)
        self.ed_session_name.textEdited.connect(self._on_session_name_changed)
        self.cmb_session_prompt_profile.currentIndexChanged.connect(self._on_session_prompt_profile_changed)
        self.cmb_session_chrome_profile.currentIndexChanged.connect(self._on_session_chrome_profile_changed)
        self.sb_session_port.valueChanged.connect(self._on_session_port_changed)
        self.ed_session_prompts.textEdited.connect(lambda: self._on_session_path_changed("prompts_file", self.ed_session_prompts))
        self.ed_session_image_prompts.textEdited.connect(
            lambda: self._on_session_path_changed("image_prompts_file", self.ed_session_image_prompts)
        )
        self.ed_session_submitted.textEdited.connect(
            lambda: self._on_session_path_changed("submitted_log", self.ed_session_submitted)
        )
        self.ed_session_failed.textEdited.connect(
            lambda: self._on_session_path_changed("failed_log", self.ed_session_failed)
        )
        self.ed_session_download_dir.textEdited.connect(
            lambda: self._on_session_path_changed("download_dir", self.ed_session_download_dir)
        )
        self.ed_session_clean_dir.textEdited.connect(
            lambda: self._on_session_path_changed("clean_dir", self.ed_session_clean_dir)
        )
        self.ed_session_titles_file.textEdited.connect(
            lambda: self._on_session_path_changed("titles_file", self.ed_session_titles_file)
        )
        self.ed_session_cursor_file.textEdited.connect(
            lambda: self._on_session_path_changed("cursor_file", self.ed_session_cursor_file)
        )
        self.te_session_notes.textChanged.connect(self._on_session_notes_changed)
        self.chk_session_auto_chrome.toggled.connect(self._on_session_auto_chrome_changed)
        self.cmb_session_autogen_mode.currentIndexChanged.connect(self._on_session_autogen_mode_changed)
        self.sb_session_max_videos.valueChanged.connect(self._on_session_max_videos_changed)
        self.chk_session_open_drafts.toggled.connect(self._on_session_open_drafts_changed)

        self._refresh_sessions_choices()
        self._refresh_sessions_list()
        self._on_session_selection_changed()

        return tab

    def _build_pipeline_page(self) -> QtWidgets.QWidget:
        root = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(root)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(16)

        header = QtWidgets.QLabel(
            "Собери последовательность шагов: выбери этапы, лимиты и действия перед запуском." 
        )
        header.setWordWrap(True)
        header.setStyleSheet("QLabel{color:#94a3b8;font-size:12px;}")
        layout.addWidget(header)

        layout.addWidget(self._build_pipeline_panel())
        layout.addStretch(1)
        return root

    def _build_automator_page(self) -> QtWidgets.QWidget:
        container = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(container)
        outer.setContentsMargins(18, 18, 18, 18)
        outer.setSpacing(12)

        intro = QtWidgets.QLabel(
            "Собери последовательность действий: выбирай шаги, отмечай сессии и запускай цепочку в один клик."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("QLabel{color:#94a3b8;font-size:12px;}")
        outer.addWidget(intro)

        preset_row = QtWidgets.QHBoxLayout()
        preset_row.setSpacing(6)
        self.cmb_automator_presets = QtWidgets.QComboBox()
        self.cmb_automator_presets.setPlaceholderText("Выбери пресет…")
        self.cmb_automator_presets.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
        preset_row.addWidget(self.cmb_automator_presets, 1)
        self.btn_automator_preset_apply = QtWidgets.QPushButton("Заменить")
        self.btn_automator_preset_append = QtWidgets.QPushButton("Добавить")
        self.btn_automator_preset_save = QtWidgets.QPushButton("Сохранить текущее")
        self.btn_automator_preset_delete = QtWidgets.QPushButton("Удалить")
        for btn in (
            self.btn_automator_preset_apply,
            self.btn_automator_preset_append,
            self.btn_automator_preset_save,
            self.btn_automator_preset_delete,
        ):
            btn.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
            preset_row.addWidget(btn)
        outer.addLayout(preset_row)

        self.lst_automator = QtWidgets.QListWidget()
        self.lst_automator.setObjectName("automatorStepList")
        self.lst_automator.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.lst_automator.setAlternatingRowColors(True)
        self.lst_automator.setSpacing(2)
        outer.addWidget(self.lst_automator, 1)

        controls = QtWidgets.QHBoxLayout()
        controls.setSpacing(8)
        self.btn_automator_add = QtWidgets.QPushButton("Добавить шаг")
        self.btn_automator_edit = QtWidgets.QPushButton("Изменить")
        self.btn_automator_remove = QtWidgets.QPushButton("Удалить")
        self.btn_automator_up = QtWidgets.QPushButton("▲")
        self.btn_automator_down = QtWidgets.QPushButton("▼")
        self.btn_automator_clear = QtWidgets.QPushButton("Очистить")
        for btn in (
            self.btn_automator_add,
            self.btn_automator_edit,
            self.btn_automator_remove,
            self.btn_automator_up,
            self.btn_automator_down,
            self.btn_automator_clear,
        ):
            btn.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
            controls.addWidget(btn)
        controls.addStretch(1)
        outer.addLayout(controls)

        cf_tip = QtWidgets.QLabel(
            "Если Cloudflare просит подтвердить, прогрей профиль: открой страницу Sora в браузере, реши проверку и запусти цепочку."
        )
        cf_tip.setWordWrap(True)
        cf_tip.setStyleSheet("QLabel{color:#94a3b8;font-size:11px;}")
        outer.addWidget(cf_tip)

        cf_row = QtWidgets.QHBoxLayout()
        cf_row.setSpacing(6)
        self.btn_open_profile_page = QtWidgets.QPushButton("🛡️ Прогреть профиль Sora")
        self.btn_open_profile_page.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        cf_row.addWidget(self.btn_open_profile_page)
        cf_row.addStretch(1)
        outer.addLayout(cf_row)

        self.btn_run_automator = QtWidgets.QPushButton("🚀 Запустить автоматизацию")
        self.btn_run_automator.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        outer.addWidget(self.btn_run_automator)
        outer.addStretch(1)
        return container

    def _build_global_log_page(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        header = QtWidgets.QLabel("Журнал объединяет статусы скачки, блюра, склейки, загрузки и автогена.")
        header.setWordWrap(True)
        header.setStyleSheet("QLabel{color:#94a3b8;font-size:12px;}")
        layout.addWidget(header)

        self.activity_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        self.activity_splitter.setChildrenCollapsible(False)
        self.activity_splitter.setHandleWidth(8)
        layout.addWidget(self.activity_splitter, 1)

        current_wrap = QtWidgets.QWidget()
        current_layout = QtWidgets.QVBoxLayout(current_wrap)
        current_layout.setContentsMargins(0, 0, 0, 0)
        current_layout.setSpacing(6)
        self.current_event_card = QtWidgets.QFrame()
        self.current_event_card.setObjectName("currentEventCard")
        self.current_event_card.setStyleSheet(
            "QFrame#currentEventCard{background:transparent;border:1px solid #27364d;border-radius:14px;padding:0;}"
            "QLabel#currentEventTitle{color:#9fb7ff;font-size:11px;letter-spacing:1px;text-transform:uppercase;}"
            "QFrame#currentEventBodyFrame{background:transparent;border:1px solid #1f2a40;border-radius:10px;}"
            "QLabel#currentEventBody{color:#f8fafc;font-size:15px;font-weight:600;background:transparent;}"
        )
        card_layout = QtWidgets.QVBoxLayout(self.current_event_card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        self.lbl_current_event_title = QtWidgets.QLabel("Сейчас")
        self.lbl_current_event_title.setObjectName("currentEventTitle")
        body_wrap = QtWidgets.QFrame()
        body_wrap.setObjectName("currentEventBodyFrame")
        body_layout = QtWidgets.QVBoxLayout(body_wrap)
        body_layout.setContentsMargins(12, 8, 12, 8)
        body_layout.setSpacing(0)
        self.lbl_current_event_body = QtWidgets.QLabel("—")
        self.lbl_current_event_body.setObjectName("currentEventBody")
        self.lbl_current_event_body.setWordWrap(True)
        body_layout.addWidget(self.lbl_current_event_body)
        self.lbl_current_event_timer = QtWidgets.QLabel("—")
        self.lbl_current_event_timer.setObjectName("currentEventTimer")
        self.lbl_current_event_timer.setStyleSheet("color:#94a3b8;font-size:11px;")
        card_layout.addWidget(self.lbl_current_event_title)
        card_layout.addWidget(body_wrap)
        card_layout.addWidget(self.lbl_current_event_timer)
        current_layout.addWidget(self.current_event_card)
        self.activity_splitter.addWidget(current_wrap)
        self.activity_current_wrap = current_wrap

        history_panel = QtWidgets.QWidget()
        history_layout = QtWidgets.QVBoxLayout(history_panel)
        history_layout.setContentsMargins(0, 0, 0, 0)
        history_layout.setSpacing(6)
        filter_row = QtWidgets.QHBoxLayout()
        self.ed_activity_filter = QtWidgets.QLineEdit()
        self.ed_activity_filter.setPlaceholderText("Фильтр по тексту или тегу…")
        self.ed_activity_filter.setClearButtonEnabled(True)
        self.btn_activity_export = QtWidgets.QPushButton("Экспорт")
        filter_row.addWidget(self.ed_activity_filter, 1)
        filter_row.addWidget(self.btn_activity_export)
        history_layout.addLayout(filter_row)
        self.lst_activity = QtWidgets.QListWidget()
        self.lst_activity.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
        self.lst_activity.setUniformItemSizes(False)
        self.lst_activity.setWordWrap(True)
        self.lst_activity.setAlternatingRowColors(False)
        self.lst_activity.setSpacing(2)
        self._apply_activity_density(persist=False)
        history_layout.addWidget(self.lst_activity, 1)
        self.lbl_activity_hint = QtWidgets.QLabel(
            "История событий пополняется автоматически при выполнении операций."
        )
        self.lbl_activity_hint.setWordWrap(True)
        self.lbl_activity_hint.setStyleSheet("QLabel{color:#94a3b8;font-size:11px;}")
        history_layout.addWidget(self.lbl_activity_hint)
        self.activity_splitter.addWidget(history_panel)
        self.activity_splitter.setStretchFactor(0, 0)
        self.activity_splitter.setStretchFactor(1, 1)

        toolbar = QtWidgets.QHBoxLayout()
        toolbar.setSpacing(8)
        self.chk_activity_visible = QtWidgets.QCheckBox("Показывать журнал")
        self.chk_activity_visible.setChecked(bool(self.cfg.get("ui", {}).get("show_activity", True)))
        self.chk_activity_visible.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        self.btn_activity_clear = QtWidgets.QPushButton("🧹 Очистить")
        toolbar.addWidget(self.chk_activity_visible)
        toolbar.addWidget(self.btn_activity_clear)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        return panel

    def _build_session_log_page(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        intro = QtWidgets.QLabel(
            "Выбери сессию, чтобы увидеть последние строки из журналов вставки промптов, скачки и загрузки."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("QLabel{color:#94a3b8;font-size:12px;}")
        layout.addWidget(intro)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(6)
        layout.addWidget(splitter, 1)

        self.lst_session_logs = QtWidgets.QListWidget()
        self.lst_session_logs.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.lst_session_logs.setAlternatingRowColors(True)
        splitter.addWidget(self.lst_session_logs)

        self.txt_session_logs = QtWidgets.QPlainTextEdit()
        self.txt_session_logs.setReadOnly(True)
        self.txt_session_logs.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
        splitter.addWidget(self.txt_session_logs)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        self.lst_session_logs.itemSelectionChanged.connect(self._on_session_log_selected)

        return panel

    def _toggle_nav_category(self, category: str) -> None:
        collapsed = not self._nav_category_collapsed.get(category, False)
        self._nav_category_collapsed[category] = collapsed
        for item in self._nav_category_members.get(category, []):
            item.setHidden(collapsed)
        ui_cfg = self.cfg.setdefault("ui", {})
        ui_cfg["nav_collapsed"] = dict(self._nav_category_collapsed)
        save_cfg(self.cfg)
        header = self._nav_group_rows.get(category)
        if header:
            arrow = "▶" if collapsed else "▼"
            header.setText(f"{arrow} {category.upper()}")

    def _on_nav_item_clicked(self, item: QtWidgets.QListWidgetItem) -> None:
        marker = item.data(QtCore.Qt.ItemDataRole.UserRole + 1)
        if marker == "header":
            category = item.data(QtCore.Qt.ItemDataRole.UserRole + 2) or ""
            if category:
                self._toggle_nav_category(str(category))
        else:
            key = item.data(QtCore.Qt.ItemDataRole.UserRole)
            if key:
                self._focus_section_from_command(str(key))

    def _refresh_session_log_panel(self) -> None:
        if not hasattr(self, "lst_session_logs"):
            return
        current = getattr(self, "_current_session_log_id", "")
        self.lst_session_logs.blockSignals(True)
        self.lst_session_logs.clear()
        target_row = 0
        for idx, session_id in enumerate(self._session_order):
            session = self._session_cache.get(session_id)
            if not session:
                continue
            item = QtWidgets.QListWidgetItem(self._session_display_label(session_id))
            item.setData(QtCore.Qt.ItemDataRole.UserRole, session_id)
            self.lst_session_logs.addItem(item)
            if session_id == current:
                target_row = idx
        if self.lst_session_logs.count():
            self.lst_session_logs.setCurrentRow(target_row)
        self.lst_session_logs.blockSignals(False)
        self._on_session_log_selected()

    def _session_log_text(self, session_id: str) -> str:
        session = self._session_cache.get(session_id)
        if not session:
            return "Сессия не найдена"

        def tail(path: Path, limit: int = 200) -> str:
            if not path.exists():
                return ""
            try:
                lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
            except Exception:
                return ""
            return "\n".join(lines[-limit:])

        parts = []
        state = self._ensure_session_state(session_id)
        if state.get("last_message"):
            parts.append(f"⚙️ Статус: {state['last_message']}")

        submitted = self._session_submitted_log_path(session)
        submitted_text = tail(submitted)
        if submitted_text:
            parts.append(f"📄 submitted.log ({submitted}):\n{submitted_text}")

        failed = self._session_failed_log_path(session)
        failed_text = tail(failed)
        if failed_text:
            parts.append(f"⚠️ failed.log ({failed}):\n{failed_text}")

        download_dir = self._session_download_dir(session)
        dl_log = download_dir / "download.log"
        dl_text = tail(dl_log)
        if dl_text:
            parts.append(f"⬇️ download.log ({dl_log}):\n{dl_text}")

        return "\n\n".join(parts) if parts else "Журналы отсутствуют"

    def _on_session_log_selected(self) -> None:
        if not hasattr(self, "lst_session_logs"):
            return
        items = self.lst_session_logs.selectedItems()
        if not items:
            self.txt_session_logs.setPlainText("Выбери сессию слева")
            self._current_session_log_id = ""
            return
        session_id = items[0].data(QtCore.Qt.ItemDataRole.UserRole) or ""
        self._current_session_log_id = str(session_id)
        text = self._session_log_text(self._current_session_log_id)
        self.txt_session_logs.setPlainText(text)

    def _refresh_logs_context(self) -> None:
        # контекст обновляется автоматически через ленту статусов
        return

    def _refresh_session_logs_context(self) -> None:
        # дополнительного состояния не требуется
        return

    def _wire(self):
        # статусы/лог — безопасные слоты GUI-потока
        self.sig_set_status.connect(self._slot_set_status)
        self.sig_log.connect(self._slot_log)

        self.cmb_chrome_profile_top.currentIndexChanged.connect(self._on_top_chrome_profile_changed)
        self.cmb_chrome_profile_top.currentIndexChanged.connect(lambda *_: self._refresh_pipeline_context())
        self.btn_scan_profiles_top.clicked.connect(self._on_toolbar_scan_profiles)
        if hasattr(self, "btn_toggle_commands"):
            self.btn_toggle_commands.toggled.connect(self._on_toolbar_commands_toggle)
        self.btn_open_chrome.clicked.connect(self._open_chrome)
        self.btn_open_root.clicked.connect(lambda: open_in_finder(self.cfg.get("project_root", PROJECT_ROOT)))
        self.btn_open_raw.clicked.connect(lambda: open_in_finder(self.cfg.get("downloads_dir", DL_DIR)))
        self.btn_collect_raw.clicked.connect(self._gather_session_downloads)
        self.btn_open_blur.clicked.connect(lambda: open_in_finder(self.cfg.get("blurred_dir", BLUR_DIR)))
        self.btn_open_merge.clicked.connect(lambda: open_in_finder(self.cfg.get("merged_dir", MERG_DIR)))
        self.btn_open_images_top.clicked.connect(self._open_genai_output_dir)
        self.btn_open_restored_top.clicked.connect(
            lambda: open_in_finder(
                _project_path(
                    self.cfg.get("watermark_cleaner", {}).get("output_dir", str(PROJECT_ROOT / "restored"))
                )
            )
        )
        self.btn_stop_all.clicked.connect(self._stop_all)
        self.btn_start_selected.clicked.connect(self._run_scenario)
        self.btn_activity_clear.clicked.connect(self._clear_activity)
        self.chk_activity_visible.toggled.connect(self._on_activity_toggle)
        self.ed_activity_filter.textChanged.connect(self._on_activity_filter_changed)
        self.btn_activity_export.clicked.connect(self._export_activity_log)
        if hasattr(self, "lst_custom_commands"):
            self.lst_custom_commands.itemSelectionChanged.connect(self._update_custom_command_buttons)
        if hasattr(self, "btn_custom_add"):
            self.btn_custom_add.clicked.connect(self._on_custom_command_add)
        if hasattr(self, "btn_custom_edit"):
            self.btn_custom_edit.clicked.connect(self._on_custom_command_edit)
        if hasattr(self, "btn_custom_delete"):
            self.btn_custom_delete.clicked.connect(self._on_custom_command_remove)
        if hasattr(self, "btn_custom_up"):
            self.btn_custom_up.clicked.connect(lambda _, direction=-1: self._on_custom_command_move(direction))
        if hasattr(self, "btn_custom_down"):
            self.btn_custom_down.clicked.connect(lambda _, direction=1: self._on_custom_command_move(direction))
        if hasattr(self, "lst_automator"):
            self.lst_automator.itemSelectionChanged.connect(self._update_automator_buttons)
            self.lst_automator.itemDoubleClicked.connect(lambda *_: self._on_automator_edit())
        if hasattr(self, "btn_automator_add"):
            self.btn_automator_add.clicked.connect(self._on_automator_add)
        if hasattr(self, "btn_automator_edit"):
            self.btn_automator_edit.clicked.connect(self._on_automator_edit)
        if hasattr(self, "btn_automator_remove"):
            self.btn_automator_remove.clicked.connect(self._on_automator_remove)
        if hasattr(self, "btn_automator_up"):
            self.btn_automator_up.clicked.connect(lambda _, direction=-1: self._on_automator_move(direction))
        if hasattr(self, "btn_automator_down"):
            self.btn_automator_down.clicked.connect(lambda _, direction=1: self._on_automator_move(direction))
        if hasattr(self, "btn_automator_clear"):
            self.btn_automator_clear.clicked.connect(self._on_automator_clear)
        if hasattr(self, "cmb_automator_presets"):
            self.cmb_automator_presets.currentIndexChanged.connect(lambda *_: self._update_automator_buttons())
        if hasattr(self, "btn_automator_preset_apply"):
            self.btn_automator_preset_apply.clicked.connect(lambda *_: self._on_automator_preset_apply())
        if hasattr(self, "btn_automator_preset_append"):
            self.btn_automator_preset_append.clicked.connect(lambda *_: self._on_automator_preset_apply(append=True))
        if hasattr(self, "btn_automator_preset_save"):
            self.btn_automator_preset_save.clicked.connect(self._on_automator_preset_save)
        if hasattr(self, "btn_automator_preset_delete"):
            self.btn_automator_preset_delete.clicked.connect(self._on_automator_preset_delete)
        if hasattr(self, "btn_open_profile_page"):
            self.btn_open_profile_page.clicked.connect(self._open_sora_profile_page)
        if hasattr(self, "btn_run_automator"):
            self.btn_run_automator.clicked.connect(self._run_automator)
        if hasattr(self, "cb_quick_activity"):
            self.cb_quick_activity.toggled.connect(lambda checked: self._apply_activity_visibility(bool(checked), persist=True))
        if hasattr(self, "cmb_quick_density"):
            self.cmb_quick_density.currentIndexChanged.connect(
                lambda _: self._apply_activity_density(self.cmb_quick_density.currentData() or "compact", persist=True)
            )

        self.btn_load_prompts.clicked.connect(self._load_prompts)
        self.btn_save_prompts.clicked.connect(self._save_prompts)
        self.btn_save_and_run_autogen.clicked.connect(self._save_and_run_autogen)
        self.btn_load_image_prompts.clicked.connect(self._load_image_prompts)
        self.btn_save_image_prompts.clicked.connect(self._save_image_prompts)
        self.btn_used_refresh.clicked.connect(self._reload_used_prompts)
        self.btn_used_clear.clicked.connect(self._clear_used_prompts)
        self.lst_prompt_profiles.itemSelectionChanged.connect(self._on_prompt_profile_selection)
        self.btn_load_titles.clicked.connect(self._load_titles)
        self.btn_save_titles.clicked.connect(self._save_titles)
        self.btn_reset_titles_cursor.clicked.connect(self._reset_titles_cursor)

        self.btn_apply_dl.clicked.connect(self._apply_dl_limit)
        if hasattr(self, "sb_max_videos"):
            self.sb_max_videos.valueChanged.connect(lambda *_: self._refresh_pipeline_context())
        if hasattr(self, "sb_merge_group"):
            self.sb_merge_group.valueChanged.connect(lambda *_: self._refresh_pipeline_context())
        self.btn_run_scenario.clicked.connect(self._run_scenario)
        self.btn_run_autogen_images.clicked.connect(self._save_and_run_autogen_images)
        self.btn_run_watermark.clicked.connect(self._run_watermark)
        self.btn_open_genai_output.clicked.connect(self._open_genai_output_dir)

        self.btn_save_settings.clicked.connect(self._save_settings_clicked)
        self.btn_save_autogen_cfg.clicked.connect(self._save_autogen_cfg)
        self.btn_reload_readme.clicked.connect(lambda: self._load_readme_preview(force=True))
        if hasattr(self, "settings_tabs"):
            self.settings_tabs.currentChanged.connect(self._on_settings_tab_changed)
        self.btn_env_check.clicked.connect(self._run_env_check)
        self.btn_update_check.clicked.connect(lambda: self._check_for_updates(dry_run=True))
        self.btn_update_pull.clicked.connect(lambda: self._check_for_updates(dry_run=False))
        self.btn_maintenance_cleanup.clicked.connect(lambda: self._run_maintenance_cleanup(manual=True))
        self.btn_maintenance_sizes.clicked.connect(self._report_dir_sizes)
        self.cmb_ui_activity_density.currentIndexChanged.connect(self._on_activity_density_changed)
        if hasattr(self, "cb_ui_show_context"):
            self.cb_ui_show_context.toggled.connect(self._on_settings_show_context_changed)
        self.cmb_active_preset.currentTextChanged.connect(self._on_active_preset_changed)
        self.btn_preset_add.clicked.connect(self._on_preset_add)
        self.btn_preset_delete.clicked.connect(self._on_preset_delete)
        self.btn_preset_preview.clicked.connect(self._open_blur_preview)
        self.btn_aw_template.clicked.connect(lambda: self._browse_file(self.ed_aw_template, "Выбери шаблон водяного знака", "Изображения (*.png *.jpg *.jpeg *.bmp);;Все файлы (*.*)"))

        self.btn_youtube_src_browse.clicked.connect(lambda: self._browse_dir(self.ed_youtube_src, "Выбери папку с клипами"))
        self.cb_youtube_draft_only.toggled.connect(self._toggle_youtube_schedule)
        self.cb_youtube_draft_only.toggled.connect(lambda _: self._update_youtube_queue_label())
        self.cb_youtube_schedule.toggled.connect(self._toggle_youtube_schedule)
        self.cb_youtube_schedule.toggled.connect(lambda _: self._update_youtube_queue_label())
        self.lst_youtube_channels.itemSelectionChanged.connect(self._on_youtube_selected)
        self.btn_yt_add.clicked.connect(self._on_youtube_add_update)
        self.btn_yt_delete.clicked.connect(self._on_youtube_delete)
        self.btn_yt_set_active.clicked.connect(self._on_youtube_set_active)
        self.btn_yt_client_browse.clicked.connect(lambda: self._browse_file(self.ed_yt_client, "client_secret.json", "JSON (*.json);;Все файлы (*.*)"))
        self.btn_yt_credentials_browse.clicked.connect(lambda: self._browse_file(self.ed_yt_credentials, "credentials.json", "JSON (*.json);;Все файлы (*.*)"))
        self.btn_youtube_archive_browse.clicked.connect(lambda: self._browse_dir(self.ed_youtube_archive, "Выбери папку архива"))
        self.cb_youtube_default_draft.toggled.connect(self._sync_draft_checkbox)
        self.sb_youtube_default_delay.valueChanged.connect(self._apply_default_delay)
        self.sb_youtube_interval_default.valueChanged.connect(lambda val: self.sb_youtube_interval.setValue(int(val)))
        self.sb_youtube_limit_default.valueChanged.connect(lambda val: self.sb_youtube_batch_limit.setValue(int(val)))
        self.btn_wmr_source_browse.clicked.connect(lambda: self._browse_dir(self.ed_wmr_source, "Выбери папку RAW"))
        self.btn_wmr_output_browse.clicked.connect(lambda: self._browse_dir(self.ed_wmr_output, "Выбери папку для готовых клипов"))
        self.btn_wmr_template_browse.clicked.connect(
            lambda: self._browse_file(
                self.ed_wmr_template,
                "Выбери шаблон водяного знака",
                "Изображения (*.png *.jpg *.jpeg *.bmp);;Все файлы (*.*)",
            )
        )
        if hasattr(self, "btn_probe_source_browse"):
            self.btn_probe_source_browse.clicked.connect(
                lambda: self._browse_dir(self.ed_probe_source, "Выбери папку RAW для проверки")
            )
        if hasattr(self, "btn_probe_output_browse"):
            self.btn_probe_output_browse.clicked.connect(
                lambda: self._browse_dir(self.ed_probe_output_dir, "Выбери папку для результата")
            )
        if hasattr(self, "btn_probe_video_browse"):
            self.btn_probe_video_browse.clicked.connect(
                lambda: self._browse_file(
                    self.ed_probe_video,
                    "Выбери видео для проверки",
                    "Видео (*.mp4 *.mov *.m4v *.webm);;Все файлы (*.*)",
                )
            )
        if hasattr(self, "btn_probe_preview"):
            self.btn_probe_preview.clicked.connect(self._open_watermark_probe_preview)
        if hasattr(self, "btn_probe_scan"):
            self.btn_probe_scan.clicked.connect(lambda: self._run_watermark_probe(flip=False))
        if hasattr(self, "btn_probe_flip"):
            self.btn_probe_flip.clicked.connect(lambda: self._run_watermark_probe(flip=True))
        if hasattr(self, "btn_probe_batch_scan"):
            self.btn_probe_batch_scan.clicked.connect(lambda: self._run_watermark_probe_batch(flip=False))
        if hasattr(self, "btn_probe_batch_flip"):
            self.btn_probe_batch_flip.clicked.connect(lambda: self._run_watermark_probe_batch(flip=True))
        self.btn_tiktok_archive_browse.clicked.connect(lambda: self._browse_dir(self.ed_tiktok_archive, "Выбери папку архива"))
        self.sb_tiktok_default_delay.valueChanged.connect(self._apply_tiktok_default_delay)
        self.sb_tiktok_interval_default.valueChanged.connect(lambda val: self.sb_tiktok_interval.setValue(int(val)))
        self.sb_tiktok_limit_default.valueChanged.connect(lambda val: self.sb_tiktok_batch_limit.setValue(int(val)))
        self.sb_youtube_interval.valueChanged.connect(self._reflect_youtube_interval)
        self.sb_youtube_batch_limit.valueChanged.connect(self._reflect_youtube_limit)
        self.btn_youtube_refresh.clicked.connect(self._update_youtube_queue_label)
        self.btn_youtube_start.clicked.connect(self._start_youtube_single)
        self.ed_youtube_src.textChanged.connect(lambda _: self._update_youtube_queue_label())
        self.dt_youtube_publish.dateTimeChanged.connect(self._sync_delay_from_datetime)
        self.btn_tg_test.clicked.connect(self._test_tg_settings)

        self.lst_tiktok_profiles.itemSelectionChanged.connect(self._on_tiktok_selected)
        self.btn_tt_add.clicked.connect(self._on_tiktok_add_update)
        self.btn_tt_delete.clicked.connect(self._on_tiktok_delete)
        self.btn_tt_set_active.clicked.connect(self._on_tiktok_set_active)
        self.btn_tt_secret.clicked.connect(lambda: self._browse_file(self.ed_tt_secret, "Выбери файл секретов", "JSON (*.json);;YAML (*.yaml *.yml);;Все файлы (*.*)"))
        self.btn_tt_secret_load.clicked.connect(self._load_tiktok_secret_file)
        if hasattr(self, "btn_context_session_window"):
            self.btn_context_session_window.clicked.connect(self._on_session_open_window)
        if hasattr(self, "btn_context_session_prompts"):
            self.btn_context_session_prompts.clicked.connect(self._on_session_run_prompts)
        if hasattr(self, "btn_context_session_images"):
            self.btn_context_session_images.clicked.connect(self._on_session_run_images)
        if hasattr(self, "btn_context_session_download"):
            self.btn_context_session_download.clicked.connect(self._on_session_run_download)
        if hasattr(self, "btn_context_session_watermark"):
            self.btn_context_session_watermark.clicked.connect(self._on_session_run_watermark)
        if hasattr(self, "btn_context_session_probe"):
            self.btn_context_session_probe.clicked.connect(lambda: self._select_section("watermark_probe"))
        if hasattr(self, "btn_context_tg_test"):
            self.btn_context_tg_test.clicked.connect(self._test_tg_settings)
        if hasattr(self, "btn_context_tg_open"):
            self.btn_context_tg_open.clicked.connect(lambda: self._select_section("telegram"))
        if hasattr(self, "btn_context_refresh_queues"):
            self.btn_context_refresh_queues.clicked.connect(self._update_youtube_queue_label)
            self.btn_context_refresh_queues.clicked.connect(self._update_tiktok_queue_label)
        if hasattr(self, "btn_context_open_project"):
            self.btn_context_open_project.clicked.connect(
                lambda: open_in_finder(self.cfg.get("project_root", str(PROJECT_ROOT)))
            )
        if hasattr(self, "btn_context_open_prompts"):
            self.btn_context_open_prompts.clicked.connect(
                lambda: open_in_finder(str(self._prompts_path()))
            )
        if hasattr(self, "btn_context_open_image_prompts"):
            self.btn_context_open_image_prompts.clicked.connect(
                lambda: open_in_finder(str(self._image_prompts_path()))
            )
        if hasattr(self, "btn_context_open_titles"):
            self.btn_context_open_titles.clicked.connect(
                lambda: open_in_finder(self.cfg.get("titles_file", str(TITLES_FILE)))
            )
        if hasattr(self, "cb_tg_enabled"):
            self.cb_tg_enabled.toggled.connect(lambda _: self._refresh_telegram_context())
        if hasattr(self, "btn_preset_full_cycle"):
            self.btn_preset_full_cycle.clicked.connect(
                lambda: self._apply_task_preset(
                    "Полный цикл",
                    ["images", "prompts", "download", "blur", "merge", "youtube", "tiktok"],
                )
            )
        if hasattr(self, "btn_preset_generate"):
            self.btn_preset_generate.clicked.connect(
                lambda: self._apply_task_preset(
                    "Генерация + постинг",
                    ["images", "prompts", "youtube", "tiktok"],
                )
            )
        if hasattr(self, "btn_preset_prompts_only"):
            self.btn_preset_prompts_only.clicked.connect(
                lambda: self._apply_task_preset("Только промпты", ["prompts"])
            )
        if hasattr(self, "btn_preset_clear"):
            self.btn_preset_clear.clicked.connect(lambda: self._apply_task_preset("Сброс", []))
        self.cb_tiktok_schedule.toggled.connect(self._toggle_tiktok_schedule)
        self.cb_tiktok_schedule.toggled.connect(lambda _: self._update_tiktok_queue_label())
        self.cb_tiktok_draft.toggled.connect(lambda _: self._update_tiktok_queue_label())
        self.sb_tiktok_interval.valueChanged.connect(self._reflect_tiktok_interval)
        self.sb_tiktok_batch_limit.valueChanged.connect(lambda _: self._update_tiktok_queue_label())
        self.ed_tiktok_src.textChanged.connect(lambda _: self._update_tiktok_queue_label())
        self.dt_tiktok_publish.dateTimeChanged.connect(self._sync_tiktok_from_datetime)
        self.btn_tiktok_src_browse.clicked.connect(lambda: self._browse_dir(self.ed_tiktok_src, "Выбери папку с клипами"))
        self.btn_tiktok_refresh.clicked.connect(self._update_tiktok_queue_label)
        self.btn_tiktok_start.clicked.connect(self._start_tiktok_single)
        self.btn_tiktok_dispatch.clicked.connect(self._dispatch_tiktok_workflow)

        # rename
        self.btn_ren_browse.clicked.connect(self._ren_browse)
        self.btn_ren_run.clicked.connect(self._ren_run)

        # merge opts
        self.btn_apply_merge.clicked.connect(self._apply_merge_opts)

        # профили
        self.lst_profiles.itemSelectionChanged.connect(self._on_profile_selected)
        self.btn_prof_add.clicked.connect(self._on_profile_add_update)
        self.btn_prof_del.clicked.connect(self._on_profile_delete)
        self.btn_prof_set.clicked.connect(self._on_profile_set_active)
        self.btn_prof_scan.clicked.connect(self._on_profile_scan)

        # browse buttons for paths
        self.btn_browse_root.clicked.connect(lambda: self._browse_dir(self.ed_root, "Выбери папку проекта"))
        self.btn_browse_downloads.clicked.connect(lambda: self._browse_dir(self.ed_downloads, "Выбери папку RAW"))
        self.btn_browse_blurred.clicked.connect(lambda: self._browse_dir(self.ed_blurred, "Выбери папку BLURRED"))
        self.btn_browse_merged.clicked.connect(lambda: self._browse_dir(self.ed_merged, "Выбери папку MERGED"))
        self.btn_browse_blur_src.clicked.connect(lambda: self._browse_dir(self.ed_blur_src, "Выбери ИСТОЧНИК для BLUR"))
        self.btn_browse_merge_src.clicked.connect(lambda: self._browse_dir(self.ed_merge_src, "Выбери ИСТОЧНИК для MERGE"))
        if hasattr(self, "btn_browse_history_path"):
            self.btn_browse_history_path.clicked.connect(lambda: self._browse_file(self.ed_history_path, "Выбери файл истории", "JSONL (*.jsonl);;Все файлы (*.*)"))
        if hasattr(self, "btn_browse_titles_path"):
            self.btn_browse_titles_path.clicked.connect(lambda: self._browse_file(self.ed_titles_path, "Выбери файл названий", "Текстовые файлы (*.txt);;Все файлы (*.*)"))
        self.btn_genai_output_browse.clicked.connect(lambda: self._browse_dir(self.ed_genai_output_dir, "Выбери папку для изображений"))
        self.btn_genai_usage_browse.clicked.connect(
            lambda: self._browse_file(
                self.ed_genai_usage_file,
                "Выбери файл статистики",
                "JSON (*.json);;Все файлы (*.*)",
            )
        )

        for button_attr, line_attr in [
            ("btn_open_root_path", "ed_root"),
            ("btn_open_downloads_path", "ed_downloads"),
            ("btn_open_blurred_path", "ed_blurred"),
            ("btn_open_merged_path", "ed_merged"),
            ("btn_open_blur_src_path", "ed_blur_src"),
            ("btn_open_merge_src_path", "ed_merge_src"),
            ("btn_open_history_path", "ed_history_path"),
            ("btn_open_titles_path", "ed_titles_path"),
            ("btn_youtube_src_open", "ed_youtube_src"),
            ("btn_youtube_archive_open", "ed_youtube_archive"),
            ("btn_tiktok_src_open", "ed_tiktok_src"),
            ("btn_tiktok_archive_open", "ed_tiktok_archive"),
            ("btn_genai_output_open", "ed_genai_output_dir"),
            ("btn_genai_usage_open", "ed_genai_usage_file"),
        ]:
            button = getattr(self, button_attr, None)
            line = getattr(self, line_attr, None)
            if isinstance(button, QtWidgets.QAbstractButton) and isinstance(line, QtWidgets.QLineEdit):
                button.clicked.connect(lambda _, l=line: self._open_path_from_edit(l))

    def _init_state(self):
        self.runner_autogen = ProcRunner("AUTOGEN")
        self.runner_dl = ProcRunner("DL")
        self.runner_upload = ProcRunner("YT")
        self.runner_tiktok = ProcRunner("TT")
        self.runner_watermark = ProcRunner("WMR")
        self.runner_autogen.line.connect(self._slot_log)
        self.runner_dl.line.connect(self._slot_log)
        self.runner_upload.line.connect(self._slot_log)
        self.runner_tiktok.line.connect(self._slot_log)
        self.runner_watermark.line.connect(self._slot_log)
        self.runner_autogen.finished.connect(self._proc_done)
        self.runner_dl.finished.connect(self._proc_done)
        self.runner_upload.finished.connect(self._proc_done)
        self.runner_tiktok.finished.connect(self._proc_done)
        self.runner_watermark.finished.connect(self._proc_done)
        self.runner_autogen.notify.connect(self._notify)
        self.runner_dl.notify.connect(self._notify)
        self.runner_upload.notify.connect(self._notify)
        self.runner_tiktok.notify.connect(self._notify)
        self.runner_watermark.notify.connect(self._notify)
        self._post_status("Готово", state="idle")

    # ----- безопасные слоты GUI-потока -----
    @QtCore.pyqtSlot(str, int, int, str)
    def _slot_set_status(self, text: str, progress: int, total: int, state: str):
        # state: idle|running|ok|error
        self.lbl_status.setText(text)
        self.lbl_context_status_text.setText(text)
        if total > 0:
            self.pb_global.setMaximum(total); self.pb_global.setValue(progress); self.pb_global.setFormat(f"{progress}/{total}")
            self.pb_context_status.setMaximum(total)
            self.pb_context_status.setValue(progress)
            self.pb_context_status.setFormat(f"{progress}/{total}")
        else:
            self.pb_global.setMaximum(1); self.pb_global.setValue(1); self.pb_global.setFormat("—")
            self.pb_context_status.setMaximum(1)
            self.pb_context_status.setValue(1)
            self.pb_context_status.setFormat("—")
        color = "#777"
        if state == "running": color = "#f6a700"
        if state == "ok": color = "#1bb55c"
        if state == "error": color = "#d74c4c"
        self.pb_global.setStyleSheet(f"QProgressBar::chunk {{ background-color: {color}; }}")
        self.pb_context_status.setStyleSheet(
            "QProgressBar#contextStatusProgress {"
            " background:#0f172a;"
            " border:1px solid rgba(148,163,184,0.16);"
            " border-radius:6px;"
            " height:12px;"
            "}"
            f"QProgressBar#contextStatusProgress::chunk {{ background-color: {color}; border-radius:6px; }}"
        )
        icon_map = {"idle": "—", "running": "⏳", "ok": "✅", "error": "⚠️"}
        self.lbl_context_status_icon.setText(icon_map.get(state, "—"))

        if state == "running":
            if self._current_step_state != "running":
                self._current_step_started = time.monotonic()
                self._current_step_timer.start()
            elapsed = 0.0
            if self._current_step_started is not None:
                elapsed = time.monotonic() - self._current_step_started
            self._set_step_timer_label(elapsed, prefix="⌛")
        else:
            if self._current_step_state == "running" and self._current_step_started is not None:
                elapsed = time.monotonic() - self._current_step_started
                self._set_step_timer_label(elapsed, prefix="⏱")
            if state == "idle":
                if hasattr(self, "lbl_current_event_timer"):
                    self.lbl_current_event_timer.setText("—")
                self._current_step_timer.stop()
                self._current_step_started = None
        self._current_step_state = state

        kind_map = {"idle": "info", "running": "running", "ok": "success", "error": "error"}
        preserve = state == "running"
        self._update_current_event(text, kind_map.get(state, "info"), preserve_timer=preserve)

    def _set_step_timer_label(self, seconds: float, prefix: str = "⌛"):
        if not hasattr(self, "lbl_current_event_timer"):
            return
        seconds = max(0, int(seconds))
        minutes, sec = divmod(seconds, 60)
        self.lbl_current_event_timer.setText(f"{prefix} {minutes:02d}:{sec:02d}")

    def _tick_step_timer(self):
        if self._current_step_started is None:
            self._current_step_timer.stop()
            return
        elapsed = time.monotonic() - self._current_step_started
        self._set_step_timer_label(elapsed, prefix="⌛")

    def _on_active_preset_changed(self, name: str):
        if not name:
            return
        self._select_preset_tab(name)
        self._mark_settings_dirty()

    def _on_preset_add(self):
        base = self.cmb_active_preset.currentText() or ""
        text, ok = QtWidgets.QInputDialog.getText(self, "Новый пресет", "Название пресета:")
        name = text.strip()
        if not ok or not name:
            return
        if name in self._preset_cache:
            self._post_status("Такой пресет уже существует", state="error")
            return
        sample = self._preset_cache.get(base) or [{"x": 0, "y": 0, "w": 0, "h": 0}]
        self._preset_cache[name] = [dict(zone) for zone in sample]
        self._create_preset_tab(name)
        self.cmb_active_preset.addItem(name)
        self.cmb_active_preset.setCurrentText(name)
        self._mark_settings_dirty()

    def _on_preset_delete(self):
        name = self.cmb_active_preset.currentText().strip()
        if not name:
            return
        if len(self._preset_cache) <= 1:
            self._post_status("Нельзя удалить последний пресет", state="error")
            return
        confirm = QtWidgets.QMessageBox.question(
            self,
            "Удалить пресет",
            f"Удалить пресет «{name}»?",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
        )
        if confirm != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        self._preset_cache.pop(name, None)
        table = self._preset_tables.pop(name, None)
        if table:
            table.deleteLater()
        for idx in range(self.tab_presets.count()):
            if self.tab_presets.tabText(idx) == name:
                self.tab_presets.removeTab(idx)
                break
        idx = self.cmb_active_preset.findText(name)
        self.cmb_active_preset.blockSignals(True)
        if idx >= 0:
            self.cmb_active_preset.removeItem(idx)
        self.cmb_active_preset.blockSignals(False)
        if self.cmb_active_preset.count():
            self.cmb_active_preset.setCurrentIndex(max(0, idx - 1))
        self._mark_settings_dirty()

    def _open_blur_preview(self):
        preset = self.cmb_active_preset.currentText().strip()
        if not preset:
            self._post_status("Нет выбранного пресета", state="error")
            return

        try:
            from blur_preview import (
                BlurPreviewDialog,
                VIDEO_PREVIEW_AVAILABLE,
                VIDEO_PREVIEW_TIP,
            )
        except Exception as exc:  # pragma: no cover - защитное сообщение для UI
            self._post_status(f"Предпросмотр недоступен: {exc}", state="error")
            return

        preview_available = VIDEO_PREVIEW_AVAILABLE
        if not preview_available:
            QtWidgets.QMessageBox.information(
                self,
                "Предпросмотр ограничен",
                (
                    "Библиотека OpenCV не найдена, поэтому видео не будет показано, "
                    "но координаты можно отредактировать в таблице.\n\n"
                    f"{VIDEO_PREVIEW_TIP}"
                ),
            )

        zones = self._preset_cache.get(preset, [])
        dirs = [
            _project_path(self.cfg.get("downloads_dir", str(DL_DIR))),
            _project_path(self.cfg.get("blur_src_dir", self.cfg.get("downloads_dir", str(DL_DIR)))),
            _project_path(self.cfg.get("blurred_dir", str(BLUR_DIR))),
        ]
        dlg = BlurPreviewDialog(self, preset, zones, dirs)
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            new_zones = dlg.zones()
            if not new_zones:
                new_zones = [{"x": 0, "y": 0, "w": 0, "h": 0}]
            self._preset_cache[preset] = new_zones
            self._populate_preset_table(preset)
            self._mark_settings_dirty()
            self._post_status(f"Пресет {preset} обновлён", state="ok")

    # ----- использованные промпты -----
    def _parse_used_prompt_line(self, line: str, fallback_instance: str) -> Tuple[str, str, str]:
        parts = line.split("\t", 2)
        if len(parts) == 3:
            ts, instance, prompt = parts
        else:
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            instance = fallback_instance
            prompt = line
        ts = ts.strip() or "—"
        instance = instance.strip() or fallback_instance
        prompt = prompt.strip()
        return ts, instance, prompt

    def _gather_used_prompts(self) -> List[Tuple[str, str, str]]:
        rows: List[Tuple[str, str, str]] = []
        seen: Set[Path] = set()

        def collect(path_str: Optional[str], instance_name: str):
            if not path_str:
                return
            path = _project_path(path_str)
            if path in seen or not path.exists():
                return
            seen.add(path)
            try:
                for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                    line = raw.strip()
                    if not line:
                        continue
                    rows.append(self._parse_used_prompt_line(line, instance_name))
            except Exception as exc:
                self._append_activity(f"Не удалось прочитать {path}: {exc}", kind="error", card_text=False)

        auto_cfg = self.cfg.get("autogen", {}) or {}
        for inst in auto_cfg.get("instances", []) or []:
            collect(inst.get("submitted_log"), inst.get("name") or "Instance")

        profile_keys: List[str] = [PROMPTS_DEFAULT_KEY]
        chrome_profiles = self.cfg.get("chrome", {}).get("profiles", []) or []
        for prof in chrome_profiles:
            if not isinstance(prof, dict):
                continue
            name = prof.get("name")
            if name:
                profile_keys.append(name)

        for key in profile_keys:
            label = self._prompt_profile_label(key)
            log_path = self._profile_submitted_log_path(key)
            collect(str(log_path), label)

        def _sort_key(row: Tuple[str, str, str]):
            ts, _, prompt = row
            try:
                return time.strptime(ts, "%Y-%m-%d %H:%M:%S")
            except Exception:
                return time.localtime(0)

        rows.sort(key=_sort_key, reverse=True)
        return rows

    def _reload_used_prompts(self):
        if not hasattr(self, "tbl_used_prompts"):
            return
        rows = self._gather_used_prompts()
        self.tbl_used_prompts.blockSignals(True)
        self.tbl_used_prompts.setRowCount(0)
        for ts, instance, prompt in rows[:400]:
            row = self.tbl_used_prompts.rowCount()
            self.tbl_used_prompts.insertRow(row)
            for col, text in enumerate([ts, instance, prompt]):
                item = QtWidgets.QTableWidgetItem(text)
                align = QtCore.Qt.AlignmentFlag.AlignCenter if col < 2 else QtCore.Qt.AlignmentFlag.AlignLeft
                item.setTextAlignment(int(align))
                self.tbl_used_prompts.setItem(row, col, item)
        self.tbl_used_prompts.blockSignals(False)

    def _clear_used_prompts(self):
        if not hasattr(self, "tbl_used_prompts"):
            return
        paths: Set[Path] = set()
        auto_cfg = self.cfg.get("autogen", {}) or {}
        for inst in auto_cfg.get("instances", []) or []:
            if inst.get("submitted_log"):
                paths.add(_project_path(inst.get("submitted_log")))

        profile_keys: List[str] = [PROMPTS_DEFAULT_KEY]
        chrome_profiles = self.cfg.get("chrome", {}).get("profiles", []) or []
        for prof in chrome_profiles:
            if not isinstance(prof, dict):
                continue
            name = prof.get("name")
            if name:
                profile_keys.append(name)

        for key in profile_keys:
            paths.add(self._profile_submitted_log_path(key))
        if not paths:
            self._post_status("Журналов нет", state="idle")
            return
        confirm = QtWidgets.QMessageBox.question(
            self,
            "Очистить журналы",
            "Удалить записи об использованных промптах?",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
        )
        if confirm != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        for path in paths:
            try:
                if path.exists():
                    path.unlink()
            except Exception as exc:
                self._append_activity(f"Не удалось удалить {path}: {exc}", kind="error", card_text=False)
        self._reload_used_prompts()
        self._post_status("Журналы промптов очищены", state="ok")

    def _append_activity(self, text: str, kind: str = "info", card_text: Optional[Union[str, bool]] = None):
        if not text:
            return

        if card_text is not False:
            display = card_text if isinstance(card_text, str) and card_text else text
            self._update_current_event(display, kind)

        stamp = time.strftime("%H:%M:%S")
        emoji_map = {
            "info": "ℹ️",
            "running": "🔄",
            "success": "✅",
            "error": "❌",
            "warn": "⚠️",
        }
        emoji = emoji_map.get(kind, "ℹ️")
        display_text = f"{stamp} · {emoji} {text}"
        item = QtWidgets.QListWidgetItem(display_text)
        palette = {
            "info": ("#93c5fd", "#15223c"),
            "running": ("#facc15", "#352b0b"),
            "success": ("#34d399", "#0f2f24"),
            "error": ("#f87171", "#3a0d15"),
            "warn": ("#facc15", "#352b0b"),
        }
        fg, bg = palette.get(kind, palette["info"])
        brush_fg = QtGui.QBrush(QtGui.QColor(fg))
        brush_bg = QtGui.QBrush(QtGui.QColor(bg))
        item.setForeground(brush_fg)
        item.setBackground(brush_bg)
        item.setTextAlignment(int(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter))
        item.setData(QtCore.Qt.ItemDataRole.UserRole, text.lower())
        self._style_activity_item(item)
        self.lst_activity.addItem(item)
        while self.lst_activity.count() > 200:
            self.lst_activity.takeItem(0)
        self.lst_activity.scrollToBottom()
        self._apply_activity_filter()

        if "telegram" in text.lower():
            self._record_telegram_activity(display_text, kind)

    def _ensure_telegram_cache(self) -> deque:
        cache = getattr(self, "_telegram_activity_cache", None)
        if cache is None:
            cache = deque(maxlen=200)
            self._telegram_activity_cache = cache
        return cache

    def _append_telegram_history_item(self, display: str, kind: str):
        if not hasattr(self, "lst_tg_history") or self.lst_tg_history is None:
            return
        palette = {
            "info": "#93c5fd",
            "running": "#facc15",
            "success": "#34d399",
            "error": "#f87171",
            "warn": "#facc15",
        }
        item = QtWidgets.QListWidgetItem(display)
        item.setForeground(QtGui.QColor(palette.get(kind, palette["info"])))
        item.setTextAlignment(int(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter))
        self.lst_tg_history.addItem(item)
        while self.lst_tg_history.count() > 120:
            self.lst_tg_history.takeItem(0)
        self.lst_tg_history.scrollToBottom()

    def _record_telegram_activity(self, display: str, kind: str):
        cache = self._ensure_telegram_cache()
        cache.append((display, kind))
        self._append_telegram_history_item(display, kind)

    def _refresh_telegram_history(self):
        if not hasattr(self, "lst_tg_history") or self.lst_tg_history is None:
            return
        cache = self._ensure_telegram_cache()
        self.lst_tg_history.blockSignals(True)
        self.lst_tg_history.clear()
        for display, kind in list(cache)[-120:]:
            self._append_telegram_history_item(display, kind)
        self.lst_tg_history.blockSignals(False)

    def _clear_telegram_history(self):
        cache = self._ensure_telegram_cache()
        cache.clear()
        if hasattr(self, "lst_tg_history") and self.lst_tg_history is not None:
            self.lst_tg_history.clear()

    def _send_quick_telegram(self):
        message = self.ed_tg_quick_message.toPlainText().strip()
        if not message:
            self.lbl_tg_status.setText("Введите текст сообщения перед отправкой")
            self.lbl_tg_status.setStyleSheet("QLabel{color:#facc15;}")
            return
        delay = int(self.sb_tg_quick_delay.value()) if hasattr(self, "sb_tg_quick_delay") else 0
        short = message if len(message) <= 60 else f"{message[:57]}…"
        if delay > 0:
            timer = QtCore.QTimer(self)
            timer.setSingleShot(True)

            def dispatch(msg: str = message, label: str = short, timer_ref: QtCore.QTimer = timer):
                ok_inner = self._send_tg(msg)
                color = "#34d399" if ok_inner else "#f87171"
                status = "Сообщение отправлено" if ok_inner else "Не удалось отправить сообщение"
                self.lbl_tg_status.setText(status)
                self.lbl_tg_status.setStyleSheet(f"QLabel{{color:{color};}}")
                entry = f"Telegram ⏱ {label}"
                self._record_telegram_activity(entry, "success" if ok_inner else "error")
                self._pending_tg_jobs = [job for job in self._pending_tg_jobs if job[0] is not timer_ref]

            timer.timeout.connect(dispatch)
            timer.start(int(delay) * 60 * 1000)
            self._pending_tg_jobs.append((timer, message))
            self.lbl_tg_status.setText(f"Запланировано через {delay} мин")
            self.lbl_tg_status.setStyleSheet("QLabel{color:#38bdf8;}")
            self._record_telegram_activity(
                f"Telegram ⏱ через {delay} мин — {short}",
                "running",
            )
            return

        ok = self._send_tg(message)
        if ok:
            self.lbl_tg_status.setText("Сообщение отправлено")
            self.lbl_tg_status.setStyleSheet("QLabel{color:#34d399;}")
            self.ed_tg_quick_message.clear()
            self._record_telegram_activity(f"Telegram ✓ {short}", "success")
        else:
            self.lbl_tg_status.setText("Не удалось отправить сообщение")
            self.lbl_tg_status.setStyleSheet("QLabel{color:#f87171;}")
            self._record_telegram_activity(f"Telegram ✗ {short}", "error")

    def _clear_quick_telegram_message(self):
        self.ed_tg_quick_message.clear()
        self.lbl_tg_status.setText("Готово к отправке")
        self.lbl_tg_status.setStyleSheet("QLabel{color:#94a3b8;}")

    @QtCore.pyqtSlot(str)
    def _slot_log(self, text: str):
        clean = text.rstrip("\n")
        if not clean:
            return

        # прогресс по скачиванию
        if "Найдено карточек:" in clean or "Собрано ссылок:" in clean:
            m = re.search(r"(Найдено карточек|Собрано ссылок):\s*(\d+)", clean)
            if m:
                total = int(m.group(2))
                self._post_status("Скачивание запущено…", progress=0, total=total, state="running")
        if "Скачано:" in clean:
            fmt = self.pb_global.format()
            try:
                done, total = map(int, fmt.split("/"))
            except Exception:
                done, total = self.pb_global.value(), self.pb_global.maximum()
            done = min(done + 1, total)
            self._post_status("Скачивание…", progress=done, total=total, state="running")

        # лёгкие нотификации по маркерам
        markers = {
            "[NOTIFY] AUTOGEN_START": ("Autogen", "Началась вставка промптов", None),
            "[NOTIFY] AUTOGEN_FINISH_OK": ("Autogen", "Вставка промптов — успешно", None),
            "[NOTIFY] AUTOGEN_FINISH_PARTIAL": ("Autogen", "Вставка промптов — частично (были отказы)", None),
            "[NOTIFY] DOWNLOAD_START": ("Downloader", "Началась автоскачка", None),
            "[NOTIFY] DOWNLOAD_FINISH": ("Downloader", "Автоскачка завершена", None),
            "[NOTIFY] CLOUDFLARE_ALERT": (
                "Downloader",
                "Похоже, Cloudflare просит проверку. Реши капчу вручную — скачка на паузе.",
                "⚠️ Cloudflare: появилось окно проверки. Пройди её и продолжай скачивание.",
            ),
        }
        notif = markers.get(clean.strip())
        if notif:
            title, message, tg = notif
            self._notify(title, message)
            if tg:
                self._send_tg(tg)
            return

        # форматируем строку для панели событий
        label_match = re.match(r"^\[(?P<tag>[^\]]+)\]\s*(?P<body>.*)$", clean)
        if label_match:
            tag = label_match.group("tag").replace(":", " · ")
            body = label_match.group("body")
            clean = f"{tag}: {body}" if body else tag

        normalized = clean.strip()
        kind = "info"
        lowered = normalized.lower()
        if any(token in lowered for token in ["ошиб", "fail", "error", "не найден", "прервана"]):
            kind = "error"
        elif any(token in normalized for token in ["✓", "успеш", "готово", "завершено", "ok"]):
            kind = "success"
        elif any(token in lowered for token in ["запуск", "старт", "загружа", "обрабаты", "выполня"]):
            kind = "running"

        self._append_activity(normalized, kind=kind, card_text=False)

    def _apply_activity_filter(self):
        if not hasattr(self, "lst_activity"):
            return
        pattern = (self._activity_filter_text or "").strip().lower()
        for i in range(self.lst_activity.count()):
            item = self.lst_activity.item(i)
            if not isinstance(item, QtWidgets.QListWidgetItem):
                continue
            if not pattern:
                item.setHidden(False)
                continue
            hay = item.data(QtCore.Qt.ItemDataRole.UserRole)
            hay_text = hay if isinstance(hay, str) else item.text().lower()
            item.setHidden(pattern not in hay_text)

    def _on_activity_filter_changed(self, text: str):
        self._activity_filter_text = text.strip().lower()
        self._apply_activity_filter()

    def _export_activity_log(self):
        if not hasattr(self, "lst_activity") or self.lst_activity.count() == 0:
            self._post_status("Нет событий для экспорта", state="warn")
            return
        default_path = _project_path(self.cfg.get("history_file", str(HIST_FILE))).with_name("activity_log.txt")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Сохранить историю событий",
            str(default_path),
            "Текстовые файлы (*.txt);;Все файлы (*.*)",
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                for i in range(self.lst_activity.count()):
                    item = self.lst_activity.item(i)
                    f.write(item.text() + "\n")
            self._post_status(f"История сохранена: {Path(path).name}", state="ok")
        except Exception as exc:
            self._post_status(f"Не удалось сохранить историю: {exc}", state="error")

    # helper для статуса
    def _post_status(self, text: str, progress: int = 0, total: int = 0, state: str = "idle"):
        self.sig_set_status.emit(text, progress, total, state)

    def _clear_activity(self):
        self.lst_activity.clear()
        self._post_status("Лента событий очищена", state="idle")
        self._update_current_event("—", "info")
        self._apply_activity_filter()

    def _update_current_event(self, text: str, kind: str = "info", persist: bool = False, preserve_timer: bool = False):
        if not hasattr(self, "current_event_card"):
            return

        palette = {
            "info": ("#27364d", "#f8fafc"),
            "success": ("#1f5136", "#34d399"),
            "error": ("#4d1f29", "#f87171"),
            "running": ("#4d3b1f", "#facc15"),
        }
        border, color = palette.get(kind, palette["info"])
        self.current_event_card.setStyleSheet(
            f"QFrame#currentEventCard{{background:#162132;border:1px solid {border};border-radius:14px;padding:0;}}"
            "QLabel#currentEventTitle{color:#9fb7ff;font-size:11px;letter-spacing:1px;text-transform:uppercase;}"
            f"QLabel#currentEventBody{{color:{color};font-size:15px;font-weight:600;}}"
        )
        self.lbl_current_event_body.setText(text or "—")
        if not preserve_timer:
            if hasattr(self, "lbl_current_event_timer"):
                self.lbl_current_event_timer.setText("—")
        self.cfg.setdefault("ui", {})["accent_kind"] = kind
        if persist:
            save_cfg(self.cfg)

    def _apply_activity_visibility(self, visible: bool, persist: bool = True):
        if not hasattr(self, "lst_activity"):
            return
        self.lst_activity.setVisible(bool(visible))
        if hasattr(self, "lbl_activity_hint"):
            self.lbl_activity_hint.setVisible(bool(visible))
        if hasattr(self, "activity_current_wrap"):
            if visible:
                self.activity_current_wrap.setSizePolicy(
                    QtWidgets.QSizePolicy.Policy.Preferred,
                    QtWidgets.QSizePolicy.Policy.Expanding,
                )
            else:
                self.activity_current_wrap.setSizePolicy(
                    QtWidgets.QSizePolicy.Policy.Preferred,
                    QtWidgets.QSizePolicy.Policy.Maximum,
                )
        if hasattr(self, "history_panel"):
            if visible:
                self.history_panel.show()
                if getattr(self, "_activity_sizes_cache", None):
                    QtCore.QTimer.singleShot(0, lambda: self.activity_splitter.setSizes(self._activity_sizes_cache))
            else:
                if hasattr(self, "activity_splitter"):
                    self._activity_sizes_cache = self.activity_splitter.sizes()
                    self.activity_splitter.setSizes([self.activity_splitter.sizes()[0], 0])
                self.history_panel.hide()
        if hasattr(self, "chk_activity_visible"):
            self.chk_activity_visible.blockSignals(True)
            self.chk_activity_visible.setChecked(bool(visible))
            self.chk_activity_visible.blockSignals(False)
        if hasattr(self, "cb_quick_activity"):
            self.cb_quick_activity.blockSignals(True)
            self.cb_quick_activity.setChecked(bool(visible))
            self.cb_quick_activity.blockSignals(False)
        if hasattr(self, "cb_ui_show_activity"):
            self.cb_ui_show_activity.blockSignals(True)
            self.cb_ui_show_activity.setChecked(bool(visible))
            self.cb_ui_show_activity.blockSignals(False)
        self.cfg.setdefault("ui", {})["show_activity"] = bool(visible)
        if persist:
            save_cfg(self.cfg)
        self._refresh_overview_context()

    @QtCore.pyqtSlot(str)
    def _update_vcodec_ui(self, text: str):
        if not hasattr(self, "cmb_vcodec"):
            return
        self.cmb_vcodec.blockSignals(True)
        try:
            self.cmb_vcodec.setCurrentText(text)
        finally:
            self.cmb_vcodec.blockSignals(False)

    def _on_activity_toggle(self, checked: bool):
        self._apply_activity_visibility(bool(checked), persist=True)

    def _on_settings_activity_toggle(self, checked: bool):
        self._apply_activity_visibility(bool(checked), persist=False)

    def _apply_activity_density(self, density: Optional[str] = None, persist: bool = False):
        if not hasattr(self, "lst_activity"):
            return
        if density is None:
            density = self.cfg.get("ui", {}).get("activity_density", "compact")
        if density not in {"compact", "cozy"}:
            density = "compact"

        margin = "2px" if density == "compact" else "4px"
        padding = "4px 6px" if density == "compact" else "6px 10px"
        radius = "6px" if density == "compact" else "10px"
        spacing = 1 if density == "compact" else 4

        self.lst_activity.setSpacing(spacing)
        self.lst_activity.setStyleSheet(
            "QListWidget{background:#101827;border:1px solid #23324b;border-radius:10px;padding:6px;}"
            f"QListWidget::item{{margin:{margin};padding:{padding};border-radius:{radius};background:#172235;}}"
        )

        if hasattr(self, "cmb_quick_density"):
            self.cmb_quick_density.blockSignals(True)
            idx = self.cmb_quick_density.findData(density)
            if idx >= 0:
                self.cmb_quick_density.setCurrentIndex(idx)
            self.cmb_quick_density.blockSignals(False)
        if hasattr(self, "cmb_ui_activity_density"):
            self.cmb_ui_activity_density.blockSignals(True)
            idx_ui = self.cmb_ui_activity_density.findData(density)
            if idx_ui >= 0:
                self.cmb_ui_activity_density.setCurrentIndex(idx_ui)
            self.cmb_ui_activity_density.blockSignals(False)

        for idx in range(self.lst_activity.count()):
            item = self.lst_activity.item(idx)
            if item:
                self._style_activity_item(item, density)

        self.cfg.setdefault("ui", {})["activity_density"] = density
        if persist:
            save_cfg(self.cfg)
        self._refresh_overview_context()

    def _style_activity_item(self, item: QtWidgets.QListWidgetItem, density: Optional[str] = None):
        density = density or self.cfg.get("ui", {}).get("activity_density", "compact")
        font = QtGui.QFont(self.font())
        font.setPointSize(10 if density == "compact" else 11)
        item.setFont(font)
        height = 28 if density == "compact" else 42
        item.setSizeHint(QtCore.QSize(0, height))

    def _on_activity_density_changed(self, idx: int):
        density = self.cmb_ui_activity_density.itemData(idx) or "compact"
        self._apply_activity_density(density, persist=False)

    # ----- обработчик завершения подпроцессов -----
    @QtCore.pyqtSlot(int, str)
    def _proc_done(self, rc: int, tag: str):
        if tag == "AUTOGEN":
            msg = "Вставка промптов завершена" + (" ✓" if rc == 0 else " ✗")
            self._post_status(msg, state=("ok" if rc == 0 else "error"))
            append_history(self.cfg, {"event": "autogen_finish", "rc": rc})
            if rc == 0:
                self._send_tg("AUTOGEN: ok")
            self._reload_used_prompts()
        elif tag == "DL":
            msg = "Скачка завершена" + (" ✓" if rc == 0 else " ✗")
            self._post_status(msg, state=("ok" if rc == 0 else "error"))
            append_history(self.cfg, {"event": "download_finish", "rc": rc})
            if rc == 0:
                self._send_tg("DOWNLOAD: ok")
        elif tag == "YT":
            msg = "YouTube загрузка завершена" + (" ✓" if rc == 0 else " ✗")
            self._post_status(msg, state=("ok" if rc == 0 else "error"))
            append_history(self.cfg, {"event": "youtube_finish", "rc": rc})
            if rc == 0:
                self._send_tg("YOUTUBE: ok")
            self.ui(self._update_youtube_queue_label)
        elif tag == "TT":
            msg = "TikTok загрузка завершена" + (" ✓" if rc == 0 else " ✗")
            self._post_status(msg, state=("ok" if rc == 0 else "error"))
            append_history(self.cfg, {"event": "tiktok_finish", "rc": rc})
            if rc == 0:
                self._send_tg("TIKTOK: ok")
            self.ui(self._update_tiktok_queue_label)
        self._refresh_stats()

        with self._scenario_wait_lock:
            if tag in self._scenario_waiters:
                self._scenario_results[tag] = rc
                self._scenario_waiters[tag].set()

    # ----- Chrome (через тень профиля) -----
    def _open_chrome(self, *, session: Optional[Dict[str, Any]] = None) -> bool:
        ch = self.cfg.get("chrome", {})
        if session:
            try:
                port = int(self._session_chrome_port(session))
            except Exception:
                port = 9222
            override_profile = session.get("chrome_profile") or session.get("prompt_profile") or ""
        else:
            override_profile = None
            try:
                port = int(self._resolve_chrome_port(ch.get("active_profile", "")))
            except Exception:
                port = 9222
        if sys.platform == "darwin":
            default_chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        elif sys.platform.startswith("win"):
            default_chrome = r"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
        else:
            default_chrome = "google-chrome"
        chrome_bin = os.path.expandvars(ch.get("binary") or default_chrome)
        profiles = ch.get("profiles", [])
        active_name = override_profile if session else ch.get("active_profile", "")
        fallback_userdir = os.path.expandvars(ch.get("user_data_dir", "") or "")

        # уже поднят CDP?
        if port_in_use(port) and cdp_ready(port):
            self._post_status(f"Chrome уже поднят (CDP {port})", state="idle")
            return True

        # активный профиль
        active = None
        if session and active_name:
            for p in profiles:
                if p.get("name") == active_name:
                    active = p
                    break
            if not active:
                # если имя указано напрямую, пробуем искать по директории профиля
                for p in profiles:
                    if p.get("profile_directory") == active_name:
                        active = p
                        break
        elif active_name:
            for p in profiles:
                if p.get("name") == active_name:
                    active = p
                    break

        shadow_root = None
        try:
            # базовая папка для теней
            shadow_base = Path.home() / ".sora_suite" / "shadows"
            shadow_base.mkdir(parents=True, exist_ok=True)

            if active:
                shadow_root = _prepare_shadow_profile(active, shadow_base)
                prof_dir = active.get("profile_directory", "Default")
            elif fallback_userdir:
                fake_active = {
                    "name": "Imported",
                    "user_data_dir": fallback_userdir,
                    "profile_directory": "Default",
                }
                shadow_root = _prepare_shadow_profile(fake_active, shadow_base)
                prof_dir = "Default"
            else:
                name = "Empty"
                shadow_root = shadow_base / name
                (shadow_root / "Default").mkdir(parents=True, exist_ok=True)
                prof_dir = "Default"

            cmd = [
                chrome_bin,
                f"--remote-debugging-port={port}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-popup-blocking",
                f"--user-data-dir={str(shadow_root)}",
                f"--profile-directory={prof_dir}",
                "--disable-features=OptimizationHints,Translate",
                "--disable-background-networking",
                "--disable-sync",
                "--metrics-recording-only",
            ]

            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            # ждём подъёма CDP (до ~10 сек)
            t0 = time.time()
            while time.time() - t0 < 10:
                if cdp_ready(port):
                    profile_label = active_name or (session.get("name") if session else "shadow")
                    self._post_status(f"Chrome c CDP {port} (профиль: {profile_label})", state="ok")
                    append_history(
                        self.cfg,
                        {
                            "event": "chrome_launch",
                            "port": port,
                            "profile": active_name,
                            "session": session.get("id") if session else None,
                            "shadow": str(shadow_root),
                        },
                    )
                    return True
                time.sleep(0.25)

            self._post_status("CDP не поднялся — проверь бинарь Chrome и порт", state="error")
            return False

        except Exception as e:
            self._post_status(f"Ошибка запуска Chrome/shadow: {e}", state="error")
            return False
        return False
    # ----- Prompts/Titles -----
    def _prompts_path(self, key: Optional[str] = None) -> Path:
        active = key or self._current_prompt_profile_key or PROMPTS_DEFAULT_KEY
        if active in ("", PROMPTS_DEFAULT_KEY):
            return self._default_profile_prompts(None)
        return self._default_profile_prompts(active)

    def _image_prompts_path(self) -> Path:
        auto_cfg = self.cfg.get("autogen", {}) or {}
        raw = auto_cfg.get("image_prompts_file") or str(WORKERS_DIR / "autogen" / "image_prompts.txt")
        return _project_path(raw)

    def _session_prompts_path(self, session: Dict[str, Any]) -> Path:
        custom = session.get("prompts_file")
        if custom:
            return _project_path(custom)
        profile_key = session.get("prompt_profile") or PROMPTS_DEFAULT_KEY
        return self._prompts_path(profile_key)

    def _session_image_prompts_path(self, session: Dict[str, Any]) -> Path:
        custom = session.get("image_prompts_file")
        if custom:
            return _project_path(custom)
        return self._image_prompts_path()

    def _session_submitted_log_path(self, session: Dict[str, Any]) -> Path:
        custom = session.get("submitted_log")
        if custom:
            return _project_path(custom)
        profile_key = session.get("prompt_profile") or PROMPTS_DEFAULT_KEY
        return self._profile_submitted_log_path(profile_key)

    def _session_failed_log_path(self, session: Dict[str, Any]) -> Path:
        custom = session.get("failed_log")
        if custom:
            return _project_path(custom)
        profile_key = session.get("prompt_profile") or PROMPTS_DEFAULT_KEY
        return self._profile_failed_log_path(profile_key)

    def _session_chrome_port(self, session: Dict[str, Any]) -> int:
        port = _coerce_int(session.get("cdp_port"))
        profile_key = session.get("prompt_profile") or PROMPTS_DEFAULT_KEY
        if port and port > 0:
            return port
        return self._resolve_chrome_port(profile_key)

    def _session_instance_label(self, session: Dict[str, Any]) -> str:
        name = str(session.get("name") or "").strip()
        if name:
            return name
        profile_key = session.get("prompt_profile") or PROMPTS_DEFAULT_KEY
        return self._prompt_profile_label(profile_key)

    def _session_download_dir(self, session: Dict[str, Any], *, ensure: bool = False) -> Path:
        custom = session.get("download_dir")
        if custom:
            path = _project_path(custom)
        else:
            base = _project_path(self.cfg.get("downloads_dir", str(DL_DIR)))
            slug = slugify(self._session_instance_label(session))
            path = base / "sessions" / slug
        if ensure:
            try:
                path.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
        return path

    def _session_watermark_output_dir(self, session: Dict[str, Any], *, ensure: bool = False) -> Path:
        custom = session.get("clean_dir")
        if custom:
            path = _project_path(custom)
        else:
            base = _project_path(self.cfg.get("watermark_cleaner", {}).get("output_dir", str(PROJECT_ROOT / "restored")))
            slug = slugify(self._session_instance_label(session))
            path = base / slug
        if ensure:
            try:
                path.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
        return path

    def _session_titles_path(self, session: Dict[str, Any]) -> Path:
        custom = session.get("titles_file")
        if custom:
            return _project_path(custom)
        base_path = _project_path(self.cfg.get("titles_file", str(TITLES_FILE)))
        slug = slugify(self._session_instance_label(session))
        stem = base_path.stem or "titles"
        suffix = base_path.suffix or ".txt"
        return base_path.with_name(f"{stem}_{slug}{suffix}")

    def _session_cursor_path(self, session: Dict[str, Any]) -> Path:
        custom = session.get("cursor_file")
        if custom:
            return _project_path(custom)
        titles_path = self._session_titles_path(session)
        return Path(os.path.splitext(str(titles_path))[0] + ".cursor")

    def _session_download_limit(self, session: Dict[str, Any]) -> int:
        limit = _coerce_int(session.get("max_videos")) or 0
        if limit and limit > 0:
            return int(limit)
        dl_cfg = self.cfg.get("downloader", {}) or {}
        return int(dl_cfg.get("max_videos", 0) or 0)

    def _session_open_drafts(self, session: Dict[str, Any]) -> bool:
        if "open_drafts" in session:
            return bool(session.get("open_drafts"))
        dl_cfg = self.cfg.get("downloader", {}) or {}
        return bool(dl_cfg.get("open_drafts", True))

    def _session_download_limit_label(self, session: Dict[str, Any]) -> str:
        limit = _coerce_int(session.get("max_videos")) or 0
        if limit and limit > 0:
            return f"{limit}"
        global_limit = int((self.cfg.get("downloader", {}) or {}).get("max_videos", 0) or 0)
        if global_limit > 0:
            return f"{global_limit} (глобально)"
        return "без лимита"

    def _session_env(self, session_id: str, *, force_images: Optional[bool] = None, images_only: bool = False) -> dict:
        session = self._session_cache.get(session_id)
        if not session:
            return self._autogen_env(force_images=force_images, images_only=images_only)

        profile_key = session.get("prompt_profile") or PROMPTS_DEFAULT_KEY
        prompts_path = self._session_prompts_path(session)
        submitted_log = self._session_submitted_log_path(session)
        failed_log = self._session_failed_log_path(session)
        image_prompts = self._session_image_prompts_path(session)
        port = self._session_chrome_port(session)
        label = self._session_instance_label(session)
        env = self._autogen_env(
            force_images=force_images,
            images_only=images_only,
            profile_key=profile_key,
            prompts_path=prompts_path,
            chrome_port=port,
            submitted_log=submitted_log,
            failed_log=failed_log,
            image_prompts_path=image_prompts,
            instance_name=label,
        )
        env["SORA_SESSION_ID"] = session_id
        return env

    def _chrome_profile_by_name(self, name: Optional[str]) -> Optional[Dict[str, Any]]:
        if not name:
            return None
        chrome_cfg = self.cfg.get("chrome", {}) or {}
        for prof in chrome_cfg.get("profiles", []) or []:
            if not isinstance(prof, dict):
                continue
            if prof.get("name") == name:
                return prof
        return None

    def _resolve_chrome_port(self, profile_name: Optional[str] = None) -> int:
        chrome_cfg = self.cfg.get("chrome", {}) or {}
        fallback = _coerce_int(chrome_cfg.get("cdp_port")) or 9222
        target = profile_name
        if not target or target == PROMPTS_DEFAULT_KEY:
            target = chrome_cfg.get("active_profile", "") or ""
        if target and target != PROMPTS_DEFAULT_KEY:
            prof = self._chrome_profile_by_name(target)
            if prof:
                port = _coerce_int(prof.get("cdp_port"))
                if port and port > 0:
                    return port
        return fallback

    def _profile_log_path(self, base_value: Optional[str], profile_key: Optional[str], default_filename: str) -> Path:
        base_raw = base_value or str(WORKERS_DIR / "autogen" / default_filename)
        base_path = _project_path(base_raw)
        if not profile_key or profile_key == PROMPTS_DEFAULT_KEY:
            return base_path
        slug = slugify(str(profile_key)) or "profile"
        suffix = base_path.suffix or Path(default_filename).suffix or ".log"
        stem = base_path.stem or Path(default_filename).stem or "log"
        return base_path.with_name(f"{stem}_{slug}{suffix}")

    def _profile_submitted_log_path(self, profile_key: Optional[str]) -> Path:
        auto_cfg = self.cfg.get("autogen", {}) or {}
        return self._profile_log_path(auto_cfg.get("submitted_log"), profile_key, "submitted.log")

    def _profile_failed_log_path(self, profile_key: Optional[str]) -> Path:
        auto_cfg = self.cfg.get("autogen", {}) or {}
        return self._profile_log_path(auto_cfg.get("failed_log"), profile_key, "failed.log")

    def _load_prompts(self):
        path = self._prompts_path()
        self._ensure_path_exists(str(path))
        txt = path.read_text(encoding="utf-8") if path.exists() else ""
        self.ed_prompts.setPlainText(txt)
        if hasattr(self, "lbl_prompts_path"):
            self.lbl_prompts_path.setText(str(path))
        self._post_status(f"Промпты загружены ({path})", state="idle")

    def _save_prompts(self):
        path = self._prompts_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.ed_prompts.toPlainText(), encoding="utf-8")
        if hasattr(self, "lbl_prompts_path"):
            self.lbl_prompts_path.setText(str(path))
        self._post_status("Промпты сохранены", state="ok")

    def _load_image_prompts(self):
        path = self._image_prompts_path()
        self._ensure_path_exists(path)
        txt = path.read_text(encoding="utf-8") if path.exists() else ""
        if hasattr(self, "ed_image_prompts"):
            self.ed_image_prompts.setPlainText(txt)
        if hasattr(self, "lbl_image_prompts_path"):
            self.lbl_image_prompts_path.setText(str(path))
        self._post_status(f"Image-промпты загружены ({path})", state="idle")

    def _save_image_prompts(self):
        path = self._image_prompts_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        if hasattr(self, "ed_image_prompts"):
            path.write_text(self.ed_image_prompts.toPlainText(), encoding="utf-8")
        if hasattr(self, "lbl_image_prompts_path"):
            self.lbl_image_prompts_path.setText(str(path))
        self._post_status("Image-промпты сохранены", state="ok")

    def _open_genai_output_dir(self):
        genai_cfg = self.cfg.get("google_genai", {}) or {}
        output_raw = genai_cfg.get("output_dir") or IMAGES_DIR
        path = self._ensure_path_exists(output_raw)
        if path:
            open_in_finder(path)

    def _autogen_env(
        self,
        force_images: Optional[bool] = None,
        *,
        images_only: bool = False,
        profile_key: Optional[str] = None,
        prompts_path: Optional[Union[str, Path]] = None,
        chrome_port: Optional[int] = None,
        submitted_log: Optional[Union[str, Path]] = None,
        failed_log: Optional[Union[str, Path]] = None,
        image_prompts_path: Optional[Union[str, Path]] = None,
        instance_name: Optional[str] = None,
    ) -> dict:
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        if profile_key is None:
            profile_key = getattr(self, "_current_prompt_profile_key", PROMPTS_DEFAULT_KEY) or PROMPTS_DEFAULT_KEY
        prompts_file = prompts_path or self._prompts_path(profile_key)
        env["SORA_PROMPTS_FILE"] = str(_project_path(prompts_file))
        instance_label = instance_name or self._prompt_profile_label(profile_key)
        env["SORA_INSTANCE_NAME"] = instance_label
        submitted_path = submitted_log or self._profile_submitted_log_path(profile_key)
        failed_path = failed_log or self._profile_failed_log_path(profile_key)
        try:
            Path(submitted_path).parent.mkdir(parents=True, exist_ok=True)
            Path(failed_path).parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        env["SORA_SUBMITTED_LOG"] = str(_project_path(submitted_path))
        env["SORA_FAILED_LOG"] = str(_project_path(failed_path))
        port = chrome_port or self._resolve_chrome_port(profile_key)
        env["SORA_CDP_ENDPOINT"] = f"http://127.0.0.1:{int(port)}"
        genai_cfg = self.cfg.get("google_genai", {}) or {}
        genai_enabled = bool(genai_cfg.get("enabled"))
        if force_images is True:
            genai_enabled = True
        elif force_images is False:
            genai_enabled = False
        if genai_enabled and not genai_cfg.get("api_key", "").strip():
            genai_enabled = False
        env["GENAI_ENABLED"] = "1" if genai_enabled else "0"
        env["GENAI_API_KEY"] = genai_cfg.get("api_key", "").strip()
        env["GENAI_MODEL"] = genai_cfg.get("model", "").strip()
        env["GENAI_PERSON_GENERATION"] = genai_cfg.get("person_generation", "").strip()
        env["GENAI_ASPECT_RATIO"] = genai_cfg.get("aspect_ratio", "").strip()
        env["GENAI_IMAGE_SIZE"] = genai_cfg.get("image_size", "").strip()
        env["GENAI_OUTPUT_MIME_TYPE"] = genai_cfg.get("output_mime_type", "").strip()
        env["GENAI_NUMBER_OF_IMAGES"] = str(int(genai_cfg.get("number_of_images", 1) or 1))
        env["GENAI_RATE_LIMIT"] = str(int(genai_cfg.get("rate_limit_per_minute", 0) or 0))
        env["GENAI_MAX_RETRIES"] = str(int(genai_cfg.get("max_retries", 3) or 0))
        env["GENAI_OUTPUT_DIR"] = str(_project_path(genai_cfg.get("output_dir", str(IMAGES_DIR))))
        env["GENAI_BASE_DIR"] = str(_project_path(self.cfg.get("project_root", PROJECT_ROOT)))
        env["GENAI_PROMPTS_DIR"] = str(Path(env["SORA_PROMPTS_FILE"]).parent.resolve())
        image_prompts = image_prompts_path or self._image_prompts_path()
        env["GENAI_IMAGE_PROMPTS_FILE"] = str(_project_path(image_prompts))
        env["GENAI_ATTACH_TO_SORA"] = "1" if bool(genai_cfg.get("attach_to_sora", True)) else "0"
        manifest_raw = genai_cfg.get("manifest_file") or (Path(genai_cfg.get("output_dir", str(IMAGES_DIR))) / "manifest.json")
        env["GENAI_MANIFEST_FILE"] = str(_project_path(manifest_raw))
        seeds_value = genai_cfg.get("seeds", "")
        if isinstance(seeds_value, (list, tuple)):
            seeds_text = ",".join(str(item).strip() for item in seeds_value if str(item).strip())
        else:
            seeds_text = str(seeds_value or "").strip()
        env["GENAI_SEEDS"] = seeds_text
        env["GENAI_CONSISTENT_CHARACTER"] = "1" if bool(genai_cfg.get("consistent_character_design")) else "0"
        env["GENAI_LENS_TYPE"] = str(genai_cfg.get("lens_type", "")).strip()
        env["GENAI_COLOR_PALETTE"] = str(genai_cfg.get("color_palette", "")).strip()
        env["GENAI_STYLE_PRESET"] = str(genai_cfg.get("style", "")).strip()
        env["GENAI_REFERENCE_HINT"] = str(genai_cfg.get("reference_prompt", "")).strip()
        env["GENAI_QUOTA_ENABLED"] = "1" if bool(genai_cfg.get("notifications_enabled", True)) else "0"
        env["GENAI_DAILY_QUOTA"] = str(int(genai_cfg.get("daily_quota", 0) or 0))
        env["GENAI_QUOTA_WARNING_LEFT"] = str(int(genai_cfg.get("quota_warning_prompts", 0) or 0))
        env["GENAI_QUOTA_ENFORCE"] = "1" if bool(genai_cfg.get("quota_enforce", False)) else "0"
        usage_raw = genai_cfg.get("usage_file") or str(Path(genai_cfg.get("output_dir", str(IMAGES_DIR))) / "usage.json")
        env["GENAI_USAGE_FILE"] = str(_project_path(usage_raw))
        env["GENAI_IMAGES_ONLY"] = "1" if images_only else "0"
        return env

    def _save_and_run_autogen(self, force_images: Optional[bool] = None, *, images_only: bool = False):
        if images_only:
            self._save_image_prompts()
        else:
            self._save_prompts()
        if force_images:
            genai_cfg = self.cfg.get("google_genai", {}) or {}
            if not genai_cfg.get("api_key", "").strip():
                QtWidgets.QMessageBox.warning(
                    self,
                    "Google AI Studio",
                    "Укажи API-ключ Google AI Studio в настройках, чтобы запустить генерацию изображений.",
                )
                return
        if not images_only:
            sl = WORKERS_DIR / "autogen" / "submitted.log"
            if sl.exists():
                box = QtWidgets.QMessageBox.question(self, "Очистить submitted.log?", "Очистить submitted.log перед запуском?",
                                                     QtWidgets.QMessageBox.StandardButton.Yes|QtWidgets.QMessageBox.StandardButton.No)
                if box == QtWidgets.QMessageBox.StandardButton.Yes:
                    try: sl.unlink()
                    except: pass
        # НЕ блокируем UI: запускаем через ProcRunner
        workdir=self.cfg.get("autogen",{}).get("workdir", str(WORKERS_DIR / "autogen"))
        entry=self.cfg.get("autogen",{}).get("entry","main.py")
        env = self._autogen_env(force_images=force_images, images_only=images_only)
        status_msg = "Вставка промптов…"
        if images_only:
            status_msg = "Только генерация картинок…"
        elif force_images:
            status_msg = "Генерация картинок и вставка промптов…"
        self._post_status(status_msg, state="running")
        self.runner_autogen.run([sys.executable, entry], cwd=workdir, env=env)

    def _save_and_run_autogen_images(self):
        self._save_and_run_autogen(force_images=True, images_only=True)

    def _titles_path(self)->Path:
        return _project_path(self.cfg.get("titles_file", str(TITLES_FILE)))

    def _cursor_path(self)->Path:
        p = self._titles_path()
        return Path(os.path.splitext(str(p))[0] + ".cursor")

    def _load_titles(self):
        p=self._titles_path()
        txt = p.read_text(encoding="utf-8") if p.exists() else ""
        self.ed_titles.setPlainText(txt)
        self._post_status(f"Названия загружены ({p})", state="idle")

    def _save_titles(self):
        p=self._titles_path(); p.parent.mkdir(parents=True,exist_ok=True)
        p.write_text(self.ed_titles.toPlainText(), encoding="utf-8")
        self._post_status("Названия сохранены", state="ok")

    def _reset_titles_cursor(self):
        c=self._cursor_path()
        try:
            if c.exists(): c.unlink(); self._post_status("Cursor сброшен", state="ok")
            else: self._post_status("Cursor не найден", state="idle")
        except Exception as e:
            self._post_status(f"Не удалось удалить cursor: {e}", state="error")

    # ----- Apply DL limit -----
    def _apply_dl_limit(self):
        n = int(self.sb_max_videos.value())
        dl_cfg = self.cfg.setdefault("downloader", {})
        dl_cfg["max_videos"] = n
        dl_cfg["open_drafts"] = bool(self.chk_open_drafts_global.isChecked())
        save_cfg(self.cfg)
        self._post_status(f"Будут скачаны последние {n if n>0 else 'ВСЕ'}", state="ok")

    # ----- Merge opts -----
    def _apply_merge_opts(self):
        n = int(self.sb_merge_group.value())
        self.cfg.setdefault("merge", {})["group_size"] = n
        save_cfg(self.cfg)
        self._post_status(f"Склеивать по {n} клипов", state="ok")

    # ----- Automator -----
    def _automator_session_choices(self) -> List[Tuple[str, str]]:
        choices: List[Tuple[str, str]] = []
        for session_id in self._session_order:
            session = self._session_cache.get(session_id)
            if not session:
                continue
            label = self._session_instance_label(session)
            choices.append((session_id, label))
        return choices

    def _describe_automator_step(self, step: Dict[str, Any]) -> str:
        step_type = step.get("type", "")
        label_map = {
            "session_prompts": "✍️ Промпты",
            "session_images": "🖼️ Картинки",
            "session_mix": "🪄 Промпты + картинки",
            "session_download": "⬇️ Скачивание",
            "session_watermark": "🧼 Замена знака",
            "session_chrome": "🚀 Chrome",
            "global_blur": "🌫️ Блюр",
            "global_merge": "🧵 Склейка",
            "global_watermark": "🧼 Замена знака (глобально)",
            "global_probe": "🧐 Проверка ВЗ",
        }
        base = label_map.get(step_type, str(step_type))
        if step_type.startswith("session_"):
            sessions = step.get("sessions") or []
            names: List[str] = []
            for sid in sessions:
                session = self._session_cache.get(sid)
                if session:
                    names.append(self._session_instance_label(session))
                else:
                    names.append(f"{sid}")
            extra = ", ".join(names)
            if step_type == "session_download":
                limit = int(step.get("limit", 0) or 0)
                if limit > 0:
                    extra = f"{extra} · {limit} шт."
                else:
                    extra = f"{extra} · лимит по настройкам"
            return f"{base}: {extra}"
        if step_type == "global_merge":
            group = int(step.get("group", 0) or 0)
            if group > 0:
                return f"{base} по {group}"
            return f"{base} (по настройкам)"
        if step_type == "global_probe":
            return f"{base} ({'flip' if step.get('flip', True) else 'scan-only'})"
        return base

    def _format_automator_step(self, step: Dict[str, Any], idx: int) -> str:
        return f"{idx}. {self._describe_automator_step(step)}"

    def _refresh_automator_presets(self):
        if not hasattr(self, "cmb_automator_presets"):
            return
        self.cmb_automator_presets.blockSignals(True)
        self.cmb_automator_presets.clear()
        for preset in self._automator_presets:
            self.cmb_automator_presets.addItem(preset.get("name", ""), preset.get("id"))
        self.cmb_automator_presets.blockSignals(False)
        self._update_automator_buttons()

    def _refresh_automator_list(self):
        if not hasattr(self, "lst_automator"):
            return
        self.lst_automator.blockSignals(True)
        self.lst_automator.clear()
        for idx, step in enumerate(self._automator_steps, start=1):
            item = QtWidgets.QListWidgetItem(self._format_automator_step(step, idx))
            item.setData(QtCore.Qt.ItemDataRole.UserRole, idx - 1)
            self.lst_automator.addItem(item)
        self.lst_automator.blockSignals(False)
        self._update_automator_buttons()

    def _update_automator_buttons(self):
        if not hasattr(self, "lst_automator"):
            return
        has_items = bool(self._automator_steps)
        current_row = self.lst_automator.currentRow()
        has_selection = current_row >= 0 and current_row < len(self._automator_steps)
        has_presets = bool(self._automator_presets)
        selected_preset = has_presets and hasattr(self, "cmb_automator_presets") and self.cmb_automator_presets.currentIndex() >= 0
        for attr in ("btn_automator_edit", "btn_automator_remove", "btn_automator_up", "btn_automator_down"):
            btn = getattr(self, attr, None)
            if btn:
                btn.setEnabled(has_selection)
        if hasattr(self, "btn_automator_clear"):
            self.btn_automator_clear.setEnabled(has_items)
        if hasattr(self, "btn_run_automator"):
            self.btn_run_automator.setEnabled(has_items)
        for attr, enabled in (
            ("btn_automator_preset_apply", bool(selected_preset)),
            ("btn_automator_preset_append", bool(selected_preset)),
            ("btn_automator_preset_delete", bool(selected_preset)),
            ("btn_automator_preset_save", bool(has_items)),
        ):
            btn = getattr(self, attr, None)
            if btn:
                btn.setEnabled(enabled)

    def _persist_automator(self):
        automator = self.cfg.setdefault("automator", {})
        automator["steps"] = [dict(step) for step in self._automator_steps]
        automator["presets"] = [dict(preset) for preset in self._automator_presets]
        save_cfg(self.cfg)

    def _selected_automator_preset(self) -> Tuple[Optional[Dict[str, Any]], int]:
        if not hasattr(self, "cmb_automator_presets"):
            return None, -1
        idx = self.cmb_automator_presets.currentIndex()
        if idx < 0 or idx >= len(self._automator_presets):
            return None, idx
        preset_id = self.cmb_automator_presets.currentData()
        for preset_idx, preset in enumerate(self._automator_presets):
            if preset.get("id") == preset_id:
                return preset, preset_idx
        return None, idx

    def _on_automator_preset_apply(self, *, append: bool = False):
        preset, _ = self._selected_automator_preset()
        if not preset:
            return
        steps = [dict(step) for step in preset.get("steps", [])]
        if not append:
            if self._automator_steps:
                confirm = QtWidgets.QMessageBox.question(
                    self,
                    "Заменить шаги",
                    "Заменить текущий список шагов выбранным пресетом?",
                    QtWidgets.QMessageBox.StandardButton.Yes,
                    QtWidgets.QMessageBox.StandardButton.No,
                )
                if confirm != QtWidgets.QMessageBox.StandardButton.Yes:
                    return
            self._automator_steps = steps
        else:
            self._automator_steps.extend(steps)
        self._persist_automator()
        self._refresh_automator_list()

    def _on_automator_preset_save(self):
        if not self._automator_steps:
            QtWidgets.QMessageBox.information(self, "Сохранение пресета", "Добавь хотя бы один шаг для сохранения")
            return
        current_name = ""
        preset, idx = self._selected_automator_preset()
        if preset and idx >= 0:
            current_name = preset.get("name", "")
        name, ok = QtWidgets.QInputDialog.getText(
            self,
            "Название пресета",
            "Как подписать цепочку?",
            text=current_name or "Новый пресет",
        )
        if not ok:
            return
        name = str(name).strip()
        if not name:
            return

        existing = None
        for preset_item in self._automator_presets:
            if preset_item.get("name") == name:
                existing = preset_item
                break
        if existing:
            existing["steps"] = [dict(step) for step in self._automator_steps]
        else:
            self._automator_presets.append(
                {
                    "id": uuid.uuid4().hex[:8],
                    "name": name,
                    "steps": [dict(step) for step in self._automator_steps],
                }
            )
        self._persist_automator()
        self._refresh_automator_presets()
        target_id = existing.get("id") if existing else self._automator_presets[-1].get("id")
        idx = self.cmb_automator_presets.findData(target_id)
        if idx >= 0:
            self.cmb_automator_presets.setCurrentIndex(idx)

    def _on_automator_preset_delete(self):
        preset, idx = self._selected_automator_preset()
        if not preset or idx < 0:
            return
        confirm = QtWidgets.QMessageBox.question(
            self,
            "Удалить пресет",
            f"Удалить пресет “{preset.get('name', 'Без названия')}”?",
            QtWidgets.QMessageBox.StandardButton.Yes,
            QtWidgets.QMessageBox.StandardButton.No,
        )
        if confirm != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        self._automator_presets.pop(idx)
        self._persist_automator()
        self._refresh_automator_presets()

    def _on_automator_add(self):
        dialog = AutomatorStepDialog(self, self._automator_session_choices())
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            self._automator_steps.append(dialog.get_data())
            self._persist_automator()
            self._refresh_automator_list()

    def _on_automator_edit(self):
        if not hasattr(self, "lst_automator"):
            return
        row = self.lst_automator.currentRow()
        if row < 0 or row >= len(self._automator_steps):
            return
        dialog = AutomatorStepDialog(self, self._automator_session_choices(), step=self._automator_steps[row])
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            self._automator_steps[row] = dialog.get_data()
            self._persist_automator()
            self._refresh_automator_list()
            self.lst_automator.setCurrentRow(row)

    def _on_automator_remove(self):
        if not hasattr(self, "lst_automator"):
            return
        row = self.lst_automator.currentRow()
        if row < 0 or row >= len(self._automator_steps):
            return
        self._automator_steps.pop(row)
        self._persist_automator()
        self._refresh_automator_list()
        if row and row - 1 < self.lst_automator.count():
            self.lst_automator.setCurrentRow(row - 1)

    def _on_automator_move(self, direction: int):
        if not hasattr(self, "lst_automator"):
            return
        row = self.lst_automator.currentRow()
        if row < 0 or row >= len(self._automator_steps):
            return
        new_row = row + direction
        if new_row < 0 or new_row >= len(self._automator_steps):
            return
        self._automator_steps[row], self._automator_steps[new_row] = (
            self._automator_steps[new_row],
            self._automator_steps[row],
        )
        self._persist_automator()
        self._refresh_automator_list()
        self.lst_automator.setCurrentRow(new_row)

    def _on_automator_clear(self):
        if not self._automator_steps:
            return
        confirm = QtWidgets.QMessageBox.question(
            self,
            "Очистить шаги",
            "Удалить все шаги автоматизации?",
            QtWidgets.QMessageBox.StandardButton.Yes,
            QtWidgets.QMessageBox.StandardButton.No,
        )
        if confirm != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        self._automator_steps.clear()
        self._persist_automator()
        self._refresh_automator_list()

    def _open_sora_profile_page(self):
        try:
            webbrowser.open("https://sora.chatgpt.com/profile")
            self._post_status("Открываю профиль Sora — реши проверку, если появится", state="info")
        except Exception as exc:  # noqa: BLE001
            self._post_status(f"Не удалось открыть профиль: {exc}", state="error")

    def _run_automator(self):
        if self._automator_running:
            self._post_status("Автоматизация уже выполняется", state="error")
            return

        steps = list(self._automator_steps)
        if not steps:
            self._post_status("Список шагов пуст", state="error")
            return
        summary = " → ".join(self._describe_automator_step(step) for step in steps)
        self._append_activity(f"Автоматизация: {summary}", kind="info", card_text=False)

        self._automator_queue = steps
        self._automator_total = len(steps)
        self._automator_index = 0
        self._automator_ok_all = True
        self._automator_running = True
        self._automator_waiting = False

        self._post_status(
            "Автоматизация запускается…",
            progress=0,
            total=self._automator_total,
            state="running",
        )
        QtCore.QTimer.singleShot(0, self._automator_tick)

    def _automator_tick(self):
        if not self._automator_running:
            return
        if self._automator_waiting:
            return
        if self._automator_index >= self._automator_total:
            state = "ok" if self._automator_ok_all else "error"
            progress = self._automator_total if self._automator_ok_all else max(
                0, self._automator_total - 1
            )
            text = "Автоматизация завершена" if self._automator_ok_all else (
                f"Автоматизация остановлена на шаге {self._automator_total}"
            )
            self._post_status(text, progress=progress, total=self._automator_total, state=state)
            self._automator_running = False
            self._refresh_stats()
            return

        idx = self._automator_index + 1
        step = self._automator_queue[self._automator_index]
        description = self._describe_automator_step(step)
        self._append_activity(
            f"Автоматизация: шаг {idx}/{self._automator_total} — {description}",
            kind="running",
            card_text=False,
        )

        def run_step():
            def on_done(ok: bool):
                self._automator_waiting = False
                self._finish_automator_step(ok, idx)

            result = self._execute_automator_step(
                step, idx, self._automator_total, async_done=on_done
            )
            if result is not None:
                self._automator_waiting = False
                self._finish_automator_step(bool(result), idx)
            else:
                self._automator_waiting = True

        QtCore.QTimer.singleShot(0, run_step)

    def _finish_automator_step(self, ok: bool, idx: int):
        if not ok:
            self._automator_ok_all = False
            self._append_activity(
                f"Автоматизация: шаг {idx} завершился ошибкой", kind="error", card_text=False
            )
            self._post_status(
                f"Автоматизация остановлена на шаге {idx}",
                progress=max(0, idx - 1),
                total=self._automator_total,
                state="error",
            )
            self._automator_running = False
            self._refresh_stats()
            return

        self._append_activity(
            f"Автоматизация: шаг {idx} выполнен", kind="success", card_text=False
        )
        self._post_status(
            f"Шаг {idx}/{self._automator_total} завершён",
            progress=idx,
            total=self._automator_total,
            state="running",
        )
        self._automator_index += 1
        QtCore.QTimer.singleShot(0, self._automator_tick)

    def _execute_automator_step(
        self,
        step: Dict[str, Any],
        idx: int,
        total: int,
        *,
        async_done: Optional[Callable[[bool], None]] = None,
    ) -> Optional[bool]:
        step_type = step.get("type", "")
        if step_type.startswith("session_"):
            sessions = step.get("sessions") or []
            if not sessions:
                return False
            if step_type == "session_chrome":
                for sid in sessions:
                    session = self._session_cache.get(sid)
                    if not session:
                        self._append_activity(f"Сессия {sid} не найдена", kind="error")
                        return False
                    label = self._session_instance_label(session)
                    self._post_status(
                        f"Шаг {idx}/{total}: {self._describe_automator_step(step)} → {label}",
                        state="running",
                    )
                    ok = bool(self._open_chrome(session=session))
                    if not ok:
                        self._append_activity(
                            f"Chrome не запущен для {label}", kind="error", card_text=False
                        )
                        return False
                return True
            limit_override = int(step.get("limit", 0) or 0) if step_type == "session_download" else 0
            if async_done:
                remaining = list(sessions)

                def run_next():
                    if not remaining:
                        async_done(True)
                        return
                    sid = remaining.pop(0)
                    session = self._session_cache.get(sid)
                    if not session:
                        self._append_activity(f"Сессия {sid} не найдена", kind="error")
                        async_done(False)
                        return
                    label = self._session_instance_label(session)
                    self._post_status(
                        f"Шаг {idx}/{total}: {self._describe_automator_step(step)} → {label}",
                        state="running",
                    )

                    def after(ok: bool):
                        if not ok:
                            async_done(False)
                        else:
                            run_next()

                    self._automator_run_session_task(
                        sid,
                        step_type,
                        limit=(limit_override if limit_override > 0 else None),
                        async_done=after,
                    )

                run_next()
                return None

            for sid in sessions:
                session = self._session_cache.get(sid)
                if not session:
                    self._append_activity(f"Сессия {sid} не найдена", kind="error")
                    return False
                label = self._session_instance_label(session)
                self._post_status(
                    f"Шаг {idx}/{total}: {self._describe_automator_step(step)} → {label}",
                    state="running",
                )
                ok = self._automator_run_session_task(
                    sid,
                    step_type,
                    limit=(limit_override if limit_override > 0 else None),
                )
                if not ok:
                    return False
            return True
        if step_type == "global_blur":
            return self._run_blur_presets_sync()
        if step_type == "global_merge":
            group = int(step.get("group", 0) or 0)
            group_override = group if group > 0 else None
            return self._run_merge_sync(group_override=group_override)
        if step_type == "global_watermark":
            return self._run_watermark_restore_sync()
        if step_type == "global_probe":
            return self._watermark_probe_batch_job(flip=bool(step.get("flip", True)), silent=True)
        return False

    def _automator_run_session_task(
        self,
        session_id: str,
        step_type: str,
        *,
        limit: Optional[int] = None,
        async_done: Optional[Callable[[bool], None]] = None,
    ) -> bool:
        expected_task = {
            "session_prompts": "autogen_prompts",
            "session_images": "autogen_images",
            "session_mix": "autogen_mix",
            "session_download": "download",
            "session_watermark": "watermark",
        }.get(step_type, "")
        if not expected_task:
            return False

        token, waiter = self._register_session_waiter(session_id, expected_task)
        started = {"ok": False}
        done = threading.Event()

        def start_task():
            try:
                session = self._session_cache.get(session_id) or {}
                if step_type == "session_prompts":
                    self._run_session_autogen(session_id)
                elif step_type == "session_images":
                    self._run_session_images(session_id)
                elif step_type == "session_mix":
                    self._run_session_autogen(session_id, force_images=True)
                elif step_type == "session_download":
                    self._run_session_download(
                        session_id,
                        override_limit=limit,
                        open_drafts_override=self._session_open_drafts(session),
                    )
                elif step_type == "session_watermark":
                    self._run_session_watermark(session_id)
            finally:
                state = self._ensure_session_state(session_id)
                started["ok"] = state.get("active_task") == expected_task
                done.set()

        QtCore.QMetaObject.invokeMethod(
            self,
            "_run_on_ui",
            QtCore.Qt.ConnectionType.QueuedConnection,
            QtCore.Q_ARG(object, start_task),
        )
        
        def handle_launch_timeout() -> bool:
            if not done.wait(5.0):
                self._append_activity(
                    f"Сессия {session_id}: задача не запустилась вовремя", kind="error", card_text=False
                )
                self._cancel_session_waiter(token)
                return False
            if not started["ok"]:
                self._cancel_session_waiter(token)
                return False
            return True

        if async_done:
            def waiter_thread():
                launch_ok = handle_launch_timeout()
                if not launch_ok:
                    self._run_on_ui(lambda: async_done(False))
                    return
                rc = self._wait_for_session(token, waiter)
                self._run_on_ui(lambda: async_done(rc == 0))

            threading.Thread(target=waiter_thread, daemon=True).start()
            return True

        launch_ok = handle_launch_timeout()
        if not launch_ok:
            return False
        rc = self._wait_for_session(token, waiter)
        return rc == 0

    @QtCore.pyqtSlot(object)
    def _run_on_ui(self, fn: object) -> None:
        if not callable(fn):
            return
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            self._append_activity(
                f"Автоматизация: ошибка запуска задачи — {exc}", kind="error", card_text=False
            )

    # ----- Scenario -----
    def _run_scenario(self):
        steps = []
        if self.cb_do_images.isChecked(): steps.append("images")
        if self.cb_do_autogen.isChecked(): steps.append("autogen")
        if self.cb_do_download.isChecked(): steps.append("download")
        if self.cb_do_blur.isChecked(): steps.append("blur")
        if self.cb_do_watermark.isChecked(): steps.append("watermark")
        if self.cb_do_merge.isChecked(): steps.append("merge")
        if self.cb_do_upload.isChecked(): steps.append("upload")
        if self.cb_do_tiktok.isChecked(): steps.append("tiktok")
        if not steps:
            self._post_status("Ничего не выбрано", state="error"); return

        self._save_settings_clicked(silent=True)
        self._post_status("Запуск сценария…", state="running")
        append_history(self.cfg, {"event":"scenario_start","steps":steps})

        label_map = {
            "images": "Images",
            "autogen": "Autogen",
            "download": "Download",
            "blur": "Blur",
            "watermark": "Watermark",
            "merge": "Merge",
            "upload": "YouTube",
            "tiktok": "TikTok",
        }
        summary = " → ".join(label_map.get(step, step) for step in steps)
        if summary:
            self._append_activity(f"Сценарий: {summary}", kind="info", card_text=False)

        def flow():
            ok_all = True
            if "images" in steps:
                ok = self._run_autogen_sync(force_images=True, images_only=True); ok_all = ok_all and ok
                if not ok:
                    self._post_status("Генерация картинок завершена с ошибкой", state="error")
                    return
            if "autogen" in steps:
                ok = self._run_autogen_sync(); ok_all = ok_all and ok
                if not ok:
                    self._post_status("Вставка промптов завершена с ошибкой", state="error")
                    return
            if "download" in steps:
                ok = self._run_download_sync(); ok_all = ok_all and ok
                if not ok:
                    self._post_status("Скачка завершена с ошибкой", state="error")
                    return
            if "blur" in steps:
                ok = self._run_blur_presets_sync(); ok_all = ok_all and ok
                if not ok:
                    self._post_status("Блюр завершён с ошибкой", state="error")
                    return
            if "watermark" in steps:
                ok = self._run_watermark_restore_sync(); ok_all = ok_all and ok
                if not ok:
                    self._post_status("Замена водяного знака завершена с ошибкой", state="error")
                    return
            if "merge" in steps:
                ok = self._run_merge_sync(); ok_all = ok_all and ok
                if not ok:
                    self._post_status("Склейка завершена с ошибкой", state="error")
                    return
            if "upload" in steps:
                ok = self._run_upload_sync(); ok_all = ok_all and ok
                if not ok:
                    self._post_status("Загрузка YouTube завершена с ошибкой", state="error")
                    return
            if "tiktok" in steps:
                ok = self._run_tiktok_sync(); ok_all = ok_all and ok
            self._post_status("Сценарий завершён", state=("ok" if ok_all else "error"))
            append_history(self.cfg, {"event":"scenario_finish","ok":ok_all})
            self._refresh_stats()

        threading.Thread(target=flow, daemon=True).start()

    # ----- run steps -----
    def _run_autogen(self):
        self._run_autogen_sync()

    def _run_autogen_images(self):
        self._run_autogen_sync(force_images=True, images_only=True)

    def _await_runner(self, runner: ProcRunner, tag: str, starter: Callable[[], None]) -> int:
        if runner.proc and runner.proc.poll() is None:
            self._append_activity(f"{tag}: задача уже выполняется", kind="error", card_text=False)
            return 1

        waiter = threading.Event()
        with self._scenario_wait_lock:
            self._scenario_waiters[tag] = waiter

        try:
            starter()
        except Exception as exc:  # noqa: BLE001
            with self._scenario_wait_lock:
                self._scenario_waiters.pop(tag, None)
                self._scenario_results.pop(tag, None)
            self._append_activity(f"{tag}: запуск не удался ({exc})", kind="error", card_text=False)
            return 1

        # ждём завершения, обновляя ожидание пока сигнал не придёт
        while not waiter.wait(0.25):
            with self._scenario_wait_lock:
                if tag not in self._scenario_waiters:
                    break

        with self._scenario_wait_lock:
            self._scenario_waiters.pop(tag, None)
            rc = self._scenario_results.pop(tag, 1)

        return rc

    def _run_autogen_sync(self, force_images: Optional[bool] = None, *, images_only: bool = False) -> bool:
        self._save_settings_clicked(silent=True)
        workdir=self.cfg.get("autogen",{}).get("workdir", str(WORKERS_DIR / "autogen"))
        entry=self.cfg.get("autogen",{}).get("entry","main.py")
        python=sys.executable; cmd=[python, entry]; env=self._autogen_env(force_images=force_images, images_only=images_only)
        if images_only:
            self._send_tg("🖼️ Autogen (картинки) запускается")
            status_msg = "Только генерация картинок…"
        elif force_images:
            self._send_tg("🖼️ Autogen (картинки) запускается")
            status_msg = "Генерация картинок и вставка промптов…"
        else:
            self._send_tg("✍️ Autogen запускается")
            status_msg = "Вставка промптов…"
        self._post_status(status_msg, state="running")
        rc = self._await_runner(self.runner_autogen, "AUTOGEN", lambda: self.runner_autogen.run(cmd, cwd=workdir, env=env))
        ok = rc == 0
        if images_only or force_images:
            self._send_tg("🖼️ Autogen (картинки) завершён" if ok else "⚠️ Autogen (картинки) завершён с ошибками")
        else:
            self._send_tg("✍️ Autogen завершён" if ok else "⚠️ Autogen завершён с ошибками")
        return ok

    def _run_download(self):
        self._run_download_sync()

    def _run_download_sync(self) -> bool:
        dest_dir = _project_path(self.cfg.get("downloads_dir", str(DL_DIR)))
        before = len(self._iter_videos(dest_dir)) if dest_dir.exists() else 0

        dl_cfg = self.cfg.get("downloader", {}) or {}
        workdir = dl_cfg.get("workdir", str(WORKERS_DIR / "downloader"))
        entry = dl_cfg.get("entry", "download_all.py")
        max_v = int(dl_cfg.get("max_videos", 0) or 0)

        python = sys.executable
        cmd = [python, entry]
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["DOWNLOAD_DIR"] = str(dest_dir)
        env["TITLES_FILE"] = str(self._titles_path())
        env["TITLES_CURSOR_FILE"] = str(self._cursor_path())
        env["MAX_VIDEOS"] = str(max_v if max_v > 0 else 0)
        self._send_tg(f"⬇️ Скачивание запускается → {dest_dir}")
        self._post_status("Скачивание…", state="running")
        rc = self._await_runner(self.runner_dl, "DL", lambda: self.runner_dl.run(cmd, cwd=workdir, env=env))
        ok = rc == 0
        after = len(self._iter_videos(dest_dir)) if dest_dir.exists() else before
        delta = max(after - before, 0)
        status = "завершено" if ok else "завершено с ошибками"
        self._post_status(
            f"Скачивание завершено: +{delta} (итого {after})",
            state=("ok" if ok else "error"),
        )
        self._send_tg(f"⬇️ Скачивание {status}: +{delta} файлов (итого {after}) → {dest_dir}")
        self._refresh_stats()
        return ok

    def _run_watermark(self):
        self._run_watermark_restore_sync()

    def _run_watermark_restore_sync(self) -> bool:
        self._save_settings_clicked(silent=True)
        cfg = self.cfg.get("watermark_cleaner", {}) or {}
        workdir = cfg.get("workdir", str(WORKERS_DIR / "watermark_cleaner"))
        entry = cfg.get("entry", "restore.py")
        source_dir = _project_path(cfg.get("source_dir", self.cfg.get("downloads_dir", str(DL_DIR))))
        output_dir = _project_path(cfg.get("output_dir", str(PROJECT_ROOT / "restored")))
        template_path = _project_path(cfg.get("template", str(PROJECT_ROOT / "watermark.png")))

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["WMR_SOURCE_DIR"] = str(source_dir)
        env["WMR_OUTPUT_DIR"] = str(output_dir)
        env["WMR_TEMPLATE"] = str(template_path)
        env["WMR_MASK_THRESHOLD"] = str(int(cfg.get("mask_threshold", 8) or 0))
        env["WMR_THRESHOLD"] = str(float(cfg.get("threshold", 0.78) or 0.78))
        env["WMR_FRAMES"] = str(int(cfg.get("frames", 120) or 1))
        env["WMR_DOWNSCALE"] = str(int(cfg.get("downscale", 1080) or 0))
        env["WMR_SCALE_MIN"] = str(float(cfg.get("scale_min", 0.85) or 0.85))
        env["WMR_SCALE_MAX"] = str(float(cfg.get("scale_max", 1.2) or 1.2))
        env["WMR_SCALE_STEPS"] = str(int(cfg.get("scale_steps", 9) or 3))
        env["WMR_PADDING_PX"] = str(int(cfg.get("padding_px", 12) or 0))
        env["WMR_PADDING_PCT"] = str(float(cfg.get("padding_pct", 0.18) or 0.0))
        env["WMR_MIN_SIZE"] = str(int(cfg.get("min_size", 32) or 2))
        env["WMR_SEARCH_SPAN"] = str(int(cfg.get("search_span", 12) or 1))
        env["WMR_POOL"] = str(int(cfg.get("pool", 4) or 1))
        env["WMR_MAX_IOU"] = str(float(cfg.get("max_iou", 0.25) or 0.0))
        env["WMR_BLEND"] = str(cfg.get("blend", "normal") or "normal")
        env["WMR_INPAINT_RADIUS"] = str(int(cfg.get("inpaint_radius", 6) or 1))
        env["WMR_INPAINT_METHOD"] = str(cfg.get("inpaint_method", "telea") or "telea")
        env["WMR_FULL_SCAN"] = "1" if bool(cfg.get("full_scan")) else "0"

        python = sys.executable
        cmd = [python, entry]
        total = len(self._iter_videos(source_dir)) if source_dir.exists() else 0
        self._post_status("Замена водяного знака…", state="running")
        self._send_tg(f"🧼 Замена водяного знака запускается: {total} файлов → {output_dir}")

        rc = self._await_runner(
            self.runner_watermark,
            "WMR",
            lambda: self.runner_watermark.run(cmd, cwd=workdir, env=env),
        )
        ok = rc == 0
        status = "завершена" if ok else "с ошибками"
        self._post_status(
            "Замена водяного знака завершена" if ok else "Замена водяного знака: ошибки",
            state=("ok" if ok else "error"),
        )
        self._send_tg(f"🧼 Замена водяного знака {status}: {total} файлов → {output_dir}")
        self._refresh_stats()
        return ok

    def _probe_region_tuple(self) -> Tuple[int, int, int, int]:
        return (
            max(0, int(self.sb_probe_x.value() if hasattr(self, "sb_probe_x") else 0)),
            max(0, int(self.sb_probe_y.value() if hasattr(self, "sb_probe_y") else 0)),
            max(1, int(self.sb_probe_w.value() if hasattr(self, "sb_probe_w") else 1)),
            max(1, int(self.sb_probe_h.value() if hasattr(self, "sb_probe_h") else 1)),
        )

    def _open_watermark_probe_preview(self):
        try:
            from blur_preview import BlurPreviewDialog, VIDEO_PREVIEW_AVAILABLE, VIDEO_PREVIEW_TIP  # type: ignore
        except Exception as exc:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(
                self,
                "Предпросмотр недоступен",
                f"Не удалось загрузить модуль предпросмотра: {exc}\n{VIDEO_PREVIEW_TIP}",
            )
            return

        source = _project_path(self.ed_probe_source.text().strip() or self.cfg.get("downloads_dir", str(DL_DIR)))
        zones = [
            {
                "x": self.sb_probe_x.value(),
                "y": self.sb_probe_y.value(),
                "w": self.sb_probe_w.value(),
                "h": self.sb_probe_h.value(),
            }
        ]
        dlg = BlurPreviewDialog(self, "Проверка водяного знака", zones, [source])
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            updated = dlg.zones()
            if updated:
                zone = updated[0]
                self.sb_probe_x.setValue(int(zone.get("x", 0)))
                self.sb_probe_y.setValue(int(zone.get("y", 0)))
                self.sb_probe_w.setValue(max(1, int(zone.get("w", 1))))
                self.sb_probe_h.setValue(max(1, int(zone.get("h", 1))))
                self._mark_settings_dirty()

    def _resolve_probe_video(self, prompt_if_missing: bool = False) -> Optional[Path]:
        raw = self.ed_probe_video.text().strip()
        if raw:
            return _project_path(raw)
        if not prompt_if_missing:
            return None
        dlg = QtWidgets.QFileDialog(self, "Выбери видео для проверки")
        dlg.setFileMode(QtWidgets.QFileDialog.FileMode.ExistingFile)
        dlg.setNameFilter("Видео (*.mp4 *.mov *.m4v *.webm);;Все файлы (*.*)")
        base = self.ed_probe_source.text().strip()
        if base and os.path.isdir(base):
            dlg.setDirectory(base)
        if dlg.exec():
            sel = dlg.selectedFiles()
            if sel:
                self.ed_probe_video.setText(sel[0])
                return Path(sel[0])
        return None

    def _run_watermark_probe(self, *, flip: bool = False) -> None:
        try:
            from watermark_detector import flip_video_with_check, scan_region_for_flash  # type: ignore[import]
        except Exception as exc:  # noqa: BLE001
            QtWidgets.QMessageBox.critical(self, "Водяной знак", f"Не удалось загрузить watermark_detector: {exc}")
            return

        video_path = self._resolve_probe_video(prompt_if_missing=True)
        if not video_path:
            self._post_status("Выбери видео для проверки", state="error")
            return

        region = self._probe_region_tuple()
        frames = int(self.sb_probe_frames.value()) if hasattr(self, "sb_probe_frames") else 120
        brightness = int(self.sb_probe_brightness.value()) if hasattr(self, "sb_probe_brightness") else 245
        coverage = float(self.dsb_probe_coverage.value()) if hasattr(self, "dsb_probe_coverage") else 0.02
        edge_ratio = float(self.dsb_probe_edge_ratio.value()) if hasattr(self, "dsb_probe_edge_ratio") else 0.006
        downscale = float(self.dsb_probe_downscale.value()) if hasattr(self, "dsb_probe_downscale") else 2.0
        min_hits = int(self.sb_probe_hits.value()) if hasattr(self, "sb_probe_hits") else 1
        method = (self.cmb_probe_method.currentData() if hasattr(self, "cmb_probe_method") else None) or "hybrid"
        flip_when = (self.cmb_probe_flip_when.currentData() if hasattr(self, "cmb_probe_flip_when") else None) or "missing"
        flip_direction = (self.cmb_probe_direction.currentData() if hasattr(self, "cmb_probe_direction") else None) or "left"

        try:
            detected = scan_region_for_flash(
                video_path,
                region,
                frames=frames,
                brightness_threshold=brightness,
                coverage_ratio=coverage,
                method=method,
                edge_ratio_threshold=edge_ratio,
                min_hits=min_hits,
                downscale=downscale,
            )
        except Exception as exc:  # noqa: BLE001
            self._post_status(f"Ошибка проверки: {exc}", state="error")
            if hasattr(self, "lbl_probe_status"):
                self.lbl_probe_status.setText(f"Ошибка: {exc}")
            return

        status_parts = ["Знак" + (" найден" if detected else " не найден"), f"кадров: {frames}"]
        if hasattr(self, "lbl_probe_status"):
            self.lbl_probe_status.setText(" · ".join(status_parts))

        if not flip:
            self._append_activity(f"Проверка водяного знака: {'найден' if detected else 'не найден'}", kind="info")
            return

        output_dir = _project_path(self.ed_probe_output_dir.text().strip() or video_path.parent)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / video_path.name if bool(self.cb_probe_autosave.isChecked()) else None

        res = flip_video_with_check(
            video_path,
            region=region,
            output_path=output_path,
            frames=frames,
            brightness_threshold=brightness,
            coverage_ratio=coverage,
            method=method,
            edge_ratio_threshold=edge_ratio,
            min_hits=min_hits,
            downscale=downscale,
            flip_when=flip_when,
            flip_direction=flip_direction,
        )
        flipped = bool(res.get("flipped"))
        out_path = Path(res.get("output", video_path))
        msg = (
            f"Флип выполнен ({res.get('filter', 'hflip')}), знак {'найден' if res.get('detected') else 'не найден'}"
            if flipped
            else f"Флип пропущен (условие не выполнено), знак {'найден' if res.get('detected') else 'не найден'}"
        )
        self._append_activity(msg, kind="success" if flipped else "info")
        self._post_status(msg, state="ok" if flipped else "warn")
        if hasattr(self, "lbl_probe_status"):
            self.lbl_probe_status.setText(msg + f" → {out_path}")
        if flipped and not self.cb_probe_autosave.isChecked():
            self._open_file(out_path)

    def _watermark_probe_batch_job(
        self,
        *,
        flip: bool = False,
        silent: bool = False,
        source_dir: Optional[Path] = None,
        output_dir: Optional[Path] = None,
        custom_files: Optional[List[Path]] = None,
    ) -> bool:
        try:
            from watermark_detector import flip_video_with_check, scan_region_for_flash  # type: ignore[import]
        except Exception as exc:  # noqa: BLE001
            if not silent:
                QtWidgets.QMessageBox.critical(self, "Водяной знак", f"Не удалось загрузить watermark_detector: {exc}")
            else:
                self._append_activity(f"WM probe: ошибка импорта {exc}", kind="error", card_text=False)
            return False

        probe_cfg = self.cfg.get("watermark_probe", {}) or {}
        region_cfg = probe_cfg.get("region", {}) or {}
        region = (
            int(region_cfg.get("x", 0)),
            int(region_cfg.get("y", 0)),
            int(region_cfg.get("w", 320)),
            int(region_cfg.get("h", 120)),
        )

        frames = int(self.sb_probe_frames.value()) if hasattr(self, "sb_probe_frames") else int(probe_cfg.get("frames", 120) or 120)
        brightness = int(self.sb_probe_brightness.value()) if hasattr(self, "sb_probe_brightness") else int(probe_cfg.get("brightness_threshold", 245) or 245)
        coverage = float(self.dsb_probe_coverage.value()) if hasattr(self, "dsb_probe_coverage") else float(probe_cfg.get("coverage_ratio", 0.002) or 0.002)
        edge_ratio = float(self.dsb_probe_edge_ratio.value()) if hasattr(self, "dsb_probe_edge_ratio") else float(probe_cfg.get("edge_ratio", 0.006) or 0.006)
        downscale = float(self.dsb_probe_downscale.value()) if hasattr(self, "dsb_probe_downscale") else float(probe_cfg.get("downscale", 2.0) or 2.0)
        min_hits = int(self.sb_probe_hits.value()) if hasattr(self, "sb_probe_hits") else int(probe_cfg.get("min_hits", 1) or 1)
        method = (self.cmb_probe_method.currentData() if hasattr(self, "cmb_probe_method") else None) or probe_cfg.get("method", "hybrid")
        flip_when = (self.cmb_probe_flip_when.currentData() if hasattr(self, "cmb_probe_flip_when") else None) or probe_cfg.get("flip_when", "missing")
        flip_direction = (self.cmb_probe_direction.currentData() if hasattr(self, "cmb_probe_direction") else None) or probe_cfg.get("flip_direction", "left")

        autosave = bool(self.cb_probe_autosave.isChecked()) if hasattr(self, "cb_probe_autosave") else True

        if source_dir is not None:
            src_dir = _project_path(source_dir)
        else:
            raw_source = self.ed_probe_source.text().strip() if hasattr(self, "ed_probe_source") else ""
            fallback = probe_cfg.get("source_dir", self.cfg.get("downloads_dir", str(DL_DIR)))
            src_dir = _project_path(raw_source or fallback)
        if not src_dir.exists():
            src_dir = _project_path(probe_cfg.get("source_dir", self.cfg.get("downloads_dir", str(DL_DIR))))
        if not src_dir.exists():
            self._post_status("Папка RAW для проверки не найдена", state="error")
            return False

        dst_dir = _project_path(
            output_dir
            or (self.ed_probe_output_dir.text().strip() if hasattr(self, "ed_probe_output_dir") else None)
            or probe_cfg.get("output_dir", str(PROJECT_ROOT / "restored"))
        )
        if flip:
            dst_dir.mkdir(parents=True, exist_ok=True)

        allowed_ext = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
        files = custom_files or []
        if not files:
            try:
                files = [p for p in sorted(src_dir.iterdir()) if p.is_file() and p.suffix.lower() in allowed_ext]
            except FileNotFoundError:
                files = []
        if not files:
            self._post_status("Нет видео для проверки", state="error")
            return False

        total = len(files)
        hits = 0
        flipped = 0
        failed = 0
        for idx, video in enumerate(files, start=1):
            try:
                detected = scan_region_for_flash(
                    video,
                    region,
                    frames=frames,
                    brightness_threshold=brightness,
                    coverage_ratio=coverage,
                    method=method,
                    edge_ratio_threshold=edge_ratio,
                    min_hits=min_hits,
                    downscale=downscale,
                )
            except Exception as exc:  # noqa: BLE001
                failed += 1
                self._append_activity(f"WM probe: {video.name} ошибка {exc}", kind="error", card_text=False)
                continue

            if detected:
                hits += 1
            if not flip:
                self._post_status(
                    f"Проверка ВЗ {idx}/{total}: {'найден' if detected else 'нет'} → {video.name}",
                    progress=idx,
                    total=total,
                    state="running",
                )
                continue

            target_path = (dst_dir / video.name) if autosave else None
            res = flip_video_with_check(
                video,
                region=region,
                output_path=target_path,
                frames=frames,
                brightness_threshold=brightness,
                coverage_ratio=coverage,
                method=method,
                edge_ratio_threshold=edge_ratio,
                min_hits=min_hits,
                downscale=downscale,
                flip_when=flip_when,
                flip_direction=flip_direction,
            )
            flipped += int(bool(res.get("flipped")))
            state = "ok" if res.get("flipped") else "warn"
            msg = (
                f"Флип {'выполнен' if res.get('flipped') else 'пропущен'}: {'знак есть' if res.get('detected') else 'знака нет'} → {video.name}"
            )
            self._post_status(msg, progress=idx, total=total, state=state)

        summary = f"Проверено {total}, найдено {hits} знаков"
        if flip:
            summary += f", флипнуто {flipped}"
        if failed:
            summary += f", ошибок {failed}"
        self._append_activity(summary, kind="success" if failed == 0 else "warn", card_text=False)
        self._post_status(summary, state="ok" if failed == 0 else "warn")
        return failed == 0

    def _run_watermark_probe_batch(
        self,
        *,
        flip: bool = False,
        source_dir: Optional[Path] = None,
        output_dir: Optional[Path] = None,
        custom_files: Optional[List[Path]] = None,
    ) -> None:
        threading.Thread(
            target=lambda: self._watermark_probe_batch_job(
                flip=flip,
                source_dir=source_dir,
                output_dir=output_dir,
                custom_files=custom_files,
            ),
            daemon=True,
        ).start()

    # ----- BLUR -----
    def _run_blur_presets_sync(self) -> bool:
        ff_cfg = self.cfg.get("ffmpeg", {}) or {}
        ffbin_raw = (ff_cfg.get("binary") or "ffmpeg").strip()
        ffbin = ffbin_raw
        if ffbin_raw:
            candidate = shutil.which(ffbin_raw)
            if candidate:
                ffbin = candidate
            else:
                guessed = Path(ffbin_raw).expanduser()
                if not guessed.is_absolute() and (os.sep in ffbin_raw or ffbin_raw.startswith(".")):
                    guessed = (_project_path(ffbin_raw))
                if guessed.exists():
                    ffbin = str(guessed)

        if not ffbin_raw:
            self._post_status("Не задан путь к ffmpeg", state="error")
            self._append_activity("FFmpeg: не указан путь к бинарю", kind="error")
            return False

        if shutil.which(ffbin) is None and not Path(ffbin).expanduser().exists():
            self._post_status(f"FFmpeg не найден: {ffbin_raw}", state="error")
            self._append_activity("Проверь путь к ffmpeg в настройках → ffmpeg", kind="error")
            self._send_tg("⚠️ FFmpeg не найден. Проверь настройку пути в разделе ffmpeg.")
            return False

        post = ff_cfg.get("post_chain", "").strip()
        vcodec_choice = (ff_cfg.get("vcodec") or "libx264").strip()
        if vcodec_choice == "copy":
            self.sig_log.emit("[BLUR] vcodec=copy несовместим с delogo — переключаю на libx264")
            vcodec_choice = "libx264"
            ff_cfg["vcodec"] = "libx264"
            save_cfg(self.cfg)
            QtCore.QMetaObject.invokeMethod(
                self,
                "_update_vcodec_ui",
                QtCore.Qt.ConnectionType.QueuedConnection,
                QtCore.Q_ARG(str, "libx264"),
            )
        crf = str(int(ff_cfg.get("crf", 18)))
        preset = ff_cfg.get("preset", "veryfast")
        fmt = (ff_cfg.get("format") or "mp4").strip()
        copy_audio = bool(ff_cfg.get("copy_audio", True))
        threads = int(ff_cfg.get("blur_threads", 2) or 1)

        preset_lookup: Dict[str, List[Dict[str, int]]] = {}
        if getattr(self, "_preset_cache", None):
            for name, zones in self._preset_cache.items():
                normalized = normalize_zone_list(zones if isinstance(zones, list) else None)
                preset_lookup[name] = normalized
        else:
            stored = ff_cfg.get("presets") or {}
            for name, body in stored.items():
                raw_list = body.get("zones") if isinstance(body, dict) else None
                preset_lookup[name] = normalize_zone_list(raw_list if isinstance(raw_list, list) else None)

        if not preset_lookup:
            preset_lookup = {
                "default": [{"x": 40, "y": 60, "w": 160, "h": 90}],
            }

        active_ui = ""
        if hasattr(self, "cmb_active_preset"):
            active_ui = self.cmb_active_preset.currentText().strip()
        active = active_ui or (ff_cfg.get("active_preset") or "").strip()
        if not active and preset_lookup:
            active = next(iter(preset_lookup.keys()))

        ff_cfg["active_preset"] = active
        save_cfg(self.cfg)

        raw_zones = preset_lookup.get(active, [])
        zones = []
        for zone in raw_zones:
            try:
                x = int(zone.get("x", 0))
                y = int(zone.get("y", 0))
                w = int(zone.get("w", 0))
                h = int(zone.get("h", 0))
                if w > 0 and h > 0:
                    zones.append({"x": x, "y": y, "w": w, "h": h})
            except Exception:
                continue
        if not zones:
            self._post_status("В пресете нет зон для блюра", state="error")
            return False

        auto_cfg = ff_cfg.get("auto_watermark") or {}
        auto_runtime: Dict[str, object] = {"enabled": False, "reason": ""}
        auto_stats: Dict[str, List[dict]] = {"fallbacks": [], "errors": []}
        if bool(auto_cfg.get("enabled")):
            try:
                from watermark_detector import (  # type: ignore[import]
                    detect_watermark,
                    prepare_template,
                )
            except Exception as exc:  # noqa: BLE001
                auto_runtime["reason"] = f"не удалось импортировать детектор: {exc}"
            else:
                template_raw = str(auto_cfg.get("template", "") or "").strip()
                if not template_raw:
                    auto_runtime["reason"] = "не указан путь к шаблону"
                else:
                    template_path = _project_path(template_raw)
                    if not template_path.exists():
                        auto_runtime["reason"] = f"шаблон не найден: {template_path}"
                if not auto_runtime.get("reason"):
                    try:
                        import cv2  # type: ignore[import]
                    except Exception as exc:  # noqa: BLE001
                        auto_runtime["reason"] = f"opencv недоступен: {exc}"
                    else:
                        template_image = cv2.imread(str(template_path), cv2.IMREAD_UNCHANGED)
                        if template_image is None or getattr(template_image, "size", 0) == 0:
                            auto_runtime["reason"] = f"не удалось загрузить шаблон: {template_path}"
                        else:
                            mask_threshold_raw = auto_cfg.get("mask_threshold")
                            try:
                                mask_threshold = (
                                    int(mask_threshold_raw)
                                    if mask_threshold_raw is not None
                                    else 8
                                )
                            except (TypeError, ValueError):
                                mask_threshold = 8
                            try:
                                template_package = prepare_template(
                                    template_image,
                                    template_path,
                                    mask_threshold=mask_threshold,
                                )
                            except Exception as exc:  # noqa: BLE001
                                auto_runtime["reason"] = f"не удалось подготовить шаблон: {exc}"
                                template_package = None
                            else:
                                auto_runtime["template_package"] = template_package
                                auto_runtime["mask_threshold"] = mask_threshold
                if not auto_runtime.get("reason"):
                    try:
                        threshold = float(auto_cfg.get("threshold", 0.75) or 0.75)
                    except (TypeError, ValueError):
                        threshold = 0.75
                    frames_to_scan = auto_cfg.get("frames", 5)
                    try:
                        frames_to_scan = int(frames_to_scan or 0)
                    except (TypeError, ValueError):
                        frames_to_scan = 5
                    frames_to_scan = max(frames_to_scan, 1)
                    downscale_val = auto_cfg.get("downscale")
                    try:
                        downscale_num = float(downscale_val)
                    except (TypeError, ValueError):
                        downscale_num = 0.0
                    downscale_value: Optional[float] = downscale_num if downscale_num > 0 else None
                    try:
                        bbox_padding_val = int(auto_cfg.get("bbox_padding", 12) or 0)
                    except (TypeError, ValueError):
                        bbox_padding_val = 12
                    bbox_padding_val = max(0, bbox_padding_val)
                    try:
                        bbox_padding_pct_val = float(auto_cfg.get("bbox_padding_pct", 0.15) or 0.0)
                    except (TypeError, ValueError):
                        bbox_padding_pct_val = 0.15
                    bbox_padding_pct_val = max(0.0, min(1.0, bbox_padding_pct_val))
                    try:
                        bbox_min_size_val = int(auto_cfg.get("bbox_min_size", 48) or 0)
                    except (TypeError, ValueError):
                        bbox_min_size_val = 48
                    bbox_min_size_val = max(2, bbox_min_size_val)
                    auto_runtime.update(
                        {
                            "enabled": True,
                            "func": detect_watermark,
                            "template_path": str(template_path),
                            "template_image": template_image,
                            "template_package": auto_runtime.get("template_package"),
                            "threshold": threshold,
                            "frames": frames_to_scan,
                            "downscale": downscale_value,
                            "mask_threshold": auto_runtime.get("mask_threshold", 8),
                            "bbox_padding": bbox_padding_val,
                            "bbox_padding_pct": bbox_padding_pct_val,
                            "bbox_min_size": bbox_min_size_val,
                        }
                    )

        # источник для BLUR
        downloads_dir = _project_path(self.cfg.get("downloads_dir", str(DL_DIR)))
        src_primary = _project_path(
            self.cfg.get("blur_src_dir", self.cfg.get("downloads_dir", str(DL_DIR)))
        )

        candidate_dirs: List[Path] = []
        if src_primary.exists():
            candidate_dirs.append(src_primary)
        else:
            self._append_activity(
                f"Источник BLUR отсутствует ({src_primary}). Беру файлы из основного Downloads.",
                kind="warn",
            )

        if downloads_dir.exists() and not any(_same_path(d, downloads_dir) for d in candidate_dirs):
            candidate_dirs.append(downloads_dir)

        for session_id in self._session_order:
            session = self._session_cache.get(session_id)
            if not session:
                continue
            session_dir = self._session_download_dir(session)
            if session_dir.exists() and not any(_same_path(d, session_dir) for d in candidate_dirs):
                candidate_dirs.append(session_dir)

        if not candidate_dirs:
            self._post_status("Нет доступных папок для блюра", state="error")
            return False

        dst_dir = _project_path(self.cfg.get("blurred_dir", str(BLUR_DIR)))
        dst_dir.mkdir(parents=True, exist_ok=True)

        source_display = src_primary if src_primary.exists() else (candidate_dirs[0] if candidate_dirs else downloads_dir)

        allowed_ext = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
        seen: set[str] = set()
        videos: List[Path] = []
        for folder in candidate_dirs:
            try:
                entries = sorted(folder.iterdir())
            except FileNotFoundError:
                continue
            for p in entries:
                if not p.is_file():
                    continue
                if p.suffix.lower() not in allowed_ext:
                    continue
                if p.name in seen:
                    continue
                videos.append(p)
                seen.add(p.name)
        total = len(videos)
        if not total:
            self._post_status("Нет видео для блюра", state="error")
            return False

        self._post_status(f"Блюр по пресету {active} ({total} видео)…", progress=0, total=total, state="running")
        self._send_tg(f"🌫️ Блюр запускается: {total} файлов → {dst_dir}")
        counter = {"done": 0}
        lock = Lock()
        failures: List[str] = []

        def _round_to_even(value: object, *, minimum: Optional[int] = None, default: int = 0) -> int:
            try:
                num = float(value)
            except Exception:
                num = float(default)
            if not math.isfinite(num):
                num = float(default)
            even = int(round(num / 2.0) * 2)
            if minimum is not None:
                min_even = int(minimum)
                if min_even < 0:
                    min_even = 0
                if min_even % 2 != 0:
                    min_even += 1
                if even < min_even:
                    even = min_even
            return even

        def _clip_zones_to_frame(
            zone_list: List[Dict[str, int]],
            frame_size: Optional[Tuple[int, int]],
        ) -> List[Dict[str, int]]:
            if not frame_size:
                return [dict(z) for z in zone_list]

            try:
                frame_w = max(2, int(float(frame_size[0])))
                frame_h = max(2, int(float(frame_size[1])))
            except Exception:
                return [dict(z) for z in zone_list]

            clipped: List[Dict[str, int]] = []
            for zone in zone_list:
                if not isinstance(zone, dict):
                    continue
                try:
                    raw_x = float(zone.get("x", 0))
                    raw_y = float(zone.get("y", 0))
                    raw_w = float(zone.get("w", 0))
                    raw_h = float(zone.get("h", 0))
                except Exception:
                    continue

                x = int(round(raw_x))
                y = int(round(raw_y))
                x = max(0, min(x, frame_w - 2))
                y = max(0, min(y, frame_h - 2))
                x -= x % 2
                y -= y % 2
                x = max(0, min(x, frame_w - 2))
                y = max(0, min(y, frame_h - 2))

                max_w = frame_w - x
                max_h = frame_h - y
                if max_w < 2 or max_h < 2:
                    continue

                w = int(round(raw_w))
                h = int(round(raw_h))
                w = max(2, min(w, max_w))
                h = max(2, min(h, max_h))

                if w % 2 != 0:
                    if w + 1 <= max_w:
                        w += 1
                    else:
                        w = max(2, w - 1)
                if h % 2 != 0:
                    if h + 1 <= max_h:
                        h += 1
                    else:
                        h = max(2, h - 1)

                max_w = frame_w - x
                max_h = frame_h - y
                w = max(2, min(w, max_w))
                h = max(2, min(h, max_h))

                clipped.append({"x": int(x), "y": int(y), "w": int(w), "h": int(h)})

            return clipped

        def _bbox_to_zone(
            bbox: Tuple[int, int, int, int],
            *,
            frame_size: Optional[Tuple[int, int]],
            pad_px: int = 0,
            pad_pct: float = 0.0,
            min_size: int = 2,
        ) -> Optional[Dict[str, int]]:
            try:
                x_raw, y_raw, w_raw, h_raw = [int(round(float(v))) for v in bbox]
            except Exception:
                return None
            if w_raw <= 0 or h_raw <= 0:
                return None

            pad_abs = max(0, int(pad_px))
            try:
                pad_ratio = float(pad_pct)
            except Exception:
                pad_ratio = 0.0
            pad_ratio = max(0.0, pad_ratio)
            try:
                min_dim = int(min_size)
            except Exception:
                min_dim = 2
            min_dim = max(2, min_dim)

            pad_w = int(round(w_raw * pad_ratio))
            pad_h = int(round(h_raw * pad_ratio))
            total_pad_x = pad_abs + pad_w
            total_pad_y = pad_abs + pad_h

            x = x_raw - total_pad_x
            y = y_raw - total_pad_y
            w = w_raw + total_pad_x * 2
            h = h_raw + total_pad_y * 2

            x = max(0, int(x))
            y = max(0, int(y))
            w = max(min_dim, int(w))
            h = max(min_dim, int(h))

            x = _round_to_even(x, minimum=0, default=x)
            y = _round_to_even(y, minimum=0, default=y)
            w = _round_to_even(w, minimum=min_dim, default=w)
            h = _round_to_even(h, minimum=min_dim, default=h)

            zone = {"x": int(x), "y": int(y), "w": int(w), "h": int(h)}
            if frame_size:
                clipped = _clip_zones_to_frame([zone], frame_size)
                if not clipped:
                    return None
                zone = clipped[0]
            return zone

        def _probe_frame_size(video_path: Path) -> Optional[Tuple[int, int]]:
            try:
                import cv2  # type: ignore[import]
            except Exception:
                return None

            cap = cv2.VideoCapture(str(video_path))
            if not cap.isOpened():
                cap.release()
                return None

            try:
                width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
                height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
                if width <= 0 or height <= 0:
                    ok, frame = cap.read()
                    if ok and frame is not None:
                        try:
                            frame_height, frame_width = frame.shape[:2]
                        except Exception:
                            frame_height = 0
                            frame_width = 0
                        if frame_width > 0 and frame_height > 0:
                            width = frame_width
                            height = frame_height
                if width > 0 and height > 0:
                    return (width, height)
                return None
            finally:
                cap.release()

        def _project_detection_onto_zones(
            base: List[Dict[str, int]],
            detected: Tuple[int, int, int, int],
            frame_size: Optional[Tuple[int, int]] = None,
        ) -> List[Dict[str, int]]:
            if not base:
                x, y, w, h = detected
                return [
                    {
                        "x": max(0, _round_to_even(x, minimum=0, default=0)),
                        "y": max(0, _round_to_even(y, minimum=0, default=0)),
                        "w": max(2, _round_to_even(w, minimum=2, default=2)),
                        "h": max(2, _round_to_even(h, minimum=2, default=2)),
                    }
                ]

            ref = base[0]
            ref_w = max(1, int(ref.get("w", 1)))
            ref_h = max(1, int(ref.get("h", 1)))
            det_x, det_y, det_w, det_h = detected
            scale_x = det_w / ref_w if ref_w else 1.0
            scale_y = det_h / ref_h if ref_h else 1.0

            frame_w: Optional[int] = None
            frame_h: Optional[int] = None
            if frame_size and len(frame_size) == 2:
                frame_w = max(1, int(frame_size[0]))
                frame_h = max(1, int(frame_size[1]))

            projected: List[Dict[str, int]] = []
            for zone in base:
                base_x = int(zone.get("x", 0))
                base_y = int(zone.get("y", 0))
                base_w = max(1, int(zone.get("w", 1)))
                base_h = max(1, int(zone.get("h", 1)))

                offset_x = base_x - int(ref.get("x", 0))
                offset_y = base_y - int(ref.get("y", 0))

                new_w = max(2, _round_to_even(base_w * scale_x, minimum=2, default=base_w))
                new_h = max(2, _round_to_even(base_h * scale_y, minimum=2, default=base_h))
                new_x = max(0, _round_to_even(det_x + offset_x * scale_x, minimum=0, default=det_x))
                new_y = max(0, _round_to_even(det_y + offset_y * scale_y, minimum=0, default=det_y))

                projected.append({"x": new_x, "y": new_y, "w": new_w, "h": new_h})

            if frame_w is not None and frame_h is not None:
                return _clip_zones_to_frame(projected, (frame_w, frame_h))

            return projected

        def _series_to_segments(
            entries: List[Dict[str, Any]],
            duration: Optional[float],
            fps: Optional[float],
            *,
            padding: float = 0.25,
        ) -> List[Dict[str, Any]]:
            cleaned: List[Dict[str, Any]] = []
            for entry in entries:
                bbox = entry.get("bbox")
                if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                    continue
                try:
                    x, y, w, h = [int(round(float(v))) for v in bbox]
                except Exception:
                    continue
                score_val = entry.get("score")
                try:
                    score_num = float(score_val) if score_val is not None else None
                except Exception:
                    score_num = None
                if score_num is None:
                    continue
                time_val = entry.get("time")
                try:
                    time_num = float(time_val) if time_val is not None else None
                except Exception:
                    time_num = None
                frame_idx = entry.get("frame")
                if time_num is None and fps and frame_idx is not None:
                    try:
                        time_num = int(frame_idx) / fps if fps else None
                    except Exception:
                        time_num = None
                if time_num is None:
                    continue
                frame_size_val = entry.get("frame_size")
                frame_size_tuple: Optional[Tuple[int, int]] = None
                if isinstance(frame_size_val, (list, tuple)) and len(frame_size_val) == 2:
                    try:
                        frame_size_tuple = (int(frame_size_val[0]), int(frame_size_val[1]))
                    except Exception:
                        frame_size_tuple = None
                cleaned.append(
                    {
                        "bbox": (x, y, w, h),
                        "score": score_num,
                        "time": max(0.0, time_num),
                        "frame": frame_idx,
                        "frame_size": frame_size_tuple,
                    }
                )

            if not cleaned:
                return []

            cleaned.sort(key=lambda item: item["time"])
            segments: List[Dict[str, Any]] = []
            epsilon = 0.05
            for idx, item in enumerate(cleaned):
                current_time = item["time"]
                prev_time = cleaned[idx - 1]["time"] if idx > 0 else None
                next_time = cleaned[idx + 1]["time"] if idx + 1 < len(cleaned) else None

                if prev_time is None:
                    if next_time is not None:
                        start = current_time - max(next_time - current_time, 0.0) / 2.0
                    else:
                        start = current_time - 1.0
                else:
                    start = (prev_time + current_time) / 2.0

                if next_time is None:
                    if duration is not None:
                        end = duration
                    elif prev_time is not None:
                        end = current_time + max(current_time - prev_time, 0.0) / 2.0
                    else:
                        end = current_time + 1.0
                else:
                    end = (current_time + next_time) / 2.0

                start = max(0.0, start - padding)
                end = end + padding
                if duration is not None:
                    end = min(duration, end)
                if end <= start:
                    end = start + max(padding, epsilon)
                    if duration is not None:
                        end = min(duration, max(end, start + epsilon))

                segments.append(
                    {
                        **item,
                        "start": max(0.0, start),
                        "end": max(max(0.0, start) + epsilon, end),
                    }
                )

            return segments

        def blur_one(v: Path) -> bool:
            out = dst_dir / v.name
            base_zones = [dict(z) for z in zones]
            active_zones = list(base_zones)
            detection_score: Optional[float] = None
            detection_error_text: Optional[str] = None
            fallback_note: Optional[str] = None
            frame_size: Optional[Tuple[int, int]] = None
            detection_info_msg: Optional[str] = None
            clip_info_msg: Optional[str] = None
            detection_applied = False
            timeline_zones: List[Dict[str, Any]] = []
            timeline_segment_count = 0
            per_video_zones_result: List[Dict[str, int]] = []
            timeline_switch_note: Optional[str] = None

            if auto_runtime.get("enabled"):
                threshold_value = float(auto_runtime.get("threshold", 0.75) or 0.75)
                try:
                    bbox_pad_px = int(auto_runtime.get("bbox_padding", 0) or 0)
                except Exception:
                    bbox_pad_px = 0
                bbox_pad_px = max(0, bbox_pad_px)
                try:
                    bbox_pad_pct = float(auto_runtime.get("bbox_padding_pct", 0.0) or 0.0)
                except Exception:
                    bbox_pad_pct = 0.0
                bbox_pad_pct = max(0.0, bbox_pad_pct)
                try:
                    bbox_min_size = int(auto_runtime.get("bbox_min_size", 2) or 2)
                except Exception:
                    bbox_min_size = 2
                bbox_min_size = max(2, bbox_min_size)

                detect_kwargs = {
                    "threshold": auto_runtime.get("threshold", 0.75),
                    "frames": auto_runtime.get("frames", 5),
                    "downscale": auto_runtime.get("downscale"),
                    "template_image": auto_runtime.get("template_image"),
                    "template_package": auto_runtime.get("template_package"),
                    "mask_threshold": auto_runtime.get("mask_threshold", auto_cfg.get("mask_threshold")),
                    "return_score": True,
                    "return_details": True,
                    "return_series": True,
                }
                extra_keys = (
                    "blur_kernel",
                    "scales",
                    "scale_min",
                    "scale_max",
                    "scale_steps",
                    "edge_weight",
                    "canny_low",
                    "canny_high",
                    "score_bias",
                    "score_floor",
                    "score_z_weight",
                )
                for key in extra_keys:
                    if key in auto_cfg:
                        value = auto_cfg.get(key)
                        if value is not None and (not isinstance(value, str) or value.strip()):
                            detect_kwargs[key] = value
                try:
                    result = auto_runtime["func"](  # type: ignore[index]
                        str(v),
                        auto_runtime["template_path"],  # type: ignore[index]
                        **detect_kwargs,
                    )
                except Exception as exc:  # noqa: BLE001
                    detection_error_text = str(exc)
                    fallback_note = f"ошибка детектора: {exc}"
                else:
                    bbox = result
                    series_entries: List[Dict[str, Any]] = []
                    fps_value: Optional[float] = None
                    duration_value: Optional[float] = None
                    if isinstance(result, tuple) and len(result) == 2:
                        bbox = result[0]
                        try:
                            detection_score = float(result[1]) if result[1] is not None else None
                        except (TypeError, ValueError):
                            detection_score = None
                    elif isinstance(result, dict):
                        bbox = result.get("bbox")
                        raw_score = result.get("score")
                        if raw_score is not None:
                            try:
                                detection_score = float(raw_score)
                            except (TypeError, ValueError):
                                detection_score = None
                        fs = result.get("frame_size")
                        if isinstance(fs, (tuple, list)) and len(fs) == 2:
                            try:
                                frame_size = (int(fs[0]), int(fs[1]))
                            except Exception:
                                frame_size = None
                        best_bbox = result.get("best_bbox")
                        if not bbox and best_bbox:
                            bbox = best_bbox
                        series_raw = result.get("series")
                        if isinstance(series_raw, list):
                            series_entries = [entry for entry in series_raw if isinstance(entry, dict)]
                        fps_raw = result.get("fps")
                        try:
                            fps_value = float(fps_raw) if fps_raw is not None else None
                        except Exception:
                            fps_value = None
                        duration_raw = result.get("duration")
                        try:
                            duration_value = float(duration_raw) if duration_raw is not None else None
                        except Exception:
                            duration_value = None
                    score_allows_detection = True
                    if detection_score is not None and detection_score < threshold_value:
                        score_allows_detection = False
                    per_video_zones: List[Dict[str, int]] = []
                    if bbox and score_allows_detection:
                        x, y, w, h = bbox
                        if w > 0 and h > 0:
                            segments: List[Dict[str, Any]] = []
                            if series_entries:
                                valid_series: List[Dict[str, Any]] = []
                                for entry in series_entries:
                                    score_raw = entry.get("score")
                                    try:
                                        score_val = float(score_raw)
                                    except (TypeError, ValueError):
                                        continue
                                    if score_val < threshold_value:
                                        continue
                                    entry_copy = dict(entry)
                                    entry_copy["score"] = score_val
                                    valid_series.append(entry_copy)
                                if not valid_series:
                                    for entry in series_entries:
                                        if entry.get("accepted"):
                                            try:
                                                score_val = float(entry.get("score", threshold_value))
                                            except (TypeError, ValueError):
                                                score_val = threshold_value
                                            entry_copy = dict(entry)
                                            entry_copy["score"] = score_val
                                            valid_series.append(entry_copy)
                                if valid_series:
                                    segments = _series_to_segments(
                                        valid_series,
                                        duration_value,
                                        fps_value,
                                    )
                            target_frame_size = frame_size
                            if target_frame_size is None:
                                target_frame_size = _probe_frame_size(v)
                            if target_frame_size and not frame_size:
                                frame_size = target_frame_size

                            zone_from_bbox = _bbox_to_zone(
                                (int(x), int(y), int(w), int(h)),
                                frame_size=target_frame_size,
                                pad_px=bbox_pad_px,
                                pad_pct=bbox_pad_pct,
                                min_size=bbox_min_size,
                            )
                            if zone_from_bbox:
                                per_video_zones = [zone_from_bbox]
                                per_video_zones_result = [dict(zone_from_bbox)]
                            else:
                                try:
                                    projected = _project_detection_onto_zones(
                                        base_zones,
                                        (int(x), int(y), int(w), int(h)),
                                        target_frame_size,
                                    )
                                except Exception as exc:  # noqa: BLE001
                                    if not detection_error_text:
                                        detection_error_text = f"проекция зон не удалась: {exc}"
                                    fallback_note = fallback_note or "проекция зон не удалась"
                                    projected = []
                                if projected:
                                    per_video_zones = list(projected)
                                    per_video_zones_result = list(projected)

                            if segments:
                                try:
                                    timeline_zones_local: List[Dict[str, Any]] = []
                                    appended = 0
                                    for seg in segments:
                                        seg_bbox = seg.get("bbox")
                                        if not seg_bbox or len(seg_bbox) != 4:
                                            continue
                                        seg_frame_val = seg.get("frame_size")
                                        seg_frame: Optional[Tuple[int, int]]
                                        if isinstance(seg_frame_val, (list, tuple)) and len(seg_frame_val) == 2:
                                            try:
                                                seg_frame = (
                                                    int(seg_frame_val[0]),
                                                    int(seg_frame_val[1]),
                                                )
                                            except Exception:
                                                seg_frame = None
                                        else:
                                            seg_frame = None
                                        target_seg_frame = seg_frame or target_frame_size
                                        zone_for_segment = _bbox_to_zone(
                                            (
                                                int(seg_bbox[0]),
                                                int(seg_bbox[1]),
                                                int(seg_bbox[2]),
                                                int(seg_bbox[3]),
                                            ),
                                            frame_size=target_seg_frame,
                                            pad_px=bbox_pad_px,
                                            pad_pct=bbox_pad_pct,
                                            min_size=bbox_min_size,
                                        )
                                        if not zone_for_segment:
                                            continue
                                        start_val = seg.get("start")
                                        end_val = seg.get("end")
                                        if start_val is None and end_val is None:
                                            continue
                                        try:
                                            start_f = float(start_val) if start_val is not None else 0.0
                                        except Exception:
                                            start_f = 0.0
                                        try:
                                            end_f = float(end_val) if end_val is not None else start_f
                                        except Exception:
                                            end_f = start_f
                                        if end_f <= start_f:
                                            end_f = start_f + 0.1
                                        appended += 1
                                        timeline_zones_local.append(
                                            {
                                                **zone_for_segment,
                                                "start": max(0.0, start_f),
                                                "end": max(start_f + 0.05, end_f),
                                            }
                                        )
                                    if timeline_zones_local:
                                        timeline_zones = timeline_zones_local
                                        timeline_segment_count = appended
                                except Exception as exc:  # noqa: BLE001
                                    if not detection_error_text:
                                        detection_error_text = f"проекция по временной шкале не удалась: {exc}"
                                    fallback_note = fallback_note or "проекция по временной шкале не удалась"

                            if not timeline_zones and per_video_zones:
                                active_zones = per_video_zones
                            if timeline_zones or per_video_zones:
                                detection_applied = True
                                zone_count = len(timeline_zones) if timeline_zones else len(per_video_zones)
                                detection_info_msg = (
                                    f"[BLUR] {v.name}: автодетект → {zone_count} зон"
                                )
                                if timeline_zones and timeline_segment_count:
                                    detection_info_msg += f" в {timeline_segment_count} окнах"
                                if detection_score is not None:
                                    detection_info_msg += f" (score={detection_score:.2f})"
                            else:
                                if not detection_error_text:
                                    detection_error_text = "проекция зон вернула пустой результат"
                                fallback_note = fallback_note or "проекция зон вернула пустой результат"
                        else:
                            detection_error_text = "некорректная зона от детектора"
                            fallback_note = "детектор вернул пустую зону"
                    if not score_allows_detection and detection_score is not None:
                        fallback_note = (
                            f"совпадение ниже порога ({threshold_value:.2f}),"
                            f" score={detection_score:.2f}"
                        )
                    elif not bbox and detection_score is not None:
                        fallback_note = f"совпадение ниже порога ({auto_runtime['threshold']:.2f})"
                        fallback_note += f", score={detection_score:.2f}"
                    elif not bbox:
                        fallback_note = f"совпадение ниже порога ({auto_runtime['threshold']:.2f})"

            if detection_applied and per_video_zones_result:
                active_zones = [dict(z) for z in per_video_zones_result]

            if frame_size is None:
                frame_size = _probe_frame_size(v)

            if active_zones and frame_size:
                clipped = _clip_zones_to_frame(active_zones, frame_size)
                if clipped:
                    if clipped != active_zones:
                        active_zones = clipped
                        if detection_applied and per_video_zones_result:
                            per_video_zones_result = [dict(z) for z in clipped]
                        clip_note = (
                            f"[BLUR] {v.name}: зоны скорректированы под кадр {frame_size[0]}x{frame_size[1]}"
                        )
                        if clip_info_msg:
                            clip_info_msg = f"{clip_info_msg}; {clip_note}"
                        else:
                            clip_info_msg = clip_note

            def _build_filter_parts(zone_list: List[Dict[str, Any]], *, timeline: bool) -> List[str]:
                parts: List[str] = []
                for zone in zone_list:
                    try:
                        x = int(zone.get("x", 0))
                        y = int(zone.get("y", 0))
                        w = int(zone.get("w", 1))
                        h = int(zone.get("h", 1))
                    except Exception:
                        continue
                    if w <= 0 or h <= 0:
                        continue
                    enable_expr = ""
                    if timeline:
                        start_val = zone.get("start")
                        end_val = zone.get("end")
                        try:
                            start_f = float(start_val) if start_val is not None else None
                        except Exception:
                            start_f = None
                        try:
                            end_f = float(end_val) if end_val is not None else None
                        except Exception:
                            end_f = None
                        if start_f is None and end_f is None:
                            continue
                        start_safe = max(0.0, start_f if start_f is not None else 0.0)
                        end_safe = end_f if end_f is not None else (start_safe + 0.1)
                        if end_safe <= start_safe:
                            end_safe = start_safe + 0.1
                        enable_expr = f":enable='between(t,{start_safe:.3f},{end_safe:.3f})'"
                    parts.append(f"delogo=x={x}:y={y}:w={w}:h={h}:show=0{enable_expr}")
                return parts

            timeline_parts = _build_filter_parts(timeline_zones, timeline=True) if timeline_zones else []
            if timeline_zones and not timeline_parts:
                timeline_zones = []
            static_source = active_zones if active_zones else zones
            static_parts = _build_filter_parts(static_source, timeline=False)

            def _compose_vf(parts: List[str]) -> str:
                if not parts:
                    return ""
                chain = list(parts)
                if post:
                    chain.append(post)
                chain.append("format=yuv420p")
                return ",".join(chain)

            vf_timeline = _compose_vf(timeline_parts)
            vf_static = _compose_vf(static_parts)

            if not vf_timeline and not vf_static:
                with lock:
                    counter["done"] += 1
                    self._post_status("Блюр…", progress=counter["done"], total=total, state="running")
                    self.sig_log.emit(f"[BLUR] Ошибка {v.name}: не найдены зоны delogo")
                    failures.append(f"{v.name}: не найдены зоны delogo")
                return False

            vf_current = vf_timeline or vf_static
            using_timeline = bool(vf_timeline)

            def _build_cmd(vf_expr: str, use_hw: bool, audio_copy: bool) -> List[str]:
                cmd = [ffbin, "-hide_banner", "-loglevel", "info", "-y"]
                if use_hw and sys.platform == "darwin":
                    cmd += ["-hwaccel", "videotoolbox"]
                cmd += ["-i", str(v), "-vf", vf_expr, "-map", "0:v", "-map", "0:a?"]
                if use_hw and sys.platform == "darwin":
                    cmd += ["-c:v", "h264_videotoolbox", "-b:v", "0", "-crf", crf]
                else:
                    codec = "libx264" if vcodec_choice in {"auto_hw", "libx264"} else vcodec_choice
                    cmd += ["-c:v", codec, "-crf", crf, "-preset", preset]
                if audio_copy:
                    cmd += ["-c:a", "copy"]
                else:
                    cmd += ["-c:a", "aac", "-b:a", "192k"]
                if fmt.lower() == "mp4":
                    cmd += ["-movflags", "+faststart"]
                cmd += [str(out)]
                return cmd

            def _register_attempt(label: str, use_hw: bool, audio_copy: bool, bucket: List[Tuple[str, bool, bool]]):
                for _, hw_flag, copy_flag in bucket:
                    if hw_flag == use_hw and copy_flag == audio_copy:
                        return
                bucket.append((label, use_hw, audio_copy))

            attempts: List[Tuple[str, bool, bool]] = []
            use_hw_pref = (vcodec_choice == "auto_hw" and sys.platform == "darwin")
            if use_hw_pref:
                _register_attempt("HW", True, copy_audio, attempts)
                if copy_audio:
                    _register_attempt("HW+aac", True, False, attempts)
                _register_attempt("SW", False, copy_audio, attempts)
                _register_attempt("SW+aac", False, False, attempts)
            else:
                _register_attempt("SW", False, copy_audio, attempts)
                if copy_audio:
                    _register_attempt("SW+aac", False, False, attempts)

            tried_labels: List[str] = []
            rc = 1
            tail: List[str] = []
            final_audio_copy = copy_audio
            error_note: Optional[str] = None
            fallback_attempted = False
            try:
                for label, use_hw, audio_copy_flag in attempts:
                    label_repr = label + ("[timeline]" if using_timeline else "")
                    tried_labels.append(label_repr)
                    rc, tail = _run_ffmpeg(
                        _build_cmd(vf_current, use_hw, audio_copy_flag),
                        log_prefix=f"BLUR:{v.name}",
                    )
                    if rc == 0:
                        final_audio_copy = audio_copy_flag
                        break
                    tail_text = "\n".join(tail[-6:]) if tail else ""
                    if (
                        using_timeline
                        and vf_static
                        and not fallback_attempted
                        and tail_text
                        and "Error reinitializing filters" in tail_text
                    ):
                        fallback_attempted = True
                        using_timeline = False
                        vf_current = vf_static
                        timeline_switch_note = "FFmpeg не смог перезапустить фильтр delogo (Error reinitializing filters)"
                        if detection_info_msg:
                            detection_info_msg += " (timeline→static)"
                        tried_labels[-1] = label + "[timeline→static]"
                        rc, tail = _run_ffmpeg(
                            _build_cmd(vf_current, use_hw, audio_copy_flag),
                            log_prefix=f"BLUR:{v.name}",
                        )
                        if rc == 0:
                            final_audio_copy = audio_copy_flag
                            break
                ok = (rc == 0)
            except Exception as exc:  # noqa: BLE001
                ok = False
                error_note = str(exc)

            with lock:
                counter["done"] += 1
                self._post_status("Блюр…", progress=counter["done"], total=total, state="running")
                detail = "→".join(tried_labels) if tried_labels else ""
                last_line = tail[-1] if tail else ""
                if not error_note and not ok and last_line:
                    error_note = last_line
                if error_note:
                    self.sig_log.emit(f"[BLUR] Ошибка {v.name}: {error_note}")
                else:
                    self.sig_log.emit(f"[BLUR] {'OK' if ok else 'FAIL'} ({detail}): {v.name}")
                if ok and copy_audio and not final_audio_copy:
                    self.sig_log.emit(f"[BLUR] {v.name}: аудио сконвертировано в AAC для совместимости")
                if detection_info_msg:
                    self.sig_log.emit(detection_info_msg)
                if clip_info_msg:
                    self.sig_log.emit(clip_info_msg)
                if timeline_switch_note:
                    msg_switch = (
                        f"Автодетект водяного знака: таймлайн → статично → {v.name}: {timeline_switch_note}"
                    )
                    self.sig_log.emit(f"[BLUR] {msg_switch}")
                    self._append_activity(msg_switch, kind="warn")
                    self._send_tg(f"⚠️ {msg_switch}")
                if auto_runtime.get("enabled"):
                    if detection_error_text:
                        auto_stats["errors"].append({"name": v.name, "error": detection_error_text})
                        self.sig_log.emit(
                            f"[BLUR] {v.name}: автодетект → пресет ({detection_error_text})"
                        )
                    elif fallback_note:
                        auto_stats["fallbacks"].append(
                            {"name": v.name, "score": detection_score, "note": fallback_note}
                        )
                        self.sig_log.emit(
                            f"[BLUR] {v.name}: автодетект → пресет ({fallback_note})"
                        )
                if not ok:
                    note = error_note or last_line or "ffmpeg завершился с ошибкой"
                    failures.append(f"{v.name}: {note}")
            return ok

        with ThreadPoolExecutor(max_workers=max(1, threads)) as ex:
            results = list(ex.map(blur_one, videos))

        if auto_cfg.get("enabled"):
            if not auto_runtime.get("enabled"):
                reason = str(auto_runtime.get("reason") or "не удалось инициализировать детектор")
                msg = f"Автодетект водяного знака отключён: {reason}"
                self.sig_log.emit(f"[BLUR] {msg}")
                self._append_activity(f"Блюр: {msg}", kind="warn")
                self._send_tg(f"⚠️ {msg}")
            else:
                if auto_stats["errors"]:
                    err_preview_parts = [
                        f"{entry['name']}: {entry['error']}" for entry in auto_stats["errors"][:3]
                    ]
                    err_preview = "; ".join(err_preview_parts)
                    if len(auto_stats["errors"]) > 3:
                        err_preview += f" … и ещё {len(auto_stats['errors']) - 3}"
                    msg = f"Автодетект водяного знака: ошибки → {err_preview}"
                    self.sig_log.emit(f"[BLUR] {msg}")
                    self._append_activity(msg, kind="warn")
                    self._send_tg(f"⚠️ {msg}")
                if auto_stats["fallbacks"]:
                    fb_preview_parts: List[str] = []
                    for entry in auto_stats["fallbacks"][:3]:
                        note = entry.get("note") or ""
                        if note:
                            fb_preview_parts.append(f"{entry['name']}: {note}")
                        else:
                            score = entry.get("score")
                            if isinstance(score, (int, float)):
                                fb_preview_parts.append(f"{entry['name']} (score={score:.2f})")
                            else:
                                fb_preview_parts.append(entry["name"])
                    fb_preview = "; ".join(fb_preview_parts)
                    if len(auto_stats["fallbacks"]) > 3:
                        fb_preview += f" … и ещё {len(auto_stats['fallbacks']) - 3}"
                    msg = f"Автодетект водяного знака: fallback к пресету → {fb_preview}"
                    self.sig_log.emit(f"[BLUR] {msg}")
                    self._append_activity(msg, kind="warn")
                    self._send_tg(f"⚠️ {msg}")

        ok_all = all(results)
        append_history(
            self.cfg,
            {
                "event": "blur_finish",
                "ok": ok_all,
                "count": total,
                "preset": active,
                "src": str(source_display) if source_display else "",
            },
        )
        status = "завершён" if ok_all else "с ошибками"
        src_name = source_display.name if isinstance(source_display, Path) else "—"
        self._send_tg(f"🌫️ Блюр {status}: {total} файлов, пресет {active}, из {src_name} → {dst_dir}")
        if ok_all:
            self._post_status("Блюр завершён", state="ok")
        else:
            self._post_status("Блюр завершён с ошибками", state="error")
            if failures:
                preview = "; ".join(failures[:3])
                if len(failures) > 3:
                    preview += f" … и ещё {len(failures) - 3}"
                self._append_activity(f"Блюр: ошибки → {preview}", kind="error")
        return ok_all

    # ----- MERGE -----
    def _run_merge_sync(self, group_override: Optional[int] = None) -> bool:
        self._save_settings_clicked(silent=True)
        merge_cfg = self.cfg.get("merge", {}) or {}
        if group_override and group_override > 0:
            group = int(group_override)
        else:
            group = int(self.sb_merge_group.value() or merge_cfg.get("group_size", 3))
        pattern = merge_cfg.get("pattern", "*.mp4")
        ff = self.ed_ff_bin.text().strip() or "ffmpeg"

        # источник для MERGE
        src_dir = _project_path(self.cfg.get("merge_src_dir", self.cfg.get("blurred_dir", str(BLUR_DIR))))
        if not src_dir.exists():
            self._post_status(f"Источник MERGE не найден: {src_dir}", state="error")
            return False

        out_dir = _project_path(self.cfg.get("merged_dir", str(MERG_DIR)))
        out_dir.mkdir(parents=True, exist_ok=True)

        # собрать файлы (поддержка нескольких расширений при pattern="auto")
        patterns = [pattern] if (pattern and pattern != "auto") else ["*.mp4", "*.mov", "*.m4v", "*.webm"]
        files: List[Path] = []
        for pat in patterns:
            files.extend(sorted(src_dir.glob(pat)))

        if not files:
            self._post_status("Нет файлов для склейки", state="error")
            return False

        groups: List[List[Path]] = [files[i:i + group] for i in range(0, len(files), group)]
        total = len(groups)
        self._post_status(f"Склейка группами по {group}…", progress=0, total=total, state="running")
        self._send_tg(f"🧵 Склейка запускается: {total} групп → {out_dir}")
        ok_all = True

        for i, g in enumerate(groups, 1):
            out = out_dir / f"merged_{i:03d}.mp4"

            # 1️⃣ создаём временный список файлов с абсолютными путями
            list_path = out_dir / f".concat_{i:03d}.txt"
            try:
                with open(list_path, "w", encoding="utf-8") as fl:
                    for p in g:
                        abs_p = p.resolve()
                        fl.write(f"file '{_ffconcat_escape(abs_p)}'\n")
            except Exception as e:
                self.sig_log.emit(f"[MERGE] Не удалось создать список: {e}")
                self._post_status("Склейка… ошибка подготовки списка", progress=i, total=total, state="error")
                ok_all = False
                continue

            # 2️⃣ Быстрая попытка без перекодирования
            cmd_fast = [
                ff, "-hide_banner", "-loglevel", "verbose", "-y",
                "-f", "concat", "-safe", "0", "-i", str(list_path),
                "-c", "copy", str(out)
            ]
            p = subprocess.Popen(cmd_fast, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            assert p.stdout
            for ln in p.stdout:
                self.sig_log.emit(f"[MERGE:{out.name}] {ln.rstrip()}")
            rc = p.wait()

            # 3️⃣ Фоллбек: перекодирование, если copy не сработал
            if rc != 0:
                self.sig_log.emit(f"[MERGE] Быстрая склейка провалилась для {out.name}, перекодируем…")
                cmd_slow = [
                    ff, "-hide_banner", "-loglevel", "verbose", "-y",
                    "-f", "concat", "-safe", "0", "-i", str(list_path),
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                    "-c:a", "aac", "-b:a", "160k", str(out)
                ]
                p2 = subprocess.Popen(cmd_slow, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                assert p2.stdout
                for ln in p2.stdout:
                    self.sig_log.emit(f"[MERGE:{out.name}] {ln.rstrip()}")
                rc = p2.wait()

            # 4️⃣ удаляем временный список
            try:
                list_path.unlink(missing_ok=True)
            except Exception:
                pass

            ok_all = ok_all and (rc == 0)
            self._post_status("Склейка…", progress=i, total=total, state="running")
            self.sig_log.emit(f"[MERGE] {'OK' if rc == 0 else 'FAIL'}: {out.name}")

        append_history(self.cfg, {
            "event": "merge_finish",
            "ok": ok_all,
            "groups": total,
            "group_size": group,
            "src": str(src_dir)
        })
        status = "завершена" if ok_all else "с ошибками"
        self._send_tg(f"🧵 Склейка {status}: {total} групп по {group}, из {src_dir.name} → {out_dir}")

        if ok_all:
            self._post_status("Склейка завершена", state="ok")
        else:
            self._post_status("Склейка завершена с ошибками", state="error")

        return ok_all


    # ----- YOUTUBE UPLOAD -----
    def _run_upload_sync(self) -> bool:
        self._save_settings_clicked(silent=True)

        yt_cfg = self.cfg.get("youtube", {}) or {}
        channel = self.cmb_youtube_channel.currentText().strip()
        if not channel:
            self._post_status("Не выбран YouTube канал", state="error")
            return False

        channels_available = [c.get("name") for c in (yt_cfg.get("channels") or []) if c.get("name")]
        if channel not in channels_available:
            self._post_status("Выбери YouTube канал в Настройках", state="error")
            return False

        src_dir = _project_path(self.ed_youtube_src.text().strip() or yt_cfg.get("upload_src_dir", self.cfg.get("merged_dir", str(MERG_DIR))))
        if not src_dir.exists():
            self._post_status(f"Папка для загрузки не найдена: {src_dir}", state="error")
            return False

        videos = [*src_dir.glob("*.mp4"), *src_dir.glob("*.mov"), *src_dir.glob("*.m4v"), *src_dir.glob("*.webm")]
        if not videos:
            self._post_status("Нет файлов для загрузки", state="error")
            return False

        publish_at = ""
        schedule_text = ""
        if self.cb_youtube_schedule.isChecked() and not self.cb_youtube_draft_only.isChecked():
            dt_local = self.dt_youtube_publish.dateTime()
            yt_cfg["last_publish_at"] = dt_local.toString(QtCore.Qt.DateFormat.ISODate)
            publish_at = dt_local.toUTC().toString("yyyy-MM-dd'T'HH:mm:ss'Z'")
            schedule_text = dt_local.toString("dd.MM HH:mm")
            save_cfg(self.cfg)

        workdir = yt_cfg.get("workdir", str(WORKERS_DIR / "uploader"))
        entry = yt_cfg.get("entry", "upload_queue.py")
        python = sys.executable
        cmd = [python, entry]

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["APP_CONFIG_PATH"] = str(CFG_PATH)
        env["YOUTUBE_CHANNEL_NAME"] = channel
        env["YOUTUBE_SRC_DIR"] = str(src_dir)
        env["YOUTUBE_DRAFT_ONLY"] = "1" if self.cb_youtube_draft_only.isChecked() else "0"
        env["YOUTUBE_ARCHIVE_DIR"] = str(_project_path(yt_cfg.get("archive_dir", str(PROJECT_ROOT / "uploaded"))))
        env["YOUTUBE_BATCH_LIMIT"] = str(int(self.sb_youtube_batch_limit.value()))
        env["YOUTUBE_BATCH_STEP_MINUTES"] = str(int(self.sb_youtube_interval.value()))
        if publish_at:
            env["YOUTUBE_PUBLISH_AT"] = publish_at

        draft_note = " (черновики)" if self.cb_youtube_draft_only.isChecked() else ""
        self._send_tg(f"📤 YouTube загрузка запускается: {len(videos)} файлов, канал {channel}{draft_note}")
        self._post_status("Загрузка на YouTube…", state="running")
        rc = self._await_runner(self.runner_upload, "YT", lambda: self.runner_upload.run(cmd, cwd=workdir, env=env))
        ok = rc == 0
        status = "завершена" if ok else "с ошибками"
        schedule_part = f", старт {schedule_text}" if schedule_text else draft_note
        self._send_tg(f"📤 YouTube загрузка {status}: {len(videos)} файлов, канал {channel}{schedule_part}")
        return ok

    def _start_youtube_single(self):
        threading.Thread(target=self._run_upload_sync, daemon=True).start()


    # ----- ПЕРЕИМЕНОВАНИЕ -----
    def _ren_browse(self):
        base = self.ed_ren_dir.text().strip() or self.cfg.get("downloads_dir", str(DL_DIR))
        dlg = QtWidgets.QFileDialog(self, "Выбери папку с видео")
        dlg.setFileMode(QtWidgets.QFileDialog.FileMode.Directory)
        dlg.setOption(QtWidgets.QFileDialog.Option.ShowDirsOnly, True)
        if base and os.path.isdir(base):
            dlg.setDirectory(base)
        if dlg.exec():
            dirs = dlg.selectedFiles()
            if dirs:
                self.ed_ren_dir.setText(dirs[0])

    def _natural_key(self, p: Path):
        def _parts(s):
            return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', s)]
        return _parts(p.name)

    def _iter_videos(self, folder: Union[str, Path]):
        path = _project_path(folder)
        return sorted(
            [*path.glob("*.mp4"), *path.glob("*.mov"), *path.glob("*.m4v"), *path.glob("*.webm")],
            key=self._natural_key
        )

    def _ren_run(self):
        folder = _project_path(self.ed_ren_dir.text().strip() or self.cfg.get("downloads_dir", str(DL_DIR)))
        if not folder.exists():
            self._post_status("Папка не найдена", state="error"); return
        files = self._iter_videos(folder)
        if not files:
            self._post_status("В папке нет видео", state="error"); return

        self._send_tg(f"📝 Переименование запускается: {len(files)} файлов в {folder}")
        use_titles = self.rb_ren_from_titles.isChecked()
        prefix = self.ed_ren_prefix.text().strip()
        start_no = int(self.ed_ren_start.value())

        titles: List[str] = []
        if use_titles:
            tpath = self._titles_path()
            if not tpath.exists():
                self._post_status("titles.txt не найден — переключись на нумерацию или создай файл", state="error")
                return
            titles = [ln.strip() for ln in tpath.read_text(encoding="utf-8").splitlines() if ln.strip()]
            if not titles:
                self._post_status("В titles.txt пусто", state="error"); return

        tmp_map = {}
        for f in files:
            tmp = f.with_name(f".tmp_ren_{int(time.time()*1000)}_{f.name}")
            try:
                f.rename(tmp)
            except Exception as e:
                self._post_status(f"Не удалось подготовить: {f.name} → {e}", state="error")
                for old, t in tmp_map.items():
                    try: t.rename(old)
                    except: pass
                return
            tmp_map[f] = tmp

        def sanitize(name: str) -> str:
            name = re.sub(r'[\\/:*?"<>|]+', "_", name)
            name = name.strip().strip(".")
            return name or "untitled"

        done = 0
        total = len(files)
        for idx, (orig, tmp) in enumerate(tmp_map.items(), start=0):
            ext = tmp.suffix.lower()
            base = sanitize(titles[idx]) if (use_titles and idx < len(titles)) else f"{prefix}{start_no + idx:03d}"
            out = folder / f"{base}{ext}"
            k = 1
            while out.exists():
                out = folder / f"{base}_{k}{ext}"
                k += 1
            try:
                tmp.rename(out)
                done += 1
                self.sig_log.emit(f"[RENAME] {orig.name} → {out.name}")
                self._post_status("Переименование…", progress=done, total=total, state="running")
            except Exception as e:
                self.sig_log.emit(f"[RENAME] Ошибка: {orig.name} → {e}")

        append_history(self.cfg, {"event":"rename", "dir": str(folder), "count": done, "mode": ("titles" if use_titles else "seq")})
        self._post_status(f"Переименовано: {done}/{total}", state=("ok" if done==total else "error"))
        self._send_tg(f"📝 Переименование завершено: {done}/{total} файлов → {folder}")
        self._refresh_stats()

    # ----- Stop -----
    def _stop_all(self):
        self._automator_queue = []
        self._automator_running = False
        self._automator_waiting = False
        self._automator_index = 0
        self._automator_total = 0
        self._automator_ok_all = False
        self._cancel_all_session_waiters()

        self.runner_autogen.stop()
        self.runner_dl.stop()
        self.runner_upload.stop()
        self.runner_tiktok.stop()
        self.runner_watermark.stop()
        for runner in list(self._session_runners.values()):
            runner.stop()
        # стоп ffmpeg / любые активные
        with self._procs_lock:
            procs = list(self._active_procs)
        for p in procs:
            try:
                if p.poll() is None:
                    p.terminate()
            except Exception:
                pass
        time.sleep(0.8)
        for p in procs:
            try:
                if p.poll() is None:
                    p.kill()
            except Exception:
                pass
        self._post_status("Остановлено", state="idle")

    # ----- History -----
    def _reload_history(self):
        hist = _project_path(self.cfg.get("history_file", str(HIST_FILE)))
        if not hist.exists():
            self.txt_history.setPlainText("История пуста"); return
        try:
            txt = hist.read_text(encoding="utf-8")
            lines_out = []
            # поддержка старого формата JSON-массивом
            if txt.strip().startswith("["):
                data = json.loads(txt or "[]")
                for r in data[-500:]:
                    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r.get("ts",0)))
                    lines_out.append(f"[{ts}] {r}")
            else:
                # JSONL
                rows = [json.loads(line) for line in txt.splitlines() if line.strip()]
                for r in rows[-500:]:
                    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r.get("ts",0)))
                    lines_out.append(f"[{ts}] {r}")
            self.txt_history.setPlainText("\n".join(lines_out))
        except Exception as e:
            self.txt_history.setPlainText(f"Ошибка чтения истории: {e}")

    # ----- settings -----
    def _save_settings_clicked(self, silent: bool=False, from_autosave: bool=False):
        self.cfg.setdefault("chrome", {})
        self.cfg["chrome"]["cdp_port"] = int(self.ed_cdp_port.text() or "9222")
        self.cfg["chrome"]["user_data_dir"] = self.ed_userdir.text().strip()
        self.cfg["chrome"]["binary"] = self.ed_chrome_bin.text().strip()

        self.cfg["project_root"] = self.ed_root.text().strip() or str(PROJECT_ROOT)
        self.cfg["downloads_dir"] = self.ed_downloads.text().strip() or str(DL_DIR)
        self.cfg["blurred_dir"] = self.ed_blurred.text().strip() or str(BLUR_DIR)
        self.cfg["merged_dir"] = self.ed_merged.text().strip() or str(MERG_DIR)

        self.cfg["blur_src_dir"] = self.ed_blur_src.text().strip() or self.cfg["downloads_dir"]
        self.cfg["merge_src_dir"] = self.ed_merge_src.text().strip() or self.cfg["blurred_dir"]
        history_line = getattr(self, "ed_history_path", None)
        titles_line = getattr(self, "ed_titles_path", None)
        history_value = history_line.text().strip() if isinstance(history_line, QtWidgets.QLineEdit) else ""
        titles_value = titles_line.text().strip() if isinstance(titles_line, QtWidgets.QLineEdit) else ""
        self.cfg["history_file"] = history_value or str(HIST_FILE)
        self.cfg["titles_file"] = titles_value or str(TITLES_FILE)

        ff = self.cfg.setdefault("ffmpeg", {})
        ff["binary"] = self.ed_ff_bin.text().strip() or "ffmpeg"
        ff["post_chain"] = self.ed_post.text().strip()
        ff["vcodec"] = self.cmb_vcodec.currentText().strip()
        ff["crf"] = int(self.ed_crf.value())
        ff["preset"] = self.cmb_preset.currentText()
        ff["format"] = self.cmb_format.currentText()
        ff["copy_audio"] = bool(self.cb_copy_audio.isChecked())
        ff["active_preset"] = self.cmb_active_preset.currentText().strip()
        ff["blur_threads"] = int(self.sb_blur_threads.value())
        auto_cfg = ff.setdefault("auto_watermark", {})
        auto_cfg["enabled"] = bool(self.cb_aw_enabled.isChecked())
        auto_cfg["template"] = self.ed_aw_template.text().strip()
        auto_cfg["threshold"] = float(self.dsb_aw_threshold.value())
        auto_cfg["frames"] = int(self.sb_aw_frames.value())
        auto_cfg["downscale"] = int(self.sb_aw_downscale.value())
        auto_cfg["bbox_padding"] = int(self.sb_aw_bbox_pad.value())
        auto_cfg["bbox_padding_pct"] = round(float(self.dsb_aw_bbox_pct.value()) / 100.0, 4)
        auto_cfg["bbox_min_size"] = int(self.sb_aw_bbox_min.value())

        presets = ff.setdefault("presets", {})
        presets.clear()
        for name, zones in self._preset_cache.items():
            presets[name] = {"zones": [dict(z) for z in zones]}

        self.cfg.setdefault("merge", {})["group_size"] = int(self.sb_merge_group.value())

        yt_cfg = self.cfg.setdefault("youtube", {})
        yt_cfg["upload_src_dir"] = self.ed_youtube_src.text().strip() or self.cfg.get("merged_dir", str(MERG_DIR))
        yt_cfg["schedule_minutes_from_now"] = int(self.sb_youtube_default_delay.value())
        yt_cfg["draft_only"] = bool(self.cb_youtube_default_draft.isChecked())
        yt_cfg["archive_dir"] = self.ed_youtube_archive.text().strip() or yt_cfg.get("archive_dir", str(PROJECT_ROOT / "uploaded"))
        yt_cfg["batch_step_minutes"] = int(self.sb_youtube_interval_default.value())
        yt_cfg["batch_limit"] = int(self.sb_youtube_limit_default.value())
        yt_cfg["last_publish_at"] = self.dt_youtube_publish.dateTime().toString(QtCore.Qt.DateFormat.ISODate)

        tk_cfg = self.cfg.setdefault("tiktok", {})
        tk_cfg["upload_src_dir"] = self.ed_tiktok_src.text().strip() or self.cfg.get("merged_dir", str(MERG_DIR))
        tk_cfg["schedule_minutes_from_now"] = int(self.sb_tiktok_default_delay.value())
        tk_cfg["draft_only"] = bool(self.cb_tiktok_default_draft.isChecked())
        tk_cfg["archive_dir"] = self.ed_tiktok_archive.text().strip() or tk_cfg.get("archive_dir", str(PROJECT_ROOT / "uploaded_tiktok"))
        tk_cfg["batch_step_minutes"] = int(self.sb_tiktok_interval_default.value())
        tk_cfg["batch_limit"] = int(self.sb_tiktok_limit_default.value())
        tk_cfg["github_workflow"] = self.ed_tiktok_workflow_settings.text().strip() or tk_cfg.get("github_workflow", ".github/workflows/tiktok-upload.yml")
        tk_cfg["github_ref"] = self.ed_tiktok_ref_settings.text().strip() or tk_cfg.get("github_ref", "main")
        tk_cfg["last_publish_at"] = self.dt_tiktok_publish.dateTime().toString(QtCore.Qt.DateFormat.ISODate)
        if hasattr(self, "cb_tiktok_schedule"):
            tk_cfg["schedule_enabled"] = bool(self.cb_tiktok_schedule.isChecked())

        tg_cfg = self.cfg.setdefault("telegram", {})
        tg_cfg["enabled"] = bool(self.cb_tg_enabled.isChecked())
        tg_cfg["bot_token"] = self.ed_tg_token.text().strip()
        tg_cfg["chat_id"] = self.ed_tg_chat.text().strip()
        tg_cfg["templates"] = list(self._telegram_templates)
        if hasattr(self, "cmb_tg_templates"):
            data = self.cmb_tg_templates.currentData()
            if data is not None:
                try:
                    tg_cfg["last_template"] = self._telegram_templates[int(data)].get("name", "")
                except (IndexError, ValueError, TypeError):
                    pass
        tg_cfg["quick_delay_minutes"] = int(self.sb_tg_quick_delay.value()) if hasattr(self, "sb_tg_quick_delay") else tg_cfg.get("quick_delay_minutes", 0)

        dl_cfg = self.cfg.setdefault("downloader", {})
        dl_cfg["max_videos"] = int(self.sb_max_videos.value())
        dl_cfg["open_drafts"] = bool(self.chk_open_drafts_global.isChecked())

        wmr_cfg = self.cfg.setdefault("watermark_cleaner", {})
        wmr_cfg["source_dir"] = self.ed_wmr_source.text().strip() or wmr_cfg.get(
            "source_dir", self.cfg.get("downloads_dir", str(DL_DIR))
        )
        wmr_cfg["output_dir"] = self.ed_wmr_output.text().strip() or wmr_cfg.get(
            "output_dir", str(PROJECT_ROOT / "restored")
        )
        wmr_cfg["template"] = self.ed_wmr_template.text().strip() or wmr_cfg.get(
            "template", str(PROJECT_ROOT / "watermark.png")
        )
        wmr_cfg["mask_threshold"] = int(self.sb_wmr_mask_threshold.value())
        wmr_cfg["threshold"] = float(self.dsb_wmr_threshold.value())
        wmr_cfg["frames"] = int(self.sb_wmr_frames.value())
        wmr_cfg["downscale"] = int(self.sb_wmr_downscale.value())
        wmr_cfg["scale_min"] = float(self.dsb_wmr_scale_min.value())
        wmr_cfg["scale_max"] = float(self.dsb_wmr_scale_max.value())
        wmr_cfg["scale_steps"] = int(self.sb_wmr_scale_steps.value())
        wmr_cfg["full_scan"] = bool(self.cb_wmr_full_scan.isChecked())
        wmr_cfg["padding_px"] = int(self.sb_wmr_padding_px.value())
        wmr_cfg["padding_pct"] = round(float(self.dsb_wmr_padding_pct.value()) / 100.0, 4)
        wmr_cfg["min_size"] = int(self.sb_wmr_min_size.value())
        wmr_cfg["search_span"] = int(self.sb_wmr_search_span.value())
        wmr_cfg["pool"] = int(self.sb_wmr_pool.value())
        wmr_cfg["max_iou"] = float(self.dsb_wmr_max_iou.value())
        wmr_cfg["blend"] = self.cmb_wmr_blend.currentText().strip() or "normal"
        wmr_cfg["inpaint_radius"] = int(self.sb_wmr_inpaint_radius.value())
        wmr_cfg["inpaint_method"] = self.cmb_wmr_inpaint_method.currentText().strip() or "telea"

        probe_cfg = self.cfg.setdefault("watermark_probe", {})
        probe_cfg["source_dir"] = self.ed_probe_source.text().strip() or probe_cfg.get(
            "source_dir", self.cfg.get("downloads_dir", str(DL_DIR))
        )
        probe_cfg["output_dir"] = self.ed_probe_output_dir.text().strip() or probe_cfg.get(
            "output_dir", str(PROJECT_ROOT / "restored")
        )
        probe_cfg["region"] = {
            "x": int(self.sb_probe_x.value()),
            "y": int(self.sb_probe_y.value()),
            "w": int(self.sb_probe_w.value()),
            "h": int(self.sb_probe_h.value()),
        }
        probe_cfg["frames"] = int(self.sb_probe_frames.value())
        probe_cfg["brightness_threshold"] = int(self.sb_probe_brightness.value())
        probe_cfg["coverage_ratio"] = float(self.dsb_probe_coverage.value())
        probe_cfg["edge_ratio"] = float(self.dsb_probe_edge_ratio.value())
        probe_cfg["downscale"] = float(self.dsb_probe_downscale.value())
        probe_cfg["min_hits"] = int(self.sb_probe_hits.value())
        probe_cfg["method"] = self.cmb_probe_method.currentData() or "hybrid"
        probe_cfg["flip_when"] = self.cmb_probe_flip_when.currentData() or "missing"
        probe_cfg["flip_direction"] = self.cmb_probe_direction.currentData() or "left"

        ui_cfg = self.cfg.setdefault("ui", {})
        ui_cfg["show_activity"] = bool(self.cb_ui_show_activity.isChecked())
        ui_cfg["show_context"] = bool(self.cb_ui_show_context.isChecked()) if hasattr(self, "cb_ui_show_context") else ui_cfg.get("show_context", True)
        ui_cfg["activity_density"] = self.cmb_ui_activity_density.currentData() or "compact"
        ui_cfg["custom_commands"] = list(self._custom_commands)

        genai_cfg = self.cfg.setdefault("google_genai", {})
        genai_cfg["enabled"] = bool(self.cb_genai_enabled.isChecked())
        genai_cfg["attach_to_sora"] = bool(self.cb_genai_attach.isChecked())
        genai_cfg["api_key"] = self.ed_genai_api_key.text().strip()
        genai_cfg["model"] = self.cmb_genai_model.currentText().strip() or "models/imagen-4.0-generate-001"
        current_person = self.cmb_genai_person.currentData()
        if isinstance(current_person, str) and current_person:
            value = current_person
        else:
            value = self.cmb_genai_person.currentText()
        genai_cfg["person_generation"] = value.strip()
        genai_cfg["aspect_ratio"] = self.ed_genai_aspect.text().strip() or "1:1"
        genai_cfg["image_size"] = self.ed_genai_size.text().strip() or "1K"
        genai_cfg["output_mime_type"] = self.ed_genai_mime.text().strip() or "image/jpeg"
        genai_cfg["number_of_images"] = int(self.sb_genai_images.value())
        genai_cfg["rate_limit_per_minute"] = int(self.sb_genai_rpm.value())
        genai_cfg["max_retries"] = int(self.sb_genai_retries.value())
        images_dir_value = self.ed_genai_output_dir.text().strip() or str(IMAGES_DIR)
        genai_cfg["output_dir"] = images_dir_value
        if hasattr(self, "ed_genai_output_dir"):
            self.ed_genai_output_dir.blockSignals(True)
            self.ed_genai_output_dir.setText(images_dir_value)
            self.ed_genai_output_dir.blockSignals(False)
        genai_cfg["manifest_file"] = str(Path(genai_cfg["output_dir"]) / "manifest.json")
        genai_cfg["seeds"] = self.ed_genai_seeds.text().strip()
        genai_cfg["consistent_character_design"] = bool(self.cb_genai_consistent.isChecked())
        genai_cfg["lens_type"] = self.ed_genai_lens.text().strip()
        genai_cfg["color_palette"] = self.ed_genai_palette.text().strip()
        genai_cfg["style"] = self.ed_genai_style.text().strip()
        genai_cfg["reference_prompt"] = self.te_genai_reference.toPlainText().strip()
        genai_cfg["notifications_enabled"] = bool(self.cb_genai_notifications.isChecked())
        genai_cfg["daily_quota"] = int(self.sb_genai_daily_quota.value())
        genai_cfg["quota_warning_prompts"] = int(self.sb_genai_quota_warning.value())
        genai_cfg["quota_enforce"] = bool(self.cb_genai_quota_enforce.isChecked())
        usage_value = self.ed_genai_usage_file.text().strip()
        if not usage_value:
            usage_value = str(Path(images_dir_value) / "usage.json")
        genai_cfg["usage_file"] = usage_value
        self.ed_genai_usage_file.blockSignals(True)
        self.ed_genai_usage_file.setText(usage_value)
        self.ed_genai_usage_file.blockSignals(False)

        maint_cfg = self.cfg.setdefault("maintenance", {})
        maint_cfg["auto_cleanup_on_start"] = bool(self.cb_maintenance_auto.isChecked())
        retention = maint_cfg.setdefault("retention_days", {})
        retention["downloads"] = int(self.sb_maint_downloads.value())
        retention["blurred"] = int(self.sb_maint_blurred.value())
        retention["merged"] = int(self.sb_maint_merged.value())

        self.cfg.setdefault("autogen", {})["sessions"] = self._session_config_snapshot()

        save_cfg(self.cfg)
        ensure_dirs(self.cfg)
        self._refresh_path_fields()
        self._refresh_youtube_ui()
        self._refresh_tiktok_ui()

        if hasattr(self, "_settings_autosave_timer"):
            self._settings_autosave_timer.stop()
        self._settings_dirty = False

        mode = "авто" if from_autosave else "вручную"
        if from_autosave or not silent:
            stamp = QtCore.QDateTime.currentDateTime().toString("HH:mm:ss")
            self.lbl_settings_status.setStyleSheet("color:#1b9c5d;")
            self.lbl_settings_status.setText(f"Настройки сохранены ({mode} {stamp})")
            self._append_activity(f"Настройки сохранены ({mode})", kind="success")
        self._refresh_settings_context()

        if not silent:
            self._post_status("Настройки сохранены", state="ok")
            if not from_autosave:
                self._send_tg("⚙️ Настройки сохранены (вручную)")

    def _run_env_check(self):
        self._save_settings_clicked(silent=True)

        self._append_activity("Проверка окружения…", kind="running", card_text="Проверка окружения")

        entries: List[Tuple[str, str, str]] = []

        def record(label: str, status: str, detail: str = ""):
            entries.append((label, status, detail))

        # FFmpeg
        ffbin = self.ed_ff_bin.text().strip() or "ffmpeg"
        ff_path = _normalize_path(ffbin)
        if ff_path.exists():
            record("FFmpeg", "ok", str(ff_path))
        else:
            found = shutil.which(ffbin)
            record("FFmpeg", "ok" if found else "warn", found or f"не найден ({ffbin})")

        # Chrome binary
        chrome_bin = self.ed_chrome_bin.text().strip() or self.cfg.get("chrome", {}).get("binary", "")
        chrome_path = _normalize_path(chrome_bin)
        if chrome_path.exists():
            record("Chrome binary", "ok", str(chrome_path))
        else:
            record("Chrome binary", "warn", f"не найден ({chrome_bin})")

        # Chrome profile availability
        ch_cfg = self.cfg.get("chrome", {}) or {}
        profiles = [p for p in (ch_cfg.get("profiles") or []) if isinstance(p, dict)]
        active_name = ch_cfg.get("active_profile", "") or ""
        if profiles:
            if active_name:
                record("Chrome профиль", "ok", active_name)
            else:
                record("Chrome профиль", "warn", "активный профиль не выбран")
        else:
            record("Chrome профиль", "warn", "список пуст")

        # Telegram configuration
        tg_cfg = self.cfg.get("telegram", {}) or {}
        if not tg_cfg.get("enabled"):
            record("Telegram", "info", "уведомления отключены")
        elif tg_cfg.get("bot_token") and tg_cfg.get("chat_id"):
            record("Telegram", "ok", "готово")
        else:
            record("Telegram", "warn", "укажи token и chat id")

        # YouTube configuration
        yt_cfg = self.cfg.get("youtube", {}) or {}
        channels = yt_cfg.get("channels") or []
        active_channel = yt_cfg.get("active_channel", "") or ""
        if active_channel:
            record("YouTube канал", "ok", active_channel)
            creds_path = ""
            for ch in channels:
                if ch.get("name") == active_channel:
                    creds_path = ch.get("credentials", "")
                    break
            if creds_path:
                cred_norm = _normalize_path(creds_path)
                record("YouTube credentials", "ok" if cred_norm.exists() else "warn", str(cred_norm))
            else:
                record("YouTube credentials", "warn", "файл не указан")
        else:
            record("YouTube канал", "warn", "не выбран")

        # Folder health
        folders = [
            ("RAW", self.cfg.get("downloads_dir", str(DL_DIR))),
            ("BLURRED", self.cfg.get("blurred_dir", str(BLUR_DIR))),
            ("MERGED", self.cfg.get("merged_dir", str(MERG_DIR))),
            ("UPLOAD", yt_cfg.get("upload_src_dir", self.cfg.get("merged_dir", str(MERG_DIR))))
        ]
        for label, raw in folders:
            folder = _normalize_path(raw)
            record(f"Каталог {label}", "ok" if folder.exists() else "warn", str(folder))

        icon_map = {"ok": "✅", "warn": "⚠️", "info": "ℹ️"}
        kind_map = {"ok": "success", "warn": "error", "info": "info"}
        summary_lines: List[str] = []

        warn_count = 0
        ok_count = 0
        considered = 0
        for label, status, detail in entries:
            icon = icon_map.get(status, "ℹ️")
            text = f"{icon} {label}"
            if detail:
                text += f" — {detail}"
            self._append_activity(f"[CHECK] {text}", kind=kind_map.get(status, "info"), card_text=False)
            if status == "warn":
                warn_count += 1
                considered += 1
            elif status == "ok":
                ok_count += 1
                considered += 1
            summary_lines.append(text)

        if considered == 0:
            considered = 1
        summary = f"Проверка окружения: {ok_count}/{considered} OK"
        result_kind = "success" if warn_count == 0 else "error"
        self._append_activity(summary, kind=result_kind)
        self._post_status(summary, state=("ok" if warn_count == 0 else "error"))

        if summary_lines:
            self._send_tg("🩺 Проверка окружения\n" + "\n".join(summary_lines))

    def _run_maintenance_cleanup(self, manual: bool = True):
        self._save_settings_clicked(silent=True)
        maint = self.cfg.get("maintenance", {}) or {}
        retention = maint.get("retention_days", {}) or {}
        mapping = [
            ("RAW", _project_path(self.cfg.get("downloads_dir", str(DL_DIR))), int(retention.get("downloads", 0))),
            ("BLURRED", _project_path(self.cfg.get("blurred_dir", str(BLUR_DIR))), int(retention.get("blurred", 0))),
            ("MERGED", _project_path(self.cfg.get("merged_dir", str(MERG_DIR))), int(retention.get("merged", 0))),
        ]

        now = time.time()
        removed_total = 0
        details: List[str] = []
        errors: List[str] = []

        self._append_activity("Очистка каталогов: запуск…", kind="running")

        for label, folder, days in mapping:
            if days <= 0:
                continue
            folder = _project_path(folder)
            if not folder.exists():
                continue
            threshold = now - days * 24 * 3600
            removed_here = 0
            try:
                entries = list(folder.iterdir())
            except Exception as exc:
                errors.append(f"{label}: не удалось прочитать каталог ({exc})")
                continue
            for item in entries:
                try:
                    mtime = item.stat().st_mtime
                except Exception as exc:
                    errors.append(f"{label}: {item.name} — {exc}")
                    continue
                if mtime >= threshold:
                    continue
                try:
                    if item.is_file():
                        item.unlink()
                        removed_here += 1
                    elif item.is_dir():
                        # удаляем только пустые директории
                        if not any(item.iterdir()):
                            item.rmdir()
                            removed_here += 1
                except Exception as exc:
                    errors.append(f"{label}: {item.name} — {exc}")
            if removed_here:
                removed_total += removed_here
                details.append(f"{label}: {removed_here}")

        if removed_total:
            summary = ", ".join(details) if details else f"удалено {removed_total} элементов"
            msg = f"Очистка каталогов завершена: {summary}"
            self._append_activity(msg, kind="success")
            if manual:
                self._post_status(msg, state="ok")
            self._send_tg(f"🧹 {msg}")
        else:
            msg = "Очистка каталогов: подходящих файлов не найдено"
            self._append_activity(msg, kind="info")
            if manual:
                self._post_status(msg, state="idle")
            self._send_tg("🧹 Очистка: подходящих файлов не найдено")

        if errors:
            err_head = f"Очистка: {len(errors)} ошибок"
            self._append_activity(err_head, kind="error")
            for detail in errors[:5]:
                self._append_activity(f"↳ {detail}", kind="error", card_text=False)
            if manual:
                self._post_status(err_head, state="error")
            self._send_tg("⚠️ Очистка завершена с ошибками")

        self._refresh_stats()

    def _report_dir_sizes(self):
        self._save_settings_clicked(silent=True)
        yt_cfg = self.cfg.get("youtube", {}) or {}
        mapping = [
            ("RAW", self.cfg.get("downloads_dir", str(DL_DIR))),
            ("BLURRED", self.cfg.get("blurred_dir", str(BLUR_DIR))),
            ("MERGED", self.cfg.get("merged_dir", str(MERG_DIR))),
            ("UPLOAD", yt_cfg.get("upload_src_dir", self.cfg.get("merged_dir", str(MERG_DIR))))
        ]

        rows: List[str] = []
        summary_parts: List[str] = []
        for label, raw in mapping:
            folder = _normalize_path(raw)
            if folder.exists():
                size = _dir_size(folder)
                human = _human_size(size)
                rows.append(f"{label}: {human} — {folder}")
                summary_parts.append(f"{label} {human}")
            else:
                rows.append(f"{label}: папка не найдена — {folder}")

        summary = ", ".join(summary_parts) if summary_parts else "нет данных"
        self._append_activity("Размеры папок подсчитаны", kind="success", card_text=summary)
        for row in rows:
            self._append_activity(row, kind="info", card_text=False)
        self._post_status("Размеры папок обновлены", state="ok")
        self._send_tg(f"📦 Размеры папок: {summary}")

    def _test_tg_settings(self):
        self._save_settings_clicked(silent=True)
        if not (self.cfg.get("telegram", {}) or {}).get("enabled"):
            self._post_status("Включи Telegram-уведомления и заполни токен/чат", state="error")
            self._append_activity("Telegram выключен — тест не отправлен", kind="error")
            return
        ok = self._send_tg("Sora Suite: тестовое уведомление")
        if ok:
            self._post_status("Тестовое уведомление отправлено", state="ok")
        else:
            self._post_status("Не удалось отправить тестовое уведомление в Telegram", state="error")
    # ----- автоген конфиг -----
    def _load_autogen_cfg_ui(self):
        cfg_path = self.cfg.get("autogen",{}).get("config_path", "")
        if not cfg_path:
            return
        path = _project_path(cfg_path)
        if not path.exists():
            return
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            qr = (data.get("queue_retry") or {})
            self.sb_auto_success_every.setValue(int(qr.get("success_pause_every_n", 2)))
            self.sb_auto_success_pause.setValue(int(qr.get("success_pause_seconds", 180)))
        except Exception:
            pass

    def _save_autogen_cfg(self):
        cfg_path = self.cfg.get("autogen",{}).get("config_path", "")
        if not cfg_path:
            self._post_status("Не задан путь к autogen/config.yaml", state="error"); return
        path = _project_path(cfg_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = {}
            if path.exists():
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            data.setdefault("queue_retry", {})
            data["queue_retry"]["success_pause_every_n"] = int(self.sb_auto_success_every.value())
            data["queue_retry"]["success_pause_seconds"] = int(self.sb_auto_success_pause.value())
            path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
            self._post_status("Настройки автогена сохранены", state="ok")
        except Exception as e:
            self._post_status(f"Не удалось сохранить autogen config: {e}", state="error")

    # ----- simple stats -----
    def _stat_for_path(self, path: Path, suffixes: Tuple[str, ...]) -> Tuple[int, int]:
        key = (str(path), tuple(sorted(suffixes)))
        try:
            mtime = path.stat().st_mtime
        except Exception:
            mtime = 0.0
        cached = self._stat_cache.get(key)
        if cached and abs(cached[0] - mtime) < 0.1:
            return cached[1], cached[2]

        count = 0
        total_size = 0
        if path.exists():
            try:
                for entry in path.iterdir():
                    if not entry.is_file():
                        continue
                    if suffixes and entry.suffix.lower() not in suffixes:
                        continue
                    count += 1
                    try:
                        total_size += entry.stat().st_size
                    except Exception:
                        continue
            except Exception:
                pass

        self._stat_cache[key] = (mtime, count, total_size)
        return count, total_size

    def _refresh_stats(self):
        try:
            video_suffixes = (".mp4", ".mov", ".m4v", ".webm", ".mkv")
            image_suffixes = (".jpg", ".jpeg", ".png", ".webp")

            raw_path = _project_path(self.cfg.get("downloads_dir", str(DL_DIR)))
            blur_path = _project_path(self.cfg.get("blurred_dir", str(BLUR_DIR)))
            merge_path = _project_path(self.cfg.get("merged_dir", str(MERG_DIR)))
            upload_path = _project_path(self.cfg.get("youtube", {}).get("upload_src_dir", self.cfg.get("merged_dir", str(MERG_DIR))))
            tiktok_path = _project_path(self.cfg.get("tiktok", {}).get("upload_src_dir", self.cfg.get("merged_dir", str(MERG_DIR))))
            images_path = _project_path(self.cfg.get("google_genai", {}).get("output_dir", str(IMAGES_DIR)))

            raw, raw_size = self._stat_for_path(raw_path, video_suffixes)
            blur, blur_size = self._stat_for_path(blur_path, video_suffixes)
            merg, merge_size = self._stat_for_path(merge_path, video_suffixes)
            upload_src, upload_size = self._stat_for_path(upload_path, video_suffixes)
            tiktok_src, tiktok_size = self._stat_for_path(tiktok_path, video_suffixes)
            images_count, images_size = self._stat_for_path(images_path, image_suffixes)

            manifest_count = 0
            manifest_path = _project_path(self.cfg.get("google_genai", {}).get("manifest_file", str(Path(images_path) / "manifest.json")))
            if manifest_path.exists():
                try:
                    data = json.loads(manifest_path.read_text(encoding="utf-8"))
                    if isinstance(data, list):
                        manifest_count = len(data)
                    elif isinstance(data, dict):
                        if isinstance(data.get("images"), list):
                            manifest_count = len(data.get("images"))
                    else:
                        manifest_count = 0
                except Exception:
                    manifest_count = 0

            self.sig_log.emit(
                f"[STAT] RAW={raw} BLURRED={blur} MERGED={merg} YT={upload_src} TT={tiktok_src} IMG={images_count}"
            )

            dash_values = getattr(self, "_dashboard_stat_values", {})
            dash_desc = getattr(self, "_dashboard_stat_desc", {})

            fmt = lambda value: format(value, ",").replace(",", " ")

            stat_widgets = {
                "raw": getattr(self, "lbl_stat_raw", None),
                "blur": getattr(self, "lbl_stat_blur", None),
                "merge": getattr(self, "lbl_stat_merge", None),
                "youtube": getattr(self, "lbl_stat_upload", None),
                "tiktok": getattr(self, "lbl_stat_tiktok", None),
                "images": getattr(self, "lbl_stat_images", None),
            }
            stat_values = {
                "raw": raw,
                "blur": blur,
                "merge": merg,
                "youtube": upload_src,
                "tiktok": tiktok_src,
                "images": images_count,
            }
            for key, value in stat_values.items():
                text_value = fmt(value)
                widget = stat_widgets.get(key)
                if widget:
                    widget.setText(text_value)
                dash_label = dash_values.get(key)
                if dash_label:
                    dash_label.setText(text_value)

            def _set_desc(key: str, text: str):
                label = self._stat_desc_labels.get(key)
                if label:
                    label.setText(text)
                dash_label = dash_desc.get(key)
                if dash_label:
                    dash_label.setText(text)

            _set_desc("raw", f"{_human_size(raw_size)} · {raw_path.name or raw_path}")
            _set_desc("blur", f"{_human_size(blur_size)} · {blur_path.name or blur_path}")
            _set_desc("merge", f"{_human_size(merge_size)} · {merge_path.name or merge_path}")
            _set_desc("youtube", f"{_human_size(upload_size)} · {upload_path.name or upload_path}")
            _set_desc("tiktok", f"{_human_size(tiktok_size)} · {tiktok_path.name or tiktok_path}")
            _set_desc("images", f"{_human_size(images_size)} · манифест {manifest_count}")
        except Exception as e:
            self.sig_log.emit(f"[STAT] ошибка: {e}")

    def _prompt_profile_label(self, key: Optional[str]) -> str:
        if not key or key == PROMPTS_DEFAULT_KEY:
            return "Общий список"
        return str(key)

    def _update_prompts_active_label(self):
        if hasattr(self, "lbl_prompts_active"):
            label = self._prompt_profile_label(self._current_prompt_profile_key)
            self.lbl_prompts_active.setText(f"Сценарий использует: {label}")
        if hasattr(self, "lbl_prompts_path"):
            path = self._prompts_path()
            self.lbl_prompts_path.setText(str(path))
        if hasattr(self, "lbl_image_prompts_path"):
            img_path = self._image_prompts_path()
            self.lbl_image_prompts_path.setText(str(img_path))

    def _set_active_prompt_profile(self, key: str, persist: bool = True, reload: bool = True):
        normalized = key or PROMPTS_DEFAULT_KEY
        if self._current_prompt_profile_key == normalized:
            self._update_prompts_active_label()
            if reload:
                self._load_prompts()
            return
        self._current_prompt_profile_key = normalized
        self.cfg.setdefault("autogen", {})["active_prompts_profile"] = normalized
        if persist:
            save_cfg(self.cfg)
        target = None if normalized == PROMPTS_DEFAULT_KEY else normalized
        self._ensure_profile_prompt_files(target)
        self._update_prompts_active_label()
        if reload:
            self._load_prompts()
        self._refresh_content_context()

    def _refresh_prompt_profiles_ui(self):
        if not hasattr(self, "lst_prompt_profiles"):
            return
        profiles = [(PROMPTS_DEFAULT_KEY, self._prompt_profile_label(PROMPTS_DEFAULT_KEY), self._default_profile_prompts(None))]
        for profile in self.cfg.get("chrome", {}).get("profiles", []) or []:
            name = profile.get("name") or profile.get("profile_directory") or ""
            if not name:
                continue
            profiles.append((name, name, self._default_profile_prompts(name)))

        target_key = self._current_prompt_profile_key or PROMPTS_DEFAULT_KEY
        keys = [key for key, _, _ in profiles]
        if target_key not in keys and profiles:
            target_key = profiles[0][0]
            self._current_prompt_profile_key = target_key

        self.lst_prompt_profiles.blockSignals(True)
        self.lst_prompt_profiles.clear()
        target_row = 0
        for idx, (key, label, path) in enumerate(profiles):
            item = QtWidgets.QListWidgetItem(label)
            item.setData(QtCore.Qt.ItemDataRole.UserRole, key)
            item.setToolTip(str(path))
            self.lst_prompt_profiles.addItem(item)
            if key == target_key:
                target_row = idx
        self.lst_prompt_profiles.blockSignals(False)

        if self.lst_prompt_profiles.count():
            self.lst_prompt_profiles.blockSignals(True)
            self.lst_prompt_profiles.setCurrentRow(target_row)
            self.lst_prompt_profiles.blockSignals(False)

        self._set_active_prompt_profile(target_key, persist=False, reload=True)
        self._refresh_sessions_choices()

    def _on_prompt_profile_selection(self):
        if not hasattr(self, "lst_prompt_profiles"):
            return
        items = self.lst_prompt_profiles.selectedItems()
        if not items:
            return
        key = items[0].data(QtCore.Qt.ItemDataRole.UserRole) or PROMPTS_DEFAULT_KEY
        self._set_active_prompt_profile(key, persist=True, reload=True)

    # ----- Профили: UI/логика -----
    def _refresh_profiles_ui(self):
        ch = self.cfg.get("chrome", {})
        profiles = ch.get("profiles", []) or []
        active = ch.get("active_profile", "") or ""

        self.lst_profiles.clear()
        for p in profiles:
            item = QtWidgets.QListWidgetItem(p.get("name", ""))
            self.lst_profiles.addItem(item)
        self.lbl_prof_active.setText(active if active else "—")
        if active:
            for row in range(self.lst_profiles.count()):
                item = self.lst_profiles.item(row)
                if item and item.text() == active:
                    self.lst_profiles.blockSignals(True)
                    self.lst_profiles.setCurrentRow(row)
                    self.lst_profiles.blockSignals(False)
                    break
        else:
            self.lst_profiles.blockSignals(True)
            self.lst_profiles.clearSelection()
            self.lst_profiles.blockSignals(False)
        self._on_profile_selected()
        self._refresh_prompt_profiles_ui()

        if hasattr(self, "cmb_chrome_profile_top"):
            self.cmb_chrome_profile_top.blockSignals(True)
            self.cmb_chrome_profile_top.clear()
            self.cmb_chrome_profile_top.addItem("— без профиля —", "")
            for p in profiles:
                label = p.get("name") or p.get("profile_directory") or ""
                if not label:
                    continue
                value = p.get("name") or label
                self.cmb_chrome_profile_top.addItem(label, value)
            idx = self.cmb_chrome_profile_top.findData(active)
            if idx < 0:
                idx = 0
            self.cmb_chrome_profile_top.setCurrentIndex(idx)
            self.cmb_chrome_profile_top.blockSignals(False)

    def _on_profile_selected(self):
        items = self.lst_profiles.selectedItems()
        if not items:
            self.ed_prof_name.clear()
            self.ed_prof_root.clear()
            self.ed_prof_dir.clear()
            if hasattr(self, "sb_prof_port"):
                self.sb_prof_port.blockSignals(True)
                self.sb_prof_port.setValue(0)
                self.sb_prof_port.blockSignals(False)
            return
        name = items[0].text()
        for p in self.cfg.get("chrome", {}).get("profiles", []):
            if p.get("name") == name:
                self.ed_prof_name.setText(p.get("name", ""))
                self.ed_prof_root.setText(p.get("user_data_dir", ""))
                self.ed_prof_dir.setText(p.get("profile_directory", ""))
                port = _coerce_int(p.get("cdp_port"))
                if hasattr(self, "sb_prof_port"):
                    self.sb_prof_port.blockSignals(True)
                    self.sb_prof_port.setValue(int(port) if port and port > 0 else 0)
                    self.sb_prof_port.blockSignals(False)
                break

    def _on_profile_add_update(self):
        name = self.ed_prof_name.text().strip()
        root = self.ed_prof_root.text().strip()
        prof = self.ed_prof_dir.text().strip()
        if not name or not root:
            self._post_status("Укажи имя и user_data_dir", state="error")
            return

        ch = self.cfg.setdefault("chrome", {})
        profiles = ch.setdefault("profiles", [])
        for p in profiles:
            if p.get("name") == name:
                p["user_data_dir"] = root
                p["profile_directory"] = prof
                port_val = int(self.sb_prof_port.value()) if hasattr(self, "sb_prof_port") else 0
                if port_val > 0:
                    p["cdp_port"] = port_val
                else:
                    p.pop("cdp_port", None)
                break
        else:
            entry = {"name": name, "user_data_dir": root, "profile_directory": prof}
            port_val = int(self.sb_prof_port.value()) if hasattr(self, "sb_prof_port") else 0
            if port_val > 0:
                entry["cdp_port"] = port_val
            profiles.append(entry)

        self._ensure_profile_prompt_files(name)
        save_cfg(self.cfg)
        self._refresh_profiles_ui()
        self._post_status(f"Профиль «{name}» сохранён", state="ok")

    def _on_profile_delete(self):
        items = self.lst_profiles.selectedItems()
        if not items:
            return
        name = items[0].text()
        ch = self.cfg.setdefault("chrome", {})
        profiles = ch.setdefault("profiles", [])
        ch["profiles"] = [p for p in profiles if p.get("name") != name]
        if ch.get("active_profile") == name:
            ch["active_profile"] = ""
        if self._current_prompt_profile_key == name:
            self._set_active_prompt_profile(PROMPTS_DEFAULT_KEY, persist=True, reload=True)
        save_cfg(self.cfg)
        self._refresh_profiles_ui()
        self._post_status(f"Профиль «{name}» удалён", state="ok")

    def _set_active_chrome_profile(self, name: str, notify: bool = True):
        chrome_cfg = self.cfg.setdefault("chrome", {})
        current = chrome_cfg.get("active_profile", "") or ""
        if current == (name or ""):
            self._refresh_profiles_ui()
            return
        chrome_cfg["active_profile"] = name or ""
        save_cfg(self.cfg)
        self._refresh_profiles_ui()
        if notify:
            label = name or "—"
            port = self._resolve_chrome_port(name or None)
            port_hint = f" (CDP {port})" if port else ""
            self._post_status(f"Активный профиль: {label}{port_hint}", state="ok")

    def _on_profile_set_active(self):
        items = self.lst_profiles.selectedItems()
        if not items:
            return
        name = items[0].text()
        self._set_active_chrome_profile(name, notify=True)

    def _on_top_chrome_profile_changed(self, index: int):
        if index < 0:
            return
        data = self.cmb_chrome_profile_top.itemData(index)
        name = data if isinstance(data, str) else str(data or "")
        self._set_active_chrome_profile(name, notify=True)

    def _on_toolbar_scan_profiles(self):
        added, total = self._apply_profile_scan(auto=False)
        if total:
            if added:
                self._post_status(f"Найдено {total} профилей Chrome, добавлено {added}", state="ok")
            else:
                self._post_status("Профили Chrome уже добавлены", state="idle")
        else:
            self._post_status("Профили Chrome не найдены", state="error")
        self._refresh_profiles_ui()

    def _auto_scan_profiles_at_start(self):
        chrome_cfg = self.cfg.get("chrome", {})
        if chrome_cfg.get("profiles"):
            self._refresh_profiles_ui()
            return
        added, total = self._apply_profile_scan(auto=True)
        if total and added:
            self._post_status(f"Автоматически добавлены профили Chrome: {added}", state="info")
        elif total:
            self._post_status("Профили Chrome обнаружены", state="info")
        self._refresh_profiles_ui()

    def _discover_chrome_profile_roots(self) -> List[Path]:
        bases: List[Path] = []
        if sys.platform == "darwin":
            bases.append(Path.home() / "Library/Application Support/Google/Chrome")
        elif sys.platform.startswith("win"):
            for env_key in ["LOCALAPPDATA", "APPDATA", "USERPROFILE"]:
                raw = os.environ.get(env_key)
                if not raw:
                    continue
                candidate = Path(raw) / "Google" / "Chrome" / "User Data"
                if candidate not in bases:
                    bases.append(candidate)
        else:
            bases.append(Path.home() / ".config/google-chrome")
            bases.append(Path.home() / ".config/chromium")
        return bases

    def _discover_chrome_profiles(self) -> List[Dict[str, str]]:
        found: List[Dict[str, str]] = []
        for base in self._discover_chrome_profile_roots():
            base = base.expanduser()
            try:
                if not base.exists():
                    continue
                entries = ["Default"] + [d for d in os.listdir(base) if d.startswith("Profile ")]
                for entry in entries:
                    path = base / entry
                    if path.is_dir():
                        found.append({"name": entry, "user_data_dir": str(base), "profile_directory": entry})
            except Exception:
                continue
        return found

    def _apply_profile_scan(self, auto: bool = False) -> Tuple[int, int]:
        found = self._discover_chrome_profiles()
        if not found:
            if not auto:
                self._post_status("Профили не найдены. Проверь путь.", state="error")
            return 0, 0

        ch = self.cfg.setdefault("chrome", {})
        profiles = ch.setdefault("profiles", [])
        names_existing = {p.get("name") for p in profiles}
        added = 0
        changed = False
        for prof in found:
            name = prof.get("name")
            if not name:
                continue
            if name not in names_existing:
                profiles.append(prof)
                names_existing.add(name)
                added += 1
                changed = True
            else:
                for existing in profiles:
                    if existing.get("name") == name:
                        if not existing.get("user_data_dir") and prof.get("user_data_dir"):
                            existing["user_data_dir"] = prof.get("user_data_dir")
                            changed = True
                        if not existing.get("profile_directory") and prof.get("profile_directory"):
                            existing["profile_directory"] = prof.get("profile_directory")
                            changed = True
                        break
            self._ensure_profile_prompt_files(name)

        if profiles and not ch.get("active_profile"):
            ch["active_profile"] = profiles[0].get("name", "")
            if ch["active_profile"]:
                changed = True

        if changed:
            save_cfg(self.cfg)

        if not auto:
            msg = f"Найдено профилей: {added if added else len(found)}"
            if added and added != len(found):
                msg += f" (новых: {added})"
            self._post_status(msg, state="ok")
        return added, len(found)

    def _on_profile_scan(self):
        added, total = self._apply_profile_scan(auto=False)
        if total:
            self._refresh_profiles_ui()


# ----- YouTube: UI/логика -----
    def _refresh_youtube_ui(self):
        yt = self.cfg.get("youtube", {}) or {}
        channels = [c for c in (yt.get("channels") or []) if isinstance(c, dict)]
        active = yt.get("active_channel", "") or ""

        self.lst_youtube_channels.blockSignals(True)
        self.lst_youtube_channels.clear()
        for ch in channels:
            name = ch.get("name", "")
            if name:
                self.lst_youtube_channels.addItem(name)
        self.lst_youtube_channels.blockSignals(False)

        channel_names = [c.get("name", "") for c in channels if c.get("name")]
        self.cmb_youtube_channel.blockSignals(True)
        self.cmb_youtube_channel.clear()
        for name in channel_names:
            self.cmb_youtube_channel.addItem(name)
        self.cmb_youtube_channel.setEnabled(bool(channel_names))
        self.cmb_youtube_channel.blockSignals(False)

        idx = -1
        if active and active in channel_names:
            idx = channel_names.index(active)
        elif channel_names:
            idx = 0
            active = channel_names[0]

        if idx >= 0:
            self.lst_youtube_channels.setCurrentRow(idx)
            self.cmb_youtube_channel.setCurrentIndex(idx)
            self.lbl_yt_active.setText(active)
        else:
            self.lst_youtube_channels.clearSelection()
            self.cmb_youtube_channel.setCurrentIndex(-1)
            self.lbl_yt_active.setText("—")

        upload_src = yt.get("upload_src_dir", self.cfg.get("merged_dir", str(MERG_DIR)))
        self.ed_youtube_src.blockSignals(True)
        self.ed_youtube_src.setText(upload_src)
        self.ed_youtube_src.blockSignals(False)
        self.ed_youtube_archive.blockSignals(True)
        self.ed_youtube_archive.setText(yt.get("archive_dir", str(PROJECT_ROOT / "uploaded")))
        self.ed_youtube_archive.blockSignals(False)

        minutes = int(yt.get("schedule_minutes_from_now", 60) or 0)
        self.sb_youtube_default_delay.blockSignals(True)
        self.sb_youtube_default_delay.setValue(minutes)
        self.sb_youtube_default_delay.blockSignals(False)

        last_publish = yt.get("last_publish_at", "") or ""
        target_dt = QtCore.QDateTime.fromString(str(last_publish), QtCore.Qt.DateFormat.ISODate)
        if not target_dt.isValid():
            target_dt = QtCore.QDateTime.currentDateTime().addSecs(minutes * 60)
        self.dt_youtube_publish.blockSignals(True)
        self.dt_youtube_publish.setDateTime(target_dt)
        self.dt_youtube_publish.blockSignals(False)

        draft_default = bool(yt.get("draft_only", False))
        self.cb_youtube_default_draft.blockSignals(True)
        self.cb_youtube_default_draft.setChecked(draft_default)
        self.cb_youtube_default_draft.blockSignals(False)
        self.cb_youtube_draft_only.blockSignals(True)
        self.cb_youtube_draft_only.setChecked(draft_default)
        self.cb_youtube_draft_only.blockSignals(False)
        self._sync_draft_checkbox()
        self.cb_youtube_schedule.blockSignals(True)
        self.cb_youtube_schedule.setChecked(not draft_default)
        self.cb_youtube_schedule.blockSignals(False)
        self._toggle_youtube_schedule()

        step = int(yt.get("batch_step_minutes", 60) or 0)
        limit = int(yt.get("batch_limit", 0) or 0)
        for spin, value in [
            (self.sb_youtube_interval_default, step),
            (self.sb_youtube_limit_default, limit),
            (self.sb_youtube_interval, step),
            (self.sb_youtube_batch_limit, limit),
        ]:
            spin.blockSignals(True)
            spin.setValue(int(value))
            spin.blockSignals(False)

        if idx >= 0:
            self._on_youtube_selected()
        else:
            self.ed_yt_name.clear()
            self.ed_yt_client.clear()
            self.ed_yt_credentials.clear()
            self.cmb_yt_privacy.setCurrentText("private")

        self._update_youtube_queue_label()

    def _update_youtube_queue_label(self):
        src_text = self.ed_youtube_src.text().strip() or self.cfg.get("youtube", {}).get("upload_src_dir", "")
        if not src_text:
            self.lbl_youtube_queue.setText("Очередь: папка не выбрана")
            return
        src = _project_path(src_text)
        if not src.exists():
            self.lbl_youtube_queue.setText("Очередь: папка не найдена")
            return

        videos = self._iter_videos(src)
        count = len(videos)
        limit = int(self.sb_youtube_batch_limit.value())
        effective = min(count, limit) if limit > 0 else count
        interval = int(self.sb_youtube_interval.value())

        if count == 0:
            self.lbl_youtube_queue.setText("Очередь: нет видео в папке")
            self._refresh_autopost_context()
            return

        parts = [f"найдено {count}"]
        if limit > 0:
            parts.append(f"будет загружено {effective}")
        if not self.cb_youtube_draft_only.isChecked() and self.cb_youtube_schedule.isChecked() and interval > 0 and effective > 1:
            parts.append(f"шаг {interval} мин")
        self.lbl_youtube_queue.setText("Очередь: " + ", ".join(parts))
        self._refresh_autopost_context()

    def _on_youtube_selected(self):
        items = self.lst_youtube_channels.selectedItems()
        if not items:
            self.ed_yt_name.clear()
            self.ed_yt_client.clear()
            self.ed_yt_credentials.clear()
            self.cmb_yt_privacy.setCurrentText("private")
            return
        name = items[0].text()
        channels = self.cfg.get("youtube", {}).get("channels", []) or []
        for ch in channels:
            if ch.get("name") == name:
                self.ed_yt_name.setText(ch.get("name", ""))
                self.ed_yt_client.setText(ch.get("client_secret", ""))
                self.ed_yt_credentials.setText(ch.get("credentials", ""))
                self.cmb_yt_privacy.setCurrentText(ch.get("default_privacy", "private"))
                break
        self.cmb_youtube_channel.blockSignals(True)
        idx = self.cmb_youtube_channel.findText(name)
        if idx >= 0:
            self.cmb_youtube_channel.setCurrentIndex(idx)
        self.cmb_youtube_channel.blockSignals(False)

    def _on_youtube_add_update(self):
        name = self.ed_yt_name.text().strip()
        client = self.ed_yt_client.text().strip()
        creds = self.ed_yt_credentials.text().strip()
        privacy = self.cmb_yt_privacy.currentText().strip() or "private"
        if not name or not client:
            self._post_status("Укажи имя канала и client_secret.json", state="error")
            return

        yt = self.cfg.setdefault("youtube", {})
        channels = yt.setdefault("channels", [])
        for ch in channels:
            if ch.get("name") == name:
                ch.update({
                    "name": name,
                    "client_secret": client,
                    "credentials": creds,
                    "default_privacy": privacy,
                })
                break
        else:
            channels.append({
                "name": name,
                "client_secret": client,
                "credentials": creds,
                "default_privacy": privacy,
            })

        save_cfg(self.cfg)
        self._refresh_youtube_ui()
        self._post_status(f"YouTube канал «{name}» сохранён", state="ok")

    def _on_youtube_delete(self):
        items = self.lst_youtube_channels.selectedItems()
        if not items:
            return
        name = items[0].text()
        yt = self.cfg.setdefault("youtube", {})
        channels = yt.setdefault("channels", [])
        yt["channels"] = [c for c in channels if c.get("name") != name]
        if yt.get("active_channel") == name:
            yt["active_channel"] = ""
        save_cfg(self.cfg)
        self._refresh_youtube_ui()
        self._post_status(f"YouTube канал «{name}» удалён", state="ok")

    def _on_youtube_set_active(self):
        items = self.lst_youtube_channels.selectedItems()
        if not items:
            return
        name = items[0].text()
        yt = self.cfg.setdefault("youtube", {})
        yt["active_channel"] = name
        save_cfg(self.cfg)
        self._refresh_youtube_ui()
        self._post_status(f"Активный YouTube канал: {name}", state="ok")

    # ----- TikTok: UI/логика -----
    def _update_tiktok_queue_label(self):
        if not hasattr(self, "lbl_tiktok_queue"):
            return
        src_text = (self.ed_tiktok_src.text().strip() if hasattr(self, "ed_tiktok_src") else "")
        if not src_text:
            src_text = self.cfg.get("tiktok", {}).get("upload_src_dir", "")
        if not src_text:
            self.lbl_tiktok_queue.setText("Очередь: папка не выбрана")
            return
        src = _project_path(src_text)
        if not src.exists():
            self.lbl_tiktok_queue.setText("Очередь: папка не найдена")
            self._refresh_autopost_context()
            return

        videos = self._iter_videos(src)
        count = len(videos)
        if count == 0:
            self.lbl_tiktok_queue.setText("Очередь: нет видео в папке")
            self._refresh_autopost_context()
            return

        limit = int(self.sb_tiktok_batch_limit.value()) if hasattr(self, "sb_tiktok_batch_limit") else 0
        effective = min(count, limit) if limit > 0 else count
        interval = int(self.sb_tiktok_interval.value()) if hasattr(self, "sb_tiktok_interval") else 0
        parts = [f"найдено {count}"]
        if limit > 0:
            parts.append(f"будет загружено {effective}")
        if self.cb_tiktok_draft.isChecked():
            parts.append("черновики")
        elif self.cb_tiktok_schedule.isChecked() and interval > 0 and effective > 1:
            parts.append(f"шаг {interval} мин")
        self.lbl_tiktok_queue.setText("Очередь: " + ", ".join(parts))
        self._refresh_autopost_context()

    def _toggle_tiktok_schedule(self):
        enable = self.cb_tiktok_schedule.isChecked() and not self.cb_tiktok_draft.isChecked()
        self.dt_tiktok_publish.setEnabled(enable)
        self.sb_tiktok_interval.setEnabled(enable)
        self.cfg.setdefault("tiktok", {})["schedule_enabled"] = bool(self.cb_tiktok_schedule.isChecked())

    def _reflect_tiktok_interval(self, value: int):
        try:
            val = int(value)
        except (TypeError, ValueError):
            val = 0
        if hasattr(self, "sb_tiktok_interval_default") and self.sb_tiktok_interval_default.value() != val:
            self.sb_tiktok_interval_default.blockSignals(True)
            self.sb_tiktok_interval_default.setValue(val)
            self.sb_tiktok_interval_default.blockSignals(False)
        self._update_tiktok_queue_label()

    def _sync_tiktok_from_datetime(self):
        if not hasattr(self, "sb_tiktok_default_delay"):
            return
        if not self.cb_tiktok_schedule.isChecked() or self.cb_tiktok_draft.isChecked():
            return
        target = self.dt_tiktok_publish.dateTime()
        if not target.isValid():
            return
        now = QtCore.QDateTime.currentDateTime()
        minutes = max(0, now.secsTo(target) // 60)
        if self.sb_tiktok_default_delay.value() != minutes:
            self.sb_tiktok_default_delay.blockSignals(True)
            self.sb_tiktok_default_delay.setValue(int(minutes))
            self.sb_tiktok_default_delay.blockSignals(False)

    def _start_tiktok_single(self):
        threading.Thread(target=self._run_tiktok_sync, daemon=True).start()

    def _active_tiktok_profile(self, name: str) -> Optional[dict]:
        tk = self.cfg.get("tiktok", {}) or {}
        for prof in tk.get("profiles", []) or []:
            if prof.get("name") == name:
                return prof
        return None

    def _run_tiktok_sync(self) -> bool:
        self._save_settings_clicked(silent=True)

        tk_cfg = self.cfg.get("tiktok", {}) or {}
        profile_name = self.cmb_tiktok_profile.currentText().strip() if hasattr(self, "cmb_tiktok_profile") else ""
        if not profile_name:
            self._post_status("Не выбран профиль TikTok", state="error")
            return False

        profile = self._active_tiktok_profile(profile_name)
        if not profile:
            self._post_status("Профиль TikTok не найден в настройках", state="error")
            return False

        src_dir = _project_path(self.ed_tiktok_src.text().strip() or tk_cfg.get("upload_src_dir", self.cfg.get("merged_dir", str(MERG_DIR))))
        if not src_dir.exists():
            self._post_status(f"Папка не найдена: {src_dir}", state="error")
            return False

        videos = self._iter_videos(src_dir)
        if not videos:
            self._post_status("Нет файлов для загрузки", state="error")
            return False

        publish_at_iso = ""
        schedule_text = ""
        if self.cb_tiktok_schedule.isChecked() and not self.cb_tiktok_draft.isChecked():
            dt_local = self.dt_tiktok_publish.dateTime()
            tk_cfg["last_publish_at"] = dt_local.toString(QtCore.Qt.DateFormat.ISODate)
            publish_at_iso = dt_local.toUTC().toString("yyyy-MM-dd'T'HH:mm:ss'Z'")
            schedule_text = dt_local.toString("dd.MM HH:mm")
            save_cfg(self.cfg)

        workdir = tk_cfg.get("workdir", str(WORKERS_DIR / "tiktok"))
        entry = tk_cfg.get("entry", "upload_queue.py")
        python = sys.executable
        cmd = [python, entry]

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["APP_CONFIG_PATH"] = str(CFG_PATH)
        env["TIKTOK_PROFILE_NAME"] = profile_name
        env["TIKTOK_SRC_DIR"] = str(src_dir)
        env["TIKTOK_ARCHIVE_DIR"] = str(_project_path(tk_cfg.get("archive_dir", str(PROJECT_ROOT / "uploaded_tiktok"))))
        env["TIKTOK_BATCH_LIMIT"] = str(int(self.sb_tiktok_batch_limit.value()))
        env["TIKTOK_BATCH_STEP_MINUTES"] = str(int(self.sb_tiktok_interval.value()))
        env["TIKTOK_DRAFT_ONLY"] = "1" if self.cb_tiktok_draft.isChecked() else "0"
        if publish_at_iso:
            env["TIKTOK_PUBLISH_AT"] = publish_at_iso

        draft_note = " (черновики)" if self.cb_tiktok_draft.isChecked() else ""
        self._send_tg(f"📤 TikTok запускается: {len(videos)} роликов{draft_note}")
        self._post_status("Загрузка в TikTok…", state="running")
        rc = self._await_runner(self.runner_tiktok, "TT", lambda: self.runner_tiktok.run([python, entry], cwd=workdir, env=env))
        ok = rc == 0
        status = "завершена" if ok else "с ошибками"
        schedule_part = f", старт {schedule_text}" if schedule_text else draft_note
        self._append_activity(f"TikTok загрузка {status}{schedule_part}", kind=("success" if ok else "error"))
        self._send_tg("TikTok: ok" if ok else "⚠️ TikTok завершился с ошибкой")
        self._update_tiktok_queue_label()
        self._refresh_stats()
        return ok

    def _dispatch_tiktok_workflow(self):
        workflow = self.ed_tiktok_workflow.text().strip()
        ref = self.ed_tiktok_ref.text().strip() or "main"
        if not workflow:
            self._post_status("Укажи имя workflow для GitHub Actions", state="error")
            return
        gh = shutil.which("gh")
        if not gh:
            self._post_status("GitHub CLI не найден (команда gh)", state="error")
            return
        profile = self.cmb_tiktok_profile.currentText().strip()
        if not profile:
            self._post_status("Сначала выбери профиль TikTok", state="error")
            return

        inputs = {
            "profile": profile,
            "limit": str(int(self.sb_tiktok_batch_limit.value())),
            "interval": str(int(self.sb_tiktok_interval.value())),
            "draft": "1" if self.cb_tiktok_draft.isChecked() else "0",
        }
        if self.cb_tiktok_schedule.isChecked() and not self.cb_tiktok_draft.isChecked():
            inputs["publish_at"] = self.dt_tiktok_publish.toUTC().toString("yyyy-MM-dd'T'HH:mm:ss'Z'")
        src = self.ed_tiktok_src.text().strip() or self.cfg.get("tiktok", {}).get("upload_src_dir", "")
        if src:
            inputs["src_dir"] = src

        cmd = [gh, "workflow", "run", workflow, "--ref", ref]
        for key, value in inputs.items():
            if value:
                cmd.extend(["--field", f"{key}={value}"])

        self._append_activity(f"GitHub Actions: {workflow} ({ref})", kind="running")
        try:
            proc = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True, check=False)
        except Exception as exc:
            self._append_activity(f"GitHub Actions не запущен: {exc}", kind="error")
            self._post_status("Не удалось вызвать gh workflow run", state="error")
            return

        if proc.returncode == 0:
            self._append_activity("GitHub Actions: запуск отправлен", kind="success")
            self._post_status("Workflow отправлен", state="ok")
        else:
            msg = proc.stderr.strip() or proc.stdout.strip() or "неизвестная ошибка"
            self._append_activity(f"GitHub Actions ошибка: {msg}", kind="error")
            self._post_status("GitHub Actions вернул ошибку", state="error")

    def _refresh_tiktok_ui(self):
        if not hasattr(self, "lst_tiktok_profiles"):
            return
        tk = self.cfg.get("tiktok", {}) or {}
        profiles = [p for p in (tk.get("profiles") or []) if isinstance(p, dict)]
        active = tk.get("active_profile", "") or ""

        self.lst_tiktok_profiles.blockSignals(True)
        self.lst_tiktok_profiles.clear()
        names = []
        for prof in profiles:
            name = prof.get("name", "")
            if name:
                self.lst_tiktok_profiles.addItem(name)
                names.append(name)
        self.lst_tiktok_profiles.blockSignals(False)

        self.cmb_tiktok_profile.blockSignals(True)
        self.cmb_tiktok_profile.clear()
        for name in names:
            self.cmb_tiktok_profile.addItem(name)
        self.cmb_tiktok_profile.setEnabled(bool(names))
        self.cmb_tiktok_profile.blockSignals(False)

        idx = -1
        if active and active in names:
            idx = names.index(active)
        elif names:
            idx = 0
            active = names[0]

        if idx >= 0:
            self.lst_tiktok_profiles.setCurrentRow(idx)
            self.cmb_tiktok_profile.setCurrentIndex(idx)
            self.lbl_tt_active.setText(active)
        else:
            self.lst_tiktok_profiles.clearSelection()
            self.cmb_tiktok_profile.clear()
            self.lbl_tt_active.setText("—")

        self._on_tiktok_selected()
        self._update_tiktok_queue_label()

    def _on_tiktok_selected(self):
        if not hasattr(self, "lst_tiktok_profiles"):
            return
        items = self.lst_tiktok_profiles.selectedItems()
        if not items:
            self.ed_tt_name.clear()
            self.ed_tt_secret.clear()
            self.ed_tt_client_key.clear()
            self.ed_tt_client_secret.clear()
            self.ed_tt_open_id.clear()
            self.ed_tt_refresh_token.clear()
            self.ed_tt_timezone.clear()
            self.sb_tt_offset.setValue(0)
            self.ed_tt_hashtags.clear()
            self.txt_tt_caption.clear()
            self._update_tiktok_token_status(None)
            return
        name = items[0].text()
        prof = self._active_tiktok_profile(name)
        if not prof:
            return
        self.ed_tt_name.setText(prof.get("name", ""))
        self.ed_tt_secret.setText(prof.get("credentials_file", ""))
        self.ed_tt_client_key.setText(prof.get("client_key", ""))
        self.ed_tt_client_secret.setText(prof.get("client_secret", ""))
        self.ed_tt_open_id.setText(prof.get("open_id", ""))
        self.ed_tt_refresh_token.setText(prof.get("refresh_token", ""))
        self.ed_tt_timezone.setText(prof.get("timezone", ""))
        self.sb_tt_offset.setValue(int(prof.get("schedule_offset_minutes", 0)))
        self.ed_tt_hashtags.setText(prof.get("default_hashtags", ""))
        self.txt_tt_caption.setPlainText(prof.get("caption_template", "{title}\n{hashtags}"))
        self._update_tiktok_token_status(prof)
        self.cmb_tiktok_profile.blockSignals(True)
        idx = self.cmb_tiktok_profile.findText(name)
        if idx >= 0:
            self.cmb_tiktok_profile.setCurrentIndex(idx)
        self.cmb_tiktok_profile.blockSignals(False)

    def _on_tiktok_add_update(self):
        name = self.ed_tt_name.text().strip()
        secret_file = self.ed_tt_secret.text().strip()
        client_key = self.ed_tt_client_key.text().strip()
        client_secret = self.ed_tt_client_secret.text().strip()
        open_id = self.ed_tt_open_id.text().strip()
        refresh_token = self.ed_tt_refresh_token.text().strip()
        if not name:
            self._post_status("Укажи имя профиля TikTok", state="error")
            return
        if not secret_file and not all([client_key, client_secret, open_id, refresh_token]):
            self._post_status("Добавь файл секретов или заполни client_key, client_secret, open_id и refresh_token", state="error")
            return
        prof = {
            "name": name,
            "credentials_file": secret_file,
            "client_key": client_key,
            "client_secret": client_secret,
            "open_id": open_id,
            "refresh_token": refresh_token,
            "timezone": self.ed_tt_timezone.text().strip(),
            "schedule_offset_minutes": int(self.sb_tt_offset.value()),
            "default_hashtags": self.ed_tt_hashtags.text().strip(),
            "caption_template": self.txt_tt_caption.toPlainText().strip() or "{title}\n{hashtags}",
        }
        tk = self.cfg.setdefault("tiktok", {})
        profiles = tk.setdefault("profiles", [])
        for existing in profiles:
            if existing.get("name") == name:
                existing.update(prof)
                break
        else:
            profiles.append(prof)
        save_cfg(self.cfg)
        self._refresh_tiktok_ui()
        self._post_status(f"TikTok профиль «{name}» сохранён", state="ok")

    def _update_tiktok_token_status(self, prof: Optional[dict]):
        if not hasattr(self, "lbl_tt_token_status"):
            return
        default_text = "Access token будет обновлён автоматически"
        if not prof:
            self.lbl_tt_token_status.setText(default_text)
            return
        expires_raw = str(prof.get("access_token_expires_at", "") or prof.get("access_token_expires", ""))
        if not expires_raw:
            self.lbl_tt_token_status.setText(default_text)
            return
        qt_dt = QtCore.QDateTime.fromString(expires_raw, QtCore.Qt.DateFormat.ISODate)
        if not qt_dt.isValid():
            self.lbl_tt_token_status.setText("Access token: неверный формат даты")
            return
        qt_dt = qt_dt.toLocalTime()
        now = QtCore.QDateTime.currentDateTime()
        seconds = now.secsTo(qt_dt)
        if seconds <= 0:
            self.lbl_tt_token_status.setText(f"Access token истёк {qt_dt.toString('dd.MM HH:mm')}")
            return
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        self.lbl_tt_token_status.setText(
            f"Access token до {qt_dt.toString('dd.MM HH:mm')} (осталось {int(hours)}ч {int(minutes)}м)"
        )

    def _load_tiktok_secret_file(self):
        path = self.ed_tt_secret.text().strip()
        if not path:
            self._post_status("Укажи путь к JSON/YAML с секретами TikTok", state="error")
            return
        file_path = _normalize_path(path)
        if not file_path.exists():
            self._post_status(f"Файл не найден: {file_path}", state="error")
            return
        try:
            text = file_path.read_text(encoding="utf-8")
            if file_path.suffix.lower() in {".yaml", ".yml"}:
                data = yaml.safe_load(text) or {}
            else:
                data = json.loads(text)
        except Exception as exc:
            self._post_status(f"Не удалось прочитать секреты: {exc}", state="error")
            return

        mapping = {
            "client_key": self.ed_tt_client_key,
            "client_secret": self.ed_tt_client_secret,
            "open_id": self.ed_tt_open_id,
            "refresh_token": self.ed_tt_refresh_token,
        }
        for key, widget in mapping.items():
            value = data.get(key)
            if value:
                widget.setText(str(value))

        if data.get("access_token_expires_at") or data.get("access_token_expires"):
            self._update_tiktok_token_status(data)
        else:
            current = self._active_tiktok_profile(self.ed_tt_name.text().strip())
            self._update_tiktok_token_status(current)

        self._post_status("Секреты TikTok подгружены", state="ok")

    def _on_tiktok_delete(self):
        items = self.lst_tiktok_profiles.selectedItems()
        if not items:
            return
        name = items[0].text()
        tk = self.cfg.setdefault("tiktok", {})
        profiles = tk.setdefault("profiles", [])
        tk["profiles"] = [p for p in profiles if p.get("name") != name]
        if tk.get("active_profile") == name:
            tk["active_profile"] = ""
        save_cfg(self.cfg)
        self._refresh_tiktok_ui()
        self._post_status(f"TikTok профиль «{name}» удалён", state="ok")

    def _on_tiktok_set_active(self):
        items = self.lst_tiktok_profiles.selectedItems()
        if not items:
            return
        name = items[0].text()
        tk = self.cfg.setdefault("tiktok", {})
        tk["active_profile"] = name
        save_cfg(self.cfg)
        self._refresh_tiktok_ui()
        self._post_status(f"Активный TikTok профиль: {name}", state="ok")

    def _check_for_updates(self, dry_run: bool = True):
        repo = PROJECT_ROOT
        git_dir = repo / ".git"
        git = shutil.which("git")
        if not git or not git_dir.exists():
            self._post_status("git недоступен или проект не является репозиторием", state="error")
            return

        action = "Проверяем обновления" if dry_run else "Обновляем из GitHub"
        self._post_status(f"{action}…", state="running")
        self._append_activity(f"{action} через git", kind="running", card_text=action)

        def run_git(args: List[str]) -> subprocess.CompletedProcess[str]:
            return subprocess.run([git, *args], cwd=repo, capture_output=True, text=True)

        fetch = run_git(["fetch", "--all", "--tags"])
        if fetch.returncode != 0:
            self._append_activity(f"git fetch: {fetch.stderr.strip() or fetch.stdout.strip()}", kind="error")
            self._post_status("Не удалось получить обновления", state="error")
            return

        branch_proc = run_git(["rev-parse", "--abbrev-ref", "HEAD"])
        branch = branch_proc.stdout.strip() or "main"
        ahead_proc = run_git(["rev-list", "--count", f"origin/{branch}..{branch}"])
        behind_proc = run_git(["rev-list", "--count", f"{branch}..origin/{branch}"])
        try:
            ahead_count = int(ahead_proc.stdout.strip() or 0)
        except ValueError:
            ahead_count = 0
        try:
            behind_count = int(behind_proc.stdout.strip() or 0)
        except ValueError:
            behind_count = 0

        status_line = f"Ветка {branch}: локально +{ahead_count}/удалённо +{behind_count}"
        kind = "success" if behind_count == 0 else "info"
        self._append_activity(f"Git статус: {status_line}", kind=kind)
        if dry_run or behind_count == 0:
            self._post_status(status_line, state=("ok" if behind_count == 0 else "info"))
            return

        pull = run_git(["pull", "--ff-only"])
        if pull.returncode == 0:
            msg = pull.stdout.strip() or "Обновлено"
            self._append_activity(f"git pull: {msg}", kind="success")
            self._post_status("Обновление завершено", state="ok")
            self._refresh_youtube_ui()
            self._refresh_tiktok_ui()
            self._load_readme_preview(force=True)
        else:
            err_text = pull.stderr.strip() or pull.stdout.strip() or "Не удалось выполнить git pull"
            self._append_activity(f"git pull: {err_text}", kind="error")
            self._post_status("Не удалось обновиться — проверь консоль", state="error")


# ---------- main ----------
def main():
    app = QtWidgets.QApplication(sys.argv)
    font=QtGui.QFont("Menlo" if sys.platform=="darwin" else "Consolas",11); app.setFont(font)
    w = MainWindow(); w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
