# 六轴接入、控制与 PC 摄像头版

本目录可独立安装为 Python 包 `elf-dexhand`。没有设备时，可以列出关节姿态、预览六轴 CAN 报文、运行模拟控制台；另外可用普通图片验证手部模型。项目中的板端独立随动路线见 [02 视觉跟随](../02_视觉跟随/README.md)。

## 无硬件复现

在本目录打开 PowerShell。Windows 64 位 Python 3.12 是本次离线测试环境。安装命令只写入本目录下新建的虚拟环境。有网时 pip 从配置的软件源获取依赖。

~~~powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\dexhand.exe frame 食指 2000
.\.venv\Scripts\dexhand.exe poses
.\.venv\Scripts\dexhand.exe pose yeah --preview
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_library.py -v
.\.venv\Scripts\python.exe .\board\hand.py --sim
~~~

单关节预览应打印 `003#AAD007E809640300`。姿态清单有 `yeah`、`open_hand`、`fist`；姿态预览依次打印 ID 001 至 006 六行。在模拟控制台输入 `3 200`、`status`、`q`，可查看目标位形并退出。上述命令不联系控制板。程序里 `joint`、未带 `--preview` 的 `pose` 和未带 `--sim` 的板端入口用于真实硬件；区分它们和预览模式。

## 代码与依赖

| 位置 | 用途 |
|---|---|
| `src/elf_dexhand/protocol.py`、`poses.py`、`data/default_poses.yaml` | 将关节名和目标位置编码为 CAN 帧，检查关节范围，加载六轴 YAML 姿态。基础依赖为 PyYAML。 |
| `connection.py`、`controller.py`、`cli.py`（在同一包目录） | 通过系统 OpenSSH 调用控制板上的 can-utils，支持就绪检查、单关节和整手姿态；地址由使用者设置。 |
| `board/hand.py` | 含独立 `--sim` 模式的板端控制台，支持设置目标、显示状态及动作序列。板端实机方式依赖 Linux AF_CAN 及系统 Python 的 socket 模块。 |
| `vision_mapping.py`、`calibration.py`、`data/vision_mapping.yaml` | 处理 21 个三维手部关键点，映射拇指两个方向和四指弯曲，进行限幅/滤波，按五种手势进行个人标定。 |
| `camera_app.py`、`calibration_app.py`、`persistent.py`、`streaming.py` | OpenCV 采集、MediaPipe 推理和标定界面；持续 SSH 会话将六轴位置帧流送往板端 canplayer。只安装基础包就能使用上表前三项，视觉流程另装 vision 依赖。 |
| `configs/` | 通用预览由包内 `data/vision_mapping.yaml` 提供。`camera_mapping_training_four_fingers.yaml` 是拇指固定、四指行程收窄的历史练习配置。它们不是新设备的个人标定。 |
| `scripts/download_hand_model.ps1`、`tests/` | 官方模型下载与 SHA-256 校验；单元测试及不占用摄像头的本地图像检查。 |
| `pyproject.toml`、`LICENSE`、`NOTICE.md` | 构建依赖、命令行入口与数据文件声明，原仓库的许可及作者声明。源码主体由 Getusol 以 MIT 授权，原文随附。模型和依赖由各自权利人提供。 |

PC 摄像头的模型下载、校准、软件与实体证据见 [PC 摄像头说明](docs/experiment-18-camera-follow.md)。实机操作前请阅读 [原仓库 NOTICE](NOTICE.md)。当前位置命令为开环控制；实际反馈、端到端跟随误差和通信中断时的机械状态需要在对应设备上检验。

## 发布范围

使用原仓库 `src` 中的代码生成安装包，数据目录包含姿态与视觉映射两个 YAML。本包保留假设备测试、原模型下载脚本和可模拟的板端入口。从源代码重新构建即可得到包括视觉模块的 wheel。旧映射示例 `my_camera_mapping.yaml` 的拇指字段已不能被本版解析，发布目录选用通过校验的练习配置。个人现场标定、旧虚拟环境、既有 wheel、系统驱动脚本和私人连接记录留于原始工作区。发布新增中文说明及 `tests/verify_image.py` 图像验收脚本。模拟 SSH 测试使用文档保留地址。团队控制算法与协议实现沿用原文件。

## 本次离线复现结果

| 检查 | 结果 |
|---|---|
| 打包及基础安装 | 用 setuptools 从发布副本构建 wheel 并装入新建虚拟环境，检查两个 YAML、视觉模块、LICENSE 与 NOTICE 均随 wheel 打包。基础环境只使用包及 PyYAML 6.0.1，9 项基础测试通过。三个本地 CLI 示例产生预期帧和姿态，板端控制台模拟模式接受食指目标 200 并正常退出。 |
| 视觉与数据流 | Python 3.12.4、MediaPipe 1.0.1、Paramiko 4.0.0、OpenCV 4.12.0。26 项测试通过，覆盖协议/YAML、模拟连接异常、六轴发送节拍、关键点几何、标定、限速与过滤。两份通用包内 YAML 和随包四指练习配置均可读取。 |
| 真实模型、离线图片 | 原模型经 SHA-256 校验。空白图得到 0 只手；一张操作者托住模块的本地照片得到 1 只手、21 个三维点，按关节 1 至 6 输出 [1499, 301, 0, 869, 754, 0]。这只验证该样例的软件路径，未比较实际关节位置。 |
| 安装条件 | 受本机出网限制，这轮依赖取自已有安装或按发行包 RECORD 检查后单独复制的本地组件。没有启动原项目的旧虚拟环境，未在空电脑上联网执行完整的 vision 依赖下载。模型下载脚本的地址、校验值及本地权重已核对。 |

## 工作记录与实验报告

以下为 Word 公开副本。

- [ELF 6-DOF 灵巧手接入 —— 09_24 工作总结 (1)](工作记录与实验报告/ELF%206-DOF%20灵巧手接入%20——%2009_24%20工作总结%20%281%29.docx)（工作记录）
- [ELF 六自由度灵巧手_底层接入与控制验证实验报告](工作记录与实验报告/ELF%20六自由度灵巧手_底层接入与控制验证实验报告.docx)（实验报告）
