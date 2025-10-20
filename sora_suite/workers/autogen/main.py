# -*- coding: utf-8 -*-
"""Sora autogen (Chrome CDP).

- Строгая валидация старта (очищение поля/рост очереди)
- Бэк-офф при лимите
- Автоматическая переподача промптов до успеха (бесконечная, с паузой)
- Статистика/метки для GUI (OK/FAIL/RETRY + NOTIFY)
- PROMPTS_FILE берётся из env SORA_PROMPTS_FILE (если задан)
- Поддержка генерации изображений через Google AI Studio
"""

import json
import os
import re
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Set, Tuple

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
SUBMITTED_LOG = Path(os.getenv("SORA_SUBMITTED_LOG", str(PROJECT_DIR / "submitted.log")))
FAILED_LOG = Path(os.getenv("SORA_FAILED_LOG", str(PROJECT_DIR / "failed.log")))
INSTANCE_NAME = os.getenv("SORA_INSTANCE_NAME", "default")
PROMPTS_DIR = PROMPTS_FILE.parent.resolve()
BASE_MEDIA_DIR = Path(os.getenv("GENAI_BASE_DIR", str(PROJECT_DIR.parent))).expanduser()
PROMPTS_BASE_DIR = Path(os.getenv("GENAI_PROMPTS_DIR", str(PROMPTS_DIR))).expanduser()
_IMAGE_PROMPTS_ENV = os.getenv("GENAI_IMAGE_PROMPTS_FILE", "").strip()
IMAGE_PROMPTS_FILE = Path(_IMAGE_PROMPTS_ENV).expanduser() if _IMAGE_PROMPTS_ENV else Path()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _to_int(value: Optional[str], fallback: int) -> int:
    if value is None:
        return fallback
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return fallback


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "prompt"


@dataclass
class PromptEntry:
    key: str
    prompt: str
    raw: str
    image_prompts: List[str] = field(default_factory=list)
    attachment_paths: List[str] = field(default_factory=list)
    images_per_prompt: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    generated_files: List[Path] = field(default_factory=list)

    def resolved_key(self) -> str:
        return self.key or self.prompt


@dataclass
class ImagePromptSpec:
    prompts: List[str]
    count: Optional[int] = None


@dataclass
class GenAiConfig:
    enabled: bool
    api_key: str
    model: str
    person_generation: str
    aspect_ratio: str
    image_size: str
    output_dir: Path
    mime_type: str
    number_of_images: int
    rate_limit: int
    max_retries: int

    @classmethod
    def from_env(cls) -> "GenAiConfig":
        enabled = _env_bool("GENAI_ENABLED", False)
        api_key = os.getenv("GENAI_API_KEY", "").strip()
        model = os.getenv("GENAI_MODEL", "models/imagen-4.0-generate-001").strip()
        person = os.getenv("GENAI_PERSON_GENERATION", "ALLOW_ALL").strip() or "ALLOW_ALL"
        aspect = os.getenv("GENAI_ASPECT_RATIO", "1:1").strip() or "1:1"
        size = os.getenv("GENAI_IMAGE_SIZE", "1K").strip() or "1K"
        mime_type = os.getenv("GENAI_OUTPUT_MIME_TYPE", "image/jpeg").strip() or "image/jpeg"
        number = _to_int(os.getenv("GENAI_NUMBER_OF_IMAGES"), 1)
        rate = max(_to_int(os.getenv("GENAI_RATE_LIMIT"), 0), 0)
        retries = max(_to_int(os.getenv("GENAI_MAX_RETRIES"), 3), 0)
        output_raw = os.getenv("GENAI_OUTPUT_DIR", str(PROJECT_DIR.parent / "generated_images"))
        output_dir = Path(output_raw).expanduser()
        if not output_dir.is_absolute():
            output_dir = (BASE_MEDIA_DIR / output_dir).resolve()
        return cls(
            enabled=enabled and bool(api_key),
            api_key=api_key,
            model=model or "models/imagen-4.0-generate-001",
            person_generation=person,
            aspect_ratio=aspect,
            image_size=size,
            output_dir=output_dir,
            mime_type=mime_type,
            number_of_images=max(1, number),
            rate_limit=rate,
            max_retries=retries,
        )


