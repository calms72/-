# 灵巧手实验资料包（Seeed N6 版）

本包配套七项实验。做板端视觉演示可直接从实验二、三或四选一个开始，三个工程都附有量化模型。要让灵巧手跟随人手，先用实验四验证视觉、用实验六验证 PLC，再按实验七联调。实验五可独立验证上位机到灵巧手的控制链路。

## 从 GitHub 获取后

本仓库保留企业实验的应用源码、STM32CubeIDE 工程、量化模型、一级引导文件和两个 CODESYS 工程。原包中的七份 Word 指导书、三个工程各自的 `Lib/` 和 `STM32Cube_FW_N6/`、PLC 的 `库/` 作为本地依赖。旧版 ThreadX 随企业原包按其许可使用。各组件的版权和许可见对应工程的 `Package_license.md` 与组件自带的许可文件。

在 Windows 上先解压企业提供的 `实验资料包(seeed_N6版).zip`，找到同时含有 `软件demo` 和 `指导书` 的目录。克隆仓库后在 PowerShell 中还原依赖，将最后一行的路径换成该目录的实际位置。

```powershell
git clone https://github.com/calms72/-.git seeed-n6
cd seeed-n6
.\restore-vendor.ps1 -Source 'D:\课程资料\实验资料包(seeed_N6版)'
```

脚本只补充缺少的文件，可重复运行。七份指导书还原到 `指导书/`，按下表编号打开。PLC 运行配置 `CODESYSControl.cfgmy` 含认证配置，由设备管理员在部署实验七时单独配置；还原脚本只复制上面列出的依赖目录。

## 实验导航

包内指导书的编号与下表一致。[软件工程](软件demo/实验前/)位于 `实验前` 目录，保留了教学用的待补全代码。表中最后一列列的是上机验收时应观察的现象。

| 实验指导书 | 从哪里开始 | 验收时看什么 |
|---|---|---|
| 一 · AI 训练服务器部署 | 在 Windows PC 上配置 Miniconda、PyTorch 和 Ultralytics；按 §1.3 准备数据、训练并导出模型 | 训练目录产生 `weights/best.pt`，转换后得到全整型量化的 `.tflite` |
| 二 · 物体检测 | [Detection 工程](软件demo/实验前/AI_Assistant_Basic_Project_Detection/)，模型为 `yolov8n_full_integer_quant.tflite` | 将可识别物体放入视野，LCD 在相应物体上叠加检测框 |
| 三 · 姿态检测 | [Pose 工程](软件demo/实验前/AI_Assistant_Basic_Project_Pose/)，模型为 `yolov8n_256_quant_pc_uf_pose_coco-st.tflite` | 人进入画面后，LCD 上的 17 个身体关键点及骨架随姿态变化 |
| 四 · 手部关键点检测 | [Hand Landmark 工程](软件demo/实验前/AI_Assistant_Basic_Project_Hand_Landmark/)，先定位手掌，再裁剪手部区域送入关键点模型 | 单手进入画面，LCD 显示 21 个手部关键点，屈伸手指时标记随之移动 |
| 五 · 上位机手部电机控制 | 在 PC 安装 `Rui_Hand_setup` 和 PEAK 驱动，通过 PCAN 连接灵巧手 | 各电机 ID 有角度回读和状态，执行单指或组合动作时回读随之变化 |
| 六 · PLC_MP257F 主板开发 | 用 CODESYS 打开 [Simple calculation.project](软件demo/实验前/plc/Simple%20calculation/Simple%20calculation.project) | 在线写入测试值后，可在监控窗口看到函数和功能块的计算结果 |
| 七 · 基于 PLC 的灵巧手闭环动作控制 | 用 CODESYS 打开 [485_CAN_4-transparent.project](软件demo/实验前/plc/DIGI+LEFT-6axis/485_CAN_4-transparent.project)，另备能输出关键点帧的 N6 固件 | PLC 收到完整帧，六路关节目标值随人手变化，灵巧手产生对应动作 |

企业原包另附实验二的 RAR，与展开后的 Detection 工程内容一致；仓库采用展开目录。

## 实验一 · 训练自己的模型

