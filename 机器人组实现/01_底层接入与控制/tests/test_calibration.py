"""Check personal endpoint calibration from repeatable gesture samples."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from elf_dexhand.calibration import STAGES, derive_mapping
from elf_dexhand.calibration_app import _write_mapping
from elf_dexhand.vision_mapping import MappingProfile


def sample_sets() -> dict[str, list[dict[str, float]]]:
    """Build five stable synthetic gestures with an informative thumb IP."""
    samples: dict[str, list[dict[str, float]]] = {}
    for stage in STAGES:
        rows: list[dict[str, float]] = []
        for index in range(30):
            jitter = (index % 5 - 2) * 0.2
            row = {
                "tip_index_ratio": 0.9 + jitter / 100,
                "palm_plane_angle": 18.0 + jitter,
                "palm_abduction_angle": 55.0 + jitter,
                "mcp_angle": 160.0 + jitter,
                "ip_angle": 170.0 + jitter,
                "wrist_tip_angle": 130.0 + jitter,
                "index": 170.0 + jitter,
                "middle": 155.0 + jitter,
                "ring": 160.0 + jitter,
                "pinky": 150.0 + jitter,
            }
            if stage == "fist":
                row.update(index=80.0 + jitter, middle=75.0 + jitter)
                row.update(ring=70.0 + jitter, pinky=65.0 + jitter)
            if stage == "thumb_tucked":
                row["tip_index_ratio"] = 0.3 + jitter / 100
            if stage == "thumb_bent":
                row["ip_angle"] = 85.0 + jitter
            rows.append(row)
        samples[stage] = rows
    return samples


class CalibrationTests(unittest.TestCase):
    """Verify selection, file validation, and weak-gesture rejection."""

    def test_derives_personal_thumb_metrics_and_four_finger_endpoints(self) -> None:
        """The strongest demonstrated thumb metrics become the saved schema."""
        mapping = derive_mapping(sample_sets(), MappingProfile.default())
        values = mapping["camera_mapping"]
        self.assertEqual(values["thumb_spread"]["metric"], "tip_index_ratio")
        self.assertEqual(values["thumb_bend"]["metric"], "ip_angle")
        self.assertLess(values["middle"]["open_angle"], 160)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mapping.yaml"
            _write_mapping(path, mapping, force=False)
            self.assertEqual(
                MappingProfile.from_file(path).thumb_bend_metric, "ip_angle"
            )
            with self.assertRaises(FileExistsError):
                _write_mapping(path, mapping, force=False)

    def test_weak_thumb_demonstration_raises(self) -> None:
        """Nearly identical thumb poses never produce guessed endpoints."""
        samples = sample_sets()
        samples["thumb_tucked"] = samples["thumb_wide"]
        with self.assertRaisesRegex(ValueError, "cannot calibrate thumb"):
            derive_mapping(samples, MappingProfile.default())

    def test_reversed_fist_is_rejected(self) -> None:
        """A mislabeled finger gesture cannot invert physical movement."""
        samples = sample_sets()
        for row in samples["open"]:
            row["index"] = 100.0
        for row in samples["fist"]:
            row["index"] = 140.0
        with self.assertRaisesRegex(ValueError, "fist angle must be smaller"):
            derive_mapping(samples, MappingProfile.default())

    def test_normal_open_hand_must_not_map_as_thumb_tucked(self) -> None:
        """Reject side-swing endpoints inconsistent with the open-hand pose."""
        samples = sample_sets()
        for row in samples["open"]:
            row["tip_index_ratio"] = 0.35
            row["palm_plane_angle"] = 3.0
            row["palm_abduction_angle"] = 15.0
        for row in samples["thumb_tucked"]:
            row["palm_plane_angle"] = 0.0
            row["palm_abduction_angle"] = 10.0
        with self.assertRaisesRegex(ValueError, "normal open hand maps"):
            derive_mapping(samples, MappingProfile.default())

    def test_open_hand_may_be_wider_than_side_swing_demo(self) -> None:
        """An open gesture beyond the motor endpoint safely clamps to open."""
        samples = sample_sets()
        for row in samples["open"]:
            row["tip_index_ratio"] = 1.1
        mapping = derive_mapping(samples, MappingProfile.default())
        self.assertEqual(
            mapping["camera_mapping"]["thumb_spread"]["metric"],
            "tip_index_ratio",
        )

    def test_selects_in_plane_abduction_when_it_is_clearer(self) -> None:
        """A clear sideways thumb motion can select the palm angle feature."""
        samples = sample_sets()
        for row in samples["thumb_wide"]:
            row["palm_abduction_angle"] = 80.0
        for row in samples["thumb_tucked"]:
            row["tip_index_ratio"] = 0.9
            row["palm_abduction_angle"] = 25.0
        mapping = derive_mapping(samples, MappingProfile.default())
        self.assertEqual(
            mapping["camera_mapping"]["thumb_spread"]["metric"],
            "palm_abduction_angle",
        )


if __name__ == "__main__":
    unittest.main()