class RateLimiter:
    def __init__(self, per_minute: int):
        self.per_minute = max(per_minute, 0)
        self._events: Deque[float] = deque()

    def wait(self):
        if not self.per_minute:
            return
        now = time.time()
        window = 60.0
        while self._events and now - self._events[0] > window:
            self._events.popleft()
        if len(self._events) >= self.per_minute:
            sleep_for = window - (now - self._events[0])
            if sleep_for > 0:
                print(f"[i] Rate limit: пауза {sleep_for:.1f}с")
                time.sleep(sleep_for)
        self._events.append(time.time())


class GenAiClient:
    def __init__(self, cfg: GenAiConfig):
        self.cfg = cfg
        self._rate = RateLimiter(cfg.rate_limit)
        self._client = None
        self._available = False
        if not cfg.enabled:
            return
        try:
            from google import genai  # type: ignore

            self._client = genai.Client(api_key=cfg.api_key)
            self._available = True
        except ModuleNotFoundError:
            print("[!] Модуль google-genai не установлен. Установи зависимости requirements.txt")
        except Exception as exc:  # noqa: BLE001
            print(f"[!] Не удалось инициализировать google-genai: {exc}")

    @property
    def enabled(self) -> bool:
        return self._available

    def generate(self, prompt: str, count: int, tag: str) -> List[Path]:
        if not self.enabled:
            return []
        self.cfg.output_dir.mkdir(parents=True, exist_ok=True)
        attempt = 0
        last_err: Optional[Exception] = None
        while attempt <= self.cfg.max_retries:
            attempt += 1
            try:
                self._rate.wait()
                result = self._client.models.generate_images(  # type: ignore[union-attr]
                    model=self.cfg.model,
                    prompt=prompt,
                    config=dict(
                        number_of_images=max(1, count),
                        output_mime_type=self.cfg.mime_type,
                        person_generation=self.cfg.person_generation,
                        aspect_ratio=self.cfg.aspect_ratio,
                        image_size=self.cfg.image_size,
                    ),
                )
                if not getattr(result, "generated_images", None):
                    print(f"[WARN] API не вернуло изображений для промпта: {prompt!r}")
                    return []
                saved: List[Path] = []
                timestamp = int(time.time())
                slug = _slugify(prompt)[:48]
                ext = "jpg"
                if self.cfg.mime_type.lower().endswith("png"):
                    ext = "png"
                for idx, generated in enumerate(result.generated_images):
                    fname = f"{slug}-{tag}-{timestamp}-{idx+1:02d}.{ext}"
                    dest = self.cfg.output_dir / fname
                    try:
                        generated.image.save(dest)  # type: ignore[attr-defined]
                        saved.append(dest)
                    except Exception as save_err:  # noqa: BLE001
                        print(f"[WARN] Не удалось сохранить изображение {fname}: {save_err}")
                if saved:
                    print(f"[OK] Сгенерировано {len(saved)} изображений → {self.cfg.output_dir}")
                return saved
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                print(f"[WARN] Ошибка генерации (попытка {attempt}/{self.cfg.max_retries + 1}): {exc}")
                if attempt <= self.cfg.max_retries:
                    time.sleep(min(5 * attempt, 20))
        if last_err:
            print(f"[x] Генерация не удалась окончательно: {last_err}")
        return []

