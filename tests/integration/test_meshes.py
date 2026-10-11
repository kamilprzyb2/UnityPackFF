"""
Meshes of real archives: what OBJMesh exports (a digest of the OBJ text) and,
for skinned meshes, how many vertices have how many weights. Every skinned
vertex's weights add up to 1.
"""

import pytest

from meshes import MESH, mesh_stats

from tests import serialized

pytestmark = [
	pytest.mark.integration,
	# nanos (Academy has one archive per nano) and Academy's character faces
	pytest.mark.bundles("FutureNano.resourceFile", "Nano_001.resourceFile", "CharacterSelection_wear_face.resourceFile"),
]


def test_meshes(build, bundle, unpacked, expected):
	stats = mesh_stats(build, bundle)
	assert any("skin" in m for f in stats.values() for m in f.values())
	expected.check("meshes/" + bundle, stats)


def test_skin_weights_add_up(build, bundle, unpacked):
	from unitypack.asset import Asset
	from unitypack.export import CompressedMeshData

	for path in build.inner_files(bundle):
		if not serialized.looks_serialized(path.read_bytes()):
			continue
		with open(path, "rb") as f:
			for obj in Asset.from_file(f).objects.values():
				if obj.type_id != MESH:
					continue
				for vertex in CompressedMeshData(obj.read()).skin:
					assert 1 <= len(vertex) <= 4
					assert sum(w for _, w in vertex) == pytest.approx(1)
