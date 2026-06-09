""" Art module - Reads land tile and static item art from artidx.mul / art.mul
(or art.uop for High Seas clients).

Land tile geometry:
    44 rows arranged as an isometric diamond:
    top half:    row widths 2, 4, 6, ..., 44 (22 rows, step +2)
    bottom half: row widths 44, 42, ..., 2  (22 rows, step -2)
    Each row is left-padded so the diamond is centred in a 44x44 bounding box.
    Pixel values read directly from the stream, ORed with 0x8000 (opaque bit).

Static art (MUL format):
    4-byte header (ignored), then <HH width height>, then RLE.

Static art (UOP / raw format):
    Payload starts directly with <HH width height> followed by raw
    width*height*2 bytes of 16-bit pixel data (row-major).
    No 4-byte ignored header, no lookup table, no RLE.
"""
import struct
from typing import Optional, List, NamedTuple
from pathlib import Path
from .file_index import FileIndex
from .files import Files
from .exceptions import FileAccessException


class ArtTile(NamedTuple):
    """Result object returned by get_static_tile / get_art.

    pixels: raw bytes (width * height * 2 bytes of 16-bit LE colours),
    OR a list of rows (List[List[int]]) for MUL-decoded tiles.
    Supports both tuple unpacking (pixels, width, height = tile) for backward
    compatibility and attribute access (tile.width, tile.height) for new code.
    Also provides to_image() for save_png() integration.
    """

    pixels: object  # bytes (raw) or List[List[int]] (2D MUL rows)
    width: int
    height: int

    def to_image(self):
        """Convert pixel data to a PIL Image (RGBA).

        Supports both raw bytes (from :meth:`_decode_raw_static`) and
        2D list of rows (from :meth:`_decode_mul_static`).

        The per-pixel work is delegated to
        :func:`_pixel_convert.image_from_uo_pixels`, which uses a NumPy
        lookup table when NumPy is available and falls back to a
        Pillow-only loop otherwise. See
        ``ultima_sdk/_pixel_convert.py`` for the conversion contract.
        """
        from ._pixel_convert import image_from_uo_pixels

        return image_from_uo_pixels(self.width, self.height, self.pixels)



class EquippedArtTile:
    """Return value from get_equipped_art(); wraps an ArtTile with a resolved graphic_id."""

    def __init__(self, tile: Optional[ArtTile], graphic_id: int):
        self._tile = tile
        self.graphic_id = graphic_id

    def __getattr__(self, name: str):
        return getattr(self._tile, name)

    def __bool__(self) -> bool:
        return self._tile is not None


