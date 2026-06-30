# XMU 签到与成绩看板

一个面向厦门大学 TronClass/LNT 与教务服务平台的 Windows 桌面工具。程序使用同一组学号和密码完成登录，在一个 Tkinter 界面中提供签到监控、课程签到总览和本人教务成绩查询。

登录成功或自动恢复登录态后，程序会在后台自动刷新课程签到总览和成绩。网络请求不会阻塞界面；用户切换到相应页面时，通常可以直接看到已经加载的数据。

## 开箱即用，同时保持开源

这个项目同时面向普通用户和希望继续开发的使用者：

- **普通用户可以开箱即用**：仓库的 `dist` 目录已经提供打包好的 Windows EXE，不需要安装 Python、Conda 或任何依赖，直接运行即可。
- **开发者可以自由修改**：完整 Python 源码、依赖声明、测试和 PyInstaller 打包脚本都保留在仓库中，可以阅读实现、修改功能并重新生成 EXE。

### 普通用户

直接运行最新版本：

```text
dist\XMU-Rollcall-Dashboard-Grades-Auto.exe
```

打开程序后，在“总览”页输入学号和密码登录即可。登录完成后，程序会自动在后台加载课程签到总览和本人已公布成绩。

EXE 已经包含 Python 解释器、Tkinter 和运行依赖。目标电脑只需要使用 Windows，并能够正常访问厦大相关平台。

### 开发者

开发者可以直接查看 `xmu-rollcall-cli/xmu_rollcall` 下的完整实现。修改源码后运行：

```powershell
.\scripts\build_dashboard_exe.bat
```

即可在 `dist` 目录重新生成可分发的独立 EXE。项目因此既可以作为普通桌面软件使用，也可以作为厦大统一认证、TronClass 接口、教务成绩查询、Tkinter 异步界面和 PyInstaller 打包的学习与二次开发项目。

> 本项目仅用于个人学习、研究和本人数据管理。请遵守学校、课程和平台规则。程序只应查询当前登录账号有权访问的数据，不应被用于绕过权限、访问他人信息或干扰平台服务。

## 主要功能

### 账号与会话

- 在“总览”页使用学号和密码登录。
- 保存当前账号配置和 TronClass Cookie。
- 再次启动时自动尝试恢复 TronClass 登录态。
- 支持退出登录并清除当前账号保存的 Cookie。
- 支持通过环境变量修改本地配置目录。

### 签到监控

- 轮询当前账号可见的签到事件。
- 识别数字签到、雷达签到和二维码签到。
- 自动读取并显示数字签到码。
- 支持手动处理选中的数字签到或雷达签到。
- 可选开启“检测后自动签到”。
- 二维码签到不会自动处理，需要用户手动完成。

### 课程签到总览

- 拉取当前账号的课程列表和课程签到记录。
- 支持按学年及第一、第二学期筛选。
- 显示课程、签到时间、签到类型、签到状态和平台状态。
- 支持只显示未签到记录。
- 登录成功后自动在后台刷新，也可以手动刷新。

### 成绩查询

- 使用首页保存的同一组账号密码登录教务服务平台。
- 只查询当前登录账号本人的已公布成绩。
- 显示学期、课程名称、课程号、学分、成绩、绩点、课程性质和考核方式。
- 根据接口返回的非零系数展示平时、期中、期末、实验及其他成绩构成。
- 查询完成后默认选择最新有成绩的学期。
- 支持在下拉框中切换其他学期或查看全部成绩。
- 登录成功或自动恢复后自动在后台查询，也可以手动刷新。

## 界面页面

| 页面 | 作用 |
| --- | --- |
| 总览 | 登录、退出、启动或停止签到监控，查看最近事件和运行状态 |
| 签到事件 | 查看所有检测到的签到，手动处理或跳过选中事件 |
| 课程签到总览 | 查看课程签到历史，按学年、学期和未签状态筛选 |
| 成绩查询 | 查看本人教务成绩，按学期筛选并查看成绩构成 |
| 策略 | 设置签到轮询间隔和自动处理开关 |
| 日志 | 查看登录、刷新、轮询和异常信息 |

## 项目结构

