# Sora Suite — новый интерфейс

Приложение целиком переехало на Electron + React с тёмным административным дизайном. Старый PyQt6 UI удалён: при запуске бэкенд больше не создаёт Qt-окна, а сразу открывает собранный фронтенд в Electron.

## Установка
1. Создайте виртуальное окружение и поставьте зависимости:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   python -m pip install --upgrade pip
   python -m pip install -r sora_suite/requirements.txt
   ```
2. Соберите фронтенд:
   ```bash
   cd sora_suite/frontend
   npm install
   npm run build
   ```

## Запуск
- Production/просмотр билда:
  ```bash
  python -m sora_suite.app
  ```
  Скрипт запускает Electron и грузит `frontend/dist/index.html` в десктопное окно.
- Разработка:
  ```bash
  npm run dev  # в другой вкладке терминала
  python -m sora_suite.app --dev --port 5173
  ```
  `--dev` подключает Electron к dev серверу Vite (порт по умолчанию можно переопределить опцией `--port`).

## Структура фронтенда
- `src/App.tsx` — роутер и каркас тёмной темы.
- `src/components/` — страницы Dashboard, Workspaces (с переносом лимитов), Automator, Content (титулы по профилям), Settings (обновлённые вкладки), Telegram, Errors, History, Docs, Watermark Check.
- `src/types.ts` — актуальные типы без старого пайлайна/автопостинга.

## Что осталось в бэкенде
Воркеры и утилиты на Python (автоген, скачка, ffmpeg, watermark detector и др.) сохранены без изменений. Они могут вызываться через новый интерфейс/IPC, но визуальная оболочка PyQt больше не запускается.

### Если Electron показывает пустой экран
- Убедитесь, что выполнена сборка (`npm run build`) и в `frontend/dist` есть `index.html`.
- В dev-режиме проверьте, что Vite слушает тот же порт, который передан опцией `--port`.
