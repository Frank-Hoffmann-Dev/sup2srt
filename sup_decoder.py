"""
sup_decoder.py - PGS RLE Decoder & Image Reconstructor
Takes a DisplaySet from 'sup_parser.py' and returns a PIL.Image.

Procedure:
1. Decode RLE data -> Pixel-Index-Array
2. Convert palette (YCbCr -> RGBA)
3. Index-Array + Palette + RGBA-Image (PIL.Image)

PGS RLE format:
Each line ends with two null bytes (0x00 0x00).
There are the following patterns in a line:

Byte 1 = 0x00 -> Escape sequenz, Byte 2 decides:
- Byte 2 = 0x00             -> End-of-Line
- Byte 2 = 0bXX000000       -> (only flags, no pixel) -> resevered
- Byte 2 = 0b00LLLLLL       -> L pixel with color 0 (transparent)
- Byte 2 = 0b01LLLLLL LL    -> (256 - 16383) Pixel with color 0
- Byte 2 = 0b10LLLLLL CC    -> L pixel with color CC
- Byte 2 = 0b11LLLLLL LL CC -> (256 - 16383) pixel with color CC

Byte != 0x00 -> 1 Pixel with color index = Byte 1

Palette (PDS):
Colors are saved as YCbCr (BT.601) and need conversion into RGB.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

from PIL import Image
import numpy as np

from sup_parser import DisplaySet, ODSSegement, PDSSegment, PCSSegment


# ------------------------------------------------------------------------------------
# Data Classes;
# ------------------------------------------------------------------------------------
@dataclass
class DecodedImage:
    """
    Result of the decoding of a DisplaySet.
    """
    image: Image.Image          # RGBA PIL Image;
    x: int                      # Position in the video (from PCS/ CompositionObject);
    y: int
    pts_ms: float               # Start timestamp in ms;
    object_id: int



# ------------------------------------------------------------------------------------
# 1. Convert YCbCr -> RGB;
# ------------------------------------------------------------------------------------
def _ycbcr_to_rgb(y: int, cb: int, cr: int) -> tuple[int, int, int]:
    """
    Convert YCbCr (BT.601, studio swing: 16-235/240) to RGB.
    Return values between 0-255 (clipped).
    """
    y_ = y - 16
    cb_ = cb - 128
    cr_ = cr - 128

    r = 1.164 * y_ + 1.596 * cr_
    g = 1.164 * y_ - 0.392 * cb_ - 0.813 * cr_
    b = 1.164 * y_ + 2.017 * cb_

    # Clip to a range between 0-255;
    r = max(0, min(255, int(r)))
    g = max(0, min(255, int(g)))
    b = max(0, min(255, int(b)))

    return r, g, b


def build_rgba_palette(pds: PDSSegment) -> np.ndarray:
    """
    Build a 256*4 RGBA-Lookup-Table from the PDS segment.
    Index 0 is always (according to the PGS specs) transparent.

    :return: numpy array with shape (256, 4), dtype=uint8
    """
    palette = np.zeros((256, 4), dtype=np.uint8)

    for idx, (y, cr, cb, alpha) in pds.entries.items():
        r, g, b = _ycbcr_to_rgb(y, cb, cr)
        palette[idx] = [r, g, b, alpha]

    # Index 0 -> always completely transparent;
    palette[0] = [0, 0, 0, 0]

    return palette



# ------------------------------------------------------------------------------------
# 2. Decode RLE Data;
# ------------------------------------------------------------------------------------
def decode_rle(rle_data: bytes, width: int, height: int) -> np.ndarray:
    """
    Decode PGS RLE data into a 2D array made of palette indices.

    :return: numpy array with shape (height, width), dtype=uint8
    Each value is a palette index (0-255).

    Throws ValueError when the display dimensions are invalid.
    """
    # Interlaced buffer: even rows at the top, odd at the bottom;
    total_pixels = width * height
    pixels = np.zeros(total_pixels, dtype=np.uint8)
    pixel_pos = 0       # Current position in the flat pixel array;
    i = 0               # Current position in the RLE data;

    while i < len(rle_data) and pixel_pos < total_pixels:
        byte1 = rle_data[i]
        i += 1

        if byte1 != 0x00:
            # Case 1: 1 pixel with color index byte1;
            pixels[pixel_pos] = byte1
            pixel_pos += 1
            continue
        
        # Escape sequenz;
        if i >= len(rle_data): break
        byte2 = rle_data[i]
        i += 1

        if byte2 == 0x00:
            # End-of-Line: Align the next line;
            # Round the pixel position to the next multiple of 'width';
            current_row = pixel_pos // width
            pixel_pos = (current_row + 1) * width
            continue

        # Read flags from the 2 bits of byte2;
        flag = (byte2 & 0xC0) >> 6
        length_high = byte2 & 0x3F          # Lower 6 bits = high part of run length;

        if flag == 0b00:
            # 0x00 0b00LLLLLL -> L transparent pixel (color 0);
            count = length_high
            color = 0

        elif flag == 0b01:
            # 0x00 0b01LLLLLL LL -> long transparent sequenz (to 16383);
            if i >= len(rle_data): break
            byte3 = rle_data[i]; i += 1
            count = (length_high << 8) | byte3
            color = 0

        elif flag == 0b10:
            # 0x00 0b10LLLLLL CC -> L pixel with color CC;
            if i >= len(rle_data): break
            color = rle_data[i]; i += 1
            count = length_high

        else: # flag == 0b11;
            # 0x00 0b11LLLLLL LL CC -> long colored sequenz;
            if i + 1 >= len(rle_data): break
            byte3 = rle_data[i]; i += 1
            color = rle_data[i]; i += 1
            count = (length_high << 8) | byte3

        # Write the pixel run;
        end_pos = min(pixel_pos + count, total_pixels)
        pixels[pixel_pos:end_pos] = color
        pixel_pos += count

    return pixels.reshape((height, width))



# ------------------------------------------------------------------------------------
# 3. Index array + palette -> RGBA PIL.Image;
# ------------------------------------------------------------------------------------
def indices_to_image(indices: np.ndarray, palette: np.ndarray) -> Image.Image:
    """
    Applies the RGBA palette onto the index array.

    indices: (H, W) uint8 array with palette indices
    palette: (256, 4) uint8 array with RGBA values

    return: PIL.Image in mode "RGBA"
    """
    # Fancy indexing: lookup for each pixel index the RGBA value;
    rgba = palette[indices]         # Shape: (H, W, 4)
    return Image.fromarray(rgba, mode="RGBA")



# ------------------------------------------------------------------------------------
# Main Decoder Functions;
# ------------------------------------------------------------------------------------
def decode_display_set(ds: DisplaySet) -> list[DecodedImage]:
    """
    Decoded a complete DisplaySet.

    A DisplaySet can contain multiple ODS objects (i.e. subtitle with two lines which are in two different windows).
    For each ODS object one DecodedImage is returned.

    Returns an empty list if the DisplaySet does not contain an image (i.e. blank event without ODS).
    """
    if not ds.ods_list or not ds.pds_list or ds.pcs is None:
        return []

    # Use the palette of the first PDS (for more complex cases: Match palette ID);
    pds = _find_palette(ds)
    if pds is None: return []

    rgba_palette = build_rgba_palette(pds)
    results = []

    for ods in ds.ods_list:
        if ods.width == 0 or ods.height == 0:
            continue        # Skip follow up fragments without dimensions;

        try:
            indices = decode_rle(ods.rle_data, ods.width, ods.height)
        except ValueError as e:
            print(f"Warning: RLE error at ODS id={ods.object_id}: {e}")
            continue

        image = indices_to_image(indices, rgba_palette)

        # Read the position from the CompositionObject in PCS;
        x, y = _find_position(ds.pcs, ods.object_id)

        results.append(
            DecodedImage(
                image=image,
                x=x, y=y,
                pts_ms=ds.pcs.pts_ms,
                object_id=ods.object_id
            )
        )

    return results


def decode_all(display_sets: list[DisplaySet]) -> list[tuple[DisplaySet, list[DecodedImage]]]:
    """
    Decodes all Display Sets from a .SUP file.

    return: List of (DisplaySet, [DecodedImage, ...]) tuples.
    Empty Display Sets (blank events) are returned with an empty image list.
    """
    results = []
    for ds in display_sets:
        images = decode_display_set(ds)
        results.append((ds, images))
    
    return results



# ------------------------------------------------------------------------------------
# Intern Helper Functions;
# ------------------------------------------------------------------------------------
def _find_palette(ds: DisplaySet) -> Optional[PDSSegment]:
    """
    Searches the PDS fitting to the PCS via it's palette_id.
    Uses the first PDS if there is no matching ID.
    """
    target_id = ds.pcs.palette_id
    for pds in ds.pds_list:
        if pds.palette_id == target_id:
            return pds

    # Fallback;
    return ds.pds_list[0] if ds.pds_list else None


def _find_position(pcs: PCSSegment, object_id: int) -> tuple[int, int]:
    """
    Reads the (x, y) positions of a ODS from the PCS.
    """
    for obj in pcs.objects:
        if obj.object_id == object_id:
            return obj.x, obj.y
        
    return 0, 0



# ------------------------------------------------------------------------------------
# Self Test / Debug Output;
# ------------------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    from sup_parser import SupParser, ticks_to_timestamp

    if len(sys.argv) < 2:
        print("Usage: python sup_decoder.py <subtitles_file.sup> [output_folder]")
        sys.exit(1)

    sup_path = sys.argv[1]
    out_dir = sys.argv[2] if len(sys.argv) > 2 else "debug_frames"

    import os
    os.makedirs(out_dir, exist_ok=True)

    print(f"Parsing: {sup_path}")
    parser = SupParser(sup_path)
    display_sets = parser.parse()
    print(f"Display Sets: {len(display_sets)}")

    saved = 0
    for i, ds in enumerate(display_sets):
        decoded = decode_display_set(ds)
        for di in decoded:
            ts = ticks_to_timestamp(ds.pcs.pts)
            filename = os.path.join(out_dir, f"frame_{i:04d}_obj{di.object_id}.png")
            di.image.save(filename)

            print(f"    [{i:04d}] {ts} -> {filename} ({di.image.width}x{di.image.height})")
            saved += 1
            if saved >= 20:
                print("\nStops after 20 frames - remove limit for complete decoding.")
                sys.exit(0)

    print(f"\n{saved} images in '{out_dir}/'")