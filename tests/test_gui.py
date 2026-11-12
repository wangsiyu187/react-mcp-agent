"""PyQt6 界面初始化、后台线程与原有功能回归测试（使用 offscreen 平台，无需真实显示）。"""
from __future__ import annotations

import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication, QMessageBox

from config import LLMConfig
from database import Database
from main_window import MainWindow


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


def _wait_until(predicate, timeout_ms: float = 10000.0, interval_ms: float = 50.0) -> bool:
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(interval_ms / 1000.0)
    return False


def test_main_window_tabs_and_management_regression(sample_db_path: Path, qapp) -> None:
    """原有第五次作业功能回归：主窗口三个标签页，招聘管理页正常加载数据。"""
    db = Database(sample_db_path)
    win = MainWindow(db)
    try:
        assert win.tabs.count() == 3
        assert win.tabs.tabText(0) == "招聘信息管理"
        assert win.tabs.tabText(1) == "AI 智能招聘查询"
        assert win.tabs.tabText(2) == "大模型配置"

        # 招聘管理页从数据库加载了全部岗位并填入表格
        assert len(win._all_jobs) == 4
        assert win.table.rowCount() == 4
        # 筛选面板与详情区存在
        assert win.search_edit is not None
        assert win.detail is not None
    finally:
        win.close()
        db.close()


def test_ai_worker_connects_and_discovers_tools(tmp_path: Path, qapp) -> None:
    """后台线程启动 MCP Server 并发现工具（真实 STDIO 连接）。"""
    db = Database(tmp_path / "gui.db")
    win = MainWindow(db)
    try:
        # 等待后台线程完成 MCP 连接与工具发现
        connected = _wait_until(lambda: win.mcp_status_label.text() == "已连接 MCP Server")
        assert connected, f"连接超时，当前状态：{win.mcp_status_label.text()}"
        assert "search_jobs" in win.mcp_tools_label.text()
        assert "get_job_detail" in win.mcp_tools_label.text()
    finally:
        win.close()
        db.close()


def test_save_config_syncs_to_worker(tmp_path: Path, qapp, monkeypatch) -> None:
    """保存配置后：主窗口与后台 AgentWorker 均获得最新参数（关键回归）。"""
    db = Database(tmp_path / "gui_save.db")
    win = MainWindow(db)
    try:
        assert _wait_until(lambda: win.mcp_status_label.text() == "已连接 MCP Server")

        win.cfg_api_key.setText("sk-test-123456")
        win.cfg_base_url.setText("https://api.deepseek.com")
        win.cfg_model.setText("deepseek-v4-pro")
        idx = win.cfg_provider.findText("deepseek")
        if idx >= 0:
            win.cfg_provider.setCurrentIndex(idx)

        captured = []
        monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: captured.append(a))
        monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: captured.append(a))

        win._save_llm_config()

        # 主线程配置立即更新
        assert win._llm_config.api_key == "sk-test-123456"
        assert win._llm_config.model == "deepseek-v4-pro"
        # 后台 worker 配置经队列同步（轮询等待）
        synced = _wait_until(lambda: win._worker._config.api_key == "sk-test-123456")
        assert synced, "后台 AgentWorker 未获得最新配置"
        assert captured, "应弹出保存成功提示"
    finally:
        win.close()
        db.close()


def test_send_query_without_config_shows_hint(tmp_path: Path, qapp, monkeypatch) -> None:
    """未配置 API Key 时发送查询应明确提示，不进入聊天流程。"""
    db = Database(tmp_path / "gui_nokey.db")
    win = MainWindow(db)
    try:
        assert _wait_until(lambda: win.mcp_status_label.text() == "已连接 MCP Server")
        win._llm_config = LLMConfig()  # 模拟未保存任何配置
        win.ai_input.setText("帮我查岗位")

        captured = []
        monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: captured.append(a))

        win._send_ai_query()

        assert captured, "应弹出提示"
        title = captured[0][1]  # information(parent, title, text)
        assert ("配置" in title) or ("API Key" in title)
        # 未追加用户消息，也未提交查询
        assert "你：" not in win.chat_display.toPlainText()
    finally:
        win.close()
        db.close()
