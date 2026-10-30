"""
bin/ scripts on real archives: dumpxdt.py finds the XDT tables by name, so
it works on every build, whatever path ID they have there.
"""

import json

import pytest

from tests.unit.support import run_script

pytestmark = [pytest.mark.integration, pytest.mark.bundles("TableData.resourceFile")]


def test_dumpxdt(build, bundle, unpacked, expected, tmp_path):
	[path] = build.inner_files(bundle)
	run_script("dumpxdt.py", path, tmp_path / "xdt.json")
	xdt = json.loads((tmp_path / "xdt.json").read_text())
	expected.check("dumpxdt/" + bundle, {name: len(table) for name, table in xdt.items() if isinstance(table, dict)})
