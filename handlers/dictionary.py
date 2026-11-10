"""Словарь в боте.

• Прислали слово → экран с вариантами перевода, тап по варианту добавляет слово.
• Прислали текст → экран с найденными словами и выражениями, отмечаешь галочками.
• Прислали русское → «Делаем карточки с русского на английский?»: слово ищем
  в словаре наоборот, текст отправляем в ИИ отдельным промптом.

Каждый экран — одно сообщение с кнопками. Что на нём показано (варианты,
найденные слова, отметки) храним в памяти по номеру сообщения. После
перезапуска бота старые кнопки ответят «экран устарел».
"""
import re
from collections import OrderedDict

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from utils import ai_prompt, database, dictionary
from utils.database import Word
from utils.keyboards import MENU_BUTTONS, add_kb, added_kb, inline, ru_offer_kb
from utils.states import AddWords, Study
from utils.ui import drop_buttons, show
from utils.words import esc, has_latin, is_russian, plural, short, words_count

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
    rows.append([('✏️ Свой перевод', 'lk:own'), ('🤖 Через ИИ', 'lk:ai')])
    rows.append([('✔️ Готово' if data['added'] else '✖️ Не надо', 'lk:close')])
    return text, inline(*rows)


async def show_lookup(message: Message, text):
    """Экран «Выбери значение» для слова или короткого выражения."""
    word = dictionary.normalize_case(text)[:60]
    options = dictionary.meanings(word)
    ipa = dictionary.transcription(word) or (options[0].ipa if options else '')
    data = {'kind': 'lookup', 'word': word, 'options': options, 'ipa': ipa, 'added': [], 'total': 0}
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


@router.callback_query(F.data == 'lk:ai')
async def lookup_ai(callback: CallbackQuery, bot: Bot):
    data = _screen(callback, 'lookup')
    if data is None:
        await callback.answer(STALE, show_alert=True)
        return
    await send_ai_prompt(bot, callback.from_user.id, data['word'])
    await callback.answer()


@router.callback_query(F.data == 'lk:close')
async def lookup_close(callback: CallbackQuery):
    _forget(callback)
    await drop_buttons(callback)
    await callback.answer()


async def _ask_own(callback, state, word, ipa='', direction='en'):
    """Ждём свой вариант: перевод английского слова или английское для русского."""
    previous = await state.get_state()
    if previous == Study.card.state:
        await state.clear()                  # свой перевод важнее начатой тренировки
        previous = None
    await state.set_state(AddWords.custom)
    await state.update_data(own_word=word, own_ipa=ipa, own_prev=previous, own_dir=direction)
    question = (f'✏️ Напиши, как по-английски «<b>{esc(word)}</b>»:' if direction == 'ru'
                else f'✏️ Напиши перевод для «<b>{esc(word)}</b>»:')
    await callback.bot.send_message(callback.from_user.id, question)
    await callback.answer()


@router.callback_query(F.data == 'lk:own')
async def lookup_own(callback: CallbackQuery, state: FSMContext):
    data = _screen(callback, 'lookup')
    if data is None:
        await callback.answer(STALE, show_alert=True)
        return
    await _ask_own(callback, state, data['word'], data['ipa'])


@router.message(AddWords.custom, F.text, ~F.text.in_(MENU_BUTTONS), ~F.text.startswith('/'))
async def own_translation(message: Message, state: FSMContext):
    data = await state.get_data()
    word = data.get('own_word')
    translation = ' '.join(message.text.split())[:100]
    direction = data.get('own_dir', 'en')
    if direction == 'ru' and word:
        if not has_latin(translation):
            await message.answer('Напиши английский вариант латиницей 🙂')
            return
        word, translation = translation, word        # английское — слово, русское — перевод
    adding = data.get('own_prev') == AddWords.waiting.state
    if adding:
        await state.set_state(AddWords.waiting)
    else:
        await state.clear()
    if not word:
        await message.answer(STALE)
        return

    added, _ = database.add_words(message.from_user.id, [
        Word(word, translation, '' if direction == 'ru' else data.get('own_ipa', ''), '', direction=direction)])
    mark = '✅ Добавил' if added else '☑️ Уже есть в словаре'
    pair = (f'🇷🇺 <b>{esc(translation)}</b> — {esc(word)}' if direction == 'ru'
            else f'<b>{esc(word)}</b> — {esc(translation)}')
    await message.answer(
        f'{mark}: {pair}\n'
        f'📚 В словаре: {words_count(database.count_words(message.from_user.id))}',
        reply_markup=add_kb if adding else added_kb)


