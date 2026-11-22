#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hand.py —— ELF 6-DOF 灵巧手 位形保持器 + 交互控制台

【在板子上运行】  (MYD-LD25X / OpenSTLinux / root)

--------------------------------------------------------------------------
为什么需要"位形保持器"这个架构
--------------------------------------------------------------------------
实测结论（2026-09-24）：

    发一次指令  ->  手运动到位并保持  ->  约 8 秒后自动释放（回到伸展）
    每 0.2 秒刷新 ->  连续 22 秒一直保持，停止刷新就松开

也就是说，**手内部的驱动器有"指令超时保护"**：一段时间收不到新指令就
主动松开。这其实是正确的安全设计 —— 如果控制端死机，宁可松手也不要
死攥着（死攥会烧电机、也会损坏被抓的物体）。

所以我们的架构是：
    一个后台线程以 20 Hz 持续重发当前目标位置（喂狗），
    你的输入只负责【修改目标值】，不直接发帧。

20 Hz 比任何可能的超时值（3~8 秒）都快 60~160 倍，余量充足。

--------------------------------------------------------------------------
协议（**实测验证**得出；厂商文档原文不在本仓库）
--------------------------------------------------------------------------
CAN ID (11 位标准帧):
    1 = 拇指侧摆    2 = 拇指弯曲    3 = 食指
    4 = 中指        5 = 无名指      6 = 小指

数据 (DLC=8):
    AA <pos_lo> <pos_hi> E8 09 64 03 00
    └ 指令头  └ 16 位小端 ┘ └─ 控制字 1~4 ─┘ └保留

位置量纲: 0 = 完全伸展, 4095 = 完全弯曲       (实测: 幅度严格随数值变化)
安全限位: ID 1 -> 0~2000,  ID 2~6 -> 0~4095

--------------------------------------------------------------------------
用法
--------------------------------------------------------------------------
    python3 hand.py                 # 连接 can0，进入交互控制台
    python3 hand.py --sim           # 模拟模式：不发真帧，只打印（可离线测试）

控制台命令:
    3 2000            把 ID3（食指）移到位置 2000
    pose 0 2675 4095 4095 4095 4095     一次设置 6 个关节
    zero              全部归零（完全伸展）
    relax             同 zero
    home              同上（别名，语义更清楚）
    rps [轮数]        自动演示「石头剪刀布」（默认 3 轮）
    wave              食指来回摆动（测试连续性）
    status            打印当前目标位形
    q / quit / exit   退出（会在 8 秒内自动松开）

