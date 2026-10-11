#!/usr/bin/env python3
"""
Every mesh of an archive, exported with unitypack's OBJMesh: geometry and,
for skinned meshes, the weights of each vertex.

  python3 tests/demo/meshes.py --build 104 --bundle FutureNano.resourceFile --out out/

Writes out/meshes/<build>/<archive>/<file>/<path ID>_<name>.obj, a
<path ID>_<name>.skin.json beside each skinned mesh (per vertex, its
[bone index, weight] pairs; the bone index points into the
SkinnedMeshRenderer's m_Bones), and out/meshes/<build>.json with the
statistics the integration test checks.
"""

import collections
import hashlib
import json
import re

from _common import builds, bundles, parser

from tests import serialized

MESH = 43  # class ID


def skin_stats(skin):
	influences = collections.Counter(len(v) for v in skin)
	return {
		"vertices": len(skin),
		"influences": {str(k): v for k, v in sorted(influences.items())},
		"max_bone": max(b for v in skin for b, _ in v),
		"sha256": hashlib.sha256(json.dumps(skin).encode()).hexdigest()[:16],
	}


def mesh_stats(build, bundle, out=None):
	"""
	{file: {path ID: stats}} for every mesh in the archive. With out, also
	writes the OBJ (and skin) of each mesh under out/<file>/.
	"""
	from unitypack.asset import Asset
	from unitypack.export import OBJMesh

	ret = {}
	for path in build.inner_files(bundle):
		if not serialized.looks_serialized(path.read_bytes()):
			continue
		with open(path, "rb") as f:
			asset = Asset.from_file(f)
			meshes = {}
			for path_id, obj in sorted(asset.objects.items()):
				if obj.type_id != MESH:
					continue
				mesh = obj.read()
				obj_mesh = OBJMesh(mesh)
				text = obj_mesh.export()
				data = obj_mesh.mesh_data
				rec = meshes[str(path_id)] = {
					"name": mesh.name,
					"compressed": bool(mesh.mesh_compression),
					"vertices": len(data.vertices),
					"triangles": [len(t) // 3 for t in data.triangles],
					"obj_sha256": hashlib.sha256(text.encode()).hexdigest()[:16],
				}
				if data.skin:
					rec["skin"] = skin_stats(data.skin)

				if out:
					d = out / path.name
					d.mkdir(parents=True, exist_ok=True)
					stem = "%d_%s" % (path_id, re.sub(r"[^\w.-]", "_", mesh.name))
					(d / (stem + ".obj")).write_text(text)
					if data.skin:
						(d / (stem + ".skin.json")).write_text(json.dumps(data.skin))
			if meshes:
				ret[path.name] = meshes
	return ret


def main():
	args = parser(__doc__).parse_args()
	for build in builds(args):
		results = {}
		for b in bundles(args, build):
			r = results[b] = mesh_stats(build, b, args.out / "meshes" / build.name / b)
			meshes = [m for f in r.values() for m in f.values()]
			print("%s %s: %d meshes, %d skinned" % (build.alias, b, len(meshes), sum("skin" in m for m in meshes)))
		(args.out / "meshes").mkdir(parents=True, exist_ok=True)
		(args.out / "meshes" / ("%s.json" % build.name)).write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
	main()
