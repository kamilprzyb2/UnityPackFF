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


def test_dump_script_hnpc(build, bundle, unpacked, expected, tmp_path):
	"all_hnpc.asset: the human NPCs' looks, as player parts by file name"
	[path] = build.inner_files(bundle)
	run_script("dump_script.py", path, "all_hnpc.asset", "-o", tmp_path / "hnpc.json")
	rows = json.loads((tmp_path / "hnpc.json").read_text())["TableElement"]
	if build.alias == "104":
		assert len(rows) == 138
		assert (rows[48]["strHairMesh"], rows[48]["iType"]) == ("f_head_014_type01.nif", 47)
	expected.check("dump_script/" + bundle, {"rows": len(rows), "fields": sorted(rows[0])})


@pytest.mark.bundles()
def test_dump_script_palettes(build, tmp_path):
	"""
	male.bsd, the skin and hair colours: in TableData's container from 728
	on, in main.unity3d's sharedassets0.assets (by name only) before
	"""
	from tests.bundles import MAIN_FILE

	tabledata = build.inner_files("TableData.resourceFile")[0]
	if "male.bsd.asset" in run_script("dump_script.py", tabledata):
		path = tabledata
	else:
		path = next(p for p in build.inner_files(MAIN_FILE) if p.name == "sharedassets0.assets")
	bsd = json.loads(run_script("dump_script.py", path, "male.bsd"))
	for colors in (bsd["cSkinColor"]["skinColor"], bsd["cHairColor"]["hairColor"]):
		assert len(colors) == 20
		assert all(len(c) == 4 and all(0 <= x <= 1 for x in c) for c in colors)
	assert (path == tabledata) == (build.alias in ("728", "1013"))
