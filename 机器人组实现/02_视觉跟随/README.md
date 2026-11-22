# 视觉跟随：板端协议与离线预览

从两帧手部关键点开始，在电脑上重放板端解析过程。第一帧的四个食指点接近一条直线，第二帧将指尖移回掌侧。回放可检查协议帧数、五指特征和候选映射，也可以运行 C 层的调度与保护测试。

使用电脑摄像头、MediaPipe、SSH 和 CAN 驱动实物的已联动版本见 [01_底层接入与控制](../01_底层接入与控制/)。本目录对应另一条研发路线：黄色模块负责识别并输出坐标，MP257 的 Linux 程序负责解释坐标、读取电机反馈，后续再接入动作控制。

## 先跑通两帧样例

在本目录打开终端。Python 仅使用标准库，以下命令已在 Windows、Python 3.12.4 上运行。无需安装模型、连接 USB 或启动板子。

```powershell
python -B python_board/replay_on_board.py samples/synthetic_two_frames.bin 2
python -B python_board/replay_feature_preview.py samples/synthetic_two_frames.bin 2
python -B -m unittest discover -s python_board/tests -p "test_*.py"
```

第一条读取 642 字节，显示两帧并通过半帧超时检查。第二条得到五个弯曲通道各两次有效测量、拇指侧摆零次有效测量，结尾显示 `MOTION_TARGETS NONE`。第三条运行本目录自带的 39 项 Python 测试，覆盖候选映射、异常输入、滤波、无效状态及进程间消息。样例按协议人工构造，其数值无人体测量或电机标定含义。

如需查看每一帧的具体数值，可在本目录运行以下 PowerShell 示例。

```powershell
@'
from pathlib import Path
import sys
sys.path.insert(0, "python_board")
from visual_protocol import VisualFrameParser
from hand_features import measure_hand
from index_reach_xy import measure_index_reach_xy

raw = Path("samples/synthetic_two_frames.bin").read_bytes()
for frame in VisualFrameParser().feed(raw, 0):
    curl = measure_hand(frame)["channels"]["index_curl"]["raw"]
    reach = measure_index_reach_xy(frame)["raw"]
    print(f"食指弯曲候选量 {curl:.3f}，指尖到腕的归一化距离 {reach:.3f}")
'@ | python -B -
```

本样例的两行数值分别约为 `0.001 / 2.137` 和 `0.848 / 1.126`。后者以掌长为分母，允许超过 1。

## 源码位置

| 位置 | 作用 |
|---|---|
| `python_board/visual_protocol.py`、`replay_on_board.py` | 从字节流识别变长帧、检查长度和分隔符、解析 63 个有限数值。实机串口程序与离线回放使用同一个解析器。 |
| `hand_features.py`、`index_reach_xy.py`、`replay_feature_preview.py`（均在 `python_board/`） | 用二维投影衡量五指弯曲，另提供食指距离特征。拇指侧摆保留诊断数值，资格保持无效。 |
| `python_board/index_preview_mapping.py`、`index_preview_filter.py` | 根据明确给定的端点计算食指候选位置，处理跳变、过期、滤波暖机及时间异常。 |
| `python_board/*worker.py` | 接收带序号的输入，计算特征，并可在当前工作目录记录源帧、映射或滤波结果。测试使用临时目录及合成输入。工作进程输出中的六个电机目标保持 `NONE`。 |
| `python_board/*.c`、`*.h` | 纯 C 模块分别处理驱动报文编码、准入判定、反馈快照、按键事件、食指计算、单指试验状态、A0/AA 调度和静态保持恢复状态。输入由调用方提供，所列模块不直接打开设备。 |
| `python_board/tests/` | Python 和 C 原有离线用例。C 用例将虚拟时钟、状态与反馈输入纯函数；Python 测试使用标准库 unittest。 |
| `samples/synthetic_two_frames.bin` | 两个合成手部坐标帧，使用和板端实测解析器相同的 ASCII 字节格式；是本次整理新增的公开样例。 |

## 可选：运行 C 层测试

