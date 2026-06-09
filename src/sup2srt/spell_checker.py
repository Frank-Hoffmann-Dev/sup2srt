"""
spell_checker.py
Author: Frank Hoffmann
AI Assistent: Anthropic Claude AI - Sonnet 4.6
Date: 09.06.2026
License: MIT
Description: LanguageTool-based spell/grammar correction for SRT entries.
============================================================

Wraps "language_tool_python" to provide optional post-OCR spell correction for SRT subtitle text.
Integrates into the sup2srt pipeline after OCR and before writing the .srt file.

Entries are checked in chunks (reducing processing time) to minimize HTTP round-trips to the local
LanguageTool JVM server. Each chunk is a single concatenated text block. Via match offsets the text
is mapped back to its originating entry without splitting the corrected string.

Supported languages (LanguageTool codes):
    Tesseract -> LanguageTool mapping is handled by TESSERACT_TO_LT.
    Languages absent from this map are unsupported and the correction is silently skipped.

Usage:
    checker = SpellChecker.for_tesseract_lang("deu")    # Returns None if unsupported lang;
    if checker:
        entries, stats = checker.correct_entries(entries)
        checker.close()

    # Or as a context manager;
    with SpellChecker.for_tesseract_lang("eng") as checker:
        if checker:
            entries, stats = checker.correct_entries(entries)

    # Reuse across multiple files (avoids JVM restart per file);
    checker = SpellChecker.for_tesseract_lang("deu")
    for path in files:
        entries, stats = checker.correct_entries(entries)

    checker.close()
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sup2srt.sup_converter import SRTEntry


TESSERACT_TO_LT: dict[str, str] = {
    "eng":      "en-US",
    "deu":      "de-DE",
    "fra":      "fr",
    "spa":      "es",
    "ita":      "it",
    "por":      "pt-PT",
    "nld":      "nl",
    "pol":      "pl",
    "rus":      "ru",
    "ukr":      "uk",
    "swe":      "sv",
    "nor":      "no",
    "dan":      "da",
    "fin":      "fi",
    "ron":      "ro",
    "ces":      "cs",
    "slk":      "sk",
    "hun":      "hu",
    "ell":      "el",
    "tur":      "tr",
    "ara":      "ar",
    "zho":      "zh-CN",
    "chi_sim":  "zh-CN",
    "jpn":      "ja"
}

# SRT inline tags that must not be spell-checked (e.g. <i>, </i>, <b>, {an8});
_TAG_RE = re.compile(r"<[^>]+>|\{[^}]+\}")

# Separator inserted between entries in a chunk;
# Must not be contain any real words - chosen to be invisible to LanguageTool's
# tokenizer (pure punctation / whitespace);
_SEPARATOR = "\n\n"

# Maximum characters per chunk sent to LanguageTool in one request;
# LanguageTool's default server limit is 40_000 chars -> stay well below it;
_CHUNK_MAX_CHARS = 30_000


######################
# Public Data Classes;
######################
@dataclass
class CorrectionStats:
    checked: int = 0
    corrected: int = 0
    total_fixes: int = 0
    chunks: int = 0

    def __str__(self) -> str:
        return (
            f"Spell-Check: {self.checked} entries checked. "
            f"{self.corrected} corrected ({self.total_fixes} fixes total), "
            f"{self.chunks} chunk{'s' if self.chunks != 1 else ''}"
        )


################
# Spell Checker;
################
class SpellChecker:
    """
    Wraps a "language_tool_python.LanguageTool" instance for post-OCR correction of SRTEntry objects.

    Entries are batched into chunks and sent to LanguageTool in one request per chunk.
    Match offsets are used to map each correction back to the correct entry without ever splitting the corrected string.

    Do not instantiate directly! Use "SpellChecker.for_tesseract_lang().
    """
    def __init__(self, lt_lang: str) -> None:
        import language_tool_python

        self._tool = language_tool_python.LanguageTool(lt_lang)
        self._lt_lang = lt_lang
        self._utils = language_tool_python.utils


    ##########
    # Factory;
    ##########
    @classmethod
    def for_tesseract_lang(cls, tesseract_lang: str) -> SpellChecker | None:
        """
        Create a SpellChecker for the given Tesseract language code.

        Handles multi-language codes like "deu+eng" by using only the first mapped language
        as LanguageTool does not support a multi-language mode.

        Returns None if:
            - The language is not supported (not in TESSERACT_TO_LT)
            - language_tool_python is not installed
            - LanguageTool fails to start
        """
        lt_lang = _resolve_lt_lang(tesseract_lang)
        if lt_lang is None: return None

        try: return cls(lt_lang)
        except ImportError: return None
        except Exception: return None


    #############
    # Public API;
    #############
    def correct_entries(self, entries: list[SRTEntry], verbose: bool = False) -> tuple[list[SRTEntry], CorrectionStats]:
        """
        Apply spell/grammar correction to a list of SRTEntry objects in-place.

        Entries are processed in chunks to reduce LanguageTool round-trips.
        Each entry's inline SRT tags (<i>, <b>, {an8}, ...) are stripped before checking and re-inserted afterwards.

        Returns (entries, stats).
        """
        stats = CorrectionStats(checked=len(entries))

        # Pre-strip tags from every entry;
        # Keep originals for tag reinsertion;
        stripped_texts: list[str] = [_TAG_RE.sub("", e.text) for e in entries]

        for chunk_entries, chunk_stripped in _iter_chunks(entries, stripped_texts):
            _correct_chunk(self._tool, self._utils, chunk_entries, chunk_stripped, stats)

        if verbose and stats.checked > 0:
            print(f"    {stats}")

        return entries, stats


    ############
    # Lifecycle;
    ############
    def close(self) -> None:
        """
        Shut down the LanguageTool JVM process.
        """
        try: self._tool.close()
        except Exception: pass

    
    def __enter__(self) -> SpellChecker:
        return self


    def __exit__(self, *_) -> None:
        self.close()


    def __repr__(self) -> str:
        return f"SpellChecker(lang={self._lt_lang})"


###########
# Chunking;
###########
def _iter_chunks(
        entries: list[SRTEntry],
        stripped_texts: list[str]
) -> list[tuple[list[SRTEntry], list[str]]]:
    """
    Yield (chunk_entries, chunk_stripped) pairs.

    Each chunk's combined text stays under _CHUNK_MAX_CHARS.
    A single entry that exceeds the limit is sent as its own chunk.
    """
    sep_len: int = len(_SEPARATOR)
    chunk_entries: list[SRTEntry] = []
    chunk_stripped: list[str] = []
    chunk_chars: int = 0

    for entry, stripped in zip(entries, stripped_texts):
        entry_len = len(stripped) + sep_len

        # Flush current chunk if adding this entry would exceed the limit;
        # Keep at least on entry per chunk;
        if chunk_entries and chunk_chars + entry_len > _CHUNK_MAX_CHARS:
            yield chunk_entries, chunk_stripped
            chunk_entries = []
            chunk_stripped = []
            chunk_chars = 0

        chunk_entries.append(entry)
        chunk_stripped.append(stripped)
        chunk_chars += entry_len

    if chunk_entries:
        yield chunk_entries, chunk_stripped


def _correct_chunk(tool, utils, entries: list[SRTEntry], stripped_texts: list[str], stats: CorrectionStats) -> None:
    """
    Check one chunk of entries against LanguageTool and apply corrections.

    Strategies:
        1. Concatenate stripped entry texts with _SEPARATOR between them.
        2. Record the absolute start offset of each entry in the combined text.
        3. Send the combined text to LanguageTool in one request.
        4. Distribute each Match to the entry whose range [start, end) contains match.offset.
           Adjust the match offset to be relative to that entry.
        5. Apply per-entry corrections independently, then re-insert SRT tags.
    """
    stats.chunks += 1

    # Build combined text and record entry start offsets;
    combined_parts: list[str] = []
    # Absolute char offset of each entry in combined;
    entry_starts: list[int] = []
    pos = 0

    for i, text, in enumerate(stripped_texts):
        if i > 0:
            combined_parts.append(_SEPARATOR)
            pos += len(_SEPARATOR)

        entry_starts.append(pos)
        combined_parts.append(text)
        pos += len(text)

    combined = "".join(combined_parts)

    # Single LanguageTool request for the whole chunk;
    all_matches = tool.check(combined)
    if not all_matches: return

    # Build per-entry exclusive end offset for fast range lookup;
    entry_ends: list[int] = []
    for i, text in enumerate(stripped_texts):
        entry_ends.append(entry_starts[i] + len(text))

    # Distribute matches to entries;
    # A match that falls on the separator (between entries) is discared;
    entry_matches: list[list] = [[] for _ in entries]

    for match in all_matches:
        abs_offset = match.offset

        # Binary-style scan: find the entry whose [start, end) contains abs_offset;
        assigned = False
        for i, (start, end) in enumerate(zip(entry_starts, entry_ends)):
            if start <= abs_offset < end:
                # Clone the match and adjust offset to be entry-relative;
                m = _clone_match_with_offset(match, abs_offset - start)
                entry_matches[i].append(m)
                assigned = True
                break

            # I not assigned, the match landed on a separator - discard it;

        # Apply corrections entry by entry;
        for entry, stripped, matches in zip(entries, stripped_texts, entry_matches):
            if not matches: continue

            corrected_stripped = utils.correct(stripped, matches)
            entry.text = _reinsert_tags(entry.text, stripped, corrected_stripped)
            stats.corrected += 1
            stats.total_fixes += len(matches)


def _clone_match_with_offset(match, new_offset: int):
    """
    Return a shallow copy of *match* with .offset replaced by *new_offset*.
    "language_tool_python" Match objects are not designed to be mutated, so we create a new instance
    via __class__ and copy all __slots__ / __dict__.
    """
    cls = match.__class__
    clone = cls.__new__(cls)

    # Copy all instance state;
    if hasattr(match, "__dict__"):
        clone.__dict__.update(match.__dict__)

    if hasattr(match, "__slots__"):
        for slot in match.__slots__:
            try: setattr(clone, slot, getattr(match, slot))
            except AttributeError: pass

    clone.offset = new_offset

    return clone


def _reinsert_tags(original: str, stripped: str, corrected_stripped: str) -> str:
    """
    Re-insert SRT inline tags from *original* into *corrected_stripped*.

    Tags are placed at proportionally scaled character positions relative to the stripped text length.
    For typical lines (short, few tags) this is accurate. if not tags are present, the corrected stripped
    text is returned directly.
    """
    tags = [(m.start(), m.group()) for m in _TAG_RE.finditer(original)]
    if not tags: return corrected_stripped

    orig_len = len(stripped)
    corr_len = len(corrected_stripped)

    if orig_len == 0: return corrected_stripped

    result = list(corrected_stripped)
    offset = 0

    for orig_pos, tag in tags:
        scaled = int(orig_pos / orig_len * corr_len)
        insert_at = min(scaled + offset, len(result))
        result.insert(insert_at, tag)
        offset += 1

    return "".join(result)


#######################
# Module-level Helpers;
#######################
def _resolve_lt_lang(tesseract_lang: str) -> str | None:
    """
    Map a Tesseract language code (possibly multi e.g. "eng+deu") to a LanguageTool language code.
    The first matching part wins.

    Returns None if no mapping exists.
    """
    parts = tesseract_lang.lower().split("+")
    for part in parts:
        lt = TESSERACT_TO_LT.get(part.strip())
        if lt: return lt

    return None


def is_available() -> bool:
    """
    Return True if "language_tool_python" is installed and importable.
    """
    try:
        import language_tool_python
        return True

    except ImportError:
        return False