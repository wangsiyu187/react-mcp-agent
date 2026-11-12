"""AgentWorker 配置同步测试（不启动真实线程 / MCP 子进程，使用内存队列与注入 Agent）。"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

from agent_worker import AgentWorker, _ConfigCommand
from config import LLMConfig
from llm_agent import LLMAgent


class DummyMcpClient:
    """最小 MCP 客户端桩，仅满足 LLMAgent 构造要求，不发起真实调用。"""

    async def list_tools(self):
        return []

    async def call_tool(self, name, arguments=None):
        return None


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


def _agent(config: LLMConfig) -> LLMAgent:
    return LLMAgent(config, DummyMcpClient())


def test_update_config_enqueues_command(qapp) -> None:
    worker = AgentWorker(LLMConfig(api_key=""))
    worker.update_config(LLMConfig(api_key="k1", model="m1", provider="deepseek"))
    item = worker._queue.get_nowait()
    assert isinstance(item, _ConfigCommand)
    assert item.config.api_key == "k1"


def test_apply_config_updates_worker_and_agent(qapp) -> None:
    worker = AgentWorker(LLMConfig(api_key=""))
    worker._agent = _agent(LLMConfig(api_key=""))
    new_cfg = LLMConfig(api_key="k2", model="m2", provider="openai", base_url="https://example.com")

    worker._apply_config(new_cfg)

    # 工作线程自身配置已更新
    assert worker._config.api_key == "k2"
    # Agent 实例配置已更新（关键：不再使用旧空 Key）
    assert worker._agent.config.api_key == "k2"
    assert worker._agent.config.model == "m2"
    assert worker._agent.config.provider == "openai"


def test_apply_config_resets_agent_client(qapp) -> None:
    """更新配置后，Agent 缓存的 OpenAI 客户端被重置，下次按新配置重建。"""
    worker = AgentWorker(LLMConfig(api_key=""))
    agent = _agent(LLMConfig(api_key="old"))
    agent._client = object()  # 模拟已缓存旧客户端
    worker._agent = agent

    worker._apply_config(LLMConfig(api_key="new", model="m"))

    assert worker._agent._client is None


def test_apply_config_before_agent_created_updates_worker_config(qapp) -> None:
    """Agent 尚未创建时更新配置：worker 配置先更新，后续创建 Agent 会用新配置。"""
    worker = AgentWorker(LLMConfig(api_key=""))
    new_cfg = LLMConfig(api_key="k3", model="m3")
    worker._apply_config(new_cfg)
    assert worker._config.api_key == "k3"
    assert worker._agent is None


def test_reapply_config_updates_again(qapp) -> None:
    """修改 API Key 并重新保存后，下一次查询使用最新配置（可多次覆盖）。"""
    worker = AgentWorker(LLMConfig(api_key=""))
    worker._agent = _agent(LLMConfig(api_key=""))

    worker._apply_config(LLMConfig(api_key="first", model="m"))
    worker._apply_config(LLMConfig(api_key="second", model="m2"))

    assert worker._config.api_key == "second"
    assert worker._agent.config.api_key == "second"
    assert worker._agent.config.model == "m2"