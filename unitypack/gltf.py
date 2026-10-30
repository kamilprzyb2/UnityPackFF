"""
glTF 2.0 export, as binary .glb files.

Unity is left-handed and glTF right-handed, so everything is mirrored along
x, as the OBJ exporter does: positions and normals become (-x, y, z),
rotations (x, -y, -z, w), matrices S M S with S = diag(-1, 1, 1, 1), and
every triangle is wound the other way. Texture coordinates become
(u, 1 - v): glTF puts v = 0 at the top of an image, and textures are
written upright.

Pointers into files that aren't loaded (a material's texture in another
archive, say) are skipped, with a note in GLTFWriter.warnings.

A GameObject exports with its skinned meshes (a skin per
SkinnedMeshRenderer, using the mesh's stored bind poses, or the prefab's
own pose where it has none) and the clips of its Animation components,
resampled at each clip's rate and interpolated linearly. A scene file
exports whole (add_scene()), terrain included: its surface with the
shifts applied and its splat textures blended into one base map.

Material render state (alphaMode, doubleSided) comes from the shader's
name, see alpha_state(). glTF can't animate material properties, so a
clip's material curves (a character's build, texture scrolling, colour
pulses) are sampled into its animation's extras; a clip that animates
nothing else goes into the document's extras instead (see
GameObjectExport.add_clip()).

Images and mesh data can go to files of their own instead (see
ExternalFiles), for exports that share them: every map tile uses the same
grass and the same fences.
"""

import copy
import hashlib
import json
import math
import os
import re
import struct
from contextlib import contextmanager
from io import BytesIO
from urllib.parse import quote

from .export import OBJMesh
from .mapdata import scene_colors
from .utils import float_sum

FLOAT = 5126
UNSIGNED_BYTE = 5121
UNSIGNED_SHORT = 5123
UNSIGNED_INT = 5125
ARRAY_BUFFER = 34962
ELEMENT_ARRAY_BUFFER = 34963

LINEAR = 9729
LINEAR_MIPMAP_LINEAR = 9987
REPEAT = 10497
CLAMP_TO_EDGE = 33071

_FORMATS = {FLOAT: "f", UNSIGNED_BYTE: "B", UNSIGNED_SHORT: "H", UNSIGNED_INT: "I"}

# class IDs of the components the exporter looks at
TRANSFORM = 4
MESH_RENDERER = 23
MESH_FILTER = 33
ANIMATION = 111
TERRAIN_COLLIDER = 154
SKINNED_MESH_RENDERER = 137
MONO_BEHAVIOUR = 114
LIGHT = 108
RIGIDBODY = 54
MESH_COLLIDER = 64
BOX_COLLIDER = 65
SPHERE_COLLIDER = 135
CAPSULE_COLLIDER = 136
CHARACTER_CONTROLLER = 143
COLLIDERS = (MESH_COLLIDER, BOX_COLLIDER, SPHERE_COLLIDER, CAPSULE_COLLIDER, CHARACTER_CONTROLLER)
RENDER_SETTINGS = 104
# Unity's Light.m_Type
LIGHT_TYPES = {0: "spot", 1: "directional", 2: "point"}


def mirror_position(p):
	return [-p[0], p[1], p[2]]


def mirror_rotation(q):
	return [q[0], -q[1], -q[2], q[3]]


def mirror_matrix(m):
	"A row-major 4x4 matrix (16 values), mirrored along x"
	s = (-1, 1, 1, 1)
	return [m[r * 4 + c] * s[r] * s[c] for r in range(4) for c in range(4)]


def _fields(obj):
	"An object's fields, whether it has an engine class or reads as a plain dict"
	return obj._obj if hasattr(obj, "_obj") else obj


def shader_name(shader):
	"A Shader's name: m_Name, or the name its source declares (Shader \"...\") when that is empty"
	if shader is None:
		return None
	name = shader.name
	if not name:
		match = re.match(r'\s*Shader\s+"([^"]+)"', _fields(shader).get("m_Script", "") or "")
		name = match.group(1) if match else ""
	return name or None


def alpha_state(shader, floats, transparent_texture):
	"""
	glTF's alphaMode, alphaCutoff and doubleSided for a shader name.
	FusionFall's own shaders spell their render state out in their names
	(normal_blendSrcalphaInvsrcalphaTest_cullOff, ...); Unity's built-in
	ones say Transparent or Cutout. Alpha testing becomes MASK; blending
	without depth writes, additive blending (which glTF can't express, but
	which is closer to BLEND than to OPAQUE) and Transparent become BLEND;
	plain alpha blending becomes BLEND only if the texture has transparent
	pixels, since most props use it with opaque textures.
	"""
	lo = (shader or "").lower()
	ret = {}
	if "test" in lo or "cutout" in lo:
		ret["alphaMode"] = "MASK"
		ret["alphaCutoff"] = float(floats.get("_Cutoff", 0.5))
	elif any(k in lo for k in ("zwriteoff", "blendone", "blendsrcalphaone", "transparent")):
		ret["alphaMode"] = "BLEND"
	elif "blend" in lo and transparent_texture:
		ret["alphaMode"] = "BLEND"
	if "culloff" in lo:
		ret["doubleSided"] = True
	return ret


def is_additive(shader):
	"Whether a shader adds its colour to what's behind it (blendOneOne, blendSrcalphaOne)"
	lo = (shader or "").lower()
	return "blendone" in lo or "blendsrcalphaone" in lo


def _with_alpha(rgb, alpha):
	from PIL import Image

	return Image.merge("RGBA", (*rgb.split(), alpha))


def _unit(x, y, z):
	"A normal as glTF requires it, of length 1 (a zero normal points up)"
	length = math.sqrt(x * x + y * y + z * z)
	return (x / length, y / length, z / length) if length else (0.0, 1.0, 0.0)


def _unit_quaternion(q):
	"A rotation of length 1; a zero quaternion (some files store one) is no rotation"
	length = math.sqrt(float_sum(c * c for c in q))
	return [c / length for c in q] if length else [0.0, 0.0, 0.0, 1.0]


def _vec(v, keys="xyz"):
	return [float(v[k]) for k in keys]


def _color(c):
	return [float(c[k]) for k in "rgba"]


class ExternalFiles:
	"""
	A folder of files named by their content (the first 16 hex digits of
	their SHA-256, and an extension), for images and mesh data that several
	.glb files share: each is written once, however many exports use it.
	A file is written under a temporary name and then renamed, so exports
	running side by side can share the folder.
	"""

	def __init__(self, directory):
		self.directory = directory
		self.names = set()  # every file put() was given
		self.written = set()  # those it had to write

	def put(self, data, ext):
		"The file name for data, written unless the folder has it already"
		name = "%s.%s" % (hashlib.sha256(data).hexdigest()[:16], ext)
		self.names.add(name)
		path = os.path.join(self.directory, name)
		if os.path.exists(path):
			return name
		os.makedirs(self.directory, exist_ok=True)
		tmp = os.path.join(self.directory, ".%s.%d.%s.tmp" % (name, os.getpid(), os.urandom(4).hex()))
		try:
			with open(tmp, "xb") as f:
				f.write(data)
			os.replace(tmp, path)
		except BaseException:
			if os.path.exists(tmp):
				os.unlink(tmp)
			raise
		self.written.add(name)
		return name


