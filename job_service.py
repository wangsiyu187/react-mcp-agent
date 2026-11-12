"""招聘信息查询服务层（无 Qt 依赖，供 MCP Server 与测试复用）。

将原先写在 main_window.py 中、依赖 Qt 控件状态的筛选逻辑抽取为纯 Python 函数，
完整复用 models.py、database.py、matcher.py 中的业务能力。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from database import DB_FILENAME, Database
from models import Job

# 项目根目录（本文件所在目录），用于基于项目目录的绝对路径定位数据库，
# 避免 MCP 子进程工作目录不同导致数据库路径错误。
PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_DB_PATH = PROJECT_ROOT / DB_FILENAME


@dataclass
class JobFilter:
    """招聘岗位筛选条件。全部字段可选，未设置时不过滤。

    薪资语义（严格，明确区分最低与最高）：
    - salary_min：岗位「起薪下限」必须已知且 >= 该值。岗位 salary_min 未知时不假定满足。
    - salary_max：岗位「薪资上限」必须已知且 <= 该值。岗位 salary_max 未知时不假定满足。
    """

    keyword: str = ""                      # 关键词：匹配职位名称 / 公司名称（忽略大小写子串）
    location: str = ""                     # 工作地点：精确匹配
    industry: str = ""                     # 行业：精确匹配
    salary_min: Optional[int] = None       # 最低薪资要求（元/月）：岗位起薪下限 >= 该值
    salary_max: Optional[int] = None       # 最高薪资要求（元/月）：岗位薪资上限 <= 该值
    limit: Optional[int] = None            # 结果数量上限（>0 时生效，否则返回全部）


def job_to_dict(job: Job) -> dict[str, Any]:
    """将 Job 模型转换为可 JSON 序列化的 dict（date -> 字符串）。"""
    return {
        "id": job.id,
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "industry": job.industry,
        "salary_min": job.salary_min,
        "salary_max": job.salary_max,
        "salary_text": job.salary_text,
        "education": job.education,
        "major": job.major,
        "tags": job.tags,
        "url": job.url,
        "deadline": job.deadline.isoformat() if job.deadline else "",
        "description": job.description,
        "favorite": job.favorite,
        "status": job.status,
        "applied_date": job.applied_date.isoformat() if job.applied_date else "",
        "note": job.note,
    }


class JobService:
    """招聘信息查询服务。

    每次查询都新建数据库连接，用完即关闭：
    1. SQLite 连接不能跨线程共享，MCP 工具函数可能运行在 worker 线程；
    2. 保证无残留连接、线程安全，也便于测试注入独立临时数据库。
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH

    def search_jobs(self, flt: Optional[JobFilter] = None) -> list[Job]:
        """按筛选条件查询岗位，返回按 id 升序的 Job 列表。"""
        flt = flt or JobFilter()
        db = Database(self.db_path)
        try:
            jobs = db.list_jobs()
        finally:
            db.close()

        matched = [job for job in jobs if self._matches(job, flt)]
        if flt.limit is not None and flt.limit > 0:
            matched = matched[: flt.limit]
        return matched

    def get_job(self, job_id: int) -> Optional[Job]:
        """按主键 id 查询单条岗位详情，不存在返回 None。"""
        db = Database(self.db_path)
        try:
            return db.get_job(job_id)
        finally:
            db.close()

    @staticmethod
    def _matches(job: Job, flt: JobFilter) -> bool:
        # 关键词：匹配职位名称 / 公司名称（忽略大小写子串），与原界面逻辑一致
        keyword = (flt.keyword or "").strip().lower()
        if keyword:
            blob = f"{job.title} {job.company}".lower()
            if keyword not in blob:
                return False

        # 地点：精确匹配
        location = (flt.location or "").strip()
        if location and job.location != location:
            return False

        # 行业：精确匹配
        industry = (flt.industry or "").strip()
        if industry and job.industry != industry:
            return False

        # 最低薪资（严格）：岗位起薪下限已知且 >= salary_min；未知起薪不假定满足
        if flt.salary_min is not None:
            if job.salary_min is None or job.salary_min < flt.salary_min:
                return False

        # 最高薪资（严格）：岗位薪资上限已知且 <= salary_max；未知上限不假定满足
        if flt.salary_max is not None:
            if job.salary_max is None or job.salary_max > flt.salary_max:
                return False

        return True
