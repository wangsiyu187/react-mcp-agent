"""主窗口：单窗口主从视图。"""
from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QAction
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from database import Database
from models import Job, Preference, STATUSES
from matcher import match_score, ranges_overlap
from csv_io import export_jobs, import_jobs
from dialogs import JobEditDialog, PreferenceDialog, SettingDialog

from config import LLMConfig
from agent_worker import AgentWorker, ConnectionTestWorker

SETTING_BASE_URL = "platform_base_url"

logger = logging.getLogger("jnu.job.mainwindow")

HEADERS = ["职位名称", "公司名称", "工作地点", "薪资范围", "学历要求",
           "投递状态", "收藏", "匹配度", "截止日期"]

COL_TITLE, COL_COMPANY, COL_LOCATION, COL_SALARY, COL_EDU = 0, 1, 2, 3, 4
COL_STATUS, COL_FAV, COL_SCORE, COL_DEADLINE = 5, 6, 7, 8


def _today() -> date:
    return date.today()


class MainWindow(QMainWindow):
    def __init__(self, db: Database):
        super().__init__()
        self.db = db
        self.setWindowTitle("暨南大学就业信息服务平台岗位信息辅助管理")
        self.resize(1280, 780)

        self._all_jobs: list[Job] = []
        self._display_jobs: list[Job] = []
        self._scores: dict[int, int] = {}
        self._sort_col: int = -1
        self._sort_asc: bool = True

        # AI 查询相关状态
        self._busy = False
        self._llm_config = LLMConfig()
        self._worker: AgentWorker | None = None
        self._test_thread: ConnectionTestWorker | None = None

        self._build_ui()
        self._connect_signals()
        self.reload()
        self._start_agent_worker()

    # ---------------- UI 构建 ----------------

    def _build_ui(self) -> None:
        self._build_toolbar()

        self.tabs = QTabWidget(self)
        self.tabs.addTab(self._build_manage_tab(), "招聘信息管理")
        self.tabs.addTab(self._build_ai_tab(), "AI 智能招聘查询")
        self.tabs.addTab(self._build_config_tab(), "大模型配置")

        self.setCentralWidget(self.tabs)
        self.statusBar().showMessage("")

    def _build_toolbar(self) -> None:
        # 工具栏（招聘信息管理相关动作）
        toolbar = QToolBar("主工具栏", self)
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        def add_action(text: str, handler) -> QAction:
            act = QAction(text, self)
            act.triggered.connect(handler)
            toolbar.addAction(act)
            return act

        add_action("新增", self._add_job)
        add_action("编辑", self._edit_job)
        add_action("删除", self._delete_job)
        add_action("打开招聘链接", self._open_url)
        toolbar.addSeparator()
        add_action("导入 CSV", self._import_csv)
        add_action("导出 CSV", self._export_csv)
        toolbar.addSeparator()
        add_action("编辑求职意向", self._edit_preference)
        add_action("设置平台基础网址", self._edit_setting)
        toolbar.addSeparator()
        add_action("按匹配度排序", self._sort_by_match)

    def _build_manage_tab(self) -> QWidget:
        # 左侧筛选面板
        filter_widget = self._build_filter_panel()

        # 中间表格
        self.table = QTableWidget(0, len(HEADERS), self)
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_TITLE, QHeaderView.ResizeMode.Stretch)
        header.setStretchLastSection(False)

        # 下方详情区
        self.detail = QTextBrowser(self)
        self.detail.setOpenExternalLinks(True)
        self.detail.setMinimumHeight(180)

        right_splitter = QSplitter(Qt.Orientation.Vertical, self)
        right_splitter.addWidget(self.table)
        right_splitter.addWidget(self.detail)
        right_splitter.setStretchFactor(0, 3)
        right_splitter.setStretchFactor(1, 1)

        main_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        main_splitter.addWidget(filter_widget)
        main_splitter.addWidget(right_splitter)
        main_splitter.setStretchFactor(0, 0)
        main_splitter.setStretchFactor(1, 1)
        main_splitter.setSizes([230, 1050])
        return main_splitter

    def _build_ai_tab(self) -> QWidget:
        widget = QWidget(self)
        layout = QVBoxLayout(widget)

        # 状态行：MCP 连接状态 + 工具列表
        status_row = QHBoxLayout()
        status_row.addWidget(QLabel("MCP 连接状态："))
        self.mcp_status_label = QLabel("未连接")
        status_row.addWidget(self.mcp_status_label)
        status_row.addSpacing(20)
        status_row.addWidget(QLabel("已发现工具："))
        self.mcp_tools_label = QLabel("—")
        self.mcp_tools_label.setWordWrap(True)
        status_row.addWidget(self.mcp_tools_label, 1)
        layout.addLayout(status_row)

        splitter = QSplitter(Qt.Orientation.Horizontal, widget)

        # 左侧：对话记录 + 输入框
        left = QWidget(splitter)
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("对话记录"))
        self.chat_display = QTextBrowser(left)
        self.chat_display.setOpenExternalLinks(False)
        left_layout.addWidget(self.chat_display, 1)

        input_row = QHBoxLayout()
        self.ai_input = QLineEdit(left)
        self.ai_input.setPlaceholderText("用自然语言描述需求，例如：帮我找珠海的 Python 实习岗位")
        self.ai_input.returnPressed.connect(self._send_ai_query)
        self.send_button = QPushButton("发送", left)
        self.send_button.clicked.connect(self._send_ai_query)
        self.clear_chat_button = QPushButton("清空对话", left)
        self.clear_chat_button.clicked.connect(self._clear_ai_chat)
        input_row.addWidget(self.ai_input, 1)
        input_row.addWidget(self.send_button)
        input_row.addWidget(self.clear_chat_button)
        left_layout.addLayout(input_row)

        # 右侧：工具调用日志 + 查询结果原始数据
        right = QWidget(splitter)
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(QLabel("MCP 工具调用日志"))
        self.tool_log_display = QTextBrowser(right)
        right_layout.addWidget(self.tool_log_display, 1)
        right_layout.addWidget(QLabel("招聘查询结果（原始数据）"))
        self.result_display = QTextBrowser(right)
        right_layout.addWidget(self.result_display, 1)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)
        return widget

    def _build_config_tab(self) -> QWidget:
        widget = QWidget(self)
        layout = QVBoxLayout(widget)

        box = QGroupBox("大模型 API 配置（OpenAI 兼容接口）", widget)
        form = QFormLayout(box)
        self.cfg_base_url = QLineEdit(LLMConfig().base_url, box)
        self.cfg_api_key = QLineEdit(box)
        self.cfg_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.cfg_api_key.setPlaceholderText("仅当前运行会话保留，不写入任何文件")
        self.cfg_model = QLineEdit(LLMConfig().model, box)
        self.cfg_provider = QComboBox(box)
        self.cfg_provider.addItems(["deepseek", "openai", "other"])
        self.cfg_timeout = QDoubleSpinBox(box)
        self.cfg_timeout.setRange(1.0, 600.0)
        self.cfg_timeout.setDecimals(1)
        self.cfg_timeout.setValue(LLMConfig().timeout)
        self.cfg_timeout.setSuffix(" 秒")
        self.cfg_rounds = QSpinBox(box)
        self.cfg_rounds.setRange(1, 20)
        self.cfg_rounds.setValue(LLMConfig().max_tool_rounds)
        form.addRow("服务商", self.cfg_provider)
        form.addRow("API Base URL", self.cfg_base_url)
        form.addRow("API Key", self.cfg_api_key)
        form.addRow("模型名称", self.cfg_model)
        form.addRow("请求超时", self.cfg_timeout)
        form.addRow("最大工具调用轮数", self.cfg_rounds)
        layout.addWidget(box)

        note = QLabel(
            "说明：API Key 仅保留在当前运行会话内，不会明文写入任何文件。\n"
            "服务商选择 deepseek 时，默认以非思考模式调用工具（thinking: disabled）。\n"
            "其他服务商不附加 DeepSeek 特有参数，保持 OpenAI 兼容接口。"
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        btn_row = QHBoxLayout()
        self.test_conn_button = QPushButton("测试连接", widget)
        self.test_conn_button.clicked.connect(self._test_llm_connection)
        self.save_cfg_button = QPushButton("保存配置", widget)
        self.save_cfg_button.clicked.connect(self._save_llm_config)
        btn_row.addWidget(self.test_conn_button)
        btn_row.addWidget(self.save_cfg_button)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)
        layout.addStretch(1)
        return widget

    def _build_filter_panel(self) -> QWidget:
        panel = QWidget(self)
        panel.setMinimumWidth(200)
        layout = QVBoxLayout(panel)

        self.search_edit = QLineEdit(panel)
        self.search_edit.setPlaceholderText("搜索职位 / 公司名称")

        self.location_combo = QComboBox(panel)
        self.industry_combo = QComboBox(panel)
        self.education_combo = QComboBox(panel)

        self.salary_min_spin = self._make_salary_spin(panel, "最低")
        self.salary_max_spin = self._make_salary_spin(panel, "最高")

        self.status_combo = QComboBox(panel)
        self.status_combo.addItem("全部")
        self.status_combo.addItems(STATUSES)

        self.favorite_check = QCheckBox("仅看收藏", panel)

        self.deadline_combo = QComboBox(panel)
        self.deadline_combo.addItems(["全部", "未过期", "已过期"])

        self.clear_button = QPushButton("清除筛选", panel)

        form = QFormLayout()
        form.addRow("关键字", self.search_edit)
        form.addRow("工作地点", self.location_combo)
        form.addRow("行业", self.industry_combo)
        form.addRow("学历要求", self.education_combo)
        form.addRow("薪资最低", self.salary_min_spin)
        form.addRow("薪资最高", self.salary_max_spin)
        form.addRow("投递状态", self.status_combo)
        form.addRow("", self.favorite_check)
        form.addRow("截止日期", self.deadline_combo)

        box = QGroupBox("搜索与筛选", panel)
        box.setLayout(form)
        layout.addWidget(box)
        layout.addWidget(self.clear_button)
        layout.addStretch(1)
        return panel

    def _make_salary_spin(self, parent, placeholder: str):
        from dialogs import NullableSpinBox
        spin = NullableSpinBox(placeholder=placeholder, parent=parent)
        return spin

    def _connect_signals(self) -> None:
        self.search_edit.textChanged.connect(self._on_filter_changed)
        self.location_combo.currentIndexChanged.connect(self._on_filter_changed)
        self.industry_combo.currentIndexChanged.connect(self._on_filter_changed)
        self.education_combo.currentIndexChanged.connect(self._on_filter_changed)
        self.salary_min_spin.spin.valueChanged.connect(self._on_filter_changed)
        self.salary_max_spin.spin.valueChanged.connect(self._on_filter_changed)
        self.status_combo.currentIndexChanged.connect(self._on_filter_changed)
        self.favorite_check.stateChanged.connect(self._on_filter_changed)
        self.deadline_combo.currentIndexChanged.connect(self._on_filter_changed)
        self.clear_button.clicked.connect(self._clear_filter)

        self.table.currentCellChanged.connect(self._on_selection_changed)
        self.table.horizontalHeader().sectionClicked.connect(self._on_header_clicked)
        self.table.doubleClicked.connect(lambda *_: self._edit_job())

    # ---------------- 数据加载 / 视图刷新 ----------------

    def reload(self) -> None:
        """重新从数据库读取全部岗位，重算匹配度，刷新视图。"""
        self._all_jobs = self.db.list_jobs()
        pref = self.db.get_preference()
        self._scores = {j.id: match_score(j, pref) for j in self._all_jobs}
        self._refresh_filter_options()
        self._apply_filter_and_fill()

    def _refresh_filter_options(self) -> None:
        """用已录数据的去重值刷新地点/行业/学历下拉，保留当前选中。"""
        locations = sorted({j.location for j in self._all_jobs if j.location})
        industries = sorted({j.industry for j in self._all_jobs if j.industry})
        educations = sorted({j.education for j in self._all_jobs if j.education})

        def refresh(combo: QComboBox, values: list[str], all_label: str = "全部") -> None:
            current = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(all_label)
            combo.addItems(values)
            idx = combo.findText(current)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            combo.blockSignals(False)

        refresh(self.location_combo, locations)
        refresh(self.industry_combo, industries)
        refresh(self.education_combo, educations)

    def _clear_filter(self) -> None:
        self.search_edit.blockSignals(True)
        self.search_edit.clear()
        self.search_edit.blockSignals(False)
        self.location_combo.blockSignals(True)
        self.location_combo.setCurrentIndex(0)
        self.location_combo.blockSignals(False)
        self.industry_combo.blockSignals(True)
        self.industry_combo.setCurrentIndex(0)
        self.industry_combo.blockSignals(False)
        self.education_combo.blockSignals(True)
        self.education_combo.setCurrentIndex(0)
        self.education_combo.blockSignals(False)
        self.salary_min_spin.set_value(None)
        self.salary_max_spin.set_value(None)
        self.status_combo.blockSignals(True)
        self.status_combo.setCurrentIndex(0)
        self.status_combo.blockSignals(False)
        self.favorite_check.blockSignals(True)
        self.favorite_check.setChecked(False)
        self.favorite_check.blockSignals(False)
        self.deadline_combo.blockSignals(True)
        self.deadline_combo.setCurrentIndex(0)
        self.deadline_combo.blockSignals(False)
        self._apply_filter_and_fill()

    def _on_filter_changed(self, *_) -> None:
        self._apply_filter_and_fill()

    def _matches_filter(self, job: Job) -> bool:
        # 文本
        text = self.search_edit.text().strip().lower()
        if text:
            blob = f"{job.title} {job.company}".lower()
            if text not in blob:
                return False

        # 地点 / 行业 / 学历（下拉，选具体值才过滤）
        if self.location_combo.currentIndex() > 0 and job.location != self.location_combo.currentText():
            return False
        if self.industry_combo.currentIndex() > 0 and job.industry != self.industry_combo.currentText():
            return False
        if self.education_combo.currentIndex() > 0 and job.education != self.education_combo.currentText():
            return False

        # 薪资区间交集
        user_min = self.salary_min_spin.value()
        user_max = self.salary_max_spin.value()
        if user_min is not None or user_max is not None:
            if job.salary_min is None and job.salary_max is None:
                return False
            if not ranges_overlap(job.salary_min, job.salary_max, user_min, user_max):
                return False

        # 投递状态
        if self.status_combo.currentIndex() > 0 and job.status != self.status_combo.currentText():
            return False

        # 收藏
        if self.favorite_check.isChecked() and not job.favorite:
            return False

        # 截止日期
        deadline_mode = self.deadline_combo.currentText()
        if deadline_mode == "未过期":
            if job.deadline is not None and job.deadline < _today():
                return False
        elif deadline_mode == "已过期":
            if job.deadline is None or job.deadline >= _today():
                return False

        return True

    def _apply_filter_and_fill(self) -> None:
        filtered = [j for j in self._all_jobs if self._matches_filter(j)]
        if self._sort_col >= 0:
            filtered = self._sort_jobs(filtered, self._sort_col, self._sort_asc)
        self._display_jobs = filtered
        self._fill_table(filtered)
        self.statusBar().showMessage(
            f"共 {len(self._all_jobs)} 条，当前筛选 {len(filtered)} 条"
        )
        self._update_detail_for_current()

    # ---------------- 排序 ----------------

    def _col_sort(self, job: Job, col: int) -> tuple[bool, object]:
        """返回 (是否为空, 排序值)。"""
        if col == COL_TITLE:
            v = job.title.lower()
            return (v == "", v)
        if col == COL_COMPANY:
            v = job.company.lower()
            return (v == "", v)
        if col == COL_LOCATION:
            v = job.location.lower()
            return (v == "", v)
        if col == COL_SALARY:
            v = job.salary_min if job.salary_min is not None else 0
            return (job.salary_min is None, v)
        if col == COL_EDU:
            v = job.education.lower()
            return (v == "", v)
        if col == COL_STATUS:
            v = STATUSES.index(job.status) if job.status in STATUSES else 0
            return (False, v)
        if col == COL_FAV:
            return (False, 1 if job.favorite else 0)
        if col == COL_SCORE:
            return (False, self._scores.get(job.id, 0))
        if col == COL_DEADLINE:
            return (job.deadline is None, job.deadline or date.max)
        return (True, "")

    def _sort_jobs(self, jobs: list[Job], col: int, asc: bool) -> list[Job]:
        if col == COL_SCORE:
            # 三级排序：匹配度降序 → 截止日期升序 → 职位名称升序
            jobs = sorted(jobs, key=lambda j: j.title.lower())
            jobs = sorted(jobs, key=lambda j: (j.deadline is None, j.deadline or date.max))
            return sorted(jobs, key=lambda j: self._scores.get(j.id, 0), reverse=True)
        if asc:
            return sorted(jobs, key=lambda j: self._col_sort(j, col))
        return sorted(jobs, key=lambda j: self._col_sort(j, col), reverse=True)

    def _on_header_clicked(self, col: int) -> None:
        if self._sort_col == col:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_col = col
            self._sort_asc = col != COL_SCORE
        self._apply_filter_and_fill()

    def _sort_by_match(self) -> None:
        self._sort_col = COL_SCORE
        self._sort_asc = False
        self._apply_filter_and_fill()

    # ---------------- 表格与详情 ----------------

    def _fill_table(self, jobs: list[Job]) -> None:
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(jobs))
        for r, job in enumerate(jobs):
            values = [
                job.title,
                job.company,
                job.location,
                job.salary_text,
                job.education,
                job.status,
                "★" if job.favorite else "",
                str(self._scores.get(job.id, 0)),
                job.deadline.isoformat() if job.deadline else "",
            ]
            for c, v in enumerate(values):
                item = QTableWidgetItem(v)
                if c in (COL_TITLE, COL_COMPANY, COL_LOCATION, COL_EDU, COL_STATUS, COL_FAV):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                elif c in (COL_SALARY, COL_SCORE):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                else:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(r, c, item)
        self.table.setSortingEnabled(False)

    def _selected_job(self) -> Optional[Job]:
        row = self.table.currentRow()
        if 0 <= row < len(self._display_jobs):
            return self._display_jobs[row]
        return None

    def _on_selection_changed(self, *_) -> None:
        self._update_detail_for_current()

    def _update_detail_for_current(self) -> None:
        job = self._selected_job()
        if job is None:
            self.detail.setHtml("<i>未选择岗位。</i>")
            return
        score = self._scores.get(job.id, 0)
        parts = [
            f"<h2>{_esc(job.title)}</h2>",
            "<table>",
            _row("公司名称", job.company),
            _row("工作地点", job.location),
            _row("行业", job.industry),
            _row("薪资", job.salary_text or "未填写"),
            _row("学历要求", job.education),
            _row("专业要求", job.major),
            _row("岗位标签", job.tags),
            _row("投递状态", job.status),
            _row("投递日期", job.applied_date.isoformat() if job.applied_date else ""),
            _row("截止日期", job.deadline.isoformat() if job.deadline else ""),
            _row("收藏", "★ 已收藏" if job.favorite else "未收藏"),
            _row("匹配度", str(score)),
            "</table>",
        ]
        if job.url:
            parts.append(f"<p><b>招聘链接：</b><a href=\"{_esc_attr(job.url)}\">{_esc(job.url)}</a></p>")
        if job.description.strip():
            parts.append(f"<h3>岗位描述</h3><p>{_esc_multiline(job.description)}</p>")
        if job.note.strip():
            parts.append(f"<h3>备注</h3><p>{_esc_multiline(job.note)}</p>")
        self.detail.setHtml("".join(parts))

    # ---------------- 动作 ----------------

    def _add_job(self) -> None:
        dlg = JobEditDialog(self)
        if dlg.exec():
            job = dlg.get_job()
            self.db.add_job(job)
            self.reload()

    def _edit_job(self) -> None:
        job = self._selected_job()
        if job is None:
            QMessageBox.information(self, "提示", "请先选择一个岗位。")
            return
        dlg = JobEditDialog(self, job)
        if dlg.exec():
            new_job = dlg.get_job()
            new_job.id = job.id
            new_job.created_at = job.created_at
            # 首次从「未投递」变为「已投递」时自动记录投递日期
            if job.status == "未投递" and new_job.status == "已投递" and new_job.applied_date is None:
                new_job.applied_date = _today()
            self.db.update_job(new_job)
            self.reload()

    def _delete_job(self) -> None:
        job = self._selected_job()
        if job is None:
            QMessageBox.information(self, "提示", "请先选择一个岗位。")
            return
        ret = QMessageBox.question(
            self, "确认删除",
            f"确定删除岗位「{job.title}」（{job.company}）吗？此操作不可恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret == QMessageBox.StandardButton.Yes:
            self.db.delete_job(job.id)
            self.reload()

    def _resolve_url(self, job: Job) -> Optional[str]:
        url = (job.url or "").strip()
        if not url:
            return None
        if url.startswith("http://") or url.startswith("https://"):
            return url
        base = self.db.get_setting(SETTING_BASE_URL, "").strip()
        if base:
            return base.rstrip("/") + "/" + url.lstrip("/")
        return None

    def _open_url(self) -> None:
        job = self._selected_job()
        if job is None:
            QMessageBox.information(self, "提示", "请先选择一个岗位。")
            return
        url = self._resolve_url(job)
        if url is None:
            QMessageBox.information(self, "提示", "该岗位没有可打开的招聘链接。")
            return
        QDesktopServices.openUrl(QUrl(url))

    def _import_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "导入 CSV", "", "CSV 文件 (*.csv);;所有文件 (*)"
        )
        if not path:
            return
        jobs, errors = import_jobs(path)
        if not jobs:
            QMessageBox.warning(self, "导入失败", "\n".join(errors) if errors else "没有读取到任何记录。")
            return

        # 去重：按 招聘链接 + 职位名称 + 公司名称
        duplicates = []
        new_jobs = []
        for job in jobs:
            if self.db.is_duplicate(job.title, job.company, job.url):
                duplicates.append(job)
            else:
                new_jobs.append(job)

        if duplicates:
            ret = QMessageBox.question(
                self, "发现重复记录",
                f"发现 {len(duplicates)} 条与库中重复的记录（招聘链接+职位+公司相同）。\n是否跳过这些重复记录？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if ret == QMessageBox.StandardButton.No:
                new_jobs.extend(duplicates)

        for job in new_jobs:
            self.db.add_job(job)
        self.reload()

        msg = f"成功导入 {len(new_jobs)} 条记录。"
        if errors:
            msg += "\n\n提示：\n" + "\n".join(errors[:10])
        QMessageBox.information(self, "导入完成", msg)

    def _export_csv(self) -> None:
        default_name = f"jobs_{_today().strftime('%Y%m%d')}.csv"
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 CSV", default_name, "CSV 文件 (*.csv);;所有文件 (*)"
        )
        if not path:
            return
        try:
            count = export_jobs(self._all_jobs, path)
        except OSError as e:
            QMessageBox.warning(self, "导出失败", str(e))
            return
        QMessageBox.information(self, "导出完成", f"已导出 {count} 条岗位记录。")

    def _edit_preference(self) -> None:
        pref = self.db.get_preference()
        dlg = PreferenceDialog(self, pref)
        if dlg.exec():
            self.db.save_preference(dlg.get_preference())
            self.reload()

    def _edit_setting(self) -> None:
        base = self.db.get_setting(SETTING_BASE_URL, "")
        dlg = SettingDialog(self, base)
        if dlg.exec():
            self.db.set_setting(SETTING_BASE_URL, dlg.get_base_url())
            QMessageBox.information(self, "已保存", "平台基础网址已保存。")

    # ---------------- AI 智能查询 ----------------

    def _start_agent_worker(self) -> None:
        self._worker = AgentWorker(self._llm_config, self)
        self._worker.statusChanged.connect(self._on_mcp_status_changed)
        self._worker.logMessage.connect(self._on_agent_log)
        self._worker.answerReady.connect(self._on_agent_answer)
        self._worker.toolsReady.connect(self._on_tools_ready)
        self._worker.toolCallLogged.connect(self._on_tool_call_logged)
        self._worker.resultData.connect(self._on_result_data)
        self._worker.errorOccurred.connect(self._on_agent_error)
        self._worker.busyChanged.connect(self._on_busy_changed)
        self._worker.start()

    def _send_ai_query(self) -> None:
        text = self.ai_input.text().strip()
        if not text:
            return
        if self._busy:
            QMessageBox.information(self, "提示", "正在处理上一条查询，请稍候。")
            return
        # 配置校验：必须已保存且包含 API Key（仅会话内）
        if not self._llm_config.has_key:
            if self.cfg_api_key.text().strip():
                QMessageBox.information(
                    self, "配置未保存",
                    "配置页已填写 API Key，但尚未点击「保存配置」。\n"
                    "请切换到「大模型配置」页点击「保存配置」后再发送查询。",
                )
            else:
                QMessageBox.information(
                    self, "未配置 API Key",
                    "尚未配置 API Key。\n请切换到「大模型配置」页填写 API Key 并点击「保存配置」。",
                )
            return
        self._append_chat("user", text)
        self.ai_input.clear()
        if self._worker is not None:
            self._worker.submit_query(text)

    def _clear_ai_chat(self) -> None:
        self.chat_display.clear()
        self.tool_log_display.clear()
        self.result_display.clear()
        if self._worker is not None:
            self._worker.clear_conversation()

    def _test_llm_connection(self) -> None:
        cfg = self._config_from_form()
        if not cfg.api_key:
            QMessageBox.information(self, "提示", "请先填写 API Key。")
            return
        self.test_conn_button.setEnabled(False)
        self.test_conn_button.setText("测试中…")
        self._test_thread = ConnectionTestWorker(cfg, self)
        self._test_thread.resultReady.connect(self._on_test_result)
        self._test_thread.finished.connect(self._on_test_finished)
        self._test_thread.start()

    def _on_test_result(self, ok: bool, message: str) -> None:
        if ok:
            QMessageBox.information(
                self, "连接成功",
                f"{message}\n\n注意：「测试连接」只验证 API 可用，不会自动保存配置。\n"
                "请点击「保存配置」后再到「AI 智能招聘查询」页使用。",
            )
        else:
            QMessageBox.warning(self, "连接失败", message)

    def _on_test_finished(self) -> None:
        self.test_conn_button.setEnabled(True)
        self.test_conn_button.setText("测试连接")
        self._test_thread = None

    def _save_llm_config(self) -> None:
        cfg = self._config_from_form()
        if not cfg.has_key:
            QMessageBox.warning(self, "未保存", "API Key 为空，请填写后再保存。")
            logger.info("保存配置被拒绝：api_key=%s", "未设置")
            return
        self._llm_config = cfg
        # 同步到后台 AgentWorker（通过队列串行生效，不重建线程/MCP 子进程）
        if self._worker is not None:
            self._worker.update_config(cfg)
        logger.info("已保存 LLM 配置：%s", cfg.describe())
        QMessageBox.information(
            self, "已保存",
            f"配置已保存并同步到 AI 查询。\n\n"
            f"服务商：{cfg.provider}\nBase URL：{cfg.base_url}\n"
            f"模型：{cfg.model}\nAPI Key：{cfg.masked_key()}",
        )

    def _config_from_form(self) -> LLMConfig:
        return LLMConfig(
            api_key=self.cfg_api_key.text().strip(),
            base_url=self.cfg_base_url.text().strip() or LLMConfig().base_url,
            model=self.cfg_model.text().strip() or LLMConfig().model,
            provider=self.cfg_provider.currentText().strip() or LLMConfig().provider,
            timeout=float(self.cfg_timeout.value()),
            max_tool_rounds=int(self.cfg_rounds.value()),
        )

    # ---------------- AI 信号处理（主线程） ----------------

    def _on_mcp_status_changed(self, text: str) -> None:
        self.mcp_status_label.setText(text)

    def _on_agent_log(self, text: str) -> None:
        self.tool_log_display.append(text)

    def _on_agent_answer(self, text: str) -> None:
        self._append_chat("assistant", text)

    def _on_tools_ready(self, tools_json: str) -> None:
        import json as _json
        try:
            tools = _json.loads(tools_json)
            names = [t.get("name", "?") for t in tools]
            self.mcp_tools_label.setText(", ".join(names) if names else "无")
        except Exception:  # noqa: BLE001
            self.mcp_tools_label.setText("解析失败")

    def _on_tool_call_logged(self, text: str) -> None:
        self.tool_log_display.append(text)

    def _on_result_data(self, data_json: str) -> None:
        if not data_json:
            return
        try:
            import json as _json
            pretty = _json.dumps(_json.loads(data_json), ensure_ascii=False, indent=2)
        except Exception:  # noqa: BLE001
            pretty = data_json
        self.result_display.clear()
        self.result_display.setPlainText(pretty)

    def _on_agent_error(self, text: str) -> None:
        self._append_chat("system", f"[错误] {text}")

    def _on_busy_changed(self, busy: bool) -> None:
        self._busy = busy
        self.send_button.setEnabled(not busy)
        self.send_button.setText("查询中…" if busy else "发送")

    def _append_chat(self, role: str, text: str) -> None:
        if role == "user":
            prefix = "<b style='color:#0057a0'>你：</b>"
        elif role == "assistant":
            prefix = "<b style='color:#0a7d3a'>AI：</b>"
        else:
            prefix = "<b style='color:#888888'>系统：</b>"
        body = _esc_multiline(text or "")
        self.chat_display.append(f"<p>{prefix}{body}</p><hr>")

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt 命名约定)
        # 程序关闭时安全释放后台线程与 MCP 子进程
        if self._worker is not None:
            self._worker.stop()
        if self._test_thread is not None and self._test_thread.isRunning():
            self._test_thread.wait(3000)
        super().closeEvent(event)


def _esc(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _esc_attr(text: str) -> str:
    return _esc(text).replace('"', "&quot;")


def _esc_multiline(text: str) -> str:
    return _esc(text).replace("\n", "<br>")


def _row(label: str, value: str) -> str:
    return f"<tr><td><b>{_esc(label)}：</b></td><td>{_esc(value)}</td></tr>"
