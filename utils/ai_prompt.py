"""Карточки через любой ИИ (ChatGPT, Claude, DeepSeek, Алиса…).

Бот собирает промпт с текстом пользователя → пользователь вставляет его в любой ИИ →
пересылает ответ боту → бот разбирает строки и делает карточки.

Формат ответа — простые строки через «|», а не JSON: с ним справляется даже простой ИИ.
"""
import re
from collections import namedtuple

from utils.words import has_latin

MAX_TEXT = 2500          # длиннее текст обрезаем, чтобы промпт влез в одно сообщение Telegram

Card = namedtuple('Card', 'phrase translation sentence sentence_ru')

PROMPT = '''Ты помогаешь учить английские слова. Найди в тексте ниже то, что стоит выучить: фразовые глаголы (look for, give up), глаголы с предлогами (wait for, depend on), устойчивые выражения и идиомы, а также нечастые слова. Самые простые слова (the, have, good, go) пропускай.

Ответ — только строки, без нумерации, заголовков и пояснений. Каждая строка строго в формате:
фраза | перевод | предложение | перевод предложения

Правила:
- фраза — как в тексте, маленькими буквами (кроме имён собственных); фразовые глаголы и выражения целиком: looking for, put up with
- перевод — по-русски, именно в этом контексте и в той же форме, что в тексте: went → пошёл, keys → ключи; у глаголов с предлогом в скобках подскажи, что идёт дальше: ждать (кого-то)
- предложение — предложение из текста, где встретилась фраза
- перевод предложения — естественный перевод на русский
- не больше 30 строк

Пример строки:
looking for | искал (кого-то, что-то) | I've been looking for my keys all morning. | Я всё утро искал ключи.

Текст:
"""
{text}
"""'''

_PREFIX = re.compile(r'^\s*(?:[-*•–—]|\d+[.)])\s+')       # «1. », «- », «• »
_SEPARATOR = re.compile(r'^[\s:\-–—]*$')                   # строка таблицы «---|---»
_HEADER_WORDS = {'фраза', 'слово', 'выражение', 'phrase', 'word', 'expression'}


def build_prompt(text):
    """Промпт с текстом пользователя внутри."""
    text = text.strip()
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT].rsplit(' ', 1)[0] + ' …'
    return PROMPT.format(text=text)


def parse_answer(text):
    """Строки ответа ИИ → список Card. Пустой список — это не ответ ИИ.

    Лишнее ИИ любит добавлять — всё это пропускается: вступление и пояснения
    (в них нет «|»), нумерация, **жирный**, `код`, ```блоки кода```, заголовок
    и разделитель таблицы, крайние «|» у таблиц в markdown.
    """
    cards, seen = [], set()
    for raw in text.splitlines():
        line = raw.strip()
        if '|' not in line or line.startswith('```'):
            continue
        line = _PREFIX.sub('', line).replace('**', '').replace('`', '').strip().strip('|')
        parts = [part.strip() for part in line.split('|')]
        if len(parts) < 2 or all(_SEPARATOR.match(part) for part in parts):
            continue
        phrase, translation = parts[0], parts[1]
        if phrase.lower() in _HEADER_WORDS or not has_latin(phrase) or not translation:
            continue
        if phrase.lower() in seen:
            continue
        seen.add(phrase.lower())
        cards.append(Card(
            phrase=' '.join(phrase.split())[:60],
            translation=translation[:100],
            sentence=parts[2][:300] if len(parts) > 2 else '',
            sentence_ru=' | '.join(parts[3:])[:300] if len(parts) > 3 else '',
        ))
    return cards
