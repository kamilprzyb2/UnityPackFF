"""
Archives of the pinned builds: they unpack to exactly the files the manifest
lists, and unitypack.load() sees the same files.
"""

import contextlib
import io

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


def test_unitypack_load(build, bundle, unpacked, request):
	names = [p.name for p in build.inner_files(bundle)]
	if len(names) > 1:
		request.applymarker(pytest.mark.xfail(strict=True,
			reason="unitypack.load() reads only the first file of an archive"))
	with open(build.fetch(bundle), "rb") as f, contextlib.redirect_stdout(io.StringIO()):
		ab = unitypack.load(f)
		assert sorted(a.name for a in ab.assets) == names
		# the file it does read is byte for byte the first one in the archive
		first = ab.assets[0]
		first._buf.seek(0)
		assert first._buf.read() == (unpacked / first.name).read_bytes()
