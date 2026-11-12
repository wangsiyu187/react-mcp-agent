# 系统设计说明

## 一、系统功能

本项目在第五次作业「暨南大学就业信息服务平台岗位信息辅助管理」基础上，新增 MCP 招聘查询能力：

1. **招聘信息管理**（保留第五次作业功能）：岗位列表、筛选、增删改、详情、CSV 导入导出、求职意向与匹配度。
2. **AI 智能招聘查询**（新增）：自然语言 → 大模型 → MCP 工具 → SQLite → 自然语言回答。
3. **大模型配置**（新增）：API Base URL / API Key / 模型 / 超时 / 工具调用轮数，测试连接。

## 二、模块结构

```
main.py ──► main_window.py（QTabWidget 三标签页）
                ├── 招聘信息管理（复用第五次作业）
                ├── AI 智能招聘查询（chat 面板 + 工具日志 + 结果）
                │       └── agent_worker.AgentWorker（QObject + 后台线程）
                │               ├── llm_agent.LLMAgent（OpenAI 兼容接口）
                │               └── mcp_client.McpJobClient（STDIO）
                └── 大模型配置
                        └── agent_worker.ConnectionTestWorker

mcp_client.py ──(STDIO 子进程)──► mcp_server.py（MCPServer v2）
                                        └── job_service.py（查询服务层）
                                                ├── database.py（SQLite）
                                                ├── models.py（Job / Preference）
                                                └── matcher.py（匹配度）
```

## 三、MCP 架构

- **Server**（`mcp_server.py`）：`from mcp.server import MCPServer`，创建 `MCPServer("JNU-Job-Search")`，用 `@mcp.tool()` 注册 `search_jobs`、`get_job_detail`，`mcp.run(transport="stdio")` 启动。日志写 stderr，不污染 stdout。
- **Client**（`mcp_client.py`）：`Client(StdioServerParameters(command=sys.executable, args=["mcp_server.py"]))`，async context manager 启动/释放子进程；提供 `list_tools()`、`call_tool()`。
- **通信**：真实 STDIO JSON-RPC（newline-delimited JSON），非普通函数调用。数据库路径基于项目目录绝对路径，规避子进程工作目录差异。

## 四、大模型调用链路

1. `AgentWorker` 后台线程启动事件循环，连接 MCP Server 并 `list_tools()` 动态发现工具。
2. `LLMAgent.discover_tools()` 把 MCP 工具转为 OpenAI function tools 格式（动态，不硬编码）。
3. 用户消息入列 → `LLMAgent.chat()`：
   - 请求模型（携带工具定义，`tool_choice="auto"`）；
   - 模型返回 `tool_calls` 时，校验工具名与参数，通过 MCP Client 调用；
   - 结果以 `role=tool`（含 `tool_call_id`）回填，再次请求模型生成回答；
   - 支持一次多个 `tool_calls`、多轮调用（上限可配，默认 5）。
4. 结果经 Qt 信号回传主线程更新 UI。

## 五、薪资筛选语义

`search_jobs` 的薪资筛选为**严格**语义：

- `salary_min`：岗位「起薪下限」必须已知且 `>=` 该值。
- `salary_max`：岗位「薪资上限」必须已知且 `<=` 该值。
- 岗位薪资未知时，严格筛选下**不假定满足**。

用户说"最低薪资不低于 4000 元"时，不会因岗位薪资上限超过 4000 而误判符合。

## 六、安全与错误处理

- 系统提示词要求：只依据实际查询结果回答，不编造岗位，数据库文本不被当作指令。
- API Key 仅会话内保留，不落盘明文；日志不输出完整 Key（工具参数不含密钥）。
- 处理：`is_error`、业务错误（如岗位不存在）、JSON 解析失败、未知工具、网络超时、鉴权失败、最大轮数。
- 关闭程序时 `closeEvent` 停止后台线程并释放 MCP 子进程。

## 七、测试

- 自动化测试（`tests/`）：40 项，使用临时 SQLite 库与 Mock 模型响应。
- 真实 API 人工验收流程：

1. 准备包含真实招聘信息的 `job_data.db`（或通过 CSV 导入）。
2. 启动 `python main.py`，在「大模型配置」填写真实 DeepSeek（或兼容）API Key。
3. 在「AI 智能招聘查询」输入："帮我找珠海的 Python 岗位"。
4. 观察：工具调用日志出现 `search_jobs` 及其参数；查询结果区显示真实岗位 JSON；AI 依据结果作答。
5. 追问："其中薪资 4000 元以上的有哪些？""第二个岗位有什么要求？"，验证多轮上下文与岗位详情调用。
6. 核对回答中的岗位信息与 `job_data.db` 中真实数据一致，确认无编造。

> 注意：自动化测试不消耗真实 API Token；真实链路验证需人工提供有效 API Key。
