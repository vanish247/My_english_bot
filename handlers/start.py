"""Приветствие и помощь."""
from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from utils import database
from utils.keyboards import BTN_HELP, main_menu

router = Router()

START_TEXT = (
    '👋 <b>Привет!</b> Я помогу выучить английские слова.\n\n'
    '<b>Как это работает</b>\n'
    '📚 <b>Добавить слова</b> — присылаешь пары «слово - перевод»\n'
    '🎴 <b>Учить</b> — карточками прямо сейчас или по расписанию: '
    'буду сам присылать слова раз в несколько минут\n'
    '📋 <b>Мои слова</b> — весь словарь\n\n'
    'Меню всегда внизу 👇'
)

HELP_TEXT = (
    'ℹ️ <b>Помощь</b>\n\n'
    '<b>Как добавлять слова</b>\n'
    'Слово и перевод — через дефис, пары — через запятую или каждая с новой строки:\n'
    '<code>apple - яблоко, fly - летать</code>\n\n'
    '<b>Команды</b>\n'
    '/start — главное меню\n'
    '/add — добавить слова\n'
    '/study — учить: карточки или по расписанию\n'
    '/vocabulary — мой словарь\n'
    '/mailing — сразу к настройке рассылки\n'
    '/help — эта справка\n\n'
    'Если что-то пошло не так — нажми любую кнопку меню, она всегда возвращает в начало.'
)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    database.init_user(message.from_user.id, message.from_user.username or '')
    await message.answer(START_TEXT, reply_markup=main_menu)


@router.message(Command('help'))
@router.message(F.text == BTN_HELP)
async def cmd_help(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(HELP_TEXT, reply_markup=main_menu)
