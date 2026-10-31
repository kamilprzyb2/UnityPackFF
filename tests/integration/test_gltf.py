"""
glTF export of real prefabs: what each document holds (node, mesh and
material counts, image sizes, a digest of every accessor), and, when the
Khronos validator is installed (see tests/tools/gltf.py), that every file
passes it with no errors and no warnings. bin/export_gltf.py exports one.
"""

import json
import re

import pytest

from tests.tools.gltf import CuratedEnvironment, export_prefabs, have_validator, validate

from tests.unit.support import run_script

pytestmark = [
	pytest.mark.integration,
	pytest.mark.bundles("FutureNano.resourceFile", "Nano_001.resourceFile", "CharacterSelection_wear_face.resourceFile",
		"World_shared_part1.resourceFile"),
]


@pytest.fixture
def env_dir(build):
	env = CuratedEnvironment(build)
	yield env.dir
	env.close()


def test_gltf(build, bundle, unpacked, expected, env_dir, tmp_path):
	stats = export_prefabs(build, bundle, env_dir, tmp_path)
	files = [p.pop("_file") for p in stats.values()]
	expected.check("gltf/" + bundle, stats)

	if have_validator():
		issues = validate(files)
		assert {f: r for f, r in issues.items() if r["errors"] or r["warnings"]} == {}


def test_export_gltf_script(build, bundle, unpacked, env_dir, tmp_path):
	from tests.glb import GLB

	path = build.inner_files(bundle)[0]
	out = run_script("export_gltf.py", path, "-o", tmp_path, "--env", env_dir)
	count = int(out.strip().splitlines()[-1].split()[0])
	files = sorted(tmp_path.glob("*.glb"))
	assert len(files) == count
	for f in files:
		GLB(f.read_bytes())


@pytest.mark.bundles("Map_04_02.unity3d", "Map_00_00.unity3d")
def test_scene(build, bundle, unpacked, expected, tmp_path):
	from tests.tools.gltf import export_scenes

	env = CuratedEnvironment(build)
	try:
		env.add_references(bundle)
		stats = export_scenes(build, bundle, env.dir, tmp_path)
	finally:
		env.close()
	files = [s.pop("_file") for s in stats.values()]
	assert stats
	expected.check("gltf-scene/" + bundle, stats)
	if bundle.startswith("Map_04_02"):
		assert all(s["terrains"] == 1 for s in stats.values())
		assert all(s["detail_layers"] > 0 for s in stats.values())

	if have_validator():
		issues = validate(files)
		assert {f: r for f, r in issues.items() if r["errors"] or r["warnings"]} == {}


@pytest.mark.bundles("FutureNano.resourceFile")
def test_export_gltf_xdt_textures(build, bundle, unpacked, expected, env_dir, tmp_path):
	"""
	The nanos with the textures the game puts on them (the XDT's, found in
	the curated TableData and NpcTexture) against their own: Eduardo's face
	material stores his body texture, and the XDT names his face texture.
	"""
	from tests.glb import GLB

	path = build.inner_files(bundle)[0]
	run_script("export_gltf.py", path, "nano/", "-o", tmp_path / "xdt", "--env", env_dir)
	run_script("export_gltf.py", path, "nano/", "-o", tmp_path / "own", "--env", env_dir, "--no-xdt")

	def images(folder):
		"{file: [(material, image)]}"
		ret = {}
		for f in sorted(folder.glob("*.glb")):
			g = GLB(f.read_bytes())
			pairs = []
			for m in g.get("materials", []):
				t = m["pbrMetallicRoughness"].get("baseColorTexture")
				pairs.append([m["name"], g["images"][g["textures"][t["index"]]["source"]]["name"] if t else None])
			ret[f.name] = pairs
		return ret

	xdt, own = images(tmp_path / "xdt"), images(tmp_path / "own")
	assert xdt and xdt.keys() == own.keys()
	eduardo = "nano_nano_eduardo.kfm.glb"
	if eduardo in xdt:
		face = "nano_eduardo-sub-link_b.dds"
		assert dict(own[eduardo])[face] == "nano_eduardo"
		assert dict(xdt[eduardo])[face] == "nano_eduardo_face"
	expected.check("gltf-xdt/" + bundle, xdt)


