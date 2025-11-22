# Sora Suite (Electron + React)

Переписанный интерфейс Sora Suite теперь работает на Electron/React с тёмным "Modern Admin" дизайном. Старый PyQt6 UI удалён: запуск `python sora_suite/app.py` поднимает Electron-окно с новым дизайном, а не вкладку браузера.

## Быстрый старт
1. Установите Python-зависимости для бэкенда (опциональные воркеры, FFmpeg и др.):
   ```bash
   python3 -m venv .venv  # используйте python, если это ваш интерпретатор по умолчанию
   source .venv/bin/activate  # или .venv\\Scripts\\activate в Windows
   python -m pip install --upgrade pip
   python -m pip install -r sora_suite/requirements.txt
   ```
2. Соберите новый интерфейс:
   ```bash
   cd sora_suite/frontend
   npm install
   npm run build
   ```
3. Запустите приложение как десктоп:
   ```bash
   python -m sora_suite.app
   ```
   Скрипт поднимает Electron и грузит собранный фронтенд. Для разработки оставьте Vite dev‑сервер (`npm run dev`) и вызовите `python -m sora_suite.app --dev --port 5173` — Electron подключится к нему.

## Что внутри
- **Frontend:** React 19 + Vite, Tailwind, Zustand, Lucide, подготовленные страницы (Dashboard, Workspaces, Automator, Content, Settings, Telegram, Errors/History/Docs, Watermark Check).
- **Backend:** существующие воркеры на Python (автоген, скачка, ffmpeg и др.) без старой оболочки PyQt.
- **Сборка:** статические файлы лежат в `sora_suite/frontend/dist` и открываются Electron-окном через `sora_suite/app.py`.

### Если окно пустое/только синий фон
- Проверьте, что есть свежая сборка: `cd sora_suite/frontend && npm run build`.
- Убедитесь, что Electron видит `frontend/dist/index.html` (стартуйте через `python -m sora_suite.app`).
- В dev-режиме убедитесь, что Vite запущен на том же порту, что передан в `--port`.
- Если окно остаётся синим, удалите старый `frontend/dist` и соберите заново (`npm run build`) — пути к ассетам теперь относительные,
  чтобы корректно открываться из `file://`.

## Полезные команды
- `npm run dev` — горячая перезагрузка UI на `localhost:5173`.
- `npm run build` — сборка production-версии UI.
- `npm run electron` — собрать и запустить интерфейс сразу в Electron (без Python-обвязки).
- `npm run electron:dev` — открыть Electron, подключившись к dev‑серверу Vite.
