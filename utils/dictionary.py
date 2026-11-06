"""Оффлайн англо-русский словарь: перевод слов и поиск слов/выражений в тексте.

Данные — файл dictionary.db рядом с ботом (собирается tools/build_dictionary.py
из Wiktionary). Если файла нет, бот работает как раньше: available() вернёт False,
а пользователь вводит переводы вручную.
"""
import logging
import os
import re
import sqlite3
from collections import namedtuple
from functools import lru_cache
from itertools import product

log = logging.getLogger(__name__)

DICT_PATH = 'dictionary.db'
SIMPLE_ZIPF = 5.5      # слова частотнее этого считаем слишком простыми для разбора текста
MAX_TEXT = 3000        # дальше этого символа текст не разбираем
MAX_ITEMS = 25         # больше кнопок в одном сообщении не показываем
PER_ENTRY = 3          # значений с одной словарной статьи
PARTICLES = {'up', 'down', 'out', 'off', 'on', 'in', 'away', 'back', 'over',
             'around', 'about', 'through', 'along'}
POS_RU = {
    'noun': 'сущ.', 'verb': 'гл.', 'adj': 'прил.', 'adv': 'нар.', 'prep': 'предл.',
    'pron': 'мест.', 'conj': 'союз', 'intj': 'межд.', 'num': 'числ.', 'det': 'опр.',
    'particle': 'част.', 'phrase': 'выраж.', 'prep_phrase': 'выраж.', 'name': 'имя',
}

# Значение слова для выбора кнопкой
Meaning = namedtuple('Meaning', 'translation pos note ipa example')
# Слово или выражение, найденное в тексте
Found = namedtuple('Found', 'shown translation ipa sentence')

TOKEN_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)*")
SENTENCE_RE = re.compile(r'[^.!?\n]+[.!?]*')

_conn = None
_phrases = None          # все выражения словаря (look for, give up, …) — держим в памяти


def available():
    return os.path.exists(DICT_PATH)


def _db():
    global _conn, _phrases
    if _conn is None:
        _conn = sqlite3.connect(f'file:{DICT_PATH}?mode=ro', uri=True, check_same_thread=False)
        _phrases = {row[0] for row in _conn.execute('SELECT word_lc FROM entries WHERE is_phrase = 1')}
        log.info('Словарь подключён: %d выражений', len(_phrases))
    return _conn


@lru_cache(maxsize=20000)
def _entries(word_lc):
    return _db().execute(
        'SELECT id, word, pos, ipa FROM entries WHERE word_lc = ? ORDER BY id', (word_lc,)).fetchall()


@lru_cache(maxsize=20000)
def _senses(entry_id):
    """Переводы статьи по значениям: [[перевод, перевод], [перевод], ...]"""
    groups = {}
    for sense, ru in _db().execute(
            'SELECT sense, ru FROM translations WHERE entry_id = ? ORDER BY rowid', (entry_id,)):
        groups.setdefault(sense, []).append(ru)
    return list(groups.values())


@lru_cache(maxsize=20000)
def _example(entry_id):
    row = _db().execute('SELECT text FROM examples WHERE entry_id = ? LIMIT 1', (entry_id,)).fetchone()
    return row[0] if row else ''


@lru_cache(maxsize=20000)
def _lemmas(word_lc):
    """Словарные формы: found → find, keys → key."""
    return [row[0] for row in _db().execute(
        'SELECT DISTINCT lemma_lc FROM forms WHERE form_lc = ?', (word_lc,))]


@lru_cache(maxsize=20000)
def _has_translation(word_lc):
    return _db().execute(
        'SELECT 1 FROM entries e JOIN translations t ON t.entry_id = e.id WHERE e.word_lc = ? LIMIT 1',
        (word_lc,)).fetchone() is not None


@lru_cache(maxsize=20000)
def _zipf(word_lc):
    """Насколько слово частое (чем больше, тем проще): 7 — «the», 3 — «ubiquitous»."""
    row = _db().execute(
        'SELECT MAX(z) FROM (SELECT zipf AS z FROM entries WHERE word_lc = ? '
        'UNION ALL SELECT zipf FROM forms WHERE form_lc = ?)', (word_lc, word_lc)).fetchone()
    return row[0] or 0


