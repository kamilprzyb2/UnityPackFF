"""
glTF export: unitypack/gltf.py on meshes, materials, textures and GameObjects
built in memory, read back with tests/glb.py.

Conventions checked below: everything is mirrored along x (Unity is
left-handed, glTF right-handed), so positions and normals get -x, rotations
(x, -y, -z, w) and triangles the other winding; texture coordinates become
(u, 1 - v), since glTF puts v = 0 at the top of the image; textures are
embedded as upright PNGs.
"""

import hashlib
import io
import math
import os
import re

import pytest
from PIL import Image

from tests.glb import GLB
from tests.unit.test_export import QUAD, compressed_mesh, fdict, mesh, packed, submesh, vec
from unitypack.engine.component import Transform
from unitypack.engine.object import GameObject
from unitypack.engine.texture import Material, Texture2D, TextureFormat
from unitypack.gltf import ExternalFiles, GLTFWriter, export_gameobject, export_mesh, mirror_matrix


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


def test_material_other_textures_are_embedded():
	ramp = Pointer(texture("ramp", pixels=((0, 0, 0, 255), (255, 255, 255, 255))), 6)
	mat = material(tex=Pointer(texture(), 5))
	mat._obj["m_SavedProperties"]["m_TexEnvs"].append(
		(fdict(name="_ShaderMap"), fdict(m_Texture=ramp, m_Scale=fdict(x=1.0, y=1.0), m_Offset=fdict(x=0.0, y=0.0))))
	g = GLB(export_mesh(mesh(**QUAD, indices=[0, 1, 2]), [mat]))
	textures = g["materials"][0]["extras"]["textures"]
	assert "index" not in textures["_MainTex"]  # that's baseColorTexture
	ramp_texture = g["textures"][textures["_ShaderMap"]["index"]]
	assert g["images"][ramp_texture["source"]]["name"] == "ramp"
	assert len(g["images"]) == 2


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


def test_texture_override():
	# the hook sees each material and its slot; here it replaces slot 0's texture only
	own, other = Pointer(texture("own"), 5), texture("other", pixels=((0, 255, 0, 255), (0, 255, 0, 255)))
	shared = material("shared", own)
	seen = []

	def override(mat, slot):
		seen.append((mat.name, slot))
		return other if slot == 0 else None

	m = mesh(**QUAD, indices=[0, 1, 2, 0, 2, 3], submeshes=[submesh(0, 3), submesh(6, 3)])
	w = GLTFWriter(texture_override=override)
	w.add_root(w.add_node("m", mesh=w.add_mesh(m, [Pointer(shared, 7), shared])))
	g = GLB(w.glb())
	assert seen == [("shared", 0), ("shared", 1)]
	first, second = [g["materials"][p["material"]] for p in g["meshes"][0]["primitives"]]

	def image_name(mat):
		return g["images"][g["textures"][mat["pbrMetallicRoughness"]["baseColorTexture"]["index"]]["source"]]["name"]

	# the same Material becomes two glTF materials: replaced on slot 0, its own on slot 1
	assert (image_name(first), image_name(second)) == ("other", "own")
	assert first["extras"]["main_texture"] == "other"
	assert first["extras"]["textures"]["_MainTex"]["texture"] == "own"  # what the material stores
	assert "main_texture" not in second["extras"]


def test_texture_override_keeps_own_on_none():
	g = GLB(export_mesh(mesh(**QUAD, indices=[0, 1, 2]), [material(tex=Pointer(texture(), 5))]))
	w = GLTFWriter(texture_override=lambda mat, slot: None)
	w.add_root(w.add_node("m", mesh=w.add_mesh(mesh(**QUAD, indices=[0, 1, 2]), [material(tex=Pointer(texture(), 5))])))
	assert GLB(w.glb())["materials"] == g["materials"]


def test_mesh_without_uvs_gets_untextured_material():
	m = mesh(QUAD["vertices"], QUAD["normals"], [], [0, 1, 2])
	g = GLB(export_mesh(m, [material(tex=Pointer(texture(), 5))]))
	p = g["meshes"][0]["primitives"][0]
	assert "TEXCOORD_0" not in p["attributes"]
	assert "baseColorTexture" not in g["materials"][p["material"]]["pbrMetallicRoughness"]


def test_compressed_mesh_with_two_uv_sets_is_textured():
	m = compressed_mesh()
	m._obj["m_CompressedMesh"]["m_UV"] = packed([0, 0, 255, 255, 153, 153, 255, 0, 0, 255, 51, 51], 8, 0.0, 1.0)
	g = GLB(export_mesh(m, [material(tex=Pointer(texture(), 5))]))
	p = g["meshes"][0]["primitives"][0]
	assert g.accessor(p["attributes"]["TEXCOORD_0"]) == approx([(0, 1), (1, 0), (0.6, 0.4)])
	assert "baseColorTexture" in g["materials"][p["material"]]["pbrMetallicRoughness"]


def test_mesh_without_triangles():
	w = GLTFWriter()
	assert w.add_mesh(mesh(**QUAD, indices=[])) is None
	assert "meshes" not in w.json


def test_node_transform_is_mirrored():
	w = GLTFWriter()
	n = w.add_node("n", translation=[1, 2, 3], rotation=[0.5, 0.5, 0.5, 0.5], scale=[2, 2, 2])
	assert w.json["nodes"][n] == {"name": "n", "translation": [-1, 2, 3], "rotation": [0.5, -0.5, -0.5, 0.5], "scale": [2, 2, 2]}
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


# Skins and clips: a "rig" GameObject with a SkinnedMeshRenderer and an
# Animation, and one child bone one unit along x. The mesh's three vertices
# are weighted to the rig and the bone.

from tests.unit.test_animation import key, serialized_curve  # noqa: E402
from tests.unit.test_export import skinned_mesh  # noqa: E402
from unitypack.engine.animation import AnimationClip  # noqa: E402


def rig(bind_poses=None, clips=(), bones=None, **animation_fields):
	ids = iter(range(300, 400))

	def node(name, pos):
		go = GameObject(fdict(m_Name=name, m_Component=[]))
		tr = Pointer(Transform(fdict(m_GameObject=None, m_LocalPosition=vec(*pos), m_LocalRotation=fdict(x=0, y=0, z=0, w=1),
			m_LocalScale=vec(1, 1, 1), m_Children=[], m_Father=None)), next(ids))
		tr.target._obj["m_GameObject"] = Pointer(go, next(ids))
		go._obj["m_Component"] = [(4, tr)]
		return go, tr

	root, root_tr = node("rig", (0, 0, 0))
	bone, bone_tr = node("bone", (1, 0, 0))
	root_tr.target._obj["m_Children"] = [bone_tr]

	# vertex 0: rig only; 1: bone only; 2: half each, the bone listed twice
	m = skinned_mesh([31, 31, 10, 10, 11], [0, 1, 0, 1, 1])
	if bind_poses is not None:
		m._obj["m_BindPose"] = bind_poses
	smr = fdict(m_GameObject=None, m_Mesh=Pointer(m, next(ids)), m_Materials=[],
		m_Bones=bones if bones is not None else [root_tr, bone_tr])
	animation = fdict(m_GameObject=None, m_Animations=[c if isinstance(c, Pointer) else Pointer(c, next(ids)) for c in clips],
		**animation_fields)
	root._obj["m_Component"] += [(137, Pointer(smr, next(ids))), (111, Pointer(animation, next(ids)))]
	return root


def clip(name="move", position_curves=(), rotation_curves=(), float_curves=(), events=(), rate=2.0):
	return AnimationClip(fdict(
		m_Name=name, m_UseCompression=False, m_RotationCurves=list(rotation_curves), m_CompressedRotationCurves=[],
		m_PositionCurves=list(position_curves), m_ScaleCurves=[], m_FloatCurves=list(float_curves),
		m_SampleRate=rate, m_Events=list(events),
	))


