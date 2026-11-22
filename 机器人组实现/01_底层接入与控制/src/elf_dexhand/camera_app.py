"""Run webcam hand tracking and optionally drive the physical hand."""

from __future__ import annotations

import argparse
import math
import os
import sys
import time
from pathlib import Path
from typing import Any

import cv2
import mediapipe as mp

from .connection import BoardConfig
from .controller import HandController
from .persistent import PersistentBoardConnection
from .protocol import Joint
from .streaming import SAFE_POSE, RealtimeCanStream
from .vision_mapping import (
    DEFAULT_CALIBRATION_PATH,
    MappingProfile,
    PoseTargetStabilizer,
    ThumbTargetStabilizer,
    estimate_pose,
    measure_hand,
)

_EDGES = (
    (0, 1),
    (1, 2),
    (2, 3),
    (3, 4),
    (0, 5),
    (5, 6),
    (6, 7),
    (7, 8),
    (5, 9),
    (9, 10),
    (10, 11),
    (11, 12),
    (9, 13),
    (13, 14),
    (14, 15),
    (15, 16),
    (13, 17),
    (17, 18),
    (18, 19),
    (19, 20),
    (0, 17),
)


def _parser() -> argparse.ArgumentParser:
    """Declare camera and motor-control options without opening hardware."""
    parser = argparse.ArgumentParser(prog="dexhand-camera")
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument(
        "--model",
        type=Path,
        default=Path("models/hand_landmarker.task"),
    )
    parser.add_argument("--mapping", type=Path)
    parser.add_argument("--board-host", default=os.environ.get("BOARD_HOST"))
    parser.add_argument("--board-user", default=os.environ.get("BOARD_USER", "root"))
    parser.add_argument("--board-key", type=Path, default=os.environ.get("BOARD_KEY"))
    parser.add_argument(
        "--control",
        action="store_true",
        help="allow arming with A in the preview window",
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help="read one camera frame without loading the model",
    )
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--max-frames", type=int)
    parser.add_argument(
        "--interval",
        type=float,
        default=0.0,
        help="minimum interval between camera targets (default: every frame)",
    )
    parser.add_argument("--stream-hz", type=float, default=50.0)
    parser.add_argument("--max-speed", type=float, default=6000.0)
    parser.add_argument("--lost-timeout", type=float, default=0.7)
    parser.add_argument("--log-positions", action="store_true")
    parser.add_argument("--stats", action="store_true")
    return parser


def _open_camera(index: int) -> cv2.VideoCapture:
    """Open a Windows camera with DirectShow, then the default backend."""
    if index < 0:
        raise ValueError("camera index cannot be negative")
    backends = (
        (cv2.CAP_DSHOW, cv2.CAP_ANY) if sys.platform == "win32" else (cv2.CAP_ANY,)
    )
    for backend in backends:
        capture = cv2.VideoCapture(index, backend)
        if capture.isOpened():
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            return capture
        capture.release()
    raise RuntimeError(f"cannot open camera index {index}")


def _draw_hand(frame: Any, landmarks: Any) -> None:
    """Draw the 21 image landmarks and skeleton on a preview frame."""
    height, width = frame.shape[:2]
    points = [(int(point.x * width), int(point.y * height)) for point in landmarks]
    for start, end in _EDGES:
        cv2.line(frame, points[start], points[end], (0, 200, 255), 2)
    for point in points:
        cv2.circle(frame, point, 3, (0, 255, 0), -1)


def _draw_status(
    frame: Any, status: str, pose: dict[Joint, int] | None, control: bool
) -> None:
    """Overlay connection state and current target positions."""
    color = (0, 255, 0) if status == "ARMED" else (0, 200, 255)
    cv2.putText(frame, status, (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
    hint = "A: arm  D: disarm  Q: quit" if control else "Preview only  Q: quit"
    cv2.putText(frame, hint, (12, 58), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 1)
    if pose is not None:
        labels = " ".join(f"{joint.value}:{pose[joint]}" for joint in Joint)
        cv2.putText(
            frame,
            labels,
            (12, frame.shape[0] - 14),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.48,
            (255, 255, 255),
            1,
        )


def _make_landmarker(model: Path) -> Any:
    """Create MediaPipe's tracking-mode hand model from a local task file."""
    if not model.is_file():
        raise FileNotFoundError(
            f"hand model missing: {model}; see docs/experiment-18-camera-follow.md"
        )
    model_bytes = model.read_bytes()
    options = mp.tasks.vision.HandLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_buffer=model_bytes),
        running_mode=mp.tasks.vision.RunningMode.VIDEO,
        num_hands=2,
        min_hand_detection_confidence=0.6,
        min_hand_presence_confidence=0.6,
        min_tracking_confidence=0.6,
    )
    return mp.tasks.vision.HandLandmarker.create_from_options(options)


