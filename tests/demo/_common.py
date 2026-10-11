"""
Shared command-line handling for the demo scripts.

Every demo takes --build (repeatable, or "all"), picks the curated archive
set unless --all or --bundle is given, and writes into --out. Integration
tests import the same functions the demos run, so the two can't drift.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # the repository: tests/demo/_common.py
if str(ROOT) not in sys.path:
	sys.path.insert(0, str(ROOT))

from tests.bundles import BUILDS, Build  # noqa: E402


def parser(description):
	p = argparse.ArgumentParser(description=description)
	p.add_argument("--build", action="append", default=[],
		help="build alias (%s) or 'all'; repeatable" % ", ".join(BUILDS))
	p.add_argument("--bundle", action="append", default=[],
		help="archive name from the manifest; repeatable (default: the curated set)")
	p.add_argument("--all", action="store_true", help="every archive in the build")
	p.add_argument("--out", type=Path, default=Path("out"), help="output directory")
	p.add_argument("--jobs", type=int, help="parallel downloads / workers (default: based on the CPU count)")
	return p


def builds(args):
	names = args.build or ["all"]
	if "all" in names:
		names = list(BUILDS)
	return [Build(n) for n in names]


def bundles(args, build):
	if args.bundle:
		return [b for b in args.bundle if b in build.bundles]
	if args.all:
		return build.bundles
	return build.curated