指导书示例在 Python 3.8 的 `yolov8` 环境安装与显卡匹配的 PyTorch，在下载的 Ultralytics 源码根目录执行 `pip install -e .`。先用 `yolo detect predict` 确认示例图片能生成预测结果，再用 `yolov8n.pt` 和 HomeObjects-3K 数据集训练。从本次训练输出目录选取 `weights/best.pt`，按 §1.3.3 在独立的 Python 3.10 环境中转换为量化 TFLite。训练框架、初始权重和数据集按指导书另行获取。换用自训模型时，实验二的类别表、输入尺寸和输出缓冲区也要与新模型对应。

## 实验二至四 · 从视觉工程到板上演示

1. **先看摄像头画面。** 准备 Seeed N6 板、摄像头、LCD 和 ST-LINK。三个工程的目标芯片均为 `STM32N657X0HxQ`。在 STM32CubeIDE 导入所选工程的 `STM32CubeIDE/.project`，保留上级目录中的 `Model/`、`Src/`、`Inc/` 和驱动。按该实验 §2.3.1 编译，断电将 BOOT1 置 High（Dev），再上电进入 Debug 并继续运行。LCD 出现实时画面即可验证采集和显示。
2. **生成模型代码与权重。** 按 §2.3.2 将 STEdgeAI Core 安装目录中的 `neural_art.json` 和 `stm32n6.mpool` 复制到该工程的 `Model/`，按书中示例配置内存池、执行对应的 `stedgeai generate` 命令。实验二选上表的物体检测模型，其指导书中有一处沿用了实验三的姿态模型文件名。实验四需分别生成 `palm_detector` 与 `hand_landmark`，各用一份内存池配置。将生成的模型 C 文件及 `*_ecblobs.h` 放回 `Model/`，保留同次生成的 `*_atonbuf.xSPI2.raw` 并按指导书改为 `.bin` 扩展名。
3. **补全推理并编译。** 按 §2.3.3 检查 `Inc/app_config.h`、`Inc/postprocess_conf.h` 和 `Src/app.c`，核对模型尺寸、缓冲区、推理调用和后处理。Detection 虽已有生成的 `network.c`，`Src/app.c` 中仍有空的推理调用；Pose 和 Hand Landmark 的模型 C 文件还有待生成的占位内容。实验四还要按书补齐 `Inc/ld.h` 的关键点数量和输入尺寸，再接通两阶段推理。
4. **写入外部 Flash 并验收。** 按 §2.3.4 用 STM32CubeProgrammer 附带的 `STM32_SigningTool_CLI` 为本次编译的应用添加签名头，得到 `*_signed.bin`。按 §2.3.5 在 Dev 模式通过 ST-LINK/SWD 连接，选用指导书指定的外部加载器 `MX66UW1G45G_STM32N6570-DK`，按下表写入同一工程的文件。完成后断电，将 BOOT1 置 Low（Flash Boot），重新上电并观察对应的检测结果。

| 写入内容 | 实验二 / 三 | 实验四 | 外部 Flash 起始地址 |
|---|---|---|---|
| 一级引导程序（随包） | `Binary/ai_assistant_fsbl.bin` | 同左 | `0x70000000` |
| 已签名的应用（编译后生成） | 当前工程的 `*_signed.bin` | 当前工程的 `*_signed.bin` | `0x70100000` |
| 第一份模型权重（生成后改扩展名） | `network_atonbuf.xSPI2.bin` | `palm_detector_atonbuf.xSPI2.bin` | `0x70380000` |
| 第二份模型权重 | — | `hand_landmark_atonbuf.xSPI2.bin` | `0x70580000` |

三个工程内的英文 README 沿用了 ST 参考工程的说明。其预编译 HEX 文件名与本包交付内容有差异；本包提供的启动文件是表中的 `ai_assistant_fsbl.bin`，应用和模型权重需按各自指导书生成。如果只有相机画面，先看 `Src/app.c` 是否已经调用模型，再核对烧入的权重与本次生成的模型代码是否配套。

## 实验五至七 · 从单机验证到联调

