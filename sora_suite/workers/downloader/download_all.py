#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Скачивание драфтов из Sora через существующий браузер Chrome."""

from __future__ import annotations

import os
import random
import re
import time
from pathlib import Path
from typing import Optional

from playwright.sync_api import Error as PwError
from playwright.sync_api import TimeoutError as PwTimeout
from playwright.sync_api import sync_playwright

DRAFTS_URL = "https://sora.chatgpt.com/drafts"

# === Дефолтные пути относительно корня проекта ===
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DOWNLOAD_DIR = PROJECT_ROOT / "downloads"
DEFAULT_TITLES_FILE = PROJECT_ROOT / "titles.txt"
DEFAULT_CURSOR_FILE = PROJECT_ROOT / "titles.cursor"

# ===== Настройки через ENV =====
CDP_ENDPOINT = os.getenv("CDP_ENDPOINT", "http://localhost:9222")
DOWNLOAD_DIR = os.path.abspath(os.getenv("DOWNLOAD_DIR", str(DEFAULT_DOWNLOAD_DIR)))
TITLES_FILE = os.getenv("TITLES_FILE", str(DEFAULT_TITLES_FILE)).strip()
TITLES_CURSOR_FILE = os.getenv("TITLES_CURSOR_FILE", str(DEFAULT_CURSOR_FILE)).strip()
MAX_VIDEOS = int(os.getenv("MAX_VIDEOS", "0") or "0")  # 0 = скачать все

# ===== UI =====
DOWNLOAD_MENU_LABELS = ["Download", "Скачать", "Download video", "Save video", "Export"]


def jitter(a: float = 0.08, b: float = 0.25) -> None:
    time.sleep(random.uniform(a, b))


def long_jitter(a: float = 0.8, b: float = 1.8) -> None:
    time.sleep(random.uniform(a, b))


# Селекторы
CARD_LINKS = "a[href*='/d/']"
RIGHT_PANEL = "div.absolute.right-0.top-0"
KEBAB_IN_RIGHT_PANEL = f"{RIGHT_PANEL} button[aria-haspopup='menu']:not([aria-label='Settings'])"
MENU_ROOT = "[role='menu']"
MENUITEM = "[role='menuitem']"
BACK_BTN = "a[aria-label='Back']"


