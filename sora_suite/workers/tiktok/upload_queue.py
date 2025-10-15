#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Загрузка видео на TikTok через Playwright.

Скрипт запускается из GUI Sora Suite и использует экспортированные cookies.
Ожидаемые переменные окружения:
- APP_CONFIG_PATH — путь к app_config.yaml;
- TIKTOK_PROFILE_NAME — имя выбранного профиля в конфиге;
- TIKTOK_SRC_DIR — папка с видео для загрузки;
- TIKTOK_ARCHIVE_DIR — куда перемещать успешно загруженные файлы;
- TIKTOK_BATCH_LIMIT — ограничение по количеству файлов (0 = без ограничения);
- TIKTOK_BATCH_STEP_MINUTES — интервал между роликами при пакетном расписании;
- TIKTOK_DRAFT_ONLY — «1» чтобы оставить ролики в черновиках;
- TIKTOK_PUBLISH_AT — ISO8601 (UTC) стартовое время публикации (опционально).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import shutil
import datetime as dt
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo  # type: ignore

from playwright.async_api import (  # type: ignore
    async_playwright,
    TimeoutError as PlaywrightTimeoutError,
)


def log(msg: str):
    print(f"[TT] {msg}", flush=True)


def err(msg: str):
    print(f"[TT][ERR] {msg}", file=sys.stderr, flush=True)


def resolve_path(base: Path, raw: Optional[str]) -> Optional[Path]:
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = (base / path).resolve()
    return path


def load_app_config(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"app_config.yaml не найден: {path}")
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def collect_videos(src: Path) -> Tuple[Path, ...]:
    patterns = ("*.mp4", "*.mov", "*.m4v", "*.webm")
    files: List[Path] = []
    for pattern in patterns:
        files.extend(src.glob(pattern))
    return tuple(sorted(files, key=lambda p: p.stat().st_mtime))


def load_metadata(video: Path) -> Dict[str, Any]:
    for ext in (".json", ".yaml", ".yml"):
        candidate = video.with_suffix(ext)
        if candidate.exists():
            try:
                if candidate.suffix.lower() == ".json":
                    return json.loads(candidate.read_text(encoding="utf-8"))
                return yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
            except Exception as exc:  # pragma: no cover
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
    return caption.strip()


def move_to_archive(video: Path, archive_dir: Path) -> List[Path]:
    archive_dir.mkdir(parents=True, exist_ok=True)
    moved: List[Path] = []
    target = archive_dir / video.name
    i = 1
    while target.exists():
        target = archive_dir / f"{video.stem}_{i}{video.suffix}"
        i += 1
    shutil.move(str(video), str(target))
    moved.append(target)
    for ext in (".json", ".yaml", ".yml"):
        meta = video.with_suffix(ext)
        if meta.exists():
            dest = archive_dir / meta.name
            j = 1
            while dest.exists():
                dest = archive_dir / f"{meta.stem}_{j}{meta.suffix}"
                j += 1
            shutil.move(str(meta), str(dest))
            moved.append(dest)
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
    step_total = max(0, step_minutes) * index
    scheduled = base + dt.timedelta(minutes=step_total)
    return scheduled.astimezone(dt.timezone.utc)


