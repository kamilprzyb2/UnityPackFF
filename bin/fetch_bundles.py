#!/usr/bin/env python3
"""
Download FusionFall archives (.unity3d, .resourceFile) and unpack them.

SOURCE is one of:
  - a launcher manifest, by path or URL: a game version's .json, as the
    OpenFusion launcher ships them and ofapi servers serve them
    (/versions/<uuid>.json). Name the archives to fetch from it, or
    patterns such as 'Map_04_*' (case doesn't matter); --list shows what it
    has, --all takes everything. Every download is checked against the
    manifest's SHA-256, and with --unpack so is every file inside.
  - the URL of one archive, downloaded as it is (nothing to check it against)
  - an archive on disk, to unpack it

  fetch_bundles.py manifest.json --list
  fetch_bundles.py manifest.json TableData.resourceFile 'Map_04_*' --unpack -o ff
  fetch_bundles.py https://cdn.example.org/ff/build/Icons.resourceFile --unpack
  fetch_bundles.py Icons.resourceFile --unpack

Archives are saved in OUTDIR. One that is already there with the manifest's
hash (or, without a manifest, at all) isn't downloaded again; one with
another hash is replaced. With --unpack, the files inside each archive
(CustomAssetBundle-..., BuildPlayer-...) are written to
OUTDIR/unpacked/<archive>/. The other scripts find files referenced from
another archive one folder down from where they look, so point them at
OUTDIR/unpacked:

  export_gltf.py ff/unpacked/FutureNano.resourceFile/CustomAssetBundle-... --env ff/unpacked
"""

import fnmatch
import hashlib
import json
import os
import posixpath
import sys
import urllib.parse
import urllib.request
from argparse import ArgumentParser
from concurrent.futures import ThreadPoolExecutor

import unitypack

USER_AGENT = "UnityPackFF"
ARCHIVE_SIGNATURES = (b"UnityWeb", b"streamed", b"UnityRaw", b"UnityFS")


class FetchError(Exception):
	pass


def is_url(source):
	return urllib.parse.urlsplit(source).scheme in ("http", "https", "file")


def is_web_url(url):
	return urllib.parse.urlsplit(url).scheme in ("http", "https")


def safe_name(name):
	"A file name taken from a manifest or an archive, if it stays inside the folder it's written to"
	if not name or name in (".", "..") or any(c in name for c in "/\\\0"):
		raise FetchError("refusing the file name %r" % (name))
	return name


def sha256(data):
	return hashlib.sha256(data).hexdigest()


def sha256_file(path):
	h = hashlib.sha256()
	with open(path, "rb") as f:
		for chunk in iter(lambda: f.read(1 << 20), b""):
			h.update(chunk)
	return h.hexdigest()


def open_url(url):
	return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=60)


def download(url, dest, want=None):
	"""
	Download url to dest (through dest.part, so dest is never half written).
	want: the manifest's {"hash", "size"}, checked before dest is replaced.
	"""
	if os.path.exists(dest):
		if want is None:
			return "already there"
		if os.path.getsize(dest) == want["size"] and sha256_file(dest) == want["hash"]:
			return "already there"

	tmp = dest + ".part"
	h = hashlib.sha256()
	try:
		with open_url(url) as resp, open(tmp, "wb") as out:
			for chunk in iter(lambda: resp.read(1 << 20), b""):
				h.update(chunk)
				out.write(chunk)
	except OSError as e:  # urllib's errors are OSErrors too
		if os.path.exists(tmp):
			os.remove(tmp)
		raise FetchError("%s: %s" % (url, e)) from e

	if want is not None and h.hexdigest() != want["hash"]:
		os.remove(tmp)
		raise FetchError("%s: SHA-256 %s, but the manifest says %s" % (url, h.hexdigest(), want["hash"]))
	os.replace(tmp, dest)
	return "downloaded"


def unpack(archive, outdir, want=None):
	"""
	Write the files inside archive to outdir and return how many there are.
	want: the manifest's {file name: {"hash", "size"}}, checked before
	anything is written.
	"""
	try:
		with open(archive, "rb") as f:
			bundle = unitypack.load(f)
	except (NotImplementedError, AssertionError, EOFError, ValueError) as e:
		raise FetchError("%s: can't read the archive (%s)" % (archive, e)) from e
	if not bundle.compressed:
		raise FetchError("%s: a %s archive, which this script can't unpack" % (archive, bundle.signature))

	files = [(safe_name(name), data) for name, data in bundle.files]
	if want is not None:
		got = {name: {"hash": sha256(data), "size": len(data)} for name, data in files}
		if got != want:
			wrong = sorted(n for n in set(got) | set(want) if got.get(n) != want.get(n))
			raise FetchError("%s: these files inside don't match the manifest: %s" % (archive, ", ".join(wrong)))

	os.makedirs(outdir, exist_ok=True)
	for name, data in files:
		with open(os.path.join(outdir, name), "wb") as f:
			f.write(data)
	return len(files)