def curve_on(path, *keys):
	return fdict(curve=serialized_curve(list(keys)), path=path)


def matrix_fields(m):
	return fdict(**{"e%d%d" % (r, c): m[r * 4 + c] for r in range(4) for c in range(4)})


def test_skin():
	g = GLB(export_gameobject(rig()))
	roots = g["scenes"][0]["nodes"]
	assert len(roots) == 2
	rig_node, skinned = g["nodes"][roots[0]], g["nodes"][roots[1]]
	assert "mesh" not in rig_node  # the skinned mesh is on a node of its own, at the top
	assert skinned["name"] == "rig (skinned)"
	skin = g["skins"][skinned["skin"]]
	assert [g["nodes"][j]["name"] for j in skin["joints"]] == ["rig", "bone"]

	a = g["meshes"][skinned["mesh"]]["primitives"][0]["attributes"]
	# vertex 2: the bone's two weights merged (21/31) and first, then the rig's
	assert g.accessor(a["JOINTS_0"]) == [(0, 0, 0, 0), (1, 0, 0, 0), (1, 0, 0, 0)]
	assert g.accessor(a["WEIGHTS_0"]) == approx([(1, 0, 0, 0), (1, 0, 0, 0), (21 / 31, 10 / 31, 0, 0)])


def test_inverse_bind_matrices_from_the_pose():
	# no bind poses stored: the prefab's pose is the bind pose
	g = GLB(export_gameobject(rig()))
	skin = g["skins"][0]
	ibm = g.accessor(skin["inverseBindMatrices"])
	assert ibm[0] == approx([(1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1)])[0]
	# column-major; the bone is at -1 along glTF's x, so its inverse moves by +1
	assert ibm[1] == approx([(1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 1, 0, 0, 1)])[0]


def test_inverse_bind_matrices_stored():
	identity = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
	moved = [1, 0, 0, -5, 0, 1, 0, 0, 0, 0, 1, 2, 0, 0, 0, 1]  # row-major, translation (-5, 0, 2)
	g = GLB(export_gameobject(rig(bind_poses=[matrix_fields(identity), matrix_fields(moved)])))
	ibm = g.accessor(g["skins"][0]["inverseBindMatrices"])
	# mirrored along x, column-major: translation (5, 0, 2) in the last column
	assert ibm[1] == approx([(1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 5, 0, 2, 1)])[0]


def test_bones_outside_the_gameobject():
	stranger = Pointer(Transform(fdict(m_GameObject=None, m_LocalPosition=vec(0, 0, 0), m_LocalRotation=fdict(x=0, y=0, z=0, w=1),
		m_LocalScale=vec(1, 1, 1), m_Children=[], m_Father=None)), 999)
	w = GLTFWriter()
	w.add_gameobject(rig(bones=[stranger, stranger]))
	assert "skins" not in w.json
	assert w.json["nodes"][-1]["name"] == "rig" and "mesh" in w.json["nodes"][-1]
	assert "not under the exported GameObject" in w.warnings[0]


def test_without_skins():
	w = GLTFWriter()
	w.add_gameobject(rig(), skins=False)
	assert "skins" not in w.json
	assert len(w.json["scenes"][0]["nodes"]) == 1


def plain_child(parent_tr, name, ids, tex=None):
	"A child GameObject of a Transform, with a MeshFilter + MeshRenderer; returns its Transform pointer"
	go = GameObject(fdict(m_Name=name, m_Component=[]))
	tr = Pointer(Transform(fdict(m_GameObject=Pointer(go, next(ids)), m_LocalPosition=vec(0, 0, 0),
		m_LocalRotation=fdict(x=0, y=0, z=0, w=1), m_LocalScale=vec(1, 1, 1), m_Children=[], m_Father=None)), next(ids))
	add_plain_mesh(go, tr, name, ids, tex)
	parent_tr.target._obj["m_Children"].append(tr)
	return tr


def add_plain_mesh(go, tr, name, ids, tex=None):
	filt = fdict(m_GameObject=None, m_Mesh=Pointer(mesh(**QUAD, indices=[0, 1, 2], name=name), next(ids)))
	renderer = fdict(m_GameObject=None, m_Materials=[Pointer(material(name, tex), next(ids))])
	go._obj["m_Component"] = [(4, tr), (33, Pointer(filt, next(ids))), (23, Pointer(renderer, next(ids)))]


def worn_item():
	"""
	A worn item, as some hair is: the rig's skinned mesh, and plain meshes
	beside it: on the bone (a joint of the skin), under the bone, and under
	the root (a textured one)
	"""
	ids = iter(range(500, 600))
	root = rig()
	root_tr = root.component[0][1]
	bone_tr = root_tr.target._obj["m_Children"][0]
	add_plain_mesh(bone_tr.target.game_object.resolve(), bone_tr, "on the bone", ids)
	plain_child(bone_tr, "Biped Object", ids)
	plain_child(root_tr, "Biped Object@#0", ids, tex=Pointer(texture("hat"), next(ids)))
	return root


def test_skinned_only():
	w = GLTFWriter()
	w.add_gameobject(worn_item(), skinned_only=True)
	g = GLB(w.glb())
	[m] = g["meshes"]
	assert "skin" in g["nodes"][g["scenes"][0]["nodes"][1]]
	# the bone stays, a joint; the nodes that only carried a plain mesh go
	assert sorted(n["name"] for n in g["nodes"]) == ["bone", "rig", "rig (skinned)"]
	assert [n.get("mesh") for n in g["nodes"] if n["name"] != "rig (skinned)"] == [None, None]
	assert "materials" not in g.json and "images" not in g.json
	assert w.left_out == ["bone", "bone/Biped Object", "Biped Object@#0"]
	[warning] = w.warnings
	assert "'Biped Object@#0'" in warning and "textured" in warning


def test_skinned_only_is_off_by_default():
	w = GLTFWriter()
	w.add_gameobject(worn_item())
	g = GLB(w.glb())
	assert len(g["meshes"]) == 4
	assert sorted(n["name"] for n in g["nodes"]) == ["Biped Object", "Biped Object@#0", "bone", "rig", "rig (skinned)"]
	assert w.left_out == [] and w.warnings == []


# What binds a worn item to the character: the actor's ActorSkinCombiner
# (actorBones, a list of Transforms) and an ActorWearIndexTable beside each
# worn SkinnedMeshRenderer (for each bone, an index into the boy's and the
# girl's actorBones)

def script(**fields):
	"A MonoBehaviour's fields, as read"
	return fdict(m_GameObject=None, m_Enabled=1, m_Script=Pointer(None, 0), m_Name="", **fields)


def actor():
	"The rig as an actor: actorBones lists the bone, a Transform elsewhere (-1) and the rig"
	root = rig()
	root_tr = root.component[0][1]
	bone_tr = root_tr.target._obj["m_Children"][0]
	stranger = Pointer(None, 999)
	combiner = script(actorBones=[bone_tr, stranger, root_tr, Pointer(None, 0)])
	root._obj["m_Component"].append((114, Pointer(combiner, 700)))
	return root


def worn(m=(5, 0), f=(7, -1), bones=None):
	"The rig as a worn item: its skin's two bones map to actor bones m and f"
	root = rig(bones=bones)
	table = script(transformIndicesM=list(m), transformIndicesF=list(f))
	root._obj["m_Component"].append((114, Pointer(table, 701)))
	return root


