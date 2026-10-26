"""
Smoke tests for the bin/ scripts, run as a user runs them, on made-up files.
"""

import json
import subprocess
import sys

from tests import synth
from tests.unit.support import ROOT, run_script as run


def test_index_bundles(tmp_path):
	(tmp_path / "X.resourceFile").write_bytes(synth.unityweb([("CustomAssetBundle-x", synth.bundle_file())]))
	(tmp_path / "Map.unity3d").write_bytes(synth.unityweb([("BuildPlayer-Map", synth.sample_file()), ("BuildPlayer-Map.sharedAssets", synth.sample_file())]))
	(tmp_path / "cache").mkdir()
	(tmp_path / "cache" / "CustomAssetBundle-y").write_bytes(synth.bundle_file())  # extracted, as in the client's cache
	(tmp_path / "notes.txt").write_text("not an asset")
	(tmp_path / "Raw.unity3d").write_bytes(b"UnityRaw\0" + bytes(60))  # reported on stderr, not indexed
	null = synth.asset_bundle("n", [], [("assets/gone.txt", 0, 0, (0, 0))])
	(tmp_path / "cache" / "CustomAssetBundle-z").write_bytes(synth.serialized_file({142: synth.ASSET_BUNDLE}, [(1, 142, 142, null)]))

	rows = [line.split("\t") for line in run("index_bundles.py", tmp_path).splitlines()]
	assert rows == [
		["Map.unity3d", "BuildPlayer-Map", "", "", "", ""],  # a scene: no AssetBundle object
		["Map.unity3d", "BuildPlayer-Map.sharedAssets", "", "", "", ""],
		["X.resourceFile", "CustomAssetBundle-x", "2", "0", "TextAsset", "assets/a.txt"],
		["-", "cache/CustomAssetBundle-y", "2", "0", "TextAsset", "assets/a.txt"],
		["-", "cache/CustomAssetBundle-z", "", "", "", "assets/gone.txt"],  # a null pointer
	]


# fetch_bundles.py on made-up archives, served from a folder (file://) or a
# local web server, with launcher manifests written for them

import hashlib  # noqa: E402
import http.server  # noqa: E402
import threading  # noqa: E402
from functools import partial  # noqa: E402


def info(data):
	return {"hash": hashlib.sha256(data).hexdigest(), "size": len(data)}


def cdn(tmp_path, base_url=None):
	"Two archives and a main archive in tmp_path/cdn, and a manifest for them; returns (manifest path, {name: inner files})"
	inner = {
		"Map_04_02.unity3d": [("BuildPlayer-Map_04_02", synth.sample_file()), ("BuildPlayer-Map_04_02.sharedAssets", synth.bundle_file())],
		"TableData.resourceFile": [("CustomAssetBundle-1dca92eecee4742d985b799d8226666d", synth.bundle_file())],
		"main.unity3d": [("mainData", synth.sample_file())],
	}
	(tmp_path / "cdn").mkdir()
	for name, files in inner.items():
		(tmp_path / "cdn" / name).write_bytes(synth.unityweb(files))
	base = base_url or (tmp_path / "cdn").as_uri()

	def entry(name):
		return {"compressed_info": info((tmp_path / "cdn" / name).read_bytes()),
			"uncompressed_info": {n: info(d) for n, d in inner[name]}}
	manifest = {
		"name": "test-build", "asset_url": base,
		"main_file_url": base + "/main.unity3d", "main_file_info": info((tmp_path / "cdn" / "main.unity3d").read_bytes()),
		"bundles": {n: entry(n) for n in inner if n != "main.unity3d"},
	}
	(tmp_path / "test-build.json").write_text(json.dumps(manifest))
	return tmp_path / "test-build.json", inner


def fetch_bundles(*args):
	return subprocess.run([sys.executable, str(ROOT / "bin" / "fetch_bundles.py"), *map(str, args)],
		capture_output=True, text=True, env={"PYTHONPATH": str(ROOT)})


def test_fetch_bundles_lists_a_manifest(tmp_path):
	manifest, _ = cdn(tmp_path)
	r = fetch_bundles(manifest, "--list")
	assert r.returncode == 0, r.stderr
	assert [line.split()[0] for line in r.stdout.splitlines()] == ["Map_04_02.unity3d", "TableData.resourceFile", "main.unity3d"]


def test_fetch_bundles_from_a_manifest(tmp_path):
	manifest, inner = cdn(tmp_path)
	out = tmp_path / "out"
	r = fetch_bundles(manifest, "map_04_*", "MAIN.unity3d", "--unpack", "-o", out)
	assert r.returncode == 0, r.stderr
	assert r.stdout.splitlines() == [
		"Map_04_02.unity3d: downloaded, 2 files unpacked",
		"main.unity3d: downloaded, 1 file unpacked",
	]
	assert (out / "Map_04_02.unity3d").read_bytes() == (tmp_path / "cdn" / "Map_04_02.unity3d").read_bytes()
	for name in ("Map_04_02.unity3d", "main.unity3d"):
		for file, data in inner[name]:
			assert (out / "unpacked" / name / file).read_bytes() == data
	assert not (out / "TableData.resourceFile").exists()
	assert not list(out.glob("*.part"))

	# again: nothing to download
	r = fetch_bundles(manifest, "Map_04_02.unity3d", "-o", out)
	assert r.stdout == "Map_04_02.unity3d: already there\n"


