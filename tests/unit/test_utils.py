import io
import math

import pytest

from unitypack.utils import BinaryReader, BinaryWriter, BitReader, unpack_floats, unpack_ints

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


def test_writer_align_writes_padding():
	f = io.BytesIO()
	w = BinaryWriter(f)
	w.write(b"abcde")
	w.align()
	w.align()
	assert f.getvalue() == b"abcde\0\0\0"
	w.align(8)
	assert f.getvalue() == b"abcde\0\0\0"
	w.write(b"x")
	w.align(8)
	assert f.getvalue() == b"abcde\0\0\0x" + bytes(7)


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


def packed_bit_vector(items, bits, **kw):
	n = sum(v << (bits * i) for i, v in enumerate(items))
	return dict(m_NumItems=len(items), m_BitSize=bits, m_Data=n.to_bytes((len(items) * bits + 7) // 8, "little"), **kw)


def test_unpack_ints():
	assert unpack_ints(packed_bit_vector([5, 0, 31, 17], 5)) == [5, 0, 31, 17]
	assert unpack_ints(packed_bit_vector([], 5)) == []


def test_unpack_ints_zero_bits():
	# no data at all: every item is 0
	assert unpack_ints(dict(m_NumItems=3, m_BitSize=0, m_Data=b"")) == [0, 0, 0]


def test_unpack_floats():
	# 2 bits over [-1, 2]: 0..3 map to -1, 0, 1, 2
	pbv = packed_bit_vector([0, 1, 2, 3], 2, m_Start=-1.0, m_Range=3.0)
	assert unpack_floats(pbv) == pytest.approx([-1, 0, 1, 2])


def test_unpack_floats_zero_bits():
	assert unpack_floats(dict(m_NumItems=2, m_BitSize=0, m_Data=b"", m_Start=0.25, m_Range=0.0)) == [0.25, 0.25]
