#!/usr/bin/env python3
"""
FusionFall's per-map data, read with unitypack.mapdata: the attribute grid
and colours of map tile scenes, and the music and ambient zones in
main.unity3d.

  python3 tests/demo/mapdata.py --build 104 --bundle Map_04_02.unity3d --bundle main.unity3d --out out/

Writes out/mapdata/<build>/<file>.json with bin/dump_mapdata.py and
out/mapdata/<build>.json with the statistics the integration test checks.
"""

import hashlib
import json
import subprocess
import sys

from _common import ROOT, builds, bundles, parser

from tests import serialized


def mapdata_stats(build, bundle, out=None):
	"{file: stats} for every file of the archive with map data"
	from unitypack.asset import Asset
	from unitypack.mapdata import map_attributes, music_zones, scene_colors

	ret = {}
	for path in build.inner_files(bundle):
		if not serialized.looks_serialized(path.read_bytes()):
			continue
		with open(path, "rb") as f:
			asset = Asset.from_file(f)
			rec = {}
			attributes = map_attributes(asset)
			if attributes is not None:
				rec["attributes"] = {
					"size": attributes.size,
					"counts": {str(bit): len(attributes.cells(bit)) for bit in (1, 2, 4, 8, 16, 32, 64, 128)},
					"sha256": hashlib.sha256(attributes.values).hexdigest()[:16],
				}
			colors = scene_colors(asset)
			if colors is not None:
				rec["colors"] = colors
			zones = music_zones(asset)
			if zones is not None:
				for kind, zs in zones.items():
					rec[kind] = {
						"zones": len(zs),
						"silent": sum(1 for z in zs if z.file is None),
						"instance": sum(1 for z in zs if z.instance),
						"sha256": hashlib.sha256(json.dumps([list(z) for z in zs]).encode()).hexdigest()[:16],
					}
		if rec:
			ret[path.name] = rec
			if out:
				out.mkdir(parents=True, exist_ok=True)
				subprocess.run([sys.executable, str(ROOT / "bin" / "dump_mapdata.py"), str(path), "-o", str(out)],
					check=True, stdout=subprocess.DEVNULL, env={"PYTHONPATH": str(ROOT)})
	return ret


def main():
	args = parser(__doc__).parse_args()
	if not args.bundle and not args.all:
		args.bundle = ["Map_04_02.unity3d", "main.unity3d"]
	for build in builds(args):
		results = {}
		for b in bundles(args, build):
			r = results[b] = mapdata_stats(build, b, args.out / "mapdata" / build.name)
			for name, rec in r.items():
				print("%s %s %s: %s" % (build.alias, b, name, ", ".join(rec)))
		(args.out / "mapdata").mkdir(parents=True, exist_ok=True)
		(args.out / "mapdata" / ("%s.json" % build.name)).write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
	main()
