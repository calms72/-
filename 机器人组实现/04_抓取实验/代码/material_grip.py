r"""Chapter 24 material-grasp pose test for the ELF 6-DOF hand.

Run from Windows PowerShell, for example:
    python .\代码\material_grip.py --mode cylinder --run
    python .\代码\material_grip.py --mode box --run

Each run starts and ends at the relaxed pose. Without --run it only generates
the CAN playback file. No Python packages need to be installed on the board.
"""

from __future__ import annotations

import argparse
import math
import re
import subprocess
import time
from pathlib import Path


POSES = {
    # Chapter 24 manual-mode connectivity check: move the index finger only.
    "manual": (0, 615, 1500, 0, 0, 0),
    # Preset1 and Preset2 in the course guide, PDF pages 102 and 111.
    "cylinder": (2500, 0, 1600, 1400, 1200, 1500),
    "box": (2500, 0, 1000, 1200, 1000, 1000),
}
REST = (0, 615, 0, 0, 0, 0)
# The Chapter 24 guide raises joint 1's software limit to 3000.
LIMITS = (3000, 4095, 4095, 4095, 4095, 4095)
RATE_HZ = 100
REMOTE_LOG = "/tmp/elf_grip_demo.log"
REMOTE_CAPTURE = "/tmp/elf_grip_capture.log"


def can_frame(joint_id: int, position: int) -> str:
    if not 1 <= joint_id <= 6 or not 0 <= position <= LIMITS[joint_id - 1]:
        raise ValueError(f"invalid joint/position: {joint_id}, {position}")
    payload = bytes((0xAA, position & 0xFF, position >> 8, 0xE8, 0x09, 0x64, 0x03, 0x00))
    return f"{joint_id:03X}#{payload.hex().upper()}"


def write_log(path: Path, target: tuple[int, ...], hold_seconds: float) -> tuple[int, float]:
    timestamp = time.time()
    ticks = 0

    with path.open("w", encoding="ascii", newline="\n") as out:

        def emit(pose: tuple[int, ...]) -> None:
            nonlocal ticks
            stamp = timestamp + ticks / RATE_HZ
            for joint_id, value in enumerate(pose, start=1):
                out.write(f"({stamp:.6f}) can0 {can_frame(joint_id, value)}\n")
            ticks += 1

        def hold(pose: tuple[int, ...], seconds: float) -> None:
            for _ in range(round(seconds * RATE_HZ)):
                emit(pose)

        def move(start: tuple[int, ...], end: tuple[int, ...], seconds: float) -> None:
            steps = math.ceil(seconds * RATE_HZ)
            for step in range(1, steps + 1):
                fraction = step / steps
                eased = fraction * fraction * (3 - 2 * fraction)
                emit(tuple(round(a + (b - a) * eased) for a, b in zip(start, end)))

        hold(REST, 0.5)
        move(REST, target, 2.0)
        hold(target, hold_seconds)
        move(target, REST, 1.2)
        hold(REST, 0.5)

    return ticks, ticks / RATE_HZ


def read_feedback(path: Path, sample_tick: int) -> tuple[int, ...] | None:
    groups: list[tuple[int, ...]] = []
    current: dict[int, int] = {}
    pattern = re.compile(r"\bcan0 10([1-6])#([0-9A-Fa-f]{16})\b")
    for line in path.read_text(encoding="ascii", errors="replace").splitlines():
        match = pattern.search(line)
        if not match:
            continue
        joint_id = int(match.group(1))
        payload = bytes.fromhex(match.group(2))
        if payload[0] != 0xAA:
            continue
        current[joint_id] = payload[2] | (payload[3] << 8)
        if len(current) == 6:
            groups.append(tuple(current[i] for i in range(1, 7)))
            current = {}
    print(f"收到 {len(groups)} 组完整的六关节反馈。")
    if sample_tick >= len(groups):
        return None
    return groups[sample_tick]


def run_on_board(log_path: Path, capture_path: Path, host: str, duration: float) -> None:
    if not re.fullmatch(r"[A-Za-z0-9.-]+", host):
        raise ValueError("--host must be an IP address or a plain hostname")
    options = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=5"]
    target = f"root@{host}"
    subprocess.run(["scp", *options, str(log_path), f"{target}:{REMOTE_LOG}"], check=True)
    print("请确认手周围空旷、物料轻便。3 秒后开始动作。", flush=True)
    time.sleep(3)
    capture_seconds = math.ceil(duration) + 5
    remote_command = (
        f"timeout {capture_seconds} candump -L can0 > {REMOTE_CAPTURE} 2>&1 < /dev/null & "
        f"sleep 0.5; canplayer -I {REMOTE_LOG}; sleep 0.5"
    )
    subprocess.run(["ssh", *options, target, remote_command], check=True)
    subprocess.run(["scp", *options, f"{target}:{REMOTE_CAPTURE}", str(capture_path)], check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="ELF 灵巧手第 24 章物料抓取位姿测试")
    parser.add_argument("--mode", choices=POSES, required=True, help="manual、cylinder 或 box")
    parser.add_argument("--hold", type=float, default=5.0, help="保持目标姿态的秒数，默认 5")
    parser.add_argument("--host", default=None, help="控制板 SSH 地址")
    parser.add_argument("--positions", help="六个关节的自定义目标值，逗号分隔")
    parser.add_argument("--label", help="自定义试验名称，用于区分生成的记录文件")
    parser.add_argument("--run", action="store_true", help="上传并运行；不加时仅生成文件")
    args = parser.parse_args()
    if args.run and not args.host:
        parser.error("--run requires an explicit --host")
    if not 1 <= args.hold <= 20:
        parser.error("--hold must be between 1 and 20 seconds")

    target = POSES[args.mode]
    if args.positions:
        try:
            target = tuple(int(part.strip()) for part in args.positions.split(","))
        except ValueError:
            parser.error("--positions must contain six comma-separated integers")
        if len(target) != 6 or any(not 0 <= value <= limit for value, limit in zip(target, LIMITS)):
            parser.error("--positions must contain six values within the joint limits")
    if args.label and not re.fullmatch(r"[A-Za-z0-9_-]+", args.label):
        parser.error("--label may contain only letters, digits, _ or -")

    root = Path(__file__).resolve().parents[1] / "运行记录"
    root.mkdir(exist_ok=True)
    stem = f"grip_{args.mode}" + (f"_{args.label or 'custom'}" if args.positions else "")
    log_path = root / f"{stem}_can.log"
    capture_path = root / f"{stem}_capture.log"
    ticks, duration = write_log(log_path, target, args.hold)
    print(f"模式 {args.mode}，目标 {target}；已生成 {ticks * 6} 帧，约 {duration:.1f} 秒。")
    if not args.run:
        print("仅生成文件；实际运行请加 --run。")
        return

    run_on_board(log_path, capture_path, args.host, duration)
    sample_tick = round((0.5 + 2.0 + args.hold / 2) * RATE_HZ)
    actual = read_feedback(capture_path, sample_tick)
    if actual is None:
        print("未获得保持阶段的完整位置反馈，请检查抓包文件。")
    else:
        print(f"保持阶段实测位置：{actual}")
        print(f"与目标差值：{tuple(value - setpoint for value, setpoint in zip(actual, target))}")
    print("动作已结束，控制指令已回到放松姿态。")


if __name__ == "__main__":
    main()
