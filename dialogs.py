"""对话框：岗位编辑、求职意向编辑、平台基础网址设置。"""
from __future__ import annotations

from datetime import date
from typing import Optional

from PyQt6.QtCore import QDate, Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from models import Job, Preference, STATUSES, DEFAULT_STATUS


class NullableSpinBox(QWidget):
    """可选空的整数输入框：值为 0 时表示空（显示为「未填写」）。"""

    def __init__(self, parent=None, placeholder: str = "未填写", maximum: int = 10_000_000):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.spin = QSpinBox(self)
        self.spin.setRange(0, maximum)
        self.spin.setSpecialValueText(placeholder)
        self.spin.setSingleStep(1000)
        layout.addWidget(self.spin)

    def value(self) -> Optional[int]:
        v = self.spin.value()
        return v if v > 0 else None

    def set_value(self, value: Optional[int]) -> None:
        self.spin.setValue(value if value is not None and value > 0 else 0)


class OptionalDateWidget(QWidget):
    """可选的日期输入：复选框启用后生效。"""

    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.check = QCheckBox(label, self)
        self.edit = QDateEdit(QDate.currentDate(), self)
        self.edit.setCalendarPopup(True)
        self.edit.setDisplayFormat("yyyy-MM-dd")
        self.edit.setEnabled(False)
        self.check.toggled.connect(self.edit.setEnabled)
        layout.addWidget(self.check)
        layout.addWidget(self.edit, 1)

    def value(self) -> Optional[date]:
        if not self.check.isChecked():
            return None
        qd = self.edit.date()
        return date(qd.year(), qd.month(), qd.day())

    def set_value(self, value: Optional[date]) -> None:
        if value is None:
            self.check.setChecked(False)
            self.edit.setDate(QDate.currentDate())
        else:
            self.check.setChecked(True)
            self.edit.setDate(QDate(value.year, value.month, value.day))


class JobEditDialog(QDialog):
    """新增 / 编辑岗位。"""

    def __init__(self, parent=None, job: Optional[Job] = None):
        super().__init__(parent)
        self.setWindowTitle("编辑岗位" if job else "新增岗位")
        self.resize(560, 640)

        self.title_edit = QLineEdit(self)
        self.company_edit = QLineEdit(self)
        self.location_edit = QLineEdit(self)
        self.industry_edit = QLineEdit(self)
        self.salary_min = NullableSpinBox(parent=self)
        self.salary_max = NullableSpinBox(parent=self)
        self.education_edit = QLineEdit(self)
        self.major_edit = QLineEdit(self)
        self.tags_edit = QLineEdit(self)
        self.url_edit = QLineEdit(self)
        self.deadline = OptionalDateWidget("截止日期", self)
        self.description_edit = QTextEdit(self)
        self.description_edit.setFixedHeight(80)
        self.favorite_check = QCheckBox("已收藏", self)
        self.status_combo = QComboBox(self)
        self.status_combo.addItems(STATUSES)
        self.applied_date = OptionalDateWidget("投递日期", self)
        self.note_edit = QTextEdit(self)
        self.note_edit.setFixedHeight(60)

        form = QFormLayout()
        form.addRow("职位名称 *", self.title_edit)
        form.addRow("公司名称 *", self.company_edit)
        form.addRow("工作地点", self.location_edit)
        form.addRow("行业", self.industry_edit)
        form.addRow("薪资最低 (元/月)", self.salary_min)
        form.addRow("薪资最高 (元/月)", self.salary_max)
        form.addRow("学历要求", self.education_edit)
        form.addRow("专业要求", self.major_edit)
        form.addRow("岗位标签 (逗号分隔)", self.tags_edit)
        form.addRow("招聘链接", self.url_edit)
        form.addRow("截止日期", self.deadline)
        form.addRow("岗位描述", self.description_edit)
        form.addRow("", self.favorite_check)
        form.addRow("投递状态", self.status_combo)
        form.addRow("投递日期", self.applied_date)
        form.addRow("备注", self.note_edit)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        self.buttons.accepted.connect(self._on_accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.buttons)

        if job is not None:
            self._load(job)

    def _load(self, job: Job) -> None:
        self.title_edit.setText(job.title)
        self.company_edit.setText(job.company)
        self.location_edit.setText(job.location)
        self.industry_edit.setText(job.industry)
        self.salary_min.set_value(job.salary_min)
        self.salary_max.set_value(job.salary_max)
        self.education_edit.setText(job.education)
        self.major_edit.setText(job.major)
        self.tags_edit.setText(job.tags)
        self.url_edit.setText(job.url)
        self.deadline.set_value(job.deadline)
        self.description_edit.setPlainText(job.description)
        self.favorite_check.setChecked(job.favorite)
        idx = self.status_combo.findText(job.status)
        if idx >= 0:
            self.status_combo.setCurrentIndex(idx)
        self.applied_date.set_value(job.applied_date)
        self.note_edit.setPlainText(job.note)

    def _on_accept(self) -> None:
        if not self.title_edit.text().strip():
            QMessageBox.warning(self, "提示", "职位名称不能为空。")
            return
        if not self.company_edit.text().strip():
            QMessageBox.warning(self, "提示", "公司名称不能为空。")
            return
        self.accept()

    def get_job(self) -> Job:
        return Job(
            title=self.title_edit.text().strip(),
            company=self.company_edit.text().strip(),
            location=self.location_edit.text().strip(),
            industry=self.industry_edit.text().strip(),
            salary_min=self.salary_min.value(),
            salary_max=self.salary_max.value(),
            education=self.education_edit.text().strip(),
            major=self.major_edit.text().strip(),
            tags=self.tags_edit.text().strip(),
            url=self.url_edit.text().strip(),
            deadline=self.deadline.value(),
            description=self.description_edit.toPlainText(),
            favorite=self.favorite_check.isChecked(),
            status=self.status_combo.currentText(),
            applied_date=self.applied_date.value(),
            note=self.note_edit.toPlainText(),
        )


