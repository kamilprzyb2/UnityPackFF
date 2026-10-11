"""
Terrain: unitypack/engine/terrain.py (TerrainData) on made-up heightmaps
(tests/synth.py terrain_file), read and saved with unitypack.

A heightmap is (2^n * 16 + 1)^2 vertices, stored x-major (vertex (x, z) at
x * height + z) as integers from 0 to 32766. Its level-of-detail patches
(16 x 16 cells at level 0, twice as wide at each level up, keeping every
2^level-th vertex) each have a min/max height and an error.
"""

import pytest

from tests import synth
from tests.unit.support import open_asset, save
from unitypack.engine.terrain import MAX_HEIGHT, Shift, TerrainData

SIZE = 33  # 2 x 2 patches at level 0, one at level 1


def terrain(heights=None, shifts=(), **kw):
	heights = heights if heights is not None else [1000] * (SIZE * SIZE)
	asset = open_asset(synth.terrain_file(heights, shifts, size=SIZE, **kw))
	return asset, asset.objects[1].contents  # kept, so save() writes changes


def flat_with(points, base=1000):
	"Flat heights, with {(x, z): height} set"
	heights = [base] * (SIZE * SIZE)
	for (x, z), h in points.items():
		heights[x * SIZE + z] = h
	return heights


def test_reads_as_terrain_data():
	_, t = terrain(flat_with({(2, 5): 3000}), [(3, 4, 2)])
	assert isinstance(t, TerrainData)
	assert (t.width, t.height, t.scale) == (SIZE, SIZE, (4.0, 600.0, 4.0))
	# x-major
	assert t.heights[2 * SIZE + 5] == 3000
	assert t.get_height(2, 5) == pytest.approx(3000 / MAX_HEIGHT * 600)
	# the records call z "y"
	assert t.shifts == [Shift(3, 4, 2)]


def test_patches():
	_, t = terrain()
	# level 0 first, x before z; then level 1, every other vertex
	assert list(t.patches()) == [
		(0, 0, 0, 16, 1), (0, 16, 0, 16, 1), (0, 0, 16, 16, 1), (0, 16, 16, 16, 1),
		(1, 0, 0, 32, 2),
	]


def test_min_max_counts_the_vertices_a_patch_keeps():
	# a peak at an odd vertex is in its level-0 patch but dropped at level 1
	asset, t = terrain(flat_with({(17, 3): 5000, (4, 20): 200}))
	t.update_lod()  # every patch
	mm = t.heightmap["m_MinMaxPatchHeights"]
	f = lambda h: pytest.approx(h / MAX_HEIGHT)
	assert mm[0:2] == [f(1000), f(1000)]  # patch (0, 0)
	assert mm[2:4] == [f(1000), f(5000)]  # patch (1, 0) holds x 17
	assert mm[4:6] == [f(200), f(1000)]  # patch (0, 1) holds z 20
	assert mm[8:10] == [f(200), f(1000)]  # level 1 keeps (4, 20), drops (17, 3)


def test_error_is_the_distance_to_the_simplified_surface():
	asset, t = terrain()
	t.set_heights(flat_with({(17, 3): 5000}))
	error = t.heightmap["m_PrecomputedError"]
	assert error[:4] == [0, 0, 0, 0]  # level 0 keeps every vertex
	# level 1 interpolates (17, 3) from its even neighbours, all at 1000
	assert error[4] == pytest.approx((5000 - 1000) / MAX_HEIGHT * 600)


def test_error_interpolates_bilinearly():
	# a slope along x is exact at every level: no error
	asset, t = terrain([x * 100 for x in range(SIZE) for z in range(SIZE)])
	t.set_heights([x * 100 + 1 for x in range(SIZE) for z in range(SIZE)])
	assert t.heightmap["m_PrecomputedError"] == pytest.approx([0] * 5)


def test_only_changed_patches_are_updated():
	marker = [9.0] * 5
	asset, t = terrain(error=marker, minmax=[9.0] * 10)
	t.set_heights(flat_with({(3, 3): 1000}))  # no change at all
	assert t.heightmap["m_PrecomputedError"] == marker
	t.set_heights(flat_with({(3, 3): 2000}))  # in patch (0, 0) and level 1
	assert t.heightmap["m_PrecomputedError"][1:4] == [9.0, 9.0, 9.0]
	assert t.heightmap["m_MinMaxPatchHeights"][2:8] == [9.0] * 6
	assert t.heightmap["m_PrecomputedError"][0] == 0


def test_with_shifts_errors_never_shrink():
	# where there are shifts, Unity's errors can be larger than the bilinear ones
	asset, t = terrain(shifts=[(5, 5, 1)], error=[0.0, 0.0, 0.0, 0.0, 4.25])
	t.set_heights(flat_with({(17, 3): 1100}))
	assert t.heightmap["m_PrecomputedError"][4] == 4.25
	t.set_heights(flat_with({(17, 3): 5000}))
	assert t.heightmap["m_PrecomputedError"][4] == pytest.approx(4000 / MAX_HEIGHT * 600)


@pytest.mark.parametrize("heights,error", [
	([0] * 10, "10 heights for a 33x33 heightmap"),
	([MAX_HEIGHT + 1] * (SIZE * SIZE), "height 32767 is not an integer from 0 to 32766"),
	([-1] * (SIZE * SIZE), "height -1"),
	([0.5] * (SIZE * SIZE), "height 0.5"),
])
def test_set_heights_checks(heights, error):
	_, t = terrain()
	with pytest.raises(ValueError, match=error):
		t.set_heights(heights)


def test_set_shifts():
	marker = [9.0] * 5
	asset, t = terrain(shifts=[(1, 1, 1)], error=marker)
	t.set_shifts([(3, 4, 2), (32, 0, 5)])
	assert t.shifts == [Shift(3, 4, 2), Shift(32, 0, 5)]
	assert t.heightmap["m_PrecomputedError"] == marker  # left as it is
	assert open_asset(save(asset)).objects[1].read().shifts == [Shift(3, 4, 2), Shift(32, 0, 5)]


@pytest.mark.parametrize("shift,error", [
	((33, 0, 1), "outside the 33x33 heightmap"),
	((0, -1, 1), "outside"),
	((0, 0, 0), "has flags 0"),
	((0, 0, 16), "has flags 16"),
])
def test_set_shifts_checks(shift, error):
	_, t = terrain()
	with pytest.raises(ValueError, match=error):
		t.set_shifts([shift])


def test_save_round_trip():
	asset, t = terrain()
	heights = flat_with({(17, 3): 5000, (0, 32): 0, (32, 32): MAX_HEIGHT})
	t.set_heights(heights)
	t2 = open_asset(save(asset)).objects[1].read()
	assert t2.heights == heights
	assert t2.heightmap["m_PrecomputedError"] == pytest.approx(t.heightmap["m_PrecomputedError"])
