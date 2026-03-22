"""
main.py - SUP to SRT Convert CLI
Entry point that wires together parser, decoder, OCR and converter.

Usage:
    python main.py <subtitle_file.sup> [options]

Options:
    --output, -o    Output .srt file path (default: same name as input)
    --lang, -l      Tesseract language code (default: eng)
    --workers, -w   Number of parallel worker processes for OCR (default: 1)
    --debug, -d     Print per-module timing and statistics

Multiprocessing:
    OCR is by far the slowest step. With --workers N the subtitle events are split into N chunks
    and processed in parallel via ProcessPoolExcectuer. Each worker runs its own Tesseract instance
    (process-isolated, safe). Results are re-sorted by start_ms before writing.

    Recommended: --workers = number of physical CPU cores.
    On a 4-core machine --workers 4 typically gives 3-4x speedup.
"""
from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from sup_parser import DisplaySet, SupParser
from sup_decoder import decode_display_set
from ocr import ocr_display_set
from sup_converter import (
    SRTEntry, ConversionResult,
    _is_erase_event,
    DEFAULT_DURATION_MS, MIN_DURATION_MS,
    _ms_to_srt_timestamp
)


# -------------------------------------------------------------------------------
# Timing Infrastructure;
# -------------------------------------------------------------------------------
@dataclass
class TimingEntry:
    name: str
    elapsed_s: float


@dataclass
class DebugStats:
    timings: list[TimingEntry] = field(default_factory=list)

    def add(self, name: str, elapsed_s: float) -> None:
        self.timings.append(TimingEntry(name, elapsed_s))

    def print_report(self) -> None:
        print()
        print("=" * 50)
        print("     Timing Report")
        print("=" * 50)

        total = sum(t.elapsed_s for t in self.timings)
        for t in self.timings:
            pct = (t.elapsed_s / total * 100) if total > 0 else 0
            print(f"    {t.name:<25} {t.elapsed_s:7.2f}s ({pct:5.1f}%)")
        print("-" * 50)
        print(f"    {'Total':<25} {total:7.2f}s (100.0%)")
        print("=" * 50)


@contextmanager
def _timer(stats: DebugStats | None, name: str):
    """
    Context manager taht measures elapsed time and stores it in stats.
    """
    start = time.perf_counter()
    yield
    elapsed = time.perf_counter() - start
    if stats is not None: stats.add(name, elapsed)



# -------------------------------------------------------------------------------
# Subtitle Event Extraction (Shared between single and multi-process paths);
# -------------------------------------------------------------------------------
@dataclass
class SubtitleEvent:
    """
    One raw subtitle event before OCR.
    """
    start_ms: float
    end_ms: float | None        # None = use default duration;
    display_set: DisplaySet


def extract_events(
        display_sets: list[DisplaySet],
        default_duration_ms: float = DEFAULT_DURATION_MS,
) -> tuple[list[SubtitleEvent], int]:
    """
    Pair subtitle DisplaySets with their end timestamps.
    Returns (events, erase_count).
    """
    events: list[SubtitleEvent] = []
    erase_count = 0
    pending: tuple[float, DisplaySet] | None = None

    for ds in display_sets:
        if ds.pcs is None: continue

        pts_ms = ds.pcs.pts_ms

        if _is_erase_event(ds):
            if pending is not None:
                events.append(SubtitleEvent(pending[0], pts_ms, pending[1]))
                pending = None
            erase_count += 1
        
        else:
            if pending is not None:
                # No erase between two subtitles - close without explicit end;
                events.append(SubtitleEvent(pending[0], None, pending[1]))
            pending = (pts_ms, ds)

    if pending is not None:
        events.append(SubtitleEvent(pending[0], None, pending[1]))

    return events, erase_count



# -------------------------------------------------------------------------------
# Single-event OCR worker (must be top-level for pickling in multiprocessing);
# -------------------------------------------------------------------------------
def _process_event(args: tuple) -> SRTEntry | None:
    """
    Process a single SubtitleEvent: decode + OCR.
    Top-level function so it can be pickled by ProcessPoolExecutor.

    Returns an SRTEntry or None if OCR produced no text.
    """
    event: SubtitleEvent
    lang: str
    index: int
    min_duration_ms: float
    default_duration_ms: float

    event, lang, index, min_duration_ms, default_duration_ms = args

    decoded = decode_display_set(event.display_set)
    if not decoded: return None

    text = ocr_display_set(decoded, lang=lang)
    if not text: return None

    end_ms = event.end_ms if event.end_ms is not None else event.start_ms + default_duration_ms
    if end_ms - event.start_ms < min_duration_ms:
        end_ms = event.start_ms + min_duration_ms

    return SRTEntry(index=index, start_ms=event.start_ms, end_ms=end_ms, text=text)



# -------------------------------------------------------------------------------
# OCR Phase: Single-process and multi-process;
# -------------------------------------------------------------------------------
def _run_ocr_single(
        events: list[SubtitleEvent],
        lang: str,
        min_duration_ms: float,
        default_duration_ms: float,
        verbose: bool
) -> tuple[list[SRTEntry], int]:
    """
    Sequential OCR processing.
    Returns (entries, skipped_count).
    """
    entries: list[SRTEntry] = []
    skipped = 0
    total = len(events)

    for i, event in enumerate(events, start=1):
        if verbose: print(f"\r  OCR progress: {i}/{total}", end="", flush=True)
        entry = _process_event((event, lang, i, min_duration_ms, default_duration_ms))

        if entry is None: skipped += 1
        else: entries.append(entry)

    if verbose: print()     # Newline after progress;

    return entries, skipped


