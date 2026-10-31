"""
Terrain: unitypack/engine/terrain.py (TerrainData) on made-up heightmaps
(tests/synth.py terrain_file), read and saved with unitypack.

A heightmap is (2^n * 16 + 1)^2 vertices, stored x-major (vertex (x, z) at
x * height + z) as integers from 0 to 32766. Its level-of-detail patches
(16 x 16 cells at level 0, twice as wide at each level up, keeping every
2^level-th vertex) each have a min/max height and an error.

Details (grass) are counts per cell of a grid over the terrain, per detail
prototype, stored in z-major patches of z-major cells.
"""

import pytest
from PIL import Image

from tests import synth
from tests.unit.support import open_asset, save
from unitypack.engine.terrain import MAX_HEIGHT, DetailPrototype, DetailRenderMode, Shift, TerrainData

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


# TerrainData.mesh(): the surface with shifts applied, in Unity's coordinates

def test_mesh_grid():
	_, t = terrain(flat_with({(2, 5): 3000}))
	positions, uvs, triangles = t.mesh()
	assert len(positions) == SIZE * SIZE
	assert len(triangles) == (SIZE - 1) ** 2 * 6
	# vertex z * width + x is heightmap vertex (x, z)
	assert positions[5 * SIZE + 2] == pytest.approx((8.0, 3000 / MAX_HEIGHT * 600, 20.0))
	assert uvs[5 * SIZE + 2] == pytest.approx((8 / 128, 20 / 128))
	assert uvs[-1] == (1.0, 1.0)


def test_mesh_default_diagonal():
	_, t = terrain()
	a, b, c, d = 0, 1, SIZE, SIZE + 1
	# split along (x, z)-(x + 1, z + 1): both triangles share a and d
	assert t.mesh()[2][:6] == [a, c, d, a, d, b]


def test_mesh_shift_moves_a_vertex_and_keeps_its_height():
	_, t = terrain(flat_with({(5, 6): 2000}), [(5, 6, 2 | 8)])  # +x and +z
	positions, uvs, _ = t.mesh()
	v = 6 * SIZE + 5
	assert positions[v] == pytest.approx((24.0, 2000 / MAX_HEIGHT * 600, 28.0))
	# right above vertex (6, 7): a vertical step
	assert positions[v][::2] == positions[7 * SIZE + 6][::2]
	# texture coordinates follow the moved position
	assert uvs[v] == pytest.approx((24 / 128, 28 / 128))


@pytest.mark.parametrize("flags,move", [(1, (-4, 0)), (2, (4, 0)), (4, (0, -4)), (8, (0, 4)), (1 | 2 | 8, (0, 4))])
def test_mesh_shift_directions(flags, move):
	_, t = terrain(shifts=[(10, 10, flags)])
	p = t.mesh()[0][10 * SIZE + 10]
	assert (p[0] - 40, p[2] - 40) == move


def test_mesh_cells_around_a_shift_with_even_sum_take_the_other_diagonal():
	_, t = terrain(shifts=[(5, 7, 1)])
	triangles = t.mesh()[2]

	def cell(x, z):
		i = (z * (SIZE - 1) + x) * 6
		return triangles[i:i + 6]

	def split(x, z, other):
		a = z * SIZE + x
		b, c, d = a + 1, a + SIZE, a + SIZE + 1
		return [a, c, b, b, c, d] if other else [a, c, d, a, d, b]

	# the four cells around (5, 7): (5, 7), (4, 7), (5, 6), (4, 6); x + z even for (5, 7) and (4, 6)
	assert cell(5, 7) == split(5, 7, True)
	assert cell(4, 6) == split(4, 6, True)
	assert cell(4, 7) == split(4, 7, False)
	assert cell(5, 6) == split(5, 6, False)
	assert cell(3, 3) == split(3, 3, False)


# blend_splats(): the splat textures blended by the control maps, as Unity's base map

def test_blend_splats():
	from PIL import Image
	from unitypack.engine.terrain import blend_splats

	red = Image.new("RGB", (4, 4), (255, 0, 0))
	blue = Image.new("RGB", (4, 4), (0, 0, 255))
	# left half all red (R channel), right half all blue (G channel)
	control = Image.new("RGBA", (2, 1))
	control.putdata([(255, 0, 0, 0), (0, 255, 0, 0)])
	out = blend_splats([(red, (16, 16), (0, 0)), (blue, (16, 16), (0, 0))], [control], (128, 128), 8)
	assert out.size == (8, 8)
	assert out.getpixel((0, 4)) == (255, 0, 0)
	assert out.getpixel((7, 4)) == (0, 0, 255)


def test_blend_splats_tiles_each_splat():
	from PIL import Image
	from unitypack.engine.terrain import blend_splats

	# a 2 x 1 splat, black then white, tiled every 32 units on a 128-unit terrain: 4 repeats in 8 pixels
	splat = Image.new("RGB", (2, 1))
	splat.putdata([(0, 0, 0), (255, 255, 255)])
	control = Image.new("RGBA", (1, 1), (255, 0, 0, 0))
	out = blend_splats([(splat, (32, 32), (0, 0))], [control], (128, 128), 8)
	assert [out.getpixel((x, 0))[0] for x in range(8)] == [0, 255] * 4