def _start_control(
    args: argparse.Namespace,
) -> tuple[PersistentBoardConnection, RealtimeCanStream]:
    """Check the board and start streaming, cleaning up any failed attempt.

    The first stream batch sends the device's safe pose. SSH, CAN, or remote
    process errors propagate so the preview can show a retryable failure.
    """
    board = PersistentBoardConnection(
        BoardConfig(
            host=args.board_host,
            user=args.board_user,
            key_path=Path(args.board_key) if args.board_key else None,
            connect_timeout=3,
        )
    )
    stream: RealtimeCanStream | None = None
    try:
        HandController(board).check_ready()
        stream = RealtimeCanStream(
            board,
            rate_hz=args.stream_hz,
            max_speed=args.max_speed,
            lost_timeout=args.lost_timeout,
        )
        stream.start()
        return board, stream
    except (OSError, RuntimeError):
        try:
            if stream is not None:
                stream.close()
        except (OSError, RuntimeError) as cleanup_error:
            print(
                f"CAN stream cleanup after failed arm: {cleanup_error}",
                file=sys.stderr,
            )
        finally:
            board.close()
        raise


def run_experiment(args: argparse.Namespace) -> int:
    """Process camera frames and drive motors only after GUI arming.

    The preview is passive until --control is supplied and the user presses A.
    On detection loss, disarm, or exit, the stream sends SAFE_POSE.
    Camera, model, network, and CAN failures propagate to main for reporting.
    """
    if not math.isfinite(args.interval) or args.interval < 0:
        raise ValueError("--interval must be a finite nonnegative value")
    if not math.isfinite(args.lost_timeout) or args.lost_timeout <= args.interval:
        raise ValueError("--lost-timeout must be finite and exceed --interval")
    if args.max_frames is not None and args.max_frames <= 0:
        raise ValueError("--max-frames must be positive")
    if args.headless and args.control:
        raise ValueError("--headless cannot control physical motors")
    if args.headless and args.max_frames is None and not args.probe:
        raise ValueError("--headless requires --max-frames")
    if args.control and not args.board_host:
        raise ValueError("--control requires --board-host or BOARD_HOST")

    if args.probe:
        capture = _open_camera(args.camera_index)
        try:
            ok, frame = capture.read()
            if not ok:
                raise RuntimeError("camera opened but did not return a frame")
            size = f"{frame.shape[1]}x{frame.shape[0]}"
            print(f"camera {args.camera_index}: {size} frame OK")
            return 0
        finally:
            capture.release()

    if args.mapping:
        profile = MappingProfile.from_file(args.mapping)
    elif DEFAULT_CALIBRATION_PATH.is_file():
        profile = MappingProfile.from_file(DEFAULT_CALIBRATION_PATH)
    elif args.control:
        raise ValueError(
            "physical control requires a personal mapping; "
            "run dexhand-calibrate first or supply --mapping"
        )
    else:
        profile = MappingProfile.default()
    capture = _open_camera(args.camera_index)
    board: PersistentBoardConnection | None = None
    hand: HandController | None = None
    stream: RealtimeCanStream | None = None
    thumb_filter = ThumbTargetStabilizer(SAFE_POSE)
    pose_filter = PoseTargetStabilizer(
        SAFE_POSE, window_size=5, alpha=0.15, max_step=250
    )
    armed = False
    arm_error = False
    valid_streak = 0
    last_target_update = 0.0
    frame_count = 0
    detected_count = 0
    previous_timestamp = -1
    last_warning = 0.0
    last_position_log = 0.0
    stats_start = time.monotonic()
    stats_frames = 0
    stats_capture = 0.0
    stats_inference = 0.0
    stats_cycles = 0
    try:
        with _make_landmarker(args.model) as landmarker:
            while args.max_frames is None or frame_count < args.max_frames:
                capture_start = time.monotonic()
                ok, frame = capture.read()
                if not ok:
                    raise RuntimeError("camera stopped returning frames")
                stats_capture += time.monotonic() - capture_start
                frame_count += 1
                stats_frames += 1
                frame = cv2.flip(frame, 1)
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                timestamp = max(int(time.monotonic() * 1000), previous_timestamp + 1)
                previous_timestamp = timestamp
                inference_start = time.monotonic()
                result = landmarker.detect_for_video(image, timestamp)
                stats_inference += time.monotonic() - inference_start
                now = time.monotonic()
                if stream is not None:
                    stream.check_health()
                target: dict[Joint, int] | None = None
                hand_count = len(result.hand_world_landmarks)
                if hand_count == 1:
                    try:
                        target = estimate_pose(result.hand_world_landmarks[0], profile)
                    except ValueError as exc:
                        if now - last_warning > 2:
                            print(f"invalid hand landmarks: {exc}", file=sys.stderr)
                            last_warning = now
                    else:
                        detected_count += 1
                        valid_streak += 1
                        _draw_hand(frame, result.hand_landmarks[0])
                        if args.log_positions and now - last_position_log >= 0.5:
                            values = measure_hand(
                                result.hand_world_landmarks[0], profile
                            )
                            metrics = " ".join(
                                (
                                    f"{joint.name}={values[joint]:.3f}"
                                    if joint is Joint.THUMB_SPREAD
                                    else f"{joint.name}={values[joint]:.1f}"
                                )
                                for joint in Joint
                            )
                            numbers = " ".join(
                                f"{joint.name}={target[joint]}" for joint in Joint
                            )
                            print(
                                "measured "
                                f"(spread {profile.spread.metric}, bend degrees): "
                                f"{metrics}"
                            )
                            print(f"camera target: {numbers}")
                            last_position_log = now
                else:
                    valid_streak = 0
                if target is None:
                    valid_streak = 0

                if stream is not None and target is not None and valid_streak >= 2:
                    if now - last_target_update >= args.interval:
                        thumb_target = thumb_filter.update(target)
                        stream.update_target(pose_filter.update(thumb_target))
                        last_target_update = now

                if args.stats and now - stats_start >= 3.0:
                    elapsed = now - stats_start
                    cycles = stream.sent_cycles if stream is not None else 0
                    rate = (
                        (cycles - stats_cycles) / elapsed
                        if stream is not None
                        else 0.0
                    )
                    print(
                        f"camera={stats_frames / elapsed:.1f}fps "
                        f"capture={stats_capture / stats_frames * 1000:.1f}ms "
                        f"inference={stats_inference / stats_frames * 1000:.1f}ms "
                        f"can_stream={rate:.1f}Hz",
                        flush=True,
                    )
                    stats_start = now
                    stats_frames = 0
                    stats_capture = 0.0
                    stats_inference = 0.0
                    stats_cycles = cycles

                status = "ARMED" if armed else "PREVIEW"
                if arm_error and not armed:
                    status += " / BOARD UNAVAILABLE - SEE CONSOLE"
                if hand_count > 1:
                    status += " / MULTIPLE HANDS"
                elif target is None:
                    status += " / NO HAND"
                _draw_status(frame, status, target, args.control)
                if not args.headless:
                    cv2.imshow("ELF hand camera follow", frame)
                    key = cv2.waitKey(1) & 0xFF
                    if key in (ord("q"), 27):
                        break
                    if args.control and key in (ord("a"), ord("A")) and not armed:
                        try:
                            board, stream = _start_control(args)
                        except (OSError, RuntimeError) as exc:
                            arm_error = True
                            print(f"control could not arm: {exc}", file=sys.stderr)
                        else:
                            hand = HandController(board)
                            thumb_filter = ThumbTargetStabilizer(SAFE_POSE)
                            pose_filter.reset(SAFE_POSE)
                            last_target_update = 0.0
                            armed = True
                            arm_error = False
                            stats_cycles = 0
                            print("control armed; camera targets now drive the hand")
                    if args.control and key in (ord("d"), ord("D")) and armed:
                        assert stream is not None
                        stream.close()
                        stream = None
                        assert board is not None
                        board.close()
                        board = None
                        hand = None
                        armed = False
                        thumb_filter = ThumbTargetStabilizer(SAFE_POSE)
                        pose_filter.reset(SAFE_POSE)
                        stats_cycles = 0
                        print("control disarmed; sent default pose")
        print(
            f"processed {frame_count} frames; exactly one valid hand in "
            f"{detected_count} frames"
        )
        return 0
    finally:
        try:
            if stream is not None:
                stream.close()
                print("exit: sent default pose")
        except Exception as exc:
            print(f"CAN stream cleanup failed: {exc}", file=sys.stderr)
            if hand is not None:
                hand.set_pose(SAFE_POSE)
                print("fallback: sent default pose with cansend")
            raise
        finally:
            if board is not None:
                board.close()
            capture.release()
            if not args.headless:
                cv2.destroyAllWindows()


def main(argv: list[str] | None = None) -> int:
    """Parse options and report every operational failure to stderr."""
    args = _parser().parse_args(argv)
    try:
        return run_experiment(args)
    except (OSError, ValueError, RuntimeError, cv2.error) as exc:
        print(f"dexhand-camera: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
