# Sora Suite — PyQt6 контрольный центр

Sora Suite управляет полным циклом создания и публикации роликов Sora: генерация промптов, автоген изображений через Google AI Studio, отправка в Sora, скачивание, блюр/склейка через FFmpeg и автопостинг на YouTube и TikTok. Интерфейс — десктопное приложение на PyQt6; воркеры запускаются рядом как CLI‑скрипты.

## Быстрый старт
1. Создайте виртуальное окружение и установите зависимости:
   ```bash
   python -m venv .venv
   # Windows: .venv\\Scripts\\activate
   # macOS/Linux: source .venv/bin/activate
   python -m pip install --upgrade pip
   python -m pip install -r sora_suite/requirements.txt
   python -m playwright install chromium
   ```
   Или одной командой:
   ```bash
   python scripts/bootstrap.py
   ```
2. Запустите контроллер:
   ```bash
   python -m sora_suite.app
   ```
3. Первый запуск создаст `sora_suite/app_config.yaml` с дефолтами путей (downloads/blurred/merged, каталоги воркеров, профили Chrome, YouTube/TikTok и т.д.).

## Структура и основные модули
- **GUI**: `sora_suite/app.py` — главное окно с вкладками «Обзор», «Рабочие пространства», «Логи сессий», «Водяной знак», «Проверка ВЗ», YouTube, TikTok, «Контент», Telegram и «Настройки».
- **Конфиг**: `sora_suite/app_config.yaml` — пути проектов, профили автогена и сессий Chrome, параметры FFmpeg, Google GenAI, очереди загрузки и UI‑настройки.
- **Утилиты**:
  - `scripts/bootstrap.py` — установка Python-зависимостей и браузера Playwright.
  - `scripts/self_update.py` — обновление репозитория через `git fetch` + `git pull --ff-only`.
- **Воркеры/CLI**:
  - `sora_suite/workers/autogen/main.py` — автоген промптов и картинок, интеграция Google AI Studio.
  - `sora_suite/workers/downloader/download_all.py` — скачивание драфтов Sora через CDP.
  - `sora_suite/workers/watermark_cleaner/restore.py` — замена водяного знака по шаблону.
  - `sora_suite/workers/uploader/upload_queue.py` — пакетная загрузка на YouTube.
  - `sora_suite/workers/tiktok/upload_queue.py` — загрузка на TikTok + GitHub Actions workflow.

## Запуск и сценарии
- Открывайте вкладку **Обзор**, чтобы отмечать этапы пайплайна (автоген, скачивание, блюр/склейка, публикация) и запускать сценарии.
- Во вкладке **Рабочие пространства** редактируйте профили Chrome/CDP и файлы промптов, запускайте автоген/скачивание по сессиям.
- **YouTube** и **TikTok** управляют очередями, метаданными и расписанием, используют OAuth/секреты из `sora_suite/secrets/`.
- **Водяной знак** и **Проверка ВЗ** дают инструменты поиска/замены логотипа, поддерживают пакетную обработку.
- **Настройки** содержат каталоги проекта, параметры FFmpeg, Playwright/Chrome, Telegram и пользовательские команды.

## Обновления и обслуживание
- Для обновления из репозитория выполните:
  ```bash
  python scripts/self_update.py
  ```
- Файл `history.jsonl` ротуируется при достижении 10 МБ. Очистку старых файлов и автоматический запуск Chrome/воркеров настраивайте во вкладке «Настройки».

## Полезные советы
- Если Playwright не видит Chromium, повторите `python -m playwright install chromium`.
- Для работы CDP убедитесь, что Chrome/Chromium запускается с нужным `--remote-debugging-port` (порт хранится в конфиге).
- FFmpeg можно переопределить в конфиге; для аппаратного ускорения используйте `vcodec: auto_hw`.
