"""
Builders for made-up test data: type trees, serialized files (formats 6
and 7, in the layout tests/serialized.py documents) and UnityWeb archives.

They are written straight from the format description with struct.pack and
share no code with unitypack, so the library's reader and writer can't
agree on a bug and still pass.
"""

import lzma
import struct

ALIGN = 0x4000


class Node:
	def __init__(self, type, name, size=-1, children=(), is_array=False, flags=0, version=1):
		self.type = type
		self.name = name
		self.size = size
		self.children = list(children)
		self.is_array = is_array
		self.flags = flags
		self.version = version

	def pack(self, endian="<", counter=None):
		counter = counter if counter is not None else [0]
		index = counter[0]
		counter[0] += 1
		out = self.type.encode() + b"\0" + self.name.encode() + b"\0"
		out += struct.pack(endian + "iiiiiI", self.size, index, int(self.is_array),
			self.version, self.flags, len(self.children))
		for c in self.children:
			out += c.pack(endian, counter)
		return out


def int_node(name, type="int", size=4):
	return Node(type, name, size)


def float_node(name):
	return Node("float", name, 4)


def string_node(name, align=True):
	return Node("string", name, -1, [
		Node("Array", "Array", -1, [int_node("size"), Node("char", "data", 1)],
			is_array=True, flags=ALIGN if align else 0),
	])


def pptr_node(name, target="Object"):
	return Node("PPtr<%s>" % target, name, 8, [int_node("m_FileID"), int_node("m_PathID")])


def array_node(name, element):
	return Node("vector", name, -1, [
		Node("Array", "Array", -1, [int_node("size"), element], is_array=True),
	])


def pack_string(s, align=True):
	b = s.encode()
	out = struct.pack("<I", len(b)) + b
	if align:
		out += b"\0" * (-len(out) % 4)
	return out


def serialized_file(types, objects, externals=(), format=6, big_ids=None, align=8):
	"""
	types:     {class id: Node}
	objects:   [(path id, type id, class id, data bytes)]
	externals: [path, ...]
	align:     where objects start: 8 as Unity writes them, 4 as Asset.save() does
	Little-endian metadata, as in every FusionFall file seen so far. Object
	offsets in the table count from the start of the file, header included.
	"""
	if big_ids is None:
		big_ids = 1 if format >= 7 else 0
	body = b""
	table = []
	for path_id, type_id, class_id, data in objects:
		body += b"\0" * (-len(body) % align)
		table.append((path_id, len(body), len(data), type_id, class_id))
		body += data

	meta = b"\0"  # little-endian
	meta += struct.pack("<i", len(types))
	for class_id, node in types.items():
		meta += struct.pack("<i", class_id) + node.pack()
	if format >= 7:
		meta += struct.pack("<i", big_ids)
	meta += struct.pack("<I", len(table))
	for path_id, offset, size, type_id, class_id in table:
		meta += struct.pack("<q" if big_ids else "<i", path_id)
		meta += struct.pack("<IIihh", offset + 16, size, type_id, class_id, 0)
	meta += struct.pack("<I", len(externals))
	for path in externals:
		meta += b"\0" + bytes(16) + struct.pack("<i", 0) + path.encode() + b"\0"
	meta += b"\0"  # user information

	file_size = 16 + len(body) + len(meta)
	header = struct.pack(">4I", len(meta), file_size, format, 0)
	return header + body + meta


def unityweb(files, engine_version="2.5.4b5"):
	"files: [(name, bytes)] -> a single-level UnityWeb archive."
	table_size = 4 + sum(len(n) + 1 + 8 for n, _ in files)
	offset = table_size + (-table_size % 4)
	entries = []
	blob = b""
	for name, data in files:
		entries.append((name, offset, len(data)))
		blob += data + b"\0" * (-len(data) % 4)
		offset += len(data) + (-len(data) % 4)
	level = struct.pack(">I", len(files))
	for name, off, size in entries:
		level += name.encode() + b"\0" + struct.pack(">2I", off, size)
	level += b"\0" * (-len(level) % 4) + blob

	compressed = lzma.compress(level, format=lzma.FORMAT_ALONE)
	header = b"UnityWeb\0" + struct.pack(">I", 2) + b"fusion-2.x.x\0" + engine_version.encode() + b"\0"
	header_size = len(header) + 16 + 8 + 4
	header_size += -header_size % 4
	total = header_size + len(compressed)
	header += struct.pack(">4I", total, header_size, 1, 1)
	header += struct.pack(">2I", len(compressed), len(level))
	header += struct.pack(">I", total)
	header += b"\0" * (header_size - len(header))
	return header + compressed


