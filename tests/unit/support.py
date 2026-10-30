"Small helpers for the unit tests: open and save made-up files with unitypack."

import io
import os
import subprocess
import sys
from pathlib import Path

from unitypack.asset import Asset

ROOT = Path(__file__).resolve().parents[2]


def open_asset(data, name="CustomAssetBundle-test"):
	f = io.BytesIO(data)
	f.name = name
	return Asset.from_file(f)


def save(asset):
	out = io.BytesIO()
	asset.save(out)
	return out.getvalue()


def run_script(script, *args):
	"Run a bin/ script as a user would, with this checkout's unitypack; returns its output."
	env = dict(os.environ, PYTHONPATH=str(ROOT))
	return subprocess.run([sys.executable, str(ROOT / "bin" / script), *map(str, args)],
		capture_output=True, text=True, check=True, env=env).stdout
