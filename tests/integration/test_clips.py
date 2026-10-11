"""
AnimationClips of real archives: curve and key counts, lengths, and a digest
of every curve sampled at the clip's rate. Compressed clips come from the
nanos of every build, plain ones from Retrobution's own additions. Every
rotation key is a unit quaternion, and compressed rotation curves end with
the clip's position curves, to the 0.01 s their key times are stored in.
"""

import pytest

from clips import CLIP, clip_stats

from tests import serialized

pytestmark = [
	pytest.mark.integration,
	pytest.mark.bundles("FutureNano.resourceFile", "Nano_001.resourceFile", "Retro_shared_part2.resourceFile"),
]


def test_clips(build, bundle, unpacked, expected):
	stats = clip_stats(build, bundle)
	assert stats
	expected.check("clips/" + bundle, stats)


def clips(build, bundle):
	from unitypack.asset import Asset

	for path in build.inner_files(bundle):
		if not serialized.looks_serialized(path.read_bytes()):
			continue
		with open(path, "rb") as f:
			for obj in Asset.from_file(f).objects.values():
				if obj.type_id == CLIP:
					yield obj.read()


def test_rotation_keys(build, bundle, unpacked):
	for clip in clips(build, bundle):
		rotations = clip.rotation_curves
		for c in rotations.values():
			for k in c.keys:
				assert sum(x * x for x in k.value) == pytest.approx(1, abs=1e-5)
		positions = clip.position_curves
		if clip.compressed and positions:
			end = max(c.end_time for c in positions.values())
			assert max(c.end_time for c in rotations.values()) == pytest.approx(end, abs=0.0101)
