#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os  # FIX: нужен в load_cfg/_open_chrome
import re  # FIX: используется в _slot_log, _natural_key
import sys
import json
import yaml
import time
import threading
import subprocess
import socket
import shutil
from pathlib import Path
from urllib.request import urlopen, Request
from typing import Optional, List, Union

from PyQt6 import QtCore, QtGui, QtWidgets
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

# ---------- базовые пути ----------
APP_DIR = Path(__file__).parent.resolve()
PROJECT_ROOT = APP_DIR.parent.resolve()
WORKERS_DIR = PROJECT_ROOT / "workers"
DL_DIR = PROJECT_ROOT / "downloads"
BLUR_DIR = PROJECT_ROOT / "blurred"
MERG_DIR = PROJECT_ROOT / "merged"
HIST_FILE = PROJECT_ROOT / "history.jsonl"   # JSONL по-умолчанию (с обратн. совместимостью)
TITLES_FILE = PROJECT_ROOT / "titles.txt"

CFG_PATH = APP_DIR / "app_config.yaml"


# ---------- утилиты ----------
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

    downloader = data.setdefault("downloader", {})
    downloader.setdefault("workdir", str(WORKERS_DIR / "downloader"))
    downloader.setdefault("entry", "download_all.py")

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

    return data


def save_cfg(cfg: dict):
    with open(CFG_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)


def ensure_dirs(cfg: dict):
    for key in ["downloads_dir", "blurred_dir", "merged_dir"]:
        raw = cfg.get(key, "") or ""
        path = Path(os.path.expandvars(raw)).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        cfg[key] = str(path)
    yt = cfg.get("youtube", {}) or {}
    archive = yt.get("archive_dir")
    if archive:
        archive_path = Path(os.path.expandvars(archive)).expanduser()
        archive_path.mkdir(parents=True, exist_ok=True)
        yt["archive_dir"] = str(archive_path)


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
    hist_path = Path(cfg.get("history_file", HIST_FILE))
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
    path = str(path)
    if sys.platform == "darwin":
        subprocess.Popen(["open", path])
    elif sys.platform.startswith("win"):
        subprocess.Popen(["explorer", path])
    else:
        subprocess.Popen(["xdg-open", path])


