# -*- coding: utf-8 -*-
"""
Sora autogen (Chrome CDP)
- Строгая валидация старта (очищение поля/рост очереди)
- Бэк-офф при лимите
- Автоматическая переподача промптов до успеха (бесконечная, с паузой)
- Статистика/метки для GUI (OK/FAIL/RETRY + NOTIFY)
- PROMPTS_FILE берётся из env SORA_PROMPTS_FILE (если задан)
"""

import os
import time
from pathlib import Path
from typing import List, Optional, Tuple, Set, Deque, Dict
from collections import deque

import yaml
from playwright.sync_api import (
    sync_playwright,
    TimeoutError as PWTimeout,
    Page, Browser, BrowserContext, ElementHandle
)

PROJECT_DIR = Path(__file__).parent
CONFIG_FILE = PROJECT_DIR / "config.yaml"
SELECTORS_FILE = PROJECT_DIR / "selectors.yaml"
PROMPTS_FILE = Path(os.getenv("SORA_PROMPTS_FILE", str(PROJECT_DIR / "prompts.txt")))
SUBMITTED_LOG = PROJECT_DIR / "submitted.log"
FAILED_LOG = PROJECT_DIR / "failed.log"

# ----------------- utils -----------------
def load_yaml(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Не найден файл: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def load_prompts() -> List[str]:
    if not PROMPTS_FILE.exists():
        print(f"[!] Нет файла {PROMPTS_FILE}. Создай его и добавь промпты по одному в строке.")
        return []
    return [ln.strip() for ln in PROMPTS_FILE.read_text(encoding="utf-8").splitlines() if ln.strip()]

def load_submitted() -> Set[str]:
    if not SUBMITTED_LOG.exists():
        return set()
    return {ln.strip() for ln in SUBMITTED_LOG.read_text(encoding="utf-8").splitlines() if ln.strip()}

def mark_submitted(prompt: str) -> None:
    with open(SUBMITTED_LOG, "a", encoding="utf-8") as f:
        f.write(prompt.replace("\n", " ") + "\n")

def mark_failed(prompt: str, reason: str) -> None:
    clean_prompt = prompt.replace("\n", " ")
    with open(FAILED_LOG, "a", encoding="utf-8") as f:
        f.write(f"{clean_prompt} || {reason}\n")

# ----------------- page lookup -----------------
def find_sora_page(ctx: BrowserContext, hint: str = "sora") -> Optional[Page]:
    hint = (hint or "").lower()
    for p in ctx.pages:
        try:
            if hint in (p.url or "").lower():
                return p
        except Exception:
            pass
    for p in ctx.pages:
        try:
            if "sora" in (p.title() or "").lower():
                return p
        except Exception:
            pass
    return None

# ----------------- textarea resolving -----------------
def resolve_textarea(page: Page, sels: dict, dom_timeout_ms: int, debug: bool=False) -> Tuple[str, str]:
    candidates = []
    primary = sels.get("textarea", {}).get("css")
    if primary:
        candidates.append(("css", primary))
    for alt in sels.get("textarea_alternatives", []) or []:
        if alt.startswith("role="):
            candidates.append(("role", alt.split("=", 1)[1]))
        else:
            candidates.append(("css", alt))
    builtin = [
        ("css", "textarea[placeholder^='Describe your video']"),
        ("css", "textarea[placeholder*='Describe']"),
        ("css", "textarea"),
        ("css", "[contenteditable='true']"),
        ("role", "textbox"),
    ]
    for b in builtin:
        if b not in candidates:
            candidates.append(b)
    last_err = None
    for kind, sel in candidates:
        try:
            if kind == "css":
                page.wait_for_selector(sel, state="visible", timeout=dom_timeout_ms)
                if debug: print(f"[i] textarea via CSS: {sel}")
                return kind, sel
            else:
                loc = page.get_by_role(sel)
                loc.first.wait_for(state="visible", timeout=dom_timeout_ms)
                if debug: print(f"[i] textarea via role={sel}")
                return kind, sel
        except Exception as e:
            last_err = e
            continue
    raise PWTimeout(f"Не найдено поле ввода. Последняя ошибка: {last_err}")

# ----------------- DOM helpers -----------------
def get_bbox(page: Page, handle: ElementHandle):
    try:
        return handle.bounding_box()
    except Exception:
        return None

def is_inside_dialog(page: Page, handle: ElementHandle) -> bool:
    try:
        return page.evaluate("(el)=>!!el.closest('[role=\"dialog\"],[aria-modal=\"true\"]')", handle)
    except Exception:
        return False

def has_svg_child(page: Page, handle: ElementHandle) -> bool:
    try:
        return page.evaluate("(el)=>!!el.querySelector('svg')", handle)
    except Exception:
        return False

def text_content(page: Page, handle: ElementHandle) -> str:
    try:
        return (page.evaluate("(el)=>el.innerText || ''", handle) or "").strip()
    except Exception:
        return ""

def nearest_container_selector() -> str:
    return ",".join(["form","section","div[class*='flex']","div[class*='grid']","main"])

def find_button_in_same_container(page: Page, ta_handle: ElementHandle, debug: bool=False) -> Optional[ElementHandle]:
    sel = nearest_container_selector()
    container = page.evaluate_handle(
        """({ ta, sel }) => {
          let n = ta;
          while (n && n.nodeType === 1) {
            if (n.matches(sel)) return n;
            n = n.parentElement;
          }
          return ta.closest(sel) || ta.parentElement || document.body;
        }""",
        {"ta": ta_handle, "sel": sel}
    )
    btn_array = page.evaluate_handle("(root) => Array.from(root.querySelectorAll('button'))", container)
    btn_handles = list(btn_array.get_properties().values())

    ta_box = get_bbox(page, ta_handle)
    ta_center_y = ta_box["y"] + ta_box["height"] / 2 if ta_box else None

    candidates = []
    for btn in btn_handles:
        try:
            if is_inside_dialog(page, btn):
                continue
            if not has_svg_child(page, btn):
                continue
            box = get_bbox(page, btn)
            if not box:
                continue
            w, h = box["width"], box["height"]
            if not (32 <= w <= 80 and 32 <= h <= 80):
                continue
            if ta_center_y is not None:
                btn_center_y = box["y"] + h / 2
                if abs(btn_center_y - ta_center_y) > 120:
                    continue
            if "add" in text_content(page, btn).lower():
                continue
            looks_like_sliders = page.evaluate("""
              (el)=>{
                const svg = el.querySelector('svg'); if(!svg) return false;
                const hasLine = svg.querySelector('line') !== null;
                const rects = svg.querySelectorAll('rect');
                if (hasLine) return true;
                if (rects.length >= 3) return true;
                return false;
              }""", btn)
            candidates.append((btn, box, looks_like_sliders))
        except Exception:
            continue

    if not candidates:
        return None
    candidates.sort(key=lambda x: (1 if x[2] else 0, x[1]["x"]))
    non_sliders = [c for c in candidates if not c[2]]
    pool = non_sliders if non_sliders else candidates
    rightmost = max(pool, key=lambda x: x[1]["x"])
    if debug: print(f"[i] button bbox in container: {rightmost[1]}")
    return rightmost[0]

# ----------------- typing / start checks -----------------
def js_inject_text(page: Page, element_handle: ElementHandle, text: str) -> None:
    page.evaluate(
        """({ el, text }) => {
          function fire(el, type){ el.dispatchEvent(new Event(type, {bubbles:true, cancelable:true})); }
          const isTextarea = el.tagName && el.tagName.toLowerCase() === 'textarea';
          const isCE = el.getAttribute && el.getAttribute('contenteditable') === 'true';
          if (isTextarea) {
            el.focus(); el.value = text;
            try { el.setSelectionRange(text.length, text.length); } catch(e){}
            fire(el,'input'); fire(el,'change');
          } else if (isCE) {
            el.focus(); el.innerText = text;
            fire(el,'input'); fire(el,'change');
          } else {
            el.focus?.(); try { el.value = text; fire(el,'input'); fire(el,'change'); } catch(e){}
          }
        }""",
        {"el": element_handle, "text": text}
    )

def type_prompt(page: Page, ta_kind: str, ta_sel: str, text: str, human_delay_ms: int, debug: bool=False) -> None:
    loc = page.locator(ta_sel).first if ta_kind == "css" else page.get_by_role(ta_sel).first
    loc.click(timeout=8000)
    try: loc.fill("", timeout=1000)
    except Exception: pass
    handle = loc.element_handle()
    if handle: js_inject_text(page, handle, text)
    try:
        loc.type(" ", delay=5); page.keyboard.press("Backspace")
        loc.type(".", delay=human_delay_ms); page.keyboard.press("Backspace")
    except Exception: pass
    if debug: print("[i] prompt typed into field.")

def is_button_enabled_handle(page: Page, handle: ElementHandle) -> bool:
    try:
        return page.evaluate("(el)=>!el.disabled && el.getAttribute('data-disabled')!=='true'", handle)
    except Exception:
        return False

def textarea_value(page: Page, ta_kind: str, ta_sel: str) -> str:
    try:
        if ta_kind == "css":
            return page.locator(ta_sel).first.input_value(timeout=300) or ""
        else:
            return (page.get_by_role(ta_sel).first.inner_text(timeout=300) or "").strip()
    except Exception:
        return ""

def error_toast_present(page: Page, sels: dict) -> bool:
    try:
        cont = sels.get("error_toast", {}).get("container")
        texts = sels.get("error_toast", {}).get("text_contains") or []
        if not cont: return False
        loc = page.locator(cont)
        if not loc.count(): return False
        txt = (loc.inner_text(timeout=500) or "").lower()
        return any(fragment.lower() in txt for fragment in texts if fragment)
    except Exception:
        return False

def queue_count_snapshot(page: Page, sels: dict) -> int:
    css_gen = (sels.get("queue_generating") or {}).get("css")
    css_ready = (sels.get("queue_ready") or {}).get("css")
    cnt = 0
    try:
        if css_gen: cnt += page.locator(css_gen).count()
    except Exception: pass
    try:
        if css_ready: cnt += page.locator(css_ready).count()
    except Exception: pass
    return cnt

def confirm_start_strict(page: Page, ta_kind: str, ta_sel: str, before_qcount: int, sels: dict, timeout_ms: int) -> bool:
    start = time.time()
    while True:
        if error_toast_present(page, sels):
            return False
        val = textarea_value(page, ta_kind, ta_sel)
        if val == "":
            return True
        after = queue_count_snapshot(page, sels)
        if after > before_qcount:
            return True
        if (time.time() - start) * 1000 > timeout_ms:
            return False
        time.sleep(0.2)

# ----------------- submit logic -----------------
def submit_prompt_once(page: Page,
                       sels: dict,
                       ta_kind: str,
                       ta_sel: str,
                       btn_handle: ElementHandle,
                       prompt: str,
                       typing_delay_ms: int,
                       start_confirm_timeout_ms: int,
                       retry_interval_ms: int,
                       backoff_seconds_on_reject: int,
                       debug: bool=False) -> Tuple[bool, str]:
    cur = textarea_value(page, ta_kind, ta_sel)
    if cur.strip() != prompt.strip():
        type_prompt(page, ta_kind, ta_sel, prompt, typing_delay_ms, debug)

    q_before = queue_count_snapshot(page, sels)

    while not is_button_enabled_handle(page, btn_handle):
        time.sleep(retry_interval_ms / 1000.0)
    try:
        btn_handle.click(timeout=8000)
    except PWTimeout:
        pass

    if confirm_start_strict(page, ta_kind, ta_sel, q_before, sels, timeout_ms=start_confirm_timeout_ms):
        print("[OK] принято UI.")
        return True, ""

    if error_toast_present(page, sels):
        msg = f"queue-limit/backoff-{backoff_seconds_on_reject}s"
        print(f"[RETRY] {msg}")
        time.sleep(backoff_seconds_on_reject)
        return False, msg

    print("[RETRY] slot-locked")
    while True:
        while not is_button_enabled_handle(page, btn_handle):
            time.sleep(retry_interval_ms / 1000.0)
        q_before = queue_count_snapshot(page, sels)
        try:
            btn_handle.click(timeout=8000)
        except PWTimeout:
            time.sleep(retry_interval_ms / 1000.0)
            continue
        if confirm_start_strict(page, ta_kind, ta_sel, q_before, sels, timeout_ms=start_confirm_timeout_ms):
            print("[OK] принято UI.")
            return True, ""
        if error_toast_present(page, sels):
            msg = f"queue-limit/backoff-{backoff_seconds_on_reject}s"
            print(f"[RETRY] {msg}")
            time.sleep(backoff_seconds_on_reject)

# ----------------- loop & bootstrap -----------------
def maybe_accept_media_agreement(page: Page, sels: dict, enable: bool) -> None:
    if not enable:
        return
    try:
        mag = sels.get("media_agreement", {}) or {}
        dlg = mag.get("dialog")
        if not dlg or not page.locator(dlg).count():
            return
        cbs = mag.get("checkboxes")
        if cbs:
            for i in range(page.locator(cbs).count()):
                page.locator(cbs).nth(i).check(force=True, timeout=2000)
        acc = mag.get("accept_btn")
        if acc:
            page.locator(acc).first.click(timeout=4000)
            time.sleep(0.3)
            print("[i] Media upload agreement принято автоматически.")
    except Exception as e:
        print(f"[!] Не удалось авто-принять agreement: {e}")

def ensure_page(pw, cfg: dict) -> Tuple[Browser, BrowserContext, Page]:
    endpoint = cfg.get("cdp_endpoint", "http://localhost:9222")
    browser: Browser = pw.chromium.connect_over_cdp(endpoint)
    context = browser.contexts[0] if browser.contexts else browser.new_context()
    page = find_sora_page(context, hint="sora") or context.new_page()
    if page.url == "about:blank":
        page.goto(cfg.get("sora_url", "https://sora.chatgpt.com/drafts"), wait_until="load")
    page.bring_to_front()
    page.wait_for_load_state("domcontentloaded")
    print(f"[i] Текущая вкладка: {page.url}")
    return browser, context, page

def run_loop(page: Page, cfg: dict, selectors: dict, prompts: List[str], already: Set[str]) -> None:
    poll_interval = int(cfg.get("poll_interval_ms", 1500)) / 1000.0
    typing_delay_ms = int(cfg.get("human_typing_delay_ms", 12))
    start_confirm_timeout_ms = int(cfg.get("start_confirmation_timeout_ms", 8000))
    retry_interval_ms = int((cfg.get("queue_retry") or {}).get("retry_interval_ms", 2500))
    backoff_on_reject = int((cfg.get("queue_retry") or {}).get("backoff_seconds_on_reject", 180))
    success_pause_every_n = int((cfg.get("queue_retry") or {}).get("success_pause_every_n", 0))
    success_pause_seconds = int((cfg.get("queue_retry") or {}).get("success_pause_seconds", 0))
    max_retry_cycles = int((cfg.get("queue_retry") or {}).get("max_retry_cycles", 0))
    max_attempts_per_prompt = int((cfg.get("queue_retry") or {}).get("max_attempts_per_prompt", 0))
    debug = bool(cfg.get("debug", False))

    print("[NOTIFY] AUTOGEN_START")

    if cfg.get("auto_accept_media_agreement", True):
        maybe_accept_media_agreement(page, selectors, True)

    dom_timeout_ms = int(cfg.get("dom_timeout_ms", 12000))
    ta_kind, ta_sel = resolve_textarea(page, selectors, dom_timeout_ms, debug=debug)
    ta_handle = (page.locator(ta_sel).first.element_handle() if ta_kind == "css"
                 else page.get_by_role(ta_sel).first.element_handle())
    btn_handle = find_button_in_same_container(page, ta_handle, debug=debug)
    if btn_handle is None:
        fb = (selectors.get("generate_button", {}) or {}).get("css")
        if fb and page.locator(fb).count():
            print("[i] Беру кнопку по fallback CSS.")
            btn_handle = page.locator(fb).first.element_handle()
    if btn_handle is None:
        raise PWTimeout("Не удалось найти кнопку отправки рядом с полем.")

    print("[i] Кнопка-стрелка определена.")

    queue = deque([p for p in prompts if p not in already])
    attempts: Dict[str, int] = {}
    if not queue:
        print("[i] Нет новых промптов — всё уже отправлено.")
        print("[NOTIFY] AUTOGEN_FINISH_OK")
        return

    print(f"[STEP] Готово к подаче: {len(queue)} промптов.")

    success = 0
    failed = 0
    retry_queue: Deque[str] = deque()

    idx_total = len(queue)
    idx_counter = 0
    t_start = time.time()

    while queue:
        prompt = queue.popleft()
        idx_counter += 1
        print(f"[STEP] {idx_counter}/{idx_total} — отправляю…")
        attempts[prompt] = attempts.get(prompt, 0) + 1
        ok, reason = submit_prompt_once(
            page=page,
            sels=selectors,
            ta_kind=ta_kind,
            ta_sel=ta_sel,
            btn_handle=btn_handle,
            prompt=prompt,
            typing_delay_ms=typing_delay_ms,
            start_confirm_timeout_ms=start_confirm_timeout_ms,
            retry_interval_ms=retry_interval_ms,
            backoff_seconds_on_reject=backoff_on_reject,
            debug=debug,
        )
        if ok:
            print("[OK] принято UI.")
            mark_submitted(prompt)
            success += 1
            if (success_pause_every_n and success_pause_seconds
                and success % success_pause_every_n == 0
                and (queue or retry_queue)):
                print(f"[INFO] Пауза {success_pause_seconds}s после {success} успешных.")
                time.sleep(success_pause_seconds)
        else:
            attempt_num = attempts[prompt]
            limit_hit = max_attempts_per_prompt and attempt_num >= max_attempts_per_prompt
            reason_to_store = reason or ""
            if limit_hit:
                extra = f"attempt-limit-{attempt_num}"
                reason_to_store = f"{reason_to_store}|{extra}" if reason_to_store else extra
                print(f"[FAIL] лимит попыток ({attempt_num}) исчерпан — удаляю из очереди.")
            else:
                print(f"[WARN] не удалось отправить (пока): {reason}")
            mark_failed(prompt, reason_to_store)
            if not limit_hit:
                retry_queue.append(prompt)
            failed += 1

        time.sleep(poll_interval)

    cycle = 0
    while retry_queue:
        cycle += 1
        if max_retry_cycles and cycle > max_retry_cycles:
            print(f"[FAIL] достигнут лимит циклов переподачи ({max_retry_cycles}). Останавливаюсь.")
            remaining = list(retry_queue)
            retry_queue.clear()
            for prompt in remaining:
                mark_failed(prompt, f"cycle-limit-{max_retry_cycles}")
            break
        print(f"[STEP] Переподача, цикл #{cycle}. Осталось: {len(retry_queue)}")
        cur_round = deque()
        while retry_queue:
            cur_round.append(retry_queue.popleft())

        for prompt in cur_round:
            print(f"[STEP] RETRY — пробую снова…")
            if max_attempts_per_prompt and attempts.get(prompt, 0) >= max_attempts_per_prompt:
                current_attempts = attempts.get(prompt, 0)
                display_attempts = current_attempts or max_attempts_per_prompt
                limit_reason = f"attempt-limit-{display_attempts}"
                print(f"[FAIL] лимит попыток ({display_attempts}) исчерпан — пропускаю повтор.")
                mark_failed(prompt, limit_reason)
                continue
            attempts[prompt] = attempts.get(prompt, 0) + 1
            ok, reason = submit_prompt_once(
                page=page,
                sels=selectors,
                ta_kind=ta_kind,
                ta_sel=ta_sel,
                btn_handle=btn_handle,
                prompt=prompt,
                typing_delay_ms=typing_delay_ms,
                start_confirm_timeout_ms=start_confirm_timeout_ms,
                retry_interval_ms=retry_interval_ms,
                backoff_seconds_on_reject=backoff_on_reject,
                debug=debug,
            )
            if ok:
                print("[OK] принято UI.")
                mark_submitted(prompt)
                success += 1
                if (success_pause_every_n and success_pause_seconds
                    and success % success_pause_every_n == 0
                    and (retry_queue)):
                    print(f"[INFO] Пауза {success_pause_seconds}s после {success} успешных.")
                    time.sleep(success_pause_seconds)
            else:
                attempt_num = attempts[prompt]
                limit_hit = max_attempts_per_prompt and attempt_num >= max_attempts_per_prompt
                reason_base = f"retry:{reason}" if reason else "retry"
                if limit_hit:
                    extra = f"attempt-limit-{attempt_num}"
                    reason_base = f"{reason_base}|{extra}" if reason_base else extra
                    print(f"[FAIL] лимит попыток ({attempt_num}) исчерпан — удаляю из очереди.")
                else:
                    print(f"[WARN] снова отказ: {reason}")
                mark_failed(prompt, reason_base)
                if not limit_hit:
                    retry_queue.append(prompt)
                failed += 1
            time.sleep(poll_interval)

        time.sleep(20)

    elapsed = int(time.time() - t_start)
    print(f"[STAT] success={success} failed={failed} elapsed={elapsed}s")
    print("[NOTIFY] AUTOGEN_FINISH_OK" if failed == 0 else "[NOTIFY] AUTOGEN_FINISH_PARTIAL")

def main():
    print("[STEP] Запуск автогена…")
    cfg = load_yaml(CONFIG_FILE)
    sels = load_yaml(SELECTORS_FILE)
    prompts = load_prompts()
    submitted = load_submitted()
    if not prompts:
        print("[x] Нет промптов — выходим")
        return
    with sync_playwright() as pw:
        browser, context, page = ensure_page(pw, cfg)
        try:
            run_loop(page, cfg, sels, prompts, submitted)
        finally:
            pass

if __name__ == "__main__":
    main()