def _phrase_keys(words):
    """Словарные выражения для набора слов: ['looking', 'for'] → ['look for']."""
    candidates = [[w] + _lemmas(w) for w in words]
    keys = [' '.join(combo) for combo in product(*candidates)]
    found = [k for k in keys if k in _phrases]
    return sorted(found, key=lambda k: not _has_translation(k))   # с переводом — первыми


def meanings(text, limit=8):
    """Варианты перевода слова или короткого выражения. Пустой список — не нашли."""
    if not available():
        return []
    _db()
    query = ' '.join(TOKEN_RE.findall(text.lower()))
    if not query:
        return []

    sources = []                                  # (ключ статьи, пометка)
    if _entries(query):
        sources.append((query, None))
    if ' ' in query:
        sources += [(key, None) for key in _phrase_keys(query.split()) if key != query]
    else:
        sources += [(lemma, f'от {lemma}') for lemma in _lemmas(query)]

    # С каждой статьи — первые переводы её значений; статьи чередуем,
    # чтобы у «found» рядом оказались и «основывать», и «находить»
    per_entry = []
    for key, note in sources:
        for entry_id, _, pos, ipa in _entries(key):
            example = _example(entry_id)
            items = [Meaning(group[0], POS_RU.get(pos, pos), note, ipa, example)
                     for group in _senses(entry_id)[:PER_ENTRY]]
            if items:
                per_entry.append(items)

    result, seen = [], set()
    for level in range(PER_ENTRY):
        for items in per_entry:
            if level < len(items) and items[level].translation.lower() not in seen:
                seen.add(items[level].translation.lower())
                result.append(items[level])
    return result[:limit]


def _sentence_of(text, position):
    for match in SENTENCE_RE.finditer(text):
        if match.start() <= position < match.end():
            return match.group().strip()[:200]
    return ''


def extract(text):
    """Слова и выражения из текста, которые стоит учить. Порядок — как в тексте."""
    if not available():
        return []
    _db()
    text = text[:MAX_TEXT]
    tokens = list(TOKEN_RE.finditer(text))
    low = [t.group().lower() for t in tokens]
    used, found = set(), []

    def add(first, shown, key):
        options = meanings(key, limit=1)
        if options:
            found.append((tokens[first].start(), Found(
                shown, options[0].translation, options[0].ipa,
                _sentence_of(text, tokens[first].start()))))

    # 1. Выражения из 4, 3, 2 слов подряд: «looking forward to», «put up with», «went to bed»
    for size in (4, 3, 2):
        for i in range(len(tokens) - size + 1):
            if any(j in used for j in range(i, i + size)):
                continue
            keys = _phrase_keys(low[i:i + size])
            if keys and _has_translation(keys[0]):
                shown = ' '.join(t.group() for t in tokens[i:i + size])
                add(i, shown, keys[0])
                used.update(range(i, i + size))

    # 2. Разорванные фразовые глаголы: «turned the music off» → turned off
    for i in range(len(tokens)):
        for gap in (1, 2):
            k = i + gap + 1
            if k < len(tokens) and low[k] in PARTICLES and i not in used and k not in used:
                keys = _phrase_keys([low[i], low[k]])
                if keys and _has_translation(keys[0]):
                    add(i, f'{tokens[i].group()} {tokens[k].group()}', keys[0])
                    used.update({i, k})
                    break

    # 3. Отдельные слова, кроме слишком простых
    for i, word in enumerate(low):
        if i in used or len(word) < 3 or _zipf(word) >= SIMPLE_ZIPF:
            continue
        add(i, tokens[i].group(), word)

    result, seen = [], set()
    for _, item in sorted(found, key=lambda pair: pair[0]):
        if item.shown.lower() not in seen:
            seen.add(item.shown.lower())
            result.append(item)
    return result
