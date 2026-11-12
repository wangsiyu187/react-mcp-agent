"""JNU-Job-Search MCP Server：通过 STDIO 提供招聘信息查询工具。

使用官方 MCP Python SDK v2 的 MCPServer（非旧版 FastMCP）。
日志一律输出到 stderr，绝不写 stdout，避免污染 STDIO JSON-RPC 通道。
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Annotated, Any, Optional

from pydantic import Field

from mcp.server import MCPServer

from job_service import JobFilter, JobService, job_to_dict

# ---- 日志：写到 stderr，不污染 STDIO 通信通道 ----
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("jnu.job.mcp.server")

# ---- 数据库路径：默认取项目目录下的 job_data.db；可用环境变量覆盖（测试用） ----
_env_db_path = os.environ.get("JOB_DB_PATH", "").strip()
_db_path = Path(_env_db_path).resolve() if _env_db_path else None

mcp = MCPServer(
    "JNU-Job-Search",
    title="暨南大学招聘信息查询服务",
    description="基于 SQLite 数据库的招聘信息查询 MCP 服务，提供岗位检索与岗位详情查询。",
    version="1.0.0",
)

_service = JobService(_db_path)


@mcp.tool(structured_output=False)
def search_jobs(
    keyword: Annotated[str, Field(description="关键词，匹配职位名称或公司名称（忽略大小写的子串匹配），可空")] = "",
    location: Annotated[str, Field(description="工作地点，精确匹配，可空")] = "",
    industry: Annotated[str, Field(description="行业，精确匹配，可空")] = "",
    salary_min: Annotated[Optional[int], Field(description="最低薪资要求（元/月）：只保留岗位起薪下限已知且不低于该值的岗位，可空")] = None,
    salary_max: Annotated[Optional[int], Field(description="最高薪资要求（元/月）：只保留岗位薪资上限已知且不高于该值的岗位，可空")] = None,
    limit: Annotated[Optional[int], Field(description="返回结果数量上限（正整数），可空表示返回全部")] = None,
) -> dict[str, Any]:
    """按条件检索招聘岗位。

    至少支持关键词、地点、行业、最低薪资、最高薪资五个维度，以及结果数量限制。
    薪资为严格筛选：salary_min 要求岗位起薪下限 >= 该值；salary_max 要求岗位薪资上限 <= 该值；
    岗位薪资未知时不会在严格筛选下被当作满足条件。
    返回 JSON：{"count": 命中数量, "jobs": [岗位对象...], "error": 错误信息(可选)}。
    空结果不是错误，count 为 0、jobs 为空数组。
    """
    try:
        flt = JobFilter(
            keyword=keyword or "",
            location=location or "",
            industry=industry or "",
            salary_min=salary_min,
            salary_max=salary_max,
            limit=limit,
        )
        jobs = _service.search_jobs(flt)
        logger.info("search_jobs keyword=%r location=%r industry=%r salary_min=%r salary_max=%r limit=%r -> %d",
                    keyword, location, industry, salary_min, salary_max, limit, len(jobs))
        return {
            "count": len(jobs),
            "jobs": [job_to_dict(job) for job in jobs],
        }
    except Exception as exc:  # noqa: BLE001 —— 查询失败返回错误，不得让服务器崩溃
        logger.exception("search_jobs 查询失败")
        return {"count": 0, "jobs": [], "error": f"查询失败：{exc}"}


@mcp.tool(structured_output=False)
def get_job_detail(
    job_id: Annotated[int, Field(description="岗位主键 id（整数）")],
) -> dict[str, Any]:
    """按岗位 id 查询单条岗位详情。

    返回 JSON：{"job": 岗位对象}；岗位不存在时返回 {"job": null, "error": "岗位不存在"}。
    """
    try:
        job = _service.get_job(job_id)
        if job is None:
            logger.info("get_job_detail job_id=%s -> 不存在", job_id)
            return {"job": None, "error": f"岗位 {job_id} 不存在"}
        logger.info("get_job_detail job_id=%s -> %s", job_id, job.title)
        return {"job": job_to_dict(job)}
    except Exception as exc:  # noqa: BLE001
        logger.exception("get_job_detail 查询失败")
        return {"job": None, "error": f"查询失败：{exc}"}


if __name__ == "__main__":
    # STDIO 传输：从 stdin 读 JSON-RPC 请求、向 stdout 写响应。
    mcp.run(transport="stdio")
