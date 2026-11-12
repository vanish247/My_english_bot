"""Все клавиатуры бота.

Два вида кнопок:
• Reply-клавиатура (main_menu) — постоянное меню внизу экрана.
  Нажатие отправляет боту текст кнопки, как будто его напечатали.
• Inline-кнопки — прикреплены к конкретному сообщению.
  Нажатие присылает боту скрытую строку callback_data, например 'study:reveal'.
"""
from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup,
)

# Тексты кнопок меню. Хэндлеры сравнивают с этими же константами —
# поэтому текст кнопки достаточно поменять в одном месте.
BTN_STUDY = '🎴 Учить'
BTN_WORDS = '📋 Мои слова'
BTN_AI = '🤖 Через ИИ'
BTN_SONG = '🎵 Песня'
BTN_ADD = '➕ Добавить'
BTN_HELP = 'ℹ️ Помощь'
# Кнопки прошлых версий: у кого-то ещё висит старое меню
OLD_BTN_ADD = '📚 Добавить слова'
OLD_BTN_MAILING = '🔔 Рассылка'
MENU_BUTTONS = {BTN_STUDY, BTN_WORDS, BTN_AI, BTN_SONG, BTN_ADD, BTN_HELP, OLD_BTN_ADD, OLD_BTN_MAILING}

# Самое частое — наверху. Слово, текст, файл бот понимает и без кнопок — об этом
# напоминает серая подсказка в поле ввода.
main_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_STUDY), KeyboardButton(text=BTN_WORDS)],
        [KeyboardButton(text=BTN_AI), KeyboardButton(text=BTN_SONG)],
        [KeyboardButton(text=BTN_ADD), KeyboardButton(text=BTN_HELP)],
    ],
    resize_keyboard=True,
    is_persistent=True,                        # меню не сворачивается
    input_field_placeholder='Пришли слово, текст, файл или mp3…',
)


def inline(*rows):
    """Собирает inline-клавиатуру. Каждый ряд — список пар (текст, callback_data)."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=text, callback_data=data) for text, data in row]
        for row in rows
    ])


# ─── Добавление слов ───────────────────────────────────────
empty_kb = inline([('➕ Как добавить слова', 'add:start')])
# Под сообщением «Добавил…»
added_kb = inline([('🎴 Учить', 'study:menu'), ('📋 Мои слова', 'voc:page:0')])

# Русский текст: делаем карточки «с русского на английский»?
ru_offer_kb = inline([('✅ Да, с русского', 'ru:yes'), ('✖️ Нет', 'ru:no')])


# ─── «Как будем учить?» ────────────────────────────────────
def study_menu_kb(unknown=0, known=0, mailing=''):
    """Экран «🎴 Учить». Первая кнопка — то, что нужно чаще всего:
    незнакомые, если часть слов уже знаешь, иначе — начать по порядку."""
    if known and unknown:
        rows = [[(f'▶️ Незнакомые ({unknown})', 'study:unknown')],
                [('📋 Все по порядку', 'study:cards'), ('🔀 Вперемешку', 'study:shuffle')]]
    else:
        rows = [[('▶️ Повторить все' if known else '▶️ Начать', 'study:cards')],
                [('🔀 Вперемешку', 'study:shuffle')]]
    rows.append([(f'🔔 Рассылка: {mailing or "выкл"}', 'study:mailing')])
    return inline(*rows)

# ─── Карточки ──────────────────────────────────────────────
card_front_kb = inline(
    [('👀 Показать перевод', 'study:reveal'), ('⏹', 'study:stop')],
)
card_back_kb = inline(
    [('✅ Знаю', 'study:know'), ('🔁 Не знаю', 'study:dontknow')],
    [('✏️ Изменить', 'study:edit'), ('🗑', 'study:delete'), ('⏹', 'study:stop')],
)
def study_done_kb(mode='cards'):
    """После тренировки: «Ещё раз» — в том же режиме (по порядку или вперемешку)."""
    return inline([('🔁 Ещё раз', f'study:{mode}'), ('◀️ К выбору', 'study:menu')])


# ─── Словарь ───────────────────────────────────────────────
def vocabulary_kb(page, pages):
    rows = []
    if pages > 1:
        rows.append([
            ('◀️', f'voc:page:{page - 1}') if page > 0 else ('·', 'noop'),
            (f'{page + 1} / {pages}', 'noop'),
            ('▶️', f'voc:page:{page + 1}') if page < pages - 1 else ('·', 'noop'),
        ])
    rows.append([('✏️ Изменить', f've:p:{page}'), ('🗑 Удалить', 'voc:del')])
    rows.append([('📊 Статистика', 'study:stats')])
    return inline(*rows)


confirm_clear_kb = inline(
    [('🗑 Да, удалить всё', 'voc:clear:yes')],
    [('◀️ Отмена', 'voc:page:0')],
)

# ─── Рассылка по расписанию ────────────────────────────────
# (минуты, текст кнопки, как писать в статусе)
INTERVALS = [
    (5, '5 мин', 'каждые 5 минут'),
    (15, '15 мин', 'каждые 15 минут'),
    (30, '30 мин', 'каждые 30 минут'),
    (60, '1 час', 'каждый час'),
    (180, '3 часа', 'каждые 3 часа'),
]
PHRASES = {minutes: phrase for minutes, _, phrase in INTERVALS}


# Под каждым словом рассылки
mailing_word_kb = inline([('🔕 Остановить', 'mail:stop'), ('⚙️ Как часто', 'mail:settings')])


def mailing_kb(current):
    """Выбор интервала. Текущий отмечен галочкой."""
    buttons = [
        (('✅ ' if minutes == current else '') + label, f'mail:{minutes}')
        for minutes, label, _ in INTERVALS
    ]
    rows = [buttons[:3], buttons[3:]]
    if current:
        rows.append([('🔕 Выключить рассылку', 'mail:off')])
    rows.append([('◀️ Назад', 'study:menu')])
    return inline(*rows)
