"""Добавление слов: пользователь присылает пары «слово - перевод»."""
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from handlers.study import study_menu
from utils import database
from utils.keyboards import BTN_ADD, add_kb, empty_kb
from utils.states import AddWords
from utils.ui import drop_buttons, show
from utils.words import esc, parse_words, short, words_count

router = Router()

PROMPT = (
    '📚 <b>Добавление слов</b>\n\n'
    'Пришли пары «слово - перевод» через запятую или каждую с новой строки:\n'
    '<code>apple - яблоко, fly - летать, go - ходить</code>\n\n'
    'Можно несколькими сообщениями подряд. Когда закончишь — жми «Готово».'
)

MAX_SHOWN = 10   # больше не перечисляем, чтобы ответ не превысил лимит Telegram


def _bullets(items):
    lines = [f'• {esc(short(item))}' for item in items[:MAX_SHOWN]]
    if len(items) > MAX_SHOWN:
        lines.append(f'…и ещё {len(items) - MAX_SHOWN}')
    return lines


@router.message(Command('add'))
@router.message(F.text == BTN_ADD)
async def add_from_menu(message: Message, state: FSMContext):
    await state.set_state(AddWords.waiting)
    await message.answer(PROMPT, reply_markup=add_kb)


@router.callback_query(F.data == 'add:start')
async def add_from_button(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AddWords.waiting)
    await show(callback, PROMPT, add_kb)
    await callback.answer()


@router.message(AddWords.waiting, F.text)
async def add_receive(message: Message):
    user_id = message.from_user.id
    pairs, bad = parse_words(message.text)
    added, duplicates = database.add_words(user_id, pairs) if pairs else ([], [])

    lines = []
    if added:
        lines.append(f'✅ Добавил {words_count(len(added))}:')
        lines += _bullets([word.replace(' - ', ' — ') for word in added])
    if duplicates:
        lines += [''] if lines else []
        lines.append('☑️ Уже были в словаре:')
        lines += _bullets(duplicates)
    if bad:
        lines += [''] if lines else []
        lines.append('⚠️ Не понял — между словом и переводом нужен дефис:')
        lines += _bullets(bad)
        lines.append('Пример: <code>apple - яблоко</code>')

    lines += ['', f'📚 В словаре: {words_count(database.count_words(user_id))}',
              'Присылай ещё или жми «Готово».']
    await message.answer('\n'.join(lines), reply_markup=add_kb)


@router.message(AddWords.waiting)
async def add_not_text(message: Message):
    """Прислали стикер, фото и т.п. вместо текста."""
    await message.answer('Жду текст — пары «слово - перевод».\nИли нажми «Готово».',
                         reply_markup=add_kb)


@router.callback_query(F.data == 'add:done')
async def add_done(callback: CallbackQuery, state: FSMContext):
    # Сбрасываем состояние, только если сейчас идёт добавление —
    # старая кнопка «Готово» не должна прерывать, например, тренировку
    if await state.get_state() == AddWords.waiting.state:
        await state.clear()
    user_id = callback.from_user.id
    await drop_buttons(callback)
    # Сразу показываем «Как будем учить?»: карточки или рассылка по расписанию
    if database.count_words(user_id):
        text, kb = study_menu(user_id, header='👍 <b>Готово!</b> Слова сохранены.\n\n')
    else:
        text, kb = '👍 Готово! Словарь пока пуст.', empty_kb
    await callback.bot.send_message(user_id, text, reply_markup=kb)
    await callback.answer()