def _run_ocr_parallel(
    events: list[SubtitleEvent],
    lang: str,
    min_duration_ms: float,
    default_duration_ms: float,
    workers: int,
    verbose: bool
) -> tuple[list[SRTEntry], int]:
    """
    Parallel OCR processing via ProcessPoolExecutor.
    Each worker ia a seperate process with its own Tesseract instance.
    Results are collected unordered and re-sorted by start_ms afterwards.
    """
    entries: list[SRTEntry] = []
    skipped = 0
    total = len(events)
    done = 0

    args_list = [
        (event, lang, i + 1, min_duration_ms, default_duration_ms)
        for i, event in enumerate(events)
    ]

    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_process_event, args): args for args in args_list}

        for future in as_completed(futures):
            done += 1
            if verbose: print(f"\r  OCR progress: {done}/{total} ({workers} workers)", end="", flush=True)

            result = future.result()
            if result is None: skipped += 1
            else: entries.append(result)

        if verbose: print()

        # Restore chronological order (futures complete out-of-order);
        entries.sort(key=lambda e: e.start_ms)

        # Re-index sequentially after sorting
        for i, entry in enumerate(entries, start=1):
            entry.index = i

        return entries, skipped



# -------------------------------------------------------------------------------
# Main Conversion Pipeline;
# -------------------------------------------------------------------------------
def run(
        sup_path: Path,
        srt_path: Path,
        lang: str,
        workers: int,
        default_duration_ms: float,
        min_duration_ms: float,
        debug: bool
) -> None:
    stats = DebugStats() if debug else None

    print(f"Input:      {sup_path}")
    print(f"Output:     {srt_path}")
    print(f"Language:   {lang}")
    if workers > 1: print(f"Workers:    {workers}")
    print()

    # 1. Phase: Parse;
    print("Parsing SUP file...")
    with _timer(stats, "1. Parse SUP"):
        parser = SupParser(sup_path)
        display_sets = parser.parse()
    print(f"    Found {len(display_sets)} display sets.")

    # 2. Phase: Parse;
    with _timer(stats, "2. Extract events"):
        events, erase_count = extract_events(display_sets, default_duration_ms)
        print(f"    Subtitle events : {len(events)}")
        print(f"    Erase events    : {erase_count}")
        print()

    # 3. Phase: Decode + OCR;
    with _timer(stats, "3. Decode + OCR"):
        if workers > 1:
            entries, skipped = _run_ocr_parallel(
                events, lang ,min_duration_ms, default_duration_ms, workers, verbose=True
            )
        
        else:
            entries, skipped = _run_ocr_single(
                events, lang, min_duration_ms, default_duration_ms, verbose=True
            )

    # 4. Phase: Write SRT;
    print("Writing SRT...")
    with _timer(stats, "4. Write SRT"):
        result = ConversionResult(entries=entries, skipped_blank=erase_count, skipped_empty=skipped)
        srt_path.write_text(result.to_srt(), encoding="utf-8")

    # Summary;
    print()
    print("Finished processing.")
    print(f"    Subtitles written   : {result.total}")
    print(f"    Skipped (no OCR)    : {skipped}")
    print(f"    Output              : {srt_path}")

    if stats: stats.print_report()



# -------------------------------------------------------------------------------
# CLI Argument Parsing;
# -------------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sup2srt",
        description="Convert Blu-Ray PGS (.sup) subtitles to SRT using OCR.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
    Examples:
        python main.py movie_subtitle.sup
        python main.py movie_subtitle.sup -o movie_en_srt -l eng
        python main.py movie_subtitle.sup -w 4 --debug
        python main.py movie_subtitle.sup -l eng+deu -w 8 --debug
    """
    )
    p.add_argument(
        "input",
        metavar="FILE.sup",
        help="Input .sup file"
    )
    p.add_argument(
        "-o", "--output",
        metavar="FILE.srt",
        default=None,
        help="Output .srt path (default: same as input with .srt extension)"
    )
    p.add_argument(
        "-l", "--lang",
        metavar="LANG",
        default="eng",
        help="Tesseract language code, e.g. eng, deu, fra, eng+deu (default: eng)"
    )
    p.add_argument(
        "-w", "--workers",
        metavar="N",
        type=int,
        default=1,
        help="Number of parallel OCR worker processes (default: 1)."
             "Use -w 0 to auto-detect CPU count."
    )
    p.add_argument(
        "-d", "--debug",
        action="store_true",
        help="Print per-module timing report after conversion"
    )
    p.add_argument(
        "--duration",
        metavar="MS",
        type=float,
        default=DEFAULT_DURATION_MS,
        help=f"Fallback subtitles duration in ms when no erase event follows "
             f"(default: {DEFAULT_DURATION_MS:.0f})"
    )

    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    sup_path = Path(args.input)
    if not sup_path.exists():
        print(f"Error: File not found '{sup_path}'", file=sys.stderr)
        sys.exit(1)

    if sup_path.suffix.lower() != ".sup":
        print(f"Warning: Input does not have .sup extension '{sup_path}'", file=sys.stderr)

    srt_path = Path(args.output) if args.output else sup_path.with_suffix(".srt")

    workers = args.workers
    if workers == 0:
        import os
        cpu_count = os.cpu_count()
        workers = int(cpu_count / 2) if cpu_count is not None and cpu_count > 1 else 1
        print(f"Auto-detected {workers} CPU cores.")

    run(
        sup_path=sup_path,
        srt_path=srt_path,
        lang=args.lang,
        workers=workers,
        default_duration_ms=args.duration,
        min_duration_ms=MIN_DURATION_MS,
        debug=args.debug
    )


if __name__ == "__main__":
    main()