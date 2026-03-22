"""
main.py - SUP to SRT Convert CLI
Author: Frank Hoffmann
AI Assistent: Anthropic Claude AI - Sonnet 4.6
Date: 22.03.2026
License: MIT
Description: Entry point that wires together parser, decoder, OCR and converter.
============================================================

Single file mode:
    python main.py <subtitle_file.sup> [options]

Batch mode:
    python main.py --batch /path/to/sup_folder [options]

Options:
    --output,       -o      Output .srt file path (default: same name as input)
    --output-ir,    -O      Output folder for batch mode (created if missing)
    --batch,        -b      Input folder: convert all .sup files inside
    --lang,         -l      Tesseract language code (default: eng)
    --workers,      -w      Number of parallel worker processes for OCR (default: 1)
    --debug,        -d      Print per-module timing and statistics
    --overwrite             Overwrite existing .srt files in batch mode (default: skip)

Batch mode notes:
    - All .sup files in the given folder are converted (non-recursive).
    - Ouput .srt files keep the same stem: movie.sup -> movie.srt
    - --output-dir sets the destination folder (default: <input_folder>/srt)
    - Files that already exist are skipped unless --overwrite is set.
    - A summary table is printed after all files are processed.
    - If a file fails, the error is logged and processing continues.

Multiprocessing:
    OCR is by far the slowest step. With --workers N the subtitle events are split into N chunks
    and processed in parallel via ProcessPoolExcectuer. Each worker runs its own Tesseract instance
    (process-isolated, safe). Results are re-sorted by start_ms before writing.

    Recommended: --workers = number of physical CPU cores.
    On a 4-core machine --workers 4 typically gives 3-4x speedup.
    With --workers 0 the number of logical cores is automatically detected and divided by 2 (physical cores). 
"""
from __future__ import annotations

import argparse
import re
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
# Language Detection from Filename;
# -------------------------------------------------------------------------------

# Mapping of common filename language tags -> Tesseract language codes.
# Key are lowercase, matched against underscore/dot/hyphen-seperated tokens in the
# file stem (e.g. "movie_en.sup" -> token "en" -> "eng").
# Add custom entries here as needed.
LANG_MAP: dict[str, str] = {
    # ISO 639-1 (2-letter)
    "en":  "eng",  "de":  "deu",  "fr":  "fra",  "es":  "spa",
    "it":  "ita",  "pt":  "por",  "nl":  "nld",  "pl":  "pol",
    "cs":  "ces",  "sk":  "slk",  "hu":  "hun",  "ro":  "ron",
    "sv":  "swe",  "no":  "nor",  "da":  "dan",  "fi":  "fin",
    "ru":  "rus",  "uk":  "ukr",  "tr":  "tur",  "el":  "ell",
    "ja":  "jpn",  "zh":  "chi_sim", "ko": "kor", "ar": "ara",
    "he":  "heb",
    # ISO 639-2/B and common abbreviations (3-letter)
    "eng": "eng",  "ger": "deu",  "deu": "deu",  "fre": "fra",
    "fra": "fra",  "spa": "spa",  "ita": "ita",  "por": "por",
    "dut": "nld",  "nld": "nld",  "pol": "pol",  "cze": "ces",
    "ces": "ces",  "slo": "slk",  "slk": "slk",  "hun": "hun",
    "rum": "ron",  "ron": "ron",  "swe": "swe",  "nor": "nor",
    "dan": "dan",  "fin": "fin",  "rus": "rus",  "ukr": "ukr",
    "tur": "tur",  "gre": "ell",  "ell": "ell",  "jpn": "jpn",
    "chi": "chi_sim", "kor": "kor", "ara": "ara", "heb": "heb"
}

