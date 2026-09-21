"""purebible — Python terminal client for the King James Pure Bible Search.

Zero-dependency, for people who live in nvim / btop / tmux.
"""
from .bible import Bible, Verse, find_text_file, CANON_GROUPS
from .search import search, parse_query
from .refs import parse_reference, resolve_reference

__all__ = ["Bible", "Verse", "find_text_file", "CANON_GROUPS", "search",
           "parse_query", "parse_reference", "resolve_reference"]
__version__ = "0.1.5"