# 1013 keeps its wearables in CharacterSelection_wear_* archives
@pytest.mark.bundles("CharacterSelection.resourceFile", "CharacterSelection_wear_face.resourceFile")
def test_export_gltf_wearables(build, bundle, unpacked, expected, tmp_path):
	"""
	A shirt, a face and a hat with the textures the game gives them: the
	XDT's item, face and hair tables, the textures from the character texture
	archives. A shirt's
	"sub" materials take the skin; a face comes once per eye colour.
	"""
	from tests.glb import GLB

	env = CuratedEnvironment(build)
	try:
		# where the clothes' textures are: one archive, or one per kind in 1013
		for name in build.bundles:
			if name.startswith(("CharTexture", "Character_Texture_")):
				env.link(name)
		path = build.inner_files(bundle)[0]
		run_script("export_gltf.py", path, "wear/m_shirt_easy", "wear/m_face_001_type01", "wear/hat_", "-o", tmp_path, "--env", env.dir)
	finally:
		env.close()

	def images(f):
		g = GLB(f.read_bytes())
		pairs = []
		for m in g.get("materials", []):
			t = m["pbrMetallicRoughness"].get("baseColorTexture")
			pairs.append([m["name"], g["images"][g["textures"][t["index"]]["source"]]["name"] if t else None])
		return pairs

	got = {f.name: images(f) for f in sorted(tmp_path.glob("*.glb"))}
	if not got:
		pytest.skip("no wearables in this archive")  # 1013's CharacterSelection
	faces = [n for n in got if n.startswith("wear_m_face_001_type01.nif@m_face_001_")]
	if faces:
		assert len(faces) == 5  # eye colours a to e
	expected.check("gltf-wear/" + bundle, got)


@pytest.mark.bundles("CharacterSelection.resourceFile", "CharacterSelection_wear_head.resourceFile", "CharacterSelection_wear_hat.resourceFile")
def test_export_gltf_as_worn(build, bundle, unpacked, tmp_path):
	"""
	Hair and hats with --as-worn and without: hair m_head_001_type02 loses
	the 53 Biped helper boxes beside its skinned mesh, and every other file,
	hats included (hung on the body whole), comes out the same.
	"""
	from tests.glb import GLB

	env = CuratedEnvironment(build)
	try:
		path = build.inner_files(bundle)[0]
		wanted = ("wear/m_head_001_type02", "wear/hat_")
		worn = run_script("export_gltf.py", path, *wanted, "-o", tmp_path / "worn", "--env", env.dir, "--as-worn")
		run_script("export_gltf.py", path, *wanted, "-o", tmp_path / "all", "--env", env.dir)
	finally:
		env.close()

	files = sorted(f.name for f in (tmp_path / "worn").glob("*.glb"))
	if not files:
		pytest.skip("no hair or hats in this archive")  # 1013's CharacterSelection
	assert files == sorted(f.name for f in (tmp_path / "all").glob("*.glb"))
	changed = {f for f in files if (tmp_path / "worn" / f).read_bytes() != (tmp_path / "all" / f).read_bytes()}
	hair = "wear_m_head_001_type02.nif.glb"
	assert changed == ({hair} & set(files))
	if hair in files:
		assert len(GLB((tmp_path / "worn" / hair).read_bytes())["meshes"]) == 1
		assert len(GLB((tmp_path / "all" / hair).read_bytes())["meshes"]) == 54
		assert "53 plain meshes left out of what players wear" in worn


