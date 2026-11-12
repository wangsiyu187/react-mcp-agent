"""CSV 导入 / 导出。"""
from __future__ import annotations

import csv
from datetime import date
from typing import Optional

from models import Job, DEFAULT_STATUS, STATUSES

# 导出列（顺序固定，与 Job 属性对应）
COLUMNS: list[tuple[str, str]] = [
    ("职位名称", "title"),
    ("公司名称", "company"),
    ("工作地点", "location"),
    ("行业", "industry"),
    ("薪资最低", "salary_min"),
    ("薪资最高", "salary_max"),
    ("学历要求", "education"),
    ("专业要求", "major"),
    ("岗位标签", "tags"),
    ("招聘链接", "url"),
    ("截止日期", "deadline"),
    ("岗位描述", "description"),
    ("收藏", "favorite"),
    ("投递状态", "status"),
    ("投递日期", "applied_date"),
    ("备注", "note"),
]

HEADERS = [h for h, _ in COLUMNS]
ATTRS = [a for _, a in COLUMNS]

# 文本列（直接写字符串）
_TEXT_ATTRS = {"title", "company", "location", "industry", "education", "major",
               "tags", "url", "description", "note"}


def _parse_int(text: str) -> Optional[int]:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _parse_date(text: str) -> Optional[date]:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def export_jobs(jobs: list[Job], path: str) -> int:
    """导出岗位列表为 CSV（UTF-8 with BOM）。返回写入行数（不含表头）。"""
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(HEADERS)
        for job in jobs:
            row = []
            for attr in ATTRS:
                if attr == "salary_min":
                    row.append("" if job.salary_min is None else str(job.salary_min))
                elif attr == "salary_max":
                    row.append("" if job.salary_max is None else str(job.salary_max))
                elif attr == "deadline":
                    row.append(job.deadline.isoformat() if job.deadline else "")
                elif attr == "favorite":
                    row.append("1" if job.favorite else "0")
                elif attr == "status":
                    row.append(job.status)
                elif attr == "applied_date":
                    row.append(job.applied_date.isoformat() if job.applied_date else "")
                else:
                    row.append(getattr(job, attr))
            writer.writerow(row)
    return len(jobs)


def import_jobs(path: str) -> tuple[list[Job], list[str]]:
    """读取 CSV，返回 (岗位列表, 错误信息列表)。"""
    jobs: list[Job] = []
    errors: list[str] = []
    with open(path, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        try:
            headers = next(reader)
        except StopIteration:
            return jobs, ["文件为空"]

        # 按表头名建立列索引（允许列顺序不同，但需列名一致）
        idx = {}
        for i, h in enumerate(headers):
            if h and h.strip():
                idx[h.strip()] = i
        missing = [h for h in HEADERS if h not in idx]
        if missing:
            return jobs, [f"缺少必需列：{', '.join(missing)}"]

        for line_no, row in enumerate(reader, start=2):
            try:
                def val(header: str) -> str:
                    i = idx[header]
                    return row[i] if i < len(row) else ""

                job = Job(
                    title=val("职位名称"),
                    company=val("公司名称"),
                    location=val("工作地点"),
                    industry=val("行业"),
                    salary_min=_parse_int(val("薪资最低")),
                    salary_max=_parse_int(val("薪资最高")),
                    education=val("学历要求"),
                    major=val("专业要求"),
                    tags=val("岗位标签"),
                    url=val("招聘链接"),
                    deadline=_parse_date(val("截止日期")),
                    description=val("岗位描述"),
                    favorite=val("收藏").strip() == "1",
                    status=val("投递状态").strip() or DEFAULT_STATUS,
                    applied_date=_parse_date(val("投递日期")),
                    note=val("备注"),
                )
                if job.status not in STATUSES:
                    errors.append(f"第 {line_no} 行：未知投递状态「{job.status}」，已置为「{DEFAULT_STATUS}」")
                    job.status = DEFAULT_STATUS
                jobs.append(job)
            except Exception as e:  # noqa: BLE001
                errors.append(f"第 {line_no} 行解析失败：{e}")
    return jobs, errors
