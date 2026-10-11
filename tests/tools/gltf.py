#!/usr/bin/env python3
"""
Every prefab (GameObject in an AssetBundle's container) of an archive,
exported with unitypack.gltf, and checked with the Khronos glTF validator
when it is installed.

  python3 -m tests.tools.gltf --build 104 --bundle FutureNano.resourceFile --out out/

Pointers into other files resolve against the build's curated archives
only (unpacked as needed), so that what resolves doesn't depend on what
else is in the cache.

Writes out/gltf/<build>/<archive>/<container path>.glb and
out/gltf/<build>.json with the statistics the integration test checks.

The validator is the gltf-validator npm package: install it with
`npm install gltf-validator` in some directory and set
UNITYPACKFF_GLTF_VALIDATOR to that directory (Node.js must be on PATH).
"""

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from tests.tools._common import builds, bundles, parser

from tests import serialized
from tests.bundles import BundleUnavailable
from tests.glb import GLB

GAME_OBJECT = 1
ASSET_BUNDLE = 142

VALIDATOR_SCRIPT = r"""
const fs = require("fs");
const v = require(require.resolve("gltf-validator", {paths: [process.env.UNITYPACKFF_GLTF_VALIDATOR]}));
(async () => {
  const out = {};
  for (const f of process.argv.slice(1)) {
    const r = await v.validateBytes(new Uint8Array(fs.readFileSync(f)));
    out[f] = {errors: r.issues.numErrors, warnings: r.issues.numWarnings,
      messages: r.issues.messages.filter(m => m.severity <= 1).slice(0, 5).map(m => m.code + " " + (m.pointer || ""))};
  }
  console.log(JSON.stringify(out));
})();
"""


def have_validator():
	d = os.environ.get("UNITYPACKFF_GLTF_VALIDATOR")
	return bool(d) and shutil.which("node") is not None and (Path(d) / "node_modules" / "gltf-validator").is_dir()


def validate(paths):
	"{path: {errors, warnings, messages}} from the Khronos validator"
	if not paths:
		return {}
	r = subprocess.run(["node", "-e", VALIDATOR_SCRIPT, *map(str, paths)], capture_output=True, text=True, check=True)
	return json.loads(r.stdout)


class CuratedEnvironment:
	"A folder of links to the build's curated archives, unpacked, for UnityEnvironment(base_path=...)"

	def __init__(self, build):
		self.dir = tempfile.mkdtemp(prefix="unitypackff-env-")
		for name in build.curated:
			try:
				src = build.unpack(name)
			except BundleUnavailable:
				continue
			os.symlink(src, os.path.join(self.dir, name))

	def close(self):
		shutil.rmtree(self.dir, ignore_errors=True)


def summary(glb_bytes):
	"""
	What the document holds, independent of how Pillow happens to compress
	the PNGs: nodes, meshes with a digest of every accessor's values,
	materials, and each image's size.
	"""
	from PIL import Image

	g = GLB(glb_bytes)
	h = hashlib.sha256()
	h.update(json.dumps(g.get("nodes", []), sort_keys=True).encode())
	for mesh in g.get("meshes", []):
		for p in mesh["primitives"]:
			for name, index in sorted(p["attributes"].items()) + [("indices", p["indices"])]:
				h.update(("%s:%r;" % (name, g.accessor(index))).encode())
	h.update(json.dumps(g.get("materials", []), sort_keys=True).encode())
	images = [list(Image.open(io.BytesIO(g.image(i))).size) for i in range(len(g.get("images", [])))]
	return {
		"nodes": len(g.get("nodes", [])),
		"meshes": len(g.get("meshes", [])),
		"primitives": sum(len(m["primitives"]) for m in g.get("meshes", [])),
		"materials": len(g.get("materials", [])),
		"images": images,
		"sha256": h.hexdigest()[:16],
	}


def export_prefabs(build, bundle, env_dir, out=None):
	"""
	{container path: summary} for every prefab of the archive, plus the
	writer's warnings; the .glb files go to out (a temporary folder if None)
	and their paths into the "_file" field.
	"""
	from unitypack.asset import Asset
	from unitypack.environment import UnityEnvironment
	from unitypack.gltf import GLTFWriter

	out = Path(out or tempfile.mkdtemp(prefix="unitypackff-gltf-"))
	out.mkdir(parents=True, exist_ok=True)
	env = UnityEnvironment(base_path=env_dir)
	ret = {}
	for path in build.inner_files(bundle):
		if not serialized.looks_serialized(path.read_bytes()):
			continue
		asset = env.get_asset_by_filename(path.name)
		ab = next((o for o in asset.objects.values() if o.type_id == ASSET_BUNDLE), None)
		if ab is None:
			continue
		for cpath, info in ab.read()["m_Container"]:
			ptr = info["asset"]
			if cpath in ret:  # listed more than once
				continue
			if ptr.file_id != 0 or ptr.path_id not in asset.objects or asset.objects[ptr.path_id].type_id != GAME_OBJECT:
				continue
			w = GLTFWriter()
			w.add_gameobject(ptr.resolve())
			data = w.glb()
			f = out / (re.sub(r"[^\w.-]", "_", cpath) + ".glb")
			f.write_bytes(data)
			ret[cpath] = dict(summary(data), warnings=len(w.warnings), _file=str(f))
	return ret


def main():
	args = parser(__doc__).parse_args()
	check = have_validator()
	for build in builds(args):
		env = CuratedEnvironment(build)
		results = {}
		try:
			for b in bundles(args, build):
				r = export_prefabs(build, b, env.dir, args.out / "gltf" / build.name / b)
				issues = validate([p["_file"] for p in r.values()]) if check else {}
				for p in r.values():
					if check:
						p["validator"] = issues[p["_file"]]
				results[b] = r
				bad = sum(1 for p in r.values() if p.get("validator", {}).get("errors"))
				print("%s %s: %d prefabs%s" % (build.alias, b, len(r),
					", %d with validator errors" % bad if check else " (validator not installed)"))
		finally:
			env.close()
		(args.out / "gltf").mkdir(parents=True, exist_ok=True)
		(args.out / "gltf" / ("%s.json" % build.name)).write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
	main()