# Sample data shared by the tests: two object layouts and files built from them

TEXT_ASSET = Node("TextAsset", "Base", children=[string_node("m_Name"), string_node("m_Script"), string_node("m_PathName")])

# a made-up class that uses one field of each kind the reader handles
THING = Node("Thing", "Base", children=[
	int_node("m_Count"),
	float_node("m_Scale"),
	Node("bool", "m_Enabled", 1, flags=ALIGN),
	string_node("m_Name"),
	pptr_node("m_Other"),
	array_node("m_Values", int_node("data", "UInt16", 2)),
])


def asset_info_node(name):
	return Node("AssetInfo", name, 16, [int_node("preloadIndex"), int_node("preloadSize"), pptr_node("asset")])


# the AssetBundle layout of the 104 client (class 142)
ASSET_BUNDLE = Node("AssetBundle", "Base", children=[
	string_node("m_Name"),
	array_node("m_PreloadTable", pptr_node("data")),
	Node("map", "m_Container", children=[
		Node("Array", "Array", children=[
			int_node("size"),
			Node("pair", "data", children=[string_node("first"), asset_info_node("second")]),
		], is_array=True),
	]),
	asset_info_node("m_MainAsset"),
])


def text_asset(name, script, path=""):
	return pack_string(name) + pack_string(script) + pack_string(path)


def thing(count, scale, enabled, name, other, values):
	data = struct.pack("<if?", count, scale, enabled) + b"\0" * 3
	data += pack_string(name)
	data += struct.pack("<ii", *other)
	return data + struct.pack("<I", len(values)) + b"".join(struct.pack("<H", v) for v in values)


def asset_bundle(name, preload, container, main=(0, 0, (0, 0))):
	"preload: [(file id, path id)]; container: [(path, preload index, preload size, (file id, path id))]"
	data = pack_string(name)
	data += struct.pack("<I", len(preload)) + b"".join(struct.pack("<ii", *p) for p in preload)
	data += struct.pack("<I", len(container))
	for path, index, size, ptr in container:
		data += pack_string(path) + struct.pack("<iiii", index, size, *ptr)
	return data + struct.pack("<iiii", main[0], main[1], *main[2])


def sample_file(format=6, align=8):
	"""
	Three objects: two TextAssets and a Thing pointing at the first one. No
	object ends in padding (unitypack drops it, see test_save_keeps_trailing_padding),
	so with align=4 this is byte for byte what Asset.save() should write.
	"""
	return serialized_file(
		{49: TEXT_ASSET, 1000: THING},
		[
			(1, 49, 49, text_asset("hello", "world")),
			(2, 1000, 1000, thing(-3, 0.5, True, "thing", (0, 1), [1, 2, 65535])),
			(5, 49, 49, text_asset("", "second", "a/bc")),
		],
		externals=["library/unity default resources"],
		format=format,
		align=align,
	)


def bundle_file():
	"An AssetBundle (path ID 1) listing one TextAsset (path ID 2), like a resourceFile's first file."
	return serialized_file(
		{142: ASSET_BUNDLE, 49: TEXT_ASSET},
		[
			(1, 142, 142, asset_bundle("bundle", [(0, 2)], [("assets/a.txt", 0, 1, (0, 2))])),
			(2, 49, 49, text_asset("a", "first")),
			(3, 49, 49, text_asset("b", "second")),
		],
	)


def ogg(seconds=2, rate=22050):
	"""
	The smallest Ogg Vorbis file tag readers accept: an identification header,
	an empty comment header and one last page whose granule position (the
	sample count) gives the length. It holds no audio.
	"""
	def page(granule, seq, packet, flags=0):
		lacing = [255] * (len(packet) // 255) + [len(packet) % 255]
		return b"OggS" + struct.pack("<BBqIIIB", 0, flags, granule, 1, seq, 0, len(lacing)) + bytes(lacing) + packet

	ident = b"\x01vorbis" + struct.pack("<IBIiiiBB", 0, 1, rate, 0, 64000, 0, 0xB8, 1)
	comment = b"\x03vorbis" + struct.pack("<I", 0) + struct.pack("<I", 0) + b"\x01"
	return page(0, 0, ident, 2) + page(0, 1, comment) + page(seconds * rate, 2, b"\0", 4)
