"""PySide6 desktop dashboard for XMU Rollcall Bot."""

from __future__ import annotations

import os
import sys
import threading
import time
import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from PySide6.QtCore import QObject, QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QAbstractItemView,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QStyle,
    QStyleOptionButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from xmulogin import xmulogin

from .. import __version__
from ..config import (
    add_account,
    get_all_accounts,
    get_account_by_id,
    get_cookies_path,
    get_current_account,
    get_rollcall_settings,
    load_config,
    save_config,
    set_current_account,
    set_rollcall_settings,
)
from ..courseware import (
    CourseSummary,
    CoursewareItem,
    download_courseware,
    fetch_courses,
    fetch_courseware,
    format_size,
    sanitize_filename,
)
from ..proxy_guard import disable_system_proxies
from ..utils import base_url, headers, load_session, save_session, verify_session
from ..verify import send_code, send_radar
from .core import (
    DEFAULT_POLL_INTERVAL_SECONDS,
    CourseRollcallRecord,
    MonitorWorker,
    RollcallRoster,
    RollcallEvent,
    current_academic_year_label,
    fetch_course_rollcall_records,
    fetch_number_code,
    fetch_rollcall_roster,
    format_duration,
    normalize_time,
)
from .theme import WINDOW_MIN_HEIGHT, WINDOW_MIN_WIDTH, stylesheet


disable_system_proxies()


class EventBus(QObject):
    event = Signal(object)


