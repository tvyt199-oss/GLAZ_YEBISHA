import os
import asyncio
import base64
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton

# --- КОНФИГУРАЦИЯ ---
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
ALLOWED_USER_ID = int(os.environ.get("ALLOWED_USER_ID", "0"))

app = FastAPI()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def send_command(self, message: dict):
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception:
                self.disconnect(connection)

manager = ConnectionManager()
current_mouse_step = 100

def get_control_keyboard(mouse_step=100):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="↖️", callback_data=f"m_up_left_{mouse_step}"),
            InlineKeyboardButton(text="⬆️ Вверх", callback_data=f"m_up_{mouse_step}"),
            InlineKeyboardButton(text="↗️", callback_data=f"m_up_right_{mouse_step}")
        ],
        [
            InlineKeyboardButton(text="⬅️ Лево", callback_data=f"m_left_{mouse_step}"),
            InlineKeyboardButton(text="🖱️ ЛКМ", callback_data="click_left"),
            InlineKeyboardButton(text="Право ➡️", callback_data=f"m_right_{mouse_step}")
        ],
        [
            InlineKeyboardButton(text="↙️", callback_data=f"m_down_left_{mouse_step}"),
            InlineKeyboardButton(text="⬇️ Вниз", callback_data=f"m_down_{mouse_step}"),
            InlineKeyboardButton(text="↘️", callback_data=f"m_down_right_{mouse_step}")
        ],
        [
            InlineKeyboardButton(text="🖱️ ПКМ", callback_data="click_right"),
            InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_screen"),
            InlineKeyboardButton(text="⌨️ Enter", callback_data="key_enter")
        ],
        [
            InlineKeyboardButton(text="Шаг: 50px", callback_data="step_50"),
            InlineKeyboardButton(text="Шаг: 200px", callback_data="step_200"),
            InlineKeyboardButton(text="Шаг: 500px", callback_data="step_500")
        ]
    ])
    return keyboard

@app.on_event("startup")
async def on_startup():
    asyncio.create_task(dp.start_polling(bot))

@app.get("/")
async def get_index():
    return HTMLResponse("<h1>Railway Remote Control Server is Active</h1>")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "screenshot":
                image_bytes = base64.b64decode(data.get("image"))
                photo = BufferedInputFile(image_bytes, filename="screen.jpg")
                
                chat_id = data.get("chat_id")
                msg_id = data.get("message_id")
                
                if chat_id:
                    try:
                        if msg_id:
                            await bot.edit_message_media(
                                media=types.InputMediaPhoto(media=photo, caption="📺 Прямой эфир обновлен"),
                                chat_id=chat_id,
                                message_id=msg_id,
                                reply_markup=get_control_keyboard(current_mouse_step)
                            )
                        else:
                            await bot.send_photo(
                                chat_id=chat_id,
                                photo=photo,
                                caption="📺 Прямой эфир начат",
                                reply_markup=get_control_keyboard(current_mouse_step)
                            )
                    except Exception as e:
                        print(f"Error updating TG stream: {e}")
    except WebSocketDisconnect:
        manager.disconnect(websocket)

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.from_user.id != ALLOWED_USER_ID:
        return
    await message.answer(
        "🎮 **Пульт управления активен на Railway!**\n"
        "Отправьте `/stream` для получения актуального экрана ПК.\n"
        "Любой отправленный текст будет автоматически напечатан на клавиатуре ПК.",
        parse_mode="Markdown"
    )

@dp.message(Command("stream"))
async def cmd_stream(message: types.Message):
    if message.from_user.id != ALLOWED_USER_ID:
        return
    await manager.send_command({
        "action": "screenshot",
        "chat_id": message.chat.id,
        "message_id": None
    })

@dp.message()
async def auto_type(message: types.Message):
    if message.from_user.id != ALLOWED_USER_ID or message.text.startswith("/"):
        return
    await manager.send_command({
        "action": "type",
        "text": message.text
    })
    await message.reply("⌨️ Отправлен запрос на ввод текста.")

@dp.callback_query()
async def process_callback(callback_query: types.CallbackQuery):
    if callback_query.from_user.id != ALLOWED_USER_ID:
        return
    
    global current_mouse_step
    data = callback_query.data
    
    if data.startswith("step_"):
        current_mouse_step = int(data.split("_")[1])
        await callback_query.message.edit_reply_markup(reply_markup=get_control_keyboard(current_mouse_step))
        await callback_query.answer(f"Шаг изменен на {current_mouse_step}px")
        return

    await manager.send_command({
        "action": "control",
        "data": data,
        "step": current_mouse_step,
        "chat_id": callback_query.message.chat.id,
        "message_id": callback_query.message.message_id
    })
    await callback_query.answer("Выполнение...")
