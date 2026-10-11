"""
Terrains of real archives: heightmap statistics, and how Unity's
level-of-detail data compares with what TerrainData works out (min/max to
float32 precision everywhere; errors equal, or larger where there are
shifts, never smaller). bin/dump_terrain.py and bin/replace_terrain.py
with no edit in between save the file exactly as Asset.save() does.
"""

import io
import shutil

import pytest

from terrain import TERRAIN, terrain_stats

from tests import serialized
from tests.unit.support import run_script

pytestmark = [pytest.mark.integration, pytest.mark.bundles("DongResources_04_02.resourceFile")]


def test_terrain(build, bundle, unpacked, expected):
	stats = terrain_stats(build, bundle)
	assert stats
	for t in stats.values():
		assert t["lod"]["minmax_other"] == 0
		assert t["lod"]["error_smaller"] == 0
		if not t["shifts"]:
			assert t["lod"]["error_larger"] == 0
	expected.check("terrain/" + bundle, stats)


def test_dump_replace_round_trip(build, bundle, unpacked, tmp_path):
	from unitypack.asset import Asset

	for path in build.inner_files(bundle):
		data = path.read_bytes()
		if not serialized.looks_serialized(data):
			continue
		asset = Asset.from_file(io.BytesIO(data))
		if not any(o.type_id == TERRAIN for o in asset.objects.values()):
			continue
		saved = io.BytesIO()
		asset.save(saved)

		shutil.copy(path, tmp_path / "tile")
		run_script("dump_terrain.py", tmp_path / "tile", tmp_path / "h.png")
		run_script("replace_terrain.py", tmp_path / "h.png", tmp_path / "tile", "--shifts", tmp_path / "h.shifts.json")
		assert (tmp_path / "tile").read_bytes() == saved.getvalue()
