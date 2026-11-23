# CV 小组开发环境基线

以已复现的 **CPython 3.11.11、项目内 `.venv` 和当前依赖锁**作为全组的开发基线。新成员先用这套环境建立可比较的起点，PyCharm 的项目解释器和运行配置都指向项目 `.venv`。升级方案经过回归后可以替换团队基线。普通开发安装基础依赖，运行本地 YOLO 检测和跟踪的同学追加视觉依赖。

## 当前 PyCharm 环境

版本来自 PyCharm 登记的 CV 课程项目 `.venv` 的实际运行结果。当前机器是 Apple Silicon macOS。

| 项目 | 实测版本 | 团队用法 |
| --- | --- | --- |
| IDE | PyCharm **2025.3.3**，构建 `PY-253.31033.139` | 建议与当前版本一致。使用其他支持 Python 3.11 的 PyCharm 版本时，按下面的步骤绑定项目解释器。 |
| Python | **3.11.11** | 当前对照基线的补丁版本。仓库的 `.python-version` 记录这个版本。 |
| pip | **24.3.1** | 在各自的 `.venv` 内安装固定版本的 pip，再安装依赖。 |
| Web 与测试 | Flask **3.1.2**、Pillow **11.3.0**、pytest **8.4.2** | 全组安装 [requirements.txt](../requirements.txt)。SQLite 由 Python 标准库提供。 |
| SQLite | 当前 pyenv 构建为 **3.54.0**，复验的 uv 构建为 **3.47.1** | 随 Python 发行包提供。两种构建上的应用测试均通过。 |
| 本地 CV | Ultralytics **8.4.175**、PyTorch **2.14.1**、Torchvision **0.29.1**、OpenCV **5.0.0.93**、NumPy **2.4.6**、lap **0.5.13** | 目标检测、ByteTrack 或训练联调需要时安装 [requirements-cv.txt](../requirements-cv.txt)。 |
| 浏览器 | Chrome **158.0.8127.36** | 当前验证浏览器。组员使用可用的 Chrome，演示时记录实际版本。 |

视觉安装锁将 `setuptools` 固定为 **80.9.0**。当前课程 `.venv` 的版本为 44.1.1，而已装的 PyTorch 要求 ≥77.0.3。继续使用当前 pyenv 的 CPython 构建时，还实测发现其缺少 `_lzma`，会导致 Torchvision 导入失败。按下面的完整 Python 发行包和依赖锁复现，两处都在新环境中处理。

现有课程项目提供 Flask、SQLite、Pillow 及 YOLO26n 目标检测。心理对话 Demo 的 WebSocket、ASR、TTS 和扩类训练需要各模块负责人在代码接入时提交对应依赖、模型版本和复现命令。这份统一环境从当前可核实的基础开始。

## 新成员安装

