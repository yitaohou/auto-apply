[English](README.md) | 简体中文

# auto-apply

Claude Code 上的半自动 ATS 求职申请工作流,由 ego-browser(ego lite)驱动。你提供一份职位 URL 队列;agent 逐个打开申请页、用你的档案填写表单,然后停在提交按钮前等你亲手点击(如果你开启自动提交,则由它提交并核验确认信息)。外部能力——例如读取邮箱验证码——由单独安装的 [provider](PROVIDERS.md) 提供;本核心不内置任何 provider。

## 前置条件

- 已安装 ego lite(ego-browser)技能的 Claude Code
- ego lite 中已登录你要投递的网站(它复用你的浏览器会话)

## 安装

1. 安装本 plugin(把 Claude Code 指向本仓库,或从收录它的 marketplace 安装)
2. 重启会话——plugin 在启动时加载

## 首次配置

1. **数据目录。** 首次运行会根据内置模板生成 `~/job-search/`。需要填写:
   - `candidate_profile.json` —— 你的事实信息(永不猜测,只照实引用)
   - `answer_bank.md` —— 填进表单的措辞
   - `data/resume_rules.csv` —— 哪类职位用哪个简历版本
2. **设置。** `data/settings.csv` 默认 `auto_submit=off`(agent 永不点击最终提交)、`batch_size=10`。设置值只能你本人修改——agent 不会改。
3. **Provider(可选,邮箱验证码功能需要)。** 让 agent *注册 provider*:它会列出 [PROVIDERS.md](PROVIDERS.md) 里的选项,经你确认后安装你选中的那个,询问授权(`email_access = read_only`)与邮箱地址,然后写入绑定。之后重启会话,并完成该 provider 自声明的访问准备(第一方 Gmail 读取器:在 ego lite 里登录一次 Gmail)。跳过这一步的唯一后果是:需要邮箱验证码的职位会被记为 blocked。

## 启动一次投递

1. 把职位 URL 逐行放进 `~/job-search/queue.txt`
2. 对 agent 说:**"run the queue"**(或 "apply to these jobs"、"process queue.txt")
3. `auto_submit=off` 时,agent 把每个填好的表单停在提交按钮前,并把浏览器 Space 交给你——逐个亲手点击提交,**不要关闭页面**,点完告诉 agent,它会逐个标签页核验结果
4. 结果在 `~/job-search/data/` 查看:`job_pool.csv`(每个职位的状态)、`blocker_queue.csv`(需要你处理的事项)、`daily_dashboard.csv`(每日汇总)

只有观察到确认证据,一个职位才会被记为 **Submitted**——点击过提交按钮永远不等于已提交。

## 目录结构

| 路径 | 内容 |
|---|---|
| `skills/auto-apply/` | 技能本体:SKILL.md + playbook、浏览器操作手册、能力索引、注册流程 |
| `contracts/` | provider 实现的能力契约 |
| `PROVIDERS.md` | 经验证的 provider 策展清单 |

本项目的部分结构改编自 [`yvonnehe772/applypilot`](https://github.com/yvonnehe772/applypilot)(MIT 许可——见 `LICENSE`)。