@router.message(AddWords.custom, ~F.text.in_(MENU_BUTTONS))
async def own_translation_not_text(message: Message):
    await message.answer('Напиши перевод текстом 🙂 Или выбери действие в меню.')


# ─── Слова из текста ───────────────────────────────────────

def _item_lines(items, direction, with_ipa=True):
    """Список находок с транскрипцией: «1. looking for /ˈlʊkɪŋ fɔː/ — искал»."""
    lines = []
    for number, item in enumerate(items, start=1):
        ipa = f' {esc(short(item.ipa, 30))}' if with_ipa and item.ipa else ''
        english, russian = esc(short(item.shown, 35)), esc(short(item.translation, 35))
        if direction == 'ru':
            lines.append(f'{number}. 🇷🇺 {russian} — <b>{english}</b>{ipa}')
        else:
            lines.append(f'{number}. <b>{english}</b>{ipa} — {russian}')
    return lines


def _visible_length(lines):
    return len(re.sub(r'<[^>]+>', '', '\n'.join(lines)))


def _text_view(data):
    items, selected = data['items'], data['selected']
    direction = data.get('direction', 'en')
    title = data.get('title') or f'📖 <b>Нашёл в тексте: {len(items)}</b>'
    lines = [title, 'Отметь, что хочешь учить, и нажми «Добавить».']
    if data['known']:
        lines.append(f'☑️ Ещё {data["known"]} уже есть в твоём словаре')
    if data['over']:
        lines.append(f'Показал первые {len(items)}, ещё {data["over"]} не поместились')
    listing = _item_lines(items, direction)
    if _visible_length(lines + listing) > 3900:      # не влезает в сообщение — без транскрипции
        listing = _item_lines(items, direction, with_ipa=False)
    lines += [''] + listing

    def front(item):                             # на кнопке — то, что будет на лицевой стороне
        return item.translation if direction == 'ru' else item.shown

    rows = [[(f'{"☑️" if i in selected else "☐"} {i + 1}. {short(front(item), 40)}', f'tx:{i}')]
            for i, item in enumerate(items)]
    rows.append([(f'✅ Добавить ({len(selected)})', 'tx:add')])
    all_selected = len(selected) == len(items)
    rows.append([('☐ Снять все' if all_selected else '☑️ Выбрать все', 'tx:all'),
                 ('✖️ Отмена', 'tx:cancel')])
    if data.get('source'):
        rows.append([('🤖 Разобрать точнее через ИИ', 'tx:ai')])
    return '\n'.join(lines), inline(*rows)


async def show_extraction(message: Message, text):
    """Экран «Нашёл в тексте» с галочками."""
    user_id = message.from_user.id
    if not dictionary.available():
        await message.answer(f'{NO_DICTIONARY} Разбирать текст пока нечем.\n'
                             'Пришли пары: <code>word - перевод</code>')
        return

    items = dictionary.extract(text)
    known = {w.word.lower() for w in database.get_words(user_id) if w.direction == 'en'}
    fresh = [item for item in items if item.shown.lower() not in known]
    data = {'kind': 'text', 'items': fresh[:dictionary.MAX_ITEMS], 'selected': set(), 'source': text,
            'known': len(items) - len(fresh), 'over': max(0, len(fresh) - dictionary.MAX_ITEMS)}

    if not data['items']:
        note = f'\n☑️ {data["known"]} уже есть в твоём словаре.' if data['known'] else ''
        sent = await message.answer(
            '🤷 Не нашёл в тексте новых слов для изучения.' + note +
            '\nСамые простые слова (the, have, good…) я пропускаю.',
            reply_markup=inline([('🤖 Разобрать через ИИ', 'tx:ai')]))
        _remember(user_id, sent.message_id, data)
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
    direction = data.get('direction', 'en')
    added, duplicates = database.add_words(
        user_id, [Word(item.shown, item.translation, item.ipa, item.sentence, item.sentence_ru, direction)
                  for item in chosen])

    lines = [f'✅ Добавил {words_count(len(added))}:'] if added else []
    if direction == 'ru':
        lines += [f'• 🇷🇺 {esc(short(w.translation, 40))} — <b>{esc(short(w.word, 40))}</b>' for w in added]
    else:
        lines += [f'• <b>{esc(short(w.word, 40))}</b> — {esc(short(w.translation, 40))}' for w in added]
    if duplicates:
        lines.append(f'☑️ Уже были в словаре: {len(duplicates)}')
    lines += ['', f'📚 В словаре: {words_count(database.count_words(user_id))}']
    _forget(callback)
    await show(callback, '\n'.join(lines), added_kb)
    await callback.answer('Добавил ✅')


