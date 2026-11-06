"""Собирает англо-русский словарь (SQLite) из выгрузки английского Wiktionary (kaikki.org).

Сборка — на своём компьютере, займёт пару минут:

    pip install -r tools/requirements.txt
    curl -L -o /tmp/kaikki-English.jsonl.gz \\
        https://kaikki.org/dictionary/English/kaikki.org-dictionary-English.jsonl.gz   # ~534 МБ
    python tools/build_dictionary.py /tmp/kaikki-English.jsonl.gz dictionary.db
    rm /tmp/kaikki-English.jsonl.gz

Готовый dictionary.db (~35 МБ) кладётся рядом с english_bot.py — и в проекте, и на сервере.
В git он не попадает (см. .gitignore): его всегда можно пересобрать.

Что попадает в базу:
  entries      — слово/выражение, часть речи, транскрипция, частотность, английское толкование
  translations — русские переводы, сгруппированные по значениям
  examples     — английские примеры употребления
  forms        — формы слова → словарная форма (found → find, looking → look, keys → key)
                 и частотность самой формы (по ней бот отсеивает слишком простые слова)

Данные Wiktionary распространяются по лицензии CC BY-SA.
"""
import gzip
import json
import re
import sqlite3
import sys
import time
import unicodedata

from wordfreq import zipf_frequency

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


def ipa(rec):
    for snd in rec.get('sounds', []):
        if snd.get('ipa') and snd['ipa'].startswith('/'):
            return snd['ipa']
    return ''


def main(src, dst):
    db = sqlite3.connect(dst)
    db.executescript('''
        DROP TABLE IF EXISTS entries; DROP TABLE IF EXISTS translations;
        DROP TABLE IF EXISTS examples; DROP TABLE IF EXISTS forms;
        CREATE TABLE entries (
            id INTEGER PRIMARY KEY, word TEXT, word_lc TEXT, pos TEXT,
            ipa TEXT, zipf REAL, is_phrase INTEGER, gloss TEXT);
        CREATE TABLE translations (entry_id INTEGER, sense TEXT, ru TEXT);
        CREATE TABLE examples (entry_id INTEGER, text TEXT);
        CREATE TABLE forms (form_lc TEXT, lemma_lc TEXT, tags TEXT, zipf REAL);
    ''')

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
                        and INFLECTION_TAGS & set(tags):
                    pending_forms.append((fw, word_lc, ','.join(tags[:3])))

            groups = order_translations(ru_translations(rec), first_gloss(rec))
            # Фразовые глаголы и выражения берём даже без русского перевода — покажем толкование
            if not groups and not (is_phrase and rec.get('pos') in {'verb', 'phrase', 'prep_phrase', 'adv'}):
                continue

            cur = db.execute(
                'INSERT INTO entries (word, word_lc, pos, ipa, zipf, is_phrase, gloss) VALUES (?,?,?,?,?,?,?)',
                (word, word_lc, rec.get('pos'), ipa(rec), zipf_frequency(word_lc, 'en'),
                 int(is_phrase), first_gloss(rec)))
            entry_id = cur.lastrowid
            for sense, words in groups:
                db.executemany('INSERT INTO translations VALUES (?,?,?)',
                               [(entry_id, sense, w) for w in words])
            db.executemany('INSERT INTO examples VALUES (?,?)', [(entry_id, e) for e in examples(rec)])
            kept += 1
            phrases += is_phrase

    # Оставляем только формы тех слов, которые есть в словаре
    lemmas = {row[0] for row in db.execute('SELECT DISTINCT word_lc FROM entries')}
    forms = {(f, l, t) for f, l, t in pending_forms if l in lemmas}
    db.executemany('INSERT INTO forms VALUES (?,?,?,?)',
                   [(f, l, t, zipf_frequency(f, 'en')) for f, l, t in sorted(forms)])

    db.executescript('''
        CREATE INDEX idx_entries_word ON entries(word_lc);
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
    main(sys.argv[1], sys.argv[2])
