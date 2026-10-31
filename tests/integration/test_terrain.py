"""
Terrains of real archives: heightmap statistics, and how Unity's
level-of-detail data compares with what TerrainData works out (min/max to
float32 precision everywhere; errors equal, or larger where there are
shifts, never smaller). bin/dump_terrain.py and bin/replace_terrain.py
with no edit in between save the file exactly as Asset.save() does. The
details (grass) hold as many instances as the patches store.
"""

import io
import shutil

import pytest

from tests.tools.terrain import TERRAIN, terrain_stats

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


def test_details(build, bundle, unpacked):
	from unitypack.asset import Asset

	for path in build.inner_files(bundle):
		if not serialized.looks_serialized(path.read_bytes()):
			continue
		with open(path, "rb") as f:
			for obj in Asset.from_file(f).objects.values():
				if obj.type_id != TERRAIN:
					continue
				t = obj.read()
				db = t.detail_database
				stored = sum(sum(p["numberOfObjects"]) for p in db["m_Patches"])
				assert stored > 0
				assert sum(sum(layer) for layer in t.detail_layers().values()) == stored
				assert all(len(layer) == t.detail_resolution ** 2 for layer in t.detail_layers().values())


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
