"""🔔 Рассылка: бот сам присылает слова, здесь выбирают, как часто.

Открывается из «🎴 Учить → 🔔 Рассылка» (или командой /mailing).
Экран обновляется на месте.
"""
from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from utils import database
from utils.keyboards import OLD_BTN_MAILING, PHRASES, empty_kb, mailing_kb
from utils.scheduler import start_mailing, stop_mailing
from utils.ui import drop_buttons, show

router = Router()


def _screen(user_id):
    """Текст и кнопки экрана рассылки."""
    if database.count_words(user_id) == 0:
        return ('🔔 <b>Рассылка</b>\n\nВ «Моих словах» пока пусто — мне нечего присылать. '
                'Сначала добавь слова 👇', empty_kb)
    minutes = database.get_mailing(user_id)
    status = f'✅ включена — {PHRASES.get(minutes, f"каждые {minutes} мин")}' if minutes else '⛔ выключена'
    text = ('🔔 <b>Рассылка</b>\n\n'
            f'Сейчас: {status}\n\n'
            'Буду сам присылать по одному слову из «Моих слов», а перевод спрячу '
            'под спойлер — нажмёшь на него, чтобы проверить себя.\n'
            '🌙 Ночью, с 22:00 до 9:00 по Москве, не беспокою.\n\n'
            'Как часто присылать?')
    return text, mailing_kb(minutes)


@router.message(Command('mailing'))
@router.message(F.text == OLD_BTN_MAILING)
async def mailing_from_command(message: Message, state: FSMContext):
    await state.clear()
    text, kb = _screen(message.from_user.id)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data == 'study:mailing')
async def mailing_from_study_menu(callback: CallbackQuery):
    text, kb = _screen(callback.from_user.id)
    await show(callback, text, kb)
    await callback.answer()


@router.callback_query(F.data == 'mail:stop')
async def mailing_stop_from_word(callback: CallbackQuery):
    """«🔕 Остановить» под словом рассылки."""
    stop_mailing(callback.from_user.id)
    await drop_buttons(callback)
    await callback.answer('🔕 Рассылка остановлена. Включить снова: 🎴 Учить → 🔔 Рассылка',
                          show_alert=True)


@router.callback_query(F.data == 'mail:settings')
async def mailing_settings_from_word(callback: CallbackQuery):
    """«⚙️ Как часто» под словом рассылки — экран рассылки отдельным сообщением."""
    text, kb = _screen(callback.from_user.id)
    await callback.bot.send_message(callback.from_user.id, text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith('mail:'))
async def mailing_set(callback: CallbackQuery, bot: Bot):
    user_id = callback.from_user.id
    value = callback.data.split(':', 1)[1]

    if value == 'off':
        stop_mailing(user_id)
        note = '🔕 Рассылка выключена'
    elif value.isdigit() and int(value) in PHRASES:
        if database.count_words(user_id) == 0:
            note = 'Сначала добавь слова'
        else:
            start_mailing(bot, user_id, int(value))
            note = f'🔔 Буду присылать {PHRASES[int(value)]}'
    else:
        note = 'Неизвестный интервал'

    text, kb = _screen(user_id)
    await show(callback, text, kb)
    await callback.answer(note)
