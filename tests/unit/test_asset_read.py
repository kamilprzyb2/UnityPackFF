"""
Reading serialized files: unitypack/asset.py (Asset.load), object.py
(ObjectInfo.read, ObjectPointer) and type.py (type trees, structs.dat),
on made-up files from tests/synth.py.
"""

import io

import pytest

from tests import synth
from tests.unit.support import open_asset
from unitypack.asset import Asset


@pytest.fixture(params=[6, 7], ids=["format6", "format7"])
def asset(request):
	"The sample file as format 6 (104, Retrobution) and format 7 (728, Academy)."
	return open_asset(synth.sample_file(request.param))


def test_metadata(asset):
	assert asset.objects.keys() == {1, 2, 5}
	assert asset.long_object_ids == (asset.format == 7)
	assert [r.file_path for r in asset.asset_refs[1:]] == ["library/unity default resources"]


def test_text_asset(asset):
	t = asset.objects[1].read()
	assert (t.name, t.script) == ("hello", "world")
	assert asset.objects[5].read().script == "second"


def test_every_field_kind(asset):
	o = asset.objects[2].read()
	assert o["m_Count"] == -3
	assert o["m_Scale"] == 0.5
	assert o["m_Enabled"] is True  # a bool, then 3 bytes of padding
	assert o["m_Name"] == "thing"
	assert o["m_Values"] == [1, 2, 65535]


def test_pointer(asset):
	ptr = asset.objects[2].read()["m_Other"]
	assert (ptr.file_id, ptr.path_id) == (0, 1)
	assert ptr.object is asset.objects[1]
	assert ptr.resolve().script == "world"


def test_type_names(asset):
	assert asset.objects[1].type == "TextAsset"
	assert asset.objects[1].type_id == asset.objects[1].class_id == 49


def test_contents_is_memoized(asset):
	obj = asset.objects[1]
	assert obj.contents is obj.contents
	assert obj.read() is obj.contents  # read() returns the memoized object once there is one


def test_format7_48bit_ids():
	# two IDs share their low 32 bits; the pointer's m_FileID carries the top 16
	asset = open_asset(synth.big_id_file())
	assert asset.objects.keys() == {1, synth.BIG_A, synth.BIG_B}
	ptr = asset.objects[1].read()["m_Other"]
	assert (ptr.file_id, ptr.path_id) == (0, synth.BIG_B)
	assert ptr.object is asset.objects[synth.BIG_B]
	assert ptr.resolve().script == "second"


def test_format7_external_pointer():
	# file index 1 with the top bits of an ID in the other file
	data = synth.serialized_file({1000: synth.THING}, [(1, 1000, 1000, synth.thing(0, 0.0, False, "", synth.big_pointer(1, synth.BIG_A), []))],
		externals=["customassetbundle-other"], format=7)
	ptr = open_asset(data).objects[1].read()["m_Other"]
	assert (ptr.file_id, ptr.path_id) == (1, synth.BIG_A)


def test_get_object_by_path():
	asset = open_asset(synth.bundle_file())
	assert asset.get_object_by_path("assets/a.txt") is asset.objects[2]
	assert asset.get_object_by_path("Assets/A.TXT") is asset.objects[2]  # case doesn't matter
	with pytest.raises(KeyError):
		asset.get_object_by_path("assets/missing.txt")


def test_get_object_by_path_skips_null_entries():
	ab = synth.asset_bundle("b", [], [("x.txt", 0, 0, (0, 0)), ("x.txt", 0, 1, (0, 2))])
	asset = open_asset(synth.serialized_file({142: synth.ASSET_BUNDLE, 49: synth.TEXT_ASSET},
		[(1, 142, 142, ab), (2, 49, 49, synth.text_asset("a", "first"))]))
	assert asset.get_object_by_path("x.txt") is asset.objects[2]


def test_get_object_by_path_needs_an_asset_bundle():
	with pytest.raises(ValueError):
		open_asset(synth.sample_file(6)).get_object_by_path("assets/a.txt")


def test_unnamed_file():
	asset = Asset.from_file(io.BytesIO(synth.sample_file()))
	assert asset.name == ""
	assert asset.objects[1].read().script == "world"


def test_no_type_trees_fall_back_to_structs_dat():
	# like the 32 untyped map tiles: a format-6 file with zero type trees
	asset = open_asset(synth.serialized_file({}, [(1, 49, 49, synth.text_asset("n", "s"))]))
	assert asset.objects[1].read().script == "s"


def test_no_type_trees_unknown_class():
	asset = open_asset(synth.serialized_file({}, [(1, 7, 7, b"\0" * 8)]))
	asset.load()
	assert asset.types[7] is None  # class 7 is not in structs.dat: no schema, but the file still opens


def test_truncated_object_is_an_error():
	data = synth.serialized_file({49: synth.TEXT_ASSET}, [(1, 49, 49, synth.pack_string("only a name"))])
	with pytest.raises(Exception):
		open_asset(data).objects[1].read()
