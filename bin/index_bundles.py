#!/usr/bin/env python3
"""
Index every asset bundle under a directory, so grep can tell which bundle
an asset is in. Takes .unity3d/.resourceFile archives (UnityWeb, as
FusionFall ships them) and extracted asset files (such as the client's
cache) alike. Other archive kinds are reported on stderr and skipped.

  index_bundles.py ~/fusionfall/cache > index.tsv
  grep -i 'nano_' index.tsv

Prints one tab-separated line per entry of each file's AssetBundle
container: archive, file, path_id, file_id, class, container path. Files
without an AssetBundle (scenes) get one line with the last four fields
empty. For an extracted file, archive is "-".
"""

import os
import struct
import sys
import traceback
from argparse import ArgumentParser

import unitypack
from unitypack.asset import Asset
from unitypack.resources import UnityClass

ARCHIVE_SIGNATURES = (b"UnityWeb", b"streamed")
OTHER_ARCHIVE_SIGNATURES = (b"UnityRaw", b"UnityFS")


def is_serialized(head, size):
	"Whether a file starting with head (16 bytes) and size bytes long looks like a serialized asset file"
	if len(head) < 16:
		return False
	metadata_size, file_size, format, data_offset = struct.unpack(">4I", head[:16])
	return 5 <= format <= 9 and file_size == size and metadata_size < file_size


def kind(path):
	"'archive', 'asset' (an extracted serialized file) or None"
	with open(path, "rb") as f:
		head = f.read(16)
	if head.startswith(ARCHIVE_SIGNATURES):
		return "archive"
	if head.startswith(OTHER_ARCHIVE_SIGNATURES):
		print("Skipping %s: only UnityWeb archives are supported" % path, file=sys.stderr)
		return None
	if is_serialized(head, os.path.getsize(path)):
		return "asset"
	return None


def rows(asset):
	"(path_id, file_id, class, container path) for each container entry; one empty row for a scene"
	ab = asset.objects.get(1)
	if ab is None or ab.class_id != 142:
		yield ("", "", "", "")
		return
	for path, info in ab.read()["m_Container"]:
		ptr = info["asset"]
		if ptr is None:
			yield ("", "", "", path)
			continue
		cls = ""
		if ptr.file_id == 0 and ptr.path_id in asset.objects:
			cls = UnityClass(asset.objects[ptr.path_id].class_id)
		yield (ptr.path_id, ptr.file_id, cls, path)


def index_file(path, root, out):
	rel = os.path.relpath(path, root)
	k = kind(path)
	if k == "archive":
		with open(path, "rb") as f:
			for asset in unitypack.load(f).assets:
				buf = asset._buf
				buf.seek(0)
				head = buf.read(16)
				if not is_serialized(head, buf.seek(0, os.SEEK_END)):
					continue  # main.unity3d also holds DLLs and other files
				for row in rows(asset):
					print(rel, asset.name, *row, sep="\t", file=out)
	elif k == "asset":
		with open(path, "rb") as f:
			asset = Asset.from_file(f)
			for row in rows(asset):
				print("-", rel, *row, sep="\t", file=out)


def main():
	p = ArgumentParser(description=__doc__.strip().split("\n\n")[0])
	p.add_argument("dirs", nargs="+")
	args = p.parse_args()

	for root in args.dirs:
		for dirpath, dirnames, filenames in os.walk(root):
			dirnames.sort()
			for name in sorted(filenames):
				path = os.path.join(dirpath, name)
				try:
					index_file(path, root, sys.stdout)
				except Exception:
					print("Error indexing %s:" % path, file=sys.stderr)
					traceback.print_exc()


if __name__ == "__main__":
	main()
