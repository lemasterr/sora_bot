#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Загрузка видео в TikTok через официальное Content Posting API.

Скрипт запускается из GUI Sora Suite и читает параметры из app_config.yaml
и переменных окружения:
- APP_CONFIG_PATH — путь к app_config.yaml (по умолчанию sora_suite/app_config.yaml);
- TIKTOK_PROFILE_NAME — имя профиля TikTok в конфиге;
- TIKTOK_SRC_DIR — папка с роликами для загрузки;
- TIKTOK_ARCHIVE_DIR — куда переносить успешно загруженные ролики и метаданные;
- TIKTOK_BATCH_LIMIT — максимум роликов за один запуск (0 = все);
- TIKTOK_BATCH_STEP_MINUTES — интервал в минутах между публикациями;
- TIKTOK_DRAFT_ONLY — «1», если нужно оставить ролики приватными;
- TIKTOK_PUBLISH_AT — стартовое время публикации в ISO (UTC), опционально;
- TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET / TIKTOK_REFRESH_TOKEN / TIKTOK_OPEN_ID —
  необязательные переопределения ключей из профиля.
"""
from __future__ import annotations

import datetime as dt
import json
import mimetypes
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
import yaml

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo  # type: ignore

API_BASE = "https://open.tiktokapis.com"
DEFAULT_TIMEOUT = 120


def log(msg: str) -> None:
    print(f"[TT] {msg}", flush=True)


def err(msg: str) -> None:
    print(f"[TT][ERR] {msg}", file=sys.stderr, flush=True)


def resolve_path(base: Path, raw: Optional[str]) -> Optional[Path]:
    if not raw:
        return None
    path = Path(os.path.expandvars(raw)).expanduser()
    if not path.is_absolute():
        path = (base / path).resolve()
    return path


def load_app_config(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"app_config.yaml не найден: {path}")
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def save_app_config(path: Path, data: Dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False)


def collect_videos(src: Path) -> Tuple[Path, ...]:
    patterns = ("*.mp4", "*.mov", "*.m4v", "*.webm")
    files: List[Path] = []
    for pattern in patterns:
        files.extend(src.glob(pattern))
    files.sort(key=lambda p: p.stat().st_mtime)
    return tuple(files)


def load_metadata(video: Path) -> Dict[str, Any]:
    for ext in (".json", ".yaml", ".yml"):
        candidate = video.with_suffix(ext)
        if not candidate.exists():
            continue
        try:
            if candidate.suffix.lower() == ".json":
                return json.loads(candidate.read_text(encoding="utf-8"))
            return yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
        except Exception as exc:  # pragma: no cover - логируем и продолжаем
            err(f"Не удалось прочитать {candidate}: {exc}")
    return {}


def build_caption(profile_cfg: Dict[str, Any], meta: Dict[str, Any], fallback_title: str) -> str:
    title = meta.get("title") or fallback_title
    hashtags_meta = meta.get("hashtags") or meta.get("tags") or ""
    if isinstance(hashtags_meta, (list, tuple)):
        hashtags_meta = " ".join(str(x) for x in hashtags_meta)
    default_hashtags = profile_cfg.get("default_hashtags", "")
    hashtags = " ".join(filter(None, [hashtags_meta, default_hashtags])).strip()
    template = profile_cfg.get("caption_template") or "{title}\n{hashtags}"
    description = meta.get("description", "")
    try:
        caption = template.format(title=title, hashtags=hashtags, description=description)
    except Exception:
        caption = f"{title}\n{hashtags}".strip()
    return caption.strip()[:2200]


def move_to_archive(video: Path, archive_dir: Path) -> List[Path]:
    archive_dir.mkdir(parents=True, exist_ok=True)
    moved: List[Path] = []
    target = archive_dir / video.name
    idx = 1
    while target.exists():
        target = archive_dir / f"{video.stem}_{idx}{video.suffix}"
        idx += 1
    shutil.move(str(video), str(target))
    moved.append(target)
    for ext in (".json", ".yaml", ".yml"):
        meta = video.with_suffix(ext)
        if not meta.exists():
            continue
        dst = archive_dir / meta.name
        j = 1
        while dst.exists():
            dst = archive_dir / f"{meta.stem}_{j}{meta.suffix}"
            j += 1
        shutil.move(str(meta), str(dst))
        moved.append(dst)
    return moved


def parse_schedule(start_iso: Optional[str], profile_cfg: Dict[str, Any], index: int, step_minutes: int) -> Optional[dt.datetime]:
    base = None
    if start_iso:
        try:
            base = dt.datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
        except ValueError:
            err(f"Неверный формат времени: {start_iso}")
    if base is None:
        offset = int(profile_cfg.get("schedule_offset_minutes", 0))
        if offset <= 0:
            return None
        base = dt.datetime.utcnow() + dt.timedelta(minutes=offset)
    tz_name = profile_cfg.get("timezone") or "UTC"
    try:
        zone = ZoneInfo(tz_name)
    except Exception:
        zone = ZoneInfo("UTC")
    scheduled = base + dt.timedelta(minutes=max(0, step_minutes) * index)
    return scheduled.astimezone(dt.timezone.utc)


def read_credentials_file(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Файл секретов TikTok не найден: {path}")
    text = path.read_text(encoding="utf-8")
    try:
        if path.suffix.lower() in {".yml", ".yaml"}:
            return yaml.safe_load(text) or {}
        return json.loads(text)
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"Не удалось распарсить {path}: {exc}") from exc


def merge_credentials(*sources: Iterable[Tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for source in sources:
        for key, value in source:
            if value:
                result[key] = value
    return result


def load_credentials(profile: Dict[str, Any], base_dir: Path) -> Dict[str, Any]:
    file_data: Dict[str, Any] = {}
    creds_path = resolve_path(base_dir, profile.get("credentials_file"))
    if creds_path:
        try:
            file_data = read_credentials_file(creds_path)
        except Exception as exc:
            err(str(exc))
    env_data = {
        "client_key": os.getenv("TIKTOK_CLIENT_KEY"),
        "client_secret": os.getenv("TIKTOK_CLIENT_SECRET"),
        "refresh_token": os.getenv("TIKTOK_REFRESH_TOKEN"),
        "open_id": os.getenv("TIKTOK_OPEN_ID"),
        "access_token": os.getenv("TIKTOK_ACCESS_TOKEN"),
        "access_token_expires_at": os.getenv("TIKTOK_ACCESS_TOKEN_EXPIRES"),
    }
    profile_data = {
        key: profile.get(key)
        for key in (
            "client_key",
            "client_secret",
            "refresh_token",
            "open_id",
            "access_token",
            "access_token_expires_at",
        )
    }
    return merge_credentials(profile_data.items(), file_data.items(), env_data.items())


def ensure_access_token(
    creds: Dict[str, Any],
    profile: Dict[str, Any],
    cfg: Dict[str, Any],
    cfg_path: Path,
) -> Tuple[str, Dict[str, Any]]:
    now = dt.datetime.now(dt.timezone.utc)
    token = creds.get("access_token")
    expires_at_str = creds.get("access_token_expires_at")
    expires_at = None
    if expires_at_str:
        try:
            expires_at = dt.datetime.fromisoformat(str(expires_at_str)).astimezone(dt.timezone.utc)
        except ValueError:
            expires_at = None
    if token and expires_at and expires_at - now > dt.timedelta(minutes=5):
        return token, {}

    client_key = creds.get("client_key")
    client_secret = creds.get("client_secret")
    refresh_token = creds.get("refresh_token")
    if not all([client_key, client_secret, refresh_token]):
        raise RuntimeError("Отсутствуют client_key/client_secret/refresh_token для TikTok профиля")

    log("Обновляем access_token через TikTok API…")
    response = requests.post(
        f"{API_BASE}/v2/oauth/token/",
        data={
            "client_key": client_key,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=DEFAULT_TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(f"TikTok token refresh вернул {response.status_code}: {response.text}")
    payload = response.json()
    data = payload.get("data") or {}
    if not data.get("access_token"):
        raise RuntimeError(f"TikTok token refresh без access_token: {payload}")

    token = data["access_token"]
    expires_in = int(data.get("expires_in", 0))
    expires_at = now + dt.timedelta(seconds=max(0, expires_in))
    new_refresh = data.get("refresh_token") or refresh_token

    updates = {
        "client_key": client_key,
        "client_secret": client_secret,
        "refresh_token": new_refresh,
        "access_token": token,
        "access_token_expires_at": expires_at.isoformat(),
    }

    profile.update(updates)
    tk_cfg = cfg.setdefault("tiktok", {})
    for prof in tk_cfg.get("profiles", []) or []:
        if prof.get("name") == profile.get("name"):
            prof.update(updates)
            break
    save_app_config(cfg_path, cfg)

    creds_path = resolve_path(cfg_path.parent, profile.get("credentials_file"))
    if creds_path and creds_path.exists():
        try:
            data_file = read_credentials_file(creds_path)
        except Exception as exc:  # pragma: no cover - уже залогировали в read_credentials_file
            err(str(exc))
        else:
            data_file.update({k: v for k, v in updates.items() if k in data_file or k in {"access_token", "refresh_token", "access_token_expires_at"}})
            creds_path.write_text(json.dumps(data_file, ensure_ascii=False, indent=2), encoding="utf-8")
    return token, updates


def init_upload(session: requests.Session, file_size: int) -> Tuple[str, str]:
    resp = session.post(
        f"{API_BASE}/v2/post/upload/",
        json={"source_info": {"source": "FILE", "video_size": file_size}},
        timeout=DEFAULT_TIMEOUT,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"TikTok upload init {resp.status_code}: {resp.text}")
    payload = resp.json()
    data = payload.get("data") or {}
    upload_url = data.get("upload_url")
    upload_id = data.get("upload_id")
    if not upload_url or not upload_id:
        raise RuntimeError(f"Пустой ответ upload init: {payload}")
    return upload_id, upload_url


def put_video(upload_url: str, video: Path) -> None:
    mime = mimetypes.guess_type(video.name)[0] or "video/mp4"
    with video.open("rb") as fh:
        resp = requests.put(upload_url, data=fh, headers={"Content-Type": mime}, timeout=DEFAULT_TIMEOUT)
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Не удалось загрузить {video.name}: {resp.status_code} {resp.text}")


def publish_video(
    session: requests.Session,
    open_id: str,
    upload_id: str,
    caption: str,
    schedule: Optional[dt.datetime],
    draft_only: bool,
) -> Dict[str, Any]:
    privacy = "PRIVATE" if draft_only else "PUBLIC"
    post_info: Dict[str, Any] = {
        "title": caption[:150] or "Video",
        "description": caption,
        "privacy_level": privacy,
    }
    if schedule is not None:
        post_info["schedule_time"] = int(schedule.timestamp())
    resp = session.post(
        f"{API_BASE}/v2/post/publish/content/",
        json={
            "open_id": open_id,
            "upload_id": upload_id,
            "post_info": post_info,
        },
        timeout=DEFAULT_TIMEOUT,
    )
    payload = resp.json()
    if resp.status_code != 200:
        raise RuntimeError(f"TikTok publish {resp.status_code}: {payload}")
    if not payload.get("data"):
        raise RuntimeError(f"TikTok publish без data: {payload}")
    return payload["data"]


def main() -> int:
    base_dir = Path(os.environ.get("APP_CONFIG_PATH", "sora_suite/app_config.yaml")).resolve()
    cfg = load_app_config(base_dir)
    tk_cfg = cfg.get("tiktok", {}) or {}
    profile_name = os.environ.get("TIKTOK_PROFILE_NAME", tk_cfg.get("active_profile", ""))
    if not profile_name:
        err("Не выбран профиль TikTok (TIKTOK_PROFILE_NAME)")
        return 1

    profiles = [p for p in tk_cfg.get("profiles") or [] if isinstance(p, dict)]
    profile = next((p for p in profiles if p.get("name") == profile_name), None)
    if not profile:
        err(f"Профиль TikTok '{profile_name}' не найден")
        return 1

    src_dir = Path(os.environ.get("TIKTOK_SRC_DIR", tk_cfg.get("upload_src_dir", "")) or "").expanduser()
    if not src_dir.is_absolute():
        src_dir = (base_dir.parent / src_dir).resolve()
    if not src_dir.exists():
        err(f"Папка с видео не найдена: {src_dir}")
        return 1

    archive_dir = Path(os.environ.get("TIKTOK_ARCHIVE_DIR", tk_cfg.get("archive_dir", "uploaded_tiktok")))
    if not archive_dir.is_absolute():
        archive_dir = (base_dir.parent / archive_dir).resolve()

    batch_limit = max(0, int(os.environ.get("TIKTOK_BATCH_LIMIT", "0") or 0))
    step_minutes = max(0, int(os.environ.get("TIKTOK_BATCH_STEP_MINUTES", tk_cfg.get("batch_step_minutes", 60)) or 0))
    draft_only = os.environ.get("TIKTOK_DRAFT_ONLY", "0") == "1"
    publish_at_iso = os.environ.get("TIKTOK_PUBLISH_AT")

    videos = collect_videos(src_dir)
    if not videos:
        err("Нет файлов для загрузки")
        return 1
    if batch_limit:
        videos = videos[:batch_limit]

    creds = load_credentials(profile, base_dir.parent)
    open_id = creds.get("open_id")
    if not open_id:
        err("Для TikTok профиля не указан open_id")
        return 1

    try:
        access_token, _ = ensure_access_token(creds, profile, cfg, base_dir)
    except Exception as exc:
        err(f"Не удалось получить access_token: {exc}")
        return 1

    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {access_token}"})

    success = 0
    errors = 0

    for idx, video in enumerate(videos):
        meta = load_metadata(video)
        caption = build_caption(profile, meta, fallback_title=video.stem)
        schedule_dt = None
        if not draft_only:
            schedule_dt = parse_schedule(publish_at_iso, profile, idx, step_minutes)
        schedule_text = schedule_dt.isoformat() if schedule_dt else ("draft" if draft_only else "immediate")
        log(f"Загружаем {video.name} (schedule={schedule_text})")
        try:
            upload_id, upload_url = init_upload(session, video.stat().st_size)
            put_video(upload_url, video)
            publish_video(session, open_id, upload_id, caption, schedule_dt, draft_only)
        except Exception as exc:
            errors += 1
            err(f"{video.name}: {exc}")
            continue

        moved = move_to_archive(video, archive_dir)
        success += 1
        log(f"Готово {video.name} → {', '.join(str(p.name) for p in moved)}")

    log(f"Итого: успешно {success}, ошибок {errors}")
    session.close()
    return 0 if success and errors == 0 else (1 if success == 0 else 0)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        log("Прервано пользователем")
        raise SystemExit(1)
