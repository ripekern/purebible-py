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


# Cover + 66 book titles, exact Cambridge-Concord wording (376 + 5 words).
# Verified: these strings tokenize to 376 words; + cover (5) = 381.
COVER_TITLE = "HOLY BIBLE KING JAMES VERSION"

BOOK_TITLES: dict[str, str] = {
    "Gen": "THE FIRST BOOK OF MOSES, CALLED GENESIS.",
    "Exod": "THE SECOND BOOK OF MOSES, CALLED EXODUS.",
    "Lev": "THE THIRD BOOK OF MOSES, CALLED LEVITICUS.",
    "Num": "THE FOURTH BOOK OF MOSES, CALLED NUMBERS.",
    "Deut": "THE FIFTH BOOK OF MOSES, CALLED DEUTERONOMY.",
    "Josh": "THE BOOK OF JOSHUA.",
    "Judg": "THE BOOK OF JUDGES.",
    "Ruth": "THE BOOK OF RUTH.",
    "1Sam": "THE FIRST BOOK OF SAMUEL, OTHERWISE CALLED, THE FIRST BOOK OF THE KINGS.",
    "2Sam": "THE SECOND BOOK OF SAMUEL, OTHERWISE CALLED, THE SECOND BOOK OF THE KINGS.",
    "1Kgs": "THE FIRST BOOK OF THE KINGS, COMMONLY CALLED, THE THIRD BOOK OF THE KINGS.",
    "2Kgs": "THE SECOND BOOK OF THE KINGS, COMMONLY CALLED, THE FOURTH BOOK OF THE KINGS.",
    "1Chr": "THE FIRST BOOK OF THE CHRONICLES.",
    "2Chr": "THE SECOND BOOK OF THE CHRONICLES.",
    "Ezra": "EZRA.",
    "Neh": "THE BOOK OF NEHEMIAH.",
    "Esth": "THE BOOK OF ESTHER.",
    "Job": "THE BOOK OF JOB.",
    "Ps": "THE BOOK OF PSALMS.",
    "Prov": "THE PROVERBS.",
    "Eccl": "ECCLESIASTES; OR, THE PREACHER.",
    "Song": "THE SONG OF SOLOMON.",
    "Isa": "THE BOOK OF THE PROPHET ISAIAH.",
    "Jer": "THE BOOK OF THE PROPHET JEREMIAH.",
    "Lam": "THE LAMENTATIONS OF JEREMIAH.",
    "Ezek": "THE BOOK OF THE PROPHET EZEKIEL.",
    "Dan": "THE BOOK OF DANIEL.",
    "Hos": "HOSEA.",
    "Joel": "JOEL.",
    "Amos": "AMOS.",
    "Obad": "OBADIAH.",
    "Jonah": "JONAH.",
    "Mic": "MICAH.",
    "Nah": "NAHUM.",
    "Hab": "HABAKKUK.",
    "Zeph": "ZEPHANIAH.",
    "Hag": "HAGGAI.",
    "Zech": "ZECHARIAH.",
    "Mal": "MALACHI.",
    "Matt": "THE GOSPEL ACCORDING TO ST. MATTHEW.",
    "Mark": "THE GOSPEL ACCORDING TO ST. MARK.",
    "Luke": "THE GOSPEL ACCORDING TO ST. LUKE.",
    "John": "THE GOSPEL ACCORDING TO ST. JOHN.",
    "Acts": "THE ACTS OF THE APOSTLES.",
    "Rom": "THE EPISTLE OF PAUL THE APOSTLE TO THE ROMANS.",
    "1Cor": "THE FIRST EPISTLE OF PAUL THE APOSTLE TO THE CORINTHIANS.",
    "2Cor": "THE SECOND EPISTLE OF PAUL THE APOSTLE TO THE CORINTHIANS.",
    "Gal": "THE EPISTLE OF PAUL THE APOSTLE TO THE GALATIANS.",
    "Eph": "THE EPISTLE OF PAUL THE APOSTLE TO THE EPHESIANS.",
    "Phil": "THE EPISTLE OF PAUL THE APOSTLE TO THE PHILIPPIANS.",
    "Col": "THE EPISTLE OF PAUL THE APOSTLE TO THE COLOSSIANS.",
    "1Thess": "THE FIRST EPISTLE OF PAUL THE APOSTLE TO THE THESSALONIANS.",
    "2Thess": "THE SECOND EPISTLE OF PAUL THE APOSTLE TO THE THESSALONIANS.",
    "1Tim": "THE FIRST EPISTLE OF PAUL THE APOSTLE TO TIMOTHY.",
    "2Tim": "THE SECOND EPISTLE OF PAUL THE APOSTLE TO TIMOTHY.",
    "Titus": "THE EPISTLE OF PAUL TO TITUS.",
    "Phlm": "THE EPISTLE OF PAUL TO PHILEMON.",
    "Heb": "THE EPISTLE OF PAUL THE APOSTLE TO THE HEBREWS.",
    "Jas": "THE GENERAL EPISTLE OF JAMES.",
    "1Pet": "THE FIRST EPISTLE GENERAL OF PETER.",
    "2Pet": "THE SECOND EPISTLE GENERAL OF PETER.",
    "1John": "THE FIRST EPISTLE GENERAL OF JOHN.",
    "2John": "THE SECOND EPISTLE OF JOHN.",
    "3John": "THE THIRD EPISTLE OF JOHN.",
    "Jude": "THE GENERAL EPISTLE OF JUDE.",
    "Rev": "THE REVELATION OF ST. JOHN THE DIVINE.",
}

