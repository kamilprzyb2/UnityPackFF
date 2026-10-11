"""
ffbuildtool extract -> unitypack save -> ffbuildtool pack -> re-open, with no
edits: every object must read back the same. Whether the saved file is byte
for byte the original is recorded: it is for every file Unity wrote.
"""

import pytest

from tests.tools.roundtrip import have_ffbuildtool, roundtrip

pytestmark = [
	pytest.mark.integration,
	pytest.mark.ffbuildtool,
	pytest.mark.skipif(not have_ffbuildtool(), reason="ffbuildtool not installed"),
	pytest.mark.bundles("TableData.resourceFile", "Map_04_02.unity3d", "Map_00_00.unity3d",
		"Icons.resourceFile", "LobbyMusic.resourceFile", "UI_map.resourceFile"),
]


def test_roundtrip(build, bundle, unpacked, expected, tmp_path):
	r = roundtrip(build, bundle, tmp_path)
	# pack-bundle takes the files in directory order, which depends on the filesystem
	assert sorted(r["packed_order"]) == sorted(r["order"])
	for name, f in r["files"].items():
		assert f["extracted_matches"], name
		assert f["packed_matches"], name
		if "digest_before" in f and f["read_errors"] == 0:
			assert "save_error" not in f, f["save_error"]
			assert f["digest_after"] == f["digest_before"], name
	expected.check("roundtrip/" + bundle, {
		n: {k: f.get(k) for k in ("saved", "identical", "save_error")} for n, f in r["files"].items()})