# ----------------- utils -----------------
def load_yaml(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Не найден файл: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def _parse_prompt_line(line: str) -> Optional[PromptEntry]:
    raw = line.strip()
    if not raw or raw.startswith("#"):
        return None

    if raw.startswith("{"):
        try:
            data = json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] Не удалось разобрать JSON промпта: {exc}")
            return None
        if isinstance(data, str):
            data = {"prompt": data}
        if not isinstance(data, dict):
            print(f"[WARN] Неподдерживаемый формат строки: {raw[:80]}")
            return None
        prompt = str(data.get("prompt") or data.get("sora_prompt") or "").strip()
        if not prompt:
            print(f"[WARN] В JSON отсутствует поле prompt: {raw[:120]}")
            return None
        key = str(data.get("key") or data.get("id") or raw)

        image_prompts_field = data.get("image_prompts") or data.get("image_prompt") or []
        if isinstance(image_prompts_field, str):
            image_prompts = [image_prompts_field.strip()]
        elif isinstance(image_prompts_field, list):
            image_prompts = [str(item).strip() for item in image_prompts_field if str(item).strip()]
        else:
            image_prompts = []

        attachments: List[str] = []
        for key_name in ("attachments", "image_paths"):
            value = data.get(key_name)
            if isinstance(value, str) and value.strip():
                attachments.append(value.strip())
            elif isinstance(value, list):
                attachments.extend(str(item).strip() for item in value if str(item).strip())

        images_value = data.get("number_of_images")
        if isinstance(images_value, int) and images_value > 0:
            images_per_prompt = images_value
        elif isinstance(data.get("images"), int) and int(data.get("images")) > 0:
            images_per_prompt = int(data.get("images"))
        elif isinstance(data.get("count"), int) and int(data.get("count")) > 0:
            images_per_prompt = int(data.get("count"))
        else:
            images_per_prompt = None

        if isinstance(data.get("images"), list):
            attachments.extend(str(item).strip() for item in data.get("images", []) if str(item).strip())

        entry = PromptEntry(
            key=key.strip() or raw,
            prompt=prompt,
            raw=raw,
            image_prompts=image_prompts,
            attachment_paths=attachments,
            images_per_prompt=images_per_prompt,
            metadata=data,
        )
        return entry

    return PromptEntry(key=raw, prompt=raw, raw=raw)


def load_prompts() -> List[PromptEntry]:
    if not PROMPTS_FILE.exists():
        print(f"[!] Нет файла {PROMPTS_FILE}. Создай его и добавь промпты по одному в строке.")
        return []
    entries: List[PromptEntry] = []
    for line in PROMPTS_FILE.read_text(encoding="utf-8").splitlines():
        entry = _parse_prompt_line(line)
        if entry:
            entries.append(entry)
    return entries


def _parse_image_prompt_spec(raw: str) -> ImagePromptSpec:
    if raw is None:
        return ImagePromptSpec(prompts=[])
    text = raw.strip()
    if not raw:
        return ImagePromptSpec(prompts=[])
    if not text or text.startswith("#"):
        return ImagePromptSpec(prompts=[])
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] Не удалось разобрать JSON image prompt: {exc}")
            return ImagePromptSpec(prompts=[])
        prompts: List[str] = []
        for key in ("prompts", "prompt", "image_prompts", "image_prompt"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                prompts = [value.strip()]
                break
            if isinstance(value, list):
                prompts = [str(item).strip() for item in value if str(item).strip()]
                if prompts:
                    break
        count: Optional[int] = None
        for key in ("count", "images", "number_of_images"):
            value = data.get(key)
            try:
                ivalue = int(value)
            except (TypeError, ValueError):
                continue
            if ivalue > 0:
                count = ivalue
                break
        return ImagePromptSpec(prompts=prompts, count=count)

    prompts = [part.strip() for part in raw.split("||") if part.strip()]
    return ImagePromptSpec(prompts=prompts)


def load_image_prompt_specs() -> List[ImagePromptSpec]:
    if not _IMAGE_PROMPTS_ENV:
        return []
    if not IMAGE_PROMPTS_FILE.exists():
        return []
    try:
        lines = IMAGE_PROMPTS_FILE.read_text(encoding="utf-8").splitlines()
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] Не удалось прочитать {IMAGE_PROMPTS_FILE}: {exc}")
        return []
    specs = [_parse_image_prompt_spec(line) for line in lines]
    print(f"[INFO] Загружено {len(specs)} image-промптов из {IMAGE_PROMPTS_FILE}")
    return specs


