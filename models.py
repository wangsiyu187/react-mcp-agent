"""数据模型定义。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

# 投递状态集合（顺序即界面下拉顺序）
STATUSES: tuple[str, ...] = (
    "未投递",
    "已投递",
    "笔试",
    "面试",
    "已录用",
    "已拒绝",
    "已放弃",
)

DEFAULT_STATUS = "未投递"


def _fmt_date(d: Optional[date]) -> str:
    """date -> 'YYYY-MM-DD'，空值返回空字符串。"""
    return d.isoformat() if d else ""


@dataclass
class Job:
    """一条岗位记录。"""
    title: str = ""
    company: str = ""
    location: str = ""
    industry: str = ""
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    education: str = ""
    major: str = ""
    tags: str = ""
    url: str = ""
    deadline: Optional[date] = None
    description: str = ""
    favorite: bool = False
    status: str = DEFAULT_STATUS
    applied_date: Optional[date] = None
    note: str = ""
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    id: Optional[int] = None

    @property
    def salary_text(self) -> str:
        """薪资拼接显示：min-max 或 min 或 max 或空。"""
        lo = self.salary_min
        hi = self.salary_max
        if lo is not None and hi is not None:
            if lo == hi:
                return str(lo)
            return f"{lo}-{hi}"
        if lo is not None:
            return f"{lo}+"
        if hi is not None:
            return f"≤{hi}"
        return ""

    def to_row(self) -> dict:
        """转为数据库行（date -> str，bool -> int，不含 created/updated/id）。"""
        return {
            "title": self.title,
            "company": self.company,
            "location": self.location,
            "industry": self.industry,
            "salary_min": self.salary_min,
            "salary_max": self.salary_max,
            "education": self.education,
            "major": self.major,
            "tags": self.tags,
            "url": self.url,
            "deadline": _fmt_date(self.deadline) or None,
            "description": self.description,
            "favorite": 1 if self.favorite else 0,
            "status": self.status,
            "applied_date": _fmt_date(self.applied_date) or None,
            "note": self.note,
        }


@dataclass
class Preference:
    """求职意向（单一、可编辑）。"""
    location: str = ""          # 逗号分隔多值
    industry: str = ""          # 单值
    salary_min: Optional[int] = None  # 元/月
    keywords: str = ""          # 逗号分隔多值

    @property
    def is_empty(self) -> bool:
        loc = self._split(self.location)
        kw = self._split(self.keywords)
        return not loc and not self.industry.strip() and self.salary_min is None and not kw

    @staticmethod
    def _split(text: str) -> list[str]:
        return [x.strip() for x in (text or "").split(",") if x.strip()]

    def location_items(self) -> list[str]:
        return self._split(self.location)

    def keyword_items(self) -> list[str]:
        return self._split(self.keywords)

    def industry_clean(self) -> str:
        return self.industry.strip()