class GLTFWriter:
	"""
	Builds one glTF document with a single binary buffer. Add meshes,
	materials and nodes, mark the top nodes with add_root(), then call glb().

	texture_override(material, slot), if given, can replace a material's
	_MainTex: it gets each renderer's Material and its slot (submesh index)
	and returns a Texture2D to use instead, or None to keep the material's
	own. FusionFall's client does this when it spawns nanos, NPCs and
	vehicles (see unitypack.xdttextures).

	With external (an ExternalFiles), every image is a PNG file there, and
	each mesh's vertex data and indices are a .bin file of their own; the
	document's own buffer keeps what belongs to this file alone (inverse
	bind matrices, animations). The same pixels make the same PNG, and the
	same mesh the same bytes, whatever file and order they're exported in.
	"""

	def __init__(self, generator="unitypack", texture_override=None, external=None):
		self.json = {"asset": {"version": "2.0", "generator": generator}, "scene": 0, "scenes": [{"nodes": []}]}
		self.bin = bytearray()
		self.warnings = []
		self.texture_override = texture_override
		self.external = external
		self._cache = {}
		self._buffers = []  # external buffers: the document's buffers after the BIN chunk's
		self._geometry_data = None  # with external, where a mesh's views go while it's added
		self._geometry_views = None
		self.left_out = []  # with skinned_only, the plain meshes left out (see add_gameobject())
		self.unbound_skins = []  # with actor_bones, the skins without an ActorWearIndexTable

	def _add(self, key, item):
		return self._add_to(self.json, key, item)

	@staticmethod
	def _add_to(parent, key, item):
		items = parent.setdefault(key, [])
		items.append(item)
		return len(items) - 1

	def _cached(self, kind, obj, make):
		"Add obj once: obj may be an ObjectPointer (resolved here) or an object"
		key = (kind, obj.source_asset.name, obj.file_id, obj.path_id) if hasattr(obj, "path_id") else (kind, id(obj))
		if key not in self._cache:
			target = self.resolve(obj) if hasattr(obj, "path_id") else obj
			self._cache[key] = make(target) if target is not None else None
		return self._cache[key]

	def resolve(self, pointer):
		"The object a pointer points to, or None if it's null or its file isn't loaded"
		if not pointer:
			return None
		try:
			return pointer.resolve()
		except (KeyError, OSError) as e:
			message = "can't resolve %r: %s" % (pointer, e)
			if message not in self.warnings:
				self.warnings.append(message)
			return None

	def buffer_view(self, data, target=None):
		buf = self.bin if self._geometry_data is None else self._geometry_data
		buf += b"\0" * (-len(buf) % 4)
		view = {"buffer": 0, "byteOffset": len(buf), "byteLength": len(data)}
		if target:
			view["target"] = target
		buf += data
		index = self._add("bufferViews", view)
		if self._geometry_views is not None:
			self._geometry_views.append(index)
		return index

	@contextmanager
	def _geometry(self):
		"With external, the views added inside go to a .bin file of their own"
		if self.external is None or self._geometry_data is not None:
			yield
			return
		self._geometry_data, self._geometry_views = bytearray(), []
		try:
			yield
			data, views = bytes(self._geometry_data), self._geometry_views
		finally:
			self._geometry_data = self._geometry_views = None
		if not views:
			return
		name = self.external.put(data, "bin")
		if ("buffer", name) not in self._cache:  # else the same mesh again, with other materials
			self._buffers.append({"byteLength": len(data), "uri": name})
			self._cache[("buffer", name)] = len(self._buffers)
		for view in views:
			self.json["bufferViews"][view]["buffer"] = self._cache[("buffer", name)]

	def accessor(self, values, type_, component_type=FLOAT, target=None, minmax=False):
		"values: flat; type_: SCALAR, VEC2, VEC3, VEC4 or MAT4"
		components = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}[type_]
		fmt = "<%d%s" % (len(values), _FORMATS[component_type])
		data = struct.pack(fmt, *values)
		acc = {"bufferView": self.buffer_view(data, target), "componentType": component_type, "count": len(values) // components, "type": type_}
		if minmax:
			stored = struct.unpack(fmt, data)  # as float32, so min/max match the data exactly
			acc["min"] = [min(stored[i::components]) for i in range(components)]
			acc["max"] = [max(stored[i::components]) for i in range(components)]
		return self._add("accessors", acc)

	def add_texture(self, texture):
		"A Texture2D (or a pointer to one) as an embedded PNG; None if it can't be decoded"
		return self._cached("texture", texture, self._make_texture)

	def _make_texture(self, texture):
		if not hasattr(texture, "image"):
			self.warnings.append("%r is not an image texture" % (texture,))
			return None
		try:
			image = texture.image
		except NotImplementedError as e:
			self.warnings.append("%r: %s" % (texture, e))
			return None
		if image is None:
			return None
		clamp = texture._obj.get("m_TextureSettings", {}).get("m_WrapMode") == 1
		index = self.add_image(image, texture.name, clamp)
		self._cache[("transparent", index)] = "A" in image.getbands() and image.getchannel("A").getextrema()[0] < 255
		self._cache[("source", index)] = (image, texture.name, clamp)
		return index

	def _brightness_alpha(self, index):
		"""
		A copy of a texture whose alpha is its brightness (the largest of R,
		G and B): drawn with BLEND, it looks like the additive blending glTF
		has no mode for.
		"""
		from PIL import ImageChops

		if ("brightness", index) not in self._cache:
			image, name, clamp = self._cache[("source", index)]
			rgb = image.convert("RGB")
			r, g, b = rgb.split()
			alpha = ImageChops.lighter(ImageChops.lighter(r, g), b)
			self._cache[("brightness", index)] = self.add_image(_with_alpha(rgb, alpha), name + " (additive)", clamp)
		return self._cache[("brightness", index)]

	def add_image(self, image, name, clamp=False):
		"A PIL image in Unity's orientation (bottom row first, as Texture2D.image gives it) as a texture"
		from PIL import ImageOps

		png = BytesIO()
		ImageOps.flip(image).save(png, format="png")
		if self.external is not None:
			image_index = self._add("images", {"name": name, "mimeType": "image/png", "uri": self.external.put(png.getvalue(), "png")})
		else:
			image_index = self._add("images", {"name": name, "mimeType": "image/png", "bufferView": self.buffer_view(png.getvalue())})
		wrap = CLAMP_TO_EDGE if clamp else REPEAT
		if ("sampler", wrap) not in self._cache:
			self._cache[("sampler", wrap)] = self._add("samplers", {
				"magFilter": LINEAR, "minFilter": LINEAR_MIPMAP_LINEAR, "wrapS": wrap, "wrapT": wrap})
		return self._add("textures", {"source": image_index, "sampler": self._cache[("sampler", wrap)]})

	def add_material(self, material, main_texture=None):
		"""
		A Material (or a pointer to one): _Color as the base colour and
		_MainTex (or main_texture, a Texture2D, in its place) as its texture.
		Every saved property and the shader's name go into the material's
		extras, since Unity shaders differ in what they do with them
		(FusionFall's toon shaders double _Color, for one); its other
		textures are embedded too, each with its glTF texture index there.
		"""
		return self._cached(("material", main_texture), material, lambda m: self._make_material(m, main_texture))

	def _make_material(self, material, main_texture=None):
		props = material.saved_properties
		colors = props.get("m_Colors", {})
		floats = props.get("m_Floats", {})
		texenvs = props.get("m_TexEnvs", {})

		color = _color(colors["_Color"]) if "_Color" in colors else [1.0, 1.0, 1.0, 1.0]
		pbr = {"baseColorFactor": [min(max(c, 0.0), 1.0) for c in color], "metallicFactor": 0.0, "roughnessFactor": 1.0}
		main = texenvs.get("_MainTex")
		if main_texture is not None:
			texture = self.add_texture(main_texture)
		else:
			texture = self.add_texture(main["m_Texture"]) if main else None
		if texture is not None:
			pbr["baseColorTexture"] = {"index": texture}

		shader = self.resolve(material.shader)
		gltf_material = {"name": material.name, "pbrMetallicRoughness": pbr}
		name = shader_name(shader)
		gltf_material.update(alpha_state(name, floats, self._cache.get(("transparent", texture), False)))
		if texture is not None and is_additive(name):
			pbr["baseColorTexture"] = {"index": self._brightness_alpha(texture)}
		textures = {}
		for name, env in texenvs.items():
			tex = self.resolve(env["m_Texture"]) if env["m_Texture"] else None
			textures[name] = {"texture": tex.name if tex is not None else None,
				"scale": _vec(env["m_Scale"], "xy"), "offset": _vec(env["m_Offset"], "xy")}
			if tex is not None and name != "_MainTex":
				# the shader's other textures (toon and spec ramps, bump and
				# reflection maps), embedded for an importer that rebuilds it
				index = self.add_texture(env["m_Texture"])
				if index is not None:
					textures[name]["index"] = index
		extras = {
			"shader": shader_name(shader),
			"colors": {k: _color(v) for k, v in colors.items()},
			"floats": {k: float(v) for k, v in floats.items()},
			"textures": textures,
		}
		if main_texture is not None:
			# what the material stores stays in "textures"
			extras["main_texture"] = main_texture.name
		gltf_material["extras"] = extras
		return self._add("materials", gltf_material)

	def _untextured(self, index):
		"The material without its texture, for a mesh without texture coordinates"
		material = self.json["materials"][index]
		if "baseColorTexture" not in material["pbrMetallicRoughness"]:
			return index
		if ("untextured", index) not in self._cache:
			copy = dict(material, pbrMetallicRoughness=dict(material["pbrMetallicRoughness"]))
			del copy["pbrMetallicRoughness"]["baseColorTexture"]
			self._cache[("untextured", index)] = self._add("materials", copy)
		return self._cache[("untextured", index)]

	def mesh_attributes(self, data):
		"The vertex attributes of an OBJMesh's mesh_data"
		n = len(data.vertices)
		attrs = {"POSITION": self.accessor([c for v in data.vertices for c in (v.x, v.y, v.z)], "VEC3", target=ARRAY_BUFFER, minmax=True)}
		if len(data.normals) == n:
			attrs["NORMAL"] = self.accessor([c for v in data.normals for c in _unit(v.x, v.y, v.z)], "VEC3", target=ARRAY_BUFFER)
		if len(data.uv1) == n:
			attrs["TEXCOORD_0"] = self.accessor([c for v in data.uv1 for c in (v.x, 1 - v.y)], "VEC2", target=ARRAY_BUFFER)
		return attrs

	def add_mesh(self, mesh, materials=(), attributes=None):
		"""
		A Mesh, one primitive per submesh, with materials[i] (a Material, a
		pointer to one, or None) on submesh i. Returns the mesh index, or
		None if it has no triangles. attributes adds vertex attributes to
		every primitive (skinning, see add_skinned_mesh()).
		"""
		with self._geometry():
			return self._make_mesh(mesh, materials, attributes)

	def _make_mesh(self, mesh, materials, attributes):
		data = OBJMesh(mesh).mesh_data
		n = len(data.vertices)
		attrs = None
		primitives = []
		for i, tris in enumerate(data.triangles):
			tris = tris[:len(tris) - len(tris) % 3]
			if not tris:
				continue
			if max(tris) >= n:
				self.warnings.append("%r: submesh %d points past the %d vertices" % (mesh, i, n))
				continue
			if attrs is None:
				attrs = self.mesh_attributes(data)
				attrs.update(attributes(data) if attributes else {})
			indices = []
			for j in range(0, len(tris), 3):
				a, b, c = tris[j:j + 3]
				indices += [a, c, b]  # wound the other way, for the mirror
			ctype = UNSIGNED_SHORT if n < 65535 else UNSIGNED_INT
			primitive = {"attributes": attrs, "indices": self.accessor(indices, "SCALAR", ctype, ELEMENT_ARRAY_BUFFER)}
			material = materials[i] if i < len(materials) else None
			if material is not None:
				index = self.add_material(material, self._override(material, i))
				if index is not None and "TEXCOORD_0" not in attrs:
					index = self._untextured(index)
				if index is not None:
					primitive["material"] = index
			primitives.append(primitive)
		if not primitives:
			return None
		return self._add("meshes", {"name": mesh.name, "primitives": primitives})

	def _override(self, material, slot):
		"texture_override's texture for a material (or a pointer to one) on a slot, or None"
		if self.texture_override is None:
			return None
		target = self.resolve(material) if hasattr(material, "path_id") else material
		return self.texture_override(target, slot) if target is not None else None

	def add_mesh_pointer(self, pointer, materials=()):
		"add_mesh() for the Mesh a pointer points to, once per mesh and materials"
		key = ("mesh", pointer.source_asset.name, pointer.file_id, pointer.path_id,
			tuple((m.file_id, m.path_id) if m else None for m in materials))
		if key not in self._cache:
			mesh = self.resolve(pointer)
			self._cache[key] = self.add_mesh(mesh, materials) if mesh is not None else None
		return self._cache[key]

	def add_terrain(self, terrain, base_map_resolution=None):
		"""
		A TerrainData's surface, shifts applied (see TerrainData.mesh()),
		with its splat textures blended into one base colour texture (at
		base_map_resolution, or the terrain's own m_BaseMapResolution). The
		material's extras keep what a terrain shader needs: the splat and
		control map textures, each splat's tiling, and the lightmap.
		"""
		from .engine.terrain import blend_splats

		positions, uvs, triangles = terrain.mesh()
		n = len(positions)
		mirrored = [mirror_position(p) for p in positions]
		indices = []
		for j in range(0, len(triangles), 3):
			a, b, c = triangles[j:j + 3]
			indices += [a, c, b]
		normals = [[0.0, 0.0, 0.0] for _ in range(n)]
		for j in range(0, len(indices), 3):
			a, b, c = (mirrored[i] for i in indices[j:j + 3])
			e1 = [b[k] - a[k] for k in range(3)]
			e2 = [c[k] - a[k] for k in range(3)]
			face = [e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2], e1[0] * e2[1] - e1[1] * e2[0]]
			for i in indices[j:j + 3]:
				for k in range(3):
					normals[i][k] += face[k]
		for v in normals:
			length = math.sqrt(float_sum(c * c for c in v))
			v[:] = [c / length for c in v] if length else [0.0, 1.0, 0.0]

		with self._geometry():
			attrs = {
				"POSITION": self.accessor([c for p in mirrored for c in p], "VEC3", target=ARRAY_BUFFER, minmax=True),
				"NORMAL": self.accessor([c for v in normals for c in v], "VEC3", target=ARRAY_BUFFER),
				"TEXCOORD_0": self.accessor([c for u, v in uvs for c in (u, 1 - v)], "VEC2", target=ARRAY_BUFFER),
			}
			ctype = UNSIGNED_SHORT if n < 65535 else UNSIGNED_INT
			primitive = {"attributes": attrs, "indices": self.accessor(indices, "SCALAR", ctype, ELEMENT_ARRAY_BUFFER)}

		splat_db = _fields(terrain.splat_database)
		size = (terrain.scale[0] * (terrain.width - 1), terrain.scale[2] * (terrain.height - 1))
		splats, splat_info = [], []
		for splat in splat_db["m_Splats"]:
			tex = self.resolve(splat["texture"])
			image = None
			if tex is not None:
				try:
					image = tex.image
				except NotImplementedError as e:
					self.warnings.append("%r: %s" % (tex, e))
			tile = _vec(splat["tileSize"], "xy")
			offset = _vec(splat["tileOffset"], "xy") if "tileOffset" in splat else [0.0, 0.0]
			if image is not None:
				splats.append((image, tile, offset))
			splat_info.append({"texture": self.add_texture(splat["texture"]) if tex is not None else None, "tile_size": tile, "tile_offset": offset})
		controls = []
		control_textures = []
		for ptr in splat_db["m_AlphaTextures"]:
			tex = self.resolve(ptr)
			controls.append(tex.image if tex is not None else None)
			control_textures.append(self.add_texture(ptr) if tex is not None else None)
		material = {"name": terrain.name or "terrain", "pbrMetallicRoughness": {"metallicFactor": 0.0, "roughnessFactor": 1.0}}
		if splats and controls and None not in controls and len(splats) == len(splat_db["m_Splats"]):
			resolution = base_map_resolution or splat_db.get("m_BaseMapResolution") or 512
			base = blend_splats(splats, controls, size, resolution)
			material["pbrMetallicRoughness"]["baseColorTexture"] = {"index": self.add_image(base, (terrain.name or "terrain") + " base map", clamp=True)}
		lightmap = self.add_texture(terrain.lightmap) if terrain.lightmap else None
		material["extras"] = {"terrain": {"splats": splat_info, "controls": control_textures, "lightmap": lightmap}}
		primitive["material"] = self._add("materials", material)
		return self._add("meshes", {"name": terrain.name or "terrain", "primitives": [primitive]})

	def add_node(self, name=None, mesh=None, translation=None, rotation=None, scale=None, children=None, **extra):
		"A node; translation and rotation in Unity's coordinates (mirrored here)"
		node = {}
		if name:
			node["name"] = name
		if translation is not None and list(translation) != [0, 0, 0]:
			node["translation"] = mirror_position(translation)
		if rotation is not None:
			rotation = _unit_quaternion(rotation)
			if rotation != [0, 0, 0, 1]:
				node["rotation"] = mirror_rotation(rotation)
		if scale is not None and list(scale) != [1, 1, 1]:
			node["scale"] = list(scale)
		if mesh is not None:
			node["mesh"] = mesh
		if children:
			node["children"] = list(children)
		node.update(extra)
		return self._add("nodes", node)

	def add_light(self, light):
		"""
		A Light as a KHR_lights_punctual light, or None for a disabled one
		(glTF lights can't be off). Intensity is Unity's, which has no unit.
		"""
		f = _fields(light)
		kind = LIGHT_TYPES.get(f["m_Type"])
		if kind is None or not f.get("m_Enabled", True):
			if kind is None:
				self.warnings.append("light type %r has no glTF form" % (f["m_Type"],))
			return None
		gltf_light = {"type": kind, "color": _color(f["m_Color"])[:3], "intensity": float(f["m_Intensity"])}
		if kind != "directional":
			gltf_light["range"] = float(f["m_Range"])
		if kind == "spot":
			gltf_light["spot"] = {"outerConeAngle": math.radians(float(f["m_SpotAngle"])) / 2}
		self.json.setdefault("extensionsUsed", [])
		if "KHR_lights_punctual" not in self.json["extensionsUsed"]:
			self.json["extensionsUsed"].append("KHR_lights_punctual")
		lights = self.json.setdefault("extensions", {}).setdefault("KHR_lights_punctual", {}).setdefault("lights", [])
		lights.append(gltf_light)
		return len(lights) - 1

	def add_collider(self, component, class_id):
		"""
		A collider as a dict for a node's extras: its "type" (mesh, box,
		sphere or capsule; a CharacterController is a capsule along y),
		"trigger", and its shape: "mesh" (the index of a glTF mesh no node
		draws, with no material) and "convex"; "center" (in the node's
		space, mirrored like positions), "size", "radius", "height" and
		"axis" (0, 1 or 2 for x, y or z). None for a disabled one or a mesh
		collider without a mesh.
		"""
		f = _fields(component)
		if not f.get("m_Enabled", True):
			return None
		info = {"trigger": bool(f.get("m_IsTrigger", False))}
		if class_id == MESH_COLLIDER:
			mesh = self.add_mesh_pointer(f["m_Mesh"]) if f["m_Mesh"] else None
			if mesh is None:
				return None
			info.update(type="mesh", mesh=mesh, convex=bool(f.get("m_Convex", False)))
			return info
		info["center"] = mirror_position(_vec(f["m_Center"]))
		if class_id == BOX_COLLIDER:
			info.update(type="box", size=_vec(f["m_Size"]))
		elif class_id == SPHERE_COLLIDER:
			info.update(type="sphere", radius=float(f["m_Radius"]))
		elif class_id == CAPSULE_COLLIDER:
			info.update(type="capsule", radius=float(f["m_Radius"]), height=float(f["m_Height"]), axis=int(f["m_Direction"]))
		else:
			info.update(type="capsule", radius=float(f["m_Radius"]), height=float(f["m_Height"]), axis=1)
		return info

	def add_root(self, node):
		self.json["scenes"][0]["nodes"].append(node)

	def add_scene(self, asset, skins=True, animations=True, sample_rate=None, inactive=False, colliders=False):
		"""
		Every root GameObject of a scene file (BuildPlayer-...): props,
		terrain, skinned and animated objects, lights. Inactive GameObjects
		are left out unless inactive is true. The scene's extras get its
		RenderSettings (fog, ambient light) and FusionFall's DongColorSetup
		colours (fog depth, fog, sky, light). Returns the root nodes.
		"""
		extras = self.json["scenes"][0].setdefault("extras", {})
		settings = next((o.read() for o in asset.objects.values() if o.type_id == RENDER_SETTINGS), None)
		if settings is not None:
			f = _fields(settings)
			extras["render_settings"] = {
				"fog": bool(f["m_Fog"]), "fog_color": _color(f["m_FogColor"]),
				"fog_density": float(f["m_FogDensity"]), "ambient": _color(f["m_AmbientLight"]),
			}
		colors = scene_colors(asset)
		if colors is not None:
			extras["colors"] = {k: list(v) if isinstance(v, tuple) else v for k, v in colors.items()}
		if not extras:
			del self.json["scenes"][0]["extras"]
		roots = []
		for obj in sorted(asset.objects.values(), key=lambda o: o.path_id):
			if obj.type_id != TRANSFORM:
				continue
			transform = obj.read()
			if transform.parent:
				continue
			gameobject = self.resolve(transform.game_object)
			if gameobject is None or not (inactive or gameobject._obj.get("m_IsActive", True)):
				continue
			roots.append(self.add_gameobject(gameobject, skins, animations, sample_rate, inactive, colliders))
		return roots

	def add_gameobject(self, gameobject, skins=True, animations=True, sample_rate=None, inactive=True, colliders=False,
			skinned_only=False, actor_bones=False):
		"""
		A GameObject and everything under it: one node per Transform, with
		the meshes of MeshFilter + MeshRenderer and SkinnedMeshRenderer
		components, skins for the latter, and the clips of Animation
		components (sampled at each clip's rate, or sample_rate). With
		colliders, each node's colliders (add_collider()) and Rigidbody go
		into its extras. Returns the root node; it and the skinned mesh
		nodes are added to the scene.

		skinned_only exports what the client takes from a worn item (see
		unitypack.xdttextures.worn_prefabs()): ActorSkinCombiner combines
		its SkinnedMeshRenderers into the character and nothing else, so
		its MeshFilter + MeshRenderer meshes are left out (some hair has 3ds
		Max Biped helper boxes there), and so are the nodes that only
		carried one. Each is listed in left_out, as its node's path; a
		textured one also gets a warning, as it may be more than a helper.

		actor_bones writes what FusionFall's client binds a worn item to the
		character with. Bone names don't do it: they aren't unique, and a
		worn item's bones are a stand-in rig. The actor (actor/m.kfm and
		w.kfm) has an ActorSkinCombiner whose actorBones lists Transforms;
		that list, as node indices, goes into its node's extras.actor_bones.
		A worn item has an ActorWearIndexTable beside each
		SkinnedMeshRenderer, whose transformIndicesM and transformIndicesF
		give for each bone the index into the boy's or the girl's
		actorBones; they go into the skin's extras.actor_bones as {"m": [...],
		"f": [...]}, one entry per joint (-1 for none). A skinned mesh
		without one is listed in unbound_skins, by name.
		"""
		export = GameObjectExport(self, gameobject, skins, animations, sample_rate, inactive, colliders, skinned_only,
			actor_bones)
		self.add_root(export.root)
		for node in export.extra_roots:
			self.add_root(node)
		return export.root

	def split_clips(self):
		"""
		The document as one without its clips, and one per clip: [(name,
		GLTFWriter)], in order, glTF animations first and then the
		document's material_clips. A clip's document has the same nodes in
		the same order, so a viewer can play it on the first one's model, but
		no meshes, skins, materials or colliders: only that clip. Each is
		ready for glb(). Warnings stay with the first.
		"""
		animations = self.json.get("animations", [])
		material_clips = self.json.get("extras", {}).get("material_clips", [])
		base = self._subset(geometry=True, animations=[], material_clips=[])
		base.warnings = self.warnings
		clips = [(a["name"], self._subset(geometry=False, animations=[a], material_clips=[])) for a in animations]
		clips += [(c["name"], self._subset(geometry=False, animations=[], material_clips=[c])) for c in material_clips]
		return base, clips

	def _subset(self, geometry, animations, material_clips):
		"A writer with a copy of this document: its nodes, its geometry or none, and the clips given"
		doc = copy.deepcopy({k: v for k, v in self.json.items() if k not in ("animations", "accessors", "bufferViews")})
		if animations:
			doc["animations"] = copy.deepcopy(animations)
		extras = doc.get("extras", {})
		extras.pop("material_clips", None)
		if material_clips:
			extras["material_clips"] = copy.deepcopy(material_clips)
		if "extras" in doc and not extras:
			del doc["extras"]
		if not geometry:
			for key in ("meshes", "skins", "materials", "textures", "images", "samplers"):
				doc.pop(key, None)
			for node in doc["nodes"]:
				node.pop("mesh", None)
				node.pop("skin", None)
				node_extras = node.get("extras", {})
				node_extras.pop("colliders", None)  # their meshes are gone
				if "extras" in node and not node_extras:
					del node["extras"]

		# the accessors, buffer views and buffers left in use, renumbered in order
		used = set()
		for mesh in doc.get("meshes", []):
			for primitive in mesh["primitives"]:
				used.update(primitive["attributes"].values())
				if "indices" in primitive:
					used.add(primitive["indices"])
		for skin in doc.get("skins", []):
			if "inverseBindMatrices" in skin:
				used.add(skin["inverseBindMatrices"])
		for animation in doc.get("animations", []):
			for sampler in animation["samplers"]:
				used.update((sampler["input"], sampler["output"]))
		accessors = {old: new for new, old in enumerate(sorted(used))}
		views = {self.json["accessors"][a]["bufferView"] for a in used}
		views.update(i["bufferView"] for i in doc.get("images", []) if "bufferView" in i)
		views = {old: new for new, old in enumerate(sorted(views))}
		buffers = {0: 0}
		for old in views:
			buffer = self.json["bufferViews"][old]["buffer"]
			if buffer not in buffers:
				buffers[buffer] = len(buffers)

		w = GLTFWriter(external=self.external)
		w.json = doc
		w._buffers = [self._buffers[old - 1] for old, new in sorted(buffers.items(), key=lambda b: b[1]) if old]
		if accessors:
			doc["accessors"] = [dict(self.json["accessors"][old], bufferView=views[self.json["accessors"][old]["bufferView"]]) for old in sorted(accessors)]
		if views:
			doc["bufferViews"] = []
			for old in sorted(views):
				view = dict(self.json["bufferViews"][old], buffer=buffers[self.json["bufferViews"][old]["buffer"]])
				if view["buffer"] == 0:  # in the BIN chunk: copied over
					data = self.bin[view["byteOffset"]:view["byteOffset"] + view["byteLength"]]
					w.bin += b"\0" * (-len(w.bin) % 4)
					view["byteOffset"] = len(w.bin)
					w.bin += data
				doc["bufferViews"].append(view)
		for mesh in doc.get("meshes", []):
			for primitive in mesh["primitives"]:
				primitive["attributes"] = {k: accessors[v] for k, v in primitive["attributes"].items()}
				if "indices" in primitive:
					primitive["indices"] = accessors[primitive["indices"]]
		for skin in doc.get("skins", []):
			if "inverseBindMatrices" in skin:
				skin["inverseBindMatrices"] = accessors[skin["inverseBindMatrices"]]
		for animation in doc.get("animations", []):
			for sampler in animation["samplers"]:
				sampler["input"], sampler["output"] = accessors[sampler["input"]], accessors[sampler["output"]]
		for image in doc.get("images", []):
			if "bufferView" in image:
				image["bufferView"] = views[image["bufferView"]]
		# in the order the whole document has its keys
		w.json = {k: doc[k] for k in self.json if k in doc}
		w.json.update((k, v) for k, v in doc.items() if k not in w.json)
		return w

	def glb(self, location="."):
		"""
		The document as a .glb file. With external files, their URIs are
		relative to location, the folder the .glb goes in.
		"""
		doc = dict(self.json)
		self.bin += b"\0" * (-len(self.bin) % 4)
		if self.bin:
			doc["buffers"] = [{"byteLength": len(self.bin)}]
		if self.external is not None:
			prefix = os.path.relpath(self.external.directory, location).replace(os.sep, "/")
			prefix = "" if prefix == "." else quote(prefix) + "/"
			if "images" in doc:
				doc["images"] = [dict(i, uri=prefix + i["uri"]) if "uri" in i else i for i in doc["images"]]
			if self._buffers:
				if not self.bin:  # no BIN chunk, so no buffer 0
					doc["bufferViews"] = [dict(v, buffer=v["buffer"] - 1) for v in doc["bufferViews"]]
				doc["buffers"] = doc.get("buffers", []) + [dict(b, uri=prefix + b["uri"]) for b in self._buffers]
		js = json.dumps(doc, separators=(",", ":")).encode()
		js += b" " * (-len(js) % 4)
		out = struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + (8 + len(self.bin) if self.bin else 0))
		out += struct.pack("<II", len(js), 0x4E4F534A) + js
		if self.bin:
			out += struct.pack("<II", len(self.bin), 0x004E4942) + bytes(self.bin)
		return out