```text
xmu-rollcall-bot/
├── README.md
├── LICENSE
├── .gitignore
├── scripts/
│   ├── build_dashboard_exe.bat
│   └── xmu_dashboard_launcher.py
├── xmu-rollcall-cli/
│   ├── README.md
│   ├── pyproject.toml
│   ├── requirements.txt
│   └── xmu_rollcall/
│       ├── __init__.py
│       ├── config.py
│       ├── desktop.py
│       ├── jw_grades.py
│       ├── proxy_guard.py
│       ├── utils.py
│       └── verify.py
├── tests/
│   └── test_jw_grades.py
├── build/                         # PyInstaller 中间产物，可重新生成
└── dist/                          # 打包后的 EXE
```

### 核心模块

#### `desktop.py`

桌面程序主模块，负责：

- 创建 Tkinter 窗口、标签页、表格和状态栏。
- 接收用户登录、刷新、筛选和签到操作。
- 启动登录、轮询、签到总览和成绩查询后台线程。
- 使用线程安全队列把后台结果传回 Tkinter 主线程。
- 在登录成功事件后自动触发签到总览和成绩查询。
- 更新表格、摘要、日志和错误状态。

所有 Tkinter 控件都由主线程更新。后台线程只执行网络请求，并将结果写入 `queue.Queue`，避免网络延迟导致窗口卡死。

#### `config.py`

负责本地配置和账号管理：

- 计算配置目录。
- 保存和读取 `config.json`。
- 管理多个账号及当前账号。
- 保存监控策略。
- 生成每个账号对应的 Cookie 文件路径。

#### `utils.py`

提供 TronClass 通用能力：

- 平台基础 URL 和请求头。
- 带退避延迟的请求重试。
- Cookie 保存与恢复。
- 通过 `/api/profile` 检查登录态。

#### `proxy_guard.py`

在程序启动时关闭 `requests.Session` 对系统代理环境变量的继承，避免错误代理导致厦大平台请求失败。

#### `verify.py`

负责签到相关处理：

- 从嵌套响应中提取数字签到码。
- 提交数字签到结果。
- 提交雷达签到结果。

二维码签到不在该模块中自动处理。

#### `jw_grades.py`

负责教务服务平台登录与成绩查询：

- 访问厦大统一身份认证页面。
- 解析认证表单并加密密码。
- 建立教务门户会话。
- 打开当前账号有权限使用的“成绩查询”应用。
- 请求学生成绩接口。
- 将平台字段归一化为 `GradeRecord`。
- 整理成绩构成系数供界面显示。

#### `xmu_dashboard_launcher.py`

PyInstaller 的最小启动入口，只负责导入并调用：

```python
from xmu_rollcall.desktop import main
```

#### `build_dashboard_exe.bat`

Windows 一键打包脚本。它指定 Conda 环境、应用名称、源码搜索路径和 PyInstaller 参数。

## 工作原理

### 总体数据流

```text
用户操作 / 自动恢复
        │
        ▼
Tkinter 主线程
        │ 创建后台线程
        ├───────────────┬──────────────────┐
        ▼               ▼                  ▼
TronClass 登录/签到   课程签到总览       教务成绩查询
        │               │                  │
        └───────────────┴──────────────────┘
                        │
                        ▼
                   queue.Queue
                        │
                        ▼
              主线程更新表格和日志
```

### TronClass 登录与恢复

1. 用户在首页输入学号和密码。
2. `xmulogin(type=3)` 完成 TronClass 登录并返回 `requests.Session`。
3. 程序访问 `/api/profile` 获取姓名并验证会话。
4. Cookie 保存到当前账号对应的 JSON 文件。
5. 下次启动时读取 Cookie，并再次访问 `/api/profile` 验证。
6. Cookie 有效则产生与手动登录相同的 `login_success` 事件。

因此，手动登录和自动恢复后续走的是同一套自动刷新流程。

### 登录后的自动刷新

主线程收到 `login_success` 后会立即启动两个互不阻塞的后台任务：

1. 刷新当前学年和默认学期范围内的课程签到总览。
2. 登录教务平台并查询全部已公布成绩。

自动刷新失败时只更新页面状态和日志，不弹出错误窗口。用户手动点击刷新时，如果失败则会显示错误对话框。

程序使用刷新中标记避免同一功能在前一次请求尚未结束时重复发起并发请求。

### 签到监控

1. 用户点击“启动监控”。
2. `MonitorWorker` 按设置的间隔轮询当前签到接口。
3. 新签到通过 ID 去重后写入队列。
4. 主线程把签到添加到“总览”和“签到事件”表格。
5. 数字签到会额外在后台读取签到码。
6. 未开启自动签到时，程序只显示事件并等待用户操作。