# Psalm 119 stanza divisions (KJV spellings, one per 8-verse section).
PS119_DIVISIONS: list[str] = [
    "ALEPH", "BETH", "GIMEL", "DALETH", "HE", "VAU", "ZAIN", "CHETH",
    "TETH", "JOD", "CAPH", "LAMED", "MEM", "NUN", "SAMECH", "AIN", "PE",
    "TZADDI", "KOPH", "RESH", "SCHIN", "TAU",
]

# Canon groups for scoped search (--law/--prophets/--ot/--nt/--gospels/
# --letters/--prophecy). Prophets = Major 5 + Minor 12; letters = Rom–Jude;
# prophecy = Revelation; firstlast = Genesis + Revelation ("First &
# Last Books" — kjvcode's scope with 10 patterns, incl. 7^3 and 777).
CANON_GROUPS: dict[str, frozenset[str]] = {
    "law": frozenset({"Gen", "Exod", "Lev", "Num", "Deut"}),
    "prophets": frozenset({"Isa", "Jer", "Lam", "Ezek", "Dan", "Hos",
                            "Joel", "Amos", "Obad", "Jonah", "Mic", "Nah",
                            "Hab", "Zeph", "Hag", "Zech", "Mal"}),
    "ot": frozenset(BOOK_ORDER[:39]),
    "nt": frozenset(BOOK_ORDER[39:]),
    "gospels": frozenset({"Matt", "Mark", "Luke", "John"}),
    "letters": frozenset({"Rom", "1Cor", "2Cor", "Gal", "Eph", "Phil",
                           "Col", "1Thess", "2Thess", "1Tim", "2Tim",
                           "Titus", "Phlm", "Heb", "Jas", "1Pet", "2Pet",
                           "1John", "2John", "3John", "Jude"}),
    "prophecy": frozenset({"Rev"}),
    "firstlast": frozenset({"Gen", "Rev"}),
}

# Trailing subscription embedded in the SW text's final-verse blocks
# (duplicates the colophon entries — stripped at load so each counts once).
_SUB_RE = re.compile(
    r"\s+(Written |Unto the Galatians written|"
    r"The (?:first|second) (?:epistle|to )|It was written ).*$",
    re.DOTALL,
)


@dataclass
class Verse:
    book: str          # SW code, e.g. "Gen"
    chapter: int
    verse: int         # 0 = superscription/colophon (extra-biblical title)
    text: str          # display text (¶ + […] style)
    plain: str = ""    # search text (no markup)
    words: list[str] = field(default_factory=list)  # tokenized plain words
    lowered: list[str] = field(default_factory=list)
    kind: str = "verse"  # verse | superscription | colophon | cover |
                         # title | heading | division | number

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
        if self.kind == "cover":
            return "Cover"
        if self.kind == "title":
            return f"{name} Title"
        if self.kind == "heading":
            return f"{name} {self.chapter} Heading"
        if self.kind == "division":
            return f"{name} {self.chapter} {self.text}"
        if self.kind == "number":
            return f"{name} {self.chapter}:{self.verse} Number"
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
        # 3-tuple keys serve lookup() for verse/superscription/colophon
        # (collision-free among those kinds); print-matter kinds use
        # 4-tuple keys so verse numbers can't shadow their verses.
        self._index: dict[tuple, Verse] = {}
        for v in verses:
            if v.kind in ("verse", "superscription", "colophon"):
                self._index[(v.book, v.chapter, v.verse)] = v
            else:
                self._index[(v.book, v.chapter, v.verse, v.kind)] = v
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
        _strip_subscriptions(verses)
        _attach_extras(verses)
        return cls(verses)

    def lookup(self, book: str, chapter: int, verse: int) -> Verse | None:
        return self._index.get((book, chapter, verse))

    def chapter(self, book: str, chapter: int) -> list[Verse]:
        return list(self._chapters.get((book, chapter), []))

    def book_chapters(self, book: str) -> list[int]:
        return sorted({ch for (b, ch) in self._chapters if b == book})

    def totals(self) -> tuple[int, int, int]:
        """Total (entries, words, chars) over all entries.

        entries = len(self) (62,423: verses + superscriptions +
        colophons + cover + titles + headings + divisions + numbers).
        words  = sum of tokenized words per entry (same tokenization
                 the search index uses) — 823,543 = 7^7 for the KJV.
        chars  = sum of len(entry.text) — the display text shown in
                 results (¶ + […] style).
        Cached after first call.
        """
        cached = getattr(self, "_totals_cache", None)
        if cached is not None:
            return cached
        n_verses = len(self.verses)
        n_words = sum(len(v.words) for v in self.verses)
        n_chars = sum(len(v.text) for v in self.verses)
        cached = (n_verses, n_words, n_chars)
        self._totals_cache = cached
        return cached

    def uniques_case_sensitive(self) -> int:
        """Distinct case-sensitive words (len of set over v.words).

        Matches `-c` / `\\C` indexing: 'LORD' != 'Lord' != 'lord'.
        Cached after first call.
        """
        cached = getattr(self, "_uniques_cs_cache", None)
        if cached is not None:
            return cached
        cached = len({w for v in self.verses for w in v.words})
        self._uniques_cs_cache = cached
        return cached

    def uniques_case_insensitive(self) -> int:
        """Distinct case-folded words (len of set over v.lowered).

        Matches default search indexing: 'LORD' == 'Lord' == 'lord'.
        Cached after first call.
        """
        cached = getattr(self, "_uniques_ci_cache", None)
        if cached is not None:
            return cached
        cached = len({w for v in self.verses for w in v.lowered})
        self._uniques_ci_cache = cached
        return cached

    def group_words(self) -> dict[str, int]:
        """Word totals per canon group (law/prophets/ot/nt/gospels/letters/
        prophecy), summed over every entry kind. Cached after first call."""
        cached = getattr(self, "_group_words_cache", None)
        if cached is not None:
            return cached
        cached = {g: sum(len(v.words) for v in self.verses if v.book in books)
                  for g, books in CANON_GROUPS.items()}
        self._group_words_cache = cached
        return cached

    def totals_right(self) -> str:
        """Pre-formatted string: '62,423 entries · 823,543 words · 14,082 Words'.

        (Kept for API compat; the TUI shows search stats or nothing idle.)
        """
        nv, nw, _ = self.totals()
        ncs = self.uniques_case_sensitive()
        return f"{nv:,} entries · {nw:,} words · {ncs:,} Words"

    def __len__(self) -> int:
        return len(self.verses)


