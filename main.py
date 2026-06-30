import os
import asyncio
import base64
import json
import requests
from datetime import datetime
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo, MenuButtonWebApp
)

# ─── КОНФИГУРАЦИЯ ─────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8858800582:AAGjBEdefs3UamNxGoT_L11Iym23gUslXlA")
ADMIN_ID = int(os.environ.get("ALLOWED_USER_ID", "6984578665"))
RAILWAY_URL = os.environ.get("RAILWAY_URL", "glazyebisha-production.up.railway.app")
JSONBIN_ID = os.environ.get("JSONBIN_ID", "6a438e2bf5f4af5e29468615")
JSONBIN_KEY = os.environ.get("JSONBIN_KEY", "$2a$10$8dtrM.qgCnu6DfWsHK370eDeqQMfPJqF5D583ERUDVENghX5j1/gW")
JSONBIN_URL = f"https://api.jsonbin.io/v3/b/{JSONBIN_ID}"

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ─── РОЛИ ─────────────────────────────────────────────────────
# admin   — всё
# operator — управление, но без питания/пользователей
# viewer  — только скрины и стрим

ROLES = {"admin": "👑 Admin", "operator": "🔧 Operator", "viewer": "👁️ Viewer"}

ROLE_CAN = {
    "admin":    {"screen","stream","control","terminal","status","clipboard","processes","lock","reboot","shutdown","users","live","file"},
    "operator": {"screen","stream","control","terminal","status","clipboard","processes","live","file"},
    "viewer":   {"screen","stream","status","live"},
}

# users: {str(user_id): {"role": "...", "name": "..."}}
# Хранится в JSONBin.io — не теряется при передеплое на Railway

def load_users() -> dict:
    try:
        headers = {"X-Master-Key": JSONBIN_KEY}
        r = requests.get(f"{JSONBIN_URL}/latest", headers=headers, timeout=5)
        if r.status_code == 200:
            data = r.json().get("record", {})
            if isinstance(data, dict):
                return data.get("users", data) if "users" in data else data
        return {}
    except Exception as e:
        print(f"[JSONBin] Load error: {e}")
        return {}

def save_users(users: dict):
    try:
        headers = {"X-Master-Key": JSONBIN_KEY, "Content-Type": "application/json"}
        requests.put(JSONBIN_URL, json={"users": users}, headers=headers, timeout=5)
    except Exception as e:
        print(f"[JSONBin] Save error: {e}")

users_db: dict = load_users()

def get_role(user_id: int) -> str:
    if user_id == ADMIN_ID:
        return "admin"
    return users_db.get(str(user_id), {}).get("role", "")

def can(user_id: int, action: str) -> bool:
    role = get_role(user_id)
    return action in ROLE_CAN.get(role, set())

def is_allowed(user_id: int) -> bool:
    return get_role(user_id) != ""

# ─── PC MANAGER ───────────────────────────────────────────────
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
        self.stats = {}  # последний статус

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

    def get_all(self):
        return list(self.pcs.values())

    def get_online(self):
        return [p for p in self.pcs.values() if p.online]

    async def send(self, pc_id: str, message: dict) -> bool:
        pc = self.pcs.get(pc_id)
        if pc and pc.online:
            try:
                await pc.ws.send_json(message)
                return True
            except Exception:
                pc.online = False
        return False

    def log(self, pc_id: str, user_id: int, action: str):
        name = self.pcs[pc_id].name if pc_id in self.pcs else pc_id
        role = get_role(user_id)
        self.command_log.append({
            "time": datetime.now().strftime("%H:%M:%S"),
            "date": datetime.now().strftime("%d.%m.%Y"),
            "pc_id": pc_id, "pc_name": name,
            "user_id": user_id, "role": role, "action": action
        })
        if len(self.command_log) > 500:
            self.command_log = self.command_log[-500:]

pm = PCManager()

active_pc: dict[int, str] = {}
mouse_step: dict[int, int] = {}
stream_tasks: dict[int, asyncio.Task] = {}
stream_msg_ids: dict[int, int] = {}
browser_clients: set = set()
browser_pc: dict = {}
last_frames: dict[str, bytes] = {}
poll_results: dict[str, dict] = {}
pending_adduser: dict[int, int] = {}  # admin_chat_id -> target_user_id

def get_step(chat_id): return mouse_step.get(chat_id, 100)
def get_active(chat_id):
    pc_id = active_pc.get(chat_id)
    return pm.get_by_id(pc_id) if pc_id else None

