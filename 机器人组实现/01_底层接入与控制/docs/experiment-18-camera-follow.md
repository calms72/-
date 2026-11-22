# PC 摄像头跟随与图片检查

PC 采集画面，MediaPipe 检测 21 个关键点，团队算法计算六轴位置。可选的实机发送通过 SSH 和板端 canplayer。板端独立实现与这条路线共享关节定义，图像处理的位置和运行依赖不同，见 [板端视觉跟随](../../02_视觉跟随/README.md)。

## 安装视觉依赖和模型

在 01 目录打开 PowerShell，先按 [基础安装](../README.md) 创建虚拟环境。安装 vision 可选项会引入 MediaPipe 与 Paramiko，MediaPipe 的发行元数据还要求 opencv-contrib-python。避免在同一环境叠装多个提供 cv2 的 OpenCV 发行包。

~~~powershell
.\.venv\Scripts\python.exe -m pip install '.[vision]'
.\scripts\download_hand_model.ps1
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py' -v
.\.venv\Scripts\python.exe .\tests\verify_image.py --model .\models\hand_landmarker.task --expect 0
~~~

空白图检查会运行真实模型，预期输出 `detected hands: 0`。可在命令后改用 `--image .\自己的单手照片.jpg --expect 1`，若模型检出单手，应打印 21 个关键点及六个位置值。图片检查不会访问摄像头、SSH 或 CAN。

下载脚本从 Google 的 Hand Landmarker 存储地址取得模型，校验 SHA-256 `fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1`。如已取得同哈希的模型，可将它放在 `models/hand_landmarker.task`。下载脚本不会接受同名但校验不同的新版本。原包未对模型权重授予 MIT 许可，因此发布源码不附权重。见 [MediaPipe 包](https://pypi.org/project/mediapipe/1.0.1/) 和 [模型下载](https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task)。

## 摄像头预览和个人标定

~~~powershell
.\.venv\Scripts\dexhand-camera.exe --probe
.\.venv\Scripts\dexhand-camera.exe --headless --max-frames 20 --stats
.\.venv\Scripts\dexhand-calibrate.exe
~~~

前两个命令分别检查摄像头可读和有限帧推理，不发送动作。标定界面按 O/F/W/T/B 采集五组手势，默认保存到 `configs/camera_mapping_calibrated.yaml`。请从 01 目录运行，以使默认的 `configs` 和 `models` 相对路径生效。控制模式还需要个人映射、指定的板卡地址、SSH 身份和已核验的主机公钥、板侧可用的 can0 和 can-utils；须显式选择 `--control` 并在预览窗口按 A 才会发出动作，D 解除控制。相关选项见 `dexhand-camera.exe --help`。

## 证据边界

原项目 2026-09-25 实验记录载有 640×480 摄像头采样、五姿态标定和使用者观察到的实体跟随。本次复核仅验证安装包、离线模型/映射、模拟 SSH 与 CAN 数据及板端控制台的模拟路径。没有重新采集真实摄像头视频、测量实时帧率或实体延迟，也没有验证每个关节的机械角度。
