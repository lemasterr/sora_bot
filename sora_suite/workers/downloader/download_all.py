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
SCROLL_MODE = os.getenv("SCROLL_MODE", "").lower() in {"1", "true", "yes", "on"}

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


def find_or_open_start_page(context):
    for page in context.pages:
        if is_card_url(page.url):
            page.bring_to_front()
            return page, True
    for page in context.pages:
        if page.url.startswith(DRAFTS_URL):
            page.bring_to_front()
            return page, False
    page = context.pages[0] if context.pages else context.new_page()
    page.bring_to_front()
    page.goto(DRAFTS_URL, wait_until="domcontentloaded")
    return page, False


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


def _wait_for_new_cards(page, previous_total: int, timeout_ms: int) -> bool:
    """Ждёт появления новых карточек; возвращает True при успехе."""

    try:
        page.wait_for_function(
            "(arg) => document.querySelectorAll(arg.selector).length > arg.count",
            {
                "selector": CARD_LINKS,
                "count": int(previous_total),
            },
            timeout=timeout_ms,
        )
        return True
    except Exception:
        return False


def _smooth_scroll(page, *, distance: int = 1400, pulses: int = 6, pause: tuple[float, float] = (0.1, 0.22)) -> None:
    """Плавно прокручивает страницу небольшими импульсами."""

    if pulses <= 0:
        pulses = 1
    step = max(int(distance / pulses), 120)
    for _ in range(pulses):
        try:
            page.mouse.wheel(0, step)
        except Exception:
            try:
                page.evaluate("window.scrollBy(0, arguments[0])", step)
            except Exception:
                break
        page.wait_for_timeout(int(random.uniform(pause[0], pause[1]) * 1000))


def _is_near_bottom(page) -> bool:
    try:
        return bool(
            page.evaluate(
                "() => (window.innerHeight + window.scrollY + 120) >= (document.body ? document.body.scrollHeight : 0)"
            )
        )
    except Exception:
        return False


def collect_card_links(page, desired: int) -> list[str]:
    """Собирает уникальные ссылки карточек плавной прокруткой ленты."""

    print("[i] Сканирую карточки Sora…")
    links: list[str] = []
    seen: set[str] = set()
    stagnation = 0
    satisfied_rounds = 0
    rounds = 0

    target_only = desired > 0
    settle_rounds = 2 if target_only else 1

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
            current = []

        dom_count = len(current)
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

        if target_only and len(links) >= desired:
            satisfied_rounds += 1
        else:
            satisfied_rounds = 0

        stagnation_limit = 12 if target_only else 6
        if (
            (target_only and satisfied_rounds >= settle_rounds)
            or stagnation >= stagnation_limit
            or rounds >= (220 if target_only else 120)
        ):
            break

        rounds += 1

        prev_total = dom_count
        pulses = 8 if target_only else 5
        distance = 1800 if target_only else 1200
        _smooth_scroll(page, distance=distance, pulses=pulses)

        waited = _wait_for_new_cards(page, prev_total, 2200 if target_only else 1400)
        if not waited:
            long_jitter(0.9, 1.4 if target_only else 1.0)
        if target_only and _is_near_bottom(page):
            # если дошли до конца, ждём возможной догрузки и завершаем
            page.wait_for_timeout(700)
            _wait_for_new_cards(page, len(current), 1400)
            break

        long_jitter(1.05, 1.55 if target_only else 1.2)

    print(f"[i] Итого уникальных карточек: {len(links)}")
    return links


def ensure_card_open(page) -> bool:
    """Гарантирует, что страница открыта на карточке Sora."""

    if is_card_url(page.url):
        try:
            page.locator(RIGHT_PANEL).wait_for(state="visible", timeout=8000)
            return True
        except PwTimeout:
            return False

    try:
        first = page.locator(CARD_LINKS)
        first.first.wait_for(state="visible", timeout=8000)
        href = first.first.get_attribute("href")
        if not href:
            return False
        return open_card(page, href)
    except Exception:
        return False


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


def scroll_to_next_card(page, *, pause_ms: int = 850, timeout_ms: int = 6500) -> bool:
    """Листает ленту вниз и ждёт смены карточки."""

    start_url = page.url
    for attempt in range(3):
        _smooth_scroll(page, distance=2200, pulses=7)
        page.wait_for_timeout(pause_ms + attempt * 200)
        try:
            page.wait_for_function(
                "({ start }) => window.location.href !== start",
                {"start": start_url},
                timeout=timeout_ms + attempt * 1200,
            )
            try:
                page.locator(RIGHT_PANEL).wait_for(state="visible", timeout=6000)
            except PwTimeout:
                pass
            return True
        except PwTimeout:
            continue
    return False


def download_feed_mode(page, desired: int) -> None:
    """Скачивает текущую карточку и листает ленту вниз как в TikTok."""

    target = desired if desired > 0 else None
    done = 0
    seen: set[str] = set()

    if not ensure_card_open(page):
        print("[x] Не удалось открыть карточку для скачивания.")
        return

    while True:
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
            page, on_card_page = find_or_open_start_page(context)
            print(f"[i] Работаю в существующем окне: {page.url}")

            desired = MAX_VIDEOS if MAX_VIDEOS > 0 else 0

            if on_card_page or SCROLL_MODE:
                print("[i] Обнаружена открытая карточка — перехожу в режим скролла.")
                download_feed_mode(page, desired)
            else:
                links = collect_card_links(page, desired)

                if desired:
                    if len(links) < desired:
                        print(
                            f"[!] Найдено только {len(links)} карточек из {desired}. Будут скачаны все доступные."
                        )
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
                    ok = download_current_card(page, DOWNLOAD_DIR)
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
