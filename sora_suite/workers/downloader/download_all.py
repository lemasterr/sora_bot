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


def find_or_open_drafts_page(context):
    for page in context.pages:
        if page.url.startswith(DRAFTS_URL):
            return page
    page = context.pages[0] if context.pages else context.new_page()
    page.bring_to_front()
    page.goto(DRAFTS_URL, wait_until="domcontentloaded")
    return page


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


def go_back_to_drafts(page) -> None:
    try:
        page.locator(BACK_BTN).click(timeout=2000)
    except PwError:
        page.go_back(wait_until="domcontentloaded")
    page.locator(CARD_LINKS).first.wait_for(timeout=10000)


def collect_card_links(page, desired: int) -> list[str]:
    """Собирает уникальные ссылки карточек, подгружая их по мере прокрутки."""

    print("[i] Сканирую карточки Sora…")
    links: list[str] = []
    seen: set[str] = set()
    stagnation = 0
    satisfied_rounds = 0
    rounds = 0

    try:
        page.evaluate("window.scrollTo(0, 0)")
    except Exception:
        pass

    cards = page.locator(CARD_LINKS)
    cards.first.wait_for(timeout=10000)

    while True:
        current = []
        try:
            current = page.eval_on_selector_all(
                CARD_LINKS,
                "elements => elements.map(el => el.href).filter(Boolean)",
            )
        except Exception:
            # fall back на count/scroll при ошибке
            current = []

        added = 0
        for href in current:
            if href not in seen:
                seen.add(href)
                links.append(href)
                added += 1

        if added:
            print(f"[i] Найдено карточек: {len(links)}")
            stagnation = 0
        else:
            stagnation += 1

        if desired and len(links) >= desired:
            satisfied_rounds += 1
        else:
            satisfied_rounds = 0

        if (desired and satisfied_rounds >= 3) or (desired and stagnation >= 8) or (
            not desired and stagnation >= 4
        ):
            break
        if rounds > 80:
            break

        rounds += 1

        try:
            page.mouse.wheel(0, 1400)
            page.wait_for_timeout(450)
            page.mouse.wheel(0, 1400)
        except Exception:
            try:
                cards.nth(cards.count() - 1).scroll_into_view_if_needed()
            except Exception:
                pass
        long_jitter(0.9, 1.4)

    print(f"[i] Итого уникальных карточек: {len(links)}")
    return links


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
            page = find_or_open_drafts_page(context)
            print(f"[i] Работаю в существующем окне: {page.url}")

            desired = MAX_VIDEOS if MAX_VIDEOS > 0 else 0
            links = collect_card_links(page, desired)

            if desired:
                links = links[:desired]
                print(f"[i] Скачаю первые {len(links)} карточек")
            else:
                print(f"[i] Скачаю все найденные карточки: {len(links)}")

            for k, href in enumerate(links, 1):
                print(f"[>] {k}/{len(links)} — открываю карточку…")
                if not open_card(page, href):
                    print("[!] Карточка недоступна — пропускаю.")
                    continue
                long_jitter()
                try:
                    open_kebab_menu(page)
                except PwTimeout:
                    print("[!] Не нашёл меню «три точки» — пропускаю.")
                    go_back_to_drafts(page)
                    continue
                try:
                    path = click_download_in_menu(page, DOWNLOAD_DIR)
                    print(f"[✓] Скачано: {os.path.basename(path)}")
                except PwTimeout:
                    print("[!] Меню есть, но загрузка не стартовала. Повтор через 1.5с…")
                    time.sleep(1.5)
                    try:
                        open_kebab_menu(page)
                        path = click_download_in_menu(page, DOWNLOAD_DIR)
                        print(f"[✓] Скачано: {os.path.basename(path)}")
                    except Exception as exc:  # noqa: BLE001
                        print(f"[x] Не удалось скачать: {exc}")
                long_jitter()
                go_back_to_drafts(page)
                long_jitter()
                try:
                    page.evaluate("window.scrollTo(0, 0)")
                except Exception:
                    pass

            print("[i] Готово.")
        except Exception as exc:  # noqa: BLE001
            print(f"[x] Критическая ошибка: {exc}")
            raise


if __name__ == "__main__":
    main()
