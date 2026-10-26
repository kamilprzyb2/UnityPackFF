#!/usr/bin/env python3
"""
Download archives of the pinned builds into the test cache and unpack them.

  python3 -m tests.tools.fetch --build 1013            # the curated set
  python3 -m tests.tools.fetch --build all --all       # every archive of every build (2.7 GB)
"""

from tests.tools._common import builds, bundles, parser

from tests.bundles import BundleUnavailable


def fetch(build, names, jobs=None, unpack=True):
	"Fetch and unpack; returns {bundle: unpacked dir or error message}."
	ret = {}
	for name, res in build.fetch_many(names, jobs).items():
		if isinstance(res, BundleUnavailable):
			ret[name] = "unavailable: %s" % res
			continue
		try:
			ret[name] = build.unpack(name) if unpack else res
		except Exception as e:
			ret[name] = "unpack failed: %r" % e
	return ret


def main():
	args = parser(__doc__).parse_args()
	failed = 0
	for build in builds(args):
		names = bundles(args, build)
		res = fetch(build, names, args.jobs)
		bad = {k: v for k, v in res.items() if isinstance(v, str)}
		failed += len(bad)
		print("%s: %d archives in %s, %d failed" % (build.name, len(res), build.cache, len(bad)))
		for k, v in sorted(bad.items()):
			print("  %s: %s" % (k, v))
	return 1 if failed else 0


if __name__ == "__main__":
	raise SystemExit(main())
