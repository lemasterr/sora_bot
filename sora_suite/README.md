# Sora Suite — Полированный комплект

Готовый переносимый проект для:
- автоподачи промптов в Sora,
- автоскачки видео драфтов,
- блюра зон (FFmpeg) и склейки,
- отложенного постинга на YouTube для нескольких каналов,
- с GUI на PyQt6 и системными уведомлениями (tray).

## Установка (один раз)

```bash
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium
```

**FFmpeg**: установите в систему (`ffmpeg` в PATH).

## Запуск GUI

```bash
python app.py
```

В GUI:
- вкладка **Задачи** — выберите шаги (Autogen / Download / Blur / Merge / Upload) и нажмите «Старт сценария»;
- вкладка **Промпты** — редактируйте `workers/autogen/prompts.txt` (1 строка = 1 промпт);
- вкладка **Настройки** — пути, CDP, профили Chrome, FFmpeg, координаты блюра;
- системные уведомления сообщают о старте/успехе/ошибке каждого шага.

## Конфиги и файлы

- `app_config.yaml` — главный конфиг GUI.
- `workers/autogen/config.yaml` — параметры автогена.
- `workers/autogen/selectors.yaml` — селекторы DOM (textarea/кнопка/тосты/очередь).
- `workers/downloader/download_config.yaml` — скачка и URL-кандидаты.
- `workers/autogen/prompts.txt` — текстовые промпты.
- `titles.txt` — имена для переименования скачанных файлов.
- `workers/uploader/upload_queue.py` — загрузка на YouTube (YouTube Data API v3).

## YouTube: отложенный постинг

1. Создайте OAuth-клиент в [Google Cloud Console](https://console.cloud.google.com/) (тип «Desktop») и скачайте `client_secret.json`.
2. В **Настройки → YouTube аккаунты** добавьте канал, указав путь к `client_secret.json`, путь для `credentials.json` (можно оставить пустым — сохранится рядом) и приватность по умолчанию.
3. Выберите активный канал. Вкладка **Задачи** позволит выбрать его для сценария, указать папку с клипами (по умолчанию `merged`) и задать время публикации.
4. При первом запуске появится консольное окно с OAuth-кодом — авторизуйтесь от имени нужного YouTube-аккаунта. Токен сохраняется в `credentials.json`, поэтому разные пользователи/каналы могут сосуществовать (по одному файлу на канал).
5. После удачной загрузки файл и метаданные перемещаются в архив (`uploaded/` по умолчанию). Для создания индивидуальных описаний положите рядом с роликом `*.json` или `*.yaml` с ключами `title`, `description`, `tags`, `publishAt`.

> **Windows/macOS/Linux**: приложение автоматически подставляет корректный путь к Chrome и создаёт рабочие каталоги, поэтому портирование между системами не требует ручного редактирования путей.

## Telegram (опционально)
В `app_config.yaml` включите `telegram.enabled: true` и задайте `bot_token`+`chat_id` — получите уведомления об окончании шагов.

