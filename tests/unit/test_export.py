"""
OBJ export: unitypack/export.py (OBJMesh, MeshData, CompressedMeshData,
strip_to_triangles) on meshes built in memory, the way ObjectInfo.read()
returns them.

Conventions the exporter applies, checked below: x is negated (Unity is
left-handed, OBJ right-handed), faces are written in reverse order to keep
them facing outwards after that mirror, and indices become 1-based. UVs are
written as stored: OBJ and Unity both put v = 0 at the bottom of the image,
and the bin/ scripts save textures upright.
"""

import math
import struct

import pytest

from unitypack.engine.mesh import Mesh, SubMesh
from unitypack.export import OBJMesh
from unitypack.object import FFOrderedDict


def fdict(**kw):
	d = FFOrderedDict()
	d.update(kw)
	return d


def vec(*xs):
	return fdict(**dict(zip("xyz", xs)))


def submesh(first_byte, index_count, strip=False):
	return SubMesh(fdict(firstByte=first_byte, indexCount=index_count, isTriStrip=int(strip), triangleCount=0))


def mesh(vertices, normals, uvs, indices, submeshes=None, strip=False, name="m"):
	"An uncompressed mesh. indices: one flat list of 16-bit indices."
	return Mesh(fdict(
		m_Name=name,
		m_MeshCompression=0,
		m_Vertices=[vec(*v) for v in vertices],
		m_Normals=[vec(*n) for n in normals],
		m_UV=[vec(*t) for t in uvs],
		m_IndexBuffer=struct.pack("<%dH" % len(indices), *indices),
		m_SubMeshes=submeshes or [submesh(0, len(indices), strip)],
	))


def parse_obj(text):
	"OBJ text -> {'v': [...], 'vn': [...], 'vt': [...], 'f': [...], 'usemtl': [...]}"
	out = {"v": [], "vn": [], "vt": [], "f": [], "usemtl": []}
	for line in text.splitlines():
		parts = line.split()
		if not parts or parts[0] not in out:
			continue
		if parts[0] == "f":
			out["f"].append(tuple(int(p.split("/")[0]) for p in parts[1:]))
		elif parts[0] == "usemtl":
			out["usemtl"].append(parts[1])
		else:
			out[parts[0]].append(tuple(float(p) for p in parts[1:]))
	return out


QUAD = dict(
	vertices=[(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)],
	normals=[(0, 0, 1)] * 4,
	uvs=[(0, 0), (1, 0), (1, 1), (0, 0.25)],
)


def test_uncompressed_quad():
	obj = parse_obj(OBJMesh(mesh(indices=[0, 1, 2, 0, 2, 3], **QUAD)).export())
	assert obj["v"] == [(-0.0, 0, 0), (-1, 0, 0), (-1, 1, 0), (-0.0, 1, 0)]  # x mirrored
	assert obj["vn"] == [(-0.0, 0, 1)] * 4
	assert obj["vt"] == [(0, 0), (1, 0), (1, 1), (0, 0.25)]  # as stored
	assert obj["f"] == [(3, 2, 1), (4, 3, 1)]  # reversed, 1-based
	assert obj["usemtl"] == ["m"]


def test_triangle_strip():
	# a strip 0 1 2 3 is the triangles (0 1 2) and (3 2 1): every other one flips
	obj = parse_obj(OBJMesh(mesh(indices=[0, 1, 2, 3], strip=True, **QUAD)).export())
	assert obj["f"] == [(3, 2, 1), (2, 3, 4)]


def test_triangle_strip_skips_degenerate_triangles():
	# repeated indices join strips; the zero-area triangles they make are dropped
	obj = parse_obj(OBJMesh(mesh(indices=[0, 1, 2, 2, 3], strip=True, **QUAD)).export())
	assert obj["f"] == [(3, 2, 1)]


def test_submeshes():
	subs = [submesh(0, 3), submesh(6, 3)]  # firstByte counts bytes: 3 indices of 2 bytes
	obj = OBJMesh(mesh(indices=[0, 1, 2, 0, 2, 3], submeshes=subs, **QUAD)).export()
	assert parse_obj(obj)["usemtl"] == ["m_0", "m_1"]
	assert parse_obj(obj)["f"] == [(3, 2, 1), (4, 3, 1)]


