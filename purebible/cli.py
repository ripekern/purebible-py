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
from .bible import Bible, ABBREV, FULLNAME, BOOK_ORDER
from .refs import looks_like_reference, parse_reference, resolve_reference
from .search import search as run_search

EXAMPLES = """examples:
  purebible "God | Jesus"                                     (OR — union)
  purebible "God Jesus"                                       (phrase, consecutive)
  purebible "love*"                                           (wildcard)
  purebible "James* & John*"                                  (AND, same verse)
  purebible --verse "Father | Son | Holy Ghost"
  purebible "John 3:16"                                       (smart → lookup)
  purebible lookup "John 3"                                   (whole chapter)
  purebible tui                                               (btop-style TUI)
  purebible search "love" | fzf                               (fzf friendly)
  purebible "Rom 12:1-2"                                      (range lookup)
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
    hits, nverses = run_search(bible, phrase,
                               case_sensitive=args.case,
                               constrain=getattr(args, "constrain", "verse") or "verse",
                               no_dup=args.no_dup,
                               limit=0,
                               hyphen_sensitive=args.hyphen)
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
