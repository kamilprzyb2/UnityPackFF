#!/usr/bin/env python3
"""
Dump script objects (MonoBehaviours: FusionFall's tables and settings) of
an asset file as JSON.

  dump_script.py CustomAssetBundle-TableData all_hnpc.asset -o hnpc.json
  dump_script.py sharedassets0.assets male.bsd
  dump_script.py CustomAssetBundle-TableData      (lists its script objects)

A name picks an object by its container path (all_hnpc.asset) or by its own
name (m_Name: all_hnpc, male.bsd), ignoring case. With one name the output
is that object's fields; with several, {name: fields}. Colours come out as
[r, g, b, a], pointers as {"file_id": ..., "path_id": ...} (null for a
null pointer), byte arrays as lists of numbers.

The skin and hair colour palettes (male.bsd) are in TableData from
beta-20100728 on, and in main.unity3d's sharedassets0.assets before.
"""

import argparse
import json
import math
import sys
from unitypack.asset import Asset
from unitypack.object import ObjectPointer

ASSET_BUNDLE = 142
MONO_BEHAVIOUR = 114


def to_json(value):
	"A read value as plain JSON types"
	if isinstance(value, ObjectPointer):
		return {"file_id": value.file_id, "path_id": value.path_id} if value else None
	if hasattr(value, "_obj"):  # an engine class
		value = value._obj
	if isinstance(value, dict):
		if set(value) == {"r", "g", "b", "a"}:
			return [float(value[k]) for k in "rgba"]
		return {k: to_json(v) for k, v in value.items()}
	if isinstance(value, (list, tuple)):
		return [to_json(v) for v in value]
	if isinstance(value, (bytes, bytearray)):
		return list(value)
	if isinstance(value, float) and not math.isfinite(value):
		return None
	return value


def type_name(obj):
	"A script object's class: its MonoScript's, or its type tree's when the MonoScript isn't loaded"
	try:
		return obj.type
	except (KeyError, OSError):
		tree = obj.asset.tree.type_trees.get(obj.type_id)
		return tree.type if tree is not None else str(obj.type_id)


def script_objects(asset):
	"(object, container path or None) of each script object in the file"
	paths = {}
	bundle = next((o for o in asset.objects.values() if o.type_id == ASSET_BUNDLE), None)
	if bundle is not None:
		for path, info in bundle.read()["m_Container"]:
			ptr = info["asset"]
			if ptr.file_id == 0:
				paths.setdefault(ptr.path_id, path)
	for obj in asset.objects.values():
		# a script's own type tree gives its objects a negative class ID
		if obj.type_id == MONO_BEHAVIOUR or obj.type_id < 0:
			yield obj, paths.get(obj.path_id)


def find(asset, name):
	"The script object a name picks: by container path first, then by m_Name"
	name = name.lower()
	objects = list(script_objects(asset))
	for obj, path in objects:
		if path is not None and path.lower() == name:
			return obj
	for obj, path in objects:
		if str(obj.read().get("m_Name", "")).lower() == name:
			return obj
	raise KeyError(name)


def main():
	p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	p.add_argument("file", help="an extracted asset file")
	p.add_argument("names", nargs="*", help="container paths or names of the objects to dump (default: list them)")
	p.add_argument("-o", "--output", help="output file (default: standard output)")
	args = p.parse_args()

	with open(args.file, "rb") as f:
		asset = Asset.from_file(f)
		if not args.names:
			for obj, path in script_objects(asset):
				print("{}\t{}\t{}\t{}".format(obj.path_id, type_name(obj), obj.read().get("m_Name", ""), path or ""))
			return
		out = {}
		for name in args.names:
			try:
				out[name] = to_json(find(asset, name).read())
			except KeyError:
				p.error("no script object {!r} in {} (run without names to list them)".format(name, args.file))
		if len(args.names) == 1:
			out = out[args.names[0]]

	if args.output:
		with open(args.output, "w") as f:
			json.dump(out, f, indent=1)
			f.write("\n")
	else:
		json.dump(out, sys.stdout, indent=1)
		print()


if __name__ == "__main__":
	main()