# Compressed meshes: every value is an N-bit integer in a PackedBitVector,
# scaled to a float with start + item * range / (2^N - 1). Every mesh in the
# FusionFall builds checked so far is stored this way.

def packed(items, bits, start=None, range_=None):
	"Pack integers least significant bit first, as BitReader reads them."
	n = sum(v << (bits * i) for i, v in enumerate(items))
	d = fdict(m_NumItems=len(items), m_BitSize=bits, m_Data=n.to_bytes((len(items) * bits + 7) // 8, "little"))
	if start is not None:
		d["m_Start"], d["m_Range"] = start, range_
	return d


def compressed_mesh(normal_items=(0,) * 6, signs=(1,) * 3, triangles=(0, 1, 2), submeshes=None):
	return Mesh(fdict(
		m_Name="c",
		m_MeshCompression=2,
		m_SubMeshes=submeshes or [submesh(0, len(triangles))],
		m_CompressedMesh=fdict(
			# 8 bits over [-1, 1]: items 0, 255 and 51 are -1, 1 and -0.6
			m_Vertices=packed([0, 0, 0, 255, 255, 255, 51, 51, 51], 8, -1.0, 2.0),
			# 8 bits over [0, 1]: 0, 1 and 0.6
			m_UV=packed([0, 0, 255, 255, 153, 153], 8, 0.0, 1.0),
			# 4 bits over [0, 1]: items 0, 9 and 15 are 0, 0.6 and 1; two per vertex (x, y)
			m_Normals=packed(normal_items, 4, 0.0, 1.0),
			m_NormalSigns=packed(signs, 1),  # 1: z positive, 0: z negative
			m_Triangles=packed(list(triangles), 2),
			# not skinned
			m_Weights=packed([], 5),
			m_BoneIndices=packed([], 6),
		),
	))


def approx(rows):
	return [tuple(pytest.approx(x, abs=1e-6) for x in r) for r in rows]


def test_compressed_triangle_strip():
	# only the strip's own triangles: (0 1 2), then (1 2 0) flipped to (0 2 1)
	obj = parse_obj(OBJMesh(compressed_mesh(triangles=[0, 1, 2, 0], submeshes=[submesh(0, 4, strip=True)])).export())
	assert obj["f"] == [(3, 2, 1), (2, 3, 1)]


def test_compressed_submeshes():
	# one index list for all submeshes; firstByte counts 2 bytes per index
	obj = parse_obj(OBJMesh(compressed_mesh(triangles=[0, 1, 2, 2, 1, 0], submeshes=[submesh(0, 3), submesh(6, 3)])).export())
	assert obj["usemtl"] == ["c_0", "c_1"]
	assert obj["f"] == [(3, 2, 1), (1, 2, 3)]


def test_compressed_positions_uvs_triangles():
	obj = parse_obj(OBJMesh(compressed_mesh([0, 0, 0, 0, 0, 0], [1, 1, 1])).export())
	assert obj["v"] == approx([(1, -1, -1), (-1, 1, 1), (0.6, -0.6, -0.6)])
	assert obj["vt"] == approx([(0, 0), (1, 1), (0.6, 0.6)])
	assert obj["f"] == [(3, 2, 1)]


def test_compressed_normals_are_unit_length():
	obj = parse_obj(OBJMesh(compressed_mesh([9, 0, 0, 9, 0, 0], [1, 0, 1])).export())
	assert obj["vn"] == approx([(-0.6, 0, 0.8), (-0.0, 0.6, -0.8), (-0.0, 0, 1)])


def test_compressed_normal_signs():
	vn = parse_obj(OBJMesh(compressed_mesh([9, 0, 0, 9, 0, 0], [1, 0, 1])).export())["vn"]
	assert [math.copysign(1, n[2]) for n in vn] == [1, -1, 1]
	assert [n[:2] for n in vn] == approx([(-0.6, 0), (0, 0.6), (0, 0)])


def test_compressed_normal_outside_unit_circle():
	# x = y = 1 after quantizing: z is 0 and (x, y) is scaled back to length 1
	vn = parse_obj(OBJMesh(compressed_mesh([15, 15, 0, 0, 0, 0], [1, 1, 1])).export())["vn"]
	h = math.sqrt(0.5)
	assert vn[0] == approx([(-h, h, 0)])[0]


# A vector with m_BitSize 0 has no data at all: every item is 0, so every
# float is m_Start. Unity writes this when all values are equal, for example
# the bone indices of a mesh skinned to a single bone.

def test_compressed_zero_bit_normal_signs():
	m = compressed_mesh([9, 0, 0, 9, 0, 0])
	m._obj["m_CompressedMesh"]["m_NormalSigns"] = packed([0, 0, 0], 0)
	vn = parse_obj(OBJMesh(m).export())["vn"]
	assert vn == approx([(-0.6, 0, -0.8), (-0.0, 0.6, -0.8), (-0.0, 0, -1)])


def test_compressed_zero_bit_floats():
	m = compressed_mesh()
	m._obj["m_CompressedMesh"]["m_UV"] = packed([0] * 6, 0, 0.5, 0.0)
	assert parse_obj(OBJMesh(m).export())["vt"] == approx([(0.5, 0.5)] * 3)


# Skin weights. A compressed mesh stores them in m_Weights, 5 bits each, out
# of 31, vertex after vertex: a vertex ends once its weights reach 31, or
# after three, when a fourth weight (31 minus the sum) is implied. Every
# weight, the implied one included, has its bone in m_BoneIndices.

def skinned_mesh(weights, bones, bone_bits=6):
	m = compressed_mesh()
	cm = m._obj["m_CompressedMesh"]
	cm["m_Weights"] = packed(weights, 5)
	cm["m_BoneIndices"] = packed(bones, bone_bits)
	return m


def skin(m):
	return OBJMesh(m).mesh_data.skin


def test_skin_weights():
	# one bone; two bones; three bones and an implied fourth
	s = skin(skinned_mesh([31, 20, 11, 10, 10, 1], [5, 1, 2, 3, 4, 63, 6]))
	assert s == [
		[(5, 1.0)],
		[(1, 20 / 31), (2, 11 / 31)],
		[(3, 10 / 31), (4, 10 / 31), (63, 1 / 31), (6, 10 / 31)],
	]


def test_skin_three_weights_reaching_31_imply_nothing():
	s = skin(skinned_mesh([31, 31, 10, 10, 11], [0, 0, 1, 2, 3]))
	assert s[2] == [(1, 10 / 31), (2, 10 / 31), (3, 11 / 31)]


def test_skin_zero_bit_bone_indices():
	# a mesh skinned to a single bone stores its bone indices in 0 bits
	assert skin(skinned_mesh([31, 31, 31], [0, 0, 0], bone_bits=0)) == [[(0, 1.0)]] * 3


def test_unskinned_mesh_has_no_skin():
	assert skin(compressed_mesh()) == []


@pytest.mark.parametrize("weights,bones,error", [
	([31, 31], [0, 0], "skin weights for 2 vertices, but 3 vertices"),
	([31, 31, 31], [0, 0], "fewer bone indices than skin weights"),
	([31, 31, 31], [0, 0, 0, 0], "don't end together"),
	([31, 31, 20], [0, 0, 0], "don't end together"),
])
def test_skin_mismatch(weights, bones, error):
	with pytest.raises(ValueError, match=error):
		skin(skinned_mesh(weights, bones))


def test_uncompressed_skin():
	m = mesh(**QUAD, indices=[0, 1, 2])
	m._obj["m_Skin"] = [
		fdict(**{"weight[0]": 1.0, "weight[1]": 0.0, "weight[2]": 0.0, "weight[3]": 0.0,
			"boneIndex[0]": 2, "boneIndex[1]": 0, "boneIndex[2]": 0, "boneIndex[3]": 0}),
		fdict(**{"weight[0]": 0.5, "weight[1]": 0.25, "weight[2]": 0.0, "weight[3]": 0.25,
			"boneIndex[0]": 1, "boneIndex[1]": 3, "boneIndex[2]": 0, "boneIndex[3]": 7}),
	]
	assert skin(m) == [[(2, 1.0)], [(1, 0.5), (3, 0.25), (7, 0.25)]]


def test_compressed_mesh_without_uvs():
	# a count of texture coordinates, 0 bits and an infinite start: none
	m = compressed_mesh()
	m._obj["m_CompressedMesh"]["m_UV"] = packed([0] * 6, 0, math.inf, -math.inf)
	obj = parse_obj(OBJMesh(m).export())
	assert obj["vt"] == []
	assert len(obj["v"]) == 3
