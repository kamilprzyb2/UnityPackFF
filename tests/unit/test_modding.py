"""
Importing files into objects: unitypack/modding.py (import_texture,
import_mesh, import_audio). Each import is checked by reading the object
back with the library's own decoders (Texture2D.image, OBJMesh), so the
writer and the reader have to agree.

import_texture needs ImageMagick through the wand package; without it
these tests skip.
"""

import struct

import pytest
from PIL import Image, ImageOps

from tests import synth
from unitypack.engine.audio import AudioClip
from unitypack.engine.mesh import Mesh
from unitypack.engine.texture import Texture2D, TextureFormat
from unitypack.export import OBJMesh
from unitypack.object import FFOrderedDict

pytest.importorskip("wand.image", reason="import_texture needs ImageMagick (wand)")
from unitypack.modding import import_audio, import_mesh, import_texture  # noqa: E402


def empty(cls, **fields):
	"An engine object with just the fields the import functions expect to exist."
	d = FFOrderedDict()
	d.update(fields)
	return cls(d)


def empty_texture():
	settings = FFOrderedDict()
	settings.update({"m_FilterMode": 0, "m_Aniso": 0, "m_MipBias": 0.0, "m_WrapMode": 0})
	return empty(Texture2D, m_Name="old", m_TextureSettings=settings)


def png(tmp_path, img, name="in.png"):
	path = tmp_path / name
	img.save(path)
	return str(path)


