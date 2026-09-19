"""Passage reference parsing: 'John 3:16', 'Ps 23', 'Gen 1:1-3', '1 John 1:1-2:3'.

Resolves to concrete verses via a Bible instance.
"""
from __future__ import annotations

import re

from .bible import BOOKS, BOOK_ORDER, FULLNAME

# alias (normalized: lowercase, no spaces/dots) -> SW code
_ALIAS: dict[str, str] = {}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


for _code, _full, _ab in BOOKS:
    _ALIAS[_norm(_code)] = _code
    _ALIAS[_norm(_full)] = _code
    _ALIAS[_norm(_ab)] = _code
# extra everyday aliases
_EXTRA = {
    "genesis": "Gen", "gen": "Gen", "ge": "Gen",
    "exodus": "Exod", "exod": "Exod", "ex": "Exod",
    "leviticus": "Lev", "lev": "Lev", "le": "Lev",
    "numbers": "Num", "num": "Num", "nu": "Num",
    "deuteronomy": "Deut", "deut": "Deut", "dt": "Deut",
    "joshua": "Josh", "josh": "Josh",
    "judges": "Judg", "judg": "Judg", "jdg": "Judg",
    "ruth": "Ruth",
    "1samuel": "1Sam", "1sam": "1Sam",
    "2samuel": "2Sam", "2sam": "2Sam",
    "1kings": "1Kgs", "1kgs": "1Kgs", "1ki": "1Kgs",
    "2kings": "2Kgs", "2kgs": "2Kgs",
    "1chronicles": "1Chr", "1chr": "1Chr",
    "2chronicles": "2Chr", "2chr": "2Chr",
    "ezra": "Ezra", "ezr": "Ezra",
    "nehemiah": "Neh", "neh": "Neh", "ne": "Neh",
    "esther": "Esth", "esth": "Esth", "est": "Esth",
    "job": "Job",
    "psalms": "Ps", "psalm": "Ps", "ps": "Ps", "psa": "Ps",
    "proverbs": "Prov", "prov": "Prov", "pr": "Prov",
    "ecclesiastes": "Eccl", "eccl": "Eccl", "ecc": "Eccl",
    "songofsolomon": "Song", "song": "Song", "songs": "Song", "sos": "Song",
    "isaiah": "Isa", "isa": "Isa",
    "jeremiah": "Jer", "jer": "Jer", "je": "Jer",
    "lamentations": "Lam", "lam": "Lam",
    "ezekiel": "Ezek", "ezek": "Ezek", "eze": "Ezek",
    "daniel": "Dan", "dan": "Dan", "da": "Dan",
    "hosea": "Hos", "hos": "Hos",
    "joel": "Joel",
    "amos": "Amos",
    "obadiah": "Obad", "obad": "Obad", "ob": "Obad",
    "jonah": "Jonah",
    "micah": "Mic", "mic": "Mic",
    "nahum": "Nah", "nah": "Nah",
    "habakkuk": "Hab", "hab": "Hab",
    "zephaniah": "Zeph", "zeph": "Zeph",
    "haggai": "Hag", "hag": "Hag",
    "zechariah": "Zech", "zech": "Zech", "zec": "Zech",
    "malachi": "Mal", "mal": "Mal",
    "matthew": "Matt", "matt": "Matt", "mat": "Matt", "mt": "Matt",
    "mark": "Mark", "mar": "Mark", "mk": "Mark",
    "luke": "Luke", "luk": "Luke", "lk": "Luke",
    "john": "John", "joh": "John", "jn": "John",
    "acts": "Acts", "act": "Acts", "ac": "Acts",
    "romans": "Rom", "rom": "Rom", "ro": "Rom",
    "1corinthians": "1Cor", "1cor": "1Cor",
    "2corinthians": "2Cor", "2cor": "2Cor",
    "galatians": "Gal", "gal": "Gal",
    "ephesians": "Eph", "eph": "Eph",
    "philippians": "Phil", "phil": "Phil",
    "colossians": "Col", "col": "Col",
    "1thessalonians": "1Thess", "1thess": "1Thess",
    "2thessalonians": "2Thess", "2thess": "2Thess",
    "1timothy": "1Tim", "1tim": "1Tim",
    "2timothy": "2Tim", "2tim": "2Tim",
    "titus": "Titus", "tit": "Titus",
    "philemon": "Phlm", "phlm": "Phlm", "phm": "Phlm",
    "hebrews": "Heb", "heb": "Heb",
    "james": "Jas", "jas": "Jas", "jam": "Jas",
    "1peter": "1Pet", "1pet": "1Pet", "1pe": "1Pet",
    "2peter": "2Pet", "2pet": "2Pet",
    "1john": "1John", "1jn": "1John",
    "2john": "2John", "2jn": "2John",
    "3john": "3John", "3jn": "3John",
    "jude": "Jude",
    "revelation": "Rev", "rev": "Rev", "re": "Rev",
}
_ALIAS.update(_EXTRA)

