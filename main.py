import os
import asyncio
import base64
import time
import json
from datetime import datetime
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton,
    WebAppInfo, MenuButtonWebApp
)

# --- КОНФИГУРАЦИЯ ---
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8858800582:AAGjBEdefs3UamNxGoT_L11Iym23gUslXlA")
ALLOWED_USER_ID = int(os.environ.get("ALLOWED_USER_ID", "6984578665"))
RAILWAY_URL = os.environ.get("RAILWAY_URL", "glazyebisha-production.up.railway.app")

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ─── ХРАНИЛИЩЕ ПК ─────────────────────────────────────────────

class PCInfo:
    def __init__(self, ws, info: dict):
        self.ws = ws
        self.id = info.get("id", "unknown")
        self.name = info.get("name", "Unknown PC")
        self.ip = info.get("ip", "?")
        self.os = info.get("os", "?")
        self.connected_at = datetime.now()
        self.last_seen = datetime.now()
        self.online = True

class PCManager:
    def __init__(self):
        self.pcs: dict[str, PCInfo] = {}
        self.ws_to_id: dict = {}
        self.command_log: list[dict] = []

    def connect(self, ws, info: dict) -> PCInfo:
        pc = PCInfo(ws, info)
        self.pcs[pc.id] = pc
        self.ws_to_id[id(ws)] = pc.id
        return pc

    def disconnect(self, ws):
        pc_id = self.ws_to_id.pop(id(ws), None)
        if pc_id and pc_id in self.pcs:
            self.pcs[pc_id].online = False

    def get_by_id(self, pc_id: str):
        return self.pcs.get(pc_id)

    def get_online(self):
        return [pc for pc in self.pcs.values() if pc.online]

    def get_all(self):
        return list(self.pcs.values())

    async def send(self, pc_id: str, message: dict) -> bool:
        pc = self.pcs.get(pc_id)
        if pc and pc.online:
            try:
                await pc.ws.send_json(message)
                return True
            except Exception:
                pc.online = False
        return False

    def log(self, pc_id: str, action: str):
        name = self.pcs[pc_id].name if pc_id in self.pcs else pc_id
        self.command_log.append({
            "time": datetime.now().strftime("%H:%M:%S"),
            "date": datetime.now().strftime("%d.%m.%Y"),
            "pc_id": pc_id,
            "pc_name": name,
            "action": action
        })
        if len(self.command_log) > 500:
            self.command_log = self.command_log[-500:]

pm = PCManager()

active_pc: dict[int, str] = {}
current_mouse_step: dict[int, int] = {}
stream_tasks: dict[int, asyncio.Task] = {}
stream_message_ids: dict[int, int] = {}
browser_clients: set = set()
browser_pc: dict = {}
last_frames: dict[str, bytes] = {}

def get_step(chat_id: int) -> int:
    return current_mouse_step.get(chat_id, 100)

def get_active(chat_id: int):
    pc_id = active_pc.get(chat_id)
    return pm.get_by_id(pc_id) if pc_id else None


# ─── MINI APP HTML ────────────────────────────────────────────