# ─── MINI APP ─────────────────────────────────────────────────
MINI_APP = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>Remote Control</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
:root{--bg:#0f0f0f;--bg2:#1a1a1a;--bg3:#242424;--border:#2a2a2a;--text:#e0e0e0;--text2:#888;--accent:#4f8ef7;--green:#4ade80;--red:#f87171;--yellow:#fbbf24}
*{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{height:100%;overflow:hidden;background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}
#tabs{display:flex;background:var(--bg2);border-bottom:1px solid var(--border)}
.tab{flex:1;padding:10px 4px;text-align:center;font-size:10px;color:var(--text2);cursor:pointer;border-bottom:2px solid transparent}
.tab.active{color:var(--accent);border-bottom-color:var(--accent)}
.tab-icon{font-size:17px;display:block;margin-bottom:2px}
.page{display:none;height:calc(100vh - 44px);overflow-y:auto;flex-direction:column}
.page.active{display:flex}

/* PC LIST */
#page-pcs{padding:10px;gap:8px}
.pc-card{background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:12px;display:flex;align-items:center;gap:10px;cursor:pointer}
.pc-card.selected{border-color:var(--accent);background:#1a2a3a}
.pc-dot{width:9px;height:9px;border-radius:50%;flex-shrink:0}
.pc-dot.on{background:var(--green);box-shadow:0 0 5px var(--green)}
.pc-dot.off{background:var(--red)}
.pc-info{flex:1;min-width:0}
.pc-name{font-size:14px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.pc-meta{font-size:11px;color:var(--text2);margin-top:2px}
.live-btn{background:var(--accent);color:#fff;border:none;border-radius:8px;padding:5px 10px;font-size:11px;cursor:pointer;flex-shrink:0}
.empty{text-align:center;color:var(--text2);margin-top:50px;font-size:13px}
.empty-icon{font-size:42px;margin-bottom:10px}

/* SCREEN */
#page-screen{position:relative}
#screen-wrap{flex:1;display:flex;align-items:center;justify-content:center;background:#000;position:relative;overflow:hidden;min-height:0}
#screen-img{max-width:100%;max-height:100%;display:none;touch-action:none}
#screen-ph{color:var(--text2);text-align:center;font-size:13px}
#screen-ph .i{font-size:40px;margin-bottom:8px}
#fps-b{position:absolute;top:6px;right:6px;background:rgba(0,0,0,.7);color:var(--green);font-size:10px;padding:2px 6px;border-radius:4px;font-family:monospace}
#conn-b{position:absolute;top:6px;left:6px;background:rgba(0,0,0,.7);font-size:10px;padding:2px 6px;border-radius:4px}
#drag-indicator{position:absolute;width:20px;height:20px;border-radius:50%;background:rgba(79,142,247,.6);pointer-events:none;display:none;transform:translate(-50%,-50%)}
#screen-btns{background:var(--bg2);border-top:1px solid var(--border);padding:7px;display:flex;gap:5px;flex-wrap:wrap;justify-content:center;flex-shrink:0}
.s-btn{background:var(--bg3);border:1px solid var(--border);color:var(--text);border-radius:7px;padding:6px 9px;font-size:12px;cursor:pointer}
.s-btn:active{background:#333}
.s-btn.r{border-color:#500}.s-btn.r:active{background:#300;color:var(--red)}
#type-bar{background:var(--bg2);border-top:1px solid var(--border);padding:7px;display:flex;gap:5px;flex-shrink:0}
#type-inp{flex:1;background:var(--bg3);border:1px solid var(--border);color:var(--text);padding:8px 10px;border-radius:9px;font-size:13px}
#type-inp:focus{outline:none;border-color:var(--accent)}
#type-go{background:var(--accent);color:#fff;border:none;border-radius:9px;padding:8px 12px;font-size:13px;cursor:pointer}

/* CONTROL */
#page-control{padding:10px;gap:8px}
.ctrl-card{background:var(--bg2);border:1px solid var(--border);border-radius:12px;overflow:hidden}
.ctrl-head{font-size:10px;color:var(--text2);padding:7px 12px 3px;text-transform:uppercase;letter-spacing:.5px}
.g{display:grid;gap:1px;background:var(--border)}
.g3{grid-template-columns:repeat(3,1fr)}
.g4{grid-template-columns:repeat(4,1fr)}
.g2{grid-template-columns:repeat(2,1fr)}
.cb{background:var(--bg2);padding:11px 5px;text-align:center;font-size:12px;cursor:pointer;user-select:none}
.cb:active{background:var(--bg3)}
.cb.big{font-size:19px;padding:13px}
.cb.red{color:var(--red)}
.steps{display:flex;gap:1px;background:var(--border)}
.st{flex:1;background:var(--bg2);padding:9px;text-align:center;font-size:11px;cursor:pointer;color:var(--text2)}
.st.on{color:var(--accent);background:#1a2030}

/* MONITOR */
#page-monitor{padding:10px;gap:8px}
.mon-card{background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:12px}
.mon-title{font-size:11px;color:var(--text2);margin-bottom:8px;text-transform:uppercase;letter-spacing:.5px;display:flex;justify-content:space-between;align-items:center}
.mon-val{font-size:22px;font-weight:700;font-family:monospace}
.mon-sub{font-size:11px;color:var(--text2);margin-top:2px}
.bar{height:8px;border-radius:4px;background:var(--border);margin-top:6px;overflow:hidden}
.bar-f{height:100%;border-radius:4px;transition:.4s}
.mon-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.chart-wrap{background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:10px}
.chart-title{font-size:11px;color:var(--text2);margin-bottom:6px;text-transform:uppercase;letter-spacing:.5px}
canvas{width:100%;height:60px;display:block}
.proc-row{display:flex;justify-content:space-between;font-size:11px;padding:4px 0;border-bottom:1px solid var(--border);font-family:monospace}
.proc-row:last-child{border-bottom:none}
.kill-btn{background:#300;border:1px solid #500;color:var(--red);border-radius:4px;padding:1px 6px;font-size:10px;cursor:pointer}

/* TERMINAL */
#page-terminal{padding:10px;gap:8px}
.term-out{background:#0a0a0a;border:1px solid var(--border);border-radius:12px;flex:1;overflow-y:auto;padding:9px;font-family:monospace;font-size:11px;min-height:150px;max-height:35vh}
.tl{line-height:1.6}.tl.cmd{color:var(--accent)}.tl.out{color:var(--green);white-space:pre-wrap;word-break:break-all}.tl.err{color:var(--red)}
.qcmds{display:flex;gap:5px;flex-wrap:wrap}
.qb{background:var(--bg2);border:1px solid var(--border);color:var(--text2);border-radius:5px;padding:4px 9px;font-size:11px;cursor:pointer;font-family:monospace}
.qb:active{background:var(--bg3)}
.term-row{display:flex;gap:6px}
.term-inp{flex:1;background:var(--bg2);border:1px solid var(--border);color:var(--text);padding:9px 11px;border-radius:9px;font-size:13px;font-family:monospace}
.term-inp:focus{outline:none;border-color:var(--accent)}
.term-go{background:var(--accent);color:#fff;border:none;border-radius:9px;padding:9px 14px;font-size:13px;cursor:pointer}

/* USERS */
#page-users{padding:10px;gap:8px}
.user-card{background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:12px;display:flex;align-items:center;gap:10px}
.role-badge{font-size:11px;padding:2px 8px;border-radius:10px;font-weight:600}
.role-admin{background:#2d1a00;color:#fbbf24}
.role-operator{background:#0f2040;color:#60a5fa}
.role-viewer{background:#0f2010;color:#4ade80}
.user-info{flex:1}
.user-name{font-size:13px;font-weight:600}
.user-id{font-size:11px;color:var(--text2)}
.rm-btn{background:#300;border:1px solid #500;color:var(--red);border-radius:7px;padding:5px 9px;font-size:11px;cursor:pointer}
.add-btn{background:var(--accent);color:#fff;border:none;border-radius:9px;padding:10px;font-size:13px;cursor:pointer;width:100%}

/* TOAST */
#toast{position:fixed;bottom:60px;left:50%;transform:translateX(-50%);background:rgba(0,0,0,.88);color:#fff;padding:7px 16px;border-radius:20px;font-size:12px;opacity:0;transition:.25s;pointer-events:none;white-space:nowrap;z-index:999}
#toast.show{opacity:1}
</style>
</head>
<body>
<div id="tabs">
  <div class="tab active" onclick="showTab('pcs')"><span class="tab-icon">🖥️</span>ПК</div>
  <div class="tab" onclick="showTab('screen')"><span class="tab-icon">📺</span>Экран</div>
  <div class="tab" onclick="showTab('control')"><span class="tab-icon">🎮</span>Управление</div>
  <div class="tab" onclick="showTab('monitor')"><span class="tab-icon">📊</span>Монитор</div>
  <div class="tab" onclick="showTab('terminal')"><span class="tab-icon">⌨️</span>Терминал</div>
  <div class="tab" onclick="showTab('users')"><span class="tab-icon">👥</span>Юзеры</div>
</div>

<!-- ПК -->
<div id="page-pcs" class="page active">
  <div id="pc-list"></div>
</div>

<!-- ЭКРАН -->
<div id="page-screen" class="page">
  <div id="screen-wrap">
    <img id="screen-img" draggable="false">
    <div id="screen-ph"><div class="i">📺</div>Выбери ПК</div>
    <div id="fps-b">-- FPS</div>
    <div id="conn-b">⚫</div>
    <div id="drag-indicator"></div>
  </div>
  <div id="screen-btns">
    <button class="s-btn" onclick="doScreen()">📸</button>
    <button class="s-btn" onclick="toggleStream()" id="stream-btn">📡 Стрим</button>
    <button class="s-btn" onclick="cmd({action:'scroll',direction:'up'})">🔼</button>
    <button class="s-btn" onclick="cmd({action:'scroll',direction:'down'})">🔽</button>
    <button class="s-btn" onclick="cmd({action:'hotkey',keys:['ctrl','c']})">📋</button>
    <button class="s-btn" onclick="cmd({action:'hotkey',keys:['ctrl','v']})">📌</button>
    <button class="s-btn" onclick="cmd({action:'key',key:'enter'})">↵</button>
    <button class="s-btn" onclick="cmd({action:'key',key:'esc'})">⎋</button>
    <button class="s-btn r" onclick="confirm('Блок?')&&cmd({action:'lock'})">🔒</button>
    <button class="s-btn r" onclick="confirm('Ребут?')&&cmd({action:'reboot'})">🔄</button>
    <button class="s-btn r" onclick="confirm('Выкл?')&&cmd({action:'shutdown'})">⏻</button>
  </div>
  <div id="type-bar">
    <input id="type-inp" type="text" placeholder="Текст → на ПК...">
    <button id="type-go" onclick="sendText()">⌨️</button>
  </div>
</div>

<!-- УПРАВЛЕНИЕ -->
<div id="page-control" class="page">
  <div class="ctrl-card">
    <div class="ctrl-head">🖱️ Мышь — <span id="step-label">100px</span></div>
    <div class="g g3">
      <div class="cb big" onclick="cmd({action:'control',data:'m_up_left_'+step})">↖️</div>
      <div class="cb big" onclick="cmd({action:'control',data:'m_up_'+step})">⬆️</div>
      <div class="cb big" onclick="cmd({action:'control',data:'m_up_right_'+step})">↗️</div>
      <div class="cb big" onclick="cmd({action:'control',data:'m_left_'+step})">⬅️</div>
      <div class="cb big" onclick="cmd({action:'control',data:'click_left'})">🖱️</div>
      <div class="cb big" onclick="cmd({action:'control',data:'m_right_'+step})">➡️</div>
      <div class="cb big" onclick="cmd({action:'control',data:'m_down_left_'+step})">↙️</div>
      <div class="cb big" onclick="cmd({action:'control',data:'m_down_'+step})">⬇️</div>
      <div class="cb big" onclick="cmd({action:'control',data:'m_down_right_'+step})">↘️</div>
    </div>
    <div class="g g3">
      <div class="cb" onclick="cmd({action:'control',data:'click_left'})">🖱️ ЛКМ</div>
      <div class="cb" onclick="cmd({action:'control',data:'click_right'})">🖱️ ПКМ</div>
      <div class="cb" onclick="cmd({action:'control',data:'click_double'})">2️⃣ Дабл</div>
    </div>
    <div class="g g2">
      <div class="cb" onclick="cmd({action:'scroll',direction:'up'})">🔼 Скролл ↑</div>
      <div class="cb" onclick="cmd({action:'scroll',direction:'down'})">🔽 Скролл ↓</div>
    </div>
    <div class="steps">
      <div class="st" id="st-50" onclick="setStep(50)">50px</div>
      <div class="st on" id="st-100" onclick="setStep(100)">100px</div>
      <div class="st" id="st-200" onclick="setStep(200)">200px</div>
      <div class="st" id="st-500" onclick="setStep(500)">500px</div>
    </div>
  </div>
  <div class="ctrl-card">
    <div class="ctrl-head">⌨️ Клавиши</div>
    <div class="g g4">
      <div class="cb" onclick="cmd({action:'key',key:'enter'})">↵ Enter</div>
      <div class="cb" onclick="cmd({action:'key',key:'esc'})">⎋ Esc</div>
      <div class="cb" onclick="cmd({action:'key',key:'tab'})">⇥ Tab</div>
      <div class="cb" onclick="cmd({action:'key',key:'backspace'})">⌫</div>
      <div class="cb" onclick="cmd({action:'key',key:'up'})">↑</div>
      <div class="cb" onclick="cmd({action:'key',key:'down'})">↓</div>
      <div class="cb" onclick="cmd({action:'key',key:'left'})">←</div>
      <div class="cb" onclick="cmd({action:'key',key:'right'})">→</div>
      <div class="cb" onclick="cmd({action:'key',key:'f5'})">F5</div>
      <div class="cb" onclick="cmd({action:'key',key:'f11'})">F11</div>
      <div class="cb" onclick="cmd({action:'key',key:'delete'})">Del</div>
      <div class="cb" onclick="cmd({action:'key',key:'printscreen'})">PrtSc</div>
    </div>
  </div>
  <div class="ctrl-card">
    <div class="ctrl-head">⚡ Комбинации</div>
    <div class="g g3">
      <div class="cb" onclick="cmd({action:'hotkey',keys:['ctrl','c']})">📋 Копировать</div>
      <div class="cb" onclick="cmd({action:'hotkey',keys:['ctrl','v']})">📌 Вставить</div>
      <div class="cb" onclick="cmd({action:'hotkey',keys:['ctrl','z']})">↩️ Отмена</div>
      <div class="cb" onclick="cmd({action:'hotkey',keys:['ctrl','a']})">✅ Выделить</div>
      <div class="cb" onclick="cmd({action:'hotkey',keys:['ctrl','s']})">💾 Сохранить</div>
      <div class="cb" onclick="cmd({action:'hotkey',keys:['ctrl','x']})">✂️ Вырезать</div>
      <div class="cb" onclick="cmd({action:'hotkey',keys:['win']})">🏠 Win</div>
      <div class="cb" onclick="cmd({action:'hotkey',keys:['alt','tab']})">🗂️ Alt+Tab</div>
      <div class="cb" onclick="cmd({action:'hotkey',keys:['alt','f4']})">❌ Alt+F4</div>
      <div class="cb" onclick="cmd({action:'hotkey',keys:['win','d']})">🖥️ Рабочий стол</div>
      <div class="cb" onclick="cmd({action:'hotkey',keys:['ctrl','shift','esc']})">📋 Диспетчер</div>
      <div class="cb" onclick="cmd({action:'hotkey',keys:['win','l']})">🔒 Блок экрана</div>
    </div>
  </div>
  <div class="ctrl-card">
    <div class="ctrl-head">🔴 Питание</div>
    <div class="g g3">
      <div class="cb red" onclick="confirm('Блок?')&&cmd({action:'lock'})">🔒 Блокировка</div>
      <div class="cb red" onclick="confirm('Ребут?')&&cmd({action:'reboot'})">🔄 Перезагрузка</div>
      <div class="cb red" onclick="confirm('Выкл?')&&cmd({action:'shutdown'})">⏻ Выключение</div>
    </div>
  </div>
  <div class="ctrl-card">
    <div class="ctrl-head">🌐 Открыть URL</div>
    <div style="padding:9px;display:flex;gap:7px">
      <input id="url-inp" type="url" placeholder="https://..." style="flex:1;background:var(--bg3);border:1px solid var(--border);color:var(--text);padding:7px 10px;border-radius:8px;font-size:13px">
      <button onclick="openUrl()" style="background:var(--accent);color:#fff;border:none;border-radius:8px;padding:7px 13px;font-size:13px;cursor:pointer">🌐</button>
    </div>
  </div>
</div>

<!-- МОНИТОР -->
<div id="page-monitor" class="page">
  <div class="mon-grid" style="padding:10px;gap:8px">
    <div class="mon-card">
      <div class="mon-title">🖥️ CPU <span id="cpu-val" style="color:var(--red);font-size:16px;font-family:monospace">—%</span></div>
      <div class="bar"><div class="bar-f" id="cpu-bar" style="background:var(--red);width:0%"></div></div>
    </div>
    <div class="mon-card">
      <div class="mon-title">🧠 RAM <span id="ram-val" style="color:#60a5fa;font-size:16px;font-family:monospace">—%</span></div>
      <div class="bar"><div class="bar-f" id="ram-bar" style="background:#60a5fa;width:0%"></div></div>
    </div>
    <div class="mon-card">
      <div class="mon-title">💾 Диск <span id="disk-val" style="color:#a78bfa;font-size:16px;font-family:monospace">—%</span></div>
      <div class="bar"><div class="bar-f" id="disk-bar" style="background:#a78bfa;width:0%"></div></div>
    </div>
    <div class="mon-card">
      <div class="mon-title">⏱️ Uptime</div>
      <div style="font-size:13px;font-family:monospace;color:var(--green)" id="uptime-val">—</div>
    </div>
  </div>
  <div class="chart-wrap" style="margin:0 10px">
    <div class="chart-title">CPU история (60 сек)</div>
    <canvas id="cpu-chart" height="60"></canvas>
  </div>
  <div class="chart-wrap" style="margin:8px 10px 0">
    <div class="chart-title">RAM история (60 сек)</div>
    <canvas id="ram-chart" height="60"></canvas>
  </div>
  <div class="mon-card" style="margin:8px 10px 10px">
    <div class="mon-title">📋 Топ процессов <button onclick="loadProcs()" style="background:var(--bg3);border:1px solid var(--border);color:var(--text2);border-radius:6px;padding:2px 8px;font-size:10px;cursor:pointer">🔄</button></div>
    <div id="proc-list" style="font-size:11px;color:var(--text2)">Загрузка...</div>
  </div>
</div>

<!-- ТЕРМИНАЛ -->
<div id="page-terminal" class="page">
  <div class="term-out" id="term-out">
    <div class="tl err">Выбери ПК...</div>
  </div>
  <div class="qcmds" style="padding:0 10px">
    <div class="qb" onclick="runQ('tasklist')">tasklist</div>
    <div class="qb" onclick="runQ('ipconfig')">ipconfig</div>
    <div class="qb" onclick="runQ('dir C:\\\\')">dir C:\</div>
    <div class="qb" onclick="runQ('whoami')">whoami</div>
    <div class="qb" onclick="runQ('systeminfo')">systeminfo</div>
    <div class="qb" onclick="runQ('netstat -an')">netstat</div>
  </div>
  <div class="term-row" style="padding:8px 10px">
    <input class="term-inp" id="term-inp" type="text" placeholder="команда...">
    <button class="term-go" onclick="sendCmd()">▶</button>
  </div>
</div>

<!-- ПОЛЬЗОВАТЕЛИ -->
<div id="page-users" class="page">
  <div id="users-list" style="padding:10px;display:flex;flex-direction:column;gap:8px"></div>
  <div style="padding:0 10px 10px">
    <button class="add-btn" onclick="addUser()">➕ Добавить пользователя</button>
  </div>
</div>

<div id="toast"></div>

<script>
const tg = window.Telegram?.WebApp;
if(tg){tg.ready();tg.expand();}
const BASE = location.origin;
let pcId = null, step = 100, streaming = false, streamInt = null;
let liveWs = null, fc = 0, lt = Date.now();
let cpuHistory = new Array(60).fill(0), ramHistory = new Array(60).fill(0);
let monitorInt = null;
let isDragging = false, dragStartX = 0, dragStartY = 0;
let currentUserRole = 'viewer';

// ── INIT ──
async function init() {
  const res = await fetch(BASE+'/api/me');
  if(res.ok){ const d = await res.json(); currentUserRole = d.role; }
  applyRoleUI();
  loadPcs();
}

function applyRoleUI() {
  const isViewer = currentUserRole === 'viewer';
  const isAdmin = currentUserRole === 'admin';
  document.querySelectorAll('.s-btn.r').forEach(b => b.style.display = isViewer?'none':'');
  document.getElementById('page-users').style.display = isAdmin?'flex':'none';
  if(isViewer) document.querySelector('[onclick="showTab(\'users\')"]').style.display='none';
}

// ── TABS ──
function showTab(n) {
  document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.getElementById('page-'+n).classList.add('active');
  const tabs=['pcs','screen','control','monitor','terminal','users'];
  const idx = tabs.indexOf(n);
  if(idx>=0) document.querySelectorAll('.tab')[idx].classList.add('active');
  if(n==='pcs') loadPcs();
  if(n==='monitor'){loadProcs();startMonitor();}else stopMonitor();
  if(n==='users') loadUsers();
}

function toast(m){
  const el=document.getElementById('toast');
  el.textContent=m;el.classList.add('show');
  setTimeout(()=>el.classList.remove('show'),2000);
}

// ── PC LIST ──
async function loadPcs(){
  const r = await fetch(BASE+'/api/pcs');
  const d = await r.json();
  const c = document.getElementById('pc-list');
  if(!d.length){c.innerHTML='<div class="empty"><div class="empty-icon">🔌</div>Нет ПК.<br>Запусти aigency.py</div>';return;}
  c.innerHTML = d.map(p=>`
  <div class="pc-card ${pcId===p.id?'selected':''}" style="margin:0 10px 8px;${d.indexOf(p)===0?'margin-top:10px':''}" onclick="selectPc('${p.id}','${p.name}')">
    <div class="pc-dot ${p.online?'on':'off'}"></div>
    <div class="pc-info">
      <div class="pc-name">${p.name}</div>
      <div class="pc-meta">${p.ip} · ${p.os}</div>
      <div class="pc-meta">${p.online?'🟢 Онлайн':'🔴 Оффлайн'} · ${p.connected_at}</div>
    </div>
    ${p.online?`<button class="live-btn" onclick="event.stopPropagation();window.open('${BASE}/live/${p.id}','_blank')">Live</button>`:''}
  </div>`).join('');
}

function selectPc(id,name){
  pcId=id;toast('✅ '+name);
  loadPcs();connectLiveWs(id);
}

// ── LIVE WS ──
function connectLiveWs(id){
  if(liveWs){liveWs.close();liveWs=null;}
  const proto=location.protocol==='https:'?'wss':'ws';
  liveWs=new WebSocket(`${proto}://${location.host}/live-ws?pc=${id}`);
  liveWs.binaryType='arraybuffer';
  const cb=document.getElementById('conn-b');
  liveWs.onopen=()=>{cb.textContent='🟢';};
  liveWs.onclose=()=>{cb.textContent='🔴';setTimeout(()=>{if(pcId===id)connectLiveWs(id);},3000);};
  liveWs.onmessage=(e)=>{
    const blob=new Blob([e.data],{type:'image/jpeg'});
    const url=URL.createObjectURL(blob);
    const img=document.getElementById('screen-img');
    const old=img.src;
    img.onload=()=>{if(old)URL.revokeObjectURL(old);};
    img.src=url;img.style.display='block';
    document.getElementById('screen-ph').style.display='none';
    fc++;
  };
}

setInterval(()=>{
  const now=Date.now();
  document.getElementById('fps-b').textContent=(fc/((now-lt)/1000)).toFixed(1)+' FPS';
  fc=0;lt=now;
},1000);

// ── DRAG & DROP ──
const screenImg = document.getElementById('screen-img');
const dragInd = document.getElementById('drag-indicator');

function getImgCoords(e, img){
  const r=img.getBoundingClientRect();
  const sx=img.naturalWidth/r.width/0.5;
  const sy=img.naturalHeight/r.height/0.5;
  const clientX = e.touches?e.touches[0].clientX:e.clientX;
  const clientY = e.touches?e.touches[0].clientY:e.clientY;
  return {
    x: Math.round((clientX-r.left)*sx),
    y: Math.round((clientY-r.top)*sy),
    rx: clientX-r.left, ry: clientY-r.top
  };
}

screenImg.addEventListener('mousedown', e=>{
  isDragging=false;
  const {x,y,rx,ry}=getImgCoords(e,screenImg);
  dragStartX=x;dragStartY=y;
  dragInd.style.left=rx+'px';dragInd.style.top=ry+'px';
  dragInd.style.display='block';
});

screenImg.addEventListener('mousemove', e=>{
  if(e.buttons!==1)return;
  isDragging=true;
  const {rx,ry}=getImgCoords(e,screenImg);
  dragInd.style.left=rx+'px';dragInd.style.top=ry+'px';
});

screenImg.addEventListener('mouseup', e=>{
  dragInd.style.display='none';
  const {x,y}=getImgCoords(e,screenImg);
  if(isDragging){
    cmd({action:'drag',x1:dragStartX,y1:dragStartY,x2:x,y2:y});
    toast('🖱️ Drag');
  } else {
    cmd({action:'click_abs',x,y});
  }
  isDragging=false;
});

// Touch drag
screenImg.addEventListener('touchstart', e=>{
  e.preventDefault();isDragging=false;
  const {x,y,rx,ry}=getImgCoords(e,screenImg);
  dragStartX=x;dragStartY=y;
  dragInd.style.left=rx+'px';dragInd.style.top=ry+'px';dragInd.style.display='block';
},{passive:false});

screenImg.addEventListener('touchmove', e=>{
  e.preventDefault();isDragging=true;
  const {rx,ry}=getImgCoords(e,screenImg);
  dragInd.style.left=rx+'px';dragInd.style.top=ry+'px';
},{passive:false});

screenImg.addEventListener('touchend', e=>{
  e.preventDefault();dragInd.style.display='none';
  const touch=e.changedTouches[0];
  const r=screenImg.getBoundingClientRect();
  const sx=screenImg.naturalWidth/r.width/0.5;
  const sy=screenImg.naturalHeight/r.height/0.5;
  const x=Math.round((touch.clientX-r.left)*sx);
  const y=Math.round((touch.clientY-r.top)*sy);
  if(isDragging){
    cmd({action:'drag',x1:dragStartX,y1:dragStartY,x2:x,y2:y});toast('🖱️ Drag');
  } else {
    cmd({action:'click_abs',x,y});
  }
  isDragging=false;
},{passive:false});

screenImg.addEventListener('contextmenu',e=>{
  e.preventDefault();
  const {x,y}=getImgCoords(e,screenImg);
  cmd({action:'click_abs_right',x,y});
});

screenImg.addEventListener('wheel',e=>{
  e.preventDefault();
  cmd({action:'scroll',direction:e.deltaY<0?'up':'down'});
},{passive:false});

// ── КОМАНДЫ ──
async function cmd(data){
  if(!pcId){toast('❌ Выбери ПК!');return;}
  await fetch(BASE+'/live-cmd?pc='+pcId,{
    method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)
  });
}

function doScreen(){
  if(!pcId){toast('❌ Выбери ПК!');return;}
  fetch(BASE+'/live-cmd?pc='+pcId,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'screenshot_live'})});
  showTab('screen');toast('📸');
}

function toggleStream(){
  if(!pcId){toast('❌ Выбери ПК!');return;}
  streaming=!streaming;
  const btn=document.getElementById('stream-btn');
  if(streaming){
    if(!liveWs||liveWs.readyState!==1)connectLiveWs(pcId);
    streamInt=setInterval(()=>{
      fetch(BASE+'/live-cmd?pc='+pcId,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'screenshot_live'})});
    },150);
    btn.textContent='⏹️ Стоп';btn.style.background='#300';
    toast('📡 Стрим');
  } else {
    clearInterval(streamInt);
    btn.textContent='📡 Стрим';btn.style.background='';
    toast('⏹️ Стоп');
  }
}

function sendText(){
  const t=document.getElementById('type-inp').value.trim();
  if(!t)return;
  cmd({action:'type',text:t});
  document.getElementById('type-inp').value='';
  toast('⌨️');
}
document.getElementById('type-inp').addEventListener('keydown',e=>{if(e.key==='Enter')sendText();});

function setStep(s){
  step=s;
  document.querySelectorAll('.st').forEach(b=>b.classList.remove('on'));
  document.getElementById('st-'+s)?.classList.add('on');
  document.getElementById('step-label').textContent=s+'px';
  toast('Шаг: '+s+'px');
}

function openUrl(){
  const u=document.getElementById('url-inp').value.trim();
  if(!u)return;
  cmd({action:'open_url',url:u});
  document.getElementById('url-inp').value='';
  toast('🌐');
}

// ── МОНИТОР ──
function drawChart(canvasId, data, color){
  const canvas=document.getElementById(canvasId);
  if(!canvas)return;
  canvas.width=canvas.offsetWidth*2;canvas.height=120;
  const ctx=canvas.getContext('2d');
  const w=canvas.width,h=canvas.height;
  ctx.clearRect(0,0,w,h);
  ctx.strokeStyle=color+'44';ctx.lineWidth=1;
  for(let i=0;i<=4;i++){
    ctx.beginPath();ctx.moveTo(0,h*i/4);ctx.lineTo(w,h*i/4);ctx.stroke();
  }
  ctx.beginPath();ctx.strokeStyle=color;ctx.lineWidth=2;
  data.forEach((v,i)=>{
    const x=i/data.length*w;const y=h-(v/100)*h;
    i===0?ctx.moveTo(x,y):ctx.lineTo(x,y);
  });
  ctx.stroke();
  ctx.lineTo(w,h);ctx.lineTo(0,h);ctx.closePath();
  ctx.fillStyle=color+'22';ctx.fill();
}

async function fetchStatus(){
  if(!pcId)return;
  const r=await fetch(BASE+'/api/status/'+pcId);
  if(!r.ok)return;
  const d=await r.json();
  if(!d.cpu)return;
  document.getElementById('cpu-val').textContent=d.cpu+'%';
  document.getElementById('ram-val').textContent=d.ram+'%';
  document.getElementById('disk-val').textContent=d.disk+'%';
  document.getElementById('uptime-val').textContent=d.uptime||'—';
  document.getElementById('cpu-bar').style.width=d.cpu+'%';
  document.getElementById('ram-bar').style.width=d.ram+'%';
  document.getElementById('disk-bar').style.width=d.disk+'%';
  cpuHistory.push(d.cpu);cpuHistory.shift();
  ramHistory.push(d.ram);ramHistory.shift();
  drawChart('cpu-chart',cpuHistory,'#f87171');
  drawChart('ram-chart',ramHistory,'#60a5fa');
}

function startMonitor(){if(!monitorInt){fetchStatus();monitorInt=setInterval(fetchStatus,2000);}}
function stopMonitor(){if(monitorInt){clearInterval(monitorInt);monitorInt=null;}}

async function loadProcs(){
  if(!pcId)return;
  const r=await fetch(BASE+'/api/processes/'+pcId);
  if(!r.ok)return;
  const d=await r.json();
  document.getElementById('proc-list').innerHTML=d.list.slice(0,15).map(p=>
    `<div class="proc-row">
      <span style="max-width:55%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${p.name}</span>
      <span style="color:var(--text2)">${p.cpu}% · PID ${p.pid}
      <button class="kill-btn" onclick="killProc(${p.pid},'${p.name}')">✕</button></span>
    </div>`
  ).join('')||'<div style="color:var(--text2)">Нет данных</div>';
}

function killProc(pid,name){
  if(confirm('Завершить '+name+'?'))cmd({action:'kill',pid});
}

// ── ТЕРМИНАЛ ──
function tlog(text,type='out'){
  const box=document.getElementById('term-out');
  const d=document.createElement('div');
  d.className='tl '+type;d.textContent=text;
  box.appendChild(d);box.scrollTop=box.scrollHeight;
}
async function sendCmd(){
  const inp=document.getElementById('term-inp');
  const c=inp.value.trim();if(!c)return;
  if(!pcId){toast('❌ Выбери ПК!');return;}
  inp.value='';tlog('> '+c,'cmd');
  await fetch(BASE+'/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({pc_id:pcId,command:c})});
}
document.getElementById('term-inp').addEventListener('keydown',e=>{if(e.key==='Enter')sendCmd();});
function runQ(c){document.getElementById('term-inp').value=c;sendCmd();}

// ── ПОЛЬЗОВАТЕЛИ ──
async function loadUsers(){
  const r=await fetch(BASE+'/api/users');
  if(!r.ok)return;
  const d=await r.json();
  const c=document.getElementById('users-list');
  const roleClass={admin:'role-admin',operator:'role-operator',viewer:'role-viewer'};
  const roleLabel={admin:'👑 Admin',operator:'🔧 Operator',viewer:'👁️ Viewer'};
  c.innerHTML=`
    <div class="user-card" style="border-color:var(--yellow)">
      <div class="user-info">
        <div class="user-name">Ты (Owner)</div>
        <div class="user-id">ID: ${d.admin_id}</div>
      </div>
      <span class="role-badge role-admin">👑 Admin</span>
    </div>
    ${d.users.map(u=>`
    <div class="user-card">
      <div class="user-info">
        <div class="user-name">${u.name}</div>
        <div class="user-id">ID: ${u.id}</div>
      </div>
      <span class="role-badge ${roleClass[u.role]}">${roleLabel[u.role]}</span>
      <button class="rm-btn" onclick="removeUser(${u.id},'${u.name}')">✕</button>
    </div>`).join('')}
    ${d.users.length===0?'<div style="color:var(--text2);font-size:13px;text-align:center;padding:20px">Нет пользователей</div>':''}
  `;
}

function addUser(){
  const id=prompt('Введи Telegram ID пользователя:');
  if(!id||!id.trim())return;
  const uid=parseInt(id.trim());
  if(isNaN(uid)){toast('❌ Неверный ID');return;}
  // Показываем выбор роли
  const role=confirm('Нажми OK для Operator, Отмена для Viewer')?'operator':'viewer';
  fetch(BASE+'/api/users/add',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id:uid,role})})
    .then(r=>r.json()).then(d=>{
      if(d.ok){toast('✅ Добавлен');loadUsers();}
      else toast('❌ '+d.error);
    });
}

function removeUser(id,name){
  if(!confirm('Удалить '+name+'?'))return;
  fetch(BASE+'/api/users/remove',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id:id})})
    .then(r=>r.json()).then(d=>{
      if(d.ok){toast('✅ Удалён');loadUsers();}
    });
}

// ── POLL (терминал) ──
async function poll(){
  if(!pcId)return;
  const r=await fetch(BASE+'/api/poll/'+pcId);
  if(!r.ok)return;
  const d=await r.json();
  if(d.command_result)tlog(d.command_result,'out');
}
setInterval(poll,1500);
setInterval(()=>{if(document.getElementById('page-pcs').classList.contains('active'))loadPcs();},5000);

init();
</script>
</body>
</html>"""


# ─── API ──────────────────────────────────────────────────────

@app.get("/api/me")
async def api_me(request: Request):
    # В реальном приложении нужна авторизация через Telegram InitData
    # Пока возвращаем admin для всех (Mini App открывается только через бота)
    return {"role": "admin", "user_id": ADMIN_ID}

@app.get("/api/pcs")
async def api_pcs():
    return [{"id":p.id,"name":p.name,"ip":p.ip,"os":p.os,
             "online":p.online,"connected_at":p.connected_at.strftime("%H:%M %d.%m")}
            for p in pm.get_all()]

@app.get("/api/status/{pc_id}")
async def api_status(pc_id: str):
    result = poll_results.get(pc_id, {}).get("status")
    if not result:
        await pm.send(pc_id, {"action":"status","chat_id":None})
        await asyncio.sleep(1.2)
        result = poll_results.get(pc_id,{}).get("status",{})
    return result or {}

@app.get("/api/processes/{pc_id}")
async def api_processes(pc_id: str):
    await pm.send(pc_id,{"action":"processes","chat_id":None})
    await asyncio.sleep(1.2)
    return {"list": poll_results.get(pc_id,{}).get("processes",[])}

@app.get("/api/log")
async def api_log():
    return {"log": pm.command_log[-50:]}

@app.get("/api/poll/{pc_id}")
async def api_poll(pc_id: str):
    result = poll_results.pop(pc_id,{})
    return result

@app.post("/api/run")
async def api_run(request: Request):
    data = await request.json()
    pc_id = data.get("pc_id"); cmd_text = data.get("command","")
    if pc_id and cmd_text:
        pm.log(pc_id, ADMIN_ID, f"run: {cmd_text}")
        await pm.send(pc_id,{"action":"run","command":cmd_text,"chat_id":None})
    return {"ok":True}

@app.get("/api/users")
async def api_users():
    return {
        "admin_id": ADMIN_ID,
        "users": [{"id":int(uid),"role":info["role"],"name":info.get("name","?")}
                  for uid,info in users_db.items()]
    }

@app.post("/api/users/add")
async def api_add_user(request: Request):
    data = await request.json()
    uid = str(data.get("user_id"))
    role = data.get("role","viewer")
    if role not in ROLES:
        return {"ok":False,"error":"Неверная роль"}
    users_db[uid] = {"role":role,"name":data.get("name","User "+uid)}
    save_users(users_db)
    return {"ok":True}

@app.post("/api/users/remove")
async def api_remove_user(request: Request):
    data = await request.json()
    uid = str(data.get("user_id"))
    users_db.pop(uid, None)
    save_users(users_db)
    return {"ok":True}


# ─── FASTAPI ROUTES ───────────────────────────────────────────

@app.on_event("startup")
async def on_startup():
    asyncio.create_task(dp.start_polling(bot))
    asyncio.create_task(setup_webapp())

async def setup_webapp():
    await asyncio.sleep(3)
    try:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="🖥️ Remote Control",
                web_app=WebAppInfo(url=f"https://{RAILWAY_URL}/app")
            )
        )
    except Exception as e:
        print(f"[Bot] WebApp: {e}")

@app.get("/")
async def index():
    pcs = pm.get_all()
    rows = "".join(f"<tr><td>{p.name}</td><td>{p.ip}</td><td>{'🟢' if p.online else '🔴'}</td><td><a href='/live/{p.id}'>Live</a></td></tr>" for p in pcs)
    return HTMLResponse(f"""<html><body style='font-family:monospace;background:#0a0a0a;color:#ccc;padding:20px'>
<h2>🖥️ Remote Control</h2><p>Online: {len(pm.get_online())}</p>
<p><a href='/app' style='color:#4f8ef7'>📱 Mini App</a></p>
<table border=1 cellpadding=6 style='border-collapse:collapse;border-color:#333;margin-top:12px'>
<tr><th>Имя</th><th>IP</th><th>Статус</th><th>Live</th></tr>
{rows or '<tr><td colspan=4 style="color:#555">Нет ПК</td></tr>'}
</table></body></html>""")

@app.get("/app")
async def mini_app():
    return HTMLResponse(MINI_APP)

@app.get("/live/{pc_id}")
async def live_page(pc_id: str):
    pc = pm.get_by_id(pc_id)
    name = pc.name if pc else pc_id
    return HTMLResponse(build_live_html(pc_id, name))

def build_live_html(pc_id, pc_name):
    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{pc_name}</title>
<style>*{{margin:0;padding:0;box-sizing:border-box}}body{{background:#000;display:flex;flex-direction:column;height:100vh;font-family:monospace;color:#fff}}
#h{{background:#111;padding:8px 12px;display:flex;align-items:center;gap:8px;border-bottom:1px solid #222;font-size:13px}}
#dot{{width:8px;height:8px;border-radius:50%;background:#f44}}#dot.on{{background:#4f8;box-shadow:0 0 5px #4f8}}
#fps{{margin-left:auto;color:#666;font-size:11px}}
#w{{flex:1;display:flex;align-items:center;justify-content:center;overflow:hidden;position:relative}}
img{{max-width:100%;max-height:100%;cursor:crosshair;user-select:none}}
#drag-ind{{position:absolute;width:18px;height:18px;border-radius:50%;background:rgba(79,142,247,.6);pointer-events:none;display:none;transform:translate(-50%,-50%)}}
#btns{{background:#111;padding:8px;display:flex;flex-wrap:wrap;gap:5px;justify-content:center;border-top:1px solid #222}}
.b{{background:#1a1a1a;border:1px solid #2a2a2a;color:#ccc;padding:5px 10px;border-radius:4px;cursor:pointer;font-size:12px}}
.b:active{{background:#333}}.b.r{{border-color:#500}}.b.r:active{{background:#300;color:#f87}}
#inp{{background:#111;padding:7px;display:flex;gap:6px;border-top:1px solid #1a1a1a}}
#ti{{flex:1;background:#1a1a1a;border:1px solid #2a2a2a;color:#fff;padding:6px 10px;border-radius:4px;font-size:13px}}
</style></head><body>
<div id="h"><div id="dot"></div><span>{pc_name}</span><span id="fps"></span></div>
<div id="w"><img id="s" src="" style="display:none"><div id="drag-ind"></div></div>
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
<div class="b" onclick="st()">⌨️</div><div class="b" onclick="ou()">🌐</div></div>
<script>
const img=document.getElementById('s'),dot=document.getElementById('dot'),fpsEl=document.getElementById('fps'),di=document.getElementById('drag-ind');
let ws,fc=0,lt=Date.now(),isDrag=false,dsx=0,dsy=0;
function conn(){{const p=location.protocol==='https:'?'wss':'ws';
ws=new WebSocket(p+'://'+location.host+'/live-ws?pc={pc_id}');ws.binaryType='arraybuffer';
ws.onopen=()=>dot.classList.add('on');
ws.onclose=()=>{{dot.classList.remove('on');setTimeout(conn,2000)}};
ws.onmessage=(e)=>{{const b=new Blob([e.data],{{type:'image/jpeg'}});const u=URL.createObjectURL(b);const o=img.src;img.onload=()=>{{if(o)URL.revokeObjectURL(o)}};img.src=u;img.style.display='block';fc++}};}}
setInterval(()=>{{const n=Date.now();fpsEl.textContent=(fc/((n-lt)/1000)).toFixed(1)+' FPS';fc=0;lt=n}},1000);
function gc(e,img){{const r=img.getBoundingClientRect();const sx=img.naturalWidth/r.width/0.5;const sy=img.naturalHeight/r.height/0.5;
const cx=e.touches?e.touches[0].clientX:e.clientX;const cy=e.touches?e.touches[0].clientY:e.clientY;
return{{x:Math.round((cx-r.left)*sx),y:Math.round((cy-r.top)*sy),rx:cx-r.left,ry:cy-r.top}};}}
img.addEventListener('mousedown',e=>{{isDrag=false;const {{x,y,rx,ry}}=gc(e,img);dsx=x;dsy=y;di.style.left=rx+'px';di.style.top=ry+'px';di.style.display='block'}});
img.addEventListener('mousemove',e=>{{if(e.buttons!==1)return;isDrag=true;const {{rx,ry}}=gc(e,img);di.style.left=rx+'px';di.style.top=ry+'px'}});
img.addEventListener('mouseup',e=>{{di.style.display='none';const {{x,y}}=gc(e,img);isDrag?c({{action:'drag',x1:dsx,y1:dsy,x2:x,y2:y}}):c({{action:'click_abs',x,y}});isDrag=false}});
img.addEventListener('touchstart',e=>{{e.preventDefault();isDrag=false;const {{x,y,rx,ry}}=gc(e,img);dsx=x;dsy=y;di.style.left=rx+'px';di.style.top=ry+'px';di.style.display='block'}},{{passive:false}});
img.addEventListener('touchmove',e=>{{e.preventDefault();isDrag=true;const {{rx,ry}}=gc(e,img);di.style.left=rx+'px';di.style.top=ry+'px'}},{{passive:false}});
img.addEventListener('touchend',e=>{{e.preventDefault();di.style.display='none';const t=e.changedTouches[0];const r=img.getBoundingClientRect();const sx=img.naturalWidth/r.width/0.5;const sy=img.naturalHeight/r.height/0.5;const x=Math.round((t.clientX-r.left)*sx);const y=Math.round((t.clientY-r.top)*sy);isDrag?c({{action:'drag',x1:dsx,y1:dsy,x2:x,y2:y}}):c({{action:'click_abs',x,y}});isDrag=false}},{{passive:false}});
img.addEventListener('contextmenu',e=>{{e.preventDefault();const {{x,y}}=gc(e,img);c({{action:'click_abs_right',x,y}})}});
img.addEventListener('wheel',e=>{{e.preventDefault();c({{action:'scroll',direction:e.deltaY<0?'up':'down'}})}},{{passive:false}});
document.addEventListener('keydown',e=>{{if(document.activeElement===document.getElementById('ti'))return;e.preventDefault();const km={{Enter:'enter',Escape:'esc',Backspace:'backspace',Delete:'delete',Tab:'tab',ArrowUp:'up',ArrowDown:'down',ArrowLeft:'left',ArrowRight:'right',F5:'f5',F11:'f11'}};if(e.ctrlKey&&e.key!=='Control')c({{action:'hotkey',keys:['ctrl',e.key.toLowerCase()]}});else if(e.altKey&&e.key!=='Alt')c({{action:'hotkey',keys:['alt',e.key.toLowerCase()]}});else if(km[e.key])c({{action:'key',key:km[e.key]}});else if(e.key.length===1)c({{action:'type',text:e.key}})}});
function c(d){{fetch('/live-cmd?pc={pc_id}',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(d)}})}}
function st(){{const t=document.getElementById('ti').value.trim();if(t){{c({{action:'type',text:t}});document.getElementById('ti').value=''}}}}
function ou(){{const u=prompt('URL:');if(u)c({{action:'open_url',url:u}})}}
document.getElementById('ti').addEventListener('keydown',e=>{{if(e.key==='Enter')st()}});
conn();
</script></body></html>"""

@app.post("/live-cmd")
async def live_cmd(request: Request):
    pc_id = request.query_params.get("pc")
    data = await request.json()
    if pc_id:
        pm.log(pc_id, ADMIN_ID, data.get("action","?"))
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
    async def req():
        while websocket in browser_clients:
            pc = pm.get_by_id(pc_id)
            if pc and pc.online:
                await pm.send(pc_id,{"action":"screenshot_live"})
            await asyncio.sleep(0.12)
    task = asyncio.create_task(req())
    try: await websocket.receive_text()
    except: pass
    finally:
        browser_clients.discard(websocket)
        browser_pc.pop(websocket,None)
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
            print(f"[+] {pc.name} ({pc.ip})")
            try:
                await bot.send_message(ADMIN_ID,
                    f"🟢 *{pc.name}* подключился\n🌐 `{pc.ip}` · `{pc.os}`",
                    parse_mode="Markdown")
            except: pass

        while True:
            data = await websocket.receive_json()
            if pc: pc.last_seen = datetime.now()
            t = data.get("type")
            pid = pc.id if pc else "?"

            if t in ("screenshot","screenshot_live"):
                img = base64.b64decode(data.get("image"))
                last_frames[pid] = img
                dead = set()
                for cl in list(browser_clients):
                    if browser_pc.get(cl) == pid:
                        try: await cl.send_bytes(img)
                        except: dead.add(cl)
                browser_clients -= dead
                for d in dead: browser_pc.pop(d,None)
                if t == "screenshot":
                    cid = data.get("chat_id"); mid = data.get("message_id")
                    if cid:
                        photo = BufferedInputFile(img, filename="screen.jpg")
                        try:
                            if mid:
                                await bot.edit_message_media(
                                    media=types.InputMediaPhoto(media=photo),
                                    chat_id=cid,message_id=mid,
                                    reply_markup=get_control_kb(get_step(cid)))
                            else:
                                sent = await bot.send_photo(cid,photo,
                                    caption=f"📺 {pc.name if pc else ''}",
                                    reply_markup=get_control_kb(get_step(cid)))
                                stream_msg_ids[cid] = sent.message_id
                        except Exception as e: print(f"[TG] {e}")

            elif t == "status":
                poll_results.setdefault(pid,{})["status"] = data
                cid = data.get("chat_id")
                if cid:
                    await bot.send_message(cid,
                        f"💻 *{pc.name if pc else 'ПК'}*\n"
                        f"CPU: `{data.get('cpu')}%` RAM: `{data.get('ram')}%`\n"
                        f"Диск: `{data.get('disk')}%` Up: `{data.get('uptime')}`",
                        parse_mode="Markdown")

            elif t == "processes":
                poll_results.setdefault(pid,{})["processes"] = data.get("list",[])
                cid = data.get("chat_id")
                if cid:
                    procs = data.get("list",[])
                    text = "📋 *Процессы:*\n"+"".join(f"`{p['pid']:>6}` {p['cpu']:>5}% {p['name']}\n" for p in procs[:25])
                    await bot.send_message(cid,text,parse_mode="Markdown")

            elif t == "command_result":
                out = data.get("output","")
                poll_results.setdefault(pid,{})["command_result"] = out
                cid = data.get("chat_id")
                if cid:
                    await bot.send_message(cid,f"```\n{out[:3000]}\n```",parse_mode="Markdown")

            elif t == "clipboard_content":
                cid = data.get("chat_id")
                if cid:
                    await bot.send_message(cid,f"📋 `{data.get('text','(пусто)')}`",parse_mode="Markdown")

            elif t == "file":
                cid = data.get("chat_id")
                if cid:
                    fb = base64.b64decode(data.get("data"))
                    doc = BufferedInputFile(fb,filename=data.get("filename","file"))
                    await bot.send_document(cid,doc)

    except WebSocketDisconnect: pass
    except Exception as e: print(f"[WS] {e}")
    finally:
        if pc:
            pm.disconnect(websocket)
            try:
                await bot.send_message(ADMIN_ID,
                    f"🔴 *{pc.name}* отключился",parse_mode="Markdown")
            except: pass


# ─── KEYBOARDS ────────────────────────────────────────────────

def get_control_kb(s=100):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↖️",callback_data=f"m_up_left_{s}"),
         InlineKeyboardButton(text="⬆️",callback_data=f"m_up_{s}"),
         InlineKeyboardButton(text="↗️",callback_data=f"m_up_right_{s}")],
        [InlineKeyboardButton(text="⬅️",callback_data=f"m_left_{s}"),
         InlineKeyboardButton(text="🖱️ЛКМ",callback_data="click_left"),
         InlineKeyboardButton(text="➡️",callback_data=f"m_right_{s}")],
        [InlineKeyboardButton(text="↙️",callback_data=f"m_down_left_{s}"),
         InlineKeyboardButton(text="⬇️",callback_data=f"m_down_{s}"),
         InlineKeyboardButton(text="↘️",callback_data=f"m_down_right_{s}")],
        [InlineKeyboardButton(text="🖱️ПКМ",callback_data="click_right"),
         InlineKeyboardButton(text="2️⃣",callback_data="click_double"),
         InlineKeyboardButton(text="🔄",callback_data="refresh_screen")],
        [InlineKeyboardButton(text="🔼",callback_data="scroll_up"),
         InlineKeyboardButton(text="⌨️Enter",callback_data="key_enter"),
         InlineKeyboardButton(text="🔽",callback_data="scroll_down")],
        [InlineKeyboardButton(text="📋",callback_data="hotkey_copy"),
         InlineKeyboardButton(text="📌",callback_data="hotkey_paste"),
         InlineKeyboardButton(text="↩️",callback_data="hotkey_undo")],
        [InlineKeyboardButton(text="🏠Win",callback_data="hotkey_win"),
         InlineKeyboardButton(text="❌Alt+F4",callback_data="hotkey_altf4"),
         InlineKeyboardButton(text="🗂️Alt+Tab",callback_data="hotkey_alttab")],
        [InlineKeyboardButton(text="50px",callback_data="step_50"),
         InlineKeyboardButton(text="200px",callback_data="step_200"),
         InlineKeyboardButton(text="500px",callback_data="step_500")]
    ])

def get_main_menu():
    url = f"https://{RAILWAY_URL}/app"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Открыть приложение",web_app=WebAppInfo(url=url))],
        [InlineKeyboardButton(text="🖥️ Список ПК",callback_data="menu_pclist"),
         InlineKeyboardButton(text="💻 Статус",callback_data="menu_status")],
        [InlineKeyboardButton(text="🖼️ Скриншот",callback_data="menu_screenshot"),
         InlineKeyboardButton(text="🎮 Управление",callback_data="menu_control")],
        [InlineKeyboardButton(text="📡 Стрим",callback_data="stream_on"),
         InlineKeyboardButton(text="⏹️ Стоп",callback_data="stream_off")],
        [InlineKeyboardButton(text="👥 Пользователи",callback_data="menu_users"),
         InlineKeyboardButton(text="📜 Лог",callback_data="menu_log")],
        [InlineKeyboardButton(text="🔒",callback_data="pc_lock"),
         InlineKeyboardButton(text="🔄 Ребут",callback_data="pc_reboot"),
         InlineKeyboardButton(text="⏻ Выкл",callback_data="pc_shutdown")]
    ])

