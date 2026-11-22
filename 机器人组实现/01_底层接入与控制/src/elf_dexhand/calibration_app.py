"""Interactively calibrate six hand joints from the user's own gestures."""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import cv2
import mediapipe as mp
import yaml

from .calibration import (
    STAGES,
    derive_mapping,
    validate_four_fingers,
    validate_thumb_bend,
    validate_thumb_spread,
)
from .camera_app import _draw_hand, _make_landmarker, _open_camera
from .protocol import Joint
from .vision_mapping import (
    DEFAULT_CALIBRATION_PATH,
    MappingProfile,
    measure_hand,
    measure_thumb_candidates,
)

_INSTRUCTIONS = {
    "open": ("O", "Straighten all fingers and spread the thumb"),
    "fist": ("F", "Make a full fist, including the thumb"),
    "thumb_wide": ("W", "Straighten fingers; move thumb fully outward"),
    "thumb_tucked": ("T", "Straighten fingers; tuck thumb toward index base"),
    "thumb_bent": ("B", "Straighten four fingers; bend only the thumb"),
}


def _parser() -> argparse.ArgumentParser:
    """Define passive camera calibration options without opening hardware."""
    parser = argparse.ArgumentParser(prog="dexhand-calibrate")
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument(
        "--model", type=Path, default=Path("models/hand_landmarker.task")
    )
    parser.add_argument("--base", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_CALIBRATION_PATH)
    parser.add_argument("--sample-seconds", type=float, default=3.0)
    parser.add_argument("--force", action="store_true")
    return parser


def _sample_row(landmarks: Any) -> dict[str, float]:
    """Measure all candidate thumb features and four finger bend angles."""
    candidates = measure_thumb_candidates(landmarks)
    measured = measure_hand(landmarks)
    for joint in (Joint.INDEX, Joint.MIDDLE, Joint.RING, Joint.PINKY):
        candidates[joint.name.lower()] = measured[joint]
    return candidates


def _median_feature(rows: list[dict[str, float]], feature: str) -> float:
    """Summarize a completed pose sample for readable diagnostics."""
    return statistics.median(row[feature] for row in rows)


