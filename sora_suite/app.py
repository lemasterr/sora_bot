#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os  # FIX: нужен в load_cfg/_open_chrome
import re  # FIX: используется в _slot_log, _natural_key
import sys
import json
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
from typing import Optional, List, Union, Tuple, Dict, Callable

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
    autogen.setdefault("active_prompts_profile", PROMPTS_DEFAULT_KEY)

    downloader = data.setdefault("downloader", {})
    downloader.setdefault("workdir", str(WORKERS_DIR / "downloader"))
    downloader.setdefault("entry", "download_all.py")
    downloader.setdefault("max_videos", 0)

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

    return data


def slugify(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower())
    value = re.sub(r"-+", "-", value).strip("-")
    return value or "instance"


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
    Повторные запуски — дозаливаем изменения (по size+mtime).
    """
    exclude_dirs = {
        "Cache", "Code Cache", "GPUCache", "Service Worker",
        "CertificateTransparency", "Crashpad", "ShaderCache",
        "GrShaderCache", "OptimizationGuide", "SafetyTips",
        "Reporting and NEL", "File System", "Session Storage"
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
                    if ss.st_size != ds.st_size or int(ss.st_mtime) != int(ds.st_mtime):
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

        self._apply_theme()

        self.setWindowTitle("Sora Suite — Control Panel")
        self.resize(1500, 950)
        self.setMinimumSize(1024, 720)

        # tray notifications
        self.tray = QtWidgets.QSystemTrayIcon(self)
        icon = self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_ComputerIcon)
        self.tray.setIcon(icon)
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

        # кеши пресетов блюра должны существовать до построения UI,
        # иначе _load_zones_into_ui() перезапишет их, а позже мы бы обнулили значения
        self._preset_cache: Dict[str, List[Dict[str, int]]] = {}
        self._preset_tables: Dict[str, QtWidgets.QTableWidget] = {}

        self._build_ui()
        self._wire()
        self._init_state()
        self._refresh_update_buttons()

        QtCore.QTimer.singleShot(0, self._perform_delayed_startup)

        # дать раннеру ffmpeg доступ к self для логов
        _run_ffmpeg._self = self  # type: ignore[attr-defined]

        self._settings_dirty = False
        self._settings_autosave_timer = QtCore.QTimer(self)
        self._settings_autosave_timer.setInterval(2000)
        self._settings_autosave_timer.setSingleShot(True)
        self._settings_autosave_timer.timeout.connect(self._autosave_settings)
        self._register_settings_autosave_sources()

    # ----- helpers -----
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
        self._refresh_youtube_ui()
        self._refresh_tiktok_ui()
        self._load_autogen_cfg_ui()
        self._load_readme_preview()
        self._reload_used_prompts()
        maint_cfg = self.cfg.get("maintenance", {}) or {}
        if maint_cfg.get("auto_cleanup_on_start"):
            QtCore.QTimer.singleShot(200, lambda: self._run_maintenance_cleanup(manual=False))

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

    def _refresh_update_buttons(self):
        available = bool(shutil.which("git")) and (PROJECT_ROOT / ".git").exists()
        tooltip_disabled = (
            "Кнопка доступна только при запуске из git-репозитория. "
            "См. раздел README → Обновления для альтернативного сценария."
        )
        buttons = [
            getattr(self, "btn_update_check", None),
            getattr(self, "btn_update_pull", None),
            getattr(self, "btn_quick_update", None),
        ]
        for btn in buttons:
            if not btn:
                continue
            btn.setEnabled(available)
            if available:
                # вернём короткий тултип, если он был задан ранее
                if btn is self.btn_quick_update:
                    btn.setToolTip("Выполнить git pull для текущего репозитория")
            else:
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
            QWidget { background-color: #0f172a; color: #f1f5f9; }
            QGroupBox { border: 1px solid #22314d; border-radius: 12px; margin-top: 14px; background-color: #101a2f; }
            QGroupBox::title { subcontrol-origin: margin; left: 16px; padding: 0 6px; background-color: #101a2f; }
            QPushButton { background-color: #1f2d4a; border: 1px solid #2f4368; border-radius: 8px; padding: 6px 14px; color: #f8fafc; }
            QPushButton:disabled { background-color: #1b2640; border-color: #2a3654; color: #66738a; }
            QPushButton:hover { background-color: #2b3c5d; }
            QPushButton:pressed { background-color: #1a2540; }
            QLineEdit, QSpinBox, QDoubleSpinBox, QDateTimeEdit, QComboBox, QTextEdit, QPlainTextEdit {
                background-color: #0b1528; border: 1px solid #22314d; border-radius: 8px; padding: 4px 8px;
                selection-background-color: #4c6ef5; selection-color: #f8fafc;
            }
            QPlainTextEdit { padding: 8px; }
            QCheckBox { color: #f8fafc; spacing: 8px; }
            QCheckBox::indicator {
                width: 18px; height: 18px; border-radius: 5px;
                border: 1px solid #334155; background: #0b1528;
            }
            QCheckBox::indicator:unchecked { image: none; }
            QCheckBox::indicator:checked {
                background: #4c6ef5; border: 1px solid #93c5fd; image: none;
            }
            QCheckBox::indicator:disabled { background: #1e293b; border-color: #27364d; }
            QListWidget { border: 1px solid #22314d; border-radius: 12px; background-color: #0b1528; color: #f1f5f9; }
            QTabWidget::pane { border: 1px solid #22314d; border-radius: 12px; margin-top: -4px; background: #0f172a; }
            QTabBar::tab { background: #101a2f; border: 1px solid #22314d; padding: 6px 12px; margin-right: 4px;
                           border-top-left-radius: 6px; border-top-right-radius: 6px; }
            QTabBar::tab:selected { background: #4c6ef5; color: #f8fafc; }
            QTabBar::tab:hover { background: #374968; }
            QLabel#statusBanner { font-size: 15px; }
            QTextBrowser { background-color: #0b1528; border: 1px solid #22314d; border-radius: 10px; padding: 12px; }
            QScrollArea { border: none; }
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
            "background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #4c6ef5,stop:1 #1d4ed8);"
            "color:#f8fafc;font-weight:600;letter-spacing:0.3px;border:1px solid #1a1f4a;}"
        )
        v.addWidget(banner)

        tb = QtWidgets.QHBoxLayout()
        self.btn_open_chrome = QtWidgets.QPushButton("Открыть Chrome (CDP)")
        self.btn_open_root = QtWidgets.QPushButton("Открыть папку проекта")
        self.btn_open_raw = QtWidgets.QPushButton("RAW (downloads)")
        self.btn_open_blur = QtWidgets.QPushButton("BLURRED")
        self.btn_open_merge = QtWidgets.QPushButton("MERGED")
        self.btn_quick_update = QtWidgets.QPushButton("Обновить из GitHub")
        self.btn_quick_update.setToolTip("Выполнить git pull для текущего репозитория")
        self.btn_start_selected = QtWidgets.QPushButton("Старт выбранного")
        self.btn_stop_all = QtWidgets.QPushButton("Стоп все")
        tb.addWidget(self.btn_open_chrome)
        tb.addWidget(self.btn_open_root)
        tb.addWidget(self.btn_open_raw)
        tb.addWidget(self.btn_open_blur)
        tb.addWidget(self.btn_open_merge)
        tb.addWidget(self.btn_quick_update)
        tb.addStretch(1)
        tb.addWidget(self.btn_start_selected)
        tb.addWidget(self.btn_stop_all)
        v.addLayout(tb)

        split = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(8)
        v.addWidget(split, 1)

        # слева — информационная панель
        self.panel_activity = QtWidgets.QFrame()
        act_layout = QtWidgets.QVBoxLayout(self.panel_activity)
        act_layout.setContentsMargins(8, 8, 8, 8)
        act_layout.setSpacing(6)

        self.activity_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        self.activity_splitter.setChildrenCollapsible(False)
        self.activity_splitter.setHandleWidth(8)
        self._activity_sizes_cache = []
        act_layout.addWidget(self.activity_splitter, 1)

        current_wrap = QtWidgets.QWidget()
        current_wrap.setObjectName("currentEventWrapper")
        current_wrap.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Preferred,
            QtWidgets.QSizePolicy.Policy.Maximum,
        )
        current_layout = QtWidgets.QVBoxLayout(current_wrap)
        current_layout.setContentsMargins(0, 0, 0, 0)
        current_layout.setSpacing(6)

        self.current_event_card = QtWidgets.QFrame()
        self.current_event_card.setObjectName("currentEventCard")
        self.current_event_card.setStyleSheet(
            "QFrame#currentEventCard{background:#162132;border:1px solid #27364d;border-radius:14px;padding:0;}"
            "QLabel#currentEventTitle{color:#9fb7ff;font-size:11px;letter-spacing:1px;text-transform:uppercase;}"
            "QLabel#currentEventBody{color:#f8fafc;font-size:15px;font-weight:600;}")
        card_layout = QtWidgets.QVBoxLayout(self.current_event_card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        self.lbl_current_event_title = QtWidgets.QLabel("Сейчас")
        self.lbl_current_event_title.setObjectName("currentEventTitle")
        self.lbl_current_event_body = QtWidgets.QLabel("—")
        self.lbl_current_event_body.setObjectName("currentEventBody")
        self.lbl_current_event_body.setWordWrap(True)
        self.lbl_current_event_timer = QtWidgets.QLabel("—")
        self.lbl_current_event_timer.setObjectName("currentEventTimer")
        self.lbl_current_event_timer.setStyleSheet("color:#94a3b8;font-size:11px;")
        card_layout.addWidget(self.lbl_current_event_title)
        card_layout.addWidget(self.lbl_current_event_body)
        card_layout.addWidget(self.lbl_current_event_timer)
        current_layout.addWidget(self.current_event_card)
        self.activity_splitter.addWidget(current_wrap)
        self.activity_current_wrap = current_wrap

        self.history_panel = QtWidgets.QWidget()
        history_layout = QtWidgets.QVBoxLayout(self.history_panel)
        history_layout.setContentsMargins(0, 0, 0, 0)
        history_layout.setSpacing(6)

        act_header = QtWidgets.QHBoxLayout()
        self.lbl_activity = QtWidgets.QLabel("<b>История событий</b>")
        self.chk_activity_visible = QtWidgets.QCheckBox("Показывать")
        self.chk_activity_visible.setChecked(bool(self.cfg.get("ui", {}).get("show_activity", True)))
        self.chk_activity_visible.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        self.btn_activity_clear = QtWidgets.QPushButton("Очистить")
        self.btn_activity_clear.setIcon(self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_DialogResetButton))
        act_header.addWidget(self.lbl_activity)
        act_header.addStretch(1)
        act_header.addWidget(self.chk_activity_visible)
        act_header.addWidget(self.btn_activity_clear)
        history_layout.addLayout(act_header)

        self.lst_activity = QtWidgets.QListWidget()
        self.lst_activity.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
        self.lst_activity.setUniformItemSizes(False)
        self.lst_activity.setWordWrap(True)
        self.lst_activity.setAlternatingRowColors(False)
        self.lst_activity.setSpacing(2)
        self._apply_activity_density(persist=False)
        history_layout.addWidget(self.lst_activity, 1)

        self.lbl_activity_hint = QtWidgets.QLabel("Здесь можно посмотреть детальный лог процессов: скачка, блюр, склейка, загрузка.")
        self.lbl_activity_hint.setWordWrap(True)
        self.lbl_activity_hint.setStyleSheet("QLabel{color:#94a3b8;font-size:11px;}")
        history_layout.addWidget(self.lbl_activity_hint)

        self.activity_splitter.addWidget(self.history_panel)
        self.activity_splitter.setStretchFactor(0, 0)
        self.activity_splitter.setStretchFactor(1, 1)

        split.addWidget(self.panel_activity)

        # справа — вкладки
        self.tabs = QtWidgets.QTabWidget()
        split.addWidget(self.tabs)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 3)

        # применяем настройки отображения после создания виджетов
        self._update_current_event("—", self.cfg.get("ui", {}).get("accent_kind", "info"), persist=False)
        self._apply_activity_visibility(self.chk_activity_visible.isChecked(), persist=False)

        # TAB: Задачи
        def make_scroll_tab(margins=(12, 12, 12, 12), spacing=12):
            area = QtWidgets.QScrollArea()
            area.setWidgetResizable(True)
            area.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
            area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            area.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            body = QtWidgets.QWidget()
            layout = QtWidgets.QVBoxLayout(body)
            layout.setContentsMargins(*margins)
            layout.setSpacing(spacing)
            area.setWidget(body)
            return area, layout

        self.tab_tasks, lt = make_scroll_tab(margins=(0, 0, 0, 0))
        tasks_intro = QtWidgets.QLabel(
            "Основная панель запуска: отметь нужные этапы, нажми старт и следи за прогрессом и статистикой."
        )
        tasks_intro.setWordWrap(True)
        tasks_intro.setStyleSheet("QLabel{color:#94a3b8;font-size:11px;padding:0 12px 8px 12px;}")
        lt.addWidget(tasks_intro)

        self.task_tabs = QtWidgets.QTabWidget()
        lt.addWidget(self.task_tabs, 1)

        grp_choose = QtWidgets.QGroupBox("Что выполнить")
        f = QtWidgets.QFormLayout(grp_choose)
        f.setVerticalSpacing(6)
        self.cb_do_autogen = QtWidgets.QCheckBox("Вставка промптов в Sora")
        self.cb_do_download = QtWidgets.QCheckBox("Авто-скачка видео")
        self.cb_do_blur = QtWidgets.QCheckBox("Блюр водяного знака (ffmpeg, пресеты 9:16 / 16:9)")
        self.cb_do_merge = QtWidgets.QCheckBox("Склейка группами N")
        self.cb_do_upload = QtWidgets.QCheckBox("Загрузка на YouTube (отложенный постинг)")
        self.cb_do_tiktok = QtWidgets.QCheckBox("Загрузка в TikTok")
        for box in (self.cb_do_autogen, self.cb_do_download, self.cb_do_blur, self.cb_do_merge, self.cb_do_upload, self.cb_do_tiktok):
            box.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        f.addRow(self.cb_do_autogen)
        f.addRow(self.cb_do_download)
        f.addRow(self.cb_do_blur)
        f.addRow(self.cb_do_merge)
        f.addRow(self.cb_do_upload)
        f.addRow(self.cb_do_tiktok)

        grp_run = QtWidgets.QGroupBox("Запуск")
        hb2 = QtWidgets.QHBoxLayout(grp_run)
        self.btn_run_scenario = QtWidgets.QPushButton("Старт сценария (галочки сверху)")
        hb2.addWidget(self.btn_run_scenario)
        hb2.addStretch(1)

        grp_stat = QtWidgets.QGroupBox("Статистика / статус")
        vb = QtWidgets.QVBoxLayout(grp_stat)
        vb.setSpacing(12)

        status_row = QtWidgets.QHBoxLayout()
        status_row.setSpacing(12)
        self.lbl_status = QtWidgets.QLabel("—")
        self.lbl_status.setObjectName("statsStatusLabel")
        self.pb_global = QtWidgets.QProgressBar()
        self.pb_global.setMinimum(0)
        self.pb_global.setMaximum(1)
        self.pb_global.setValue(1)
        self.pb_global.setFormat("—")
        self.pb_global.setTextVisible(False)
        self.pb_global.setFixedHeight(8)
        self.pb_global.setStyleSheet("QProgressBar{background:#0f172a;border-radius:4px;}QProgressBar::chunk{background:#4c6ef5;border-radius:4px;}")
        status_row.addWidget(self.lbl_status, 2)
        status_row.addWidget(self.pb_global, 3)
        vb.addLayout(status_row)

        stats_strip = QtWidgets.QFrame()
        stats_strip.setObjectName("statsStrip")
        stats_strip.setStyleSheet(
            "QFrame#statsStrip{background:rgba(15,23,42,0.92);border:1px solid #1f2a40;border-radius:16px;}"
        )
        strip = QtWidgets.QHBoxLayout(stats_strip)
        strip.setContentsMargins(18, 14, 18, 14)
        strip.setSpacing(16)

        def make_stat_card(key: str, title: str, desc: str, tooltip: str, accent: str) -> QtWidgets.QLabel:
            card = QtWidgets.QFrame()
            card.setObjectName(f"statCard_{key}")
            card.setStyleSheet(
                (
                    "QFrame#statCard_{key}{background:#0f172a;border:1px solid rgba(148,163,184,0.28);border-radius:14px;}"
                    "QLabel#statTitle_{key}{color:#cbd5f5;font-size:11px;letter-spacing:0.5px;text-transform:uppercase;}"
                    "QLabel#statDesc_{key}{color:#8aa2c7;font-size:11px;}"
                ).replace("{key}", key)
            )
            layout = QtWidgets.QVBoxLayout(card)
            layout.setContentsMargins(14, 12, 14, 12)
            layout.setSpacing(6)

            accent_bar = QtWidgets.QFrame()
            accent_bar.setFixedHeight(4)
            accent_bar.setStyleSheet(f"QFrame{{background:{accent};border-radius:2px;}}")
            layout.addWidget(accent_bar)

            title_lbl = QtWidgets.QLabel(title)
            title_lbl.setObjectName(f"statTitle_{key}")
            value_lbl = QtWidgets.QLabel("0")
            value_lbl.setStyleSheet(
                f"QLabel{{font:700 24px 'JetBrains Mono','Menlo','Consolas';color:{accent};padding-top:2px;}}"
            )
            value_lbl.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            desc_lbl = QtWidgets.QLabel(desc)
            desc_lbl.setObjectName(f"statDesc_{key}")
            desc_lbl.setWordWrap(True)
            desc_lbl.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

            layout.addWidget(title_lbl, 0, QtCore.Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(value_lbl, 0, QtCore.Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(desc_lbl, 0, QtCore.Qt.AlignmentFlag.AlignCenter)

            card.setToolTip(tooltip)
            strip.addWidget(card, 1)
            return value_lbl

        self.lbl_stat_raw = make_stat_card(
            "raw",
            "RAW",
            "Скачанные черновики",
            "Количество видеофайлов в папке RAW",
            "#38bdf8",
        )
        self.lbl_stat_blur = make_stat_card(
            "blur",
            "BLURRED",
            "Готовы к блюру",
            "Сколько клипов ждут обработки блюром",
            "#a855f7",
        )
        self.lbl_stat_merge = make_stat_card(
            "merge",
            "MERGED",
            "Склеенные ролики",
            "Готовые склейки в итоговой папке",
            "#f97316",
        )
        self.lbl_stat_upload = make_stat_card(
            "youtube",
            "YOUTUBE",
            "Очередь YouTube",
            "Сколько файлов попадёт в очередь YouTube",
            "#4ade80",
        )
        self.lbl_stat_tiktok = make_stat_card(
            "tiktok",
            "TIKTOK",
            "Очередь TikTok",
            "Сколько файлов ожидают выгрузку в TikTok",
            "#f472b6",
        )

        strip.addStretch(1)
        vb.addWidget(stats_strip)

        pipeline_tab, pipeline_layout = make_scroll_tab()
        pipeline_layout.addWidget(grp_choose)
        pipeline_layout.addWidget(grp_run)
        pipeline_layout.addWidget(grp_stat)
        pipeline_layout.addStretch(1)
        self.task_tabs.addTab(pipeline_tab, "Пайплайн")

        # --- Скачка: лимит N ---
        grp_dl = QtWidgets.QGroupBox("Скачка")
        hb = QtWidgets.QHBoxLayout(grp_dl)
        hb.addWidget(QtWidgets.QLabel("Скачать N последних:"))
        self.sb_max_videos = QtWidgets.QSpinBox()
        self.sb_max_videos.setRange(0, 10000)
        self.sb_max_videos.setValue(int(self.cfg.get("downloader", {}).get("max_videos", 0)))
        hb.addWidget(self.sb_max_videos)
        self.btn_apply_dl = QtWidgets.QPushButton("Применить")
        hb.addWidget(self.btn_apply_dl)
        hb.addStretch(1)

        tab_download, download_layout = make_scroll_tab()
        dl_hint = QtWidgets.QLabel("Галочку \"Авто-скачка видео\" можно оставить включённой для сценария или запускать скачку отдельно.")
        dl_hint.setWordWrap(True)
        dl_hint.setStyleSheet("QLabel{color:#94a3b8;font-size:11px;}")
        download_layout.addWidget(dl_hint)
        download_layout.addWidget(grp_dl)
        download_layout.addStretch(1)
        self.task_tabs.addTab(tab_download, "Скачка")

        # --- Переименование файлов ---
        grp_ren = QtWidgets.QGroupBox("Переименование файлов")
        ren_l = QtWidgets.QGridLayout(grp_ren)
        self.ed_ren_dir = QtWidgets.QLineEdit(self.cfg.get("downloads_dir", str(DL_DIR)))
        self.btn_ren_browse = QtWidgets.QPushButton("…")
        self.rb_ren_from_titles = QtWidgets.QRadioButton("По списку из titles.txt")
        self.rb_ren_from_titles.setChecked(True)
        self.rb_ren_sequential = QtWidgets.QRadioButton("Последовательно (1,2,3…)")
        self.ed_ren_prefix = QtWidgets.QLineEdit("")
        self.ed_ren_start = QtWidgets.QSpinBox(); self.ed_ren_start.setRange(1, 1_000_000); self.ed_ren_start.setValue(1)
        self.btn_ren_run = QtWidgets.QPushButton("Переименовать")
        row = 0
        ren_l.addWidget(QtWidgets.QLabel("Папка:"), row, 0)
        ren_l.addWidget(self.ed_ren_dir, row, 1)
        ren_l.addWidget(self.btn_ren_browse, row, 2)
        row += 1
        ren_l.addWidget(self.rb_ren_from_titles, row, 0, 1, 3); row += 1
        ren_l.addWidget(self.rb_ren_sequential, row, 0, 1, 3); row += 1
        ren_l.addWidget(QtWidgets.QLabel("Префикс (для нумерации):"), row, 0)
        ren_l.addWidget(self.ed_ren_prefix, row, 1, 1, 2); row += 1
        ren_l.addWidget(QtWidgets.QLabel("Начать с №:"), row, 0)
        ren_l.addWidget(self.ed_ren_start, row, 1)
        ren_l.addWidget(self.btn_ren_run, row, 2); row += 1

        rename_tab, rename_layout = make_scroll_tab()
        ren_hint = QtWidgets.QLabel("Переименуй ролики перед блюром: можно тянуть названия из titles.txt или нумеровать автоматически.")
        ren_hint.setWordWrap(True)
        ren_hint.setStyleSheet("QLabel{color:#94a3b8;font-size:11px;}")
        rename_layout.addWidget(ren_hint)
        rename_layout.addWidget(grp_ren)
        rename_layout.addStretch(1)
        self.task_tabs.addTab(rename_tab, "Переименование")

        # --- Склейка: сколько клипов в один ---
        grp_merge = QtWidgets.QGroupBox("Склейка (merge)")
        mg = QtWidgets.QHBoxLayout(grp_merge)
        mg.addWidget(QtWidgets.QLabel("Склеивать по N клипов:"))
        self.sb_merge_group = QtWidgets.QSpinBox(); self.sb_merge_group.setRange(1, 1000)
        self.sb_merge_group.setValue(int(self.cfg.get("merge",{}).get("group_size",3)))
        self.btn_apply_merge = QtWidgets.QPushButton("Применить")
        mg.addWidget(self.sb_merge_group)
        mg.addWidget(self.btn_apply_merge)
        mg.addStretch(1)

        merge_tab, merge_layout = make_scroll_tab()
        merge_hint = QtWidgets.QLabel("После блюра можно склеить клипы в ленты — выбери размер группы и нажми применить.")
        merge_hint.setWordWrap(True)
        merge_hint.setStyleSheet("QLabel{color:#94a3b8;font-size:11px;}")
        merge_layout.addWidget(merge_hint)
        merge_layout.addWidget(grp_merge)
        merge_layout.addStretch(1)
        self.task_tabs.addTab(merge_tab, "Склейка")

        self.tabs.addTab(self.tab_tasks, "Задачи")

        # TAB: YouTube uploader
        yt_cfg = self.cfg.get("youtube", {}) or {}
        self.tab_youtube, ty = make_scroll_tab()

        grp_channels = QtWidgets.QGroupBox("Каналы и доступы")
        gc_layout = QtWidgets.QHBoxLayout(grp_channels)
        gc_layout.setSpacing(12)

        self.lst_youtube_channels = QtWidgets.QListWidget()
        self.lst_youtube_channels.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        gc_layout.addWidget(self.lst_youtube_channels, 1)

        ch_form = QtWidgets.QFormLayout()
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

        src_wrap = QtWidgets.QWidget(); src_l = QtWidgets.QHBoxLayout(src_wrap); src_l.setContentsMargins(0,0,0,0)
        self.ed_youtube_src = QtWidgets.QLineEdit(yt_cfg.get("upload_src_dir", self.cfg.get("merged_dir", str(MERG_DIR))))
        self.btn_youtube_src_browse = QtWidgets.QPushButton("…")
        src_l.addWidget(self.ed_youtube_src, 1)
        src_l.addWidget(self.btn_youtube_src_browse)
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
        self.tabs.addTab(self.tab_youtube, "YouTube")

        tk_cfg = self.cfg.get("tiktok", {}) or {}
        self.tab_tiktok, tt = make_scroll_tab()

        grp_tt_profiles = QtWidgets.QGroupBox("Профили и авторизация")
        tp_layout = QtWidgets.QHBoxLayout(grp_tt_profiles)
        tp_layout.setSpacing(16)
        tp_layout.setContentsMargins(16, 16, 16, 16)

        self.lst_tiktok_profiles = QtWidgets.QListWidget()
        self.lst_tiktok_profiles.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.lst_tiktok_profiles.setUniformItemSizes(True)
        self.lst_tiktok_profiles.setMinimumWidth(180)
        tp_layout.addWidget(self.lst_tiktok_profiles, 1)

        profile_panel = QtWidgets.QWidget()
        profile_layout = QtWidgets.QVBoxLayout(profile_panel)
        profile_layout.setContentsMargins(0, 0, 0, 0)
        profile_layout.setSpacing(10)

        tt_hint = QtWidgets.QLabel("Укажи client_key, client_secret, open_id и refresh_token TikTok. Можно загрузить их из JSON/" "YAML файла и хранить вне конфига.")
        tt_hint.setWordWrap(True)
        tt_hint.setStyleSheet("QLabel{color:#94a3b8;font-size:11px;}")
        profile_layout.addWidget(tt_hint)

        tt_form = QtWidgets.QFormLayout()
        tt_form.setSpacing(10)
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
        btn_tt_row.setSpacing(8)
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
        self.ed_tiktok_src = QtWidgets.QLineEdit(tk_cfg.get("upload_src_dir", self.cfg.get("merged_dir", str(MERG_DIR))))
        self.btn_tiktok_src_browse = QtWidgets.QPushButton("…")
        src_tt_layout.addWidget(self.ed_tiktok_src, 1)
        src_tt_layout.addWidget(self.btn_tiktok_src_browse)
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
        self.tabs.addTab(self.tab_tiktok, "TikTok")

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
        self.btn_save_and_run_autogen = QtWidgets.QPushButton("Сохранить и запустить автоген")
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
        self.tabs.addTab(self.tab_prompts, "Промпты")

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
        self.tabs.addTab(self.tab_titles, "Названия")

        # TAB: Настройки
        self.tab_settings = QtWidgets.QScrollArea()
        self.tab_settings.setWidgetResizable(True)
        settings_body = QtWidgets.QWidget()
        settings_layout = QtWidgets.QVBoxLayout(settings_body)
        settings_layout.setContentsMargins(16, 16, 16, 16)
        settings_intro = QtWidgets.QLabel("Настройки сгруппированы по вкладкам: каталоги, Chrome, FFmpeg, YouTube, Telegram и обслуживание.")
        settings_intro.setWordWrap(True)
        settings_intro.setStyleSheet("QLabel{color:#94a3b8;font-size:11px;}")
        settings_layout.addWidget(settings_intro)

        self.settings_tabs = QtWidgets.QTabWidget()
        settings_layout.addWidget(self.settings_tabs, 1)

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
        self.tabs.addTab(self.tab_settings, "Настройки")
        # TAB: Ошибки
        self.tab_errors = QtWidgets.QWidget(); err_layout = QtWidgets.QVBoxLayout(self.tab_errors)
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
        self.tabs.addTab(self.tab_errors, "Ошибки")
        # TAB: История
        self.tab_history = QtWidgets.QWidget(); h = QtWidgets.QVBoxLayout(self.tab_history)
        self.btn_reload_history = QtWidgets.QPushButton("Обновить")
        self.txt_history = QtWidgets.QPlainTextEdit(); self.txt_history.setReadOnly(True)
        h.addWidget(self.btn_reload_history, 0, QtCore.Qt.AlignmentFlag.AlignLeft)
        h.addWidget(self.txt_history, 1)
        self.tabs.addTab(self.tab_history, "История")

        self._load_zones_into_ui()
        self._toggle_youtube_schedule()

    def _build_settings_pages(self):
        ch = self.cfg.get("chrome", {})
        yt_cfg = self.cfg.get("youtube", {})

        # --- Пути проекта ---
        page_paths = QtWidgets.QWidget()
        grid_paths = QtWidgets.QGridLayout(page_paths)
        grid_paths.setColumnStretch(1, 1)
        grid_paths.setVerticalSpacing(8)
        grid_paths.setHorizontalSpacing(10)
        row = 0

        self.ed_root = QtWidgets.QLineEdit(self.cfg.get("project_root", str(PROJECT_ROOT)))
        self.btn_browse_root = QtWidgets.QPushButton("…")
        grid_paths.addWidget(QtWidgets.QLabel("Папка проекта:"), row, 0)
        grid_paths.addWidget(self.ed_root, row, 1)
        grid_paths.addWidget(self.btn_browse_root, row, 2)
        row += 1

        self.ed_downloads = QtWidgets.QLineEdit(self.cfg.get("downloads_dir", str(DL_DIR)))
        self.btn_browse_downloads = QtWidgets.QPushButton("…")
        grid_paths.addWidget(QtWidgets.QLabel("Папка RAW:"), row, 0)
        grid_paths.addWidget(self.ed_downloads, row, 1)
        grid_paths.addWidget(self.btn_browse_downloads, row, 2)
        row += 1

        self.ed_blurred = QtWidgets.QLineEdit(self.cfg.get("blurred_dir", str(BLUR_DIR)))
        self.btn_browse_blurred = QtWidgets.QPushButton("…")
        grid_paths.addWidget(QtWidgets.QLabel("Папка BLURRED:"), row, 0)
        grid_paths.addWidget(self.ed_blurred, row, 1)
        grid_paths.addWidget(self.btn_browse_blurred, row, 2)
        row += 1

        self.ed_merged = QtWidgets.QLineEdit(self.cfg.get("merged_dir", str(MERG_DIR)))
        self.btn_browse_merged = QtWidgets.QPushButton("…")
        grid_paths.addWidget(QtWidgets.QLabel("Папка MERGED:"), row, 0)
        grid_paths.addWidget(self.ed_merged, row, 1)
        grid_paths.addWidget(self.btn_browse_merged, row, 2)
        row += 1

        self.ed_blur_src = QtWidgets.QLineEdit(self.cfg.get("blur_src_dir", self.cfg.get("downloads_dir", str(DL_DIR))))
        self.btn_browse_blur_src = QtWidgets.QPushButton("…")
        grid_paths.addWidget(QtWidgets.QLabel("Источник BLUR:"), row, 0)
        grid_paths.addWidget(self.ed_blur_src, row, 1)
        grid_paths.addWidget(self.btn_browse_blur_src, row, 2)
        row += 1

        self.ed_merge_src = QtWidgets.QLineEdit(self.cfg.get("merge_src_dir", self.cfg.get("blurred_dir", str(BLUR_DIR))))
        self.btn_browse_merge_src = QtWidgets.QPushButton("…")
        grid_paths.addWidget(QtWidgets.QLabel("Источник MERGE:"), row, 0)
        grid_paths.addWidget(self.ed_merge_src, row, 1)
        grid_paths.addWidget(self.btn_browse_merge_src, row, 2)

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
        ui_layout = QtWidgets.QVBoxLayout(page_ui)
        ui_layout.setContentsMargins(12, 12, 12, 12)
        grp_ui = QtWidgets.QGroupBox("Отображение")
        ui_form = QtWidgets.QFormLayout(grp_ui)
        self.cb_ui_show_activity = QtWidgets.QCheckBox("Показывать историю событий в левой панели")
        self.cb_ui_show_activity.setChecked(bool(self.cfg.get("ui", {}).get("show_activity", True)))
        ui_form.addRow(self.cb_ui_show_activity)

        self.cmb_ui_activity_density = QtWidgets.QComboBox()
        self.cmb_ui_activity_density.addItem("Компактная", "compact")
        self.cmb_ui_activity_density.addItem("Стандартная", "cozy")
        density_cur = self.cfg.get("ui", {}).get("activity_density", "compact")
        idx = self.cmb_ui_activity_density.findData(density_cur)
        if idx < 0:
            idx = 0
        self.cmb_ui_activity_density.setCurrentIndex(idx)
        ui_form.addRow("Вид истории событий:", self.cmb_ui_activity_density)

        ui_hint = QtWidgets.QLabel("Когда история скрыта, остаётся только карточка с текущим этапом.")
        ui_hint.setWordWrap(True)
        ui_hint.setStyleSheet("QLabel{color:#94a3b8;font-size:11px;}")
        ui_form.addRow(ui_hint)
        ui_layout.addWidget(grp_ui)
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
        self.ed_prof_root = self.ed_prof_userdir
        self.ed_prof_dir = self.ed_prof_directory
        form.addRow("Название:", self.ed_prof_name)
        form.addRow("user_data_dir:", self.ed_prof_userdir)
        form.addRow("profile_directory:", self.ed_prof_directory)
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
        self.cmb_active_preset = QtWidgets.QComboBox()
        ff_form.addRow("Активный пресет:", self.cmb_active_preset)
        ff_layout.addLayout(ff_form)

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
        grid_yt = QtWidgets.QGridLayout(page_yt)
        grid_yt.setColumnStretch(1, 1)
        self.sb_youtube_default_delay = QtWidgets.QSpinBox(); self.sb_youtube_default_delay.setRange(0, 7 * 24 * 60)
        self.sb_youtube_default_delay.setValue(int(yt_cfg.get("schedule_minutes_from_now", 60)))
        grid_yt.addWidget(QtWidgets.QLabel("Отложить по умолчанию (мин):"), 0, 0)
        grid_yt.addWidget(self.sb_youtube_default_delay, 0, 1)
        self.cb_youtube_default_draft = QtWidgets.QCheckBox("По умолчанию только приватный черновик")
        self.cb_youtube_default_draft.setChecked(bool(yt_cfg.get("draft_only", False)))
        grid_yt.addWidget(self.cb_youtube_default_draft, 1, 0, 1, 2)

        archive_wrap = QtWidgets.QWidget(); archive_l = QtWidgets.QHBoxLayout(archive_wrap); archive_l.setContentsMargins(0, 0, 0, 0)
        self.ed_youtube_archive = QtWidgets.QLineEdit(yt_cfg.get("archive_dir", str(PROJECT_ROOT / "uploaded")))
        self.btn_youtube_archive_browse = QtWidgets.QPushButton("…")
        archive_l.addWidget(self.ed_youtube_archive, 1)
        archive_l.addWidget(self.btn_youtube_archive_browse)
        grid_yt.addWidget(QtWidgets.QLabel("Архив загруженных:"), 2, 0)
        grid_yt.addWidget(archive_wrap, 2, 1)

        grid_yt.addWidget(QtWidgets.QLabel("Интервал для пакетов (мин):"), 3, 0)
        self.sb_youtube_interval_default = QtWidgets.QSpinBox(); self.sb_youtube_interval_default.setRange(0, 7 * 24 * 60)
        self.sb_youtube_interval_default.setValue(int(yt_cfg.get("batch_step_minutes", 60)))
        grid_yt.addWidget(self.sb_youtube_interval_default, 3, 1)
        grid_yt.addWidget(QtWidgets.QLabel("Ограничение пакета (0 = все):"), 4, 0)
        self.sb_youtube_limit_default = QtWidgets.QSpinBox(); self.sb_youtube_limit_default.setRange(0, 999)
        self.sb_youtube_limit_default.setValue(int(yt_cfg.get("batch_limit", 0)))
        grid_yt.addWidget(self.sb_youtube_limit_default, 4, 1)

        self.settings_tabs.addTab(page_yt, "YouTube")

        page_tt = QtWidgets.QWidget()
        grid_tt = QtWidgets.QGridLayout(page_tt)
        grid_tt.setColumnStretch(1, 1)
        tk_defaults = self.cfg.get("tiktok", {}) or {}

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
        self.ed_tiktok_archive = QtWidgets.QLineEdit(tk_defaults.get("archive_dir", str(PROJECT_ROOT / "uploaded_tiktok")))
        self.btn_tiktok_archive_browse = QtWidgets.QPushButton("…")
        archive_tt_layout.addWidget(self.ed_tiktok_archive, 1)
        archive_tt_layout.addWidget(self.btn_tiktok_archive_browse)
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
        self.ed_tiktok_workflow_settings = QtWidgets.QLineEdit(tk_defaults.get("github_workflow", ".github/workflows/tiktok-upload.yml"))
        self.ed_tiktok_ref_settings = QtWidgets.QLineEdit(tk_defaults.get("github_ref", "main"))
        workflow_tt_layout.addWidget(self.ed_tiktok_workflow_settings, 1)
        workflow_tt_layout.addWidget(self.ed_tiktok_ref_settings, 1)
        grid_tt.addWidget(QtWidgets.QLabel("Workflow / Branch:"), 5, 0)
        grid_tt.addWidget(workflow_tt_wrap, 5, 1)

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

        # --- Telegram ---
        tg_cfg = self.cfg.get("telegram", {}) or {}
        page_tg = QtWidgets.QWidget()
        tg_form = QtWidgets.QFormLayout(page_tg)
        self.cb_tg_enabled = QtWidgets.QCheckBox("Включить уведомления")
        self.cb_tg_enabled.setChecked(bool(tg_cfg.get("enabled", False)))
        tg_form.addRow(self.cb_tg_enabled)
        self.ed_tg_token = QtWidgets.QLineEdit(tg_cfg.get("bot_token", ""))
        self.ed_tg_token.setPlaceholderText("123456:ABCDEF...")
        tg_form.addRow("Bot token:", self.ed_tg_token)
        self.ed_tg_chat = QtWidgets.QLineEdit(tg_cfg.get("chat_id", ""))
        self.ed_tg_chat.setPlaceholderText("@channel или chat id")
        tg_form.addRow("Chat ID:", self.ed_tg_chat)
        btn_row = QtWidgets.QHBoxLayout()
        self.btn_tg_test = QtWidgets.QPushButton("Отправить тест")
        btn_row.addWidget(self.btn_tg_test)
        btn_row.addStretch(1)
        tg_form.addRow(btn_row)
        hint = QtWidgets.QLabel("Уведомления отправляются после завершения шагов сценария.")
        hint.setWordWrap(True)
        tg_form.addRow(hint)
        self.settings_tabs.addTab(page_tg, "Telegram")

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
        self.settings_tabs.addTab(page_docs, "Документация")

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
            (getattr(self, "ed_tiktok_src", None), self.cfg.get("tiktok", {}).get("upload_src_dir", self.cfg.get("merged_dir", str(MERG_DIR))))
        ]
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
            (self.sb_max_videos, "valueChanged"),
            (self.cb_ui_show_activity, "toggled"),
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
        ]
        for widget, signal_name in watchers:
            signal = getattr(widget, signal_name, None)
            if signal:
                signal.connect(self._mark_settings_dirty)

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

    def _load_readme_preview(self):
        if not hasattr(self, "txt_readme"):
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
                return

        self.txt_readme.setPlainText("README.md не найден в папке приложения")
        if hasattr(self, "lst_activity"):
            self._append_activity("README.md не найден", kind="error")

    def _wire(self):
        # статусы/лог — безопасные слоты GUI-потока
        self.sig_set_status.connect(self._slot_set_status)
        self.sig_log.connect(self._slot_log)

        self.btn_open_chrome.clicked.connect(self._open_chrome)
        self.btn_open_root.clicked.connect(lambda: open_in_finder(self.cfg.get("project_root", PROJECT_ROOT)))
        self.btn_open_raw.clicked.connect(lambda: open_in_finder(self.cfg.get("downloads_dir", DL_DIR)))
        self.btn_open_blur.clicked.connect(lambda: open_in_finder(self.cfg.get("blurred_dir", BLUR_DIR)))
        self.btn_open_merge.clicked.connect(lambda: open_in_finder(self.cfg.get("merged_dir", MERG_DIR)))
        self.btn_stop_all.clicked.connect(self._stop_all)
        self.btn_start_selected.clicked.connect(self._run_scenario)
        self.btn_activity_clear.clicked.connect(self._clear_activity)
        self.chk_activity_visible.toggled.connect(self._on_activity_toggle)

        self.btn_load_prompts.clicked.connect(self._load_prompts)
        self.btn_save_prompts.clicked.connect(self._save_prompts)
        self.btn_save_and_run_autogen.clicked.connect(self._save_and_run_autogen)
        self.btn_used_refresh.clicked.connect(self._reload_used_prompts)
        self.btn_used_clear.clicked.connect(self._clear_used_prompts)
        self.lst_prompt_profiles.itemSelectionChanged.connect(self._on_prompt_profile_selection)
        self.btn_load_titles.clicked.connect(self._load_titles)
        self.btn_save_titles.clicked.connect(self._save_titles)
        self.btn_reset_titles_cursor.clicked.connect(self._reset_titles_cursor)

        self.btn_apply_dl.clicked.connect(self._apply_dl_limit)
        self.btn_run_scenario.clicked.connect(self._run_scenario)

        self.btn_reload_history.clicked.connect(self._reload_history)
        self.btn_save_settings.clicked.connect(self._save_settings_clicked)
        self.btn_save_autogen_cfg.clicked.connect(self._save_autogen_cfg)
        self.btn_reload_readme.clicked.connect(self._load_readme_preview)
        self.btn_env_check.clicked.connect(self._run_env_check)
        self.btn_update_check.clicked.connect(lambda: self._check_for_updates(dry_run=True))
        self.btn_update_pull.clicked.connect(lambda: self._check_for_updates(dry_run=False))
        self.btn_quick_update.clicked.connect(lambda: self._check_for_updates(dry_run=False))
        self.btn_maintenance_cleanup.clicked.connect(lambda: self._run_maintenance_cleanup(manual=True))
        self.btn_maintenance_sizes.clicked.connect(self._report_dir_sizes)
        self.cmb_ui_activity_density.currentIndexChanged.connect(self._on_activity_density_changed)
        self.cmb_active_preset.currentTextChanged.connect(self._on_active_preset_changed)
        self.btn_preset_add.clicked.connect(self._on_preset_add)
        self.btn_preset_delete.clicked.connect(self._on_preset_delete)
        self.btn_preset_preview.clicked.connect(self._open_blur_preview)

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

    def _init_state(self):
        self.runner_autogen = ProcRunner("AUTOGEN")
        self.runner_dl = ProcRunner("DL")
        self.runner_upload = ProcRunner("YT")
        self.runner_tiktok = ProcRunner("TT")
        self.runner_autogen.line.connect(self._slot_log)
        self.runner_dl.line.connect(self._slot_log)
        self.runner_upload.line.connect(self._slot_log)
        self.runner_tiktok.line.connect(self._slot_log)
        self.runner_autogen.finished.connect(self._proc_done)
        self.runner_dl.finished.connect(self._proc_done)
        self.runner_upload.finished.connect(self._proc_done)
        self.runner_tiktok.finished.connect(self._proc_done)
        self.runner_autogen.notify.connect(self._notify)
        self.runner_dl.notify.connect(self._notify)
        self.runner_upload.notify.connect(self._notify)
        self.runner_tiktok.notify.connect(self._notify)
        self._post_status("Готово", state="idle")

    # ----- безопасные слоты GUI-потока -----
    @QtCore.pyqtSlot(str, int, int, str)
    def _slot_set_status(self, text: str, progress: int, total: int, state: str):
        # state: idle|running|ok|error
        self.lbl_status.setText(text)
        if total > 0:
            self.pb_global.setMaximum(total); self.pb_global.setValue(progress); self.pb_global.setFormat(f"{progress}/{total}")
        else:
            self.pb_global.setMaximum(1); self.pb_global.setValue(1); self.pb_global.setFormat("—")
        color = "#777"
        if state == "running": color = "#f6a700"
        if state == "ok": color = "#1bb55c"
        if state == "error": color = "#d74c4c"
        self.pb_global.setStyleSheet(f"QProgressBar::chunk {{ background-color: {color}; }}")

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
        seen: set[Path] = set()

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
        collect(auto_cfg.get("submitted_log"), "Основной")
        for inst in auto_cfg.get("instances", []) or []:
            collect(inst.get("submitted_log"), inst.get("name") or "Instance")

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
        paths = set()
        auto_cfg = self.cfg.get("autogen", {}) or {}
        if auto_cfg.get("submitted_log"):
            paths.add(_project_path(auto_cfg.get("submitted_log")))
        for inst in auto_cfg.get("instances", []) or []:
            if inst.get("submitted_log"):
                paths.add(_project_path(inst.get("submitted_log")))
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

        item = QtWidgets.QListWidgetItem(text)
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
        self._style_activity_item(item)
        self.lst_activity.addItem(item)
        while self.lst_activity.count() > 200:
            self.lst_activity.takeItem(0)
        self.lst_activity.scrollToBottom()

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
            "[NOTIFY] AUTOGEN_START": ("Autogen", "Началась вставка промптов"),
            "[NOTIFY] AUTOGEN_FINISH_OK": ("Autogen", "Вставка промптов — успешно"),
            "[NOTIFY] AUTOGEN_FINISH_PARTIAL": ("Autogen", "Вставка промптов — частично (были отказы)"),
            "[NOTIFY] DOWNLOAD_START": ("Downloader", "Началась автоскачка"),
            "[NOTIFY] DOWNLOAD_FINISH": ("Downloader", "Автоскачка завершена"),
        }
        notif = markers.get(clean.strip())
        if notif:
            self._notify(*notif)
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

        timestamp = time.strftime("%H:%M:%S")
        pretty = f"[{timestamp}] {normalized}"
        self._append_activity(pretty, kind=kind, card_text=False)

    # helper для статуса
    def _post_status(self, text: str, progress: int = 0, total: int = 0, state: str = "idle"):
        self.sig_set_status.emit(text, progress, total, state)

    def _clear_activity(self):
        self.lst_activity.clear()
        self._post_status("Лента событий очищена", state="idle")
        self._update_current_event("—", "info")

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
        if hasattr(self, "cb_ui_show_activity"):
            self.cb_ui_show_activity.blockSignals(True)
            self.cb_ui_show_activity.setChecked(bool(visible))
            self.cb_ui_show_activity.blockSignals(False)
        self.cfg.setdefault("ui", {})["show_activity"] = bool(visible)
        if persist:
            save_cfg(self.cfg)

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

        for idx in range(self.lst_activity.count()):
            item = self.lst_activity.item(idx)
            if item:
                self._style_activity_item(item, density)

        self.cfg.setdefault("ui", {})["activity_density"] = density
        if persist:
            save_cfg(self.cfg)

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
    def _open_chrome(self):
        try:
            port = int(self.cfg.get("chrome", {}).get("cdp_port", 9222))
        except Exception:
            port = 9222

        ch = self.cfg.get("chrome", {})
        if sys.platform == "darwin":
            default_chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        elif sys.platform.startswith("win"):
            default_chrome = r"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
        else:
            default_chrome = "google-chrome"
        chrome_bin = os.path.expandvars(ch.get("binary") or default_chrome)
        profiles = ch.get("profiles", [])
        active_name = ch.get("active_profile", "")
        fallback_userdir = os.path.expandvars(ch.get("user_data_dir", "") or "")

        # уже поднят CDP?
        if port_in_use(port) and cdp_ready(port):
            self._post_status(f"Chrome уже поднят (CDP {port})", state="idle")
            return

        # активный профиль
        active = None
        if active_name:
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
                    self._post_status(f"Chrome c CDP {port} (профиль: {active_name or 'shadow'})", state="ok")
                    append_history(self.cfg, {"event": "chrome_launch", "port": port, "profile": active_name, "shadow": str(shadow_root)})
                    return
                time.sleep(0.25)

            self._post_status("CDP не поднялся — проверь бинарь Chrome и порт", state="error")

        except Exception as e:
            self._post_status(f"Ошибка запуска Chrome/shadow: {e}", state="error")

    # ----- Prompts/Titles -----
    def _prompts_path(self, key: Optional[str] = None) -> Path:
        active = key or self._current_prompt_profile_key or PROMPTS_DEFAULT_KEY
        if active in ("", PROMPTS_DEFAULT_KEY):
            return self._default_profile_prompts(None)
        return self._default_profile_prompts(active)

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

    def _save_and_run_autogen(self):
        self._save_prompts()
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
        env=os.environ.copy(); env["PYTHONUNBUFFERED"]="1"
        env["SORA_PROMPTS_FILE"]=str(self._prompts_path())  # FIX: автоген читает именно этот файл
        self._post_status("Вставка промптов…", state="running")
        self.runner_autogen.run([sys.executable, entry], cwd=workdir, env=env)

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
        self.cfg.setdefault("downloader", {})["max_videos"] = n
        save_cfg(self.cfg)
        self._post_status(f"Будут скачаны последние {n if n>0 else 'ВСЕ'}", state="ok")

    # ----- Merge opts -----
    def _apply_merge_opts(self):
        n = int(self.sb_merge_group.value())
        self.cfg.setdefault("merge", {})["group_size"] = n
        save_cfg(self.cfg)
        self._post_status(f"Склеивать по {n} клипов", state="ok")

    # ----- Scenario -----
    def _run_scenario(self):
        steps = []
        if self.cb_do_autogen.isChecked(): steps.append("autogen")
        if self.cb_do_download.isChecked(): steps.append("download")
        if self.cb_do_blur.isChecked(): steps.append("blur")
        if self.cb_do_merge.isChecked(): steps.append("merge")
        if self.cb_do_upload.isChecked(): steps.append("upload")
        if self.cb_do_tiktok.isChecked(): steps.append("tiktok")
        if not steps:
            self._post_status("Ничего не выбрано", state="error"); return

        self._save_settings_clicked(silent=True)
        self._post_status("Запуск сценария…", state="running")
        append_history(self.cfg, {"event":"scenario_start","steps":steps})

        label_map = {
            "autogen": "Autogen",
            "download": "Download",
            "blur": "Blur",
            "merge": "Merge",
            "upload": "YouTube",
            "tiktok": "TikTok",
        }
        summary = " → ".join(label_map.get(step, step) for step in steps)
        if summary:
            self._append_activity(f"Сценарий: {summary}", kind="info", card_text=False)

        def flow():
            ok_all = True
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

    def _run_autogen_sync(self) -> bool:
        self._save_settings_clicked(silent=True)
        workdir=self.cfg.get("autogen",{}).get("workdir", str(WORKERS_DIR / "autogen"))
        entry=self.cfg.get("autogen",{}).get("entry","main.py")
        python=sys.executable; cmd=[python, entry]; env=os.environ.copy(); env["PYTHONUNBUFFERED"]="1"
        env["SORA_PROMPTS_FILE"]=str(self._prompts_path())  # FIX: синхронный запуск тоже
        self._send_tg("✍️ Autogen запускается")
        self._post_status("Вставка промптов…", state="running")
        rc = self._await_runner(self.runner_autogen, "AUTOGEN", lambda: self.runner_autogen.run(cmd, cwd=workdir, env=env))
        ok = rc == 0
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
        self._send_tg(f"⬇️ Скачивание {status}: +{delta} файлов (итого {after}) → {dest_dir}")
        return ok

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

        def blur_one(v: Path) -> bool:
            out = dst_dir / v.name
            delogos = ",".join([
                f"delogo=x={z['x']}:y={z['y']}:w={z['w']}:h={z['h']}:show=0" for z in zones
            ])
            vf = delogos + (f",{post}" if post else "") + ",format=yuv420p"

            def _build_cmd(use_hw: bool, audio_copy: bool) -> List[str]:
                cmd = [ffbin, "-hide_banner", "-loglevel", "info", "-y"]
                if use_hw and sys.platform == "darwin":
                    cmd += ["-hwaccel", "videotoolbox"]
                cmd += ["-i", str(v), "-vf", vf, "-map", "0:v", "-map", "0:a?"]
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
            try:
                for label, use_hw, audio_copy_flag in attempts:
                    tried_labels.append(label)
                    rc, tail = _run_ffmpeg(_build_cmd(use_hw, audio_copy_flag), log_prefix=f"BLUR:{v.name}")
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
                if not ok:
                    note = error_note or last_line or "ffmpeg завершился с ошибкой"
                    failures.append(f"{v.name}: {note}")
            return ok

        with ThreadPoolExecutor(max_workers=max(1, threads)) as ex:
            results = list(ex.map(blur_one, videos))

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
    def _run_merge_sync(self) -> bool:
        self._save_settings_clicked(silent=True)
        merge_cfg = self.cfg.get("merge", {}) or {}
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
        self.runner_autogen.stop()
        self.runner_dl.stop()
        self.runner_upload.stop()
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

        dl_cfg = self.cfg.setdefault("downloader", {})
        dl_cfg["max_videos"] = int(self.sb_max_videos.value())

        ui_cfg = self.cfg.setdefault("ui", {})
        ui_cfg["show_activity"] = bool(self.cb_ui_show_activity.isChecked())
        ui_cfg["activity_density"] = self.cmb_ui_activity_density.currentData() or "compact"

        maint_cfg = self.cfg.setdefault("maintenance", {})
        maint_cfg["auto_cleanup_on_start"] = bool(self.cb_maintenance_auto.isChecked())
        retention = maint_cfg.setdefault("retention_days", {})
        retention["downloads"] = int(self.sb_maint_downloads.value())
        retention["blurred"] = int(self.sb_maint_blurred.value())
        retention["merged"] = int(self.sb_maint_merged.value())

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
    def _refresh_stats(self):
        try:
            def _count_vids(p: Path) -> int:
                if not p.exists():
                    return 0
                return sum(len(list(p.glob(x))) for x in ("*.mp4", "*.mov", "*.m4v", "*.webm"))

            raw  = _count_vids(_project_path(self.cfg.get("downloads_dir", str(DL_DIR))))
            blur = _count_vids(_project_path(self.cfg.get("blurred_dir", str(BLUR_DIR))))
            merg = _count_vids(_project_path(self.cfg.get("merged_dir", str(MERG_DIR))))
            upload_src = _count_vids(_project_path(self.cfg.get("youtube", {}).get("upload_src_dir", self.cfg.get("merged_dir", str(MERG_DIR)))))
            tiktok_src = _count_vids(_project_path(self.cfg.get("tiktok", {}).get("upload_src_dir", self.cfg.get("merged_dir", str(MERG_DIR)))))
            self.sig_log.emit(f"[STAT] RAW={raw} BLURRED={blur} MERGED={merg} YT={upload_src} TT={tiktok_src}")

            # обновляем визуальные счетчики
            fmt = lambda value: format(value, ",").replace(",", " ")
            self.lbl_stat_raw.setText(fmt(raw))
            self.lbl_stat_blur.setText(fmt(blur))
            self.lbl_stat_merge.setText(fmt(merg))
            self.lbl_stat_upload.setText(fmt(upload_src))
            if hasattr(self, "lbl_stat_tiktok"):
                self.lbl_stat_tiktok.setText(fmt(tiktok_src))
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
        self._refresh_prompt_profiles_ui()

    def _on_profile_selected(self):
        items = self.lst_profiles.selectedItems()
        if not items:
            self.ed_prof_name.clear()
            self.ed_prof_root.clear()
            self.ed_prof_dir.clear()
            return
        name = items[0].text()
        for p in self.cfg.get("chrome", {}).get("profiles", []):
            if p.get("name") == name:
                self.ed_prof_name.setText(p.get("name", ""))
                self.ed_prof_root.setText(p.get("user_data_dir", ""))
                self.ed_prof_dir.setText(p.get("profile_directory", ""))
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
                break
        else:
            profiles.append({"name": name, "user_data_dir": root, "profile_directory": prof})

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

    def _on_profile_set_active(self):
        items = self.lst_profiles.selectedItems()
        if not items:
            return
        name = items[0].text()
        self.cfg.setdefault("chrome", {})["active_profile"] = name
        save_cfg(self.cfg)
        self._refresh_profiles_ui()
        self._post_status(f"Активный профиль: {name}", state="ok")

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
            return

        parts = [f"найдено {count}"]
        if limit > 0:
            parts.append(f"будет загружено {effective}")
        if not self.cb_youtube_draft_only.isChecked() and self.cb_youtube_schedule.isChecked() and interval > 0 and effective > 1:
            parts.append(f"шаг {interval} мин")
        self.lbl_youtube_queue.setText("Очередь: " + ", ".join(parts))

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
            return

        videos = self._iter_videos(src)
        count = len(videos)
        if count == 0:
            self.lbl_tiktok_queue.setText("Очередь: нет видео в папке")
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
            self._load_readme_preview()
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
