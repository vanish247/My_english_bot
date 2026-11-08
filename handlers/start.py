"""Приветствие и помощь."""
from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from handlers.dictionary import show_for_text
from utils import database
from utils.keyboards import BTN_HELP, main_menu
from utils.words import esc, short

router = Router()

START_TEXT = (
    '👋 <b>Привет!</b> Я помогу выучить английские слова.\n\n'
    '<b>Как это работает</b>\n'
    '📚 <b>Добавить слова</b> — присылаешь слово, текст или пары «слово - перевод»\n'
    '🎴 <b>Учить</b> — карточками прямо сейчас или по расписанию: '
    'буду сам присылать слова раз в несколько минут\n'
    '📋 <b>Мои слова</b> — весь словарь\n\n'
    '✨ <b>Можно просто прислать</b> английское слово — найду перевод, '
    'или любой текст, пост, песню — найду в нём слова для изучения.\n\n'
    'Меню всегда внизу 👇'
)

HELP_TEXT = (
    'ℹ️ <b>Помощь</b>\n\n'
    '<b>Как добавлять слова</b>\n'
    '🔤 Пришли английское слово — покажу варианты перевода, выбери нужный\n'
    '📖 Пришли текст, пост или песню — найду слова и выражения, отметь незнакомые\n'
    '✍️ Или пары через дефис, через запятую или каждую с новой строки:\n'
    '<code>apple - яблоко, fly - летать</code>\n\n'
    '<b>Команды</b>\n'
    '/start — главное меню\n'
    '/add — добавить слова\n'
    '/study — учить: карточки или по расписанию\n'
    '/vocabulary — мой словарь\n'
    '/mailing — сразу к настройке рассылки\n'
    '/help — эта справка\n\n'
    '<b>В любом чате</b>\n'
    'Напиши <code>@Allomisha_bot слово</code> или фразу — над клавиатурой появится перевод. '
    'Можно добавить слова себе или отправить карточку собеседнику.\n\n'
    'Если что-то пошло не так — нажми любую кнопку меню, она всегда возвращает в начало.'
)


INLINE_HELP = (
    '🔍 <b>Перевод в любом чате</b>\n\n'
    'В поле ввода любого чата напиши моё имя и слово или фразу:\n'
    '<code>@Allomisha_bot ubiquitous</code>\n'
    '<code>@Allomisha_bot I\'ve been looking for my keys</code>\n\n'
    'Над клавиатурой появятся переводы.\n'
    '📥 <b>Кнопка над списком</b> — выбрать слова и добавить себе, собеседник ничего не увидит.\n'
    '👆 <b>Нажать на перевод</b> — отправить карточку в чат, под ней будет «➕ В мой словарь».'
)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, command: CommandObject):
    await state.clear()
    database.init_user(message.from_user.id, message.from_user.username or '')
    args = command.args or ''

    # Пришли из inline-режима по кнопке «📥 Выбрать слова и добавить в бота»
    if args.startswith('q_'):
        text = database.get_inline_text(args[2:])
        if text:
            await message.answer(f'📥 Слова из фразы:\n<i>{esc(short(text, 200))}</i>', reply_markup=main_menu)
            await show_for_text(message, text)
            return
    if args == 'inline_help':
        await message.answer(INLINE_HELP, reply_markup=main_menu)
        return
    await message.answer(START_TEXT, reply_markup=main_menu)


@router.message(Command('help'))
@router.message(F.text == BTN_HELP)
async def cmd_help(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(HELP_TEXT, reply_markup=main_menu)
