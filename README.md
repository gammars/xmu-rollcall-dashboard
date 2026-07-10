> Forked from [KrsMt-0113/XMU-Rollcall-Bot](https://github.com/KrsMt-0113/XMU-Rollcall-Bot)
> 课件下载实现参考[KrsMt-0113/XMUFD](https://github.com/KrsMt-0113/XMUFD)
# XMU 签到与课件看板

一个面向厦门大学 TronClass/LNT 的 Windows 桌面工具。程序使用 PySide6 构建浅色工业风格界面，提供签到监控、课程签到总览、课程课件查看与授权下载。

登录成功或自动恢复登录态后，程序会在后台刷新课程签到总览和课件课程列表，并预加载默认课程的课件。网络请求在后台线程执行，课件详情会并发拉取，不会阻塞界面。

> 本项目仅用于个人学习、研究和本人数据管理。请遵守学校、课程和平台规则。程序只应查询当前登录账号有权访问的数据，不应被用于绕过权限、访问他人信息或干扰平台服务。

## 开箱即用

打包后的 Windows EXE 会生成到本地 `dist` 目录。普通用户运行 EXE 即可使用，不需要单独安装 Python 或 PySide6。

开发者修改源码后运行：

```powershell
.\scripts\build_dashboard_exe.bat
```

即可在本地 `dist` 目录重新生成独立 EXE。`dist` 和 `build` 属于构建产物，默认不提交到 Git。

## 开发运行

项目使用 Conda 环境：

```powershell
conda run -n xmu-rollcall-dashboard python -m pip install -r .\xmu-rollcall-cli\requirements.txt pyinstaller
```

开发时启动：

```powershell
$env:PYTHONPATH=(Resolve-Path .\xmu-rollcall-cli).Path
conda run -n xmu-rollcall-dashboard python -m xmu_rollcall.desktop
```

## 主要功能

### 账号与会话

- 在“总览”页使用学号和密码登录。
- 保存当前账号配置和 TronClass Cookie。
- 再次启动时自动尝试恢复 TronClass 登录态。
- 支持退出登录并清除当前账号保存的 Cookie。
- 支持通过 `XMU_ROLLCALL_CONFIG_DIR` 修改本地配置目录。

### 签到监控

- 轮询当前账号可见的签到事件。
- 识别数字签到、雷达签到和二维码签到。
- 自动读取并显示数字签到码。
- 支持手动处理选中的数字签到或雷达签到。
- 可选开启“检测后自动签到”。
- 二维码签到不会自动处理，需要用户手动完成。
- 总览页直接展示完整签到事件表，可在同一页启动监控、停止监控、处理选中签到或标记跳过。

### 课程签到总览

- 拉取当前账号的课程列表和课程签到记录。
- 支持按学年、第一/第二/第三学期和时间范围筛选。
- 时间范围支持今天、本周、本学期，默认显示本学期。
- 按日期聚合展示签到记录。
- 默认按未签、未知、已签、无记录排序。
- 显示课程、签到时间、签到类型、签到状态和平台状态。
- 支持只显示未签到记录。

### 课程课件

- 从当前登录账号可见的课程列表中选择课程。
- 支持按学年、第一/第二/第三学期和课程筛选，交互与“课程签到”页保持一致。
- 读取课程发布的课件活动、章节、小节和附件信息。
- 课程列表保持平台返回顺序；课件展示按平台章节树顺序排列。
- 课件详情使用并发请求读取，以减少课程课件列表等待时间。
- 显示文件名、类型、大小、发布时间、处理状态和下载权限。
- 支持双击或点击按钮下载选中附件。
- 支持按住 Ctrl 多选课件后下载选中项。
- 支持批量下载当前课程内全部可下载附件。
- 对 `allow_download=false` 但脚本可下载的附件显示为“可下载（平台版权保护）”，并允许下载。
- 下载文件按课程保存到所选目录，重名文件不会覆盖已有文件。

## 界面页面

| 页面 | 作用 |
| --- | --- |
| 总览 | 登录、退出、启动或停止签到监控，查看所有签到事件，手动处理或跳过选中事件 |
| 课程签到 | 查看课程签到历史，按学年、学期和未签状态筛选 |
| 课程课件 | 按学年、学期和课程筛选课件，查看附件及下载权限，下载选中或全部可下载附件 |
| 策略 | 设置签到轮询间隔和自动处理开关 |
| 日志 | 查看登录、刷新、轮询和异常信息 |

## 实现说明

### 签到监控与数字签到码

- 监控页通过 `GET /api/radar/rollcalls` 读取当前账号可见的签到事件。
- 数字签到事件会先从监控结果中取得 `rollcall_id`。
- 程序随后请求 `GET /api/rollcall/{rollcall_id}/student_rollcalls`，并在返回数据中递归查找 `number_code` 字段。
- 手动处理或自动处理数字签到时，会提交到 `PUT /api/rollcall/{rollcall_id}/answer_number_rollcall`，请求体包含 `deviceId` 和 `numberCode`。

### 雷达签到位置

- 雷达签到提交接口是 `PUT /api/rollcall/{rollcall_id}/answer`，请求体包含经纬度、精度、设备 ID 等字段。
- 当前代码没有直接读取“老师位置”的接口。
- 如果平台返回距离信息，程序会用两个已知坐标提交后得到的 `distance` 进行圆交点估算，再尝试候选签到中心点。
- 因此这里更准确地说是“通过距离反馈估算雷达签到中心点”，不是直接获取老师地理位置。

### 课程课件读取

- 课程列表通过课程接口探测读取，课程下拉保持平台返回顺序。
- 课件活动通过 `GET /api/course/{course_id}/courseware-activities` 读取。
- 章节信息通过 `GET /api/courses/{course_id}/modules` 读取。
- 每个课件活动详情通过 `GET /api/activities/{activity_id}` 读取。详情请求会并发执行，但结果合并时按平台章节树顺序展示。
- `homework` 类型活动会在请求详情前过滤，不作为课件展示。

## Git 提交范围

建议提交这些内容：

- `README.md`、`LICENSE`、`.gitignore`
- `scripts/` 下的构建入口和启动脚本
- `xmu-rollcall-cli/` 下的源码、依赖声明和包配置
- `tests/` 下的测试代码

不要提交这些内容：

- `build/`：PyInstaller 中间产物，可重新生成
- `dist/`：本地打包 EXE，建议通过 Release 或网盘分发
- `.xmu_rollcall/`：本地账号配置和 Cookie
- `.runtime/`、`.agents/`：本地运行状态或工具目录
- `.pytest_cache/`、`__pycache__/`、`*.pyc`：缓存文件
- `venv/`、`.venv/`、`env/`、`.env/`：本地虚拟环境
- `*.spec`：本地 PyInstaller 规格文件，当前项目通过 `scripts/build_dashboard_exe.bat` 重新生成

## 项目结构

```text
xmu-rollcall-bot/
├── README.md
├── LICENSE
├── scripts/
│   ├── build_dashboard_exe.bat
│   └── xmu_dashboard_launcher.py
├── xmu-rollcall-cli/
│   ├── README.md
│   ├── pyproject.toml
│   ├── requirements.txt
│   └── xmu_rollcall/
│       ├── config.py
│       ├── courseware.py
│       ├── desktop.py
│       ├── desktop_qt/
│       │   ├── app.py
│       │   ├── core.py
│       │   └── theme.py
│       ├── proxy_guard.py
│       ├── utils.py
│       └── verify.py
└── tests/
    └── test_courseware.py
```

## 本地配置

默认配置目录为当前用户主目录下的：

```text
~\.xmu_rollcall
```

其中 `config.json` 保存账号和策略配置，`1.json`、`2.json` 等文件保存对应账号的 Cookie。该目录属于当前 Windows 用户，免安装 EXE 也可以正常读写。
