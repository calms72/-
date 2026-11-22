"""Verify hand geometry and bounded motion without a camera or motor."""

from __future__ import annotations

import unittest
from dataclasses import dataclass

from elf_dexhand.protocol import Joint
from elf_dexhand.vision_mapping import (
    MappingProfile,
    MotionFilter,
    ThumbTargetStabilizer,
    estimate_pose,
    measure_hand,
    measure_thumb_candidates,
)


@dataclass(frozen=True)
class Point:
    """One synthetic three-dimensional hand landmark."""

    x: float
    y: float
    z: float = 0.0


def straight_hand() -> list[Point]:
    """Create 21 valid landmarks with straight PIP joints."""
    points = [Point(0, 0) for _ in range(21)]
    points[0] = Point(2, -2)
    points[1], points[2], points[3], points[4] = (
        Point(-1, 0),
        Point(-2, 0),
        Point(-3, 0),
        Point(0, 2),
    )
    for mcp, pip, dip, x in (
        (5, 6, 7, 0),
        (9, 10, 11, 1),
        (13, 14, 15, 2),
        (17, 18, 19, 4),
    ):
        points[mcp] = Point(x, 0)
        points[pip] = Point(x, 1)
        points[dip] = Point(x, 2)
    return points


class VisionMappingTests(unittest.TestCase):
    """Check mapping endpoints and motor-rate limiting."""

    def test_index_bend_increases_motor_position(self) -> None:
        """A 90-degree PIP bend is more closed than a straight finger."""
        profile = MappingProfile.default()
        points = straight_hand()
        open_pose = estimate_pose(points, profile)
        points[7] = Point(1, 1)
        bent_pose = estimate_pose(points, profile)
        self.assertEqual(open_pose[Joint.INDEX], 0)
        self.assertGreater(bent_pose[Joint.INDEX], 2500)
        self.assertEqual(open_pose[Joint.MIDDLE], 0)

    def test_measurements_support_camera_calibration(self) -> None:
        """Measured angles expose the values needed to tune YAML endpoints."""
        points = straight_hand()
        self.assertAlmostEqual(measure_hand(points)[Joint.INDEX], 180.0)
        points[7] = Point(1, 1)
        self.assertAlmostEqual(measure_hand(points)[Joint.INDEX], 90.0)

    def test_thumb_abduction_uses_palm_plane(self) -> None:
        """The thumb's in-plane angle changes with a sideways movement."""
        points = straight_hand()
        points[4] = Point(-2, 2)
        tucked = measure_thumb_candidates(points)["palm_abduction_angle"]
        points[4] = Point(-3, 1)
        wide = measure_thumb_candidates(points)["palm_abduction_angle"]
        self.assertGreater(wide - tucked, 20)

    def test_bad_landmarks_are_reported(self) -> None:
        """Incomplete and collapsed detections cannot create a motor pose."""
        profile = MappingProfile.default()
        with self.assertRaisesRegex(ValueError, "21 landmarks"):
            estimate_pose(straight_hand()[:20], profile)
        points = straight_hand()
        points[6] = points[5]
        with self.assertRaisesRegex(ValueError, "collapsed"):
            estimate_pose(points, profile)

    def test_motion_filter_limits_each_command(self) -> None:
        """No one camera update can request a large sudden motor jump."""
        initial = {joint: 0 for joint in Joint}
        motion = MotionFilter(initial, alpha=1.0, max_step=250)
        target = {joint: 0 for joint in Joint}
        target[Joint.INDEX] = 3200
        self.assertEqual(motion.update(target)[Joint.INDEX], 250)
        target[Joint.INDEX] = 5000
        with self.assertRaises(ValueError):
            motion.update(target)
        target[Joint.INDEX] = 3200
        self.assertEqual(motion.update(target)[Joint.INDEX], 500)

    def test_thumb_filter_rejects_single_frame_spike(self) -> None:
        """An isolated thumb landmark jump cannot create a motor twitch."""
        initial = {joint: 0 for joint in Joint}
        filter_ = ThumbTargetStabilizer(initial)
        target = dict(initial)
        target[Joint.THUMB_SPREAD] = 1200
        self.assertEqual(filter_.update(target)[Joint.THUMB_SPREAD], 0)
        self.assertGreater(filter_.update(target)[Joint.THUMB_SPREAD], 900)
        target[Joint.INDEX] = 500
        self.assertEqual(filter_.update(target)[Joint.INDEX], 500)


if __name__ == "__main__":
    unittest.main()
