# ⚡ DropFlow

![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688?logo=fastapi&logoColor=white)
![TailwindCSS](https://img.shields.io/badge/Tailwind_CSS-CDN-38B2AC?logo=tailwindcss&logoColor=white)
![WebSockets](https://img.shields.io/badge/WebSockets-Real--Time-indigo)
![License](https://img.shields.io/badge/License-MIT-green)

**DropFlow** — быстрый и лёгкий сервис для P2P-подобного обмена файлами и синхронизации общего буфера обмена между любыми устройствами в локальной сети (ПК, смартфоны, планшеты) без облаков, регистраций и установки мобильных приложений.

Достаточно запустить сервер на одном устройстве и отсканировать QR-код камерой телефона — веб-интерфейс откроется мгновенно.

---

## ✨ Возможности

- **Потоковая передача файлов любого размера**: загрузка и скачивание чанками (1 МБ буфер через `aiofiles` и `FileResponse`) с минимальным потреблением RAM — подходит даже для многогигабайтных архивов и видео.
- **Drag-and-Drop веб-интерфейс**: тёмная тема на Tailwind CSS с отображением прогресса загрузки в реальном времени и мгновенным обновлением списка файлов у всех участников комнаты.
- **Общий буфер обмена по WebSocket**: синхронизация текста, ссылок, JSON и фрагментов кода в реальном времени с автоопределением типа контента, историей, дедупликацией и антиспам-защитой.
- **Изолированные комнаты (`/ws/{room_id}`)**: поддержка раздельных комнат для разных устройств или команд.
- **Безопасность хранения**: санитизация имён файлов, защита от path traversal и автоматическое разрешение коллизий имён (`file (1).txt`).
- **Удобный CLI с QR-кодом**: автоматическое определение локального IP в Wi-Fi/LAN, поиск свободного порта и вывод ASCII QR-кода прямо в терминал.

---

## 🛠 Стек технологий

- **Backend**: Python 3.10+, FastAPI, Uvicorn, WebSockets, `aiofiles`, `python-multipart`
- **Frontend**: Single-page HTML + Vanilla JS + Tailwind CSS (CDN)
- **CLI & Network**: Typer, Rich, `qrcode`
- **Testing**: Pytest, `pytest-asyncio`, HTTPX / FastAPI `TestClient`

---

## 📦 Установка

Клонируйте репозиторий и установите зависимости (рекомендуется использовать виртуальное окружение):

```bash
git clone <URL_РЕПОЗИТОРИЯ>
cd drop-flow

python3 -m venv .venv
source .venv/bin/activate  # для Windows: .venv\Scripts\activate

pip install -e ".[dev]"
```

---

## 🚀 Запуск

### Через CLI-команду `dropflow`

```bash
dropflow
```

Или через модуль Python:

```bash
python -m src.cli
```

При запуске в терминале отобразится локальный URL (например, `http://192.168.1.42:8000`) и QR-код для быстрого подключения со смартфона.

### Доступные опции CLI

```bash
dropflow --host 0.0.0.0 --port 8000 --dir ./downloads --room general
```

- `--host`, `-h` — интерфейс привязки (по умолчанию `0.0.0.0`)
- `--port`, `-p` — порт (по умолчанию `8000`; при занятости автоматически выбирается следующий свободный)
- `--dir`, `-d` — директория для сохранения файлов (по умолчанию `downloads`)
- `--room`, `-r` — комната синхронизации по умолчанию (`general`)
- `--qr / --no-qr` — показывать или скрыть QR-код в терминале
- `--reload` — автоперезагрузка для разработки
- `--version`, `-v` — показать версию и выйти

---

## 🧪 Тестирование

Для запуска интеграционных тестов (загрузка, скачивание, удаление, health/info и WebSocket-события):

```bash
pytest -v
```

---

## 📁 Структура проекта

```text
drop-flow/
├── src/
│   ├── __init__.py
│   ├── cli.py          # Typer CLI и запуск Uvicorn
│   ├── clipboard.py    # Логика общего буфера обмена и дедупликация
│   ├── network.py      # Определение локального IP, портов и QR-баннер
│   ├── server.py       # FastAPI приложение, эндпоинты и WebSocket-менеджер
│   └── static/
│       └── index.html  # Single-page интерфейс в тёмной теме
├── tests/
│   └── test_flow.py    # Интеграционные тесты API и WebSocket
├── pyproject.toml
├── .gitignore
└── README.md
```
