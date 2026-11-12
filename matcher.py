"""匹配度计算。"""
from __future__ import annotations

from typing import Optional

from models import Job, Preference

# 四维满分
W_KEYWORD = 40
W_LOCATION = 25
W_INDUSTRY = 20
W_SALARY = 15


def _contains(a: str, b: str) -> bool:
    """忽略大小写的双向子串匹配。"""
    a = a.strip().lower()
    b = b.strip().lower()
    if not a or not b:
        return False
    return a in b or b in a


def match_score(job: Job, pref: Preference) -> int:
    """计算匹配度（0-100 整数）。"""
    dims: list[tuple[int, float]] = []  # (满分, 得分)

    # 关键词（40）
    kw_items = pref.keyword_items()
    if kw_items:
        blob = " ".join(x for x in (job.title, job.company, job.tags) if x).lower()
        hit = sum(1 for k in kw_items if k.lower() in blob)
        dims.append((W_KEYWORD, W_KEYWORD * hit / len(kw_items)))

    # 地点（25）
    loc_items = pref.location_items()
    if loc_items:
        job_loc = job.location or ""
        hit = any(_contains(l, job_loc) for l in loc_items)
        dims.append((W_LOCATION, W_LOCATION if hit else 0.0))

    # 行业（20）
    pref_ind = pref.industry_clean()
    if pref_ind:
        hit = _contains(pref_ind, job.industry or "")
        dims.append((W_INDUSTRY, W_INDUSTRY if hit else 0.0))

    # 薪资（15）
    if pref.salary_min is not None:
        if job.salary_max is not None:
            ok = job.salary_max >= pref.salary_min
        elif job.salary_min is not None:
            ok = job.salary_min >= pref.salary_min
        else:
            ok = False
        dims.append((W_SALARY, W_SALARY if ok else 0.0))

    if not dims:
        return 0

    total_max = sum(w for w, _ in dims)
    score = sum(s for _, s in dims)
    return round(score / total_max * 100)


def ranges_overlap(a_lo: Optional[int], a_hi: Optional[int],
                   b_lo: Optional[int], b_hi: Optional[int]) -> bool:
    """判断两个（可空边界的）数值区间是否有交集。

    None 下限视为 -inf，None 上限视为 +inf。
    """
    def max_of(lo_a: Optional[int], lo_b: Optional[int]) -> Optional[int]:
        vals = [v for v in (lo_a, lo_b) if v is not None]
        return max(vals) if vals else None

    def min_of(hi_a: Optional[int], hi_b: Optional[int]) -> Optional[int]:
        vals = [v for v in (hi_a, hi_b) if v is not None]
        return min(vals) if vals else None

    lo = max_of(a_lo, b_lo)
    hi = min_of(a_hi, b_hi)
    if lo is None or hi is None:
        return True  # 至少一侧无边界，必然相交
    return lo <= hi
