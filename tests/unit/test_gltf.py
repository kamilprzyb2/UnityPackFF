"""
glTF export: unitypack/gltf.py on meshes, materials, textures and GameObjects
built in memory, read back with tests/glb.py.

Conventions checked below: everything is mirrored along x (Unity is
left-handed, glTF right-handed), so positions and normals get -x, rotations
(x, -y, -z, w) and triangles the other winding; texture coordinates become
(u, 1 - v), since glTF puts v = 0 at the top of the image; textures are
embedded as upright PNGs.
"""

import io
import math

import pytest
from PIL import Image

from tests.glb import GLB
from tests.unit.test_export import QUAD, compressed_mesh, fdict, mesh, submesh, vec
from unitypack.engine.component import Transform
from unitypack.engine.object import GameObject
from unitypack.engine.texture import Material, Texture2D, TextureFormat
from unitypack.gltf import GLTFWriter, export_gameobject, export_mesh, mirror_matrix


def color(r, g, b, a=1.0):
	return fdict(r=r, g=g, b=b, a=a)


def texture(name="tex", pixels=((255, 0, 0, 255), (0, 0, 255, 255)), wrap=0):
	"RGBA32, 1 x 2, stored bottom row first: red at the bottom, blue at the top"
	return Texture2D(fdict(
		m_Name=name, m_Width=1, m_Height=2, m_TextureFormat=int(TextureFormat.RGBA32),
		m_TextureSettings=fdict(m_WrapMode=wrap), **{"image data": bytes(c for p in pixels for c in p)},
	))


class Pointer:
	"Stands in for an ObjectPointer: resolve() returns the target, or raises KeyError if there's none"

	def __init__(self, target, path_id):
		self.target = target
		self.file_id = 0
		self.path_id = path_id
		self.source_asset = type("Asset", (), {"name": "test"})()

	def __bool__(self):
		return self.path_id != 0

	def resolve(self):
		if self.target is None:
			raise KeyError("No such asset: 'elsewhere'")
		return self.target


def material(name="mat", tex=None, rgba=(0.5, 0.25, 1.0, 1.0)):
	texenvs = []
	if tex is not None:
		texenvs.append((fdict(name="_MainTex"), fdict(m_Texture=tex, m_Scale=fdict(x=1.0, y=1.0), m_Offset=fdict(x=0.0, y=0.0))))
	return Material(fdict(
		m_Name=name,
		m_Shader=Pointer(None, 0),
		m_SavedProperties=fdict(
			m_TexEnvs=texenvs,
			m_Floats=[(fdict(name="_Outline"), 0.002)],
			m_Colors=[(fdict(name="_Color"), color(*rgba)), (fdict(name="_Emission"), color(2.0, 0.0, 0.0))],
		),
	))


def approx(rows):
	return [tuple(pytest.approx(x, abs=1e-6) for x in r) for r in rows]


def test_glb_layout():
	g = GLB(export_mesh(mesh(**QUAD, indices=[0, 1, 2])))
	assert g["asset"]["version"] == "2.0"
	assert g["scenes"][g["scene"]]["nodes"] == [0]
	assert g["nodes"][0] == {"name": "m", "mesh": 0}


def test_mesh_is_mirrored():
	g = GLB(export_mesh(mesh(**QUAD, indices=[0, 1, 2, 0, 2, 3])))
	[p] = g["meshes"][0]["primitives"]
	a = p["attributes"]
	assert g.accessor(a["POSITION"]) == approx([(0, 0, 0), (-1, 0, 0), (-1, 1, 0), (0, 1, 0)])
	assert g.accessor(a["NORMAL"]) == approx([(0, 0, 1)] * 4)
	# v = 0 at the top of the image
	assert g.accessor(a["TEXCOORD_0"]) == approx([(0, 1), (1, 1), (1, 0), (0, 0.75)])
	# the other winding
	assert g.accessor(p["indices"]) == [0, 2, 1, 0, 3, 2]
	acc = g["accessors"][a["POSITION"]]
	assert (acc["min"], acc["max"]) == ([-1, 0, 0], [0, 1, 0])
	assert g["accessors"][p["indices"]]["componentType"] == 5123  # unsigned short