class PreferenceDialog(QDialog):
    """编辑求职意向。"""

    def __init__(self, parent=None, pref: Optional[Preference] = None):
        super().__init__(parent)
        self.setWindowTitle("编辑求职意向")
        self.resize(480, 240)

        self.location_edit = QLineEdit(self)
        self.location_edit.setPlaceholderText("如：广州,深圳,珠海（逗号分隔）")
        self.industry_edit = QLineEdit(self)
        self.industry_edit.setPlaceholderText("如：互联网")
        self.salary_min = NullableSpinBox(parent=self)
        self.keywords_edit = QLineEdit(self)
        self.keywords_edit.setPlaceholderText("如：Python,后端,数据分析（逗号分隔）")

        form = QFormLayout()
        form.addRow("期望地点", self.location_edit)
        form.addRow("期望行业", self.industry_edit)
        form.addRow("期望薪资下限 (元/月)", self.salary_min)
        form.addRow("期望职位关键词/技能", self.keywords_edit)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.buttons)

        if pref is not None:
            self.location_edit.setText(pref.location)
            self.industry_edit.setText(pref.industry)
            self.salary_min.set_value(pref.salary_min)
            self.keywords_edit.setText(pref.keywords)

    def get_preference(self) -> Preference:
        return Preference(
            location=self.location_edit.text().strip(),
            industry=self.industry_edit.text().strip(),
            salary_min=self.salary_min.value(),
            keywords=self.keywords_edit.text().strip(),
        )


class SettingDialog(QDialog):
    """设置平台基础网址。"""

    def __init__(self, parent=None, base_url: str = ""):
        super().__init__(parent)
        self.setWindowTitle("设置平台基础网址")
        self.resize(480, 120)

        self.url_edit = QLineEdit(self)
        self.url_edit.setPlaceholderText("留空表示不设置；仅用于拼接相对链接")
        self.url_edit.setText(base_url)

        form = QFormLayout()
        form.addRow("平台基础网址", self.url_edit)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.buttons)

    def get_base_url(self) -> str:
        return self.url_edit.text().strip()
