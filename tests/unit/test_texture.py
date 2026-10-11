"""
Texture decoding: unitypack/engine/texture.py (Texture2D.image) for every
format the FusionFall builds use, on pixel data written by hand from each
format's layout.

The image comes out exactly as stored: Unity stores rows bottom-up, so it is
upside down (bin/unityextract.py flips it when saving a PNG).
"""

import struct

import pytest

from unitypack.engine.texture import Texture2D, TextureFormat
from unitypack.object import FFOrderedDict


def texture(fmt, width, height, data):
	d = FFOrderedDict()
	d.update({"m_Name": "t", "m_Width": width, "m_Height": height, "m_TextureFormat": int(fmt), "image data": data})
	return Texture2D(d)


def pixels(tex):
	"Every pixel as (r, g, b, a), row by row."
	img = tex.image.convert("RGBA")
	return [img.getpixel((x, y)) for y in range(img.height) for x in range(img.width)]


RED_DXT1 = struct.pack("<HHI", 0xF800, 0x001F, 0)  # colour 0 (red) everywhere

CASES = [
	# format, width, height, data, first pixel (r, g, b, a)
	(TextureFormat.Alpha8, 2, 1, bytes([0x10, 0xF0]), (0, 0, 0, 0x10)),
	(TextureFormat.RGB24, 1, 1, bytes([10, 20, 30]), (10, 20, 30, 255)),
	(TextureFormat.RGBA32, 1, 1, bytes([10, 20, 30, 40]), (10, 20, 30, 40)),
	(TextureFormat.ARGB32, 1, 1, bytes([40, 10, 20, 30]), (10, 20, 30, 40)),
	# DXT: one 4x4 block; two 565 colours then 2-bit indices (0 = colour 0)
	(TextureFormat.DXT1, 4, 4, RED_DXT1, (255, 0, 0, 255)),
	# DXT3: 4-bit alpha per pixel (0x8 -> 0x88), then a DXT1 block (green)
	(TextureFormat.DXT3, 4, 4, bytes([0x88] * 8) + struct.pack("<HHI", 0x07E0, 0x001F, 0), (0, 255, 0, 0x88)),
	# DXT5: alpha endpoints 0x80 0x80 + 3-bit indices, then a DXT1 block whose indices pick colour 1 (blue)
	(TextureFormat.DXT5, 4, 4, bytes([0x80, 0x80, 0, 0, 0, 0, 0, 0]) + struct.pack("<HHI", 0xF800, 0x001F, 0x55555555), (0, 0, 255, 128)),
]


@pytest.mark.parametrize("fmt,w,h,data,first", CASES, ids=[c[0].name for c in CASES])
def test_decode(fmt, w, h, data, first):
	tex = texture(fmt, w, h, data)
	assert tex.image.size == (w, h)
	px = pixels(tex)
	assert px[0] == first
	assert len(set(px)) == (2 if fmt == TextureFormat.Alpha8 else 1)


# RGB565 is a little-endian 16-bit word, red in the top 5 bits. CharTexture uses it for 6 textures.
def test_decode_rgb565():
	assert pixels(texture(TextureFormat.RGB565, 1, 1, struct.pack("<H", 0xF800))) == [(255, 0, 0, 255)]


# ARGB4444: a little-endian 16-bit word, 4 bits each of A, R, G, B from the top
def test_decode_argb4444():
	assert pixels(texture(TextureFormat.ARGB4444, 1, 1, struct.pack("<H", 0xF842))) == [(0x88, 0x44, 0x22, 0xFF)]


def test_rows_are_not_flipped():
	# 1x2 RGB24: the first row in the data is the top row of the image
	tex = texture(TextureFormat.RGB24, 1, 2, bytes([255, 0, 0, 0, 0, 255]))
	assert pixels(tex) == [(255, 0, 0, 255), (0, 0, 255, 255)]


def test_empty_texture():
	assert texture(TextureFormat.RGBA32, 0, 0, b"").image is None


@pytest.mark.parametrize("fmt", [TextureFormat.ETC_RGB4, TextureFormat.DXT1Crunched])
def test_unsupported_formats(fmt):
	with pytest.raises(NotImplementedError):
		texture(fmt, 4, 4, bytes(8)).image


def test_repr():
	assert repr(texture(TextureFormat.DXT1, 4, 4, RED_DXT1)) == "<Texture2D t (DXT1 4x4)>"