def test_compressed_mesh():
	g = GLB(export_mesh(compressed_mesh([0] * 6, [1, 1, 1])))
	a = g["meshes"][0]["primitives"][0]["attributes"]
	# OBJ export gives (1, -1, -1), (-1, 1, 1), (0.6, -0.6, -0.6) for this mesh: the same mirror
	assert g.accessor(a["POSITION"]) == approx([(1, -1, -1), (-1, 1, 1), (0.6, -0.6, -0.6)])


def test_submeshes_and_materials():
	m = mesh(**QUAD, indices=[0, 1, 2, 0, 2, 3], submeshes=[submesh(0, 3), submesh(6, 0), submesh(6, 3)])
	red, blue = material("red", rgba=(1, 0, 0, 1)), material("blue", rgba=(0, 0, 1, 1))
	g = GLB(export_mesh(m, [red, None, blue]))
	prims = g["meshes"][0]["primitives"]
	assert len(prims) == 2  # the empty submesh has no primitive
	assert [g["materials"][p["material"]]["name"] for p in prims] == ["red", "blue"]
	assert g.accessor(prims[1]["indices"]) == [0, 3, 2]
	# both primitives share the vertex attributes
	assert prims[0]["attributes"] == prims[1]["attributes"]


def test_material():
	g = GLB(export_mesh(mesh(**QUAD, indices=[0, 1, 2]), [material(tex=Pointer(texture(), 5))]))
	[mat] = g["materials"]
	pbr = mat["pbrMetallicRoughness"]
	assert pbr["baseColorFactor"] == [0.5, 0.25, 1.0, 1.0]
	assert (pbr["metallicFactor"], pbr["roughnessFactor"]) == (0, 1)
	# every property as stored, for shaders that use them their own way
	assert mat["extras"]["colors"] == {"_Color": [0.5, 0.25, 1.0, 1.0], "_Emission": [2.0, 0.0, 0.0, 1.0]}
	assert mat["extras"]["floats"] == {"_Outline": pytest.approx(0.002)}
	assert mat["extras"]["textures"]["_MainTex"]["texture"] == "tex"

	tex = g["textures"][pbr["baseColorTexture"]["index"]]
	assert g["samplers"][tex["sampler"]]["wrapS"] == 10497  # repeat
	img = Image.open(io.BytesIO(g.image(tex["source"]))).convert("RGBA")
	# upright: the top row (stored last) first
	assert [img.getpixel((0, y)) for y in range(2)] == [(0, 0, 255, 255), (255, 0, 0, 255)]


def test_colour_factor_is_clamped():
	g = GLB(export_mesh(mesh(**QUAD, indices=[0, 1, 2]), [material(rgba=(2.0, -1.0, 0.5, 1.0))]))
	assert g["materials"][0]["pbrMetallicRoughness"]["baseColorFactor"] == [1.0, 0.0, 0.5, 1.0]


def test_clamped_texture_and_one_copy_per_texture():
	tex = Pointer(texture(wrap=1), 5)
	m = mesh(**QUAD, indices=[0, 1, 2, 0, 2, 3], submeshes=[submesh(0, 3), submesh(6, 3)])
	g = GLB(export_mesh(m, [material("a", tex), material("b", tex)]))
	assert len(g["images"]) == len(g["textures"]) == 1
	assert g["samplers"][0]["wrapS"] == 33071  # clamp to edge


def test_missing_texture():
	w = GLTFWriter()
	w.add_root(w.add_node("m", mesh=w.add_mesh(mesh(**QUAD, indices=[0, 1, 2]), [material(tex=Pointer(None, 9))])))
	g = GLB(w.glb())
	assert "baseColorTexture" not in g["materials"][0]["pbrMetallicRoughness"]
	assert "images" not in g.json
	assert len(w.warnings) == 1 and "elsewhere" in w.warnings[0]


