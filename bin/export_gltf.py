#!/usr/bin/env python3
"""
Export the prefabs (GameObjects) of an asset file as glTF binaries (.glb):
their Transform tree, meshes and materials with textures embedded, skins,
and the clips of their Animation components.

Prefabs are taken from the file's AssetBundle container; name container
paths to export only those (a path matches if it contains the text given).
A scene file (BuildPlayer-Map_XX_YY, which has no container) is exported
whole, terrain included, as one .glb named after it.
Pointers into other files resolve when those files are in the same folder
as the input, or one folder down (as extracted archives are laid out); use
--env to look elsewhere.
"""

import argparse
import os
import re
import sys
from unitypack.asset import Asset
from unitypack.environment import UnityEnvironment
from unitypack.gltf import GLTFWriter

GAME_OBJECT = 1
ASSET_BUNDLE = 142


def prefabs(asset):
	"""
	(container path, GameObject pointer) of every GameObject in the file's
	container; a container can list the same entry more than once
	"""
	bundle = next(o for o in asset.objects.values() if o.type_id == ASSET_BUNDLE)
	seen = set()
	for path, info in bundle.read()['m_Container']:
		ptr = info['asset']
		if (path, ptr.path_id) in seen:
			continue
		seen.add((path, ptr.path_id))
		if ptr.file_id == 0 and ptr.path_id in asset.objects and asset.objects[ptr.path_id].type_id == GAME_OBJECT:
			yield path, ptr


def write(writer, outdir, name):
	out = os.path.join(outdir, re.sub(r'[^\w.-]', '_', name) + '.glb')
	os.makedirs(outdir, exist_ok=True)
	with open(out, 'wb') as o:
		o.write(writer.glb())
	for warning in writer.warnings:
		print('  warning: ' + warning, file=sys.stderr)
	return out


def main():
	p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	p.add_argument('file', help='an extracted asset file (CustomAssetBundle-... or BuildPlayer-...)')
	p.add_argument('paths', nargs='*', help='container paths to export (default: every prefab)')
	p.add_argument('-o', '--outdir', default='.', help='output directory')
	p.add_argument('--env', help='folder with the other extracted files (default: the input file\'s folder)')
	args = p.parse_args()

	env = UnityEnvironment(base_path=args.env or os.path.dirname(os.path.abspath(args.file)))
	with open(args.file, 'rb') as f:
		asset = Asset.from_file(f, environment=env)
		env.assets[os.path.basename(args.file).lower()] = asset

		if asset.objects and not asset.tree.type_trees:
			# Map_16..23_12..15 of beta-20100104 and beta-20100728; the
			# layouts in structs.dat are from a later Unity and don't fit
			sys.exit('{}: this file has no type trees, so its objects can\'t be read. '
				'Some map tiles of the early builds are like this; the assets they '
				'point to aren\'t in any known build either.'.format(os.path.basename(args.file)))

		if not any(o.type_id == ASSET_BUNDLE for o in asset.objects.values()):
			w = GLTFWriter()
			w.add_scene(asset)
			print('scene -> {}'.format(write(w, args.outdir, os.path.basename(args.file))))
			return

		count = 0
		for path, ptr in prefabs(asset):
			if args.paths and not any(want.lower() in path.lower() for want in args.paths):
				continue
			w = GLTFWriter()
			w.add_gameobject(ptr.resolve())
			print('{} -> {}'.format(path, write(w, args.outdir, path)))
			count += 1
	print('{} prefabs exported'.format(count))


if __name__ == '__main__':
	main()
