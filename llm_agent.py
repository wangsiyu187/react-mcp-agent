"""大模型 Agent：通过 OpenAI 兼容接口 + 真实 MCP 工具完成招聘查询。

完整链路：用户自然语言 → 大模型返回 tool_calls → 经 mcp_client 通过 STDIO 调用 MCP Server
→ 查询 SQLite → 将真实结果回填 → 大模型生成自然语言回答。

本模块不依赖 Qt；模型调用使用注入的 OpenAI 客户端（测试时可替换为 Mock）。
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from config import LLMConfig
from system_prompt import SYSTEM_PROMPT

logger = logging.getLogger("jnu.job.agent")

# 日志回调：Agent 用它输出事件日志（由调用方决定去向：UI / 文件 / 测试收集器）
LogCallback = Callable[[str], None]


@dataclass
class ToolCallTrace:
    """一次 MCP 工具调用的记录。"""

    name: str
    arguments: dict[str, Any]
    is_error: bool
    result_summary: str = ""
    result_data: Any = None


@dataclass
class AgentResult:
    """一次查询的最终结果。"""

    answer: str
    trace: list[ToolCallTrace] = field(default_factory=list)
    rounds: int = 0
    error: Optional[str] = None


def convert_mcp_tools_to_openai(mcp_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把 MCP 工具列表转换为 OpenAI 兼容的 function tools 格式。

    输入：mcp_client.list_tools() 返回的 [{name, description, input_schema}, ...]
    输出：[{"type": "function", "function": {"name", "description", "parameters"}}, ...]
    """
    result: list[dict[str, Any]] = []
    for tool in mcp_tools:
        result.append(
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", "") or "",
                    "parameters": tool.get("input_schema") or {"type": "object", "properties": {}},
                },
            }
        )
    return result


def parse_tool_arguments(arguments_text: Optional[str]) -> tuple[dict[str, Any], Optional[str]]:
    """解析大模型返回的工具参数 JSON。返回 (参数字典, 错误信息或 None)。"""
    text = (arguments_text or "").strip()
    if not text:
        return {}, None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        return {}, f"参数 JSON 解析失败：{exc}"
    if not isinstance(parsed, dict):
        return {}, "参数不是 JSON 对象"
    return parsed, None


def build_extra_body(config: LLMConfig) -> Optional[dict[str, Any]]:
    """按服务商构建请求的 extra_body（DeepSeek 特有参数按 provider 处理）。

    - provider == "deepseek"：默认附加非思考模式参数
      `{"thinking": {"type": "disabled"}}`，保证工具调用稳定；
      用户自定义 config.extra_body 可覆盖该默认值。
    - 其他服务商（openai / other）：仅透传用户自定义 config.extra_body，
      不附加任何 DeepSeek 特有参数，保持 OpenAI 兼容接口纯净。
    """
    provider = (config.provider or "").strip().lower()
    extra: dict[str, Any] = {}
    if config.extra_body:
        extra.update(config.extra_body)
    if provider == "deepseek":
        extra.setdefault("thinking", {"type": "disabled"})
    return extra or None


def _summarize_result(data: Any, max_len: int = 300) -> str:
    """生成工具返回结果的人读摘要（用于 trace 日志）。"""
    try:
        if isinstance(data, dict):
            if "count" in data:
                return f"count={data['count']}"
            if "job" in data:
                job = data["job"]
                if isinstance(job, dict):
                    return f"job.id={job.get('id')} title={job.get('title')}"
                return "job=None"
        if isinstance(data, list):
            return f"list[{len(data)}]"
        return str(data)[:max_len]
    except Exception:  # noqa: BLE001
        return str(data)[:max_len]