def test_mesh_without_uvs_gets_untextured_material():
	m = mesh(QUAD["vertices"], QUAD["normals"], [], [0, 1, 2])
	g = GLB(export_mesh(m, [material(tex=Pointer(texture(), 5))]))
	p = g["meshes"][0]["primitives"][0]
	assert "TEXCOORD_0" not in p["attributes"]
	assert "baseColorTexture" not in g["materials"][p["material"]]["pbrMetallicRoughness"]


def test_mesh_without_triangles():
	w = GLTFWriter()
	assert w.add_mesh(mesh(**QUAD, indices=[])) is None
	assert "meshes" not in w.json


def test_node_transform_is_mirrored():
	w = GLTFWriter()
	n = w.add_node("n", translation=[1, 2, 3], rotation=[0.1, 0.2, 0.3, 0.9], scale=[2, 2, 2])
	assert w.json["nodes"][n] == {"name": "n", "translation": [-1, 2, 3], "rotation": [0.1, -0.2, -0.3, 0.9], "scale": [2, 2, 2]}
	# identity transforms are left out
	assert w.json["nodes"][w.add_node("i", translation=[0, 0, 0], rotation=[0, 0, 0, 1], scale=[1, 1, 1])] == {"name": "i"}


def test_mirror_matrix():
	# a translation by (1, 2, 3), row-major: mirrored, it moves by (-1, 2, 3)
	m = [1, 0, 0, 1, 0, 1, 0, 2, 0, 0, 1, 3, 0, 0, 0, 1]
	assert mirror_matrix(m) == [1, 0, 0, -1, 0, 1, 0, 2, 0, 0, 1, 3, 0, 0, 0, 1]
	# a rotation about y by 90 degrees turns the other way
	c, s = 0.0, 1.0
	r = [c, 0, s, 0, 0, 1, 0, 0, -s, 0, c, 0, 0, 0, 0, 1]
	assert mirror_matrix(r) == [c, 0, -s, 0, 0, 1, 0, 0, s, 0, c, 0, 0, 0, 0, 1]


# GameObjects: a root with a child that has a MeshFilter and a MeshRenderer.

def gameobject_tree():
	ids = iter(range(100, 200))

	def transform(go, pos, rot=(0, 0, 0, 1), children=()):
		t = Transform(fdict(m_GameObject=None, m_LocalPosition=vec(*pos), m_LocalRotation=fdict(**dict(zip("xyzw", rot))),
			m_LocalScale=vec(1, 1, 1), m_Children=list(children), m_Father=None))
		return Pointer(t, next(ids))

	child_go = GameObject(fdict(m_Name="child", m_Component=[]))
	child_tr = transform(child_go, (1, 0, 0), (0, math.sqrt(0.5), 0, math.sqrt(0.5)))
	child_tr.target._obj["m_GameObject"] = Pointer(child_go, next(ids))
	filt = fdict(m_GameObject=None, m_Mesh=Pointer(mesh(**QUAD, indices=[0, 1, 2], name="quad"), next(ids)))
	renderer = fdict(m_GameObject=None, m_Materials=[Pointer(material(), next(ids))])
	child_go._obj["m_Component"] = [(4, child_tr), (33, Pointer(filt, next(ids))), (23, Pointer(renderer, next(ids)))]

	root_go = GameObject(fdict(m_Name="root", m_Component=[]))
	root_tr = transform(root_go, (0, 2, 0), children=[child_tr])
	# a script component: not resolved
	root_go._obj["m_Component"] = [(4, root_tr), (114, Pointer(None, next(ids)))]
	return root_go


def test_gameobject():
	g = GLB(export_gameobject(gameobject_tree()))
	root, child = g["nodes"][g["scenes"][0]["nodes"][0]], None
	assert root["name"] == "root"
	assert root["translation"] == [0, 2, 0]
	[ci] = root["children"]
	child = g["nodes"][ci]
	assert child["name"] == "child"
	assert child["translation"] == [-1, 0, 0]
	assert child["rotation"] == pytest.approx([0, -math.sqrt(0.5), 0, math.sqrt(0.5)])
	assert g["meshes"][child["mesh"]]["name"] == "quad"
	assert g["materials"][g["meshes"][child["mesh"]]["primitives"][0]["material"]]["name"] == "mat"
