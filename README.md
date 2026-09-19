# purebible (Python) — for people who live in nvim, btop & the terminal

Zero-dependency Python remake of the `purebible` terminal client.
Vim keys. btop-style bars. Pipes like a good Unix citizen. No Qt, no build.

```
purebible "God | Jesus"        # OR search
purebible "God Jesus"          # exact phrase
purebible "James* & John*"     # AND in same verse (new: old CLI gave 0 rows)
purebible "John 3:16"          # smart-dispatch → lookup
purebible lookup "Rom 12:1-2"  # ranges + whole chapters ("John 3")
purebible tui "love*"          # fullscreen: j/k, /, :, n/N, y, q
```

## Install

```sh
cd ~/Work/purebible-py
pip install -e . --break-system-packages   # gives `purebible` + short alias `pb`
# or without install:
python3 -m purebible "John 3:16"
```

Needs the KJV text (ships with purebiblesearch, 8.8 MB) — one-time setup:

```sh
mkdir -p ~/.local/share/purebible
ln -s ~/Work/purebiblesearch/text/complete/SW1769Bible_both.txt \
      ~/.local/share/purebible/SW1769Bible_both.txt
# alternatives: export PUREBIBLE_TEXT=/path/to/SW1769Bible_both.txt,
# or copy it to ./data/ or ~/.config/purebible/
```

## Why this exists

`purebible-cli` (C++/Qt) is exact but heavy: CMake, Qt6, daemon
sockets, 30 MB servers. This is the same *terminal client* rewritten in
pure Python for terminal-first users:

- **nvim**: `purebible` prints `ref: text`, one per line — `:r !pb "Rom 12:1-2"`,
  quickfix-friendly, `--refs-only` + `fzf` ready, `--no-color` for pipes.
- **btop**: `purebible tui` is a live dashboard — header stats bar,
  reverse-video statusline, instant ms timings.
- **terminal**: nvim-style NORMAL/INSERT/COMMAND modes (`/` fresh search,
  `i` caret before current query, `a` caret after it, arrows + `C-u`/`C-w`
  editing, `:` for commands, `Esc` drops back to NORMAL and never quits — quitting
  is `q`, `ZZ`/`ZQ` or `:q`, NORMAL mode only), `:` opens a centered
  noice-style popup in the upper third, and the bar stays minimal —
  mode tag + current search only (confirmations flash briefly).
  Jumplist (`Enter` opens a
  chapter, `C-o` jumps back), `↑`/`↓` prompt history, `C-u`/`C-w` line
  editing, even `:w` politely refuses. Lualine-style mode tags and refs
  colored for your system theme (auto-detected,
  `PUREBIBLE_THEME=dark|light` to override), `:help` opens a scrollable
  screen with the key list + every search and lookup pattern, `:clear` to
  clear results, `NO_COLOR` respected, works over SSH with zero deps.

## Query language

| syntax | meaning | example |
|---|---|---|
| `a b` | phrase (consecutive) | `"God said"` → 9 hits |
| `a \| b` | OR (union) | `"God \| Jesus"` |
| `a & b` | AND (same verse) | `"James* & John*"` |
| `a & -b` | NOT (verse must lack `b`) | `"love & -loved"` |
| `love* ?eth [a-z]` | wildcards per word | `"four*"` |
| `\c` / `\C` | vim case override (`\C` sensitive) | `"Amen\C"` → 77 |
| bare `*` | any single word gap | `"God * heaven"` |

Flags mirror the old client: `-c/--case`, `-A/--abbrev`,
`-w/--no-wordindex`, `-d/--no-dup`, `--comma`, `--refs-only`,
`--book/--chapter/--verse` (scope for `&`), plus `--count`, `--limit N`.

## License

Open source. Code is GPL-3.0-or-later (same as the project it was ported
from); the King James Bible text itself is public domain.

## Notes / differences

- Native build ships **KJV 1769 only** (`bibles` → `1`). Asking for another
  id prints a note and maps to 1 (the old Qt backend had 40+).
- Search runs on the whole-Bible word stream like KJPBS (phrases may span
  verse boundaries); `*` inside a phrase is a single-word skip. Hyphenated
  compounds (`Bar-jesus`), ligatures (`Cæsar`→`Caesar`), psalm
  superscriptions and Pauline colophons all match the C++ concordance —
  verified identical counts and word indexes on a battery of queries
  (`Jesus` 973, `God` 4444, `David` 930 deduped, …). `-y` enables
  hyphen-sensitive mode.
- `&` is new (old CLI treated it as a literal word → always 0). `|` and
  phrase/wildcard semantics match the original.
