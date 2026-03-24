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

Enable the environments:
```bash
source .venv/bin/activate
```

Install the package using pip:

```bash
pip install sup2srt
```


## Running sup2srt

In dem Python Environment lässt sich sup2srt verwenden.

Check the basic commands with:
```bash
sup2srt --help
```


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