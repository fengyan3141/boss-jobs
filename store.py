"""本地 SQLite：每个岗位一行，列表信息 + 分桶 + 详情状态 + JD。"""
import json
import re
import sqlite3
import unicodedata
from datetime import datetime

SCHEMA = """
create table if not exists jobs (
  job_id text primary key,
  city text, district text, keyword text,
  title text, company text, industry text,
  salary text, experience text, degree text, skills text,
  first_seen text, last_seen text,
  bucket text, want_detail integer, hits text,
  detail_status text not null default '',  -- '' 未开 / ok / closed 已下架 / failed
  detail_attempts integer not null default 0,
  jd text not null default '', jd_source text not null default '',
  jd_hits text not null default '', jd_at text not null default ''
);
"""

# 详情失败超过这个次数就不再自动开：失败重试几乎不会成功，只会多碰风控
MAX_DETAIL_ATTEMPTS = 2


# BOSS 会把 JD 里的部分常用字换成长得一样的康熙部首（「能⼒」里的「⼒」是 U+2F12），
# 肉眼看不出来，但搜「能力」搜不到。NFKC 能还原它们；「⾤」NFKC 会变成「釆」，实际是「采」。
_RADICAL = re.compile(r"[\u2f00-\u2fdf]")


def normalize_text(text):
    return _RADICAL.sub(lambda m: "采" if m.group() == "⾤" else unicodedata.normalize("NFKC", m.group()),
                        text or "")


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def connect(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def job_from_raw(raw):
    """BOSS 列表接口的一条 → 我们存的字段。不保存 securityId、lid 等会话参数。"""
    return {
        "job_id": raw.get("encryptJobId") or "",
        "city": raw.get("cityName") or "",
        "district": raw.get("areaDistrict") or "",
        "title": raw.get("jobName") or "",
        "company": raw.get("brandName") or "",
        "industry": raw.get("brandIndustry") or "",
        "salary": raw.get("salaryDesc") or "",
        "experience": raw.get("jobExperience") or "",
        "degree": raw.get("jobDegree") or "",
        "skills": raw.get("skills") or [],
    }


def upsert_listing(conn, job, keyword, rules):
    """存列表岗位并分桶；返回库里这一行（含此前的详情状态）。"""
    bucket, want, hits = rules.classify(job)
    ts = now()
    conn.execute(
        """insert into jobs (job_id, city, district, keyword, title, company, industry, salary,
                             experience, degree, skills, first_seen, last_seen, bucket, want_detail, hits)
           values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           on conflict(job_id) do update set
             last_seen=excluded.last_seen, title=excluded.title, salary=excluded.salary,
             skills=excluded.skills, bucket=excluded.bucket, want_detail=excluded.want_detail,
             hits=excluded.hits""",
        (job["job_id"], job["city"], job["district"], keyword, job["title"], job["company"],
         job["industry"], job["salary"], job["experience"], job["degree"],
         json.dumps(job["skills"], ensure_ascii=False), ts, ts, bucket, int(want), hits))
    conn.commit()
    return get(conn, job["job_id"])


def get(conn, job_id):
    return conn.execute("select * from jobs where job_id=?", (job_id,)).fetchone()


def needs_detail(row):
    return (bool(row["want_detail"]) and row["detail_status"] in ("", "failed")
            and row["detail_attempts"] < MAX_DETAIL_ATTEMPTS)


def save_detail(conn, job_id, status, jd="", source="", jd_hits=""):
    conn.execute(
        """update jobs set detail_status=?, detail_attempts=detail_attempts+1,
                  jd=?, jd_source=?, jd_hits=?, jd_at=? where job_id=?""",
        (status, jd, source, jd_hits, now(), job_id))
    conn.commit()


def pending_details(conn, limit=None):
    rows = conn.execute(
        """select * from jobs where want_detail=1 and detail_status in ('', 'failed')
           and detail_attempts < ? order by first_seen desc""", (MAX_DETAIL_ATTEMPTS,)).fetchall()
    return rows[:limit] if limit else rows


def reclassify(conn, rules):
    """改了规则后重新分桶；已抓到的 JD 不受影响。"""
    n = 0
    for row in conn.execute("select * from jobs").fetchall():
        job = dict(row)
        job["skills"] = json.loads(row["skills"] or "[]")
        bucket, want, hits = rules.classify(job)
        jd_hits = rules.hits(row["jd"]) if row["jd"] else ""
        conn.execute("update jobs set bucket=?, want_detail=?, hits=?, jd_hits=? where job_id=?",
                     (bucket, int(want), hits, jd_hits, row["job_id"]))
        n += 1
    conn.commit()
    return n