# ----- titles helpers -----
def sanitize_filename(name: str) -> str:
    name = name.strip()
    name = re.sub(r'[\\/:*?"<>|]+', " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:120] if len(name) > 120 else name


def read_titles_list(path: str) -> list[str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return [ln.strip() for ln in f if ln.strip()]
    except Exception:
        return []


def read_cursor(path: str) -> int:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return int(f.read().strip() or "0")
    except Exception:
        return 0


def write_cursor(path: str, idx: int) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(str(idx))
    except Exception:
        pass


def next_custom_title() -> Optional[str]:
    """Берём следующее имя из titles.txt."""

    if not TITLES_FILE:
        return None
    titles = read_titles_list(TITLES_FILE)
    if not titles:
        return None
    cursor_path = TITLES_CURSOR_FILE or (os.path.splitext(TITLES_FILE)[0] + ".cursor")
    idx = read_cursor(cursor_path)
    if idx < 0 or idx >= len(titles):
        return None
    raw = titles[idx]
    title = sanitize_filename(raw)
    write_cursor(cursor_path, idx + 1)  # двигаем в любом случае
    if not title:
        return None
    return title


def next_numbered_filename(save_dir: str, ext: str) -> str:
    existing = [f for f in os.listdir(save_dir) if f.lower().endswith((".mp4", ".mov", ".webm"))]
    numbers = []
    for filename in existing:
        stem = os.path.splitext(filename)[0]
        if stem.isdigit():
            numbers.append(int(stem))
    next_num = max(numbers) + 1 if numbers else 1
    return os.path.join(save_dir, f"{next_num}{ext}")


# ----- browser attach -----
def attach_browser(play):
    return play.chromium.connect_over_cdp(CDP_ENDPOINT)


def is_card_url(url: str) -> bool:
    return "/d/" in (url or "")


def open_drafts_page(context):
    """Открывает страницу драфтов, даже если была открыта карточка."""

    page = context.pages[0] if context.pages else context.new_page()
    page.bring_to_front()
    try:
        page.goto(DRAFTS_URL, wait_until="domcontentloaded")
    except Exception:
        try:
            page.goto(DRAFTS_URL)
        except Exception:
            pass
    try:
        page.wait_for_load_state("networkidle")
    except Exception:
        pass
    return page


def _open_first_card_on_page(page, *, allow_reload: bool = True) -> bool:
    """Пробует открыть первую карточку на текущей странице черновиков."""

    try:
        cards = page.locator(CARD_LINKS)
        cards.first.wait_for(state="visible", timeout=15000)
    except PwTimeout:
        if allow_reload:
            try:
                page.reload()
                page.wait_for_timeout(500)
            except Exception:
                pass
            return _open_first_card_on_page(page, allow_reload=False)
        return False

    try:
        box = cards.first.bounding_box()
        if box:
            page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            jitter(0.05, 0.12)
        cards.first.click()
        page.wait_for_url("**/d/**", timeout=15000)
    except PwTimeout:
        if allow_reload:
            try:
                page.reload()
                page.wait_for_timeout(600)
            except Exception:
                pass
            return _open_first_card_on_page(page, allow_reload=False)
        if not is_card_url(page.url):
            return False
    except Exception:
        if not is_card_url(page.url):
            return False

    try:
        page.locator(RIGHT_PANEL).wait_for(state="visible", timeout=10000)
    except PwTimeout:
        if not is_card_url(page.url):
            return False
    return True


def open_card(page, href: str) -> bool:
    """Открывает карточку по прямой ссылке."""

    if not href:
        return False

    try:
        page.goto(href, wait_until="domcontentloaded")
    except Exception:
        try:
            page.goto(href)
        except Exception:
            return False

    try:
        page.locator(RIGHT_PANEL).wait_for(state="visible", timeout=10000)
    except PwTimeout:
        return False
    return True


def open_kebab_menu(page) -> None:
    kebabs = page.locator(KEBAB_IN_RIGHT_PANEL)
    kebabs.first.wait_for(state="visible", timeout=8000)
    btn = kebabs.first
    box = btn.bounding_box()
    if box:
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        jitter(0.1, 0.25)
    btn.click()
    page.locator(MENU_ROOT).wait_for(state="visible", timeout=6000)


def click_download_in_menu(page, save_dir: str) -> str:
    menu = page.locator(MENU_ROOT)
    candidate = None
    for label in DOWNLOAD_MENU_LABELS:
        loc = menu.locator(f"{MENUITEM}:has-text('{label}')")
        if loc.count() > 0:
            candidate = loc.first
            break
    if candidate is None:
        candidate = menu.locator(MENUITEM).first

    with page.expect_download(timeout=20000) as dl_info:
        candidate.click()
    download = dl_info.value

    os.makedirs(save_dir, exist_ok=True)

    ext = os.path.splitext(download.suggested_filename)[1] or ".mp4"

    custom = next_custom_title()
    if custom:
        target_path = os.path.join(save_dir, f"{custom}{ext}")
    else:
        target_path = next_numbered_filename(save_dir, ext)

    base, extension = os.path.splitext(target_path)
    suffix = 1
    while os.path.exists(target_path):
        target_path = f"{base} ({suffix}){extension}"
        suffix += 1

    download.save_as(target_path)
    return target_path


def _long_swipe_once(page) -> None:
    """Один длинный свайп вверх для переключения карточки."""

    try:
        viewport = page.viewport_size or {"width": 1280, "height": 720}
        page.mouse.move(viewport["width"] / 2, viewport["height"] * 0.35)
    except Exception:
        pass

    def _wheel(delta: int) -> bool:
        try:
            page.mouse.wheel(0, delta)
            return True
        except Exception:
            try:
                page.evaluate("window.scrollBy(0, arguments[0])", delta)
                return True
            except Exception:
                return False

    # один плавный жест из нескольких порций, но без спама
    performed = False
    for _ in range(3):
        performed = _wheel(900) or performed
        page.wait_for_timeout(160)

    if not performed:
        _wheel(2400)

    page.wait_for_timeout(820)


def ensure_card_open(page, *, from_drafts: bool = True) -> bool:
    """Гарантирует, что страница открыта на карточке Sora."""

    if is_card_url(page.url):
        try:
            page.locator(RIGHT_PANEL).wait_for(state="visible", timeout=8000)
            return True
        except PwTimeout:
            return False

    if from_drafts:
        return _open_first_card_on_page(page)

    try:
        first = page.locator(CARD_LINKS)
        first.first.wait_for(state="visible", timeout=12000)
        href = first.first.get_attribute("href")
        if not href:
            return False
        opened = open_card(page, href)
        if opened:
            return True
    except Exception:
        pass

    return _open_first_card_on_page(page, allow_reload=False)


def download_current_card(page, save_dir: str) -> bool:
    try:
        open_kebab_menu(page)
    except PwTimeout:
        print("[!] Не нашёл меню «три точки» — пропускаю.")
        return False
    try:
        path = click_download_in_menu(page, save_dir)
        print(f"[✓] Скачано: {os.path.basename(path)}")
        return True
    except PwTimeout:
        print("[!] Меню есть, но загрузка не стартовала. Повтор через 1.5с…")
        time.sleep(1.5)
        try:
            open_kebab_menu(page)
            path = click_download_in_menu(page, save_dir)
            print(f"[✓] Скачано: {os.path.basename(path)}")
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"[x] Не удалось скачать: {exc}")
            return False


def _current_video_src(page) -> str:
    try:
        return page.evaluate("() => document.querySelector('video')?.currentSrc || ''") or ""
    except Exception:
        return ""


def _cloudflare_detected(page) -> bool:
    """Проверяем типичные элементы Cloudflare/Turnstile."""

    try:
        return bool(
            page.evaluate(
                """
                () => {
                    const text = document.body?.innerText?.toLowerCase?.() || '';
                    if (text.includes('checking if the site connection is secure') || text.includes('verify you are human')) {
                        return true;
                    }
                    const cfForm = document.querySelector('form#challenge-form') || document.querySelector('form[action*="challenge"]');
                    const cfFrame = Array.from(document.querySelectorAll('iframe')).some(f => (f.src || '').includes('challenges.cloudflare.com'));
                    const turnstile = document.querySelector('[data-sitekey][data-cf-challenge]') || document.querySelector('iframe[src*="turnstile"]');
                    const overlay = document.querySelector('.challenge-container, #cf-stage, #challenge-stage');
                    return Boolean(cfForm || cfFrame || turnstile || overlay);
                }
                """
            )
        )
    except Exception:
        return False


def _wait_for_cloudflare(page) -> bool:
    """Если всплыло окно Cloudflare, ждём решения и уведомляем пользователя."""

    if not _cloudflare_detected(page):
        return False

    print("[NOTIFY] CLOUDFLARE_ALERT")
    print("[!] Похоже, появился Cloudflare. Пройди проверку — я подожду.")

    loops = 0
    while _cloudflare_detected(page):
        page.wait_for_timeout(1200)
        loops += 1
        if loops % 5 == 0:
            print("[!] Всё ещё жду прохождения Cloudflare…")

    print("[i] Проверка завершена, продолжаю работу.")
    page.wait_for_timeout(800)
    return True


def scroll_to_next_card(page, *, pause_ms: int = 1800, timeout_ms: int = 9000) -> bool:
    """Листает ленту вниз одним длинным свайпом и ждёт смены карточки.

    Между свайпами даём странице время на смену адреса и источника видео,
    чтобы не перескочить сразу две карточки подряд.
    """

    start_url = page.url
    start_src = _current_video_src(page)

    if _wait_for_cloudflare(page):
        start_url = page.url
        start_src = _current_video_src(page)

    def _wait_for_change(total_ms: int) -> bool:
        deadline = time.time() + (total_ms / 1000)
        while time.time() < deadline:
            if _cloudflare_detected(page):
                return False
            if (page.url != start_url) or (_current_video_src(page) != start_src):
                return True
            page.wait_for_timeout(180)
        return (page.url != start_url) or (_current_video_src(page) != start_src)

    def _changed() -> bool:
        return (page.url != start_url) or (_current_video_src(page) != start_src)

    _long_swipe_once(page)
    page.wait_for_timeout(pause_ms)

    if _cloudflare_detected(page):
        _wait_for_cloudflare(page)
        start_url = page.url
        start_src = _current_video_src(page)
    if _wait_for_change(timeout_ms):
        page.wait_for_timeout(700)
        try:
            page.locator(RIGHT_PANEL).wait_for(state="visible", timeout=6500)
        except PwTimeout:
            pass
        return True

    if not _changed():
        long_jitter(0.7, 1.2)
        _long_swipe_once(page)
        page.wait_for_timeout(int(pause_ms * 0.9))

    if _wait_for_change(int(timeout_ms * 0.9)):
        page.wait_for_timeout(700)
        try:
            page.locator(RIGHT_PANEL).wait_for(state="visible", timeout=6500)
        except PwTimeout:
            pass
        return True

    return _changed()


def download_feed_mode(page, desired: int) -> None:
    """Скачивает текущую карточку и листает ленту вниз как в TikTok."""

    target = desired if desired > 0 else None
    done = 0
    seen: set[str] = set()

    if not ensure_card_open(page):
        print("[x] Не удалось открыть карточку для скачивания.")
        return

    while True:
        _wait_for_cloudflare(page)

        current_url = page.url
        if current_url in seen:
            print("[!] Карточка уже была, листать дальше не получается — стоп.")
            break

        page.bring_to_front()
        ok = download_current_card(page, DOWNLOAD_DIR)
        if ok:
            done += 1
            seen.add(current_url)

        if target and done >= target:
            break

        if not scroll_to_next_card(page):
            print("[!] Не смог перейти к следующему видео — останавливаюсь.")
            break
        page.wait_for_timeout(600)
        long_jitter()


def main() -> None:
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    with sync_playwright() as p:
        try:
            browser = attach_browser(p)
            contexts = browser.contexts
            if not contexts:
                raise RuntimeError(
                    "Нет контекстов Chrome. Запусти Chrome с --remote-debugging-port=9222 и сессией Sora."
                )
            context = contexts[0]
            open_drafts = os.getenv("OPEN_DRAFTS_FIRST", "1").lower() not in {"0", "false", "off"}
            page = open_drafts_page(context) if open_drafts else (context.pages[0] if context.pages else context.new_page())
            page.bring_to_front()
            print(f"[i] Работаю в существующем окне: {page.url}")

            desired = MAX_VIDEOS if MAX_VIDEOS > 0 else 0

            if not ensure_card_open(page, from_drafts=open_drafts):
                print("[x] Не удалось открыть первую карточку — остановка.")
                return
            print("[i] Открыта первая карточка — перехожу в режим скролла.")
            download_feed_mode(page, desired)

            print("[i] Готово.")
        except Exception as exc:  # noqa: BLE001
            print(f"[x] Критическая ошибка: {exc}")
            raise


if __name__ == "__main__":
    main()
