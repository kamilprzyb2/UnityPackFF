"""
Shared pytest setup.

  pytest                          unit tests only (offline)
  pytest -m integration           curated archives of all four builds
  pytest --sweep                  every archive of every build (2.7 GB)
  pytest -m integration --build 1013
  pytest -m integration --record  rewrite tests/expected/<build>.json

Integration tests take a `build` (and usually a `bundle`) argument and are
parametrized over the pinned builds here. Expected values are digests and
statistics only, never client data.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "tests" / "demo"):
	if str(p) not in sys.path:
		sys.path.insert(0, str(p))

from tests.bundles import BUILDS, Build, BundleUnavailable  # noqa: E402

EXPECTED_DIR = Path(__file__).resolve().parent / "expected"


def pytest_addoption(parser):
	parser.addoption("--record", action="store_true",
		help="write this run's values into tests/expected/<build>.json instead of comparing")
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
	"Directory with the archive's files; skips when the archive can't be fetched."
	try:
		return build.unpack(bundle)
	except BundleUnavailable as e:
		pytest.skip(str(e))


class Expected:
	def __init__(self, name, record):
		self.path = EXPECTED_DIR / ("%s.json" % name)
		self.record = record
		self.dirty = False
		self.data = json.loads(self.path.read_text()) if self.path.exists() else {}

	def check(self, key, value):
		value = json.loads(json.dumps(value))
		if self.record:
			self.data[key] = value
			self.dirty = True
			return
		if key not in self.data:
			pytest.fail("no expected value for %r in %s; run with --record" % (key, self.path.name))
		assert value == self.data[key]

	def save(self):
		if self.dirty:
			EXPECTED_DIR.mkdir(exist_ok=True)
			self.path.write_text(json.dumps(dict(sorted(self.data.items())), indent=1) + "\n")


_expected = {}


@pytest.fixture
def expected(request, build):
	"expected.check(key, value): compare with (or with --record, store into) tests/expected/<build>.json."
	if build.name not in _expected:
		_expected[build.name] = Expected(build.name, request.config.getoption("--record"))
	return _expected[build.name]


def pytest_sessionfinish(session):
	for e in _expected.values():
		e.save()
