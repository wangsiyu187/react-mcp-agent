"""llm_agent.py 测试：使用 Mock 模型响应，验证真实 MCP 工具调用流程与各种边界情况。"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx2
import pytest
from openai import APITimeoutError, AuthenticationError

from config import LLMConfig
from llm_agent import (
    AgentResult,
    LLMAgent,
    build_extra_body,
    convert_mcp_tools_to_openai,
    parse_tool_arguments,
)
from mcp_client import McpJobClient, ToolCallResult


# ---------------- Mock 模型响应 ----------------

class _FakeFunction:
    def __init__(self, name: str, arguments: str):
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, call_id: str, name: str, arguments: str):
        self.id = call_id
        self.type = "function"
        self.function = _FakeFunction(name, arguments)


class _FakeMessage:
    def __init__(self, content: str | None = None, tool_calls: list[_FakeToolCall] | None = None):
        self.content = content
        self.tool_calls = tool_calls or []


class _FakeChoice:
    def __init__(self, message: _FakeMessage):
        self.message = message


class _FakeResponse:
    def __init__(self, message: _FakeMessage):
        self.choices = [_FakeChoice(message)]


class _FakeCompletions:
    def __init__(self, script: list[Any]):
        self.script = list(script)
        self.calls: list[dict] = []

    def create(self, **kwargs) -> _FakeResponse:
        self.calls.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return _FakeResponse(item)


class FakeOpenAI:
    def __init__(self, script: list[Any]):
        self.chat = type("Chat", (), {"completions": _FakeCompletions(script)})()


def msg(content: str | None = None, tool_calls: list[_FakeToolCall] | None = None) -> _FakeMessage:
    return _FakeMessage(content=content, tool_calls=tool_calls)


def tc(call_id: str, name: str, arguments: str) -> _FakeToolCall:
    return _FakeToolCall(call_id, name, arguments)


class FakeMcpClient:
    """伪造 MCP 客户端：返回预设工具与结果，不启动子进程。"""

    def __init__(self, tools: list[dict] | None = None, results: dict[str, Any] | None = None):
        self.tools = tools or [
            {
                "name": "search_jobs",
                "description": "按条件检索招聘岗位",
                "input_schema": {"type": "object", "properties": {"keyword": {"type": "string"}}},
            },
            {
                "name": "get_job_detail",
                "description": "按 id 查询岗位详情",
                "input_schema": {"type": "object", "properties": {"job_id": {"type": "integer"}}, "required": ["job_id"]},
            },
        ]
        self.results = results or {}
        self.calls: list[tuple[str, dict]] = []

    async def list_tools(self) -> list[dict]:
        return self.tools

    async def call_tool(self, name: str, arguments: dict | None = None) -> ToolCallResult:
        self.calls.append((name, arguments or {}))
        handler = self.results.get(name)
        if callable(handler):
            return handler(arguments or {})
        if isinstance(handler, ToolCallResult):
            return handler
        return ToolCallResult(is_error=False, data={"count": 0, "jobs": []})


def _config() -> LLMConfig:
    return LLMConfig(api_key="test-key", base_url="https://example.com", model="test-model")


# ---------------- 纯函数测试 ----------------


def test_convert_mcp_tools_to_openai() -> None:
    mcp_tools = [
        {"name": "search_jobs", "description": "检索岗位", "input_schema": {"type": "object"}},
        {"name": "get_job_detail", "description": "详情", "input_schema": {"type": "object"}},
    ]
    converted = convert_mcp_tools_to_openai(mcp_tools)
    assert len(converted) == 2
    assert converted[0]["type"] == "function"
    assert converted[0]["function"]["name"] == "search_jobs"
    assert converted[0]["function"]["parameters"] == {"type": "object"}


def test_parse_tool_arguments() -> None:
    args, err = parse_tool_arguments('{"keyword": "Python"}')
    assert err is None and args == {"keyword": "Python"}

    _, err = parse_tool_arguments("not-json")
    assert err is not None and "JSON" in err

    _, err = parse_tool_arguments("[1,2]")
    assert err is not None

    args, err = parse_tool_arguments("")
    assert err is None and args == {}


# ---------------- 非思考模式 / 服务商差异测试 ----------------


def test_build_extra_body_deepseek() -> None:
    """DeepSeek 默认附加非思考模式参数。"""
    cfg = LLMConfig(api_key="k", provider="deepseek")
    assert build_extra_body(cfg) == {"thinking": {"type": "disabled"}}


def test_build_extra_body_non_deepseek() -> None:
    """非 DeepSeek 服务商不附加 DeepSeek 特有参数。"""
    cfg = LLMConfig(api_key="k", provider="openai")
    assert build_extra_body(cfg) is None

    cfg = LLMConfig(api_key="k", provider="other")
    assert build_extra_body(cfg) is None


def test_build_extra_body_custom_override() -> None:
    """用户自定义 extra_body 优先；DeepSeek 默认值可被覆盖。"""
    cfg = LLMConfig(api_key="k", provider="deepseek", extra_body={"thinking": {"type": "enabled"}})
    assert build_extra_body(cfg) == {"thinking": {"type": "enabled"}}

    # 非 DeepSeek：仅透传用户自定义 extra_body
    cfg = LLMConfig(api_key="k", provider="openai", extra_body={"temperature": 0.1})
    assert build_extra_body(cfg) == {"temperature": 0.1}


def test_deepseek_request_includes_extra_body() -> None:
    """DeepSeek 请求实际携带 extra_body 非思考模式参数。"""
    model = FakeOpenAI([msg(content="你好，我是招聘查询助手。")])
    agent = LLMAgent(
        LLMConfig(api_key="k", base_url="https://api.deepseek.com", model="deepseek-v4-pro", provider="deepseek"),
        FakeMcpClient(),
        client=model,
    )
    asyncio.run(agent.chat("你好"))
    call_kwargs = model.chat.completions.calls[0]
    assert call_kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
    # FakeMcpClient 默认提供工具，chat 会自动发现并传入 tools
    assert "tools" in call_kwargs
    assert call_kwargs["tool_choice"] == "auto"


def test_openai_request_excludes_extra_body() -> None:
    """非 DeepSeek 请求不携带 extra_body，保持 OpenAI 兼容接口纯净。"""
    model = FakeOpenAI([msg(content="你好。")])
    agent = LLMAgent(
        LLMConfig(api_key="k", base_url="https://api.openai.com", model="gpt-4o", provider="openai"),
        FakeMcpClient(),
        client=model,
    )
    asyncio.run(agent.chat("你好"))
    call_kwargs = model.chat.completions.calls[0]
    assert "extra_body" not in call_kwargs


def test_deepseek_tool_call_request_has_extra_body_and_tools() -> None:
    """DeepSeek 工具调用请求同时携带 tools 与 extra_body，Function Calling 逻辑保留。"""
    model = FakeOpenAI([
        msg(tool_calls=[tc("c1", "search_jobs", '{"keyword": "Python"}')]),
        msg(content="找到岗位"),
    ])
    agent = LLMAgent(
        LLMConfig(api_key="k", base_url="https://api.deepseek.com", model="deepseek-v4-pro", provider="deepseek"),
        FakeMcpClient(results={"search_jobs": ToolCallResult(is_error=False, data={"count": 0, "jobs": []})}),
        client=model,
    )
    asyncio.run(agent.chat("找 Python 岗位"))
    first_kwargs = model.chat.completions.calls[0]
    assert first_kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "tools" in first_kwargs
    assert first_kwargs["tool_choice"] == "auto"
    # 第二轮（工具结果回填后）仍携带 extra_body
    second_kwargs = model.chat.completions.calls[1]
    assert second_kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
    tool_msgs = [m for m in second_kwargs["messages"] if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["tool_call_id"] == "c1"


# ---------------- 工具调用循环测试（FakeMcpClient） ----------------


def test_single_tool_call() -> None:
    mcp = FakeMcpClient(results={"search_jobs": ToolCallResult(is_error=False, data={"count": 1, "jobs": [{"id": 1, "title": "Python 后端工程师"}]})})
    model = FakeOpenAI([
        msg(tool_calls=[tc("c1", "search_jobs", '{"keyword": "Python"}')]),
        msg(content="找到 1 个岗位：Python 后端工程师"),
    ])
    agent = LLMAgent(_config(), mcp, client=model)

    result = asyncio.run(agent.chat("找 Python 岗位"))
    assert result.answer == "找到 1 个岗位：Python 后端工程师"
    assert len(result.trace) == 1
    assert result.trace[0].name == "search_jobs"
    assert result.trace[0].is_error is False
    assert mcp.calls == [("search_jobs", {"keyword": "Python"})]
    # 模型被调用了两次（工具调用前一次 + 拿到结果后再一次）
    assert len(model.chat.completions.calls) == 2
    # 第二次调用携带了 role=tool 的上下文
    second_messages = model.chat.completions.calls[1]["messages"]
    assert any(m.get("role") == "tool" and m.get("tool_call_id") == "c1" for m in second_messages)


def test_multiple_tool_calls() -> None:
    mcp = FakeMcpClient(results={
        "search_jobs": ToolCallResult(is_error=False, data={"count": 1, "jobs": [{"id": 1, "title": "算法工程师"}]}),
        "get_job_detail": ToolCallResult(is_error=False, data={"job": {"id": 1, "title": "算法工程师", "description": "负责算法研发"}}),
    })
    model = FakeOpenAI([
        msg(tool_calls=[
            tc("c1", "search_jobs", '{"location": "北京"}'),
            tc("c2", "get_job_detail", '{"job_id": 1}'),
        ]),
        msg(content="北京有算法工程师岗位"),
    ])
    agent = LLMAgent(_config(), mcp, client=model)

    result = asyncio.run(agent.chat("北京有什么算法岗位"))
    assert [t.name for t in result.trace] == ["search_jobs", "get_job_detail"]
    assert len(mcp.calls) == 2
    assert result.answer == "北京有算法工程师岗位"


def test_tool_result_then_model_again() -> None:
    """工具返回结果后，模型被再次调用以生成最终回答。"""
    mcp = FakeMcpClient(results={"search_jobs": ToolCallResult(is_error=False, data={"count": 0, "jobs": []})})
    model = FakeOpenAI([
        msg(tool_calls=[tc("c1", "search_jobs", '{"keyword": "不存在"}')]),
        msg(content="没有找到符合条件的岗位"),
    ])
    agent = LLMAgent(_config(), mcp, client=model)
    result = asyncio.run(agent.chat("查一下不存在的岗位"))
    assert result.answer == "没有找到符合条件的岗位"
    assert len(model.chat.completions.calls) == 2


def test_no_tool_call_direct_reply() -> None:
    mcp = FakeMcpClient()
    model = FakeOpenAI([msg(content="你好，我是招聘查询助手。")])
    agent = LLMAgent(_config(), mcp, client=model)
    result = asyncio.run(agent.chat("你好"))
    assert result.answer == "你好，我是招聘查询助手。"
    assert result.trace == []
    assert mcp.calls == []
    assert len(model.chat.completions.calls) == 1


def test_parse_error() -> None:
    mcp = FakeMcpClient()
    model = FakeOpenAI([
        msg(tool_calls=[tc("c1", "search_jobs", "not-json")]),
        msg(content="参数有问题"),
    ])
    agent = LLMAgent(_config(), mcp, client=model)
    result = asyncio.run(agent.chat("查岗位"))
    assert len(result.trace) == 1
    assert result.trace[0].is_error is True
    assert "JSON" in result.trace[0].result_summary
    # 参数解析失败时不调用 MCP
    assert mcp.calls == []


def test_unknown_tool() -> None:
    mcp = FakeMcpClient()
    model = FakeOpenAI([
        msg(tool_calls=[tc("c1", "no_such_tool", '{}')]),
        msg(content="未知工具"),
    ])
    agent = LLMAgent(_config(), mcp, client=model)
    result = asyncio.run(agent.chat("查岗位"))
    assert result.trace[0].is_error is True
    assert "未知工具" in result.trace[0].result_summary
    assert mcp.calls == []


def test_mcp_business_error() -> None:
    """MCP 返回业务错误（如岗位不存在）时，如实回传。"""
    mcp = FakeMcpClient(results={"get_job_detail": ToolCallResult(is_error=False, data={"job": None, "error": "岗位 999 不存在"})})
    model = FakeOpenAI([
        msg(tool_calls=[tc("c1", "get_job_detail", '{"job_id": 999}')]),
        msg(content="该岗位不存在"),
    ])
    agent = LLMAgent(_config(), mcp, client=model)
    result = asyncio.run(agent.chat("999 岗位详情"))
    assert result.trace[0].is_error is False
    assert "不存在" in result.trace[0].result_summary


def test_api_timeout() -> None:
    req = httpx2.Request("POST", "https://example.com/chat/completions")
    mcp = FakeMcpClient()
    model = FakeOpenAI([APITimeoutError(request=req)])
    agent = LLMAgent(_config(), mcp, client=model)
    result = asyncio.run(agent.chat("查岗位"))
    assert result.error is not None
    assert "超时" in result.answer


def test_api_auth_error() -> None:
    req = httpx2.Request("POST", "https://example.com/chat/completions")
    resp = httpx2.Response(401, request=req)
    mcp = FakeMcpClient()
    model = FakeOpenAI([AuthenticationError("bad key", response=resp, body=None)])
    agent = LLMAgent(_config(), mcp, client=model)
    result = asyncio.run(agent.chat("查岗位"))
    assert result.error is not None
    assert "鉴权失败" in result.answer


def test_missing_api_key() -> None:
    """未配置 API Key（未注入客户端）时，返回明确错误且不发起网络请求。"""
    mcp = FakeMcpClient()
    agent = LLMAgent(LLMConfig(api_key="", base_url="https://x", model="m"), mcp)
    result = asyncio.run(agent.chat("查岗位"))
    assert result.error is not None
    assert "API Key" in result.answer


def test_multi_turn_context() -> None:
    mcp = FakeMcpClient(results={"search_jobs": ToolCallResult(is_error=False, data={"count": 1, "jobs": [{"id": 2, "title": "数据分析师"}]})})
    model = FakeOpenAI([
        msg(tool_calls=[tc("c1", "search_jobs", '{"keyword": "数据"}')]),
        msg(content="找到 1 个数据分析岗位"),
        msg(content="其中薪资 4000 以上的有 1 个"),
    ])
    agent = LLMAgent(_config(), mcp, client=model)

    r1 = asyncio.run(agent.chat("帮我找数据分析岗位"))
    assert r1.answer == "找到 1 个数据分析岗位"
    # 第二轮：模型直接回答（无工具调用），但历史上下文保留
    r2 = asyncio.run(agent.chat("其中薪资 4000 以上的有哪些"))
    assert r2.answer == "其中薪资 4000 以上的有 1 个"

    roles = [m["role"] for m in agent.messages]
    assert roles.count("user") == 2
    assert roles.count("tool") == 1
    # assistant 共 3 条：第 1 轮「带 tool_calls」+「最终回答」，第 2 轮「最终回答」
    assert roles.count("assistant") == 3


def test_max_rounds_limit() -> None:
    """模型持续请求工具时，在最大轮数内停止。"""
    mcp = FakeMcpClient(results={"search_jobs": ToolCallResult(is_error=False, data={"count": 0, "jobs": []})})
    # 每轮都返回工具调用，触发轮数上限
    script = [msg(tool_calls=[tc(f"c{i}", "search_jobs", '{"keyword": "x"}')]) for i in range(10)]
    model = FakeOpenAI(script)
    agent = LLMAgent(LLMConfig(api_key="k", base_url="https://x", model="m", max_tool_rounds=3), mcp, client=model)
    result = asyncio.run(agent.chat("查岗位"))
    assert result.rounds == 3
    assert "最大工具调用轮数" in result.answer


# ---------------- 真实 MCP 集成测试（Mock 模型 + 真实 STDIO） ----------------


def test_real_mcp_single_tool(sample_db_path: Path) -> None:
    model = FakeOpenAI([
        msg(tool_calls=[tc("c1", "search_jobs", '{"keyword": "Python"}')]),
        msg(content="找到 Python 岗位"),
    ])

    async def run():
        async with McpJobClient(extra_env={"JOB_DB_PATH": str(sample_db_path)}) as client:
            agent = LLMAgent(_config(), client, client=model)
            return await agent.chat("找 Python 岗位")

    result = asyncio.run(run())
    assert result.answer == "找到 Python 岗位"
    assert len(result.trace) == 1
    assert result.trace[0].name == "search_jobs"
    assert result.trace[0].result_data["count"] == 1


def test_real_mcp_empty_result(sample_db_path: Path) -> None:
    model = FakeOpenAI([
        msg(tool_calls=[tc("c1", "search_jobs", '{"keyword": "不存在的关键词xyz"}')]),
        msg(content="没有找到符合条件的岗位"),
    ])

    async def run():
        async with McpJobClient(extra_env={"JOB_DB_PATH": str(sample_db_path)}) as client:
            agent = LLMAgent(_config(), client, client=model)
            return await agent.chat("查不存在的岗位")

    result = asyncio.run(run())
    assert result.trace[0].result_data["count"] == 0
    assert result.trace[0].is_error is False
    assert result.answer == "没有找到符合条件的岗位"


# ---------------- 配置更新测试 ----------------


def test_agent_update_config_changes_model() -> None:
    """更新 Agent 配置后，下一次模型调用使用新配置（含新 API Key）。"""
    model = FakeOpenAI([msg(content="你好。")])
    agent = LLMAgent(
        LLMConfig(api_key="old-key", base_url="https://old", model="old-model", provider="openai"),
        FakeMcpClient(),
        client=model,
    )
    new_cfg = LLMConfig(api_key="new-key", base_url="https://new", model="new-model", provider="deepseek")
    agent.update_config(new_cfg)
    asyncio.run(agent.chat("你好"))

    call_kwargs = model.chat.completions.calls[0]
    assert call_kwargs["model"] == "new-model"
    # DeepSeek provider 生效，携带非思考模式 extra_body
    assert call_kwargs["extra_body"] == {"thinking": {"type": "disabled"}}


def test_agent_update_config_resets_cached_client() -> None:
    """更新配置后自建缓存的客户端被清空，随后按新配置惰性重建。"""
    agent = LLMAgent(LLMConfig(api_key="old"), FakeMcpClient())  # 无注入客户端
    agent._client = object()  # 模拟已自建缓存的客户端
    agent.update_config(LLMConfig(api_key="new", provider="openai"))
    assert agent._client is None
    assert agent.config.api_key == "new"


def test_agent_update_config_keeps_injected_client() -> None:
    """外部注入的客户端在配置更新后保留（测试桩不被误清除）。"""
    injected = object()
    agent = LLMAgent(LLMConfig(api_key="old"), FakeMcpClient(), client=injected)
    agent.update_config(LLMConfig(api_key="new", provider="openai"))
    assert agent._client is injected


def test_config_has_key_and_describe_no_leak() -> None:
    """诊断描述与脱敏不泄露完整 Key。"""
    cfg = LLMConfig(api_key="sk-abcdefghijkl", provider="deepseek", model="m")
    desc = cfg.describe()
    assert "已设置" in desc
    assert "sk-abcdefghijkl" not in desc
    assert "abcdefghijkl" not in cfg.masked_key()


def test_config_persists_across_consecutive_queries() -> None:
    """连续发送多条消息不会丢失已更新的配置。"""
    model = FakeOpenAI([
        msg(content="第一条回复"),
        msg(content="第二条回复"),
    ])
    agent = LLMAgent(
        LLMConfig(api_key="old", model="old-model", provider="openai"),
        FakeMcpClient(),
        client=model,
    )
    agent.update_config(LLMConfig(api_key="new", model="new-model", provider="deepseek"))

    asyncio.run(agent.chat("第一条"))
    asyncio.run(agent.chat("第二条"))

    assert len(model.chat.completions.calls) == 2
    for call_kwargs in model.chat.completions.calls:
        assert call_kwargs["model"] == "new-model"
        assert call_kwargs["extra_body"] == {"thinking": {"type": "disabled"}}