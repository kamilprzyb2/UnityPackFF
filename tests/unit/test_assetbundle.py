"""
Opening archives: unitypack.load() and unitypack/assetbundle.py
(AssetBundle, Asset.from_bundle), on made-up UnityWeb archives.
"""

import io

import pytest

import unitypack
from tests import synth
from unitypack.environment import UnityEnvironment


def load(files, name="X.resourceFile"):
	f = io.BytesIO(synth.unityweb(files))
	f.name = name
	return unitypack.load(f)


def test_one_file():
	bundle = load([("CustomAssetBundle-x", synth.sample_file())])
	assert bundle.signature == "UnityWeb"
	assert bundle.compressed
	assert [a.name for a in bundle.assets] == ["CustomAssetBundle-x"]
	assert bundle.assets[0].objects[1].read().script == "world"


def test_header_fields():
	bundle = load([("CustomAssetBundle-x", synth.sample_file())])
	assert bundle.unity_version == "fusion-2.x.x"  # what UnityPackFF calls the player version
	assert bundle.generator_version == "2.5.4b5"


def test_unnamed_archives():
	# BytesIO has no .name; two of them in one environment are still two bundles
	env = UnityEnvironment()
	a = env.load(io.BytesIO(synth.unityweb([("CustomAssetBundle-a", synth.sample_file())])))
	b = env.load(io.BytesIO(synth.unityweb([("CustomAssetBundle-b", synth.bundle_file())])))
	assert [x.name for x in a.assets + b.assets] == ["CustomAssetBundle-a", "CustomAssetBundle-b"]
	assert list(env.bundles.values()) == [a, b]
	assert b.assets[0].objects[2].read().script == "first"


def test_not_an_archive():
	f = io.BytesIO(b"PK\x03\x04" + bytes(60))
	f.name = "x.zip"
	with pytest.raises(NotImplementedError):
		unitypack.load(f)


def test_two_files():
	# like a map tile: the scene and its .sharedAssets
	bundle = load([("BuildPlayer-Map", synth.sample_file()), ("BuildPlayer-Map.sharedAssets", synth.bundle_file())], "Map.unity3d")
	assert [a.name for a in bundle.assets] == ["BuildPlayer-Map", "BuildPlayer-Map.sharedAssets"]
	assert bundle.assets[0].objects[1].read().script == "world"
	assert bundle.assets[1].objects[2].read().script == "first"