def test_actor_bones_of_the_actor():
	g = GLB(export_gameobject_with(actor(), actor_bones=True))
	root = g["nodes"][g["scenes"][0]["nodes"][0]]
	names = {i: n["name"] for i, n in enumerate(g["nodes"])}
	bones = root["extras"]["actor_bones"]
	assert [names.get(b) for b in bones] == ["bone", None, "rig", None]
	assert bones[1] == bones[3] == -1


def test_actor_bones_of_a_worn_item():
	w = GLTFWriter()
	w.add_gameobject(worn(), actor_bones=True)
	g = GLB(w.glb())
	[skin] = g["skins"]
	# one entry per joint, in the skin's joint order; none is -1
	assert skin["extras"]["actor_bones"] == {"m": [5, 0], "f": [7, -1]}
	assert w.unbound_skins == [] and w.warnings == []


def test_actor_bones_are_off_by_default():
	g = GLB(export_gameobject(worn()))
	assert "extras" not in g["skins"][0]
	g = GLB(export_gameobject(actor()))
	assert "actor_bones" not in g["nodes"][g["scenes"][0]["nodes"][0]]["extras"]


def test_skin_without_index_table():
	w = GLTFWriter()
	w.add_gameobject(rig(), actor_bones=True)
	assert w.unbound_skins == ["c"]  # the mesh's name
	assert "extras" not in w.json["skins"][0]


def test_index_table_of_the_wrong_length():
	w = GLTFWriter()
	w.add_gameobject(worn(m=(1, 2, 3), f=(4,)), actor_bones=True)
	assert w.json["skins"][0]["extras"]["actor_bones"] == {"m": [1, 2], "f": [4, -1]}
	assert len(w.warnings) == 2 and "transformIndicesM has 3 entries for 2 bones" in w.warnings[0]


def export_gameobject_with(gameobject, **options):
	w = GLTFWriter()
	w.add_gameobject(gameobject, **options)
	return w.glb()


def test_clip():
	move = clip(position_curves=[curve_on("bone", key(0.0, vec(1, 0, 0), vec(1, 0, 0)), key(1.0, vec(2, 0, 0), vec(1, 0, 0)))])
	g = GLB(export_gameobject(rig(clips=[move])))
	[anim] = g["animations"]
	assert anim["name"] == "move"
	[ch] = anim["channels"]
	assert g["nodes"][ch["target"]["node"]]["name"] == "bone"
	assert ch["target"]["path"] == "translation"
	s = anim["samplers"][ch["sampler"]]
	assert s["interpolation"] == "LINEAR"
	# 2 frames a second over 1 second, mirrored along x
	assert g.accessor(s["input"]) == [0, 0.5, 1]
	assert g.accessor(s["output"]) == approx([(-1, 0, 0), (-1.5, 0, 0), (-2, 0, 0)])


def test_clip_constant_curve_has_one_sample():
	still = clip(position_curves=[
		curve_on("bone", key(0.0, vec(1, 0, 0), vec(0, 0, 0)), key(1.0, vec(1, 0, 0), vec(0, 0, 0))),
		curve_on("", key(0.0, vec(0, 0, 0), vec(0, 0, 0)), key(1.0, vec(0, 3, 0), vec(0, 3, 0))),
	])
	g = GLB(export_gameobject(rig(clips=[still])))
	anim = g["animations"][0]
	counts = {g["nodes"][c["target"]["node"]]["name"]: len(g.accessor(anim["samplers"][c["sampler"]]["output"])) for c in anim["channels"]}
	assert counts == {"bone": 1, "rig": 3}  # "" is the GameObject with the Animation


def test_clip_rotations():
	# a half turn about y: samples are normalized, mirrored, and keep one sign
	turn = clip(rotation_curves=[curve_on("bone",
		key(0.0, fdict(x=0, y=0, z=0, w=2), fdict(x=0, y=0, z=0, w=0)),  # not normalized
		key(1.0, fdict(x=0, y=-1, z=0, w=0), fdict(x=0, y=0, z=0, w=0)))])
	g = GLB(export_gameobject(rig(clips=[turn])))
	anim = g["animations"][0]
	out = g.accessor(anim["samplers"][anim["channels"][0]["sampler"]]["output"])
	assert out[0] == approx([(0, 0, 0, 1)])[0]
	# halfway, flat slopes: (0, -0.5, 0, 1), normalized; mirrored, y flips sign
	n = math.sqrt(1.25)
	assert out[1] == approx([(0, 0.5 / n, 0, 1 / n)])[0]
	assert out[2] == approx([(0, 1, 0, 0)])[0]
	for q in out:
		assert sum(c * c for c in q) == pytest.approx(1, abs=1e-6)
	for a, b in zip(out, out[1:]):
		assert sum(x * y for x, y in zip(a, b)) >= 0


def test_clip_extras_and_missing_paths():
	c = clip(
		position_curves=[curve_on("gone", key(0.0, vec(0, 0, 0), vec(0, 0, 0)), key(1.0, vec(1, 0, 0), vec(0, 0, 0))),
			curve_on("bone", key(0.0, vec(1, 0, 0), vec(0, 0, 0)), key(1.0, vec(2, 0, 0), vec(0, 0, 0)))],
		float_curves=[fdict(curve=serialized_curve([key(0.0, 0.0, 0.0)]), attribute="_Color.r", path="", classID=21, script=None)],
		events=[fdict(time=0.75, functionName="end", data="", messageOptions=1)],
	)
	w = GLTFWriter()
	w.add_gameobject(rig(clips=[c]))
	anim = w.json["animations"][0]
	assert anim["extras"]["events"] == [{"time": 0.75, "function": "end"}]
	assert anim["extras"]["float_curves"] == {":_Color.r": {"times": [0.0], "values": [0.0], "node": w.json["scenes"][0]["nodes"][0]}}
	assert anim["extras"]["unresolved"] == ["gone"]
	assert len(anim["channels"]) == 1
	assert "no node for 1 curve paths, such as 'gone'" in w.warnings[0]


def float_curve(attribute, *keys, path=""):
	return fdict(curve=serialized_curve(list(keys)), attribute=attribute, path=path, classID=21, script=None)


def test_clip_float_curve_values():
	# a character's build: _FatFactor 0.0075 -> 0 -> -0.0078, sampled at the clip's rate as transforms are
	shape = clip("shape", position_curves=[curve_on("bone", key(0.0, vec(1, 0, 0), vec(0, 0, 0)), key(1.0, vec(1, 0, 0), vec(0, 0, 0)))],
		float_curves=[
			float_curve("[0]._FatFactor", key(0.0, 0.0075, -0.015), key(0.5, 0.0, -0.0153), key(1.0, -0.0078, -0.0156)),
			float_curve("_Color.r", key(0.0, 0.5, 0.0), key(1.0, 0.5, 0.0), path="bone"),
		])
	g = GLB(export_gameobject(rig(clips=[shape])))
	curves = g["animations"][0]["extras"]["float_curves"]
	assert list(curves) == [":[0]._FatFactor", "bone:_Color.r"]
	fat = curves[":[0]._FatFactor"]
	assert fat["times"] == [0, 0.5, 1]
	assert fat["values"] == pytest.approx([0.0075, 0.0, -0.0078])
	assert g["nodes"][fat["node"]]["name"] == "rig"
	# a constant curve: one sample, as for transforms
	assert curves["bone:_Color.r"]["values"] == [0.5] and curves["bone:_Color.r"]["times"] == [0]
	assert g["nodes"][curves["bone:_Color.r"]["node"]]["name"] == "bone"


