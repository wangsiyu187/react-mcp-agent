"""mcp_server.py 测试：MCP Server 工具注册与参数 Schema。"""
from __future__ import annotations

import asyncio
from typing import Any

import mcp_server


def _list_tools() -> list[Any]:
    result = asyncio.run(mcp_server.mcp.list_tools())
    return result.tools if hasattr(result, "tools") else result


def test_tools_registered() -> None:
    """search_jobs 与 get_job_detail 两个工具均已注册。"""
    names = {t.name for t in _list_tools()}
    assert {"search_jobs", "get_job_detail"} <= names


def test_search_jobs_schema() -> None:
    """search_jobs 工具的参数类型与说明完整。"""
    tools = {t.name: t for t in _list_tools()}
    tool = tools["search_jobs"]
    assert tool.description
    props = tool.input_schema["properties"]
    assert set(props) == {"keyword", "location", "industry", "salary_min", "salary_max", "limit"}
    assert props["keyword"]["type"] == "string"
    # salary_min / salary_max 为 Optional[int]：schema 用 anyOf 表达 integer 与 null
    salary_min_types = {item["type"] for item in props["salary_min"]["anyOf"]}
    assert {"integer", "null"} <= salary_min_types
    salary_max_types = {item["type"] for item in props["salary_max"]["anyOf"]}
    assert {"integer", "null"} <= salary_max_types
    assert props["keyword"]["description"]


def test_get_job_detail_schema() -> None:
    """get_job_detail 工具要求必填整数 job_id。"""
    tools = {t.name: t for t in _list_tools()}
    tool = tools["get_job_detail"]
    assert tool.description
    assert "job_id" in tool.input_schema["required"]
    assert tool.input_schema["properties"]["job_id"]["type"] == "integer"


def test_tool_functions_callable() -> None:
    """工具对应的业务函数可被直接调用（返回 dict，失败不抛异常）。"""
    # 在默认（正式）库上仅验证“不存在”与“空参数”这类只读行为，不写库。
    result = mcp_server.search_jobs(keyword="")
    assert isinstance(result, dict)
    assert "jobs" in result and "count" in result
    detail = mcp_server.get_job_detail(99999999)
    assert isinstance(detail, dict)
    assert detail.get("job") is None
    assert "error" in detail
