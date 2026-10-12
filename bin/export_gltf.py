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

Nanos, NPCs and mobs, shinies, the bus and vehicles get the textures the
game puts on them: the XDT's mesh rows name them, and they're taken from
the "texture/<name>.dds" entries of the files in the environment (see
unitypack.xdttextures). The XDT comes from the TableData file in the
environment, or --xdt; --no-xdt keeps the prefabs' own textures. A prefab
the XDT uses with several sets of textures (car colours, hoverboard
designs) is written once per set, as <path>@<texture>.glb.
"""

import argparse
import logging
import os
import re
import struct
import sys
from unitypack.asset import Asset
from unitypack.environment import UnityEnvironment
from unitypack.gltf import GLTFWriter
from unitypack.xdttextures import prefab_key, texture_swaps

GAME_OBJECT = 1
ASSET_BUNDLE = 142
TEXTURE_2D = 28
# the TableData file of 104 and Retrobution, and of 728 and 1013
TABLEDATA_FILES = ('CustomAssetBundle-1dca92eecee4742d985b799d8226666d', 'CustomAssetBundle-TableData')
XDT_PATH = 'xdtdatas.asset'


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
	out = os.path.join(outdir, re.sub(r'[^\w.@-]', '_', name) + '.glb')
	os.makedirs(outdir, exist_ok=True)
	with open(out, 'wb') as o:
		o.write(writer.glb())
	for warning in writer.warnings:
		print('  warning: ' + warning, file=sys.stderr)
	return out


def load_xdt(env, path):
	"The XDT object of a TableData file (path), or of the one in the environment; None if there's none"
	if path:
		f = open(path, 'rb')
		env.files.append(f)
		return Asset.from_file(f, environment=env).get_object_by_path(XDT_PATH).read()
	for name in TABLEDATA_FILES:
		try:
			asset = env.get_asset_by_filename(name)
		except KeyError:
			continue
		return asset.get_object_by_path(XDT_PATH).read()
	return None


def is_serialized(path):
	with open(path, 'rb') as f:
		head = f.read(16)
	if len(head) < 16:
		return False
	metadata_size, file_size, fmt, _ = struct.unpack('>4I', head)
	return 5 <= fmt <= 9 and file_size == os.path.getsize(path) and metadata_size < file_size


class Textures:
	"""
	The environment's "texture/<name>.dds" container entries, found once:
	the files in its folder and one folder down, as UnityEnvironment looks
	"""

	def __init__(self, env):
		self.env = env
		self.index = None
		self.loaded = {}

	def _scan(self):
		# files without type trees log one warning per class they lack; not this scan's business
		root = logging.getLogger()
		level = root.level
		root.setLevel(logging.ERROR)
		try:
			self._scan_files()
		finally:
			root.setLevel(level)

	def _scan_files(self):
		self.index = {}
		base = self.env.base_path or '.'
		paths = []
		for entry in sorted(os.listdir(base)):
			full = os.path.join(base, entry)
			if os.path.isdir(full):
				paths += [os.path.join(full, e) for e in sorted(os.listdir(full))]
			else:
				paths.append(full)
		for path in paths:
			if not os.path.isfile(path) or not is_serialized(path):
				continue
			with open(path, 'rb') as f:
				asset = Asset.from_file(f)
				bundle = asset.objects.get(1)
				if bundle is None or bundle.type_id != ASSET_BUNDLE:
					continue
				for cpath, info in bundle.read()['m_Container']:
					ptr = info['asset']
					if cpath.lower().startswith('texture/') and ptr is not None and ptr.file_id == 0:
						self.index.setdefault(cpath.lower(), (os.path.basename(path), ptr.path_id))

	def get(self, name):
		"The Texture2D the game loads as texture/<name>.dds, or None"
		key = 'texture/%s.dds' % name.lower()
		if key not in self.loaded:
			if self.index is None:
				self._scan()
			found = self.index.get(key)
			texture = None
			if found is not None:
				obj = self.env.get_asset_by_filename(found[0]).objects[found[1]]
				texture = obj.read() if obj.type_id == TEXTURE_2D else None
			self.loaded[key] = texture
		return self.loaded[key]


def override(swap, textures, missing):
	"A GLTFWriter texture_override that applies one XDT row's swap"
	def choose(material, slot):
		name = swap.texture_for(material.name, slot)
		if name is None:
			return None
		texture = textures.get(name)
		if texture is None:
			missing.add(name)  # as in the game, the material keeps its own
		return texture
	return choose


def variant_name(path, swap, several):
	if not several:
		return path
	return '{}@{}'.format(path, '+'.join(t for t in (swap.main, swap.sub) if t))


def main():
	p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	p.add_argument('file', help='an extracted asset file (CustomAssetBundle-... or BuildPlayer-...)')
	p.add_argument('paths', nargs='*', help='container paths to export (default: every prefab)')
	p.add_argument('-o', '--outdir', default='.', help='output directory')
	p.add_argument('--env', help='folder with the other extracted files (default: the input file\'s folder)')
	xdt_group = p.add_mutually_exclusive_group()
	xdt_group.add_argument('--xdt', metavar='TABLEDATA', help='the TableData file whose XDT names the game\'s textures (default: the one in the environment)')
	xdt_group.add_argument('--no-xdt', action='store_true', help='keep the prefabs\' own textures')
	args = p.parse_args()

	env = UnityEnvironment(base_path=args.env or os.path.dirname(os.path.abspath(args.file)))
	with open(args.file, 'rb') as f:
		asset = Asset.from_file(f, environment=env)
		env.assets[os.path.basename(args.file).lower()] = asset

		if not any(o.type_id == ASSET_BUNDLE for o in asset.objects.values()):
			w = GLTFWriter()
			w.add_scene(asset)
			print('scene -> {}'.format(write(w, args.outdir, os.path.basename(args.file))))
			return

		swaps = {}
		if not args.no_xdt:
			try:
				xdt = load_xdt(env, args.xdt)
			except (KeyError, ValueError) as e:
				p.error('{} has no XDT ({}): give --xdt the TableData file, or use --no-xdt'.format(args.xdt, e))
			if xdt is None:
				print('no TableData in {}: exporting the prefabs\' own textures (use --xdt or --no-xdt)'.format(env.base_path), file=sys.stderr)
			else:
				swaps = texture_swaps(xdt)
		textures = Textures(env)

		count = swapped = exported = 0
		for path, ptr in prefabs(asset):
			if args.paths and not any(want.lower() in path.lower() for want in args.paths):
				continue
			sets = swaps.get(prefab_key(path), [None])
			for swap in sets:
				missing = set()
				w = GLTFWriter(texture_override=override(swap, textures, missing) if swap else None)
				w.add_gameobject(ptr.resolve())
				name = variant_name(path, swap, len(sets) > 1) if swap else path
				print('{} -> {}'.format(name, write(w, args.outdir, name)))
				for t in sorted(missing):
					print('  warning: no texture/{}.dds in the environment; kept the prefab\'s own'.format(t), file=sys.stderr)
				count += 1
			exported += 1
			swapped += sets != [None]
	print('{} files exported ({} prefabs, {} with the XDT\'s textures)'.format(count, exported, swapped))


if __name__ == '__main__':
	main()
