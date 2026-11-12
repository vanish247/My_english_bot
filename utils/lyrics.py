"""Тексты песен из LRCLIB (lrclib.net) — бесплатной открытой базы, без ключей.

Базу целиком не храним (это ~46 ГБ): на каждую песню — один запрос в интернет.
Если сервис не ответил, бот просто попросит вставить текст сам.
"""
import logging
import re
from collections import namedtuple

import aiohttp

log = logging.getLogger(__name__)

API = 'https://lrclib.net/api/search'
HEADERS = {'User-Agent': 'EnglishWordsTelegramBot/1.0'}   # сервис просит представляться
TIMEOUT = 10
MAX_RESULTS = 6

Song = namedtuple('Song', 'artist title lyrics duration')

_AUDIO_EXT = re.compile(r'\.(mp3|m4a|aac|ogg|oga|opus|flac|wav|wma)$', re.I)


class LyricsUnavailable(Exception):
    """Сервис не ответил: нет сети, лимит запросов, сбой на их стороне."""


async def _fetch(params):
    """Запрос к API → список записей (JSON). Отдельная функция, чтобы подменять в тестах."""
    timeout = aiohttp.ClientTimeout(total=TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout, headers=HEADERS) as session:
        async with session.get(API, params=params) as response:
            response.raise_for_status()
            return await response.json(content_type=None)


async def search(query='', artist='', title='', duration=None):
    """Песни с текстом: [Song], лучшие первыми. Пустой список — не нашли.

    query — свободный поиск («david bowie man who sold the world»); artist/title — из
    свойств mp3. duration — длительность файла: из одинаковых песен выше та,
    чья длительность ближе (у концертной версии другой текст и длина).
    """
    attempts = []
    if title:
        attempts.append({'track_name': title, **({'artist_name': artist} if artist else {})})
        attempts.append({'q': f'{artist} {title}'.strip()})
    if query:
        attempts.append({'q': query})
    try:
        for params in attempts:
            songs = _songs(await _fetch(params))
            if songs:
                break
        else:
            return []
    except Exception as error:
        log.warning('LRCLIB не ответил: %s', error)
        raise LyricsUnavailable() from error
    if duration:
        songs.sort(key=lambda s: abs((s.duration or 0) - duration) > 3)   # стабильно: остальное по релевантности
    return songs[:MAX_RESULTS]


def _songs(records):
    songs, seen = [], set()
    for record in records if isinstance(records, list) else []:
        lyrics = (record.get('plainLyrics') or '').strip()
        if not lyrics or record.get('instrumental'):
            continue
        key = ((record.get('artistName') or '').lower(), (record.get('trackName') or '').lower())
        if key in seen:
            continue
        seen.add(key)
        songs.append(Song(record.get('artistName') or '', record.get('trackName') or '', lyrics,
                          record.get('duration')))
    return songs


def from_filename(name):
    """«David Bowie - The Man Who Sold the World.mp3» → строка для поиска (или '')."""
    name = _AUDIO_EXT.sub('', name or '')
    name = re.sub(r'^\d{1,3}[\s._-]+', '', name)            # «03. Song» → «Song»
    return ' '.join(re.sub(r'[_\-–—]+', ' ', name).split())


def is_audio_name(name):
    return bool(_AUDIO_EXT.search(name or ''))
