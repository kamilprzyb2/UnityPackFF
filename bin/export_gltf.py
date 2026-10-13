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

Nanos, NPCs and mobs, shinies, the bus, vehicles and what players wear
(clothes, hats, glasses, weapons, back items, faces, hair) get the textures
the game puts on them: the XDT's mesh rows name them, and they're taken from
the "texture/<name>.dds" entries of the files in the environment (see
unitypack.xdttextures). The XDT comes from the TableData file in the
environment, or --xdt; --no-xdt keeps the prefabs' own textures. A prefab
the XDT uses with several sets of textures (car colours, hoverboard
designs, a face's eye colours) is written once per set, as
<path>@<texture>.glb.

With --as-worn, what players wear skinned into the character (clothes,
capes, faces, hair: the XDT says which) exports as the game uses it: only
its skinned meshes. The game never draws the plain meshes beside them
(some hair carries 3ds Max Biped helper boxes there), so they are left out,
with a warning for any that is textured. It needs the XDT.

With --actor-bones, the scripts the game binds worn items to the character
with go into the extras: the actor's (actor/m.kfm, w.kfm) list of bones,
as node indices, on its root; and on each worn item's skin, the index into
that list of each joint, for boys and for girls (see
unitypack.gltf.GLTFWriter.add_gameobject()).

With --split-clips, each export is written without its clips, as
<name>.glb, and each clip on its own as <name>@<clip>.glb: the same nodes
in the same order, with no meshes, so a viewer can load a clip when it's
needed and play it on the model.

With --external DIR, images and mesh data go to DIR instead of into each
.glb, as files named by their content that every .glb using them points to
(by a path relative to the output directory). Exports into the same DIR
share them: map tiles their textures and props, prefabs their skins.
"""

import argparse
import logging
import os
import re
import struct
import sys
from unitypack.asset import Asset
from unitypack.environment import UnityEnvironment
from unitypack.gltf import ExternalFiles, GLTFWriter
from unitypack.xdttextures import prefab_key, texture_swaps, worn_prefabs

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


def write(writer, outdir, name, split_clips=False):
	"""
	Write a .glb named after name; with split_clips, one without the clips
	and one per clip (<name>@<clip>.glb). Returns the first's path and the
	number of clip files.
	"""
	parts = [(name, writer)]
	if split_clips:
		base, clips = writer.split_clips()
		parts = [(name, base)]
		seen = {}
		for clip, w in clips:
			seen[clip] = seen.get(clip, 0) + 1
			parts.append(('{}@{}'.format(name, clip) + ('-%d' % seen[clip] if seen[clip] > 1 else ''), w))
	os.makedirs(outdir, exist_ok=True)
	for n, w in parts:
		out = os.path.join(outdir, re.sub(r'[^\w.@-]', '_', n) + '.glb')
		with open(out, 'wb') as o:
			o.write(w.glb(outdir))
	for warning in writer.warnings:
		print('  warning: ' + warning, file=sys.stderr)
	first = os.path.join(outdir, re.sub(r'[^\w.@-]', '_', name) + '.glb')
	return first, len(parts) - 1


def clip_note(count):
	return ' (+{} clip files)'.format(count) if count else ''


def report_external(external):
	if external is not None:
		print('{} files in {} ({} new)'.format(len(external.names), external.directory, len(external.written)))


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
					if not cpath.lower().startswith('texture/') or ptr is None:
						continue
					# an entry can point into another file (728's CharTexture does)
					where = os.path.basename(path) if ptr.file_id == 0 else os.path.basename(asset.asset_refs[ptr.file_id].file_path)
					self.index.setdefault(cpath.lower(), (where, ptr.path_id))

	def get(self, name):
		"The Texture2D the game loads as texture/<name>.dds, or None"
		key = 'texture/%s.dds' % name.lower()
		if key not in self.loaded:
			if self.index is None:
				self._scan()
			found = self.index.get(key)
			texture = None
			if found is not None:
				try:
					obj = self.env.get_asset_by_filename(found[0]).objects[found[1]]
				except KeyError:  # in a file the environment doesn't have
					obj = None
				texture = obj.read() if obj is not None and obj.type_id == TEXTURE_2D else None
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


def variant_name(path, swap, sets):
	"The prefab's name for one of its texture sets: by its first texture, or both when that's not enough"
	if len(sets) == 1:
		return path
	if len({s.main for s in sets}) == len(sets):
		return '{}@{}'.format(path, swap.main)
	return '{}@{}'.format(path, '+'.join(t for t in (swap.main, swap.sub) if t))


def main():
	p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	p.add_argument('file', help='an extracted asset file (CustomAssetBundle-... or BuildPlayer-...)')
	p.add_argument('paths', nargs='*', help='container paths to export (default: every prefab)')
	p.add_argument('-o', '--outdir', default='.', help='output directory')
	p.add_argument('--env', help='folder with the other extracted files (default: the input file\'s folder)')
	p.add_argument('--colliders', action='store_true', help='add colliders and rigidbodies to the nodes\' extras, with collision meshes no node draws')
	p.add_argument('--external', metavar='DIR', help='write images and mesh data to DIR, named by their content and shared by every export into it')
	xdt_group = p.add_mutually_exclusive_group()
	xdt_group.add_argument('--xdt', metavar='TABLEDATA', help='the TableData file whose XDT names the game\'s textures (default: the one in the environment)')
	xdt_group.add_argument('--no-xdt', action='store_true', help='keep the prefabs\' own textures (no XDT: not with --as-worn)')
	p.add_argument('--split-clips', action='store_true', help='write each clip to a file of its own, <name>@<clip>.glb, and the model without them')
	p.add_argument('--actor-bones', action='store_true', help='add the bone lists that bind worn items to the character (ActorSkinCombiner, ActorWearIndexTable) to the extras')
	p.add_argument('--as-worn', action='store_true', help='export what players wear (clothes, capes, faces, hair) with its skinned meshes only, as the game combines it; needs the XDT')
	args = p.parse_args()
	if args.as_worn and args.no_xdt:
		p.error('--as-worn needs the XDT to know what is worn: drop --no-xdt')

	env = UnityEnvironment(base_path=args.env or os.path.dirname(os.path.abspath(args.file)))
	external = ExternalFiles(args.external) if args.external else None
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
			w = GLTFWriter(external=external)
			w.add_scene(asset, colliders=args.colliders)
			out, clips = write(w, args.outdir, os.path.basename(args.file), args.split_clips)
			print('scene -> {}{}'.format(out, clip_note(clips)))
			report_external(external)
			return

		swaps, worn, worn_all = {}, set(), set()
		if not args.no_xdt:
			try:
				xdt = load_xdt(env, args.xdt)
			except (KeyError, ValueError) as e:
				p.error('{} has no XDT ({}): give --xdt the TableData file, or use --no-xdt'.format(args.xdt, e))
			if xdt is None:
				if args.as_worn:
					p.error('no TableData in {}: --as-worn needs the XDT, give --xdt the TableData file'.format(env.base_path))
				print('no TableData in {}: exporting the prefabs\' own textures (use --xdt or --no-xdt)'.format(env.base_path), file=sys.stderr)
			else:
				swaps = texture_swaps(xdt)
				worn_all = worn_prefabs(xdt)
				worn = worn_all if args.as_worn else set()
		textures = Textures(env)

		count = swapped = exported = left_out = 0
		for path, ptr in prefabs(asset):
			if args.paths and not any(want.lower() in path.lower() for want in args.paths):
				continue
			key = prefab_key(path)
			sets = swaps.get(key, [None])
			for swap in sets:
				missing = set()
				w = GLTFWriter(texture_override=override(swap, textures, missing) if swap else None, external=external)
				w.add_gameobject(ptr.resolve(), colliders=args.colliders, skinned_only=key in worn, actor_bones=args.actor_bones)
				name = variant_name(path, swap, sets) if swap else path
				note = ' ({} plain meshes left out)'.format(len(w.left_out)) if w.left_out else ''
				out, clips = write(w, args.outdir, name, args.split_clips)
				print('{} -> {}{}{}'.format(name, out, note, clip_note(clips)))
				for t in sorted(missing):
					print('  warning: no texture/{}.dds in the environment; kept the prefab\'s own'.format(t), file=sys.stderr)
				if key in worn_all:
					for mesh in w.unbound_skins:
						print('  warning: {} has no ActorWearIndexTable: it can\'t be bound to the character'.format(mesh), file=sys.stderr)
				count += 1
				left_out += len(w.left_out)
			exported += 1
			swapped += sets != [None]
	report_external(external)
	worn_note = ', {} plain meshes left out of what players wear'.format(left_out) if args.as_worn else ''
	print('{} files exported ({} prefabs, {} with the XDT\'s textures{})'.format(count, exported, swapped, worn_note))


if __name__ == '__main__':
	main()
