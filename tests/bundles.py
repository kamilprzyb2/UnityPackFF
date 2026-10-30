"""
Pinned FusionFall client builds for the test suite and the tools in tests/tools/.

Each build is pinned by a launcher manifest in tests/manifests/: the URL of
every archive plus a SHA-256 for the archive and for every file inside it.
Archives are downloaded once into a local cache and checked against those
hashes, so no client data ever needs to live in the repository.

Environment:
  UNITYPACKFF_CACHE   cache directory (default ~/.cache/unitypackff-tests)
  UNITYPACKFF_MIRROR  extra local directories (os.pathsep-separated) holding
                      archives by file name; a file is copied from a mirror
                      only if its hash matches the manifest

The UnityWeb reader here is deliberately independent of unitypack, so the
two can be checked against each other and against the manifest hashes.
"""

import hashlib
import json
import lzma
import os
import shutil
import struct
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

MANIFEST_DIR = Path(__file__).resolve().parent / "manifests"

# short alias -> manifest name
BUILDS = {
	"104": "beta-20100104",
	"728": "beta-20100728",
	"1013": "beta-20111013",
}

MAIN_FILE = "main.unity3d"

# A small set per build that covers each kind of archive: table data, map
# tiles (Orchid Bay, the empty corner tile, the 728 tile with no type trees),
# icons, music, sound, a nano, NPC textures, character models and the main
# archive. Names a build doesn't have are skipped.
CURATED = [
	"TableData.resourceFile",
	"Map_04_02.unity3d",
	"DongResources_04_02.resourceFile",
	"Map_00_00.unity3d",
	"Map_18_14.unity3d",
	"Icons.resourceFile",
	"LobbyMusic.resourceFile",
	"FUSound.resourceFile",
	"FutureNano.resourceFile",
	"NpcTexture.resourceFile",
	"CharacterSelection.resourceFile",
	"World_shared_part1.resourceFile",
	MAIN_FILE,
	# Academy splits things up
	"Nano_001.resourceFile",
	"Character_Texture_npc3.resourceFile",
	"CharacterSelection_wear_face.resourceFile",
	"Manifest.resourceFile",
	"UI_map.resourceFile",
]

USER_AGENT = "UnityPackFF-tests"


class BundleUnavailable(Exception):
	"The archive could not be fetched (gone from the CDN, or no network)."


def cache_root():
	env = os.environ.get("UNITYPACKFF_CACHE")
	if env:
		return Path(env).expanduser()
	return Path.home() / ".cache" / "unitypackff-tests"


def mirrors():
	env = os.environ.get("UNITYPACKFF_MIRROR", "")
	return [Path(p).expanduser() for p in env.split(os.pathsep) if p]


def resolve_build(name):
	"Accept an alias (104, 728, 1013) or a full manifest name."
	if name in BUILDS:
		return BUILDS[name]
	if name in BUILDS.values():
		return name
	raise KeyError("unknown build %r (choose from %s)" % (name, ", ".join(BUILDS)))


def sha256_file(path):
	h = hashlib.sha256()
	with open(path, "rb") as f:
		for chunk in iter(lambda: f.read(1 << 20), b""):
			h.update(chunk)
	return h.hexdigest()


def sha256(data):
	return hashlib.sha256(data).hexdigest()


