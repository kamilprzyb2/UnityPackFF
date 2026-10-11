"""
Stable digests of what unitypack reads, so tests can record a short hash
instead of output files, and round trips can compare before and after.
"""

import hashlib
import json

from unitypack.engine.object import Object
from unitypack.object import ObjectPointer


def canon(value, pointers=None):
	"""
	Turn a read object into plain JSON data with a fixed shape. If pointers
	is a list, every ObjectPointer found is appended to it.
	"""
	if isinstance(value, Object):
		value = value._obj
	if isinstance(value, dict):
		return {"d": [[k, canon(v, pointers)] for k, v in value.items()]}
	if isinstance(value, (list, tuple)):
		return [canon(v, pointers) for v in value]
	if isinstance(value, (bytes, bytearray)):
		return {"b": len(value), "h": hashlib.sha256(value).hexdigest()[:16]}
	if isinstance(value, ObjectPointer):
		if pointers is not None:
			pointers.append(value)
		return {"p": [value.file_id, value.path_id]}
	if isinstance(value, float):
		return {"f": repr(value)}
	if value is None or isinstance(value, (bool, int, str)):
		return value
	raise TypeError("can't digest %r" % type(value))


def object_digest(value, pointers=None):
	data = json.dumps(canon(value, pointers), separators=(",", ":"), sort_keys=False)
	return hashlib.sha256(data.encode()).hexdigest()


def error_name(e):
	"ValueError, or struct.error rather than a bare 'error'."
	t = type(e)
	return t.__qualname__ if t.__module__ == "builtins" else "%s.%s" % (t.__module__, t.__qualname__)


def asset_digest(asset, pointers=None):
	"""
	Read every object of a unitypack Asset and fold the per-object digests,
	in path ID order, into one. Returns (digest, objects read, errors), where
	errors maps path ID -> exception text. Pointers are collected as in canon().
	"""
	h = hashlib.sha256()
	errors = {}
	count = 0
	for path_id in sorted(asset.objects):
		obj = asset.objects[path_id]
		try:
			d = object_digest(obj.read(), pointers)
		except Exception as e:
			errors[path_id] = "%s: %s" % (error_name(e), e)
			d = "error"
		h.update(("%d:%d:%s;" % (path_id, obj.type_id, d)).encode())
		count += 1
	return h.hexdigest(), count, errors
