"""
sup_parser.py - PGS/ SUP Binary Parser
Reads a .SUP file and extracts it into structured display sets.

PGS Structure:
Each segment starts with a 13 byte header:
- Magic:        2 Bytes (0x50, 0x47 = "PG")
- PTS:          4 Bytes (Presentation Timestamp, 90 kHz)
- DTS:          4 Bytes (Decoding Timestamp, 90 kHz)
- Seg-Type:     1 Byte
- Set-Length:   2 Bytes (Payload length without header)

Segment Types:
- 0x14 = PDS (Palette Definition Segment)
- 0x15 = ODS (Object Definition Segment)
- 0x16 = PCS (Presentation Composition Segment)
- 0x17 = WDS (Window Definition Segment)
- 0x80 = END (End of Display Set)

A 'Display Set' includes all Segments of a subtitle (from one PCS to the next END).
"""

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ------------------------------------------------------------------------------------
# Constants;
# ------------------------------------------------------------------------------------
MAGIC = b"PG"
HEADER_SIZE = 13    # Magic(2) + PTS(4) + DTS(4) + Type(1) + Length(2);

SEG_PDS = 0x14
SEG_ODS = 0x15
SEG_PCS = 0x16
SEG_WDS = 0x17
SEG_END = 0x80

PTS_HZ = 90_000 # Ticks per second;


# ------------------------------------------------------------------------------------
# Data classes for parsed segments;
# ------------------------------------------------------------------------------------
@dataclass
class PCSSegment:
    """
    Presentation Composition Segment: Contains timestamp and layout information.
    """
    pts: int                            # Raw PTS value in 90 kHz ticks;
    dts: int
    width: int                          # Video width in pixels;
    height: int                         # Video height in pixels;
    frame_rate: int                     # Framerate ID (usually 0x10 = 24 fps);
    composition_number: int             # Running number of the display set;
    composition_state: int              # 0x00=Normal, 0x40=Acquistion Point, 0x80=Epoch Start;
    palette_update_flat: bool           # True = only the palette is updated
    palette_id: int

    # Composition Objects (Reference to ODS + position);
    objects: list = field(default_factory=list)

    @property
    def pts_ms(self) -> float:
        """PTS in ms."""
        return self.pts / PTS_HZ * 1_000

    @property
    def is_forced(self) -> bool:
        return self.composition_state == 0x80



@dataclass
class CompositionObject:
    """
    Reference to an ODS object inside a PCS.
    """
    object_id: int
    window_id: int
    x: int          # Horizontal position (left);
    y: int          # Vertical position (top);
    forced: bool



@dataclass
class WDSSegment:
    """
    Window Definition Segment: Defines the display area.
    """
    pts: int
    dts: int
    windows: list = field(default_factory=list)



@dataclass
class WindowDefinition:
    window_id: int
    x: int
    y: int
    width: int
    height: int



@dataclass
class PDSSegment:
    """
    Palette Definition Segment: Color palette (256 RGBA entries).
    """
    pts: int
    dts: int
    palette_id: int
    version: int

    # Dict: index -> (Y, Cr, Cb, Alpha) - YCbCr color space;
    entries: dict = field(default_factory=dict)



@dataclass
class ODSSegement:
    """
    Object Definition Segment: Contains RLE compressed image data.
    """
    pts: int
    dts: int
    object_id: int
    version: int

    # 0xC0 = first (and last) fragment, 0x80 = first, 0x40 = last;
    sequence_flag: int
    width: int
    height: int
    rle_data: bytes         # Raw, (possibly fragmented) RLE data;



@dataclass
class DisplaySet:
    """
    A complete Display Set: One subtitle event.
    Contains exactly one PCS and an arbitrary amount of PDS/WDS/ODS segments.
    """
    pcs: Optional[PCSSegment] = None
    wds: Optional[WDSSegment] = None
    pds_list: list = field(default_factory=list)        # Might have multiple PDS fragments;
    ods_list: list = field(default_factory=list)        # Might have multiple ODS fragments;



