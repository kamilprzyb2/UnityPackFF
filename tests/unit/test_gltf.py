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
from tests.unit.test_export import QUAD, compressed_mesh, fdict, mesh, packed, submesh, vec
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


def rig(bind_poses=None, clips=(), bones=None):
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
	animation = fdict(m_GameObject=None, m_Animations=[Pointer(c, next(ids)) for c in clips])
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
	assert anim["extras"] == {"float_curves": [":_Color.r"], "events": [{"time": 0.75, "function": "end"}]}
	assert len(anim["channels"]) == 1
	assert "no node for 1 curve paths, such as 'gone'" in w.warnings[0]


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