@router.callback_query(F.data == 'tx:ai')
async def text_ai(callback: CallbackQuery, bot: Bot):
    data = _screen(callback, 'text')
    if data is None or not data.get('source'):
        await callback.answer(STALE, show_alert=True)
        return
    await send_ai_prompt(bot, callback.from_user.id, data['source'])
    await callback.answer()


@router.callback_query(F.data == 'tx:cancel')
async def text_cancel(callback: CallbackQuery):
    _forget(callback)
    await show(callback, 'Хорошо, ничего не добавил.')
    await callback.answer()


# ─── Что прислали: слово или текст ─────────────────────────

SHORT = 4          # до стольких слов без точек — это «слово», длиннее — «текст»


def _as_one_word(text):
    """Показать как одно слово (экран «Выбери значение») или разобрать по частям?

    Одно слово — всегда как слово. Короткая фраза — как слово, только если она
    есть в словаре целиком («look for»). Иначе — по частям, как текст:
    «waiting for people to» → нашёл «waiting for». «Не нашёл» — только если
    по частям тоже ничего нет.
    """
    if len(text.split()) == 1 or not dictionary.available():
        return True
    return bool(dictionary.meanings(text)) or not dictionary.extract(text)


def looks_like_word(text):
    """Слово или короткое выражение (а не текст для разбора)."""
    return len(text.split()) <= SHORT and not re.search(r'[.!?]', text) and _as_one_word(text)


async def show_for_text(message: Message, text):
    """Слово → «Выбери значение», текст → «Нашёл в тексте»."""
    if looks_like_word(text):
        await show_lookup(message, text)
    else:
        await show_extraction(message, text)


# ─── Карточки через любой ИИ ───────────────────────────────

AI_HOWTO = (
    '🤖 <b>Карточки через ИИ</b>\n\n'
    '1. Нажми на промпт ниже — он скопируется\n'
    '2. Вставь его в любой ИИ: ChatGPT, Claude, DeepSeek, Алиса…\n'
    '3. ИИ ответит блоком с карточками. Скопируй его кнопкой «Копировать» над блоком '
    '(так сохранятся переносы строк) и пришли сюда — сделаю карточки'
)
AI_RU_NOTE = '\n\n🇷🇺 Текст на русском — карточки будут <b>с русского на английский</b>.'
MAX_PROMPTS = 10   # больше частей за раз не присылаем — длинную книгу лучше по главам
AI_PROMPT_PASTED = ('🤖 Это сам промпт 🙂 Вставь его в ИИ — ChatGPT, Claude, DeepSeek, Алису, '
                    'а сюда пришли <b>ответ</b> ИИ.')