def detect_language(stem: str, fallback: str) -> tuple[str, bool]:
    """
    Try to detect the Tesseract language code from a file stem.

    Strategy: Split the stem on underscore, dots and hyphes, then check each
    token from right to left against LANG_MAP. The rightmost matching token
    wins (e.g. "movie_en" -> "en" -> "eng").

    Returns (lang_code, was_detected).
    detected=False means not token matched and the fallback was used.

    Examples:
        "movie_en"          -> ("eng", True)
        "movie_ger"         -> ("deu", True)
        "film_subtitle_fra" -> ("fra", True)
        "movie"             -> (fallback, False)
        "movie_und"         -> (fallback, False)    # Unknown tag;
    """
    tokens = re.split(r"[_.\-]", stem)
    for token in reversed(tokens):
        code = LANG_MAP.get(token.lower())
        if code: return code, True

    return fallback, False



# -------------------------------------------------------------------------------
# Batch Processing;
# -------------------------------------------------------------------------------
@dataclass
class BatchResult:
    """
    Result summary for one file in a batch run.
    """
    sup_path: Path
    srt_path: Path
    success: bool
    lang: str = ""
    subtitles: int = 0
    skipped: int = 0
    elapsed_s: float = 0.0
    error: str = ""


def run_batch(
        input_dir: Path,
        output_dir: Path,
        lang: str,
        workers: int,
        default_duration_ms: float,
        debug: bool,
        overwrite: bool
) -> None:
    """
    Convert all .SUP files in input_dir to .SRT files in output_dir.

    Files are processed sequentially (one after another). The --workers flag
    still controls parallism within each file's OCR phase.
    """
    sup_files = sorted(input_dir.glob("*.sup"))

    if not sup_files:
        print(f"No .sup files found in '{input_dir}'")
        sys.exit(0)

    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Batch mode")
    print(f"    Input folder    : {input_dir}")
    print(f"    Output folder   : {output_dir}")
    print(f"    Files found     : {len(sup_files)}")
    print(f"    Language        : {lang}")
    if workers > 1: print(f"    Workers         : {workers}")
    print()

    batch_results: list[BatchResult] = []
    batch_start = time.perf_counter()

    for file_index, sup_path in enumerate(sup_files, start=1):
        srt_path = output_dir / sup_path.with_suffix(".srt").name

        # Detect language from filename;
        # Fallback back to --lang if not found;
        file_lang, lang_detected = detect_language(sup_path.stem, fallback=lang)
        lang_note = "(detected from filename)" if lang_detected else "(fallback)"

        print(f"[{file_index}/{len(sup_files)}] {sup_path.name} - lang: {file_lang} {lang_note}")

        # Skip existing files unless --overwrite flag is set;
        if srt_path.exists() and not overwrite:
            print(f"    Skipping - output already exists: {srt_path.name}")
            print(f"    (use --overwrite to force reconversion)")
            batch_results.append(BatchResult(
                sup_path=sup_path,
                srt_path=srt_path,
                success=True,
                error="skipped (already exists)"
            ))
            print()
            continue

        file_start = time.perf_counter()
        try:
            run(
                sup_path=sup_path,
                srt_path=srt_path,
                lang=file_lang,
                workers=workers,
                default_duration_ms=default_duration_ms,
                min_duration_ms=MIN_DURATION_MS,
                debug=debug
            )
            elapsed = time.perf_counter() - file_start

            # Read subtitle count from the written file to report it;
            srt_text = srt_path.read_text(encoding="utf-8")
            subtitle_count = srt_text.count("\n\n") + (1 if srt_text.strip() else 0)

            batch_results.append(BatchResult(
                sup_path=sup_path,
                srt_path=srt_path,
                success=True,
                lang=file_lang,
                subtitles=subtitle_count,
                elapsed_s=elapsed
            ))

        except Exception as e:
            elapsed = time.perf_counter() - file_start
            error_msg = f"{type(e).__name__}: {e}"
            print(f"    Error: {error_msg}", file=sys.stderr)
            batch_results.append(BatchResult(
                sup_path=sup_path,
                srt_path=srt_path,
                success=False,
                lang=file_lang,
                elapsed_s=elapsed,
                error=error_msg
            ))

        print()

    # Batch summary table;
    batch_elapsed = time.perf_counter() - batch_start
    _print_batch_summary(batch_results, batch_elapsed)


