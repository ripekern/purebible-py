"""btop-style, vim-keyed fullscreen TUI (curses, stdlib only).

Modes (nvim-style):
    NORMAL   motion + commands. The ONLY mode that can quit
             (q, ZZ/ZQ, :q). Esc here is a no-op — it never quits.
    INSERT   the '/' search prompt. Type, Enter runs, Esc drops back
             to NORMAL (nothing runs, nothing quits).
    COMMAND  the ':' prompt (:lookup, :search, :clear, :yank, :q).
             Enter executes, Esc drops back to NORMAL.

Layout
    ┌ header (reverse video): purebible · KJV 1769 · 31,102 verses · 12ms · N hits
    │ query line + results: ref (theme-colored/bold) + wrapped verse text
    └ statusline: nvim-style mode tag (highlighted NORMAL / INSERT / ...)
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import textwrap
import time

from .bible import CANON_GROUPS
from .refs import looks_like_reference, resolve_reference
from .search import search as run_search

_SEARCH_CAP = 100  # max rows shown for a search (reading a chapter is uncapped)

_THEME_CACHE: str | None = None


# -- built-in help screens (:h / :help and :e / :examples) ------------------
# ("h1"/"h2" = section headers, "row" = key/desc column, "" = plain body)
# NOTE: examples live in the :e overlay (EXAMPLES below, merged greatest
# to least). CLI flag tables are imported from cli.py so the two helps
# can't drift apart.

from .cli import LOOKUP_OPTS, SEARCH_OPTS


def _cli_rows(opts: list[tuple[str, str]]) -> list[tuple[str, str]]:
    width = max(len(flag) for flag, _ in opts)
    return [("row", f"  {flag.ljust(width)}  {desc}") for flag, desc in opts]


# Palette for cross-referencing repeated numbers in the examples overlay.
# Pairs 6..11 are initialized in _UI.main(); number -> pair cycles through
# them in first-seen order, so shared digits visibly share a color.
_NUM_PAIRS = 6

# Reference-number prefix on :e rows ("  0001 / ..."). Excluded from the
# number highlighter so line digits can't fake cross-reference links.
_ROW_NUM_RE = re.compile(r"^(\s*\d+\s+)")


def _layout_help_rows(entries: list[tuple[str, str]]) -> list[tuple]:
    """Group overlay entries into wide-mode screen rows (paired)."""
    rows: list[tuple] = []
    i, n = 0, len(entries)
    while i < n:
        if i + 1 < n:
            rows.append((entries[i], entries[i + 1]))
        else:
            rows.append((entries[i], None))
        i += 2
    return rows


def _repeat_number_runs(texts: list[str]) -> list[str]:
    """Digit runs (2+ digits, plus '7') found in 2+ rows, first-seen order.

    Maximal runs only, so 639 and 6391 never link; within-row repeats
    don't self-trigger. Pure helper (no curses) for testability.
    """
    counts: dict[str, int] = {}
    order: list[str] = []
    for t in texts:
        # appearance order (not set order) so colors are stable run to run
        for m in dict.fromkeys(re.findall(r"\d+", t)):
            if len(m) < 2 and m != "7":
                continue
            if m not in counts:
                counts[m] = 0
                order.append(m)
            counts[m] += 1
    return [m for m in order if counts[m] >= 2]


# Curated examples. Single source for the :e overlay — every entry was
# verified against this engine (counts are hits, not verses; slash-pairs
# like 81 / 70v give hits / verses). kind is "word" (single token),
# "phrase" (exact, consecutive words), "multi" (OR / AND / wildcards /
# case override), "flags" (\C case and inline --chapter/--book scopes)
# or "signatures" (totals that cross-reference each other and CANON).
# :e merges every kind into one list, greatest to least; each entry
# runs from / as typed.

EXAMPLES: list[tuple[str, str, str, str]] = [
    # -- single words --
    ("Jesus", "973", "139x7, cf Jesus* 983", "word"),
    ("mercy", "276", "every soul saved, Acts 27:37", "word"),
    ("Abraham", "231", "33x7 (cf wine: 231)", "word"),
    ("wine", "231", "33x7 (Gen 14:18, to Abraham)", "word"),
    ("beast", "180", "60+60+60", "word"),
    ("atonement", "81 / 70v", "in exactly 70 verses, 10x7", "word"),
    ("cross", "28", "4x7", "word"),
    ("charity", "28", "4x7, greatest of these", "word"),
    ("forgiveness", "7", "perfect 7, in 7 verses", "word"),
    # -- phrases --
    ("saith the LORD", "854", "122x7, God's signature", "phrase"),
    ("Son of man", "196", "Jesus' title, 28x7", "phrase"),
    ("Jesus Christ", "196", "28x7 — same as Son of man!", "phrase"),
    ("Lord Jesus Christ", "84", "the full title, 12x7", "phrase"),
    ("Verily I say unto", "77", "Jesus' signature phrase", "phrase"),
    ("In the beginning", "17", "matches 'for it is written'", "phrase"),
    ("for it is written", "17", "matches 'In the beginning'", "phrase"),
    ("Holy Spirit", "7", "perfect 7 (cf Holy Ghost: 90)", "phrase"),
    ("bottomless pit", "7", "perfect 7", "phrase"),
    ("a thousand years", "7", "perfect 7 (Rev 20+)", "phrase"),
    ("six hundred thousand", "7", "the Exodus multitude", "phrase"),
    ("Know that I am the LORD", "77", "perfect 77 in 77 verses", "phrase"),
    ("The Voice of the LORD", "49", "7x7", "phrase"),
    ("His love", "7", "first mention Deut 7:7", "phrase"),
    # -- multi-word (operators) --
    ("grace | mercy | peace", "875", "125x7, pastoral greeting", "multi"),
    ("The Father | Holy Ghost | The Word", "777", "the Godhead named (OR)", "multi"),
    ("mercy | truth", "511", "73x7, met together (Ps 85:10)", "multi"),
    ("Sin | Forgiven", "490", "70x7 (forgiven joins forgiv* 112)", "multi"),
    ("Justified | Blood", "490", "70x7", "multi"),
    ("disciple*", "273", "39x7 (cf 39 OT books)", "multi"),
    ("sing*", "196", "wildcard sing/sang/sung, 28x7, same as the twins", "multi"),
    ("preach*", "154", "fishers of men + PREACHER title", "multi"),
    ("repent*", "112", "16x7, twin of forgiv*", "multi"),
    ("forgiv*", "112", "16x7, twin of repent*", "multi"),
    ("bond | free", "78 / 70v", "70 verses like atonement", "multi"),
    ("male | female", "70 / 49v", "both sevened (Gen 1:27)", "multi"),
    ("flesh & blood", "28", "AND in same verse, 4x7", "multi"),
    ("blood & water", "18", "from His side: life", "multi"),
    ("Peter & John", "18", "the two pillars", "multi"),
    # -- flags (case / scopes) --
    ("LORD\\C", "6391", "913x7, the covenant name", "flags"),
    ("--chapter faith & grace", "1281", "183x7 (Eph 2:8 inside)", "flags"),
    ("Lord\\C", "1211", "173x7", "flags"),
    ("--book hope & charity", "938", "134x7", "flags"),
    ("Amen\\C", "77", "last word of the Bible (\\C sensitive)", "flags"),
    ("Word\\C", "7", "the divine Word, all John", "flags"),
    # -- signatures (totals that cross-reference each other and CANON) --
    ("David | Abraham", "1316", "188x7 (lamb family)", "multi"),
    ("Jesus*", "983", "967 + 6 JESUS + 10 Jesus'; NT ends 188,983", "signatures"),
    ("--firstlast In | Amen", "777", "first & last words", "signatures"),
    ("Beast | Mark | Sin", "666", "the beast number", "multi"),
    ("--law Moses", "639", "634 in text + 5 titles; OT ends 634,555", "signatures"),
    ("--law In | Israel\\C", "613", "613 commandments, in 555 verses", "signatures"),
    ("Christ", "555", "OT ends 634,555", "signatures"),
    ("--firstlast God | Jesus", "343", "7x7x7, first & last books", "signatures"),
    ("book", "214", "188 in text + 26 titles; book of life", "signatures"),
    ("call", "196", "28x7, joins the 196 twins", "signatures"),
    ("lamb*", "188", "Lamb's book of life (Rev 21:27)", "signatures"),
    ("life* --nt", "188", "book of life, NT only", "signatures"),
    ("hearken", "153", "cf 153 fishes", "signatures"),
    ("Fish | Men --gospels", "153", "153 fishes, John 21", "signatures"),
    ("tribes", "112", "16x7, joins repent*/forgiv*; twelve tribes", "signatures"),
    ("beloved", "111", "3x37", "signatures"),
    ("Moses --nt", "77", "Moses 77x in the NT", "signatures"),
    ("Son & Jesus", "70", "70 in 70 verses, like atonement", "multi"),
    ("cross | tree --gospels", "49", "7x7 in Gospels", "signatures"),
    ("Word of God", "49", "7x7", "phrase"),
    ("love\\C --gospels", "49", "lowercase love, 7x7 in Gospels", "signatures"),
    ("crucified", "37", "perfect 37 in 37", "signatures"),
    ("ordained", "37", "perfect 37 in 37, incl. titles", "signatures"),
    ("saviour", "37", "perfect 37 in 37", "signatures"),
    ("sow", "37", "the sower, perfect 37 in 37", "signatures"),
    ("thirty and seven", "7", "thirty-seven itself, sevened", "signatures"),
]


EXAMPLES_HELP: list[tuple[str, str]] = [
    ("h1", "purebible — examples"),
    ("", ""),
    ("", "  The entire King James Bible — cover, titles, chapters,"),
    ("", "  verses, words — counts 823,543 = 7^7 (the Elton anomaly)."),
    ("", "  Every figure below was verified in this engine; together"),
    ("", "  they unpack that total, greatest to least."),
    ("", ""),
    ("", "  Counts verified in this engine (hits, not verses)."),
    ("", "  Every entry runs from / as typed, greatest to least."),
    ("", ""),
]


HELP: list[tuple[str, str]] = [
    ("h1", "purebible — help"),
    ("", ""),
    ("", "  The KJV (1769), searchable in your terminal."),
    ("", "  Query, browse, and look up any passage."),
    ("", ""),
    ("h2", "QUICK START"),
    ("row", "  / type Enter     search · j/k move · Enter chapter · C-o back"),
    ("row", "  : type Enter     command · q quit (NORMAL only) · :h details"),
    ("", ""),
    ("h2", "MODES  (the statusline always shows the current one)"),
    ("row", "  NORMAL      motion keys — the ONLY mode that can quit"),
    ("row", "              (q, ZZ/ZQ, :q). Esc does nothing on purpose."),
    ("row", "              → INSERT: i a / · → COMMAND: :"),
    ("row", "  INSERT      search prompt. Enter runs, Esc cancels (runs nothing)."),
    ("row", "  COMMAND     : popup (upper third). Enter executes, Esc cancels."),
    ("row", "  HELP        this screen — stays open while you type (/ i a :)."),
    ("row", "              Enter runs (reveals results) · q/Esc close, never quit."),
    ("", ""),
    ("", "  Always starts in NORMAL — / or i to search."),
    ("", ""),
    ("h2", "NORMAL KEYS"),
    ("row", "  j / k       move selection down / up (↑ / ↓ work too)"),
    ("row", "  C-d / C-u   half-page down / up"),
    ("row", "  C-f / C-b   next / previous page of 100 (big searches page)"),
    ("row", "  gg / G      first / last result on this page"),
    ("row", "  /           fresh search (empty prompt)"),
    ("row", "  i           edit current query, caret at start (prepend)"),
    ("row", "  a           edit current query, caret at end (append)"),
    ("row", "  :           command (COMMAND mode)"),
    ("row", "  Enter       open the whole chapter of the selected verse"),
    ("row", "  C-o         jump back to your last search (jumplist)"),
    ("row", "  n / N       next / previous match"),
    ("row", "  y           yank verse (xclip / xsel / pbcopy / wl-copy)"),
    ("row", "  q  ZZ  ZQ   quit (NORMAL only)"),
    ("", ""),
    ("h2", "PROMPT KEYS  (INSERT and COMMAND modes)"),
    ("row", "  ↑ / ↓       older / newer line (history, like nvim cmdline)"),
    ("row", "  ← / →       move caret"),
    ("row", "  C-u         clear the whole prompt"),
    ("row", "  C-w         delete one word back"),
    ("row", "  backspace   delete one char · Esc cancels · Enter runs"),
    ("", ""),
    ("h2", "COMMANDS"),
    ("row", "  :lookup <ref>     look up verse(s)"),
    ("row", "  :search <phrase>  search text"),
    ("row", "  :clear  (:cls)    clear results"),
    ("row", "  :yank             yank selected verse"),
    ("row", "  :w                nothing to save — text is read-only"),
    ("row", "  :h  (:help)       this screen"),
    ("row", "  :e  (:examples)   examples by category (separate screen)"),
    ("row", "  :q  (:wq)         quit"),
    ("", ""),
    ("h2", "SEARCH PATTERNS"),
    ("row", "  word word         phrase, consecutive words"),
    ("row", "  a | b             OR (union)"),
    ("row", "  a & b             AND, same verse"),
    ("row", "  a & -b            NOT, exclude"),
    ("row", "  love*  *eth       wildcards *, ?, [seq] per word"),
    ("row", "  *                 any single word (gap)"),
    ("row", "  \\c / \\C          vim case override (\\C = sensitive)"),
    ("row", "  --chapter/--book  scope for '&' (last one wins)"),
    ("row", "  --law/--prophets/--ot/--nt  canon filter (last wins)"),
    ("row", "  --gospels/--letters/--prophecy  gospels, Rom-Jude, Rev"),
    ("row", "  --firstlast  Genesis + Revelation"),
    ("", "  Phrases run on the whole-Bible word stream, so they may span"),
    ("", "  a verse boundary — like the C++ engine."),
    ("", "  Big searches page in 100s — C-f / C-b, or refine with & or *."),
    ("", ""),
    ("h2", "LOOKUP PATTERNS  (works in / too — refs auto-detect)"),
    ("row", "  single verse"),
    ("row", "  whole chapter"),
    ("row", "  verse range"),
    ("row", "  cross-chapter range"),
    ("row", "  abbreviations: Gen Ex Lev Ps Jn Rom Rev ..."),
    ("", ""),
    ("h2", "CLI SEARCH OPTIONS  (`purebible search …` in a shell)"),
    *_cli_rows(SEARCH_OPTS),
    ("", ""),
    ("h2", "CLI LOOKUP OPTIONS  (`purebible lookup …` in a shell)"),
    *_cli_rows(LOOKUP_OPTS),
    ("", ""),
    ("h2", "EXAMPLES"),
    ("row", "  :e  examples, greatest to least — counts verified"),
    ("row", "      a number glowing in 2+ rows links those totals;"),
    ("row", "      descriptions glow past 10"),
    ("", ""),
]


def detect_theme() -> str:
    """Return 'dark' or 'light', matching the system theme (best effort).

    Order: PUREBIBLE_THEME override → macOS system → GNOME color-scheme /
    gtk-theme → $COLORFGBG terminal hint → dark (most terminals).
    """
    global _THEME_CACHE
    if _THEME_CACHE is not None:
        return _THEME_CACHE
    theme = _detect_theme_uncached()
    _THEME_CACHE = theme
    return theme


def _detect_theme_uncached() -> str:
    pref = os.environ.get("PUREBIBLE_THEME", "").strip().lower()
    if pref in ("dark", "light"):
        return pref
    if sys.platform == "darwin" and shutil.which("defaults"):
        try:
            out = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"],
                                 capture_output=True, text=True, timeout=2).stdout.strip().lower()
            return "dark" if "dark" in out else "light"
        except Exception:
            pass
    if shutil.which("gsettings"):
        for key in ("color-scheme", "gtk-theme"):
            try:
                out = subprocess.run(
                    ["gsettings", "get", "org.gnome.desktop.interface", key],
                    capture_output=True, text=True, timeout=2).stdout.strip().lower()
                if "dark" in out:
                    return "dark"
                if "light" in out:
                    return "light"
            except Exception:
                pass
    # $COLORFGBG like "0;default;15" (urxvt/rxvt): last field is the bg.
    fgbg = os.environ.get("COLORFGBG", "").strip()
    if fgbg:
        bg = fgbg.replace(":", ";").split(";")[-1].strip().lower()
        if bg in ("7", "15", "white", "light"):
            return "light"
        if bg not in ("default", ""):
            try:
                return "light" if int(bg) in (7, 15) else "dark"
            except ValueError:
                pass
    return "dark"


def run_tui(bible, initial: str = "") -> None:
    import curses
    # Standalone Esc otherwise takes ~1s: curses waits ESCDELAY (default
    # 1000ms) to disambiguate Esc from Alt+key sequences. 25ms keeps
    # Alt-combos working and makes Esc-to-NORMAL instant (setdefault:
    # users can still override via ESCDELAY in the environment).
    os.environ.setdefault("ESCDELAY", "25")

    ui = _UI(bible, initial or "")
    curses.wrapper(ui.main)


class _UI:
    def __init__(self, bible, initial: str):
        self.bible = bible
        self.query = initial
        self.hits: list = []
        self.nverses = 0
        self.ms = 0.0
        self.sel = 0
        self.top = 0
        self.mode = "normal"  # normal | insert (/) | command (:)
        self.buf = ""
        self.msg = "press / to search · :lookup <ref> · q to quit"
        self.theme = detect_theme()
        self._mono = True  # set False in main() when color is usable
        self._z_pending = False  # saw 'Z' in NORMAL, awaiting second Z/Q
        self.help_top = 0  # scroll offset for the help screen
        self.help_open = False  # help overlay (stays up while you type)
        self.examples_top = 0  # scroll offset for the examples screen
        self.examples_open = False  # :e overlay (stays up while you type)
        self._back: list = []  # jumplist: (query, hits, nverses, sel, msg, view, page)
        self._view = "search"  # "search" (paged display) | "read" (chapter/lookup)
        self._page = 0  # result page (searches page in 100s, nvim C-f/C-b)
        self._hist = {"insert": [], "command": []}  # prompt histories (raw lines)
        self._hpos = {"insert": 0, "command": 0}  # one-past-end cursor per history
        self._cursor = 0  # caret position inside the prompt buffer
        self._flash: str | None = None  # transient footer message, cleared on next key
        if initial:
            self._run(initial)

    def _say(self, text: str) -> None:
        """Show a transient footer message until the next keypress."""
        self.msg = text
        self._flash = text

    # -- model -----------------------------------------------------------
    def _run(self, q: str) -> None:
        q = q.strip()
        self.query = q
        if not q:
            self.hits, self.nverses, self.ms = [], 0, 0.0
            self._page = 0
            self._say("empty query")
            return
        # inline scope markers (same as `purebible search --chapter/--book`):
        # last one wins, stripped before the search runs
        constrain = "verse"
        marks = {"--verse": "verse", "--chapter": "chapter", "--book": "book"}
        canon_marks = {"--law": "law", "--prophets": "prophets", "--ot": "ot",
                       "--nt": "nt", "--gospels": "gospels",
                       "--letters": "letters", "--prophecy": "prophecy",
                       "--firstlast": "firstlast"}
        toks = q.split()
        scoped = [t for t in toks if t in marks]
        if scoped:
            constrain = marks[scoped[-1]]
            toks = [t for t in toks if t not in marks]
        canons = [t for t in toks if t in canon_marks]
        canon = canon_marks[canons[-1]] if canons else None
        if canons:
            toks = [t for t in toks if t not in canon_marks]
        q = " ".join(toks)
        if not q:
            self.hits, self.nverses, self.ms = [], 0, 0.0
            self._page = 0
            self._say("empty query")
            return
        if looks_like_reference(q):
            try:
                verses = resolve_reference(self.bible, q)
            except ValueError as e:
                self._say(str(e))
                return
            from .search import Hit
            self.hits = [Hit(v, 1) for v in verses]
            self.nverses = len(verses)
            self.ms = 0.0
            self.msg = f"lookup {q} — {len(verses)} verse(s)"
            self._view = "read"
        else:
            t0 = time.time()
            try:
                hits, nv = run_search(
                    self.bible, q, constrain=constrain,
                    books=CANON_GROUPS.get(canon) if canon else None)
            except Exception as e:  # never crash the TUI on bad pattern
                self._say(f"search error: {e}")
                return
            self.ms = (time.time() - t0) * 1000
            self.hits, self.nverses = hits, nv
            self.msg = f"{len(hits)} matches in {nv} verses · {self.ms:.0f} ms"
            self._view = "search"
        self.sel = 0
        self.top = 0
        self._page = 0

    def _cmd(self, cmd: str) -> bool:
        """Run a ':' command. Return False to quit."""
        c = cmd.strip()
        if c in ("q", "quit", "qa", "q!", "quit!"):
            return False
        if c in ("h", "help"):
            self.help_open = True
            self.help_top = 0
            self.examples_open = False
            return True
        if c in ("e", "examples"):
            self.examples_open = True
            self.examples_top = 0
            self.help_open = False
            return True
        if c in ("clear", "cls"):
            self.query = ""
            self.hits, self.nverses, self.ms = [], 0, 0.0
            self.sel = self.top = self._page = 0
            self._say("cleared")
            return True
        if c in ("w", "write"):
            self._say("nothing to save — the text is read-only (y to yank)")
            return True
        if c in ("wq", "x", "xa"):
            return False  # like nvim: nothing to write, just quit
        if c.startswith("lookup ") or c.startswith("l "):
            self._run(c.split(None, 1)[1])
        elif c.startswith("search ") or c.startswith("s "):
            self._run(c.split(None, 1)[1])
        elif c.startswith("yank"):
            self._yank()
        elif c == "":
            pass
        else:
            # bare text after ':' → treat as query (looking up refs works too)
            self._run(c)
        return True

    def _jump_back(self) -> None:
        """Restore the previous view (nvim C-o jumplist)."""
        if not self._back:
            self._say("already at oldest change")
            return
        q, hits, nv, sel, msg, view, page = self._back.pop()
        self.query, self.hits, self.nverses, self.msg = q, hits, nv, msg
        self._view, self._page = view, page
        self.sel = max(0, min(sel, self._shown_len() - 1)) if hits else 0
        self.top = 0

    def _shown_len(self) -> int:
        """Cursor rows available on the current page."""
        start, end, _ = self._page_bounds()
        return end - start

    def _page_bounds(self) -> tuple[int, int, int]:
        """(start, end, total) — searches page in 100s, reading doesn't."""
        total = len(self.hits)
        if self._view != "search" or total <= _SEARCH_CAP:
            return 0, total, total
        start = min(self._page * _SEARCH_CAP, max(0, total - 1))
        return start, min(start + _SEARCH_CAP, total), total

    def _visible_hits(self) -> tuple[list, int, int]:
        """(shown hits, hidden-before, hidden-after). sel is page-relative."""
        start, end, total = self._page_bounds()
        return self.hits[start:end], start, total - end

    def _hist_push(self, kind: str, line: str) -> None:
        hist = self._hist[kind]
        if line and (not hist or hist[-1] != line):
            hist.append(line)
            del hist[:-100]
        self._hpos[kind] = len(self._hist[kind])

    def _hist_move(self, kind: str, delta: int) -> None:
        hist = self._hist[kind]
        self._hpos[kind] = max(0, min(len(hist), self._hpos[kind] + delta))
        pos = self._hpos[kind]
        self.buf = hist[pos] if pos < len(hist) else ""
        self._cursor = len(self.buf)

    def _yank(self) -> None:
        if not self.hits:
            self._say("nothing to yank")
            return
        h = self.hits[self.sel]
        s = f"{h.verse.book_name} {h.verse.chapter}:{h.verse.verse} {h.verse.text}"
        for clip in (["xclip", "-selection", "clipboard"], ["xsel", "-ib"], ["pbcopy"], ["wl-copy"]):
            if shutil.which(clip[0]):
                try:
                    subprocess.run(clip, input=s.encode(), timeout=2, check=False,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    self._say(f"yanked: {h.verse.ref()}")
                    return
                except Exception:
                    pass
        self._say(s[:120])

    # -- curses ----------------------------------------------------------
    def main(self, stdscr) -> None:
        import curses
        try:
            curses.curs_set(0)
        except Exception:
            pass
        stdscr.keypad(True)
        # blocking input: zero idle work (no 100ms redraw spin). Resize still
        # arrives as KEY_RESIZE; flash messages clear on the next keypress.
        stdscr.timeout(-1)
        self._mono = bool(os.environ.get("NO_COLOR"))
        if curses.has_colors() and not self._mono:
            curses.start_color()
            try:
                curses.use_default_colors()
            except Exception:
                pass
            # palette: refs follow the system theme (cyan pops on dark
            # terminals but is unreadable on light ones — blue there);
            # mode tags get lualine-style per-mode colors.
            try:
                dark = self.theme == "dark"
                pairs = {1: curses.COLOR_CYAN if dark else curses.COLOR_BLUE,
                         2: curses.COLOR_BLUE,
                         3: curses.COLOR_GREEN,
                         4: curses.COLOR_YELLOW if dark else curses.COLOR_RED,
                         5: curses.COLOR_MAGENTA}
                for num, fg in pairs.items():
                    curses.init_pair(num, fg, -1)
                # number-highlighter pairs 6..11 (examples overlay)
                for i, fg in enumerate((curses.COLOR_GREEN, curses.COLOR_YELLOW,
                                        curses.COLOR_MAGENTA, curses.COLOR_RED,
                                        curses.COLOR_CYAN, curses.COLOR_BLUE)):
                    curses.init_pair(6 + i, fg, -1)
            except Exception:
                self._mono = True
            # reference-number tan (pair 12): isolated so a missing
            # 256-color tan can never knock out the main palette
            try:
                if not self._mono:
                    tan = 180 if curses.COLORS >= 256 else curses.COLOR_YELLOW
                    curses.init_pair(12, tan, -1)
            except Exception:
                pass

        # nvim-style: always start in NORMAL (even with no query).

        while True:
            self._draw(stdscr)
            ch = stdscr.getch()
            rc = self._key(stdscr, ch)
            if rc == "quit":
                return

    def _key(self, stdscr, ch: int):
        import curses
        self._flash = None  # any key dismisses the transient message
        if self.mode in ("insert", "command"):
            kind = self.mode
            if ch == 27:  # Esc → back to NORMAL. Help stays open if it was.
                self.mode, self.buf, self._cursor = "normal", "", 0
                self._hpos[kind] = len(self._hist[kind])
            elif ch in (10, 13, curses.KEY_ENTER):
                line = self.buf
                self.buf = ""
                self._cursor = 0
                self.mode = "normal"
                self._hist_push(kind, line)
                self.help_open = False  # reveal the results underneath
                self.examples_open = False
                if kind == "insert":
                    self._run(line)
                else:
                    if not self._cmd(line):
                        return "quit"
            elif ch in (curses.KEY_BACKSPACE, 127, 8):
                if self._cursor > 0:
                    self.buf = self.buf[:self._cursor - 1] + self.buf[self._cursor:]
                    self._cursor -= 1
            elif ch == curses.KEY_RESIZE:
                pass
            elif ch == curses.KEY_UP:
                self._hist_move(kind, -1)  # older line (nvim <Up> in cmdline)
            elif ch == curses.KEY_DOWN:
                self._hist_move(kind, +1)  # newer line
            elif ch == curses.KEY_LEFT:
                self._cursor = max(0, self._cursor - 1)
            elif ch == curses.KEY_RIGHT:
                self._cursor = min(len(self.buf), self._cursor + 1)
            elif ch == 21:  # C-u → clear line (readline/nvim cmdline)
                self.buf = ""
                self._cursor = 0
            elif ch == 23:  # C-w → delete word back (readline)
                head = self.buf[:self._cursor].rstrip()
                cut = head.rfind(" ")
                head = "" if cut < 0 else head[:cut + 1]
                self.buf = head + self.buf[self._cursor:]
                self._cursor = len(head)
            elif 32 <= ch <= 126:
                self.buf = self.buf[:self._cursor] + chr(ch) + self.buf[self._cursor:]
                self._cursor += 1
            return None

        # NORMAL mode — the ONLY mode that can quit (nvim-style).
        # With an overlay open, dismiss keys only close it, never quit.
        if (self.help_open or self.examples_open) and ch in (ord("q"), 27, 10, 13, curses.KEY_ENTER, ord("Z")):
            self.help_open = False
            self.examples_open = False
            self._z_pending = False
            return None
        if ch == 27:
            # Esc in NORMAL is a no-op (nvim) — it must never quit.
            self._z_pending = False
            return None
        if self._z_pending:
            self._z_pending = False
            if ch in (ord("Z"), ord("Q")):
                return "quit"
            # otherwise fall through and handle ch normally
        elif ch == ord("Z"):
            self._z_pending = True
            return None
        if ch == ord("q"):
            return "quit"
        if ch in (ord("/"),):
            self.mode, self.buf, self._cursor = "insert", "", 0  # fresh search
        elif ch == ord("i"):
            # caret after '/' but before the current query: prepend to it
            self.mode, self.buf = "insert", self.query
            self._cursor = 0
        elif ch == ord("a"):
            # caret at the end of the current query: append to it
            self.mode, self.buf = "insert", self.query
            self._cursor = len(self.query)
        elif ch == ord(":"):
            self.mode, self.buf, self._cursor = "command", "", 0
        elif self.help_open or self.examples_open:
            # overlay open: scroll it (dismiss keys handled above;
            # / i a : above enter their prompts with it still open)
            entries = HELP if self.help_open else self._examples_entries()
            top = self.help_top if self.help_open else self.examples_top
            try:
                _H, _W = stdscr.getmaxyx()
            except Exception:
                _W = 80
            if _W >= 80 and self.help_open:
                bound = len(_layout_help_rows(entries)) - 1
            else:
                bound = len(entries) - 1
            page = 15
            if ch in (ord("j"), curses.KEY_DOWN):
                top += 1
            elif ch in (ord("k"), curses.KEY_UP):
                top = max(0, top - 1)
            elif ch == 4:  # C-d
                top += page
            elif ch == 21:  # C-u
                top = max(0, top - page)
            elif ch == ord("G"):
                top = max(0, bound)
            elif ch == ord("g"):
                top = 0
            top = min(top, max(0, bound))
            if self.help_open:
                self.help_top = top
            else:
                self.examples_top = top
            return None
        elif ch in (ord("j"), curses.KEY_DOWN):
            self.sel = min(self._shown_len() - 1, self.sel + 1) if self.hits else 0
        elif ch in (ord("k"), curses.KEY_UP):
            self.sel = max(0, self.sel - 1)
        elif ch == 4:  # C-d
            self.sel = min(self._shown_len() - 1, self.sel + 15) if self.hits else 0
        elif ch == 21:  # C-u
            self.sel = max(0, self.sel - 15)
        elif ch == ord("G"):
            self.sel = self._shown_len() - 1 if self.hits else 0
        elif ch == ord("g"):
            self.sel = 0  # (gg == g; fine for a pager)
        elif ch in (ord("n"),):
            self.sel = min(self._shown_len() - 1, self.sel + 1) if self.hits else 0
        elif ch in (ord("N"),):
            self.sel = max(0, self.sel - 1)
        elif ch in (ord("y"),):
            self._yank()
        elif ch == 15:  # C-o → jump back (nvim jumplist; Enter pushes)
            self._jump_back()
        elif ch == 6:  # C-f → next page of 100 (nvim)
            if self._view == "search" and (self._page + 1) * _SEARCH_CAP < len(self.hits):
                self._page += 1
                self.sel = 0
                self.top = 0
            else:
                self._say("last page")
        elif ch == 2:  # C-b → previous page (nvim)
            if self._page > 0:
                self._page -= 1
                self.sel = 0
                self.top = 0
            else:
                self._say("first page")
        elif ch in (10, 13, curses.KEY_ENTER):
            shown, _, _ = self._visible_hits()
            if shown and self.sel < len(shown):
                h = shown[self.sel]
                # remember where we were, then jump to the whole chapter
                self._back.append((self.query, self.hits, self.nverses,
                                   self.sel, self.msg, self._view, self._page))
                del self._back[:-50]
                ch_verses = self.bible.chapter(h.verse.book, h.verse.chapter)
                from .search import Hit
                self.hits = [Hit(v, 1) for v in ch_verses]
                self.nverses = len(ch_verses)
                self._view = "read"  # chapters always show in full
                self.query = h.verse.ref()
                self._say(f"chapter: {h.verse.book_name} {h.verse.chapter} (C-o back)")
                self.sel = max(0, min(self.sel, len(self.hits) - 1))
                self.top = 0
        return None

    def _draw(self, stdscr) -> None:
        import curses
        H, W = stdscr.getmaxyx()
        stdscr.erase()
        # slim title line — plain text, no highlighting (stats live bottom-right)
        try:
            stdscr.addnstr(0, 0, "▚ purebible · KJV 1769"[:W], W, self._ref_attr())
        except Exception:
            pass
        if self.help_open:
            self.help_top = self._draw_help(stdscr, H, W, HELP, self.help_top)
            right = self._stats_right()
            if self.mode == "insert":
                self._footer(stdscr, H, W, "insert", f"/{self.buf}", right,
                             cursor=1 + self._cursor)
            elif self.mode == "command":
                self._footer(stdscr, H, W, "command",
                             f"/{self.query}" if self.query else "", right)
                self._draw_cmd_popup(stdscr, H, W)
            else:
                self._footer(stdscr, H, W, "help", "", right)
            return
        if self.examples_open:
            entries = self._examples_entries()
            rep = _repeat_number_runs([_ROW_NUM_RE.sub("", t)
                                       for k, t in entries if k == "row"])
            num_map = {m: i % _NUM_PAIRS for i, m in enumerate(rep)}
            self.examples_top = self._draw_help(stdscr, H, W, entries,
                                                self.examples_top, num_map,
                                                single_column=True)
            right = self._stats_right()
            if self.mode == "insert":
                self._footer(stdscr, H, W, "insert", f"/{self.buf}", right,
                             cursor=1 + self._cursor)
            elif self.mode == "command":
                self._footer(stdscr, H, W, "command",
                             f"/{self.query}" if self.query else "", right)
                self._draw_cmd_popup(stdscr, H, W)
            else:
                self._footer(stdscr, H, W, "examples", "", right)
            return
        fresh = not self.hits and not self.query  # nothing searched yet
        if fresh:
            self._draw_welcome(stdscr, H, W)
            return
        # query line
        qline = f"/{self.query}" if self.query else "/(type / to search, :lookup John 3:16)"
        try:
            stdscr.addnstr(1, 0, qline[:W], W, curses.A_BOLD)
        except Exception:
            pass

        # results area: rows 2..H-3 (paged: only visible hits get wrapped)
        top = 2
        bottom = max(top, H - 2)
        # build wrapped lines: (hit_idx, ref_part, text_part) so the ref
        # gets ref color while verse text stays uniform on every line
        shown, before, after = self._visible_hits()
        total = len(self.hits)
        lines: list[tuple[int, str, str]] = []
        for i, h in enumerate(shown):
            ref = f"{h.verse.ref()}  "
            avail = max(10, W - len(ref) - 1)
            wrapped = textwrap.wrap(h.verse.text, width=avail) or [""]
            lines.append((i, ref, wrapped[0]))
            pad = " " * min(len(ref), 12)
            for cont in wrapped[1:]:
                lines.append((i, pad, cont))
        if after:
            lines.append((-1, "", f"… {after} more — C-f next page · refine with | & *"))
        elif before:
            lines.append((-1, "", f"— end of {total} · C-b previous page —"))
        if not lines:
            # query ran but found nothing (fresh state is handled earlier)
            self._draw_centered(
                stdscr, H, W, top,
                [(f"No matches for '{self.query}'", "bold"),
                 ("", ""),
                 ("try wildcards (love*) · OR (a | b) · :h for patterns", "dim")])
        else:
            # keep selection visible (plus the page marker below the last row)
            first = next((li for li, (hi, _, _) in enumerate(lines) if hi == self.sel), 0)
            if (after or before) and self.sel == len(shown) - 1:
                first = len(lines) - 1
            if first < self.top:
                self.top = first
            page = bottom - top
            if first >= self.top + page:
                self.top = first - page + 1
            for row in range(page):
                li = self.top + row
                if li >= len(lines):
                    break
                hi, ref_part, text_part = lines[li]
                if hi == -1:  # overflow marker: dim, never selectable
                    dim = getattr(curses, "A_DIM", 0)
                    try:
                        stdscr.addnstr(top + row, 0, text_part[:W], W, dim)
                    except Exception:
                        pass
                    continue
                ref_attr = self._ref_attr()
                if hi == self.sel:
                    # subtle selection: theme-colored bold text, no
                    # reverse-video white block.
                    text_attr = self._sel_attr()
                else:
                    text_attr = 0
                try:
                    stdscr.addnstr(top + row, 0, ref_part[:W], W, ref_attr)
                    if len(ref_part) < W:
                        stdscr.addnstr(top + row, len(ref_part), text_part[:W - len(ref_part)],
                                       W - len(ref_part), text_attr)
                except Exception:
                    pass

        # statusline: mode tag + current search, stats right-aligned
        right = self._stats_right()
        if self.mode == "insert":
            self._footer(stdscr, H, W, "insert", f"/{self.buf}", right,
                         cursor=1 + self._cursor)
        elif self.mode == "command":
            self._footer(stdscr, H, W, "command",
                         f"/{self.query}" if self.query else "", right)
            self._draw_cmd_popup(stdscr, H, W)
        else:
            self._footer(stdscr, H, W, "normal",
                         f"/{self.query}" if self.query else "", right)

    def _ref_attr(self) -> int:
        """Theme-colored ref attribute (bold monochrome under NO_COLOR)."""
        import curses
        if self._mono:
            return curses.A_BOLD
        try:
            return curses.color_pair(1) | curses.A_BOLD
        except Exception:
            return curses.A_BOLD

    def _sel_attr(self) -> int:
        """Subtle selection attribute: bold (+ theme color), never reverse."""
        import curses
        if self._mono:
            return curses.A_BOLD
        try:
            return curses.color_pair(1) | curses.A_BOLD
        except Exception:
            return curses.A_BOLD

    def _examples_entries(self) -> list[tuple[str, str]]:
        """One list, greatest to least: static EXAMPLES plus live canon
        group totals. Every row runs from / as typed."""
        gw = self.bible.group_words()
        total = self.bible.totals()[1]
        labels = [("ot", "old testament"), ("nt", "new testament"),
                  ("prophets", "prophets"), ("law", "law"),
                  ("gospels", "gospels"), ("letters", "letters (Rom-Jude)"),
                  ("prophecy", "prophecy (Revelation)"),
                  ("firstlast", "first & last books")]
        items: list[tuple[str, int, str, str]] = [
            (q, int(c.split("/")[0].replace(",", "").strip()), c, m)
            for q, c, m, _ in EXAMPLES
        ]
        items.append(("?*", total, f"{total:,}", "every word & number"))
        items += [(f"?* --{g}", gw[g], f"{gw[g]:,}", lab) for g, lab in labels]
        items.sort(key=lambda t: -t[1])
        width = max(len(q) for q, _, _, _ in items)
        rows: list[tuple[str, str]] = list(EXAMPLES_HELP)
        rows += [("row", f"  {i:04d} / {q.ljust(width)}  {c} — {m}")
                 for i, (q, _, c, m) in enumerate(items, 1)]
        return rows

    def _stats_right(self) -> str:
        """Pattern-finding stats: hits · verses · chapters · books.

        Idle (nothing searched): total words · OT words · NT words.
        """
        if not self.hits and not self.query:
            nw = self.bible.totals()[1]
            gw = self.bible.group_words()
            return f"{nw:,} words · {gw['ot']:,} OT · {gw['nt']:,} NT"
        n_books = len({h.verse.book for h in self.hits if h.verse.book})
        n_chaps = len({(h.verse.book, h.verse.chapter) for h in self.hits})

        def pl(n: int, one: str, many: str) -> str:
            return f"{n} {one if n == 1 else many}"

        mid = (f"{pl(len(self.hits), 'hit', 'hits')} · "
               f"{pl(self.nverses, 'verse', 'verses')} · "
               f"{pl(n_chaps, 'chapter', 'chapters')} · "
               f"{pl(n_books, 'book', 'books')}")
        shown, before, after = self._visible_hits()
        if before or after:
            return f"{before + 1}–{before + len(shown)} of {mid}"
        return mid

    def _tag_attr(self, kind: str, reverse: bool = True) -> int:
        """lualine-style mode-tag highlight (theme color, reverse for footer)."""
        import curses
        if self._mono:
            base = curses.A_BOLD
        else:
            pair = {"normal": 2, "insert": 3, "command": 4, "help": 5,
                    "examples": 5}.get(kind, 2)
            try:
                base = curses.color_pair(pair) | curses.A_BOLD
            except Exception:
                base = curses.A_BOLD
        return base | curses.A_REVERSE if reverse else base

    def _footer(self, stdscr, H: int, W: int, kind: str, rest: str, right: str = "",
                cursor: int = -1) -> None:
        """Status bar: highlighted mode tag + current search, stats right-aligned.

        Mode tag uses the theme color with reverse highlighting (stats dimmed).
        A transient flash message takes over the middle until next keypress.
        cursor >= 0 draws a block caret at that index of rest (prompt modes).
        """
        import curses
        if self._flash is not None:
            rest, right, cursor = self._flash, "", -1
        tags = {"normal": " NORMAL ▶ ", "insert": " INSERT ▶ ",
                "command": " COMMAND ▶ ", "help": " HELP ▶ ",
                "examples": " EXAMPLES ▶ "}
        tag = tags.get(kind, " NORMAL ▶ ")
        gap = "  "
        dim = getattr(curses, "A_DIM", 0)
        try:
            x = 0
            stdscr.addnstr(H - 1, 0, tag[:W], W, self._tag_attr(kind))
            x = min(W, len(tag))
            if x < W:
                stdscr.addnstr(H - 1, x, gap[:W - x], W - x, 0)
                x = min(W, x + len(gap))
            if rest and x < W:
                if 0 <= cursor <= len(rest):
                    pre, ch, post = rest[:cursor], rest[cursor:cursor + 1], rest[cursor + 1:]
                    stdscr.addnstr(H - 1, x, pre[:W - x], W - x, 0)
                    x = min(W, x + len(pre))
                    if x < W:
                        stdscr.addnstr(H - 1, x, (ch if ch else " ")[:W - x],
                                       W - x, curses.A_REVERSE)
                        x = min(W, x + 1)
                    if post and x < W:
                        stdscr.addnstr(H - 1, x, post[:W - x], W - x, 0)
                        x = min(W, x + len(post))
                else:
                    stdscr.addnstr(H - 1, x, rest[:W - x], W - x, 0)
                    x = min(W, x + len(rest))
            if right:
                rx = max(x + 1, W - len(right))
                if rx < W:
                    stdscr.addnstr(H - 1, rx, right[:W - rx], W - rx, dim)
        except Exception:
            pass
        stdscr.refresh()

    def _draw_cmd_popup(self, stdscr, H: int, W: int) -> None:
        """noice.nvim-style centered cmdline popup in the upper third."""
        import curses
        full = ":" + self.buf
        cur = 1 + self._cursor  # caret index into full
        inner = min(56, W - 10)
        if inner < 8 or H < 8:
            return  # too narrow: fall back to the status bar below
        if len(full) <= inner:
            disp, ccur = full, cur
        else:
            # window around the caret so it stays visible
            end = min(len(full), max(cur + 5, inner - 1))
            start = max(0, end - (inner - 1))
            disp = ("…" if start > 0 else "") + full[start:end]
            ccur = cur - start + (1 if start > 0 else 0)
        bw = inner + 4
        x0 = max(0, (W - bw) // 2)
        y0 = max(1, H // 3 - 1)
        border = self._tag_attr("command", reverse=False)
        title = " COMMAND "
        if bw >= len(title) + 6:
            top = "┌─" + title + "─" * (bw - 3 - len(title)) + "┐"
        else:
            top = "┌" + "─" * (bw - 2) + "┐"
        bot = "└" + "─" * (bw - 2) + "┘"
        try:
            stdscr.addnstr(y0, x0, top[:W - x0], W - x0, border)
            # content with block caret
            cx = x0 + 2
            pre = disp[:ccur]
            stdscr.addnstr(y0 + 1, x0, "│ "[:W - x0], W - x0, border)
            stdscr.addnstr(y0 + 1, cx, pre[:(W - cx)], W - cx, curses.A_BOLD)
            cx = min(W, cx + len(pre))
            if cx < W:
                ch = disp[ccur:ccur + 1] if ccur < len(disp) else " "
                stdscr.addnstr(y0 + 1, cx, ch[:W - cx], W - cx, curses.A_REVERSE)
                cx = min(W, cx + 1)
            if cx < W:
                post = disp[ccur + 1:].ljust(max(0, inner - ccur - 1))
                stdscr.addnstr(y0 + 1, cx, post[:W - cx], W - cx, curses.A_BOLD)
                cx = min(W, cx + len(post))
            if cx < W:
                stdscr.addnstr(y0 + 1, cx, " │"[:W - cx], W - cx, border)
            stdscr.addnstr(y0 + 2, x0, bot[:W - x0], W - x0, border)
        except Exception:
            pass

    def _draw_help(self, stdscr, H: int, W: int,
                     entries: list[tuple[str, str]], top: int,
                     num_map: dict[str, int] | None = None,
                     single_column: bool = False) -> int:
        """Draw a scrolling overlay (HELP or EXAMPLES); return new top.

        num_map (number -> palette index) highlights repeated numbers so
        shared totals visibly link across example rows; None disables.
        single_column forces one column (the examples overlay).
        """
        import curses
        t = 2
        visible = max(1, H - 3)  # rows 2..H-2; footer lives at H-1
        two = W >= 80 and not single_column
        if not two:
            top = max(0, min(top, max(0, len(entries) - visible)))
            for row in range(visible):
                li = top + row
                if li >= len(entries):
                    break
                self._draw_help_cell(stdscr, t + row, 0, entries[li], W, W,
                                     num_map)
            return top
        row_groups = _layout_help_rows(entries)
        top = max(0, min(top, max(0, len(row_groups) - visible)))
        col_w = (W - 3) // 2
        for row in range(visible):
            li = top + row
            if li >= len(row_groups):
                break
            left, right = row_groups[li]
            if left is not None:
                self._draw_help_cell(stdscr, t + row, 0, left,
                                     col_w, W, num_map)
            if right is not None:
                self._draw_help_cell(stdscr, t + row, col_w + 3, right,
                                     col_w, W, num_map)
            try:
                stdscr.addnstr(t + row, col_w + 1, "│"[:W - col_w - 1],
                               W - col_w - 1, getattr(curses, "A_DIM", 0))
            except Exception:
                pass
        return top

    def _num_attr(self, pair_idx: int) -> int:
        """Palette style for a highlighted number: 6 colors alternate
        bold then plain for 12 distinct styles before cycling."""
        import curses
        if self._mono:
            return curses.A_BOLD
        try:
            base = curses.color_pair(6 + (pair_idx % _NUM_PAIRS))
        except Exception:
            return curses.A_BOLD
        if (pair_idx % (2 * _NUM_PAIRS)) < _NUM_PAIRS:
            return base | curses.A_BOLD
        return base

    def _draw_help_cell(self, stdscr, y: int, x: int,
                         entry: tuple[str, str], width: int, W: int,
                         num_map: dict[str, int] | None = None) -> None:
        import curses
        kind, text = entry
        if kind == "h1" and not self._mono:
            attr = curses.color_pair(1) | curses.A_BOLD
        elif kind in ("h1", "h2"):
            attr = curses.A_BOLD
        else:
            attr = 0
        disp = text[:width][:max(0, W - x)]
        if not num_map or kind != "row" or not disp:
            try:
                stdscr.addnstr(y, x, disp, max(0, W - x), attr)
            except Exception:
                pass
            return
        # segmented draw: repeated numbers glow in their mapped color.
        # A leading reference number (if any) always stays plain.
        cx = x
        m_pre = _ROW_NUM_RE.match(disp)
        if m_pre:
            try:
                pre_attr = 0 if self._mono else curses.color_pair(12)
            except Exception:
                pre_attr = 0
            try:
                stdscr.addnstr(y, cx, m_pre.group(1)[:max(0, W - cx)],
                               max(0, W - cx), pre_attr)
            except Exception:
                pass
            cx += len(m_pre.group(1))
            disp = disp[len(m_pre.group(1)):]
        # glow numbers in the query/count head; the "— note" lights
        # only numbers greater than 10
        head, sep, note = disp.partition(" — ")
        for tok in re.split(r"(\d+)", head):
            if not tok or cx >= W:
                break
            if tok.isdigit() and tok in num_map:
                a = self._num_attr(num_map[tok])
            else:
                a = attr
            try:
                stdscr.addnstr(y, cx, tok[:max(0, W - cx)], max(0, W - cx), a)
            except Exception:
                pass
            cx += len(tok)
        if sep and cx < W:
            rest = sep + note
            # notes: shared numbers greater than 10 only
            for tok in re.split(r"(\d+)", rest):
                if not tok or cx >= W:
                    break
                if tok.isdigit() and tok in num_map and int(tok) > 10:
                    a = self._num_attr(num_map[tok])
                else:
                    a = attr
                try:
                    stdscr.addnstr(y, cx, tok[:max(0, W - cx)],
                                   max(0, W - cx), a)
                except Exception:
                    pass
                cx += len(tok)

    def _center_attr(self, kind: str) -> int:
        import curses
        if kind == "title":
            return curses.A_BOLD if self._mono else curses.color_pair(1) | curses.A_BOLD
        if kind == "bold":
            return curses.A_BOLD
        if kind == "dim":
            return getattr(curses, "A_DIM", 0)
        return 0

    def _draw_centered(self, stdscr, H: int, W: int, top: int,
                       lines: list[tuple[str, str]]) -> None:
        """Vertically centered block in rows top..H-2. lines = (text, kind)."""
        start = max(top, top + max(0, (H - 2 - top - len(lines)) // 2))
        for i, (text, kind) in enumerate(lines):
            y = start + i
            if y >= H - 1 or not text:
                continue
            x = max(0, (W - len(text)) // 2)
            try:
                stdscr.addnstr(y, x, text[:max(0, W - x)], max(0, W - x),
                               self._center_attr(kind))
            except Exception:
                pass

    def _draw_welcome(self, stdscr, H: int, W: int) -> None:
        """Start screen: one Words heading plus directions to the overlays."""
        total = self.bible.totals()[1]
        heading = "7\u2077" if total == 823543 else f"{total:,}"
        self._draw_centered(stdscr, H, W, 1, [(heading, "title"),
                                              ("Words", "dim"),
                                              ("", ""),
                                              ("type :h for help", "title"),
                                              ("type :e for examples", "title")])
        right = self._stats_right()
        if self.mode == "insert":
            self._footer(stdscr, H, W, "insert", f"/{self.buf}", right,
                         cursor=1 + self._cursor)
        elif self.mode == "command":
            self._footer(stdscr, H, W, "command", "", right)
            self._draw_cmd_popup(stdscr, H, W)
        else:
            self._footer(stdscr, H, W, "normal", "", right)