_REF_RE = re.compile(
    r"^\s*(?P<book>[1-3]?\s?[A-Za-z]+(?:\s+of\s+[A-Za-z]+)?)\s+"
    r"(?P<ch1>\d+)(?:\s*:\s*(?P<vs1>\d+))?"
    r"(?:\s*[-–—]\s*(?:(?P<ch2>\d+)\s*:\s*)?(?P<vs2>\d+))?\s*$"
)

LOOKS_LIKE_REF = re.compile(r"^[A-Za-z0-9 ]+\s+\d+(?::\d+)?(\s*-\s*(\d+:)?\d+)?$")


def looks_like_reference(s: str) -> bool:
    s = s.strip()
    if not s or not LOOKS_LIKE_REF.match(s):
        return False
    m = _REF_RE.match(s)
    if not m:
        return False
    return _norm(m.group("book")) in _ALIAS


def parse_reference(s: str) -> tuple[str, int, int | None, int | None, int | None]:
    """Return (book_code, ch1, vs1, ch2, vs2). vs may be None (whole chapter).

    Raises ValueError on failure.
    """
    m = _REF_RE.match(s.strip())
    if not m:
        raise ValueError(f"not a reference: {s!r}")
    code = _ALIAS.get(_norm(m.group("book")))
    if not code:
        raise ValueError(f"unknown book: {m.group('book')!r}")
    ch1 = int(m.group("ch1"))
    vs1 = int(m.group("vs1")) if m.group("vs1") else None
    ch2 = int(m.group("ch2")) if m.group("ch2") else None
    vs2 = int(m.group("vs2")) if m.group("vs2") else None
    if vs2 is not None and ch2 is None and vs1 is not None:
        ch2 = ch1  # "3:16-18" -> same chapter
    return code, ch1, vs1, ch2, vs2


def resolve_reference(bible, s: str) -> list:
    """Resolve a reference string to a list of Verse (may be empty)."""
    from .bible import BOOK_ORDER  # local to avoid cycle in docs
    code, ch1, vs1, ch2, vs2 = parse_reference(s)
    if vs1 is None and vs2 is None:
        return bible.chapter(code, ch1)  # whole chapter
    if ch2 is None:
        ch2 = ch1
    if vs1 is None:
        vs1 = 1
    if vs2 is None:
        vs2 = vs1
        ch2 = ch1
    out = []
    bi1, bi2 = BOOK_ORDER.index(code), BOOK_ORDER.index(code)
    # same-book range (possibly cross-chapter)
    b = code
    for ch in range(ch1, ch2 + 1):
        for v in bible.chapter(b, ch):
            if ch == ch1 and ch == ch2:
                if vs1 <= v.verse <= vs2:
                    out.append(v)
            elif ch == ch1:
                if v.verse >= vs1:
                    out.append(v)
            elif ch == ch2:
                if v.verse <= vs2:
                    out.append(v)
            else:
                out.append(v)
    _ = (bi1, bi2)
    return out


def book_names() -> list[str]:
    return [FULLNAME[c] for c in BOOK_ORDER]