# 1013 keeps its actors in CharacterCreation and its shirts in CharacterSelection_wear_shirts
@pytest.mark.bundles("CharacterSelection.resourceFile", "CharacterCreation.resourceFile", "CharacterSelection_wear_shirts.resourceFile")
def test_export_gltf_actor_bones(build, bundle, unpacked, expected, tmp_path):
	"""
	--actor-bones: the actors' bone lists (133 for boys, 159 for girls),
	every entry a node, and each shirt's skins bound to them, one index per
	joint and within the actor's list
	"""
	from tests.digest import object_digest as digest
	from tests.glb import GLB

	env = CuratedEnvironment(build)
	try:
		path = build.inner_files(bundle)[0]
		out = run_script("export_gltf.py", path, "actor/m.kfm", "actor/w.kfm", "wear/m_shirt_easy", "wear/f_shirt_easy", "-o", tmp_path,
			"--env", env.dir, "--actor-bones")
	finally:
		env.close()
	got = {}
	for f in sorted(tmp_path.glob("*.glb")):
		g = GLB(f.read_bytes())
		if f.name.startswith("actor_"):
			bones = g["nodes"][g["scenes"][0]["nodes"][0]]["extras"]["actor_bones"]
			assert len(bones) == {"actor_m.kfm.glb": 133, "actor_w.kfm.glb": 159}[f.name]
			assert all(0 <= b < len(g["nodes"]) for b in bones)
			got[f.name] = digest(bones)
			continue
		skins = g["skins"]
		assert skins
		for skin in skins:
			bound = skin["extras"]["actor_bones"]
			assert len(bound["m"]) == len(bound["f"]) == len(skin["joints"])
			assert all(0 <= i < 133 for i in bound["m"]) and all(0 <= i < 159 for i in bound["f"])
		got[f.name] = digest([s["extras"]["actor_bones"] for s in skins])
	if not got:
		pytest.skip("no actors or shirts in this archive")
	assert "ActorWearIndexTable" not in out
	expected.check("gltf-actor-bones/" + bundle, got)


@pytest.mark.bundles("CharacterSelection.resourceFile", "CharacterCreation.resourceFile")
def test_export_gltf_float_curves(build, bundle, unpacked, tmp_path):
	"""
	The boy's build: the shape clip moves the materials' _FatFactor from
	0.0075 through 0 to -0.0078, the height clip keeps it at 0; both are
	sampled into the animations' extras
	"""
	from tests.glb import GLB

	env = CuratedEnvironment(build)
	try:
		run_script("export_gltf.py", build.inner_files(bundle)[0], "actor/m.kfm", "-o", tmp_path, "--env", env.dir)
	finally:
		env.close()
	f = tmp_path / "actor_m.kfm.glb"
	if not f.exists():
		pytest.skip("no actors in this archive")
	anims = {a["name"]: a for a in GLB(f.read_bytes())["animations"]}
	shape, height = anims["shape"]["extras"]["float_curves"], anims["height"]["extras"]["float_curves"]
	assert sorted(shape) == sorted(height) == sorted(":[%d]._FatFactor" % i for i in range(10))
	for track in shape.values():
		at = dict(zip(track["times"], track["values"]))
		assert [at[0.0], at[0.5], at[1.0]] == pytest.approx([0.0075, 0.0, -0.0078])
	assert all(track["values"] == [0.0] for track in height.values())


