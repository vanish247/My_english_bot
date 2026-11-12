"""Редактор уже добавленной карточки: слово, перевод, транскрипция, пример, перевод примера.

Открывается двумя путями:
• из тренировки — «✏️ Изменить» на обороте карточки; «◀️ К карточке» возвращает
  в тренировку с исправленной карточкой;
• из «📋 Мои слова» — «✏️ Изменить» → выбрать слово; там же можно удалить слово.

Какое слово открыто и откуда, хранится в данных состояния (FSM): we_id, we_ctx
('study' или 'voc'), we_page, we_message. Нажатие на устаревший экран — «устарел».
"""
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from handlers.study import _card, _session
from handlers.vocabulary import PAGE_SIZE, _page
from utils import database
from utils.database import Word
from utils.keyboards import MENU_BUTTONS, card_back_kb, inline
from utils.scheduler import stop_mailing
from utils.states import Study, WordEdit
from utils.ui import show
from utils.words import esc, has_latin, short

router = Router()

STALE = 'Этот экран устарел. Открой слово заново.'

# поле → (колонка в базе, как спросить, ограничение длины)
FIELDS = {
    'word': ('word', 'английское слово или выражение', 60),
    'tr': ('translation', 'перевод', 100),
    'ipa': ('transcription', 'транскрипцию, например /ˈlʊkɪŋ fɔː/', 60),
    'ex': ('example', 'пример — предложение на английском', 300),
    'exru': ('example_ru', 'перевод примера', 300),
}


def _editor_view(item, ctx, note=''):
    english = f'🇬🇧 <b>{esc(item.word)}</b>' + (f'  {esc(item.transcription)}' if item.transcription else '')
    russian = f'🇷🇺 {esc(item.translation) if item.translation else "—"}'
    sides = [russian, english] if item.direction == 'ru' else [english, russian]
    lines = ([note, ''] if note else []) + ['✏️ <b>Изменить карточку</b>', ''] + sides
    if item.example or item.example_ru:
        lines += ['', f'💬 <i>{esc(item.example) or "—"}</i>', f'     {esc(item.example_ru) or "—"}']
    rows = [
        [('🇬🇧 Слово', 'we:word'), ('🇷🇺 Перевод', 'we:tr')],
        [('🔤 Транскрипция', 'we:ipa'), ('💬 Пример', 'we:ex')],
        [('💬 Перевод примера', 'we:exru')],
    ]
    if ctx == 'voc':
        rows.append([('🗑 Удалить слово', 'we:del')])
    rows.append([('◀️ К карточке' if ctx == 'study' else '◀️ К списку', 'we:back')])
    return '\n'.join(lines), inline(*rows)


async def _open(callback, state, item, ctx, page=0):
    shown = await show(callback, *_editor_view(item, ctx))
    await state.update_data(we_id=item.id, we_ctx=ctx, we_page=page, we_message=shown.message_id)


async def _current(callback, state):
    """Данные редактора, если нажали на актуальный экран редактора. Иначе None."""
    data = await state.get_data()
    if callback.message is None or data.get('we_message') != callback.message.message_id:
        await callback.answer(STALE, show_alert=True)
        return None
    return data


# ─── Как открыть ───────────────────────────────────────────

@router.callback_query(Study.card, F.data == 'study:edit')
async def edit_from_card(callback: CallbackQuery, state: FSMContext):
    data = await _session(callback, state)
    if data is None:
        return
    item = database.get_word(callback.from_user.id, Word(*data['cards'][data['pos']]).id)
    if item is None:
        await callback.answer('Этого слова уже нет', show_alert=True)
        return
    await _open(callback, state, item, 'study')
    await callback.answer()


def _choose_view(user_id, page):
    words = database.get_words(user_id)
    if not words:
        return _page(user_id, 0)
    pages = (len(words) + PAGE_SIZE - 1) // PAGE_SIZE
    page = max(0, min(page, pages - 1))
    rows = []
    for number, item in enumerate(words[page * PAGE_SIZE:(page + 1) * PAGE_SIZE], start=page * PAGE_SIZE + 1):
        front = item.translation if item.direction == 'ru' else item.word
        back = item.word if item.direction == 'ru' else item.translation
        rows.append([(f'{number}. {short(front, 25)} — {short(back, 22)}', f've:w:{item.id}:{page}')])
    if pages > 1:
        rows.append([
            ('◀️', f've:p:{page - 1}') if page > 0 else ('·', 'noop'),
            (f'{page + 1} / {pages}', 'noop'),
            ('▶️', f've:p:{page + 1}') if page < pages - 1 else ('·', 'noop'),
        ])
    rows.append([('◀️ К списку', f'voc:page:{page}')])
    return '✏️ <b>Какое слово изменить?</b>', inline(*rows)


@router.callback_query(F.data.startswith('ve:p:'))
async def choose_word(callback: CallbackQuery):
    text, kb = _choose_view(callback.from_user.id, int(callback.data.rsplit(':', 1)[1]))
    await show(callback, text, kb)
    await callback.answer()


@router.callback_query(F.data.startswith('ve:w:'))
async def edit_from_list(callback: CallbackQuery, state: FSMContext):
    _, _, word_id, page = callback.data.split(':')
    item = database.get_word(callback.from_user.id, int(word_id))
    if item is None:
        await callback.answer('Этого слова уже нет', show_alert=True)
        return
    if await state.get_state() is not None:
        await state.clear()
    await _open(callback, state, item, 'voc', int(page))
    await callback.answer()


# ─── Поля ──────────────────────────────────────────────────