def send_tg(cfg: dict, text: str):
    tg = cfg.get("telegram", {}) or {}
    if not tg.get("enabled"):
        return
    token, chat = tg.get("bot_token"), tg.get("chat_id")
    if not token or not chat:
        return
    try:
        import urllib.parse
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = urllib.parse.urlencode({"chat_id": chat, "text": text})
        req = Request(url, data=payload.encode("utf-8"), headers={
            "Content-Type": "application/x-www-form-urlencoded"
        })
        with urlopen(req, timeout=5):
            pass
    except Exception:
        pass


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
def _run_ffmpeg(cmd: List[str], log_prefix: str = "FFMPEG") -> int:
    """
    Запускает FFmpeg, пишет stdout/stderr в логи через self.sig_log.
    self передаём через _run_ffmpeg._self из конструктора окна.
    """
    try:
        p = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
        assert p.stdout
        for ln in p.stdout:
            self = getattr(_run_ffmpeg, "_self", None)
            if self:
                self.sig_log.emit(f"[{log_prefix}] {ln.rstrip()}")
        rc = p.wait()
        return rc
    except FileNotFoundError:
        self = getattr(_run_ffmpeg, "_self", None)
        if self:
            self.sig_log.emit(f"[{log_prefix}] ffmpeg не найден. Проверь путь в Настройках → ffmpeg.")
        return 127
    except Exception as e:
        self = getattr(_run_ffmpeg, "_self", None)
        if self:
            self.sig_log.emit(f"[{log_prefix}] ошибка запуска: {e}")
        return 1


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

        self.setWindowTitle("Sora Suite — Control Panel")
        self.resize(1500, 950)

        # tray notifications
        self.tray = QtWidgets.QSystemTrayIcon(self)
        icon = self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_ComputerIcon)
        self.tray.setIcon(icon)
        self.tray.setToolTip("Sora Suite")
        self.tray.show()

        # трекинг активных подпроцессов (ffmpeg и т.п.)
        self._active_procs: set[subprocess.Popen] = set()
        self._procs_lock = Lock()

        self._build_ui()
        self._wire()
        self._init_state()
        self._refresh_stats()
        self._reload_history()
        self._refresh_profiles_ui()
        self._refresh_youtube_ui()
        self._load_autogen_cfg_ui()

        # дать раннеру ffmpeg доступ к self для логов
        _run_ffmpeg._self = self  # type: ignore[attr-defined]

    # ----- helpers -----
    def _notify(self, title: str, message: str):
        try:
            self.tray.showMessage(title, message, QtWidgets.QSystemTrayIcon.MessageIcon.Information, 5000)
        except Exception:
            pass

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

    def _sync_draft_checkbox(self):
        self.cb_youtube_draft_only.blockSignals(True)
        self.cb_youtube_draft_only.setChecked(self.cb_youtube_default_draft.isChecked())
        self.cb_youtube_draft_only.blockSignals(False)
        self._toggle_youtube_schedule()

    def _apply_default_delay(self):
        minutes = int(self.sb_youtube_default_delay.value())
        self.dt_youtube_publish.setDateTime(QtCore.QDateTime.currentDateTime().addSecs(minutes * 60))

    # ----- UI -----
    def _build_ui(self):
        central = QtWidgets.QWidget(self)
        self.setCentralWidget(central)
        v = QtWidgets.QVBoxLayout(central)

        banner = QtWidgets.QLabel("<b>Sora Suite</b>: выбери шаги и запусти сценарий. Уведомления появятся в системном трее.")
        banner.setStyleSheet("QLabel{padding:8px;background:#eef;border:1px solid #cde;}")
        v.addWidget(banner)

        tb = QtWidgets.QHBoxLayout()
        self.btn_open_chrome = QtWidgets.QPushButton("Открыть Chrome (CDP)")
        self.btn_open_root = QtWidgets.QPushButton("Открыть папку проекта")
        self.btn_open_raw = QtWidgets.QPushButton("RAW (downloads)")
        self.btn_open_blur = QtWidgets.QPushButton("BLURRED")
        self.btn_open_merge = QtWidgets.QPushButton("MERGED")
        self.btn_stop_all = QtWidgets.QPushButton("Стоп все")
        tb.addWidget(self.btn_open_chrome)
        tb.addWidget(self.btn_open_root)
        tb.addWidget(self.btn_open_raw)
        tb.addWidget(self.btn_open_blur)
        tb.addWidget(self.btn_open_merge)
        tb.addStretch(1)
        tb.addWidget(self.btn_stop_all)
        v.addLayout(tb)

        split = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        v.addWidget(split, 1)

        # слева — логи
        self.tabs_left = QtWidgets.QTabWidget()
        self.page_logs = QtWidgets.QWidget(); gl = QtWidgets.QVBoxLayout(self.page_logs)
        self.txt_logs = QtWidgets.QPlainTextEdit()
        self.txt_logs.setReadOnly(True)
        self.txt_logs.setMaximumBlockCount(3000)  # ограничение роста буфера логов
        gl.addWidget(self.txt_logs, 1)
        self.tabs_left.addTab(self.page_logs, "Логи")
        split.addWidget(self.tabs_left)

        # справа — вкладки
        self.tabs = QtWidgets.QTabWidget()
        split.addWidget(self.tabs)

        # TAB: Задачи
        self.tab_tasks = QtWidgets.QWidget(); lt = QtWidgets.QVBoxLayout(self.tab_tasks)

        grp_choose = QtWidgets.QGroupBox("Что выполнить")
        f = QtWidgets.QFormLayout(grp_choose)
        self.cb_do_autogen = QtWidgets.QCheckBox("Вставка промптов в Sora")
        self.cb_do_download = QtWidgets.QCheckBox("Авто-скачка видео")
        self.cb_do_blur = QtWidgets.QCheckBox("Блюр водяного знака (ffmpeg, пресеты 9:16 / 16:9)")
        self.cb_do_merge = QtWidgets.QCheckBox("Склейка группами N")
        self.cb_do_upload = QtWidgets.QCheckBox("Загрузка на YouTube (отложенный постинг)")
        f.addRow(self.cb_do_autogen); f.addRow(self.cb_do_download)
        f.addRow(self.cb_do_blur); f.addRow(self.cb_do_merge)
        f.addRow(self.cb_do_upload)
        lt.addWidget(grp_choose)

        # --- Скачка: лимит N ---
        grp_dl = QtWidgets.QGroupBox("Скачка")
        hb = QtWidgets.QHBoxLayout(grp_dl)
        hb.addWidget(QtWidgets.QLabel("Скачать N последних:"))
        self.sb_max_videos = QtWidgets.QSpinBox(); self.sb_max_videos.setRange(0, 10000); self.sb_max_videos.setValue(0)
        hb.addWidget(self.sb_max_videos)
        self.btn_apply_dl = QtWidgets.QPushButton("Применить")
        hb.addWidget(self.btn_apply_dl)
        hb.addStretch(1)
        lt.addWidget(grp_dl)

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

        lt.addWidget(grp_ren)

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
        lt.addWidget(grp_merge)

        # --- YouTube загрузка ---
        grp_yt = QtWidgets.QGroupBox("YouTube")
        yt_layout = QtWidgets.QFormLayout(grp_yt)
        self.cmb_youtube_channel = QtWidgets.QComboBox()
        yt_layout.addRow("Канал:", self.cmb_youtube_channel)

        schedule_box = QtWidgets.QWidget()
        sb_l = QtWidgets.QHBoxLayout(schedule_box); sb_l.setContentsMargins(0, 0, 0, 0)
        self.cb_youtube_schedule = QtWidgets.QCheckBox("Планировать публикацию")
        self.cb_youtube_schedule.setChecked(True)
        self.dt_youtube_publish = QtWidgets.QDateTimeEdit(QtCore.QDateTime.currentDateTime())
        self.dt_youtube_publish.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.dt_youtube_publish.setCalendarPopup(True)
        sb_l.addWidget(self.cb_youtube_schedule)
        sb_l.addWidget(self.dt_youtube_publish, 1)
        yt_layout.addRow(schedule_box)

        self.cb_youtube_draft_only = QtWidgets.QCheckBox("Только загрузить как приватный черновик")
        yt_layout.addRow(self.cb_youtube_draft_only)

        src_wrap = QtWidgets.QWidget(); src_l = QtWidgets.QHBoxLayout(src_wrap); src_l.setContentsMargins(0, 0, 0, 0)
        self.ed_youtube_src = QtWidgets.QLineEdit(self.cfg.get("youtube", {}).get("upload_src_dir", self.cfg.get("merged_dir", str(MERG_DIR))))
        self.btn_youtube_src_browse = QtWidgets.QPushButton("…")
        src_l.addWidget(self.ed_youtube_src, 1)
        src_l.addWidget(self.btn_youtube_src_browse)
        yt_layout.addRow("Источник клипов:", src_wrap)

        lt.addWidget(grp_yt)

        # --- Запуск сценария ---
        grp_run = QtWidgets.QGroupBox("Запуск")
        hb2 = QtWidgets.QHBoxLayout(grp_run)
        self.btn_run_scenario = QtWidgets.QPushButton("Старт сценария (галочки сверху)")
        hb2.addWidget(self.btn_run_scenario); hb2.addStretch(1)
        lt.addWidget(grp_run)

        # --- Статистика / статус ---
        grp_stat = QtWidgets.QGroupBox("Статистика / статус")
        vb = QtWidgets.QVBoxLayout(grp_stat)

        # статусная строка + прогресс
        self.lbl_status = QtWidgets.QLabel("—")
        self.pb_global = QtWidgets.QProgressBar(); self.pb_global.setMinimum(0); self.pb_global.setMaximum(1); self.pb_global.setValue(1); self.pb_global.setFormat("—")
        vb.addWidget(self.lbl_status); vb.addWidget(self.pb_global)

        # компактная стата по папкам
        grid_stat = QtWidgets.QGridLayout()
        grid_stat.addWidget(QtWidgets.QLabel("<b>RAW</b>"), 0, 0)
        grid_stat.addWidget(QtWidgets.QLabel("<b>BLURRED</b>"), 0, 1)
        grid_stat.addWidget(QtWidgets.QLabel("<b>MERGED</b>"), 0, 2)
        grid_stat.addWidget(QtWidgets.QLabel("<b>UPLOAD</b>"), 0, 3)
        self.lbl_stat_raw = QtWidgets.QLabel("0")
        self.lbl_stat_blur = QtWidgets.QLabel("0")
        self.lbl_stat_merge = QtWidgets.QLabel("0")
        self.lbl_stat_upload = QtWidgets.QLabel("0")
        for w in (self.lbl_stat_raw, self.lbl_stat_blur, self.lbl_stat_merge, self.lbl_stat_upload):
            w.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            w.setStyleSheet("QLabel{font: 700 16px 'Menlo'; padding:4px; border:1px solid #ddd; background:#fafafa;}")
        grid_stat.addWidget(self.lbl_stat_raw, 1, 0)
        grid_stat.addWidget(self.lbl_stat_blur, 1, 1)
        grid_stat.addWidget(self.lbl_stat_merge, 1, 2)
        grid_stat.addWidget(self.lbl_stat_upload, 1, 3)
        vb.addLayout(grid_stat)

        lt.addWidget(grp_stat)
        self.tabs.addTab(self.tab_tasks, "Задачи")

        # TAB: Промпты
        self.tab_prompts = QtWidgets.QWidget(); pp = QtWidgets.QVBoxLayout(self.tab_prompts)
        bar = QtWidgets.QHBoxLayout()
        self.btn_load_prompts = QtWidgets.QPushButton("Загрузить")
        self.btn_save_prompts = QtWidgets.QPushButton("Сохранить")
        self.btn_save_and_run_autogen = QtWidgets.QPushButton("Сохранить и запустить автоген")
        bar.addWidget(self.btn_load_prompts); bar.addWidget(self.btn_save_prompts); bar.addStretch(1); bar.addWidget(self.btn_save_and_run_autogen)
        pp.addLayout(bar)
        self.ed_prompts = QtWidgets.QPlainTextEdit()
        self.ed_prompts.setPlaceholderText("По одному промпту на строке…")
        pp.addWidget(self.ed_prompts, 1)
        self.tabs.addTab(self.tab_prompts, "Промпты")

        # TAB: Названия
        self.tab_titles = QtWidgets.QWidget(); pt = QtWidgets.QVBoxLayout(self.tab_titles)
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
        s = QtWidgets.QFormLayout(settings_body)

        ch = self.cfg.get("chrome", {})
        yt_cfg = self.cfg.get("youtube", {})
        self.ed_cdp_port = QtWidgets.QLineEdit(str(ch.get("cdp_port", 9222)))
        self.ed_userdir = QtWidgets.QLineEdit(ch.get("user_data_dir", ""))
        self.ed_chrome_bin = QtWidgets.QLineEdit(ch.get("binary", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"))
        s.addRow("Chrome CDP порт:", self.ed_cdp_port)
        s.addRow("Chrome user data dir:", self.ed_userdir)
        s.addRow("Chrome binary:", self.ed_chrome_bin)

        # --- Менеджер профилей Chrome ---
        grp_prof = QtWidgets.QGroupBox("Профили Chrome")
        vlp = QtWidgets.QVBoxLayout(grp_prof)

        top = QtWidgets.QHBoxLayout()
        self.lst_profiles = QtWidgets.QListWidget()
        top.addWidget(self.lst_profiles, 1)

        form = QtWidgets.QFormLayout()
        self.ed_prof_name = QtWidgets.QLineEdit()
        self.ed_prof_root = QtWidgets.QLineEdit()   # user_data_dir
        self.ed_prof_dir  = QtWidgets.QLineEdit()   # profile_directory
        form.addRow("Имя профиля:", self.ed_prof_name)
        form.addRow("user_data_dir:", self.ed_prof_root)
        form.addRow("profile_directory:", self.ed_prof_dir)

        btns = QtWidgets.QHBoxLayout()
        self.btn_prof_add = QtWidgets.QPushButton("Добавить/Обновить")
        self.btn_prof_del = QtWidgets.QPushButton("Удалить")
        self.btn_prof_set = QtWidgets.QPushButton("Сделать активным")
        self.btn_prof_scan = QtWidgets.QPushButton("Автонайти (macOS)")
        btns.addWidget(self.btn_prof_add)
        btns.addWidget(self.btn_prof_del)
        btns.addWidget(self.btn_prof_set)
        btns.addWidget(self.btn_prof_scan)

        form.addRow(btns)
        top.addLayout(form, 2)
        vlp.addLayout(top)

        hl = QtWidgets.QHBoxLayout()
        hl.addWidget(QtWidgets.QLabel("Активный профиль:"))
        self.lbl_prof_active = QtWidgets.QLabel("—")
        hl.addWidget(self.lbl_prof_active); hl.addStretch(1)
        vlp.addLayout(hl)

        s.addRow(grp_prof)

        # --- YouTube аккаунты ---
        grp_yt_cfg = QtWidgets.QGroupBox("YouTube аккаунты")
        vlyt = QtWidgets.QVBoxLayout(grp_yt_cfg)

        yt_top = QtWidgets.QHBoxLayout()
        self.lst_youtube_channels = QtWidgets.QListWidget()
        yt_top.addWidget(self.lst_youtube_channels, 1)

        yt_form = QtWidgets.QFormLayout()
        self.ed_yt_name = QtWidgets.QLineEdit()
        client_wrap = QtWidgets.QWidget(); client_l = QtWidgets.QHBoxLayout(client_wrap); client_l.setContentsMargins(0,0,0,0)
        self.ed_yt_client = QtWidgets.QLineEdit()
        self.btn_yt_client_browse = QtWidgets.QPushButton("…")
        client_l.addWidget(self.ed_yt_client, 1); client_l.addWidget(self.btn_yt_client_browse)

        cred_wrap = QtWidgets.QWidget(); cred_l = QtWidgets.QHBoxLayout(cred_wrap); cred_l.setContentsMargins(0,0,0,0)
        self.ed_yt_credentials = QtWidgets.QLineEdit()
        self.btn_yt_credentials_browse = QtWidgets.QPushButton("…")
        cred_l.addWidget(self.ed_yt_credentials, 1); cred_l.addWidget(self.btn_yt_credentials_browse)

        self.cmb_yt_privacy = QtWidgets.QComboBox(); self.cmb_yt_privacy.addItems(["private", "unlisted", "public"])

        yt_form.addRow("Имя канала:", self.ed_yt_name)
        yt_form.addRow("client_secret.json:", client_wrap)
        yt_form.addRow("credentials.json:", cred_wrap)
        yt_form.addRow("Приватность по умолчанию:", self.cmb_yt_privacy)

        yt_buttons = QtWidgets.QHBoxLayout()
        self.btn_yt_add = QtWidgets.QPushButton("Добавить/Обновить")
        self.btn_yt_delete = QtWidgets.QPushButton("Удалить")
        self.btn_yt_set_active = QtWidgets.QPushButton("Сделать активным")
        yt_buttons.addWidget(self.btn_yt_add)
        yt_buttons.addWidget(self.btn_yt_delete)
        yt_buttons.addWidget(self.btn_yt_set_active)
        yt_form.addRow(yt_buttons)

        yt_top.addLayout(yt_form, 2)
        vlyt.addLayout(yt_top)

        yt_info = QtWidgets.QHBoxLayout()
        yt_info.addWidget(QtWidgets.QLabel("Активный канал:"))
        self.lbl_yt_active = QtWidgets.QLabel("—")
        yt_info.addWidget(self.lbl_yt_active)
        yt_info.addStretch(1)
        vlyt.addLayout(yt_info)

        grid_yt = QtWidgets.QGridLayout()
        self.sb_youtube_default_delay = QtWidgets.QSpinBox(); self.sb_youtube_default_delay.setRange(0, 7 * 24 * 60)
        self.sb_youtube_default_delay.setValue(int(yt_cfg.get("schedule_minutes_from_now", 60)))
        grid_yt.addWidget(QtWidgets.QLabel("Отложить по умолчанию (мин):"), 0, 0)
        grid_yt.addWidget(self.sb_youtube_default_delay, 0, 1)

        self.cb_youtube_default_draft = QtWidgets.QCheckBox("По умолчанию только приватный черновик")
        self.cb_youtube_default_draft.setChecked(bool(yt_cfg.get("draft_only", False)))
        grid_yt.addWidget(self.cb_youtube_default_draft, 1, 0, 1, 2)

        archive_wrap = QtWidgets.QWidget(); archive_l = QtWidgets.QHBoxLayout(archive_wrap); archive_l.setContentsMargins(0,0,0,0)
        self.ed_youtube_archive = QtWidgets.QLineEdit(yt_cfg.get("archive_dir", str(PROJECT_ROOT / "uploaded")))
        self.btn_youtube_archive_browse = QtWidgets.QPushButton("…")
        archive_l.addWidget(self.ed_youtube_archive, 1)
        archive_l.addWidget(self.btn_youtube_archive_browse)
        grid_yt.addWidget(QtWidgets.QLabel("Архив загруженных:"), 2, 0)
        grid_yt.addWidget(archive_wrap, 2, 1)

        vlyt.addLayout(grid_yt)

        s.addRow(grp_yt_cfg)

        # --- Пути проекта (с кнопками выбора)
        row_paths = QtWidgets.QGridLayout()
        r = 0
        self.ed_root = QtWidgets.QLineEdit(self.cfg.get("project_root", str(PROJECT_ROOT)));  self.btn_browse_root = QtWidgets.QPushButton("…")
        row_paths.addWidget(QtWidgets.QLabel("Папка проекта:"), r, 0); row_paths.addWidget(self.ed_root, r, 1); row_paths.addWidget(self.btn_browse_root, r, 2); r += 1

        self.ed_downloads = QtWidgets.QLineEdit(self.cfg.get("downloads_dir", str(DL_DIR))); self.btn_browse_downloads = QtWidgets.QPushButton("…")
        row_paths.addWidget(QtWidgets.QLabel("Папка RAW:"), r, 0); row_paths.addWidget(self.ed_downloads, r, 1); row_paths.addWidget(self.btn_browse_downloads, r, 2); r += 1

        self.ed_blurred = QtWidgets.QLineEdit(self.cfg.get("blurred_dir", str(BLUR_DIR)));   self.btn_browse_blurred = QtWidgets.QPushButton("…")
        row_paths.addWidget(QtWidgets.QLabel("Папка BLURRED:"), r, 0); row_paths.addWidget(self.ed_blurred, r, 1); row_paths.addWidget(self.btn_browse_blurred, r, 2); r += 1

        self.ed_merged = QtWidgets.QLineEdit(self.cfg.get("merged_dir", str(MERG_DIR)));     self.btn_browse_merged = QtWidgets.QPushButton("…")
        row_paths.addWidget(QtWidgets.QLabel("Папка MERGED:"), r, 0); row_paths.addWidget(self.ed_merged, r, 1); row_paths.addWidget(self.btn_browse_merged, r, 2); r += 1

        # источники для BLUR/MERGE
        self.ed_blur_src = QtWidgets.QLineEdit(self.cfg.get("blur_src_dir", self.cfg.get("downloads_dir", str(DL_DIR))))
        self.btn_browse_blur_src = QtWidgets.QPushButton("…")
        row_paths.addWidget(QtWidgets.QLabel("Источник BLUR:"), r, 0); row_paths.addWidget(self.ed_blur_src, r, 1); row_paths.addWidget(self.btn_browse_blur_src, r, 2); r += 1

        self.ed_merge_src = QtWidgets.QLineEdit(self.cfg.get("merge_src_dir", self.cfg.get("blurred_dir", str(BLUR_DIR))))
        self.btn_browse_merge_src = QtWidgets.QPushButton("…")
        row_paths.addWidget(QtWidgets.QLabel("Источник MERGE:"), r, 0); row_paths.addWidget(self.ed_merge_src, r, 1); row_paths.addWidget(self.btn_browse_merge_src, r, 2); r += 1

        s.addRow(row_paths)

        ff = self.cfg.get("ffmpeg", {})
        self.ed_ff_bin = QtWidgets.QLineEdit(ff.get("binary","ffmpeg"))
        self.ed_post = QtWidgets.QLineEdit(ff.get("post_chain","boxblur=1:1,noise=alls=2:allf=t,unsharp=3:3:0.5:3:3:0.0"))
        self.cmb_vcodec = QtWidgets.QComboBox()
        self.cmb_vcodec.addItems(["auto_hw","libx264","copy"])
        self.cmb_vcodec.setCurrentText(ff.get("vcodec","auto_hw"))
        self.ed_crf = QtWidgets.QSpinBox(); self.ed_crf.setRange(0,51); self.ed_crf.setValue(int(ff.get("crf",18)))
        self.cmb_preset = QtWidgets.QComboBox(); self.cmb_preset.addItems(
            ["ultrafast","superfast","veryfast","faster","fast","medium","slow","slower","veryslow","placebo"])
        self.cmb_preset.setCurrentText(ff.get("preset","veryfast"))
        self.cmb_format = QtWidgets.QComboBox(); self.cmb_format.addItems(["mp4","mov","webm"]); self.cmb_format.setCurrentText(ff.get("format","mp4"))
        self.cb_copy_audio = QtWidgets.QCheckBox("Копировать аудио"); self.cb_copy_audio.setChecked(bool(ff.get("copy_audio",True)))
        self.sb_blur_threads = QtWidgets.QSpinBox(); self.sb_blur_threads.setRange(1, 8); self.sb_blur_threads.setValue(int(ff.get("blur_threads",2)))
        s.addRow("ffmpeg:", self.ed_ff_bin)
        s.addRow("POST (цепочка фильтров):", self.ed_post)
        s.addRow("vcodec:", self.cmb_vcodec)
        s.addRow("CRF:", self.ed_crf)
        s.addRow("preset:", self.cmb_preset)
        s.addRow("format:", self.cmb_format)
        s.addRow("Потоки BLUR:", self.sb_blur_threads)
        s.addRow("", self.cb_copy_audio)

        self.cmb_aspect = QtWidgets.QComboBox()
        self.cmb_aspect.addItems(["portrait_9x16", "landscape_16x9"])
        self.cmb_aspect.setCurrentText(ff.get("active_preset","portrait_9x16"))
        s.addRow("Активный пресет:", self.cmb_aspect)

        self.grp_portrait = QtWidgets.QGroupBox("Координаты 9:16 (три зоны delogo)")
        gp = QtWidgets.QGridLayout(self.grp_portrait)
        self.p_edits = self._make_zone_edits(gp)

        self.grp_landscape = QtWidgets.QGroupBox("Координаты 16:9 (три зоны delogo)")
        glp = QtWidgets.QGridLayout(self.grp_landscape)
        self.l_edits = self._make_zone_edits(glp)

        s.addRow(self.grp_portrait)
        s.addRow(self.grp_landscape)

        # --- Автоген: параметры пауз из worker config ---
        grp_auto = QtWidgets.QGroupBox("Автоген — паузы и лимиты (workers/autogen/config.yaml)")
        fa = QtWidgets.QFormLayout(grp_auto)
        self.sb_auto_success_every = QtWidgets.QSpinBox(); self.sb_auto_success_every.setRange(1, 999); self.sb_auto_success_every.setValue(2)
        self.sb_auto_success_pause = QtWidgets.QSpinBox(); self.sb_auto_success_pause.setRange(0, 3600); self.sb_auto_success_pause.setValue(180)
        self.btn_save_autogen_cfg = QtWidgets.QPushButton("Сохранить автоген конфиг")
        fa.addRow("Пауза после каждых N успешных:", self.sb_auto_success_every)
        fa.addRow("Длительность паузы, сек:", self.sb_auto_success_pause)
        fa.addRow(self.btn_save_autogen_cfg)
        s.addRow(grp_auto)

        self.btn_save_settings = QtWidgets.QPushButton("Сохранить настройки")
        s.addRow("", self.btn_save_settings)

        self.tab_settings.setWidget(settings_body)
        self.tabs.addTab(self.tab_settings, "Настройки")

        # TAB: История
        self.tab_history = QtWidgets.QWidget(); h = QtWidgets.QVBoxLayout(self.tab_history)
        self.btn_reload_history = QtWidgets.QPushButton("Обновить")
        self.txt_history = QtWidgets.QPlainTextEdit(); self.txt_history.setReadOnly(True)
        h.addWidget(self.btn_reload_history, 0, QtCore.Qt.AlignmentFlag.AlignLeft)
        h.addWidget(self.txt_history, 1)
        self.tabs.addTab(self.tab_history, "История")

        self._load_zones_into_ui()
        self._toggle_youtube_schedule()

    def _make_zone_edits(self, grid: QtWidgets.QGridLayout):
        edits = []
        headers = ["Зона", "x", "y", "w", "h"]
        for j, name in enumerate(headers):
            lbl = QtWidgets.QLabel(f"<b>{name}</b>")
            grid.addWidget(lbl, 0, j)
        for i in range(3):
            grid.addWidget(QtWidgets.QLabel(f"{i+1}"), i+1, 0)
            x = QtWidgets.QSpinBox(); x.setRange(0, 4000)
            y = QtWidgets.QSpinBox(); y.setRange(0, 4000)
            w = QtWidgets.QSpinBox(); w.setRange(0, 4000)
            h = QtWidgets.QSpinBox(); h.setRange(0, 4000)
            grid.addWidget(x, i+1, 1); grid.addWidget(y, i+1, 2)
            grid.addWidget(w, i+1, 3); grid.addWidget(h, i+1, 4)
            edits.append((x, y, w, h))
        return edits

    def _load_zones_into_ui(self):
        ff = self.cfg.get("ffmpeg", {})
        pr = ff.get("presets", {})
        pz = (pr.get("portrait_9x16") or {}).get("zones", [])
        lz = (pr.get("landscape_16x9") or {}).get("zones", [])
        def fill(edits, zones):
            for i in range(3):
                if i < len(zones):
                    x,y,w,h = zones[i]["x"], zones[i]["y"], zones[i]["w"], zones[i]["h"]
                else:
                    x=y=w=h=0
                edits[i][0].setValue(int(x))
                edits[i][1].setValue(int(y))
                edits[i][2].setValue(int(w))
                edits[i][3].setValue(int(h))
        fill(self.p_edits, pz)
        fill(self.l_edits, lz)

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

        self.btn_load_prompts.clicked.connect(self._load_prompts)
        self.btn_save_prompts.clicked.connect(self._save_prompts)
        self.btn_save_and_run_autogen.clicked.connect(self._save_and_run_autogen)

        self.btn_load_titles.clicked.connect(self._load_titles)
        self.btn_save_titles.clicked.connect(self._save_titles)
        self.btn_reset_titles_cursor.clicked.connect(self._reset_titles_cursor)

        self.btn_apply_dl.clicked.connect(self._apply_dl_limit)
        self.btn_run_scenario.clicked.connect(self._run_scenario)

        self.btn_reload_history.clicked.connect(self._reload_history)
        self.btn_save_settings.clicked.connect(self._save_settings_clicked)
        self.btn_save_autogen_cfg.clicked.connect(self._save_autogen_cfg)

        self.btn_youtube_src_browse.clicked.connect(lambda: self._browse_dir(self.ed_youtube_src, "Выбери папку с клипами"))
        self.cb_youtube_draft_only.toggled.connect(self._toggle_youtube_schedule)
        self.cb_youtube_schedule.toggled.connect(self._toggle_youtube_schedule)
        self.lst_youtube_channels.itemSelectionChanged.connect(self._on_youtube_selected)
        self.btn_yt_add.clicked.connect(self._on_youtube_add_update)
        self.btn_yt_delete.clicked.connect(self._on_youtube_delete)
        self.btn_yt_set_active.clicked.connect(self._on_youtube_set_active)
        self.btn_yt_client_browse.clicked.connect(lambda: self._browse_file(self.ed_yt_client, "client_secret.json", "JSON (*.json);;Все файлы (*.*)"))
        self.btn_yt_credentials_browse.clicked.connect(lambda: self._browse_file(self.ed_yt_credentials, "credentials.json", "JSON (*.json);;Все файлы (*.*)"))
        self.btn_youtube_archive_browse.clicked.connect(lambda: self._browse_dir(self.ed_youtube_archive, "Выбери папку архива"))
        self.cb_youtube_default_draft.toggled.connect(self._sync_draft_checkbox)
        self.sb_youtube_default_delay.valueChanged.connect(self._apply_default_delay)

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
        self.runner_autogen.line.connect(self._slot_log)
        self.runner_dl.line.connect(self._slot_log)
        self.runner_upload.line.connect(self._slot_log)
        self.runner_autogen.finished.connect(self._proc_done)
        self.runner_dl.finished.connect(self._proc_done)
        self.runner_upload.finished.connect(self._proc_done)
        self.runner_autogen.notify.connect(self._notify)
        self.runner_dl.notify.connect(self._notify)
        self.runner_upload.notify.connect(self._notify)
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

    @QtCore.pyqtSlot(str)
    def _slot_log(self, text: str):
        self.txt_logs.appendPlainText(text.rstrip("\n"))
        self.txt_logs.verticalScrollBar().setValue(self.txt_logs.verticalScrollBar().maximum())
        # прогресс по скачиванию
        if "Найдено карточек:" in text or "Собрано ссылок:" in text:
            m = re.search(r"(Найдено карточек|Собрано ссылок):\s*(\d+)", text)
            if m:
                total = int(m.group(2))
                self._post_status("Скачивание запущено…", progress=0, total=total, state="running")
        if "Скачано:" in text:
            fmt = self.pb_global.format()
            try:
                done, total = map(int, fmt.split("/"))
            except Exception:
                done, total = self.pb_global.value(), self.pb_global.maximum()
            done = min(done + 1, total)
            self._post_status("Скачивание…", progress=done, total=total, state="running")

        # лёгкие нотификации по маркерам
        if text.strip() == "[NOTIFY] AUTOGEN_START":
            self._notify("Autogen", "Началась вставка промптов")
        if text.strip() == "[NOTIFY] AUTOGEN_FINISH_OK":
            self._notify("Autogen", "Вставка промптов — успешно")
        if text.strip() == "[NOTIFY] AUTOGEN_FINISH_PARTIAL":
            self._notify("Autogen", "Вставка промптов — частично (были отказы)")
        if text.strip() == "[NOTIFY] DOWNLOAD_START":
            self._notify("Downloader", "Началась автоскачка")
        if text.strip() == "[NOTIFY] DOWNLOAD_FINISH":
            self._notify("Downloader", "Автоскачка завершена")

    # helper для статуса
    def _post_status(self, text: str, progress: int = 0, total: int = 0, state: str = "idle"):
        self.sig_set_status.emit(text, progress, total, state)

    # ----- обработчик завершения подпроцессов -----
    @QtCore.pyqtSlot(int, str)
    def _proc_done(self, rc: int, tag: str):
        if tag == "AUTOGEN":
            msg = "Вставка промптов завершена" + (" ✓" if rc == 0 else " ✗")
            self._post_status(msg, state=("ok" if rc == 0 else "error"))
            append_history(self.cfg, {"event": "autogen_finish", "rc": rc})
            if rc == 0:
                send_tg(self.cfg, "AUTOGEN: ok")
        elif tag == "DL":
            msg = "Скачка завершена" + (" ✓" if rc == 0 else " ✗")
            self._post_status(msg, state=("ok" if rc == 0 else "error"))
            append_history(self.cfg, {"event": "download_finish", "rc": rc})
            if rc == 0:
                send_tg(self.cfg, "DOWNLOAD: ok")
        elif tag == "YT":
            msg = "YouTube загрузка завершена" + (" ✓" if rc == 0 else " ✗")
            self._post_status(msg, state=("ok" if rc == 0 else "error"))
            append_history(self.cfg, {"event": "youtube_finish", "rc": rc})
            if rc == 0:
                send_tg(self.cfg, "YOUTUBE: ok")
        self._refresh_stats()

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
    def _prompts_path(self)->Path:
        return WORKERS_DIR / "autogen" / "prompts.txt"

    def _load_prompts(self):
        p=self._prompts_path()
        txt = p.read_text(encoding="utf-8") if p.exists() else ""
        self.ed_prompts.setPlainText(txt)
        self._post_status(f"Промпты загружены ({p})", state="idle")

    def _save_prompts(self):
        p=self._prompts_path(); p.parent.mkdir(parents=True,exist_ok=True)
        p.write_text(self.ed_prompts.toPlainText(), encoding="utf-8")
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
        return Path(self.cfg.get("titles_file", str(TITLES_FILE)))

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
        n = self.sb_max_videos.value()
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
        if not steps:
            self._post_status("Ничего не выбрано", state="error"); return

        self._post_status("Запуск сценария…", state="running")
        append_history(self.cfg, {"event":"scenario_start","steps":steps})

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
            self._post_status("Сценарий завершён", state=("ok" if ok_all else "error"))
            append_history(self.cfg, {"event":"scenario_finish","ok":ok_all})
            self._refresh_stats()

        threading.Thread(target=flow, daemon=True).start()

    # ----- run steps -----
    def _run_autogen(self):
        self._run_autogen_sync()

    def _run_autogen_sync(self) -> bool:
        workdir=self.cfg.get("autogen",{}).get("workdir", str(WORKERS_DIR / "autogen"))
        entry=self.cfg.get("autogen",{}).get("entry","main.py")
        python=sys.executable; cmd=[python, entry]; env=os.environ.copy(); env["PYTHONUNBUFFERED"]="1"
        env["SORA_PROMPTS_FILE"]=str(self._prompts_path())  # FIX: синхронный запуск тоже
        done = threading.Event(); rc_holder = {"rc":1}
        def on_finish(rc, tag):
            if tag=="AUTOGEN": rc_holder["rc"]=rc; done.set()
        self.runner_autogen.finished.connect(on_finish)
        self.runner_autogen.run(cmd, cwd=workdir, env=env)
        self._post_status("Вставка промптов…", state="running")
        done.wait()
        self.runner_autogen.finished.disconnect(on_finish)
        return rc_holder["rc"] == 0

    def _run_download(self):
        self._run_download_sync()

    def _run_download_sync(self) -> bool:
        workdir=self.cfg.get("downloader",{}).get("workdir", str(WORKERS_DIR / "downloader"))
        entry=self.cfg.get("downloader",{}).get("entry","download_all.py")
        python=sys.executable; cmd=[python, entry]; env=os.environ.copy(); env["PYTHONUNBUFFERED"]="1"
        env["DOWNLOAD_DIR"] = self.cfg.get("downloads_dir", str(DL_DIR))
        env["TITLES_FILE"] = str(self._titles_path())
        env["TITLES_CURSOR_FILE"] = str(self._cursor_path())
        max_v = int(self.sb_max_videos.value())
        env["MAX_VIDEOS"] = str(max_v if max_v>0 else 0)
        done = threading.Event(); rc_holder = {"rc":1}
        def on_finish(rc, tag):
            if tag=="DL": rc_holder["rc"]=rc; done.set()
        self.runner_dl.finished.connect(on_finish)
        self.runner_dl.run(cmd, cwd=workdir, env=env)
        self._post_status("Скачивание…", state="running")
        done.wait()
        self.runner_dl.finished.disconnect(on_finish)
        return rc_holder["rc"] == 0

    # ----- BLUR -----
    def _run_blur_presets_sync(self) -> bool:
        self._save_settings_clicked(silent=True)

        ff = self.cfg.get("ffmpeg", {})
        ffbin = self.ed_ff_bin.text().strip() or "ffmpeg"
        post = self.ed_post.text().strip()
        vcodec = self.cmb_vcodec.currentText().strip()
        crf = str(self.ed_crf.value())
        preset = self.cmb_preset.currentText()
        fmt = self.cmb_format.currentText()
        copy_audio = self.cb_copy_audio.isChecked()
        threads = int(self.sb_blur_threads.value())

        active = self.cmb_aspect.currentText().strip()
        presets = (self.cfg.get("ffmpeg", {}).get("presets") or {})
        zones = (presets.get(active) or {}).get("zones") or []
        if len(zones) != 3:
            self._post_status("Нужно три зоны delogo в пресете", state="error")
            return False

        # источник для BLUR
        src_dir = Path(self.cfg.get("blur_src_dir", self.cfg.get("downloads_dir", str(DL_DIR))))
        if not src_dir.exists():
            self._post_status(f"Источник BLUR не найден: {src_dir}", state="error")
            return False

        dst_dir = Path(self.cfg.get("blurred_dir", str(BLUR_DIR)))
        dst_dir.mkdir(parents=True, exist_ok=True)

        videos = [*src_dir.glob("*.mp4"), *src_dir.glob("*.mov"), *src_dir.glob("*.m4v"), *src_dir.glob("*.webm")]
        total = len(videos)
        if not total:
            self._post_status("Нет видео для блюра", state="error")
            return False

        self._post_status(f"Блюр по пресету {active} ({total} видео)…", progress=0, total=total, state="running")
        counter = {"done": 0}
        lock = Lock()

        def blur_one(v: Path) -> bool:
            out = dst_dir / v.name
            delogos = ",".join([f"delogo=x={z['x']}:y={z['y']}:w={z['w']}:h={z['h']}:show=0" for z in zones])
            vf = delogos + (f",{post}" if post else "") + ",format=yuv420p"

            # Нельзя -c:v copy при фильтрах. Пробуем HW (mac) -> SW.
            def _cmd_hw():
                c = [ffbin, "-hide_banner", "-loglevel", "verbose", "-y"]
                if sys.platform == "darwin":
                    c += ["-hwaccel", "videotoolbox"]
                c += ["-i", str(v), "-vf", vf, "-map", "0:v", "-map", "0:a?"]
                if sys.platform == "darwin":
                    c += ["-c:v", "h264_videotoolbox", "-b:v", "0", "-crf", crf]
                else:
                    c += ["-c:v", "libx264", "-crf", crf, "-preset", preset]
                if copy_audio:
                    c += ["-c:a", "copy"]
                else:
                    c += ["-c:a", "aac", "-b:a", "192k"]
                if fmt.lower() == "mp4":
                    c += ["-movflags", "+faststart"]
                c += [str(out)]
                return c

            def _cmd_sw():
                c = [ffbin, "-hide_banner", "-loglevel", "verbose", "-y",
                     "-i", str(v), "-vf", vf, "-map", "0:v", "-map", "0:a?",
                     "-c:v", "libx264", "-crf", crf, "-preset", preset]
                if copy_audio:
                    c += ["-c:a", "copy"]
                else:
                    c += ["-c:a", "aac", "-b:a", "192k"]
                if fmt.lower() == "mp4":
                    c += ["-movflags", "+faststart"]
                c += [str(out)]
                return c

            tried = []
            if vcodec == "auto_hw" and sys.platform == "darwin":
                cmd = _cmd_hw(); tried.append("HW")
                rc = _run_ffmpeg(cmd, log_prefix=f"BLUR:{v.name}")
                if rc != 0:
                    self.sig_log.emit(f"[BLUR] HW кодек не сработал для {v.name}, пробуем libx264…")
                    cmd = _cmd_sw(); tried.append("SW")
                    rc = _run_ffmpeg(cmd, log_prefix=f"BLUR:{v.name}")
            else:
                cmd = _cmd_sw(); tried.append("SW")
                rc = _run_ffmpeg(cmd, log_prefix=f"BLUR:{v.name}")

            ok = (rc == 0)
            with lock:
                counter["done"] += 1
                self._post_status("Блюр…", progress=counter["done"], total=total, state="running")
                self.sig_log.emit(f"[BLUR] {'OK' if ok else 'FAIL'} ({'→'.join(tried)}): {v.name}")
            return ok

        with ThreadPoolExecutor(max_workers=max(1, threads)) as ex:
            results = list(ex.map(blur_one, videos))

        ok_all = all(results)
        append_history(self.cfg, {"event":"blur_finish","ok":ok_all,"count":total,"preset":active,"src":str(src_dir)})
        send_tg(self.cfg, f"BLUR: завершено (ok={ok_all}, {total} файлов, пресет={active})")
        if ok_all:
            self._post_status("Блюр завершён", state="ok")
        else:
            self._post_status("Блюр завершён с ошибками", state="error")
        return ok_all

    # ----- MERGE -----
    def _run_merge_sync(self) -> bool:
        merge_cfg = self.cfg.get("merge", {}) or {}
        group = int(self.sb_merge_group.value() or merge_cfg.get("group_size", 3))
        pattern = merge_cfg.get("pattern", "*.mp4")
        ff = self.ed_ff_bin.text().strip() or "ffmpeg"

        # источник для MERGE
        src_dir = Path(self.cfg.get("merge_src_dir", self.cfg.get("blurred_dir", str(BLUR_DIR))))
        if not src_dir.exists():
            self._post_status(f"Источник MERGE не найден: {src_dir}", state="error")
            return False

        out_dir = Path(self.cfg.get("merged_dir", str(MERG_DIR)))
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
        send_tg(self.cfg, f"MERGE: завершено (ok={ok_all}, groups={total}, by={group})")

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

        src_dir = Path(self.ed_youtube_src.text().strip() or yt_cfg.get("upload_src_dir", self.cfg.get("merged_dir", str(MERG_DIR))))
        if not src_dir.exists():
            self._post_status(f"Папка для загрузки не найдена: {src_dir}", state="error")
            return False

        videos = [*src_dir.glob("*.mp4"), *src_dir.glob("*.mov"), *src_dir.glob("*.m4v"), *src_dir.glob("*.webm")]
        if not videos:
            self._post_status("Нет файлов для загрузки", state="error")
            return False

        publish_at = ""
        if self.cb_youtube_schedule.isChecked() and not self.cb_youtube_draft_only.isChecked():
            publish_at = self.dt_youtube_publish.dateTime().toString(QtCore.Qt.DateFormat.ISODate)

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
        env["YOUTUBE_ARCHIVE_DIR"] = yt_cfg.get("archive_dir", str(PROJECT_ROOT / "uploaded"))
        if publish_at:
            env["YOUTUBE_PUBLISH_AT"] = publish_at

        done = threading.Event(); rc_holder = {"rc": 1}

        def on_finish(rc, tag):
            if tag == "YT":
                rc_holder["rc"] = rc
                done.set()

        self.runner_upload.finished.connect(on_finish)
        self.runner_upload.run(cmd, cwd=workdir, env=env)
        self._post_status("Загрузка на YouTube…", state="running")
        done.wait()
        self.runner_upload.finished.disconnect(on_finish)
        return rc_holder["rc"] == 0


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

    def _iter_videos(self, folder: Path):
        return sorted(
            [*folder.glob("*.mp4"), *folder.glob("*.mov"), *folder.glob("*.m4v"), *folder.glob("*.webm")],
            key=self._natural_key
        )

    def _ren_run(self):
        folder = Path(self.ed_ren_dir.text().strip() or self.cfg.get("downloads_dir", str(DL_DIR)))
        if not folder.exists():
            self._post_status("Папка не найдена", state="error"); return
        files = self._iter_videos(folder)
        if not files:
            self._post_status("В папке нет видео", state="error"); return

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
        send_tg(self.cfg, f"RENAME: {done}/{total} в {folder.name}")
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
        hist = Path(self.cfg.get("history_file", str(HIST_FILE)))
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
    def _save_settings_clicked(self, silent: bool=False):
        self.cfg.setdefault("chrome", {})
        self.cfg["chrome"]["cdp_port"] = int(self.ed_cdp_port.text() or "9222")
        self.cfg["chrome"]["user_data_dir"] = self.ed_userdir.text().strip()
        self.cfg["chrome"]["binary"] = self.ed_chrome_bin.text().strip()
        save_cfg(self.cfg)

        self.cfg["project_root"] = self.ed_root.text().strip() or str(PROJECT_ROOT)
        self.cfg["downloads_dir"] = self.ed_downloads.text().strip() or str(DL_DIR)
        self.cfg["blurred_dir"] = self.ed_blurred.text().strip() or str(BLUR_DIR)
        self.cfg["merged_dir"] = self.ed_merged.text().strip() or str(MERG_DIR)

        # источники
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
        ff["active_preset"] = self.cmb_aspect.currentText().strip()
        ff["blur_threads"] = int(self.sb_blur_threads.value())

        presets = ff.setdefault("presets", {})
        for key, edits in [("portrait_9x16", self.p_edits), ("landscape_16x9", self.l_edits)]:
            zones = []
            for i in range(3):
                zones.append({
                    "x": edits[i][0].value(),
                    "y": edits[i][1].value(),
                    "w": edits[i][2].value(),
                    "h": edits[i][3].value(),
                })
            presets.setdefault(key, {})["zones"] = zones

        self.cfg.setdefault("merge", {})["group_size"] = int(self.sb_merge_group.value())

        yt_cfg = self.cfg.setdefault("youtube", {})
        yt_cfg["upload_src_dir"] = self.ed_youtube_src.text().strip() or self.cfg.get("merged_dir", str(MERG_DIR))
        yt_cfg["schedule_minutes_from_now"] = int(self.sb_youtube_default_delay.value())
        yt_cfg["draft_only"] = bool(self.cb_youtube_default_draft.isChecked())
        yt_cfg["archive_dir"] = self.ed_youtube_archive.text().strip() or yt_cfg.get("archive_dir", str(PROJECT_ROOT / "uploaded"))

        save_cfg(self.cfg)
        ensure_dirs(self.cfg)
        if not silent:
            self._post_status("Настройки сохранены", state="ok")

    # ----- автоген конфиг -----
    def _load_autogen_cfg_ui(self):
        path = Path(self.cfg.get("autogen",{}).get("config_path",""))
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
        path = Path(self.cfg.get("autogen",{}).get("config_path",""))
        if not path:
            self._post_status("Не задан путь к autogen/config.yaml", state="error"); return
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

            raw  = _count_vids(Path(self.cfg.get("downloads_dir", str(DL_DIR))))
            blur = _count_vids(Path(self.cfg.get("blurred_dir", str(BLUR_DIR))))
            merg = _count_vids(Path(self.cfg.get("merged_dir", str(MERG_DIR))))
            upload_src = _count_vids(Path(self.cfg.get("youtube", {}).get("upload_src_dir", self.cfg.get("merged_dir", str(MERG_DIR)))))
            self.sig_log.emit(f"[STAT] RAW={raw} BLURRED={blur} MERGED={merg} YT={upload_src}")

            # обновляем визуальные счетчики
            self.lbl_stat_raw.setText(str(raw))
            self.lbl_stat_blur.setText(str(blur))
            self.lbl_stat_merge.setText(str(merg))
            self.lbl_stat_upload.setText(str(upload_src))
        except Exception as e:
            self.sig_log.emit(f"[STAT] ошибка: {e}")

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

    def _on_profile_scan(self):
        # macOS: авто-поиск
        base = os.path.expanduser("~/Library/Application Support/Google/Chrome")
        found = []
        try:
            if os.path.isdir(base):
                candidates = ["Default"] + [d for d in os.listdir(base) if d.startswith("Profile ")]
                for d in candidates:
                    p = os.path.join(base, d)
                    if os.path.isdir(p):
                        found.append({"name": d, "user_data_dir": base, "profile_directory": d})
        except Exception:
            pass

        if not found:
            self._post_status("Профили не найдены. Проверь путь.", state="error")
            return

        ch = self.cfg.setdefault("chrome", {})
        names_existing = {p.get("name") for p in ch.setdefault("profiles", [])}
        for p in found:
            if p["name"] not in names_existing:
                ch["profiles"].append(p)

        if not ch.get("active_profile") and ch["profiles"]:
            ch["active_profile"] = ch["profiles"][0]["name"]

        save_cfg(self.cfg)
        self._refresh_profiles_ui()
        self._post_status(f"Найдено профилей: {len(found)}", state="ok")


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
        self.ed_youtube_src.setText(upload_src)
        self.ed_youtube_archive.setText(yt.get("archive_dir", str(PROJECT_ROOT / "uploaded")))

        minutes = int(yt.get("schedule_minutes_from_now", 60) or 0)
        self.sb_youtube_default_delay.setValue(minutes)
        self.dt_youtube_publish.setDateTime(QtCore.QDateTime.currentDateTime().addSecs(minutes * 60))

        self.cb_youtube_default_draft.setChecked(bool(yt.get("draft_only", False)))
        self._sync_draft_checkbox()
        self.cb_youtube_schedule.blockSignals(True)
        self.cb_youtube_schedule.setChecked(not self.cb_youtube_default_draft.isChecked())
        self.cb_youtube_schedule.blockSignals(False)
        self._toggle_youtube_schedule()

        if idx >= 0:
            self._on_youtube_selected()
        else:
            self.ed_yt_name.clear()
            self.ed_yt_client.clear()
            self.ed_yt_credentials.clear()
            self.cmb_yt_privacy.setCurrentText("private")

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


# ---------- main ----------
def main():
    app = QtWidgets.QApplication(sys.argv)
    font=QtGui.QFont("Menlo" if sys.platform=="darwin" else "Consolas",11); app.setFont(font)
    w = MainWindow(); w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
