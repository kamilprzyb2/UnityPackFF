from collections import namedtuple
from enum import IntEnum
from .object import Object, field


# Heights are stored as integers from 0 to MAX_HEIGHT for 0 to m_Scale.y
MAX_HEIGHT = 32766

# m_Shifts flags: the vertex moves one whole cell (m_Scale.x or m_Scale.z)
SHIFT_MINUS_X = 1
SHIFT_PLUS_X = 2
SHIFT_MINUS_Z = 4
SHIFT_PLUS_Z = 8
SHIFT_FLAGS = SHIFT_MINUS_X | SHIFT_PLUS_X | SHIFT_MINUS_Z | SHIFT_PLUS_Z

# Vertices per side of the smallest level-of-detail patch, minus one
PATCH_SIZE = 16


class Shift(namedtuple("Shift", "x z flags")):
	"""
	A shifted heightmap vertex: it keeps its height but is drawn one cell
	over, along -x, +x, -z and/or +z (SHIFT_* flags), so that it sits right
	above its neighbour and the terrain gets a vertical step there.
	"""


class DetailRenderMode(IntEnum):
	"How a detail prototype is drawn (DetailPrototype.render_mode)"
	GrassBillboard = 0  # its texture on quads that turn to face the camera, waving
	VertexLit = 1  # its mesh, lit, not waving
	Grass = 2  # its mesh, waving like grass


def _rgba(c):
	return (c["r"], c["g"], c["b"], c["a"])


def _render_mode(value):
	"A DetailRenderMode, or the stored number if it isn't one"
	try:
		return DetailRenderMode(value)
	except ValueError:
		return value


class DetailPrototype(namedtuple("DetailPrototype", (
		"texture prototype use_mesh render_mode min_width max_width min_height max_height "
		"noise_spread bend_factor healthy_color dry_color grayscale_lighting lightmap_factor"))):
	"""
	One kind of terrain detail (grass, flowers, small rocks), as stored.
	texture is a pointer to its Texture2D and prototype a pointer to a
	GameObject; use_mesh says which of the two it draws. Each instance
	gets a width and a height between the min and max (FusionFall stores
	min 1.0 for every prototype and max 0.5 to 2.0, so max can be the
	smaller), and a colour between healthy_color and dry_color, picked by a
	noise over the terrain whose features are noise_spread apart.
	bend_factor is how far the wind bends it, lightmap_factor how much the
	terrain's lightmap darkens it, grayscale_lighting whether its texture is
	lit as grayscale. Colours are (r, g, b, a).
	"""

	@classmethod
	def from_dict(cls, d):
		return cls(
			texture=d["prototypeTexture"], prototype=d["prototype"], use_mesh=bool(d["usePrototypeMesh"]),
			render_mode=_render_mode(d["renderMode"]), min_width=d["minWidth"], max_width=d["maxWidth"],
			min_height=d["minHeight"], max_height=d["maxHeight"], noise_spread=d["noiseSpread"],
			bend_factor=d["bendFactor"], healthy_color=_rgba(d["healthyColor"]), dry_color=_rgba(d["dryColor"]),
			grayscale_lighting=bool(d["grayscaleLighting"]), lightmap_factor=d["lightmapFactor"],
		)


