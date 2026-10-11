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
"""

import json
import struct
from io import BytesIO

from .export import OBJMesh

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
SKINNED_MESH_RENDERER = 137


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


def _vec(v, keys="xyz"):
	return [float(v[k]) for k in keys]


def _color(c):
	return [float(c[k]) for k in "rgba"]


class GLTFWriter:
	"""
	Builds one glTF document with a single binary buffer. Add meshes,
	materials and nodes, mark the top nodes with add_root(), then call glb().
	"""

	def __init__(self, generator="unitypack"):
		self.json = {"asset": {"version": "2.0", "generator": generator}, "scene": 0, "scenes": [{"nodes": []}]}
		self.bin = bytearray()
		self.warnings = []
		self._cache = {}

	def _add(self, key, item):
		items = self.json.setdefault(key, [])
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
		self.bin += b"\0" * (-len(self.bin) % 4)
		view = {"buffer": 0, "byteOffset": len(self.bin), "byteLength": len(data)}
		if target:
			view["target"] = target
		self.bin += data
		return self._add("bufferViews", view)

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
		from PIL import ImageOps

		try:
			image = texture.image
		except NotImplementedError as e:
			self.warnings.append("%r: %s" % (texture, e))
			return None
		if image is None:
			return None
		png = BytesIO()
		ImageOps.flip(image).save(png, format="png")  # Texture2D images are stored bottom-up
		image_index = self._add("images", {"name": texture.name, "mimeType": "image/png", "bufferView": self.buffer_view(png.getvalue())})

		wrap = CLAMP_TO_EDGE if texture._obj.get("m_TextureSettings", {}).get("m_WrapMode") == 1 else REPEAT
		if ("sampler", wrap) not in self._cache:
			self._cache[("sampler", wrap)] = self._add("samplers", {
				"magFilter": LINEAR, "minFilter": LINEAR_MIPMAP_LINEAR, "wrapS": wrap, "wrapT": wrap})
		return self._add("textures", {"source": image_index, "sampler": self._cache[("sampler", wrap)]})

	def add_material(self, material):
		"""
		A Material (or a pointer to one): _Color as the base colour and
		_MainTex as its texture. Every saved property and the shader's name
		go into the material's extras, since Unity shaders differ in what
		they do with them (FusionFall's toon shaders double _Color, for one).
		"""
		return self._cached("material", material, self._make_material)

	def _make_material(self, material):
		props = material.saved_properties
		colors = props.get("m_Colors", {})
		floats = props.get("m_Floats", {})
		texenvs = props.get("m_TexEnvs", {})

		color = _color(colors["_Color"]) if "_Color" in colors else [1.0, 1.0, 1.0, 1.0]
		pbr = {"baseColorFactor": [min(max(c, 0.0), 1.0) for c in color], "metallicFactor": 0.0, "roughnessFactor": 1.0}
		main = texenvs.get("_MainTex")
		if main:
			texture = self.add_texture(main["m_Texture"])
			if texture is not None:
				pbr["baseColorTexture"] = {"index": texture}

		shader = self.resolve(material.shader)
		textures = {}
		for name, env in texenvs.items():
			tex = self.resolve(env["m_Texture"]) if env["m_Texture"] else None
			textures[name] = {"texture": tex.name if tex is not None else None,
				"scale": _vec(env["m_Scale"], "xy"), "offset": _vec(env["m_Offset"], "xy")}
		extras = {
			"shader": shader.name if shader is not None else None,
			"colors": {k: _color(v) for k, v in colors.items()},
			"floats": {k: float(v) for k, v in floats.items()},
			"textures": textures,
		}
		return self._add("materials", {"name": material.name, "pbrMetallicRoughness": pbr, "extras": extras})

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
			attrs["NORMAL"] = self.accessor([c for v in data.normals for c in (v.x, v.y, v.z)], "VEC3", target=ARRAY_BUFFER)
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
				index = self.add_material(material)
				if index is not None and "TEXCOORD_0" not in attrs:
					index = self._untextured(index)
				if index is not None:
					primitive["material"] = index
			primitives.append(primitive)
		if not primitives:
			return None
		return self._add("meshes", {"name": mesh.name, "primitives": primitives})

	def add_node(self, name=None, mesh=None, translation=None, rotation=None, scale=None, children=None, **extra):
		"A node; translation and rotation in Unity's coordinates (mirrored here)"
		node = {}
		if name:
			node["name"] = name
		if translation is not None and list(translation) != [0, 0, 0]:
			node["translation"] = mirror_position(translation)
		if rotation is not None and list(rotation) != [0, 0, 0, 1]:
			node["rotation"] = mirror_rotation(rotation)
		if scale is not None and list(scale) != [1, 1, 1]:
			node["scale"] = list(scale)
		if mesh is not None:
			node["mesh"] = mesh
		if children:
			node["children"] = list(children)
		node.update(extra)
		return self._add("nodes", node)

	def add_root(self, node):
		self.json["scenes"][0]["nodes"].append(node)

	def add_gameobject(self, gameobject):
		"""
		A GameObject and everything under it: one node per Transform, with
		the meshes of MeshFilter + MeshRenderer and SkinnedMeshRenderer
		components. Returns the root node, also added to the scene.
		"""
		root = GameObjectExport(self, gameobject).root
		self.add_root(root)
		return root

	def glb(self):
		"The document as a .glb file"
		doc = dict(self.json)
		self.bin += b"\0" * (-len(self.bin) % 4)
		if self.bin:
			doc["buffers"] = [{"byteLength": len(self.bin)}]
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


class GameObjectExport:
	"Walks a GameObject's Transform tree into a GLTFWriter's nodes"

	def __init__(self, writer, gameobject):
		self.writer = writer
		self.nodes = {}  # Transform path ID -> node index
		self.paths = {}  # node index -> path from the root ("" for the root)
		self.root = self.visit(gameobject, "")

	def visit(self, gameobject, path):
		w = self.writer
		comps = list(components(gameobject))
		transform_ptr = next(p for cid, p in comps if cid == TRANSFORM)
		transform = w.resolve(transform_ptr)

		mesh = None
		skinned = None
		renderer = next((w.resolve(p) for cid, p in comps if cid == MESH_RENDERER), None)
		for cid, ptr in comps:
			if cid == MESH_FILTER and renderer is not None:
				filt = w.resolve(ptr)
				m = w.resolve(_fields(filt)["m_Mesh"]) if filt is not None else None
				if m is not None:
					mesh = w.add_mesh(m, _fields(renderer)["m_Materials"])
			elif cid == SKINNED_MESH_RENDERER:
				skinned = w.resolve(ptr)

		children = []
		for child_ptr in _fields(transform)["m_Children"]:
			child = w.resolve(child_ptr)
			child_go = w.resolve(_fields(child)["m_GameObject"]) if child is not None else None
			if child_go is not None:
				children.append(self.visit(child_go, "%s/%s" % (path, child_go.name) if path else child_go.name))

		node = w.add_node(
			gameobject.name,
			mesh=mesh,
			translation=_vec(transform.position),
			rotation=_vec(transform.rotation, "xyzw"),
			scale=_vec(transform.scale),
			children=children,
		)
		self.nodes[transform_ptr.path_id] = node
		self.paths[node] = path
		if skinned is not None:
			self.add_skinned(node, skinned)
		return node

	def add_skinned(self, node, smr):
		"A SkinnedMeshRenderer's mesh on its node (without skinning)"
		w = self.writer
		m = w.resolve(_fields(smr)["m_Mesh"])
		if m is None:
			return
		mesh = w.add_mesh(m, _fields(smr)["m_Materials"])
		if mesh is not None:
			w.json["nodes"][node]["mesh"] = mesh


def export_mesh(mesh, materials=()):
	"One Mesh as a .glb file"
	w = GLTFWriter()
	w.add_root(w.add_node(mesh.name, mesh=w.add_mesh(mesh, materials)))
	return w.glb()


def export_gameobject(gameobject):
	"A GameObject and everything under it as a .glb file"
	w = GLTFWriter()
	w.add_gameobject(gameobject)
	return w.glb()
