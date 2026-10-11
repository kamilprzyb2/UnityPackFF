"""
Smoke tests for the bin/ scripts, run as a user runs them, on made-up files.
"""

import json
import subprocess
import sys

from PIL import Image

from tests import synth
from tests.unit.support import ROOT, open_asset, run_script as run


def test_index_bundles(tmp_path):
	(tmp_path / "X.resourceFile").write_bytes(synth.unityweb([("CustomAssetBundle-x", synth.bundle_file())]))
	(tmp_path / "Map.unity3d").write_bytes(synth.unityweb([("BuildPlayer-Map", synth.sample_file()), ("BuildPlayer-Map.sharedAssets", synth.sample_file())]))
	(tmp_path / "cache").mkdir()
	(tmp_path / "cache" / "CustomAssetBundle-y").write_bytes(synth.bundle_file())  # extracted, as in the client's cache
	(tmp_path / "notes.txt").write_text("not an asset")
	(tmp_path / "Raw.unity3d").write_bytes(b"UnityRaw\0" + bytes(60))  # reported on stderr, not indexed
	null = synth.asset_bundle("n", [], [("assets/gone.txt", 0, 0, (0, 0))])
	(tmp_path / "cache" / "CustomAssetBundle-z").write_bytes(synth.serialized_file({142: synth.ASSET_BUNDLE}, [(1, 142, 142, null)]))

	rows = [line.split("\t") for line in run("index_bundles.py", tmp_path).splitlines()]
	assert rows == [
		["Map.unity3d", "BuildPlayer-Map", "", "", "", ""],  # a scene: no AssetBundle object
		["Map.unity3d", "BuildPlayer-Map.sharedAssets", "", "", "", ""],
		["X.resourceFile", "CustomAssetBundle-x", "2", "0", "TextAsset", "assets/a.txt"],
		["-", "cache/CustomAssetBundle-y", "2", "0", "TextAsset", "assets/a.txt"],
		["-", "cache/CustomAssetBundle-z", "", "", "", "assets/gone.txt"],  # a null pointer
	]


# dump_terrain.py / replace_terrain.py on a made-up 33 x 33 heightmap

def terrain_heights(f):
	t = open_asset(f.read_bytes()).objects[1].read()
	return t.heights, t.shifts


def test_dump_terrain(tmp_path):
	heights = [x * 100 + z for x in range(33) for z in range(33)]  # x-major
	(tmp_path / "t").write_bytes(synth.terrain_file(heights, [(1, 2, 8)]))
	run("dump_terrain.py", tmp_path / "t", tmp_path / "h.png")

	img = Image.open(tmp_path / "h.png")
	assert img.mode in ("I;16", "I") and img.size == (33, 33)
	# north up: column x, row 32 - z
	assert img.getpixel((5, 32)) == 500  # x 5, z 0
	assert img.getpixel((5, 0)) == 532  # x 5, z 32
	assert img.getpixel((0, 30)) == 2
	shifts = json.loads((tmp_path / "h.shifts.json").read_text())
	assert shifts["shifts"] == [[1, 2, 8]]
	assert (shifts["width"], shifts["max_height"], shifts["scale"]) == (33, 32766, [4.0, 600.0, 4.0])


def test_replace_terrain(tmp_path):
	heights = [x * 100 + z for x in range(33) for z in range(33)]
	(tmp_path / "t").write_bytes(synth.terrain_file(heights, [(1, 2, 8)]))
	run("dump_terrain.py", tmp_path / "t", tmp_path / "h.png")

	img = Image.open(tmp_path / "h.png")
	img.putpixel((5, 32), 32766)  # x 5, z 0
	img.save(tmp_path / "h2.png")
	(tmp_path / "s.json").write_text(json.dumps({"shifts": [[3, 4, 2], [0, 0, 5]]}))

	run("replace_terrain.py", tmp_path / "h2.png", tmp_path / "t", "-o", tmp_path / "t2")
	new, shifts = terrain_heights(tmp_path / "t2")
	assert new == heights[:5 * 33] + [32766] + heights[5 * 33 + 1:]
	assert shifts == [(1, 2, 8)]  # kept

	run("replace_terrain.py", tmp_path / "h.png", tmp_path / "t2", "--shifts", tmp_path / "s.json")  # in place
	new, shifts = terrain_heights(tmp_path / "t2")
	assert new == heights
	assert shifts == [(3, 4, 2), (0, 0, 5)]


def test_replace_terrain_8_bit(tmp_path):
	# as older versions of dump_terrain.py wrote them: one row per x, 255 = 20000

	(tmp_path / "t").write_bytes(synth.terrain_file([0] * (33 * 33)))
	img = Image.new("RGB", (33, 33))
	img.putpixel((7, 2), (255, 255, 255))  # x 2, z 7
	img.save(tmp_path / "old.png")
	run("replace_terrain.py", tmp_path / "old.png", tmp_path / "t")
	new, _ = terrain_heights(tmp_path / "t")
	assert new[2 * 33 + 7] == 20000
	assert sum(new) == 20000


def test_replace_terrain_rejects_old_usage(tmp_path):
	(tmp_path / "t").write_bytes(synth.terrain_file([0] * (33 * 33)))
	r = subprocess.run([sys.executable, str(ROOT / "bin" / "replace_terrain.py"), "x.png", "0x1234", str(tmp_path / "t")],
		capture_output=True, text=True, env={"PYTHONPATH": str(ROOT)})
	assert r.returncode != 0
	assert "no longer takes an offset" in r.stderr
