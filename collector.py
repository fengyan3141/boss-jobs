"""浏览器部分：用已登录的 Chrome 打开 BOSS 搜索页，边滚列表边开详情。

几条踩过的坑（2026-09 ~ 10 实测）：
- 搜索结果只能靠滚动加载（每批约 15 条，单词最多约 300 条）；URL 里的 &page=N 是假翻页。
- 详情链接不带 securityId。它是一次性的，失败后重试会让这条岗位再也打不开。
- 后台标签页会被 Chrome 节流，安全校验页的脚本跑不起来。所以详情页平时放后台不抢焦点，
  遇到安全校验时再切到前台等它通过（见 check）。
- 跳到 security-check 页一般 30 秒左右会自动通过；滑块、验证码、访问受限才是真风控。
- 2026-10-22 实测：详情页直接渲染 JD，没有单独的详情接口可截，JD 都来自页面。
"""
import json
import random
import re
import time
from html import unescape
from urllib.parse import quote

from DrissionPage import ChromiumOptions, ChromiumPage

JOBLIST = "zpgeek/search/joblist"
DETAIL_API = "wapi/zpgeek"
SEARCH_URL = "https://www.zhipin.com/web/geek/job?query={kw}&city={city}"
DETAIL_URL = "https://www.zhipin.com/job_detail/{id}.html"

RISK_URL = ("verify", "captcha")
CHALLENGE_URL = ("security-check", "_security_check")
LOGIN_URL = ("/web/user",)
RISK_TEXT = ("请完成验证", "拖动滑块", "访问受限", "访问异常", "异常访问", "账号存在异常",
             "操作过于频繁", "访问行为异常")
CLOSED_TEXT = ("职位已关闭", "职位已下线", "该职位已失效", "停止招聘", "职位不存在", "已下架",
               "职位已过期")


class Stop(Exception):
    """需要结束本次运行。risk=True 表示平台风控，别马上重跑。"""

    def __init__(self, msg, risk=False):
        super().__init__(msg)
        self.risk = risk


