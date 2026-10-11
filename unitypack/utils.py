import struct
from os import SEEK_CUR


def lz4_decompress(data, size):
	try:
		from lz4.block import decompress
	except ImportError:
		raise RuntimeError("python-lz4 >= 0.9 is required to read UnityFS files")

	return decompress(data, size)


def extract_audioclip_samples(d) -> dict:
	"""
	Extract all the sample data from an AudioClip and
	convert it from FSB5 if needed.
	"""
	ret = {}

	if not d.data:
		# eg. StreamedResource not available
		return {}

	try:
		from fsb5 import FSB5
	except ImportError as e:
		raise RuntimeError("python-fsb5 is required to extract AudioClip")

	af = FSB5(d.data)
	for i, sample in enumerate(af.samples):
		if i > 0:
			filename = "%s-%i.%s" % (d.name, i, af.get_sample_extension())
		else:
			filename = "%s.%s" % (d.name, af.get_sample_extension())
		try:
			sample = af.rebuild_sample(sample)
		except ValueError as e:
			print("WARNING: Could not extract %r (%s)" % (d, e))
			continue
		ret[filename] = sample

	return ret


class BitReader:
        def __init__(self, buf, size):
                self.buf = buf
                self.size = size

                self.bitcount = 8
                self.i = 0
                self.byte = self.nextbyte()

        def nextbyte(self):
                if self.i >= len(self.buf):
                        return 0
                b = self.buf[self.i]
                self.i += 1
                return b

        def read(self):
                if self.size == 8 and self.bitcount == 0:
                        return self.nextbyte()

                while self.bitcount < self.size:
                        newbyte = self.nextbyte()
                        # XXX: not sure why disunity has a special case for -1 here
                        self.byte |= newbyte << self.bitcount
                        self.bitcount += 8

                ret = self.byte & ((1 << self.size) - 1)
                self.byte >>= self.size
                self.bitcount -= self.size
                return ret


def unpack_ints(pbv):
	"""
	The items of a PackedBitVector (m_NumItems, m_BitSize, m_Data): integers
	of m_BitSize bits each, least significant bit first. With m_BitSize 0
	there is no data and every item is 0.
	"""
	if pbv['m_NumItems'] == 0:
		return []
	if pbv['m_BitSize'] == 0:
		return [0] * pbv['m_NumItems']

	reader = BitReader(pbv['m_Data'], pbv['m_BitSize'])
	return [reader.read() for i in range(pbv['m_NumItems'])]


def unpack_floats(pbv):
	"""
	The items of a PackedBitVector as floats spread evenly over
	[m_Start, m_Start + m_Range]: item * m_Range / (2^m_BitSize - 1) + m_Start.
	"""
	if pbv['m_NumItems'] == 0:
		return []
	if pbv['m_BitSize'] == 0:
		return [float(pbv['m_Start'])] * pbv['m_NumItems']

	scale = pbv['m_Range'] / ((1 << pbv['m_BitSize']) - 1)
	return [float(i * scale + pbv['m_Start']) for i in unpack_ints(pbv)]


class NaN32(float):
	"""
	A 32-bit NaN read from a file, with its bit pattern (bits), so that it is
	written back unchanged: going through a Python float would turn a
	signaling NaN into a quiet one.
	"""

	def __new__(cls, data, endian="<"):
		self = super().__new__(cls, "nan")
		self.bits = int.from_bytes(data, "little" if endian == "<" else "big")
		return self


class BinaryReader:
	def __init__(self, buf, endian="<"):
		self.buf = buf
		self.endian = endian

	def align(self):
		old = self.tell()
		new = (old + 3) & -4
		if new > old:
			self.seek(new - old, SEEK_CUR)

	def read(self, *args):
		return self.buf.read(*args)

	def seek(self, *args):
		return self.buf.seek(*args)

	def tell(self):
		return self.buf.tell()

	def read_string(self, size=None, encoding="utf-8"):
		if size is None:
			ret = self.read_cstring()
		else:
			ret = struct.unpack(self.endian + "%is" % (size), self.read(size))[0]
		try:
			return ret.decode(encoding)
		except UnicodeDecodeError:
			return ret

	def read_cstring(self) -> bytes:
		ret = []
		c = b""
		while c != b"\0":
			ret.append(c)
			c = self.read(1)
			if not c:
				raise ValueError("Unterminated string: %r" % (ret))
		return b"".join(ret)

	def read_boolean(self) -> bool:
		return bool(struct.unpack(self.endian + "b", self.read(1))[0])

	def read_byte(self) -> int:
		return struct.unpack(self.endian + "b", self.read(1))[0]

	def read_ubyte(self) -> int:
		return struct.unpack(self.endian + "B", self.read(1))[0]

	def read_int16(self) -> int:
		return struct.unpack(self.endian + "h", self.read(2))[0]

	def read_uint16(self) -> int:
		return struct.unpack(self.endian + "H", self.read(2))[0]

	def read_int(self) -> int:
		return struct.unpack(self.endian + "i", self.read(4))[0]

	def read_uint(self) -> int:
		return struct.unpack(self.endian + "I", self.read(4))[0]

	def read_float(self) -> float:
		data = self.read(4)
		ret = struct.unpack(self.endian + "f", data)[0]
		if ret != ret:
			ret = NaN32(data, self.endian)
		return ret

	def read_double(self) -> float:
		return struct.unpack(self.endian + "d", self.read(8))[0]

	def read_int64(self) -> int:
		return struct.unpack(self.endian + "q", self.read(8))[0]

	def read_uint64(self) -> int:
		return struct.unpack(self.endian + "Q", self.read(8))[0]


class BinaryWriter:
	def __init__(self, buf, endian="<"):
		self.buf = buf
		self.endian = endian

	def align(self, n=4):
		# write the padding: a seek leaves no bytes behind if nothing follows
		old = self.tell()
		new = (old + n - 1) & -n
		if new > old:
			self.write(b"\0" * (new - old))

	def write(self, *args):
		return self.buf.write(*args)

	def seek(self, *args):
		return self.buf.seek(*args)

	def tell(self):
		return self.buf.tell()

	def write_string(self, _val, pascal=False, encoding="utf-8"):
		# Passing sizes is awkward if the string actually contains Unicode characters,
		# so instead we just explicitly ask for Pascal strings instead of passing a size.
		val = _val.encode(encoding)
		if pascal is False:
			return self.write_cstring(val)
		else:
			return self.write(struct.pack(self.endian + "%is" % (len(val)), val))

	def write_cstring(self, val):
		self.write(val)
		self.write(b'\0')
		return len(val) + 1

	def write_boolean(self, val):
		return self.write_byte(val)

	def write_byte(self, val):
		return self.write(struct.pack(self.endian + "b", val))

	def write_ubyte(self, val):
		return self.write(struct.pack(self.endian + "B", val))

	def write_int16(self, val):
		return self.write(struct.pack(self.endian + "h", val))

	def write_uint16(self, val):
		return self.write(struct.pack(self.endian + "H", val))

	def write_int(self, val):
		return self.write(struct.pack(self.endian + "i", val))

	def write_uint(self, val):
		return self.write(struct.pack(self.endian + "I", val))

	def write_float(self, val):
		if isinstance(val, NaN32):
			return self.write(val.bits.to_bytes(4, "little" if self.endian == "<" else "big"))
		return self.write(struct.pack(self.endian + "f", val))

	def write_double(self, val):
		return self.write(struct.pack(self.endian + "d", val))

	def write_int64(self, val):
		return self.write(struct.pack(self.endian + "q", val))

	def write_uint64(self, val):
		return self.write(struct.pack(self.endian + "Q", val))
