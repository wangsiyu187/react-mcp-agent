"""MCP Client：通过真实 STDIO 启动 mcp_server.py 子进程并调用招聘查询工具。

使用官方 MCP Python SDK v2 的 Client + StdioServerParameters（非旧版 ClientSession）。
通过 sys.executable 指定当前 Python 解释器，自动拉起 mcp_server.py 子进程。
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from mcp import StdioServerParameters
from mcp.client import Client
from mcp.types import TextContent

PROJECT_ROOT = Path(__file__).resolve().parent
SERVER_SCRIPT = PROJECT_ROOT / "mcp_server.py"


@dataclass
class ToolCallResult:
    """统一封装一次工具调用的结果。"""

    is_error: bool = False
    data: Any = None                 # 解析后的 JSON 数据（dict / list），或无法解析时的原始文本
    raw_text: str = ""               # content 中提取的原始文本
    structured_content: Any = None   # SDK 的结构化输出（本服务未启用，通常为 None）


def _extract_text(content: Any) -> str:
    """从 CallToolResult.content 中提取纯文本（拼接所有 TextContent）。"""
    if not content:
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, TextContent):
            parts.append(block.text or "")
        elif hasattr(block, "text"):
            parts.append(block.text or "")
    return "".join(parts)


def _parse_json_or_text(text: str) -> Any:
    """尝试把文本解析为 JSON，失败则返回原始文本。"""
    stripped = text.strip()
    if not stripped:
        return ""
    try:
        return json.loads(stripped)
    except (ValueError, TypeError):
        return stripped


class McpJobClient:
    """STDIO MCP 客户端：连接 JNU-Job-Search 服务。"""

    def __init__(
        self,
        server_script: Optional[Path] = None,
        cwd: Optional[Path] = None,
        extra_env: Optional[dict[str, str]] = None,
    ):
        self.server_script = Path(server_script) if server_script else SERVER_SCRIPT
        self.cwd = Path(cwd) if cwd else PROJECT_ROOT
        self.params = StdioServerParameters(
            command=sys.executable,           # 当前 Python 解释器
            args=[str(self.server_script)],   # 子进程入口 mcp_server.py
            cwd=str(self.cwd),
            env=dict(extra_env) if extra_env else None,
        )
        self._client: Optional[Client] = None

    # ---- 上下文管理：进入时启动子进程并握手，退出时释放资源、终止子进程 ----
    async def __aenter__(self) -> "McpJobClient":
        self._client = Client(self.params)
        await self._client.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._client is not None:
            await self._client.__aexit__(exc_type, exc_val, exc_tb)
        self._client = None

    @property
    def client(self) -> Client:
        if self._client is None:
            raise RuntimeError("客户端尚未进入上下文（请使用 async with McpJobClient(...)）")
        return self._client

    async def list_tools(self) -> list[dict[str, Any]]:
        """获取 MCP 工具列表，每个工具为 {name, description, input_schema}。"""
        result = await self.client.list_tools()
        return [
            {
                "name": tool.name,
                "description": tool.description or "",
                "input_schema": tool.input_schema,
            }
            for tool in result.tools
        ]

    async def call_tool(self, name: str, arguments: Optional[dict[str, Any]] = None) -> ToolCallResult:
        """调用指定工具，返回统一封装的结果。

        正确处理：结构化 JSON 结果、空结果（count=0）、错误结果（is_error）。
        """
        result = await self.client.call_tool(name, arguments or {})
        raw_text = _extract_text(result.content)
        return ToolCallResult(
            is_error=bool(result.is_error),
            data=_parse_json_or_text(raw_text),
            raw_text=raw_text,
            structured_content=result.structured_content,
        )