### 成绩查询

TronClass 和教务平台属于不同系统，Cookie 不能直接共用。因此成绩查询会使用保存的同一组账号密码建立独立教务会话：

1. 登录统一身份认证系统。
2. 初始化教务门户会话及路由 Cookie。
3. 打开成绩查询应用，建立应用级会话。
4. 请求当前学生成绩接口。
5. 解析平台返回的课程和成绩字段。
6. 将结果发送回主线程。
7. 根据学期代码选中最新有成绩的学期。

如果学校调整认证流程、应用编号或接口字段，`jw_grades.py` 可能需要同步更新。

## 环境准备

推荐使用独立 Conda 环境：

```powershell
conda create -y -n xmu-rollcall-dashboard python=3.11
conda run -n xmu-rollcall-dashboard python -m pip install -r .\xmu-rollcall-cli\requirements.txt pyinstaller
```

主要依赖：

| 依赖 | 用途 |
| --- | --- |
| Python 3.11+ | 程序运行环境 |
| requests | HTTP 会话与接口请求 |
| pycryptodome | 统一认证密码加密 |
| xmulogin | 厦大统一认证和 TronClass 登录 |
| Tkinter | Windows 桌面界面，通常随 Python/Conda 提供 |
| PyInstaller | 生成 Windows EXE，仅打包时需要 |

## 从源码运行

在仓库根目录执行：

```powershell
$env:PYTHONPATH=(Resolve-Path .\xmu-rollcall-cli).Path
conda run -n xmu-rollcall-dashboard python -m xmu_rollcall.desktop
```

如果已经安装本项目，也可以使用命令入口：

```powershell
conda run -n xmu-rollcall-dashboard xmu-dashboard
```

## 使用方法

### 首次登录

1. 启动程序。
2. 在“总览”页输入学号和密码。
3. 点击“登录”。
4. 等待状态变为“已登录”。
5. 程序会自动刷新签到总览和成绩。

### 查看课程签到

1. 打开“课程签到总览”。
2. 查看自动加载的记录。
3. 需要查询其他范围时修改学年和学期。
4. 点击“刷新课程签到数据”。
5. 勾选“只显示未签”可过滤结果。

### 查看成绩

1. 打开“成绩查询”。
2. 等待自动查询完成。
3. 默认显示最新有成绩的学期。
4. 使用学期下拉框查看其他学期或全部成绩。
5. “成绩构成”仅展示教务接口实际返回的非零系数，不代表平台一定公开了各组成部分的具体分数。

### 启动签到监控

1. 登录成功后点击“启动监控”。
2. 在总览或“签到事件”页查看检测结果。
3. 未开启自动签到时，选择事件并点击“立即处理选中签到”。
4. 点击“停止监控”可结束轮询。

## 打包 Windows EXE

执行：

```powershell
.\scripts\build_dashboard_exe.bat
```

脚本执行的核心命令等价于：

```powershell
python -m PyInstaller `
  --noconfirm `
  --clean `
  --windowed `
  --onefile `
  --name XMU-Rollcall-Dashboard-Grades-Auto `
  --paths .\xmu-rollcall-cli `
  --collect-submodules xmu_rollcall `
  --collect-submodules xmulogin `
  .\scripts\xmu_dashboard_launcher.py
