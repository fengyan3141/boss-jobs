# boss-jobs

在 BOSS 直聘按城市和关键词搜岗位，**边扫列表边按规则筛**，筛中的岗位当场打开详情抓完整 JD，结果存进本地 SQLite，可以导出 xlsx。

做成"边扫边抓"，是为了解决先扫名单、后抓详情这种做法的两个老问题：

- 搜索是模糊匹配，扫回来的岗位大多不是你要的。现在每扫一批，就先用职位名和技能标签过一遍规则，明确不要的不开详情。
- 名单扫完再回头抓详情，中间隔了几小时甚至几天，岗位已经下架了。现在从看到岗位到抓到 JD 只隔几分钟。

方向（关键词、城市、筛选规则）全部写在 `profiles/*.yaml` 里。想抓别的岗位，复制一份配置改一改就行，不用动代码。

## 安装

需要 Python 3.9+ 和 Chrome。

```bash
git clone https://github.com/fengyan3141/boss-jobs.git
cd boss-jobs
pip install -r requirements.txt
```

### 作为 Agent Skill 使用

仓库根目录有 `SKILL.md`，clone 到 agent 的 skills 目录下就能用。比如 Claude Code：

```bash
git clone https://github.com/fengyan3141/boss-jobs.git ~/.claude/skills/boss-jobs
pip install -r ~/.claude/skills/boss-jobs/requirements.txt
```

装好之后对 agent 说"帮我把北京的数据分析岗位抓下来看看"就会触发。agent 会先跟你确认城市和关键词，再上线抓取。

## 用法

```bash
# 扫列表 + 筛中即抓详情
python3 boss.py collect profiles/aigc-pm.yaml

# 先试一小把：只扫北京一个词，最多开 3 个详情
python3 boss.py collect profiles/aigc-pm.yaml --cities 北京 --keywords AIGC产品经理 --max-details 3

# 看进度和各分类数量（直接读本地库，随时可以跑）
python3 boss.py stats profiles/aigc-pm.yaml

# 导出 xlsx：总览 + 每个分类一张表，含 JD 全文和岗位链接
python3 boss.py export profiles/aigc-pm.yaml

# 改了规则后，对库里已有的岗位重新分桶（不用重新抓）
python3 boss.py reclassify profiles/aigc-pm.yaml

# 上次中途停了，给"要详情但还没抓到"的岗位补抓
python3 boss.py details profiles/aigc-pm.yaml

# 导出 JSON，给 JD 阅读页导入（只含已抓到 JD 的岗位）
python3 boss.py export profiles/aigc-pm.yaml --out 岗位.json

# 导入以前抓的 jsonl（BOSS 原始列表字段，可以带 jd_full）
python3 boss.py import profiles/aigc-pm.yaml old/*.jsonl
```

第一次运行会打开一个独立的 Chrome 窗口（登录信息保存在 `~/.boss-jobs/browser`），在窗口里用微信扫码登录 BOSS，程序会自动继续。跑完之后浏览器不关，下次不用重新登录。

整个过程只用 2 个标签页（列表页和详情页），详情页在后台打开，不会抢焦点，窗口可以最小化。只有遇到安全校验页时才会切到前台，等校验自动通过。

数据库默认在 `~/.boss-jobs/data/<配置文件名>.sqlite`，可以在配置里写 `db:`，或者用环境变量 `BOSS_DATA_DIR` 换位置。再跑同一个配置时，已经见过的岗位不会重复处理；详情失败两次的岗位不会再自动打开。

## JD 阅读页

`viewer/jd-viewer.html` 是一个单文件的岗位阅读页，双击就能在浏览器打开，不用联网也不用装东西：

- **录入**：在「录入数据」里拖入上面导出的 JSON；或者在 BOSS 上复制职位描述，粘贴到页面里逐条添加。
- **浏览**：概览页有高频技能标签、城市、经验和学历分布；岗位浏览页可以按城市、薪资、经验、技能标签筛选，也能搜 JD 里的词；投递管理页可以把岗位标成想看、已投、面试或已拒。
- **清洗**：导入或粘贴时会自动清掉「微信」「举报」、招聘者署名、资质说明、水印字，并把 BOSS 换进来的康熙部首（「能⼒」的「⼒」）还原成常用字。
- **分享**：数据只保存在当前浏览器里。点「保存为单文件」会生成一个带数据的 HTML，发给别人打开就能看。

页面不预设任何方向，标签和统计都从导入的数据里生成，什么岗位都能用。

## 写一份自己的方向配置

参考 `profiles/aigc-pm.yaml`。最少需要这些：

```yaml
name: 数据分析师
cities: [北京, 上海, {西安: "101110100"}]   # 不在 cities.py 里的城市，自己带上编码
keywords: [数据分析师, 商业分析]
salary_floor_k: 20          # 按薪资区间上限比较，0 表示不限

patterns:                   # 具名正则；默认匹配"职位名 + 技能标签"
  analyst: {in: title, regex: '数据分析|商业分析|BI'}
  not_role: {in: title, regex: '实习|销售|讲师'}
  senior: '(高级|资深|专家)'

defs:                       # 可选：用 and / or / not 组合出中间判断
  target: analyst and not not_role

buckets:                    # 从上往下匹配，第一个成立的生效；detail: true 才开详情
  - {name: 要-资深分析, when: target and senior, detail: true}
  - {name: 要-分析, when: target, detail: true}
  - {name: 不要, when: true}
```

pattern 可以匹配的字段：`title`（职位名）、`title_clean`（去掉编号和城市后缀的职位名）、`skills`（技能标签）、`industry`（公司行业）、`company`（公司名）、`text`（职位名 + 技能标签，默认）。
`skills_within: [...]` 表示"技能标签全部落在这个清单里，或者没有标签"。

写好规则后，可以先用 `--no-detail` 只扫列表，再用 `stats` / `export` 看分得对不对，调整之后用 `reclassify` 重新分桶，确认没问题再开详情。

## 防风控

- 两次页面操作之间至少隔 800ms，每批之后再随机等 2.5–5.5 秒，换关键词时等 4–8 秒。
- 每次运行默认最多 600 次页面操作（导航和滚动都算），用完就停，可以用 `--max-actions` 调整。
- 出现滑块或验证码、访问受限、接口返回错误码、登录失效，**立即停止**，不自动重试。这时先在浏览器里手动看一眼，隔一段时间再跑。
- 跳到 security-check 安全校验页时，先等最多 60 秒让它自动通过，过不了也停止。
- 本地不保存 `securityId`、cookie 这类会话参数。

数据只是采集时能看到的样本，不等于市场全量，也不代表这些岗位一定还在招。

## 声明

本工具只用于个人求职和岗位市场研究：它用你自己登录的账号、以接近人工浏览的速度查看公开的岗位信息。请遵守 BOSS 直聘的用户协议和相关法律法规，不要用于大规模抓取、转售数据或任何商业用途。使用本工具产生的账号限制等后果由使用者自行承担。