def get_pc_list_kb():
    pcs = pm.get_all()
    if not pcs:
        return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Нет ПК",callback_data="noop")]])
    rows = [[InlineKeyboardButton(text=f"{'🟢' if p.online else '🔴'} {p.name} ({p.ip})",callback_data=f"select_pc_{p.id}")] for p in pcs]
    rows.append([InlineKeyboardButton(text="🔙 Назад",callback_data="menu_back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def get_users_kb():
    rows = [[InlineKeyboardButton(text=f"➕ Добавить пользователя",callback_data="adduser_start")]]
    for uid, info in users_db.items():
        role_icon = {"admin":"👑","operator":"🔧","viewer":"👁️"}.get(info["role"],"?")
        rows.append([
            InlineKeyboardButton(text=f"{role_icon} {info.get('name','User')} ({uid})",callback_data="noop"),
            InlineKeyboardButton(text="✕",callback_data=f"removeuser_{uid}")
        ])
    rows.append([InlineKeyboardButton(text="🔙 Назад",callback_data="menu_back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def get_role_kb(target_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔧 Operator",callback_data=f"setrole_{target_id}_operator")],
        [InlineKeyboardButton(text="👁️ Viewer",callback_data=f"setrole_{target_id}_viewer")],
        [InlineKeyboardButton(text="❌ Отмена",callback_data="menu_users")]
    ])


# ─── TG КОМАНДЫ ───────────────────────────────────────────────

def check(uid): return is_allowed(uid)
def is_admin(uid): return get_role(uid) == "admin"

async def require_pc(message: types.Message):
    pc = get_active(message.chat.id)
    if not pc: await message.answer("❌ Выбери ПК через /pcs"); return None
    if not pc.online: await message.answer(f"❌ `{pc.name}` оффлайн",parse_mode="Markdown"); return None
    return pc

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if not check(message.from_user.id):
        await message.answer("⛔ Нет доступа. Обратись к администратору.")
        return
    role = get_role(message.from_user.id)
    role_label = ROLES.get(role,"?")
    await message.answer(
        f"🖥️ *Remote Control*\n👤 Роль: {role_label}\n\n"
        "Нажми кнопку ниже чтобы открыть приложение 👇",
        parse_mode="Markdown",reply_markup=get_main_menu())

@dp.message(Command("adduser"))
async def cmd_adduser(message: types.Message):
    if not is_admin(message.from_user.id): return
    await message.answer(
        "👥 *Добавить пользователя*\n\n"
        "Отправь мне Telegram ID пользователя которого хочешь добавить.\n"
        "Пользователь может узнать свой ID через @userinfobot",
        parse_mode="Markdown")
    pending_adduser[message.chat.id] = -1  # ждём ID

@dp.message(Command("users"))
async def cmd_users(message: types.Message):
    if not is_admin(message.from_user.id): return
    await message.answer("👥 *Пользователи:*",parse_mode="Markdown",reply_markup=get_users_kb())

@dp.message(Command("pcs"))
async def cmd_pcs(message: types.Message):
    if not check(message.from_user.id): return
    await message.answer("🖥️ *Список ПК:*",parse_mode="Markdown",reply_markup=get_pc_list_kb())

@dp.message(Command("screen"))
async def cmd_screen(message: types.Message):
    if not check(message.from_user.id): return
    if not can(message.from_user.id,"screen"): await message.answer("⛔ Нет прав"); return
    pc = await require_pc(message)
    if pc:
        pm.log(pc.id,message.from_user.id,"screenshot")
        await pm.send(pc.id,{"action":"screenshot","chat_id":message.chat.id,"message_id":None})

@dp.message(Command("live"))
async def cmd_live(message: types.Message):
    if not check(message.from_user.id): return
    pc = get_active(message.chat.id)
    if not pc: await message.answer("❌ Выбери ПК /pcs"); return
    await message.answer(f"🔴 https://{RAILWAY_URL}/live/{pc.id}")

@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    if not check(message.from_user.id): return
    pc = await require_pc(message)
    if pc: await pm.send(pc.id,{"action":"status","chat_id":message.chat.id})

@dp.message(Command("run"))
async def cmd_run(message: types.Message):
    if not check(message.from_user.id): return
    if not can(message.from_user.id,"terminal"): await message.answer("⛔ Нет прав"); return
    pc = await require_pc(message)
    if not pc: return
    c = message.text.replace("/run","",1).strip()
    if not c: await message.answer("Пример: /run tasklist"); return
    pm.log(pc.id,message.from_user.id,f"run: {c}")
    await pm.send(pc.id,{"action":"run","command":c,"chat_id":message.chat.id})

@dp.message(Command("log"))
async def cmd_log(message: types.Message):
    if not is_admin(message.from_user.id): return
    logs = pm.command_log[-15:]
    if not logs: await message.answer("Лог пуст"); return
    text = "📜 *Лог:*\n"+"".join(f"`{l['time']}` [{l['role']}] {l['pc_name']}: {l['action']}\n" for l in reversed(logs))
    await message.answer(text,parse_mode="Markdown")

@dp.message(Command("stream"))
async def cmd_stream(message: types.Message):
    if not check(message.from_user.id): return
    if not can(message.from_user.id,"stream"): await message.answer("⛔ Нет прав"); return
    pc = await require_pc(message)
    if not pc: return
    cid = message.chat.id
    if cid in stream_tasks and not stream_tasks[cid].done(): stream_tasks[cid].cancel()
    async def loop():
        while True:
            await pm.send(pc.id,{"action":"screenshot","chat_id":cid,"message_id":stream_msg_ids.get(cid)})
            await asyncio.sleep(3)
    stream_tasks[cid] = asyncio.create_task(loop())
    await message.answer("📡 Стрим запущен")

@dp.message(Command("stopstream"))
async def cmd_stopstream(message: types.Message):
    if not check(message.from_user.id): return
    cid = message.chat.id
    if cid in stream_tasks and not stream_tasks[cid].done():
        stream_tasks[cid].cancel()
        await message.answer("⏹️ Остановлен")

@dp.message()
async def on_message(message: types.Message):
    if not message.text: return

    # Обработка добавления пользователя
    if is_admin(message.from_user.id) and message.chat.id in pending_adduser:
        if pending_adduser[message.chat.id] == -1:
            try:
                target_id = int(message.text.strip())
                if str(target_id) in users_db:
                    await message.answer("⚠️ Пользователь уже добавлен.")
                    del pending_adduser[message.chat.id]
                    return
                pending_adduser[message.chat.id] = target_id
                await message.answer(
                    f"👤 ID: `{target_id}`\n\nВыбери роль:",
                    parse_mode="Markdown",
                    reply_markup=get_role_kb(target_id))
            except ValueError:
                await message.answer("❌ Неверный ID. Введи числовой Telegram ID.")
            return

    if not check(message.from_user.id):
        await message.answer("⛔ Нет доступа.")
        return
    if message.text.startswith("/"): return
    if not can(message.from_user.id,"control"): return
    pc = get_active(message.chat.id)
    if not pc or not pc.online: await message.answer("❌ Выбери ПК /pcs"); return
    await pm.send(pc.id,{"action":"type","text":message.text})
    await message.reply("⌨️ Введено")


# ─── CALLBACKS ────────────────────────────────────────────────

@dp.callback_query()
async def callbacks(cb: types.CallbackQuery):
    uid = cb.from_user.id
    data = cb.data
    cid = cb.message.chat.id
    mid = cb.message.message_id

    if not is_allowed(uid) and not data.startswith("setrole_"):
        await cb.answer("⛔ Нет доступа",show_alert=True); return

    # Выбор ПК
    if data.startswith("select_pc_"):
        pc_id = data.replace("select_pc_","")
        pc = pm.get_by_id(pc_id)
        if not pc: await cb.answer("Не найден",show_alert=True); return
        active_pc[cid] = pc_id
        await cb.message.edit_text(f"✅ *{pc.name}*\n`{pc.ip}` · `{pc.os}`",
            parse_mode="Markdown",reply_markup=get_main_menu())
        await cb.answer(f"✅ {pc.name}"); return

    if data == "noop": await cb.answer(); return
    if data == "menu_back":
        await cb.message.edit_text("🖥️ *Remote Control*",parse_mode="Markdown",reply_markup=get_main_menu())
        await cb.answer(); return
    if data == "menu_pclist":
        await cb.message.edit_text("🖥️ *Список ПК:*",parse_mode="Markdown",reply_markup=get_pc_list_kb())
        await cb.answer(); return

    # Пользователи (только admin)
    if data == "menu_users":
        if not is_admin(uid): await cb.answer("⛔",show_alert=True); return
        await cb.message.edit_text("👥 *Пользователи:*",parse_mode="Markdown",reply_markup=get_users_kb())
        await cb.answer(); return

    if data == "adduser_start":
        if not is_admin(uid): await cb.answer("⛔",show_alert=True); return
        pending_adduser[cid] = -1
        await cb.message.answer("👤 Введи Telegram ID пользователя:")
        await cb.answer(); return

    if data.startswith("removeuser_"):
        if not is_admin(uid): await cb.answer("⛔",show_alert=True); return
        target = data.replace("removeuser_","")
        name = users_db.get(target,{}).get("name","?")
        users_db.pop(target,None)
        save_users(users_db)
        await cb.message.edit_text("👥 *Пользователи:*",parse_mode="Markdown",reply_markup=get_users_kb())
        await cb.answer(f"✅ {name} удалён"); return

    if data.startswith("setrole_"):
        if not is_admin(uid): await cb.answer("⛔",show_alert=True); return
        parts = data.split("_")
        target_id = parts[1]; role = parts[2]
        users_db[target_id] = {"role":role,"name":f"User {target_id}"}
        save_users(users_db)
        pending_adduser.pop(cid,None)
        # Уведомить нового пользователя
        try:
            role_label = ROLES.get(role,"?")
            await bot.send_message(int(target_id),
                f"✅ Тебе выдан доступ!\n👤 Роль: {role_label}\n\nНапиши /start")
        except: pass
        await cb.message.edit_text("👥 *Пользователи:*",parse_mode="Markdown",reply_markup=get_users_kb())
        await cb.answer(f"✅ Роль {role} выдана"); return

    if data == "menu_log":
        if not is_admin(uid): await cb.answer("⛔",show_alert=True); return
        logs = pm.command_log[-10:]
        text = "📜 *Лог:*\n"+"".join(f"`{l['time']}` [{l['role']}] {l['pc_name']}: {l['action']}\n" for l in reversed(logs))
        await cb.message.answer(text or "Пусто",parse_mode="Markdown")
        await cb.answer(); return

    # Дальше нужен активный ПК
    pc = get_active(cid)
    if not pc: await cb.answer("❌ Выбери ПК",show_alert=True); return
    if not pc.online: await cb.answer(f"❌ {pc.name} оффлайн",show_alert=True); return

    if data == "menu_screenshot":
        if not can(uid,"screen"): await cb.answer("⛔",show_alert=True); return
        pm.log(pc.id,uid,"screenshot")
        await pm.send(pc.id,{"action":"screenshot","chat_id":cid,"message_id":None})
        await cb.answer("📸"); return
    if data == "menu_control":
        await cb.message.answer("🎮",reply_markup=get_control_kb(get_step(cid)))
        await cb.answer(); return
    if data == "menu_status":
        await pm.send(pc.id,{"action":"status","chat_id":cid}); await cb.answer("📊"); return
    if data == "menu_live":
        await cb.message.answer(f"🔴 https://{RAILWAY_URL}/live/{pc.id}"); await cb.answer(); return
    if data in ("pc_lock","pc_reboot","pc_shutdown"):
        if not can(uid,"lock"): await cb.answer("⛔",show_alert=True); return
        action = data.replace("pc_","")
        pm.log(pc.id,uid,action)
        await pm.send(pc.id,{"action":action})
        await cb.answer({"pc_lock":"🔒","pc_reboot":"🔄","pc_shutdown":"⏻"}[data]); return
    if data == "stream_on":
        if not can(uid,"stream"): await cb.answer("⛔",show_alert=True); return
        if cid in stream_tasks and not stream_tasks[cid].done(): stream_tasks[cid].cancel()
        async def loop():
            while True:
                await pm.send(pc.id,{"action":"screenshot","chat_id":cid,"message_id":stream_msg_ids.get(cid)})
                await asyncio.sleep(3)
        stream_tasks[cid] = asyncio.create_task(loop())
        await cb.answer("📡"); return
    if data == "stream_off":
        if cid in stream_tasks and not stream_tasks[cid].done(): stream_tasks[cid].cancel()
        await cb.answer("⏹️"); return
    if data.startswith("step_"):
        s = int(data.split("_")[1]); mouse_step[cid] = s
        await cb.message.edit_reply_markup(reply_markup=get_control_kb(s))
        await cb.answer(f"{s}px"); return
    hk={"hotkey_copy":["ctrl","c"],"hotkey_paste":["ctrl","v"],"hotkey_undo":["ctrl","z"],
        "hotkey_win":["win"],"hotkey_altf4":["alt","f4"],"hotkey_alttab":["alt","tab"]}
    if data in hk:
        if not can(uid,"control"): await cb.answer("⛔",show_alert=True); return
        await pm.send(pc.id,{"action":"hotkey","keys":hk[data],"chat_id":cid,"message_id":mid})
        await cb.answer("✅"); return
    if data in ("scroll_up","scroll_down"):
        await pm.send(pc.id,{"action":"scroll","direction":data.split("_")[1],"chat_id":cid,"message_id":mid})
        await cb.answer(); return
    if not can(uid,"control"): await cb.answer("⛔",show_alert=True); return
    await pm.send(pc.id,{"action":"control","data":data,"step":get_step(cid),"chat_id":cid,"message_id":mid})
    await cb.answer("✅")