def apply_image_prompt_specs(entries: List[PromptEntry], specs: List[ImagePromptSpec]) -> None:
    if not entries or not specs:
        return
    applied = 0
    idx = 0
    total_specs = len(specs)
    for entry in entries:
        if idx >= total_specs:
            break
        spec = specs[idx]
        idx += 1
        if entry.image_prompts:
            continue
        if not spec.prompts:
            continue
        entry.image_prompts = list(spec.prompts)
        if spec.count and not entry.images_per_prompt:
            entry.images_per_prompt = spec.count
        applied += 1
    if applied:
        print(f"[INFO] Image-промпты сопоставлены: {applied} из {total_specs}")
    elif total_specs:
        print("[INFO] Файл image-промптов найден, но строки пустые — ничего не применяем")
    if idx < total_specs:
        print(f"[INFO] Осталось неиспользованных image-промптов: {total_specs - idx}")

def load_submitted() -> Set[str]:
    if not SUBMITTED_LOG.exists():
        return set()
    submitted: Set[str] = set()
    for raw in SUBMITTED_LOG.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) >= 4:
            key = parts[2].strip() or parts[3].strip()
        elif len(parts) == 3:
            key = parts[2].strip()
        elif len(parts) == 2:
            key = parts[1].strip()
        else:
            key = line
        if key:
            submitted.add(key)
    return submitted


def mark_submitted(entry: PromptEntry, attachments: Optional[List[Path]] = None) -> None:
    SUBMITTED_LOG.parent.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    clean_prompt = entry.prompt.replace("\n", " ")
    key = entry.resolved_key().replace("\n", " ")
    suffix = ""
    if attachments:
        names = [p.name for p in attachments if isinstance(p, Path)]
        if names:
            suffix = f" [media: {', '.join(names)}]"
    with open(SUBMITTED_LOG, "a", encoding="utf-8") as f:
        f.write(f"{ts}\t{INSTANCE_NAME}\t{key}\t{clean_prompt}{suffix}\n")


def mark_failed(entry: PromptEntry, reason: str) -> None:
    clean_prompt = entry.prompt.replace("\n", " ")
    key = entry.resolved_key().replace("\n", " ")
    FAILED_LOG.parent.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(FAILED_LOG, "a", encoding="utf-8") as f:
        f.write(f"{ts}\t{INSTANCE_NAME}\t{key}\t{clean_prompt}\t{reason}\n")


def _resolve_media_path(raw: str) -> Path:
    candidate = Path(str(raw).strip())
    if not str(candidate):
        return candidate
    if candidate.is_absolute():
        return candidate
    probes = [
        PROMPTS_BASE_DIR / candidate,
        BASE_MEDIA_DIR / candidate,
        PROMPTS_DIR / candidate,
        PROJECT_DIR / candidate,
        Path.cwd() / candidate,
    ]
    for probe in probes:
        try:
            resolved = probe.expanduser().resolve()
        except Exception:
            resolved = probe.expanduser()
        if resolved.exists():
            return resolved
    try:
        return (PROMPTS_BASE_DIR / candidate).resolve()
    except Exception:
        return PROMPTS_BASE_DIR / candidate


