"""pytest 共享配置与夹具。

将项目根目录加入 sys.path，使测试可 import 项目模块；
提供基于临时目录的独立 SQLite 数据库与虚构测试岗位（仅测试用，非真实招聘信息）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database import Database  # noqa: E402
from models import Job  # noqa: E402

# 明确标注为虚构的测试岗位，仅用于验证查询逻辑，不冒充真实招聘信息。
SAMPLE_JOBS: list[dict] = [
    {
        "title": "Python 后端工程师",
        "company": "测试公司A",
        "location": "深圳",
        "industry": "互联网",
        "salary_min": 20000,
        "salary_max": 40000,
        "education": "本科",
        "tags": "Python,后端",
        "description": "虚构测试岗位：负责后端服务开发。",
    },
    {
        "title": "数据分析师",
        "company": "测试公司B",
        "location": "广州",
        "industry": "互联网",
        "salary_min": 15000,
        "salary_max": 30000,
        "education": "本科",
        "tags": "SQL,Python",
        "description": "虚构测试岗位：负责数据分析。",
    },
    {
        "title": "算法工程师",
        "company": "测试公司C",
        "location": "北京",
        "industry": "人工智能",
        "salary_min": 30000,
        "salary_max": 60000,
        "education": "硕士",
        "tags": "算法,机器学习",
        "description": "虚构测试岗位：负责算法研发。",
    },
    {
        "title": "测试开发工程师",
        "company": "测试公司D",
        "location": "北京",
        "industry": "互联网",
        "salary_min": 25000,
        "salary_max": 45000,
        "education": "本科",
        "tags": "测试,Python",
        "description": "虚构测试岗位：负责质量保障。",
    },
]


@pytest.fixture
def sample_db_path(tmp_path: Path) -> Path:
    """在临时目录创建独立数据库并插入虚构测试岗位，返回其路径。"""
    path = tmp_path / "jobs_test.db"
    db = Database(path)
    try:
        for data in SAMPLE_JOBS:
            db.add_job(Job(**data))
    finally:
        db.close()
    return path


@pytest.fixture
def empty_db_path(tmp_path: Path) -> Path:
    """在临时目录创建空的独立数据库（仅建表，无数据），返回其路径。"""
    path = tmp_path / "jobs_empty.db"
    db = Database(path)
    db.close()
    return path
