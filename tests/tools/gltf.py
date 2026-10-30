#!/usr/bin/env python3
"""
Every prefab (GameObject in an AssetBundle's container) of an archive, or
the whole scene of a Map_XX_YY archive, exported with unitypack.gltf, and
checked with the Khronos glTF validator when it is installed.

  python3 -m tests.tools.gltf --build 104 --bundle FutureNano.resourceFile --out out/

Pointers into other files resolve against the build's curated archives
only (unpacked as needed), plus, for a scene, the archives it points into,
so that what resolves doesn't depend on what else is in the cache.

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
const path = require("path");
const v = require(require.resolve("gltf-validator", {paths: [process.env.UNITYPACKFF_GLTF_VALIDATOR]}));
(async () => {
  const out = {};
  for (const f of process.argv.slice(1)) {
    // buffers and images in files of their own are next to the .glb, by relative URI
    const external = uri => fs.promises.readFile(path.resolve(path.dirname(f), decodeURIComponent(uri))).then(b => new Uint8Array(b));
    const r = await v.validateBytes(new Uint8Array(fs.readFileSync(f)), {uri: f, externalResourceFunction: external});
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
		self.build = build
		self.dir = tempfile.mkdtemp(prefix="unitypackff-env-")
		self.linked = set()
		for name in build.curated:
			self.link(name)

	def add_references(self, bundle, depth=2):
		"""
		Also link the archives that bundle's files point into, and theirs,
		depth levels deep (a scene points at its DongResources, which points
		at the World_shared part with some terrain textures)
		"""
		from unitypack.asset import Asset

		archive_of = {f.lower(): name for name, info in self.build.manifest["bundles"].items()
			for f in (info.get("uncompressed_info") or {})}
		todo = [bundle]
		for _ in range(depth + 1):
			found = []
			for name in todo:
				self.link(name)
				for path in self.build.inner_files(name):
					if not serialized.looks_serialized(path.read_bytes()):
						continue
					with open(path, "rb") as f:
						asset = Asset.from_file(f)
						asset.objects  # loads the list of referenced files
						for ref in asset.asset_refs[1:]:
							other = archive_of.get(os.path.basename(ref.file_path).lower())
							if other and other not in self.linked:
								found.append(other)
			todo = found

	def link(self, name):
		if name in self.linked:
			return
		self.linked.add(name)
		try:
			src = self.build.unpack(name)
		except BundleUnavailable:
			return
		os.symlink(src, os.path.join(self.dir, name))

	def close(self):
		shutil.rmtree(self.dir, ignore_errors=True)


def summary(glb_bytes, base=None):
	"""
	What the document holds, independent of how Pillow happens to compress
	the PNGs: nodes, meshes with a digest of every accessor's values,
	materials, and each image's size. base: the folder external files'
	URIs are relative to.
	"""
	from PIL import Image

	g = GLB(glb_bytes, base)
	h = hashlib.sha256()
	h.update(json.dumps(g.get("nodes", []), sort_keys=True).encode())
	for mesh in g.get("meshes", []):
		for p in mesh["primitives"]:
			for name, index in sorted(p["attributes"].items()) + [("indices", p["indices"])]:
				h.update(("%s:%r;" % (name, g.accessor(index))).encode())
	h.update(json.dumps(g.get("materials", []), sort_keys=True).encode())
	for skin in g.get("skins", []):
		h.update(("%r;%r;" % (skin["joints"], g.accessor(skin["inverseBindMatrices"]))).encode())
	for anim in g.get("animations", []):
		h.update(json.dumps([anim["name"], anim["channels"], anim.get("extras")], sort_keys=True).encode())
		for s in anim["samplers"]:
			h.update(("%r;%r;" % (g.accessor(s["input"]), g.accessor(s["output"]))).encode())
	if "extras" in g.json:  # clips that only animate materials
		h.update(json.dumps(g["extras"], sort_keys=True).encode())
	images = [list(Image.open(io.BytesIO(g.image(i))).size) for i in range(len(g.get("images", [])))]
	return {
		"nodes": len(g.get("nodes", [])),
		"meshes": len(g.get("meshes", [])),
		"primitives": sum(len(m["primitives"]) for m in g.get("meshes", [])),
		"materials": len(g.get("materials", [])),
		"skins": len(g.get("skins", [])),
		"animations": len(g.get("animations", [])),
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


def export_scenes(build, bundle, env_dir, out=None):
	"""
	{scene file: summary} for the scene of a Map_XX_YY archive, exported
	whole (props, terrain, skinned and animated objects), as export_prefabs()
	"""
	from unitypack.environment import UnityEnvironment
	from unitypack.gltf import GLTFWriter

	out = Path(out or tempfile.mkdtemp(prefix="unitypackff-gltf-"))
	out.mkdir(parents=True, exist_ok=True)
	env = UnityEnvironment(base_path=env_dir)
	ret = {}
	for path in build.inner_files(bundle):
		if not serialized.looks_serialized(path.read_bytes()) or path.name.endswith(".sharedAssets"):
			continue
		asset = env.get_asset_by_filename(path.name)
		w = GLTFWriter()
		w.add_scene(asset)
		data = w.glb()
		f = out / (path.name + ".glb")
		f.write_bytes(data)
		terrains = sum(1 for m in w.json.get("materials", []) if "terrain" in m.get("extras", {}))
		ret[path.name] = dict(summary(data), terrains=terrains, warnings=len(w.warnings), _file=str(f))
	return ret


def main():
	args = parser(__doc__).parse_args()
	check = have_validator()
	for build in builds(args):
		env = CuratedEnvironment(build)
		results = {}
		try:
			for b in bundles(args, build):
				if b.startswith("Map_"):
					env.add_references(b)
					r = export_scenes(build, b, env.dir, args.out / "gltf" / build.name / b)
				else:
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
