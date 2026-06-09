"""Tests for the vectorized UO 16-bit pixel -> RGBA converter.

Contract:
- ``uo_16bit_pixels_to_rgba_bytes(pixels: bytes) -> bytes``: 16-bit 5-5-5 LE
  pixels in, RGBA bytes out (4 bytes per pixel, transparent for value 0).
- ``image_from_uo_pixels(width, height, pixels)`` builds a PIL.Image.
  Accepts either raw 16-bit bytes or a 2D list-of-rows of 16-bit values
  (rows are right-padded with 0 when shorter than ``width``; rows that
  exceed ``width`` are not truncated here - callers control input shape).
- Both NumPy and Pillow-only code paths must produce byte-identical output.
"""
from __future__ import annotations

import struct

import pytest
from PIL import Image

from ultima_sdk._pixel_convert import (
    uo_16bit_pixels_to_rgba_bytes,
    image_from_uo_pixels,
    _HAS_NUMPY,
)


def test_empty_input():
    assert uo_16bit_pixels_to_rgba_bytes(b"") == b""


def test_zero_pixel_is_transparent():
    rgba = uo_16bit_pixels_to_rgba_bytes(struct.pack("<H", 0x0000))
    assert rgba == b"\x00\x00\x00\x00"


def test_known_color_red():
    # 0x7C00 = 5-bit red 0x1F, green 0, blue 0
    rgba = uo_16bit_pixels_to_rgba_bytes(struct.pack("<H", 0x7C00))
    assert rgba == b"\xff\x00\x00\xff"


def test_known_color_green():
    rgba = uo_16bit_pixels_to_rgba_bytes(struct.pack("<H", 0x03E0))
    assert rgba == b"\x00\xff\x00\xff"


def test_known_color_blue():
    rgba = uo_16bit_pixels_to_rgba_bytes(struct.pack("<H", 0x001F))
    assert rgba == b"\x00\x00\xff\xff"


def test_5bit_to_8bit_msb_replication():
    # 5-bit value 0x1E (30) -> (30 << 3) | (30 >> 2) = 0xF0 | 0x07 = 0xF7.
    # 0x7BDE bits: r5=0x1E, g5=0x1E, b5=0x1E.
    rgba = uo_16bit_pixels_to_rgba_bytes(struct.pack("<H", 0x7BDE))
    assert rgba == b"\xf7\xf7\xf7\xff"


def test_multiple_pixels_length():
    pixels = struct.pack("<HHH", 0x7C00, 0x03E0, 0x001F)
    rgba = uo_16bit_pixels_to_rgba_bytes(pixels)
    assert len(rgba) == 12
    assert rgba[0:4] == b"\xff\x00\x00\xff"
    assert rgba[4:8] == b"\x00\xff\x00\xff"
    assert rgba[8:12] == b"\x00\x00\xff\xff"


def test_even_length_required():
    with pytest.raises(ValueError, match="even"):
        uo_16bit_pixels_to_rgba_bytes(b"\x00")


def test_image_from_uo_pixels_bytes_basic():
    pixels = struct.pack("<HHHH", 0x7C00, 0x0000, 0x0000, 0x03E0)
    img = image_from_uo_pixels(2, 2, pixels)
    assert isinstance(img, Image.Image)
    assert img.size == (2, 2)
    assert img.mode == "RGBA"
    assert img.getpixel((0, 0)) == (255, 0, 0, 255)
    assert img.getpixel((1, 0)) == (0, 0, 0, 0)
    assert img.getpixel((0, 1)) == (0, 0, 0, 0)
    assert img.getpixel((1, 1)) == (0, 255, 0, 255)


def test_image_from_uo_pixels_list_basic():
    rows = [
        [0x7C00, 0x03E0],
        [0x001F, 0x0000],
    ]
    img = image_from_uo_pixels(2, 2, rows)
    assert img.size == (2, 2)
    assert img.getpixel((0, 0)) == (255, 0, 0, 255)
    assert img.getpixel((1, 0)) == (0, 255, 0, 255)
    assert img.getpixel((0, 1)) == (0, 0, 255, 255)
    assert img.getpixel((1, 1)) == (0, 0, 0, 0)


def test_image_from_uo_pixels_list_short_row_pads():
    # Short rows are zero-padded to declared width.
    rows = [[0x7C00]]
    img = image_from_uo_pixels(4, 1, rows)
    assert img.size == (4, 1)
    assert img.getpixel((0, 0)) == (255, 0, 0, 255)
    assert img.getpixel((1, 0)) == (0, 0, 0, 0)
    assert img.getpixel((3, 0)) == (0, 0, 0, 0)