def test_fetch_bundles_replaces_a_wrong_copy(tmp_path):
	manifest, _ = cdn(tmp_path)
	(tmp_path / "out").mkdir()
	(tmp_path / "out" / "TableData.resourceFile").write_bytes(b"not this")
	r = fetch_bundles(manifest, "TableData.resourceFile", "-o", tmp_path / "out")
	assert r.stdout == "TableData.resourceFile: downloaded\n"
	assert (tmp_path / "out" / "TableData.resourceFile").read_bytes() == (tmp_path / "cdn" / "TableData.resourceFile").read_bytes()


def test_fetch_bundles_checks_hashes(tmp_path):
	manifest, _ = cdn(tmp_path)
	m = json.loads(manifest.read_text())
	m["bundles"]["TableData.resourceFile"]["compressed_info"]["hash"] = "0" * 64
	m["bundles"]["Map_04_02.unity3d"]["uncompressed_info"]["BuildPlayer-Map_04_02"]["hash"] = "0" * 64
	manifest.write_text(json.dumps(m))
	out = tmp_path / "out"

	r = fetch_bundles(manifest, "--all", "--unpack", "-o", out)
	assert r.returncode == 1
	assert "TableData.resourceFile: SHA-256" in r.stderr
	assert "these files inside don't match the manifest: BuildPlayer-Map_04_02" in r.stderr
	assert "2 of 3 archives failed" in r.stderr
	assert "main.unity3d: downloaded, 1 file unpacked" in r.stdout
	assert not (out / "TableData.resourceFile").exists()
	assert not list(out.glob("*.part"))
	assert not (out / "unpacked" / "Map_04_02.unity3d").exists()


def test_fetch_bundles_refuses_names_that_leave_the_folder(tmp_path):
	manifest, _ = cdn(tmp_path)
	m = json.loads(manifest.read_text())
	m["bundles"]["../evil.unity3d"] = m["bundles"]["Map_04_02.unity3d"]
	manifest.write_text(json.dumps(m))
	r = fetch_bundles(manifest, "*evil*", "-o", tmp_path / "out")
	assert r.returncode == 1
	assert "refusing the file name '../evil.unity3d'" in r.stderr
	assert not (tmp_path / "evil.unity3d").exists()

	# and inside an archive
	(tmp_path / "bad.unity3d").write_bytes(synth.unityweb([("../escaped", synth.sample_file())]))
	r = fetch_bundles(tmp_path / "bad.unity3d", "--unpack", "-o", tmp_path / "out")
	assert r.returncode == 1
	assert "refusing the file name '../escaped'" in r.stderr
	assert not (tmp_path / "out" / "unpacked" / "escaped").exists()


def test_fetch_bundles_archive_url_and_file(tmp_path):
	_, inner = cdn(tmp_path)
	out = tmp_path / "out"
	r = fetch_bundles((tmp_path / "cdn" / "TableData.resourceFile").as_uri(), "-o", out)
	assert r.stdout == "TableData.resourceFile: downloaded\n"
	r = fetch_bundles(tmp_path / "cdn" / "Map_04_02.unity3d", "--unpack", "-o", out)
	assert r.stdout == "Map_04_02.unity3d: 2 files unpacked\n"
	assert (out / "unpacked" / "Map_04_02.unity3d" / "BuildPlayer-Map_04_02").read_bytes() == inner["Map_04_02.unity3d"][0][1]

	r = fetch_bundles(tmp_path / "cdn" / "Map_04_02.unity3d", "Icons.resourceFile")
	assert r.returncode == 2
	assert "go with a manifest" in r.stderr


def test_fetch_bundles_rejects_what_isnt_a_manifest(tmp_path):
	(tmp_path / "x.json").write_text('{"game_versions": []}')
	r = fetch_bundles(tmp_path / "x.json", "--list")
	assert r.returncode == 1
	assert "not a launcher manifest" in r.stderr
	manifest, _ = cdn(tmp_path)
	m = json.loads(manifest.read_text())
	del m["bundles"]["TableData.resourceFile"]["compressed_info"]["hash"]
	(tmp_path / "broken.json").write_text(json.dumps(m))
	r = fetch_bundles(tmp_path / "broken.json", "--list")
	assert r.returncode == 1
	assert "'TableData.resourceFile' in the manifest has no compressed_info with a hash and size" in r.stderr
	r = fetch_bundles(manifest, "Nope.resourceFile")
	assert r.returncode == 2
	assert "not in the manifest: Nope.resourceFile" in r.stderr


def serve(directory):
	server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(directory)))
	threading.Thread(target=server.serve_forever, daemon=True).start()
	return server


class QuietHandler(http.server.SimpleHTTPRequestHandler):
	def log_message(self, *args):
		pass


def test_fetch_bundles_over_http(tmp_path):
	server = serve(tmp_path)
	try:
		url = "http://127.0.0.1:%d" % (server.server_address[1])
		manifest, inner = cdn(tmp_path, url + "/cdn")
		r = fetch_bundles(url + "/" + manifest.name, "TableData.resourceFile", "--unpack", "-o", tmp_path / "out")
		assert r.returncode == 0, r.stderr
		assert r.stdout == "TableData.resourceFile: downloaded, 1 file unpacked\n"

		# a manifest from the web can't point at local files
		m = json.loads(manifest.read_text())
		m["asset_url"] = (tmp_path / "cdn").as_uri()
		manifest.write_text(json.dumps(m))
		r = fetch_bundles(url + "/" + manifest.name, "TableData.resourceFile", "-o", tmp_path / "out2")
		assert r.returncode == 1
		assert "refusing non-http URLs for TableData.resourceFile" in r.stderr

		r = fetch_bundles(url + "/missing.json", "--list")
		assert r.returncode == 1
		assert "404" in r.stderr
	finally:
		server.shutdown()
