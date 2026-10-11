import io
import math

import pytest

from unitypack.utils import BinaryReader, BinaryWriter, BitReader

VALUES = [
	("boolean", True),
	("byte", -5),
	("ubyte", 250),
	("int16", -30000),
	("uint16", 60000),
	("int", -2 ** 31),
	("uint", 2 ** 32 - 1),
	("int64", -2 ** 63),
	("uint64", 2 ** 64 - 1),
	("float", 0.25),
	("double", math.pi),
]


@pytest.mark.parametrize("endian", ["<", ">"])
def test_round_trip(endian):
	f = io.BytesIO()
	w = BinaryWriter(f, endian)
	for kind, v in VALUES:
		getattr(w, "write_" + kind)(v)
	w.write_string("cstr")
	w.write_string("pascal", pascal=True)

	f.seek(0)
	r = BinaryReader(f, endian)
	for kind, v in VALUES:
		assert getattr(r, "read_" + kind)() == v
	assert r.read_string() == "cstr"
	assert r.read_string(6) == "pascal"


def test_endianness_on_the_wire():
	f = io.BytesIO()
	BinaryWriter(f, ">").write_uint(1)
	BinaryWriter(f, "<").write_uint(1)
	assert f.getvalue() == b"\0\0\0\1\1\0\0\0"


def test_align():
	f = io.BytesIO(bytes(16))
	r = BinaryReader(f)
	r.read(5)
	r.align()
	assert r.tell() == 8
	r.align()
	assert r.tell() == 8


def test_unterminated_string():
	with pytest.raises(ValueError):
		BinaryReader(io.BytesIO(b"abc")).read_string()


def test_bit_reader():
	# 3-bit items 1..7 then 0, packed least significant bit first
	items = [1, 2, 3, 4, 5, 6, 7, 0]
	n = sum(v << (3 * i) for i, v in enumerate(items))
	r = BitReader(n.to_bytes(3, "little"), 3)
	assert [r.read() for _ in items] == items


def test_bit_reader_bytes():
	r = BitReader(b"\x01\xff\x80", 8)
	assert [r.read() for _ in range(3)] == [1, 255, 128]
