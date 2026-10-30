"""
Archives of the pinned builds: they unpack to exactly the files the manifest
lists, and unitypack.load() sees the same files.
"""

import pytest

import unitypack
from tests.bundles import read_unityweb_header

pytestmark = pytest.mark.integration


def test_unpack_matches_manifest(build, bundle, unpacked, expected):
	# Build.unpack() already checked every file against the manifest hashes
	with open(build.fetch(bundle), "rb") as f:
		header = read_unityweb_header(f.read(4096))
	names = [p.name for p in build.inner_files(bundle)]
	assert header["num_levels"] == 1
	expected.check("archive/" + bundle, {"header": header, "files": names})


def test_unitypack_load(build, bundle, unpacked):
	names = [p.name for p in build.inner_files(bundle)]
	with open(build.fetch(bundle), "rb") as f:
		ab = unitypack.load(f)
		assert sorted(a.name for a in ab.assets) == names
		# every file is byte for byte the one in the archive
		for asset in ab.assets:
			asset._buf.seek(0)
			assert asset._buf.read() == (unpacked / asset.name).read_bytes()
