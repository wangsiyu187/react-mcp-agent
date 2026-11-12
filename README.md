# 第九次作业：MCP 招聘查询系统

暨南大学《智能软件开发技术》第九次作业，主题为 **MCP（Model Context Protocol）**。

本项目在第五次作业「招聘信息管理系统」（PyQt6 + SQLite）的基础上，把招聘查询功能封装为基于 **官方 MCP Python SDK v2**、**STDIO 通信** 的 MCP 服务，并接入大模型（OpenAI 兼容接口，默认 DeepSeek）完成自然语言招聘查询。

## 完整链路

```
用户自然语言
   → 大模型（OpenAI 兼容接口）返回 tool_calls
   → MCP Client（STDIO 子进程，mcp_client.py）
   → MCP Server（mcp_server.py，MCPServer v2）
   → job_service.py（复用 models / database / matcher）
   → SQLite（job_data.db）
   → 真实岗位数据 → 大模型组织自然语言回答 → PyQt6 界面显示
```

## 目录结构

| 文件 | 说明 |
|---|---|
| `models.py` / `database.py` / `matcher.py` / `csv_io.py` | 第五次作业业务层（复用） |
| `dialogs.py` / `main_window.py` / `main.py` | PyQt6 界面（改造为三标签页） |
| `job_service.py` | 招聘查询服务层（无 Qt 依赖） |
| `mcp_server.py` | MCP Server（SDK v2 `MCPServer`，STDIO） |
| `mcp_client.py` | MCP Client（SDK v2 `Client` + `StdioServerParameters`） |
| `llm_agent.py` | 大模型 Agent：工具发现 + 工具调用循环 |
| `agent_worker.py` | 后台线程 + Qt 信号（不阻塞主线程） |
| `system_prompt.py` | 招聘查询 Agent 系统提示词 |
| `config.py` | 大模型配置（仅会话内保留 API Key） |
| `tests/` | 自动化测试 |

## 安装与运行

```bash
pip install -r requirements.txt

# 启动 PyQt6 图形界面
python main.py

# 单独启动 MCP Server（STDIO，供 MCP 客户端连接）
python mcp_server.py
```

在界面的「大模型配置」标签页填写 API Base URL / API Key / 模型名称（默认 DeepSeek `deepseek-v4-pro`），点击「保存配置」，再到「AI 智能招聘查询」标签页输入自然语言即可。

## 测试

```bash
python -X utf8 -m pytest tests -v
```

测试使用临时 SQLite 数据库，不会修改正式 `job_data.db`。大模型响应使用 Mock，避免消耗真实 API Token；另提供真实 API 的人工验收流程（见 `system.md`）。

## 说明

- API Key 仅保留在当前运行会话内，不写入任何文件。
- MCP 通信为真实 STDIO（JSON-RPC），非普通函数调用。
- 数据库信息可能过时，查询结果以官方最新公告为准。