class Build:
	def __init__(self, name, cache=None):
		self.name = resolve_build(name)
		self.alias = next(a for a, n in BUILDS.items() if n == self.name)
		with open(MANIFEST_DIR / (self.name + ".json")) as f:
			self.manifest = json.load(f)
		self.uuid = self.manifest["uuid"]
		self.asset_url = self.manifest["asset_url"].rstrip("/")
		self.cache = Path(cache) if cache else cache_root() / self.name

	def __repr__(self):
		return "<Build %s>" % self.name

	@property
	def bundles(self):
		"Every archive in the build, the main archive last."
		return sorted(self.manifest["bundles"]) + [MAIN_FILE]

	@property
	def curated(self):
		have = set(self.bundles)
		return [b for b in CURATED if b in have]

	def info(self, bundle):
		if bundle == MAIN_FILE:
			# the manifest has no per-file hashes for the main archive
			return {"compressed_info": self.manifest["main_file_info"], "uncompressed_info": None}
		return self.manifest["bundles"][bundle]

	def url(self, bundle):
		return "%s/%s" % (self.asset_url, bundle)

	def path(self, bundle):
		return self.cache / bundle

	def is_cached(self, bundle):
		p = self.path(bundle)
		return p.exists() and p.stat().st_size == self.info(bundle)["compressed_info"]["size"]

	def fetch(self, bundle):
		"Return the path of the archive, downloading and checking it if needed."
		dest = self.path(bundle)
		want = self.info(bundle)["compressed_info"]
		if self.is_cached(bundle):
			return dest

		dest.parent.mkdir(parents=True, exist_ok=True)
		tmp = dest.with_name(dest.name + ".part")

		for mirror in mirrors():
			src = mirror / bundle
			if src.exists() and src.stat().st_size == want["size"] and sha256_file(src) == want["hash"]:
				shutil.copyfile(src, tmp)
				os.replace(tmp, dest)
				return dest

		req = urllib.request.Request(self.url(bundle), headers={"User-Agent": USER_AGENT})
		h = hashlib.sha256()
		try:
			with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as out:
				for chunk in iter(lambda: resp.read(1 << 20), b""):
					h.update(chunk)
					out.write(chunk)
		except (urllib.error.URLError, OSError) as e:
			tmp.unlink(missing_ok=True)
			raise BundleUnavailable("%s: %s" % (self.url(bundle), e)) from e

		if h.hexdigest() != want["hash"]:
			tmp.unlink(missing_ok=True)
			raise BundleUnavailable("%s: SHA-256 mismatch (got %s, manifest says %s)" % (
				self.url(bundle), h.hexdigest(), want["hash"]))
		os.replace(tmp, dest)
		return dest

	def fetch_many(self, bundles, jobs=8):
		"Fetch several archives in parallel; returns {bundle: path or exception}."
		def one(b):
			try:
				return b, self.fetch(b)
			except BundleUnavailable as e:
				return b, e
		with ThreadPoolExecutor(jobs) as pool:
			return dict(pool.map(one, bundles))

	def unpacked_dir(self, bundle):
		return self.cache / "unpacked" / bundle

	def unpack(self, bundle):
		"""
		Extract the archive's files next to each other, the way the web
		player's cache lays them out, and check them against the manifest.
		Returns the directory.
		"""
		out = self.unpacked_dir(bundle)
		done = out / ".complete"
		if done.exists():
			return out

		archive = read_unityweb(self.fetch(bundle).read_bytes())
		want = self.info(bundle)["uncompressed_info"]
		files = archive.files()
		if want is not None:
			got = {name: {"hash": sha256(data), "size": len(data)} for name, data in files}
			if got != want:
				raise AssertionError("%s: unpacked files do not match the manifest" % bundle)

		out.mkdir(parents=True, exist_ok=True)
		for name, data in files:
			(out / name).write_bytes(data)
		done.write_text("")
		return out

	def inner_files(self, bundle):
		d = self.unpack(bundle)
		return sorted(p for p in d.iterdir() if p.name != ".complete")


# UnityWeb archives (Unity 2.x web player streams)

class UnityWebArchive:
	def __init__(self):
		self.header = {}
		self.levels = []  # one list of (name, bytes) per level

	def files(self):
		return [f for level in self.levels for f in level]


def _cstring(data, pos):
	end = data.index(b"\0", pos)
	return data[pos:end].decode("ascii"), end + 1


def read_unityweb(data):
	"""
	Parse a UnityWeb archive from bytes. All integers are big-endian.

	  signature "UnityWeb", u32 stream version, player version, engine version,
	  u32 min streamed bytes, u32 header size, u32 min levels for load,
	  u32 level count, (u32 compressed end, u32 uncompressed end) per level,
	  u32 bundle size

	Each level is an LZMA-alone stream holding a file table (u32 count, then
	name, u32 offset, u32 size per file; offsets from the level start)
	followed by the files.
	"""
	arc = UnityWebArchive()
	h = arc.header = read_unityweb_header(data)

	start = h["header_size"]
	for compressed_end, _ in h["level_ends"]:
		dec = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE)
		level = dec.decompress(data[start:h["header_size"] + compressed_end])
		start = h["header_size"] + compressed_end

		(count,) = struct.unpack_from(">I", level, 0)
		p = 4
		files = []
		for _ in range(count):
			name, p = _cstring(level, p)
			offset, size = struct.unpack_from(">2I", level, p)
			p += 8
			files.append((name, level[offset:offset + size]))
		arc.levels.append(files)
	return arc


def read_unityweb_header(data):
	"Parse just the archive header (see read_unityweb)."
	h = {}
	pos = 0
	h["signature"], pos = _cstring(data, pos)
	if h["signature"] != "UnityWeb":
		raise ValueError("not a UnityWeb archive (signature %r)" % h["signature"])
	(h["stream_version"],) = struct.unpack_from(">I", data, pos)
	pos += 4
	h["player_version"], pos = _cstring(data, pos)
	h["engine_version"], pos = _cstring(data, pos)
	h["min_streamed_bytes"], h["header_size"], h["min_levels_for_load"], h["num_levels"] = \
		struct.unpack_from(">4I", data, pos)
	pos += 16
	h["level_ends"] = []
	for _ in range(h["num_levels"]):
		h["level_ends"].append(list(struct.unpack_from(">2I", data, pos)))
		pos += 8
	(h["bundle_size"],) = struct.unpack_from(">I", data, pos)
	return h
