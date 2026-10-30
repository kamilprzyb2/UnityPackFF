"""
glTF export of real prefabs: what each document holds (node, mesh and
material counts, image sizes, a digest of every accessor), and, when the
Khronos validator is installed (see tests/tools/gltf.py), that every file
passes it with no errors and no warnings. bin/export_gltf.py exports one.
"""

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