class CheckmarkCheckBox(QCheckBox):
    """Keep a visible check mark over the theme's filled checkbox indicator."""

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.isChecked():
            option = QStyleOptionButton()
            self.initStyleOption(option)
            rect = self.style().subElementRect(QStyle.SubElement.SE_CheckBoxIndicator, option, self)
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(QColor("white"), 2, Qt.PenStyle.SolidLine,
                                Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.drawLine(QPointF(rect.left() + 4, rect.center().y()),
                             QPointF(rect.left() + 7, rect.bottom() - 4))
            painter.drawLine(QPointF(rect.left() + 7, rect.bottom() - 4),
                             QPointF(rect.right() - 3, rect.top() + 4))


COURSE_STATUS_DISPLAY = {
    "未签到": "未签",
    "未签": "未签",
    "未知": "未知",
    "已签到": "已签",
    "已签": "已签",
    "无签到记录": "无记录",
    "无记录": "无记录",
}

COURSE_STATUS_PRIORITY = {
    "未签": 0,
    "未知": 1,
    "已签": 2,
    "无记录": 3,
}

COURSE_STATUS_COLORS = {
    "未签": ("#8a1c1c", "#fde8e8"),
    "未知": ("#8a5a00", "#fff4ce"),
    "已签": ("#166534", "#e7f6ec"),
    "无记录": ("#5f6b7a", "#eef2f6"),
}


class RollcallRosterDialog(QDialog):
    def __init__(self, course_title: str, rollcall_id: str, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(f"签到名单 - {course_title}")
        self.resize(1080, 620)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        title = QLabel(course_title)
        title.setObjectName("PageTitle")
        self.summary = QLabel(f"签到 ID：{rollcall_id}；正在读取名单...")
        self.summary.setObjectName("Subtle")
        layout.addWidget(title)
        layout.addWidget(self.summary)

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ("姓名", "学号", "班级", "院系", "签到状态", "签到时间", "距离", "状态详情")
        )
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        for index, width in enumerate((110, 130, 150, 190, 100, 165, 100, 180)):
            self.table.setColumnWidth(index, width)
        layout.addWidget(self.table, 1)

        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.close)
        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(close_button)
        layout.addLayout(button_row)

    def set_roster(self, roster: RollcallRoster):
        self.table.setRowCount(0)
        status_counts: dict[str, int] = {}
        for row, student in enumerate(roster.students):
            self.table.insertRow(row)
            status_counts[student.rollcall_status] = status_counts.get(student.rollcall_status, 0) + 1
            values = (
                student.name,
                student.user_no,
                student.klass,
                student.department,
                student.rollcall_status,
                student.rollcall_time,
                student.distance,
                student.status_detail,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column in (1, 4, 5, 6):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if column == 4:
                    status_key = COURSE_STATUS_DISPLAY.get(str(value), str(value))
                    foreground, background = COURSE_STATUS_COLORS.get(
                        status_key,
                        ("#8a5a00", "#fff4ce"),
                    )
                    item.setForeground(QColor(foreground))
                    item.setBackground(QColor(background))
                self.table.setItem(row, column, item)

        counts = "；".join(f"{status} {count}" for status, count in status_counts.items())
        self.summary.setText(
            f"签到 ID：{roster.rollcall_id}；共 {len(roster.students)} 人"
            + (f"；{counts}" if counts else "；暂无名单记录")
        )

    def set_error(self, message: str):
        self.summary.setText(f"名单读取失败：{message}")


class DashboardWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"XMU Rollcall Dashboard v{__version__}")
        self.resize(1280, 780)
        self.setMinimumSize(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT)

        self.bus = EventBus()
        self.bus.event.connect(self._handle_event)

        self.session = None
        self.account = None
        self.monitor_worker: MonitorWorker | None = None
        self.monitor_stop_event: threading.Event | None = None
        self.started_at: float | None = None

        self.event_sequence = 0
        self.events_by_id: dict[str, RollcallEvent] = {}
        self.event_order: list[str] = []
        self.course_records: list[CourseRollcallRecord] = []
        self.courseware_courses: list[CourseSummary] = []
        self.courseware_items: list[CoursewareItem] = []
        self.courseware_course_by_display: dict[str, CourseSummary] = {}
        self.roster_dialogs: dict[str, RollcallRosterDialog] = {}
        self.strategy_pending_ids: set[str] = set()
        self.strategy_checks_in_progress: set[str] = set()

        self.course_refresh_in_progress = False
        self.courseware_courses_refresh_in_progress = False
        self.courseware_refresh_in_progress = False
        self.courseware_download_in_progress = False

        self._build_ui()
        self._load_current_account_hint()

        self.runtime_timer = QTimer(self)
        self.runtime_timer.timeout.connect(self._tick_runtime)
        self.runtime_timer.start(1000)

        self.strategy_timer = QTimer(self)
        self.strategy_timer.timeout.connect(self._check_pending_strategies)
        self.strategy_timer.start(5000)

        QTimer.singleShot(400, self.auto_restore_current_session)

    def _build_ui(self):
        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)

        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(218)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(18, 18, 18, 18)
        sidebar_layout.setSpacing(14)

        brand = QLabel("XMU Rollcall Dashboard")
        brand.setObjectName("Brand")
        sidebar_layout.addWidget(brand)

        self.nav = QListWidget()
        self.nav.setObjectName("Navigation")
        for title in ("总览", "课程签到", "课程课件", "策略", "日志"):
            self.nav.addItem(QListWidgetItem(title))
        self.nav.currentRowChanged.connect(self._show_page)
        sidebar_layout.addWidget(self.nav, 1)

        self.sidebar_status = QLabel("未登录")
        self.sidebar_status.setObjectName("StatusWarn")
        sidebar_layout.addWidget(self.sidebar_status)

        self.pages = QStackedWidget()
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(22, 18, 22, 18)
        content_layout.setSpacing(14)

        self.page_title = QLabel("总览")
        self.page_title.setObjectName("PageTitle")
        self.top_status = QLabel("未登录")
        self.top_status.setObjectName("StatusWarn")
        title_row = QHBoxLayout()
        title_row.addWidget(self.page_title)
        title_row.addStretch(1)
        title_row.addWidget(self.top_status)
        content_layout.addLayout(title_row)
        content_layout.addWidget(self.pages, 1)

        layout.addWidget(sidebar)
        layout.addWidget(content, 1)

        self.pages.addWidget(self._build_overview_page())
        self.pages.addWidget(self._build_courses_page())
        self.pages.addWidget(self._build_courseware_page())
        self.pages.addWidget(self._build_settings_page())
        self.pages.addWidget(self._build_logs_page())
        self.nav.setCurrentRow(0)

    def _show_page(self, row: int):
        if row < 0:
            return
        self.pages.setCurrentIndex(row)
        item = self.nav.item(row)
        self.page_title.setText(item.text() if item else "")

    def _build_overview_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        top = QHBoxLayout()
        login_panel = self._panel("账号登录")
        login_layout = QGridLayout(login_panel)
        login_layout.setContentsMargins(16, 40, 16, 16)
        login_layout.setHorizontalSpacing(12)
        login_layout.setVerticalSpacing(10)

        self.username_input = QLineEdit()
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.Password)
        login_layout.addWidget(QLabel("学号"), 0, 0)
        login_layout.addWidget(self.username_input, 0, 1)
        login_layout.addWidget(QLabel("密码"), 1, 0)
        login_layout.addWidget(self.password_input, 1, 1)

        login_button = QPushButton("登录")
        login_button.setObjectName("PrimaryButton")
        login_button.clicked.connect(self.login)
        logout_button = QPushButton("退出登录")
        logout_button.clicked.connect(self.logout)
        buttons = QHBoxLayout()
        buttons.addWidget(login_button)
        buttons.addWidget(logout_button)
        buttons.addStretch(1)
        login_layout.addLayout(buttons, 2, 0, 1, 2)

        metrics_panel = self._panel("运行状态")
        metrics_layout = QGridLayout(metrics_panel)
        metrics_layout.setContentsMargins(16, 40, 16, 16)
        metrics_layout.setSpacing(10)
        self.metric_account = self._metric(metrics_layout, 0, 0, "当前账号", "未登录")
        self.metric_monitor = self._metric(metrics_layout, 0, 1, "监控状态", "未启动")
        self.metric_query_count = self._metric(metrics_layout, 1, 0, "轮询次数", "0")
        self.metric_runtime = self._metric(metrics_layout, 1, 1, "运行时长", "0s")
        self.metric_last_check = self._metric(metrics_layout, 2, 0, "最近检查", "-")
        self.metric_rollcall_count = self._metric(metrics_layout, 2, 1, "当前签到数", "0")
        self.metric_last_result = self._metric(metrics_layout, 3, 0, "最近结果", "-")

        top.addWidget(login_panel, 1)
        top.addWidget(metrics_panel, 1)
        layout.addLayout(top)

        control_panel = self._panel("监控控制")
        control_layout = QHBoxLayout(control_panel)
        control_layout.setContentsMargins(16, 40, 16, 16)
        start_button = QPushButton("启动监控")
        start_button.setObjectName("PrimaryButton")
        start_button.clicked.connect(self.start_monitor)
        stop_button = QPushButton("停止监控")
        stop_button.clicked.connect(self.stop_monitor)
        self.auto_answer_check = CheckmarkCheckBox("开启自动签到（谨慎）")
        self.auto_answer_check.toggled.connect(self._toggle_auto_answer)
        answer_button = QPushButton("立即处理选中签到")
        answer_button.setObjectName("PrimaryButton")
        answer_button.clicked.connect(self.answer_selected_rollcall)
        skip_button = QPushButton("标记为跳过")
        skip_button.clicked.connect(self.skip_selected_rollcall)
        control_layout.addWidget(start_button)
        control_layout.addWidget(stop_button)
        control_layout.addWidget(answer_button)
        control_layout.addWidget(skip_button)
        control_layout.addStretch(1)
        control_layout.addWidget(self.auto_answer_check)
        layout.addWidget(control_panel)

        self.events_table = self._make_table(
            ("时间", "课程", "发起人", "类型", "平台状态", "签到码", "处理状态", "详情"),
            (92, 240, 170, 100, 110, 90, 110, 320),
        )
        self.events_table.setToolTip("双击签到记录查看实时名单")
        self.events_table.cellDoubleClicked.connect(self._open_overview_roster)
        layout.addWidget(self.events_table, 1)
        return page

    def _build_courses_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        toolbar = self._panel("筛选")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(16, 40, 16, 16)
        self.academic_year_combo = QComboBox()
        self.academic_year_combo.addItems(self._academic_year_options())
        self.academic_year_combo.setCurrentText(current_academic_year_label())
        self.academic_year_combo.setFixedWidth(130)
        self.semester_combo = QComboBox()
        self.semester_combo.addItems(("全部", "第一学期", "第二学期", "第三学期"))
        self.semester_combo.setFixedWidth(110)
        self.time_range_combo = QComboBox()
        self.time_range_combo.addItems(("今天", "本周", "本学期"))
        self.time_range_combo.setCurrentText("本学期")
        self.time_range_combo.setFixedWidth(100)
        self.time_range_combo.currentTextChanged.connect(lambda _text: self._refresh_course_table())
        refresh_button = QPushButton("刷新课程签到数据")
        refresh_button.setObjectName("PrimaryButton")
        refresh_button.clicked.connect(self.refresh_course_rollcalls)
        self.only_unsigned_check = QCheckBox("只显示未签")
        self.only_unsigned_check.stateChanged.connect(self._refresh_course_table)
        self.course_summary = QLabel("登录后点击刷新，查看课程签到数据。")
        self.course_summary.setObjectName("Subtle")
        toolbar_layout.addWidget(QLabel("学年"))
        toolbar_layout.addWidget(self.academic_year_combo)
        toolbar_layout.addWidget(QLabel("学期"))
        toolbar_layout.addWidget(self.semester_combo)
        toolbar_layout.addWidget(QLabel("时间范围"))
        toolbar_layout.addWidget(self.time_range_combo)
        toolbar_layout.addWidget(refresh_button)
        toolbar_layout.addWidget(self.only_unsigned_check)
        toolbar_layout.addWidget(self.course_summary, 1)
        layout.addWidget(toolbar)

        self.course_table = self._make_table(
            ("日期", "课程", "签到时间", "类型", "状态", "平台状态", "签到 ID", "详情"),
            (130, 260, 160, 100, 90, 120, 100, 320),
        )
        self.course_table.setToolTip("双击签到记录查看实时名单")
        self.course_table.cellDoubleClicked.connect(self._open_course_roster)
        layout.addWidget(self.course_table, 1)
        return page

    def _build_courseware_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        toolbar = self._panel("筛选")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(16, 40, 16, 16)
        self.courseware_academic_year_combo = QComboBox()
        self.courseware_academic_year_combo.addItems(self._academic_year_options())
        self.courseware_academic_year_combo.setCurrentText(current_academic_year_label())
        self.courseware_academic_year_combo.setFixedWidth(130)
        self.courseware_academic_year_combo.currentTextChanged.connect(
            lambda _text: self._apply_courseware_course_filters()
        )
        self.courseware_semester_combo = QComboBox()
        self.courseware_semester_combo.addItems(("全部", "第一学期", "第二学期", "第三学期"))
        self.courseware_semester_combo.setFixedWidth(110)
        self.courseware_semester_combo.currentTextChanged.connect(lambda _text: self._apply_courseware_course_filters())
        self.courseware_combo = QComboBox()
        self.courseware_combo.setMinimumWidth(300)
        self.courseware_combo.currentTextChanged.connect(lambda _text: self.refresh_selected_courseware(silent=True))
        refresh_courses = QPushButton("刷新课程")
        refresh_courses.clicked.connect(self.refresh_courseware_courses)
        refresh_items = QPushButton("刷新课件")
        refresh_items.setObjectName("PrimaryButton")
        refresh_items.clicked.connect(self.refresh_selected_courseware)
        self.courseware_summary = QLabel("登录后自动读取课程和课件。")
        self.courseware_summary.setObjectName("Subtle")
        toolbar_layout.addWidget(QLabel("学年"))
        toolbar_layout.addWidget(self.courseware_academic_year_combo)
        toolbar_layout.addWidget(QLabel("学期"))
        toolbar_layout.addWidget(self.courseware_semester_combo)
        toolbar_layout.addWidget(QLabel("课程"))
        toolbar_layout.addWidget(self.courseware_combo)
        toolbar_layout.addWidget(refresh_courses)
        toolbar_layout.addWidget(refresh_items)
        toolbar_layout.addWidget(self.courseware_summary, 1)
        layout.addWidget(toolbar)

        self.courseware_table = self._make_table(
            ("章节", "小节", "课件活动", "文件名", "类型", "大小", "发布时间", "下载权限", "文件状态"),
            (120, 140, 260, 300, 90, 90, 160, 130, 110),
        )
        self.courseware_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.courseware_table.cellDoubleClicked.connect(lambda _row, _column: self.activate_selected_courseware())
        layout.addWidget(self.courseware_table, 1)

        actions = QHBoxLayout()
        selected_button = QPushButton("下载选中（Ctrl 多选）")
        selected_button.setObjectName("PrimaryButton")
        selected_button.setToolTip("按住 Ctrl 可选择多个课件后批量下载。")
        selected_button.clicked.connect(self.download_selected_courseware)
        all_button = QPushButton("下载全部可下载课件")
        all_button.clicked.connect(self.download_all_courseware)
        open_button = QPushButton("打开平台页面")
        open_button.clicked.connect(self.open_selected_courseware)
        choose_button = QPushButton("选择目录")
        choose_button.clicked.connect(self.choose_courseware_download_dir)
        self.courseware_download_dir = QLineEdit(str(Path.home() / "Downloads" / "XMU-Courseware"))
        actions.addWidget(selected_button)
        actions.addWidget(all_button)
        actions.addWidget(open_button)
        actions.addWidget(choose_button)
        actions.addWidget(self.courseware_download_dir, 1)
        layout.addLayout(actions)
        return page

    def _build_settings_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        panel = self._panel("监控策略")
        panel_layout = QGridLayout(panel)
        panel_layout.setContentsMargins(16, 40, 16, 16)
        self.poll_interval_spin = QSpinBox()
        self.poll_interval_spin.setRange(1, 30)
        self.poll_interval_spin.setValue(DEFAULT_POLL_INTERVAL_SECONDS)
        panel_layout.addWidget(QLabel("轮询间隔（秒）"), 0, 0)
        panel_layout.addWidget(self.poll_interval_spin, 0, 1)

        self.attendance_threshold_enabled_check = QCheckBox("启用签到比例提醒")
        self.attendance_threshold_enabled_check.setChecked(True)
        self.attendance_threshold_spin = QSpinBox()
        self.attendance_threshold_spin.setRange(1, 100)
        self.attendance_threshold_spin.setValue(10)
        self.attendance_threshold_spin.setSuffix(" %")
        self.attendance_threshold_enabled_check.toggled.connect(self.attendance_threshold_spin.setEnabled)
        panel_layout.addWidget(QLabel("人数达到比例"), 1, 0)
        threshold_row = QHBoxLayout()
        threshold_row.addWidget(self.attendance_threshold_enabled_check)
        threshold_row.addWidget(self.attendance_threshold_spin)
        threshold_row.addStretch(1)
        panel_layout.addLayout(threshold_row, 1, 1)

        save_button = QPushButton("保存策略")
        save_button.setObjectName("PrimaryButton")
        save_button.clicked.connect(self.save_rollcall_strategy)
        panel_layout.addWidget(save_button, 2, 1, Qt.AlignmentFlag.AlignLeft)

        note = QLabel("每 5 秒检查签到比例。总览中开启自动签到后，达到阈值直接提交；关闭时仅提醒。自动签到仅在本次登录期间有效，停止监控后关闭；二维码仍需手动扫码。")
        note.setObjectName("Subtle")
        note.setWordWrap(True)
        panel_layout.addWidget(note, 3, 0, 1, 2)
        layout.addWidget(panel)
        layout.addStretch(1)
        return page

    def _build_logs_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        layout.addWidget(self.log_text, 1)
        return page

    def _panel(self, title: str) -> QFrame:
        frame = QFrame()
        frame.setObjectName("Panel")
        label = QLabel(title, frame)
        label.setObjectName("Subtle")
        label.move(16, 12)
        return frame

    def _metric(self, layout: QGridLayout, row: int, column: int, label: str, value: str) -> QLabel:
        frame = QFrame()
        frame.setObjectName("Metric")
        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(12, 8, 12, 8)
        label_widget = QLabel(label)
        label_widget.setObjectName("MetricLabel")
        value_widget = QLabel(value)
        value_widget.setObjectName("MetricValue")
        frame_layout.addWidget(label_widget)
        frame_layout.addWidget(value_widget)
        layout.addWidget(frame, row, column)
        return value_widget

    def _academic_year_options(self) -> list[str]:
        current = current_academic_year_label()
        try:
            start_year = int(current.split("-", 1)[0])
        except (ValueError, IndexError):
            start_year = datetime.now().year
        return [f"{year}-{year + 1}" for year in range(start_year + 1, start_year - 6, -1)]

    def _make_table(self, headers: tuple[str, ...], widths: tuple[int, ...]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setAlternatingRowColors(True)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setStretchLastSection(True)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        for index, width in enumerate(widths):
            table.setColumnWidth(index, width)
        return table

    def _set_table_rows(self, table: QTableWidget, rows: list[tuple], row_ids: list[str] | None = None):
        table.setRowCount(0)
        for row_index, values in enumerate(rows):
            table.insertRow(row_index)
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if row_ids and column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, row_ids[row_index])
                if column in (0, 3, 4, 5, 6, 7, 8):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                table.setItem(row_index, column, item)

    def _run_thread(self, target, *args):
        threading.Thread(target=target, args=args, daemon=True).start()

    def _emit(self, event: tuple):
        self.bus.event.emit(event)

    def _load_current_account_hint(self):
        try:
            account = get_current_account(load_config())
            if account:
                self.username_input.setText(account.get("username", ""))
                self._apply_rollcall_settings(account)
        except Exception as exc:
            self.log(f"读取配置失败：{exc}")

    def _apply_rollcall_settings(self, account: dict):
        settings = get_rollcall_settings(account)
        self.attendance_threshold_enabled_check.setChecked(
            settings["attendance_threshold_enabled"]
        )
        self.attendance_threshold_spin.setValue(settings["attendance_threshold_percent"])

    def save_rollcall_strategy(self):
        if not self.account:
            QMessageBox.warning(self, "尚未登录", "请先登录后再保存签到策略。")
            return
        try:
            config = load_config()
            account = get_account_by_id(config, self.account.get("id"))
            if not account:
                raise RuntimeError("当前账号不在配置文件中")
            settings = get_rollcall_settings(account)
            settings.update({
                "attendance_threshold_enabled": self.attendance_threshold_enabled_check.isChecked(),
                "attendance_threshold_percent": self.attendance_threshold_spin.value(),
            })
            set_rollcall_settings(account, settings)
            save_config(config)
            self.account["rollcall_settings"] = account["rollcall_settings"].copy()
            if not settings["attendance_threshold_enabled"]:
                self.strategy_pending_ids.clear()
            self.log(f"签到比例提醒策略已保存：{self.attendance_threshold_spin.value()}%。")
            QMessageBox.information(self, "保存成功", "签到策略已保存到当前账号。")
        except Exception as exc:
            QMessageBox.critical(self, "保存失败", str(exc))

    def login(self):
        username = self.username_input.text().strip()
        password = self.password_input.text().strip()
        if not username or not password:
            QMessageBox.warning(self, "缺少信息", "请输入学号和密码。")
            return
        self._set_login_status("正在登录...", warn=True)
        self.log("开始登录。")
        self._run_thread(self._login_worker, username, password)

    def _login_worker(self, username: str, password: str):
        try:
            session = xmulogin(type=3, username=username, password=password)
            if not session:
                self._emit(("login_failed", "登录失败，请检查学号或密码。"))
                return

            try:
                profile = session.get(f"{base_url}/api/profile", headers=headers, timeout=15).json()
                name = profile.get("name") or username
            except Exception:
                name = username

            config = load_config()
            account = next((item for item in get_all_accounts(config) if item.get("username") == username), None)
            if account is None:
                account_id = add_account(config, username, password, name)
                account = next(item for item in get_all_accounts(config) if item.get("id") == account_id)
            else:
                account["password"] = password
                account["name"] = name

            set_current_account(config, account["id"])
            set_rollcall_settings(account, get_rollcall_settings(account))
            save_config(config)
            save_session(session, get_cookies_path(account["id"]))
            self._emit(("login_success", session, account))
        except Exception as exc:
            self._emit(("login_failed", str(exc)))

    def auto_restore_current_session(self):
        if self.session:
            return
        try:
            account = get_current_account(load_config())
            if not account:
                return
            cookies_path = get_cookies_path(account.get("id", 1))
            if not os.path.exists(cookies_path):
                return
            self._set_login_status("正在自动恢复会话...", warn=True)
            self.log("检测到上次登录会话，正在自动恢复。")
            self._run_thread(self._restore_worker, True)
        except Exception as exc:
            self.log(f"自动恢复检查失败：{exc}")

    def _restore_worker(self, silent=False):
        try:
            account = get_current_account(load_config())
            if not account:
                self._emit(("restore_failed", "没有可恢复的当前账号，请先登录。", silent))
                return
            session = requests.Session()
            if not load_session(session, get_cookies_path(account.get("id", 1))):
                self._emit(("restore_failed", "Cookie 读取失败，请重新登录。", silent))
                return
            if not verify_session(session):
                self._emit(("restore_failed", "登录态已失效，请重新登录。", silent))
                return
            self._emit(("login_success", session, account))
        except Exception as exc:
            self._emit(("restore_failed", str(exc), silent))

    def logout(self):
        if not self.account and not self.session:
            self._set_login_status("未登录", warn=True)
            self.metric_account.setText("未登录")
            return
        if QMessageBox.question(self, "退出登录", "确定要退出登录并清除当前账号的本地 Cookie 吗？") != QMessageBox.StandardButton.Yes:
            return

        self.stop_monitor()
        account = self.account
        self.session = None
        self.account = None
        self.strategy_pending_ids.clear()
        self.strategy_checks_in_progress.clear()
        for dialog in tuple(self.roster_dialogs.values()):
            dialog.close()
        self.password_input.clear()
        self._set_login_status("未登录", warn=True)
        self.metric_account.setText("未登录")
        self.metric_monitor.setText("未启动")
        self.metric_last_result.setText("-")
        self.course_refresh_in_progress = False
        self.courseware_courses_refresh_in_progress = False
        self.courseware_refresh_in_progress = False
        self.courseware_download_in_progress = False
        self.courseware_courses = []
        self.courseware_items = []
        self.courseware_course_by_display = {}
        self.courseware_combo.clear()
        self._refresh_courseware_table()
        self.courseware_summary.setText("登录后自动读取课程和课件。")

        if account:
            try:
                cookies_path = get_cookies_path(account.get("id", 1))
                if os.path.exists(cookies_path):
                    os.remove(cookies_path)
                self.log("已退出登录并清除本地 Cookie。")
            except Exception as exc:
                self.log(f"清除 Cookie 失败：{exc}")

    def start_monitor(self):
        if not self.session or not self.account:
            QMessageBox.warning(self, "尚未登录", "请先登录。")
            return
        if self.monitor_worker and self.monitor_worker.is_alive():
            return
        self.monitor_stop_event = threading.Event()
        self.monitor_worker = MonitorWorker(
            self.session,
            self._emit,
            self.monitor_stop_event,
            self.poll_interval_spin.value(),
        )
        self.started_at = time.time()
        self.monitor_worker.start()
        self.log("监控已启动。")

    def stop_monitor(self):
        self.auto_answer_check.setChecked(False)
        if self.monitor_stop_event:
            self.monitor_stop_event.set()
        self.log("正在停止监控。")

    def _toggle_auto_answer(self, enabled: bool):
        if enabled and (not self.session or not self.account):
            self.auto_answer_check.setChecked(False)
            QMessageBox.warning(self, "尚未登录", "请先登录后再开启自动签到。")
            return
        self.attendance_threshold_enabled_check.setEnabled(not enabled)
        if enabled:
            self.attendance_threshold_enabled_check.setChecked(True)
            self.start_monitor()
            for event_id, event in self.events_by_id.items():
                if event.result in ("待处理", "等待阈值", "可处理"):
                    self.strategy_pending_ids.add(event_id)
            self.log(f"自动签到已开启：达到 {self.attendance_threshold_spin.value()}% 后自动提交。")
            self._check_pending_strategies()
        else:
            self.log("自动签到已关闭；已经提交的请求不受影响。")

    def answer_selected_rollcall(self):
        event_id = self._selected_event_id()
        if not event_id:
            QMessageBox.information(self, "未选择", "请先选择一条签到事件。")
            return
        self._answer_event(event_id, self.events_by_id[event_id])

    def skip_selected_rollcall(self):
        event_id = self._selected_event_id()
        if event_id:
            if self.events_by_id[event_id].result in ("处理中", "已签到"):
                return
            self.strategy_pending_ids.discard(event_id)
            self._update_event_result(event_id, "已跳过", "用户手动跳过")

    def _selected_event_id(self) -> str:
        row = self.events_table.currentRow()
        if row < 0 or not self.events_table.item(row, 0):
            return ""
        return self.events_table.item(row, 0).data(Qt.ItemDataRole.UserRole) or ""

    def _open_overview_roster(self, row: int, _column: int):
        item = self.events_table.item(row, 0)
        event_id = item.data(Qt.ItemDataRole.UserRole) if item else ""
        event = self.events_by_id.get(event_id)
        if event:
            self._open_rollcall_roster(event.rollcall_id, event.course_title)

    def _open_course_roster(self, row: int, _column: int):
        item = self.course_table.item(row, 0)
        if not item:
            return
        rollcall_id = item.data(Qt.ItemDataRole.UserRole)
        course_title = item.data(int(Qt.ItemDataRole.UserRole) + 1)
        if rollcall_id:
            self._open_rollcall_roster(str(rollcall_id), str(course_title or "课程签到"))

    def _open_rollcall_roster(self, rollcall_id: str, course_title: str):
        if not self.session:
            QMessageBox.warning(self, "尚未登录", "请先登录。")
            return
        if not rollcall_id or rollcall_id == "-":
            QMessageBox.information(self, "无签到名单", "该课程记录没有有效的签到 ID。")
            return

        existing = self.roster_dialogs.get(rollcall_id)
        if existing:
            existing.show()
            existing.raise_()
            existing.activateWindow()
            return

        dialog = RollcallRosterDialog(course_title, rollcall_id, self)
        self.roster_dialogs[rollcall_id] = dialog
        dialog.finished.connect(lambda _result, key=rollcall_id: self.roster_dialogs.pop(key, None))
        dialog.show()
        self.log(f"按需读取签到名单：{course_title} / {rollcall_id}")
        self._run_thread(self._rollcall_roster_worker, rollcall_id)

    def _rollcall_roster_worker(self, rollcall_id: str):
        try:
            roster = fetch_rollcall_roster(self.session, rollcall_id)
            self._emit(("rollcall_roster", rollcall_id, roster))
        except Exception as exc:
            self._emit(("rollcall_roster_error", rollcall_id, str(exc)))

    def _check_pending_strategies(self):
        if not self.session or not self.attendance_threshold_enabled_check.isChecked():
            return
        for event_id in tuple(self.strategy_pending_ids):
            if event_id not in self.strategy_checks_in_progress:
                self.strategy_checks_in_progress.add(event_id)
                self._run_thread(self._strategy_check_worker, event_id)

    def _strategy_check_worker(self, event_id: str):
        event = self.events_by_id.get(event_id)
        if not event:
            self._emit(("strategy_check_error", event_id, "签到事件已不存在"))
            return
        try:
            roster = fetch_rollcall_roster(self.session, event.rollcall_id)
            self._emit(("strategy_check", event_id, roster))
        except Exception as exc:
            self._emit(("strategy_check_error", event_id, str(exc)))

    def _handle_strategy_check(self, event_id: str, roster: RollcallRoster):
        self.strategy_checks_in_progress.discard(event_id)
        if (event_id not in self.strategy_pending_ids or not self.session
                or not self.attendance_threshold_enabled_check.isChecked()):
            return

        total = len(roster.students)
        signed = sum(
            student.rollcall_status in ("已签到", "迟到")
            for student in roster.students
        )
        percent = signed * 100 / total if total else 0
        threshold = self.attendance_threshold_spin.value()
        if roster.status.lower() in ("finished", "closed", "ended"):
            self.strategy_pending_ids.discard(event_id)
            self._update_event_result(event_id, "已结束", f"签到结束时比例为 {percent:.1f}%（{signed}/{total}）")
            return
        if total and percent >= threshold:
            self.strategy_pending_ids.discard(event_id)
            detail = f"签到比例 {percent:.1f}%（{signed}/{total}），已达到 {threshold}%"
            self._update_event_result(event_id, "可处理", detail)
            self.metric_last_result.setText("签到比例已达阈值")
            self.log(detail)
            event = self.events_by_id.get(event_id)
            if self.auto_answer_check.isChecked() and event:
                self._answer_event(event_id, event, automatic=True)
                return
            QMessageBox.information(
                self,
                "签到比例已达阈值",
                f"{event.course_title if event else '课程签到'}\n{detail}\n请在总览中手动确认处理。",
            )
            return

        self._update_event_result(
            event_id,
            "等待阈值",
            f"当前签到比例 {percent:.1f}%（{signed}/{total}），目标 {threshold}%",
        )

    def _answer_event(self, event_id: str, event: RollcallEvent, automatic: bool = False):
        if event.result in ("处理中", "已签到", "已结束"):
            return
        if not self.session:
            QMessageBox.warning(self, "尚未登录", "请先登录。")
            return
        self.strategy_pending_ids.discard(event_id)
        if event.rollcall_type not in ("数字签到", "雷达签到"):
            self._update_event_result(event_id, "需手动", f"{event.rollcall_type}需要手动处理")
            if not automatic:
                QMessageBox.information(self, "需要手动处理", "该签到类型暂不支持自动处理，请手动完成。")
            return
        self._update_event_result(event_id, "处理中", "达到阈值，自动提交中" if automatic else "正在提交")
        self._run_thread(self._answer_worker, event_id, event, self.session)

    def _answer_worker(self, event_id: str, event: RollcallEvent, session):
        try:
            if event.rollcall_type == "数字签到":
                ok = send_code(session, event.rollcall_id)
            elif event.rollcall_type == "雷达签到":
                ok = send_radar(session, event.rollcall_id)
            else:
                ok = False
            self._emit(("answer_result", event_id, ok, "提交成功" if ok else "提交失败"))
        except Exception as exc:
            self._emit(("answer_result", event_id, False, str(exc)))

    def refresh_course_rollcalls(self, silent=False):
        if not self.session or not self.account:
            if not silent:
                QMessageBox.warning(self, "尚未登录", "请先登录。")
            return
        if self.course_refresh_in_progress:
            return
        self.course_refresh_in_progress = True
        self.course_summary.setText("正在刷新课程签到数据...")
        self.log("开始刷新课程签到总览。")
        self._run_thread(self._course_rollcalls_worker, silent)

    def _course_rollcalls_worker(self, silent=False):
        try:
            username = str(self.account.get("username", "")) if self.account else ""
            records, source = fetch_course_rollcall_records(
                self.session,
                username=username,
                academic_year=self.academic_year_combo.currentText().strip(),
                semester=self.semester_combo.currentText().strip(),
            )
            self._emit(("course_rollcalls", records, source))
        except Exception as exc:
            self._emit(("course_rollcalls_error", str(exc), silent))

    def refresh_courseware_courses(self, silent=False):
        if not self.session:
            if not silent:
                QMessageBox.warning(self, "尚未登录", "请先在总览页登录。")
            return
        if self.courseware_courses_refresh_in_progress:
            return
        self.courseware_courses_refresh_in_progress = True
        self.courseware_summary.setText("正在读取课程列表...")
        self.log("开始读取课件课程列表。")
        self._run_thread(self._courseware_courses_worker, silent)

    def _courseware_courses_worker(self, silent=False):
        try:
            courses, source = fetch_courses(self.session)
            self._emit(("courseware_courses", courses, source))
        except Exception as exc:
            self._emit(("courseware_courses_error", str(exc), silent))

    def refresh_selected_courseware(self, silent=False):
        if not self.session:
            if not silent:
                QMessageBox.warning(self, "尚未登录", "请先在总览页登录。")
            return
        course = self._selected_courseware_course()
        if not course:
            if not silent:
                QMessageBox.information(self, "未选择课程", "请先选择一门课程。")
            return
        if self.courseware_refresh_in_progress:
            return
        self.courseware_refresh_in_progress = True
        self.courseware_summary.setText(f"正在读取《{course.title}》课件...")
        self.log(f"开始读取课程课件：{course.title}。")
        self._run_thread(self._courseware_worker, course, silent)

    def _courseware_worker(self, course: CourseSummary, silent=False):
        try:
            self._emit(("courseware", course, fetch_courseware(self.session, course.course_id)))
        except Exception as exc:
            self._emit(("courseware_error", str(exc), silent))

    def choose_courseware_download_dir(self):
        selected = QFileDialog.getExistingDirectory(
            self,
            "选择课件下载目录",
            self.courseware_download_dir.text() or str(Path.home()),
        )
        if selected:
            self.courseware_download_dir.setText(selected)

    def download_selected_courseware(self):
        rows = self._selected_courseware_rows()
        if not rows:
            QMessageBox.information(self, "未选择", "请先选择一个或多个课件附件。")
            return
        selected_items = [self.courseware_items[row] for row in rows if row < len(self.courseware_items)]
        downloadable = [item for item in selected_items if self._courseware_can_download(item)]
        blocked = [item for item in selected_items if not self._courseware_can_download(item)]
        if not downloadable:
            status = blocked[0].download_status if blocked else "当前选择不可下载"
            QMessageBox.warning(self, "无法下载", status)
            return
        if blocked:
            self.log(f"已跳过 {len(blocked)} 个不可下载课件。")
        self._start_courseware_download(downloadable)

    def _selected_courseware_rows(self) -> list[int]:
        rows = {
            index.row()
            for index in self.courseware_table.selectionModel().selectedRows()
            if index.row() >= 0
        }
        if not rows and self.courseware_table.currentRow() >= 0:
            rows.add(self.courseware_table.currentRow())
        return sorted(rows)

    def activate_selected_courseware(self):
        row = self.courseware_table.currentRow()
        if row < 0 or row >= len(self.courseware_items):
            return
        item = self.courseware_items[row]
        if self._courseware_can_download(item):
            self._start_courseware_download([item])
        else:
            self.open_selected_courseware()

    def open_selected_courseware(self):
        row = self.courseware_table.currentRow()
        if row < 0 or row >= len(self.courseware_items):
            QMessageBox.information(self, "未选择", "请先选择一个课件活动。")
            return
        item = self.courseware_items[row]
        url = f"{base_url}/course/{item.course_id}/learning-activity#/{item.activity_id}"
        if not webbrowser.open(url):
            QMessageBox.critical(self, "打开失败", "无法调用系统浏览器打开平台页面。")

    def download_all_courseware(self):
        items = [item for item in self.courseware_items if self._courseware_can_download(item)]
        if not items:
            QMessageBox.information(self, "没有可下载课件", "当前课程没有可下载的附件。")
            return
        total_size = sum(item.size for item in items)
        if QMessageBox.question(
            self,
            "确认批量下载",
            f"将下载 {len(items)} 个文件，共 {format_size(total_size)}。是否继续？",
        ) != QMessageBox.StandardButton.Yes:
            return
        self._start_courseware_download(items)

    def _start_courseware_download(self, items: list[CoursewareItem]):
        if self.courseware_download_in_progress:
            QMessageBox.information(self, "正在下载", "请等待当前下载任务完成。")
            return
        course = self._selected_courseware_course()
        if not course:
            return
        self.courseware_download_in_progress = True
        destination = Path(self.courseware_download_dir.text()) / sanitize_filename(course.title)
        self.courseware_summary.setText(f"准备下载 {len(items)} 个文件...")
        self._run_thread(self._courseware_download_worker, items, destination)

    def _courseware_download_worker(self, items: list[CoursewareItem], destination: Path):
        downloaded = []
        errors = []
        for index, item in enumerate(items, start=1):
            self._emit(("courseware_download_progress", index, len(items), item.filename))
            try:
                downloaded.append(download_courseware(self.session, item, destination))
            except Exception as exc:
                errors.append(f"{item.filename or item.activity_title}：{exc}")
        self._emit(("courseware_download_done", downloaded, errors, destination))

    def _refresh_after_login(self):
        self.log("登录完成，开始后台刷新课程签到总览和课件课程。")
        self.refresh_course_rollcalls(silent=True)
        self.refresh_courseware_courses(silent=True)

    def _handle_event(self, event):
        kind = event[0]
        if kind == "login_success":
            _, session, account = event
            self.auto_answer_check.setChecked(False)
            self.strategy_pending_ids.clear()
            self.strategy_checks_in_progress.clear()
            self.events_by_id.clear()
            self.event_order.clear()
            self._refresh_event_tables()
            self.session = session
            self.account = account
            display = account.get("name") or account.get("username") or "已登录"
            self._set_login_status("已登录", warn=False)
            self.metric_account.setText(display)
            self._apply_rollcall_settings(account)
            self.log(f"登录成功：{display}")
            self._refresh_after_login()
        elif kind == "login_failed":
            self._set_login_status("未登录", warn=True)
            self.metric_account.setText("未登录")
            self.log(f"登录失败：{event[1]}")
            QMessageBox.critical(self, "登录失败", event[1])
        elif kind == "restore_failed":
            _, message, silent = event
            self._set_login_status("未登录", warn=True)
            self.metric_account.setText("未登录")
            self.log(f"会话恢复失败：{message}")
            if not silent:
                QMessageBox.critical(self, "恢复失败", message)
        elif kind == "monitor_status":
            self.metric_monitor.setText(event[1])
            self.log(f"监控状态：{event[1]}")
        elif kind == "poll":
            _, query_count, checked_at, rollcall_count = event
            self.metric_query_count.setText(str(query_count))
            self.metric_last_check.setText(time.strftime("%H:%M:%S", time.localtime(checked_at)))
            self.metric_rollcall_count.setText(str(rollcall_count))
        elif kind == "rollcall":
            self._add_rollcall_event(event[1])
        elif kind == "answer_result":
            _, event_id, ok, detail = event
            self._update_event_result(event_id, "已签到" if ok else "失败", detail)
            self.metric_last_result.setText(detail)
            self.log(f"签到处理结果：{detail}")
        elif kind == "number_code":
            event_id = event[1]
            code = event[2]
            detail = event[3] if len(event) > 3 else ""
            self._update_event_code(event_id, code, detail)
        elif kind == "rollcall_roster":
            _, rollcall_id, roster = event
            dialog = self.roster_dialogs.get(rollcall_id)
            if dialog:
                dialog.set_roster(roster)
        elif kind == "rollcall_roster_error":
            _, rollcall_id, message = event
            dialog = self.roster_dialogs.get(rollcall_id)
            if dialog:
                dialog.set_error(message)
            self.log(f"签到名单读取失败：{rollcall_id} / {message}")
        elif kind == "strategy_check":
            self._handle_strategy_check(event[1], event[2])
        elif kind == "strategy_check_error":
            _, event_id, message = event
            self.strategy_checks_in_progress.discard(event_id)
            self.log(f"签到比例检查失败：{message}")
        elif kind == "course_rollcalls":
            _, records, source = event
            self.course_refresh_in_progress = False
            self.course_records = records
            self._refresh_course_table()
            self.log(f"课程签到总览刷新完成。{source}")
        elif kind == "course_rollcalls_error":
            self.course_refresh_in_progress = False
            self.course_summary.setText("刷新失败")
            self.log(f"课程签到总览刷新失败：{event[1]}")
            if not event[2]:
                QMessageBox.critical(self, "刷新失败", event[1])
        elif kind == "courseware_courses":
            self.courseware_courses_refresh_in_progress = False
            self._set_courseware_courses(event[1], event[2])
        elif kind == "courseware_courses_error":
            self.courseware_courses_refresh_in_progress = False
            self.courseware_summary.setText("课程列表读取失败")
            self.log(f"课件课程列表读取失败：{event[1]}")
            if not event[2]:
                QMessageBox.critical(self, "课程列表读取失败", event[1])
        elif kind == "courseware":
            self.courseware_refresh_in_progress = False
            self._set_courseware_items(event[1], event[2])
        elif kind == "courseware_error":
            self.courseware_refresh_in_progress = False
            self.courseware_summary.setText("课件读取失败")
            self.log(f"课程课件读取失败：{event[1]}")
            if not event[2]:
                QMessageBox.critical(self, "课件读取失败", event[1])
        elif kind == "courseware_download_progress":
            _, index, total, filename = event
            self.courseware_summary.setText(f"正在下载 {index}/{total}：{filename}")
        elif kind == "courseware_download_done":
            self.courseware_download_in_progress = False
            _, downloaded, errors, destination = event
            self.courseware_summary.setText(f"下载完成 {len(downloaded)} 个；失败 {len(errors)} 个")
            self.log(f"课件下载完成：{len(downloaded)} 个，目录：{destination}")
            if errors:
                self.log("课件下载失败：" + "；".join(errors))
                QMessageBox.warning(
                    self,
                    "部分下载失败",
                    f"成功 {len(downloaded)} 个，失败 {len(errors)} 个。\n" + "\n".join(errors[:8]),
                )
            else:
                QMessageBox.information(self, "下载完成", f"已下载 {len(downloaded)} 个文件到：\n{destination}")
        elif kind == "error":
            self.log(event[1])

    def _set_login_status(self, text: str, warn: bool):
        self.top_status.setText(text)
        self.sidebar_status.setText(text)
        object_name = "StatusWarn" if warn else "StatusGood"
        self.top_status.setObjectName(object_name)
        self.sidebar_status.setObjectName(object_name)
        self.top_status.style().unpolish(self.top_status)
        self.top_status.style().polish(self.top_status)
        self.sidebar_status.style().unpolish(self.sidebar_status)
        self.sidebar_status.style().polish(self.sidebar_status)

    def _add_rollcall_event(self, event: RollcallEvent):
        self.event_sequence += 1
        event_id = f"event-{self.event_sequence}"
        self.events_by_id[event_id] = event
        self.event_order.insert(0, event_id)
        self.metric_last_result.setText(f"检测到 {event.rollcall_type}")
        self.log(f"检测到签到：{event.course_title} / {event.rollcall_type}")
        self._refresh_event_tables()

        if event.rollcall_type == "数字签到":
            self._run_thread(self._number_code_worker, event_id, event)
        if self.attendance_threshold_enabled_check.isChecked():
            self.strategy_pending_ids.add(event_id)
            self._update_event_result(
                event_id,
                "等待阈值",
                f"等待签到比例达到 {self.attendance_threshold_spin.value()}%",
            )
            self._check_pending_strategies()

    def _number_code_worker(self, event_id: str, event: RollcallEvent):
        try:
            self._emit(("number_code", event_id, fetch_number_code(self.session, event.rollcall_id)))
        except Exception as exc:
            self._emit(("number_code", event_id, "", str(exc)))

    def _event_values(self, event: RollcallEvent):
        return (
            time.strftime("%H:%M:%S", time.localtime(event.detected_at)),
            event.course_title,
            event.teacher,
            event.rollcall_type,
            event.status,
            event.number_code or "-",
            event.result,
            event.detail,
        )

    def _refresh_event_tables(self):
        rows = [self._event_values(self.events_by_id[event_id]) for event_id in self.event_order]
        self._set_table_rows(self.events_table, rows, self.event_order)

    def _update_event_result(self, event_id: str, result: str, detail: str):
        event = self.events_by_id.get(event_id)
        if not event:
            return
        event.result = result
        event.detail = detail
        self._refresh_event_tables()

    def _update_event_code(self, event_id: str, code: str, detail: str = ""):
        event = self.events_by_id.get(event_id)
        if not event:
            return
        if code:
            event.number_code = code
            if not event.detail:
                event.detail = "已获取签到码"
            self.log(f"数字签到码已获取：{event.course_title} / {code}")
        elif detail:
            event.detail = f"签到码获取失败：{detail}"
            self.log(event.detail)
        else:
            event.detail = "未获取到签到码"
            self.log(f"未获取到数字签到码：{event.course_title}")
        self._refresh_event_tables()

    def _course_status_text(self, status: str) -> str:
        return COURSE_STATUS_DISPLAY.get(status, status or "未知")

    def _course_status_priority(self, record: CourseRollcallRecord) -> int:
        return COURSE_STATUS_PRIORITY.get(self._course_status_text(record.signed_status), 1)

    def _course_record_datetime(self, record: CourseRollcallRecord) -> datetime | None:
        text = str(record.rollcall_time or "").strip()
        if not text or text == "-":
            return None
        for candidate in (text, text[:19]):
            try:
                return datetime.fromisoformat(candidate.replace("Z", "+00:00")).replace(tzinfo=None)
            except ValueError:
                continue
        return None

    def _course_record_in_time_range(self, record: CourseRollcallRecord) -> bool:
        selected = self.time_range_combo.currentText() if hasattr(self, "time_range_combo") else "本学期"
        if selected == "本学期":
            return True

        record_time = self._course_record_datetime(record)
        if record_time is None:
            return False

        today = date.today()
        record_date = record_time.date()
        if selected == "今天":
            return record_date == today
        if selected == "本周":
            week_start = today - timedelta(days=today.weekday())
            week_end = week_start + timedelta(days=6)
            return week_start <= record_date <= week_end
        return True

    def _course_group_label(self, record: CourseRollcallRecord) -> tuple[str, date | None]:
        record_time = self._course_record_datetime(record)
        if record_time is None:
            return "时间未知", None
        record_date = record_time.date()
        if record_date == date.today():
            return "今天", record_date
        return record_date.isoformat(), record_date

    def _sorted_course_records(self, records: list[CourseRollcallRecord]) -> list[CourseRollcallRecord]:
        def sort_time(record: CourseRollcallRecord) -> float:
            record_time = self._course_record_datetime(record)
            if record_time is None:
                return 0
            return record_time.toordinal() * 86400 + record_time.hour * 3600 + record_time.minute * 60 + record_time.second

        return sorted(
            records,
            key=lambda record: (
                self._course_status_priority(record),
                -sort_time(record),
                record.course_title,
            ),
        )

    def _insert_course_group_row(self, row: int, label: str, count: int) -> None:
        self.course_table.insertRow(row)
        item = QTableWidgetItem(f"{label}  ({count} 条)")
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        item.setFont(QFont("Microsoft YaHei UI", 10, QFont.Weight.DemiBold))
        item.setForeground(QColor("#334155"))
        item.setBackground(QColor("#e9eff5"))
        self.course_table.setItem(row, 0, item)
        self.course_table.setSpan(row, 0, 1, self.course_table.columnCount())

    def _insert_course_record_row(self, row: int, group_label: str, record: CourseRollcallRecord) -> None:
        self.course_table.insertRow(row)
        status_text = self._course_status_text(record.signed_status)
        values = (
            group_label,
            record.course_title,
            record.rollcall_time,
            record.rollcall_type,
            status_text,
            record.platform_status,
            record.rollcall_id,
            record.detail,
        )
        for column, value in enumerate(values):
            item = QTableWidgetItem(str(value))
            if column == 0 and record.rollcall_id and record.rollcall_id != "-":
                item.setData(Qt.ItemDataRole.UserRole, record.rollcall_id)
                item.setData(int(Qt.ItemDataRole.UserRole) + 1, record.course_title)
            if column in (0, 2, 3, 4, 5, 6):
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if column == 4:
                foreground, background = COURSE_STATUS_COLORS.get(status_text, COURSE_STATUS_COLORS["未知"])
                item.setForeground(QColor(foreground))
                item.setBackground(QColor(background))
                item.setFont(QFont("Microsoft YaHei UI", 9, QFont.Weight.DemiBold))
            self.course_table.setItem(row, column, item)

    def _set_course_empty_state(self, message: str) -> None:
        self.course_table.setRowCount(0)
        self.course_table.insertRow(0)
        item = QTableWidgetItem(message)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        item.setForeground(QColor("#687586"))
        self.course_table.setItem(0, 0, item)
        self.course_table.setSpan(0, 0, 1, self.course_table.columnCount())

    def _refresh_course_table(self):
        records = [record for record in self.course_records if self._course_record_in_time_range(record)]
        if self.only_unsigned_check.isChecked():
            records = [record for record in records if self._course_status_text(record.signed_status) == "未签"]

        self.course_table.setRowCount(0)
        if not records:
            if not self.course_records:
                empty_message = "登录后可查看课程签到数据"
            elif self.time_range_combo.currentText() == "今天":
                empty_message = "今天暂无签到记录"
            elif self.time_range_combo.currentText() == "本周":
                empty_message = "本周暂无签到记录"
            else:
                empty_message = "当前学期暂无签到记录"
            self._set_course_empty_state(empty_message)
        else:
            grouped: dict[str, list[CourseRollcallRecord]] = {}
            group_dates: dict[str, date | None] = {}
            for record in records:
                label, group_date = self._course_group_label(record)
                grouped.setdefault(label, []).append(record)
                group_dates[label] = group_date

            labels = sorted(
                grouped,
                key=lambda label: group_dates[label] or date.min,
                reverse=True,
            )
            row = 0
            for label in labels:
                group_records = self._sorted_course_records(grouped[label])
                self._insert_course_group_row(row, label, len(group_records))
                row += 1
                for record in group_records:
                    self._insert_course_record_row(row, label, record)
                    row += 1

        signed = sum(1 for record in self.course_records if record.signed_status == "已签到")
        unsigned = sum(1 for record in self.course_records if record.signed_status == "未签到")
        unknown = sum(1 for record in self.course_records if record.signed_status == "未知")
        no_rollcalls = sum(1 for record in self.course_records if record.signed_status == "无签到记录")
        summary = f"共 {len(self.course_records)} 条；已签 {signed}；未签 {unsigned}；未知 {unknown}；无记录 {no_rollcalls}"
        if self.only_unsigned_check.isChecked():
            summary += f"；当前显示 {len(records)} 条未签"
        self.course_summary.setText(summary)

    def _selected_courseware_course(self) -> CourseSummary | None:
        return self.courseware_course_by_display.get(self.courseware_combo.currentText())

    def _courseware_course_matches_filters(self, course: CourseSummary) -> bool:
        academic_year = self.courseware_academic_year_combo.currentText().strip()
        if academic_year and academic_year not in course.term and academic_year not in course.search_text:
            return False

        semester = self.courseware_semester_combo.currentText().strip()
        semester_code = course.semester_code or course.term
        if semester == "第一学期":
            return semester_code.endswith("-1")
        if semester == "第二学期":
            return semester_code.endswith("-2")
        if semester == "第三学期":
            return semester_code.endswith("-3")
        return True

    def _refresh_courseware_course_combo(self, previous: str = "") -> list[str]:
        self.courseware_course_by_display = {}
        displays = []
        for course in self.courseware_courses:
            if not self._courseware_course_matches_filters(course):
                continue
            display = course.display_name
            if display in self.courseware_course_by_display:
                display = f"{display}  ({course.course_id})"
            self.courseware_course_by_display[display] = course
            displays.append(display)

        self.courseware_combo.blockSignals(True)
        self.courseware_combo.clear()
        self.courseware_combo.addItems(displays)
        if previous in self.courseware_course_by_display:
            self.courseware_combo.setCurrentText(previous)
        self.courseware_combo.blockSignals(False)
        return displays

    def _apply_courseware_course_filters(self):
        previous = self.courseware_combo.currentText()
        displays = self._refresh_courseware_course_combo(previous)
        self.courseware_items = []
        self._refresh_courseware_table()
        self.courseware_summary.setText(f"共 {len(self.courseware_courses)} 门课程；当前显示 {len(displays)} 门")
        if displays:
            self.refresh_selected_courseware(silent=True)

    def _courseware_can_download(self, item: CoursewareItem) -> bool:
        return item.download_status in ("可下载", "可下载（平台版权保护）")

    def _set_courseware_courses(self, courses: list[CourseSummary], source: str):
        previous = self.courseware_combo.currentText()
        self.courseware_courses = courses
        displays = self._refresh_courseware_course_combo(previous)

        self.courseware_summary.setText(f"共 {len(courses)} 门课程；当前显示 {len(displays)} 门")
        self.log(f"课件课程列表读取完成，共 {len(courses)} 门，当前筛选 {len(displays)} 门；接口：{source}")
        if displays:
            self.refresh_selected_courseware(silent=True)
        else:
            self.courseware_items = []
            self._refresh_courseware_table()

    def _set_courseware_items(self, course: CourseSummary, items: list[CoursewareItem]):
        selected = self._selected_courseware_course()
        if selected and selected.course_id != course.course_id:
            self.refresh_selected_courseware(silent=True)
            return
        self.courseware_items = items
        self._refresh_courseware_table()
        downloadable = sum(self._courseware_can_download(item) for item in items)
        protected = sum(item.download_status == "可下载（平台版权保护）" for item in items)
        self.courseware_summary.setText(f"课件 {len(items)} 条；可下载 {downloadable}；版权保护 {protected}")
        self.log(f"课程课件读取完成：{course.title}，共 {len(items)} 条，可下载 {downloadable} 条。")

    def _refresh_courseware_table(self):
        type_names = {
            "material": "资料",
            "online_video": "视频",
            "slide": "课件",
            "web_link": "链接",
            "page": "页面",
            "scorm": "SCORM",
            "h5_courseware": "H5课件",
        }
        rows = [
            (
                item.module_name,
                item.syllabus_name or "-",
                item.activity_title,
                item.filename or "-",
                type_names.get(item.activity_type, item.media_type or item.activity_type or "-"),
                format_size(item.size) if item.size else "-",
                normalize_time(item.published_at),
                item.download_status,
                item.upload_status or "-",
            )
            for item in self.courseware_items
        ]
        self._set_table_rows(self.courseware_table, rows)

    def _tick_runtime(self):
        if self.started_at and self.monitor_worker and self.monitor_worker.is_alive():
            self.metric_runtime.setText(format_duration(int(time.time() - self.started_at)))

    def log(self, message: str):
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        self.log_text.append(f"[{timestamp}] {message}")

    def closeEvent(self, event):
        self.stop_monitor()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(stylesheet())
    window = DashboardWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
