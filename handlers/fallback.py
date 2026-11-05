"""«Страховочная сетка»: ловит всё, что не поймали остальные хэндлеры.

Подключается последним. Благодаря ему бот никогда не молчит
и всегда возвращает пользователю меню.
"""
from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from utils.keyboards import main_menu
from utils.states import Study

router = Router()


@router.message(Study.card)
async def during_study(message: Message):
    await message.answer(
        '🎴 Сейчас идёт тренировка — отвечай кнопками под карточкой.\n'
        'Чтобы выйти, нажми «⏹ Закончить» или любую кнопку меню.',
        reply_markup=main_menu,
    )


@router.message()
async def unknown_message(message: Message):
    await message.answer('Не понял 🤔 Выбери действие в меню 👇', reply_markup=main_menu)


@router.callback_query(F.data == 'noop')
async def noop(callback: CallbackQuery):
    """Кнопки-надписи вроде «2 / 5» — ничего не делают."""
    await callback.answer()


@router.callback_query()
async def stale_button(callback: CallbackQuery):
    """Старые кнопки, например после перезапуска бота."""
    await callback.answer('Эта кнопка устарела. Открой раздел заново из меню 👇', show_alert=True)