退出时【故意不发归零指令】—— 让它按自己的超时保护自然松开，
这符合"失去控制就放手"的安全原则。
"""

import socket
import struct
import sys
import threading
import time

# ============================== 配置 ==============================
CAN_IFACE = "can0"
SEND_HZ = 20.0          # 刷新频率
SLEW_PER_CYCLE = 120    # 每个刷新周期每个关节最多移动多少（平滑用）

# CAN 帧结构（Linux struct can_frame）= 16 字节
CAN_FRAME_FMT = "=IBBBB8s"

# 关节名 + 安全限位（**实测**：拇指侧摆限 2000，其余全量程 4095）
JOINTS = {
    1: ("拇指-侧摆", 0, 2000),
    2: ("拇指-弯曲", 0, 4095),
    3: ("食指",      0, 4095),
    4: ("中指",      0, 4095),
    5: ("无名指",    0, 4095),
    6: ("小指",      0, 4095),
}

# 三个手势的关节位置（我们自己选定并实测有效的位形）
GESTURES = {
    "石头": [0, 2675, 4095, 4095, 4095, 4095],
    "剪刀": [0, 2675, 0,    0,    4095, 4095],
    "布":   [0, 0,    0,    0,    0,    0],
}
RPS_ORDER = ["石头", "剪刀", "布"]


# ============================== CAN 层 ==============================
def make_socket(sim=False):
    """建立 SocketCAN 原始套接字。sim=True 时返回一个假套接字（离线测试用）。"""
    if sim:
        class FakeSock:
            def __init__(self):
                self.last = {}

            def bind(self, _):
                pass

            def send(self, frame):
                cid, dlc, _, _, _, data = struct.unpack(CAN_FRAME_FMT, frame)
                # 只在内容变化时打印，否则 20Hz×6关节 会刷屏
                key = (cid, data[:dlc])
                if self.last.get(cid) != key:
                    self.last[cid] = key
                    print(f"    [sim] ID={cid:03X} DLC={dlc} "
                          + " ".join(f"{b:02X}" for b in data[:dlc]))
        return FakeSock()

    s = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
    s.bind((CAN_IFACE,))
    return s


def build_frame(joint_id, pos):
    """构造灵巧手位置控制帧

    注意 can_id 必须是【关节号】—— 这是最容易写错的地方：
    协议里 CAN ID 本身就是"发给哪个关节"的地址，不是常量。
    """
    pos = int(pos) & 0xFFFF
    data = bytes([0xAA, pos & 0xFF, (pos >> 8) & 0xFF,
                  0xE8, 0x09, 0x64, 0x03, 0x00])
    return struct.pack(CAN_FRAME_FMT, joint_id, 8, 0, 0, 0, data)


def clamp(joint_id, pos):
    """安全限幅 —— 保护机械结构，防止电机堵转"""
    _, lo, hi = JOINTS[joint_id]
    pos = int(pos)
    if pos < lo:
        pos = lo
    if pos > hi:
        pos = hi
    return pos


# ============================== 控制器 ==============================
class Hand:
    def __init__(self, sim=False):
        self.sock = make_socket(sim)
        self.sim = sim
        # target = 我们想去的地方；current = 实际发出去的值（做平滑用）
        self.target = {j: 0 for j in JOINTS}
        self.current = {j: 0 for j in JOINTS}
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = None
        self.frames_sent = 0

    # ---- 刷新线程：以 SEND_HZ 持续重发，喂狗 ----
    def _refresh_loop(self):
        period = 1.0 / SEND_HZ
        while not self._stop.is_set():
            t0 = time.time()
            with self._lock:
                planned = dict(self.target)
            for jid, goal in planned.items():
                cur = self.current[jid]
                if cur != goal:
                    # 限速平滑，避免关节突跳
                    delta = goal - cur
                    if delta > SLEW_PER_CYCLE:
                        cur += SLEW_PER_CYCLE
                    elif delta < -SLEW_PER_CYCLE:
                        cur -= SLEW_PER_CYCLE
                    else:
                        cur = goal
                    self.current[jid] = cur
                frame = build_frame(jid, self.current[jid])
                try:
                    self.sock.send(frame)
                    self.frames_sent += 1
                except OSError as e:
                    print(f"\n  !! 发送失败: {e}")
                    self._stop.set()
                    return
            dt = time.time() - t0
            if dt < period:
                time.sleep(period - dt)

    def start(self):
        self._thread = threading.Thread(target=self._refresh_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)

    # ---- 改目标（线程安全）----
    def set_joint(self, jid, pos, quiet=False):
        if jid not in JOINTS:
            print(f"  !! 没有 ID {jid}（有效范围 1-6）")
            return
        p = clamp(jid, pos)
        if p != int(pos):
            name, lo, hi = JOINTS[jid]
            print(f"  !! 限幅: ID{jid}({name}) {pos} -> {p}  (安全范围 {lo}~{hi})")
        with self._lock:
            self.target[jid] = p
        if not quiet:
            name = JOINTS[jid][0]
            print(f"  ID{jid} {name} -> {p}")

    def set_pose(self, values, quiet=False):
        for jid, v in zip(sorted(JOINTS), values):
            self.set_joint(jid, v, quiet=quiet)

    def status(self):
        print("  关节            目标    当前发送")
        for jid in sorted(JOINTS):
            name = JOINTS[jid][0]
            print(f"  ID{jid} {name:<10} {self.target[jid]:>6} {self.current[jid]:>10}")
        print(f"  已发送 {self.frames_sent} 帧  频率 {SEND_HZ:g} Hz")


# ============================== 演示动作 ==============================
def demo_rps(hand, rounds=3):
    """石头剪刀布演示（用 200Hz 刷新把位形焊住）"""
    print(f"\n  石头剪刀布演示，{rounds} 轮。每轮保持 2 秒。")
    for r in range(1, rounds + 1):
        for name in RPS_ORDER:
            if hand._stop.is_set():
                return
            vals = GESTURES[name]
            print(f"\n  [{r}/{rounds}] {name}  {vals}")
            hand.set_pose(vals, quiet=True)
            time.sleep(2.0)
    print("\n  演示结束，回零")
    hand.set_pose([0] * 6, quiet=True)


def demo_wave(hand, cycles=6):
    """食指来回摆动，测试运动的连续性"""
    print(f"\n  食指摆动 {cycles} 个来回")
    for _ in range(cycles):
        if hand._stop.is_set():
            return
        hand.set_joint(3, 2500, quiet=True)
        time.sleep(0.8)
        hand.set_joint(3, 200, quiet=True)
        time.sleep(0.8)


# ============================== 主循环 ==============================
HELP = """可用命令:
  3 2000                          把 ID3(食指) 移到 2000
  pose 0 2675 4095 4095 4095 4095 一次设置 6 个关节
  zero | relax | home             全部归零（完全伸展）
  rps [轮数]                      自动演示石头剪刀布
  wave                            食指来回摆动
  status                          查看当前目标位形
  q                               退出