@pytest.mark.bundles("CharacterSelection.resourceFile", "CharacterCreation.resourceFile")
def test_export_gltf_split_clips(build, bundle, unpacked, tmp_path):
	"""
	--split-clips on the boy actor: the model without clips, and a file per
	clip with the same nodes and that clip as the whole export has it
	"""
	from tests.glb import GLB

	env = CuratedEnvironment(build)
	try:
		path = build.inner_files(bundle)[0]
		run_script("export_gltf.py", path, "actor/m.kfm", "-o", tmp_path / "split", "--env", env.dir, "--split-clips")
		run_script("export_gltf.py", path, "actor/m.kfm", "-o", tmp_path / "whole", "--env", env.dir)
	finally:
		env.close()
	whole_file = tmp_path / "whole" / "actor_m.kfm.glb"
	if not whole_file.exists():
		pytest.skip("no actors in this archive")
	whole = GLB(whole_file.read_bytes())
	model = GLB((tmp_path / "split" / "actor_m.kfm.glb").read_bytes())
	assert "animations" not in model.json

	def by_names(nodes):
		"The nodes with their Animations' clips by name only (each file numbers its own clips)"
		nodes = json.loads(json.dumps(nodes))
		for n in nodes:
			for c in n.get("extras", {}).get("animation", {}).get("clips", []):
				c.pop("animation", None)
				c.pop("material_clip", None)
		return nodes

	assert model["nodes"] == by_names(whole["nodes"]) and model.get("meshes") == whole.get("meshes")

	clips = sorted((tmp_path / "split").glob("actor_m.kfm@*.glb"))
	assert len(clips) == len(whole["animations"]) > 200
	by_name = {a["name"]: a for a in whole["animations"]}
	for f in clips:
		g = GLB(f.read_bytes())
		assert by_names(g["nodes"]) == [{k: v for k, v in n.items() if k not in ("mesh", "skin")} for n in by_names(whole["nodes"])]
		[a] = g["animations"]
		refs = [c for n in g["nodes"] for c in n.get("extras", {}).get("animation", {}).get("clips", []) if "animation" in c]
		assert refs and all(c == {"name": a["name"], "animation": 0} for c in refs)
		assert f.name == "actor_m.kfm@%s.glb" % re.sub(r"[^\w.@-]", "_", a["name"])
		w = by_name[a["name"]]
		assert a["channels"] == w["channels"] and a.get("extras") == w.get("extras")
		assert [g.accessor(s["output"]) for s in a["samplers"]] == [whole.accessor(s["output"]) for s in w["samplers"]]

	if have_validator():
		issues = validate([tmp_path / "split" / "actor_m.kfm.glb"] + clips[:20])
		assert {f: r for f, r in issues.items() if r["errors"] or r["warnings"]} == {}


@pytest.mark.bundles("Map_04_02.unity3d")
def test_external_files(build, bundle, unpacked, tmp_path):
	"""
	Two neighbouring map tiles exported with --external into one folder:
	they share files, so the folder holds fewer than the two reference, and
	each holds what the embedded export does.
	"""
	import os

	from tests.tools.gltf import summary

	from tests.glb import GLB

	neighbour = "Map_04_03.unity3d"
	if neighbour not in build.bundles:
		pytest.skip("no %s in this build" % neighbour)
	env = CuratedEnvironment(build)
	try:
		scenes = []
		for name in (bundle, neighbour):
			env.add_references(name)
			scenes += [p for p in build.inner_files(name) if p.name.startswith("BuildPlayer-") and not p.name.endswith(".sharedAssets")]
		for path in scenes:
			run_script("export_gltf.py", path, "-o", tmp_path / "tiles", "--env", env.dir, "--external", tmp_path / "shared")
			run_script("export_gltf.py", path, "-o", tmp_path / "embedded", "--env", env.dir)
	finally:
		env.close()

	files = sorted((tmp_path / "tiles").glob("*.glb"))
	assert len(files) == 2
	referenced = []
	for f in files:
		g = GLB(f.read_bytes(), base=f.parent)
		uris = [x["uri"] for k in ("images", "buffers") for x in g.get(k, []) if "uri" in x]
		assert uris and all(u.startswith("../shared/") for u in uris)
		assert not any("bufferView" in i for i in g.get("images", []))
		referenced.append({os.path.basename(u) for u in uris})
		assert summary(f.read_bytes(), f.parent) == summary((tmp_path / "embedded" / f.name).read_bytes())
	shared = {p.name for p in (tmp_path / "shared").iterdir()}
	assert shared == referenced[0] | referenced[1]
	assert len(shared) < len(referenced[0]) + len(referenced[1])

	if have_validator():
		issues = validate(files)
		assert {f: r for f, r in issues.items() if r["errors"] or r["warnings"]} == {}
