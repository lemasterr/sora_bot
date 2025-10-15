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

## Telegram (опционально)
В `app_config.yaml` включите `telegram.enabled: true` и задайте `bot_token`+`chat_id` — получите уведомления об окончании шагов.

