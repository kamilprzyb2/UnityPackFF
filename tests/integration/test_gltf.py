"""
glTF export of real prefabs: what each document holds (node, mesh and
material counts, image sizes, a digest of every accessor), and, when the
Khronos validator is installed (see tests/demo/gltf.py), that every file
passes it with no errors and no warnings. bin/export_gltf.py exports one.
"""

import pytest

from gltf import CuratedEnvironment, export_prefabs, have_validator, validate

from tests.unit.support import run_script

pytestmark = [
	pytest.mark.integration,
	pytest.mark.bundles("FutureNano.resourceFile", "Nano_001.resourceFile", "CharacterSelection_wear_face.resourceFile",
		"World_shared_part1.resourceFile", "Retro_shared_part2.resourceFile"),
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
	from gltf import export_scenes

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
