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
	align:     where objects start: 8 as Unity writes them, 4 as older UnityPackFF versions did
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
	data += struct.pack("<iI", *other)
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
	Three objects: two TextAssets and a Thing pointing at the first one.
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


# Format-7 resource files number objects with 48-bit IDs. A pointer has only
# 32 bits for the ID, so the top 16 bits go in the top half of m_FileID:
# m_FileID = high 16 bits of the ID << 16 | file index.
BIG_A, BIG_B = 0x1234_8000_0001, 0x5678_8000_0001  # same low 32 bits


def big_pointer(file_index, path_id):
	return ((path_id >> 32) << 16 | file_index, path_id & 0xFFFFFFFF)


def big_id_file():
	"Format 7: two TextAssets whose IDs share the low 32 bits, and a Thing (ID 1) pointing at the second."
	return serialized_file(
		{49: TEXT_ASSET, 1000: THING},
		[
			(1, 1000, 1000, thing(0, 0.0, False, "", big_pointer(0, BIG_B), [])),
			(BIG_A, 49, 49, text_asset("a", "first")),
			(BIG_B, 49, 49, text_asset("b", "second")),
		],
		format=7,
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


# TerrainData (class 156) with its heightmap: the 104 client's Heightmap
# layout; the splat and detail databases are left out of the tree
HEIGHTMAP = Node("Heightmap", "m_Heightmap", children=[
	Node("vector", "m_Heights", children=[
		Node("Array", "Array", children=[int_node("size"), int_node("data", "SInt16", 2)], is_array=True),
	], flags=ALIGN),
	Node("vector", "m_Shifts", children=[
		Node("Array", "Array", children=[
			int_node("size"),
			Node("Shift", "data", 6, [int_node("x", "UInt16", 2), int_node("y", "UInt16", 2), int_node("flags", "UInt16", 2)]),
		], is_array=True),
	], flags=ALIGN),
	array_node("m_PrecomputedError", float_node("data")),
	array_node("m_MinMaxPatchHeights", float_node("data")),
	pptr_node("m_DefaultPhysicMaterial", "PhysicMaterial"),
	int_node("m_Width"),
	int_node("m_Height"),
	int_node("m_Levels"),
	Node("Vector3f", "m_Scale", 12, [float_node("x"), float_node("y"), float_node("z")]),
])

TERRAIN_DATA = Node("TerrainData", "Base", children=[
	string_node("m_Name"),
	HEIGHTMAP,
	pptr_node("m_Lightmap", "Texture2D"),
])


def vector3_node(name):
	return Node("Vector3f", name, 12, [float_node("x"), float_node("y"), float_node("z")])


def color_node(name):
	return Node("ColorRGBA", name, 16, [float_node(c) for c in "rgba"])


def bytes_node(name):
	return Node("vector", name, children=[
		Node("Array", "Array", children=[int_node("size"), int_node("data", "UInt8", 1)], is_array=True, flags=ALIGN),
	])


# the 104 client's DetailDatabase (terrain details: grass), trees included
DETAIL_DATABASE = Node("DetailDatabase", "m_DetailDatabase", children=[
	array_node("m_Patches", Node("DetailPatch", "data", children=[
		Node("AABB", "bounds", 24, [vector3_node("m_Center"), vector3_node("m_Extent")]),
		bytes_node("layerIndices"),
		bytes_node("numberOfObjects"),
	])),
	array_node("m_DetailPrototypes", Node("DetailPrototype", "data", 88, [
		pptr_node("prototype", "GameObject"),
		pptr_node("prototypeTexture", "Texture2D"),
		*[float_node(n) for n in ("minWidth", "maxWidth", "minHeight", "maxHeight", "noiseSpread", "bendFactor")],
		color_node("healthyColor"),
		color_node("dryColor"),
		int_node("grayscaleLighting"),
		float_node("lightmapFactor"),
		int_node("renderMode"),
		int_node("usePrototypeMesh"),
	], version=2)),
	int_node("m_PatchCount"),
	int_node("m_PatchSamples"),
	array_node("m_RandomRotations", vector3_node("data")),
	color_node("m_WavingGrassTint"),
	float_node("m_WavingGrassStrength"),
	float_node("m_WavingGrassAmount"),
	float_node("m_WavingGrassSpeed"),
	array_node("m_TreeInstances", Node("TreeInstance", "data", 32, [
		vector3_node("position"), float_node("widthScale"), float_node("heightScale"),
		Node("ColorRGBA", "color", 4, [int_node("rgba", "UInt32")]),
		Node("ColorRGBA", "lightmapColor", 4, [int_node("rgba", "UInt32")]),
		int_node("index"),
	])),
	array_node("m_TreePrototypes", Node("TreePrototype", "data", 12, [pptr_node("prefab", "GameObject"), float_node("bendFactor")])),
	array_node("m_PreloadTextureAtlasData", pptr_node("data", "Texture2D")),
])

TERRAIN_DATA_WITH_DETAILS = Node("TerrainData", "Base", children=[
	string_node("m_Name"),
	DETAIL_DATABASE,
	HEIGHTMAP,
	pptr_node("m_Lightmap", "Texture2D"),
])


def detail_prototype(texture=(0, 0), prototype=(0, 0), width=(1.0, 2.0), height=(1.0, 0.5), render_mode=0, use_mesh=0):
	"One DetailPrototype: pointers as (file ID, path ID); fixed noise, bend, colours and lighting"
	return (struct.pack("<ii", *prototype) + struct.pack("<ii", *texture)
		+ struct.pack("<6f", width[0], width[1], height[0], height[1], 0.25, 0.1)
		+ struct.pack("<4f", 0.25, 1.0, 0.125, 1.0) + struct.pack("<4f", 0.75, 0.5, 0.0, 1.0)
		+ struct.pack("<ifii", 1, 0.5, render_mode, use_mesh))


def detail_database(patches, prototypes, count, samples):
	"""
	patches: count * count [(layer indices, counts)], z-major, each with
	len(layer indices) * samples^2 counts; prototypes: detail_prototype()s
	"""
	def byte_vector(b):
		b = bytes(b)
		return struct.pack("<I", len(b)) + b + b"\0" * (-len(b) % 4)

	data = struct.pack("<I", len(patches))
	for layers, counts in patches:
		data += struct.pack("<6f", 1e-30, 2e-30, 3e-30, 4e-30, 5e-30, 6e-30)  # as junk as the real ones
		data += byte_vector(layers) + byte_vector(counts)
	data += struct.pack("<I", len(prototypes)) + b"".join(prototypes)
	data += struct.pack("<ii", count, samples)
	data += struct.pack("<I", 0)  # m_RandomRotations
	data += struct.pack("<4f", 1.0, 0.875, 0.75, 1.0) + struct.pack("<3f", 0.5, 0.25, 0.125)
	data += struct.pack("<II", 0, 0)  # no trees
	data += struct.pack("<I", 0)  # m_PreloadTextureAtlasData
	return data


def terrain_data(heights, shifts, error, minmax, size, levels, scale=(4.0, 600.0, 4.0), name="terrain", details=None):
	"heights: x-major, size * size values; shifts: [(x, z, flags)]; details: detail_database() bytes"
	def aligned(b):
		return b + b"\0" * (-len(b) % 4)

	data = pack_string(name)
	if details is not None:
		data += details
	data += aligned(struct.pack("<I", len(heights)) + b"".join(struct.pack("<h", h) for h in heights))
	data += aligned(struct.pack("<I", len(shifts)) + b"".join(struct.pack("<HHH", *s) for s in shifts))
	data += struct.pack("<I", len(error)) + b"".join(struct.pack("<f", e) for e in error)
	data += struct.pack("<I", len(minmax)) + b"".join(struct.pack("<f", m) for m in minmax)
	data += struct.pack("<ii", 0, 0)
	data += struct.pack("<iii", size, size, levels)
	data += struct.pack("<fff", *scale)
	return data + struct.pack("<ii", 0, 0)


def terrain_file(heights, shifts=(), size=33, levels=1, error=None, minmax=None, details=None):
	"""
	A file holding one TerrainData (path ID 1); its level-of-detail arrays
	default to the right length, all 0. With details (detail_database()
	bytes), its type tree has the detail database
	"""
	patches = sum(4 ** level for level in range(levels + 1))
	error = error if error is not None else [0.0] * patches
	minmax = minmax if minmax is not None else [0.0] * (2 * patches)
	tree = TERRAIN_DATA if details is None else TERRAIN_DATA_WITH_DETAILS
	data = terrain_data(heights, list(shifts), error, minmax, size, levels, details=details)
	return serialized_file({156: tree}, [(1, 156, 156, data)])
