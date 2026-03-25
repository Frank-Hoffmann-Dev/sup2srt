"""
color_stdout.py - Very simple wrapper for Colorama
Author: Frank Hoffmann
AI Assistent: Anthropic Claude AI - Sonnet 4.6
Date: 24.03.2026
License: MIT
Description: Wraps colorama colors in an easy way.
============================================================
"""
from enum import Enum
from colorama import Fore, Style


class Color(Enum):
    RED = Fore.RED
    GREEN = Fore.GREEN
    YELLOW = Fore.YELLOW


def print_color(text: str, color: Color) -> None:
    print((f"{color.value}{text}{Style.RESET_ALL}"))


def print_green(text: str) -> None:
    print((f"{Color.GREEN.value}{text}{Style.RESET_ALL}"))


def print_red(text: str) -> None:
    print((f"{Color.RED.value}{text}{Style.RESET_ALL}"))


def print_yellow(text: str) -> None:
    print((f"{Color.YELLOW.value}{text}{Style.RESET_ALL}"))