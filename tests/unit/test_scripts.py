"""
Smoke tests for the bin/ scripts, run as a user runs them, on made-up files.
"""

import os
import subprocess
import sys
from pathlib import Path

from tests import synth

ROOT = Path(__file__).resolve().parents[2]


def run(script, *args):
	env = dict(os.environ, PYTHONPATH=str(ROOT))  # this checkout's unitypack
	return subprocess.run([sys.executable, str(ROOT / "bin" / script), *map(str, args)],
		capture_output=True, text=True, check=True, env=env).stdout


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