def halves(top, bottom, size=8):
	"An image whose top half is one colour and bottom half another, so a flip shows."
	img = Image.new("RGBA", (size, size), bottom)
	img.paste(Image.new("RGBA", (size, size // 2), top), (0, 0))
	return img


# red over green: a red/blue swap turns the top blue, a flip puts green on top
RED, GREEN = (255, 0, 0, 255), (0, 255, 0, 255)


def decoded_upright(tex):
	"Decode with the library and flip back, as bin/unityextract.py does."
	return ImageOps.flip(tex.image.convert("RGBA"))


def same_pixels(a, b):
	return a.size == b.size and a.tobytes() == b.tobytes()


def test_rgba32_round_trip(tmp_path):
	img = halves(RED, GREEN)
	tex = empty_texture()
	import_texture(tex, png(tmp_path, img), name="new", fmt="rgba32")
	assert (tex.name, tex.width, tex.height, tex.format) == ("new", 8, 8, TextureFormat.RGBA32)
	assert len(tex.data) == tex.complete_image_size == 8 * 8 * 4
	assert same_pixels(decoded_upright(tex), img)  # stored bottom-up, like Unity


def test_dxt1_round_trip(tmp_path):
	img = halves(RED, GREEN)  # one colour per 4x4 block, so DXT1 is lossless here
	tex = empty_texture()
	import_texture(tex, png(tmp_path, img), fmt="dxt1")
	assert tex.format == TextureFormat.DXT1
	assert len(tex.data) == (8 // 4) * (8 // 4) * 8  # 8 bytes per 4x4 block
	assert same_pixels(decoded_upright(tex), img)


def test_dxt5_with_alpha(tmp_path):
	tex = empty_texture()
	import_texture(tex, png(tmp_path, halves((255, 0, 0, 128), GREEN)), fmt="dxt5")
	assert tex.format == TextureFormat.DXT5
	assert len(tex.data) == 4 * 16  # 16 bytes per block
	assert decoded_upright(tex).getpixel((0, 0))[3] in range(120, 137)  # alpha survives, roughly


def test_dxt5_without_alpha(tmp_path):
	# Some ImageMagick versions write DXT1 when there's no transparency, even when asked
	# for DXT5; import_texture checks the DDS header and records what it really got.
	img = halves(RED, GREEN)
	tex = empty_texture()
	import_texture(tex, png(tmp_path, img), fmt="dxt5")
	block = {TextureFormat.DXT1: 8, TextureFormat.DXT5: 16}[tex.format]
	assert len(tex.data) == 4 * block  # the recorded format matches the data
	assert same_pixels(decoded_upright(tex), img)


def test_rgb16_round_trip(tmp_path):
	img = halves(RED, GREEN)
	tex = empty_texture()
	import_texture(tex, png(tmp_path, img), fmt="rgb16")
	assert tex.format == TextureFormat.RGB565
	assert same_pixels(decoded_upright(tex), img)


def test_rgba16_round_trip(tmp_path):
	img = halves(RED, GREEN)
	tex = empty_texture()
	import_texture(tex, png(tmp_path, img), fmt="rgba16")
	assert tex.format == TextureFormat.ARGB4444
	assert same_pixels(decoded_upright(tex), img)


def test_texture_settings(tmp_path):
	tex = empty_texture()
	import_texture(tex, png(tmp_path, halves(RED, GREEN)), fmt="rgba32", clamp=True)
	assert tex._obj["m_TextureSettings"]["m_WrapMode"] == 1
	assert (tex._obj["m_MipMap"], tex._obj["m_TextureDimension"], tex._obj["m_Limit"]) == (False, 2, -1)


def test_import_texture_checks_arguments(tmp_path):
	path = png(tmp_path, halves(RED, GREEN))
	with pytest.raises(ValueError):
		import_texture({}, path)
	with pytest.raises(ValueError):
		import_texture(empty_texture(), path, fmt="jpeg")


OBJ_TRIANGLE = """v 1 2 3
v 4 5 6
v 7 8 9
vn 0 0 1
vt 0 0.25
vt 1 0
vt 0 1
f 1/1/1 2/2/1 3/3/1
"""


def imported_mesh(tmp_path, text=OBJ_TRIANGLE):
	path = tmp_path / "in.obj"
	path.write_text(text)
	m = empty(Mesh, m_SubMeshes=[])
	import_mesh(m, str(path), name="tri")
	return m


def test_import_mesh(tmp_path):
	m = imported_mesh(tmp_path)
	assert m.name == "tri"
	assert (m.mesh_compression, m.use_16bit_indices) == (0, 1)
	assert [(v["x"], v["y"], v["z"]) for v in m.vertices] == [(-1, 2, 3), (-4, 5, 6), (-7, 8, 9)]  # x mirrored
	assert [(n["x"], n["z"]) for n in m.normals] == [(-0.0, 1)] * 3
	assert [(t["x"], t["y"]) for t in m.uvs] == [(0, 0.25), (1, 0), (0, 1)]  # v kept as is
	assert struct.unpack("<3H", m.index_buffer) == (2, 1, 0)  # reversed: the mirror flips winding
	sub = m.submeshes[0]
	assert (sub.first_byte, sub.index_count, sub.triangle_count, sub.is_tri_strip) == (0, 3, 1, 0)


def test_import_then_export(tmp_path):
	out = OBJMesh(imported_mesh(tmp_path)).export().splitlines()
	assert [l for l in out if l.startswith("v ")] == ["v 1.0 2.0 3.0", "v 4.0 5.0 6.0", "v 7.0 8.0 9.0"]
	assert [l.strip() for l in out if l.startswith("f ")] == ["f 1/1/1 2/2/2 3/3/3"]
	# export writes v as 1 - v but import doesn't undo it, so v comes back flipped.
	# Whether that's a bug depends on the texture orientation each side assumes (open question).
	assert [l for l in out if l.startswith("vt ")] == ["vt 0.0 0.75", "vt 1.0 1.0", "vt 0.0 0.0"]


def test_import_mesh_needs_triangles(tmp_path):
	quad = "v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nvn 0 0 1\nvt 0 0\nf 1/1/1 2/1/1 3/1/1 4/1/1\n"
	with pytest.raises(ValueError):
		imported_mesh(tmp_path, quad)


def test_import_audio(tmp_path):
	path = tmp_path / "in.ogg"
	path.write_bytes(synth.ogg(seconds=2, rate=22050))
	clip = empty(AudioClip)
	import_audio(clip, str(path), name="song")
	assert clip.audio_data == path.read_bytes()  # the whole Ogg file is the clip's data
	assert (clip.name, clip.length, clip.frequency) == ("song", 2.0, 22050)
	assert (clip._obj["m_Format"], clip._obj["m_Size"]) == (5, len(clip.audio_data))


def test_import_audio_checks_arguments(tmp_path):
	with pytest.raises(ValueError):
		import_audio(empty(AudioClip), str(tmp_path / "song.mp3"))
	with pytest.raises(ValueError):
		import_audio({}, str(tmp_path / "song.ogg"))
