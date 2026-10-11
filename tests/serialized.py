"""
A minimal, independent parser for the serialized files of FusionFall's
Unity 2.5 builds (formats 5 to 7), used by the survey and the tests to
check unitypack's reader against a second implementation.

Layout as observed in all four target builds:

  header (big-endian):  u32 metadata size, u32 file size, u32 format, u32 data offset
  object data
  metadata, the last <metadata size> bytes of the file:
    u8   endianness (0 = little); everything below uses it
    i32  type count, then per type: i32 class id, type tree
    i32  "big ID" flag                       (format 7 only)
    u32  object count, then per object:
         i32 path id (i64 with big IDs), u32 data offset, u32 size,
         i32 type id, i16 class id, i16 is-destroyed
    u32  external count, then per external:
         string (empty), 16-byte GUID, i32 type, string path
    string user information (empty)

  type tree node: string type, string name, i32 size, i32 index,
                  i32 is-array, i32 version, i32 flags, u32 child count, children

Unity's own format 7 puts a version string after the endianness byte.
FusionFall's format-7 files have none.
"""

import struct

FLAG_ALIGN = 0x4000


class SerializedFile:
	def __init__(self):
		self.format = None
		self.metadata_size = 0
		self.file_size = 0
		self.data_offset = 0
		self.endian = "<"
		self.types = {}  # class id -> root TypeNode
		self.big_ids = None
		self.objects = []  # dicts: path_id, offset, size, type_id, class_id, destroyed
		self.externals = []  # dicts: guid, type, path
		self.user_info = ""
		self.end = 0  # where parsing stopped; == file_size when the layout is right


class TypeNode:
	__slots__ = ("type", "name", "size", "index", "is_array", "version", "flags", "children")

	def __init__(self):
		self.children = []

	def walk(self):
		yield self
		for c in self.children:
			yield from c.walk()


class _Reader:
	def __init__(self, data, pos, endian):
		self.data = data
		self.pos = pos
		self.endian = endian

	def unpack(self, fmt):
		v = struct.unpack_from(self.endian + fmt, self.data, self.pos)
		self.pos += struct.calcsize(fmt)
		return v

	def i32(self):
		return self.unpack("i")[0]

	def u32(self):
		return self.unpack("I")[0]

	def cstring(self):
		end = self.data.index(b"\0", self.pos)
		s = self.data[self.pos:end].decode("utf-8", "replace")
		self.pos = end + 1
		return s

	def tree(self, depth=0):
		if depth > 64:
			raise ValueError("type tree too deep")
		n = TypeNode()
		n.type = self.cstring()
		n.name = self.cstring()
		n.size, n.index, n.is_array, n.version, n.flags, count = self.unpack("iiiiiI")
		if count > 10000:
			raise ValueError("implausible child count %d" % count)
		n.children = [self.tree(depth + 1) for _ in range(count)]
		return n


def looks_serialized(data):
	"Cheap check: header sizes agree with the data and the format is plausible."
	if len(data) < 20:
		return False
	metadata_size, file_size, fmt, data_offset = struct.unpack_from(">4I", data, 0)
	return file_size == len(data) and 0 < metadata_size < file_size and 5 <= fmt <= 22


def parse(data):
	sf = SerializedFile()
	sf.metadata_size, sf.file_size, sf.format, sf.data_offset = struct.unpack_from(">4I", data, 0)
	if sf.format > 7:
		raise NotImplementedError("format %d" % sf.format)

	pos = sf.file_size - sf.metadata_size
	sf.endian = "<" if data[pos] == 0 else ">"
	r = _Reader(data, pos + 1, sf.endian)

	for _ in range(r.i32()):
		class_id = r.i32()
		sf.types[class_id] = r.tree()

	if sf.format >= 7:
		sf.big_ids = r.i32()

	id_fmt = "q" if sf.big_ids else "i"
	for _ in range(r.u32()):
		(path_id,) = r.unpack(id_fmt)
		offset, size, type_id, class_id, destroyed = r.unpack("IIihh")
		sf.objects.append({
			"path_id": path_id, "offset": offset, "size": size,
			"type_id": type_id, "class_id": class_id, "destroyed": destroyed,
		})

	for _ in range(r.u32()):
		r.cstring()
		guid = r.data[r.pos:r.pos + 16]
		r.pos += 16
		typ = r.i32()
		sf.externals.append({"guid": guid.hex(), "type": typ, "path": r.cstring()})

	if sf.format >= 5:
		sf.user_info = r.cstring()

	sf.end = r.pos
	return sf