async def send_ai_prompt(bot: Bot, user_id, text, direction=None):
    """Присылает инструкцию и промпт с текстом пользователя отдельным сообщением.

    Длинный текст — несколько промптов «Промпт 1 из N». direction: 'en' — английский
    текст, 'ru' — русский (карточки с русского), None — определить по тексту.
    """
    if direction is None:
        direction = 'ru' if is_russian(text) else 'en'
    prompts = ai_prompt.build_prompts(text, direction)
    total = len(prompts)
    prompts = prompts[:MAX_PROMPTS]
    # Весь текст — чтобы потом расставить карточки в порядке текста
    database.save_ai_text(user_id, text[:200_000], direction)

    note = AI_RU_NOTE if direction == 'ru' else ''
    if len(prompts) > 1:
        note += (f'\n\n✂️ Текст длинный — разбил на {len(prompts)} '
                 f'{plural(len(prompts), "промпт", "промпта", "промптов")}. Вставляй их по очереди '
                 '(можно в один чат с ИИ), а каждый ответ присылай сюда.')
    if total > MAX_PROMPTS:
        note += f' Это начало текста, остальное пришли потом отдельно.'
    await bot.send_message(user_id, AI_HOWTO + note)
    for number, prompt in enumerate(prompts, start=1):
        header = f'<b>Промпт {number} из {len(prompts)}</b>\n' if len(prompts) > 1 else ''
        await bot.send_message(user_id, f'{header}<pre>{esc(prompt)}</pre>')


@router.callback_query(F.data == 'ai:start')
async def ai_start(callback: CallbackQuery, state: FSMContext):
    """«🤖 Через ИИ» в режиме добавления слов: сначала просим текст."""
    previous = await state.get_state()
    await state.set_state(AddWords.ai)
    await state.update_data(ai_prev=previous)
    await callback.bot.send_message(
        callback.from_user.id,
        '🤖 Пришли текст, пост, песню, слово или файл (.txt, .docx, .pdf) — соберу из него промпт для ИИ.\n'
        'Можно и по-русски — тогда карточки будут с русского на английский.')
    await callback.answer()


