# Sora Suite (Electron + React)

Переписанный интерфейс Sora Suite теперь работает на Electron/React с тёмным "Modern Admin" дизайном. Старый PyQt6 UI удалён: запуск `python sora_suite/app.py` поднимает локальный сервер и открывает новый фронтенд.

## Быстрый старт
1. Установите Python-зависимости для бэкенда (опциональные воркеры, FFmpeg и др.):
   ```bash
   python -m venv .venv
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
3. Запустите приложение:
   ```bash
   python sora_suite/app.py
   ```
   Скрипт сам откроет браузер/оболочку с новым дизайном. Для разработки можно оставить Vite dev‑сервер (`npm run dev`) и вызвать `python sora_suite/app.py --dev`.

## Что внутри
- **Frontend:** React 19 + Vite, Tailwind, Zustand, Lucide, подготовленные страницы (Dashboard, Workspaces, Automator, Content, Settings, Telegram, Errors/History/Docs, Watermark Check).
- **Backend:** существующие воркеры на Python (автоген, скачка, ffmpeg и др.) без старой оболочки PyQt.
- **Сборка:** статические файлы лежат в `sora_suite/frontend/dist` и обслуживаются `sora_suite/app.py`.

## Полезные команды
- `npm run dev` — горячая перезагрузка UI на `localhost:5173`.
- `npm run build` — сборка production-версии UI.
- `python sora_suite/app.py --no-browser` — запуск сервера без автооткрытия вкладки.