def test_material_only_clip():
	# no transform curve, so no glTF animation: the document's extras keep it
	scroll = clip("nif-default", float_curves=[float_curve("_MainTex.offset.x", key(0.0, 0.0, 1.0), key(1.0, 1.0, 1.0))],
		events=[fdict(time=0.5, functionName="half", data="", messageOptions=1)])
	g = GLB(export_gameobject(rig(clips=[scroll])))
	assert "animations" not in g.json
	[c] = g["extras"]["material_clips"]
	assert c["name"] == "nif-default"
	assert c["float_curves"][":_MainTex.offset.x"]["values"] == pytest.approx([0, 0.5, 1])
	assert c["events"] == [{"time": 0.5, "function": "half"}]


# How Animation components play their clips (extras.animation), and what a
# clip's curve paths name: nodes under the Animation's own node, found the
# way Unity's Transform.Find finds them

def animation_fields(**kw):
	"An Animation's other fields as Unity 2.5 stores them, with some changed"
	fields = dict(m_Enabled=True, m_Animation=Pointer(None, 0), m_WrapMode=0, m_PlayAutomatically=True,
		m_AnimatePhysics=False, m_AnimateIfVisible=True)
	fields.update(kw)
	return fields


def test_animation_extras():
	move = clip(position_curves=[curve_on("bone", key(0.0, vec(1, 0, 0), vec(1, 0, 0)), key(1.0, vec(2, 0, 0), vec(1, 0, 0)))])
	scroll = clip("scroll", float_curves=[float_curve("_MainTex.offset.x", key(0.0, 0.0, 1.0), key(1.0, 1.0, 1.0))])
	empty = clip("empty")
	move_ptr, scroll_ptr = Pointer(move, 801), Pointer(scroll, 802)
	root = rig(clips=[move_ptr, Pointer(None, 803), scroll_ptr, Pointer(empty, 804), move_ptr],
		**animation_fields(m_Animation=scroll_ptr, m_Enabled=False, m_WrapMode=2, m_PlayAutomatically=False, m_AnimatePhysics=True))
	g = GLB(export_gameobject(root))
	info = g["nodes"][g["scenes"][0]["nodes"][0]]["extras"]["animation"]
	assert info == {
		# in order: a clip elsewhere has no name, one with nothing to export no index, the same clip twice one animation
		"clips": [{"name": "move", "animation": 0}, {"name": None}, {"name": "scroll", "material_clip": 0}, {"name": "empty"},
			{"name": "move", "animation": 0}],
		"default": 2, "wrap_mode": "Loop", "play_automatically": False, "enabled": False, "animate_physics": True,
		"animate_if_visible": True,
	}
	assert len(g["animations"]) == 1 and len(g["extras"]["material_clips"]) == 1


def test_animation_default_clip_not_listed():
	# Unity plays the default clip even if the list hasn't it: it goes last
	move = clip(position_curves=[curve_on("bone", key(0.0, vec(1, 0, 0), vec(1, 0, 0)), key(1.0, vec(2, 0, 0), vec(1, 0, 0)))])
	root = rig(clips=[], **animation_fields(m_Animation=Pointer(move, 805), m_WrapMode=3, m_CullingType=1))
	del root.component[2][1].target["m_AnimateIfVisible"]
	g = GLB(export_gameobject(root))
	info = g["nodes"][g["scenes"][0]["nodes"][0]]["extras"]["animation"]
	assert info["clips"] == [{"name": "move", "animation": 0}] and info["default"] == 0
	assert info["wrap_mode"] == 3  # not a WrapMode: the number
	assert info["culling_type"] == 1 and "animate_if_visible" not in info


def test_animation_without_clips_exported():
	move = clip(position_curves=[curve_on("bone", key(0.0, vec(1, 0, 0), vec(1, 0, 0)), key(1.0, vec(2, 0, 0), vec(1, 0, 0)))])
	w = GLTFWriter()
	w.add_gameobject(rig(clips=[move]), animations=False)
	assert "animations" not in w.json
	assert w.json["nodes"][w.json["scenes"][0]["nodes"][0]]["extras"]["animation"]["clips"] == [{"name": "move"}]


def tree(spec, ids):
	"""
	A GameObject tree: spec is (name, [components], [child specs]), a
	component (class ID, fields); returns the root GameObject
	"""
	name, comps, children = spec
	go = GameObject(fdict(m_Name=name, m_Component=[]))
	tr = Transform(fdict(m_GameObject=Pointer(go, next(ids)), m_LocalPosition=vec(0, 0, 0), m_LocalRotation=fdict(x=0, y=0, z=0, w=1),
		m_LocalScale=vec(1, 1, 1), m_Children=[], m_Father=None))
	for child in children:
		child_go = tree(child, ids)
		tr._obj["m_Children"].append(child_go.component[0][1])
	go._obj["m_Component"] = [(4, Pointer(tr, next(ids)))] + [(cid, Pointer(fields, next(ids))) for cid, fields in comps]
	return go


def spin(name="nif-default", path="Plane01"):
	"A clip turning path once about y"
	return clip(name, rotation_curves=[curve_on(path,
		key(0.0, fdict(x=0, y=0, z=0, w=1), fdict(x=0, y=0, z=0, w=0)),
		key(1.0, fdict(x=0, y=1, z=0, w=0), fdict(x=0, y=0, z=0, w=0)))])


def test_clip_paths_are_relative_to_the_animation():
	# map tile props: unnamed siblings, each with an Animation and a clip of the same name, on its own Plane01
	ids = iter(range(900, 1000))
	shared = Pointer(spin(), 899)
	prop = ("", [(111, fdict(m_GameObject=None, m_Animations=[shared], **animation_fields(m_Animation=shared)))], [("Plane01", [], [])])
	root = tree(("Map_11_11", [], [prop, prop]), ids)
	w = GLTFWriter()
	w.add_gameobject(root)
	g = GLB(w.glb())
	planes = [i for i, n in enumerate(g["nodes"]) if n.get("name") == "Plane01"]
	assert len(planes) == 2
	assert sorted(a["channels"][0]["target"]["node"] for a in g["animations"]) == planes
	props = [n for n in g["nodes"] if n.get("name") is None]
	assert [n["extras"]["animation"]["clips"] for n in props] == [[{"name": "nif-default", "animation": a}] for a in (0, 1)]
	assert w.warnings == []
	# the same clip twice: its samples once
	first, second = g["animations"]
	assert [(s["input"], s["output"]) for s in first["samplers"]] == [(s["input"], s["output"]) for s in second["samplers"]]


def test_clip_ambiguous_path():
	# two children named "wheel": the first is animated, as in Unity
	ids = iter(range(900, 1000))
	animation = fdict(m_GameObject=None, m_Animations=[Pointer(spin(path="wheel/hub"), 898)], **animation_fields())
	root = tree(("ride", [(111, animation)], [("wheel", [], []), ("wheel", [], [("hub", [], [])])]), ids)
	w = GLTFWriter()
	w.add_gameobject(root)
	g = GLB(w.glb())
	[a] = g["animations"]
	hub = next(i for i, n in enumerate(g["nodes"]) if n.get("name") == "hub")
	# the first wheel has no hub, so Transform.Find goes on to the second
	assert a["channels"][0]["target"]["node"] == hub
	assert "ambiguous" not in a.get("extras", {})

	animation["m_Animations"] = [Pointer(spin(path="wheel"), 897)]
	w = GLTFWriter()
	w.add_gameobject(tree(("ride", [(111, animation)], [("wheel", [], []), ("wheel", [], [])]), iter(range(900, 1000))))
	g = GLB(w.glb())
	[a] = g["animations"]
	wheels = [i for i, n in enumerate(g["nodes"]) if n.get("name") == "wheel"]
	assert a["channels"][0]["target"]["node"] == wheels[0]
	assert a["extras"]["ambiguous"] == ["wheel"]
	assert "clip 'nif-default': 1 curve paths name more than one node, such as 'wheel'; the first is animated" in w.warnings


