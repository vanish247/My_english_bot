"""Словарь в боте.

• Прислали слово → экран с вариантами перевода, тап по варианту добавляет слово.
• Прислали текст → экран с найденными словами и выражениями, отмечаешь галочками.

Каждый экран — одно сообщение с кнопками. Что на нём показано (варианты,
найденные слова, отметки) храним в памяти по номеру сообщения. После
перезапуска бота старые кнопки ответят «экран устарел».
"""
from collections import OrderedDict

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from utils import database, dictionary
from utils.database import Word
from utils.keyboards import MENU_BUTTONS, add_kb, added_kb, inline
from utils.states import AddWords, Study
from utils.ui import drop_buttons, show
from utils.words import esc, short, words_count

router = Router()

STALE = 'Этот экран устарел. Пришли слово или текст ещё раз.'
NO_DICTIONARY = '📕 Словарь пока не подключён.'
MAX_SCREENS = 500

_screens = OrderedDict()        # (user_id, message_id) → данные экрана


def _remember(user_id, message_id, data):
    _screens[(user_id, message_id)] = data
    _screens.move_to_end((user_id, message_id))
    while len(_screens) > MAX_SCREENS:
        _screens.popitem(last=False)


def _screen(callback, kind):
    if callback.message is None:
        return None
    data = _screens.get((callback.from_user.id, callback.message.message_id))
    return data if data and data['kind'] == kind else None


def _forget(callback):
    if callback.message is not None:
        _screens.pop((callback.from_user.id, callback.message.message_id), None)


# ─── Перевод слова ─────────────────────────────────────────

def _lookup_view(data):
    word = data['word']
    head = f'🇬🇧 <b>{esc(word)}</b>' + (f'  {esc(data["ipa"])}' if data['ipa'] else '')
    if data['added']:
        chosen = ', '.join(esc(t) for t in data['added'])
        text = (f'{head}\n\n✅ Добавил: <b>{esc(word)}</b> — {chosen}\n'
                f'📚 В словаре: {words_count(data["total"])}\n\nМожно выбрать ещё значение:')
    elif data['options']:
        text = f'{head}\n\nВыбери значение — добавлю в словарь:'
    elif not dictionary.available():
        text = f'{head}\n\n{NO_DICTIONARY} Напиши перевод сам:'
    else:
        text = f'{head}\n\n😕 Не нашёл в словаре. Напиши перевод сам:'

    rows = []
    for i, option in enumerate(data['options']):
        mark = '✅ ' if option.translation in data['added'] else ''
        note = option.pos + (f', {option.note}' if option.note else '')
        rows.append([(f'{mark}{short(option.translation, 30)} · {note}', f'lk:{i}')])
    rows.append([('✏️ Свой перевод', 'lk:own'),
                 ('✔️ Готово' if data['added'] else '✖️ Не надо', 'lk:close')])
    return text, inline(*rows)


async def show_lookup(message: Message, text):
    """Экран «Выбери значение» для слова или короткого выражения."""
    word = ' '.join(text.split())[:60]
    options = dictionary.meanings(word)
    data = {'kind': 'lookup', 'word': word, 'options': options,
            'ipa': options[0].ipa if options else '', 'added': [], 'total': 0}
    view, kb = _lookup_view(data)
    sent = await message.answer(view, reply_markup=kb)
    _remember(message.from_user.id, sent.message_id, data)


@router.callback_query(F.data.regexp(r'^lk:\d+$'))
async def lookup_pick(callback: CallbackQuery):
    data = _screen(callback, 'lookup')
    index = int(callback.data.split(':')[1])
    if data is None or index >= len(data['options']):
        await callback.answer(STALE, show_alert=True)
        return
    option = data['options'][index]
    if option.translation in data['added']:
        await callback.answer('Это значение уже добавлено')
        return

    user_id = callback.from_user.id
    added, _ = database.add_words(
        user_id, [Word(data['word'], option.translation, option.ipa, option.example)])
    data['added'].append(option.translation)
    data['total'] = database.count_words(user_id)
    view, kb = _lookup_view(data)
    shown = await show(callback, view, kb)
    _remember(user_id, shown.message_id, data)
    await callback.answer('Добавил ✅' if added else 'Уже было в словаре')


@router.callback_query(F.data == 'lk:close')
async def lookup_close(callback: CallbackQuery):
    _forget(callback)
    await drop_buttons(callback)
    await callback.answer()


@router.callback_query(F.data == 'lk:own')
async def lookup_own(callback: CallbackQuery, state: FSMContext):
    data = _screen(callback, 'lookup')
    if data is None:
        await callback.answer(STALE, show_alert=True)
        return
    previous = await state.get_state()
    if previous == Study.card.state:
        await state.clear()                  # свой перевод важнее начатой тренировки
        previous = None
    await state.set_state(AddWords.custom)
    await state.update_data(own_word=data['word'], own_ipa=data['ipa'], own_prev=previous)
    await callback.bot.send_message(
        callback.from_user.id, f'✏️ Напиши перевод для «<b>{esc(data["word"])}</b>»:')
    await callback.answer()


