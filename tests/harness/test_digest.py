import struct

from unitypack.engine.object import Object
from unitypack.object import FFOrderedDict, ObjectPointer
from tests.digest import canon, error_name, object_digest


def ptr(file_id, path_id):
	p = ObjectPointer(None, None)
	p.file_id, p.path_id = file_id, path_id
	return p


def test_canon_shapes():
	d = FFOrderedDict()
	d["a"] = 1
	d["b"] = [0.5, b"\x00\x01", ptr(0, 7), None, (True, "s")]
	assert canon(d) == {"d": [
		["a", 1],
		["b", [{"f": "0.5"}, {"b": 2, "h": canon(b"\x00\x01")["h"]}, {"p": [0, 7]}, None, [True, "s"]]],
	]}


def test_canon_collects_pointers():
	p, q = ptr(0, 7), ptr(1, 2)
	found = []
	canon({"a": [p, {"b": q}], "c": 3}, found)
	assert found == [p, q]


def test_engine_objects_digest_like_their_fields():
	d = FFOrderedDict()
	d["m_Name"] = "x"
	assert object_digest(Object(d)) == object_digest(d)


def test_digest_sees_order_and_types():
	a, b = FFOrderedDict(), FFOrderedDict()
	a["x"], a["y"] = 1, 2
	b["y"], b["x"] = 2, 1
	assert object_digest(a) != object_digest(b)
	assert object_digest([1]) != object_digest([1.0])
	assert object_digest([True]) != object_digest([1])


def test_error_name():
	assert error_name(ValueError()) == "ValueError"
	try:
		struct.unpack("<I", b"")
	except struct.error as e:
		assert error_name(e) == "struct.error"