def html_to_text(value):
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", str(value or ""))
    text = re.sub(r"<[^>]+>", "", text)
    lines = (re.sub(r"[ \t]+", " ", unescape(line)).strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def body_of(pkt):
    body = getattr(getattr(pkt, "response", None), "body", None)
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except ValueError:
            return {}
    return body if isinstance(body, dict) else {}


def nap(lo=2.5, hi=5.5):
    time.sleep(random.uniform(lo, hi))


class Browser:
    def __init__(self, profile_dir, port, min_interval_ms=800, max_actions=600):
        co = ChromiumOptions()
        co.set_local_port(port)
        co.set_user_data_path(profile_dir)
        co.set_argument("--no-sandbox")
        co.set_argument("--disable-crashpad")
        # 窗口最小化或被挡住时也照常跑，详情页才能放后台（只在新启动浏览器时生效）
        for flag in ("--disable-background-timer-throttling", "--disable-renderer-backgrounding",
                     "--disable-backgrounding-occluded-windows"):
            co.set_argument(flag)
        self.page = ChromiumPage(co)
        self.detail_tab = None
        # 浏览器是复用的：留下当前页和一个详情页，其余上次遗留的标签页关掉
        for tab in self.page.get_tabs():
            if tab.tab_id == self.page.tab_id:
                continue
            if self.detail_tab is None and "job_detail" in (tab.url or ""):
                self.detail_tab = tab
            else:
                tab.close()
        self.min_interval = min_interval_ms / 1000
        self.max_actions = max_actions
        self.actions = 0
        self._last = 0.0
        self._empty_streak = 0

    # ---- 节流与风控 ----

    def tick(self):
        """每次页面导航或滚动前调用：计数并保证最小间隔。"""
        if self.max_actions and self.actions >= self.max_actions:
            raise Stop(f"已用满本次 {self.max_actions} 次页面操作上限，主动停止")
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self.actions += 1
        self._last = time.monotonic()

    def page_text(self, tab):
        try:
            return tab.run_js("return document.body ? document.body.innerText.slice(0, 5000) : ''") or ""
        except Exception:
            return ""

    def check(self, tab, where, look_text=False):
        url = tab.url or ""
        if any(k in url for k in CHALLENGE_URL):
            print(f"   {where}：进入安全校验页，等它自动通过（最多 60 秒）")
            activate(self.page, tab)
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline and any(k in (tab.url or "") for k in CHALLENGE_URL):
                time.sleep(3)
            url = tab.url or ""
            if any(k in url for k in CHALLENGE_URL):
                raise Stop(f"{where}：安全校验 60 秒没有自动通过", risk=True)
        if any(k in url for k in RISK_URL):
            raise Stop(f"{where}：出现验证页", risk=True)
        if any(k in url for k in LOGIN_URL):
            raise Stop(f"{where}：跳到了登录页，登录状态失效，请在浏览器里重新登录", risk=True)
        if look_text:
            text = self.page_text(tab)
            hit = next((k for k in RISK_TEXT if k in text), None)
            if hit:
                raise Stop(f"{where}：页面提示「{hit}」", risk=True)

    def wait_packet(self, tab, timeout):
        try:
            return tab.listen.wait(timeout=timeout)
        except Exception:
            return None

    # ---- 列表 ----

    def open_home(self, kw, city, login_wait=300):
        """打开搜索页；没登录就在浏览器里等扫码，登录后继续。"""
        url = SEARCH_URL.format(kw=quote(kw), city=city)
        self.tick()
        self.page.get(url, timeout=20)
        time.sleep(3)  # 未登录时页面会稍后才跳到登录页
        if any(k in (self.page.url or "") for k in LOGIN_URL):
            activate(self.page, self.page)
            print(f"\n需要登录：请在打开的 Chrome 窗口里用微信扫码登录 BOSS（最多等 {login_wait // 60} 分钟）")
            deadline = time.monotonic() + login_wait
            while time.monotonic() < deadline and any(k in (self.page.url or "") for k in LOGIN_URL):
                time.sleep(3)
            if any(k in (self.page.url or "") for k in LOGIN_URL):
                raise Stop("等待扫码登录超时")
            print("已登录，继续")
            self.tick()
            self.page.get(url, timeout=20)
        self.check(self.page, "打开 BOSS", look_text=True)

    def search(self, city, kw, on_batch, where):
        """打开一个城市+关键词的搜索页，一批批滚动加载，每批交给 on_batch(jobs)，它返回新岗位数。"""
        p = self.page
        p.listen.start(JOBLIST)
        self.tick()
        p.get(SEARCH_URL.format(kw=quote(kw), city=city), timeout=20)
        self.check(p, where)
        batch, dead, zero_new = 1, 0, 0
        while True:
            pkt = self.wait_packet(p, 15)
            if not pkt:
                self.check(p, where, look_text=True)
                dead += 1
                self._empty_streak += 1
                if self._empty_streak >= 8:
                    raise Stop("连续 8 次拿不到列表数据，先停下来看看浏览器里是什么情况", risk=True)
                if dead >= 3:
                    return  # 滚不出新数据，这个词到底了
                self.scroll(p)
                continue
            dead = 0
            self._empty_streak = 0
            body = body_of(pkt)
            if body.get("code") not in (0, None):
                raise Stop(f"{where}：列表接口返回 code={body.get('code')} {body.get('message', '')}", risk=True)
            zp = body.get("zpData") or {}
            jobs = zp.get("jobList") or []
            if not jobs:
                return
            new = on_batch(jobs, batch)
            if zp.get("hasMore") is False:
                return
            zero_new = 0 if new else zero_new + 1
            if zero_new >= 4:
                return  # 连续几批都是看过的
            nap()
            p.listen.start(JOBLIST)
            self.scroll(p)
            batch += 1

    def scroll(self, p, max_scrolls=8):
        """连续滚动，直到触发下一批列表请求。"""
        target = None
        for sel in (".job-list-box", ".search-job-result", ".job-list-container", ".job-list"):
            target = p.ele(sel, timeout=0.5)
            if target:
                break
        p.actions.move_to(target or (700, 700), duration=0.2)
        for _ in range(max_scrolls):
            self.tick()
            p.actions.scroll(delta_y=random.randint(1400, 2000))
            time.sleep(random.uniform(0.8, 1.4))
            try:
                if p.listen._caught.qsize() > 0:
                    break
            except Exception:
                pass
        self.check(p, "滚动加载")

    # ---- 详情 ----

    def detail(self, job_id):
        """返回 (状态, JD, 来源)。状态：ok / closed（已下架）/ failed。遇到风控抛 Stop。"""
        if self.detail_tab is None:
            self.detail_tab = self.page.new_tab(background=True)
        t = self.detail_tab
        t.listen.start(DETAIL_API)
        try:
            self.tick()
            try:
                t.get(DETAIL_URL.format(id=job_id), timeout=20)
            except Exception as e:
                print(f"   详情打开出错：{type(e).__name__}")
                return "failed", "", ""
            self.check(t, "详情页")
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                pkt = self.wait_packet(t, deadline - time.monotonic())
                if not pkt:
                    break
                body = body_of(pkt)
                zp = body.get("zpData")
                if not isinstance(zp, dict):
                    continue
                info = zp.get("jobInfo") if isinstance(zp.get("jobInfo"), dict) else zp
                desc = info.get("postDescription") or info.get("jobDesc")
                if desc:
                    return "ok", html_to_text(desc), "api"
            # 正文里夹着 visibility:hidden 的水印 span（「直聘」「boss」「kanzhun」），
            # innerText 会跳过隐藏元素，element.text 不会
            text = ""
            if t.ele(".job-sec-text", timeout=3):
                text = html_to_text(t.run_js(
                    "return document.querySelector('.job-sec-text').innerText") or "")
            if len(text) >= 50:
                return "ok", text, "dom"
            page = self.page_text(t)
            if any(k in page for k in CLOSED_TEXT):
                return "closed", "", ""
            hit = next((k for k in RISK_TEXT if k in page), None)
            if hit:
                raise Stop(f"详情页提示「{hit}」", risk=True)
            return "failed", "", ""
        finally:
            try:
                t.listen.stop()
            except Exception:
                pass
            nap()


def activate(page, tab):
    try:
        tab.set.activate()
    except Exception:
        try:
            page.activate_tab(tab)
        except Exception:
            pass
