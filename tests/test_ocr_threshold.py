"""
Binarization of subtitle images before OCR: fixed threshold for white text, adaptive for dim text.
"""
import shutil

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont, ImageOps

from sup2srt import ocr
from sup2srt.sup_decoder import DecodedImage


def _subtitle(fill: int, outline: int = 0, size: tuple[int, int] = (120, 40)) -> Image.Image:
    """
    A PGS-like RGBA image: transparent background, a dark outlined box with a text-coloured
    bar inside, and a half-transparent anti-aliasing edge.
    """
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((9, 9, 111, 31), fill=(outline // 2, outline // 2, outline // 2, 128))
    draw.rectangle((10, 10, 110, 30), fill=(outline, outline, outline, 255))
    draw.rectangle((20, 15, 100, 25), fill=(fill, fill, fill, 255))
    return image


def _fixed_binary(image: Image.Image) -> Image.Image:
    """
    The binarization before the adaptive threshold existed.
    """
    bg = Image.new("RGBA", image.size, (0, 0, 0, 255))
    bg.paste(image, mask=image.split()[3])
    gray = ImageOps.invert(bg.convert("L"))
    return gray.point(lambda px: 0 if px < ocr.BINARY_THRESHOLD else 255, "L")


def _text_mask(image: Image.Image) -> np.ndarray:
    return np.asarray(ocr._rgba_to_binary(image)) == 0


@pytest.mark.parametrize("fill", [235, 254, 255])
def test_white_text_is_binarized_exactly_as_before(fill: int) -> None:
    image = _subtitle(fill)

    assert ocr._rgba_to_binary(image).tobytes() == _fixed_binary(image).tobytes()


@pytest.mark.parametrize("fill", [144, 150])
def test_grey_uhd_text_is_kept_as_text(fill: int) -> None:
    """
    The fixed threshold turns grey text white, so Tesseract reads nothing.
    """
    image = _subtitle(fill)

    assert not (np.asarray(_fixed_binary(image)) == 0).any()
    text = _text_mask(image)
    assert text[15:26, 20:101].all()
    assert text.sum() == 11 * 81


def test_the_threshold_follows_the_text_luma() -> None:
    gray = Image.new("L", (10, 10), 0)
    gray.paste(144, (0, 0, 10, 5))
    opaque = Image.new("L", (10, 10), 255)

    assert ocr.binary_threshold(gray, opaque) == 255 - round(144 * ocr.TEXT_LUMA_FRACTION)


@pytest.mark.parametrize(
    "image",
    [
        Image.new("RGBA", (40, 20), (0, 0, 0, 0)),
        Image.new("RGBA", (40, 20), (40, 40, 40, 255)),
        Image.new("RGBA", (40, 20), (255, 255, 255, 100)),
    ],
    ids=["transparent", "dark-box", "faint-overlay"],
)
def test_images_without_readable_text_keep_the_fixed_threshold(image: Image.Image) -> None:
    alpha = image.split()[3]
    bg = Image.new("RGBA", image.size, (0, 0, 0, 255))
    bg.paste(image, mask=alpha)

    assert ocr.binary_threshold(bg.convert("L"), alpha) == ocr.BINARY_THRESHOLD
    assert ocr._rgba_to_binary(image).tobytes() == _fixed_binary(image).tobytes()


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="needs the tesseract binary")
@pytest.mark.parametrize("fill", [144, 235])
def test_tesseract_reads_grey_and_white_text(fill: int) -> None:
    font = ImageFont.load_default(size=36)
    image = Image.new("RGBA", (360, 70), (0, 0, 0, 0))
    ImageDraw.Draw(image).text(
        (12, 12), "Get down now!", font=font, fill=(fill, fill, fill, 255),
        stroke_width=2, stroke_fill=(0, 0, 0, 255),
    )
    decoded = DecodedImage(image=image, x=0, y=0, pts_ms=0.0, object_id=0)

    assert ocr.ocr_image(decoded).text == "Get down now!"
