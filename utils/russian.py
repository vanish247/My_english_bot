"""Русские формы слов: перевод ставится в ту же форму, что и английское слово.

    found  (прошедшее от find)  →  «найти»    →  «нашёл»
    goes   (3-е лицо от go)     →  «идти»     →  «идёт»
    keys   (мн. число от key)   →  «ключ»     →  «ключи»
    turned off (прошедшее)      →  «выключить» →  «выключил»

Работает на бесплатной библиотеке pymorphy3. Если её нет, переводы остаются
в начальной форме — бот при этом не ломается.
"""
import logging

log = logging.getLogger(__name__)

_morph = None
_unavailable = False


def _analyzer():
    global _morph, _unavailable
    if _morph is None and not _unavailable:
        try:
            import pymorphy3
            _morph = pymorphy3.MorphAnalyzer()
        except Exception:
            _unavailable = True
            log.warning('pymorphy3 не установлен — переводы будут в начальной форме')
    return _morph


def available():
    return _analyzer() is not None


def inflect(translation, pos, tags):
    """Ставит перевод в форму английского слова.

    translation — русский перевод в начальной форме («найти», «ждать с нетерпением»);
    pos         — часть речи английского слова ('verb', 'noun', …);
    tags        — теги английской формы из словаря: {'past'}, {'plural'}, {'third-person', …}.
    Меняется только первое слово перевода: «ждать с нетерпением» → «ждал с нетерпением».
    """
    morph = _analyzer()
    if morph is None or not tags or not translation:
        return translation

    first, _, rest = translation.partition(' ')
    parses = morph.parse(first)
    result = None
    if pos == 'verb':
        infinitive = next((p for p in parses if p.tag.POS == 'INFN'), None)
        if infinitive is not None:
            if 'past' in tags:
                result = infinitive.inflect({'VERB', 'past', 'masc', 'sing'})
            elif 'third-person' in tags:
                result = (infinitive.inflect({'VERB', 'pres', '3per', 'sing'})
                          or infinitive.inflect({'VERB', 'futr', '3per', 'sing'}))
    elif pos == 'noun' and 'plural' in tags:
        noun = next((p for p in parses if p.tag.POS == 'NOUN' and 'nomn' in p.tag), None)
        if noun is not None:
            result = noun.inflect({'plur', 'nomn'})

    if result is None:
        return translation
    word = result.word.capitalize() if first[:1].isupper() else result.word
    return f'{word} {rest}' if rest else word
