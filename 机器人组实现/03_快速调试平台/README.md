# 灵巧手快速调试平台

用本地浏览器调节六路驱动的位置、执行预设手势、查看反馈、录制动作序列，并以 JSON 或 Markdown 导出。页面左侧用二维示意图显示最近一次确认的位置。模拟模式用软件反馈完成同样的页面操作，一台电脑即可复现。

## 先运行模拟

以下命令从本目录执行。准备 Python 3.10 或更高版本。Windows PowerShell 示例使用虚拟环境中的解释器，不要求更改执行策略。

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r web\requirements.txt
.\.venv\Scripts\python -X utf8 web\app.py --simulate --port 8766
```

在浏览器打开 <http://127.0.0.1:8766>，点击连接。尝试调节食指并应用，观察目标、反馈与左侧姿态。再选择开始录制，执行两个动作，各自完成后结束并保存。页面可直接回放或导出记录。退出服务按 Ctrl+C。新产生的模拟数据位于 `web/data-simulation/`。

Linux 同样使用 `python3 -m venv .venv`，再以 `.venv/bin/python` 执行上述安装和启动命令。纯模拟只需 Flask；无需串口、CAN、板端程序或模型权重。

## 源码入口

| 文件 | 作用 |
| --- | --- |
| `web/app.py` | Flask 入口，提供本地页面与关节、手势、录制、标定、停止等 API，管理动作状态。 |
| `web/templates/index.html`、`web/static/` | 页面、样式、六路控制与二维手形，支持导出和回放。 |
| `web/device_connection.py`、`web/board_identity.py` | 读取本机设定的板端身份，核对开发板型号与 CAN 状态。 |
| `web/serial_console*.py` | 选择 Windows 或 Linux 的串口读写实现。模拟流程不会打开串口。 |
| `web/board-motion.py`、`board-motion.sh`、`board-query.sh` | 板上执行动作、反馈核对、持续保持与状态查询。正常使用由网页后端管理。 |
| `web/test_*.py`、`web/test_frontend_hold.cjs` | 离线后端、虚拟串口、板端控制逻辑与前端状态回归测试。 |

六路依次是拇指侧摆、拇指弯曲、食指、中指、无名指、小指。网页角度按两端行程线性换算。默认范围是参考角度，实物机械角度精度需另行标定。接入实机也仅控制手指驱动。

## 实机所需配置

适配对象为 MYiR STM32MP257x Linux 控制板、USB 调试串口、1 Mbps 经典 CAN 和配套六路灵巧手。板上需有 Python 3、shell、flock 及 can-utils 中的 canplayer、candump、cansend。应按现场板卡型号、布线、供电和协议核对。Windows 另装 `requirements-windows.txt` 中的 pywin32。实机运行需有权限访问串口与 CAN；USB 串口连接的页面流程不会通过 SSH 发送动作。

先在本地确定串口、授权登录账户及串口提示符中实际的主机名。以下示例从交互输入读取这三个值，不会将其写入发布文件。若设备显示密码提示，先用本地终端完成登录并释放串口。程序可识别已登录提示符或在无密码提示的情况下发送指定用户名；不自动填写密码。

```powershell
.\.venv\Scripts\python -m pip install -r requirements-windows.txt
$env:HANDUI_BOARD_USER = Read-Host '板端登录账户'
$env:HANDUI_BOARD_HOST = Read-Host '板端提示符中的主机名'
$boardPort = Read-Host '设备管理器中的串口名'
.\.venv\Scripts\python -X utf8 web\app.py --serial $boardPort --port 8787
```

Linux 可在当前终端用 `export` 设置同名两个变量，并将经确认的 `/dev/ttyACM*` 或 `/dev/ttyUSB*` 路径传入 `--serial`。页面从 <http://127.0.0.1:8787> 打开。连接操作会检查板型和 can0，并准备板端程序。选择动作后才会发送目标。板端已接收的动作或保持可在网页关闭后继续，软件“停止并张开”依赖串口和 CAN 通信。

## 离线测试与本次验证

```powershell
.\.venv\Scripts\python -m pip install -r web\requirements-dev.txt
.\.venv\Scripts\python -X utf8 -m pytest -q web
node web\test_frontend_hold.cjs
```

在独立 Windows 暂存副本验证，使用 Python 3.12.4、Flask 3.0.3、pytest 7.4.4 和 Node.js 24.14.0。77 项 Python 测试通过，2 项仅适用于 Linux 的 sysfs 测试跳过。前端保持状态回归通过。另实际启动了 `web/app.py --simulate` 本机服务，完成从加载网页、模拟连接、关节动作、录制保存、导出 JSON/Markdown 到回放的 HTTP 检查。未在现场板卡验证此发布副本及新配置参数。实机测试记录应区分历史工作版与当前副本。

## 与工作版的差异

Windows 维护版原有两个串口模块固定了设备提示符及登录账户，握手结果也带固定主机名；原测试使用了相同身份和现场日志片段。发布副本改用本机环境变量，将测试夹具改成虚构身份及示例日志，保留原有板型与 CAN 检查。模拟启动延迟载入串口依赖；不再从历史项目日志恢复设备姿态。新产生的本地会话、标定、录制和日志可在本机保存，本发布副本从空数据状态开始。


## 工作记录与实验报告

以下为 Word 公开副本。

- [ELF 六自由度灵巧手快速调试平台实验报告](工作记录与实验报告/ELF%20六自由度灵巧手快速调试平台实验报告.docx)（实验报告）
- [灵巧手快速调试平台](工作记录与实验报告/灵巧手快速调试平台.docx)（工作记录）