@router.message(AddWords.custom, F.text, ~F.text.in_(MENU_BUTTONS), ~F.text.startswith('/'))
async def own_translation(message: Message, state: FSMContext):
    data = await state.get_data()
    word = data.get('own_word')
    translation = ' '.join(message.text.split())[:100]
    adding = data.get('own_prev') == AddWords.waiting.state
    if adding:
        await state.set_state(AddWords.waiting)
    else:
        await state.clear()
    if not word:
        await message.answer(STALE)
        return

    added, _ = database.add_words(
        message.from_user.id, [Word(word, translation, data.get('own_ipa', ''), '')])
    mark = '✅ Добавил' if added else '☑️ Уже есть в словаре'
    await message.answer(
        f'{mark}: <b>{esc(word)}</b> — {esc(translation)}\n'
        f'📚 В словаре: {words_count(database.count_words(message.from_user.id))}',
        reply_markup=add_kb if adding else added_kb)


@router.message(AddWords.custom, ~F.text.in_(MENU_BUTTONS))
async def own_translation_not_text(message: Message):
    await message.answer('Напиши перевод текстом 🙂 Или выбери действие в меню.')


# ─── Слова из текста ───────────────────────────────────────

def _text_view(data):
    items, selected = data['items'], data['selected']
    lines = [f'📖 <b>Нашёл в тексте: {len(items)}</b>',
             'Отметь, что хочешь учить, и нажми «Добавить».']
    if data['known']:
        lines.append(f'☑️ Ещё {data["known"]} уже есть в твоём словаре')
    if data['over']:
        lines.append(f'Показал первые {len(items)}, ещё {data["over"]} не поместились')

    rows = [[(f'{"☑️" if i in selected else "☐"} {short(item.shown, 25)} — {short(item.translation, 22)}',
              f'tx:{i}')] for i, item in enumerate(items)]
    rows.append([(f'✅ Добавить ({len(selected)})', 'tx:add')])
    all_selected = len(selected) == len(items)
    rows.append([('☐ Снять все' if all_selected else '☑️ Выбрать все', 'tx:all'),
                 ('✖️ Отмена', 'tx:cancel')])
    return '\n'.join(lines), inline(*rows)


async def show_extraction(message: Message, text):
    """Экран «Нашёл в тексте» с галочками."""
    user_id = message.from_user.id
    if not dictionary.available():
        await message.answer(f'{NO_DICTIONARY} Разбирать текст пока нечем.\n'
                             'Пришли пары: <code>word - перевод</code>')
        return

    items = dictionary.extract(text)
    known = {w.word.lower() for w in database.get_words(user_id)}
    fresh = [item for item in items if item.shown.lower() not in known]
    data = {'kind': 'text', 'items': fresh[:dictionary.MAX_ITEMS], 'selected': set(),
            'known': len(items) - len(fresh), 'over': max(0, len(fresh) - dictionary.MAX_ITEMS)}

    if not data['items']:
        note = f'\n☑️ {data["known"]} уже есть в твоём словаре.' if data['known'] else ''
        await message.answer('🤷 Не нашёл в тексте новых слов для изучения.' + note +
                             '\nСамые простые слова (the, have, good…) я пропускаю.')
        return
    view, kb = _text_view(data)
    sent = await message.answer(view, reply_markup=kb)
    _remember(user_id, sent.message_id, data)


@router.callback_query(F.data.regexp(r'^tx:\d+$'))
async def text_toggle(callback: CallbackQuery):
    data = _screen(callback, 'text')
    index = int(callback.data.split(':')[1])
    if data is None or index >= len(data['items']):
        await callback.answer(STALE, show_alert=True)
        return
    data['selected'] ^= {index}                  # отметить / снять отметку
    view, kb = _text_view(data)
    await show(callback, view, kb)
    await callback.answer()


@router.callback_query(F.data == 'tx:all')
async def text_toggle_all(callback: CallbackQuery):
    data = _screen(callback, 'text')
    if data is None:
        await callback.answer(STALE, show_alert=True)
        return
    everything = set(range(len(data['items'])))
    data['selected'] = set() if data['selected'] == everything else everything
    view, kb = _text_view(data)
    await show(callback, view, kb)
    await callback.answer()


@router.callback_query(F.data == 'tx:add')
async def text_add(callback: CallbackQuery):
    data = _screen(callback, 'text')
    if data is None:
        await callback.answer(STALE, show_alert=True)
        return
    if not data['selected']:
        await callback.answer('Сначала отметь слова, которые хочешь учить')
        return

    user_id = callback.from_user.id
    chosen = [data['items'][i] for i in sorted(data['selected'])]
    added, duplicates = database.add_words(
        user_id, [Word(item.shown, item.translation, item.ipa, item.sentence) for item in chosen])

    lines = [f'✅ Добавил {words_count(len(added))}:'] if added else []
    lines += [f'• <b>{esc(short(w.word, 40))}</b> — {esc(short(w.translation, 40))}' for w in added]
    if duplicates:
        lines.append(f'☑️ Уже были в словаре: {len(duplicates)}')
    lines += ['', f'📚 В словаре: {words_count(database.count_words(user_id))}']
    _forget(callback)
    await show(callback, '\n'.join(lines), added_kb)
    await callback.answer('Добавил ✅')


@router.callback_query(F.data == 'tx:cancel')
async def text_cancel(callback: CallbackQuery):
    _forget(callback)
    await show(callback, 'Хорошо, ничего не добавил.')
    await callback.answer()
