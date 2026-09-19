"""Bible text model + SW1769 text loader (stdlib only).

Source of truth is the plain-text edition shipped with purebiblesearch:
    text/complete/SW1769Bible_both.txt

Format per verse (lines may wrap — a block starts with '@' and ends
with a trailing '@'):

    $$$Gen.1.1
    @<plain, no pilcrows, no markup>@
    @<rich,  with ¶ pilcrows + <i>…</i> italics>@

We keep both: plain for searching, rich (converted to the familiar
``¶`` / ``[…]`` style used by KJVSearch) for display.
"""
from __future__ import annotations

import html
import os
import re
import unicodedata
from dataclasses import dataclass, field

# SW-code -> (Full name, short/abbrev)
BOOKS: list[tuple[str, str, str]] = [
    ("Gen", "Genesis", "Gen"),
    ("Exod", "Exodus", "Ex"),
    ("Lev", "Leviticus", "Lev"),
    ("Num", "Numbers", "Num"),
    ("Deut", "Deuteronomy", "Deut"),
    ("Josh", "Joshua", "Josh"),
    ("Judg", "Judges", "Judg"),
    ("Ruth", "Ruth", "Ruth"),
    ("1Sam", "1 Samuel", "1Sam"),
    ("2Sam", "2 Samuel", "2Sam"),
    ("1Kgs", "1 Kings", "1Kgs"),
    ("2Kgs", "2 Kings", "2Kgs"),
    ("1Chr", "1 Chronicles", "1Chr"),
    ("2Chr", "2 Chronicles", "2Chr"),
    ("Ezra", "Ezra", "Ezra"),
    ("Neh", "Nehemiah", "Neh"),
    ("Esth", "Esther", "Esth"),
    ("Job", "Job", "Job"),
    ("Ps", "Psalms", "Ps"),
    ("Prov", "Proverbs", "Prov"),
    ("Eccl", "Ecclesiastes", "Eccl"),
    ("Song", "Song Of Solomon", "Song"),
    ("Isa", "Isaiah", "Isa"),
    ("Jer", "Jeremiah", "Jer"),
    ("Lam", "Lamentations", "Lam"),
    ("Ezek", "Ezekiel", "Ezek"),
    ("Dan", "Daniel", "Dan"),
    ("Hos", "Hosea", "Hos"),
    ("Joel", "Joel", "Joel"),
    ("Amos", "Amos", "Amos"),
    ("Obad", "Obadiah", "Obad"),
    ("Jonah", "Jonah", "Jonah"),
    ("Mic", "Micah", "Mic"),
    ("Nah", "Nahum", "Nah"),
    ("Hab", "Habakkuk", "Hab"),
    ("Zeph", "Zephaniah", "Zeph"),
    ("Hag", "Haggai", "Hag"),
    ("Zech", "Zechariah", "Zech"),
    ("Mal", "Malachi", "Mal"),
    ("Matt", "Matthew", "Matt"),
    ("Mark", "Mark", "Mark"),
    ("Luke", "Luke", "Luke"),
    ("John", "John", "John"),
    ("Acts", "Acts", "Acts"),
    ("Rom", "Romans", "Rom"),
    ("1Cor", "1 Corinthians", "1Cor"),
    ("2Cor", "2 Corinthians", "2Cor"),
    ("Gal", "Galatians", "Gal"),
    ("Eph", "Ephesians", "Eph"),
    ("Phil", "Philippians", "Phil"),
    ("Col", "Colossians", "Col"),
    ("1Thess", "1 Thessalonians", "1Thess"),
    ("2Thess", "2 Thessalonians", "2Thess"),
    ("1Tim", "1 Timothy", "1Tim"),
    ("2Tim", "2 Timothy", "2Tim"),
    ("Titus", "Titus", "Titus"),
    ("Phlm", "Philemon", "Phlm"),
    ("Heb", "Hebrews", "Heb"),
    ("Jas", "James", "Jas"),
    ("1Pet", "1 Peter", "1Pet"),
    ("2Pet", "2 Peter", "2Pet"),
    ("1John", "1 John", "1Jn"),
    ("2John", "2 John", "2Jn"),
    ("3John", "3 John", "3Jn"),
    ("Jude", "Jude", "Jude"),
    ("Rev", "Revelation", "Rev"),
]

BOOK_ORDER = [code for code, _, _ in BOOKS]
FULLNAME = {code: full for code, full, _ in BOOKS}
ABBREV = {code: ab for code, _, ab in BOOKS}

_WORD_RE = re.compile(r"[A-Za-z0-9']+")
_WORD_RE_HYPHEN = re.compile(r"[A-Za-z0-9'\-]+")

