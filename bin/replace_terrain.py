#!/usr/bin/env python3
"""
Replace a terrain's heights with an image, and optionally its shifted
vertices, then save the file.

A 16-bit image is read the way dump_terrain.py writes it: north up, pixel
values 0 to 32766. An 8-bit image is read the way older versions of
dump_terrain.py wrote it: one row per x, 255 standing for a height of 20000.

Shifts are kept unless --shifts gives a JSON file in dump_terrain.py's
format (only its "shifts" list is read: [x, z, flags] records, flags 1 = -x,
2 = +x, 4 = -z, 8 = +z). Patches whose heights changed get new
level-of-detail data; how the game counts shifts in it is not known, so
adding shifts doesn't update it.
"""

import argparse
import json
import os
import sys
from unitypack.asset import Asset
from unitypack.engine.terrain import MAX_HEIGHT
from PIL import Image

# what older versions of dump_terrain.py mapped 255 to
OLD_MAX_HEIGHT = 20000


def find_terrain(asset):
	for k, v in asset.objects.items():
		if v.type_id > 0 and v.type == 'TerrainData':  # (a script object's type needs its script's file)
			return v.contents  # kept, so save() writes the changes
	sys.exit('no TerrainData in this file')


def image_heights(img, w, h):
	"The heights in stored order (x-major) from an image"
	if img.size != (w, h):
		sys.exit('the image is {}x{}, the heightmap {}x{}'.format(img.size[0], img.size[1], w, h))

	if img.mode in ('I;16', 'I;16B', 'I'):
		pixels = [img.getpixel((x, y)) for y in range(h) for x in range(w)]
		bad = next((p for p in pixels if not 0 <= p <= MAX_HEIGHT), None)
		if bad is not None:
			sys.exit('pixel value {} is outside 0 to {}'.format(bad, MAX_HEIGHT))
		# north up: row 0 is z = h - 1
		return [pixels[(h - 1 - z) * w + x] for x in range(w) for z in range(h)]

	# older dump_terrain.py: 8 bits, one row per x
	pixels = list(img.convert('L').tobytes())
	return [int((p * OLD_MAX_HEIGHT) / 255) for p in pixels]


def main():
	if len(sys.argv) == 4 and sys.argv[2].lstrip('-').isdigit() or sys.argv[2:3] and sys.argv[2].lower().startswith('0x'):
		sys.exit('replace_terrain.py no longer takes an offset: pass the image and the file, and it saves the file with unitypack (see --help)')

	p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	p.add_argument('image', help='heightmap image (PNG)')
	p.add_argument('asset', help='the file holding the TerrainData (DongResources_XX_YY)')
	p.add_argument('-o', '--output', help='where to save (default: replace the input file)')
	p.add_argument('--shifts', help='JSON file with the new shifts')
	args = p.parse_args()

	with open(args.asset, 'rb') as f:
		asset = Asset.from_file(f)
		terrain = find_terrain(asset)

		terrain.set_heights(image_heights(Image.open(args.image), terrain.width, terrain.height))
		if args.shifts:
			with open(args.shifts) as sf:
				try:
					terrain.set_shifts(json.load(sf)['shifts'])
				except ValueError as e:
					sys.exit('{}: {}'.format(args.shifts, e))

		output = args.output or args.asset
		tmp = output + '.tmp'
		with open(tmp, 'wb') as out:
			asset.save(out)
	os.replace(tmp, output)
	print('wrote {} ({} shifts)'.format(output, len(terrain.shifts)))


if __name__ == '__main__':
	main()
