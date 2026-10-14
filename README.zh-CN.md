[English](README.md) | 简体中文

# auto-apply

Claude Code 上的半自动 ATS 求职申请工作流,由 ego-browser(ego lite)驱动。你提供一份职位 URL 队列;agent 逐个打开申请页、用你的档案填写表单,然后停在提交按钮前等你亲手点击(如果你开启自动提交,则由它提交并核验确认信息)。内置两个子 agent:一个从你的邮箱(只读)读取验证码,一个在 LinkedIn/Indeed 上搜索新职位。

## 前置条件

- 已安装 ego lite(ego-browser)技能的 Claude Code
- ego lite 中已登录你要投递的网站(它复用你的浏览器会话)
- `PATH` 中有 Python 3(搜索辅助脚本用)

## 安装

1. 把本仓库添加为 plugin marketplace,然后安装:

       claude plugin marketplace add yitaohou/auto-apply
       claude plugin install auto-apply@auto-apply

   (也可以在会话里用交互式的 `/plugin` 界面完成同样的事;想试本地克隆的话,把 `marketplace add` 指向其路径即可)
2. 重启会话——plugin 在启动时加载

## 首次配置

1. **个人档案——说一句 "set up" 就行。** agent 会发起配置访谈:先在 Finder 里打开简历文件夹让你把简历拖进去,然后从简历起草你的档案,**每个值都经你确认后才写入**——签证、薪资、到岗时间这类敏感信息永远直接问你、绝不推断。访谈会填好 `~/job-search/` 下的 `candidate_profile.json`、`answer_bank.md` 和 `data/resume_rules.csv`;这些文件你随时也可以手动编辑。
2. **设置。** `data/settings.csv` 只有三个键,全部由你本人修改——agent 永远不写这个文件:
   - `auto_submit`——`on`(默认):agent 点击最终提交并核验确认页;`off`:每个填好的表单停下等你点击。
   - `email_access`——`off`(默认)或 `read_only`。设为 `read_only` 后,需要邮箱验证码的职位交给内置的 `email-code-reader` agent 处理:它从你在 ego lite 中登录的 Gmail 读取验证码,绝不发送、回复或删除任何邮件。启用前先在 ego lite 里登录一次 Gmail。
   - `email_address`——ATS 发送验证码的目标地址。

   跳过邮箱访问的唯一后果是:需要验证码的职位会被记为 blocked。

## 启动一次投递

1. 把职位 URL 逐行放进 `~/job-search/queue.txt`
2. 对 agent 说:**"run the queue"**(或 "apply to these jobs"、"process queue.txt")
3. `auto_submit=off` 时,agent 把每个填好的表单停在提交按钮前,并把浏览器 Space 交给你——逐个亲手点击提交,**不要关闭页面**,点完告诉 agent,它会逐个标签页核验结果
4. 需要你处理的事项(验证码、登录墙、档案答不了的问题)会出现在 agent 打开的 dashboard 标签页上;处理完一项就打个勾
5. 结果在 `~/job-search/data/` 查看:`job_pool.csv`(每个职位的状态)、`blocker_queue.csv`(需要你处理的事项)、`daily_dashboard.csv`(每日汇总)

只有观察到确认证据,一个职位才会被记为 **Submitted**——点击过提交按钮永远不等于已提交。

## 搜索职位

填好 `~/job-search/data/search_profile.csv`(关键词、地点、远程政策、时间窗口、自由文本要求、默认结果数),然后说 **"find me jobs"**。`job-searcher` agent 会把匹配的职位追加到 `queue.txt`;之后再跑队列。搜索永远不会自动发生。

## 目录结构

| 路径 | 内容 |
|---|---|
| `skills/auto-apply/SKILL.md` | 技能本体:契约、边界、运行循环 |
| `skills/auto-apply/references/` | `blockers.md`(状态、确认规则、阻塞处理)、`ats-notes.md`(各 ATS 事实)、`page-loop.md`(逐页填表循环)、`dashboard.md`、`setup.md` |
| `skills/auto-apply/scripts/` | 逐页填表循环——`walker.js`(页面 → 字段)、`decide.py`(字段 → 来源,规则缓存)、`driver.js`(填入 + 回读)、`formfill.js`(在 `ego-browser nodejs` 内运行的入口);`fixtures/form.html` 测试表单;`seen.py` 去重/规范化 |
| `skills/auto-apply/templates/` | 档案模板与首次运行时复制到 `~/job-search/data/` 的 CSV 表头 |
| `agents/` | `email-code-reader`、`job-searcher` |

本项目的部分结构改编自 [`yvonnehe772/applypilot`](https://github.com/yvonnehe772/applypilot)(MIT 许可——见 `LICENSE`)。