ITALIC_RE = re.compile(r"<i>(.*?)</i>", re.DOTALL)
TRANS_RE = re.compile(r"<transChange[^>]*>(.*?)</transChange>", re.DOTALL)
FONT_RE = re.compile(r"</?font[^>]*>", re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")
_CORE_RE = re.compile(r"^([^A-Za-z0-9']*)([A-Za-z0-9']+)(.*)$", re.DOTALL)

# ligatures NFKD won't split + dash characters (hyphen, en/em dashes, …)
_LIGATURES = {"æ": "ae", "Æ": "Ae", "œ": "oe", "Œ": "Oe", "ß": "ss", "ẞ": "SS"}
_QUOTES = {"‘": "'", "’": "'", "‚": "'", "“": '"', "”": '"', "„": '"'}
_DASHES = "‐‑‒–—―"  # U+2010 U+2011 U+2012 U+2013 U+2014 U+2015


def fold_text(text: str, hyphen_sensitive: bool = False) -> str:
    """Normalize for searching: split ligatures, strip accents, handle dashes.

    Matches the C++ default (hyphen-insensitive): hyphenated compounds are
    single words (``Bar-jesus`` → ``Barjesus``), so ``Jesus`` won't match
    inside them. Hyphen-sensitive keeps ``-`` inside words instead.
    """
    for a, b in _LIGATURES.items():
        text = text.replace(a, b)
    for a, b in _QUOTES.items():
        text = text.replace(a, b)
    text = "".join(c for c in unicodedata.normalize("NFKD", text)
                   if not unicodedata.combining(c))
    if hyphen_sensitive:
        for d in _DASHES:
            text = text.replace(d, "-")
    else:
        text = text.replace("-", "")
        for d in _DASHES:
            text = text.replace(d, "")
    return text


def _bracket_words(inner: str) -> str:
    """Wrap each word of an added-text span: ``tarry: for`` → ``[tarry]: [for]``.

    Punctuation stays outside the brackets, like the C++ richifier (which
    tags whole concordance words, never the punctuation around them).
    """
    out = []
    for tok in inner.split():
        m = _CORE_RE.match(tok)
        out.append(f"{m.group(1)}[{m.group(2)}]{m.group(3)}" if m else tok)
    return " ".join(out)


def display_text(rich_block: str) -> str:
    """Convert a rich block to KJVSearch-style display text (¶ + […] style)."""
    t = rich_block.strip()
    if t.startswith("@"):
        t = t[1:]
    if t.endswith("@"):
        t = t[:-1]
    t = t.replace("\n", " ")
    t = ITALIC_RE.sub(lambda m: _bracket_words(m.group(1)), t)
    t = TRANS_RE.sub(lambda m: _bracket_words(m.group(1)), t)
    # red-letter (<font color="red">) and small-caps LORD (<font size="-1">):
    # keep inner text, drop the tags (terminal has no red-letter type)
    t = FONT_RE.sub("", t)
    t = TAG_RE.sub("", t)
    t = html.unescape(t)
    return re.sub(r"\s+", " ", t).strip()


def plain_text(plain_block: str) -> str:
    """Searchable text: all markup stripped, inner words kept."""
    t = plain_block.strip()
    if t.startswith("@"):
        t = t[1:]
    if t.endswith("@"):
        t = t[:-1]
    t = t.replace("\n", " ")
    t = TAG_RE.sub("", t)
    t = html.unescape(t)
    return re.sub(r"\s+", " ", t).strip()


def tokenize(text: str, hyphen_sensitive: bool = False) -> list[str]:
    return (_WORD_RE_HYPHEN if hyphen_sensitive else _WORD_RE).findall(
        fold_text(text, hyphen_sensitive))


@dataclass
class Verse:
    book: str          # SW code, e.g. "Gen"
    chapter: int
    verse: int         # 0 = superscription/colophon (extra-biblical title)
    text: str          # display text (¶ + […] style)
    plain: str = ""    # search text (no markup)
    words: list[str] = field(default_factory=list)  # tokenized plain words
    lowered: list[str] = field(default_factory=list)
    kind: str = "verse"  # "verse" | "superscription" | "colophon"

    def __post_init__(self) -> None:
        if not self.plain:
            self.plain = re.sub(r"[¶\[\]]", "", self.text)
            self.plain = re.sub(r"\s+", " ", self.plain).strip()
        if not self.words:
            self.words = tokenize(self.plain)
            self.lowered = [w.lower() for w in self.words]

    @property
    def book_name(self) -> str:
        return FULLNAME.get(self.book, self.book)

    def ref(self, abbreviated: bool = False) -> str:
        name = ABBREV.get(self.book, self.book) if abbreviated else FULLNAME.get(self.book, self.book)
        if self.kind == "superscription":
            return f"{name} {self.chapter} Superscription"
        if self.kind == "colophon":
            return f"{name} Colophon"
        return f"{name} {self.chapter}:{self.verse}"


def find_text_file(explicit: str | None = None) -> str | None:
    """Locate SW1769Bible_both.txt. Returns path or None."""
    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)
    env = os.environ.get("PUREBIBLE_TEXT")
    if env:
        candidates.append(env)
    here = os.path.dirname(os.path.abspath(__file__))
    candidates += [
        os.path.join(os.getcwd(), "data", "SW1769Bible_both.txt"),
        os.path.join(here, "..", "data", "SW1769Bible_both.txt"),
        os.path.expanduser("~/.local/share/purebible/SW1769Bible_both.txt"),
        os.path.expanduser("~/.config/purebible/SW1769Bible_both.txt"),
        # bundled text (purebible/data/ — ships in the wheel, works out of the box)
        os.path.join(here, "data", "SW1769Bible_both.txt"),
        # original checkout locations (dev convenience)
        os.path.expanduser("~/Work/purebiblesearch/text/complete/SW1769Bible_both.txt"),
    ]
    # legacy env used by the C++ tools
    base = os.environ.get("KJPBS_BASE_PATH")
    if base:
        candidates.append(os.path.join(base, "SW1769Bible_both.txt"))
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


