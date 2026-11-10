"""Собирает англо-русский словарь (SQLite) из выгрузки английского Wiktionary (kaikki.org).

Сборка — на своём компьютере, займёт пару минут:

    pip install -r tools/requirements.txt
    # английский Викисловарь (~534 МБ)
    curl -L -o /tmp/en.jsonl.gz https://kaikki.org/dictionary/English/kaikki.org-dictionary-English.jsonl.gz
    # английские слова в русском Викисловаре (~10 МБ)
    curl -L -o /tmp/ru.jsonl.gz "https://kaikki.org/ruwiktionary/%D0%90%D0%BD%D0%B3%D0%BB%D0%B8%D0%B9%D1%81%D0%BA%D0%B8%D0%B9/kaikki.org-dictionary-%D0%90%D0%BD%D0%B3%D0%BB%D0%B8%D0%B9%D1%81%D0%BA%D0%B8%D0%B9.jsonl.gz"
    # словарь произношения CMU (~3,5 МБ)
    curl -L -o /tmp/cmudict.dict https://raw.githubusercontent.com/cmusphinx/cmudict/master/cmudict.dict
    python tools/build_dictionary.py /tmp/en.jsonl.gz dictionary.db --ru /tmp/ru.jsonl.gz --cmu /tmp/cmudict.dict
    rm /tmp/en.jsonl.gz /tmp/ru.jsonl.gz /tmp/cmudict.dict

Готовый dictionary.db (~55 МБ) кладётся рядом с english_bot.py — и в проекте, и на сервере.
В git он не попадает (см. .gitignore): его всегда можно пересобрать.

Что попадает в базу:
  entries      — слово/выражение, часть речи, транскрипция, частотность, английское толкование
  translations — русские переводы, сгруппированные по значениям
  examples     — английские примеры употребления
  forms        — формы слова → словарная форма (found → find, looking → look, keys → key)
                 и частотность самой формы (по ней бот отсеивает слишком простые слова)
  pron         — британская транскрипция любого слова и формы (looking → /ˈlʊkɪŋ/)

Источники переводов: английский Викисловарь (таблицы переводов) и русский Викисловарь
(у английских слов значения сразу по-русски). Главный перевод выбирается по согласию двух
словарей: bus → «автобус», а не «шина»; dog → «собака», а не «пёс».

Данные Wiktionary распространяются по лицензии CC BY-SA.
"""
import argparse
import gzip
import json
import os
import re
import sqlite3
import sys
import time
import unicodedata

from wordfreq import zipf_frequency

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pronunciation import load_cmu  # noqa: E402

SKIP_POS = {'name', 'symbol', 'character', 'suffix', 'prefix', 'affix', 'infix',
            'interfix', 'punct', 'circumfix', 'combining_form', 'romanization'}
SKIP_SENSE_TAGS = {'obsolete', 'archaic', 'rare', 'dated'}
WORD_RE = re.compile(r"^[a-z][a-z' .-]*[a-z]$|^[a-z]$")
MAX_RU_PER_SENSE = 4
INFLECTION_TAGS = {'plural', 'past', 'participle', 'present', 'third-person', 'gerund',
                   'comparative', 'superlative', 'singular'}
MAX_SENSES = 6


def clean_ru(text):
    """Убирает знаки ударения: слова́рь → словарь."""
    text = unicodedata.normalize('NFD', text)
    text = text.replace('́', '').replace('̀', '')
    text = unicodedata.normalize('NFC', text)
    text = re.sub(r'\s+[A-Za-z][A-Za-z.\s]*$', '', text)     # «сдать pf trans.» → «сдать»
    return text.strip(' ,;')


def is_cyrillic(text):
    return any('а' <= ch.lower() <= 'я' or ch.lower() == 'ё' for ch in text)


