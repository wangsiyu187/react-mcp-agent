"""job_service.py 业务层测试：数据库初始化、关键词查询、地点/薪资筛选、详情查询、空库。"""
from __future__ import annotations

from pathlib import Path

from database import Database
from job_service import JobFilter, JobService, job_to_dict
from models import Job


def test_database_init_and_read(sample_db_path: Path) -> None:
    """数据库初始化：文件不存在时自动建库建表，插入后可读取。"""
    db = Database(sample_db_path)
    try:
        jobs = db.list_jobs()
        assert len(jobs) == 4
        assert all(isinstance(job, Job) for job in jobs)
    finally:
        db.close()


def test_empty_database(empty_db_path: Path) -> None:
    """空数据库：查询正常返回空列表，不抛异常。"""
    service = JobService(empty_db_path)
    jobs = service.search_jobs()
    assert jobs == []
    assert service.get_job(1) is None


def test_keyword_search(sample_db_path: Path) -> None:
    """关键词查询：匹配职位名称或公司名称（忽略大小写子串）。"""
    service = JobService(sample_db_path)

    # 匹配职位名称（忽略大小写）
    jobs = service.search_jobs(JobFilter(keyword="python"))
    assert [j.title for j in jobs] == ["Python 后端工程师"]

    # 中文关键词匹配多个职位名称
    jobs = service.search_jobs(JobFilter(keyword="工程师"))
    assert {j.title for j in jobs} == {"Python 后端工程师", "算法工程师", "测试开发工程师"}

    # 匹配公司名称
    jobs = service.search_jobs(JobFilter(keyword="测试公司A"))
    assert [j.title for j in jobs] == ["Python 后端工程师"]


def test_location_filter(sample_db_path: Path) -> None:
    """地点筛选：精确匹配。"""
    service = JobService(sample_db_path)
    jobs = service.search_jobs(JobFilter(location="北京"))
    assert {j.title for j in jobs} == {"算法工程师", "测试开发工程师"}

    jobs = service.search_jobs(JobFilter(location="上海"))
    assert jobs == []


def test_industry_filter(sample_db_path: Path) -> None:
    """行业筛选：精确匹配。"""
    service = JobService(sample_db_path)
    jobs = service.search_jobs(JobFilter(industry="人工智能"))
    assert [j.title for j in jobs] == ["算法工程师"]


def test_salary_min_filter(sample_db_path: Path) -> None:
    """最低薪资筛选（严格）：只保留岗位起薪下限已知且 >= 该值的岗位。"""
    service = JobService(sample_db_path)
    # 起薪下限 >= 30000：仅算法工程师（30000）满足
    jobs = service.search_jobs(JobFilter(salary_min=30000))
    assert [j.title for j in jobs] == ["算法工程师"]

    # 起薪下限 >= 10000：起薪 15000/20000/25000/30000 均满足
    jobs = service.search_jobs(JobFilter(salary_min=10000))
    assert {j.title for j in jobs} == {"Python 后端工程师", "数据分析师", "算法工程师", "测试开发工程师"}


def test_salary_max_filter(sample_db_path: Path) -> None:
    """最高薪资筛选（严格）：只保留岗位薪资上限已知且 <= 该值的岗位。"""
    service = JobService(sample_db_path)
    # 薪资上限 <= 45000：算法工程师（60000）不满足
    jobs = service.search_jobs(JobFilter(salary_max=45000))
    assert {j.title for j in jobs} == {"Python 后端工程师", "数据分析师", "测试开发工程师"}


def test_salary_unknown_not_assumed(sample_db_path: Path) -> None:
    """未知薪资在严格筛选下不能被假定满足要求。"""
    db = Database(sample_db_path)
    try:
        db.add_job(Job(title="薪资未知岗位", company="测试公司E", salary_min=None, salary_max=None))
    finally:
        db.close()

    service = JobService(sample_db_path)
    jobs = service.search_jobs(JobFilter(salary_min=1000))
    assert "薪资未知岗位" not in {j.title for j in jobs}
    jobs = service.search_jobs(JobFilter(salary_max=100000))
    assert "薪资未知岗位" not in {j.title for j in jobs}


def test_combined_location_and_salary(sample_db_path: Path) -> None:
    """组合筛选：地点 + 最低薪资。"""
    service = JobService(sample_db_path)
    jobs = service.search_jobs(JobFilter(location="广州", salary_min=10000))
    assert [j.title for j in jobs] == ["数据分析师"]


def test_limit(sample_db_path: Path) -> None:
    """结果数量限制。"""
    service = JobService(sample_db_path)
    jobs = service.search_jobs(JobFilter(limit=2))
    assert len(jobs) == 2
    # 按 id 升序截取
    assert [j.id for j in jobs] == [1, 2]


def test_get_job_detail(sample_db_path: Path) -> None:
    """岗位详情查询：存在返回 Job，不存在返回 None。"""
    service = JobService(sample_db_path)
    job = service.get_job(1)
    assert job is not None
    assert job.title == "Python 后端工程师"
    assert job.salary_text == "20000-40000"

    assert service.get_job(9999) is None


def test_job_to_dict_serializable(sample_db_path: Path) -> None:
    """job_to_dict 产出可 JSON 序列化结构。"""
    import json

    service = JobService(sample_db_path)
    job = service.get_job(1)
    d = job_to_dict(job)
    assert d["id"] == 1
    assert d["salary_text"] == "20000-40000"
    # 日期字段已转为字符串，整体可 json.dumps
    json.dumps(d)