class Art:
    """Static class for loading art tiles."""

    _index: Optional[object] = None
    _initialized: bool = False

    # Pre-computed land-tile row widths: 44 rows, diamond shape, step 2.
    # Top half: 2, 4, ..., 44 (indices 0-21)
    # Bottom half: 44, 42, ..., 2 (indices 22-43)
    LAND_ROW_WIDTHS: List[int] = (
        list(range(2, 46, 2))   # 2..44 -- 22 values
        + list(range(44, 0, -2))  # 44..2 -- 22 values
    )  # total 44 values; max = 44

    # Patch cache: maps mul-file id -> raw bytes.
    # Populated by apply_verdata_patch(); checked before hitting _index.
    _patch_cache: dict = {}

    @classmethod
    def initialize(
        cls,
        idx_path: Optional[str] = None,
        mul_path: Optional[str] = None,
    ) -> bool:
        """Initialize Art static state.
        Tries MUL files first; if unavailable falls back to artlegacymul.uop.
        Optionally accepts explicit paths for testing.
        """
        if cls._initialized and not idx_path and not mul_path:
            return True

        # --- MUL path ---
        try:
            resolved_idx = idx_path or Files.get_file_path("artidx.mul")
            resolved_mul = mul_path or Files.get_file_path("art.mul")
            if resolved_idx and resolved_mul:
                cls._index = FileIndex(resolved_idx, resolved_mul, entry_size=12)
                cls._initialized = True
                return True
        except Exception as e:
            raise FileAccessException(f"Failed to initialize Art: {e}")

        # --- UOP fallback ---
        try:
            from .uop import UopBackedIndex
            uop_path = Files.get_file_path("artlegacymul.uop")
            if uop_path:
                cls._index = UopBackedIndex(
                    uop_path,
                    "build/artlegacymul/{0:D8}.tga",
                    has_extra=False,
                )
                cls._initialized = True
                return True
        except Exception as e:
            raise FileAccessException(f"Failed to initialize Art via UOP: {e}")

        return False

    @classmethod
    def _is_uop(cls) -> bool:
        """Return True if the active index is a UOP-backed index."""
        try:
            from .uop import UopBackedIndex
            return isinstance(cls._index, UopBackedIndex)
        except Exception:
            return False

    @classmethod
    def get_art(cls, id: int) -> Optional[ArtTile]:
        """Return ArtTile for the given art id.
        For id >= 0x4000: delegates to get_static_tile(id - 0x4000).
        For id < 0x4000: reads raw data and decodes as a raw/UOP-format tile.
        """
        if id >= 0x4000:
            return cls.get_static_tile(id - 0x4000)

        if not cls._initialized:
            cls.initialize()
        data = cls._get_raw_data(id)
        if data is None:
            return None
        return cls._decode_raw_static(data)

    @classmethod
    def get_equipped_art(
        cls,
        id: int,
        body_id: Optional[int] = None,
    ) -> Optional[EquippedArtTile]:
        """Return EquippedArtTile for art id, applying EquipConv remapping."""
        from .equipconv import EquipConv
        resolved_id = EquipConv.try_convert(id, body_id=body_id)
        if resolved_id is None:
            resolved_id = id
        tile = cls.get_art(resolved_id)
        if tile is None:
            return None
        return EquippedArtTile(tile, graphic_id=resolved_id)

    @classmethod
    def save_png(
        cls,
        id: int,
        path: str | Path,
        *,
        body_id: Optional[int] = None,
    ) -> bool:
        """Render art to PNG file.
        Uses get_equipped_art() when body_id is supplied, get_art() otherwise.
        Raises exceptions for invalid paths or other I/O errors.
        """
        if body_id is not None:
            data = cls.get_equipped_art(id, body_id=body_id)
        else:
            data = cls.get_art(id)
        if data is None:
            return False
        img = data.to_image()
        img.save(path, "PNG")
        return True

    @classmethod
    def _get_raw_data(cls, mul_id: int) -> Optional[bytes]:
        """Read raw bytes for mul_id, checking the verdata patch cache first."""
        if mul_id in cls._patch_cache:
            return cls._patch_cache[mul_id]
        if cls._index is None:
            return None
        return cls._index.read_raw(mul_id)

    @classmethod
    def get_land_tile(cls, id: int) -> Optional[List[List[int]]]:
        """Return a list of 44 rows (each 44 pixels wide) for land tile id.
        Returns the raw pixel list directly for backward compatibility.
        """
        if not cls._initialized:
            cls.initialize()
        data = cls._get_raw_data(id)
        if data is None:
            return None
        pos = 0
        rows: List[List[int]] = []
        for row_idx, row_width in enumerate(cls.LAND_ROW_WIDTHS):
            left_pad = (44 - row_width) // 2
            row: List[int] = [0] * 44
            for col in range(row_width):
                if pos + 2 > len(data):
                    break
                (pixel,) = struct.unpack_from("<H", data, pos)
                row[left_pad + col] = pixel | 0x8000
                pos += 2
            rows.append(row)
        return rows

    @classmethod
    def get_static_tile(cls, id: int) -> Optional[ArtTile]:
        """Return ArtTile(pixels, width, height) for static tile id, or None."""
        if not cls._initialized:
            cls.initialize()
        if cls._is_uop():
            data = cls._get_raw_data(id)
        else:
            data = cls._get_raw_data(id + 0x4000)
        if data is None or len(data) < 4:
            return None
        if cls._is_uop():
            return cls._decode_raw_static(data)
        else:
            return cls._decode_mul_static(data)

    @classmethod
    def _decode_raw_static(cls, data: bytes) -> Optional[ArtTile]:
        """Decode raw/UOP static art: <HH width height> + width*height*2 raw pixel bytes."""
        if len(data) < 4:
            return None
        width, height = struct.unpack_from("<HH", data, 0)
        if width == 0 or height == 0:
            return None
        pixel_bytes = data[4: 4 + width * height * 2]
        return ArtTile(pixels=pixel_bytes, width=width, height=height)

    @classmethod
    def _decode_mul_static(cls, data: bytes) -> Optional[ArtTile]:
        """Decode MUL static art payload: 4 ignored bytes, <HH width height>, RLE."""
        if len(data) < 8:
            return None
        pos = 4  # skip 4-byte ignored header
        width, height = struct.unpack_from("<HH", data, pos)
        pos += 4
        if width == 0 or height == 0:
            return None
        lookup_bytes = height * 2
        if pos + lookup_bytes > len(data):
            return None
        lookups = struct.unpack_from(f"<{height}H", data, pos)
        pos += lookup_bytes
        pixel_data_start = pos
        rows: List[List[int]] = [[0] * width for _ in range(height)]
        for y in range(height):
            rle_pos = pixel_data_start + lookups[y] * 2
            x = 0
            while x < width:
                if rle_pos + 4 > len(data):
                    break
                offset, run = struct.unpack_from("<HH", data, rle_pos)
                rle_pos += 4
                if offset == 0 and run == 0:
                    break
                x += offset
                for _ in range(run):
                    if rle_pos + 2 > len(data) or x >= width:
                        break
                    (pixel,) = struct.unpack_from("<H", data, rle_pos)
                    rle_pos += 2
                    rows[y][x] = pixel
                    x += 1
        return ArtTile(pixels=rows, width=width, height=height)

    @classmethod
    def apply_verdata_patch(
        cls,
        block_id: int,
        data: bytes,
        extra: Optional[int] = None,
    ) -> None:
        """Cache raw verdata patch bytes for a static art tile.
        block_id is the static tile index (0 = first static tile).
        """
        if not cls._initialized:
            cls.initialize()
        cls._patch_cache[block_id] = data
        if extra:
            cls._patch_cache[(block_id, "extra")] = extra
