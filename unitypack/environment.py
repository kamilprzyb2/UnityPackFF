import os
from urllib.parse import urlparse
from .asset import Asset
from .assetbundle import AssetBundle


def find_file(directory, name):
	"""
	The path of name in directory, ignoring case if there's no exact match:
	files refer to each other in lowercase ("customassetbundle-..."), while
	the files on disk keep their case. None if there's no such file.
	"""
	path = os.path.join(directory, name)
	if os.path.isfile(path):
		return path
	parent, base = os.path.split(path)
	try:
		entries = sorted(os.listdir(parent or "."))
	except OSError:
		return None
	for ent in entries:
		if ent.lower() == base.lower() and os.path.isfile(os.path.join(parent, ent)):
			return os.path.join(parent, ent)
	return None


class UnityEnvironment:
	def __init__(self, base_path=""):
		self.bundles = {}
		self.assets = {}
		self.base_path = base_path
		self.files = []

	def __del__(self):
		for f in self.files:
			f.close()

	def __repr__(self):
		return "%s(base_path=%r)" % (self.__class__.__name__, self.base_path)

	def load(self, file):
		name = getattr(file, "name", "")
		for bundle in self.bundles.values():
			if name and os.path.abspath(name) == os.path.abspath(bundle.path):
				return bundle
		ret = AssetBundle(self)
		ret.load(file)
		# unnamed (in-memory) archives get a key of their own each
		key = ret.name.lower() or "<unnamed %d>" % len(self.bundles)
		self.bundles[key] = ret
		for asset in ret.assets:
			self.assets[asset.name.lower()] = asset
		return ret

	def discover(self, name):
		for bundle in list(self.bundles.values()):
			dirname = os.path.dirname(os.path.abspath(bundle.path))
			for filename in os.listdir(dirname):
				basename = os.path.splitext(os.path.basename(filename))[0]
				if name.lower() == "cab-" + basename.lower():
					f = open(os.path.join(dirname, filename), "rb")
					self.files.append(f)
					self.load(f)

	def get_asset_by_filename(self, name):
		short = os.path.basename(name).lower()
		if short in self.assets:
			return self.assets[short]

		# in base_path, then one directory deep
		path = find_file(self.base_path, name)
		if path is None:
			for d in sorted(os.listdir(self.base_path or ".")):
				if os.path.isdir(os.path.join(self.base_path, d)):
					path = find_file(os.path.join(self.base_path, d), name)
					if path is not None:
						break

		if path is not None:
			f = open(path, "rb")
			self.files.append(f)
			self.assets[short] = Asset.from_file(f, environment=self)
			return self.assets[short]

		self.discover(name)
		self.populate_assets()
		if short in self.assets:
			return self.assets[short]

		raise KeyError("No such asset: %r" % (name))
		#print('WARNING: failed to open asset: %r' % (name))

	def populate_assets(self):
		for bundle in self.bundles.values():
			for asset in bundle.assets:
				asset_name = asset.name.lower()
				if asset_name not in self.assets:
					self.assets[asset_name] = asset

	def get_asset(self, url):
		if not url:
			return None

		u = urlparse(url)
		if u.scheme == "archive":
			archive, name = os.path.split(u.path.lstrip("/").lower())
		else:
			raise NotImplementedError("Unsupported scheme: %r" % (u.scheme))

		if archive not in self.bundles:
			self.discover(archive)

			# Still didn't find it? Give up...
			if archive not in self.bundles:
				raise NotImplementedError("Cannot find %r in %r" % (archive, self.bundles))

		bundle = self.bundles[archive]

		for asset in bundle.assets:
			if asset.name.lower() == name:
				return asset
		raise KeyError("No such asset: %r" % (name))
		#print('WARNING: failed to open asset: %r' % (name))