class LLMAgent:
    """招聘查询 Agent：动态发现 MCP 工具并执行工具调用循环。"""

    def __init__(
        self,
        config: LLMConfig,
        mcp_client: Any,
        client: Any = None,
        log_callback: Optional[LogCallback] = None,
    ):
        self.config = config
        self.mcp_client = mcp_client           # McpJobClient（含 list_tools / call_tool）
        self._log = log_callback or (lambda _msg: None)
        self.messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        self._openai_tools: list[dict[str, Any]] = []
        self._tool_names: set[str] = set()

        # OpenAI 客户端惰性构造：MCP 连接/工具发现阶段不依赖 API Key，
        # 只有真正调用大模型时才校验并创建客户端（测试可注入 fake client）。
        self._client = client
        self._injected_client = client  # 外部注入的客户端在配置更新时保留

        self._log(f"创建 LLMAgent：{self.config.describe()}")

    def update_config(self, config: LLMConfig) -> None:
        """更新大模型配置并重置自建的 OpenAI 客户端。

        对话历史保留；下次调用模型时按新配置（含新 API Key/Base URL/Model/Provider）
        重新构造客户端。若客户端为外部注入（测试用），则保留。
        """
        self.config = config
        self._client = self._injected_client  # 注入的客户端保留；自建客户端重置为 None
        self._log(f"LLMAgent 配置已更新：{config.describe()}")

    # ---------------- 工具发现 ----------------

    async def discover_tools(self) -> list[dict[str, Any]]:
        """通过真实 MCP Client 获取工具列表并转换为 OpenAI 格式。"""
        mcp_tools = await self.mcp_client.list_tools()
        self._openai_tools = convert_mcp_tools_to_openai(mcp_tools)
        self._tool_names = {t["function"]["name"] for t in self._openai_tools}
        self._log(f"MCP 工具发现成功：{sorted(self._tool_names)}")
        return mcp_tools

    def clear_conversation(self) -> None:
        """清空对话历史（保留 system 提示词）。"""
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    # ---------------- 主流程 ----------------

    async def chat(self, user_text: str) -> AgentResult:
        """处理一条用户消息，返回最终结果。

        若工具尚未发现则先自动发现（防御性）。支持多轮工具调用与一次多个 tool_calls。
        """
        if not self._openai_tools:
            await self.discover_tools()

        self.messages.append({"role": "user", "content": user_text})
        trace: list[ToolCallTrace] = []

        for round_idx in range(1, self.config.max_tool_rounds + 1):
            try:
                response = await asyncio.to_thread(
                    self._request_model, self.messages, self._openai_tools
                )
            except Exception as exc:  # noqa: BLE001 —— 统一转成用户可读错误
                msg = self._format_api_error(exc)
                self._log(f"模型调用异常：{type(exc).__name__}")
                result = AgentResult(answer=msg, trace=trace, rounds=round_idx, error=msg)
                # 回退：移除刚加入的 user 消息？保留便于上下文；这里保留历史。
                return result

            message = response.choices[0].message
            tool_calls = message.tool_calls or []

            if not tool_calls:
                # 无工具调用：视为最终回答
                answer = message.content or ""
                if not answer.strip():
                    answer = "（模型未返回文本内容，请重试或检查模型配置。）"
                self.messages.append({"role": "assistant", "content": answer})
                return AgentResult(answer=answer, trace=trace, rounds=round_idx)

            # 保存包含 tool_calls 的 assistant 消息（仅保留标准字段，兼容 DeepSeek 思考模式）
            self.messages.append(
                {
                    "role": "assistant",
                    "content": message.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": getattr(tc, "type", "function") or "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments or "{}",
                            },
                        }
                        for tc in tool_calls
                    ],
                }
            )

            # 逐个执行工具调用（支持一次多个）
            for tc in tool_calls:
                name = tc.function.name
                arguments, parse_err = parse_tool_arguments(tc.function.arguments)
                self._log(f"大模型选择工具：{name}")

                if parse_err is not None:
                    tool_content = json.dumps({"error": parse_err}, ensure_ascii=False)
                    trace.append(ToolCallTrace(name=name, arguments={}, is_error=True, result_summary=parse_err))
                else:
                    self._log(f"MCP 工具调用参数：{json.dumps(arguments, ensure_ascii=False)}")
                    tool_content, t = await self._execute_tool(name, arguments)
                    trace.append(t)

                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": tool_content,
                    }
                )

        # 超出最大轮数
        msg = f"查询步骤超过最大工具调用轮数（{self.config.max_tool_rounds}），已停止，请简化问题重试。"
        return AgentResult(answer=msg, trace=trace, rounds=self.config.max_tool_rounds, error=msg)

    # ---------------- 内部方法 ----------------

    def _request_model(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]):
        """同步调用 OpenAI 兼容接口（由 asyncio.to_thread 在工作线程中执行）。

        保留 OpenAI 兼容 Function Calling 逻辑；DeepSeek 特有参数通过
        extra_body 按 provider 处理（默认非思考模式）。
        """
        client = self._get_client()
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        extra_body = build_extra_body(self.config)
        if extra_body:
            kwargs["extra_body"] = extra_body
        return client.chat.completions.create(**kwargs)

    def _get_client(self):
        """惰性创建 OpenAI 客户端；未配置 API Key 时给出明确错误。"""
        if self._client is not None:
            return self._client
        if not (self.config.api_key or "").strip():
            raise ValueError("未配置 API Key，请先在大模型配置页填写 API Key 并保存。")
        from openai import OpenAI

        self._client = OpenAI(
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            timeout=self.config.timeout,
        )
        return self._client

    async def _execute_tool(self, name: str, arguments: dict[str, Any]) -> tuple[str, ToolCallTrace]:
        """校验并调用 MCP 工具，返回 (回填给模型的 JSON 文本, trace)。"""
        if name not in self._tool_names:
            content = json.dumps({"error": f"未知工具：{name}"}, ensure_ascii=False)
            self._log(f"未知工具被请求：{name}")
            return content, ToolCallTrace(name=name, arguments=arguments, is_error=True, result_summary=f"未知工具 {name}")

        try:
            result = await self.mcp_client.call_tool(name, arguments)
        except Exception as exc:  # noqa: BLE001
            content = json.dumps({"error": f"MCP 调用异常：{type(exc).__name__}: {exc}"}, ensure_ascii=False)
            self._log(f"MCP 工具调用异常：{type(exc).__name__}")
            return content, ToolCallTrace(name=name, arguments=arguments, is_error=True, result_summary=str(exc))

        if result.is_error:
            content = json.dumps({"error": "工具调用失败", "detail": result.raw_text or ""}, ensure_ascii=False)
            self._log(f"MCP 工具返回 is_error：{name}")
            return content, ToolCallTrace(name=name, arguments=arguments, is_error=True, result_summary=result.raw_text[:200], result_data={"error": result.raw_text or ""})

        # 业务级错误（如"岗位不存在"）：如实回传，让模型据实回答
        data = result.data
        if isinstance(data, dict) and data.get("error"):
            content = json.dumps(data, ensure_ascii=False)
            self._log(f"MCP 查询完成（业务错误）：{name} -> {_summarize_result(data)}")
            return content, ToolCallTrace(name=name, arguments=arguments, is_error=False, result_summary=str(data.get('error'))[:200], result_data=data)

        content = json.dumps(data, ensure_ascii=False)
        summary = _summarize_result(data)
        self._log(f"MCP 查询完成：{name} -> {summary}")
        if isinstance(data, dict) and "count" in data:
            self._log(f"查询结果数量：{data['count']}")
        return content, ToolCallTrace(name=name, arguments=arguments, is_error=False, result_summary=summary, result_data=data)

    @staticmethod
    def _format_api_error(exc: Exception) -> str:
        """把 OpenAI 异常转为用户可读提示。"""
        name = type(exc).__name__
        text = str(exc)
        # 未配置 / 无效凭据的明确提示
        if "API Key" in text or "credentials" in text.lower() or "api_key" in text:
            return f"API Key 未配置或无效：{text}"
        try:
            from openai import (
                APIConnectionError,
                APITimeoutError,
                AuthenticationError,
                PermissionDeniedError,
                RateLimitError,
            )

            if isinstance(exc, AuthenticationError):
                return "API 鉴权失败：请检查 API Key 是否正确。"
            if isinstance(exc, PermissionDeniedError):
                return "API 权限不足：请检查账号权限。"
            if isinstance(exc, APITimeoutError):
                return "请求超时：请稍后重试或增大超时时间。"
            if isinstance(exc, RateLimitError):
                return "请求过于频繁或额度不足：请稍后重试。"
            if isinstance(exc, APIConnectionError):
                return "无法连接 API 服务：请检查网络或 Base URL。"
        except ImportError:
            pass
        return f"API 请求失败（{name}）：{exc}"