def test_clip_without_channels_is_left_out():
	empty = clip(position_curves=[curve_on("gone", key(0.0, vec(0, 0, 0), vec(0, 0, 0)))])
	w = GLTFWriter()
	w.add_gameobject(rig(clips=[empty]))
	assert "animations" not in w.json
	w = GLTFWriter()
	w.add_gameobject(rig(clips=[empty]), animations=False)
	assert w.warnings == []


# Render state from shader names, terrain, and whole scenes

from unitypack.engine.terrain import TerrainData  # noqa: E402
from unitypack.engine.text import Shader  # noqa: E402
from unitypack.gltf import alpha_state, export_scene, shader_name  # noqa: E402


@pytest.mark.parametrize("shader,transparent,state", [
	("normal_blendSrcalphaInvsrcalpha", False, {}),  # most props: opaque textures
	("normal_blendSrcalphaInvsrcalpha", True, {"alphaMode": "BLEND"}),
	("normal_blendSrcalphaInvsrcalphaTest_cullOff", False, {"alphaMode": "MASK", "alphaCutoff": 0.3, "doubleSided": True}),
	("normal_blendOneOne_zwriteOff", False, {"alphaMode": "BLEND"}),
	("normal_blendSrcalphaOne_zwriteOff", False, {"alphaMode": "BLEND"}),
	("normal_blendSrcalphaInvsrcalpha_zwriteOff", False, {"alphaMode": "BLEND"}),
	("Transparent/Diffuse", False, {"alphaMode": "BLEND"}),
	("Transparent/Cutout/Diffuse", False, {"alphaMode": "MASK", "alphaCutoff": 0.3}),
	("Diffuse", True, {}),
	(None, True, {}),
])
def test_alpha_state(shader, transparent, state):
	assert alpha_state(shader, {"_Cutoff": 0.3}, transparent) == state


def test_shader_name():
	assert shader_name(Shader(fdict(m_Name="Diffuse", m_Script=""))) == "Diffuse"
	# these builds leave m_Name empty; the source names the shader
	assert shader_name(Shader(fdict(m_Name="", m_Script='Shader "normal_blendOneOne" {\n SubShader {}\n}'))) == "normal_blendOneOne"
	assert shader_name(Shader(fdict(m_Name="", m_Script=""))) is None
	assert shader_name(None) is None


def shaded_material(shader_source, tex):
	m = material(tex=tex)
	m._obj["m_Shader"] = Pointer(Shader(fdict(m_Name="", m_Script=shader_source)), 77)
	return m


def test_material_render_state():
	half_transparent = Pointer(texture(pixels=((255, 0, 0, 255), (0, 0, 255, 128))), 5)
	g = GLB(export_mesh(mesh(**QUAD, indices=[0, 1, 2]), [shaded_material('Shader "normal_blendSrcalphaInvsrcalpha" {}', half_transparent)]))
	mat = g["materials"][0]
	assert mat["alphaMode"] == "BLEND"
	assert mat["extras"]["shader"] == "normal_blendSrcalphaInvsrcalpha"


def test_additive_material_gets_brightness_as_alpha():
	# black and dim red: drawn additively they add little light; as BLEND, their brightness is their opacity
	tex = Pointer(texture(pixels=((0, 0, 0, 255), (128, 64, 0, 255))), 5)
	g = GLB(export_mesh(mesh(**QUAD, indices=[0, 1, 2]), [shaded_material('Shader "normal_blendOneOne_zwriteOff" {}', tex)]))
	mat = g["materials"][0]
	assert mat["alphaMode"] == "BLEND"
	image = g["textures"][mat["pbrMetallicRoughness"]["baseColorTexture"]["index"]]["source"]
	img = Image.open(io.BytesIO(g.image(image))).convert("RGBA")
	# upright: the second stored row on top
	assert [img.getpixel((0, y)) for y in range(2)] == [(128, 64, 0, 128), (0, 0, 0, 0)]


def terrain_data(splats=True):
	size = 33
	heights = [x * 10 for x in range(size) for z in range(size)]  # rising along x
	red = texture("red", pixels=((255, 0, 0, 255),) * 2)
	control = Texture2D(fdict(m_Name="control", m_Width=1, m_Height=1, m_TextureFormat=int(TextureFormat.RGBA32),
		m_TextureSettings=fdict(m_WrapMode=1), **{"image data": bytes((255, 0, 0, 0))}))
	return TerrainData(fdict(
		m_Name="ground",
		m_SplatDatabase=fdict(
			m_Splats=[fdict(texture=Pointer(red, 40), tileSize=fdict(x=16.0, y=16.0), tileOffset=fdict(x=0.0, y=0.0))] if splats else [],
			m_AlphaTextures=[Pointer(control, 41)] if splats else [], m_AlphamapResolution=1, m_BaseMapResolution=8),
		m_DetailDatabase=fdict(),
		m_Heightmap=fdict(m_Heights=heights, m_Shifts=[fdict(x=5, y=5, flags=2)], m_PrecomputedError=[0.0] * 5,
			m_MinMaxPatchHeights=[0.0] * 10, m_DefaultPhysicMaterial=None, m_Width=size, m_Height=size, m_Levels=1,
			m_Scale=vec(4.0, 600.0, 4.0)),
		m_Lightmap=Pointer(texture("lightmap"), 42),
	))


def test_terrain():
	w = GLTFWriter()
	w.add_root(w.add_node("t", mesh=w.add_terrain(terrain_data())))
	g = GLB(w.glb())
	[p] = g["meshes"][0]["primitives"]
	positions = g.accessor(p["attributes"]["POSITION"])
	assert len(positions) == 33 * 33
	# mirrored; the shifted vertex (5, 5) moved +x, so -4 in glTF
	assert positions[5 * 33 + 5] == approx([(-24, 50 / 32766 * 600, 20)])[0]
	assert g.accessor(p["attributes"]["TEXCOORD_0"])[0] == approx([(0, 1)])[0]
	# normals face up, leaning against the slope (terrain rises along Unity's x, so along -x in glTF)
	for n in g.accessor(p["attributes"]["NORMAL"])[:5]:
		assert n[1] > 0.9 and n[0] > 0
	mat = g["materials"][p["material"]]
	base = mat["pbrMetallicRoughness"]["baseColorTexture"]["index"]
	img = Image.open(io.BytesIO(g.image(g["textures"][base]["source"]))).convert("RGB")
	assert img.size == (8, 8) and img.getpixel((3, 3)) == (255, 0, 0)
	extras = mat["extras"]["terrain"]
	assert extras["splats"][0]["tile_size"] == [16.0, 16.0]
	[control] = extras["controls"]
	assert g["samplers"][g["textures"][control]["sampler"]]["wrapS"] == 33071  # the control map, clamped
	assert extras["lightmap"] is not None


def detail_database(grass_texture):
	"2 x 2 patches of 2 x 2 cells: a grass texture with instances, an unused one, and a mesh"
	def prototype(texture=None, mesh=None, mode=0):
		return fdict(prototype=mesh or Pointer(None, 0), prototypeTexture=texture or Pointer(None, 0),
			minWidth=1.0, maxWidth=2.0, minHeight=1.0, maxHeight=0.5, noiseSpread=0.25, bendFactor=0.5,
			healthyColor=color(0.25, 1.0, 0.0), dryColor=color(1.0, 0.5, 0.0), grayscaleLighting=0, lightmapFactor=1.0,
			renderMode=mode, usePrototypeMesh=int(mesh is not None))

	def patch(layers, counts):
		return fdict(layerIndices=bytes(layers), numberOfObjects=bytes(counts))

	bush = GameObject(fdict(m_Name="bush", m_Component=[]))
	return fdict(
		m_Patches=[patch([0], [0, 0, 1, 0]), patch([], []), patch([], []), patch([0, 2], [0, 0, 0, 2, 0, 0, 0, 0])],
		m_DetailPrototypes=[prototype(Pointer(grass_texture, 43)), prototype(Pointer(None, 44)), prototype(mesh=Pointer(bush, 45), mode=1)],
		m_PatchCount=2, m_PatchSamples=2,
		m_WavingGrassTint=color(1.0, 0.875, 0.75), m_WavingGrassStrength=0.5, m_WavingGrassAmount=0.25, m_WavingGrassSpeed=0.125,
	)


