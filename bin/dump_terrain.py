#!/usr/bin/env python3
"""
Write a terrain's heightmap as a 16-bit grayscale PNG, losslessly, and its
shifted vertices as JSON next to it (out.png -> out.shifts.json).

The PNG is the map seen from above, north (+z) up and east (+x) right:
pixel (column, row) is heightmap vertex (x = column, z = height - 1 - row).
Pixel values are the stored heights, 0 to 32766 for 0 to the terrain's
height scale (the JSON gives the scales).
"""

import json
import sys
from unitypack.asset import Asset
from unitypack.engine.terrain import MAX_HEIGHT
from PIL import Image


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


def shifts_path(png_path):
	return (png_path[:-4] if png_path.lower().endswith('.png') else png_path) + '.shifts.json'


def main(filename, outpath):
	with open(filename, 'rb') as f:
		asset = Asset.from_file(f)
		indx, terrain = find_terrain(asset)

	terrain_image(terrain).save(outpath)
	with open(shifts_path(outpath), 'w') as f:
		f.write(shifts_json(terrain))

	print('TerrainData object {}: {}x{} heights, {} shifts'.format(indx, terrain.width, terrain.height, len(terrain.shifts)))
	print('wrote {} and {}'.format(outpath, shifts_path(outpath)))


if __name__ == '__main__':
	if len(sys.argv) != 3:
		sys.exit('usage: dump_terrain.py assetbundle out.png')
	main(sys.argv[1], sys.argv[2])
