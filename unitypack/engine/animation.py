import math
import struct
from bisect import bisect_right
from collections import namedtuple
from enum import IntEnum
from functools import cached_property
from ..utils import unpack_floats, unpack_ints
from .component import Behaviour, Component
from .object import Object, field


class Animation(Behaviour):
	animate_physics = field("m_AnimatePhysics", bool)
	culling_type = field("m_CullingType")
	play_automatically = field("m_PlayAutomatically", bool)
	wrap_mode = field("m_WrapMode")
	animation = field("m_Animation")
	animations = field("m_Animations")


class Motion(Object):
	pass


# AnimationCurve, Keyframe and FloatCurve share their names with types in
# the type trees, so they stay out of unitypack.engine: load_object() would
# wrap every serialized curve in them.

class CurveInfinity(IntEnum):
	"What a curve does before its first key (m_PreInfinity) and after its last (m_PostInfinity)"
	PingPong = 0
	Repeat = 1
	Clamp = 2


class Keyframe(namedtuple("Keyframe", "time value in_slope out_slope")):
	"""
	One key of an AnimationCurve. value and the slopes are floats, or tuples
	(x, y, z) for positions and scales and (x, y, z, w) for rotations. Slopes
	are per second; an infinite slope holds the value until the next key.
	"""


def _value(v):
	if isinstance(v, (int, float)):
		return float(v)
	return tuple(float(v[k]) for k in "xyzw" if k in v)


def _hermite(k0, k1, t):
	dt = k1.time - k0.time
	if dt <= 0:
		return k1.value
	s = (t - k0.time) / dt
	h00 = 2 * s**3 - 3 * s**2 + 1
	h10 = s**3 - 2 * s**2 + s
	h01 = -2 * s**3 + 3 * s**2
	h11 = s**3 - s**2

	def one(v0, out0, v1, in1):
		if not (math.isfinite(out0) and math.isfinite(in1)):
			return v0
		return h00 * v0 + h10 * dt * out0 + h01 * v1 + h11 * dt * in1

	if isinstance(k0.value, tuple):
		return tuple(map(one, k0.value, k0.out_slope, k1.value, k1.in_slope))
	return one(k0.value, k0.out_slope, k1.value, k1.in_slope)


class AnimationCurve:
	"""
	Keys in time order, evaluated the way Unity does: a cubic Hermite spline
	between neighbouring keys, using the first key's out slope and the second
	key's in slope.
	"""

	def __init__(self, keys, pre_infinity=CurveInfinity.Clamp, post_infinity=CurveInfinity.Clamp):
		self.keys = list(keys)
		self.pre_infinity = CurveInfinity(pre_infinity)
		self.post_infinity = CurveInfinity(post_infinity)

	def __repr__(self):
		return "<AnimationCurve %d keys>" % (len(self.keys))

	@classmethod
	def from_dict(cls, d):
		"A curve as serialized: {m_Curve: [{time, value, inSlope, outSlope}], m_PreInfinity, m_PostInfinity}"
		keys = [Keyframe(k["time"], _value(k["value"]), _value(k["inSlope"]), _value(k["outSlope"])) for k in d["m_Curve"]]
		return cls(keys, d["m_PreInfinity"], d["m_PostInfinity"])

	@property
	def start_time(self):
		return self.keys[0].time if self.keys else 0.0

	@property
	def end_time(self):
		return self.keys[-1].time if self.keys else 0.0

	def _wrap(self, t, mode):
		start, length = self.start_time, self.end_time - self.start_time
		if mode == CurveInfinity.Clamp or length <= 0:
			return min(max(t, start), self.end_time)
		if mode == CurveInfinity.Repeat:
			return start + (t - start) % length
		u = (t - start) % (2 * length)
		return start + (2 * length - u if u > length else u)

	def evaluate(self, t):
		"The curve's value at time t (seconds)"
		if not self.keys:
			raise ValueError("%r has no keys" % (self))
		if t < self.start_time:
			t = self._wrap(t, self.pre_infinity)
		elif t > self.end_time:
			t = self._wrap(t, self.post_infinity)

		i = bisect_right([k.time for k in self.keys], t)
		if i == 0:
			return self.keys[0].value
		if i == len(self.keys):
			return self.keys[-1].value
		return _hermite(self.keys[i - 1], self.keys[i], t)


def unpack_quaternions(pqv):
	"""
	The (x, y, z, w) quaternions of a PackedQuatVector (m_NumItems, m_Data).
	Each takes one little-endian 32-bit word, least significant bit first:
	2 bits for the index of the largest component, which is left out, and 1
	for its sign; then the other three in order, the one right after the
	left-out index in 9 bits and the others in 10, each scaled from
	[0, 2^bits - 1] to [-1, 1]. The left-out component makes the length 1.
	"""
	n = pqv["m_NumItems"]
	words = struct.unpack("<%dI" % (n), bytes(pqv["m_Data"])[:n * 4])

	ret = []
	for word in words:
		largest = word & 3
		negative = word & 4
		word >>= 3
		q = [0.0] * 4
		for i in range(4):
			if i == largest:
				continue
			bits = 9 if i == (largest + 1) % 4 else 10
			q[i] = (word & ((1 << bits) - 1)) / ((1 << bits) - 1) * 2 - 1
			word >>= bits
		w = math.sqrt(max(0.0, 1 - sum(c * c for c in q)))
		q[largest] = -w if negative else w
		ret.append(tuple(q))
	return ret


