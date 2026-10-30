#!/usr/bin/env python3
"""
Dump FusionFall's per-map data as JSON, one <file>.json per input file
that has any:

- from a map tile scene (BuildPlayer-Map_XX_YY): its MapAttributeTable
  (rows of 129 flag bytes, z = 0 first, x across; flags 8 = poison,
  16 = heal, 1 and 4 = item rules, 64 and up = roads), how many cells
  have each flag, and its fog, sky and light colours;
- from main.unity3d's sharedassets0.assets: the music and ambient zones
  (polygons on the map and the track each plays).
"""

import argparse
import json
import os
import sys
from unitypack.asset import Asset
from unitypack.mapdata import map_attributes, music_zones, scene_colors


def dump(asset):
	out = {}
	attributes = map_attributes(asset)
	if attributes is not None:
		out['attributes'] = {
			'size': attributes.size,
			'counts': {str(bit): len(attributes.cells(bit)) for bit in (1, 2, 4, 8, 16, 32, 64, 128)},
			'rows': attributes.rows(),
		}
	colors = scene_colors(asset)
	if colors is not None:
		out['colors'] = colors
	zones = music_zones(asset)
	if zones is not None:
		out.update({kind: [z._asdict() for z in zs] for kind, zs in zones.items()})
	return out


def main():
	p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	p.add_argument('files', nargs='+', help='extracted asset files')
	p.add_argument('-o', '--outdir', default='.', help='output directory')
	args = p.parse_args()

	os.makedirs(args.outdir, exist_ok=True)
	for path in args.files:
		with open(path, 'rb') as f:
			data = dump(Asset.from_file(f))
		if not data:
			print('{}: no map data'.format(path), file=sys.stderr)
			continue
		out = os.path.join(args.outdir, os.path.basename(path) + '.json')
		with open(out, 'w') as f:
			json.dump(data, f, separators=(',', ':'))
		print('{} -> {} ({})'.format(path, out, ', '.join(data)))


if __name__ == '__main__':
	main()