1. 克隆小组仓库，在 PyCharm 打开克隆得到的项目根目录。所有命令都在该目录执行。
2. 安装 uv，然后使用它管理的完整 CPython 3.11.11 构建创建 `.venv`。macOS、Windows PowerShell 和 Linux 均使用同一组命令。已有完整 CPython 3.11.11 的同学也可以用其 `python -m venv .venv`，并执行步骤 3 的标准库检查。 [uv 安装说明](https://docs.astral.sh/uv/getting-started/installation/) · [uv 的 Python 版本管理](https://docs.astral.sh/uv/guides/install-python/) · [CPython 3.11.11](https://www.python.org/downloads/release/python-31111/)

   ```sh
   uv python install 3.11.11
   uv venv --managed-python --python 3.11.11 --seed .venv
   ```

3. 按系统安装基础依赖，并检查解释器与依赖。命令直接调用 `.venv`，校验和安装都落在同一个环境。

   **macOS / Linux**

   ```sh
   .venv/bin/python --version
   .venv/bin/python -c "import lzma, sqlite3, ssl"
   .venv/bin/python -m pip install pip==24.3.1
   .venv/bin/python -m pip install -r requirements.txt
   .venv/bin/python -m pip check
   ```

   **Windows PowerShell**

   ```powershell
   .\.venv\Scripts\python.exe --version
   .\.venv\Scripts\python.exe -c "import lzma, sqlite3, ssl"
   .\.venv\Scripts\python.exe -m pip install pip==24.3.1
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   .\.venv\Scripts\python.exe -m pip check
   ```

4. 在 PyCharm 打开 **Settings → Python → Interpreter**，添加当前项目下的已有虚拟环境。macOS 和 Linux 选 `.venv/bin/python`，Windows 选 `.venv\Scripts\python.exe`。在 PyCharm 的 **Python Console** 执行下面两行，核对 IDE 实际使用的路径和版本。 [PyCharm 解释器配置](https://www.jetbrains.com/help/pycharm/configuring-python-interpreter.html)

   ```python
   import sys
   print(sys.executable, sys.version.split()[0])
   ```

   路径应位于当前克隆目录的 `.venv` 内，版本应为 `3.11.11`。若项目里同时存在其他课程或历史环境，以当前目录的 `.venv` 为准。

## 本地视觉开发

需要实际调用本地 YOLO 或 ByteTrack 时，用上述同一个 `.venv` 安装 `requirements-cv.txt`。文件已经包含基础依赖。

```sh
.venv/bin/python -m pip install -r requirements-cv.txt
.venv/bin/python -m pip check
```

Windows 将命令中的解释器换成 `.\.venv\Scripts\python.exe`。Apple Silicon 遵循课程项目的设备判断，MPS 可用时可使用 MPS，CPU 路径可以用来做基础验证。NVIDIA 环境按驱动和操作系统选择官方 PyTorch wheel，保持锁中的 PyTorch 与 Torchvision 版本一致。PyTorch 的设备轮子和运行速度按机器记录。 [PyTorch 本机安装选项](https://pytorch.org/get-started/locally/)

现有课程检测和跟踪使用 `models/yolo26n.pt`。在持有课程源码的分支上按同一个权重版本测试，默认路径通过 `CVISION_YOLO_MODEL` 可以覆盖。当前权重的 SHA-256 为 `9b09cc8bf347f0fc8a5f7657480587f25db09b34bf33b0652110fb03a8ad4fef`。小组共享权重时记下来源和 SHA-256。训练负责人另记数据集版本、数据划分、随机种子和设备型号。

## 验收与源码位置

- **环境验收**。PyCharm 控制台显示当前 `.venv` 和 Python `3.11.11`。`python -m pip check` 输出 `No broken requirements found.`。需要视觉的成员再用自己的解释器确认 `import torch, torchvision, cv2, ultralytics` 能执行。
- **应用验收**。在含课程应用源码的分支，用项目 `.venv` 运行 `python run.py`，工作目录设置为仓库根目录。打开 [本机健康检查](http://127.0.0.1:5050/health)，再执行 `python -m pytest`。Python 控制台、终端和运行配置指向同一个解释器。
- **源码位置**。当前 PyCharm 课程目录已有 `app/`、`run.py` 和测试，作为本地环境复验的来源。小组 GitHub 仓库中的环境锁可以独立安装核对。Demo 源码合入后，在相应版本按应用验收步骤运行。 [小组仓库](https://github.com/XiaoBaner/Computer_Version)

在 Apple Silicon 上使用 uv 0.13.9 新建 CPython 3.11.11 环境。基础与视觉依赖均通过 `pip check`。课程源码副本的 68 项测试通过，并用当前 `yolo26n.pt` 完成一次 CPU 检测与 ByteTrack 冒烟测试。Windows x64 和 Linux x64 的二进制依赖均通过解析检查，安装、摄像头和加速设备的实际运行仍需在对应电脑上验证。

提交到 GitHub 的是 `.python-version`、依赖文件和这份说明。每台电脑自己创建 `.venv`。PyCharm 的本机设置、服务密钥和本地数据库保留在各自电脑。每次新增模块依赖，在同一个 PR 更新相应的依赖文件，并报告 `pip check` 和测试结果。

## 谁负责环境和升级验收

| 负责人 | 环境方面的交付 |
| --- | --- |
| **陈尧武，Flask 与总集成** | 维护 `.python-version`、依赖文件和启动说明。第 1 天发布基线，找一名未参与配置的同学从干净目录复现。升级时负责复跑原用例和更新 Git 版本记录。 |
| **施孔烨、王皓、陈跃权，各自模块** | 按基线跑本模块，把新增依赖版本、安装方法和最小验证随 PR 提交。语音负责人另提供 ASR/TTS 版本，前端负责人记录浏览器版本和声画测试。 |
| **王杰，实时 CV 接入** | 验证浏览器摄像头、运行时视觉依赖和模型推理。组内有 Windows 或 Linux 设备时，收集对应实机记录。 |
| **郭子毅，专职 CV 训练** | 单独维护训练环境记录，交数据版本、模型权重、运行设备和推理复现命令。王杰用交付物在运行环境验证。 |

**升级验收**。在相同源码、设备和用例下，对比基线与候选环境。依赖检查、既有应用测试和原来能运行的模型样例都要通过。记录至少一项可复验的改善，例如安装成功率、多支持一种平台、修复依赖冲突或更短的运行时间。课程工作台的 68 项测试与 YOLO26n 冒烟测试适用于复用该源码的改动。心理对话 Demo 以本项目自己留存的验收记录为对照。 [六人分工与 Demo 验收](demo-development-baseline.md#9-六人分工与七天交付)
