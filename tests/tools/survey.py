#!/usr/bin/env python3
"""
Format survey: what every archive and serialized file of a build looks like,
and what UnityPackFF can read from it.

  python3 -m tests.tools.survey --build 1013 --out out/              # curated set
  python3 -m tests.tools.survey --build all --all --out out/

Writes out/survey/<build>.json (one record per archive and inner file) and
out/survey/summary.md (tables per build).
"""

import collections
import contextlib
import io
import json
import logging
import time
from concurrent.futures import ProcessPoolExecutor

from tests.tools._common import builds, bundles, parser

from tests import serialized
from tests.bundles import MAIN_FILE, read_unityweb_header, sha256


def survey_file(path, read_objects=True):
	"Survey one unpacked file. Timing fields start with an underscore."
	data = path.read_bytes()
	rec = {"size": len(data), "sha256": sha256(data)}
	if not serialized.looks_serialized(data):
		rec["kind"] = path.suffix.lstrip(".") or "other"
		return rec

	rec["kind"] = "serialized"
	sf = serialized.parse(data)
	classes = collections.Counter(o["class_id"] for o in sf.objects)
	rec.update({
		"format": sf.format,
		"endian": sf.endian,
		"type_trees": len(sf.types),
		"tree_class_ids": sorted(sf.types),
		"big_ids": sf.big_ids,
		"objects": len(sf.objects),
		"destroyed": sum(1 for o in sf.objects if o["destroyed"]),
		"script_objects": sum(1 for o in sf.objects if o["type_id"] < 0),
		"classes": {str(k): v for k, v in sorted(classes.items())},
		# classes of objects with no type tree in this file (scripts are typed by negative type IDs)
		"untyped_classes": sorted({o["class_id"] for o in sf.objects if o["type_id"] not in sf.types}),
		"externals": [e["path"] for e in sf.externals],
		"layout_ok": sf.end == sf.file_size,
		# tells writers apart: Unity starts every object on 8 bytes,
		# unitypack's Asset.save() on 4
		"object_align": next(a for a in (8, 4, 2, 1) if all(o["offset"] % a == 0 for o in sf.objects if o["size"])),
	})
	rec["unitypackff"] = survey_unitypackff(path, read_objects)
	return rec


def survey_unitypackff(path, read_objects=True):
	from unitypack.asset import Asset
	from tests.digest import asset_digest

	ret = {}
	t = time.time()
	logging.disable(logging.WARNING)  # "absent from structs.dat" is recorded below
	try:
		with open(path, "rb") as f:
			asset = Asset.from_file(f)
			try:
				asset.load()
			except Exception as e:
				ret["open"] = "%s: %s" % (type(e).__name__, e)
				return ret
			ret["open"] = "ok"
			ret["objects"] = len(asset.objects)
			ret["no_struct"] = sorted({o.class_id for o in asset.objects.values() if asset.types.get(o.type_id) is None})
			if read_objects:
				digest, count, errors = asset_digest(asset)
				ret["read_ok"] = count - len(errors)
				ret["errors"] = dict(collections.Counter(e.split(":")[0] for e in errors.values()))
				ret["first_error"] = next(iter(errors.values()), None)
				ret["digest"] = digest
	finally:
		logging.disable(logging.NOTSET)
		ret["_seconds"] = round(time.time() - t, 2)
	return ret


def archive_meta(build, bundle):
	"Archive header and the inner files unitypack.load() exposes."
	import unitypack

	archive = build.fetch(bundle)
	with open(archive, "rb") as f:
		rec = {"header": read_unityweb_header(f.read(4096))}
	with open(archive, "rb") as f:
		try:
			# it prints a warning when an archive holds more than one file
			with contextlib.redirect_stdout(io.StringIO()):
				rec["unitypack_load"] = [a.name for a in unitypack.load(f).assets]
		except Exception as e:
			rec["unitypack_load"] = "%s: %s" % (type(e).__name__, e)
	return rec


def survey_archive(build, bundle, read_objects=True):
	"Survey one archive: header, inner files, and how unitypack.load() sees it."
	rec = archive_meta(build, bundle)
	rec["files"] = {p.name: survey_file(p, read_objects) for p in build.inner_files(bundle)}
	return rec


def survey_build(build, names, pool, read_objects=True, progress=None):
	"survey_archive() for many archives, with every file and archive a pool task."
	names = sorted(names, key=lambda b: -build.info(b)["compressed_info"]["size"])
	metas = {b: pool.submit(archive_meta, build, b) for b in names}
	files = {}
	for b in names:
		for p in build.inner_files(b):
			files[b, p.name] = pool.submit(survey_file, p, read_objects)
	res = {}
	for i, b in enumerate(names):
		rec = metas[b].result()
		rec["files"] = {n: f.result() for (bb, n), f in files.items() if bb == b}
		res[b] = rec
		if progress:
			progress(i + 1, len(names), b)
	return dict(sorted(res.items()))


