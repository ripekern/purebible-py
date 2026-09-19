"""Native search engine (stdlib only).

Query language (superset of KJPBS notation):

    word word      phrase — consecutive words in order. Like the C++
                   engine, matching runs on the whole-Bible word stream,
                   so a phrase may span a verse/chapter boundary
                   (e.g. "God Jesus" also finds verses ending in "God"
                   whose next verse starts with "Jesus").
    a | b          OR — union of matches
    a & b          AND — same verse must contain both (extension; the C++
                   GUI has no single-string AND, the old CLI treated '&'
                   as a literal word and always returned 0 rows)
    a & -b         NOT — verses matching `a` but not `b` (extension; the
                   old CLI has no exclusion at all). Exclusion is always
                   verse-scoped: `-loved`, `-love*` work; a lone `-b`
                   branch means "every verse without b".
    love*  *eth   wildcards per word: * ? [seq]  (fnmatch style)
    *              bare star = any single word (gap / skip); a query of
                   only stars matches every verse once
    \\c / \\C      vim-style case override: \\c insensitive, \\C sensitive
                   (markers work in TUI and CLI, anywhere in the query)

Flags: case-sensitive, constrain scope for '&' (verse/chapter/book),
dedupe verses.
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field


@dataclass
class Hit:
    verse: object  # Verse
    word: int      # 1-based word index of match start within its verse
    pos: int = field(default=-1, compare=False)  # global stream position (sort key)


def _split_or(query: str) -> list[str]:
    return [p.strip() for p in query.split("|")]


def _split_and(branch: str) -> list[str]:
    # '&' is our AND extension; keep it simple (no quoting/escaping).
    return [p.strip() for p in branch.split("&")]


def _tokens(phrase: str) -> list[str]:
    return phrase.split()


def parse_query(query: str) -> list[list[list[str]]]:
    """Parse to OR[ AND[ phrase-tokens ] ]."""
    ors: list[list[list[str]]] = []
    for branch in _split_or(query):
        ands: list[list[str]] = []
        for grp in _split_and(branch):
            toks = _tokens(grp)
            if toks:
                ands.append(toks)
        if ands:
            ors.append(ands)
    return ors


def _strip_case_markers(ors: list[list[list[str]]]) -> tuple[list[list[list[str]]], bool | None]:
    """Pull vim-style \\c (insensitive) / \\C (sensitive) overrides out.

    Markers work attached (``Amen\\C``) or standalone, anywhere in the
    query; the last one wins (vim). Returns the cleaned query and the
    forced flag (None = no marker). Tokens containing [...] classes are
    left alone so ranges like [a-z] survive.
    """
    forced: bool | None = None
    clean: list[list[list[str]]] = []
    for ands in ors:
        groups: list[list[str]] = []
        for toks in ands:
            kept: list[str] = []
            for t in toks:
                if "[" in t:
                    kept.append(t)
                    continue
                rest = []
                pos = 0
                for m in re.finditer(r"\\[cC]", t):
                    rest.append(t[pos:m.start()])
                    forced = (m.group(0) == "\\C")
                    pos = m.end()
                rest.append(t[pos:])
                t = "".join(rest)
                if t:
                    kept.append(t)
            if kept:
                groups.append(kept)
        if groups:
            clean.append(groups)
    return clean, forced


def _verse_key(v) -> tuple:
    return (v.book, v.chapter, v.verse)


def _book_idx(code: str) -> int:
    from .bible import BOOK_ORDER
    try:
        return BOOK_ORDER.index(code)
    except ValueError:
        return 999


# -- whole-Bible word stream + inverted index (built lazily, cached) ----

def _ensure_stream(bible, case_sensitive: bool, hyphen_sensitive: bool = False):
    """Return (words, postings, pos_verse, pos_word).

    words: global word list (raw or lowered); postings: word->sorted positions;
    pos_verse[i]: Verse starting at/owning position i; pos_word[i]: 1-based
    index within that verse.
    """
    from .bible import tokenize
    attr = ("_stream_cs" if case_sensitive else "_stream_ci") + ("_hy" if hyphen_sensitive else "")
    cached = getattr(bible, attr, None)
    if cached is not None:
        return cached
    words: list[str] = []
    pos_verse: list = []
    pos_word: list[int] = []
    postings: dict[str, list[int]] = {}
    for v in bible.verses:
        if hyphen_sensitive:
            ws = tokenize(v.plain, hyphen_sensitive=True)
            if not case_sensitive:
                ws = [w.lower() for w in ws]
        else:
            ws = v.words if case_sensitive else v.lowered
        for j, w in enumerate(ws):
            p = len(words)
            words.append(w)
            pos_verse.append(v)
            pos_word.append(j + 1)
            postings.setdefault(w, []).append(p)
    cached = (words, postings, pos_verse, pos_word)
    setattr(bible, attr, cached)
    return cached


def _expand(pat: str, postings: dict[str, list[int]], case_sensitive: bool) -> list[int]:
    """Positions where a single (possibly wild) token matches."""
    if pat == "*":
        return []  # handled by caller (matches anything)
    if any(c in pat for c in "*?[]"):
        out: list[int] = []
        for w, lst in postings.items():
            if fnmatch.fnmatchcase(w, pat):
                out.extend(lst)
        out.sort()
        return out
    return postings.get(pat, [])


def _norm_pat(tok: str, case_sensitive: bool, hyphen_sensitive: bool) -> str:
    """Normalize a query token exactly like indexed words (fold, hyphens).

    Hyphens inside [...] character classes are preserved (``[a-z]``).
    """
    from .bible import fold_text
    if tok == "*":
        return tok
    if "[" in tok and not hyphen_sensitive:
        tok = "".join(p if p.startswith("[") else fold_text(p, False)
                      for p in re.split(r"(\[[^\]]*\])", tok))
    else:
        tok = fold_text(tok, hyphen_sensitive)
    return tok if case_sensitive else tok.lower()


def phrase_stream_matches(bible, tokens: list[str], case_sensitive: bool,
                          hyphen_sensitive: bool = False) -> list[tuple[int, object, int]]:
    """Match a token phrase on the global stream.

    Returns [(global_pos, verse, word_idx)] ordered by global_pos.
    """
    if not tokens:
        return []
    toks = [_norm_pat(t, case_sensitive, hyphen_sensitive) for t in tokens]
    words, postings, pos_verse, pos_word = _ensure_stream(bible, case_sensitive, hyphen_sensitive)
    n = len(words)
    m = len(toks)
    if m == 1 and toks[0] == "*":
        # every verse once (avoid 800k rows)
        seen: set[tuple] = set()
        out: list[tuple[int, object, int]] = []
        for i, v in enumerate(pos_verse):
            if pos_word[i] == 1 and _verse_key(v) not in seen:
                seen.add(_verse_key(v))
                out.append((i, v, 1))
        return out
    if all(t == "*" for t in toks):
        # all-gap pattern: every stream window matches; report per verse
        seen2: set[tuple] = set()
        out2: list[tuple[int, object, int]] = []
        for i in range(0, n - m + 1):
            v = pos_verse[i]
            if _verse_key(v) not in seen2:
                seen2.add(_verse_key(v))
                out2.append((i, v, pos_word[i]))
        return out2
    # anchor on first concrete token
    try:
        anchor = next(i for i, t in enumerate(toks) if t != "*")
    except StopIteration:
        return []
    cands = _expand(toks[anchor], postings, True)
    res: list[tuple[int, object, int]] = []
    for p in cands:
        start = p - anchor
        if start < 0 or start + m > n:
            continue
        ok = True
        for j, pat in enumerate(toks):
            if pat == "*":
                continue
            w = words[start + j]
            if any(c in pat for c in "*?[]"):
                if not fnmatch.fnmatchcase(w, pat):
                    ok = False
                    break
            elif w != pat:
                ok = False
                break
        if ok:
            res.append((start, pos_verse[start], pos_word[start]))
    res.sort(key=lambda r: r[0])
    return res


def search(
    bible,
    query: str,
    case_sensitive: bool = False,
    constrain: str = "verse",   # verse | chapter | book  (scope for '&')
    no_dup: bool = False,
    limit: int = 0,
    hyphen_sensitive: bool = False,
) -> tuple[list[Hit], int]:
    """Run query. Returns (hits, verses_matched).

    Vim-style \\c / \\C anywhere in the query override case_sensitive.
    """
    ors, forced = _strip_case_markers(parse_query(query))
    if forced is not None:
        case_sensitive = forced
    if not ors:
        return [], 0

    def scope(verse) -> tuple:
        if constrain == "book":
            return (verse.book,)
        if constrain == "chapter":
            return (verse.book, verse.chapter)
        return (verse.book, verse.chapter, verse.verse)

    branch_hits: list[list[Hit]] = []
    for ands in ors:
        # split positive groups from -exclusions (branch scope, verse level)
        pos_groups: list[list[str]] = []
        neg_pats: list[str] = []
        for toks in ands:
            pos = [t for t in toks if not (t.startswith("-") and len(t) > 1)]
            neg_pats.extend(t[1:] for t in toks
                            if t.startswith("-") and len(t) > 1)
            if pos:
                pos_groups.append(pos)
        excluded: set[tuple] = set()
        for pat in neg_pats:
            for _pos, v, _w in phrase_stream_matches(bible, [pat], case_sensitive,
                                                     hyphen_sensitive):
                excluded.add(_verse_key(v))
        if not pos_groups:
            # all-negative branch: every verse except the excluded ones
            branch_hits.append([Hit(v, 1, -1) for v in bible.verses
                                if _verse_key(v) not in excluded])
            continue
        if len(pos_groups) == 1:
            ms = phrase_stream_matches(bible, pos_groups[0], case_sensitive, hyphen_sensitive)
            if no_dup:
                seen: set[tuple] = set()
                hh: list[Hit] = []
                for pos, v, w in ms:
                    if _verse_key(v) not in seen:
                        seen.add(_verse_key(v))
                        hh.append(Hit(v, w, pos))
            else:
                hh = [Hit(v, w, pos) for pos, v, w in ms]
            if excluded:
                hh = [h for h in hh if _verse_key(h.verse) not in excluded]
            branch_hits.append(hh)
        else:
            per_group = [phrase_stream_matches(bible, toks, case_sensitive, hyphen_sensitive)
                         for toks in pos_groups]
            if constrain == "verse":
                sets = [{_verse_key(v) for _, v, _ in g} for g in per_group]
                common = set.intersection(*sets) if sets else set()
                first = { _verse_key(v): (pos, w) for pos, v, w in per_group[0] }
                hh2 = [Hit(bible._index[k], first[k][1], first[k][0])
                       for k in common if k in bible._index]
                hh2.sort(key=lambda h: h.pos)
                if no_dup:
                    seen3: set[tuple] = set()
                    hh2 = [h for h in hh2 if not (_verse_key(h.verse) in seen3 or seen3.add(_verse_key(h.verse)))]
            else:
                sets_s = [{scope(v) for _, v, _ in g} for g in per_group]
                common_s = set.intersection(*sets_s) if sets_s else set()
                # fan out: every verse inside a matching scope
                hh2 = [Hit(v, 1, -1) for v in bible.verses if scope(v) in common_s]
                hh2.sort(key=lambda h: (_book_idx(h.verse.book), h.verse.chapter, h.verse.verse))
            if excluded:
                hh2 = [h for h in hh2 if _verse_key(h.verse) not in excluded]
            branch_hits.append(hh2)

    # OR = union. With no_dup collapse to one row per verse.
    if no_dup:
        best: dict[tuple, Hit] = {}
        for hh in branch_hits:
            for h in hh:
                k = _verse_key(h.verse)
                if k not in best or h.pos < best[k].pos:
                    best[k] = h
        hits = sorted(best.values(),
                      key=lambda h: (h.pos if h.pos >= 0
                                     else (_book_idx(h.verse.book), h.verse.chapter, h.verse.verse)))
    else:
        seen_mk: set[tuple] = set()
        hits = []
        for hh in branch_hits:
            for h in hh:
                mk = (_verse_key(h.verse), h.word, h.pos)
                if mk not in seen_mk:
                    seen_mk.add(mk)
                    hits.append(h)
        hits.sort(key=lambda h: h.pos)
    if limit and len(hits) > limit:
        hits = hits[:limit]
    verses_matched = len({_verse_key(h.verse) for h in hits})
    return hits, verses_matched