def test_terrain_details():
	t = terrain_data()
	t._obj["m_DetailDatabase"] = detail_database(texture("grass"))
	w = GLTFWriter()
	w.add_root(w.add_node("t", mesh=w.add_terrain(t)))
	g = GLB(w.glb())
	details = g["materials"][0]["extras"]["terrain"]["details"]
	assert details["resolution"] == 4
	assert details["waving_grass"] == {"tint": [1.0, 0.875, 0.75, 1.0], "strength": 0.5, "amount": 0.25, "speed": 0.125}
	grass, missing, bush = details["prototypes"]
	assert g["images"][g["textures"][grass["texture"]]["source"]]["name"] == "grass"
	assert grass["render_mode"] == "GrassBillboard" and grass["use_mesh"] is False and grass["prototype"] is None
	assert (grass["min_width"], grass["max_width"], grass["healthy_color"]) == (1.0, 2.0, [0.25, 1.0, 0.0, 1.0])

	# counts per cell, unfiltered and clamped, laid over the terrain as its texture coordinates are:
	# glTF puts z = 0 at the bottom of the image, as for the terrain's own texture
	density = g["textures"][grass["density"]]
	assert g["samplers"][density["sampler"]] == {"magFilter": 9728, "minFilter": 9728, "wrapS": 33071, "wrapT": 33071}
	img = Image.open(io.BytesIO(g.image(density["source"])))
	assert img.mode == "L" and img.size == (4, 4)
	cells = {(x, 3 - row): img.getpixel((x, row)) for x in range(4) for row in range(4) if img.getpixel((x, row))}
	assert cells == {(0, 1): 1, (3, 3): 2}  # patch (0, 0) cell (0, 1); patch (1, 1) cell (1, 1)

	assert missing["texture"] is None and missing["density"] is None  # its texture is elsewhere, and it has none
	assert bush["prototype"] == "bush" and bush["use_mesh"] is True and bush["render_mode"] == "VertexLit"
	assert any("detail prototype 2 of 'ground' is a mesh ('bush'), which isn't exported" in m for m in w.warnings)


def test_terrain_without_details():
	w = GLTFWriter()
	w.add_root(w.add_node("t", mesh=w.add_terrain(terrain_data())))
	assert "details" not in w.json["materials"][0]["extras"]["terrain"]


def test_terrain_without_splats():
	w = GLTFWriter()
	w.add_root(w.add_node("t", mesh=w.add_terrain(terrain_data(splats=False))))
	g = GLB(w.glb())
	assert "baseColorTexture" not in g["materials"][0]["pbrMetallicRoughness"]


class FakeObject:
	def __init__(self, path_id, type_id, obj):
		self.path_id, self.type_id, self.obj = path_id, type_id, obj

	def read(self):
		return self.obj


def test_scene():
	tree = gameobject_tree()
	hidden = GameObject(fdict(m_Name="hidden", m_IsActive=False, m_Component=[]))
	hidden_tr = Transform(fdict(m_GameObject=Pointer(hidden, 501), m_LocalPosition=vec(0, 0, 0), m_LocalRotation=fdict(x=0, y=0, z=0, w=1),
		m_LocalScale=vec(1, 1, 1), m_Children=[], m_Father=Pointer(None, 0)))
	hidden._obj["m_Component"] = [(4, Pointer(hidden_tr, 500))]
	root_tr = tree.component[0][1].target
	root_tr._obj["m_Father"] = Pointer(None, 0)
	root_tr._obj["m_GameObject"] = Pointer(tree, 601)
	child_tr = root_tr._obj["m_Children"][0].target
	child_tr._obj["m_Father"] = Pointer(root_tr, 600)
	asset = type("Asset", (), {"objects": {
		600: FakeObject(600, 4, root_tr), 500: FakeObject(500, 4, hidden_tr), 601: FakeObject(601, 1, tree),
		602: FakeObject(602, 4, child_tr),
	}})()
	g = GLB(export_scene(asset))
	# one root (the hidden one is left out), with its child
	assert [g["nodes"][n]["name"] for n in g["scenes"][0]["nodes"]] == ["root"]
	w = GLTFWriter()
	assert len(w.add_scene(asset, inactive=True)) == 2


def test_scene_extras():
	# RenderSettings and FusionFall's DongColorSetup (a script object) go into the scene's extras
	settings = fdict(m_Fog=1, m_FogColor=color(0.5, 0.5, 0.5), m_FogDensity=0.25, m_AmbientLight=color(0.2, 0.2, 0.2))
	dong = fdict(fogDepth=0.75, fogColor=color(0, 0, 1), skyColor=color(0, 1, 0), lightColor=color(1, 1, 1))
	asset = type("Asset", (), {"objects": {1: FakeObject(1, 104, settings), 2: FakeObject(2, -1, dong)}})()
	extras = GLB(export_scene(asset))["scenes"][0]["extras"]
	assert extras["render_settings"] == {"fog": True, "fog_color": [0.5, 0.5, 0.5, 1.0], "fog_density": 0.25, "ambient": pytest.approx([0.2, 0.2, 0.2, 1.0])}
	assert extras["colors"] == {"fog_depth": 0.75, "fog": [0, 0, 1, 1], "sky": [0, 1, 0, 1], "light": [1, 1, 1, 1]}
	# a scene without them has no extras
	assert "extras" not in GLB(export_scene(type("Asset", (), {"objects": {}})()))["scenes"][0]


def lit_gameobject(**light):
	go = GameObject(fdict(m_Name="lamp", m_Component=[]))
	tr = Transform(fdict(m_GameObject=Pointer(go, 2), m_LocalPosition=vec(0, 0, 0), m_LocalRotation=fdict(x=0, y=0, z=0, w=1),
		m_LocalScale=vec(1, 1, 1), m_Children=[], m_Father=None))
	fields = dict(m_Enabled=True, m_Type=1, m_Color=color(1.0, 0.5, 0.25), m_Intensity=0.75, m_Range=10.0, m_SpotAngle=60.0)
	fields.update(light)
	go._obj["m_Component"] = [(4, Pointer(tr, 1)), (108, Pointer(fdict(**fields), 3))]
	return go


def test_light():
	g = GLB(export_gameobject(lit_gameobject()))
	assert g["extensionsUsed"] == ["KHR_lights_punctual"]
	[light] = g["extensions"]["KHR_lights_punctual"]["lights"]
	assert light == {"type": "directional", "color": [1.0, 0.5, 0.25], "intensity": 0.75}
	[root] = g["scenes"][0]["nodes"]
	[child] = g["nodes"][root]["children"]
	# on a child turned around y: glTF lights shine along -z, Unity's along +z
	assert g["nodes"][child]["extensions"] == {"KHR_lights_punctual": {"light": 0}}
	assert [abs(c) for c in g["nodes"][child]["rotation"]] == [0, 1, 0, 0]