def _print_batch_summary(results: list[BatchResult], total_elapsed_s: float) -> None:
    """
    Print a formatted summary table for the batch run.
    """
    col_w = max((len(r.sup_path.name) for r in results), default=20)
    col_w = max(col_w, 20)

    header = f"    {'File':<{col_w}} {'Status':<8} {'Subtitles':>10} {'Time':>8}"
    header_length = len(header)

    print("=" * header_length)
    print("    Batch Summary")
    print("=" * header_length)
    print(header)
    print("-" * header_length)

    succeeded = 0
    failed = 0
    skipped = 0

    for r in results:
        if r.error == "skipped (already exists)":
            status = "SKIPPED"
            subs = "-"
            t = "-"
            skipped += 1
        
        elif r.success:
            status = "OK"
            subs = str(r.subtitles)
            t = f"{r.elapsed_s:.1f}s"
            succeeded += 1

        else:
            status = "FAILED"
            subs = "-"
            t = f"{r.elapsed_s:.1f}s"
            failed += 1

        print(f"    {r.sup_path.name:<{col_w}} {status:<8} {subs:>10} {t:>8}")
        if not r.success and r.error and r.error != "skipped (already exists)":
            print(f"    {'':>{col_w}} {r.error}")

    print("-" * header_length)
    print(f"    {succeeded} succeeded, {failed} failed, {skipped} skipped - "
          f"Total: {total_elapsed_s:.1f}s")
    print("=" * header_length)



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
    # Single file
    python main.py movie_subtitle.sup
    python main.py movie_subtitle.sup -o movie_en_srt -l eng
    python main.py movie_subtitle.sup -w 0 --debug
    python main.py movie_subtitle.sup -l eng+deu -w 8 --debug

    # Batch mode
    python main.py --batch /path/to/movie_subtitles/
    python main.py --batch /path/to/movie_subtitles/ -O /home/user/abc/output/srt/ -l eng -w 0
    python main.py --batch /path/to/movie_subtitles/ -l deu --overwrite --debug
    """
    )
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "input",
        metavar="FILE.sup",
        nargs="?",
        help="Input .sup file (single file mode)"
    )
    mode.add_argument(
        "-b", "--batch",
        metavar="FOLDER",
        help="Input folder containing .sup files (batch mode)"
    )

    p.add_argument(
        "-o", "--output",
        metavar="FILE.srt",
        default=None,
        help="Output .srt path (default: same as input with .srt extension)"
    )
    p.add_argument(
        "-O", "--output-dir",
        metavar="FOLDER",
        default=None,
        help="Output folder for batch mode (default: <input_folder>/srt/)"
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
    p.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing .srt files in batch mode (default: skip)"
    )

    return p


def main() -> None:
    import os
    parser = build_parser()
    args = parser.parse_args()

    # Resolve worker count;
    workers = args.workers
    if workers == 0:
        cpu_count = os.cpu_count()
        workers = int(cpu_count / 2) if cpu_count is not None and cpu_count > 1 else 1
        print(f"Auto-detected {workers} CPU cores.")

    # Batch mode;
    if args.batch:
        input_dir = Path(args.batch)
        if not input_dir.is_dir():
            print(f"Error: Target is not a directory '{input_dir}'", file=sys.stderr)
            sys.exit(1)

        output_dir = Path(args.output_dir) if args.output_dir else input_dir / "srt"

        run_batch(
            input_dir=input_dir,
            output_dir=output_dir,
            lang=args.lang,
            workers=workers,
            default_duration_ms=args.duration,
            debug=args.debug,
            overwrite=args.overwrite
        )
        return

    # Single file mode;
    sup_path = Path(args.input)
    if not sup_path.exists():
        print(f"Error: File not found '{sup_path}'", file=sys.stderr)
        sys.exit(1)

    if sup_path.suffix.lower() != ".sup":
        print(f"Warning: Input does not have .sup extension '{sup_path}'", file=sys.stderr)

    srt_path = Path(args.output) if args.output else sup_path.with_suffix(".srt")

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