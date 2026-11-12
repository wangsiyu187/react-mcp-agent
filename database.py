"""SQLite 数据访问层。"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from models import Job, Preference, DEFAULT_STATUS

DB_FILENAME = "job_data.db"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _parse_date(text: Optional[str]) -> Optional[date]:
    if not text:
        return None
    text = text.strip()
    if not text:
        return None
    return date.fromisoformat(text)


def _row_to_job(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"],
        title=row["title"] or "",
        company=row["company"] or "",
        location=row["location"] or "",
        industry=row["industry"] or "",
        salary_min=row["salary_min"],
        salary_max=row["salary_max"],
        education=row["education"] or "",
        major=row["major"] or "",
        tags=row["tags"] or "",
        url=row["url"] or "",
        deadline=_parse_date(row["deadline"]),
        description=row["description"] or "",
        favorite=bool(row["favorite"]),
        status=row["status"] or DEFAULT_STATUS,
        applied_date=_parse_date(row["applied_date"]),
        note=row["note"] or "",
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class Database:
    """封装对 job_data.db 的所有读写。"""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else Path(__file__).resolve().parent / DB_FILENAME
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                title        TEXT NOT NULL DEFAULT '',
                company      TEXT NOT NULL DEFAULT '',
                location     TEXT NOT NULL DEFAULT '',
                industry     TEXT NOT NULL DEFAULT '',
                salary_min   INTEGER,
                salary_max   INTEGER,
                education    TEXT NOT NULL DEFAULT '',
                major        TEXT NOT NULL DEFAULT '',
                tags         TEXT NOT NULL DEFAULT '',
                url          TEXT NOT NULL DEFAULT '',
                deadline     TEXT,
                description  TEXT NOT NULL DEFAULT '',
                favorite     INTEGER NOT NULL DEFAULT 0,
                status       TEXT NOT NULL DEFAULT '未投递',
                applied_date TEXT,
                note         TEXT NOT NULL DEFAULT '',
                created_at   TEXT NOT NULL DEFAULT '',
                updated_at   TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS preference (
                id         INTEGER PRIMARY KEY CHECK (id = 1),
                location   TEXT NOT NULL DEFAULT '',
                industry   TEXT NOT NULL DEFAULT '',
                salary_min INTEGER,
                keywords   TEXT NOT NULL DEFAULT ''
            );
            """
        )
        self.conn.commit()

    # ---------- 岗位 ----------

    def list_jobs(self) -> list[Job]:
        rows = self.conn.execute("SELECT * FROM jobs ORDER BY id").fetchall()
        return [_row_to_job(r) for r in rows]

    def get_job(self, job_id: int) -> Optional[Job]:
        row = self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _row_to_job(row) if row else None

    def add_job(self, job: Job) -> Job:
        row = job.to_row()
        now = _now()
        row["created_at"] = now
        row["updated_at"] = now
        cols = list(row.keys())
        placeholders = ", ".join("?" for _ in cols)
        sql = f"INSERT INTO jobs ({', '.join(cols)}) VALUES ({placeholders})"
        cur = self.conn.execute(sql, [row[c] for c in cols])
        self.conn.commit()
        job.id = cur.lastrowid
        job.created_at = now
        job.updated_at = now
        return job

    def update_job(self, job: Job) -> None:
        row = job.to_row()
        row["updated_at"] = _now()
        cols = [c for c in row.keys() if c != "id"]
        assignments = ", ".join(f"{c} = ?" for c in cols)
        sql = f"UPDATE jobs SET {assignments} WHERE id = ?"
        self.conn.execute(sql, [row[c] for c in cols] + [job.id])
        self.conn.commit()

    def delete_job(self, job_id: int) -> None:
        self.conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        self.conn.commit()

    def is_duplicate(self, title: str, company: str, url: str, exclude_id: Optional[int] = None) -> bool:
        """按「招聘链接 + 职位名称 + 公司名称」判断是否已存在。"""
        sql = "SELECT COUNT(*) FROM jobs WHERE title = ? AND company = ? AND url = ?"
        args: list = [title, company, url]
        if exclude_id is not None:
            sql += " AND id != ?"
            args.append(exclude_id)
        return self.conn.execute(sql, args).fetchone()[0] > 0

    # ---------- 配置 ----------

    def get_setting(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.conn.commit()

    # ---------- 求职意向 ----------

    def get_preference(self) -> Preference:
        row = self.conn.execute("SELECT * FROM preference WHERE id = 1").fetchone()
        if row is None:
            return Preference()
        return Preference(
            location=row["location"] or "",
            industry=row["industry"] or "",
            salary_min=row["salary_min"],
            keywords=row["keywords"] or "",
        )

    def save_preference(self, pref: Preference) -> None:
        self.conn.execute(
            "INSERT INTO preference (id, location, industry, salary_min, keywords) "
            "VALUES (1, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "location = excluded.location, industry = excluded.industry, "
            "salary_min = excluded.salary_min, keywords = excluded.keywords",
            (pref.location, pref.industry, pref.salary_min, pref.keywords),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