def test_spot_and_disabled_lights():
	spot = GLB(export_gameobject(lit_gameobject(m_Type=0)))["extensions"]["KHR_lights_punctual"]["lights"][0]
	assert spot["range"] == 10.0 and spot["spot"]["outerConeAngle"] == pytest.approx(math.radians(30))
	off = GLB(export_gameobject(lit_gameobject(m_Enabled=False)))
	assert "extensions" not in off.json and "children" not in off["nodes"][0]


def collided_gameobject():
	"A GameObject with no renderer, a mesh collider, a sphere trigger and a kinematic rigidbody"
	go = GameObject(fdict(m_Name="wall", m_Component=[]))
	tr = Transform(fdict(m_GameObject=Pointer(go, 2), m_LocalPosition=vec(0, 0, 0), m_LocalRotation=fdict(x=0, y=0, z=0, w=1),
		m_LocalScale=vec(1, 1, 1), m_Children=[], m_Father=None))
	quad = Pointer(mesh(**QUAD, indices=[0, 1, 2], name="collision"), 3)
	go._obj["m_Component"] = [
		(4, Pointer(tr, 1)),
		(64, Pointer(fdict(m_Material=None, m_IsTrigger=False, m_Convex=True, m_SmoothSphereCollisions=False, m_Mesh=quad), 4)),
		(135, Pointer(fdict(m_Material=None, m_IsTrigger=True, m_Radius=1.5, m_Center=vec(1, 2, 3)), 5)),
		(54, Pointer(fdict(m_Mass=2.0, m_Drag=0.0, m_AngularDrag=0.05, m_UseGravity=True, m_IsKinematic=True), 6)),
	]
	return go


def test_colliders():
	w = GLTFWriter()
	w.add_root(w.add_gameobject(collided_gameobject(), colliders=True))
	g = GLB(w.glb())
	[node] = g["nodes"]
	mesh_collider, sphere = node["extras"]["colliders"]
	assert mesh_collider == {"trigger": False, "type": "mesh", "mesh": 0, "convex": True}
	assert sphere == {"trigger": True, "center": [-1, 2, 3], "type": "sphere", "radius": 1.5}  # x mirrored
	assert node["extras"]["rigidbody"] == {"mass": 2.0, "kinematic": True, "gravity": True}
	# the collision mesh is in the file, but no node draws it
	assert "mesh" not in node and g["meshes"][0]["name"] == "collision"
	assert "material" not in g["meshes"][0]["primitives"][0]


def test_colliders_are_off_by_default():
	g = GLB(export_gameobject(collided_gameobject()))
	assert "extras" not in g["nodes"][0] and "meshes" not in g.json


def test_normals_are_made_unit_length():
	m = mesh(QUAD["vertices"], [(0, 0, 2), (1, 1, 1), (0, 0, 0), (0, 0, 1)], QUAD["uvs"], [0, 1, 2])
	g = GLB(export_mesh(m))
	s = math.sqrt(1 / 3)
	assert g.accessor(g["meshes"][0]["primitives"][0]["attributes"]["NORMAL"]) == approx([(0, 0, 1), (-s, s, s), (0, 1, 0), (0, 0, 1)])


def test_texture_that_is_not_an_image():
	from unitypack.engine.movie import MovieTexture

	movie = Pointer(MovieTexture(fdict(m_Name="tv")), 5)
	w = GLTFWriter()
	w.add_root(w.add_node("m", mesh=w.add_mesh(mesh(**QUAD, indices=[0, 1, 2]), [material(tex=movie)])))
	g = GLB(w.glb())
	assert "baseColorTexture" not in g["materials"][0]["pbrMetallicRoughness"]
	assert "not an image texture" in w.warnings[0]


def test_node_rotations_are_unit_length():
	w = GLTFWriter()
	zero = w.add_node("zero", rotation=[0, 0, 0, 0])  # some files store this: no rotation
	long = w.add_node("long", rotation=[0, 0, 0, 2])
	half = w.add_node("half", rotation=[0, 1, 0, 1])
	nodes = w.json["nodes"]
	assert nodes[zero] == {"name": "zero"}
	assert nodes[long] == {"name": "long"}
	assert nodes[half]["rotation"] == pytest.approx([0, -math.sqrt(0.5), 0, math.sqrt(0.5)])


# External files: images and each mesh's data in a shared folder, named by
# their content, and the .glb pointing at them by relative URI.

def external_export(shared, out, *meshes):
	"A .glb in out with (mesh, materials) on a node each, its images and mesh data in shared; its GLB"
	w = GLTFWriter(external=ExternalFiles(str(shared)))
	for m, materials in meshes:
		w.add_root(w.add_node(m.name, mesh=w.add_mesh(m, materials)))
	out.mkdir(exist_ok=True)
	(out / "x.glb").write_bytes(w.glb(str(out)))
	return GLB((out / "x.glb").read_bytes(), base=out)


def test_external_files_are_shared(tmp_path):
	shared = tmp_path / "shared"
	quad = mesh(**QUAD, indices=[0, 1, 2, 0, 2, 3], name="quad")
	tri = mesh(**QUAD, indices=[0, 1, 2], name="tri")
	a = external_export(shared, tmp_path / "a", (quad, [material("one", tex=Pointer(texture(), 5))]))
	# the same mesh and pixels, read again, with another material and texture name
	quad_again = mesh(**QUAD, indices=[0, 1, 2, 0, 2, 3], name="quad")
	b = external_export(shared, tmp_path / "b", (tri, []), (quad_again, [material("two", tex=Pointer(texture("other"), 6))]))

	assert len(list(shared.glob("*.png"))) == 1
	assert len(list(shared.glob("*.bin"))) == 2  # the quad once, and the triangle
	[png] = shared.glob("*.png")
	for g in (a, b):
		[image] = g["images"]
		assert image["uri"] == "../shared/" + png.name and "bufferView" not in image
		assert image["mimeType"] == "image/png"
		assert g.image(0) == png.read_bytes()
		# nothing left for a BIN chunk: every buffer is a file
		assert g.bin == b"" and all("uri" in buf for buf in g["buffers"])
	assert a["images"][0]["name"] == "tex" and b["images"][0]["name"] == "other"
	quad_uri = a["buffers"][0]["uri"]
	assert quad_uri in [buf["uri"] for buf in b["buffers"]]
	# and the data reads back through them
	p = b["meshes"][1]["primitives"][0]
	assert b.accessor(p["attributes"]["POSITION"]) == approx([(0, 0, 0), (-1, 0, 0), (-1, 1, 0), (0, 1, 0)])
	assert b.accessor(p["indices"]) == [0, 2, 1, 0, 3, 2]


def test_external_mesh_data_does_not_depend_on_its_material(tmp_path):
	shared = tmp_path / "shared"
	no_uvs = dict(QUAD, uvs=[])
	# a textured material on a mesh without UVs becomes an untextured copy: the mesh's data is the same
	external_export(shared, tmp_path / "a", (mesh(**no_uvs, indices=[0, 1, 2]), [material(tex=Pointer(texture(), 5))]))
	external_export(shared, tmp_path / "b", (mesh(**no_uvs, indices=[0, 1, 2]), []))
	assert len(list(shared.glob("*.bin"))) == 1


def test_external_same_mesh_twice_in_one_file(tmp_path):
	"Added once per set of materials, the mesh's data is one buffer"
	quad = mesh(**QUAD, indices=[0, 1, 2], name="quad")
	g = external_export(tmp_path / "shared", tmp_path / "a", (quad, [material("one")]), (quad, [material("two")]))
	assert len(g["meshes"]) == 2 and len(g["buffers"]) == 1


