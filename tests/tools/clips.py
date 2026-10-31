#!/usr/bin/env python3
"""
Every AnimationClip of an archive, read with unitypack's AnimationClip and
sampled at the clip's own rate, and every Animation component with what
its clips' curve paths bind to.

  python3 -m tests.tools.clips --build 104 --bundle FutureNano.resourceFile --out out/

Writes out/clips/<build>/<archive>/<file>/<path ID>_<name>.json per clip
(its keys per curve, and every curve sampled at m_SampleRate from 0 to the
clip's length) and out/clips/<build>.json with the statistics the
integration test checks.
"""

import hashlib
import json
import re

from tests.tools._common import builds, bundles, parser

from tests import serialized

CLIP = 74  # class IDs
ANIMATION = 111


def clip_curves(clip):
	"[(kind, name, AnimationCurve)] in a fixed order"
	ret = []
	for kind, curves in (("rotation", clip.rotation_curves), ("position", clip.position_curves), ("scale", clip.scale_curves)):
		ret += [(kind, path, c) for path, c in curves.items()]
	ret += [("float", "%s:%s" % (f.path, f.attribute), f.curve) for f in clip.float_curves]
	return ret


def sample(clip, curves):
	"Every curve at m_SampleRate frames per second, from 0 to the clip's length (none for a curve without keys)"
	length = clip.length
	frames = int(round(length * clip.sample_rate)) + 1
	times = [min(i / clip.sample_rate, length) for i in range(frames)]
	return [[c.evaluate(t) for t in times] if c.keys else [] for _, _, c in curves]


def clip_record(clip):
	curves = clip_curves(clip)
	samples = sample(clip, curves)
	kinds = {}
	for kind, _, c in curves:
		k = kinds.setdefault(kind, {"curves": 0, "keys": 0})
		k["curves"] += 1
		k["keys"] += len(c.keys)
	return {
		"name": clip.name,
		"compressed": clip.compressed,
		"sample_rate": clip.sample_rate,
		"length": clip.length,
		"curves": kinds,
		"events": [e["functionName"] for e in clip.events],
		"samples_sha256": hashlib.sha256(json.dumps(samples).encode()).hexdigest()[:16],
	}, curves, samples


def clip_stats(build, bundle, out=None):
	"""
	{file: {path ID: stats}} for every clip in the archive. With out, also
	writes each clip's keys and samples under out/<file>/.
	"""
	from unitypack.asset import Asset

	ret = {}
	for path in build.inner_files(bundle):
		if not serialized.looks_serialized(path.read_bytes()):
			continue
		with open(path, "rb") as f:
			clips = {}
			for path_id, obj in sorted(Asset.from_file(f).objects.items()):
				if obj.type_id != CLIP:
					continue
				clip = obj.read()
				rec, curves, samples = clip_record(clip)
				clips[str(path_id)] = rec
				if out:
					d = out / path.name
					d.mkdir(parents=True, exist_ok=True)
					stem = "%d_%s" % (path_id, re.sub(r"[^\w.-]", "_", clip.name))
					(d / (stem + ".json")).write_text(json.dumps({
						**rec,
						"curves": [
							{"kind": kind, "name": name, "keys": [k._asdict() for k in c.keys], "samples": s}
							for (kind, name, c), s in zip(curves, samples)
						],
					}))
			if clips:
				ret[path.name] = clips
	return ret


def binding_stats(build, bundle, env_dir):
	"""
	{file: stats} for the Animation components of the archive's files:
	how many, how they play their clips (default clip listed or none,
	wrap modes, play automatically, enabled), and over their clips (those
	in files env_dir doesn't have are counted apart) how many curve paths
	bind to a Transform, name none, or name more than one. Pointers resolve
	against env_dir, as in tests.tools.gltf.
	"""
	from unitypack.environment import UnityEnvironment

	env = UnityEnvironment(base_path=str(env_dir))
	ret = {}
	for path in build.inner_files(bundle):
		if not serialized.looks_serialized(path.read_bytes()):
			continue
		asset = env.get_asset_by_filename(path.name)
		stats = {"animations": 0, "clips": 0, "clips_elsewhere": 0, "default": {"listed": 0, "unlisted": 0, "none": 0},
			"wrap_modes": {}, "play_automatically": 0, "enabled": 0, "paths": 0, "unresolved": 0, "ambiguous": 0}
		for obj in asset.objects.values():
			if obj.type_id != ANIMATION or obj.uses_structs_dat:
				continue
			animation = obj.read()
			stats["animations"] += 1
			stats["play_automatically"] += animation.play_automatically
			stats["enabled"] += animation.enabled
			wrap = str(animation.wrap_mode)
			stats["wrap_modes"][wrap] = stats["wrap_modes"].get(wrap, 0) + 1
			default = animation.animation
			listed = [(p.file_id, p.path_id) for p in animation.animations if p]
			kind = "none" if not default else "listed" if (default.file_id, default.path_id) in listed else "unlisted"
			stats["default"][kind] += 1
			for ptr in animation.animations:
				try:
					clip = ptr.resolve()
				except KeyError:
					stats["clips_elsewhere"] += 1
					continue
				binding = animation.bind(clip)
				stats["clips"] += 1
				stats["paths"] += len(binding.targets)
				stats["unresolved"] += len(binding.unresolved)
				stats["ambiguous"] += len(binding.ambiguous)
		if stats["animations"]:
			ret[path.name] = stats
	return ret


def main():
	from tests.tools.gltf import CuratedEnvironment

	args = parser(__doc__).parse_args()
	for build in builds(args):
		results = {}
		env = CuratedEnvironment(build)
		try:
			for b in bundles(args, build):
				r = clip_stats(build, b, args.out / "clips" / build.name / b)
				clips = [c for f in r.values() for c in f.values()]
				env.add_references(b)
				bindings = binding_stats(build, b, env.dir)
				results[b] = {"clips": r, "bindings": bindings}
				print("%s %s: %d clips, %d compressed; %d Animation components, %d unresolved and %d ambiguous paths" % (
					build.alias, b, len(clips), sum(c["compressed"] for c in clips), sum(s["animations"] for s in bindings.values()),
					sum(s["unresolved"] for s in bindings.values()), sum(s["ambiguous"] for s in bindings.values())))
		finally:
			env.close()
		(args.out / "clips").mkdir(parents=True, exist_ok=True)
		(args.out / "clips" / ("%s.json" % build.name)).write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
	main()
