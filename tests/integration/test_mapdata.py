"""
FusionFall's per-map data on real archives: the attribute grid (flag
counts and a digest) and colours of a map tile, and the music and ambient
zones of main.unity3d.
"""

import pytest

from mapdata import mapdata_stats

pytestmark = [pytest.mark.integration, pytest.mark.bundles("Map_04_02.unity3d", "Map_00_00.unity3d", "main.unity3d")]


def test_mapdata(build, bundle, unpacked, expected):
	stats = mapdata_stats(build, bundle)
	if bundle.startswith("Map_04_02"):
		[scene] = stats.values()
		assert scene["attributes"]["size"] == 129
		assert "colors" in scene
	if bundle == "main.unity3d":
		[storage] = stats.values()
		assert storage["music"]["zones"] > 0 and storage["ambient"]["zones"] > 0
	expected.check("mapdata/" + bundle, stats)