def test_external_skin_and_clips_stay_in_the_glb(tmp_path):
	shared = tmp_path / "shared"
	w = GLTFWriter(external=ExternalFiles(str(shared)))
	w.add_gameobject(rig(clips=[clip(position_curves=[curve_on("bone", key(0.0, vec(1, 0, 0), vec(1, 0, 0)), key(1.0, vec(2, 0, 0), vec(1, 0, 0)))])]))
	g = GLB(w.glb(str(tmp_path)), base=tmp_path)
	assert len(g["buffers"]) == 2 and "uri" not in g["buffers"][0]

	def buffer(accessor):
		return g["bufferViews"][g["accessors"][accessor]["bufferView"]]["buffer"]

	mesh_accessors = [a for m in g["meshes"] for p in m["primitives"] for a in [*p["attributes"].values(), p["indices"]]]
	assert {buffer(a) for a in mesh_accessors} == {1}  # skin weights included
	assert buffer(g["skins"][0]["inverseBindMatrices"]) == 0
	samplers = g["animations"][0]["samplers"]
	assert {buffer(s[k]) for s in samplers for k in ("input", "output")} == {0}
	assert g.accessor(g["meshes"][0]["primitives"][0]["attributes"]["WEIGHTS_0"])[1] == approx([(1, 0, 0, 0)])[0]


def test_external_terrain(tmp_path):
	shared = tmp_path / "shared"
	w = GLTFWriter(external=ExternalFiles(str(shared)))
	w.add_root(w.add_node("t", mesh=w.add_terrain(terrain_data())))
	g = GLB(w.glb(str(tmp_path)), base=tmp_path)
	[p] = g["meshes"][0]["primitives"]
	assert len(g.accessor(p["attributes"]["POSITION"])) == 33 * 33
	assert g.bin == b"" and len(g["buffers"]) == 1
	# the base map, the splat, the control map and the lightmap
	assert len(g["images"]) == 4 and all(i["uri"].startswith("shared/") for i in g["images"])
	assert len(list(shared.glob("*.png"))) == 4


def test_external_uris(tmp_path):
	w = GLTFWriter(external=ExternalFiles(str(tmp_path / "shared files")))
	w.add_root(w.add_node("m", mesh=w.add_mesh(mesh(**QUAD, indices=[0, 1, 2]))))
	name = GLB(w.glb(str(tmp_path / "shared files")), base=tmp_path / "shared files")["buffers"][0]["uri"]
	assert re.fullmatch(r"[0-9a-f]{16}\.bin", name)
	tiles = tmp_path / "tiles" / "x"
	tiles.mkdir(parents=True)
	assert GLB(w.glb(str(tiles)), base=tiles)["buffers"][0]["uri"] == "../../shared%20files/" + name


def test_external_files_put(tmp_path):
	store = ExternalFiles(str(tmp_path / "d"))
	name = store.put(b"data", "bin")
	assert name == hashlib.sha256(b"data").hexdigest()[:16] + ".bin"
	assert store.put(b"data", "bin") == name
	assert store.written == {name} and store.names == {name}
	assert os.listdir(tmp_path / "d") == [name]  # no temporary file left
	# another export's file of that name is not written again
	other = ExternalFiles(str(tmp_path / "d"))
	(tmp_path / "d" / name).write_bytes(b"kept")
	assert other.put(b"data", "bin") == name and other.written == set()
	assert (tmp_path / "d" / name).read_bytes() == b"kept"


# Clips in files of their own

def split_rig():
	"The rig with two clips, a material-only clip and a collider"
	move = clip("move", position_curves=[curve_on("bone", key(0.0, vec(1, 0, 0), vec(1, 0, 0)), key(1.0, vec(2, 0, 0), vec(1, 0, 0)))])
	turn = clip("turn", rotation_curves=[curve_on("bone",
		key(0.0, fdict(x=0, y=0, z=0, w=1), fdict(x=0, y=0, z=0, w=0)), key(1.0, fdict(x=0, y=1, z=0, w=0), fdict(x=0, y=0, z=0, w=0)))])
	scroll = clip("scroll", float_curves=[float_curve("_MainTex.offset.x", key(0.0, 0.0, 1.0), key(1.0, 1.0, 1.0))])
	return rig(clips=[move, turn, scroll])


def test_split_clips():
	w = GLTFWriter()
	w.add_gameobject(split_rig())
	whole = GLB(w.glb())
	base, clips = w.split_clips()
	assert [name for name, _ in clips] == ["move", "turn", "scroll"]

	model = GLB(base.glb())
	assert "animations" not in model.json and "extras" not in model.json
	rig_node = next(i for i, n in enumerate(whole["nodes"]) if n.get("name") == "rig")
	# the rig's Animation lists its clips by name only: the model has none of them
	assert model["nodes"][rig_node]["extras"]["animation"]["clips"] == [{"name": "move"}, {"name": "turn"}, {"name": "scroll"}]
	assert whole["nodes"][rig_node]["extras"]["animation"]["clips"] == [
		{"name": "move", "animation": 0}, {"name": "turn", "animation": 1}, {"name": "scroll", "material_clip": 0}]
	for n in model["nodes"] + whole["nodes"]:
		n.pop("extras", None)
	assert model["nodes"] == whole["nodes"]
	assert model["meshes"] == whole["meshes"] and model["skins"] == whole["skins"]
	skinned = model["meshes"][0]["primitives"][0]["attributes"]
	for attribute, accessor in skinned.items():
		assert model.accessor(accessor) == whole.accessor(whole["meshes"][0]["primitives"][0]["attributes"][attribute])
	assert model.accessor(model["skins"][0]["inverseBindMatrices"]) == whole.accessor(whole["skins"][0]["inverseBindMatrices"])

	for (name, writer), animation in zip(clips, whole["animations"]):
		g = GLB(writer.glb())
		# the same nodes in the same order, without meshes or skins
		assert [n.get("name") for n in g["nodes"]] == [n.get("name") for n in whole["nodes"]]
		assert [n.get("children") for n in g["nodes"]] == [n.get("children") for n in whole["nodes"]]
		assert not any("mesh" in n or "skin" in n for n in g["nodes"])
		for key in ("meshes", "skins", "materials", "images"):
			assert key not in g.json
		[a] = g["animations"]
		assert a["name"] == name == animation["name"]
		# its own clip, as this document numbers it; the others by name
		assert [c for c in g["nodes"][rig_node]["extras"]["animation"]["clips"] if len(c) > 1] == [{"name": name, "animation": 0}]
		assert a["channels"] == animation["channels"]
		for s, ws in zip(a["samplers"], animation["samplers"]):
			assert g.accessor(s["input"]) == whole.accessor(ws["input"])
			assert g.accessor(s["output"]) == whole.accessor(ws["output"])
	scroll = GLB(clips[2][1].glb())
	assert "animations" not in scroll.json
	assert scroll["extras"]["material_clips"] == whole["extras"]["material_clips"]
	assert scroll["nodes"][rig_node]["extras"]["animation"]["clips"][2] == {"name": "scroll", "material_clip": 0}


def test_split_clips_with_external_files(tmp_path):
	w = GLTFWriter(external=ExternalFiles(tmp_path / "shared"))
	w.add_gameobject(split_rig())
	base, clips = w.split_clips()
	model = GLB(base.glb(tmp_path), base=tmp_path)
	assert [b.get("uri", "").startswith("shared/") for b in model["buffers"]] == [False, True]
	assert len(model.accessor(model["meshes"][0]["primitives"][0]["attributes"]["POSITION"])) == 3
	move = GLB(clips[0][1].glb(tmp_path), base=tmp_path)
	assert [b.get("uri") for b in move["buffers"]] == [None]  # the clip's own samples only


def test_split_clips_without_clips():
	w = GLTFWriter()
	w.add_gameobject(gameobject_tree())
	base, clips = w.split_clips()
	assert clips == []
	assert base.glb() == w.glb()
