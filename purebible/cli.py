"""purebible CLI — compatible with the C++ purebible wrapper, plus TUI.

    purebible search [options] <phrase>
    purebible lookup [options] <reference>
    purebible bibles
    purebible tui [initial-query]
    purebible help

No subcommand + looks-like-reference → lookup, else search.
No args + TTY → launch the TUI (nvim/btop crowd); piped → help.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import time

from . import __version__
from .bible import Bible, ABBREV, FULLNAME, BOOK_ORDER, CANON_GROUPS
from .refs import looks_like_reference, parse_reference, resolve_reference
from .search import search as run_search

EXAMPLES = """EXAMPLES  (counts verified in this engine, greatest to least):
  single words:
  purebible "Jesus"                                    973, 139x7, cf Jesus* 983
  purebible "mercy"                                    276, every soul saved (Acts 27:37)
  purebible "Abraham"                                  231, 33x7 (cf wine: 231)
  purebible "wine"                                     231, 33x7 (Gen 14:18)
  purebible "beast"                                    180, 60+60+60
  purebible "atonement"                       81 hits in exactly 70 verses, 10x7
  purebible "cross"                                     28, 4x7
  purebible "charity"                                   28, 4x7, greatest of these
  purebible "forgiveness"                                7 in 7 verses
  phrases (exact, consecutive words):
  purebible "saith the LORD"                           854, 122x7, God's signature
  purebible "Son of man"                               196, 28x7, Jesus' title
  purebible "Jesus Christ"                             196, 28x7, same as Son of man!
  purebible "Lord Jesus Christ"                         84, 12x7, the full title
  purebible "Verily I say unto"                        77, Jesus' signature phrase
  purebible "Know that I am the LORD"                  77 in 77 verses
  purebible "The Voice of the LORD"                    49 in 47 verses, 7x7
  purebible "In the beginning"                          17, matches 'for it is written'
  purebible "for it is written"                         17, matches 'In the beginning'
  purebible "Holy Spirit"                                7 in 7 verses (cf Holy Ghost: 90)
  purebible "bottomless pit"                             7 in 7 verses
  purebible "a thousand years"                           7, Rev 20+
  purebible "six hundred thousand"                       7, the Exodus multitude
  purebible "His love"                                  7 in 7 verses, first Deut 7:7
  multi-word (OR / AND / wildcards / case):
  purebible "grace | mercy | peace"                   875, 125x7, pastoral greeting
  purebible "The Father | Holy Ghost | The Word"      777, the Godhead named (OR)
  purebible "mercy | truth"                           511, 73x7, met together (Ps 85:10)
  purebible "Sin | Forgiven"                          490, 70x7 (forgiven joins forgiv* 112)
  purebible "Justified | Blood"                       490, 70x7
  purebible "disciple*"                               273, 39x7 (cf 39 OT books)
  purebible "sing*"                                    196, wildcard sing/sang/sung, 28x7, same as the twins
  purebible "preach*"                                  154, fishers of men + PREACHER title
  purebible "repent*"                                  112, 16x7, twin of forgiv*
  purebible "forgiv*"                                  112, 16x7, twin of repent*
  purebible "bond | free"                    78 hits in exactly 70 verses, like atonement
  purebible "male | female"                   70 hits in 49 verses, both sevened
  purebible "flesh & blood"                             28, AND in same verse, 4x7
  purebible "blood & water"                             18, from His side: life
  purebible "Peter & John"                              18, the two pillars
  flags (case/scopes):
  purebible "LORD\\C"                                 6391, 913x7, the covenant name
  purebible search --chapter "faith & grace"          1281, 183x7 (Eph 2:8 inside)
  purebible "Lord\\C"                                 1211, 173x7
  purebible search --book "hope & charity"             938, 134x7
  purebible "Amen\\C"                                    77, last word of the Bible (\\C sensitive)
  purebible "Word\\C"                                     7, the divine Word, all John
  signatures (cross-referenced totals):
  purebible "David | Abraham"                          1316, 188x7 (lamb family)
  purebible "Jesus*"                                   983, 967 + 6 JESUS + 10 Jesus'
  purebible search --firstlast "In | Amen"             777, first & last words
  purebible "Beast | Mark | Sin"                       666, the beast number
  purebible search --law "Moses"                       639, 634 in text + 5 titles
  purebible search --law "In | Israel" --case          613, 613 commandments, in 555 verses
  purebible "Christ"                                   555, OT ends 634,555
  purebible search --firstlast "God | Jesus"           343, 7x7x7, first & last books
  purebible "book"                                     214, 188 in text + 26 titles
  purebible "call"                                     196, 28x7, joins the 196 twins
  purebible "lamb*"                                    188, Lamb's book of life
  purebible search --nt "life*"                        188, book of life, NT only
  purebible "hearken"                                  153, cf 153 fishes
  purebible search --gospels "Fish | Men"               153, 153 fishes, John 21
  purebible "tribes"                                   112, 16x7, joins repent*/forgiv*
  purebible "beloved"                                  111, 3x37
  purebible search --nt "Moses"                          77, Moses 77x in the NT
  purebible "Son & Jesus"                                70 in 70 verses, like atonement
  purebible search --gospels "cross | tree"              49, 7x7 in Gospels
  purebible "Word of God"                                49, 7x7
  purebible search --gospels "love" --case               49, lowercase love, 7x7
  purebible "crucified"                                 37 in 37 verses
  purebible "ordained"                                  37 in 37 verses, incl. titles
  purebible "saviour"                                   37 in 37 verses
  purebible "sow"                                       37 in 37 verses, the sower
  purebible "thirty and seven"                           7 in 7 verses, thirty-seven sevened
  canon scopes (filters, last wins):
  purebible search --ot "?*"                       634555, every OT word
  purebible search --nt "?*"                       188983, every NT word
  purebible search --gospels "Jesus"                 617, of 973, none in the OT
  purebible search --letters "grace"                 114 in 106 verses
  purebible tui "The Father | Holy Ghost | The Word"  (open the 777 in the TUI)