@router.message(AddWords.ai, F.text | F.caption, ~F.document, ~F.text.in_(MENU_BUTTONS), ~F.text.startswith('/'))
async def ai_text(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    if data.get('ai_prev') == AddWords.waiting.state:
        await state.set_state(AddWords.waiting)
    else:
        await state.clear()
    await send_ai_prompt(bot, message.from_user.id, message.text or message.caption)


@router.message(AddWords.ai, ~F.document, ~F.text.in_(MENU_BUTTONS))  # файлы — в handlers/add.py
async def ai_not_text(message: Message):
    await message.answer('Пришли текст — из него я соберу промпт для ИИ 🙂')


async def show_ai_cards(message: Message, cards):
    """Экран с галочками из ответа ИИ."""
    user_id = message.from_user.id
    source, direction = database.get_ai_text(user_id)
    known = {w.word.lower() for w in database.get_words(user_id) if w.direction == direction}
    cards = ai_prompt.order_by_text(cards, source, direction)   # в порядке текста
    items = []
    for card in cards:
        word = dictionary.normalize_case(card.phrase)
        if word.lower() in known:
            continue
        items.append(dictionary.Found(word, card.translation, dictionary.transcription(word),
                                      card.sentence, card.sentence_ru))
    limit = ai_prompt.MAX_CARDS
    kind = ' с русского' if direction == 'ru' else ''
    data = {'kind': 'text', 'items': items[:limit], 'selected': set(), 'direction': direction,
            'known': len(cards) - len(items), 'over': max(0, len(items) - limit),
            'title': f'🤖 <b>Карточки от ИИ{kind}: {min(len(items), limit)}</b>'}
    if not data['items']:
        await message.answer('☑️ Все слова из ответа ИИ уже есть в твоём словаре.')
        return
    view, kb = _text_view(data)
    sent = await message.answer(view, reply_markup=kb)
    _remember(user_id, sent.message_id, data)


# ─── С русского на английский ──────────────────────────────

async def offer_russian(message: Message, text):
    """Прислали русское: спрашиваем, делать ли карточки «с русского на английский»."""
    sent = await message.answer(
        f'🇷🇺 <i>{esc(short(text, 200))}</i>\n\n'
        'Это по-русски. <b>Делаем карточки с русского на английский?</b>\n'
        'На карточке будет русское, а вспоминать нужно английское.',
        reply_markup=ru_offer_kb)
    _remember(message.from_user.id, sent.message_id, {'kind': 'ru', 'text': text})


def _looks_like_ru_word(text):
    return len(text.split()) <= 3 and not re.search(r'[.!?]', text)


@router.callback_query(F.data == 'ru:yes')
async def russian_yes(callback: CallbackQuery, bot: Bot):
    data = _screen(callback, 'ru')
    if data is None:
        await callback.answer(STALE, show_alert=True)
        return
    _forget(callback)
    text = data['text']
    if _looks_like_ru_word(text):                # слово — по словарю наоборот
        word = ' '.join(text.lower().split())[:60]
        rdata = {'kind': 'rlookup', 'word': word, 'options': dictionary.reverse_meanings(word), 'added': []}
        view, kb = _reverse_view(rdata)
        shown = await show(callback, view, kb)
        _remember(callback.from_user.id, shown.message_id, rdata)
    else:                                        # текст — через ИИ, отдельным промптом
        await drop_buttons(callback)
        await send_ai_prompt(bot, callback.from_user.id, text, 'ru')
    await callback.answer()


@router.callback_query(F.data == 'ru:no')
async def russian_no(callback: CallbackQuery):
    _forget(callback)
    await show(callback, 'Хорошо 🙂 Пришли английское слово, текст или пары «слово - перевод».')
    await callback.answer()


def _reverse_view(data):
    head = f'🇷🇺 <b>{esc(data["word"])}</b> → 🇬🇧'
    if data['added']:
        chosen = ', '.join(esc(w) for w in data['added'])
        text = f'{head}\n\n✅ Добавил карточку с русского: <b>{esc(data["word"])}</b> — {chosen}\n\nМожно выбрать ещё:'
    elif data['options']:
        text = f'{head}\n\nВыбери английский вариант — сделаю карточку с русского:'
    elif not dictionary.available():
        text = f'{head}\n\n{NO_DICTIONARY} Напиши английский вариант сам или спроси ИИ:'
    else:
        text = f'{head}\n\n😕 Не нашёл в словаре. Напиши английский вариант сам или спроси ИИ:'
    rows = []
    for i, option in enumerate(data['options']):
        mark = '✅ ' if option.translation in data['added'] else ''
        ipa = f' {option.ipa}' if option.ipa else ''
        rows.append([(f'{mark}{short(option.translation, 25)}{short(ipa, 20)} · {option.pos}', f'rk:{i}')])
    rows.append([('✏️ Свой вариант', 'rk:own'), ('🤖 Через ИИ', 'rk:ai')])
    rows.append([('✔️ Готово' if data['added'] else '✖️ Не надо', 'rk:close')])
    return text, inline(*rows)


@router.callback_query(F.data.regexp(r'^rk:\d+$'))
async def reverse_pick(callback: CallbackQuery):
    data = _screen(callback, 'rlookup')
    index = int(callback.data.split(':')[1])
    if data is None or index >= len(data['options']):
        await callback.answer(STALE, show_alert=True)
        return
    option = data['options'][index]
    if option.translation in data['added']:
        await callback.answer('Этот вариант уже добавлен')
        return
    user_id = callback.from_user.id
    added, _ = database.add_words(
        user_id, [Word(option.translation, data['word'], option.ipa, option.example or '', direction='ru')])
    data['added'].append(option.translation)
    view, kb = _reverse_view(data)
    shown = await show(callback, view, kb)
    _remember(user_id, shown.message_id, data)
    await callback.answer('Добавил ✅' if added else 'Уже было в словаре')


@router.callback_query(F.data == 'rk:own')
async def reverse_own(callback: CallbackQuery, state: FSMContext):
    data = _screen(callback, 'rlookup')
    if data is None:
        await callback.answer(STALE, show_alert=True)
        return
    await _ask_own(callback, state, data['word'], direction='ru')


@router.callback_query(F.data == 'rk:ai')
async def reverse_ai(callback: CallbackQuery, bot: Bot):
    data = _screen(callback, 'rlookup')
    if data is None:
        await callback.answer(STALE, show_alert=True)
        return
    await send_ai_prompt(bot, callback.from_user.id, data['word'], 'ru')
    await callback.answer()


@router.callback_query(F.data == 'rk:close')
async def reverse_close(callback: CallbackQuery):
    _forget(callback)
    await drop_buttons(callback)
    await callback.answer()
