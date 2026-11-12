"""后台工作线程：在独立线程中运行 asyncio 事件循环，承载 LLM Agent 与 MCP 通信。

职责：
- 网络请求与 MCP 通信全部在工作线程执行，不阻塞 PyQt6 主线程；
- 通过 Qt 信号把状态/日志/回答回传主线程（UI 只通过信号更新）；
- 管理 MCP 子进程生命周期，退出时安全释放、不残留子进程；
- 串行处理查询，避免并发冲突。
"""
from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
from dataclasses import dataclass
from typing import Any, Optional

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from config import LLMConfig
from llm_agent import AgentResult, LLMAgent
from mcp_client import McpJobClient

logger = logging.getLogger("jnu.job.agent.worker")

# 队列命令标记
_STOP = object()
_CLEAR = object()


@dataclass
class _ConfigCommand:
    """通过队列传递给工作线程的配置更新命令。"""

    config: LLMConfig


class AgentWorker(QObject):
    """负责 Agent 查询的后台工作者（QObject + 独立线程事件循环）。"""

    statusChanged = pyqtSignal(str)        # MCP 连接状态
    logMessage = pyqtSignal(str)           # 事件日志
    answerReady = pyqtSignal(str)          # 最终回答
    toolsReady = pyqtSignal(str)           # 工具列表（JSON 字符串）
    toolCallLogged = pyqtSignal(str)       # 单次工具调用日志（人类可读）
    resultData = pyqtSignal(str)           # 单次工具调用的原始结果（JSON 字符串）
    queryStarted = pyqtSignal()
    queryFinished = pyqtSignal()
    errorOccurred = pyqtSignal(str)
    busyChanged = pyqtSignal(bool)

    def __init__(self, config: LLMConfig, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._config = config
        self._queue: "queue.Queue[Any]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._running = False
        self._agent: Optional[LLMAgent] = None

    # ---------------- 生命周期 ----------------

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, name="jnu-agent-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 8.0) -> None:
        """请求停止并等待线程退出；尽力释放 MCP 子进程。"""
        self._running = False
        if self._queue is not None:
            self._queue.put(_STOP)
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        if self._thread is not None and self._thread.is_alive():
            logger.warning("后台线程未在 %s 秒内退出（可能仍在等待网络响应）", timeout)

    # ---------------- 对外接口（主线程调用） ----------------

    def submit_query(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        self._queue.put(text)

    def clear_conversation(self) -> None:
        self._queue.put(_CLEAR)

    def update_config(self, config: LLMConfig) -> None:
        """安全更新后台 Agent 的配置（通过队列串行生效，不阻塞主线程、不重建线程/MCP 子进程）。"""
        self._queue.put(_ConfigCommand(config))

    # ---------------- 线程体 ----------------

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main())
        except Exception as exc:  # noqa: BLE001
            logger.exception("Agent 工作线程异常")
            self.errorOccurred.emit(f"后台线程异常：{type(exc).__name__}: {exc}")
        finally:
            self._loop.close()
            self._loop = None

    async def _main(self) -> None:
        try:
            async with McpJobClient() as client:
                self.statusChanged.emit("正在连接 MCP Server…")
                self.logMessage.emit("MCP Server 启动与连接中…")
                tools = await client.list_tools()
                self.toolsReady.emit(json.dumps(tools, ensure_ascii=False))
                self.logMessage.emit(f"MCP 工具发现成功：{[t['name'] for t in tools]}")
                self.statusChanged.emit("已连接 MCP Server")

                self._agent = LLMAgent(self._config, client, log_callback=self.logMessage.emit)
                await self._agent.discover_tools()

                loop = asyncio.get_running_loop()
                while self._running:
                    item = await loop.run_in_executor(None, self._queue.get)
                    if item is _STOP:
                        break
                    if isinstance(item, _ConfigCommand):
                        self._apply_config(item.config)
                        continue
                    if item is _CLEAR:
                        if self._agent is not None:
                            self._agent.clear_conversation()
                        self.logMessage.emit("对话历史已清空")
                        continue
                    await self._handle_query(item)

            self.statusChanged.emit("MCP Server 已断开")
        except Exception as exc:  # noqa: BLE001
            logger.exception("MCP 连接失败")
            self.statusChanged.emit("MCP 连接失败")
            self.errorOccurred.emit(f"MCP 服务启动失败：{type(exc).__name__}: {exc}")

    def _apply_config(self, config: LLMConfig) -> None:
        """在工作线程内应用新配置（串行、安全），并更新已存在的 Agent。"""
        self._config = config
        if self._agent is not None:
            self._agent.update_config(config)
        self.logMessage.emit(f"配置已更新：{config.describe()}")
        logger.info("AgentWorker 配置已更新：%s", config.describe())

    async def _handle_query(self, text: str) -> None:
        if self._agent is None:
            self.errorOccurred.emit("Agent 尚未就绪")
            return
        self.busyChanged.emit(True)
        self.queryStarted.emit()
        self.logMessage.emit(f"用户发起查询：{text}")
        logger.info("收到查询，当前配置：%s", self._config.describe())
        try:
            result: AgentResult = await self._agent.chat(text)
            self.answerReady.emit(result.answer)
            for t in result.trace:
                self.toolCallLogged.emit(self._format_trace(t))
                self.resultData.emit(json.dumps(t.result_data, ensure_ascii=False) if t.result_data is not None else "")
            if result.error:
                self.errorOccurred.emit(result.error)
        except Exception as exc:  # noqa: BLE001
            logger.exception("查询处理失败")
            self.errorOccurred.emit(f"查询失败：{type(exc).__name__}: {exc}")
        finally:
            self.busyChanged.emit(False)
            self.queryFinished.emit()

    @staticmethod
    def _format_trace(trace) -> str:
        args = json.dumps(trace.arguments, ensure_ascii=False)
        status = "失败" if trace.is_error else "成功"
        return f"[工具] {trace.name} | 参数 {args} | {status} | {trace.result_summary}"


class ConnectionTestWorker(QThread):
    """在后台测试大模型 API 连接，避免阻塞主线程。"""

    resultReady = pyqtSignal(bool, str)

    def __init__(self, config: LLMConfig, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._config = config

    def run(self) -> None:  # noqa: D102
        try:
            from openai import OpenAI

            client = OpenAI(
                api_key=self._config.api_key,
                base_url=self._config.base_url,
                timeout=self._config.timeout,
            )
            client.chat.completions.create(
                model=self._config.model,
                messages=[{"role": "user", "content": "请只回复两个字：成功"}],
                max_tokens=20,
            )
            self.resultReady.emit(True, "连接成功")
        except Exception as exc:  # noqa: BLE001
            self.resultReady.emit(False, f"{type(exc).__name__}: {exc}")