"""


def _add_search_opts(p: argparse.ArgumentParser) -> None:
    p.add_argument("-b", "--bible", default="1", help="Bible database UUID-Index (only 1 = KJV 1769 in native Python)")
    p.add_argument("-c", "--case", action="store_true", help="Case-sensitive search")
    p.add_argument("-a", "--accent", action="store_true", help="Accepted, ignored (KJV has no accents)")
    p.add_argument("-y", "--hyphen", action="store_true", help="Hyphen-sensitive search (Bar-jesus stays one word by default)")
    p.add_argument("-A", "--abbrev", action="store_true", help="Abbreviated book names")
    p.add_argument("-w", "--no-wordindex", action="store_true", help="Omit the word index from references")
    p.add_argument("-d", "--no-dup", action="store_true", help="Drop duplicate verses (one row per verse)")
    p.add_argument("--comma", action="store_true", help="Refs comma-separated on one line")
    p.add_argument("--refs-only", action="store_true", help="Print references without verse text")
    p.add_argument("--book", dest="constrain", action="store_const", const="book", help="Constrain '&' matches to whole books")
    p.add_argument("--chapter", dest="constrain", action="store_const", const="chapter", help="Constrain '&' matches to whole chapters")
    p.add_argument("--verse", dest="constrain", action="store_const", const="verse", help="Constrain '&' matches to whole verses (default)")
    p.add_argument("--law", dest="canon", action="store_const", const="law", help="Search Genesis–Deuteronomy only")
    p.add_argument("--prophets", dest="canon", action="store_const", const="prophets", help="Search Major + Minor Prophets only")
    p.add_argument("--ot", dest="canon", action="store_const", const="ot", help="Search Old Testament only")
    p.add_argument("--nt", dest="canon", action="store_const", const="nt", help="Search New Testament only")
    p.add_argument("--gospels", dest="canon", action="store_const", const="gospels", help="Search Matthew–John only")
    p.add_argument("--letters", dest="canon", action="store_const", const="letters", help="Search Romans–Jude only")
    p.add_argument("--prophecy", dest="canon", action="store_const", const="prophecy", help="Search Revelation only")
    p.add_argument("--firstlast", dest="canon", action="store_const", const="firstlast", help="Search Genesis + Revelation only")
    p.add_argument("--count", action="store_true", help="Print only the match count")
    p.add_argument("--limit", type=int, default=0, help="Max rows to print (0 = all)")
    p.add_argument("--no-color", action="store_true", help="Disable colorized refs")
    p.add_argument("--text", default=None, help="Path to SW1769Bible_both.txt (or PUREBIBLE_TEXT)")
    p.add_argument("phrase", nargs=argparse.REMAINDER, help="Search phrase (supports | & * wildcards)")
    p.set_defaults(constrain="verse")


def _add_lookup_opts(p: argparse.ArgumentParser) -> None:
    p.add_argument("-b", "--bible", default="1", help="Bible database UUID-Index (only 1 in native Python)")
    p.add_argument("-r", "--reference", dest="include_ref", action="store_true", default=True, help="Include reference (default on)")
    p.add_argument("--no-reference", dest="include_ref", action="store_false", help="Omit reference text")
    p.add_argument("-a", "--abbrev", action="store_true", help="Abbreviated book names")
    p.add_argument("--no-color", action="store_true", help="Disable colorized refs")
    p.add_argument("--text", default=None, help="Path to SW1769Bible_both.txt")
    p.add_argument("reference", nargs=argparse.REMAINDER, help="Passage reference, e.g. 'John 3:16'")
    p.add_argument("-m", "--html", action="store_true", help="Accepted for compat (plain output)")
    p.add_argument("-j", "--no-jesus", action="store_true", help="Compat no-op")
    p.add_argument("-t", "--no-transchange", action="store_true", help="Hide […] translation-change markup")
    p.add_argument("--brackets", action="store_true", help="Compat no-op (markup already uses brackets)")
    p.add_argument("-p", "--no-pilcrows", action="store_true", help="Hide ¶ pilcrows")
    p.add_argument("--no-ps119", action="store_true", help="Compat no-op")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="purebible", add_help=False,
                                 description="King James Pure Bible Search — Python terminal client")
    ap.add_argument("--version", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    ps = sub.add_parser("search", add_help=False)
    _add_search_opts(ps)
    pl = sub.add_parser("lookup", add_help=False)
    _add_lookup_opts(pl)
    pb = sub.add_parser("bibles", add_help=False)
    pb.add_argument("--text", default=None)
    pt = sub.add_parser("tui", add_help=False)
    pt.add_argument("--text", default=None)
    pt.add_argument("query", nargs=argparse.REMAINDER)
    ph = sub.add_parser("help", add_help=False)
    return ap


SEARCH_OPTS = [
    ("-b, --bible <n>", "Bible database (default: 1 = KJV 1769)"),
    ("-c, --case", "Case-sensitive search"),
    ("-a, --accent", "Accepted, ignored (KJV has no accents)"),
    ("-y, --hyphen", "Hyphen-sensitive (compounds stay split)"),
    ("-A, --abbrev", "Abbreviated book names"),
    ("-w, --no-wordindex", "Omit the word index from references"),
    ("-d, --no-dup", "Drop duplicate verses"),
    ("--comma", "Refs comma-separated on one line"),
    ("--refs-only", "Print references without the verse text"),
    ("--book/--chapter/--verse", "Scope for '&' queries (default: verse)"),
    ("--law/--prophets/--ot/--nt", "Canon scope: law, prophets, OT, NT,"),
    ("--gospels/--letters/--prophecy", "gospels, letters (Rom–Jude), Revelation"),
    ("--firstlast", "Genesis + Revelation (first & last books)"),
    ("--count", "Print only the match count"),
    ("--limit N", "Max rows (0 = all)"),
    ("--no-color", "Plain output (also: NO_COLOR=1)"),
    ("--text PATH", "SW1769Bible_both.txt location"),
]

LOOKUP_OPTS = [
    ("-b, --bible <n>", "Bible database (default: 1)"),
    ("-r, --reference", "Include the reference text (default: on)"),
    ("--no-reference", "Omit the reference text"),
    ("-a, --abbrev", "Use abbreviated reference"),
    ("-m, --html", "Accepted, plain output (compat)"),
    ("-j, --no-jesus", "Compat no-op"),
    ("-t, --no-transchange", "Hide […] translation-change markup"),
    ("--brackets", "Compat no-op (markup already bracketed)"),
    ("-p, --no-pilcrows", "Hide ¶ pilcrow characters"),
    ("--no-ps119", "Compat no-op"),
    ("--no-color", "Plain output (also: NO_COLOR=1)"),
    ("--text PATH", "SW1769Bible_both.txt location"),
]


def _opt_block(title: str, opts: list[tuple[str, str]]) -> list[str]:
    width = max(len(flag) for flag, _ in opts)
    return [title] + [f"  {flag.ljust(width)}  {desc}" for flag, desc in opts]


def _two_columns(left: list[str], right: list[str], gap: int = 4) -> list[str]:
    """Lay two text blocks side by side (falls back to stacked if narrow)."""
    try:
        term_w = shutil.get_terminal_size((80, 20)).columns
    except Exception:
        term_w = 80
    left_w = max(len(line) for line in left)
    if left_w + gap + max(len(line) for line in right) > term_w:
        return left + [""] + right
    out = []
    for i in range(max(len(left), len(right))):
        l = left[i] if i < len(left) else ""
        r = right[i] if i < len(right) else ""
        out.append(l.ljust(left_w + gap) + r if r else l)
    return out


def print_help(out=None) -> None:
    out = out or sys.stdout
    out.write(
        "purebible - King James Pure Bible Search from the terminal (Python)\n\n"
        "Usage:\n"
        "  purebible search [options] <phrase>      Search the bible text\n"
        "  purebible lookup [options] <reference>   Look up a passage or whole chapter\n"
        "  purebible bibles                          List available bible databases\n"
        "  purebible tui [query]                     Full-screen vim-keys TUI (btop style)\n"
        "  purebible help                            Show this help\n\n"
        "If no subcommand is given and the argument looks like a passage\n"
        "reference, 'lookup' is assumed; otherwise 'search'.\n"
        "With no args on a TTY, the TUI launches; piped, this help prints.\n\n"
    )
    for line in _two_columns(_opt_block("search options:", SEARCH_OPTS),
                             _opt_block("lookup options:", LOOKUP_OPTS)):
        out.write(line + "\n")
    out.write(
        "\n"
        "query language:  'word word' = phrase · 'a | b' = OR · 'a & b' = AND (same verse)\n"
        "                 'a & -b' = NOT (exclude) · wildcards * ? [seq] per word\n"
        "                 bare '*' = any single word · \\c/\\C = vim case override\n"
        "                 (a leading '-word' needs '--' first)\n\n"
        "Give a chapter reference without a verse (e.g. \"John 3\") to print\n"
        "the whole chapter. Ranges work: \"Rom 12:1-2\", \"Gen 1:1-2:3\".\n\n"
        + EXAMPLES
    )


def _use_color(args) -> bool:
    if getattr(args, "no_color", False):
        return False
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def _fmt_ref(verse, abbreviated: bool, word: int, no_wordindex: bool, color: bool) -> str:
    ref = verse.ref(abbreviated)
    if not no_wordindex and word:
        ref += f" [{word}]"
    if color:
        return f"\x1b[1;36m{ref}\x1b[0m"
    return ref


def _clean(text: str, args) -> str:
    if getattr(args, "no_transchange", False):
        text = re.sub(r"\[([^\]]*)\]", r"\1", text)
    if getattr(args, "no_pilcrows", False):
        text = text.replace("¶", "").strip()
        text = re.sub(r"\s+", " ", text)
    return text


def cmd_search(args, bible: Bible) -> int:
    phrase = " ".join(args.phrase or []).strip()
    if not phrase:
        sys.stderr.write("Empty search phrase\n")
        return 1
    if str(args.bible) != "1":
        sys.stderr.write(f"Note: native Python build ships KJV 1769 only; requested bible {args.bible!r} mapped to 1.\n")
    t0 = time.time()
    books = CANON_GROUPS.get(getattr(args, "canon", None) or "", None)
    hits, nverses = run_search(bible, phrase,
                               case_sensitive=args.case,
                               constrain=getattr(args, "constrain", "verse") or "verse",
                               no_dup=args.no_dup,
                               limit=0,
                               hyphen_sensitive=args.hyphen,
                               books=books)
    dt = (time.time() - t0) * 1000
    if args.count:
        sys.stdout.write(f"{len(hits)}\n")
        sys.stderr.write(f"Found {len(hits)} matches in {nverses} verses ({dt:.0f} ms)\n")
        return 0
    color = _use_color(args)
    rows: list[str] = []
    for h in hits:
        ref = _fmt_ref(h.verse, args.abbrev, h.word, args.no_wordindex, color)
        if args.refs_only:
            rows.append(ref)
        else:
            rows.append(f"{ref} {_clean(h.verse.text, args)}")
    if args.limit and len(rows) > args.limit:
        rows = rows[:args.limit]
    if args.comma and args.refs_only:
        sys.stdout.write(",".join(rows) + "\n")
    else:
        for r in rows:
            sys.stdout.write(r + "\n")
    sys.stderr.write(f"Found {len(hits)} matches in {nverses} verses ({dt:.0f} ms)\n")
    return 0


def cmd_lookup(args, bible: Bible) -> int:
    refstr = " ".join(args.reference or []).strip()
    if not refstr:
        sys.stderr.write("Empty reference\n")
        return 1
    if str(args.bible) != "1":
        sys.stderr.write(f"Note: native Python build ships KJV 1769 only; requested bible {args.bible!r} mapped to 1.\n")
    try:
        verses = resolve_reference(bible, refstr)
    except ValueError as e:
        sys.stderr.write(f"*** ERROR: {e}\n")
        return 1
    if not verses:
        sys.stderr.write(f'*** ERROR: Bible Database doesn\'t contain reference "{refstr}"!\n')
        return 1
    color = _use_color(args)
    for v in verses:
        txt = _clean(v.text, args)
        if args.include_ref:
            name = ABBREV.get(v.book, v.book) if args.abbrev else FULLNAME.get(v.book, v.book)
            ref = f"{name} {v.chapter}:{v.verse}"
            if color:
                sys.stdout.write(f"\x1b[1;36m{ref}\x1b[0m {txt}\n")
            else:
                sys.stdout.write(f"{ref} {txt}\n")
        else:
            sys.stdout.write(f"{txt}\n")
    return 0


def cmd_bibles(args) -> int:
    sys.stdout.write("1 = King James Bible (1769) [native Python]\n")
    return 0


def _load_bible(text_opt) -> Bible:
    try:
        return Bible.load(text_opt)
    except FileNotFoundError as e:
        sys.stderr.write(f"Error: {e}\n")
        sys.exit(1)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if any(a in ("-h", "--help", "help") for a in argv):
        # 'help' subcommand or bare -h/--help anywhere → help
        if argv and argv[0] in ("search", "lookup"):
            print_help()
            return 0
        print_help()
        return 0
    if any(a == "--version" for a in argv):
        sys.stdout.write(f"purebible {__version__} (Python)\n")
        return 0
    if not argv:
        if sys.stdin.isatty() and sys.stdout.isatty():
            from .tui import run_tui
            run_tui(_load_bible(None), "")
            return 0
        print_help()
        return 1

    first = argv[0]
    if first in ("search", "lookup", "bibles", "tui"):
        cmd = first
        rest = argv[1:]
    else:
        # smart dispatch on raw tail (keep flags like -c working)
        smart = " ".join(argv)
        # strip leading flags for the ref test on the payload part
        payload = " ".join(a for a in argv if not a.startswith("-")).strip()
        if payload and looks_like_reference(payload):
            cmd, rest = "lookup", argv
        else:
            cmd, rest = "search", argv

    if cmd == "bibles":
        ap = argparse.ArgumentParser(prog="purebible bibles", add_help=False)
        ap.add_argument("--text", default=None)
        a = ap.parse_args(rest)
        return cmd_bibles(a)
    if cmd == "tui":
        ap = argparse.ArgumentParser(prog="purebible tui", add_help=False)
        ap.add_argument("--text", default=None)
        ap.add_argument("query", nargs=argparse.REMAINDER)
        a = ap.parse_args(rest)
        from .tui import run_tui
        run_tui(_load_bible(a.text), " ".join(a.query))
        return 0
    if cmd == "search":
        ap = argparse.ArgumentParser(prog="purebible search", add_help=False)
        _add_search_opts(ap)
        a = ap.parse_args(rest)
        return cmd_search(a, _load_bible(a.text))
    if cmd == "lookup":
        ap = argparse.ArgumentParser(prog="purebible lookup", add_help=False)
        _add_lookup_opts(ap)
        a = ap.parse_args(rest)
        return cmd_lookup(a, _load_bible(a.text))
    print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