```

参数说明：

| 参数 | 作用 |
| --- | --- |
| `--noconfirm` | 自动覆盖同名构建输出 |
| `--clean` | 清理 PyInstaller 缓存后重新分析依赖 |
| `--windowed` | 不显示命令行窗口 |
| `--onefile` | 将程序和依赖打包成单个 EXE |
| `--name` | 设置最终程序文件名 |
| `--paths` | 将源码包目录加入模块搜索路径 |
| `--collect-submodules` | 显式收集动态导入或不易自动发现的子模块 |

成功后生成：

```text
dist\XMU-Rollcall-Dashboard-Grades-Auto.exe
```

打包后的 EXE 不要求目标电脑安装 Python 或 Conda，但仍需要：

- Windows 系统。
- 能正常访问厦大统一认证、TronClass 和教务平台的网络环境。
- 学校平台当前接口与程序实现兼容。

如果同名 EXE 正在运行，Windows 会锁定文件并导致覆盖失败。请先关闭旧程序，或修改 `APP_NAME` 生成另一个文件名。

## 哪些文件是必要的

### 从源码运行所需

- `xmu-rollcall-cli/xmu_rollcall/` 下全部 Python 模块。
- 已安装运行依赖的 Python 环境。
- 设置正确的 `PYTHONPATH`，或者安装项目包。

### 使用当前脚本重新打包所需

- `scripts/xmu_dashboard_launcher.py`
- `scripts/build_dashboard_exe.bat`
- `xmu-rollcall-cli/xmu_rollcall/` 下全部模块
- `xmu-rollcall-cli/requirements.txt`
- Conda 环境 `xmu-rollcall-dashboard`
- PyInstaller 及运行依赖

### 不影响运行的辅助文件

- `README.md`
- `LICENSE`
- `tests/`
- `xmu-rollcall-cli/README.md`
- 已经安装好依赖时的 `requirements.txt`
- 直接通过 `--paths` 打包时的 `pyproject.toml`

这些文件不进入核心运行流程，但建议在源码仓库中保留，便于维护、测试和重新部署。

### 可以重新生成或删除的产物

- `build/`
- `__pycache__/`
- `*.pyc`
- 自动生成的根目录 `*.spec`
- 不再需要的旧版 EXE

最终分发给普通用户时，只需要提供 `dist` 中最新的 EXE。

## 本地配置与安全

默认配置目录：

```text
%USERPROFILE%\.xmu_rollcall
```

典型内容：

```text
.xmu_rollcall/
├── config.json       # 账号、密码和当前账号配置
├── 1.json            # 账号 1 的 TronClass Cookie
└── 2.json            # 账号 2 的 TronClass Cookie
```

可以通过环境变量修改位置：

```powershell
$env:XMU_ROLLCALL_CONFIG_DIR="D:\path\to\config"
```

当前实现会在本地配置文件中保存密码和 Cookie，未使用操作系统凭据保险库加密。请注意：

- 不要把 `.xmu_rollcall` 提交到 Git。
- 不要把配置目录发送给他人。
- 不要在公共电脑上保存账号。
- 退出登录会删除当前账号的 Cookie，但账号配置仍可能保留在 `config.json` 中。

## 测试

运行成绩字段归一化测试：

```powershell
$env:PYTHONPATH=(Resolve-Path .\xmu-rollcall-cli).Path
conda run -n xmu-rollcall-dashboard python -m unittest discover -s tests -v
```

执行语法检查：

```powershell
conda run -n xmu-rollcall-dashboard python -m compileall -q .\xmu-rollcall-cli\xmu_rollcall .\tests
```

## 常见问题

### 登录失败

- 检查学号和密码是否正确。
- 检查统一认证是否新增验证码或二次验证。
- 尝试使用浏览器正常登录学校平台，确认账号没有被锁定。

### 自动恢复失败

- Cookie 可能已经过期，回到总览页重新输入密码登录。
- 确认配置目录中的 Cookie 文件没有损坏。

### 成绩查询失败但 TronClass 正常

- TronClass 与教务平台是两套独立会话，前者登录成功不代表后者一定可用。
- 检查教务服务平台是否正在维护。
- 如果认证或成绩应用入口发生变化，需要更新 `jw_grades.py`。

### 打包时提示拒绝访问 EXE

- 同名 EXE 仍在运行。
- 在任务管理器中关闭对应程序后重新打包。
- 或修改 `build_dashboard_exe.bat` 中的 `APP_NAME`。

### 打包成功但程序打不开

- 查看 `build/<应用名>/warn-<应用名>.txt`。
- 从源码运行一次，以便看到完整异常。
- 确认 PyInstaller 收集了 `xmu_rollcall`、`xmulogin`、Tkinter 和 Crypto 依赖。

## 维护建议

- 平台接口字段发生变化时，优先在归一化层兼容，不要把平台原始字段散落到 UI 中。
- 新网络操作应放入后台线程，禁止在 Tkinter 主线程中直接发请求。
- 后台线程不应直接更新控件，应通过队列交给主线程处理。
- 查询范围必须限制为当前登录账号有权访问的数据。
- 修改成绩解析后运行 `tests/test_jw_grades.py`。
- 发布新版前至少完成语法检查、单元测试、真实登录查询和 EXE 启动测试。

## License

MIT