class TerrainData(Object):
	"""
	A terrain's heightmap and its textures. The heightmap is a square grid of
	m_Width x m_Height vertices m_Scale.x and m_Scale.z apart, stored x-major:
	the height of vertex (x, z) is m_Heights[x * m_Height + z].

	Besides heights and shifts, the heightmap holds level-of-detail data
	worked out from the heights: per patch, the lowest and highest height
	(m_MinMaxPatchHeights) and how far its simplified surface is from the
	real one (m_PrecomputedError). set_heights() updates it.

	The detail database (grass) is a grid of detail_resolution x
	detail_resolution cells over the whole terrain, with, per detail
	prototype, how many instances of it each cell holds; Unity scatters
	them inside the cell when it draws them. It is stored in square patches
	of m_PatchSamples cells, m_PatchCount along each side; each patch lists
	the prototypes it has (layerIndices) and their counts, one layer of
	m_PatchSamples^2 cells after the other. Patches and the cells in a
	patch are z-major (x first), unlike the heights.
	"""

	heightmap = field("m_Heightmap")
	lightmap = field("m_Lightmap")
	splat_database = field("m_SplatDatabase")
	detail_database = field("m_DetailDatabase")

	@property
	def width(self):
		"Vertices along x"
		return self.heightmap["m_Width"]

	@property
	def height(self):
		"Vertices along z"
		return self.heightmap["m_Height"]

	@property
	def scale(self):
		"(x, y, z): the distance between vertices along x and z, and the height of MAX_HEIGHT"
		s = self.heightmap["m_Scale"]
		return (s["x"], s["y"], s["z"])

	@property
	def heights(self):
		"The stored heights, x-major (see the class docstring), 0 to MAX_HEIGHT"
		return list(self.heightmap["m_Heights"])

	def get_height(self, x, z):
		"Vertex (x, z)'s height in world units"
		return self.heightmap["m_Heights"][x * self.height + z] / MAX_HEIGHT * self.scale[1]

	@property
	def shifts(self):
		"[Shift] (the records name z 'y'); none for terrains saved by newer Unity versions, which have no m_Shifts"
		return [Shift(s["x"], s["y"], s["flags"]) for s in self.heightmap.get("m_Shifts", [])]

	def _details(self):
		"The detail database's fields; none if the object was read without one"
		return self._obj.get("m_DetailDatabase") or {}

	@property
	def detail_prototypes(self):
		"[DetailPrototype], in the order the detail layers number them"
		return [DetailPrototype.from_dict(d) for d in self._details().get("m_DetailPrototypes", [])]

	@property
	def detail_resolution(self):
		"Detail cells along each side of the terrain (0 if it has no detail database)"
		db = self._details()
		return db.get("m_PatchCount", 0) * db.get("m_PatchSamples", 0)

	@property
	def waving_grass(self):
		"The wind on billboard and grass details: tint (r, g, b, a), strength, amount and speed"
		db = self._details()
		return {
			"tint": _rgba(db["m_WavingGrassTint"]), "strength": db["m_WavingGrassStrength"],
			"amount": db["m_WavingGrassAmount"], "speed": db["m_WavingGrassSpeed"],
		}

	def detail_layer(self, prototype):
		"""
		How many instances of detail prototype (an index into
		detail_prototypes) each cell holds, as bytes: detail_resolution^2
		counts, z-major (cell (x, z) at z * detail_resolution + x), cell
		(0, 0) at the terrain's origin. Image.frombytes("L", ...) makes
		it an image in Unity's orientation, like Texture2D.image.
		"""
		return self.detail_layers(only=prototype).get(prototype, bytes(self.detail_resolution ** 2))

	def detail_layers(self, only=None):
		"{prototype index: detail_layer()} for every prototype with at least one instance (only: that prototype alone)"
		db = self._details()
		count, samples = db.get("m_PatchCount", 0), db.get("m_PatchSamples", 0)
		resolution = count * samples
		cells = samples * samples
		patches = db.get("m_Patches", [])
		if len(patches) != count * count:
			raise ValueError("%d detail patches for %d x %d" % (len(patches), count, count))
		layers = {}
		for i, patch in enumerate(patches):
			pz, px = divmod(i, count)
			indices, counts = patch["layerIndices"], patch["numberOfObjects"]
			if len(counts) != len(indices) * cells:
				raise ValueError("detail patch %d has %d counts for %d layers of %d cells" % (i, len(counts), len(indices), cells))
			for j, prototype in enumerate(indices):
				if only is not None and prototype != only:
					continue
				layer = counts[j * cells:(j + 1) * cells]
				if not any(layer):
					continue
				if prototype not in layers:
					layers[prototype] = bytearray(resolution * resolution)
				out = layers[prototype]
				for sz in range(samples):
					start = (pz * samples + sz) * resolution + px * samples
					out[start:start + samples] = layer[sz * samples:(sz + 1) * samples]
		return {k: bytes(layers[k]) for k in sorted(layers)}

	def set_heights(self, heights):
		"""
		Replace the heights with a list in the stored order (x-major,
		width * height values from 0 to MAX_HEIGHT), and update the
		level-of-detail data of every patch with a changed vertex.
		"""
		heights = list(heights)
		if len(heights) != self.width * self.height:
			raise ValueError("%d heights for a %dx%d heightmap" % (len(heights), self.width, self.height))
		bad = next((h for h in heights if not (isinstance(h, int) and 0 <= h <= MAX_HEIGHT)), None)
		if bad is not None:
			raise ValueError("height %r is not an integer from 0 to %d" % (bad, MAX_HEIGHT))
		old = self.heightmap["m_Heights"]
		changed = {(i // self.height, i % self.height) for i, (a, b) in enumerate(zip(old, heights)) if a != b}
		self.heightmap["m_Heights"] = heights
		self.update_lod(changed)

	def set_shifts(self, shifts):
		"""
		Replace the shifts with (x, z, flags) records. The level-of-detail
		data is left as it is: how the engine counts shifts in it is not
		worked out (see update_lod()).
		"""
		if "m_Shifts" not in self.heightmap:
			raise ValueError("%r was saved without m_Shifts (a newer Unity version), so it can't have shifts" % (self,))
		records = []
		for x, z, flags in shifts:
			if not (0 <= x < self.width and 0 <= z < self.height):
				raise ValueError("shift at (%d, %d) is outside the %dx%d heightmap" % (x, z, self.width, self.height))
			if not flags or flags & ~SHIFT_FLAGS:
				raise ValueError("shift at (%d, %d) has flags %d; only %d, %d, %d and %d are allowed" % (
					x, z, flags, SHIFT_MINUS_X, SHIFT_PLUS_X, SHIFT_MINUS_Z, SHIFT_PLUS_Z))
			records.append({"x": x, "y": z, "flags": flags})
		self.heightmap["m_Shifts"] = records

	def patches(self):
		"""
		The level-of-detail patches, in the order of m_MinMaxPatchHeights and
		m_PrecomputedError: level 0 (every vertex) first, then each coarser
		level, which keeps every 2^level-th vertex and has a quarter as many
		patches; within a level, x first. Yields (level, x0, z0, size, step):
		the patch covers vertices x0 to x0 + size and z0 to z0 + size.
		"""
		levels = self.heightmap["m_Levels"]
		for level in range(levels + 1):
			count = 1 << (levels - level)
			size = PATCH_SIZE << level
			for pz in range(count):
				for px in range(count):
					yield level, px * size, pz * size, size, 1 << level

	def update_lod(self, changed=None):
		"""
		Work out m_MinMaxPatchHeights and m_PrecomputedError from the heights,
		for every patch that holds one of the changed (x, z) vertices (all
		patches if changed is None); the others keep their values.

		Per patch, min/max are the lowest and highest of the vertices it
		keeps, as fractions of MAX_HEIGHT. The error is how far, in world
		units, any vertex is from the patch's simplified surface (bilinear
		between the vertices it keeps); 0 for level 0. Unity's own values
		match these, to float precision, on every terrain without shifts.
		With shifts, Unity's errors can be larger (the engine also counts the
		shifted geometry, in a way not worked out), never smaller; so on a
		terrain with shifts each recomputed patch keeps the larger of its old
		and new error.
		"""
		heights = self.heightmap["m_Heights"]
		w = self.height
		sy = self.scale[1]
		minmax = list(self.heightmap["m_MinMaxPatchHeights"])
		error = list(self.heightmap["m_PrecomputedError"])
		patches = list(self.patches())
		if len(minmax) != 2 * len(patches) or len(error) != len(patches):
			minmax = [0.0] * (2 * len(patches))
			error = [0.0] * len(patches)
			changed = None
		keep_old = bool(self.heightmap.get("m_Shifts"))

		def h(x, z):
			return heights[x * w + z] / MAX_HEIGHT

		for i, (level, x0, z0, size, step) in enumerate(patches):
			if changed is not None and not any(x0 <= x <= x0 + size and z0 <= z <= z0 + size for x, z in changed):
				continue
			kept = [heights[x * w + z] for x in range(x0, x0 + size + 1, step) for z in range(z0, z0 + size + 1, step)]
			minmax[2 * i:2 * i + 2] = [min(kept) / MAX_HEIGHT, max(kept) / MAX_HEIGHT]

			e = 0.0
			if level:
				for cx in range(x0, x0 + size, step):
					for cz in range(z0, z0 + size, step):
						h00, h10, h01, h11 = h(cx, cz), h(cx + step, cz), h(cx, cz + step), h(cx + step, cz + step)
						for dx in range(step + 1):
							fx = dx / step
							for dz in range(step + 1):
								fz = dz / step
								v = (h00 * (1 - fx) + h10 * fx) * (1 - fz) + (h01 * (1 - fx) + h11 * fx) * fz
								e = max(e, abs(h(cx + dx, cz + dz) - v))
			e *= sy
			error[i] = max(e, error[i]) if keep_old else e

		self.heightmap["m_MinMaxPatchHeights"] = minmax
		self.heightmap["m_PrecomputedError"] = error

	def mesh(self):
		"""
		The terrain's surface with its shifts applied: (positions, uvs,
		triangles) in Unity's coordinates, relative to the terrain. Vertex
		z * width + x is heightmap vertex (x, z).

		A shifted vertex moves one cell along its flags and keeps its
		height. Its texture coordinates follow the moved position (the
		terrain's textures are projected from above), as do everyone's:
		(x, z) / the terrain's size. Each cell is two triangles split along
		(x, z)-(x + 1, z + 1); the four cells around a shifted vertex are
		marked, and marked cells with x + z even are split along the other
		diagonal, so that the step comes out as a clean wall.
		"""
		w = self.width
		sx, sy, sz = self.scale
		size_x, size_z = sx * (w - 1), sz * (self.height - 1)
		heights = self.heightmap["m_Heights"]

		moves = {}
		other_diagonal = set()
		for x, z, flags in self.shifts:
			if not (0 <= x < w and 0 <= z < self.height):
				continue
			moves[(x, z)] = flags
			for cx, cz in ((x, z), (x - 1, z), (x, z - 1), (x - 1, z - 1)):
				if cx >= 0 and cz >= 0 and (cx + cz) % 2 == 0:
					other_diagonal.add((cx, cz))

		positions = []
		uvs = []
		for z in range(self.height):
			for x in range(w):
				px, pz = x * sx, z * sz
				flags = moves.get((x, z), 0)
				if flags & SHIFT_MINUS_X:
					px -= sx
				if flags & SHIFT_PLUS_X:
					px += sx
				if flags & SHIFT_MINUS_Z:
					pz -= sz
				if flags & SHIFT_PLUS_Z:
					pz += sz
				positions.append((px, heights[x * self.height + z] / MAX_HEIGHT * sy, pz))
				uvs.append((px / size_x, pz / size_z))

		triangles = []
		for z in range(self.height - 1):
			for x in range(w - 1):
				a = z * w + x
				b, c, d = a + 1, a + w, a + w + 1
				if (x, z) in other_diagonal:
					triangles += [a, c, b, b, c, d]
				else:
					triangles += [a, c, d, a, d, b]
		return positions, uvs, triangles


def blend_splats(splats, controls, size, resolution):
	"""
	The terrain's textures blended into one image of resolution x resolution
	pixels covering the whole terrain, as Unity's distant-terrain base map:
	splats is [(image, (tile width, tile height), (offset x, offset y))],
	each image tiled over the terrain every tile size (in world units);
	controls are the control maps, whose channels R, G, B, A weigh four
	splats each, in order. size is the terrain's (width, length). Images are
	PIL images in Unity's orientation (the first row at z = 0), and so is the
	result.
	"""
	from PIL import Image, ImageChops

	out = Image.new("RGB", (resolution, resolution))
	for i, (image, tile, offset) in enumerate(splats):
		if i // 4 >= len(controls):
			break
		weight = controls[i // 4].convert("RGBA").getchannel("RGBA"[i % 4]).resize((resolution, resolution), Image.BILINEAR)
		tw = max(1, round(tile[0] / size[0] * resolution))
		th = max(1, round(tile[1] / size[1] * resolution))
		tile_image = image.convert("RGB").resize((tw, th), Image.BILINEAR)
		ox = round(offset[0] / size[0] * resolution) % tw
		oy = round(offset[1] / size[1] * resolution) % th
		tiled = Image.new("RGB", (resolution, resolution))
		for y in range(-oy, resolution, th):
			for x in range(-ox, resolution, tw):
				tiled.paste(tile_image, (x, y))
		out = ImageChops.add(out, ImageChops.multiply(tiled, Image.merge("RGB", (weight, weight, weight))))
	return out
