"""Помощники для обновления сообщений с кнопками."""
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message


async def show(callback: CallbackQuery, text, reply_markup=None):
    """Заменяет текст сообщения, на котором нажали кнопку («экран» обновляется на месте).

    Если отредактировать нельзя (сообщение старше 48 часов или удалено) —
    присылает новое. Возвращает сообщение, которое теперь видит пользователь.
    """
    message = callback.message
    if isinstance(message, Message):
        try:
            result = await message.edit_text(text, reply_markup=reply_markup)
            return result if isinstance(result, Message) else message
        except TelegramBadRequest as error:
            if 'message is not modified' in str(error):
                return message           # текст тот же — ничего делать не надо
    return await callback.bot.send_message(callback.from_user.id, text, reply_markup=reply_markup)


async def drop_buttons(callback: CallbackQuery):
    """Убирает кнопки у сообщения, чтобы по старым кнопкам не нажимали."""
    if isinstance(callback.message, Message):
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
