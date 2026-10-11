"""
FusionFall's per-map data: unitypack/mapdata.py (map_attributes,
music_zones, scene_colors) and bin/dump_mapdata.py, on script objects
(MonoBehaviours) written by hand with tests/synth.py.

A script object's type ID is negative, its class ID 114, and its type tree
starts with MonoBehaviour's own fields before the script's.
"""

import json
import struct

import pytest

from tests import synth
from tests.synth import ALIGN, Node, array_node, float_node, int_node, pack_string, pptr_node, string_node
from tests.unit.support import open_asset, run_script
from unitypack.mapdata import ATTRIBUTE_HEAL, ATTRIBUTE_POISON, MapAttributes, map_attributes, music_zones, scene_colors

MONO_BEHAVIOUR = 114


def behaviour(*fields):
	return Node("MonoBehaviour", "Base", children=[
		pptr_node("m_GameObject", "GameObject"),
		Node("UInt8", "m_Enabled", 1, flags=ALIGN),
		pptr_node("m_Script", "MonoScript"),
		string_node("m_Name"),
		*fields,
	])


def behaviour_data(name=""):
	return struct.pack("<ii", 0, 0) + struct.pack("<B", 1) + b"\0" * 3 + struct.pack("<ii", 0, 0) + pack_string(name)


def color_node(name):
	return Node("ColorRGBA", name, 16, [float_node(c) for c in "rgba"])


ATTRIBUTE_TABLE = behaviour(Node("vector", "attributes", children=[
	Node("Array", "Array", children=[int_node("size"), Node("UInt8", "data", 1)], is_array=True, flags=ALIGN),
]))

COLOR_SETUP = behaviour(float_node("fogDepth"), color_node("fogColor"), color_node("skyColor"), color_node("lightColor"))

MUSIC_DATA = Node("MusicData", "data", children=[
	int_node("index"), string_node("name"),
	array_node("points", Node("Vector2f", "data", 8, [float_node("x"), float_node("y")])),
	string_node("fileName"), int_node("delay"), int_node("bInstanceFlag"),
])
MUSIC_STORAGE = behaviour(array_node("musicDatas", MUSIC_DATA), array_node("ambientDatas", MUSIC_DATA))


def music_data(index, file, points, instance=0):
	data = struct.pack("<i", index) + pack_string("")
	data += struct.pack("<I", len(points)) + b"".join(struct.pack("<ff", *p) for p in points)
	return data + pack_string(file) + struct.pack("<ii", 0, instance)


SQUARE = [(0, 0), (0, 512), (512, 512), (512, 0)]


def map_file():
	cells = bytearray(9)  # a 3 x 3 grid
	cells[1 + 2 * 3] = ATTRIBUTE_POISON  # (1, 2)
	cells[2 + 0 * 3] = ATTRIBUTE_POISON | ATTRIBUTE_HEAL  # (2, 0)
	attributes = behaviour_data() + struct.pack("<I", 9) + bytes(cells) + b"\0" * 3
	colors = behaviour_data() + struct.pack("<f", 0.5) + struct.pack("<12f", 0.1, 0.2, 0.3, 1, 0, 0, 1, 1, 1, 1, 1, 1)
	return synth.serialized_file(
		{-1: ATTRIBUTE_TABLE, -2: COLOR_SETUP},
		[(1, -1, MONO_BEHAVIOUR, attributes), (2, -2, MONO_BEHAVIOUR, colors)],
	)


def music_file():
	music = [music_data(0, "00 Main Theme.ogg", SQUARE), music_data(1, "none.ogg", SQUARE, instance=1)]
	ambient = [music_data(0, "ambient Wind.ogg", [(1.5, 2.5), (3, 4), (5, 6)])]
	data = behaviour_data("MusicDataStorage")
	data += struct.pack("<I", 2) + b"".join(music) + struct.pack("<I", 1) + b"".join(ambient)
	return synth.serialized_file({-1: MUSIC_STORAGE}, [(1, -1, MONO_BEHAVIOUR, data)])


def test_map_attributes():
	a = map_attributes(open_asset(map_file()))
	assert a.size == 3
	assert a.get(1, 2) == ATTRIBUTE_POISON
	assert a.cells(ATTRIBUTE_POISON) == [(2, 0), (1, 2)]
	assert a.cells(ATTRIBUTE_HEAL) == [(2, 0)]
	# rows: z = 0 first, x across
	assert a.rows() == [[0, 0, 24], [0, 0, 0], [0, 8, 0]]


def test_map_attributes_must_be_square():
	with pytest.raises(ValueError, match="8 attribute bytes"):
		MapAttributes(bytes(8))


def test_scene_colors():
	c = scene_colors(open_asset(map_file()))
	assert c["fog_depth"] == 0.5
	assert c["fog"] == pytest.approx((0.1, 0.2, 0.3, 1))
	assert c["sky"] == (0, 0, 1, 1)


def test_music_zones():
	z = music_zones(open_asset(music_file()))
	main, silent = z["music"]
	assert (main.index, main.file, main.instance, main.points) == (0, "00 Main Theme.ogg", False, SQUARE)
	assert (silent.file, silent.instance) == (None, True)  # none.ogg: silence
	assert z["ambient"][0].points == [(1.5, 2.5), (3, 4), (5, 6)]


def test_files_without_map_data():
	asset = open_asset(synth.sample_file())
	assert map_attributes(asset) is None
	assert music_zones(asset) is None
	assert scene_colors(asset) is None


def test_dump_mapdata(tmp_path):
	(tmp_path / "BuildPlayer-Map_00_00").write_bytes(map_file())
	(tmp_path / "sharedassets0.assets").write_bytes(music_file())
	(tmp_path / "other").write_bytes(synth.sample_file())
	run_script("dump_mapdata.py", tmp_path / "BuildPlayer-Map_00_00", tmp_path / "sharedassets0.assets", tmp_path / "other", "-o", tmp_path / "out")

	scene = json.loads((tmp_path / "out" / "BuildPlayer-Map_00_00.json").read_text())
	assert scene["attributes"]["rows"] == [[0, 0, 24], [0, 0, 0], [0, 8, 0]]
	assert scene["attributes"]["counts"]["8"] == 2
	assert scene["colors"]["fog_depth"] == 0.5
	music = json.loads((tmp_path / "out" / "sharedassets0.assets.json").read_text())
	assert [z["file"] for z in music["music"]] == ["00 Main Theme.ogg", None]
	assert not (tmp_path / "out" / "other.json").exists()
