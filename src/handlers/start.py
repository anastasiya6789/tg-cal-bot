# handlers/start.py
import secrets
import logging
from aiogram import Router, types, F, Bot
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import WEBHOOK_URL, BOT_USERNAME, logger
from oauth import get_auth_url
from keyboards import main_menu_kb

router = Router()

@router.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 Привет! Я твой помощник по расписанию.\n\n"
        "🔧 Команды:\n/start — Меню\n/create — Создать\n/schedule — Расписание",
        reply_markup=main_menu_kb()
    )

@router.callback_query(F.data == "start")
async def back_to_menu(callback: types.CallbackQuery):
    await callback.message.edit_text("👋 Главное меню:", reply_markup=main_menu_kb())
    await callback.answer()

@router.message(Command("connect"))
@router.callback_query(F.data == "connect")
async def cmd_connect(message_or_cb: types.Message | types.CallbackQuery):
    user_id = message_or_cb.from_user.id
    state = f"{user_id}_{secrets.token_hex(8)}"
    url = get_auth_url(state)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔐 Авторизовать Google", url=url)]])
    response = message_or_cb.message if hasattr(message_or_cb, 'message') else message_or_cb
    await response.answer("🔐 Нажми кнопку, чтобы разрешить доступ к календарю:", reply_markup=kb)
    if hasattr(message_or_cb, 'answer'): 
        await message_or_cb.answer()