---
name: boss-jobs
description: 在 BOSS 直聘按城市和关键词采集岗位，边扫列表边按方向规则分桶，筛中的岗位当场抓完整 JD，存进本地 SQLite 并可导出 xlsx。当用户要抓岗位、采集 JD、做岗位/招聘市场研究、把某个方向的 BOSS 岗位抓下来筛时使用。Use when the user wants to collect, filter, or analyze BOSS Zhipin (zhipin.com) job listings with full JD text for any job direction. Not for other job sites, applying to jobs, or messaging recruiters.
---

# BOSS 岗位采集

本 skill 目录就是工具本身。命令都在这个目录下用 `python3 boss.py …` 执行，用法和配置写法见 `README.md`。第一次用先装依赖：`pip install -r requirements.txt`。

## 三条底线

1. **上线之前，用户要明确说过这次抓什么**：哪些城市、哪些关键词、要不要抓详情。只是讨论需求不算同意；"数量少就多扩几个词"同样需要用户点头。
2. **程序因为风控停下来**（验证码、访问受限、接口报错、登录失效）时，不要重跑，也不要换端口、换账号、调参数绕过去。把停止原因原样告诉用户，由用户在浏览器里处理，隔一段时间之后再跑。
3. **结论只是"采集时可见的样本"**，不等于市场全量，也不代表岗位一定还在招。汇报时要带上这句话。

## 工作顺序

1. **选或写方向配置。** `profiles/` 里有示例，新方向就复制一份改。规则拿不准时，先用 `collect --no-detail` 只扫列表，用 `stats` / `export` 看分桶对不对，改完规则用 `reclassify` 重新分桶，确认之后再开详情。
2. **先看本地。** 有旧数据时先跑 `stats`，想清楚缺什么再上线。
3. **先小后大。** 先用一城一词、`--max-details 3` 试一下，确认 JD 能抓到，再放量。长任务用 `nohup … &` 放后台跑。
4. **汇报读库，不读日志。** 进度用 `stats` 查。在"首批详情成功""停止""跑完"这几个节点各报一次。
5. **交付。** 用 `export` 导出 xlsx。给用户的 JD 直接取库里的原文，不让模型改写。

## 注意

- 第一次运行，或者登录失效时，程序会在弹出的 Chrome 窗口里等用户扫码登录（最多 5 分钟），这一步必须用户本人操作。浏览器跑完不关，登录状态保存在 `--browser-profile` 目录里（默认 `~/.boss-jobs/browser`）。
- 默认每次运行最多 600 次页面操作，两次操作之间至少隔 800ms。放宽 `--max-actions` 之前先问用户：操作过多会触发平台限流。
- 详情状态分四种：已抓到、已下架、失败、未开。失败两次的岗位不会再自动打开。
- 数据默认存在 `~/.boss-jobs/data/<配置名>.sqlite`，可以用配置里的 `db:` 或环境变量 `BOSS_DATA_DIR` 改位置。