# ------------------------------------------------------------------------------------
# Helper Functions;
# ------------------------------------------------------------------------------------
def ticks_to_timestamp(ticks: int) -> str:
    """
    Converts 90 kHz tick into a SRT timestamp (HH:MM:SS,mmm).
    """
    ms_total = ticks / PTS_HZ * 1_000
    ms = int(ms_total % 1_000)
    s = int(ms_total // 1_000)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"



# ------------------------------------------------------------------------------------
# Segment Parser;
# ------------------------------------------------------------------------------------
def _parse_pcs(data: bytes, pts: int, dts: int) -> PCSSegment:
    """
    Parses the payload of a PCS segment.
    """
    # Bytes 0-1:    Video Width;
    # Bytes 2-3:    Video Height;
    # Byte  4:      Framerate ID;
    # Bytes 5 - 6:  Composition Number;
    # Byte  7:      Composition State;
    # Byte  8:      Palette Update Flag (0x80 = True)
    # Byte  9:      Palette ID;
    # Byte  10:     Number of Composition Objects;

    width, height = struct.unpack_from(">HH", data, 0)
    frame_rate = data[4]
    comp_number = struct.unpack_from(">H", data, 5)[0]
    comp_state = data[7]
    palette_update = (data[8] == 0x80)
    palette_id = data[9]
    num_objects = data[10]

    objects = []
    offset = 11
    for _ in range(num_objects):
        obj_id = struct.unpack_from(">H", data, offset)[0]
        win_id = data[offset + 2]
        forced = bool(data[offset + 3] & 0x40)
        x = struct.unpack_from(">H", data, offset + 4)[0]
        y = struct.unpack_from(">H", data, offset + 6)[0]
        objects.append(CompositionObject(obj_id, win_id, x, y, forced))
        offset += 8

    return PCSSegment(
        pts=pts, dts=dts,
        width=width, height=height,
        frame_rate=frame_rate,
        composition_number=comp_number,
        composition_state=comp_state,
        palette_update_flat=palette_update,
        palette_id=palette_id,
        objects=objects
    )


def _parse_wds(data: bytes, pts: int, dts: int) -> WDSSegment:
    """
    Parses the payload of a WDS segment.
    """
    num_windows = data[0]
    windows = []
    offset = 1
    for _ in range(num_windows):
        win_id = data[offset]
        x, y = struct.unpack_from(">HH", data, offset + 1)
        w, h = struct.unpack_from(">HH", data, offset + 5)
        windows.append(WindowDefinition(win_id, x, y, w, h))
        offset += 9
    
    return WDSSegment(pts=pts, dts=dts, windows=windows)


def _parse_pds(data: bytes, pts: int, dts: int) -> PDSSegment:
    """
    Parses the payload of a PDS segement (color palette).
    """
    palette_id = data[0]
    version = data[1]
    entries = {}
    offset = 2

    # Each entry: 5 bytes (Index, Y, Cr, Cb, Alpha);
    while offset + 4 < len(data):
        idx = data[offset]
        y, cr, cb, alpha = data[offset + 1], data[offset + 2], data[offset + 3], data[offset + 4]
        entries[idx] = (y, cr, cb, alpha)
        offset += 5

    return PDSSegment(pts=pts, dts=dts, palette_id=palette_id, version=version, entries=entries)


def _parse_ods(data: bytes, pts: int, dts: int) -> ODSSegement:
    """
    Parse the payload from a ODS segmen.
    """
    object_id = struct.unpack_from(">H", data, 0)[0]
    version = data[2]
    sequence_flag = data[3]

    # The first fragment: Bytes 4 - 6 = total length of the RLE data (3 bytes!);
    # Bytes 7 - 8: Width, Bytes 9 - 10: Height;
    if sequence_flag in (0xC0, 0x80):       # First (or only) fragment;
        # Data length = 3 Byte big-endian-value (unusuall!!);
        data_length = (data[4] << 16) | (data[5] << 8) | data[6]
        width, height = struct.unpack_from(">HH", data, 7)
        rle_data = data[11:]

    else:
        # Following fragment: No explicit length, directly RLE data;
        width, height = 0, 0
        rle_data = data[4:]

    return ODSSegement(
        pts=pts, dts=dts,
        object_id=object_id,
        version=version,
        sequence_flag=sequence_flag,
        width=width, height=height,
        rle_data=rle_data
    )



# ------------------------------------------------------------------------------------
# Main Parser;
# ------------------------------------------------------------------------------------
class SupParser:
    """
    Reads a .SUP file and returns a list of 'DisplaySets'.

    Usage:
        parser = SupParser("subtitle.sup")
        display_sets = parser.parse()
    """

    def __init__(self, file_path: str | Path):
        self.file_path = Path(file_path)
        self._data = b""
        self._pos = 0

    # --------------------------------------------------------------------------------
    # Public API;
    # --------------------------------------------------------------------------------
    def parse(self) -> list[DisplaySet]:
        """
        Parses the whole .SUP file and return all Display Sets.
        """
        self._data = self.file_path.read_bytes()
        self._pos = 0
        display_sets = []
        current_ds = DisplaySet()

        while self._pos < len(self._data):
            seg_type, pts, dts, payload = self._read_next_segment()

            if seg_type is None:
                break       # End-of-file or error;

            if seg_type == SEG_PCS:
                # A new 'Display Set' always starts with a PCS;
                current_ds = DisplaySet()
                current_ds.pcs = _parse_pcs(payload, pts, dts)

            elif seg_type == SEG_WDS:
                current_ds.wds = _parse_wds(payload, pts, dts)

            elif seg_type == SEG_PDS:
                current_ds.pds_list.append(_parse_pds(payload, pts, dts))

            elif seg_type == SEG_ODS:
                ods = _parse_ods(payload, pts, dts)

                # Merge fragements (multiple ODS for one image);
                if ods.sequence_flag in (0xC0, 0x80):
                    # First (or single) fragment -> new ODS;
                    current_ds.ods_list.append(ods)
                else:
                    # Following segment -> attach RLE data;
                    if current_ds.ods_list:
                        current_ds.ods_list[-1].rle_data += ods.rle_data

            elif seg_type == SEG_END:
                # Finished Display Set;
                if current_ds.pcs is not None:
                    display_sets.append(current_ds)
                current_ds = DisplaySet()

        return display_sets


    # --------------------------------------------------------------------------------
    # Internal Helper Functions;
    # --------------------------------------------------------------------------------
    def _read_next_segment(self) -> tuple:
        """
        Reads the next segment from the byte buffer.
        Returns (seg_type, pts, dts, payload) or (None, ...) when an error occurrs.
        """
        if self._pos + HEADER_SIZE > len(self._data):
            return None, 0, 0, b""

        # Check the Magic;
        magic = self._data[self._pos:self._pos + 2]
        if magic != MAGIC:
            raise ValueError(f"Invalid Magic value on position {self._pos}: {magic!r} (Expected: b'PG')")

        # Read header: >2sIIBH = Big-Endian: 2 Chars, uint32, uint32, uint8, uint16
        _, pts, dts, seg_type, seg_len = struct.unpack_from(">2sIIBH", self._data, self._pos)
        self._pos += HEADER_SIZE

        # Read payload;
        payload = self._data[self._pos:self._pos + seg_len]
        self._pos += seg_len

        return seg_type, pts, dts, payload



# ------------------------------------------------------------------------------------
# Simple Self Test / Debug Output;
# ------------------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python parser.py <subtitle_file.sub>")
        sys.exit(1)

    path = sys.argv[1]
    print(f"Parsing: {path}\n")

    parser = SupParser(path)
    display_sets = parser.parse()

    print(f"Found DisplaySets: {len(display_sets)}\n")

    for i, ds in enumerate(display_sets[:10]):      # Print only the first 10 DisplaySets;
        pcs = ds.pcs
        start_ts = ticks_to_timestamp(pcs.pts)
        print(f"[{i + 1:04d}] PTS={start_ts} | "
              f"State=0x{pcs.composition_state:02X} | "
              f"Objects={len(pcs.objects)} | "
              f"PDS={len(ds.pds_list)} | "
              f"ODS={len(ds.ods_list)}")

        for ods in ds.ods_list:
            print(f"    ODS id={ods.object_id} | "
                  f"Size={ods.width}x{ods.height} | "
                  f"RLE-Bytes={len(ods.rle_data)}")

    if len(display_sets) > 10:
        print(f"\n... and {len(display_sets) - 10} more.")