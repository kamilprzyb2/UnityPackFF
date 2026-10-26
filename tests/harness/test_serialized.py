"""Our independent serialized-file parser (tests/serialized.py), on made-up files."""

import pytest

from tests import serialized, synth


@pytest.fixture(params=[6, 7], ids=["format6", "format7"])
def sample(request):
	return request.param, synth.sample_file(request.param)


def test_parses_to_the_last_byte(sample):
	fmt, data = sample
	sf = serialized.parse(data)
	assert sf.format == fmt
	assert sf.end == sf.file_size == len(data)


def test_metadata(sample):
	fmt, data = sample
	sf = serialized.parse(data)
	assert sorted(sf.types) == [49, 1000]
	assert [o["path_id"] for o in sf.objects] == [1, 2, 5]
	assert [o["class_id"] for o in sf.objects] == [49, 1000, 49]
	assert sf.big_ids == (1 if fmt == 7 else None)  # format 7 only
	assert [e["path"] for e in sf.externals] == ["library/unity default resources"]


def test_type_tree(sample):
	_, data = sample
	thing = serialized.parse(data).types[1000]
	assert [n.name for n in thing.walk()][:4] == ["Base", "m_Count", "m_Scale", "m_Enabled"]
	assert thing.children[2].flags & synth.ALIGN


def test_looks_serialized(sample):
	_, data = sample
	assert serialized.looks_serialized(data)
	assert not serialized.looks_serialized(b"MZ" + bytes(100))  # a DLL
	assert not serialized.looks_serialized(data[:-1])  # sizes no longer agree
