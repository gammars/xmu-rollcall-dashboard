# XMU 签到看板

这是一个面向厦门大学 TronClass/LNT 平台的桌面签到看板。项目已经精简为桌面应用核心代码，只保留登录、会话持久化、签到监控、课程签到总览、数字/雷达签到处理和 Windows EXE 打包链路。

> 本项目仅用于学习、研究和个人工具化实践。请遵守学校、课程和平台规则，账号风险与使用后果由使用者自行承担。

## 功能

- 学号密码登录。
- 登录态 Cookie 持久化，启动后自动恢复会话。
- 退出登录并清除本地 Cookie。
- 轮询当前可用签到事件。
- 数字签到自动读取并显示签到码。
- 手动处理数字签到和雷达签到。
- 可选开启自动签到，当前仅处理数字签到和雷达签到。
- 按学年、第一学期、第二学期拉取课程签到记录。
- 课程签到总览支持“只显示未签”筛选。
- 不包含外部推送功能。

## 项目结构

```text
.
├── README.md
├── LICENSE
├── scripts
│   ├── build_dashboard_exe.bat
│   └── xmu_dashboard_launcher.py
└── xmu-rollcall-cli
    ├── README.md
    ├── pyproject.toml
    ├── requirements.txt
    └── xmu_rollcall
        ├── __init__.py
        ├── config.py
        ├── desktop.py
        ├── proxy_guard.py
        ├── utils.py
        └── verify.py
```

核心入口是 `xmu-rollcall-cli/xmu_rollcall/desktop.py`。

## 环境准备

推荐使用 Conda：

```powershell
conda create -y -n xmu-rollcall-dashboard python=3.11
conda run -n xmu-rollcall-dashboard python -m pip install -r .\xmu-rollcall-cli\requirements.txt pyinstaller
```

## 本地运行

在仓库根目录执行：

```powershell
$env:PYTHONPATH=(Resolve-Path .\xmu-rollcall-cli).Path
conda run -n xmu-rollcall-dashboard python -m xmu_rollcall.desktop
```

如果你已经把包安装到当前环境，也可以使用命令入口：

```powershell
conda run -n xmu-rollcall-dashboard xmu-dashboard
```

## 打包 EXE

直接运行：

```powershell
.\scripts\build_dashboard_exe.bat
```

成功后生成：

```text
dist\XMU-Rollcall-Dashboard.exe
```

这是 PyInstaller `--onefile --windowed` 模式产物。打包后的 exe 理论上可以脱离 Conda/Python 环境运行，但仍依赖目标电脑能访问厦大平台网络，并可能受到 Windows Defender 或系统代理设置影响。

## 使用流程

1. 打开 `dist\XMU-Rollcall-Dashboard.exe`。
2. 在“总览”页输入学号和密码，点击“登录”。
3. 后续打开应用会自动尝试恢复上次登录态。
4. 点击“启动监控”开始轮询当前签到。
5. 在“签到事件”页查看检测到的签到事件。
6. 需要提交时选择一条事件，点击“立即处理选中签到”。
7. 在“课程签到总览”页选择学年和学期，点击“刷新课程签到数据”。
8. 勾选“只显示未签”可以只看未签到记录。

## 自动签到逻辑

勾选“检测后自动签到（谨慎）”后：

- 数字签到：系统会尝试从接口读取签到码，并调用数字签到提交接口。
- 雷达签到：系统会调用雷达签到提交接口。
- 二维码签到：当前不自动处理，会标记为需要手动。

未勾选自动签到时，系统只记录事件和签到码，等待用户手动处理。

## 数据保存

本地配置默认保存在用户目录：

```text
%USERPROFILE%\.xmu_rollcall
```

其中包含账号配置和 Cookie 文件。也可以通过环境变量指定保存目录：

```powershell
$env:XMU_ROLLCALL_CONFIG_DIR="D:\path\to\config"
```

## 维护说明

- `desktop.py`：桌面 UI、后台轮询、课程签到总览。
- `config.py`：账号、配置、Cookie 路径管理。
- `utils.py`：请求重试、基础请求头、Cookie 保存/恢复。
- `verify.py`：数字签到码提取、数字签到提交、雷达签到提交。
- `proxy_guard.py`：启动时关闭系统代理干扰。
- `scripts/build_dashboard_exe.bat`：Windows 一键打包脚本。

## License

MIT
