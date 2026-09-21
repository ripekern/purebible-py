import os
import subprocess
import sys

TEXT = os.path.expanduser("~/Work/purebiblesearch/text/complete/SW1769Bible_both.txt")
ENV = {"PUREBIBLE_TEXT": TEXT, "NO_COLOR": "1", "PATH": os.environ.get("PATH", "")}
PY = [sys.executable, "-m", "purebible"]


def run(*args):
    p = subprocess.run(PY, input=None, capture_output=True, text=True, env={**os.environ, **ENV},
                       cwd="/home/methuselah/Work/purebible-py")
    return p


def test_load():
    from purebible import Bible
    b = Bible.load(TEXT)
    assert len(b) == 31102 + 116 + 14 + 1 + 66 + 1189 + 22 + 29913, len(b)
    assert b.totals()[1] == 823543, b.totals()  # 7^7 words & numbers
    v = b.lookup("John", 3, 16)
    assert v is not None and "God so loved" in v.text
    s = b.lookup("Ps", 4, 0)
    assert s is not None and s.kind == "superscription"
    assert s.ref() == "Psalms 4 Superscription"
    c = b.lookup("Rom", 0, 0)
    assert c is not None and c.kind == "colophon"
    assert c.ref() == "Romans Colophon"
    assert len(b.chapter("Ps", 4)) == 8  # extras excluded from chapters


def test_lookup_cli():
    p = subprocess.run(PY + ["John 3:16"], capture_output=True, text=True,
                       env={**os.environ, **ENV}, cwd="/home/methuselah/Work/purebible-py")
    assert p.returncode == 0, p.stderr
    assert "John 3:16" in p.stdout and "God so loved" in p.stdout


def test_search_phrase():
    p = subprocess.run(PY + ["search", "--refs-only", "--no-dup", "God Jesus"], capture_output=True,
                       text=True, env={**os.environ, **ENV}, cwd="/home/methuselah/Work/purebible-py")
    assert p.returncode == 0, p.stderr
    assert "Found" in p.stderr


def test_or_and():
    from purebible import Bible, search
    b = Bible.load(TEXT)
    h_or, _ = search(b, "God | Jesus")
    h_g, _ = search(b, "God")
    h_j, _ = search(b, "Jesus")
    assert len(h_or) == len(h_g) + len(h_j)
    h_and, nv = search(b, "James* & John*")
    assert nv > 0


def test_refs():
    from purebible import Bible, resolve_reference
    b = Bible.load(TEXT)
    assert len(resolve_reference(b, "John 3")) == 36
    assert len(resolve_reference(b, "Rom 12:1-2")) == 2


def test_canon_groups():
    from purebible import Bible, search, CANON_GROUPS
    b = Bible.load(TEXT)
    h, _ = search(b, "?*", books=CANON_GROUPS["ot"])
    assert len(h) == 634555, len(h)
    h, _ = search(b, "?*", books=CANON_GROUPS["nt"])
    assert len(h) == 188983, len(h)
    h, _ = search(b, "Jesus", books=CANON_GROUPS["gospels"])
    assert len(h) == 617, len(h)
    h, _ = search(b, "Jesus", books=CANON_GROUPS["ot"])
    assert len(h) == 0, len(h)
    h, _ = search(b, "?*", books=CANON_GROUPS["firstlast"])
    assert len(h) == 52280, len(h)
    h, _ = search(b, "In | Amen", books=CANON_GROUPS["firstlast"])
    assert len(h) == 777, len(h)
    h, _ = search(b, "God | Jesus", books=CANON_GROUPS["firstlast"])
    assert len(h) == 343, len(h)