def gather_media(entry: PromptEntry, client: GenAiClient) -> List[Path]:
    attachments: List[Path] = []
    for raw in entry.attachment_paths:
        if not raw:
            continue
        path = _resolve_media_path(raw)
        if path.exists():
            attachments.append(path)
        else:
            print(f"[WARN] Файл вложения не найден: {raw}")

    if entry.image_prompts:
        if not client.enabled:
            print(f"[WARN] Генерация изображений отключена — пропускаю image_prompt для {entry.prompt!r}")
        else:
            if not entry.generated_files:
                tag = _slugify(entry.resolved_key())[:16] or uuid.uuid4().hex[:8]
                for prompt in entry.image_prompts:
                    if not prompt:
                        continue
                    count = entry.images_per_prompt or client.cfg.number_of_images
                    generated = client.generate(prompt, count, tag)
                    entry.generated_files.extend(generated)
            attachments.extend([p for p in entry.generated_files if isinstance(p, Path) and p.exists()])

    unique: List[Path] = []
    seen: Set[str] = set()
    for path in attachments:
        try:
            key = str(path.resolve())
        except Exception:
            key = str(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


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
def upload_images(page: Page, sels: dict, files: List[Path], debug: bool=False) -> bool:
    if not files:
        return True
    upload_cfg = (sels.get("image_upload") or {})
    clear_sel = upload_cfg.get("clear")
    if clear_sel:
        try:
            loc = page.locator(clear_sel)
            for i in range(loc.count()):
                loc.nth(i).click(timeout=2000)
                time.sleep(0.1)
        except Exception as exc:  # noqa: BLE001
            if debug:
                print(f"[WARN] Не удалось очистить предыдущие вложения: {exc}")

    trigger_sel = upload_cfg.get("trigger") or upload_cfg.get("button")
    if trigger_sel:
        try:
            page.locator(trigger_sel).first.click(timeout=5000)
            time.sleep(0.2)
        except Exception as exc:  # noqa: BLE001
            if debug:
                print(f"[WARN] Не удалось нажать кнопку добавления: {exc}")

    input_sel = upload_cfg.get("css") or "input[type='file']"
    try:
        locator = page.locator(input_sel)
        if not locator.count():
            raise PWTimeout(f"Не найден input для загрузки: {input_sel}")
        resolved: List[str] = []
        for f in files:
            path = Path(f)
            if path.exists():
                resolved.append(str(path))
            else:
                print(f"[WARN] Пропускаю отсутствующий файл: {path}")
        if not resolved:
            return False
        locator.first.set_input_files(resolved)
        wait_sel = upload_cfg.get("wait_for")
        wait_timeout = int(upload_cfg.get("wait_timeout_ms", 8000) or 8000)
        if wait_sel:
            page.locator(wait_sel).first.wait_for(state="visible", timeout=wait_timeout)
        else:
            time.sleep(min(len(resolved) * 0.6, 2.5))
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] Не удалось загрузить изображения: {exc}")
        return False


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
                       entry: PromptEntry,
                       typing_delay_ms: int,
                       start_confirm_timeout_ms: int,
                       retry_interval_ms: int,
                       backoff_seconds_on_reject: int,
                       prepare_media: Optional[Callable[[], bool]] = None,
                       debug: bool=False) -> Tuple[bool, str]:
    prompt = entry.prompt
    cur = textarea_value(page, ta_kind, ta_sel)
    if cur.strip() != prompt.strip():
        type_prompt(page, ta_kind, ta_sel, prompt, typing_delay_ms, debug)

    if prepare_media and not prepare_media():
        return False, "media-upload"

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
        if prepare_media and not prepare_media():
            return False, "media-upload"
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