def ru_translations(rec):
    """[(толкование значения, [переводы]), ...] — из общего списка и из значений."""
    groups = {}
    order = []

    def add(sense, word):
        word = clean_ru(word or '')
        if not word or not is_cyrillic(word) or len(word) > 60:
            return
        sense = (sense or '').strip()
        if sense not in groups:
            groups[sense] = []
            order.append(sense)
        if word not in groups[sense]:
            groups[sense].append(word)

    for t in rec.get('translations', []):
        if t.get('lang_code') == 'ru' or t.get('code') == 'ru':
            add(t.get('sense'), t.get('word'))
    for s in rec.get('senses', []):
        if SKIP_SENSE_TAGS & set(s.get('tags') or []):
            continue
        gloss = (s.get('glosses') or [''])[0]
        for t in s.get('translations', []):
            if t.get('lang_code') == 'ru' or t.get('code') == 'ru':
                add(gloss[:80], t.get('word'))
    return [(sense, groups[sense]) for sense in order]


def order_translations(groups, main_gloss):
    """Порядок, в котором бот предлагает переводы.

    В Wiktionary таблицы переводов идут не по популярности: у «go» первой стоит
    сленговое «to say» → «говорить». Поэтому:
      • первой ставим таблицу главного значения статьи (её заголовок почти
        совпадает с первым толкованием: «To move, either physically…»);
      • внутри значения первым — самый употребительный русский перевод
        («терпеть» чаще, чем «сносить»).
    """
    main = (main_gloss or '').lower()[:25]
    ordered = sorted(groups, key=lambda group: not (main and group[0].lower()[:25] == main))
    return [(sense, sorted(words, key=lambda w: -zipf_frequency(w, 'ru'))[:MAX_RU_PER_SENSE])
            for sense, words in ordered[:MAX_SENSES]]


def first_gloss(rec):
    for s in rec.get('senses', []):
        if s.get('form_of') or SKIP_SENSE_TAGS & set(s.get('tags') or []):
            continue
        if s.get('glosses'):
            return s['glosses'][0][:200]
    return ''


def examples(rec, limit=2):
    out = []
    for s in rec.get('senses', []):
        for ex in s.get('examples', []):
            text = (ex.get('text') or '').strip()
            if 15 <= len(text) <= 160 and text not in out:
                out.append(text)
            if len(out) >= limit:
                return out
    return out


# Приводим к привычной школьной британской записи: went /went/, couch /kaʊtʃ/
_IPA_SCHOOL = {'ɹ': 'r', 'g': 'ɡ', 'ɛ': 'e', 'ɝ': 'ɜː', 'ɚ': 'ə', 'ɐ': 'ʌ', 'ɫ': 'l', 'ɾ': 't',
               '\u0361': '', '\u032f': '', '\u0329': '', '\u02de': '', '.': '', '‿': ''}


def _clean_ipa(text):
    """'/ˈdɪk.ʃə.nə.ɹi/' → '/ˈdɪkʃənəri/' — без точек между слогами, с привычной «r».

    Только первый вариант и без мусора: «/əˈkʌmpəni, əˈkʌmpni/» → /əˈkʌmpəni/,
    «/ˈænθəm/ [ˈæn̪θəm]» → /ˈænθəm/, «/ˈmʌndeɪ]/» → /ˈmʌndeɪ/, «'blɪsfəl» → ˈblɪsfəl.
    """
    text = text.strip()
    for old, new in _IPA_SCHOOL.items():
        text = text.replace(old, new)
    text = text.replace("'", 'ˈ').replace('ʹ', 'ˈ')
    match = re.match(r'[/\[]([^/\[\],]+)', text)
    if not match:
        return ''
    body = re.sub(r'[()]', '', match.group(1)).strip()
    return f'/{body}/' if body and not body.startswith('-') else ''


def ipa(rec):
    """Британская транскрипция из английского Викисловаря (с пометкой UK/RP), иначе ''."""
    for snd in rec.get('sounds', []):
        tags = ' '.join(snd.get('tags') or [])
        if snd.get('ipa') and snd['ipa'].startswith('/') and ('UK' in tags or 'Received' in tags):
            return _clean_ipa(snd['ipa'])
    return ''


