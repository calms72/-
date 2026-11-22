r"""Replay the ELF 6-DOF hand's rock/scissors/paper poses over CAN.

Run on the Windows PC (Python 3, no extra packages):
    python .\代码\rps_demo.py --run

Without --run, the script only writes a candump-format log for inspection.
"""

from __future__ import annotations

import argparse
import math
import re
import subprocess
import time
from pathlib import Path


# Joint order: thumb abduction, thumb flexion, index, middle, ring, little.
# Values are from the course guide's rock/scissors/paper example.
POSES = {
    "stone": (0, 2675, 4095, 4095, 4095, 4095),
    "scissors": (0, 2675, 0, 0, 4095, 4095),
    "paper": (0, 0, 0, 0, 0, 0),
}
REST = (0, 615, 0, 0, 0, 0)
LIMITS = (2000, 4095, 4095, 4095, 4095, 4095)
RATE_HZ = 100
REMOTE_LOG = "/tmp/elf_rps_demo.log"


def can_frame(joint_id: int, position: int) -> str:
    if not 1 <= joint_id <= 6 or not 0 <= position <= LIMITS[joint_id - 1]:
        raise ValueError(f"invalid joint/position: {joint_id}, {position}")
    payload = bytes((0xAA, position & 0xFF, position >> 8, 0xE8, 0x09, 0x64, 0x03, 0x00))
    return f"{joint_id:03X}#{payload.hex().upper()}"


def write_log(path: Path, rounds: int) -> tuple[int, float]:
    timestamp = time.time()
    ticks = 0
    interval = 1 / RATE_HZ
    position = REST

    with path.open("w", encoding="ascii", newline="\n") as out:

        def emit(pose: tuple[int, ...]) -> None:
            nonlocal ticks
            stamp = timestamp + ticks * interval
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
        for _ in range(rounds):
            for name in ("stone", "scissors", "paper"):
                target = POSES[name]
                move(position, target, 1.2)
                hold(target, 1.5)
                position = target
        move(position, REST, 0.8)
        hold(REST, 0.5)

    return ticks * 6, ticks * interval


def run_on_board(path: Path, host: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9.-]+", host):
        raise ValueError("--host must be an IP address or a plain hostname")
    ssh_options = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=5"]
    target = f"root@{host}"
    subprocess.run(["scp", *ssh_options, str(path), f"{target}:{REMOTE_LOG}"], check=True)
    print("已上传；请让手周围保持空旷，开始演示：石头 → 剪刀 → 布。", flush=True)
    subprocess.run(["ssh", *ssh_options, target, f"canplayer -I {REMOTE_LOG}"], check=True)
    print("演示完成，灵巧手已回到放松姿态。")


def main() -> None:
    parser = argparse.ArgumentParser(description="ELF 6-DOF 灵巧手石头剪刀布演示")
    parser.add_argument("--run", action="store_true", help="上传日志到控制板并实际运行")
    parser.add_argument("--host", default=None, help="控制板 SSH 地址")
    parser.add_argument("--rounds", type=int, default=1, help="演示轮数，默认 1")
    args = parser.parse_args()
    if args.run and not args.host:
        parser.error("--run requires an explicit --host")
    if not 1 <= args.rounds <= 10:
        parser.error("--rounds must be between 1 and 10")

    log_dir = Path(__file__).resolve().parents[1] / "运行记录"
    log_dir.mkdir(exist_ok=True)
    path = log_dir / "rps_can.log"
    frames, duration = write_log(path, args.rounds)
    print(f"已生成 {path}（{frames} 帧，约 {duration:.1f} 秒）。")
    if args.run:
        run_on_board(path, args.host)
    else:
        print("仅生成日志；实际运行请加 --run。")


if __name__ == "__main__":
    main()
