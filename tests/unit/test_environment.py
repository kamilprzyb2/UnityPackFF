"""
Pointers into other files: unitypack/environment.py (UnityEnvironment,
find_file) finding the files an asset refers to, on made-up files in a
temporary folder.

Files name the files they point into in lowercase
("customassetbundle-..."), while the files on disk keep their case; the
lookup must work on case-sensitive file systems too.
"""

import pytest

from unitypack.asset import Asset
from unitypack.environment import UnityEnvironment, find_file
from tests import synth


def pointing_file(target="customassetbundle-other"):
	"A Thing whose m_Other points at path ID 1 of the first external file."
	return synth.serialized_file({1000: synth.THING}, [(1, 1000, 1000, synth.thing(0, 0.0, False, "", (1, 1), []))], externals=[target])


def resolve(path):
	with open(path, "rb") as f:
		return Asset.from_file(f).objects[1].read()["m_Other"].resolve()


def test_target_beside_the_file(tmp_path):
	(tmp_path / "CustomAssetBundle-main").write_bytes(pointing_file())
	(tmp_path / "CustomAssetBundle-other").write_bytes(synth.sample_file())
	assert resolve(tmp_path / "CustomAssetBundle-main").script == "world"


def test_target_one_folder_down(tmp_path):
	# the client's cache: one folder per archive
	(tmp_path / "Main.resourceFile").mkdir()
	(tmp_path / "Other.resourceFile").mkdir()
	(tmp_path / "Main.resourceFile" / "CustomAssetBundle-main").write_bytes(pointing_file())
	(tmp_path / "Other.resourceFile" / "CustomAssetBundle-other").write_bytes(synth.sample_file())
	with open(tmp_path / "Main.resourceFile" / "CustomAssetBundle-main", "rb") as f:
		asset = Asset.from_file(f)
		asset.environment.base_path = str(tmp_path)  # as ffextract.py points it at the cache
		assert asset.objects[1].read()["m_Other"].resolve().script == "world"


def test_find_file(tmp_path):
	(tmp_path / "CustomAssetBundle-a").write_bytes(b"")
	(tmp_path / "customassetbundle-b").write_bytes(b"")
	(tmp_path / "CustomAssetBundle-b").write_bytes(b"")
	(tmp_path / "customassetbundle-c").mkdir()
	assert find_file(str(tmp_path), "customassetbundle-a") == str(tmp_path / "CustomAssetBundle-a")
	assert find_file(str(tmp_path), "CustomAssetBundle-b") == str(tmp_path / "CustomAssetBundle-b")  # exact match first
	assert find_file(str(tmp_path), "customassetbundle-c") is None  # folders don't count
	assert find_file(str(tmp_path), "customassetbundle-d") is None
	assert find_file(str(tmp_path / "missing"), "x") is None


def test_missing_file_in_the_default_environment(tmp_path, monkeypatch):
	# base_path "" means the current directory, as for a file opened by bare name
	monkeypatch.chdir(tmp_path)
	with pytest.raises(KeyError):
		UnityEnvironment().get_asset_by_filename("customassetbundle-missing")