class Bible:
    """Full KJV text with per-verse token cache."""

    def __init__(self, verses: list[Verse]):
        self.verses = verses
        self._index: dict[tuple[str, int, int], Verse] = {
            (v.book, v.chapter, v.verse): v for v in verses
        }
        self._chapters: dict[tuple[str, int], list[Verse]] = {}
        for v in verses:
            if v.kind == "verse":
                self._chapters.setdefault((v.book, v.chapter), []).append(v)

    @classmethod
    def load(cls, path: str | None = None) -> "Bible":
        found = find_text_file(path)
        if not found:
            raise FileNotFoundError(
                "SW1769Bible_both.txt not found. Set PUREBIBLE_TEXT=/path/to/SW1769Bible_both.txt "
                "or copy it to ./data/ or ~/.local/share/purebible/."
            )
        return cls.load_file(found)

    @classmethod
    def load_file(cls, path: str) -> "Bible":
        verses: list[Verse] = []
        with open(path, encoding="utf-8", errors="replace") as fh:
            cur_ref: str | None = None
            buf: list[str] = []
            blocks: list[str] = []  # completed @…@ blocks for cur_ref
            in_block = False

            def flush_block() -> None:
                if buf:
                    blocks.append("\n".join(buf))
                    buf.clear()

            for raw in fh:
                line = raw.rstrip("\n")
                if line.startswith("$$$"):
                    if cur_ref is not None and blocks:
                        verses.append(_make_verse(cur_ref, blocks))
                    cur_ref = line[3:].strip()
                    blocks = []
                    buf = []
                    in_block = False
                elif line.startswith("@") and not in_block:
                    # start of a block; may also end on same line
                    if line.endswith("@") and len(line) > 1:
                        blocks.append(line)
                    else:
                        in_block = True
                        buf = [line]
                elif in_block:
                    buf.append(line)
                    if line.endswith("@"):
                        flush_block()
                        in_block = False
                elif line == "":
                    continue
            if cur_ref is not None and blocks:
                verses.append(_make_verse(cur_ref, blocks))
        _attach_extras(verses)
        return cls(verses)

    def lookup(self, book: str, chapter: int, verse: int) -> Verse | None:
        return self._index.get((book, chapter, verse))

    def chapter(self, book: str, chapter: int) -> list[Verse]:
        return list(self._chapters.get((book, chapter), []))

    def book_chapters(self, book: str) -> list[int]:
        return sorted({ch for (b, ch) in self._chapters if b == book})

    def __len__(self) -> int:
        return len(self.verses)


def _make_extra(book: str, chapter: int, kind: str, raw: str) -> Verse:
    """Build a searchable superscription/colophon entry from OSIS inner text."""
    v = Verse(book=book, chapter=chapter, verse=0, kind=kind,
              text=display_text(raw), plain=plain_text(raw))
    v.__post_init__()
    return v


def _attach_extras(verses: list[Verse]) -> None:
    """Insert psalm superscriptions (before ch.1) and Pauline colophons
    (after the book's last verse) so they search exactly like the C++ engine.
    Lookup/chapter views only contain kind == "verse" and are unaffected."""
    from .superscriptions import COLOPHONS, SUPERSCRIPTIONS
    # superscriptions: insert before the chapter's first verse
    pending = dict(SUPERSCRIPTIONS)
    out: list[Verse] = []
    for v in verses:
        key = (v.book, v.chapter)
        if v.kind == "verse" and key in pending:
            out.append(_make_extra(v.book, v.chapter, "superscription", pending.pop(key)))
        out.append(v)
    verses[:] = out
    # colophons: append after the book's last verse
    last_idx: dict[str, int] = {}
    for i, v in enumerate(verses):
        last_idx[v.book] = i
    offset = 0
    for book in BOOK_ORDER:
        if book in COLOPHONS and book in last_idx:
            pos = last_idx[book] + 1 + offset
            verses.insert(pos, _make_extra(book, 0, "colophon", COLOPHONS[book]))
            offset += 1


def _make_verse(ref: str, blocks: list[str]) -> Verse:
    # ref like "Gen.1.1"
    parts = ref.split(".")
    book = parts[0]
    chapter = int(parts[1]) if len(parts) > 1 else 1
    verse = int(parts[2]) if len(parts) > 2 else 1
    plain = plain_text(blocks[0]) if len(blocks) >= 1 else ""
    rich = blocks[1] if len(blocks) >= 2 else blocks[0]
    disp = display_text(rich)
    v = Verse(book=book, chapter=chapter, verse=verse, text=disp, plain=plain)
    v.__post_init__()
    return v