"""


def main():
    sim = "--sim" in sys.argv
    hand = Hand(sim=sim)

    print("=" * 60)
    print("  ELF 6-DOF 灵巧手 位形保持器")
    print("=" * 60)
    print(f"  接口     : {CAN_IFACE}" + ("   【模拟模式，不发真帧】" if sim else ""))
    print(f"  刷新频率 : {SEND_HZ:g} Hz  (驱动约 8 秒超时，余量充足)")
    print(f"  平滑限速 : 每周期最多移动 {SLEW_PER_CYCLE}")
    print()
    print(HELP)
    print("  提示：安全范围 ID1=0~2000，ID2~6=0~4095")
    print("=" * 60)

    hand.start()
    try:
        while True:
            try:
                line = input("hand> ").strip()
            except EOFError:
                break
            if not line:
                continue
            parts = line.split()
            cmd = parts[0].lower()

            if cmd in ("q", "quit", "exit"):
                break
            elif cmd == "status":
                hand.status()
            elif cmd in ("zero", "relax", "home"):
                hand.set_pose([0] * 6, quiet=True)
                print("  已归零（全部完全伸展）")
            elif cmd == "rps":
                rounds = int(parts[1]) if len(parts) > 1 else 3
                demo_rps(hand, rounds)
            elif cmd == "wave":
                demo_wave(hand)
            elif cmd == "pose":
                if len(parts) < 7:
                    print("  用法: pose p1 p2 p3 p4 p5 p6")
                    continue
                hand.set_pose([int(x) for x in parts[1:7]])
            elif cmd == "help" or cmd == "?":
                print(HELP)
            else:
                # 简写: "<id> <pos>"
                try:
                    jid, pos = int(parts[0]), int(parts[1])
                except (ValueError, IndexError):
                    print("  看不懂。输入 help 看命令列表")
                    continue
                hand.set_joint(jid, pos)
    except KeyboardInterrupt:
        print()
    finally:
        hand.stop()
        print("  已停止刷新。手会在约 8 秒内按自身的超时保护自然松开。")
        print("  （这是故意的：失联就放手，比死攥着安全）")


if __name__ == "__main__":
    main()
