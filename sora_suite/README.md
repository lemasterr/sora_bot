# Sora Suite — Полированный комплект

Готовый переносимый проект для:
- автоподачи промптов в Sora,
- автоскачки видео драфтов,
- блюра зон (FFmpeg) и склейки,
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
- вкладка **Задачи** — выберите шаги (Autogen / Download / Blur / Merge) и нажмите «Старт сценария»;
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

### Лимиты автоподачи

В блоке `queue_retry` файла `workers/autogen/config.yaml` можно задать:

- `max_retry_cycles` — максимальное число полных циклов переподачи (0 = бесконечно).
- `max_attempts_per_prompt` — лимит попыток для одного промпта до его исключения из очереди (0 = без лимита).

При достижении лимитов автоген прекратит переподачу и пометит промпт в `failed.log` с соответствующей причиной.

## Telegram (опционально)
На вкладке **Настройки → Telegram** можно включить уведомления, указать токен и chat ID, а также отправить тестовое сообщение прямо из приложения. Альтернативно в `app_config.yaml` включите `telegram.enabled: true` и задайте `bot_token`+`chat_id` вручную.