def _write_mapping(path: Path, data: dict[str, object], force: bool) -> None:
    """Validate, then atomically save the calibrated YAML file.

    The destination is left untouched if schema validation or writing fails.
    Existing files require force=True so a previous calibration is preserved.
    """
    if path.exists() and not force:
        raise FileExistsError(f"calibration already exists: {path}; use --force")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".yaml",
            prefix=".dexhand-calibration-",
            dir=path.parent,
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            yaml.safe_dump(data, stream, sort_keys=False, allow_unicode=True)
        MappingProfile.from_file(temporary)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def run_calibration(args: argparse.Namespace) -> int:
    """Record five user-demonstrated poses and save a personal mapping.

    Only the computer camera is used. This command never opens SSH or sends
    CAN frames. Camera and model errors propagate; weak or missing gesture
    samples cause a visible retry or a clear calibration error.
    """
    if not 1.0 <= args.sample_seconds <= 10.0:
        raise ValueError("--sample-seconds must be between 1 and 10")
    settle_seconds = min(0.5, args.sample_seconds / 4)
    if args.output.exists() and not args.force:
        raise FileExistsError(
            f"calibration already exists: {args.output}; use --force to replace it"
        )
    base = (
        MappingProfile.from_file(args.base) if args.base else MappingProfile.default()
    )
    capture = _open_camera(args.camera_index)
    samples: dict[str, list[dict[str, float]]] = {}
    stage_index = 0
    collecting_until: float | None = None
    collecting_started: float | None = None
    current_samples: list[dict[str, float]] = []
    latest_row: dict[str, float] | None = None
    previous_timestamp = -1
    last_warning = 0.0
    window = "ELF hand calibration"
    print("Calibration reads the webcam only. Press Q or Esc to cancel.")
    try:
        with _make_landmarker(args.model) as landmarker:
            while stage_index < len(STAGES):
                stage = STAGES[stage_index]
                key_label, instruction = _INSTRUCTIONS[stage]
                ok, frame = capture.read()
                if not ok:
                    raise RuntimeError("camera stopped returning frames")
                frame = cv2.flip(frame, 1)
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                timestamp = max(int(time.monotonic() * 1000), previous_timestamp + 1)
                previous_timestamp = timestamp
                result = landmarker.detect_for_video(image, timestamp)
                now = time.monotonic()
                latest_row = None
                if len(result.hand_world_landmarks) == 1:
                    try:
                        row = _sample_row(result.hand_world_landmarks[0])
                    except ValueError as exc:
                        if now - last_warning >= 2:
                            print(f"invalid hand landmarks: {exc}", file=sys.stderr)
                            last_warning = now
                    else:
                        latest_row = row
                        _draw_hand(frame, result.hand_landmarks[0])
                        if (
                            collecting_until is not None
                            and collecting_started is not None
                            and now - collecting_started >= settle_seconds
                        ):
                            current_samples.append(row)

                if collecting_until is None:
                    status = f"{stage_index + 1}/5  {instruction}"
                    hint = f"Press {key_label} to capture {args.sample_seconds:g}s"
                else:
                    remaining = max(0.0, collecting_until - now)
                    status = f"{stage_index + 1}/5  HOLD {stage.upper()}"
                    hint = (
                        f"{remaining:.1f}s left  valid frames: {len(current_samples)}"
                    )
                cv2.putText(
                    frame,
                    status,
                    (10, 28),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.55,
                    (0, 220, 255),
                    2,
                )
                cv2.putText(
                    frame,
                    hint,
                    (10, 55),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.55,
                    (0, 220, 255),
                    1,
                )
                if latest_row is not None:
                    thumb_status = (
                        f"Thumb gap {latest_row['tip_index_ratio']:.2f}  "
                        f"abd {latest_row['palm_abduction_angle']:.0f} deg  "
                        f"plane {latest_row['palm_plane_angle']:.0f} deg"
                    )
                    cv2.putText(
                        frame,
                        thumb_status,
                        (10, 81),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.5,
                        (255, 255, 255),
                        1,
                    )
                cv2.putText(
                    frame,
                    "One hand only  Q: cancel",
                    (10, frame.shape[0] - 15),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    (255, 255, 255),
                    1,
                )
                cv2.imshow(window, frame)
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), ord("Q"), 27):
                    raise RuntimeError("calibration cancelled by user")
                if collecting_until is None and key in (
                    ord(key_label.lower()),
                    ord(key_label),
                ):
                    current_samples = []
                    collecting_started = now
                    collecting_until = now + args.sample_seconds
                    print(f"capturing {stage}: {instruction}", flush=True)
                if collecting_until is not None and now >= collecting_until:
                    if len(current_samples) < 20:
                        print(
                            f"{stage}: only {len(current_samples)} valid frames; "
                            "keep one hand in view and retry",
                            file=sys.stderr,
                        )
                    else:
                        samples[stage] = current_samples
                        print(f"{stage}: saved {len(current_samples)} valid frames")
                        gap = _median_feature(current_samples, "tip_index_ratio")
                        abduction = _median_feature(
                            current_samples, "palm_abduction_angle"
                        )
                        plane = _median_feature(current_samples, "palm_plane_angle")
                        print(
                            f"{stage}: median thumb gap {gap:.3f}, "
                            f"abduction {abduction:.1f} deg, "
                            f"plane {plane:.1f} deg",
                            flush=True,
                        )
                        if stage == "fist":
                            try:
                                validate_four_fingers(samples)
                            except ValueError as exc:
                                print(
                                    f"fist calibration failed: {exc}; "
                                    "make a clearer fist and press F again",
                                    file=sys.stderr,
                                    flush=True,
                                )
                                samples.pop("fist")
                            else:
                                stage_index += 1
                        elif stage == "thumb_tucked":
                            try:
                                validate_thumb_spread(samples)
                            except ValueError as exc:
                                print(
                                    "thumb side-swing calibration failed: "
                                    f"{exc}; repeat W and T with the same "
                                    "hand orientation and distance as O",
                                    file=sys.stderr,
                                    flush=True,
                                )
                                samples.pop("thumb_wide")
                                samples.pop("thumb_tucked")
                                stage_index = STAGES.index("thumb_wide")
                            else:
                                stage_index += 1
                        elif stage == "thumb_bent":
                            try:
                                validate_thumb_bend(samples)
                            except ValueError as exc:
                                print(
                                    f"thumb bend calibration failed: {exc}; "
                                    "bend the thumb more clearly and press B again",
                                    file=sys.stderr,
                                    flush=True,
                                )
                                samples.pop("thumb_bent")
                            else:
                                stage_index += 1
                        else:
                            stage_index += 1
                    collecting_until = None
                    collecting_started = None
            mapping = derive_mapping(samples, base)
            _write_mapping(args.output, mapping, args.force)
            selected = mapping["camera_mapping"]
            assert isinstance(selected, dict)
            print(f"saved calibration: {args.output}")
            print(f"thumb spread metric: {selected['thumb_spread']['metric']}")
            print(f"thumb bend metric: {selected['thumb_bend']['metric']}")
            return 0
    finally:
        capture.release()
        cv2.destroyAllWindows()


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and report camera or calibration failure to stderr."""
    args = _parser().parse_args(argv)
    try:
        return run_calibration(args)
    except (OSError, ValueError, RuntimeError, cv2.error) as exc:
        print(f"dexhand-calibrate: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
