"""
Every serialized file of an archive: the independent parser reads its
metadata to the last byte, and what UnityPackFF reads from it (object counts,
classes, a digest of every object) matches the recorded values.
"""

import pytest

from tests.tools.survey import strip_timing, survey_archive

pytestmark = pytest.mark.integration

# Known library bugs, by the error they cause; strict, so fixing one flags the marker for removal
KNOWN = {
	"Duplicate asset object": "format 7: 64-bit object IDs are truncated to 32 bits, and in a few files two of them collide",
}


def test_survey(build, bundle, unpacked, expected, request):
	rec = strip_timing(survey_archive(build, bundle, read_objects=True))
	expected.check("survey/" + bundle, rec)

	files = {n: f for n, f in rec["files"].items() if f["kind"] == "serialized"}
	for f in files.values():
		reason = next((r for k, r in KNOWN.items() if k in f["unitypackff"]["open"]), None)
		if reason:
			request.applymarker(pytest.mark.xfail(strict=True, reason=reason))

	for name, f in files.items():
		assert f["layout_ok"], name
		assert f["unitypackff"]["open"] == "ok", name