def ipa_any(rec):
    for snd in rec.get('sounds', []):
        if snd.get('ipa') and snd['ipa'].startswith('/'):
            return _clean_ipa(snd['ipa'])
    return ''


# ─── Русский Викисловарь ───────────────────────────────────

_RU_LABELS = re.compile(r'\b(?:[а-яё]{1,10}\.)(?:\s*,\s*[а-яё]{1,10}\.)*\s*', re.I)   # зоол., перен., неисч.


def _clean_ru_gloss(gloss):
    """'перен., бран. собака, подлец (о человеке)' → ['собака', 'подлец']."""
    g = unicodedata.normalize('NFD', gloss).replace('\u0301', '').replace('\u0300', '')
    g = unicodedata.normalize('NFC', g)
    g = re.sub(r'\([^)]*\)', '', g)
    g = _RU_LABELS.sub('', g)
    g = re.sub(r'^(?:и|или)\s+', '', g.strip())
    g = re.sub(r'\s+во всех значениях.*$', '', g)
    return [p.strip() for p in re.split(r'[,;]', g) if p.strip() and is_cyrillic(p)]


def load_ru_wiktionary(path):
    """Английские слова из русского Викисловаря: {(слово, часть речи): [[переводы значения], …]}
    и {слово: транскрипция}."""
    senses, sounds = {}, {}
    with gzip.open(path, 'rt', encoding='utf-8') as f:
        for line in f:
            rec = json.loads(line)
            word = (rec.get('word') or '').lower()
            for s in rec.get('senses', []):
                parts = _clean_ru_gloss((s.get('glosses') or [''])[0])
                if parts:
                    senses.setdefault((word, rec.get('pos')), []).append(parts)
            for snd in rec.get('sounds', []):
                if snd.get('ipa') and word not in sounds:
                    cleaned = _clean_ipa(snd['ipa'])
                    if cleaned:
                        sounds[word] = cleaned
    return senses, sounds


def _norm(text):
    return text.lower().replace('ё', 'е').strip()


def merge_with_ru(groups, ru_senses):
    """Главный перевод — по согласию английского и русского Викисловарей.

    • главный перевод русского есть среди английских → он первым (dog → «собака»);
    • первый английский подтверждается русским → оставляем как есть;
    • иначе первым ставим английский перевод, который подтверждает русский;
    • иначе — главный русский, если он короткий (а не описание вроде
      «колесное транспортное средство»).
    Слово без английских переводов получает переводы из русского.
    """
    senses = ru_senses[:2]
    if not senses:
        return groups
    if not groups:
        return [('', [w for w in s if len(w.split()) <= 3][:MAX_RU_PER_SENSE]) for s in ru_senses[:3]
                if any(len(w.split()) <= 3 for w in s)]
    ru_main = senses[0][0]
    ru_words = {_norm(w) for s in senses for w in s}
    flat = [w for _, ws in groups for w in ws]
    main = next((w for w in flat if _norm(w) == _norm(ru_main)), None)
    if main is None:
        if _norm(groups[0][1][0]) in ru_words:
            return groups
        main = next((w for w in flat if _norm(w) in ru_words), None)
        if main is None:
            if len(ru_main.split()) > 2:
                return groups
            main = ru_main
    rest = [(s, [w for w in ws if _norm(w) != _norm(main)]) for s, ws in groups]
    return [('', [main])] + [(s, ws) for s, ws in rest if ws]


def ru_key(translation):
    """Для поиска с русского: 'ждать (кого-то, что-то)' → 'ждать'."""
    t = re.sub(r'\([^)]*\)', '', translation).split(';')[0]
    return _norm(t)


PHRASES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'phrases.txt')


