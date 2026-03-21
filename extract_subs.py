#!/usr/bin/python
"""
extract_subs.py
Author: Frank Hoffmann
Date: 19.03.2026
Description: Simple script to extract all subtitle tracks from a MKV file.
License: MIT
"""

from enum import Enum
from colorama import Fore, Style
import subprocess
import sys
import json
import os


class Color(Enum):
    RED = Fore.RED
    GREEN = Fore.GREEN
    YELLOW = Fore.YELLOW

def print_color(text: str, color: Color) -> None:
    print(f"{color.value}{text}{Style.RESET_ALL}")


def get_subtitle_tracks(file_path: str) -> list[dict[str, str]]:
    try:
        result = subprocess.run(
            ["mkvmerge", "-J", file_path],
            capture_output=True,
            text=True,
            check=True
        )
    except subprocess.CalledProcessError as e:
        print_color(f"An error occurred during the reading of the tracks: {e}", Color.RED)
        sys.exit(1)

    data = json.loads(result.stdout)

    tracks = []
    for track in data["tracks"]:
        if track["type"] == "subtitles":
            track_id = track["id"]
            props = track.get("properties", {})

            language = props.get("language", "und")
            name = props.get("track_name", "")
            codec = track.get("codec", "unknown")

            tracks.append({
                "id": track_id,
                "language": language,
                "name": name,
                "codec": codec
            })

    return tracks


def extract_subtitles(file_path: str, tracks: list[dict[str, str]]) -> None:
    base_name = os.path.splitext(file_path)[0]
    total = len(tracks)

    for i, track in enumerate(tracks, start=1):
        track_id = track["id"]
        lang = track["language"]
        codec = track["codec"]

        # Determine the suffix;
        if "SubRip" in codec or "SRT" in codec: ext = "srt"
        elif "ASS" in codec: ext = "ass"
        elif "PGS" in codec: ext = "sup"
        else: ext = "sub"

        output_file = f"{base_name}_{lang}_track{track_id}.{ext}"
        print_color(f"[{i}/{total}] Extracting track {track_id} -> {output_file}", Color.GREEN)

        try:
            subprocess.run(
                ["mkvextract", "tracks", file_path, f"{track_id}:{output_file}"],
                check=True
            )
        except subprocess.CalledProcessError:
            print_color(f"An error occurred during processing track: {track_id}", Color.RED)

# Not available!
# 'SubtitleEdit' is a Windows program!
def convert_sup_to_srt(sup_file: str):
    srt_file = os.path.splitext(sup_file)[0] + ".srt"
    print_color(f"Converting {sup_file} -> {srt_file}", Color.GREEN)

    try:
        subprocess.run([
            "SubtitleEdit",
            "/convert",
            sup_file,
            "srt"
        ], check=True)
    except subprocess.CalledProcessError as e:
        print_color(f"An error occurred during converting SUP to SRT {e}", Color.RED)

    return srt_file


def main():
    if len(sys.argv) != 2:
        print_color("Usage: python extract_subs.py <file.mkv>", Color.YELLOW)
        sys.exit(1)

    file_path = sys.argv[1]

    if not os.path.exists(file_path):
        print_color("Failed to find the file!", Color.YELLOW)
        sys.exit(1)

    tracks = get_subtitle_tracks(file_path=file_path)

    if not tracks:
        print_color("Could not detect any subtitles in the file.", Color.YELLOW)
        return

    print("Found subtitles:")
    for track in tracks:
        print(f"    ID {track['id']} | Lang: {track['language']} | Name: {track['name']} | Codec: {track['codec']}")

    print("\n--- Starting extraction ---")
    extract_subtitles(file_path, tracks)
    print_color("\nFinished extraction process!", Color.GREEN)

if __name__ == "__main__":
    main()
