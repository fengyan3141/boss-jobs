import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import rules  # noqa: E402
import store  # noqa: E402

PROFILE = Path(__file__).resolve().parent.parent / "profiles" / "aigc-pm.yaml"


def job(title, skills=(), industry="互联网", salary="20-30K"):
    return {"title": title, "skills": list(skills), "industry": industry, "salary": salary}


class AigcProfileTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _, cls.r = rules.load(PROFILE)

    def bucket(self, *a, **kw):
        return self.r.classify(job(*a, **kw))[:2]

    def test_buckets(self):
        self.assertEqual(self.bucket("AIGC视频产品经理"), ("保留-AIGC方向PM", True))
        self.assertEqual(self.bucket("AI产品经理（J14418）-杭州"), ("待定A1-泛称AI产品经理看不出方向", True))
        self.assertEqual(self.bucket("AI产品经理", ["Agent", "B端产品"]), ("删-AI但方向非内容生成", False))
        self.assertEqual(self.bucket("短剧产品经理"), ("待定B-内容/视频相关但无AI信号", True))
        self.assertEqual(self.bucket("AIGC算法工程师"), ("删-非产品经理岗（含AIGC信号）", False))
        self.assertEqual(self.bucket("销售经理"), ("删-非产品经理岗", False))
        self.assertEqual(self.bucket("APP产品经理", industry="光伏"), ("删-PM但无AI也无内容信号", False))

    def test_ai_industry_counts_as_ai(self):
        self.assertEqual(self.bucket("产品经理", ["视频"], industry="人工智能")[0], "保留-AIGC方向PM")

    def test_hits(self):
        self.assertIn("AIGC", self.r.classify(job("AIGC视频产品经理"))[2])


class EngineTest(unittest.TestCase):
    def make(self, **extra):
        cfg = {"patterns": {"pm": {"in": "title", "regex": "产品"}},
               "buckets": [{"name": "要", "when": "pm", "detail": True}, {"name": "不要", "when": "true"}]}
        cfg.update(extra)
        return rules.Rules(cfg)

    def test_salary_floor_uses_upper_bound(self):
        r = self.make(salary_floor_k=30)
        self.assertEqual(r.classify(job("产品经理", salary="18-40K·15薪"))[0], "要")
        self.assertEqual(r.classify(job("产品经理", salary="15-25K"))[0], "删-低于薪资下限")
        self.assertEqual(r.classify(job("产品经理", salary="面议"))[0], "要")

    def test_unknown_name_rejected(self):
        with self.assertRaises(ValueError):
            self.make(buckets=[{"name": "x", "when": "pm and nope"}])

    def test_no_code_in_expressions(self):
        with self.assertRaises(ValueError):
            self.make(buckets=[{"name": "x", "when": "__import__('os')"}])

    def test_name_containing_true_is_not_rewritten(self):
        r = self.make(patterns={"untrue": "产品"}, buckets=[{"name": "x", "when": "untrue"}])
        self.assertEqual(r.classify(job("产品经理"))[0], "x")


class StoreTest(unittest.TestCase):
    def test_detail_lifecycle(self):
        _, r = rules.load(PROFILE)
        conn = store.connect(":memory:")
        raw = {"encryptJobId": "abc", "jobName": "AIGC产品经理", "securityId": "secret",
               "cityName": "北京", "salaryDesc": "30-50K", "skills": ["AIGC"]}
        row = store.upsert_listing(conn, store.job_from_raw(raw), "AIGC产品经理", r)
        self.assertTrue(store.needs_detail(row))
        self.assertNotIn("secret", str(dict(row)))
        store.save_detail(conn, "abc", "failed")
        self.assertTrue(store.needs_detail(store.get(conn, "abc")))
        store.save_detail(conn, "abc", "failed")
        self.assertFalse(store.needs_detail(store.get(conn, "abc")))  # 失败两次不再开
        store.upsert_listing(conn, store.job_from_raw(raw), "AIGC产品经理", r)
        self.assertEqual(store.get(conn, "abc")["detail_attempts"], 2)  # 再次扫到不重置


if __name__ == "__main__":
    unittest.main()
