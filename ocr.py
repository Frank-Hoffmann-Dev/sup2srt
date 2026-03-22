"""
ocr.py OCR for PGS Subtitle Images
Takes DecodedImage objects from 'sup_decoder.py' and returns recognized text.

Pipline per image:
    1. Preprocess: RGBA -> grayscale, upscale, contrast, threshold
    2. Tesseract OCR -> raw text
    3. Postprocess: clean up whitespace and common OCR mistakes

Tesseract Config:
    - PSM 6: Assume a uniform block of text (best for subtitles)
    - OEM 3: Default engine (LSTM + legacy)

Dependencies:
    - pip install pytesseract pillow numpy
    - apt install tesseract-ocr tesseract-ocr-<lang>
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

import numpy as np
import pytesseract
from PIL import Image, ImageFilter, ImageOps

from sup_decoder import DecodedImage


# -------------------------------------------------------------------------------
# Configuration;
# -------------------------------------------------------------------------------

# Tesseract page segmentation modes relevant for subtitles:
#   PSM_BLOCK   = 6
#   PSM_LINE    = 7
PSM_BLOCK   = 6
PSM_LINE    = 7

# Scale factor applied before OCR.
# Tesseract works best with characters heights of ~30-50px.
# Subtitle bitmaps are often only 20-30px tall -> upscaling helps therefore a lot.
UPSCALE_FACTOR = 2.0

# Binarization threshold (0-255).
# Pixels brighter than this become white (text), darker becomes black (bg).
# Subtitles are almost always light text on dark/transparent background.
BINARY_THRESHOLD = 80



# -------------------------------------------------------------------------------
# Data Classes;
# -------------------------------------------------------------------------------
@dataclass
class OCRResult:
    """
    OCR result for a single DecodedImage.
    """
    text: str           # Cleaned recognized text;
    raw_text: str       # Raw Tesseract output (for debugging);
    pts_ms: float       # Start timestamp in ms (from DecodedImage);
    object_id: int



# -------------------------------------------------------------------------------
# Image Presprocessing;
# -------------------------------------------------------------------------------
def _rgba_to_binary(image: Image.Image) -> Image.Image:
    """
    Convert an RGBA subtitle image to a high-contrast black-on-white binary image suitable for Tesseract.

    Steps:
        1. Composite onto black background (flatten alpha correctly)
        2. Convert to greyscale
        3. Invert (subtitle text is bright -> make it dark for Tesseract)
        4. Apply threshold -> pure black/white
    """
    # 1. Flatten alpha onto black background;
    bg = Image.new("RGBA", image.size, (0, 0, 0, 255))
    bg.paste(image, mask=image.split()[3])      # Alpha channel as mask;
    gray = bg.convert("L")                      # L = greyscale;

    # 2. Invert: bright subtitle text becomes dark;
    gray = ImageOps.invert(gray)

    # 3. Binarize;
    binary = gray.point(lambda px: 0 if px < BINARY_THRESHOLD else 255, "L")

    return binary


def preprocess(image: Image.Image, upscale: float = UPSCALE_FACTOR) -> Image.Image:
    """
    Full preprocessing pipeline for a subtitle RGBA image.

    Returns a binary (black text on white background) PIL image, upscaled and sharpened, ready for Tesseract.
    """
    binary = _rgba_to_binary(image)

    # Upscale using LANCZOS for best quality with text;
    if upscale != 1.0:
        new_w = int(binary.width * upscale)
        new_h = int(binary.height * upscale)
        binary = binary.resize((new_w, new_h), Image.Resampling.LANCZOS)

    binary = binary.filter(ImageFilter.SHARPEN)

    return binary



# -------------------------------------------------------------------------------
# OCR;
# -------------------------------------------------------------------------------
def _build_tesseract_config(lang: str, psm: int) -> str:
    """
    Build the Tesseract config string.
    """
    return f"--oem 3 --psm {psm} -l {lang}"


def run_ocr(image: Image.Image, lang: str = "eng", psm: int = PSM_BLOCK) -> str:
    """
    Run Tesseract OCR on a preprocessed image.

    :param image: Preprocessed binary PIL image (black text on white bg)
    :param lang:  Tesseract language code (e.g. 'deu', 'eng', 'fra')
    :param psm:   Page segmentation mode (6 = block, 7 = single line)
    :return:      Raw Tesseract output string
    """
    config = _build_tesseract_config(lang, psm)
    return pytesseract.image_to_string(image, config=config)



# -------------------------------------------------------------------------------
# Postprocessing;
# -------------------------------------------------------------------------------

# Common Tesseract substitution errors for subtitle text;
_OCR_SUBSTITUTIONS: list[tuple[re.Pattern, str]] = [
    # Stray pipe/vertical bar that should be 'I' or 'l';
    (re.compile(r"(?<=[a-z])\|(?=[a-z])"), "l"),
    (re.compile(r"\|"),                    "I"),

    # Stray backtick or grave accent;
    (re.compile(r"`"),                     "'"),

    # Double space to single;
    (re.compile(r" {2,}"),                 " "),

    # Trailing/leading whitespace per line;
    (re.compile(r"[ \t]+$", re.MULTILINE), ""),
    (re.compile(r"^[ \t]+", re.MULTILINE), ""),
]


def _normalize_unicode(text: str) -> str:
    """
    Normalize unicode to NFC (composed form) and strip control characters.
    """
    text = unicodedata.normalize("NFC", text)

    # Remove control characters expect newline and tab;
    text = "".join(c for c in text if unicodedata.category(c) != "Cc" or c in "\n\t")

    return text


def postprocess(raw: str) -> str:
    """
    Clean up raw Tesseract output.

    - Normalize unicode
    - Apply common substitute fixes
    - Collapse multiple blank lines into one
    - Strip leading/trailing whitespace
    """
    text = _normalize_unicode(raw)

    for pattern, replacement in _OCR_SUBSTITUTIONS:
        text = pattern.sub(replacement, text)

    # Collapse 3+ consecutive newlines to max 2;
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()



# -------------------------------------------------------------------------------
# Main Entry Point;
# -------------------------------------------------------------------------------
def ocr_image(
        decoded: DecodedImage,
        lang: str = "eng",
        psm: int = PSM_BLOCK,
        upscale: float = UPSCALE_FACTOR
) -> OCRResult:
    """
    Full OCR pipeline for a single DecodedImage.

    :param decoded:     DecodedImage from 'sup_decoder.decode_display_set()'
    :param lang:        Tesseract language string (e.g. 'deu', 'eng', 'deu+eng')
    :param psm:         Tesseract PSM mode
    :param upscale:     Upscale factor applied before OCR
    :return:            OCRResult with cleaned text and metadata
    """
    preprocessed = preprocess(decoded.image, upscale=upscale)
    raw = run_ocr(preprocessed, lang=lang, psm=psm)
    text = postprocess(raw)

    return OCRResult(
        text=text,
        raw_text=raw,
        pts_ms=decoded.pts_ms,
        object_id=decoded.object_id
    )


def ocr_display_set(
        decoded_images: list[DecodedImage],
        lang: str = "eng",
        psm: int = PSM_BLOCK,
        upscale: float = UPSCALE_FACTOR
) -> str:
    """
    OCR all DecodeImages of one DisplaySet and merge the results.

    Multiple images per DisplaySet (e.g. two seperate subtitle windows)
    are sorted by vertical position (y) and joined with a newline.

    :return: Combined cleaned text for the entire DisplaySet.
    """
    if not decoded_images: return ""

    # Sort by vertical position so top image comes first;
    sorted_images = sorted(decoded_images, key=lambda d: d.y)

    parts = []
    for decoded in sorted_images:
        result = ocr_image(decoded, lang=lang, psm=psm, upscale=upscale)
        if result.text:
            parts.append(result.text)

    return "\n".join(parts)



# -------------------------------------------------------------------------------
# Self Test / Debugging Output;
# -------------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    from sup_parser import SupParser, ticks_to_timestamp
    from sup_decoder import decode_display_set

    if len(sys.argv) < 2:
        print("Usage: python ocr.py <subtitle_file.sup> [lang]")
        print("     lang: Tesseract language code, default 'eng'")
        print("     e.g.: python ocr.py movie.sup eng")
        sys.exit(1)

    sup_path = sys.argv[1]
    lang = sys.argv[2] if len(sys.argv) > 2 else "eng"

    print(f"Parsing:        {sup_path}")
    print(f"Language:       {lang}\n")

    parser = SupParser(sup_path)
    display_sets = parser.parse()
    print(f"Display sets: {len(display_sets)}\n")
    print("-" * 40)

    for i, ds in enumerate(display_sets[:20]):
        decoded = decode_display_set(ds)
        if not decoded: continue        # Skip blank events;

        ts = ticks_to_timestamp(ds.pcs.pts)
        text = ocr_display_set(decoded, lang=lang)

        if text:
            print(f"[{i:04d}] {ts}")
            print(text)
            print("-" * 60)

    if len(display_sets) > 20:
        print(f"\n... and {len(display_sets) - 20} more display sets.")