def components(gameobject):
	"(class ID, pointer) of each of a GameObject's components"
	for c in gameobject.component:
		if isinstance(c, tuple):
			yield c
		else:  # newer files: {component: pointer}, the class only in the target
			yield (c["component"].object.type_id, c["component"])


def trs_matrix(t, q, s):
	"A row-major 4x4 matrix: translation t, rotation quaternion q (x, y, z, w), scale s"
	x, y, z, w = q
	r = [
		1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w),
		2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w),
		2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y),
	]
	return [
		r[0] * s[0], r[1] * s[1], r[2] * s[2], t[0],
		r[3] * s[0], r[4] * s[1], r[5] * s[2], t[1],
		r[6] * s[0], r[7] * s[1], r[8] * s[2], t[2],
		0.0, 0.0, 0.0, 1.0,
	]


def matmul(a, b):
	return [float_sum(a[r * 4 + k] * b[k * 4 + c] for k in range(4)) for r in range(4) for c in range(4)]


def affine_inverse(m):
	"The inverse of a row-major 4x4 matrix whose last row is (0, 0, 0, 1)"
	a, b, c, d, e, f, g, h, i = m[0], m[1], m[2], m[4], m[5], m[6], m[8], m[9], m[10]
	det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
	if det == 0:
		raise ValueError("singular matrix")
	inv = [
		(e * i - f * h) / det, (c * h - b * i) / det, (b * f - c * e) / det,
		(f * g - d * i) / det, (a * i - c * g) / det, (c * d - a * f) / det,
		(d * h - e * g) / det, (b * g - a * h) / det, (a * e - b * d) / det,
	]
	t = [m[3], m[7], m[11]]
	it = [-(inv[r * 3] * t[0] + inv[r * 3 + 1] * t[1] + inv[r * 3 + 2] * t[2]) for r in range(3)]
	return inv[0:3] + [it[0]] + inv[3:6] + [it[1]] + inv[6:9] + [it[2]] + [0.0, 0.0, 0.0, 1.0]