async def ensure_cookies(context, cookies_path: Path):
    if not cookies_path.exists():
        raise FileNotFoundError(f"Файл cookies не найден: {cookies_path}")
    try:
        data = json.loads(cookies_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Не удалось прочитать cookies: {exc}") from exc
    cookies: List[Dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        value = item.get("value")
        if not name or value is None:
            continue
        domain = item.get("domain") or ".tiktok.com"
        cookies.append(
            {
                "name": name,
                "value": value,
                "domain": domain,
                "path": item.get("path", "/"),
                "secure": bool(item.get("secure", True)),
                "httpOnly": bool(item.get("httpOnly", False)),
                "expires": int(item.get("expires", -1)),
            }
        )
    if not cookies:
        raise RuntimeError("Файл cookies пустой")
    await context.add_cookies(cookies)
    log(f"Загружено cookies: {len(cookies)}")


async def upload_single(page, video: Path, caption: str, schedule: Optional[dt.datetime], draft_only: bool) -> None:
    await page.goto("https://www.tiktok.com/upload?lang=en", wait_until="domcontentloaded")
    file_input = page.locator('input[type="file"]')
    await file_input.set_input_files(str(video))
    await page.wait_for_timeout(1000)

    caption_box = page.locator('textarea')
    try:
        await caption_box.first.click()
        await caption_box.first.fill(caption[:2200])
    except PlaywrightTimeoutError:
        err("Не удалось заполнить подпись")

    if draft_only:
        toggle = page.locator('[data-e2e="draft-checkbox"] input[type="checkbox"]')
        try:
            await toggle.wait_for(timeout=5000)
            if not await toggle.is_checked():
                await toggle.click()
        except PlaywrightTimeoutError:
            err("Чекбокс Draft не найден — продолжаем")
    elif schedule:
        try:
            schedule_toggle = page.locator('[data-e2e="schedule-button"] input[type="checkbox"]')
            await schedule_toggle.wait_for(timeout=5000)
            if not await schedule_toggle.is_checked():
                await schedule_toggle.click()
            await page.wait_for_timeout(300)
            date_input = page.locator('input[data-e2e="schedule-date"]')
            time_input = page.locator('input[data-e2e="schedule-time"]')
            await date_input.fill(schedule.strftime("%Y-%m-%d"))
            await date_input.press("Enter")
            await time_input.fill(schedule.strftime("%H:%M"))
            await time_input.press("Enter")
        except PlaywrightTimeoutError:
            err("Не удалось включить расписание — публикуем сразу")

    publish_btn = page.locator('[data-e2e="post-button"]')
    await publish_btn.wait_for(state="visible", timeout=300000)
    await publish_btn.click()
    try:
        await page.wait_for_selector('div[data-e2e="upload-success"]', timeout=300000)
    except PlaywrightTimeoutError:
        err("Не дождались подтверждения загрузки — продолжим")


async def run_async(cfg: Dict[str, Any], profile_name: str, src_dir: Path, archive_dir: Path,
                    batch_limit: int, step_minutes: int, draft_only: bool, publish_iso: Optional[str]) -> int:
    tk_cfg = cfg.get("tiktok", {}) or {}
    profile = None
    for prof in tk_cfg.get("profiles", []) or []:
        if prof.get("name") == profile_name:
            profile = prof
            break
    if not profile:
        err(f"Профиль {profile_name} не найден")
        return 2

    cookies_path = resolve_path(Path(cfg.get("project_root", ".")), profile.get("cookies_file"))
    if not cookies_path:
        err("Не указан файл cookies")
        return 2

    videos = collect_videos(src_dir)
    if batch_limit > 0:
        videos = videos[:batch_limit]

    if not videos:
        log("Нет файлов для загрузки")
        return 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        await ensure_cookies(context, cookies_path)
        page = await context.new_page()

        for idx, video in enumerate(videos):
            meta = load_metadata(video)
            caption = build_caption(profile, meta, video.stem)
            schedule = parse_schedule(publish_iso, profile, idx, step_minutes) if not draft_only else None
            schedule_text = schedule.isoformat() if schedule else "immediate"
            log(f"Загружаем {video.name} (schedule={schedule_text})")
            try:
                await upload_single(page, video, caption, schedule, draft_only)
            except Exception as exc:
                err(f"Ошибка при загрузке {video.name}: {exc}")
                continue
            moved = move_to_archive(video, archive_dir)
            log(f"Файлы перемещены в архив: {', '.join(str(m) for m in moved)}")
            await page.wait_for_timeout(1500)

        await browser.close()
    return 0


def main() -> int:
    cfg_path = Path(os.environ.get("APP_CONFIG_PATH", Path(__file__).resolve().parents[2] / "app_config.yaml"))
    cfg = load_app_config(cfg_path)

    profile_name = os.environ.get("TIKTOK_PROFILE_NAME", "").strip()
    if not profile_name:
        err("Не задан TIKTOK_PROFILE_NAME")
        return 2

    src_dir = Path(os.environ.get("TIKTOK_SRC_DIR", "")).expanduser().resolve()
    if not src_dir.exists():
        err(f"Папка источника не найдена: {src_dir}")
        return 3

    archive_dir = Path(os.environ.get("TIKTOK_ARCHIVE_DIR", "")).expanduser().resolve()
    batch_limit = int(os.environ.get("TIKTOK_BATCH_LIMIT", "0") or 0)
    step_minutes = int(os.environ.get("TIKTOK_BATCH_STEP_MINUTES", "0") or 0)
    draft_only = os.environ.get("TIKTOK_DRAFT_ONLY", "0") == "1"
    publish_iso = os.environ.get("TIKTOK_PUBLISH_AT")

    return asyncio.run(run_async(cfg, profile_name, src_dir, archive_dir, batch_limit, step_minutes, draft_only, publish_iso))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
