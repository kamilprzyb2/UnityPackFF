import json

import pytest

from tests import bundles, synth
from tests.bundles import BUILDS, Build, BundleUnavailable, read_unityweb, sha256

# Moving a pin means a new manifest plus re-recorded expected values in one commit.
PINS = {
	"104": ("ec8063b2-54d4-4ee1-8d9e-381f5babd420", 383),
	"728": ("ec1bd459-9f07-4c2e-9073-99cd7d2062be", 390),
	"1013": ("6543a2bb-d154-4087-b9ee-3c8aa778580a", 454),
}


@pytest.mark.parametrize("alias", BUILDS)
def test_manifest_pinned(alias):
	b = Build(alias)
	uuid, count = PINS[alias]
	assert b.uuid == uuid
	assert len(b.manifest["bundles"]) == count
	assert b.bundles[-1] == bundles.MAIN_FILE
	for name in b.bundles:
		info = b.info(name)["compressed_info"]
		assert len(info["hash"]) == 64 and info["size"] > 0


@pytest.mark.parametrize("alias", BUILDS)
def test_curated_set(alias):
	b = Build(alias)
	assert "TableData.resourceFile" in b.curated
	assert bundles.MAIN_FILE in b.curated
	assert set(b.curated) <= set(b.bundles)
	assert len(b.curated) >= 8


def test_resolve_build():
	assert bundles.resolve_build("1013") == "beta-20111013"
	assert bundles.resolve_build("beta-20111013") == "beta-20111013"
	with pytest.raises(KeyError):
		bundles.resolve_build("2.5")


def test_cache_env(monkeypatch, tmp_path):
	monkeypatch.setenv("UNITYPACKFF_CACHE", str(tmp_path))
	assert Build("728").cache == tmp_path / "beta-20100728"


FILES = [("CustomAssetBundle-test", b"first file" * 10), ("second.sharedAssets", b"x" * 7)]


def test_read_unityweb():
	arc = read_unityweb(synth.unityweb(FILES))
	assert arc.header["signature"] == "UnityWeb"
	assert arc.header["player_version"] == "fusion-2.x.x"
	assert arc.header["engine_version"] == "2.5.4b5"
	assert arc.header["num_levels"] == 1
	assert arc.files() == FILES


def test_read_unityweb_rejects_other_signatures():
	with pytest.raises(ValueError):
		read_unityweb(b"UnityRaw\0" + bytes(64))


@pytest.fixture
def fake_build(tmp_path):
	"A build whose CDN is a local directory, with a manifest made up for it."
	cdn = tmp_path / "cdn"
	cdn.mkdir()
	data = synth.unityweb(FILES)
	(cdn / "Test.resourceFile").write_bytes(data)
	b = Build("104", cache=tmp_path / "cache")
	b.asset_url = cdn.as_uri()
	b.manifest = {"bundles": {"Test.resourceFile": {
		"compressed_info": {"hash": sha256(data), "size": len(data)},
		"uncompressed_info": {n: {"hash": sha256(d), "size": len(d)} for n, d in FILES},
	}}, "main_file_info": {"hash": "0" * 64, "size": 1}}
	return b


def test_fetch_and_unpack(fake_build):
	p = fake_build.fetch("Test.resourceFile")
	assert p.read_bytes() == (fake_build.cache / "Test.resourceFile").read_bytes()
	d = fake_build.unpack("Test.resourceFile")
	assert sorted(f.name for f in d.iterdir()) == [".complete", "CustomAssetBundle-test", "second.sharedAssets"]
	assert [f.read_bytes() for f in fake_build.inner_files("Test.resourceFile")] == [FILES[0][1], FILES[1][1]]


def test_fetch_rejects_bad_hash(fake_build):
	fake_build.manifest["bundles"]["Test.resourceFile"]["compressed_info"]["hash"] = "0" * 64
	with pytest.raises(BundleUnavailable, match="SHA-256"):
		fake_build.fetch("Test.resourceFile")
	assert not fake_build.path("Test.resourceFile").exists()


def test_fetch_missing(fake_build):
	fake_build.manifest["bundles"]["Gone.resourceFile"] = {
		"compressed_info": {"hash": "0" * 64, "size": 1}, "uncompressed_info": {}}
	with pytest.raises(BundleUnavailable):
		fake_build.fetch("Gone.resourceFile")


def test_fetch_from_mirror(fake_build, tmp_path, monkeypatch):
	mirror = tmp_path / "mirror"
	mirror.mkdir()
	(tmp_path / "cdn" / "Test.resourceFile").rename(mirror / "Test.resourceFile")
	monkeypatch.setenv("UNITYPACKFF_MIRROR", str(mirror))
	assert fake_build.fetch("Test.resourceFile").exists()


def test_unpack_checks_inner_hashes(fake_build):
	fake_build.manifest["bundles"]["Test.resourceFile"]["uncompressed_info"]["second.sharedAssets"]["hash"] = "0" * 64
	with pytest.raises(AssertionError, match="manifest"):
		fake_build.unpack("Test.resourceFile")


def test_manifests_hold_no_game_data():
	for p in bundles.MANIFEST_DIR.glob("*.json"):
		m = json.loads(p.read_text())
		assert set(m) <= {"uuid", "asset_url", "name", "parent_uuid", "hidden", "description",
			"main_file_url", "main_file_info", "total_compressed_size", "total_uncompressed_size", "bundles"}


@pytest.mark.parametrize("value, required", [(None, False), ("", False), ("0", False), ("1", True), ("yes", True)])
def test_archives_required(monkeypatch, value, required):
	if value is None:
		monkeypatch.delenv("UNITYPACKFF_REQUIRE_ARCHIVES", raising=False)
	else:
		monkeypatch.setenv("UNITYPACKFF_REQUIRE_ARCHIVES", value)
	assert bundles.archives_required() is required
