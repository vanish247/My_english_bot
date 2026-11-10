"""Карточки через любой ИИ (ChatGPT, Claude, DeepSeek, Алиса…).

Бот собирает промпт с текстом пользователя → пользователь вставляет его в любой ИИ →
пересылает ответ боту → бот разбирает строки и делает карточки.

Формат ответа — простые строки через «|», а не JSON: с ним справляется даже простой ИИ.
Разбор терпимый: понимает ответ с лишним текстом, нумерацией, таблицей и даже
ответ одним абзацем без переносов строк.
"""
import re
from collections import namedtuple

from utils.words import has_latin

Card = namedtuple('Card', 'phrase translation sentence sentence_ru')

_RULES = '''СТРОГИЕ ПРАВИЛА:
- Весь ответ — внутри одного блока кода (```), чтобы его можно было скопировать одной кнопкой.
- Каждая карточка — с новой строки. Ровно 4 поля, разделитель « | ». Внутри полей символ | не используй.
- Никаких заголовков, нумерации, таблиц, жирного шрифта и пояснений до или после блока кода.
- Карточки — строго в том порядке, в котором фразы встречаются в тексте, сверху вниз.
- Не больше 40 карточек. Если находок больше — оставь самые полезные, но порядок текста сохрани.

ТЕКСТ:
<<<
{text}
>>>

Ответь только блоком кода с карточками.'''

PROMPT = '''Ты — составитель карточек для изучения английских слов. Из текста ниже выбери то, что стоит выучить русскоязычному ученику, и оформи каждую находку как карточку.

ЧТО ВЫБИРАТЬ:
1. Фразовые глаголы: look for, give up, go on, made my way back.
2. Глаголы и прилагательные с предлогами: wait for, depend on, afraid of.
3. Устойчивые выражения и идиомы: face to face, came as a surprise.
4. Нечастые слова: roamed, gazed.

ЧТО ПРОПУСКАТЬ: только артикли (a, an, the) и повторы. Больше ничего не пропускай — даже простое может оказаться важным.
ПРОСТЫЕ СЛОВА (go, people, good, have…) бери не отдельно, а вместе с тем, с чем они стоят в тексте: go to the park, people say, good at, have to.

ГЛАВНОЕ — ПЕРЕВОД ПО КОНТЕКСТУ:
- Переводи значение фразы именно в этом предложении, а не первое значение из словаря: run a company — «руководить компанией», а не «бегать»; I caught a cold — «простудился», а не «поймал холод».
- Порядок работы: сначала переведи предложение целиком так, как сказал бы носитель русского языка, а потом возьми из этого перевода кусок, который соответствует фразе. Перевод фразы должен совпадать по смыслу с тем, как она переведена в предложении.
- Идиомы и выражения переводи по смыслу, а не по словам: break the ice — «разрядить обстановку».
- Время, число и форма — как в тексте: went → пошёл, keys → ключи.

ФОРМАТ — одна карточка на строку, ровно 4 поля через « | »:
фраза | перевод | предложение | перевод предложения

- фраза — как в тексте и в той же форме, маленькими буквами; выражение целиком.
- перевод — по-русски, в смысле этого предложения. Если после глагола нужно дополнение, подскажи в скобках: ждать (кого-то).
- предложение — целиком предложение или строка песни из текста, где встретилась фраза. Не сокращай и не меняй его.
- перевод предложения — естественный перевод на русский.

ПРИМЕР ОТВЕТА:
```
looking for | искал (кого-то, что-то) | I've been looking for my keys all morning. | Я всё утро искал ключи.
came as a surprise | стало неожиданностью | Which came as a surprise | Что стало неожиданностью.
roamed | скитался | For years and years I roamed | Годами я скитался.
```

''' + _RULES

# Русский текст → карточки «с русского на английский». Поля в том же порядке, что и у
# английского промпта: английская фраза | русская фраза | английское предложение | русское предложение
PROMPT_RU = '''Ты — составитель карточек для изучения английского. Ниже текст на русском языке. Выбери в нём слова и выражения, которые русскоязычному ученику полезно научиться говорить по-английски, и оформи каждую находку как карточку.

ЧТО ВЫБИРАТЬ: слова и выражения, устойчивые обороты, глаголы с управлением (ждать кого-то → wait for), идиомы. Простые слова бери вместе с тем, с чем они стоят в тексте. Пропускай только повторы.

ГЛАВНОЕ — ПЕРЕВОД ПО КОНТЕКСТУ:
- Сначала переведи на английский всё предложение естественно, как сказал бы носитель, а потом возьми из этого перевода английский эквивалент фразы.
- Идиомы — английским аналогом по смыслу, а не дословно: «как снег на голову» → out of the blue.
- Время и форма — как в тексте: искала → was looking for, ключи → keys.

ФОРМАТ — одна карточка на строку, ровно 4 поля через « | »:
английская фраза | русская фраза как в тексте | английский перевод предложения | исходное русское предложение

ПРИМЕР ОТВЕТА:
```
was looking for | искала | I was looking for my keys all day. | Я весь день искала ключи.
out of the blue | как снег на голову | The news came out of the blue. | Новость свалилась как снег на голову.
```

''' + _RULES

# Сколько текста помещается, чтобы промпт влез в одно сообщение Telegram (4096 символов)
MAX_TEXT = 4000 - len(PROMPT)
MAX_TEXT_RU = 4000 - len(PROMPT_RU)
# По этим фразам узнаём, что пользователь прислал боту сам промпт, а не ответ ИИ
_PROMPT_MARKERS = ('составитель карточек', 'ЧТО ВЫБИРАТЬ', 'СТРОГИЕ ПРАВИЛА')

