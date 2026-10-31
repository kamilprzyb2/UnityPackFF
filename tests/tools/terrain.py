#!/usr/bin/env python3
"""
Every terrain of an archive: its heightmap dumped with bin/dump_terrain.py
(and its details, --details), how Unity's level-of-detail data compares with
what TerrainData works out from the heights, and its details (grass).

  python3 -m tests.tools.terrain --build 104 --bundle DongResources_04_02.resourceFile --out out/

Writes out/terrain/<build>/<archive>/<file>.png, .shifts.json, .details.json
and .detail<N>.png (as dump_terrain.py writes them) and out/terrain/<build>.json with the
statistics the integration test checks.
"""

import hashlib
import json
import struct
import subprocess
import sys

from tests.tools._common import ROOT, builds, bundles, parser

from tests import serialized

TERRAIN = 156  # class ID


def f32(v):
	return struct.unpack("<f", struct.pack("<f", v))[0]


def lod_check(terrain):
	"""
	Recompute every patch's min/max and error from the heights, without
	shifts, and count how Unity's stored values compare: min/max equal as
	float32 or within one float32 step; errors equal (to 0.1%), larger or
	smaller than the bilinear ones.
	"""
	hm = terrain.heightmap
	stored_minmax = list(hm["m_MinMaxPatchHeights"])
	stored_error = list(hm["m_PrecomputedError"])
	shifts = hm["m_Shifts"]
	hm["m_Shifts"] = []
	terrain.update_lod()
	minmax, error = hm["m_MinMaxPatchHeights"], hm["m_PrecomputedError"]
	hm["m_Shifts"], hm["m_MinMaxPatchHeights"], hm["m_PrecomputedError"] = shifts, stored_minmax, stored_error

	ret = {"minmax_equal": 0, "minmax_one_step": 0, "minmax_other": 0, "error_equal": 0, "error_larger": 0, "error_smaller": 0}
	for ours, theirs in zip(minmax, stored_minmax):
		if f32(ours) == theirs:
			ret["minmax_equal"] += 1
		elif abs(f32(ours) - theirs) <= abs(theirs) * 2 ** -23:
			ret["minmax_one_step"] += 1
		else:
			ret["minmax_other"] += 1
	for ours, theirs in zip(error, stored_error):
		if abs(ours - theirs) <= 1e-3 * max(1.0, abs(theirs)):
			ret["error_equal"] += 1
		elif theirs > ours:
			ret["error_larger"] += 1
		else:
			ret["error_smaller"] += 1
	return ret


def detail_stats(terrain):
	"The details: resolution, prototypes, render modes, and the instances per layer with a digest of each"
	prototypes = terrain.detail_prototypes
	layers = terrain.detail_layers()
	return {
		"resolution": terrain.detail_resolution,
		"prototypes": len(prototypes),
		"render_modes": sorted({int(p.render_mode) for p in prototypes}),
		"meshes": sum(1 for p in prototypes if p.use_mesh),
		"layers": {str(i): [sum(layer), hashlib.sha256(layer).hexdigest()[:16]] for i, layer in layers.items()},
	}


def terrain_stats(build, bundle, out=None):
	"""
	{file: stats} for every serialized file of the archive holding a
	TerrainData. With out, also dumps each with bin/dump_terrain.py.
	"""
	from unitypack.asset import Asset

	ret = {}
	for path in build.inner_files(bundle):
		if not serialized.looks_serialized(path.read_bytes()):
			continue
		with open(path, "rb") as f:
			terrains = [obj.read() for obj in Asset.from_file(f).objects.values() if obj.type_id == TERRAIN]
		if not terrains:
			continue
		[t] = terrains
		heights = t.heights
		ret[path.name] = {
			"size": [t.width, t.height],
			"levels": t.heightmap["m_Levels"],
			"scale": list(t.scale),
			"heights": [min(heights), max(heights)],
			"heights_sha256": hashlib.sha256(struct.pack("<%dH" % len(heights), *heights)).hexdigest()[:16],
			"shifts": len(t.shifts),
			"shift_flags": sorted({s.flags for s in t.shifts}),
			"lod": lod_check(t),
			"details": detail_stats(t),
		}
		if out:
			out.mkdir(parents=True, exist_ok=True)
			dump(path, out / (path.name + ".png"))
	return ret


def dump(path, png):
	subprocess.run([sys.executable, str(ROOT / "bin" / "dump_terrain.py"), str(path), str(png), "--details"],
		check=True, stdout=subprocess.DEVNULL, env={"PYTHONPATH": str(ROOT)})


def main():
	p = parser(__doc__)
	args = p.parse_args()
	if not args.bundle and not args.all:
		args.bundle = ["DongResources_04_02.resourceFile"]
	for build in builds(args):
		results = {}
		for b in bundles(args, build):
			r = results[b] = terrain_stats(build, b, args.out / "terrain" / build.name / b)
			for name, t in r.items():
				print("%s %s: %dx%d, %d shifts, LOD %s, %d detail instances" % (build.alias, b, t["size"][0], t["size"][1],
					t["shifts"], t["lod"], sum(n for n, _ in t["details"]["layers"].values())))
		(args.out / "terrain").mkdir(parents=True, exist_ok=True)
		(args.out / "terrain" / ("%s.json" % build.name)).write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
	main()