@router.callback_query(F.data.regexp(r'^we:(word|tr|ipa|ex|exru)$'))
async def ask_field(callback: CallbackQuery, state: FSMContext):
    data = await _current(callback, state)
    if data is None:
        return
    item = database.get_word(callback.from_user.id, data['we_id'])
    if item is None:
        await callback.answer('Этого слова уже нет', show_alert=True)
        return
    field = callback.data.split(':')[1]
    column, what, _ = FIELDS[field]
    await state.update_data(we_field=field, we_back_state=await state.get_state())
    await state.set_state(WordEdit.value)
    current = getattr(item, column)
    await callback.bot.send_message(
        callback.from_user.id,
        f'✏️ Напиши {what} для «<b>{esc(short(item.word, 60))}</b>»\nСейчас: {esc(current) if current else "—"}',
        reply_markup=inline([('◀️ Не менять', 'we:cancel')]))
    await callback.answer()


async def _back_to_state(state):
    """Из «жду новое значение» — туда, где были: в тренировку или просто в меню."""
    data = await state.get_data()
    await state.set_state(Study.card if data.get('we_back_state') == Study.card.state else None)
    return data


@router.callback_query(F.data == 'we:cancel')
async def cancel_field(callback: CallbackQuery, state: FSMContext):
    if await state.get_state() == WordEdit.value.state:
        await _back_to_state(state)
    if callback.message is not None:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
    await callback.answer('Оставил как было')


@router.message(WordEdit.value, F.text, ~F.text.in_(MENU_BUTTONS), ~F.text.startswith('/'))
async def save_field(message: Message, state: FSMContext):
    user_id = message.from_user.id
    data = await state.get_data()
    field = data.get('we_field')
    if field not in FIELDS:
        await _back_to_state(state)
        await message.answer(STALE)
        return
    column, _, limit = FIELDS[field]
    value = ' '.join(message.text.split())[:limit]
    if field == 'word' and not has_latin(value):
        await message.answer('Напиши английское слово латиницей 🙂')
        return
    if field == 'ipa' and value[:1] not in '/[':
        value = f'/{value.strip("/")}/'
    data = await _back_to_state(state)
    item = database.get_word(user_id, data['we_id'])
    if item is None:
        await message.answer('Этого слова уже нет 🤷')
        return
    database.update_word_field(user_id, item.id, column, value)
    item = database.get_word(user_id, item.id)

    if data.get('we_ctx') == 'study' and data.get('cards'):   # в тренировке — та же карточка, исправленная
        cards = data['cards']
        for i, card in enumerate(cards):
            if Word(*card).id == item.id:
                cards[i] = tuple(item)

    # Старый экран закрываем, исправленную карточку присылаем вниз — продолжать удобнее
    try:
        await message.bot.edit_message_reply_markup(chat_id=user_id, message_id=data['we_message'], reply_markup=None)
    except Exception:
        pass
    view, kb = _editor_view(item, data.get('we_ctx'), note='✅ Исправил')
    sent = await message.answer(view, reply_markup=kb)
    update = {'we_message': sent.message_id}
    if data.get('we_ctx') == 'study':
        update.update(cards=data.get('cards'), message_id=sent.message_id)
    await state.update_data(**update)


@router.message(WordEdit.value, ~F.text.in_(MENU_BUTTONS))
async def field_not_text(message: Message):
    await message.answer('Напиши новое значение текстом 🙂', reply_markup=inline([('◀️ Не менять', 'we:cancel')]))


# ─── Назад и удаление ──────────────────────────────────────

@router.callback_query(F.data == 'we:back')
async def back(callback: CallbackQuery, state: FSMContext):
    data = await _current(callback, state)
    if data is None:
        return
    if data.get('we_ctx') == 'study' and await state.get_state() == Study.card.state and data.get('cards'):
        shown = await show(callback, _card(data, revealed=True), card_back_kb)
        await state.update_data(message_id=shown.message_id, we_message=None)
    else:
        text, kb = _page(callback.from_user.id, data.get('we_page', 0))
        await show(callback, text, kb)
        await state.update_data(we_message=None)
    await callback.answer()


@router.callback_query(F.data == 'we:del')
async def ask_delete(callback: CallbackQuery, state: FSMContext):
    data = await _current(callback, state)
    if data is None:
        return
    item = database.get_word(callback.from_user.id, data['we_id'])
    if item is None:
        await callback.answer('Этого слова уже нет', show_alert=True)
        return
    await show(callback, f'🗑 Удалить «<b>{esc(item.word)}</b> — {esc(item.translation)}»?\n\nЭто нельзя отменить.',
               inline([('🗑 Да, удалить', 'we:del:yes')], [('◀️ Нет', 'we:del:no')]))
    await callback.answer()


@router.callback_query(F.data.in_({'we:del:yes', 'we:del:no'}))
async def delete(callback: CallbackQuery, state: FSMContext):
    data = await _current(callback, state)
    if data is None:
        return
    user_id = callback.from_user.id
    item = database.get_word(user_id, data['we_id'])
    if callback.data == 'we:del:no' and item is not None:
        await show(callback, *_editor_view(item, 'voc'))
        await callback.answer()
        return
    if item is not None:
        database.delete_word(user_id, item.id)
        if not database.count_words(user_id):
            stop_mailing(user_id)                 # слать больше нечего
    text, kb = _page(user_id, data.get('we_page', 0))
    await show(callback, (f'🗑 Удалил «{esc(item.word)}»\n\n' if item else '') + text, kb)
    await state.update_data(we_message=None)
    await callback.answer('Удалено')
