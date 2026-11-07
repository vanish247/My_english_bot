"""Прогон бота без Telegram: подменяем сетевой слой и кормим диспетчер апдейтами.

Запуск из папки проекта:   python3 tests/run_tests.py

Тесты работают во временной папке со своей пустой базой — твоя bot_database.db не трогается.
"""
import asyncio
import os
import re
import shutil
import sys
import tempfile
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(tempfile.mkdtemp(prefix='english_bot_tests_'))
if os.path.exists(os.path.join(ROOT, 'dictionary.db')):          # словарь — если он есть в проекте
    shutil.copy(os.path.join(ROOT, 'dictionary.db'), 'dictionary.db')

os.environ['BOT_TOKEN'] = '123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'

from aiogram.client.session.base import BaseSession  # noqa: E402
from aiogram.types import (Chat, InlineKeyboardMarkup, Message,  # noqa: E402
                           ReplyKeyboardMarkup)

UID = 555
BOT_ID = 123456789


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.next_id = 1000

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        name = type(method).__name__
        if name == 'SendMessage':
            self.next_id += 1
            return Message(message_id=self.next_id, date=datetime.now(),
                           chat=Chat(id=method.chat_id, type='private'), text=method.text)
        if name == 'EditMessageText':
            return Message(message_id=method.message_id, date=datetime.now(),
                           chat=Chat(id=method.chat_id, type='private'), text=method.text)
        return True

    async def stream_content(self, *a, **k):
        raise NotImplementedError

    async def close(self):
        pass


ALLOWED = {'b', 'i', 'u', 's', 'code', 'pre', 'a', 'tg-spoiler'}
TAG = re.compile(r'<(/?)([a-z-]+)[^>]*>')


def check_html(text):
    stack = []
    for closing, tag in TAG.findall(text):
        assert tag in ALLOWED, f'недопустимый тег <{tag}> в: {text!r}'
        if closing:
            assert stack and stack[-1] == tag, f'несбалансированный </{tag}> в: {text!r}'
            stack.pop()
        else:
            stack.append(tag)
    assert not stack, f'незакрытые теги {stack} в: {text!r}'
    rest = TAG.sub('', text)
    assert '<' not in rest and '>' not in rest, f'неэкранированные < > в: {text!r}'
    for amp in re.finditer(r'&', rest):
        assert re.match(r'&(lt|gt|amp|quot);', rest[amp.start():]), f'голый & в: {text!r}'


def check_markup(markup):
    if isinstance(markup, InlineKeyboardMarkup):
        for row in markup.inline_keyboard:
            for btn in row:
                assert len(btn.callback_data.encode()) <= 64, btn.callback_data


uid_counter = [0]


def user():
    return {'id': UID, 'is_bot': False, 'first_name': 'Тест', 'username': 'tester'}


def msg_update(text=None, dice=False):
    uid_counter[0] += 1
    m = {'message_id': uid_counter[0], 'date': int(time.time()),
         'chat': {'id': UID, 'type': 'private'}, 'from': user()}
    if dice:
        m['dice'] = {'emoji': '🎲', 'value': 3}
    else:
        m['text'] = text
    return {'update_id': uid_counter[0], 'message': m}


def cb_update(data, message_id):
    uid_counter[0] += 1
    return {'update_id': uid_counter[0], 'callback_query': {
        'id': str(uid_counter[0]), 'from': user(), 'chat_instance': 'ci', 'data': data,
        'message': {'message_id': message_id, 'date': int(time.time()),
                    'chat': {'id': UID, 'type': 'private'},
                    'from': {'id': BOT_ID, 'is_bot': True, 'first_name': 'Bot'}, 'text': 'x'}}}


