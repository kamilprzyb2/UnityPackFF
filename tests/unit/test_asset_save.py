"""
Writing serialized files: unitypack/asset.py (Asset.save, add_object,
add2ab) and object.py (ObjectInfo.save_data, write_value, init). Saved
files are compared byte for byte with what tests/synth.py builds, or read
back with unitypack.
"""

import pytest

from tests import synth
from tests.digest import object_digest
from tests.unit.support import open_asset, save


@pytest.fixture(params=[6, 7], ids=["format6", "format7"])
def fmt(request):
	return request.param


def digests(asset):
	return {pid: object_digest(o.read()) for pid, o in asset.objects.items()}


def test_save_reads_back_the_same(fmt):
	before = open_asset(synth.sample_file(fmt))
	expected = digests(before)
	after = open_asset(save(before))
	assert digests(after) == expected
	assert after.format == fmt  # only set once the file is loaded, which .objects does lazily


def test_source_still_reads_after_save():
	asset = open_asset(synth.sample_file(6))
	expected = digests(asset)
	asset = open_asset(synth.sample_file(6))  # fresh, nothing memoized
	save(asset)
	assert digests(asset) == expected


def test_save_twice():
	asset = open_asset(synth.sample_file(6))
	assert save(asset) == save(asset)


def test_save_writes_expected_bytes(fmt):
	# what Asset.save() should write: the same file with objects on 4-byte boundaries
	assert save(open_asset(synth.sample_file(fmt))) == synth.sample_file(fmt, align=4)


def test_save_keeps_trailing_padding():
	# the last string, "a/b", needs one byte of padding after it
	data = synth.serialized_file({49: synth.TEXT_ASSET}, [(1, 49, 49, synth.text_asset("n", "s", "a/b"))], align=4)
	assert save(open_asset(data)) == data


def test_edits_survive_save(fmt):
	asset = open_asset(synth.sample_file(fmt))
	t = asset.objects[1].contents  # contents, not read(): edits must stay on the memoized object
	t.name = "renamed"
	t.script = "a longer script than before"
	asset.objects[2].contents["m_Values"].append(7)

	after = open_asset(save(asset))
	assert (after.objects[1].read().name, after.objects[1].read().script) == ("renamed", "a longer script than before")
	assert after.objects[2].read()["m_Values"] == [1, 2, 65535, 7]
	assert after.objects[5].read().script == "second"  # later objects move, but still read


@pytest.mark.xfail(strict=True, reason="Asset.save() starts objects on 4 bytes; Unity-written files always start them on 8")
def test_save_is_byte_identical():
	data = synth.sample_file(6)  # synth lays objects out like Unity: 8-byte aligned
	assert save(open_asset(data)) == data


def test_add_object():
	asset = open_asset(synth.sample_file(6))
	obj = asset.add_object(49)
	assert obj.path_id == 6  # highest ID + 1
	obj.contents.name = "new"
	obj.contents.script = "added"

	after = open_asset(save(asset))
	assert after.objects.keys() == {1, 2, 5, 6}
	assert after.objects[6].read().script == "added"


def test_add_object_starts_empty():
	obj = open_asset(synth.sample_file(6)).add_object(1000)
	o = obj.contents
	assert (o["m_Count"], o["m_Scale"], o["m_Name"], o["m_Values"], o["m_Other"]) == (0, 0.0, "", [], None)


def test_add_object_format7_unsupported():
	# 64-bit IDs in format 7 are not sequential, so the next ID is unknown
	with pytest.raises(NotImplementedError):
		open_asset(synth.sample_file(7)).add_object(49)


def test_add2ab():
	asset = open_asset(synth.bundle_file())
	path, info = asset.add2ab("assets/b.txt", 3)
	assert (info["preloadIndex"], info["preloadSize"]) == (1, 1)

	ab = open_asset(save(asset)).objects[1].read()
	assert [(p.file_id, p.path_id) for p in ab["m_PreloadTable"]] == [(0, 2), (0, 3)]
	names = [(name, i["preloadIndex"], i["preloadSize"], i["asset"].path_id) for name, i in ab["m_Container"]]
	assert names == [("assets/a.txt", 0, 1, 2), ("assets/b.txt", 1, 1, 3)]


def test_add2ab_with_preloads():
	asset = open_asset(synth.bundle_file())
	preloads = list(asset.objects[1].contents["m_PreloadTable"]) * 2  # any list of pointers
	_, info = asset.add2ab("assets/c.txt", 3, preloads=preloads)
	assert (info["preloadIndex"], info["preloadSize"]) == (1, 2)
	ab = open_asset(save(asset)).objects[1].read()
	assert len(ab["m_PreloadTable"]) == 3


def test_add2ab_needs_an_asset_bundle():
	with pytest.raises(ValueError):
		open_asset(synth.sample_file(6)).add2ab("x", 1)
