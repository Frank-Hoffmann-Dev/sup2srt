## Prerequisites

Before installing sup2srt, install Tesseract OCR on your machine:

**Github Repo**: [tesseract-ocr](https://github.com/tesseract-ocr/tesseract)


You will need the Tesseract engine and the specific language packs that you want to use. 

For example on an Arch Linux machine:

**Arch Linux**
```bash
sudo pacman -S tesseract tesseract-data-eng tesseract-data-deu 
```

If you're using a different Linux distribution or operating system, check your distribution's documentation.


## Installation

The best approach is to install it globally via pipx:
```bash
pipx install sup2srt
```

Alternatively you can install it in a Python environment:
```bash
python -m venv .venv
```

Enable the environment:
```bash
source .venv/bin/activate
```

Install the package using pip:
```bash
pip install sup2srt
```


## Compiling from Source Code

If you want to "compile" the tool from the source code/ build a package:
```bash
# Create a new folder and clone the repository;
git clone "https://github.com/Frank-Hoffmann-Dev/sup2srt.git"

# Change directory;
cd sup2srt

# Build the package;
python -m build
```

The packages are now in the *dist* directory and can be installed using **pip/pipx**:
```bash
# Version number may vary;
pipx install dist/sup2srt-0.1.1-py3-none-any.whl  
```


## Running sup2srt

Check the basic commands with:
```bash
sup2srt --help
```

Convert a .SUP file (Tesseract OCR with English language):
```bash
sup2srt movie_subtitles.sup --lang eng
```

Here are a few different options:
```bash
# With multiple threads;
# The number of threads is automatically determined by the number of CPU cores;
# Alternatively you can specify a number of threads;
sup2srt movie_subtitles.sup -w 0

# With two different languages;
sup2srt movie_subtitles.sup -w 0 --lang "eng+deu"

# Provides additional information, such as statistics on processing time;
sup2srt movie_subtitles.sup -w 0 --lang eng --debug
```

Using batch mode, you can convert multiple .SUP files all at once:
```bash
sup2srt --batch /path/to/directory

# You can use the '-O' option to specify the output directory;
sup2srt --batch /path/to/directory -O /home/user/abc/output/srt -l eng -w 0
```

Batch mode attempts to determine the languages for OCR based on the filename.
The '-l' flag can be used to specify fallback languages if the filename does not contain any language information.
If no fallback language is specified, 'eng' is used by default.
```bash
# Examples for .SUP files;
watchmen_eng.sup
dune_ger.sup
```


## OCR

This tool uses **Tesseract OCR**.

Subtitles typically consist of white text on a transparent background.
However, OCR works best with black text on a white background.

The images extracted from the .SUP files are preprocessed to achieve the best possible results from the OCR engine, but errors in character recognition still occur.
Based on my tests, the subtitles are readable and understandable, but they are not perfect.

**Tesseract OCR** can be configured, and it is quite possible that the results can be further optimized.

The settings are located in the **ocr.py** module and can be modified as needed.

In general, all modules can be used independently, as they contain self-tests (see the source code).


## License

MIT License
```
Copyright 2026 Frank Hoffmann

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the “Software”), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```

## About

I developed this tool primarily for my own use.
As a Linux user, I couldn’t find a satisfying solution for converting SUP files to SRT files.

While there are tools available for Windows, (and these can likely be run on Linux using Wine) I wanted a native solution.

Since I couldn’t find a PIP package for this (maybe there is one after all??), I decided to create one myself.


## Thanks

Many thanks to all the developers of the libraries used and to the community for creating so many incredibly useful tools.


## AI Usage

I used the AI assistant **Anthropic Claude AI - Sonnet 4.6** to create this tool.

As a computer scientist, I have reviewed and approved every single line of code, and I understand the tool’s internal processes and how it works.
I didn’t just copy and paste the code from the AI.
Instead, I wrote it by hand, line by line, making changes whenever I deemed it necessary.

Nevertheless, there may still be errors or poor design choices.
Everyone is free to examine, modify, improve, fork the code or call it AI slop :D