**实验五，上位机直连。** 指导书列出的 `Rui_Hand_setup.zip`、PEAK 驱动安装包及预设动作文件需另行取得。断电接好 USB-CAN、CAN 总线和灵巧手电源；在上位机选 `RY-H2` 和用户模式，按实物选择左右手，再选 **PCAN → 端口扫描 → 打开设备**。先确认角度回读和各 ID 状态有数据，再配置控制模式、速度和电流。做抓取时按指导书选择“位置速度电流控制模式”，从单指动作开始。

**实验六，PLC 独立验证。** 使用 CODESYS V3.5 SP21 或以上版本。在设备存储库安装 `plc/库/STMicroelectronics-Linux-ARM_64.devdesc.xml`，在库存储库安装 `plc/库/CAN API.library`。给主板接 24 V 电源并通过以太网连 PC。指导书示例为主板 `192.168.2.100`、PC `192.168.2.101/24`。扫描设备 `myd-ld25x-rt`，编译、登录并启动工程，用 `Ctrl+F7` 写入测试值。扫描不到时先核对网段和 `CODESYS Gateway V3` 服务。

**实验七，关键点驱动六个关节。** 链路为 **N6 摄像头 → 手部 21 点 → RS485 → MP257F PLC → CAN → 灵巧手**。指导书引用了带 `1_` 前缀的 `1_AI_Assistant_Basic_Project_Hand_Landmark` 发送版。此包仅收录实验四的基础工程，其中 `Src/main.c` 将控制台 USART1 配为 115200，未见实验七所需的关键点发送实现。做整链路验收前，需取得企业提供的发送版本，或按实验七的帧格式完成 N6 端适配。

1. 按实验七接线图连接串口转 RS485 模块、PLC 和 CAN 总线；断电接线。按 [PLC 工程附注](软件demo/实验前/plc/DIGI+LEFT-6axis/README.txt) 核对实物对应的串口号。
2. 打开表中的主 `.project`，在 CODESYS 内进入 `PLC_PRG`。按 §1.3.3 核对接收拼帧、角度计算、滤波映射及 CAN 发送限幅四处逻辑。
3. 先看接收。指导书的 N6 发送端使用 921600 bps，帧头为 `0x0D`、帧尾为 `0x0A`。在 PLC 监控 `g485RXed` 和 `CAL_START` 是否周期性变化。再缓慢屈伸手指，观察 `gFingerAngle` 和滤波后的 `gFingerAngle_RH`；最后核对 CAN 目标值和实际跟随动作。

## 另一个「指导书源码包」包含什么

企业另行提供的「指导书源码包」配套《灵巧手实验指导手册 V1.1》的 **STM32N647** 板卡路线。可在其 `Software_Package(n6与ai）/Projects/` 中按下表查找。两个包都讲到视觉算法，但目标芯片、板级驱动和外部 Flash 加载器不同，复现时应使用各自工程配套的引导程序和烧录配置。

| V1.1 手册章节 | 指导书源码包中的工程 | 与本包的关系 |
|---|---|---|
| 第 3–15 章 | 蜂鸣器、按键与外部中断、串口、基本定时器、RGB LCD、DMA、数字温度传感器、RS485、FDCAN、QMI8658A、IMX335 摄像头、触摸屏 | 属于板级外设实验，本包的七份指导书没有把这些项目单列为实验 |
| 第 17–19 章 | `AI_Applications/AI_YOLOv8n`、`AI_Pose`、`AI_Hand_Landmarks` | 主题分别对应本包实验二、三、四；使用 Seeed 设备时从上表的 Detection、Pose、Hand Landmark 工程进入 |

如需对照实验七的串口发送逻辑，另一包的 `AI_Applications/AI_Hand_Landmarks_N647_AI21_CACHE_DIAG/Appli/Core/Src/` 下，`main.c` 配置了 921600 bps 的 USART3，`ld.c` 打包 21 个关键点的三维坐标。移植到 Seeed 板时仍需适配串口和引脚，并与 PLC 接收端核对帧格式。

本说明依据包内七份指导书、源码和工程配置整理。板上显示、PLC 在线变量及灵巧手动作均为指导书的验收项目，本次整理未进行编译、烧录或实机联调。