def _make_extra(book: str, chapter: int, kind: str, raw: str, verse: int = 0) -> Verse:
    """Build a searchable extra entry (superscription, colophon, cover,
    title, heading, division, number) from plain text."""
    v = Verse(book=book, chapter=chapter, verse=verse, kind=kind,
              text=display_text(raw), plain=plain_text(raw))
    v.__post_init__()
    return v


def _strip_subscriptions(verses: list[Verse]) -> int:
    """Cut the colophon subscription embedded in the SW text's final-verse
    blocks (it duplicates the colophon entries — each must count once).

    Returns total words removed (186 for the KJV: integrity checksum).
    """
    from .superscriptions import COLOPHONS
    removed = 0
    for bk in COLOPHONS:
        last = next(v for v in reversed(verses)
                    if v.kind == "verse" and v.book == bk)
        for attr in ("plain", "text"):
            t = getattr(last, attr)
            ms = list(_SUB_RE.finditer(t))
            if ms:
                setattr(last, attr, t[:ms[-1].start()].strip())
        before = len(last.words)
        last.words = tokenize(last.plain)
        last.lowered = [w.lower() for w in last.words]
        removed += before - len(last.words)
    return removed


def _attach_extras(verses: list[Verse]) -> None:
    """Insert all print matter in canonical order so the whole printed
    page is searchable: cover, book titles, chapter headings, psalm
    superscriptions, Psalm 119 divisions, verse numbers (every verse but
    each chapter's unnumbered verse 1, like print), Pauline colophons.

    Lookup/chapter views only contain kind == "verse" and are unaffected.
    """
    from .superscriptions import COLOPHONS, SUPERSCRIPTIONS
    chaps: dict[str, list[int]] = {}
    by_ch: dict[tuple[str, int], list[Verse]] = {}
    for v in verses:
        if v.kind != "verse":
            continue
        chaps.setdefault(v.book, []).append(v.chapter)
        by_ch.setdefault((v.book, v.chapter), []).append(v)
    for bk in chaps:
        chaps[bk] = sorted(set(chaps[bk]))

    pending_sup = dict(SUPERSCRIPTIONS)
    out: list[Verse] = [_make_extra("", 0, "cover", COVER_TITLE)]
    for book in BOOK_ORDER:
        if book not in chaps:
            continue
        out.append(_make_extra(book, 0, "title", BOOK_TITLES[book]))
        for ch in chaps[book]:
            out.append(_make_extra(book, ch, "heading", f"CHAPTER {ch}"))
            key = (book, ch)
            if key in pending_sup:
                out.append(_make_extra(book, ch, "superscription",
                                       pending_sup.pop(key)))
            for v in by_ch[key]:
                if book == "Ps" and ch == 119 and (v.verse - 1) % 8 == 0:
                    out.append(_make_extra(
                        book, ch, "division",
                        PS119_DIVISIONS[(v.verse - 1) // 8], verse=v.verse))
                if v.verse > 1:
                    # printed verse number (verse 1 is unnumbered in print)
                    out.append(_make_extra(book, ch, "number", str(v.verse),
                                           verse=v.verse))
                out.append(v)
        if book in COLOPHONS:
            out.append(_make_extra(book, 0, "colophon", COLOPHONS[book]))
    verses[:] = out


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
