#!/usr/bin/env python3
"""
Modding round trip through ffbuildtool, without edits:

  1. ffbuildtool extract-bundle         archive -> files
  2. unitypack Asset.from_file + save   every serialized file, rewritten
  3. ffbuildtool pack-bundle            files -> archive
  4. re-open the new archive            same files, and every object reads back the same

  python3 -m tests.tools.roundtrip --build 104 --bundle TableData.resourceFile --out out/

With --client-build DIR, also lays out a whole build in DIR (the cached
archives, with the round-tripped ones swapped in) and writes DIR/manifest.json
with ffbuildtool gen-manifest, for a check in the real client through the
launcher. Serve DIR over HTTP at --asset-url.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

from tests.tools._common import builds, bundles, parser

from tests import serialized
from tests.bundles import read_unityweb, sha256
from tests.digest import asset_digest

FFBUILDTOOL = os.environ.get("FFBUILDTOOL", "ffbuildtool")


def have_ffbuildtool():
	return shutil.which(FFBUILDTOOL) is not None


def run(*args):
	subprocess.run([FFBUILDTOOL, *map(str, args)], check=True, stdout=subprocess.DEVNULL)


def read_digest(path):
	from unitypack.asset import Asset
	with open(path, "rb") as f:
		return asset_digest(Asset.from_file(f))


def save_copy(src, dst):
	from unitypack.asset import Asset
	with open(src, "rb") as f, open(dst, "wb") as out:
		Asset.from_file(f).save(out)


def roundtrip(build, bundle, work, level=4):
	"""
	Returns {"order": [...], "packed_order": [...], "files": {name: record}}.
	Each serialized file's record has its original and saved SHA-256, whether
	the bytes are identical, and the object digests before and after.
	"""
	work = Path(work) / build.name / bundle
	shutil.rmtree(work, ignore_errors=True)
	extracted, saved, packed = work / "extracted", work / "saved", work / "packed" / bundle
	saved.mkdir(parents=True)
	packed.parent.mkdir()

	run("extract-bundle", "-i", build.fetch(bundle), "-o", extracted)
	original = read_unityweb(build.fetch(bundle).read_bytes()).files()

	files = {}
	for name, data in original:
		src = extracted / name
		rec = {"original": sha256(data), "extracted_matches": sha256(src.read_bytes()) == sha256(data)}
		if serialized.looks_serialized(data):
			digest, count, errors = read_digest(src)
			rec.update({"objects": count, "read_errors": len(errors), "digest_before": digest})
			try:
				save_copy(src, saved / name)
			except Exception as e:
				rec["save_error"] = "%s: %s" % (type(e).__name__, e)
				shutil.copyfile(src, saved / name)
			else:
				rec["digest_after"] = read_digest(saved / name)[0]
		else:
			shutil.copyfile(src, saved / name)
		new = (saved / name).read_bytes()
		rec["saved"] = sha256(new)
		rec["identical"] = new == data
		files[name] = rec

	run("pack-bundle", "-i", saved, "-o", packed, "-l", level)
	repacked = read_unityweb(packed.read_bytes()).files()
	for name, data in repacked:
		files[name]["packed_matches"] = sha256(data) == files[name]["saved"]

	return {
		"order": [n for n, _ in original],
		"packed_order": [n for n, _ in repacked],
		"packed": str(packed),
		"files": files,
	}


def client_build(build, packed, out, asset_url, name):
	"Lay out a complete build with the packed archives swapped in, plus its manifest."
	out = Path(out)
	out.mkdir(parents=True, exist_ok=True)
	got = build.fetch_many(build.bundles)
	missing = [b for b, p in got.items() if not isinstance(p, Path)]
	if missing:
		raise SystemExit("can't fetch %d archives, e.g. %s" % (len(missing), missing[0]))
	for b, p in got.items():
		dst = out / b
		dst.unlink(missing_ok=True)
		src = Path(packed[b]) if b in packed else p
		try:
			os.link(src, dst)
		except OSError:
			shutil.copyfile(src, dst)
	run("gen-manifest", "-b", out, "-u", asset_url, "-n", name, "-p", build.uuid,
		"-o", out / "manifest.json")
	return out / "manifest.json"


def main():
	p = parser(__doc__)
	p.add_argument("--level", type=int, default=4, help="LZMA level for pack-bundle")
	p.add_argument("--client-build", type=Path, help="also write a whole launcher-loadable build here")
	p.add_argument("--asset-url", default="http://localhost:8000", help="where --client-build will be served")
	p.add_argument("--name", help="build name for the manifest (default: <build>-unitypackff)")
	args = p.parse_args()
	if not have_ffbuildtool():
		raise SystemExit("ffbuildtool not found (cargo install --git https://github.com/OpenFusionProject/ffbuildtool)")

	out = args.out / "roundtrip"
	for build in builds(args):
		results = {}
		for b in bundles(args, build):
			r = results[b] = roundtrip(build, b, out / "work", args.level)
			ok = all(f.get("digest_before") == f.get("digest_after") and f.get("packed_matches")
				for f in r["files"].values())
			print("%s %s: %s%s" % (build.alias, b, "ok" if ok else "DIFFERENT",
				"" if r["order"] == r["packed_order"] else " (file order changed)"))
			for n, f in r["files"].items():
				if "save_error" in f:
					print("  %s: %s" % (n, f["save_error"]))
		(out / ("%s.json" % build.name)).write_text(json.dumps(results, indent=1))

		if args.client_build:
			packed = {b: r["packed"] for b, r in results.items()}
			name = args.name or "%s-unitypackff" % build.name
			m = client_build(build, packed, args.client_build / build.name, args.asset_url.rstrip("/") + "/" + build.name, name)
			print("wrote", m)


if __name__ == "__main__":
	main()