def load_phrases(db):
    """Свои сочетания с предлогами (tools/data/phrases.txt).

    Записываются в базу раньше Wiktionary, поэтому у них меньшие номера, и бот
    предлагает их первыми: «look at» → «смотреть на», а не редкое «светить».
    """
    count = 0
    with open(PHRASES_FILE, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            phrase, pos, translation = (part.strip() for part in line.split('|'))
            cur = db.execute(
                'INSERT INTO entries (word, word_lc, pos, ipa, zipf, is_phrase, gloss) VALUES (?,?,?,?,?,?,?)',
                (phrase, phrase.lower(), pos, '', zipf_frequency(phrase.lower(), 'en'),
                 int(' ' in phrase), translation))
            db.execute('INSERT INTO translations VALUES (?,?,?,?)',
                       (cur.lastrowid, '', translation, ru_key(translation)))
            count += 1
    print(f'  свои сочетания: {count}', flush=True)


def main(src, dst, ru_path=None, cmu_path=None):
    db = sqlite3.connect(dst)
    db.executescript('''
        DROP TABLE IF EXISTS entries; DROP TABLE IF EXISTS translations;
        DROP TABLE IF EXISTS examples; DROP TABLE IF EXISTS forms; DROP TABLE IF EXISTS pron;
        CREATE TABLE entries (
            id INTEGER PRIMARY KEY, word TEXT, word_lc TEXT, pos TEXT,
            ipa TEXT, zipf REAL, is_phrase INTEGER, gloss TEXT);
        CREATE TABLE translations (entry_id INTEGER, sense TEXT, ru TEXT, ru_lc TEXT);
        CREATE TABLE examples (entry_id INTEGER, text TEXT);
        CREATE TABLE forms (form_lc TEXT, lemma_lc TEXT, tags TEXT, zipf REAL);
        CREATE TABLE pron (word_lc TEXT PRIMARY KEY, ipa TEXT);
    ''')

    ru_senses, ru_sounds = load_ru_wiktionary(ru_path) if ru_path else ({}, {})
    print(f'  русский Викисловарь: {len(ru_senses):,} статей', flush=True)
    cmu = load_cmu(cmu_path) if cmu_path else {}
    print(f'  словарь произношения CMU: {len(cmu):,} слов', flush=True)
    uk_ipa, any_ipa = {}, {}

    load_phrases(db)
    started = time.time()
    seen = kept = phrases = 0
    pending_forms = []
    with gzip.open(src, 'rt', encoding='utf-8') as f:
        for line in f:
            seen += 1
            if seen % 200000 == 0:
                print(f'  обработано {seen:,} записей, оставлено {kept:,} ({time.time() - started:.0f} с)', flush=True)
            rec = json.loads(line)
            if rec.get('lang_code') != 'en':
                continue
            # Имена собственные (месяцы, страны) — только частые
            if rec.get('pos') in SKIP_POS and not (
                    rec.get('pos') == 'name' and zipf_frequency((rec.get('word') or '').lower(), 'en') >= 4.0):
                continue
            senses = rec.get('senses', [])
            # «went to bed = форма от go to bed» — служебные записи без своего значения пропускаем
            if senses and all(s.get('form_of') or 'form-of' in (s.get('tags') or []) for s in senses):
                continue
            word = (rec.get('word') or '').strip()
            word_lc = word.lower()
            if not WORD_RE.match(word_lc) or len(word_lc) > 40:
                continue
            is_phrase = ' ' in word_lc

            # Формы слова (found ← find) берём из словарной статьи
            for form in rec.get('forms', []):
                fw = (form.get('form') or '').strip().lower()
                tags = form.get('tags') or []
                if fw and fw != word_lc and WORD_RE.match(fw) and ' ' not in fw \
                        and INFLECTION_TAGS & set(tags) \
                        and not {'second-person', 'singular'} <= set(tags) \
                        and not {'archaic', 'obsolete', 'dialectal'} & set(tags):   # thou art, hast
                    pending_forms.append((fw, word_lc, ','.join(tags[:3])))

            groups = order_translations(ru_translations(rec), first_gloss(rec))
            # Русский Викисловарь — только для обычной записи слова: у DOG (аббревиатура)
            # и Dog с заглавной своих русских переводов нет, иначе «собака» → DOG
            if groups or word == word_lc:
                groups = merge_with_ru(groups, ru_senses.get((word_lc, rec.get('pos')), []))
            if not is_phrase:
                if ipa(rec):
                    uk_ipa.setdefault(word_lc, ipa(rec))
                if ipa_any(rec):
                    any_ipa.setdefault(word_lc, ipa_any(rec))
            # Фразовые глаголы и выражения берём даже без русского перевода — покажем толкование
            if not groups and not (is_phrase and rec.get('pos') in {'verb', 'phrase', 'prep_phrase', 'adv'}):
                continue

            cur = db.execute(
                'INSERT INTO entries (word, word_lc, pos, ipa, zipf, is_phrase, gloss) VALUES (?,?,?,?,?,?,?)',
                (word, word_lc, rec.get('pos'), '', zipf_frequency(word_lc, 'en'),
                 int(is_phrase), first_gloss(rec)))
            entry_id = cur.lastrowid
            for sense, words in groups:
                db.executemany('INSERT INTO translations VALUES (?,?,?,?)',
                               [(entry_id, sense, w, ru_key(w)) for w in words])
            db.executemany('INSERT INTO examples VALUES (?,?)', [(entry_id, e) for e in examples(rec)])
            kept += 1
            phrases += is_phrase

    # Оставляем только формы тех слов, которые есть в словаре
    lemmas = {row[0] for row in db.execute('SELECT DISTINCT word_lc FROM entries')}
    forms = {(f, l, t) for f, l, t in pending_forms if l in lemmas}
    db.executemany('INSERT INTO forms VALUES (?,?,?,?)',
                   [(f, l, t, zipf_frequency(f, 'en')) for f, l, t in sorted(forms)])

    # Транскрипция: британская из английского Викисловаря → из русского → из CMU → любая
    pron = dict(any_ipa)
    pron.update(cmu)
    pron.update(ru_sounds)
    pron.update(uk_ipa)
    pron.update({w: ipa_ for w, ipa_ in cmu.items() if len(w) == 1})   # I /aɪ/, a /ə/ — из CMU
    db.executemany('INSERT OR REPLACE INTO pron VALUES (?,?)', sorted(pron.items()))
    db.execute('UPDATE entries SET ipa = COALESCE((SELECT ipa FROM pron WHERE pron.word_lc = entries.word_lc), \'\')')
    print(f'  транскрипций: {len(pron):,}', flush=True)

    db.executescript('''
        CREATE INDEX idx_entries_word ON entries(word_lc);
        CREATE INDEX idx_tr_ru ON translations(ru_lc);
        CREATE INDEX idx_tr_entry ON translations(entry_id);
        CREATE INDEX idx_ex_entry ON examples(entry_id);
        CREATE INDEX idx_forms_form ON forms(form_lc);
    ''')
    db.commit()
    db.execute('VACUUM')
    db.close()
    print(f'ГОТОВО за {time.time() - started:.0f} с: записей прочитано {seen:,}, '
          f'в словаре {kept:,} (из них выражений {phrases:,}), форм {len(forms):,}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Сборка англо-русского словаря для бота')
    parser.add_argument('src', help='английский Викисловарь (kaikki, .jsonl.gz)')
    parser.add_argument('dst', help='куда сохранить dictionary.db')
    parser.add_argument('--ru', help='английские слова из русского Викисловаря (kaikki, .jsonl.gz)')
    parser.add_argument('--cmu', help='словарь произношения CMU (cmudict.dict)')
    args = parser.parse_args()
    main(args.src, args.dst, args.ru, args.cmu)