def strip_timing(rec):
	"Drop fields that change from run to run (keys starting with '_')."
	if isinstance(rec, dict):
		return {k: strip_timing(v) for k, v in rec.items() if not k.startswith("_")}
	if isinstance(rec, list):
		return [strip_timing(v) for v in rec]
	return rec


def summarize(name, archives):
	"Markdown tables for one build."
	lines = ["## %s" % name, ""]
	n_files = collections.Counter()
	engines = collections.Counter()
	levels = collections.Counter()
	formats = collections.Counter()
	kinds = collections.Counter()
	notree = []
	layout_bad = []
	upff_open = collections.Counter()
	upff_read = collections.Counter()
	load_partial = []
	class_ids = collections.Counter()
	no_struct = collections.Counter()
	upff_errors = collections.Counter()
	layout = collections.Counter()
	seconds = 0.0

	for bundle, rec in archives.items():
		engines[rec["header"]["engine_version"]] += 1
		levels[rec["header"]["num_levels"]] += 1
		n_files[len(rec["files"])] += 1
		ul = rec.get("unitypack_load")
		if isinstance(ul, str) or len(ul) != len(rec["files"]):
			load_partial.append(bundle)
		for fname, f in rec["files"].items():
			kinds[f["kind"]] += 1
			if f["kind"] != "serialized":
				continue
			formats[(f["format"], f["endian"], f["big_ids"])] += 1
			layout["%d bytes" % f["object_align"]] += 1
			if f["type_trees"] == 0:
				notree.append("%s/%s" % (bundle, fname))
			if not f["layout_ok"]:
				layout_bad.append("%s/%s" % (bundle, fname))
			for k, v in f["classes"].items():
				class_ids[int(k)] += v
			u = f["unitypackff"]
			upff_open["ok" if u["open"] == "ok" else u["open"].split(":")[0]] += 1
			seconds += u.get("_seconds", 0)
			for c in u.get("no_struct", []):
				no_struct[c] += 1
			if "read_ok" in u:
				upff_read["all objects" if u["read_ok"] == u["objects"] else "some failed"] += 1
				for k, v in u["errors"].items():
					upff_errors[k] += v

	def row(k, v):
		lines.append("| %s | %s |" % (k, v))

	def fmt(c):
		return ", ".join("%s: %d" % (k, v) for k, v in sorted(c.items(), key=lambda kv: str(kv[0])))

	lines += ["| | |", "| --- | --- |"]
	row("Archives", len(archives))
	row("Engine version in archive header", fmt(engines))
	row("Levels per archive", fmt(levels))
	row("Files per archive", fmt(n_files))
	row("Inner files by kind", fmt(kinds))
	row("Serialized (format, endian, big IDs)", fmt(formats))
	row("Layout parsed to the last byte", "%d of %d" % (sum(formats.values()) - len(layout_bad), sum(formats.values())))
	row("Object alignment (8: Unity, 4: `Asset.save()`)", fmt(layout))
	row("Files with no type trees", "%d %s" % (len(notree), ", ".join(notree[:8]) + (" ..." if len(notree) > 8 else "")))
	row("Distinct class IDs", "%d: %s" % (len(class_ids), ", ".join(str(c) for c in sorted(class_ids))))
	row("`unitypack.load()` misses files", "%d archives" % len(load_partial))
	row("UnityPackFF opens", fmt(upff_open))
	row("UnityPackFF reads", fmt(upff_read))
	if upff_errors:
		row("UnityPackFF read errors", fmt(upff_errors))
	if no_struct:
		row("Classes without any type info (files)", fmt(no_struct))
	row("UnityPackFF read time", "%.0f s (sum over files)" % seconds)
	lines.append("")
	return "\n".join(lines)


def main():
	p = parser(__doc__)
	p.add_argument("--no-read", action="store_true", help="only open files, don't read objects")
	args = p.parse_args()

	out = args.out / "survey"
	out.mkdir(parents=True, exist_ok=True)
	summary = ["# Format survey", ""]
	with ProcessPoolExecutor(args.jobs) as pool:
		for build in builds(args):
			names = bundles(args, build)
			t = time.time()
			res = survey_build(build, names, pool, not args.no_read,
				lambda i, n, b: print(("\r%s: %d/%d %s" % (build.alias, i, n, b)).ljust(80), end="", flush=True))
			print(("\r%s: %d archives in %.0f s" % (build.name, len(names), time.time() - t)).ljust(80))
			with open(out / ("%s.json" % build.name), "w") as f:
				json.dump({"build": build.name, "uuid": build.uuid, "archives": res}, f, indent=1)
			summary.append(summarize(build.name, res))
	(out / "summary.md").write_text("\n".join(summary))
	print("wrote", out)


if __name__ == "__main__":
	main()
