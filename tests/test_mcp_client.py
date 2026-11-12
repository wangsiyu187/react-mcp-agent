"""mcp_client.py 测试：通过真实 STDIO 启动 mcp_server.py 子进程完成 MCP 通信。"""
from __future__ import annotations

import asyncio
from pathlib import Path

from mcp_client import McpJobClient


def _run(coro):
    return asyncio.run(coro)


async def _with_client(db_path: Path, fn):
    async with McpJobClient(extra_env={"JOB_DB_PATH": str(db_path)}) as client:
        return await fn(client)


def test_list_tools_via_stdio(sample_db_path: Path) -> None:
    """通过真实 STDIO 启动子进程并发现 MCP 工具。"""
    async def body(client: McpJobClient):
        tools = await client.list_tools()
        return tools

    tools = _run(_with_client(sample_db_path, body))
    names = {t["name"] for t in tools}
    assert {"search_jobs", "get_job_detail"} <= names
    # 工具包含可用的输入 schema
    search = next(t for t in tools if t["name"] == "search_jobs")
    assert "properties" in search["input_schema"]


def test_call_search_jobs_via_stdio(sample_db_path: Path) -> None:
    """通过 MCP 协议调用 search_jobs，返回真实数据库中的查询结果。"""
    async def body(client: McpJobClient):
        return await client.call_tool("search_jobs", {"keyword": "python"})

    result = _run(_with_client(sample_db_path, body))
    assert result.is_error is False
    assert result.data["count"] == 1
    titles = {j["title"] for j in result.data["jobs"]}
    assert titles == {"Python 后端工程师"}


def test_call_get_job_detail_via_stdio(sample_db_path: Path) -> None:
    """通过 MCP 协议调用 get_job_detail，返回岗位详情。"""
    async def body(client: McpJobClient):
        return await client.call_tool("get_job_detail", {"job_id": 1})

    result = _run(_with_client(sample_db_path, body))
    assert result.is_error is False
    job = result.data["job"]
    assert job["title"] == "Python 后端工程师"
    assert job["company"] == "测试公司A"


def test_empty_result_via_stdio(sample_db_path: Path) -> None:
    """空结果是正常结果（count=0），不是错误。"""
    async def body(client: McpJobClient):
        return await client.call_tool("search_jobs", {"keyword": "不存在的关键词xyz"})

    result = _run(_with_client(sample_db_path, body))
    assert result.is_error is False
    assert result.data["count"] == 0
    assert result.data["jobs"] == []


def test_get_nonexistent_job_via_stdio(sample_db_path: Path) -> None:
    """查询不存在的岗位：返回明确 error，不崩溃。"""
    async def body(client: McpJobClient):
        return await client.call_tool("get_job_detail", {"job_id": 999999})

    result = _run(_with_client(sample_db_path, body))
    assert result.is_error is False
    assert result.data["job"] is None
    assert "不存在" in result.data["error"]


def test_invalid_arguments_via_stdio(sample_db_path: Path) -> None:
    """错误参数：SDK 校验失败并标记 is_error，客户端不崩溃。"""
    async def body(client: McpJobClient):
        return await client.call_tool("get_job_detail", {"job_id": "not-an-int"})

    result = _run(_with_client(sample_db_path, body))
    assert result.is_error is True


def test_repeated_connections(sample_db_path: Path) -> None:
    """重复建立/关闭连接均正常，验证资源可释放、无残留子进程干扰。"""
    async def one_round():
        async with McpJobClient(extra_env={"JOB_DB_PATH": str(sample_db_path)}) as client:
            tools = await client.list_tools()
            assert len(tools) == 2
        # 退出上下文后客户端内部引用已清空
        return True

    for _ in range(3):
        assert _run(one_round()) is True
