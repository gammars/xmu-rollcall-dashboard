## 2026-10-19 更新：畅课新签到接口（v4.3.0）

- 数字码和到课人数改由 `timetable_rollcalls` 获取，并按签到 ID 精确匹配，支持 `0026` 等前导零。
- 总览与课程签到页显示截止时间、到课人数、完整签到码和扩展数据状态。
- 雷达签到改用“地点库 + 课程绑定”，不再进行两点测距推算；每次只提交一次请求。
- 数字与雷达共用到课比例阈值；“开启策略自动签到”默认关闭且不跨重启保存。

## 2026-09-23 更新：自动恢复登录

- 启动时先读取并验证本地 Cookie；Cookie 缺失、损坏或验证失败时，使用已保存的账号密码自动重新登录。
- 重新登录成功并验证通过后更新本地 Cookie；仍失败或未保存完整账号密码时显示“未登录”，可手动登录。

## 2026-09-22 更新：达到签到阈值后再自动签到

为了避免在所有实际到课同学都未知签到码的情况下签到的尴尬，新增加此功能：

* 现双击课程可查看**课程签到名单**，并在总览页面显示课程签到人数比例

- 新增签到比例阈值自动签到：发现新签到后，每 **5 秒**检查一次签到人数比例，达到设定阈值后自动提交数字或雷达签到。
- 阈值默认 **10%**，可在“策略”页设置为 **1%–100%**。
- 在总览监控控制栏**最右侧**勾选“**开启策略自动签到**”即可启动监控并开启自动签到；打钩表示开启，取消勾选表示关闭。
- 自动签到默认关闭，停止监控或退出登录后自动关闭。关闭自动签到后，启用比例提醒时仍会在达到阈值后提醒手动处理。
- 已加入防重复提交和已结束签到检查；二维码签到仍需手动完成。

---

