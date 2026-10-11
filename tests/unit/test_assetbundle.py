"""
Opening archives: unitypack.load() and unitypack/assetbundle.py
(AssetBundle, Asset.from_bundle), on made-up UnityWeb archives.
"""

import io

import pytest

import unitypack
from tests import synth


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


def test_not_an_archive():
	f = io.BytesIO(b"PK\x03\x04" + bytes(60))
	f.name = "x.zip"
	with pytest.raises(NotImplementedError):
		unitypack.load(f)


@pytest.mark.xfail(strict=True, reason="unitypack.load() reads only the first file of an archive")
def test_two_files():
	data = synth.sample_file()
	bundle = load([("BuildPlayer-Map", data), ("BuildPlayer-Map.sharedAssets", data)], "Map.unity3d")
	assert [a.name for a in bundle.assets] == ["BuildPlayer-Map", "BuildPlayer-Map.sharedAssets"]
