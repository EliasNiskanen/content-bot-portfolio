"""Kirosanasuodatin: varmistaa ettei voiceoverissa ole kiroilua, vaikka malli lipsuisi."""
import re

# (pattern, replacement) - korvaukset pitävät lauseen luettavana
_RULES = [
    (r"\bf+u+c+k+\w*|\bf\*+k\w*|\bf\W?ck\w*", ""),
    (r"\bmotherf\w*", ""),
    (r"\bsh+i+t+\w*|\bs\*+t\b", "stuff"),
    (r"\bbullsh\w*", "nonsense"),
    (r"\bgod\s?damn\w*", ""),
    (r"\bdamn(ed|it)?\b", "dang"),
    (r"\bhell\b", "heck"),
    (r"\bcrap(py)?\b", "junk"),
    (r"\bass(hole)?\b|\bjackass\b|\bbadass\b", "beast"),
    (r"\bbitch\w*", ""),
    (r"\bbastard\w*", ""),
    (r"\bdick\w*\b", ""),
    (r"\bpiss\w*", "annoy"),
    (r"\bbloody\b", "seriously"),
    (r"\bwtf\b", "what in the world"),
    (r"\bstfu\b", ""),
    (r"\blmao\b|\blmfao\b", "haha"),
]
_COMPILED = [(re.compile(p, re.I), r) for p, r in _RULES]


def find_profanity(text: str) -> list[str]:
    hits = []
    for rx, _ in _COMPILED:
        hits += [m.group(0) for m in rx.finditer(text)]
    return hits


def clean(text: str) -> str:
    for rx, rep in _COMPILED:
        text = rx.sub(rep, text)
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\s+([,.!?])", r"\1", text)
    return text.strip()
