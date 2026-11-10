"""Inline-режим: в любом чате пишешь «@Allomisha_bot слово или фраза».

• Над клавиатурой всплывает список: варианты перевода слова или слова и
  выражения из фразы (с переводом в форме из фразы).
• Кнопка над списком «📥 Выбрать слова и добавить в бота» открывает чат с ботом,
  где ждёт экран с галочками — в переписку при этом ничего не уходит.
• Нажал на строку — в чат уходит карточка с кнопкой «➕ В мой словарь»: она
  добавляет слово тому, кто её нажал (тебе или собеседнику).

Бот видит только то, что написано после его имени (до 256 символов) — сообщения
в чате ему не видны. Карточки и фразы храним в базе, чтобы кнопки работали
и после перезапуска бота.
"""
from aiogram import F, Router
from aiogram.types import (
    CallbackQuery, InlineQuery, InlineQueryResultArticle, InlineQueryResultsButton,
    InputTextMessageContent,
)

from handlers.dictionary import looks_like_word
from utils import database, dictionary
from utils.keyboards import inline
from utils.words import esc, has_latin

router = Router()

MAX_RESULTS = 20


def _variants(text):
    """[(слово, перевод, транскрипция, пример, подпись)] для всплывающего списка."""
    if looks_like_word(text):
        word = dictionary.normalize_case(text)
        ipa = dictionary.transcription(word)
        return [(word, m.translation, ipa or m.ipa, m.example, m.pos + (f', {m.note}' if m.note else ''))
                for m in dictionary.meanings(text)]
    return [(f.shown, f.translation, f.ipa, f.sentence, 'из фразы') for f in dictionary.extract(text)]


def _card_text(word, translation, ipa, example):
    text = f'🇬🇧 <b>{esc(word)}</b>' + (f'  {esc(ipa)}' if ipa else '')
    text += f'\n🇷🇺 {esc(translation)}'
    if example:
        text += f'\n\n💬 <i>{esc(example)}</i>'
    return text


@router.inline_query()
async def inline_lookup(query: InlineQuery):
    text = ' '.join(query.query.split())
    if not text or not has_latin(text):
        await query.answer([], cache_time=60, button=InlineQueryResultsButton(
            text='📖 Напиши английское слово или фразу', start_parameter='inline_help'))
        return

    results, seen = [], set()
    for word, translation, ipa, example, note in _variants(text)[:MAX_RESULTS]:
        card_id = database.save_shared_card(word, translation, ipa, example)
        if card_id in seen:
            continue
        seen.add(card_id)
        results.append(InlineQueryResultArticle(
            id=card_id,
            title=f'{word} — {translation}',
            description=' · '.join(part for part in (ipa, note) if part),
            input_message_content=InputTextMessageContent(
                message_text=_card_text(word, translation, ipa, example), parse_mode='HTML'),
            reply_markup=inline([('➕ В мой словарь', f'sh:{card_id}')]),
        ))

    text_id = database.save_inline_text(text)
    await query.answer(results, cache_time=60, button=InlineQueryResultsButton(
        text='📥 Выбрать слова и добавить в бота' if results else '😕 Не нашёл — открыть бота',
        start_parameter=f'q_{text_id}'))


@router.callback_query(F.data.startswith('sh:'))
async def add_shared_card(callback: CallbackQuery):
    """«➕ В мой словарь» под карточкой в чате — слово получает тот, кто нажал."""
    card = database.get_shared_card(callback.data[3:])
    if card is None:
        await callback.answer('Эта карточка устарела 🤷', show_alert=True)
        return
    user_id = callback.from_user.id
    new_user = not database.user_exists(user_id)
    database.init_user(user_id, callback.from_user.username or '')
    added, _ = database.add_words(user_id, [card])

    if added:
        note = f'✅ Добавил в твой словарь:\n{card.word} — {card.translation}'
    else:
        note = f'☑️ «{card.word}» уже есть в твоём словаре'
    if new_user:
        username = (await callback.bot.me()).username
        note += f'\n\nОткрой @{username} и нажми «Запустить», чтобы учить слова'
    await callback.answer(note[:200], show_alert=True)