def load_manifest(source):
	try:
		if is_url(source):
			with open_url(source) as resp:
				data = resp.read()
		else:
			with open(source, "rb") as f:
				data = f.read()
	except OSError as e:
		raise FetchError("%s: %s" % (source, e)) from e
	try:
		manifest = json.loads(data)
	except ValueError:
		manifest = None
	if not isinstance(manifest, dict) or not isinstance(manifest.get("bundles"), dict) or not isinstance(manifest.get("asset_url"), str):
		raise FetchError("%s: not a launcher manifest (no asset_url and bundles)" % (source))
	return manifest


def manifest_archives(manifest):
	"{archive name: (url, its hash and size, {file name: hash and size} or None)}"
	base = manifest["asset_url"].rstrip("/")
	ret = {}
	for name, info in manifest["bundles"].items():
		if not isinstance(info, dict) or not isinstance(info.get("compressed_info"), dict) or not {"hash", "size"} <= set(info["compressed_info"]):
			raise FetchError("%r in the manifest has no compressed_info with a hash and size" % (name))
		ret[name] = (base + "/" + urllib.parse.quote(name), info["compressed_info"], info.get("uncompressed_info"))
	if manifest.get("main_file_url") and manifest.get("main_file_info"):
		# the manifest has no hashes for the files inside the main archive
		url = manifest["main_file_url"]
		name = urllib.parse.unquote(posixpath.basename(urllib.parse.urlsplit(url).path)) or "main.unity3d"
		ret[name] = (url, manifest["main_file_info"], None)
	return ret


def pick(archives, patterns):
	"The archive names that match any of the patterns, and the patterns that match none"
	picked, unmatched = set(), []
	for pattern in patterns:
		hits = [n for n in archives if fnmatch.fnmatchcase(n.lower(), pattern.lower())]
		if not hits:
			unmatched.append(pattern)
		picked.update(hits)
	return sorted(picked), unmatched


def fetch(name, url, want, inner, args):
	"Download one archive (url None: it's already on disk at name) and unpack it if asked; returns a report line"
	if url is None:
		path, done = name, []
		name = os.path.basename(name)
	else:
		path = os.path.join(args.outdir, safe_name(name))
		done = [download(url, path, want)]
	if args.unpack:
		n = unpack(path, os.path.join(args.outdir, "unpacked", safe_name(name)), inner)
		done.append("%d file%s unpacked" % (n, "" if n == 1 else "s"))
	return "%s: %s" % (name, ", ".join(done) or "nothing to do (add --unpack)")


def main():
	p = ArgumentParser(description="Download FusionFall archives from a launcher manifest or a URL, and unpack them.")
	p.add_argument("source", help="a launcher manifest (path or URL), an archive URL, or an archive on disk")
	p.add_argument("names", nargs="*", help="with a manifest: archive names or patterns to fetch")
	p.add_argument("-o", "--outdir", default=".", help="where archives go (default: here); unpacked files go to OUTDIR/unpacked/<archive>/")
	p.add_argument("--unpack", action="store_true", help="also write the files inside each archive")
	p.add_argument("--list", action="store_true", help="with a manifest: list its archives and their sizes, and stop")
	p.add_argument("--all", action="store_true", help="with a manifest: fetch every archive in it")
	p.add_argument("--jobs", type=int, default=4, help="archives fetched at once (default: 4)")
	args = p.parse_args()

	source = args.source
	if is_url(source):
		is_manifest = urllib.parse.urlsplit(source).path.lower().endswith(".json")
	elif os.path.isfile(source):
		with open(source, "rb") as f:
			is_manifest = not f.read(16).startswith(ARCHIVE_SIGNATURES)
	else:
		p.error("no such file: %s" % (source))

	if not is_manifest:
		if args.names or args.list or args.all:
			p.error("archive names, --list and --all go with a manifest; %s is an archive" % (source))
		if is_url(source):
			name = urllib.parse.unquote(posixpath.basename(urllib.parse.urlsplit(source).path))
			jobs = [(name, source, None, None)]
		else:
			jobs = [(source, None, None, None)]
	else:
		try:
			archives = manifest_archives(load_manifest(source))
		except FetchError as e:
			sys.exit(str(e))
		if args.list:
			for name in sorted(archives):
				print("%-48s %10.1f MB" % (name, archives[name][1]["size"] / 1e6))
			return
		if args.all:
			names = sorted(archives)
		elif args.names:
			names, unmatched = pick(archives, args.names)
			if unmatched:
				p.error("not in the manifest: %s (see --list)" % (", ".join(unmatched)))
		else:
			p.error("name the archives to fetch (see --list), or give --all")
		if is_web_url(source):
			# a manifest from the web only sends us to the web, not to local files
			names_off = [n for n in names if not is_web_url(archives[n][0])]
			if names_off:
				sys.exit("%s: refusing non-http URLs for %s" % (source, ", ".join(names_off)))
		jobs = [(n, *archives[n]) for n in names]

	os.makedirs(args.outdir, exist_ok=True)

	def run(job):
		try:
			return True, fetch(*job, args)
		except FetchError as e:
			return False, str(e)

	failed = 0
	with ThreadPoolExecutor(max(1, args.jobs)) as pool:
		for ok, line in pool.map(run, jobs):
			print(line, file=sys.stdout if ok else sys.stderr)
			failed += not ok
	if failed:
		sys.exit("%d of %d archives failed" % (failed, len(jobs)))


if __name__ == "__main__":
	main()
