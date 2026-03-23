"""
sup_converter.py - PGS DisplaySet -> SRT Converter
Author: Frank Hoffmann
AI Assistent: Anthropic Claude AI - Sonnet 4.6
Date: 22.03.2026
License: MIT
Description: Combines parser, decoder and OCR into a complete .SRT file.
============================================================

SRT format:
    <index>
    <start> --> <end>
    <text>
    <blank line>

Timestamp pairing:
PGS does not store an explicit end-time per subtitle. Instead, a "blank" DisplaySet
(PCS with no ODS / empty composition) signals that the previous subtitle should be hidden.
The converter pairs every subtitle-event with the PTS of the following blank event.

Fallback: if no blank event follows (last subtitle, or encoder omitted it), a configurable
default duration is added to the start PTS to produce an end timestamp.

Edge cases handled:
    - Overlapping timestamps (end <= start)     -> minimum duration enforced
    - Empty OCR result                          -> entry skipped
    - Multiple OCR per DisplaySet               -> merged into one SRT block
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from sup2srt.sup_parser import DisplaySet, SupParser
from sup2srt.sup_decoder import decode_display_set
from sup2srt.ocr import ocr_display_set


# -------------------------------------------------------------------------------
# Configuration;
# -------------------------------------------------------------------------------

# Default duration in ms when no explicit end-event is found;
DEFAULT_DURATION_MS: float = 3_000.0

# Minimum duration in ms (prevents end <= start after rounding)
MIN_DURATION_MS: float = 100.0



# -------------------------------------------------------------------------------
# Data Classes;
# -------------------------------------------------------------------------------
@dataclass
class SRTEntry:
    """
    One complete subtitle entry ready for SRT serialization.
    """
    index: int
    start_ms: float
    end_ms: float
    text: str

    def to_srt_block(self) -> str:
        """
        Serialize to an SRT block string (without trailing newline).
        """
        return (
            f"{self.index}\n"
            f"{_ms_to_srt_timestamp(self.start_ms)} --> {_ms_to_srt_timestamp(self.end_ms)}\n"
            f"{self.text}"
        )


@dataclass
class ConversionResult:
    """
    Result of a full SUP -> SRT conversion.
    """
    entries: list[SRTEntry] = field(default_factory=list)
    skipped_blank: int = 0          # DisplaySets with no image (erase events);
    skipped_empty: int = 0          # DisplaySets where OCR returned nothing;
    skipped_overlap:int = 0         # Entries where timing had to be clamped;

    @property
    def total(self) -> int:
        return len(self.entries)

    def to_srt(self) -> str:
        """
        Render the full SRT file as a string.
        """
        return "\n\n".join(e.to_srt_block() for e in self.entries) + "\n"



# -------------------------------------------------------------------------------
# Timestamp Helpers;
# -------------------------------------------------------------------------------
def _ms_to_srt_timestamp(ms: float) -> str:
    """
    Convert milliseconds to SRT timestamp format: HH:MM:SS,mmm
    """
    ms = max(0.0, ms)
    total_ms = int(round(ms))
    millis = total_ms % 1_000
    total_s = total_ms // 1_000
    seconds = total_s % 60
    total_m = total_s // 60
    minutes = total_m % 60
    hours = total_m // 60

    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"



# -------------------------------------------------------------------------------
# Subtitle Event Detection;
# -------------------------------------------------------------------------------
def _is_erase_event(ds: DisplaySet) -> bool:
    """
    Return True if this DisplaySet is a blank/erase event.

    An erase event has a PCS with no CompositionObjects (nothing to display).
    It signals the end of the previous subtitle.
    """
    if ds.pcs is None: return True
    return len(ds.pcs.objects) == 0



# -------------------------------------------------------------------------------
# Core Conversion Logic;
# -------------------------------------------------------------------------------
def convert(
        display_sets: list[DisplaySet],
        lang: str = "eng",
        default_duration_ms: float = DEFAULT_DURATION_MS,
        min_duration_ms: float = MIN_DURATION_MS
) -> ConversionResult:
    """
    Convert a list of DisplaySets into SRT entries.

    :param display_sets:            Parsed DisplaySets from 'SupParser.parse()'
    :param lang:                    Tesseract language code
    :param default_duration_ms:     Duration to use when no erase event follows
    :param min_duration_ms:         Minimum subtitle duration
    :return:                        ConversionResult with all SRTEntry objects
    """
    result = ConversionResult()
    srt_index = 1

    # Build a flat list of (start_ms, end_ms | None, DisplaySet) by pairing
    # each subtitle event with the PTS of the next erase event;
    subtitle_events: list[tuple[float, float | None, DisplaySet]] = []

    pending: tuple[float, DisplaySet] | None = None         # (start_ms, ds)

    for ds in display_sets:
        if ds.pcs is None: continue

        pts_ms = ds.pcs.pts_ms

        if _is_erase_event(ds):
            # This is an end-marker: close the pending subtitle;
            if pending is not None:
                subtitle_events.append((pending[0], pts_ms, pending[1]))
                pending = None
            result.skipped_blank += 1

        else:
            # New subtitle: if there was an unclosed one, close it first;
            # (Encoder omitted the erase event between two subtitles);
            if pending is not None:
                subtitle_events.append((pending[0], None, pending[1]))
            pending = (pts_ms, ds)

    # Handle the last pending subtitle (no erase event at end of file);
    if pending is not None:
        subtitle_events.append((pending[0], None, pending[1]))

    # OCR and build SRT entries;
    for start_ms, end_ms, ds in subtitle_events:
        decoded = decode_display_set(ds)

        if not decoded:
            result.skipped_empty += 1
            continue

        text = ocr_display_set(decoded, lang=lang)

        if not text:
            result.skipped_empty += 1
            continue

        # Determine end timestamp;
        if end_ms is None:
            end_ms = start_ms + default_duration_ms

        # Enforce minimum duration;
        if end_ms - start_ms < min_duration_ms:
            end_ms = start_ms + min_duration_ms
            result.skipped_overlap += 1

        result.entries.append(SRTEntry(
            index=srt_index,
            start_ms=start_ms,
            end_ms=end_ms,
            text=text
        ))
        srt_index += 1

    return result



# -------------------------------------------------------------------------------
# High-level File Conversion;
# -------------------------------------------------------------------------------
def convert_file(
        sup_path: str | Path,
        srt_path: str | Path | None = None,
        lang: str = "eng",
        default_duration_ms: float = DEFAULT_DURATION_MS,
        encoding: str = "utf-8",
        verbose: bool = True,
) -> ConversionResult:
    """
    Convert a .SUP file directly to a .SRT file.

    :param sup_path:            Path to the input .SUP file.
    :param srt_path:            Path to the output .SRT file.
                                Defaults to the same path with .srt extension.
    :param lang:                Tesseract language code.
    :param default_duration_ms: Fallback duration for subtitles without erase event.
    :param encoding:            Output file encoding (default: utf-8).
    :param verbose:             Print progress to stdout.
    :return:                    ConversionResult
    """
    sup_path = Path(sup_path)
    if srt_path is None: srt_path = sup_path.with_suffix(".srt")
    srt_path = Path(srt_path)

    if verbose:
        print(f"Input:      {sup_path}")
        print(f"Output:     {srt_path}")
        print(f"Language:   {lang}")
        print()

    # Parse;
    if verbose: print("Parsing SUP file...")
    parser = SupParser(sup_path)
    display_sets = parser.parse()

    if verbose:
        print(f"Found {len(display_sets)} display sets.")
        print()
        print("Running OCR...")

    # Convert;
    result = convert(display_sets=display_sets, lang=lang, default_duration_ms=default_duration_ms)

    # Write SRT;
    srt_path.write_text(result.to_srt(), encoding=encoding)

    if verbose:
        print()
        print("Process finished.")
        print(f"    Subtitle written    : {result.total}")
        print(f"    Erasure events      : {result.skipped_blank}")
        print(f"    Skipped (no OCR)    : {result.skipped_empty}")
        print(f"    Clamped timings     : {result.skipped_overlap}")
        print(f"    Output              : {srt_path}")

    return result



# -------------------------------------------------------------------------------
# Self Test / Debugging Output;
# -------------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python sup_converter.py <subtitle_file.sup> [output.srt] [lang]")
        print()
        print("     output.srt  : optional, defaults to <subtile_file.srt>")
        print("     lang        : Tesseract language code, default 'eng'")
        print()
        print("Examples: ")
        print("     python sup_converter.py movie.sup")
        print("     python sup_converter.py test_folder/movie_eng.sup eng")
        sys.exit(1)

    sup = sys.argv[1]
    srt = sys.argv[2] if len(sys.argv) > 2 else None
    lang = sys.argv[3] if len(sys.argv) > 3 else "eng"

    convert_file(sup, srt, lang=lang, verbose=True)