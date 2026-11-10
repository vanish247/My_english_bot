"""Перевод записи словаря произношения CMU в британскую транскрипцию.

CMU Pronouncing Dictionary — бесплатный, 134 тыс. слов вместе с формами (looking,
went, keys), но американский и в своей записи: «car → K AA1 R». Переводим в
привычную британскую транскрипцию: /kɑː/.

Британский вариант получаем правилами — не идеально, но единообразно:
• «r» не читается, если за ним нет гласной: car /kɑː/, card /kɑːd/;
• ear/air/our/ire: here /hɪə/, care /keə/, tour /tʊə/, fire /ˈfaɪə/;
• американское «o» как в hot → британское /ɒ/ (если в написании есть «o»);
• /oʊ/ → /əʊ/: go /ɡəʊ/.
"""
import re

CONSONANTS = {
    'B': 'b', 'CH': 'tʃ', 'D': 'd', 'DH': 'ð', 'F': 'f', 'G': 'ɡ', 'HH': 'h', 'JH': 'dʒ',
    'K': 'k', 'L': 'l', 'M': 'm', 'N': 'n', 'NG': 'ŋ', 'P': 'p', 'R': 'r', 'S': 's',
    'SH': 'ʃ', 'T': 't', 'TH': 'θ', 'V': 'v', 'W': 'w', 'Y': 'j', 'Z': 'z', 'ZH': 'ʒ',
}
VOWELS = {
    'AE': 'æ', 'AO': 'ɔː', 'AW': 'aʊ', 'AY': 'aɪ', 'EH': 'e', 'EY': 'eɪ', 'IH': 'ɪ',
    'IY': 'iː', 'OW': 'əʊ', 'OY': 'ɔɪ', 'UH': 'ʊ', 'UW': 'uː',
}
# С каких сочетаний согласных может начинаться английский слог — перед ними ставится ударение:
# problem /ˈprɒbləm/, beautiful /ˈbjuːtɪfəl/, computer /kəmˈpjuːtə/, understand /ʌndəˈstænd/
ONSETS = {
    'pr', 'br', 'tr', 'dr', 'kr', 'ɡr', 'fr', 'θr', 'ʃr', 'pl', 'bl', 'kl', 'ɡl', 'fl', 'sl',
    'sp', 'st', 'sk', 'sm', 'sn', 'sw', 'tw', 'dw', 'kw', 'ɡw', 'θw', 'sf',
    'pj', 'bj', 'tj', 'dj', 'kj', 'ɡj', 'mj', 'nj', 'fj', 'vj', 'hj', 'lj', 'sj', 'θj',
    'spr', 'str', 'skr', 'spl', 'skw', 'spj', 'stj', 'skj',
}

# Гласная + «r» без гласной после неё — британские сочетания
BEFORE_R = {'IY': 'ɪə', 'IH': 'ɪə', 'EH': 'eə', 'EY': 'eə', 'UH': 'ʊə', 'UW': 'ʊə',
            'AY': 'aɪə', 'AW': 'aʊə', 'AO': 'ɔː', 'OW': 'ɔː', 'AA': 'ɑː'}


def _vowel(base, stress, word):
    if base == 'AH':
        return 'ə' if stress == '0' else 'ʌ'
    if base == 'ER':
        return 'ə' if stress == '0' else 'ɜː'
    if base == 'AA':
        if any(x in word for x in ('au', 'aw', 'ough')):
            return 'ɔː'                              # caught /kɔːt/, bought /bɔːt/
        # hot /hɒt/, want /wɒnt/, но father /ˈfɑːðə/
        return 'ɒ' if ('o' in word or 'wa' in word or 'qua' in word) else 'ɑː'
    return VOWELS[base]


def to_british(word, arpabet):
    """('car', 'K AA1 R') → '/kɑː/'."""
    phones = arpabet.split()
    units = []                                       # (звук, это гласная?, ударение)
    i = 0
    while i < len(phones):
        p = phones[i]
        m = re.match(r'^([A-Z]+)([012]?)$', p)
        base, stress = m.group(1), m.group(2)
        if stress:                                   # гласная
            nxt = phones[i + 1] if i + 1 < len(phones) else ''
            after = phones[i + 2] if i + 2 < len(phones) else ''
            r_then_consonant = nxt == 'R' and not re.search(r'\d', after)
            if r_then_consonant and base in BEFORE_R:
                units.append((BEFORE_R[base], True, stress))
                i += 2                               # «r» съедается
                continue
            sound = _vowel(base, stress, word.lower())
            w = word.lower()
            # dog, long, strong, coffee, lost, cloth: американское /ɔː/ → британское /ɒ/
            # (talk, law, thought, caught — остаются /ɔː/)
            if base == 'AO' and nxt in ('NG', 'F', 'TH', 'S', 'G') and 'o' in w \
                    and not any(x in w for x in ('au', 'aw', 'ough', 'oa')):
                sound = 'ɒ'
            if base == 'IY' and stress == '0' and i == len(phones) - 1:
                sound = 'i'                          # happy /ˈhæpi/
            units.append((sound, True, stress))
        elif base == 'R':
            nxt = phones[i + 1] if i + 1 < len(phones) else ''
            if re.search(r'\d', nxt):                # «r» перед гласной читается
                units.append(('r', False, ''))
        else:
            units.append((CONSONANTS[base], False, ''))
        i += 1

    # Знак ударения ˈ — перед началом ударного слога (только если слогов больше одного)
    syllables = sum(1 for _, vowel, _ in units if vowel)
    out = []
    for k, (sound, vowel, stress) in enumerate(units):
        if vowel and stress == '1' and syllables > 1:
            cluster = []                              # согласные прямо перед ударной гласной
            j = k - 1
            while j >= 0 and not units[j][1]:
                cluster.insert(0, units[j][0])
                j -= 1
            onset = 0                                 # сколько из них уходят в ударный слог
            for size in range(min(3, len(cluster)), 0, -1):
                if size == 1 or ''.join(cluster[-size:]) in ONSETS:
                    onset = size
                    break
            if j < 0:
                onset = len(cluster)                  # начало слова — весь кластер в слог
            out.insert(len(out) - onset, 'ˈ')
        out.append(sound)
    return '/' + ''.join(out) + '/'


def load_cmu(path):
    """Словарь CMU → {слово: британская транскрипция}. Берётся первый вариант произношения."""
    result = {}
    with open(path, encoding='utf-8') as f:
        for line in f:
            parts = line.split('#')[0].split()
            if len(parts) < 2:
                continue
            word = parts[0]
            if '(' in word or not re.fullmatch(r"[a-z][a-z'.-]*", word):
                continue                             # «read(2)» — второй вариант, пропускаем
            result.setdefault(word, to_british(word, ' '.join(parts[1:])))
    return result