MINI_APP_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>Remote Control</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
:root {
  --bg: #0f0f0f;
  --bg2: #1a1a1a;
  --bg3: #242424;
  --border: #2a2a2a;
  --text: #e0e0e0;
  --text2: #888;
  --accent: #4f8ef7;
  --green: #4ade80;
  --red: #f87171;
  --yellow: #fbbf24;
}
* { margin:0; padding:0; box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
html,body { height:100%; overflow:hidden; background:var(--bg); color:var(--text); font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; }

/* ── TABS ── */
#tabs { display:flex; background:var(--bg2); border-bottom:1px solid var(--border); }
.tab { flex:1; padding:12px 6px; text-align:center; font-size:11px; color:var(--text2); cursor:pointer; border-bottom:2px solid transparent; transition:.2s; }
.tab.active { color:var(--accent); border-bottom-color:var(--accent); }
.tab-icon { font-size:18px; display:block; margin-bottom:2px; }

/* ── PAGES ── */
.page { display:none; height:calc(100vh - 46px); overflow-y:auto; }
.page.active { display:flex; flex-direction:column; }

/* ── PC LIST ── */
#page-pcs { padding:12px; gap:10px; }
.pc-card { background:var(--bg2); border:1px solid var(--border); border-radius:12px; padding:14px; display:flex; align-items:center; gap:12px; cursor:pointer; transition:.15s; }
.pc-card:active { background:var(--bg3); }
.pc-card.selected { border-color:var(--accent); background:#1a2a3a; }
.pc-dot { width:10px; height:10px; border-radius:50%; flex-shrink:0; }
.pc-dot.online { background:var(--green); box-shadow:0 0 6px var(--green); }
.pc-dot.offline { background:var(--red); }
.pc-info { flex:1; min-width:0; }
.pc-name { font-size:15px; font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.pc-meta { font-size:12px; color:var(--text2); margin-top:2px; }
.pc-live { background:var(--accent); color:#fff; border:none; border-radius:8px; padding:6px 12px; font-size:12px; cursor:pointer; flex-shrink:0; }
.empty { text-align:center; color:var(--text2); margin-top:60px; font-size:14px; }
.empty-icon { font-size:48px; margin-bottom:12px; }

/* ── SCREEN ── */
#page-screen { position:relative; }
#screen-container { flex:1; display:flex; align-items:center; justify-content:center; background:#000; overflow:hidden; position:relative; }
#screen-img { max-width:100%; max-height:100%; display:block; cursor:crosshair; }
#screen-placeholder { color:var(--text2); text-align:center; }
#screen-placeholder .icon { font-size:48px; margin-bottom:8px; }
#screen-bar { background:var(--bg2); border-top:1px solid var(--border); padding:8px; display:flex; gap:6px; align-items:center; flex-wrap:wrap; justify-content:center; flex-shrink:0; }
.s-btn { background:var(--bg3); border:1px solid var(--border); color:var(--text); border-radius:8px; padding:7px 11px; font-size:13px; cursor:pointer; }
.s-btn:active { background:#333; }
.s-btn.danger { border-color:#500; }
.s-btn.danger:active { background:#300; color:var(--red); }
#fps-badge { position:absolute; top:8px; right:8px; background:rgba(0,0,0,.6); color:var(--green); font-size:11px; padding:2px 6px; border-radius:4px; font-family:monospace; }
#conn-badge { position:absolute; top:8px; left:8px; background:rgba(0,0,0,.6); font-size:11px; padding:2px 6px; border-radius:4px; }

/* ── CONTROL ── */
#page-control { padding:12px; gap:10px; }
.ctrl-section { background:var(--bg2); border:1px solid var(--border); border-radius:12px; overflow:hidden; }
.ctrl-title { font-size:11px; color:var(--text2); padding:8px 12px 4px; text-transform:uppercase; letter-spacing:.5px; }
.grid { display:grid; gap:1px; background:var(--border); }
.grid-3 { grid-template-columns:repeat(3,1fr); }
.grid-4 { grid-template-columns:repeat(4,1fr); }
.grid-2 { grid-template-columns:repeat(2,1fr); }
.c-btn { background:var(--bg2); padding:12px 6px; text-align:center; font-size:13px; cursor:pointer; user-select:none; }
.c-btn:active { background:var(--bg3); }
.c-btn.big { font-size:20px; padding:14px; }
.c-btn.red { color:var(--red); }
.c-btn.green { color:var(--green); }
.step-row { display:flex; gap:1px; background:var(--border); }
.step-btn { flex:1; background:var(--bg2); padding:10px; text-align:center; font-size:12px; cursor:pointer; color:var(--text2); }
.step-btn.active { color:var(--accent); background:#1a2030; }
.step-btn:active { background:var(--bg3); }

/* ── TERMINAL ── */
#page-terminal { padding:12px; gap:10px; }
.term-box { background:#0a0a0a; border:1px solid var(--border); border-radius:12px; flex:1; overflow-y:auto; padding:10px; font-family:monospace; font-size:12px; min-height:200px; max-height:40vh; }
.term-line { line-height:1.7; }
.term-line.cmd { color:var(--accent); }
.term-line.out { color:var(--green); white-space:pre-wrap; word-break:break-all; }
.term-line.err { color:var(--red); }
.term-input-row { display:flex; gap:8px; }
.term-input { flex:1; background:var(--bg2); border:1px solid var(--border); color:var(--text); padding:10px 12px; border-radius:10px; font-size:14px; font-family:monospace; }
.term-input:focus { outline:none; border-color:var(--accent); }
.term-send { background:var(--accent); color:#fff; border:none; border-radius:10px; padding:10px 16px; font-size:14px; cursor:pointer; }
.quick-cmds { display:flex; gap:6px; flex-wrap:wrap; }
.q-btn { background:var(--bg2); border:1px solid var(--border); color:var(--text2); border-radius:6px; padding:5px 10px; font-size:12px; cursor:pointer; font-family:monospace; }
.q-btn:active { background:var(--bg3); }

/* ── INFO ── */
#page-info { padding:12px; gap:10px; }
.info-card { background:var(--bg2); border:1px solid var(--border); border-radius:12px; padding:14px; }
.info-title { font-size:13px; color:var(--text2); margin-bottom:10px; text-transform:uppercase; letter-spacing:.5px; }
.info-row { display:flex; justify-content:space-between; align-items:center; padding:6px 0; border-bottom:1px solid var(--border); }
.info-row:last-child { border-bottom:none; }
.info-label { color:var(--text2); font-size:13px; }
.info-val { font-size:13px; font-family:monospace; }
.bar { height:6px; border-radius:3px; background:var(--border); margin-top:4px; overflow:hidden; }
.bar-fill { height:100%; border-radius:3px; transition:.5s; }
.proc-row { display:flex; justify-content:space-between; font-size:12px; padding:4px 0; border-bottom:1px solid var(--border); font-family:monospace; }
.proc-row:last-child { border-bottom:none; }
.log-line { font-size:12px; color:var(--text2); padding:4px 0; border-bottom:1px solid var(--border); font-family:monospace; }
.log-line:last-child { border-bottom:none; }
.log-time { color:var(--accent); }

/* ── INPUT BAR ── */
#input-bar { background:var(--bg2); border-top:1px solid var(--border); padding:8px; display:flex; gap:6px; flex-shrink:0; }
#type-input { flex:1; background:var(--bg3); border:1px solid var(--border); color:var(--text); padding:9px 12px; border-radius:10px; font-size:14px; }
#type-input:focus { outline:none; border-color:var(--accent); }
#type-send { background:var(--accent); color:#fff; border:none; border-radius:10px; padding:9px 14px; font-size:14px; cursor:pointer; }

/* ── TOAST ── */
#toast { position:fixed; bottom:80px; left:50%; transform:translateX(-50%); background:rgba(0,0,0,.85); color:#fff; padding:8px 16px; border-radius:20px; font-size:13px; opacity:0; transition:.3s; pointer-events:none; white-space:nowrap; z-index:999; }
#toast.show { opacity:1; }
</style>
</head>
<body>

<div id="tabs">
  <div class="tab active" onclick="showTab('pcs')"><span class="tab-icon">🖥️</span>ПК</div>
  <div class="tab" onclick="showTab('screen')"><span class="tab-icon">📺</span>Экран</div>
  <div class="tab" onclick="showTab('control')"><span class="tab-icon">🎮</span>Управление</div>
  <div class="tab" onclick="showTab('terminal')"><span class="tab-icon">⌨️</span>Терминал</div>
  <div class="tab" onclick="showTab('info')"><span class="tab-icon">📊</span>Инфо</div>
</div>

<!-- ПК LIST -->
<div id="page-pcs" class="page active">
  <div id="pc-list-content" style="display:contents"></div>
</div>

<!-- ЭКРАН -->
<div id="page-screen" class="page">
  <div id="screen-container">
    <img id="screen-img" src="" style="display:none">
    <div id="screen-placeholder"><div class="icon">📺</div>Выбери ПК и нажми Скрин</div>
    <div id="fps-badge" style="display:none">-- FPS</div>
    <div id="conn-badge">⚫</div>
  </div>
  <div id="screen-bar">
    <button class="s-btn" onclick="doScreenshot()">📸 Скрин</button>
    <button class="s-btn" onclick="toggleStream()">📡 Стрим</button>
    <button class="s-btn" onclick="cmd({action:'scroll',direction:'up'})">🔼</button>
    <button class="s-btn" onclick="cmd({action:'scroll',direction:'down'})">🔽</button>
    <button class="s-btn" onclick="cmd({action:'hotkey',keys:['ctrl','c']})">📋</button>
    <button class="s-btn" onclick="cmd({action:'hotkey',keys:['ctrl','v']})">📌</button>
    <button class="s-btn" onclick="cmd({action:'key',key:'enter'})">↵</button>
    <button class="s-btn" onclick="cmd({action:'key',key:'esc'})">⎋</button>
    <button class="s-btn danger" onclick="confirmAction('lock',{action:'lock'})">🔒</button>
    <button class="s-btn danger" onclick="confirmAction('reboot',{action:'reboot'})">🔄</button>
    <button class="s-btn danger" onclick="confirmAction('shutdown',{action:'shutdown'})">⏻</button>
  </div>
  <div id="input-bar">
    <input id="type-input" type="text" placeholder="Введи текст → на ПК...">
    <button id="type-send" onclick="sendText()">⌨️</button>
  </div>
</div>

<!-- УПРАВЛЕНИЕ -->
<div id="page-control" class="page">
  <div class="ctrl-section">
    <div class="ctrl-title">🖱️ Мышь</div>
    <div class="grid grid-3">
      <div class="c-btn big" onclick="cmd({action:'control',data:'m_up_left_'+step})">↖️</div>
      <div class="c-btn big" onclick="cmd({action:'control',data:'m_up_'+step})">⬆️</div>
      <div class="c-btn big" onclick="cmd({action:'control',data:'m_up_right_'+step})">↗️</div>
      <div class="c-btn big" onclick="cmd({action:'control',data:'m_left_'+step})">⬅️</div>
      <div class="c-btn big" onclick="cmd({action:'control',data:'click_left',step:step})">🖱️</div>
      <div class="c-btn big" onclick="cmd({action:'control',data:'m_right_'+step})">➡️</div>
      <div class="c-btn big" onclick="cmd({action:'control',data:'m_down_left_'+step})">↙️</div>
      <div class="c-btn big" onclick="cmd({action:'control',data:'m_down_'+step})">⬇️</div>
      <div class="c-btn big" onclick="cmd({action:'control',data:'m_down_right_'+step})">↘️</div>
    </div>
    <div class="grid grid-3">
      <div class="c-btn" onclick="cmd({action:'control',data:'click_left'})">🖱️ ЛКМ</div>
      <div class="c-btn" onclick="cmd({action:'control',data:'click_right'})">🖱️ ПКМ</div>
      <div class="c-btn" onclick="cmd({action:'control',data:'click_double'})">2️⃣ Дабл</div>
    </div>
    <div class="grid grid-2">
      <div class="c-btn" onclick="cmd({action:'scroll',direction:'up'})">🔼 Скролл ↑</div>
      <div class="c-btn" onclick="cmd({action:'scroll',direction:'down'})">🔽 Скролл ↓</div>
    </div>
    <div class="step-row">
      <div class="step-btn active" id="step-50" onclick="setStep(50)">50px</div>
      <div class="step-btn active" id="step-100" onclick="setStep(100)">100px</div>
      <div class="step-btn" id="step-200" onclick="setStep(200)">200px</div>
      <div class="step-btn" id="step-500" onclick="setStep(500)">500px</div>
    </div>
  </div>

  <div class="ctrl-section">
    <div class="ctrl-title">⌨️ Клавиши</div>
    <div class="grid grid-4">
      <div class="c-btn" onclick="cmd({action:'key',key:'enter'})">↵ Enter</div>
      <div class="c-btn" onclick="cmd({action:'key',key:'esc'})">⎋ Esc</div>
      <div class="c-btn" onclick="cmd({action:'key',key:'tab'})">⇥ Tab</div>
      <div class="c-btn" onclick="cmd({action:'key',key:'backspace'})">⌫ Del</div>
      <div class="c-btn" onclick="cmd({action:'key',key:'up'})">↑</div>
      <div class="c-btn" onclick="cmd({action:'key',key:'down'})">↓</div>
      <div class="c-btn" onclick="cmd({action:'key',key:'left'})">←</div>
      <div class="c-btn" onclick="cmd({action:'key',key:'right'})">→</div>
      <div class="c-btn" onclick="cmd({action:'key',key:'f5'})">F5</div>
      <div class="c-btn" onclick="cmd({action:'key',key:'f11'})">F11</div>
      <div class="c-btn" onclick="cmd({action:'key',key:'printscreen'})">PrtSc</div>
      <div class="c-btn" onclick="cmd({action:'key',key:'delete'})">Delete</div>
    </div>
  </div>

  <div class="ctrl-section">
    <div class="ctrl-title">⚡ Комбинации</div>
    <div class="grid grid-3">
      <div class="c-btn" onclick="cmd({action:'hotkey',keys:['ctrl','c']})">📋 Копировать</div>
      <div class="c-btn" onclick="cmd({action:'hotkey',keys:['ctrl','v']})">📌 Вставить</div>
      <div class="c-btn" onclick="cmd({action:'hotkey',keys:['ctrl','z']})">↩️ Отмена</div>
      <div class="c-btn" onclick="cmd({action:'hotkey',keys:['ctrl','a']})">✅ Выделить</div>
      <div class="c-btn" onclick="cmd({action:'hotkey',keys:['ctrl','x']})">✂️ Вырезать</div>
      <div class="c-btn" onclick="cmd({action:'hotkey',keys:['ctrl','s']})">💾 Сохранить</div>
      <div class="c-btn" onclick="cmd({action:'hotkey',keys:['win']})">🏠 Win</div>
      <div class="c-btn" onclick="cmd({action:'hotkey',keys:['alt','tab']})">🗂️ Alt+Tab</div>
      <div class="c-btn" onclick="cmd({action:'hotkey',keys:['alt','f4']})">❌ Alt+F4</div>
      <div class="c-btn" onclick="cmd({action:'hotkey',keys:['win','d']})">🖥️ Рабочий стол</div>
      <div class="c-btn" onclick="cmd({action:'hotkey',keys:['ctrl','shift','esc']})">📋 Диспетчер</div>
      <div class="c-btn" onclick="cmd({action:'hotkey',keys:['win','l']})">🔒 Блок</div>
    </div>
  </div>

  <div class="ctrl-section">
    <div class="ctrl-title">🔴 Питание</div>
    <div class="grid grid-3">
      <div class="c-btn red" onclick="confirmAction('lock',{action:'lock'})">🔒 Блокировка</div>
      <div class="c-btn red" onclick="confirmAction('reboot',{action:'reboot'})">🔄 Перезагрузка</div>
      <div class="c-btn red" onclick="confirmAction('shutdown',{action:'shutdown'})">⏻ Выключение</div>
    </div>
  </div>

  <div class="ctrl-section">
    <div class="ctrl-title">🌐 Открыть URL</div>
    <div style="padding:10px;display:flex;gap:8px">
      <input id="url-input" type="url" placeholder="https://..." style="flex:1;background:var(--bg3);border:1px solid var(--border);color:var(--text);padding:8px 12px;border-radius:8px;font-size:14px">
      <button onclick="openUrl()" style="background:var(--accent);color:#fff;border:none;border-radius:8px;padding:8px 14px;font-size:14px;cursor:pointer">🌐</button>
    </div>
  </div>
</div>

<!-- ТЕРМИНАЛ -->
<div id="page-terminal" class="page">
  <div class="term-box" id="term-box">
    <div class="term-line err">Выбери ПК для начала работы</div>
  </div>
  <div class="quick-cmds">
    <div class="q-btn" onclick="runCmd('tasklist')">tasklist</div>
    <div class="q-btn" onclick="runCmd('ipconfig')">ipconfig</div>
    <div class="q-btn" onclick="runCmd('dir C:\\')">dir C:\</div>
    <div class="q-btn" onclick="runCmd('whoami')">whoami</div>
    <div class="q-btn" onclick="runCmd('systeminfo')">systeminfo</div>
    <div class="q-btn" onclick="runCmd('netstat -an')">netstat</div>
  </div>
  <div class="term-input-row">
    <input class="term-input" id="term-input" type="text" placeholder="команда...">
    <button class="term-send" onclick="sendCmd()">▶</button>
  </div>
</div>

<!-- ИНФО -->
<div id="page-info" class="page">
  <div class="info-card" id="status-card">
    <div class="info-title">💻 Статус ПК</div>
    <div class="info-row"><span class="info-label">CPU</span><span class="info-val" id="s-cpu">—</span></div>
    <div class="bar"><div class="bar-fill" id="b-cpu" style="width:0%;background:#f87171"></div></div>
    <div class="info-row"><span class="info-label">RAM</span><span class="info-val" id="s-ram">—</span></div>
    <div class="bar"><div class="bar-fill" id="b-ram" style="width:0%;background:#60a5fa"></div></div>
    <div class="info-row"><span class="info-label">Диск</span><span class="info-val" id="s-disk">—</span></div>
    <div class="bar"><div class="bar-fill" id="b-disk" style="width:0%;background:#a78bfa"></div></div>
    <div class="info-row"><span class="info-label">Uptime</span><span class="info-val" id="s-uptime">—</span></div>
    <div style="margin-top:10px">
      <button onclick="refreshStatus()" style="background:var(--accent);color:#fff;border:none;border-radius:8px;padding:8px 16px;font-size:13px;cursor:pointer;width:100%">🔄 Обновить</button>
    </div>
  </div>
  <div class="info-card">
    <div class="info-title">📋 Процессы (топ CPU)</div>
    <div id="proc-list"><div style="color:var(--text2);font-size:13px">Нажми обновить</div></div>
    <div style="margin-top:10px">
      <button onclick="refreshProcs()" style="background:var(--bg3);color:var(--text);border:1px solid var(--border);border-radius:8px;padding:8px 16px;font-size:13px;cursor:pointer;width:100%">🔄 Процессы</button>
    </div>
  </div>
  <div class="info-card">
    <div class="info-title">📜 Лог команд</div>
    <div id="log-list"><div style="color:var(--text2);font-size:13px">Пусто</div></div>
    <div style="margin-top:10px">
      <button onclick="refreshLog()" style="background:var(--bg3);color:var(--text);border:1px solid var(--border);border-radius:8px;padding:8px 16px;font-size:13px;cursor:pointer;width:100%">🔄 Лог</button>
    </div>
  </div>
</div>

<div id="toast"></div>

<script>
const tg = window.Telegram?.WebApp;
if(tg) { tg.ready(); tg.expand(); }

const BASE = location.origin;
let activePcId = null;
let step = 100;
let streaming = false;
let streamInterval = null;
let frameCount = 0, lastFpsTime = Date.now();
let liveWs = null;

// ── TABS ──
function showTab(name) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById('page-'+name).classList.add('active');
  const tabs = ['pcs','screen','control','terminal','info'];
  document.querySelectorAll('.tab')[tabs.indexOf(name)].classList.add('active');
  if(name==='pcs') loadPcs();
  if(name==='info') refreshStatus();
}

// ── TOAST ──
function toast(msg) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), 2000);
}

// ── PC LIST ──
async function loadPcs() {
  const res = await fetch(BASE+'/api/pcs');
  const data = await res.json();
  const container = document.getElementById('pc-list-content');
  if(!data.length) {
    container.innerHTML = '<div class="empty"><div class="empty-icon">🔌</div>Нет подключённых ПК.<br>Запусти aigency.py на компьютере.</div>';
    return;
  }
  container.innerHTML = data.map(pc => `
    <div class="pc-card ${activePcId===pc.id?'selected':''}" onclick="selectPc('${pc.id}','${pc.name}')">
      <div class="pc-dot ${pc.online?'online':'offline'}"></div>
      <div class="pc-info">
        <div class="pc-name">${pc.name}</div>
        <div class="pc-meta">${pc.ip} · ${pc.os}</div>
        <div class="pc-meta">${pc.online?'🟢 Онлайн':'🔴 Оффлайн'} · ${pc.connected_at}</div>
      </div>
      ${pc.online ? `<button class="pc-live" onclick="event.stopPropagation();openLive('${pc.id}')">Live</button>` : ''}
    </div>
  `).join('');
}

function selectPc(id, name) {
  activePcId = id;
  toast(`✅ Выбран: ${name}`);
  loadPcs();
  connectLiveWs(id);
}

function openLive(id) {
  window.open(BASE+'/live/'+id, '_blank');
}

// ── LIVE WS (для экрана) ──
function connectLiveWs(pcId) {
  if(liveWs) { liveWs.close(); liveWs=null; }
  const proto = location.protocol==='https:'?'wss':'ws';
  liveWs = new WebSocket(`${proto}://${location.host}/live-ws?pc=${pcId}`);
  liveWs.binaryType = 'arraybuffer';
  const badge = document.getElementById('conn-badge');
  liveWs.onopen = () => { badge.textContent='🟢'; badge.style.display='block'; };
  liveWs.onclose = () => { badge.textContent='🔴'; setTimeout(()=>connectLiveWs(pcId),3000); };
  liveWs.onmessage = (e) => {
    const blob = new Blob([e.data],{type:'image/jpeg'});
    const url = URL.createObjectURL(blob);
    const img = document.getElementById('screen-img');
    const old = img.src;
    img.onload = () => { if(old) URL.revokeObjectURL(old); };
    img.src = url;
    img.style.display = 'block';
    document.getElementById('screen-placeholder').style.display = 'none';
    document.getElementById('fps-badge').style.display = 'block';
    frameCount++;
  };
}

setInterval(()=>{
  const now = Date.now();
  const fps = (frameCount/((now-lastFpsTime)/1000)).toFixed(1);
  document.getElementById('fps-badge').textContent = fps+' FPS';
  frameCount=0; lastFpsTime=now;
},1000);

// Клик по экрану
document.getElementById('screen-img').addEventListener('click', (e) => {
  if(!activePcId) return;
  const img = e.target;
  const r = img.getBoundingClientRect();
  const sx = img.naturalWidth/r.width/0.5;
  const sy = img.naturalHeight/r.height/0.5;
  const x = Math.round((e.clientX-r.left)*sx);
  const y = Math.round((e.clientY-r.top)*sy);
  cmd({action:'click_abs',x,y});
});

document.getElementById('screen-img').addEventListener('contextmenu', (e) => {
  e.preventDefault();
  if(!activePcId) return;
  const img = e.target;
  const r = img.getBoundingClientRect();
  const sx = img.naturalWidth/r.width/0.5;
  const sy = img.naturalHeight/r.height/0.5;
  cmd({action:'click_abs_right',x:Math.round((e.clientX-r.left)*sx),y:Math.round((e.clientY-r.top)*sy)});
});

// ── КОМАНДЫ ──
async function cmd(data) {
  if(!activePcId) { toast('❌ Выбери ПК!'); return; }
  await fetch(BASE+'/live-cmd?pc='+activePcId, {
    method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)
  });
}

function doScreenshot() {
  if(!activePcId) { toast('❌ Выбери ПК!'); return; }
  // Один кадр через WS (уже идёт через live-ws)
  fetch(BASE+'/live-cmd?pc='+activePcId, {
    method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify({action:'screenshot_live'})
  });
  showTab('screen');
  toast('📸 Запрос скрина...');
}

function toggleStream() {
  if(!activePcId) { toast('❌ Выбери ПК!'); return; }
  streaming = !streaming;
  if(streaming) {
    // Подключаем live-ws если не подключён
    if(!liveWs || liveWs.readyState!==WebSocket.OPEN) connectLiveWs(activePcId);
    // Запрашиваем кадры
    streamInterval = setInterval(()=>{
      fetch(BASE+'/live-cmd?pc='+activePcId, {
        method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({action:'screenshot_live'})
      });
    }, 200);
    toast('📡 Стрим запущен');
  } else {
    clearInterval(streamInterval);
    toast('⏹️ Стрим остановлен');
  }
}

function sendText() {
  const t = document.getElementById('type-input').value.trim();
  if(!t) return;
  cmd({action:'type',text:t});
  document.getElementById('type-input').value='';
  toast('⌨️ Отправлено');
}

document.getElementById('type-input').addEventListener('keydown', e => {
  if(e.key==='Enter') sendText();
});

function setStep(s) {
  step = s;
  document.querySelectorAll('.step-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('step-'+s)?.classList.add('active');
  toast(`Шаг: ${s}px`);
}

function confirmAction(name, data) {
  const labels = {lock:'заблокировать экран',reboot:'перезагрузить ПК',shutdown:'выключить ПК'};
  if(confirm(`Ты уверен? Хочешь ${labels[name]}?`)) cmd(data);
}

function openUrl() {
  const url = document.getElementById('url-input').value.trim();
  if(!url) return;
  cmd({action:'open_url',url});
  document.getElementById('url-input').value='';
  toast('🌐 Открываю...');
}

// ── ТЕРМИНАЛ ──
function termLog(text, type='out') {
  const box = document.getElementById('term-box');
  const line = document.createElement('div');
  line.className = 'term-line '+type;
  line.textContent = text;
  box.appendChild(line);
  box.scrollTop = box.scrollHeight;
}

async function sendCmd() {
  const input = document.getElementById('term-input');
  const command = input.value.trim();
  if(!command) return;
  if(!activePcId) { toast('❌ Выбери ПК!'); return; }
  input.value = '';
  termLog('> '+command, 'cmd');
  await fetch(BASE+'/api/run', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify({pc_id:activePcId, command})
  });
  toast('⚙️ Выполняю...');
}

document.getElementById('term-input').addEventListener('keydown', e => {
  if(e.key==='Enter') sendCmd();
});

function runCmd(c) {
  document.getElementById('term-input').value = c;
  sendCmd();
}

// ── ИНФО ──
async function refreshStatus() {
  if(!activePcId) return;
  const res = await fetch(BASE+'/api/status/'+activePcId);
  if(!res.ok) return;
  const d = await res.json();
  document.getElementById('s-cpu').textContent = d.cpu+'%';
  document.getElementById('s-ram').textContent = d.ram+'% ('+d.ram_used+'/'+d.ram_total+' GB)';
  document.getElementById('s-disk').textContent = d.disk+'% ('+d.disk_used+'/'+d.disk_total+' GB)';
  document.getElementById('s-uptime').textContent = d.uptime;
  document.getElementById('b-cpu').style.width = d.cpu+'%';
  document.getElementById('b-ram').style.width = d.ram+'%';
  document.getElementById('b-disk').style.width = d.disk+'%';
}

async function refreshProcs() {
  if(!activePcId) { toast('❌ Выбери ПК!'); return; }
  const res = await fetch(BASE+'/api/processes/'+activePcId);
  if(!res.ok) return;
  const d = await res.json();
  document.getElementById('proc-list').innerHTML = d.list.slice(0,20).map(p=>
    `<div class="proc-row"><span>${p.name.substring(0,25)}</span><span style="color:var(--text2)">${p.cpu}% CPU · PID ${p.pid}</span></div>`
  ).join('');
}

async function refreshLog() {
  const res = await fetch(BASE+'/api/log');
  const d = await res.json();
  document.getElementById('log-list').innerHTML = d.log.slice(-15).reverse().map(l=>
    `<div class="log-line"><span class="log-time">${l.time}</span> ${l.pc_name}: ${l.action}</div>`
  ).join('') || '<div style="color:var(--text2);font-size:13px">Пусто</div>';
}

// ── POLLING для терминала и статуса ──
async function pollResults() {
  if(!activePcId) return;
  const res = await fetch(BASE+'/api/poll/'+activePcId);
  if(!res.ok) return;
  const d = await res.json();
  if(d.command_result) { termLog(d.command_result, 'out'); }
  if(d.status) {
    document.getElementById('s-cpu').textContent = d.status.cpu+'%';
    document.getElementById('s-ram').textContent = d.status.ram+'%';
    document.getElementById('b-cpu').style.width = d.status.cpu+'%';
    document.getElementById('b-ram').style.width = d.status.ram+'%';
    document.getElementById('b-disk').style.width = d.status.disk+'%';
    document.getElementById('s-uptime').textContent = d.status.uptime;
  }
}
setInterval(pollResults, 1500);

// Автообновление списка ПК
setInterval(()=>{ if(document.getElementById('page-pcs').classList.contains('active')) loadPcs(); }, 5000);

loadPcs();
</script>
</body>
</html>"""


# ─── API ENDPOINTS для Mini App ───────────────────────────────

poll_results: dict[str, dict] = {}  # pc_id -> latest result

@app.get("/api/pcs")
async def api_pcs():
    return [{
        "id": pc.id, "name": pc.name, "ip": pc.ip,
        "os": pc.os, "online": pc.online,
        "connected_at": pc.connected_at.strftime("%H:%M %d.%m")
    } for pc in pm.get_all()]

@app.get("/api/status/{pc_id}")
async def api_status(pc_id: str):
    result = poll_results.get(pc_id, {}).get("status")
    if not result:
        await pm.send(pc_id, {"action": "status", "chat_id": None})
        await asyncio.sleep(1.5)
        result = poll_results.get(pc_id, {}).get("status", {})
    return result or {}

@app.get("/api/processes/{pc_id}")
async def api_processes(pc_id: str):
    await pm.send(pc_id, {"action": "processes", "chat_id": None})
    await asyncio.sleep(1.5)
    procs = poll_results.get(pc_id, {}).get("processes", [])
    return {"list": procs}

@app.get("/api/log")
async def api_log():
    return {"log": pm.command_log[-50:]}

@app.get("/api/poll/{pc_id}")
async def api_poll(pc_id: str):
    result = poll_results.pop(pc_id, {})
    return result

@app.post("/api/run")
async def api_run(request: Request):
    data = await request.json()
    pc_id = data.get("pc_id")
    command = data.get("command", "")
    if pc_id and command:
        pm.log(pc_id, f"run: {command}")
        await pm.send(pc_id, {"action": "run", "command": command, "chat_id": None})
    return {"ok": True}


# ─── FASTAPI РОУТЫ ────────────────────────────────────────────

@app.on_event("startup")
async def on_startup():
    asyncio.create_task(dp.start_polling(bot))
    # Устанавливаем кнопку Mini App в боте
    asyncio.create_task(setup_webapp_button())

async def setup_webapp_button():
    await asyncio.sleep(3)
    try:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="🖥️ Remote Control",
                web_app=WebAppInfo(url=f"https://{RAILWAY_URL}/app")
            )
        )
        print("[Bot] WebApp button set!")
    except Exception as e:
        print(f"[Bot] WebApp button error: {e}")

@app.get("/")
async def index():
    online = pm.get_online()
    rows = "".join(
        f"<tr><td>{p.name}</td><td>{p.ip}</td><td>{p.os}</td>"
        f"<td>{'🟢' if p.online else '🔴'}</td>"
        f"<td><a href='/live/{p.id}'>Live</a></td></tr>"
        for p in pm.get_all()
    )
    return HTMLResponse(f"""<html><body style='font-family:monospace;background:#0a0a0a;color:#ccc;padding:20px'>
<h2>🖥️ Remote Control</h2><p>Online: {len(online)}</p>
<p><a href='/app' style='color:#4f8ef7'>📱 Mini App</a></p>
<table border=1 cellpadding=6 style='border-collapse:collapse;border-color:#333;margin-top:12px'>
<tr><th>Имя</th><th>IP</th><th>ОС</th><th>Статус</th><th>Live</th></tr>
{rows or '<tr><td colspan=5 style="color:#555">Нет ПК</td></tr>'}
</table></body></html>""")

@app.get("/app")
async def mini_app():
    return HTMLResponse(MINI_APP_HTML)

@app.get("/live/{pc_id}")
async def live_page(pc_id: str):
    from fastapi.responses import HTMLResponse as HR
    pc = pm.get_by_id(pc_id)
    name = pc.name if pc else pc_id
    return HR(build_live_html(pc_id, name))

@app.post("/live-cmd")
async def live_cmd(request: Request):
    pc_id = request.query_params.get("pc")
    data = await request.json()
    if pc_id:
        pm.log(pc_id, data.get("action","?"))
        await pm.send(pc_id, data)
    return {"ok": True}

@app.websocket("/live-ws")
async def live_ws(websocket: WebSocket):
    await websocket.accept()
    pc_id = websocket.query_params.get("pc")
    browser_clients.add(websocket)
    browser_pc[websocket] = pc_id
    if pc_id and pc_id in last_frames:
        try: await websocket.send_bytes(last_frames[pc_id])
        except: pass
    async def req_frames():
        while websocket in browser_clients:
            pc = pm.get_by_id(pc_id)
            if pc and pc.online:
                await pm.send(pc_id, {"action": "screenshot_live"})
            await asyncio.sleep(0.15)
    task = asyncio.create_task(req_frames())
    try: await websocket.receive_text()
    except: pass
    finally:
        browser_clients.discard(websocket)
        browser_pc.pop(websocket, None)
        task.cancel()

@app.websocket("/ws")
async def agent_ws(websocket: WebSocket):
    await websocket.accept()
    pc = None
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=10)
        info = json.loads(raw)
        if info.get("type") == "register":
            pc = pm.connect(websocket, info)
            print(f"[Server] PC: {pc.name} ({pc.ip})")
            try:
                await bot.send_message(ALLOWED_USER_ID,
                    f"🟢 *ПК подключился*\n💻 `{pc.name}`\n🌐 `{pc.ip}`\n🖥️ `{pc.os}`",
                    parse_mode="Markdown")
            except: pass

        while True:
            data = await websocket.receive_json()
            if pc: pc.last_seen = datetime.now()
            msg_type = data.get("type")
            pc_id = pc.id if pc else "unknown"

            if msg_type in ("screenshot", "screenshot_live"):
                image_bytes = base64.b64decode(data.get("image"))
                last_frames[pc_id] = image_bytes
                dead = set()
                for client in list(browser_clients):
                    if browser_pc.get(client) == pc_id:
                        try: await client.send_bytes(image_bytes)
                        except: dead.add(client)
                browser_clients -= dead
                for d in dead: browser_pc.pop(d, None)
                if msg_type == "screenshot":
                    chat_id = data.get("chat_id")
                    msg_id = data.get("message_id")
                    if chat_id:
                        photo = BufferedInputFile(image_bytes, filename="screen.jpg")
                        try:
                            if msg_id:
                                await bot.edit_message_media(
                                    media=types.InputMediaPhoto(media=photo),
                                    chat_id=chat_id, message_id=msg_id,
                                    reply_markup=get_control_keyboard(get_step(chat_id)))
                            else:
                                sent = await bot.send_photo(chat_id, photo,
                                    caption=f"📺 {pc.name if pc else ''}",
                                    reply_markup=get_control_keyboard(get_step(chat_id)))
                                stream_message_ids[chat_id] = sent.message_id
                        except Exception as e:
                            print(f"[TG] {e}")

            elif msg_type == "status":
                chat_id = data.get("chat_id")
                poll_results.setdefault(pc_id, {})["status"] = data
                if chat_id:
                    await bot.send_message(chat_id,
                        f"💻 *{pc.name if pc else 'ПК'}*\n"
                        f"CPU: `{data.get('cpu')}%` | RAM: `{data.get('ram')}%`\n"
                        f"Диск: `{data.get('disk')}%` | Up: `{data.get('uptime')}`",
                        parse_mode="Markdown")

            elif msg_type == "processes":
                poll_results.setdefault(pc_id, {})["processes"] = data.get("list", [])
                chat_id = data.get("chat_id")
                if chat_id:
                    procs = data.get("list", [])
                    text = f"📋 *Процессы*\n"
                    for p in procs[:25]:
                        text += f"`{p['pid']:>6}` {p['cpu']:>5}% {p['name']}\n"
                    await bot.send_message(chat_id, text, parse_mode="Markdown")

            elif msg_type == "command_result":
                output = data.get("output", "")
                poll_results.setdefault(pc_id, {})["command_result"] = output
                chat_id = data.get("chat_id")
                if chat_id:
                    await bot.send_message(chat_id, f"```\n{output[:3000]}\n```", parse_mode="Markdown")

            elif msg_type == "clipboard_content":
                chat_id = data.get("chat_id")
                if chat_id:
                    await bot.send_message(chat_id,
                        f"📋 `{data.get('text','(пусто)')}`", parse_mode="Markdown")

            elif msg_type == "file":
                chat_id = data.get("chat_id")
                if chat_id:
                    file_bytes = base64.b64decode(data.get("data"))
                    doc = BufferedInputFile(file_bytes, filename=data.get("filename","file"))
                    await bot.send_document(chat_id, doc)

    except WebSocketDisconnect: pass
    except Exception as e: print(f"[WS] {e}")
    finally:
        if pc:
            pm.disconnect(websocket)
            try:
                await bot.send_message(ALLOWED_USER_ID,
                    f"🔴 *ПК отключился*\n💻 `{pc.name}`", parse_mode="Markdown")
            except: pass


# ─── КЛАВИАТУРЫ ───────────────────────────────────────────────

def get_control_keyboard(mouse_step=100):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↖️",callback_data=f"m_up_left_{mouse_step}"),
         InlineKeyboardButton(text="⬆️",callback_data=f"m_up_{mouse_step}"),
         InlineKeyboardButton(text="↗️",callback_data=f"m_up_right_{mouse_step}")],
        [InlineKeyboardButton(text="⬅️",callback_data=f"m_left_{mouse_step}"),
         InlineKeyboardButton(text="🖱️ЛКМ",callback_data="click_left"),
         InlineKeyboardButton(text="➡️",callback_data=f"m_right_{mouse_step}")],
        [InlineKeyboardButton(text="↙️",callback_data=f"m_down_left_{mouse_step}"),
         InlineKeyboardButton(text="⬇️",callback_data=f"m_down_{mouse_step}"),
         InlineKeyboardButton(text="↘️",callback_data=f"m_down_right_{mouse_step}")],
        [InlineKeyboardButton(text="🖱️ПКМ",callback_data="click_right"),
         InlineKeyboardButton(text="2️⃣Дабл",callback_data="click_double"),
         InlineKeyboardButton(text="🔄Скрин",callback_data="refresh_screen")],
        [InlineKeyboardButton(text="🔼Скролл",callback_data="scroll_up"),
         InlineKeyboardButton(text="⌨️Enter",callback_data="key_enter"),
         InlineKeyboardButton(text="🔽Скролл",callback_data="scroll_down")],
        [InlineKeyboardButton(text="📋Копировать",callback_data="hotkey_copy"),
         InlineKeyboardButton(text="📌Вставить",callback_data="hotkey_paste"),
         InlineKeyboardButton(text="↩️Отмена",callback_data="hotkey_undo")],
        [InlineKeyboardButton(text="🏠Win",callback_data="hotkey_win"),
         InlineKeyboardButton(text="❌Alt+F4",callback_data="hotkey_altf4"),
         InlineKeyboardButton(text="🗂️Alt+Tab",callback_data="hotkey_alttab")],
        [InlineKeyboardButton(text="Шаг:50",callback_data="step_50"),
         InlineKeyboardButton(text="Шаг:200",callback_data="step_200"),
         InlineKeyboardButton(text="Шаг:500",callback_data="step_500")]
    ])

def get_main_menu():
    webapp_url = f"https://{RAILWAY_URL}/app"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Открыть Remote Control", web_app=WebAppInfo(url=webapp_url))],
        [InlineKeyboardButton(text="🖥️ Список ПК",callback_data="menu_pclist"),
         InlineKeyboardButton(text="💻 Статус",callback_data="menu_status")],
        [InlineKeyboardButton(text="🖼️ Скриншот",callback_data="menu_screenshot"),
         InlineKeyboardButton(text="🎮 Управление",callback_data="menu_control")],
        [InlineKeyboardButton(text="📡 Стрим Вкл",callback_data="stream_on"),
         InlineKeyboardButton(text="⏹️ Стрим Выкл",callback_data="stream_off")],
        [InlineKeyboardButton(text="📋 Буфер",callback_data="menu_clipboard"),
         InlineKeyboardButton(text="📊 Процессы",callback_data="menu_processes")],
        [InlineKeyboardButton(text="📜 Лог",callback_data="menu_log"),
         InlineKeyboardButton(text="🔴 Live",callback_data="menu_live")],
        [InlineKeyboardButton(text="🔒 Блок",callback_data="pc_lock"),
         InlineKeyboardButton(text="🔄 Ребут",callback_data="pc_reboot"),
         InlineKeyboardButton(text="⏻ Выкл",callback_data="pc_shutdown")]
    ])

def get_pc_list_keyboard():
    pcs = pm.get_all()
    if not pcs:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Нет ПК",callback_data="noop")]
        ])
    rows = []
    for pc in pcs:
        rows.append([InlineKeyboardButton(
            text=f"{'🟢' if pc.online else '🔴'} {pc.name} ({pc.ip})",
            callback_data=f"select_pc_{pc.id}"
        )])
    rows.append([InlineKeyboardButton(text="🔙 Назад",callback_data="menu_back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def build_live_html(pc_id, pc_name):
    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{pc_name} Live</title>
<style>*{{margin:0;padding:0;box-sizing:border-box}}body{{background:#000;display:flex;flex-direction:column;height:100vh;font-family:monospace;color:#fff}}
#h{{background:#111;padding:8px 12px;display:flex;align-items:center;gap:8px;border-bottom:1px solid #222;font-size:13px}}
#dot{{width:8px;height:8px;border-radius:50%;background:#f44}}#dot.on{{background:#4f8;box-shadow:0 0 5px #4f8}}
#fps{{margin-left:auto;color:#666;font-size:11px}}
#w{{flex:1;display:flex;align-items:center;justify-content:center;overflow:hidden}}
img{{max-width:100%;max-height:100%;cursor:crosshair}}
#btns{{background:#111;padding:8px;display:flex;flex-wrap:wrap;gap:5px;justify-content:center;border-top:1px solid #222}}
.b{{background:#1a1a1a;border:1px solid #2a2a2a;color:#ccc;padding:5px 10px;border-radius:4px;cursor:pointer;font-size:12px}}
.b:active{{background:#333}}.b.r{{border-color:#500}}.b.r:active{{background:#300;color:#f88}}
#inp{{background:#111;padding:7px;display:flex;gap:6px;border-top:1px solid #1a1a1a}}
#ti{{flex:1;background:#1a1a1a;border:1px solid #2a2a2a;color:#fff;padding:6px 10px;border-radius:4px;font-size:13px}}
</style></head><body>
<div id="h"><div id="dot"></div><span>{pc_name}</span><span id="fps"></span></div>
<div id="w"><img id="s" src="" alt=""></div>
<div id="btns">
<div class="b" onclick="c({{action:'control',data:'click_left'}})">🖱️ЛКМ</div>
<div class="b" onclick="c({{action:'control',data:'click_right'}})">🖱️ПКМ</div>
<div class="b" onclick="c({{action:'control',data:'click_double'}})">2️⃣</div>
<div class="b" onclick="c({{action:'scroll',direction:'up'}})">🔼</div>
<div class="b" onclick="c({{action:'scroll',direction:'down'}})">🔽</div>
<div class="b" onclick="c({{action:'hotkey',keys:['ctrl','c']}})">📋</div>
<div class="b" onclick="c({{action:'hotkey',keys:['ctrl','v']}})">📌</div>
<div class="b" onclick="c({{action:'key',key:'enter'}})">↵</div>
<div class="b" onclick="c({{action:'key',key:'esc'}})">⎋</div>
<div class="b" onclick="c({{action:'hotkey',keys:['win']}})">🏠</div>
<div class="b" onclick="c({{action:'hotkey',keys:['alt','tab']}})">🗂️</div>
<div class="b r" onclick="if(confirm('Блок?'))c({{action:'lock'}})">🔒</div>
<div class="b r" onclick="if(confirm('Ребут?'))c({{action:'reboot'}})">🔄</div>
<div class="b r" onclick="if(confirm('Выкл?'))c({{action:'shutdown'}})">⏻</div>
</div>
<div id="inp"><input id="ti" type="text" placeholder="Текст → Enter...">
<div class="b" onclick="st()">⌨️</div>
<div class="b" onclick="ou()">🌐</div></div>
<script>
const img=document.getElementById('s'),dot=document.getElementById('dot'),fpsEl=document.getElementById('fps');
let ws,fc=0,lt=Date.now();
function conn(){{const p=location.protocol==='https:'?'wss':'ws';
ws=new WebSocket(p+'://'+location.host+'/live-ws?pc={pc_id}');
ws.binaryType='arraybuffer';
ws.onopen=()=>{{dot.className='on'}};
ws.onclose=()=>{{dot.className='';setTimeout(conn,2000)}};
ws.onmessage=(e)=>{{const b=new Blob([e.data],{{type:'image/jpeg'}});const u=URL.createObjectURL(b);const o=img.src;img.onload=()=>{{if(o)URL.revokeObjectURL(o)}};img.src=u;img.style.display='block';fc++}};}}
setInterval(()=>{{const n=Date.now();fpsEl.textContent=(fc/((n-lt)/1000)).toFixed(1)+' FPS';fc=0;lt=n}},1000);
img.addEventListener('click',(e)=>{{const r=img.getBoundingClientRect();const sx=img.naturalWidth/r.width/0.5;const sy=img.naturalHeight/r.height/0.5;c({{action:'click_abs',x:Math.round((e.clientX-r.left)*sx),y:Math.round((e.clientY-r.top)*sy)}})}});
img.addEventListener('contextmenu',(e)=>{{e.preventDefault();const r=img.getBoundingClientRect();c({{action:'click_abs_right',x:Math.round((e.clientX-r.left)*(img.naturalWidth/r.width/0.5)),y:Math.round((e.clientY-r.top)*(img.naturalHeight/r.height/0.5))}})}});
img.addEventListener('wheel',(e)=>{{e.preventDefault();c({{action:'scroll',direction:e.deltaY<0?'up':'down'}})}},{{passive:false}});
document.addEventListener('keydown',(e)=>{{if(document.activeElement===document.getElementById('ti'))return;e.preventDefault();const km={{Enter:'enter',Escape:'esc',Backspace:'backspace',Delete:'delete',Tab:'tab',ArrowUp:'up',ArrowDown:'down',ArrowLeft:'left',ArrowRight:'right',F5:'f5',F11:'f11'}};if(e.ctrlKey&&e.key!=='Control')c({{action:'hotkey',keys:['ctrl',e.key.toLowerCase()]}});else if(km[e.key])c({{action:'key',key:km[e.key]}});else if(e.key.length===1)c({{action:'type',text:e.key}})}});
function c(d){{fetch('/live-cmd?pc={pc_id}',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(d)}})}}
function st(){{const t=document.getElementById('ti').value.trim();if(t){{c({{action:'type',text:t}});document.getElementById('ti').value=''}}}}
function ou(){{const u=prompt('URL:');if(u)c({{action:'open_url',url:u}})}}
document.getElementById('ti').addEventListener('keydown',e=>{{if(e.key==='Enter')st()}});
conn();
</script></body></html>"""


# ─── TELEGRAM КОМАНДЫ ─────────────────────────────────────────

def check(uid): return uid == ALLOWED_USER_ID

async def require_pc(message: types.Message):
    pc = get_active(message.chat.id)
    if not pc:
        await message.answer("❌ Выбери ПК через /pcs", reply_markup=get_pc_list_keyboard())
        return None
    if not pc.online:
        await message.answer(f"❌ `{pc.name}` оффлайн", parse_mode="Markdown")
        return None
    return pc

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if not check(message.from_user.id): return
    await message.answer(
        "🖥️ *Remote Control*\n\n"
        "Нажми кнопку ниже чтобы открыть приложение 👇\n\n"
        "Или используй команды:\n"
        "/pcs — список ПК\n/screen — скриншот\n/live — прямой эфир\n"
        "/status — статус\n/run <cmd> — команда\n/log — лог",
        parse_mode="Markdown", reply_markup=get_main_menu())

@dp.message(Command("pcs"))
async def cmd_pcs(message: types.Message):
    if not check(message.from_user.id): return
    await message.answer("🖥️ *Список ПК:*", parse_mode="Markdown", reply_markup=get_pc_list_keyboard())

@dp.message(Command("screen"))
async def cmd_screen(message: types.Message):
    if not check(message.from_user.id): return
    pc = await require_pc(message)
    if pc: await pm.send(pc.id, {"action":"screenshot","chat_id":message.chat.id,"message_id":None})

@dp.message(Command("live"))
async def cmd_live(message: types.Message):
    if not check(message.from_user.id): return
    pc = get_active(message.chat.id)
    if not pc: await message.answer("❌ Выбери ПК /pcs"); return
    await message.answer(f"🔴 *Live:* https://{RAILWAY_URL}/live/{pc.id}", parse_mode="Markdown")

@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    if not check(message.from_user.id): return
    pc = await require_pc(message)
    if pc: await pm.send(pc.id, {"action":"status","chat_id":message.chat.id})

@dp.message(Command("run"))
async def cmd_run(message: types.Message):
    if not check(message.from_user.id): return
    pc = await require_pc(message)
    if not pc: return
    c = message.text.replace("/run","",1).strip()
    if not c: await message.answer("Пример: /run tasklist"); return
    pm.log(pc.id, f"run: {c}")
    await pm.send(pc.id, {"action":"run","command":c,"chat_id":message.chat.id})

@dp.message(Command("log"))
async def cmd_log(message: types.Message):
    if not check(message.from_user.id): return
    logs = pm.command_log[-15:]
    if not logs: await message.answer("Лог пуст"); return
    text = "📜 *Лог:*\n"
    for l in reversed(logs):
        text += f"`{l['time']}` {l['pc_name']}: {l['action']}\n"
    await message.answer(text, parse_mode="Markdown")

@dp.message(Command("stream"))
async def cmd_stream(message: types.Message):
    if not check(message.from_user.id): return
    pc = await require_pc(message)
    if not pc: return
    chat_id = message.chat.id
    if chat_id in stream_tasks and not stream_tasks[chat_id].done():
        stream_tasks[chat_id].cancel()
    async def loop():
        while True:
            await pm.send(pc.id, {"action":"screenshot","chat_id":chat_id,"message_id":stream_message_ids.get(chat_id)})
            await asyncio.sleep(3)
    stream_tasks[chat_id] = asyncio.create_task(loop())
    await message.answer("📡 Стрим запущен")

@dp.message(Command("stopstream"))
async def cmd_stopstream(message: types.Message):
    if not check(message.from_user.id): return
    chat_id = message.chat.id
    if chat_id in stream_tasks and not stream_tasks[chat_id].done():
        stream_tasks[chat_id].cancel()
        await message.answer("⏹️ Остановлен")

@dp.message()
async def auto_type(message: types.Message):
    if not check(message.from_user.id): return
    if not message.text or message.text.startswith("/"): return
    pc = get_active(message.chat.id)
    if not pc or not pc.online: await message.answer("❌ Выбери ПК /pcs"); return
    await pm.send(pc.id, {"action":"type","text":message.text})
    await message.reply("⌨️ Введено")

@dp.callback_query()
async def callbacks(cb: types.CallbackQuery):
    if not check(cb.from_user.id): return
    data = cb.data
    chat_id = cb.message.chat.id
    msg_id = cb.message.message_id

    if data.startswith("select_pc_"):
        pc_id = data.replace("select_pc_","")
        pc = pm.get_by_id(pc_id)
        if not pc: await cb.answer("Не найден",show_alert=True); return
        active_pc[chat_id] = pc_id
        await cb.message.edit_text(
            f"✅ *{pc.name}*\n🌐 `{pc.ip}` · `{pc.os}`",
            parse_mode="Markdown", reply_markup=get_main_menu())
        await cb.answer(f"✅ {pc.name}")
        return

    if data == "noop": await cb.answer(); return
    if data == "menu_pclist":
        await cb.message.edit_text("🖥️ *Список ПК:*", parse_mode="Markdown", reply_markup=get_pc_list_keyboard())
        await cb.answer(); return
    if data == "menu_back":
        await cb.message.edit_text("🖥️ *Remote Control*", parse_mode="Markdown", reply_markup=get_main_menu())
        await cb.answer(); return
    if data == "menu_log":
        logs = pm.command_log[-10:]
        text = "📜 *Лог:*\n" + "".join(f"`{l['time']}` {l['pc_name']}: {l['action']}\n" for l in reversed(logs))
        await cb.message.answer(text or "Пусто", parse_mode="Markdown")
        await cb.answer(); return

    pc = get_active(chat_id)
    if not pc: await cb.answer("❌ Выбери ПК", show_alert=True); return
    if not pc.online: await cb.answer(f"❌ {pc.name} оффлайн", show_alert=True); return

    if data == "menu_screenshot":
        await pm.send(pc.id,{"action":"screenshot","chat_id":chat_id,"message_id":None})
        await cb.answer("📸"); return
    if data == "menu_control":
        await cb.message.answer("🎮", reply_markup=get_control_keyboard(get_step(chat_id)))
        await cb.answer(); return
    if data == "menu_status":
        await pm.send(pc.id,{"action":"status","chat_id":chat_id}); await cb.answer("📊"); return
    if data == "menu_clipboard":
        await pm.send(pc.id,{"action":"get_clipboard","chat_id":chat_id}); await cb.answer("📋"); return
    if data == "menu_processes":
        await pm.send(pc.id,{"action":"processes","chat_id":chat_id}); await cb.answer("📋"); return
    if data == "menu_live":
        await cb.message.answer(f"🔴 https://{RAILWAY_URL}/live/{pc.id}"); await cb.answer(); return
    if data == "pc_lock":
        await pm.send(pc.id,{"action":"lock"}); await cb.answer("🔒"); return
    if data == "pc_reboot":
        await pm.send(pc.id,{"action":"reboot"}); await cb.answer("🔄"); return
    if data == "pc_shutdown":
        await pm.send(pc.id,{"action":"shutdown"}); await cb.answer("⏻"); return
    if data == "stream_on":
        if chat_id in stream_tasks and not stream_tasks[chat_id].done():
            stream_tasks[chat_id].cancel()
        async def loop():
            while True:
                await pm.send(pc.id,{"action":"screenshot","chat_id":chat_id,"message_id":stream_message_ids.get(chat_id)})
                await asyncio.sleep(3)
        stream_tasks[chat_id] = asyncio.create_task(loop())
        await cb.answer("📡 Стрим"); return
    if data == "stream_off":
        if chat_id in stream_tasks and not stream_tasks[chat_id].done():
            stream_tasks[chat_id].cancel()
        await cb.answer("⏹️"); return
    if data.startswith("step_"):
        s = int(data.split("_")[1])
        current_mouse_step[chat_id] = s
        await cb.message.edit_reply_markup(reply_markup=get_control_keyboard(s))
        await cb.answer(f"Шаг: {s}px"); return
    hk = {"hotkey_copy":["ctrl","c"],"hotkey_paste":["ctrl","v"],"hotkey_undo":["ctrl","z"],
          "hotkey_win":["win"],"hotkey_altf4":["alt","f4"],"hotkey_alttab":["alt","tab"]}
    if data in hk:
        await pm.send(pc.id,{"action":"hotkey","keys":hk[data],"chat_id":chat_id,"message_id":msg_id})
        await cb.answer("✅"); return
    if data == "scroll_up":
        await pm.send(pc.id,{"action":"scroll","direction":"up","chat_id":chat_id,"message_id":msg_id})
        await cb.answer("🔼"); return
    if data == "scroll_down":
        await pm.send(pc.id,{"action":"scroll","direction":"down","chat_id":chat_id,"message_id":msg_id})
        await cb.answer("🔽"); return
    step = get_step(chat_id)
    await pm.send(pc.id,{"action":"control","data":data,"step":step,"chat_id":chat_id,"message_id":msg_id})
    await cb.answer("✅")