def test_terrain_without_shifts_field():
	# terrains saved by newer Unity versions (some of Retrobution's) have no m_Shifts
	_, t = terrain(flat_with({(17, 3): 5000}), [(5, 5, 2)])
	del t.heightmap["m_Shifts"]
	assert t.shifts == []
	positions, _, _ = t.mesh()
	assert positions[5 * SIZE + 5][0] == 20.0
	t.set_heights(flat_with({(17, 3): 6000}))  # recomputes as without shifts
	assert t.heightmap["m_PrecomputedError"][4] == pytest.approx(5000 / MAX_HEIGHT * 600)
	with pytest.raises(ValueError, match="without m_Shifts"):
		t.set_shifts([(1, 1, 1)])


# Details: 2 x 2 patches of 2 x 2 cells (a 4 x 4 grid), three prototypes

DETAIL_PATCHES = [
	([0], [1, 0, 0, 0]),  # patch x 0, z 0: prototype 0 in cell (0, 0)
	([1, 0], [0, 0, 0, 3] + [0, 2, 0, 0]),  # patch x 1, z 0: layers in any order; 3 in (3, 1), 2 in (3, 0)
	([], []),  # patch x 0, z 1: none
	([2], [0, 0, 0, 0]),  # patch x 1, z 1: an empty layer
]


def detailed_terrain(patches=DETAIL_PATCHES):
	prototypes = [
		synth.detail_prototype(texture=(1, 7)),
		synth.detail_prototype(prototype=(0, 5), width=(0.5, 0.75), height=(1.5, 2.5), render_mode=1, use_mesh=1),
		synth.detail_prototype(texture=(0, 9), render_mode=7),
	]
	details = synth.detail_database(patches, prototypes, count=2, samples=2)
	asset = open_asset(synth.terrain_file([1000] * (SIZE * SIZE), details=details))
	return asset, asset.objects[1].contents


def grid(cells):
	"A 4 x 4 layer, z-major, with {(x, z): count} set"
	out = bytearray(16)
	for (x, z), n in cells.items():
		out[z * 4 + x] = n
	return bytes(out)


def test_detail_prototypes():
	_, t = detailed_terrain()
	grass, mesh, odd = t.detail_prototypes
	assert isinstance(grass, DetailPrototype)
	assert (grass.texture.file_id, grass.texture.path_id) == (1, 7)
	assert not grass.prototype and not grass.use_mesh
	assert grass.render_mode is DetailRenderMode.GrassBillboard
	assert (grass.min_width, grass.max_width, grass.min_height, grass.max_height) == (1.0, 2.0, 1.0, 0.5)
	assert (grass.noise_spread, grass.bend_factor) == (0.25, pytest.approx(0.1))
	assert grass.healthy_color == (0.25, 1.0, 0.125, 1.0) and grass.dry_color == (0.75, 0.5, 0.0, 1.0)
	assert grass.grayscale_lighting is True and grass.lightmap_factor == 0.5
	assert mesh.use_mesh and mesh.prototype.path_id == 5 and mesh.render_mode is DetailRenderMode.VertexLit
	assert (mesh.min_width, mesh.max_width, mesh.min_height, mesh.max_height) == (0.5, 0.75, 1.5, 2.5)
	assert odd.render_mode == 7  # not a DetailRenderMode: kept as stored


def test_detail_resolution_and_wind():
	_, t = detailed_terrain()
	assert t.detail_resolution == 4
	assert t.waving_grass == {"tint": (1.0, 0.875, 0.75, 1.0), "strength": 0.5, "amount": 0.25, "speed": 0.125}


def test_detail_layers_are_z_major():
	_, t = detailed_terrain()
	layers = t.detail_layers()
	assert list(layers) == [0, 1]  # prototype 2 has no instances
	assert layers[0] == grid({(0, 0): 1, (3, 0): 2})
	assert layers[1] == grid({(3, 1): 3})
	assert t.detail_layer(1) == layers[1]
	assert t.detail_layer(2) == bytes(16)


def test_detail_layer_as_image():
	_, t = detailed_terrain()
	img = Image.frombytes("L", (4, 4), t.detail_layer(1))
	assert img.getpixel((3, 1)) == 3  # (x, z): Unity's orientation, as Texture2D.image


@pytest.mark.parametrize("patches, error", [
	(DETAIL_PATCHES[:3], "3 detail patches for 2 x 2"),
	(DETAIL_PATCHES[:3] + [([2], [0, 0, 0])], "detail patch 3 has 3 counts for 1 layers of 4 cells"),
])
def test_detail_layers_check_the_patches(patches, error):
	_, t = detailed_terrain(patches)
	with pytest.raises(ValueError, match=error):
		t.detail_layers()


def test_terrain_without_details():
	_, t = terrain()
	assert t.detail_prototypes == [] and t.detail_resolution == 0 and t.detail_layers() == {}


def test_save_round_trip_with_details():
	asset, t = detailed_terrain()
	before = save(asset)
	again = open_asset(before)
	assert save(again) == before
	assert again.objects[1].read().detail_layers() == t.detail_layers()