_PREFIX = re.compile(r'^\s*(?:[-*•–—]|\d+[.)])\s+')       # «1. », «- », «• »
_SEPARATOR = re.compile(r'^[\s:\-–—]*$')                   # строка таблицы «---|---»
_HEADER_WORDS = {'фраза', 'слово', 'выражение', 'phrase', 'word', 'expression'}
# Граница карточек внутри поля, когда ИИ ответил одним абзацем:
# «…Мы встретились на лестнице. spoke of» — русский текст, а за ним английская фраза
_BOUNDARY = re.compile(r"^(?P<ru>.*[А-Яа-яЁё][^A-Za-z]*?)\s+(?P<en>[A-Za-z][A-Za-z0-9\s'’\-]*)$")


MAX_CARDS = 40


def _norm(text):
    """Для поиска в тексте: маленькие буквы, одинаковые апострофы, один пробел."""
    return ' '.join(text.lower().replace('’', "'").replace('‘', "'").split())


def order_by_text(cards, source, direction='en'):
    """Расставляет карточки в порядке, в котором фразы встречаются в исходном тексте.

    Ищем предложение карточки в тексте, внутри предложения — саму фразу, чтобы
    несколько карточек из одного предложения тоже шли по порядку. Карточки,
    которых в тексте не нашлось, — в конце, в том порядке, как их дал ИИ.
    """
    if not source:
        return list(cards)
    text = _norm(source)
    not_found = len(text) + 1

    def position(card):
        if direction == 'ru':                          # исходный текст русский
            sentence, phrase = _norm(card.sentence_ru), _norm(card.translation)
        else:
            sentence, phrase = _norm(card.sentence), _norm(card.phrase)
        start = text.find(sentence) if sentence else -1
        if start >= 0:
            offset = sentence.find(phrase)
            return start + max(offset, 0)
        start = text.find(phrase)
        return start if start >= 0 else not_found

    indexed = list(enumerate(cards))
    indexed.sort(key=lambda pair: (position(pair[1]), pair[0]))
    return [card for _, card in indexed]


def split_text(text, limit):
    """Делит длинный текст на куски не длиннее limit — по предложениям и строкам песни."""
    units = [u for u in re.split(r'(?<=[.!?…])\s+|\n+', text.strip()) if u.strip()]
    chunks, current = [], ''
    for unit in units:
        while len(unit) > limit:                       # очень длинное «предложение» — режем по словам
            head = unit[:limit].rsplit(' ', 1)[0] or unit[:limit]
            if current:
                chunks.append(current)
                current = ''
            chunks.append(head)
            unit = unit[len(head):].strip()
        joined = f'{current}\n{unit}' if current else unit
        if len(joined) > limit:
            chunks.append(current)
            current = unit
        else:
            current = joined
    if current:
        chunks.append(current)
    return chunks or ['']


def build_prompts(text, direction='en'):
    """Промпты с текстом пользователя внутри. Длинный текст — несколько промптов по очереди.
    direction='ru' — русский текст, карточки «с русского на английский»."""
    template, limit = (PROMPT_RU, MAX_TEXT_RU) if direction == 'ru' else (PROMPT, MAX_TEXT)
    return [template.format(text=chunk) for chunk in split_text(text, limit)]


def build_prompt(text, direction='en'):
    """Первый промпт и признак, что текст не влез в один промпт."""
    prompts = build_prompts(text, direction)
    return prompts[0], len(prompts) > 1


def is_prompt(text):
    """Прислали боту сам промпт (а не ответ ИИ)?"""
    return sum(marker in text for marker in _PROMPT_MARKERS) >= 2


def _split_cards(line):
    """Строка, в которой склеились несколько карточек, → по строке на карточку."""
    if line.count('|') <= 4:
        return [line]
    cards, fields = [], []
    for part in (p.strip() for p in line.split('|')):
        if not part:
            continue                                   # пустое поле «| |»
        boundary = _BOUNDARY.match(part) if len(fields) >= 2 else None
        if boundary:
            fields.append(boundary.group('ru'))
            cards.append(' | '.join(fields))
            fields = [boundary.group('en')]
        else:
            fields.append(part)
    if fields:
        cards.append(' | '.join(fields))
    return cards


def parse_answer(text):
    """Ответ ИИ → список Card. Пустой список — это не ответ ИИ.

    Пропускается всё лишнее, что ИИ любит добавлять: вступление и пояснения
    (в них нет «|»), нумерация, **жирный**, `код`, ```блоки кода```, заголовок
    и разделитель таблицы, крайние «|» у таблиц, пустые поля «| |».
    Ответ одним абзацем делится на карточки по смене русского текста на английский.
    """
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if '|' not in line or line.startswith('```'):
            continue
        line = _PREFIX.sub('', line).replace('**', '').replace('`', '').strip().strip('|')
        lines += _split_cards(line)

    cards, seen = [], set()
    for line in lines:
        parts = [part.strip() for part in line.split('|') if part.strip()]
        if len(parts) < 2 or all(_SEPARATOR.match(part) for part in parts):
            continue
        phrase, translation = parts[0], parts[1]
        if phrase.lower() in _HEADER_WORDS or not has_latin(phrase) or phrase.lower() in seen:
            continue
        seen.add(phrase.lower())
        cards.append(Card(
            phrase=' '.join(phrase.split())[:60],
            translation=translation[:100],
            sentence=parts[2][:300] if len(parts) > 2 else '',
            sentence_ru=' | '.join(parts[3:])[:300] if len(parts) > 3 else '',
        ))
    return cards
