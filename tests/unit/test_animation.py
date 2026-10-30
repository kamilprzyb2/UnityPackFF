"""
Animation: unitypack/engine/animation.py (AnimationClip, AnimationCurve,
unpack_quaternions, decompress_rotation_curve) on curves built in memory, the
way ObjectInfo.read() returns them.

Rotations are mostly stored compressed: each quaternion packed into 32 bits,
key times as steps of 0.01 s, and one slope per key. Positions, scales and
material properties are plain keyframe lists.
"""

import math

import pytest

import unitypack.engine
from unitypack.engine.animation import (
	AnimationClip, AnimationCurve, CurveInfinity, Keyframe, decompress_rotation_curve, unpack_quaternions
)
from unitypack.object import FFOrderedDict


def fdict(**kw):
	d = FFOrderedDict()
	d.update(kw)
	return d


def vec(*xs):
	return fdict(**dict(zip("xyzw", xs)))


def packed(items, bits, start=None, range_=None):
	"A PackedBitVector, least significant bit first."
	n = sum(v << (bits * i) for i, v in enumerate(items))
	d = fdict(m_NumItems=len(items), m_BitSize=bits, m_Data=n.to_bytes((len(items) * bits + 7) // 8, "little"))
	if start is not None:
		d["m_Start"], d["m_Range"] = start, range_
	return d


# PackedQuatVector, written out from the format: per quaternion one 32-bit
# word, least significant bit first: the index of the largest component
# (2 bits) and its sign (1 bit), then the other three in order, the one
# right after the largest in 9 bits and the others in 10, each mapped from
# [-1, 1] to [0, 2^bits - 1].

def pack_quaternion(q):
	largest = max(range(4), key=lambda i: abs(q[i]))
	word = largest | (4 if q[largest] < 0 else 0)
	shift = 3
	for i in range(4):
		if i == largest:
			continue
		bits = 9 if i == (largest + 1) % 4 else 10
		word |= round((q[i] + 1) / 2 * ((1 << bits) - 1)) << shift
		shift += bits
	return word


def packed_quats(qs):
	return fdict(m_NumItems=len(qs), m_Data=b"".join(pack_quaternion(q).to_bytes(4, "little") for q in qs))


def normalized(q):
	n = math.sqrt(sum(c * c for c in q))
	return tuple(c / n for c in q)


QUATS = [normalized(q) for q in [
	(0.9, 0.1, -0.2, 0.3),  # largest x
	(0.1, -0.9, 0.2, 0.1),  # largest y, negative
	(0.2, 0.3, 0.8, -0.1),  # largest z
	(-0.1, 0.2, 0.3, -0.9),  # largest w, negative
	(0, 0, 0, 1),
]]


def test_unpack_quaternions():
	out = unpack_quaternions(packed_quats(QUATS))
	assert len(out) == len(QUATS)
	for got, want in zip(out, QUATS):
		# 9 bits over [-1, 1] are 2/511 apart
		assert got == pytest.approx(want, abs=1 / 511)
		assert sum(c * c for c in got) == pytest.approx(1)


def test_unpack_quaternion_bit_layout():
	# largest component w (3), positive; then x in 9 bits, y and z in 10
	word = 3 | (383 << 3) | (767 << 12) | (256 << 22)
	x, y, z = 383 / 511 * 2 - 1, 767 / 1023 * 2 - 1, 256 / 1023 * 2 - 1
	w = math.sqrt(1 - x * x - y * y - z * z)
	[q] = unpack_quaternions(fdict(m_NumItems=1, m_Data=word.to_bytes(4, "little")))
	assert q == pytest.approx((x, y, z, w))


def test_unpack_quaternion_negative_largest():
	# largest y (1), negative: x, z (9 bits) and w follow
	word = 1 | 4 | (511 << 3) | (255 << 13) | (511 << 22)
	[q] = unpack_quaternions(fdict(m_NumItems=1, m_Data=word.to_bytes(4, "little")))
	assert q[1] < 0
	assert q[0] == pytest.approx(511 / 1023 * 2 - 1)
	assert q[2] == pytest.approx(255 / 511 * 2 - 1)


def compressed_curve(times, quats, slopes, path="Bip01"):
	return fdict(
		m_Path=path,
		m_Times=packed(times, 8),
		m_Values=packed_quats(quats),
		# 8 bits over [-2, 2]
		m_Slopes=packed([round((s + 2) / 4 * 255) for s in slopes], 8, -2.0, 4.0),
		m_PreInfinity=2,
		m_PostInfinity=2,
	)


def test_compressed_curve_times_and_slopes():
	# times are steps of 0.01 s; one (x, y, z, w) slope per key, both in and out
	slopes = [0, 0, 0, 0, 2, -2, 0, 0]
	curve = decompress_rotation_curve(compressed_curve([0, 80], QUATS[:2], slopes))
	assert [k.time for k in curve.keys] == [0, 0.8]
	assert curve.keys[1].in_slope == curve.keys[1].out_slope == pytest.approx((2, -2, 0, 0), abs=0.01)
	assert curve.keys[0].value == pytest.approx(QUATS[0], abs=1 / 511)

	curve = decompress_rotation_curve(compressed_curve([10, 80, 56], QUATS[:3], [0] * 12))
	assert [k.time for k in curve.keys] == [0.1, 0.9, 1.46]


def test_compressed_curve_mismatch():
	with pytest.raises(ValueError, match="2 times, 2 values, 4 slopes"):
		decompress_rotation_curve(compressed_curve([0, 80], QUATS[:2], [0] * 4))


# AnimationCurve.evaluate: Unity's cubic Hermite between neighbouring keys,
# with the first key's out slope and the second key's in slope.

def curve(*keys, pre=CurveInfinity.Clamp, post=CurveInfinity.Clamp):
	return AnimationCurve([Keyframe(*k) for k in keys], pre, post)


def test_evaluate_at_keys():
	c = curve((0, 1.0, 0, 0), (1, 3.0, 0, 0), (3, -1.0, 0, 0))
	assert [c.evaluate(t) for t in (0, 1, 3)] == [1, 3, -1]


def test_evaluate_linear():
	# slopes equal to the line's slope: the spline is that line
	c = curve((0, 0.0, 2, 2), (2, 4.0, 2, 2))
	assert [c.evaluate(t) for t in (0.5, 1, 1.5)] == pytest.approx([1, 2, 3])


def test_evaluate_flat_slopes():
	# zero slopes ease in and out: 3s^2 - 2s^3 of the way
	c = curve((0, 0.0, 0, 0), (1, 1.0, 0, 0))
	assert c.evaluate(0.5) == pytest.approx(0.5)
	assert c.evaluate(0.25) == pytest.approx(3 * 0.25**2 - 2 * 0.25**3)


def test_evaluate_uses_out_then_in_slope():
	c = curve((0, 0.0, 99, 1), (1, 1.0, 1, 99))
	assert c.evaluate(0.5) == pytest.approx(0.5)


def test_evaluate_stepped():
	# an infinite slope holds the value until the next key
	c = curve((0, 1.0, 0, math.inf), (1, 2.0, 0, 0))
	assert c.evaluate(0.99) == 1
	assert c.evaluate(1) == 2


def test_evaluate_vectors():
	c = curve((0, (0.0, 1.0, 2.0), (1, 0, 0), (1, 0, 0)), (1, (1.0, 1.0, 2.0), (1, 0, 0), (1, 0, 0)))
	assert c.evaluate(0.5) == pytest.approx((0.5, 1, 2))


def test_evaluate_repeated_time():
	c = curve((0, 0.0, 0, 0), (1, 1.0, 0, 0), (1, 5.0, 0, 0), (2, 5.0, 0, 0))
	assert c.evaluate(1) == 5
	assert c.evaluate(0.5) == pytest.approx(0.5)


@pytest.mark.parametrize("mode,before,after", [
	(CurveInfinity.Clamp, 1, 3),
	(CurveInfinity.Repeat, 2, 2),  # t = -1 and t = 5 both wrap to t = 1
	(CurveInfinity.PingPong, 2, 2),  # and mirror to t = 1
])
def test_evaluate_outside_keys(mode, before, after):
	c = curve((0, 1.0, 1, 1), (2, 3.0, 1, 1), pre=mode, post=mode)
	assert c.evaluate(-1) == pytest.approx(before)
	assert c.evaluate(5) == pytest.approx(after)


def test_evaluate_repeat_and_pingpong_differ():
	c = curve((0, 0.0, 1, 1), (2, 2.0, 1, 1), post=CurveInfinity.Repeat)
	assert c.evaluate(2.5) == pytest.approx(0.5)
	c.post_infinity = CurveInfinity.PingPong
	assert c.evaluate(2.5) == pytest.approx(1.5)


def test_evaluate_single_key_and_empty():
	assert curve((0.5, 7.0, 0, 0)).evaluate(3) == 7
	with pytest.raises(ValueError):
		curve().evaluate(0)


def key(time, value, slope):
	return fdict(time=time, value=value, inSlope=slope, outSlope=slope)


def serialized_curve(keys, pre=2, post=2):
	return fdict(m_Curve=keys, m_PreInfinity=pre, m_PostInfinity=post)


def test_curve_from_dict():
	c = AnimationCurve.from_dict(serialized_curve([key(0.0, vec(1, 2, 3), vec(0, 0, 0)), key(1.0, vec(4, 5, 6), vec(1, 1, 1))], 1, 0))
	assert c.keys[1] == Keyframe(1.0, (4, 5, 6), (1, 1, 1), (1, 1, 1))
	assert (c.pre_infinity, c.post_infinity) == (CurveInfinity.Repeat, CurveInfinity.PingPong)
	assert (c.start_time, c.end_time) == (0, 1)


def clip(**kw):
	d = fdict(
		m_Name="walk",
		m_UseCompression=True,
		m_RotationCurves=[],
		m_CompressedRotationCurves=[compressed_curve([0, 150], QUATS[:2], [0] * 8, "Bip01/Bip01 Pelvis")],
		m_PositionCurves=[fdict(curve=serialized_curve([key(0.0, vec(0, 0, 0), vec(0, 0, 0)), key(1.6, vec(0, 1, 0), vec(0, 0, 0))]), path="Bip01")],
		m_ScaleCurves=[],
		m_FloatCurves=[fdict(curve=serialized_curve([key(0.0, 0.0, 0.0), key(0.5, 1.0, 0.0)], 1, 1),
			attribute="_MainTex.offset.x", path="Plane", classID=21, script=None)],
		m_SampleRate=30.0,
		m_Events=[],
	)
	d.update(kw)
	return AnimationClip(d)


def test_clip_curves():
	c = clip()
	assert c.compressed
	assert list(c.rotation_curves) == ["Bip01/Bip01 Pelvis"]
	assert c.rotation_curves["Bip01/Bip01 Pelvis"].keys[1].time == 1.5
	assert c.position_curves["Bip01"].evaluate(1.6) == (0, 1, 0)
	assert c.scale_curves == {}
	[f] = c.float_curves
	assert (f.path, f.attribute, f.class_id) == ("Plane", "_MainTex.offset.x", 21)
	assert f.curve.post_infinity == CurveInfinity.Repeat
	assert c.length == 1.6


def test_clip_uncompressed_rotations():
	# newer files name the flag m_Compressed and store rotations as plain keys
	d = clip()._obj
	del d["m_UseCompression"]
	d["m_Compressed"] = False
	d["m_CompressedRotationCurves"] = []
	d["m_RotationCurves"] = [fdict(curve=serialized_curve([key(0.0, vec(0, 0, 0, 1), vec(0, 0, 0, 0))]), path="Bip01")]
	c = AnimationClip(d)
	assert not c.compressed
	assert c.rotation_curves["Bip01"].evaluate(0) == (0, 0, 0, 1)


def test_clip_without_curves():
	c = clip(m_CompressedRotationCurves=[], m_PositionCurves=[], m_FloatCurves=[])
	assert c.length == 0


@pytest.mark.parametrize("name", ["AnimationCurve", "Keyframe", "FloatCurve"])
def test_curve_classes_dont_wrap_serialized_types(name):
	# load_object() wraps every serialized value whose type has a class of the
	# same name in unitypack.engine; these names are type tree types too
	assert not hasattr(unitypack.engine, name)
