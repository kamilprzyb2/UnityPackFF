#!/usr/bin/env python3
"""
Write a terrain's heightmap as a 16-bit grayscale PNG, losslessly, and its
shifted vertices as JSON next to it (out.png -> out.shifts.json).

The PNG is the map seen from above, north (+z) up and east (+x) right:
pixel (column, row) is heightmap vertex (x = column, z = height - 1 - row).
Pixel values are the stored heights, 0 to 32766 for 0 to the terrain's
height scale (the JSON gives the scales).

With --details, also the terrain's details (grass): out.details.json with
the detail prototypes, the wind and the detail resolution, and for each
prototype with any instances an 8-bit PNG out.detail<N>.png, north up as
the heightmap, whose pixel values are how many instances each cell holds.
"""

import argparse
import json
import sys
from unitypack.asset import Asset
from unitypack.engine.terrain import MAX_HEIGHT
from PIL import Image, ImageOps


def find_terrain(asset):
	for k, v in asset.objects.items():
		if v.type_id > 0 and v.type == 'TerrainData':  # (a script object's type needs its script's file)
			return k, v.read()
	sys.exit('no TerrainData in this file')


def terrain_image(terrain):
	"The heightmap as a 16-bit PNG image, north up"
	w, h = terrain.width, terrain.height
	heights = terrain.heights
	img = Image.new('I;16', (w, h))
	img.putdata([heights[x * h + (h - 1 - row)] for row in range(h) for x in range(w)])
	return img


def shifts_json(terrain):
	"The scales and shifts as JSON, one [x, z, flags] shift per line"
	head = json.dumps({'width': terrain.width, 'height': terrain.height, 'scale': terrain.scale, 'max_height': MAX_HEIGHT})
	shifts = ',\n'.join('  ' + json.dumps(list(s)) for s in terrain.shifts)
	return head[:-1] + ',\n "shifts": [\n' + shifts + '\n ]\n}\n'


def stem(png_path):
	return png_path[:-4] if png_path.lower().endswith('.png') else png_path


def shifts_path(png_path):
	return stem(png_path) + '.shifts.json'


def pointer_json(ptr):
	"A texture or GameObject pointer: its name if it's in this file, else the pointer"
	if not ptr:
		return None
	if ptr.file_id == 0:
		try:
			return ptr.resolve().name
		except KeyError:
			pass
	return {'file_id': ptr.file_id, 'path_id': ptr.path_id}


def details_json(terrain):
	"The detail prototypes, the wind and the resolution, as JSON"
	prototypes = []
	for p in terrain.detail_prototypes:
		d = p._asdict()
		d['texture'], d['prototype'] = pointer_json(p.texture), pointer_json(p.prototype)
		d['render_mode'] = getattr(p.render_mode, 'name', p.render_mode)
		prototypes.append(d)
	return json.dumps({'resolution': terrain.detail_resolution, 'waving_grass': terrain.waving_grass,
		'prototypes': prototypes}, indent=1) + '\n'


def detail_images(terrain):
	"{prototype index: 8-bit image of its instance counts, north up}"
	r = terrain.detail_resolution
	return {i: ImageOps.flip(Image.frombytes('L', (r, r), layer)) for i, layer in terrain.detail_layers().items()}


def main():
	ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	ap.add_argument('assetbundle')
	ap.add_argument('out', help='the heightmap PNG to write')
	ap.add_argument('--details', action='store_true', help='also write the details (grass)')
	args = ap.parse_args()

	with open(args.assetbundle, 'rb') as f:
		asset = Asset.from_file(f)
		indx, terrain = find_terrain(asset)
		if args.details:
			details = details_json(terrain)
			images = detail_images(terrain)

	terrain_image(terrain).save(args.out)
	with open(shifts_path(args.out), 'w') as f:
		f.write(shifts_json(terrain))

	print('TerrainData object {}: {}x{} heights, {} shifts'.format(indx, terrain.width, terrain.height, len(terrain.shifts)))
	print('wrote {} and {}'.format(args.out, shifts_path(args.out)))
	if args.details:
		with open(stem(args.out) + '.details.json', 'w') as f:
			f.write(details)
		for i, img in images.items():
			img.save('{}.detail{}.png'.format(stem(args.out), i))
		print('wrote {}.details.json and {} detail layers'.format(stem(args.out), len(images)))


if __name__ == '__main__':
	main()
