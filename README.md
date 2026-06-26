# 🖥️ Remote Control — Telegram Bot

Система удалённого управления ПК через Telegram бот и браузер.  
Состоит из двух частей: **сервер** (Railway) и **агент** (запускается на ПК).

---

## 📁 Структура проекта

```
├── main.py                # Сервер (FastAPI + Telegram бот)
├── aigency.py             # Агент для Windows/Linux/macOS
├── requirements.txt       # Зависимости сервера
├── requirements_agent.txt # Зависимости агента
├── Procfile               # Команда запуска для Railway
└── .pc_id                 # Уникальный ID ПК (создаётся автоматически)
```

---

## 🚀 Установка сервера (Railway)

### 1. Создай бота
- Открой [@BotFather](https://t.me/BotFather) в Telegram
- Напиши `/newbot` и следуй инструкциям
- Сохрани токен бота

### 2. Узнай свой Telegram ID
- Напиши [@userinfobot](https://t.me/userinfobot)
- Сохрани свой числовой ID

### 3. Деплой на Railway
1. Зайди на [railway.app](https://railway.app)
2. Создай новый проект → Deploy from GitHub
3. Залей файлы: `main.py`, `requirements.txt`, `Procfile`
4. Перейди в **Variables** и добавь:

| Переменная | Значение |
|---|---|
| `BOT_TOKEN` | токен от BotFather |
| `ALLOWED_USER_ID` | твой Telegram ID |
| `RAILWAY_URL` | твой домен (например `myapp.up.railway.app`) |

5. Задеплой — сервер запустится автоматически

---

## 💻 Установка агента на ПК

### Вариант A — Запуск через Python

```bash
# Установи зависимости
pip install websockets pillow pyautogui pyperclip psutil

# Запусти агента
python aigency.py
```

### Вариант B — Сборка в EXE (Windows)

```bash
# Установи PyInstaller
pip install pyinstaller

# Собери exe
pyinstaller --onefile --noconsole ^
  --hidden-import PIL ^
  --hidden-import PIL.ImageGrab ^
  --hidden-import pyautogui ^
  --hidden-import psutil ^
  --hidden-import pyperclip ^
  --hidden-import websockets ^
  --name RemoteAgent aigency.py

# Готовый файл: dist/RemoteAgent.exe
```

Запусти `RemoteAgent.exe` **один раз от администратора** — он сам пропишется в автозагрузку Windows.

### Автозагрузка
Агент автоматически добавляет себя в реестр при первом запуске:
```
HKCU\Software\Microsoft\Windows\CurrentVersion\Run → RemoteAgent
```
После этого запускается при каждом входе в Windows без каких-либо действий.

---

## 🌐 Веб-интерфейс

| Страница | Описание |
|---|---|
| `/` | Список всех подключённых ПК |
| `/live/{pc_id}` | Прямой эфир конкретного ПК |

На странице `/live/{pc_id}`:
- 🖱️ **Клик** по экрану — двигает мышь на точные координаты
- 🖱️ **Правый клик** — ПКМ
- 🖱️ **Колёсико** — скролл
- ⌨️ **Клавиатура** работает напрямую (Ctrl+C/V, Alt+Tab, F5 и др.)
- 📋 Лог всех действий в реальном времени
- FPS счётчик

---

## 🤖 Команды Telegram бота

### 📌 Основные

| Команда | Описание |
|---|---|
| `/start` | Главное меню со всеми кнопками |
| `/pcs` | Список всех ПК (онлайн и оффлайн) |
| `/live` | Ссылка на прямой эфир активного ПК |

### 🖥️ Экран

| Команда | Описание |
|---|---|
| `/screen` | Сделать скриншот активного ПК |
| `/stream` | Авто-скриншот каждые 3 секунды в Telegram |
| `/stopstream` | Остановить авто-стрим |

### 💻 Информация о ПК

| Команда | Описание |
|---|---|
| `/status` | CPU, RAM, диск, время работы |
| `/ps` | Топ процессов по загрузке CPU |
| `/kill <pid>` | Завершить процесс по PID |

**Пример:**
```
/kill 1234
```

### ⌨️ Управление

| Команда | Описание |
|---|---|
| `/key <клавиша>` | Нажать одну клавишу |
| `/hotkey <k1> <k2> ...` | Нажать комбинацию клавиш |
| Любой текст | Автоматически вводится на ПК |

**Примеры:**
```
/key esc
/key f5
/key tab
/key delete
/hotkey ctrl c
/hotkey ctrl alt delete
/hotkey alt f4
/hotkey win d
```

**Доступные клавиши:** `enter`, `esc`, `tab`, `backspace`, `delete`, `space`, `up`, `down`, `left`, `right`, `home`, `end`, `pageup`, `pagedown`, `f1`–`f12`, `win`, `alt`, `ctrl`, `shift`, `printscreen`, `insert`

### 🖱️ Кнопки управления мышью

После команды `/screen` или в меню **🎮 Управление** появляется клавиатура:

```
↖️  ⬆️  ↗️
⬅️ 🖱️ЛКМ ➡️
↙️  ⬇️  ↘️
🖱️ПКМ  2️⃣Дабл  🔄Скрин
🔼Скролл  ⌨️Enter  🔽Скролл
📋Копировать  📌Вставить  ↩️Отмена
🏠Win  ❌Alt+F4  🗂️Alt+Tab
Шаг:50  Шаг:200  Шаг:500
```

### 📂 Файлы

| Команда | Описание |
|---|---|
| `/file <путь>` | Скачать файл с ПК в Telegram |

**Примеры:**
```
/file C:\Users\user\Documents\doc.txt
/file C:\Users\user\Desktop\photo.jpg
/file /home/user/file.txt
```

### 📋 Буфер обмена

| Команда | Описание |
|---|---|
| `/clip` | Прочитать содержимое буфера обмена |
| `/setclip <текст>` | Записать текст в буфер обмена |

**Пример:**
```
/setclip Привет мир!
```

### 🌐 Браузер

| Команда | Описание |
|---|---|
| `/url <ссылка>` | Открыть URL в браузере на ПК |

**Пример:**
```
/url https://google.com
/url https://youtube.com/watch?v=dQw4w9WgXcQ
```

### ⚙️ Команды CMD / PowerShell

| Команда | Описание |
|---|---|
| `/run <команда>` | Выполнить команду в терминале |

**Примеры:**
```
/run tasklist
/run ipconfig
/run dir C:\Users
/run shutdown /a
/run powershell Get-Process
/run echo Hello World
```

### 🔒 Питание и блокировка

| Команда | Описание |
|---|---|
| `/lock` | Заблокировать экран ПК |
| `/reboot` | Перезагрузить ПК (через 5 сек) |
| `/shutdown` | Выключить ПК (через 5 сек) |

> ⚠️ Перезагрузка и выключение выполняются через 5 секунд после команды — успеешь отменить через `/run shutdown /a`

### 📜 Лог

| Команда | Описание |
|---|---|
| `/log` | Последние 20 выполненных команд с временем |

---

## 🔔 Автоматические уведомления

Бот сам присылает сообщения когда:
- 🟢 ПК **подключился** к серверу (имя, IP, ОС)
- 🔴 ПК **отключился** от сервера

---

## 🔧 Главное меню (кнопки)

```
🖥️ Список ПК     💻 Статус
🖼️ Скриншот      🎮 Управление
📡 Стрим Вкл     ⏹️ Стрим Выкл
📋 Буфер         📊 Процессы
📜 Лог команд    🔴 Live
🔒 Блокировка    🔄 Ребут    ⏻ Выкл
```

---

## ⚡ Быстрый старт

```
1. Задеплой main.py на Railway
2. Добавь переменные BOT_TOKEN, ALLOWED_USER_ID, RAILWAY_URL
3. Запусти aigency.py на ПК
4. Напиши /start боту
5. Нажми "🖥️ Список ПК" → выбери свой ПК
6. Готово!
```

---

## 🛠️ Технологии

- **Сервер:** Python, FastAPI, aiogram 3, WebSocket
- **Агент:** Python, pyautogui, Pillow, psutil, websockets
- **Хостинг:** Railway
- **Протокол:** WebSocket (wss://)
