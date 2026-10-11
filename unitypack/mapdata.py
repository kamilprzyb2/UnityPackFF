"""
FusionFall's per-map data, kept in script objects (MonoBehaviours):

- MapAttributeTable, one per map tile scene (BuildPlayer-Map_XX_YY): a
  grid of flags, one byte per heightmap vertex.
- MusicDataStorage, in main.unity3d: the polygons that pick the music and
  the ambient sound playing where the player stands.
- DongColorSetup, one per map tile scene: fog, sky and light colours.

Script objects are found by their fields, since their classes are only
named in the scripts they point to.
"""

from collections import namedtuple

# MapAttributeTable flags
ATTRIBUTE_ITEM_1 = 1  # 1 and 4 gate where some items can be used
ATTRIBUTE_ITEM_4 = 4
ATTRIBUTE_POISON = 8  # standing here hurts
ATTRIBUTE_HEAL = 16  # standing here heals (the server may not support it)
ATTRIBUTE_SURFACE = 64  # values from 64 up: roads and pavements, it seems; such cells are left out of the item checks

MusicZone = namedtuple("MusicZone", "index name file delay instance points")
MusicZone.__doc__ = """
A polygon (points: [(x, y)] in world units on the map) and the track
(file, None for silence) that plays inside it; instance zones apply only
in instances. The first zone that contains the player wins.
"""


def script_objects(asset, *fields):
	"Every script object (MonoBehaviour) of the asset that has all the fields"
	for path_id, obj in sorted(asset.objects.items()):
		if obj.type_id >= 0:
			continue
		try:
			data = obj.read()
		except Exception:  # a script whose type tree this file doesn't have
			continue
		if hasattr(data, "keys") and all(f in data for f in fields):
			yield data


class MapAttributes:
	"""
	A MapAttributeTable: one byte of flags (ATTRIBUTE_*) per vertex of the
	tile's 129 x 129 heightmap, stored x-major as cell x + z * size.
	"""

	def __init__(self, values):
		self.values = bytes(values)
		self.size = int(round(len(self.values) ** 0.5))
		if self.size * self.size != len(self.values):
			raise ValueError("%d attribute bytes don't make a square" % (len(self.values)))

	def get(self, x, z):
		return self.values[x + z * self.size]

	def cells(self, flag):
		"(x, z) of every cell with the flag set"
		return [(i % self.size, i // self.size) for i, v in enumerate(self.values) if v & flag]

	def rows(self):
		"The grid as rows of values, z = 0 first"
		return [list(self.values[z * self.size:(z + 1) * self.size]) for z in range(self.size)]


def map_attributes(asset):
	"The scene's MapAttributes, or None"
	data = next(script_objects(asset, "attributes"), None)
	return MapAttributes(data["attributes"]) if data is not None else None


def music_zones(asset):
	"{'music': [MusicZone], 'ambient': [MusicZone]} from MusicDataStorage, or None"
	data = next(script_objects(asset, "musicDatas", "ambientDatas"), None)
	if data is None:
		return None
	ret = {}
	for kind, key in (("music", "musicDatas"), ("ambient", "ambientDatas")):
		ret[kind] = [
			MusicZone(
				z["index"], z["name"],
				None if z["fileName"].lower() == "none.ogg" else z["fileName"],
				z["delay"], bool(z["bInstanceFlag"]),
				[(p["x"], p["y"]) for p in z["points"]],
			)
			for z in data[key]
		]
	return ret


def scene_colors(asset):
	"DongColorSetup's colours as {fog_depth, fog, sky, light} ((r, g, b, a) each), or None"
	data = next(script_objects(asset, "fogColor", "skyColor", "lightColor"), None)
	if data is None:
		return None

	def rgba(c):
		return tuple(float(c[k]) for k in "rgba")

	return {"fog_depth": float(data["fogDepth"]), "fog": rgba(data["fogColor"]), "sky": rgba(data["skyColor"]), "light": rgba(data["lightColor"])}
