"""
A small reader for binary glTF (.glb) files, written from the glTF 2.0
specification, for checking what unitypack.gltf writes: the JSON document,
and each accessor's values as tuples. Buffers and images in files of their
own are read from the folder given as base.
"""

import json
import os
import struct
from urllib.parse import unquote

COMPONENTS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}
FORMATS = {5120: "b", 5121: "B", 5122: "h", 5123: "H", 5125: "I", 5126: "f"}


class GLB:
	def __init__(self, data, base=None):
		magic, version, length = struct.unpack_from("<III", data)
		assert magic == 0x46546C67, "not a glb file"
		assert version == 2
		assert length == len(data)
		pos = 12
		self.json = None
		self.bin = b""
		while pos < length:
			size, kind = struct.unpack_from("<II", data, pos)
			chunk = data[pos + 8:pos + 8 + size]
			assert size % 4 == 0, "chunks are padded to 4 bytes"
			if kind == 0x4E4F534A:
				self.json = json.loads(chunk)
			elif kind == 0x004E4942:
				self.bin = chunk
			pos += 8 + size
		assert self.json is not None
		self.base = base
		self.buffers = []
		for i, buffer in enumerate(self.json.get("buffers", [])):
			if "uri" in buffer:
				data = self.external(buffer["uri"])
			else:
				assert i == 0, "only the first buffer can be the BIN chunk"
				data = self.bin
			assert buffer["byteLength"] == len(data)
			self.buffers.append(data)

	def external(self, uri):
		"The bytes of a file a URI names, relative to base"
		assert self.base is not None, "%s is in a file of its own: give GLB a base folder" % uri
		with open(os.path.join(self.base, unquote(uri)), "rb") as f:
			return f.read()

	def __getitem__(self, key):
		return self.json[key]

	def get(self, key, default=None):
		return self.json.get(key, default)

	def accessor(self, index):
		"The accessor's elements: tuples (or plain values for SCALAR)"
		acc = self.json["accessors"][index]
		view = self.json["bufferViews"][acc["bufferView"]]
		n = COMPONENTS[acc["type"]]
		fmt = "<%d%s" % (acc["count"] * n, FORMATS[acc["componentType"]])
		start = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
		assert start + struct.calcsize(fmt) <= view.get("byteOffset", 0) + view["byteLength"]
		values = struct.unpack_from(fmt, self.buffers[view["buffer"]], start)
		if n == 1:
			return list(values)
		return [values[i:i + n] for i in range(0, len(values), n)]

	def image(self, index):
		"An image's bytes"
		image = self.json["images"][index]
		if "uri" in image:
			return self.external(image["uri"])
		view = self.json["bufferViews"][image["bufferView"]]
		start = view.get("byteOffset", 0)
		return self.buffers[view["buffer"]][start:start + view["byteLength"]]