def column_major(m):
	"A row-major 4x4 matrix in glTF's column-major order"
	return [m[r * 4 + c] for c in range(4) for r in range(4)]


def bind_pose_matrix(m):
	"A Matrix4x4f (fields eRC: row R, column C) as a row-major list"
	return [float(m["e%d%d" % (r, c)]) for r in range(4) for c in range(4)]


class GameObjectExport:
	"""
	Walks a GameObject's Transform tree into a GLTFWriter's nodes, then adds
	its skinned meshes (each on a node of its own at the top of the scene,
	as glTF wants, with a skin whose joints are the renderer's bones) and the
	clips of its Animation components.
	"""

	def __init__(self, writer, gameobject, skins=True, animations=True, sample_rate=None, inactive=True, colliders=False,
			skinned_only=False, actor_bones=False):
		self.writer = writer
		self.inactive = inactive
		self.colliders = colliders
		self.skinned_only = skinned_only
		self.actor_bones = actor_bones
		self.actor_tables = []  # (node, actorBones)
		self.index_tables = {}  # node -> ActorWearIndexTable fields
		# with skinned_only, the Transforms the skins use: kept whatever they carry
		self.joints = self.bones(gameobject) if skinned_only else set()
		self.nodes = {}  # Transform path ID -> node index
		self.paths = {}  # path from the root ("" for the root) -> node index
		self.local = {}  # node index -> local matrix, in Unity's coordinates
		self.parent = {}  # node index -> parent node index
		self.skinned = []  # (node, SkinnedMeshRenderer)
		self.animations = []  # (path, Animation component)
		self.sample_rate = sample_rate
		self.extra_roots = []
		self.root = self.visit(gameobject, "")
		for node, bones in self.actor_tables:
			extras = writer.json["nodes"][node].setdefault("extras", {})
			extras["actor_bones"] = [self.nodes.get(b.path_id, -1) if b else -1 for b in bones]
		for node, smr in self.skinned:
			if skins:
				self.add_skin(node, smr)
			else:
				self.add_unskinned(node, smr)
		if animations:
			clips = {}
			for path, animation in self.animations:
				for ptr in _fields(animation)["m_Animations"]:
					clip = writer.resolve(ptr)
					if clip is not None and (path, clip.name) not in clips:
						clips[(path, clip.name)] = clip
						self.add_clip(path, clip)

	def bones(self, gameobject):
		"The path IDs of the Transforms the SkinnedMeshRenderers under a GameObject use as bones"
		w = self.writer
		ret = set()
		comps = list(components(gameobject))
		for cid, ptr in comps:
			if cid == SKINNED_MESH_RENDERER:
				smr = w.resolve(ptr)
				if smr is not None:
					ret.update(b.path_id for b in _fields(smr)["m_Bones"] if b)
		transform = w.resolve(next(p for cid, p in comps if cid == TRANSFORM))
		for child_ptr in _fields(transform)["m_Children"] if transform is not None else ():
			child = w.resolve(child_ptr)
			child_go = w.resolve(_fields(child)["m_GameObject"]) if child is not None else None
			if child_go is not None:
				ret |= self.bones(child_go)
		return ret

	def leave_out(self, path, mesh_ptr, renderer):
		"Note a plain mesh skinned_only leaves out"
		w = self.writer
		w.left_out.append(path)
		textured = False
		for ptr in _fields(renderer)["m_Materials"]:
			material = w.resolve(ptr)
			main = material.saved_properties.get("m_TexEnvs", {}).get("_MainTex") if material is not None else None
			textured = textured or bool(main and main["m_Texture"])
		if textured:
			mesh = w.resolve(mesh_ptr)
			w.warnings.append("left out %r on %r, which is textured: the game doesn't draw plain meshes on worn items" % (
				mesh.name if mesh is not None else mesh_ptr, path))

	def visit(self, gameobject, path):
		"The node of a GameObject and what's under it, or None for one skinned_only leaves out"
		w = self.writer
		comps = list(components(gameobject))
		transform_ptr = next(p for cid, p in comps if cid == TRANSFORM)
		transform = w.resolve(transform_ptr)

		mesh = None
		skinned = None
		light = None
		extras = {}
		left_out = animated = False
		actor_bones = index_table = None
		renderer = next((w.resolve(p) for cid, p in comps if cid == MESH_RENDERER), None)
		for cid, ptr in comps:
			if cid == MESH_FILTER and renderer is not None:
				filt = w.resolve(ptr)
				if filt is not None and _fields(filt)["m_Mesh"]:
					if self.skinned_only:
						self.leave_out(path or gameobject.name, _fields(filt)["m_Mesh"], renderer)
						left_out = True
					else:
						mesh = w.add_mesh_pointer(_fields(filt)["m_Mesh"], _fields(renderer)["m_Materials"])
			elif cid == TERRAIN_COLLIDER:
				collider = w.resolve(ptr)
				terrain = w.resolve(_fields(collider)["m_TerrainData"]) if collider is not None else None
				if terrain is not None:
					mesh = w.add_terrain(terrain)
			elif cid == SKINNED_MESH_RENDERER:
				skinned = w.resolve(ptr)
			elif cid == ANIMATION:
				animation = w.resolve(ptr)
				if animation is not None:
					self.animations.append((path, animation))
					animated = True
			elif self.actor_bones and (cid == MONO_BEHAVIOUR or cid < 0):
				script = w.resolve(ptr)
				fields = _fields(script) if script is not None else {}
				if "actorBones" in fields:
					actor_bones = fields["actorBones"]
				elif "transformIndicesM" in fields and "transformIndicesF" in fields:
					index_table = fields
			elif cid == LIGHT:
				component = w.resolve(ptr)
				if component is not None:
					light = w.add_light(component)
			elif self.colliders and cid in COLLIDERS:
				component = w.resolve(ptr)
				collider = w.add_collider(component, cid) if component is not None else None
				if collider is not None:
					extras.setdefault("colliders", []).append(collider)
			elif self.colliders and cid == RIGIDBODY:
				body = w.resolve(ptr)
				if body is not None:
					f = _fields(body)
					extras["rigidbody"] = {"mass": float(f["m_Mass"]), "kinematic": bool(f["m_IsKinematic"]), "gravity": bool(f["m_UseGravity"])}

		children = []
		for child_ptr in _fields(transform)["m_Children"]:
			child = w.resolve(child_ptr)
			child_go = w.resolve(_fields(child)["m_GameObject"]) if child is not None else None
			if child_go is not None and (self.inactive or child_go._obj.get("m_IsActive", True)):
				node = self.visit(child_go, "%s/%s" % (path, child_go.name) if path else child_go.name)
				if node is not None:
					children.append(node)

		if (left_out and path and not children and skinned is None and light is None and not extras and not animated
				and actor_bones is None and transform_ptr.path_id not in self.joints):
			return None  # it only carried what was left out

		if light is not None:
			# Unity lights shine along their +z, glTF's along -z: turn around y
			children.append(w.add_node(gameobject.name + " (light)", rotation=(0, 1, 0, 0),
				extensions={"KHR_lights_punctual": {"light": light}}))
		t, q, sc = _vec(transform.position), _unit_quaternion(_vec(transform.rotation, "xyzw")), _vec(transform.scale)
		node = w.add_node(gameobject.name, mesh=mesh, translation=t, rotation=q, scale=sc, children=children,
			**({"extras": extras} if extras else {}))
		self.nodes[transform_ptr.path_id] = node
		self.paths.setdefault(path, node)
		if actor_bones is not None:
			self.actor_tables.append((node, actor_bones))
		if index_table is not None:
			self.index_tables[node] = index_table
		self.local[node] = trs_matrix(t, q, sc)
		for child in children:
			self.parent[child] = node
		if skinned is not None:
			self.skinned.append((node, skinned))
		return node

	def world(self, node):
		"A node's matrix relative to the exported root's parent, in Unity's coordinates"
		m = self.local[node]
		while node in self.parent:
			node = self.parent[node]
			m = matmul(self.local[node], m)
		return m

	def add_unskinned(self, node, smr):
		"A SkinnedMeshRenderer's mesh on its own node, as a plain mesh"
		w = self.writer
		if self.actor_bones and node in self.index_tables:
			w.warnings.append("%r exported unskinned: its ActorWearIndexTable is left out" % (w.resolve(_fields(smr)["m_Mesh"]),))
		m = w.resolve(_fields(smr)["m_Mesh"])
		if m is None:
			return
		mesh = w.add_mesh(m, _fields(smr)["m_Materials"])
		if mesh is not None:
			w.json["nodes"][node]["mesh"] = mesh

	def add_skin(self, node, smr):
		w = self.writer
		fields = _fields(smr)
		m = w.resolve(fields["m_Mesh"])
		if m is None:
			return
		bones = [w.resolve(b) for b in fields["m_Bones"]]
		joints = [self.nodes.get(b_ptr.path_id) if b is not None else None for b_ptr, b in zip(fields["m_Bones"], bones)]
		data = OBJMesh(m).mesh_data
		if not bones or not data.skin:
			return self.add_unskinned(node, smr)
		if None in joints:
			w.warnings.append("%r: %d of its bones are not under the exported GameObject; exported unskinned" % (m, joints.count(None)))
			return self.add_unskinned(node, smr)
		if max(b for v in data.skin for b, _ in v) >= len(joints):
			w.warnings.append("%r: skin weights name bones past the renderer's %d" % (m, len(joints)))
			return self.add_unskinned(node, smr)

		# Unity's bind poses map the mesh into each bone's space; without
		# them, the prefab's own pose is the bind pose
		stored = m._obj.get("m_BindPose", [])
		if len(stored) == len(joints):
			binds = [bind_pose_matrix(b) for b in stored]
		else:
			mesh_world = self.world(node)
			binds = [matmul(affine_inverse(self.world(j)), mesh_world) for j in joints]
		ibm = [x for b in binds for x in column_major(mirror_matrix(b))]

		def skin_attributes(data):
			js, ws = [], []
			for vertex in data.skin:
				merged = {}
				for bone, weight in vertex:
					if weight > 0:
						merged[bone] = merged.get(bone, 0.0) + weight
				top = sorted(merged.items(), key=lambda kv: -kv[1])[:4]
				total = float_sum(wt for _, wt in top) or 1.0
				top += [(0, 0.0)] * (4 - len(top))
				js += [b for b, _ in top]
				ws += [wt / total for _, wt in top]
			ctype = UNSIGNED_BYTE if len(joints) <= 256 else UNSIGNED_SHORT
			return {
				"JOINTS_0": w.accessor(js, "VEC4", ctype, ARRAY_BUFFER),
				"WEIGHTS_0": w.accessor(ws, "VEC4", FLOAT, ARRAY_BUFFER),
			}

		mesh = w.add_mesh(m, fields["m_Materials"], attributes=skin_attributes)
		if mesh is None:
			return
		skin = w._add("skins", {"joints": joints, "inverseBindMatrices": w.accessor(ibm, "MAT4")})
		if self.actor_bones:
			self.bind_to_actor(node, skin, m, len(joints))
		# glTF ignores a skinned mesh node's own transform, so it goes at the top of the scene
		name = w.json["nodes"][node].get("name", "")
		self.extra_roots.append(w.add_node(name + " (skinned)", mesh=mesh, skin=skin))

	def bind_to_actor(self, node, skin, mesh, count):
		"A skin's extras.actor_bones, from the ActorWearIndexTable beside its renderer"
		w = self.writer
		table = self.index_tables.get(node)
		if table is None:
			w.unbound_skins.append(mesh.name)
			return
		bones = {}
		for gender, key in (("m", "transformIndicesM"), ("f", "transformIndicesF")):
			indices = [int(i) if i >= 0 else -1 for i in table[key]]
			if len(indices) != count:
				w.warnings.append("%r: %s has %d entries for %d bones" % (mesh, key, len(indices), count))
				indices = (indices + [-1] * count)[:count]
			bones[gender] = indices
		w.json["skins"][skin]["extras"] = {"actor_bones": bones}

	def add_clip(self, path, clip):
		"""
		One clip as a glTF animation: every transform curve sampled at the
		clip's rate (or self.sample_rate), linearly interpolated. Curves on
		other properties (material colours, say) are sampled the same way
		into the animation's extras, as float_curves: {"<path>:<attribute>":
		{"times": [...], "values": [...], "node": n}}, with the path from
		the exported root (as node names go) and the node it names, if
		there is one; a curve that doesn't change has one sample. The
		clip's events go there too. A clip with no transform curve isn't a
		glTF animation (one needs a channel): it goes into the document's
		extras.material_clips, as {"name": ..., "float_curves": ...,
		"events": ...}.
		"""
		w = self.writer
		rate = self.sample_rate or clip.sample_rate or 30.0
		length = clip.length
		frames = int(round(length * rate)) + 1
		times = [min(i / rate, length) for i in range(frames)]
		times_accessor = {}

		def full_path(p):
			return "/".join(x for x in (path, p) if x)

		def sampler(values, type_):
			n = len(values)
			key = n
			if key not in times_accessor:
				times_accessor[key] = w.accessor(times[:n] if n > 1 else [0.0], "SCALAR", minmax=True)
			flat = [x for v in values for x in v]
			return w._add_to(anim, "samplers", {"input": times_accessor[key], "output": w.accessor(flat, type_), "interpolation": "LINEAR"})

		anim = {"name": clip.name, "channels": [], "samplers": []}
		kinds = (
			("rotation", clip.rotation_curves, "VEC4"),
			("translation", clip.position_curves, "VEC3"),
			("scale", clip.scale_curves, "VEC3"),
		)
		missing = set()
		for target, curves, type_ in kinds:
			for p, curve in curves.items():
				node = self.paths.get(full_path(p))
				if node is None:
					missing.add(full_path(p))
					continue
				if not curve.keys:
					continue
				values = [curve.evaluate(t) for t in times]
				if target == "rotation":
					values = _rotation_samples(values)
				elif target == "translation":
					values = [mirror_position(v) for v in values]
				else:
					values = [list(v) for v in values]
				if all(v == values[0] for v in values):
					values = values[:1]
				anim["channels"].append({"sampler": sampler(values, type_), "target": {"node": node, "path": target}})

		extras = {}
		floats = {}
		for f in clip.float_curves:
			if not f.curve.keys:
				continue
			values = [float(f.curve.evaluate(t)) for t in times]
			values = [v if math.isfinite(v) else None for v in values]
			if all(v == values[0] for v in values):
				values = values[:1]
			track = {"times": times[:len(values)] if len(values) > 1 else [0.0], "values": values}
			node = self.paths.get(full_path(f.path))
			if node is not None:
				track["node"] = node
			floats["%s:%s" % (full_path(f.path), f.attribute)] = track
		if floats:
			extras["float_curves"] = dict(sorted(floats.items()))
		if clip.events:
			extras["events"] = [{"time": float(e["time"]), "function": e["functionName"]} for e in clip.events]
		if extras:
			anim["extras"] = extras
		if missing:
			w.warnings.append("clip %r: no node for %d curve paths, such as %r" % (clip.name, len(missing), sorted(missing)[0]))
		if anim["channels"]:
			w._add("animations", anim)
		elif floats:
			w.json.setdefault("extras", {}).setdefault("material_clips", []).append(dict(name=clip.name, **extras))


def _rotation_samples(values):
	"Quaternions normalized, mirrored, and each on the same side as the one before"
	out = []
	for q in values:
		n = math.sqrt(float_sum(c * c for c in q)) or 1.0
		q = mirror_rotation([c / n for c in q])
		if out and float_sum(a * b for a, b in zip(out[-1], q)) < 0:
			q = [-c for c in q]
		out.append(q)
	return out


def export_mesh(mesh, materials=()):
	"One Mesh as a .glb file"
	w = GLTFWriter()
	w.add_root(w.add_node(mesh.name, mesh=w.add_mesh(mesh, materials)))
	return w.glb()


def export_scene(asset):
	"Every active GameObject of a scene file as a .glb file"
	w = GLTFWriter()
	w.add_scene(asset)
	return w.glb()


def export_gameobject(gameobject):
	"A GameObject and everything under it as a .glb file"
	w = GLTFWriter()
	w.add_gameobject(gameobject)
	return w.glb()