def decompress_rotation_curve(c):
	"""
	An AnimationCurve of quaternions from a CompressedAnimationCurve:
	m_Times holds the key times as steps of 0.01 s from the previous key,
	m_Values the rotations, m_Slopes one (x, y, z, w) slope per key that is
	both its in and its out slope.
	"""
	times = []
	t = 0
	for step in unpack_ints(c["m_Times"]):
		t += step
		times.append(t / 100)
	values = unpack_quaternions(c["m_Values"])
	slopes = unpack_floats(c["m_Slopes"])
	if not (len(times) == len(values) and len(slopes) == 4 * len(values)):
		raise ValueError("compressed curve %r: %d times, %d values, %d slopes" % (c["m_Path"], len(times), len(values), len(slopes)))

	keys = []
	for i, (t, q) in enumerate(zip(times, values)):
		slope = tuple(slopes[i * 4:i * 4 + 4])
		keys.append(Keyframe(t, q, slope, slope))
	return AnimationCurve(keys, c["m_PreInfinity"], c["m_PostInfinity"])


FloatCurve = namedtuple("FloatCurve", "path attribute class_id script curve")


class AnimationClip(Motion):
	"""
	A clip's curves, keyed by the path of the transform they move (relative
	to the animated object, "" for the object itself). They are decoded on
	first use and kept.
	"""
	sample_rate = field("m_SampleRate")
	events = field("m_Events")

	@property
	def compressed(self):
		"Whether rotations are stored compressed (m_UseCompression; m_Compressed in newer files)"
		return bool(self._obj.get("m_UseCompression", self._obj.get("m_Compressed", False)))

	@cached_property
	def rotation_curves(self):
		"""
		{transform path: AnimationCurve of (x, y, z, w)}, compressed or not.
		evaluate() interpolates each component on its own, so between keys
		the result needs normalizing to be a rotation.
		"""
		ret = {}
		for c in self._obj["m_CompressedRotationCurves"]:
			ret[c["m_Path"]] = decompress_rotation_curve(c)
		for c in self._obj["m_RotationCurves"]:
			ret[c["path"]] = AnimationCurve.from_dict(c["curve"])
		return ret

	@cached_property
	def position_curves(self):
		"{transform path: AnimationCurve of (x, y, z)}"
		return {c["path"]: AnimationCurve.from_dict(c["curve"]) for c in self._obj["m_PositionCurves"]}

	@cached_property
	def scale_curves(self):
		"{transform path: AnimationCurve of (x, y, z)}"
		return {c["path"]: AnimationCurve.from_dict(c["curve"]) for c in self._obj["m_ScaleCurves"]}

	@cached_property
	def float_curves(self):
		"[FloatCurve]: an animated property (such as a material's _Color.r) of a component of class_id on path"
		return [
			FloatCurve(c["path"], c["attribute"], c["classID"], c["script"], AnimationCurve.from_dict(c["curve"]))
			for c in self._obj["m_FloatCurves"]
		]

	def curves(self):
		"Every curve of the clip"
		yield from self.rotation_curves.values()
		yield from self.position_curves.values()
		yield from self.scale_curves.values()
		yield from (c.curve for c in self.float_curves)

	@property
	def length(self):
		"Seconds from 0 to the last key of any curve"
		return max((c.end_time for c in self.curves()), default=0.0)


class RuntimeAnimatorController(Object):
	animation_clips = field("m_AnimationClips")


class AnimatorController(RuntimeAnimatorController):
	controller = field("m_Controller")
	controller_size = field("m_ControllerSize")
	multithreaded_state_machine = field("m_MultiThreadedStateMachine")
	state_machine_behaviours = field("m_StateMachineBehaviours")
	state_machine_behaviour_vector_description = field("m_StateMachineBehaviourVectorDescription")
	TOS = field("m_TOS")


class AnimatorCullingMode(IntEnum):
	AlwaysAnimate = 0
	CullUpdateTransforms = 1
	CullCompletely = 2
	BasedOnRenderers = 1


class AnimatorUpdateMode(IntEnum):
	Normal = 0
	AnimatePhysics = 1
	unscaledTime = 2


class Animator(Behaviour):
	allow_constant_clip_sampling_optimization = field("m_AllowConstantClipSamplingOptimization", bool)
	apply_root_motion = field("m_ApplyRootMotion", bool)
	avatar = field("m_Avatar")
	controller = field("m_Controller")
	culling_mode = field("m_CullingMode", AnimatorCullingMode)
	has_transform_hierarchy = field("m_HasTransformHierarchy", bool)
	linear_velocity_binding = field("m_LinearVelocityBlending", bool)
	update_mode = field("m_UpdateMode", AnimatorUpdateMode)


class ParticleAnimator(Component):
	autodestruct = field("autodestruct", bool)
	damping = field("damping")
	does_animate_color = field("Does Animate Color?", bool)
	force = field("force")
	local_rotation_axis = field("localRotationAxis")
	rnd_force = field("rndForce")
	stop_simulation = field("stopSimulation")
	size_grow = field("sizeGrow")
	world_rotation_axis = field("worldRotationAxis")

	@property
	def color_animation(self):
		ret = []
		i = 0
		while True:
			k = "colorAnimation[%i]" % (i)
			if k in self._obj:
				ret.append(self._obj[k])
			else:
				break
			i += 1

		return ret