> Forked from [KrsMt-0113/XMU-Rollcall-Bot](https://github.com/KrsMt-0113/XMU-Rollcall-Bot)
> 课件下载实现参考 [KrsMt-0113/XMUFD](https://github.com/KrsMt-0113/XMUFD)

# XMU 畅课签到与课件看板

一个面向厦门大学 TronClass/LNT 的签到工具。项目提供 PySide6 桌面应用，以及基于 FastAPI 的本地 Web 控制台。

这个项目有两个使用方式：

- **开箱即用**：普通用户下载 Release 里的 Windows EXE，双击运行即可，不需要安装 Python、或 PySide6。
- **二次开发**：开发者可以直接修改源码，运行、测试和重新打包 EXE。

> 本项目仅用于个人学习、研究和本人数据管理。请遵守学校、课程和平台规则。程序只应查询当前登录账号有权访问的数据，不应被用于绕过权限、访问他人信息或干扰平台服务。

## 开箱即用

建议从 GitHub Releases 下载最新版本：

```text
XMU-Rollcall-Dashboard.exe
```

下载后直接双击运行。首次打开后，在“总览”页输入学号和密码登录。登录成功后，程序会自动：

- 恢复或保存当前账号的 TronClass Cookie。
- 刷新课程签到总览。
- 读取课程课件课程列表。
- 预加载默认课程的课件。

EXE 是独立打包文件，已经包含 Python 运行时和 PySide6 等依赖。目标电脑只需要：

- Windows 系统。
- 可以正常访问厦大统一认证和 TronClass/LNT。
- 当前平台接口与程序实现兼容。

## 主要功能

### 总览与签到监控

- 登录、退出登录和自动恢复登录态。
- 轮询当前账号可见的签到事件。
- 识别数字签到、雷达签到和二维码签到。
- 自动读取并显示数字签到码。
- 支持手动处理选中的数字签到或雷达签到。
- 支持把选中签到标记为跳过。
- 勾选总览监控控制栏右侧的“开启策略自动签到”会启动监控，每 5 秒检查签到比例，达到策略页设定的阈值后自动提交数字签到；雷达签到还要求课程已绑定地点（默认 10%，可设置 1%–100%）。
- 自动签到默认关闭，停止监控或退出登录后关闭；关闭自动签到开关后继续监控并仅提醒。已提交的请求不会撤回，提交失败后可手动重试。
- 二维码签到不会自动处理，需要用户手动完成。

### 课程签到

- 拉取当前账号的课程列表和课程签到记录。
- 支持按学年、第一/第二/第三学期筛选。
- 支持时间范围筛选：今天、本周、本学期。
- 默认显示本学期。
- 按日期聚合展示签到记录。
- 状态顺序为：未签、未知、已签、无记录。
- 支持只显示未签到记录。

### 课程课件

- 支持按学年、学期和课程筛选。
- 读取课程发布的课件活动、章节、小节和附件信息。
- 课程下拉保持平台课程接口返回顺序。
- 课件展示按平台章节树顺序排列，而不是简单使用课件活动接口返回顺序。
- 课件详情并发拉取，减少等待时间。
- 显示文件名、类型、大小、发布时间、处理状态和下载权限。
- 支持 Ctrl 多选后批量下载选中课件。
- 支持下载当前课程全部可下载课件。
- 对 `allow_download=false` 但脚本可下载的附件显示为“可下载（平台版权保护）”，并允许下载。
- 下载文件按课程保存到所选目录，重名文件不会覆盖已有文件。

## 页面说明

| 页面 | 作用 |
| --- | --- |
| 总览 | 登录、退出、启动或停止签到监控，查看签到事件，处理或跳过选中事件 |
| 课程签到 | 查看课程签到历史，按学年、学期、时间范围和未签状态筛选 |
| 课程课件 | 按学年、学期和课程筛选课件，查看附件状态并下载 |
| 策略 | 设置轮询间隔、签到比例提醒和人数比例阈值 |
| 日志 | 查看登录、刷新、轮询和异常信息 |

## 二次开发

### 环境准备

本人使用 Conda 环境（推荐），环境名：

```text
xmu-rollcall-dashboard
```

安装依赖：

```powershell
conda run -n xmu-rollcall-dashboard python -m pip install -r .\xmu-rollcall-cli\requirements.txt pyinstaller
```

### 从源码运行

在仓库根目录执行：

```powershell
$env:PYTHONPATH=(Resolve-Path .\xmu-rollcall-cli).Path
conda run -n xmu-rollcall-dashboard python -m xmu_rollcall.desktop
```

### 运行本地 Web 控制台

Web 版只包含登录、会话恢复、签到监听、比例策略和雷达课程地点绑定，不包含课件功能。自动提交在每次启动时均默认关闭。

在仓库根目录执行：

```powershell
.\scripts\start_web.ps1
```

浏览器打开 [http://127.0.0.1:8765](http://127.0.0.1:8765)。开发时如需修改前端后自动重载：

```powershell
.\scripts\start_web.ps1 -Reload
```

同一局域网内用手机访问时，必须先设置 Web 访问密码，然后监听所有网卡：

```powershell
$env:XMU_ROLLCALL_WEB_PASSWORD = "请换成足够长的密码"
.\scripts\start_web.ps1 -HostAddress 0.0.0.0
```

再使用电脑的局域网 IPv4 地址访问 `http://电脑IP:8765`。浏览器会弹出 HTTP Basic 登录框，用户名可任意填写，密码使用上面设置的值。不要直接把该 HTTP 服务暴露到公网；正式服务器部署应由 HTTPS 反向代理终止 TLS，并继续设置访问密码。服务只支持单进程运行，不要给 Uvicorn 配置多个 worker。

### 测试与编译检查

```powershell
conda run -n xmu-rollcall-dashboard python -m pytest tests
conda run -n xmu-rollcall-dashboard python -m compileall xmu-rollcall-cli\xmu_rollcall
```

### 打包 EXE

```powershell
.\scripts\build_dashboard_exe.bat
```

打包完成后生成：

```text
dist\XMU-Rollcall-Dashboard.exe
```

`dist/` 是本地构建产物，不建议提交到 Git。发布时应把 EXE 上传到 GitHub Release 附件。

构建脚本会隔离 DLL 搜索路径，避免其他软件的 ICU/Qt 依赖混入，并自动运行生成的 EXE 检查主窗口及自动签到按钮。检查不会读取登录信息或访问平台，结果保存在 `build/dashboard-smoke-test.json`；检查失败时构建命令返回失败。

## 发布 Release

推荐流程：

1. 修改版本号：

```python
# xmu-rollcall-cli/xmu_rollcall/__init__.py
__version__ = "x.y.z"
```

2. 运行测试和编译检查。
3. 执行 `.\scripts\build_dashboard_exe.bat`。
4. 计算 EXE 校验值：

```powershell
Get-FileHash .\dist\XMU-Rollcall-Dashboard.exe -Algorithm SHA256
```

5. 提交源码变更并打 tag：

```powershell
git add README.md .gitignore scripts xmu-rollcall-cli tests
git commit -m "Release vx.y.z"
git tag -a vx.y.z -m "vx.y.z"
git push origin HEAD
git push origin vx.y.z
```

6. 在 GitHub Releases 新建 Release，上传：

```text
dist\XMU-Rollcall-Dashboard.exe
```

也可以使用 GitHub CLI：

```powershell
gh release create vx.y.z .\dist\XMU-Rollcall-Dashboard.exe --title "vx.y.z" --notes "Release notes"
```

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

## 接口实现说明

### 数字签到码

- 通过 `GET /api/radar/rollcalls` 读取当前账号可见的签到事件。
- 从数字签到事件中取得 `rollcall_id`。
- 请求 `GET /api/rollcall/{rollcall_id}` 获取课程 ID 和签到时间，并换算为 UTC 日期。
- 请求 `GET /api/timetable_rollcalls?course_ids=...&rollcall_date=...`，按 `id == rollcall_id` 精确匹配。
- `number_code` 始终按字符串保存；旧 `student_rollcalls` 接口只在双击详情时用于读取姓名等完整名单。
- 提交数字签到时调用 `PUT /api/rollcall/{rollcall_id}/answer_number_rollcall`。

### 雷达签到

- 雷达签到提交接口是 `PUT /api/rollcall/{rollcall_id}/answer`。
- 请求体包含经纬度、精度和设备 ID 等字段。
- 在策略页维护地点库，并按平台 `course_id` 为课程绑定地点。
- 程序只提交用户配置的经纬度和精度，PUT 请求不会自动重试；未绑定地点时不会猜测位置或提交。

### 课程课件

- 课程列表通过课程接口探测读取。
- 课件活动通过 `GET /api/course/{course_id}/courseware-activities` 读取。
- 章节树通过 `GET /api/courses/{course_id}/modules` 读取。
- 活动详情通过 `GET /api/activities/{activity_id}` 并发读取。
- 最终展示顺序按章节树顺序合并。
- `homework` 类型活动会在请求详情前过滤，不作为课件展示。


## 本地配置

默认配置目录为当前 Windows 用户主目录下：

```text
~\.xmu_rollcall
```

其中：

- `config.json` 保存账号和策略配置。
- `1.json`、`2.json` 等文件保存对应账号的 Cookie。

可以通过环境变量修改配置目录：

```powershell
$env:XMU_ROLLCALL_CONFIG_DIR="D:\path\to\config"
```
