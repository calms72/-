"""Stream interpolated hand poses through one long-lived canplayer process."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Mapping

import paramiko

from .persistent import PersistentBoardConnection
from .protocol import Joint, encode_cansend, validate_interface, validate_position

SAFE_POSE: dict[Joint, int] = {
    Joint.THUMB_SPREAD: 0,
    Joint.THUMB_BEND: 615,
    Joint.INDEX: 0,
    Joint.MIDDLE: 0,
    Joint.RING: 0,
    Joint.PINKY: 18,
}


def _validated_pose(pose: Mapping[Joint, int]) -> dict[Joint, int]:
    """Copy a complete six-joint pose or raise before any CAN action."""
    if set(pose) != set(Joint):
        raise ValueError("stream target must contain exactly six joints")
    return {joint: validate_position(joint, pose[joint]) for joint in Joint}


class SpeedLimitedTrajectory:
    """Interpolate in motor-position units per second, independent of camera FPS."""

    def __init__(self, initial: Mapping[Joint, int], max_speed: float) -> None:
        """Validate initial position and speed without accessing hardware."""
        if not math.isfinite(max_speed) or max_speed <= 0:
            raise ValueError("max_speed must be a finite positive number")
        self._position = {
            joint: float(value) for joint, value in _validated_pose(initial).items()
        }
        self.max_speed = max_speed

    def advance(self, target: Mapping[Joint, int], elapsed: float) -> dict[Joint, int]:
        """Move toward target by at most max_speed * elapsed per joint.

        This changes only local state. Invalid targets or elapsed times raise
        before any state change; no CAN frames are transmitted here.
        """
        checked = _validated_pose(target)
        if not math.isfinite(elapsed) or elapsed < 0:
            raise ValueError("elapsed time must be finite and nonnegative")
        distance_limit = self.max_speed * elapsed
        result: dict[Joint, int] = {}
        for joint in Joint:
            current = self._position[joint]
            difference = checked[joint] - current
            step = max(-distance_limit, min(distance_limit, difference))
            updated = current + step
            self._position[joint] = updated
            result[joint] = validate_position(joint, round(updated))
        return result


def candump_batch(
    pose: Mapping[Joint, int], timestamp_us: int, interface: str = "can0"
) -> bytes:
    """Encode six timestamped canplayer lines without sending them.

    Adjacent joints have 500 microsecond spacing. The caller must advance
    timestamp_us monotonically between batches so canplayer can pace them.
    """
    checked = _validated_pose(pose)
    selected_interface = validate_interface(interface)
    if timestamp_us < 0:
        raise ValueError("timestamp_us cannot be negative")
    lines: list[str] = []
    for offset, joint in enumerate(Joint):
        seconds, microseconds = divmod(timestamp_us + offset * 500, 1_000_000)
        frame = encode_cansend(joint, checked[joint], selected_interface)
        lines.append(f"({seconds}.{microseconds:06d}) {selected_interface} {frame}\n")
    return "".join(lines).encode("ascii")


class RealtimeCanStream:
    """Keep the newest camera target and refresh all six joints at 50 Hz.

    Camera updates never wait for SSH or CAN. A background thread reads the
    latest target, interpolates by elapsed time, and streams six frames through
    one canplayer process. A stale target becomes SAFE_POSE after lost_timeout.
    Call close() on disarm and exit; it sends SAFE_POSE before ending the stream.
    """

    def __init__(
        self,
        board: PersistentBoardConnection,
        *,
        interface: str = "can0",
        rate_hz: float = 50.0,
        max_speed: float = 6000.0,
        lost_timeout: float = 0.7,
    ) -> None:
        """Store settings without moving the hand or starting a remote process."""
        if not math.isfinite(rate_hz) or not 10 <= rate_hz <= 100:
            raise ValueError("rate_hz must be between 10 and 100")
        if not math.isfinite(lost_timeout) or lost_timeout <= 1 / rate_hz:
            raise ValueError("lost_timeout must exceed one stream cycle")
        self.board = board
        self.interface = validate_interface(interface)
        self.rate_hz = rate_hz
        self.lost_timeout = lost_timeout
        self.trajectory = SpeedLimitedTrajectory(SAFE_POSE, max_speed)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._target = dict(SAFE_POSE)
        self._last_target = 0.0
        self._failure: Exception | None = None
        self._channel: paramiko.Channel | None = None
        self._thread: threading.Thread | None = None
        self.sent_cycles = 0

    def start(self) -> None:
        """Start one verified remote canplayer and the local writer thread.

        Raises if canplayer is absent, another canplayer is active, SSH fails,
        or the writer cannot deliver its first safe-pose batch. The caller owns
        the board and must close both stream and board after use.
        """
        if self._thread is not None:
            raise RuntimeError("CAN stream is already running")
        self.board.run("command -v canplayer >/dev/null")
        active = self.board.run("pgrep -x canplayer || true").stdout.strip()
        if active:
            raise RuntimeError(f"another canplayer is already running: {active}")
        channel = self.board.open_channel("canplayer -g 0 -x")
        channel.settimeout(2.0)
        self._channel = channel
        self._stop.clear()
        self._ready.clear()
        self._failure = None
        self._thread = threading.Thread(
            target=self._writer,
            name="dexhand-can-stream",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(3.0):
            self.close()
            raise RuntimeError("CAN stream did not start within 3 seconds")
        self.check_health()

    def update_target(self, pose: Mapping[Joint, int]) -> None:
        """Replace the target with the latest camera pose without blocking CAN."""
        checked = _validated_pose(pose)
        self.check_health()
        with self._lock:
            self._target = checked
            self._last_target = time.monotonic()

    def check_health(self) -> None:
        """Raise a visible error if the writer or remote canplayer has failed."""
        with self._lock:
            failure = self._failure
        if failure is not None:
            raise RuntimeError(f"CAN streaming failed: {failure}") from failure
        channel = self._channel
        if channel is not None and channel.exit_status_ready():
            error = self._read_stderr(channel)
            code = channel.recv_exit_status()
            raise RuntimeError(f"canplayer exited early ({code}): {error}")

    @staticmethod
    def _read_stderr(channel: paramiko.Channel) -> str:
        """Drain and decode available remote diagnostics without suppressing them."""
        chunks = bytearray()
        while channel.recv_stderr_ready():
            chunks.extend(channel.recv_stderr(65536))
        return chunks.decode("utf-8", errors="replace").strip()

    def _writer(self) -> None:
        """Publish the newest interpolated pose at a fixed real-time cadence."""
        channel = self._channel
        if channel is None:
            with self._lock:
                self._failure = RuntimeError("CAN channel was not initialized")
            self._ready.set()
            return
        period = 1.0 / self.rate_hz
        origin = time.monotonic()
        previous = origin
        next_tick = origin
        try:
            while True:
                now = time.monotonic()
                stopping = self._stop.is_set()
                with self._lock:
                    target = dict(self._target)
                    last_target = self._last_target
                if stopping or now - last_target >= self.lost_timeout:
                    target = SAFE_POSE
                elapsed = min(now - previous, 2 * period)
                previous = now
                pose = (
                    dict(SAFE_POSE)
                    if stopping
                    else self.trajectory.advance(target, elapsed)
                )
                stamp_us = 1_750_000_000_000_000 + round((now - origin) * 1_000_000)
                channel.sendall(candump_batch(pose, stamp_us, self.interface))
                diagnostics = self._read_stderr(channel)
                if diagnostics:
                    raise RuntimeError(f"canplayer: {diagnostics}")
                if channel.exit_status_ready():
                    raise RuntimeError(
                        f"canplayer exited with {channel.recv_exit_status()}"
                    )
                self.sent_cycles += 1
                self._ready.set()
                if stopping:
                    break
                next_tick += period
                delay = next_tick - time.monotonic()
                if delay < 0:
                    next_tick = time.monotonic()
                    delay = 0
                self._stop.wait(delay)
        except Exception as exc:
            with self._lock:
                self._failure = exc
            self._ready.set()

    def close(self) -> None:
        """Send a final safe pose and close the remote stream.

        Raises if the writer failed, remained blocked, or canplayer returned a
        nonzero status. The caller should then attempt a separate safe command
        if SSH still works and use the physical stop if it does not.
        """
        thread = self._thread
        channel = self._channel
        if thread is None or channel is None:
            return
        self._stop.set()
        thread.join(timeout=3.0)
        try:
            if thread.is_alive():
                raise RuntimeError("CAN writer did not stop within 3 seconds")
            self.check_health()
            channel.shutdown_write()
            deadline = time.monotonic() + 3.0
            while not channel.exit_status_ready():
                if time.monotonic() >= deadline:
                    raise RuntimeError("canplayer did not exit after stdin closed")
                time.sleep(0.01)
            diagnostics = self._read_stderr(channel)
            code = channel.recv_exit_status()
            if code != 0 or diagnostics:
                raise RuntimeError(f"canplayer exited {code}: {diagnostics}")
        finally:
            channel.close()
            self._channel = None
            self._thread = None
