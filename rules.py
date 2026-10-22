"""按方向配置（YAML）给列表岗位分桶，并决定要不要开详情。

配置里三样东西：
  patterns  具名正则，各自指定匹配哪个字段
  defs      用 and/or/not 组合出的中间判断，可引用 patterns 和前面的 defs
  buckets   按顺序匹配，第一个 when 为真的桶生效；detail: true 才开详情
"""
import ast
import re

import yaml

from cities import CITY_CODES

# 可供 pattern 匹配的字段
FIELDS = ("title", "title_clean", "skills", "industry", "company", "text", "jd")

_CITY_ALT = "|".join(map(re.escape, CITY_CODES))
_JOB_CODE = re.compile(r"\s*[（(]?\s*[A-Z]{0,3}\d{3,}\s*[）)]?", re.I)
_CITY_SUFFIX = re.compile(rf"[（(\-－—|｜]\s*({_CITY_ALT}|base\S*)[^）)]*[）)]?", re.I)


def clean_title(title):
    """去掉职位编号和城市后缀：「AI产品经理（J14418）-杭州」→「AI产品经理」。"""
    return _CITY_SUFFIX.sub("", _JOB_CODE.sub("", title)).strip()


def salary_upper_k(salary):
    """「18-40K·15薪」→ 40；面议、日薪等无法解析的返回 None。"""
    m = re.search(r"(\d+)\s*-\s*(\d+)\s*[kK]", salary or "")
    return int(m.group(2)) if m else None


class Rules:
    def __init__(self, cfg):
        self.name = cfg.get("name", "")
        self.salary_floor_k = int(cfg.get("salary_floor_k") or 0)
        self.ignore_skills = set(cfg.get("ignore_skills") or [])
        self.strip_from_text = cfg.get("strip_from_text") or []
        self.patterns = {}
        for name, spec in (cfg.get("patterns") or {}).items():
            if isinstance(spec, str):
                spec = {"regex": spec}
            if "skills_within" in spec:
                self.patterns[name] = ("within", set(spec["skills_within"]))
                continue
            field = spec.get("in", "text")
            if field not in FIELDS:
                raise ValueError(f"pattern {name}: 未知字段 {field}，可选 {FIELDS}")
            flags = 0 if spec.get("case_sensitive") else re.I
            self.patterns[name] = ("regex", field, re.compile(spec["regex"], flags))
        self.hit_patterns = cfg.get("hit_patterns") or [
            n for n, p in self.patterns.items() if p[0] == "regex" and p[1] == "text"]
        known = set(self.patterns)
        self.defs = []
        for name, expr in (cfg.get("defs") or {}).items():
            self.defs.append((name, _compile(expr, known, f"defs.{name}")))
            known.add(name)
        self.buckets = []
        for b in cfg.get("buckets") or []:
            self.buckets.append(dict(
                name=b["name"], note=b.get("note", ""), detail=bool(b.get("detail")),
                when=_compile(b.get("when", True), known, f"bucket {b['name']}")))
        if not self.buckets:
            raise ValueError("配置里至少要有一个 bucket")

    def fields(self, job, jd=""):
        title = job.get("title") or ""
        skills = [s for s in job.get("skills") or [] if s not in self.ignore_skills]
        text = title
        for term in self.strip_from_text:
            text = text.replace(term, "")
        return {
            "title": title,
            "title_clean": clean_title(title),
            "skills": " ".join(skills),
            "industry": job.get("industry") or "",
            "company": job.get("company") or "",
            "text": text + " " + " ".join(skills),
            "jd": jd or "",
        }

    def _truth(self, job, f):
        env = {}
        for name, p in self.patterns.items():
            if p[0] == "within":
                env[name] = set(job.get("skills") or []) <= p[1]
            else:
                env[name] = bool(p[2].search(f[p[1]]))
        for name, fn in self.defs:
            env[name] = fn(env)
        return env

    def classify(self, job):
        """返回 (桶名, 是否开详情, 命中词)。"""
        f = self.fields(job)
        hits = self.hits(f["text"])
        upper = salary_upper_k(job.get("salary"))
        if self.salary_floor_k and upper is not None and upper < self.salary_floor_k:
            return "删-低于薪资下限", False, hits
        env = self._truth(job, f)
        for b in self.buckets:
            if b["when"](env):
                return b["name"], b["detail"], hits
        return "未分类", False, hits

    def hits(self, text):
        found = set()
        for name in self.hit_patterns:
            p = self.patterns.get(name)
            if p and p[0] == "regex":
                found.update(m.group(0) for m in p[2].finditer(text))
        return "、".join(sorted(found))


def _compile(expr, known, where):
    """把 'a and (b or not c)' 编译成函数；只允许 and/or/not/括号/名字/true/false。"""
    if isinstance(expr, bool):
        return lambda env, v=expr: v
    src = re.sub(r"\btrue\b", "True", re.sub(r"\bfalse\b", "False", str(expr)))
    tree = ast.parse(src, mode="eval")
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id not in known:
            raise ValueError(f"{where}: 未定义的名字 {node.id}")
        if not isinstance(node, (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp,
                                 ast.Not, ast.Name, ast.Load, ast.Constant)):
            raise ValueError(f"{where}: 不支持的写法 {type(node).__name__}")
    code = compile(tree, where, "eval")
    return lambda env: bool(eval(code, {"__builtins__": {}}, env))


def load(path):
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return cfg, Rules(cfg)
