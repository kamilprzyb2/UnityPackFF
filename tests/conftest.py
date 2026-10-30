"""
Shared pytest setup.

  pytest                          unit tests only (offline)
  pytest -m integration           curated archives of all three builds
  pytest --sweep                  every archive of every build (2.2 GB; only the curated
                                  archives have recorded values yet, see tests/README.md)
  pytest -m integration --build 1013
  pytest -m integration --record  rewrite tests/expected/<build>/

Integration tests take a `build` (and usually a `bundle`) argument and are
parametrized over the pinned builds here. Expected values are digests and
statistics only, never client data.
"""

import json
from pathlib import Path

import pytest

from tests.bundles import BUILDS, Build, BundleUnavailable, archives_required

EXPECTED_DIR = Path(__file__).resolve().parent / "expected"


def pytest_addoption(parser):
	parser.addoption("--record", action="store_true",
		help="write this run's values into tests/expected/<build>/ instead of comparing")
	parser.addoption("--sweep", action="store_true",
		help="run integration tests over every archive of each build, not just the curated set")
	parser.addoption("--build", action="append", default=[], dest="builds",
		help="limit integration tests to these builds (%s); repeatable" % ", ".join(BUILDS))


def pytest_configure(config):
	if config.getoption("--sweep"):
		config.option.markexpr = "integration"


_builds = {}


def get_build(alias):
	if alias not in _builds:
		_builds[alias] = Build(alias)
	return _builds[alias]


def pytest_generate_tests(metafunc):
	if "build" not in metafunc.fixturenames:
		return
	aliases = metafunc.config.getoption("builds") or list(BUILDS)
	sweep = metafunc.config.getoption("--sweep")
	# a test can narrow the archives with @pytest.mark.bundles(...)
	marker = metafunc.definition.get_closest_marker("bundles")

	if "bundle" not in metafunc.fixturenames:
		metafunc.parametrize("build", aliases, indirect=True)
		return

	params = []
	for alias in aliases:
		b = get_build(alias)
		if marker:
			names = [n for n in marker.args if n in b.bundles]
		else:
			names = b.bundles if sweep else b.curated
		params += [pytest.param(alias, n, id="%s-%s" % (alias, n)) for n in names]
	metafunc.parametrize("build,bundle", params, indirect=["build"])


@pytest.fixture
def build(request):
	return get_build(request.param)


@pytest.fixture
def unpacked(build, bundle):
	"Directory with the archive's files; skips (fails, if archives are required) when the archive can't be fetched."
	try:
		return build.unpack(bundle)
	except BundleUnavailable as e:
		if archives_required():
			pytest.fail(str(e))
		pytest.skip(str(e))


class Expected:
	"""
	The expected values of one build, one file per area: the value of key
	"area/name" is stored under "name" in tests/expected/<build>/<area>.json,
	so tests of different features keep to files of their own.
	"""

	def __init__(self, name, record):
		self.dir = EXPECTED_DIR / name
		self.record = record
		self.areas = {}
		self.dirty = set()

	def area(self, area):
		if area not in self.areas:
			path = self.dir / ("%s.json" % area)
			self.areas[area] = json.loads(path.read_text()) if path.exists() else {}
		return self.areas[area]

	def check(self, key, value):
		area, _, name = key.partition("/")
		if not name:
			raise ValueError("expected values are keyed area/name, not %r" % key)
		data = self.area(area)
		value = json.loads(json.dumps(value))
		if self.record:
			data[name] = value
			self.dirty.add(area)
			return
		if name not in data:
			pytest.fail("no expected value for %r in %s/%s.json; run with --record" % (name, self.dir.name, area))
		assert value == data[name]

	def save(self):
		for area in sorted(self.dirty):
			self.dir.mkdir(parents=True, exist_ok=True)
			data = dict(sorted(self.areas[area].items()))
			(self.dir / ("%s.json" % area)).write_text(json.dumps(data, indent=1) + "\n")


_expected = {}


@pytest.fixture
def expected(request, build):
	"expected.check(\"area/name\", value): compare with (or with --record, store into) tests/expected/<build>/<area>.json."
	if build.name not in _expected:
		_expected[build.name] = Expected(build.name, request.config.getoption("--record"))
	return _expected[build.name]


def pytest_sessionfinish(session):
	for e in _expected.values():
		e.save()