async def main():
    import english_bot
    from utils import database, scheduler as sch
    session = FakeSession()
    english_bot.bot.session = session
    bot, dp = english_bot.bot, english_bot.dp

    database.init_db()
    sch.scheduler.start()
    failures = []
    passed = [0]

    async def send(update, label):
        """Отправляет апдейт, проверяет общие правила, возвращает ответы бота."""
        before = len(session.calls)
        await dp.feed_raw_update(bot, update)
        new = session.calls[before:]
        texts = [c for c in new if type(c).__name__ in ('SendMessage', 'EditMessageText')]
        answers = [c for c in new if type(c).__name__ == 'AnswerCallbackQuery']
        is_cb = 'callback_query' in update
        if is_cb:
            assert answers, f'[{label}] на нажатие кнопки нет callback.answer() — крутилка зависнет'
        else:
            assert texts, f'[{label}] БОТ ПРОМОЛЧАЛ'
        for c in texts:
            assert len(c.text) <= 4096, f'[{label}] сообщение длиной {len(c.text)} > 4096'
            check_html(c.text)
            check_markup(c.reply_markup)
        return texts, answers

    async def case(label, fn):
        try:
            await fn()
            passed[0] += 1
            print(f'  ✅ {label}')
        except Exception as e:
            failures.append(label)
            print(f'  ❌ {label}: {e}')

    def last_id():
        sends = [c for c in session.calls if type(c).__name__ == 'SendMessage']
        return session.next_id if sends else 0

    # ───────── сценарии ─────────
    async def t_start():
        texts, _ = await send(msg_update('/start'), 'start')
        assert isinstance(texts[-1].reply_markup, ReplyKeyboardMarkup)
        assert texts[-1].reply_markup.is_persistent
        labels = [b.text for row in texts[-1].reply_markup.keyboard for b in row]
        assert labels == ['📚 Добавить слова', '🎴 Учить', '📋 Мои слова', 'ℹ️ Помощь'], labels
    await case('/start показывает постоянное меню', t_start)

    async def t_study_empty():
        texts, _ = await send(msg_update('🎴 Учить'), 'study empty')
        assert 'пуст' in texts[-1].text and texts[-1].reply_markup is not None
    await case('Учить при пустом словаре → подсказка с кнопкой', t_study_empty)

    async def t_dash_idle():
        texts, _ = await send(msg_update('привет - пока'), 'dash idle')
        assert 'Не понял' in texts[-1].text
    await case('Текст с дефисом вне режима добавления → бот отвечает (раньше молчал)', t_dash_idle)

    async def t_yes_idle():
        texts, _ = await send(msg_update('да'), 'yes idle')
        assert isinstance(texts[-1].reply_markup, ReplyKeyboardMarkup)
    await case('«да» вне диалога → меню, а не «используй формат»', t_yes_idle)

    async def t_add():
        texts, _ = await send(msg_update('📚 Добавить слова'), 'add start')
        assert texts[-1].reply_markup.inline_keyboard[0][0].callback_data == 'add:done'
        texts, _ = await send(msg_update(
            'apple - яблоко, fly-летать, well-known - известный\nrun — бежать, просто текст, , <3 - love & joy'),
            'add words')
        t = texts[-1].text
        assert 'Добавил 5 слов' in t, t
        assert 'просто текст' in t and '&lt;3' in t
        assert database.count_words(UID) == 5
        words = database.get_words(UID)
        pairs = [(w.word, w.translation) for w in words]
        assert ('well-known', 'известный') in pairs and ('run', 'бежать') in pairs, pairs
    await case('Добавление: дефис/тире/без пробелов/перенос строки, кривые пары, спецсимволы', t_add)

    async def t_add_dup():
        texts, _ = await send(msg_update('apple - яблоко, cat - кот'), 'dup')
        assert 'Уже были' in texts[-1].text and database.count_words(UID) == 6
    await case('Повторное слово не дублируется', t_add_dup)

    async def t_add_second_without_yes():
        texts, _ = await send(msg_update('dog - собака'), 'second batch')
        assert 'Добавил 1 слово' in texts[-1].text
    await case('Вторая порция слов без «Да» — принимается (раньше молчал)', t_add_second_without_yes)

    async def t_add_sticker():
        texts, _ = await send(msg_update(dice=True), 'sticker in add')
        assert 'Жду текст' in texts[-1].text
    await case('Стикер/кубик в режиме добавления → подсказка', t_add_sticker)

    async def t_add_huge():
        big = ', '.join(f'word{i} - слово{i}' for i in range(300))
        texts, _ = await send(msg_update(big), 'huge paste')
        assert '…и ещё 290' in texts[-1].text
        assert database.count_words(UID) == 307
    await case('Вставка 300 пар за раз → ответ не превышает лимит', t_add_huge)

    async def t_menu_escape():
        texts, _ = await send(msg_update('📋 Мои слова'), 'menu escape')
        assert 'Твой словарь' in texts[-1].text
        texts, _ = await send(msg_update('cow - корова'), 'after escape')
        data = [b.callback_data for row in texts[-1].reply_markup.inline_keyboard for b in row]
        assert 'add:done' not in data, 'кнопка меню должна выводить из режима добавления'
        assert 'Добавил 1 слово' in texts[-1].text
    await case('Кнопка меню выводит из режима добавления', t_menu_escape)

    async def t_vocab_pages():
        texts, _ = await send(msg_update('/vocabulary'), 'vocab')
        mid = session.next_id
        kb = texts[-1].reply_markup.inline_keyboard
        assert kb[0][1].text == '1 / 16', kb[0][1].text
        texts, _ = await send(cb_update('voc:page:15', mid), 'vocab last page')
        assert '308.' in texts[-1].text
        texts, _ = await send(cb_update('voc:page:99', mid), 'vocab bad page')
        assert '308.' in texts[-1].text
        await send(cb_update('noop', mid), 'noop')
    await case('Словарь по страницам (308 слов → 16 страниц), лишняя страница не ломает', t_vocab_pages)

    async def t_study_flow():
        database.delete_words(UID)
        database.add_words(UID, [database.Word('apple', 'яблоко', '/ˈæp.əl/', 'An apple a day.'),
                                 database.Word('cat', 'кот', '', ''),
                                 database.Word('dog', 'собака', '', '')])
        texts, _ = await send(msg_update('🎴 Учить'), 'study menu')
        mid = session.next_id
        assert 'Как будем учить' in texts[-1].text and '3 слова' in texts[-1].text, texts[-1].text
        data = [b.callback_data for row in texts[-1].reply_markup.inline_keyboard for b in row]
        assert data == ['study:cards', 'study:mailing', 'voc:page:0'], data
        texts, _ = await send(cb_update('study:cards', mid), 'cards')
        assert 'Карточка 1 из 3' in texts[-1].text and '🇷🇺' not in texts[-1].text
        front = [b.callback_data for row in texts[-1].reply_markup.inline_keyboard for b in row]
        assert 'study:dontknow' in front, 'на лицевой стороне должна быть кнопка «Пропустить»'
        texts, _ = await send(cb_update('study:reveal', mid), 'reveal')
        assert '🇷🇺' in texts[-1].text
        await send(cb_update('study:know', mid), 'know')
        texts, _ = await send(cb_update('study:dontknow', mid), 'skip without reveal')
        assert 'Карточка 3 из 3' in texts[-1].text
        await send(cb_update('study:reveal', mid), 'reveal3')
        texts, _ = await send(cb_update('study:know', mid), 'last')
        assert 'завершена' in texts[-1].text and 'Знаю: 2' in texts[-1].text, texts[-1].text
        texts, _ = await send(cb_update('study:menu', mid), 'back to choice')
        assert 'Как будем учить' in texts[-1].text
    await case('Учить → выбор → карточки: показать / пропустить / знаю → итог → назад к выбору', t_study_flow)

    async def t_study_stale():
        texts, _ = await send(msg_update('🎴 Учить'), 'study again')
        await send(cb_update('study:cards', session.next_id), 'cards again')
        _, answers = await send(cb_update('study:reveal', 1), 'old card')
        assert answers[-1].show_alert and 'устарела' in answers[-1].text
        mid = session.next_id
        texts, _ = await send(cb_update('study:stop', mid), 'stop')
        assert 'остановлена' in texts[-1].text
        _, answers = await send(cb_update('study:know', mid), 'after stop')
        assert answers[-1].show_alert
    await case('Старая карточка и нажатия после остановки → «устарела», без падений', t_study_stale)

    async def t_study_text():
        await send(msg_update('🎴 Учить'), 'study for text')
        await send(cb_update('study:cards', session.next_id), 'cards for text')
        texts, _ = await send(msg_update('что-то пишу'), 'text in study')
        assert 'идёт тренировка' in texts[-1].text
    await case('Текст во время тренировки → подсказка', t_study_text)

    async def t_mailing():
        texts, _ = await send(msg_update('🎴 Учить'), 'study menu for mailing')
        mid = session.next_id
        texts, _ = await send(cb_update('study:mailing', mid), 'open mailing')
        assert 'Учить по расписанию' in texts[-1].text and 'выключена' in texts[-1].text
        assert texts[-1].reply_markup.inline_keyboard[-1][0].callback_data == 'study:menu', 'нет кнопки Назад'
        texts, _ = await send(cb_update('mail:30', mid), 'mail on')
        assert 'каждые 30 минут' in texts[-1].text
        assert sch.scheduler.get_job(f'mailing_{UID}') and database.get_mailing(UID) == 30
        before = len(session.calls)
        await sch.send_next_word(bot, UID)
        sent = session.calls[before:][-1]
        check_html(sent.text)
        assert '<tg-spoiler>' in sent.text, sent.text
        texts, _ = await send(cb_update('study:menu', mid), 'back to choice')
        assert 'Рассылка: ✅ каждые 30 минут' in texts[-1].text, texts[-1].text
        texts, _ = await send(cb_update('study:mailing', mid), 'reopen mailing')
        texts, _ = await send(cb_update('mail:off', mid), 'mail off')
        assert sch.scheduler.get_job(f'mailing_{UID}') is None and database.get_mailing(UID) == 0
        _, answers = await send(cb_update('mail:999', mid), 'mail bad')
    await case('Учить → По расписанию → 30 мин → слово со спойлером → статус на экране выбора → выкл', t_mailing)

    async def t_mailing_legacy():
        texts, _ = await send(msg_update('🔔 Рассылка'), 'old mailing button')
        assert 'Учить по расписанию' in texts[-1].text
        texts, _ = await send(msg_update('/mailing'), 'mailing command')
        assert 'Учить по расписанию' in texts[-1].text
        texts, _ = await send(cb_update('study:start', 77), 'old study:start button')
        assert 'Как будем учить' in texts[-1].text
    await case('Кнопки и команды старой версии ведут в новые экраны', t_mailing_legacy)

    async def t_mailing_restore():
        database.set_mailing(UID, 15)
        sch.restore_mailings(bot)
        assert sch.scheduler.get_job(f'mailing_{UID}')
        sch.stop_mailing(UID)
    await case('Рассылка восстанавливается из базы после перезапуска', t_mailing_restore)

    async def t_mailing_index():
        sch._positions[UID] = 50          # позиция больше числа слов
        before = len(session.calls)
        await sch.send_next_word(bot, UID)
        assert session.calls[before:], 'рассылка упала на выходе за границы'
    await case('Рассылка не падает, если слов стало меньше позиции', t_mailing_index)

    async def t_clear():
        database.set_mailing(UID, 5)
        sch._schedule(bot, UID, 5)
        texts, _ = await send(msg_update('📋 Мои слова'), 'vocab for clear')
        mid = session.next_id
        texts, _ = await send(cb_update('voc:clear', mid), 'ask clear')
        assert 'нельзя отменить' in texts[-1].text
        texts, _ = await send(cb_update('voc:clear:yes', mid), 'clear yes')
        assert database.count_words(UID) == 0 and database.get_mailing(UID) == 0
        assert sch.scheduler.get_job(f'mailing_{UID}') is None
    await case('Очистка словаря с подтверждением + выключение рассылки', t_clear)

    async def t_restart_stale():
        _, answers = await send(cb_update('study:reveal', 42), 'stale after restart')
        assert answers[-1].show_alert
        _, answers = await send(cb_update('garbage', 42), 'garbage')
        assert answers[-1].show_alert
    await case('Кнопки после перезапуска бота → «устарела»', t_restart_stale)

    async def t_old_buttons():
        texts, _ = await send(msg_update('📖 Учить'), 'old label')
        assert isinstance(texts[-1].reply_markup, ReplyKeyboardMarkup)
    await case('Кнопка старой версии «📖 Учить» → бот присылает новое меню', t_old_buttons)

    async def t_help_and_commands():
        for t in ('/help', 'ℹ️ Помощь', '/add', '/study', '/mailing', '/unknown'):
            await send(msg_update(t), t)
        await english_bot.bot.set_my_commands(english_bot.COMMANDS)
    await case('Все команды отвечают', t_help_and_commands)

    async def t_add_done():
        await send(msg_update('/add'), 'add for done')
        await send(msg_update('a - б'), 'one word')
        mid = session.next_id
        texts, _ = await send(cb_update('add:done', mid), 'done')
        assert 'Готово' in texts[-1].text and 'Как будем учить' in texts[-1].text, texts[-1].text
        done_id = session.next_id
        texts, _ = await send(cb_update('study:mailing', done_id), 'mailing from done')
        texts, _ = await send(cb_update('mail:15', done_id), 'mail 15 from done')
        assert database.get_mailing(UID) == 15 and sch.scheduler.get_job(f'mailing_{UID}')
        sch.stop_mailing(UID)
        texts, _ = await send(msg_update('x - y'), 'after done')
        data = [b.callback_data for row in texts[-1].reply_markup.inline_keyboard for b in row]
        assert 'add:done' not in data, '«Готово» должно завершать режим добавления'
    await case('«Готово» → «Как будем учить?» → включить рассылку прямо оттуда', t_add_done)

    def cb_list(msg):
        return [b.callback_data for row in msg.reply_markup.inline_keyboard for b in row]

    async def t_mailing_buttons():
        database.delete_words(UID)
        database.add_words(UID, [database.Word('apple', 'яблоко', '', '')])
        sch.start_mailing(bot, UID, 5)
        before = len(session.calls)
        await sch.send_next_word(bot, UID)
        sent = session.calls[before:][-1]
        assert cb_list(sent) == ['mail:stop', 'mail:settings'], cb_list(sent)
        word_msg = session.next_id
        texts, _ = await send(cb_update('mail:settings', word_msg), 'mailing settings from word')
        assert 'Учить по расписанию' in texts[-1].text and 'включена' in texts[-1].text
        _, answers = await send(cb_update('mail:stop', word_msg), 'stop from word')
        assert answers[-1].show_alert and 'остановлена' in answers[-1].text
        assert sch.scheduler.get_job(f'mailing_{UID}') is None and database.get_mailing(UID) == 0
    await case('Под словом рассылки кнопки «Остановить» и «Как часто»', t_mailing_buttons)

    async def start_cards(words):
        database.delete_words(UID)
        database.add_words(UID, [database.Word(w, t, '', '') for w, t in words])
        await send(msg_update('🎴 Учить'), 'study menu')
        mid = session.next_id
        await send(cb_update('study:cards', mid), 'cards')
        return mid

    async def t_card_edit():
        mid = await start_cards([('found', 'основывать')])
        texts, _ = await send(cb_update('study:reveal', mid), 'reveal')
        assert 'study:edit' in cb_list(texts[-1]) and 'study:delete' in cb_list(texts[-1])
        texts, _ = await send(cb_update('study:edit', mid), 'edit')
        assert 'Напиши правильный перевод' in texts[-1].text and 'основывать' in texts[-1].text
        texts, _ = await send(msg_update('нашёл'), 'new translation')
        assert 'Перевод исправлен' in texts[-1].text and 'нашёл' in texts[-1].text
        assert [w.translation for w in database.get_words(UID)] == ['нашёл']
        new_card = session.next_id
        texts, _ = await send(cb_update('study:know', new_card), 'know after edit')
        assert 'завершена' in texts[-1].text and 'Знаю: 1' in texts[-1].text
    await case('Карточка: «Исправить» → новый перевод сохраняется, тренировка продолжается', t_card_edit)

    async def t_card_edit_cancel_and_menu():
        mid = await start_cards([('apple', 'яблоко'), ('cat', 'кот')])
        await send(cb_update('study:reveal', mid), 'reveal')
        await send(cb_update('study:edit', mid), 'edit')
        prompt = session.next_id
        _, answers = await send(cb_update('study:edit_cancel', prompt), 'cancel edit')
        assert 'Оставил' in answers[-1].text
        texts, _ = await send(cb_update('study:know', mid), 'continue after cancel')
        assert 'Карточка 2 из 2' in texts[-1].text
        await send(cb_update('study:reveal', mid), 'reveal 2')
        await send(cb_update('study:edit', mid), 'edit 2')
        texts, _ = await send(msg_update('📋 Мои слова'), 'menu during edit')
        assert 'Твой словарь' in texts[-1].text, 'кнопка меню не должна стать переводом'
        assert {w.translation for w in database.get_words(UID)} == {'яблоко', 'кот'}
    await case('«Не менять» и кнопки меню во время исправления', t_card_edit_cancel_and_menu)

    async def t_card_delete():
        mid = await start_cards([('apple', 'яблоко'), ('cat', 'кот')])
        await send(cb_update('study:reveal', mid), 'reveal')
        texts, answers = await send(cb_update('study:delete', mid), 'delete first')
        assert 'Удалил' in answers[-1].text and 'Карточка 1 из 1' in texts[-1].text
        assert database.count_words(UID) == 1
        await send(cb_update('study:reveal', mid), 'reveal last')
        texts, _ = await send(cb_update('study:delete', mid), 'delete last')
        assert 'завершена' in texts[-1].text and database.count_words(UID) == 0
    await case('Карточка: «Удалить» убирает слово и показывает следующее', t_card_delete)

    from utils import dictionary as dic
    from utils import russian as rus

    def kb_data(msg):
        return [b.callback_data for row in msg.reply_markup.inline_keyboard for b in row]

    def kb_text(msg):
        return [b.text for row in msg.reply_markup.inline_keyboard for b in row]

    async def t_card_fields():
        database.delete_words(UID)
        database.add_words(UID, [database.Word('apple', 'яблоко', '/ˈæp.əl/', 'An apple a day.')])
        await send(msg_update('🎴 Учить'), 'study menu')
        mid = session.next_id
        texts, _ = await send(cb_update('study:cards', mid), 'card')
        assert '/ˈæp.əl/' in texts[-1].text and 'яблоко' not in texts[-1].text
        texts, _ = await send(cb_update('study:reveal', mid), 'reveal')
        assert 'яблоко' in texts[-1].text and 'An apple a day.' in texts[-1].text
        await send(cb_update('study:stop', mid), 'stop')
    await case('Карточка: транскрипция спереди, перевод и пример — после «Показать»', t_card_fields)

    async def t_migration():
        import sqlite3
        with sqlite3.connect(database.DB) as conn:
            conn.execute("INSERT INTO words (user_id, word, translation) VALUES (?, 'old - старый', '')", (UID,))
        database.init_db()
        assert ('old', 'старый') in [(w.word, w.translation) for w in database.get_words(UID)]
    await case('Старые слова «apple - яблоко» переносятся в новые поля', t_migration)

    if not dic.available():
        print('  ⚠️  dictionary.db нет в проекте — тесты словаря пропущены')
    else:
        async def t_lookup_found():
            database.delete_words(UID)
            texts, _ = await send(msg_update('found'), 'lookup found')
            mid = session.next_id
            labels = kb_text(texts[-1])
            target = 'нашёл' if rus.available() else 'найти'
            assert labels[0].startswith(target) and 'от find' in labels[0], labels
            assert any(l.startswith('основ') or l.startswith('учред') for l in labels), labels
            idx = 0
            texts, answers = await send(cb_update(f'lk:{idx}', mid), f'pick {target}')
            assert 'Добавил' in texts[-1].text and f'✅ {target}' in kb_text(texts[-1])[idx]
            assert ('found', target) in [(w.word, w.translation) for w in database.get_words(UID)]
            other = next(i for i, l in enumerate(labels) if 'от find' not in l)
            texts, _ = await send(cb_update(f'lk:{other}', mid), 'pick own meaning')
            assert len(database.get_words(UID)) == 2, 'второе значение тоже добавляется'
            _, answers = await send(cb_update(f'lk:{idx}', mid), 'pick again')
            assert 'уже добавлено' in answers[-1].text
            await send(cb_update('lk:close', mid), 'close')
            _, answers = await send(cb_update('lk:0', mid), 'after close')
            assert answers[-1].show_alert, 'после закрытия кнопки устаревают'
        await case('Слово «found» → первым «нашёл · от find», есть и своё значение, можно добавить оба', t_lookup_found)

        async def t_inflection():
            if not rus.available():
                print('  ⚠️  pymorphy3 не установлен — проверка русских форм пропущена')
                return
            expect = {'went': 'шёл', 'found': 'нашёл', 'saw': 'видел', 'keys': 'ключи',
                      'goes': 'идёт', 'took': 'взял', 'children': 'дети', 'put up with': 'терпеть'}
            got = {w: dic.meanings(w)[0].translation for w in expect}
            assert got == expect, got
            labels = [m.translation for m in dic.meanings('went')]
            assert not any(l.startswith('очеред') for l in labels), 'у формы глагола нет существительных'
            found = {i.shown: i.translation for i in dic.extract('I turned the music off and went to bed.')}
            assert found == {'turned off': 'выключил', 'went to bed': 'лёг спать'}, found
        await case('Перевод в форме слова: went → шёл, found → нашёл, keys → ключи, turned off → выключил', t_inflection)

        async def t_no_be_phrases():
            assert dic.extract('I am there for you.') == []
            assert any(i.shown == 'has to' for i in dic.extract('He has to go.'))
        await case('«I am there» не превращается в идиому «быть рядом», «has to» находится', t_no_be_phrases)

        async def t_lookup_phrase():
            texts, _ = await send(msg_update('Looking For'), 'lookup phrase')
            assert any(l.startswith('искать') for l in kb_text(texts[-1])), kb_text(texts[-1])
        await case('Выражение в форме из текста «Looking For» → «искать»', t_lookup_phrase)

        async def t_lookup_own():
            texts, _ = await send(msg_update('qwertyzz'), 'lookup unknown')
            mid = session.next_id
            assert 'Не нашёл' in texts[-1].text and 'lk:own' in kb_data(texts[-1])
            texts, _ = await send(cb_update('lk:own', mid), 'own')
            assert 'Напиши перевод' in texts[-1].text
            texts, _ = await send(msg_update('📋 Мои слова'), 'menu during own')
            assert 'Твой словарь' in texts[-1].text, 'кнопка меню не должна стать переводом'
            await send(cb_update('lk:own', mid), 'own again')
            texts, _ = await send(msg_update('моё слово'), 'own translation')
            assert 'Добавил' in texts[-1].text
            assert ('qwertyzz', 'моё слово') in [(w.word, w.translation) for w in database.get_words(UID)]
            texts, _ = await send(msg_update('ещё текст'), 'after own')
            assert 'Не понял' in texts[-1].text, 'после своего перевода бот возвращается в обычный режим'
        await case('Нет в словаре → «Свой перевод»; кнопки меню при этом работают', t_lookup_own)

        async def t_own_in_add_mode():
            await send(msg_update('/add'), 'add mode')
            await send(msg_update('zzqqxx'), 'unknown in add mode')
            await send(cb_update('lk:own', session.next_id), 'own in add')
            texts, _ = await send(msg_update('перевод'), 'own translation in add')
            assert 'add:done' in kb_data(texts[-1]), 'после своего перевода остаёмся в режиме добавления'
            await send(cb_update('add:done', session.next_id), 'done')
        await case('«Свой перевод» в режиме добавления возвращает в режим добавления', t_own_in_add_mode)

        async def t_extract():
            database.delete_words(UID)
            database.add_words(UID, [database.Word('couch', 'диван', '', '')])
            text = ("I've been looking for my keys all morning and finally found them under the couch. "
                    "I turned the music off and went to bed.")
            texts, _ = await send(msg_update(text), 'extract')
            mid = session.next_id
            labels = kb_text(texts[-1])
            assert any('looking for — искать' in l for l in labels), labels
            assert any('turned off' in l for l in labels) and any('went to bed' in l for l in labels), labels
            assert not any('couch' in l for l in labels), 'слова из словаря не предлагаются'
            assert 'уже есть в твоём словаре' in texts[-1].text
            _, answers = await send(cb_update('tx:add', mid), 'add nothing')
            assert 'Сначала отметь' in answers[-1].text
            await send(cb_update('tx:0', mid), 'toggle 0')
            texts, _ = await send(cb_update('tx:1', mid), 'toggle 1')
            assert '✅ Добавить (2)' in kb_text(texts[-1])
            texts, _ = await send(cb_update('tx:add', mid), 'add 2')
            assert 'Добавил 2 слова' in texts[-1].text, texts[-1].text
            words = database.get_words(UID)
            looking = next(w for w in words if w.word == 'looking for')
            assert looking.translation == 'искать' and 'keys all morning' in looking.example, looking
            _, answers = await send(cb_update('tx:0', mid), 'after add')
            assert answers[-1].show_alert
        await case('Текст → нашёл выражения (и разорванное turned … off), галочки → добавил с примером', t_extract)

        async def t_extract_all_and_simple():
            texts, _ = await send(msg_update('Tomorrow I will figure out the weekend plan, she said.'), 'extract 2')
            mid = session.next_id
            n = sum(1 for d in kb_data(texts[-1]) if d.startswith('tx:') and d[3:].isdigit())
            texts, _ = await send(cb_update('tx:all', mid), 'select all')
            assert f'✅ Добавить ({n})' in kb_text(texts[-1])
            texts, _ = await send(cb_update('tx:cancel', mid), 'cancel')
            assert 'ничего не добавил' in texts[-1].text
            texts, _ = await send(msg_update('We like it here, but the old house is small.'), 'only simple words')
            assert 'Не нашёл в тексте новых слов' in texts[-1].text
        await case('Текст: «Выбрать все», «Отмена», текст из одних простых слов', t_extract_all_and_simple)

        async def t_caption():
            uid_counter[0] += 1
            upd = {'update_id': uid_counter[0], 'message': {
                'message_id': uid_counter[0], 'date': int(time.time()), 'chat': {'id': UID, 'type': 'private'},
                'from': user(), 'caption': 'ubiquitous',
                'photo': [{'file_id': 'x', 'file_unique_id': 'y', 'width': 1, 'height': 1}]}}
            texts, _ = await send(upd, 'photo with caption')
            assert any(l.startswith('вездесущий') for l in kb_text(texts[-1]))
        await case('Пост с картинкой: слово берётся из подписи', t_caption)

    async def t_lowercase():
        database.delete_words(UID)
        await send(msg_update('Apple - Яблоко, CAT - кот'), 'pairs with capitals')
        words = {w.word for w in database.get_words(UID)}
        assert words == {'apple', 'cat'}, words
        await send(msg_update('apple - яблоко'), 'same word lowercase')
        assert database.count_words(UID) == 2, 'Apple и apple — одно слово'
        if dic.available():
            await send(msg_update('London - Лондон'), 'proper noun')
            assert 'London' in {w.word for w in database.get_words(UID)}, 'имена собственные — с заглавной'
            texts, _ = await send(msg_update('Tomorrow'), 'lookup capitalized')
            assert '<b>tomorrow</b>' in texts[-1].text
            await send(cb_update('lk:0', session.next_id), 'pick')
            assert 'tomorrow' in {w.word for w in database.get_words(UID)}
    await case('Слова сохраняются маленькими буквами, кроме имён собственных (London)', t_lowercase)

    async def t_lowercase_migration():
        import sqlite3
        database.delete_words(UID)
        with sqlite3.connect(database.DB) as conn:
            conn.executemany("INSERT INTO words (user_id, word, translation) VALUES (?, ?, ?)",
                             [(UID, 'Dog', 'собака'), (UID, 'dog', 'собака'), (UID, 'Bird', 'птица')])
        database.init_db()
        assert sorted((w.word, w.translation) for w in database.get_words(UID)) == \
            [('bird', 'птица'), ('dog', 'собака')], database.get_words(UID)
    await case('Уже добавленные слова переводятся в нижний регистр, дубли склеиваются', t_lowercase_migration)

    async def t_no_archaic():
        if not dic.available():
            return
        assert dic.meanings('art')[0].translation == 'искусство'
        assert all(i.translation != 'быть' for i in dic.extract('I am interested in art.'))
    await case('Старинные формы не мешают: art → искусство, а не «быть» (thou art)', t_no_archaic)

    async def t_no_dictionary():
        saved = dic.DICT_PATH
        dic.DICT_PATH = 'нет-такого-файла.db'
        try:
            texts, _ = await send(msg_update('serendipity'), 'lookup without dictionary')
            assert 'не подключён' in texts[-1].text and 'lk:own' in kb_data(texts[-1])
            texts, _ = await send(msg_update('This is a long English text about many things.'), 'text without dict')
            assert 'не подключён' in texts[-1].text
        finally:
            dic.DICT_PATH = saved
    await case('Без dictionary.db бот не падает и предлагает ввести перевод сам', t_no_dictionary)

    sch.scheduler.shutdown(wait=False)
    print(f'\nИТОГ: {passed[0]} прошло, {len(failures)} упало')
    sys.exit(1 if failures else 0)


asyncio.run(main())
