"Small helpers for the unit tests: open and save made-up files with unitypack."

import io

from unitypack.asset import Asset


def open_asset(data, name="CustomAssetBundle-test"):
	"Asset.from_file() needs a file object with a .name, so wrap the bytes in one."
	f = io.BytesIO(data)
	f.name = name
	return Asset.from_file(f)


def save(asset):
	out = io.BytesIO()
	asset.save(out)
	return out.getvalue()
