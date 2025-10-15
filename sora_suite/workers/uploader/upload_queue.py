#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Загрузка видео на YouTube с отложенным постингом.

Скрипт ожидает окружение от GUI Sora Suite:
- APP_CONFIG_PATH — путь к app_config.yaml;
- YOUTUBE_CHANNEL_NAME — выбранный канал;
- YOUTUBE_SRC_DIR — папка с клипами для загрузки;
- YOUTUBE_PUBLISH_AT — опциональная дата/время (ISO) публикации;
- YOUTUBE_DRAFT_ONLY — "1" чтобы оставить как приват без расписания;
- YOUTUBE_ARCHIVE_DIR — куда перемещать успешно загруженные файлы.
- YOUTUBE_BATCH_STEP_MINUTES — шаг между роликами при пакетном расписании;
- YOUTUBE_BATCH_LIMIT — сколько роликов брать за один запуск (0 = все).
"""
from __future__ import annotations

import json
import os
import sys
import time
import shutil
import datetime as dt
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import yaml
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def log(msg: str):
    print(f"[YT] {msg}", flush=True)


def err(msg: str):
    print(f"[YT][ERR] {msg}", file=sys.stderr, flush=True)


def resolve_path(base: Path, path: Optional[str]) -> Optional[Path]:
    if not path:
        return None
    p = Path(path)
    if not p.is_absolute():
        p = (base / p).resolve()
    return p


def load_app_config(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"app_config.yaml не найден: {path}")
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def ensure_credentials(channel_cfg: Dict[str, Any], config_dir: Path) -> Credentials:
    client_path = resolve_path(config_dir, channel_cfg.get("client_secret"))
    if not client_path or not client_path.exists():
        raise FileNotFoundError("client_secret.json не найден — укажите путь в настройках YouTube")

    cred_path = resolve_path(config_dir, channel_cfg.get("credentials"))
    if not cred_path:
        cred_path = client_path.with_name(f"{channel_cfg.get('name', 'channel')}_credentials.json")

    creds: Optional[Credentials] = None
    if cred_path.exists():
        creds = Credentials.from_authorized_user_file(str(cred_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            log("Обновили refresh_token")
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(client_path), SCOPES)
            if hasattr(flow, "run_local_server"):
                log("Запускаем локальное окно авторизации Google")
                try:
                    creds = flow.run_local_server(port=0, prompt="consent")
                except TypeError:
                    # для старых версий без параметра prompt
                    creds = flow.run_local_server(port=0)
            else:
                log("run_local_server недоступен, откроется консольный ввод кода")
                creds = flow.run_console()
        cred_path.parent.mkdir(parents=True, exist_ok=True)
        with cred_path.open("w", encoding="utf-8") as fh:
            fh.write(creds.to_json())
            log(f"Токен сохранён: {cred_path}")
    return creds


def collect_videos(src: Path) -> Tuple[Path, ...]:
    patterns = ["*.mp4", "*.mov", "*.m4v", "*.webm"]
    files = []
    for pat in patterns:
        files.extend(src.glob(pat))
    return tuple(sorted(files, key=lambda p: p.stat().st_mtime))


def load_metadata(video: Path) -> Dict[str, Any]:
    candidates = [video.with_suffix(ext) for ext in (".json", ".yaml", ".yml")]
    for meta_path in candidates:
        if meta_path.exists():
            try:
                if meta_path.suffix.lower() == ".json":
                    return json.loads(meta_path.read_text(encoding="utf-8"))
                return yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
            except Exception as exc:
                err(f"Не удалось прочитать метаданные {meta_path}: {exc}")
    return {}


def to_rfc3339(iso_value: Optional[str]) -> Optional[str]:
    if not iso_value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(iso_value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.datetime.now().astimezone().tzinfo or dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    except ValueError:
        err(f"Неверный формат publish_at: {iso_value}")
        return None


def move_to_archive(video: Path, archive: Path) -> Tuple[Path, ...]:
    archive.mkdir(parents=True, exist_ok=True)
    target = archive / video.name
    i = 1
    while target.exists():
        target = archive / f"{video.stem}_{i}{video.suffix}"
        i += 1
    shutil.move(str(video), str(target))

    moved: Tuple[Path, ...] = (target,)

    for ext in (".json", ".yaml", ".yml"):
        meta = video.with_suffix(ext)
        if meta.exists():
            target_meta = archive / meta.name
            j = 1
            while target_meta.exists():
                target_meta = archive / f"{meta.stem}_{j}{meta.suffix}"
                j += 1
            shutil.move(str(meta), str(target_meta))
            moved += (target_meta,)

    return moved


def upload_video(service, video: Path, body: Dict[str, Any]):
    media = MediaFileUpload(str(video), chunksize=-1, resumable=True)
    request = service.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        try:
            status, response = request.next_chunk()
        except HttpError as exc:
            err(f"Ошибка загрузки {video.name}: {exc}")
            raise
        if status:
            percent = int(status.progress() * 100)
            log(f"{video.name}: {percent}%")
    return response


def main() -> int:
    config_path = Path(os.environ.get("APP_CONFIG_PATH", Path(__file__).resolve().parents[2] / "app_config.yaml")).resolve()
    cfg = load_app_config(config_path)

    channel_name = os.environ.get("YOUTUBE_CHANNEL_NAME", "").strip()
    if not channel_name:
        err("Не задан YOUTUBE_CHANNEL_NAME")
        return 2

    yt_cfg = cfg.get("youtube", {}) or {}
    channel_cfg = None
    for ch in yt_cfg.get("channels", []) or []:
        if ch.get("name") == channel_name:
            channel_cfg = ch
            break
    if not channel_cfg:
        err(f"Канал {channel_name} не найден в app_config.yaml")
        return 2

    src_dir = Path(os.environ.get("YOUTUBE_SRC_DIR", yt_cfg.get("upload_src_dir", ""))).expanduser().resolve()
    if not src_dir.exists():
        err(f"Папка с видео не найдена: {src_dir}")
        return 3

    draft_only = os.environ.get("YOUTUBE_DRAFT_ONLY", "0") == "1"
    publish_at_env = None if draft_only else os.environ.get("YOUTUBE_PUBLISH_AT")
    publish_at_global = to_rfc3339(publish_at_env)
    try:
        step_minutes = max(0, int(os.environ.get("YOUTUBE_BATCH_STEP_MINUTES", "0") or 0))
    except ValueError:
        step_minutes = 0
    try:
        batch_limit = int(os.environ.get("YOUTUBE_BATCH_LIMIT", "0") or 0)
    except ValueError:
        batch_limit = 0

    archive_dir = Path(os.environ.get("YOUTUBE_ARCHIVE_DIR", yt_cfg.get("archive_dir", src_dir / "uploaded")) ).expanduser().resolve()

    videos = collect_videos(src_dir)
    if batch_limit > 0:
        videos = videos[:batch_limit]
    if not videos:
        log("Нет файлов для загрузки")
        return 0

    creds = ensure_credentials(channel_cfg, config_path.parent)
    service = build("youtube", "v3", credentials=creds, cache_discovery=False)

    base_dt = None
    if publish_at_global:
        try:
            base_dt = dt.datetime.fromisoformat(publish_at_global.replace("Z", "+00:00"))
        except ValueError:
            err(f"Неверный publish_at: {publish_at_global}")
            publish_at_global = None

    uploaded = 0
    for idx, video in enumerate(videos):
        log(f"Загружаем {video.name}")
        meta = load_metadata(video)
        title = meta.get("title") or video.stem
        description = meta.get("description") or ""
        tags = meta.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]

        publish_at_video = to_rfc3339(meta.get("publishAt") or meta.get("publish_at") or publish_at_env)
        publish_at = publish_at_video or publish_at_global
        if not publish_at_video and publish_at_global and base_dt and step_minutes > 0:
            scheduled_dt = base_dt + dt.timedelta(minutes=step_minutes * idx)
            publish_at = scheduled_dt.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")

        privacy = (meta.get("privacy") or meta.get("privacyStatus") or meta.get("privacy_status") or channel_cfg.get("default_privacy") or "private").lower()
        status: Dict[str, Any] = {
            "privacyStatus": "private" if publish_at and not draft_only else privacy,
            "selfDeclaredMadeForKids": bool(meta.get("made_for_kids", False)),
        }
        if publish_at and not draft_only:
            status["publishAt"] = publish_at
            log(f"→ расписано на {publish_at}")
        elif draft_only:
            log("→ сохранится как приватный черновик")
        body = {
            "snippet": {
                "title": title,
                "description": description,
                "categoryId": str(meta.get("categoryId", 24)),
            },
            "status": status,
        }
        if tags:
            body["snippet"]["tags"] = tags

        try:
            response = upload_video(service, video, body)
            video_id = response.get("id") if isinstance(response, dict) else None
            log(f"✓ Загружено: {video.name} → https://youtube.com/watch?v={video_id}" if video_id else f"✓ Загружено: {video.name}")
            moved = move_to_archive(video, archive_dir)
            if moved:
                log(f"Файл перемещён в архив: {moved[0]}")
                for extra in moved[1:]:
                    log(f"↳ также перенесено: {extra}")
            uploaded += 1
            time.sleep(1.0)
        except Exception as exc:
            err(f"Загрузка прервана: {exc}")
            return 1

    log(f"Готово. Успешно: {uploaded}/{len(videos)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