需要已安装的 C11 编译器。下例适用于 PowerShell 与 GCC，在 `python_board` 中执行，将 12 个测试程序分别编译到临时目录。Windows MinGW-w64 GCC 15.2.0 已全部通过。其中总线调度测试检查了六节点回包的 720 种顺序。需要核对某模块时，只保留对应测试项即可。

```powershell
Set-Location python_board
$cases = [ordered]@{
  native_control_gate_test = @("control_gate")
  native_control_gate_scope_review = @("control_gate")
  native_drive_protocol_test = @("drive_protocol")
  native_index_follow_math_test = @("index_follow_math")
  native_index_follow_math_review = @("index_follow_math")
  native_index_follow_math_review_v2 = @("index_follow_math")
  native_index_follow_commission_review = @("index_follow_commission")
  native_index_follow_commission_review_v3 = @("index_follow_commission")
  native_index_follow_bus_schedule_review = @("index_follow_bus_schedule", "index_follow_commission")
  native_key_guard_events_test = @("key_guard_events")
  native_readonly_snapshot_test = @("readonly_snapshot")
  native_recovery_hold_review = @("recovery_hold_fsm")
}
$buildDir = Join-Path ([IO.Path]::GetTempPath()) ("elf-vision-" + [guid]::NewGuid())
[void][IO.Directory]::CreateDirectory($buildDir)
foreach ($case in $cases.GetEnumerator()) {
    $sources = @($case.Value | ForEach-Object { "$_.c" })
    $binary = Join-Path $buildDir ($case.Key + ".exe")
    gcc -std=c11 -D__USE_MINGW_ANSI_STDIO=1 @sources "tests/$($case.Key).c" -lm -o $binary
    if ($LASTEXITCODE -ne 0) { throw "编译失败 $($case.Key)" }
    & $binary
    if ($LASTEXITCODE -ne 0) { throw "测试失败 $($case.Key)" }
}
```

本地还保留一个较早的食指调试 V2 测试。它对当前控制器的移动准入仍采用旧预期，第 108 行断言未通过。本目录采用与当前保护条件一致、已通过的 V3 版本，V1 作为其依赖一并保留。

## 实物进度与发布范围

| 路线 | 已有证据 | 当前边界 |
|---|---|---|
| 电脑摄像头与电脑视觉程序 | 团队既有记录包含现场标定、张手握拳和操作者对实体跟随的观察。 | 使用相邻 01 目录的电脑视觉入口；实际角度误差及物理端到端延迟尚无仪器测量。 |
| 团队黄板 + MP257 Linux C/Python 续作 | 历史板端记录包括 10 秒发布 299 帧、另一窗口 288 次 A0 查询及对应反馈、食指小步 P0→131→0、无运动预览中的出画清值和入画恢复。 | 视觉驱动单指、运动中的动态停止、六轴独立跟随、十分钟连续运行、断电脑和冷启动仍待整体验收。静态保持及收到指令应答只能证明对应分项。 |
| 设备原装 CODESYS 随动 | 既有开机检查观察到原装应用占用视觉串口、现场手指跟随以及六节点位置反馈变化。 | 该会话中的整手动作由设备原装程序执行；原装程序与课程源码包的精确版本对应仍待确认。 |

完整现场采集与运动控制链留在本地，包括直接读取指定串口的 `live_parse_preview.py`、依赖 Linux 串口/CAN 的 `readonly_runtime.c`、`index_follow_runtime*.c`、单次维护与动作程序、每次现场会话的参数及证据。这里公开的是可复跑的解析、预览和控制逻辑离线部分。公开的回放程序直接将同一格式的字节流交给 `VisualFrameParser`，本次验证全程使用合成数据和模拟反馈。

## 工作记录与实验报告

以下为 Word 公开副本。

- [ELF 六自由度灵巧手_底层接入与控制验证实验报告](工作记录与实验报告/ELF%20六自由度灵巧手_底层接入与控制验证实验报告.docx)（接入实验参考）
- [灵巧手的视觉跟随](工作记录与实验报告/灵巧手的视觉跟随.docx)（视觉跟随工作记录）

本目录里的《底层接入与控制验证实验报告》是共用接入链路的参考资料，正文与底层接入目录的同名报告一致。