def run_loop(page: Page, cfg: dict, selectors: dict, prompts: List[PromptEntry], already: Set[str]) -> None:
    poll_interval = int(cfg.get("poll_interval_ms", 1500)) / 1000.0
    typing_delay_ms = int(cfg.get("human_typing_delay_ms", 12))
    start_confirm_timeout_ms = int(cfg.get("start_confirmation_timeout_ms", 8000))
    retry_interval_ms = int((cfg.get("queue_retry") or {}).get("retry_interval_ms", 2500))
    backoff_on_reject = int((cfg.get("queue_retry") or {}).get("backoff_seconds_on_reject", 180))
    success_pause_every_n = int((cfg.get("queue_retry") or {}).get("success_pause_every_n", 0))
    success_pause_seconds = int((cfg.get("queue_retry") or {}).get("success_pause_seconds", 0))
    debug = bool(cfg.get("debug", False))

    genai_cfg = GenAiConfig.from_env()
    genai_client = GenAiClient(genai_cfg)
    if genai_cfg.enabled:
        if genai_client.enabled:
            print(f"[i] Генерация изображений включена → {genai_cfg.output_dir}")
        else:
            print("[WARN] Генерация изображений была включена, но клиент не инициализирован.")

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

    queue = deque([p for p in prompts if p.resolved_key() not in already])
    if not queue:
        print("[i] Нет новых промптов — всё уже отправлено.")
        print("[NOTIFY] AUTOGEN_FINISH_OK")
        return

    print(f"[STEP] Готово к подаче: {len(queue)} промптов.")

    success = 0
    failed = 0
    retry_queue: Deque[PromptEntry] = deque()

    idx_total = len(queue)
    idx_counter = 0
    t_start = time.time()

    while queue:
        entry = queue.popleft()
        idx_counter += 1
        print(f"[STEP] {idx_counter}/{idx_total} — отправляю…")
        attachments_cache: List[Path] = []

        def ensure_media() -> bool:
            nonlocal attachments_cache
            attachments_cache = gather_media(entry, genai_client)
            if not attachments_cache:
                return True
            return upload_images(page, selectors, attachments_cache, debug=debug)

        ok, reason = submit_prompt_once(
            page=page,
            sels=selectors,
            ta_kind=ta_kind,
            ta_sel=ta_sel,
            btn_handle=btn_handle,
            entry=entry,
            typing_delay_ms=typing_delay_ms,
            start_confirm_timeout_ms=start_confirm_timeout_ms,
            retry_interval_ms=retry_interval_ms,
            backoff_seconds_on_reject=backoff_on_reject,
            prepare_media=ensure_media,
            debug=debug,
        )
        if ok:
            print("[OK] принято UI.")
            mark_submitted(entry, attachments_cache)
            success += 1
            if (success_pause_every_n and success_pause_seconds
                and success % success_pause_every_n == 0
                and (queue or retry_queue)):
                print(f"[INFO] Пауза {success_pause_seconds}s после {success} успешных.")
                time.sleep(success_pause_seconds)
        else:
            print(f"[WARN] не удалось отправить (пока): {reason}")
            mark_failed(entry, reason)
            retry_queue.append(entry)
            failed += 1

        time.sleep(poll_interval)

    cycle = 0
    while retry_queue:
        cycle += 1
        print(f"[STEP] Переподача, цикл #{cycle}. Осталось: {len(retry_queue)}")
        cur_round = deque()
        while retry_queue:
            cur_round.append(retry_queue.popleft())

        for entry in cur_round:
            print(f"[STEP] RETRY — пробую снова…")
            attachments_cache: List[Path] = []

            def ensure_media_retry() -> bool:
                nonlocal attachments_cache
                attachments_cache = gather_media(entry, genai_client)
                if not attachments_cache:
                    return True
                return upload_images(page, selectors, attachments_cache, debug=debug)

            ok, reason = submit_prompt_once(
                page=page,
                sels=selectors,
                ta_kind=ta_kind,
                ta_sel=ta_sel,
                btn_handle=btn_handle,
                entry=entry,
                typing_delay_ms=typing_delay_ms,
                start_confirm_timeout_ms=start_confirm_timeout_ms,
                retry_interval_ms=retry_interval_ms,
                backoff_seconds_on_reject=backoff_on_reject,
                prepare_media=ensure_media_retry,
                debug=debug,
            )
            if ok:
                print("[OK] принято UI.")
                mark_submitted(entry, attachments_cache)
                success += 1
                if (success_pause_every_n and success_pause_seconds
                    and success % success_pause_every_n == 0
                    and (retry_queue)):
                    print(f"[INFO] Пауза {success_pause_seconds}s после {success} успешных.")
                    time.sleep(success_pause_seconds)
            else:
                print(f"[WARN] снова отказ: {reason}")
                mark_failed(entry, f"retry:{reason}")
                retry_queue.append(entry)
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
    specs = load_image_prompt_specs()
    if specs:
        apply_image_prompt_specs(prompts, specs)
    submitted = load_submitted()
    if not prompts:
        print("[x] Нет промптов — выходим")
        return
    endpoint_override = os.getenv("SORA_CDP_ENDPOINT")
    if endpoint_override:
        cfg["cdp_endpoint"] = endpoint_override
    with sync_playwright() as pw:
        browser, context, page = ensure_page(pw, cfg)
        try:
            run_loop(page, cfg, sels, prompts, submitted)
        finally:
            pass

if __name__ == "__main__":
    main()
