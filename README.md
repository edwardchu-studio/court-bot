# 🎾 court-bot — RIOC 网球场自动抢位

> civicpermits.com (RIOC) 在 T-2 天 08:00:00 ET 释放预约窗口. 此 bot 提前 5 分钟启动, 到点毫秒级提交.

> 📌 **你在 `public` 分支** — 这是给朋友用的简化版, 通知默认走 macOS 桌面通知中心, 不需要 Telegram bot. 想加 TG 推送看 `.env.example`.

---

## 朋友版部署 (4 步, 大约 30 分钟)

> **前提** (开始前请确认):
> - ✅ 你是 **macOS** 用户 (本工具暂不支持 Windows/Linux)
> - ✅ 你的 Mac 系统时区是 **America/New_York** (`date` 命令看, 显示 `EDT` 或 `EST`)
> - ✅ 你已经在 https://rioc.civicpermits.com 注册并能登录
> - ✅ Mac 在 07:55-08:01 ET 期间能保持**醒着 + 接电源 + 联网** (不必盯着, 但不能 sleep / 合盖断电)

### Step 1 — 装环境 (5 分钟)

```bash
git clone https://github.com/edwardchu-studio/court-bot.git
cd court-bot
git checkout public
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium
cp .env.example .env             # 默认 NOTIFY_PROVIDER=desktop, 不用改
```

### Step 2 — 一次性登录 (5 分钟)

```bash
.venv/bin/python login_persist.py
```

→ 弹出 Chromium 窗口
→ 你手动用 civicpermits 账号登录
→ 登录后切回终端按 [Enter]
→ `auth.json` 生成 (含 session cookies, **不要发给别人**)
→ 30 天后过期, 到时重跑此脚本

### Step 3 — 探测你想抢的 Court UUID (5 分钟)

```bash
.venv/bin/python probe_courts.py
```

→ 列出所有 Tennis Court 的 UUID
→ 复制你想抢的 court 的 UUID
→ 粘贴进 `config.yaml` 的 `location_uuid` 字段 (替换掉 TODO 占位符)

### Step 4 — 填 config.yaml (10 分钟)

打开 `config.yaml`, 改这两块:

**(a) `permit_answers`** — 把所有 `TODO:` 字段改成你的真实情况. RIOC 审核会看, 别照抄别人的:

```yaml
permit_answers:
  activity_description: "Casual tennis with my partner"
  num_people: "2"
  prior_permit: "No"     # 你以前是否在 RIOC 申过 permit
  # 其他字段保留默认即可
```

**(b) `preferences`** — 调整你想抢的时段优先级 (从高到低):

```yaml
preferences:
  - { start_hour: 18, end_hour: 19, label: "6-7 PM" }    # 最优先
  - { start_hour: 19, end_hour: 20, label: "7-8 PM" }
```

### Step 5 — 测试 + 部署

**dry-run 测试** (跑一次但不真 submit, 日期自动算 T-2):

```bash
.venv/bin/python reserve.py --no-submit --now --date $(date -v+2d +%Y-%m-%d)
```

应该看到桌面通知"🎾 court-bot 启动" + 浏览器自动填表 + dry-run 截图存到 `logs/`. 看不到通知见 [FAQ Q5](#q5-看不到桌面通知).

> ⚠️ 如果你想测的日期 RIOC 还没开放 (例如今天测后天的, 但 08:00 ET 释放窗口还没到), 会报 `日期 X 8s 内不可点击` — 这是预期的, 见 [FAQ Q2](#q2-dry-run-报-日期-x-8s-内不可点击-是不是坏了).

**定时跑** — 每天 07:55 ET 自动触发:

```bash
crontab -e
# 加这一行 (假设项目在 ~/court-bot):
55 7 * * * cd ~/court-bot && .venv/bin/python reserve.py >> logs/cron.log 2>&1
```

> ⚠️ Mac 必须**不能进入 sleep**. 见 [FAQ Q3](#q3-我-mac-会-sleep--合盖了怎么办).

> ⚠️ **ToS 提醒**: 自动化抢自己用的 1 个 slot 通常 OK. 别帮陌生人抢、别一人占多个、别提高 `max_attempts`. 被封号联系 RIOC 人工解封.

---

## FAQ — 朋友们最常问的

### Q1: 我怎么测试不真的抢一个?

加 `--no-submit` flag, 填完表停在 Submit 前一刻, 不真点提交:

```bash
.venv/bin/python reserve.py --no-submit --now --date $(date -v+2d +%Y-%m-%d)
```

跑完看 `logs/screen_*_dry_run_ready_to_submit.png` 截图确认表填对了.

### Q2: dry-run 报 "日期 X 8s 内不可点击" 是不是坏了?

**不是 bug**. RIOC 在 **T-2 天 08:00:00 ET** 才开放对应日期的预约窗口. 例:
- 今天周一 10:00 AM, 想 dry-run 周三 → 失败 (周三还没释放)
- 今天周一 08:01 AM, 想 dry-run 周三 → 成功 (周三刚释放)

**测试时机选择**:
- 用 `date -v+2d` 算的是后天, 必须等今天 08:00 ET 后才能测后天
- 或者用前天/昨天试试逻辑 (会报 "not available" 但能验证 fill_form 走通)

真实场景下你不需要手动跑, cron 会在每天 07:55 启动等到 08:00 抢明天后天.

### Q3: 我 Mac 会 sleep / 合盖了怎么办?

**这是头号失败原因**. 三层防护任选:

1. **改系统设置** (推荐, 一次搞定):
   - 系统设置 → 显示器 → 高级 → 打开 "**接通电源时, 防止自动进入睡眠**"
   - 系统设置 → 锁屏 → "**显示器关闭时间**" 设 "永不" (或长一点)

2. **临时撑着** (抢券前一晚跑):
   ```bash
   caffeinate -dimsu &  # 撑到下次重启或 kill
   ```

3. **合盖也能跑** — 装 [Amphetamine](https://apps.apple.com/us/app/amphetamine/id937984704) (App Store 免费), 设定 07:50-08:10 期间强制 awake.

**验证**: 半夜睡前跑 `pmset -g log | grep -i sleep | tail -5`, 看有没有 "Entered Sleep" — 如果有, 上面没设对.

### Q4: 08:00 弹个 Chrome 窗口很烦, 能后台跑吗?

**不能**. 实测 RIOC 的 "Add Facility" 按钮在 headless Chrome 下 JS 不响应 (有反爬检测). 三个缓解办法:

1. **新建一个 Desktop / Space** — 08:00 把 Chrome 窗口扔到那个 Space, 不影响你主屏工作
2. **缩小**: 脚本启动后 Chrome 会获得焦点 ~3 秒, 之后你可以 `Cmd-M` 最小化, 不影响后台抢券 (脚本不依赖窗口可见)
3. **预约重要会议日不跑** — 临时 `crontab -e` 注释那行就行

### Q5: 看不到桌面通知?

第一次 `osascript` 发通知 macOS 会拦截要授权. 修:

1. 系统设置 → 通知 → 找 "**Script Editor**" 或 "**osascript**" 或 "**Terminal**"
2. 打开 "允许通知" + 横幅样式选 "提示"
3. 重新跑一次 `reserve.py --no-submit --now --date ...` 应该就有了

**还看不到** → 改用 Telegram (见 `.env.example` 里 `NOTIFY_PROVIDER=telegram`), 或直接看 `logs/cron.log` 的最后几行也能看到结果.

### Q6: 报错 "cookies 失效, 跳到登录页"?

session cookie 过期了 (约 30 天). 重跑一次登录就好:

```bash
.venv/bin/python login_persist.py
```

旧 `auth.json` 会被覆盖. 下次抢券前一两天提前重登, 别等到 07:55 才发现.

### Q7: 报错 "找不到 facility listitem 'Tennis Courts'" / "Add Facility 8 次 click 都没弹 modal"?

RIOC 网站改版了, selector 失效. 两个办法:

1. **找作者修** — 我会更新 main, 你 `git pull && git checkout public && git merge main` 拿新 selector
2. **自己 codegen** (技术活):
   ```bash
   .venv/bin/playwright codegen https://rioc.civicpermits.com/Permits/New
   ```
   手动走一遍预约 (不真 submit), Inspector 自动生成新代码, 对比 `reserve.py` 的 `fill_form()` 看哪个 selector 变了, 替换掉.

### Q8: 我和朋友想抢同一个 court 同一时段怎么办?

**不要这样**. 你俩同时 08:00:00 撞 RIOC server, 谁先到谁拿, 另一个就 fail. 协调方式:

- **错开 `location_uuid`**: 跑 `probe_courts.py` 看所有 court UUID, 你抢 Court 1, 朋友抢 Court 2
- **错开 `preferences`**: 同一 court 但你抢 5 PM, 朋友抢 6 PM
- **轮流抢日**: 你抢周一周三, 朋友抢周二周四

### Q9: 没抢到 08:00 那一波, 还能补救吗?

可以, 手动跑 (会立刻试, 因为 08:00 已过):

```bash
cd ~/court-bot
.venv/bin/python reserve.py --now
```

但 08:00 那一刻黄金时段大概率被人秒了, 手动跑只能抢"别人放弃的剩货". 9:00 AM 后建议直接 `scan_availability.py` 看哪些时段还在:

```bash
.venv/bin/python scan_availability.py $(date -v+2d +%Y-%m-%d)
```

### Q10: permit_answers 应该怎么填才不被 RIOC 拒?

RIOC 审核人工看, 抓两点:
- **真实**: 别写 "Professional tournament training" 然后只有你一个人, 一眼假
- **简短**: 一句话, 别长篇大论

参考 (改成你自己的):
```yaml
activity_description: "Casual tennis with my partner"  # 或 "Tennis practice with a friend"
num_people: "2"
prior_permit: "Yes"      # 你过去申过 → 写 Yes (RIOC 会查记录, 写 No 反而触发审核)
parking_needs: "None"    # 几乎所有人都填 None
```

**关键**: `prior_permit` 别瞎填 — RIOC 数据库有你的申请历史, 你写错了反而显得可疑.

### Q11: 怎么停掉, 不想抢了?

```bash
crontab -e
# 删掉 court-bot 那一行, 保存退出
crontab -l    # 验证已删
```

或者临时 disable, 行首加 `#` 注释.

### Q12: 我想抢 2 小时不是 1 小时?

`config.yaml` 里 `end_hour = start_hour + 2`:

```yaml
preferences:
  - { start_hour: 18, end_hour: 20, label: "6-8 PM (2h)" }
```

⚠️ RIOC 单次预约**上限通常是 2 小时**, 别试 3 小时 (会被拒).

### Q13: 抢到了在哪看确认?

- **桌面通知**: 08:00:0X 会弹 "✅ 抢到了!" 横幅
- **logs/screen_*_success.png**: 成功截图, 含 confirmation 页面
- **civicpermits 网站**: 登录后 → My Permits → 看到 Pending/Approved 的就是你抢的
- **邮箱**: RIOC 会自动发 confirmation 邮件 (检查垃圾邮件夹)

### Q14: 我 1 个月没用了, 重新启用要做啥?

1. cookies 一定过期了 → 重跑 `login_persist.py`
2. RIOC 可能改版 → 跑一次 `--no-submit --now` 看会不会报 selector 错
3. 如果你换了 Mac → 整个流程重做 (auth.json 不能跨机器复制)

### Q15: 出问题怎么找作者?

把这三样发给我:
- `logs/reserve_<最新时间>.log` (最后 ~50 行)
- `logs/screen_*_<最新>.png` (失败截图)
- 你的 `config.yaml` (脱敏 — 把 location_uuid 留着, permit_answers 内容可以隐去)

---

## 高级 / 原作者部署清单 (按顺序做完就 ready)

### ✅ Step 1 — 我已做完
- 项目骨架 `~/projects/court-bot/`
- venv + Playwright + chromium 安装
- `config.yaml`, `reserve.py`, `login_persist.py` 写好
- `.env` 含 Telegram bot token (借用贞观系统)
- `launchd plist` 写好待加载

### 🟡 Step 2 — 你需要做的 3 件事

#### **(a) 登录持久化** (5 分钟, 一次性)

```bash
cd ~/projects/court-bot
.venv/bin/python login_persist.py
```

→ 弹出 Chromium 窗口
→ 你手动用 civicpermits 账号登录
→ 登录后切回终端按 [Enter]
→ `auth.json` 生成, 之后免登录

#### **(b) selector 探测** (15-30 分钟, 一次性)

`reserve.py` 里的 `fill_form()` 现在有 `NotImplementedError` 占位符. 你需要:

```bash
cd ~/projects/court-bot
.venv/bin/playwright codegen https://rioc.civicpermits.com/Permits/New
```

这会:
1. 弹出一个 Chromium + Inspector 窗口
2. **你手动执行一次完整的预约流程** (但**最后不要真的点 submit**)
3. Inspector 自动生成对应的 Playwright 代码
4. **把生成的代码 copy 出来粘进 reserve.py 的 `fill_form()` 函数**
5. 删掉 `raise NotImplementedError(...)` 这行

参考样例 (你的实际 selector 可能不同):
```python
def fill_form(page, target_date_iso, court, time_slot, duration_min):
    page.goto(cfg["url"])
    page.click("text=Tennis Court")              # 选服务类型
    page.fill("input[name='Date']", target_date_iso)
    page.click(f"text={court}")                   # 球场
    page.click(f"text={time_slot}")               # 时段
    # 任何中间步骤 (continue 按钮, 选 duration 等)
    # 但**不要点最后的 submit**, 留给 submit_form()
```

同样的, `submit_form()` 的 submit button selector 和成功判断也要根据真实页面调:
```python
def submit_form(page):
    page.click("button.btn-primary:has-text('Submit Permit')")  # 真实 selector
    page.wait_for_url("**/Confirmation/**", timeout=8000)        # 真实成功 URL
    return True
```

#### **(c) dry-run 测试** (5 分钟)

```bash
cd ~/projects/court-bot
.venv/bin/python reserve.py
```

注意当前 `release_time` 是今天 08:00 ET, 今天 (周三) 已经过了 → 脚本会立刻尝试抢周五的位.

**dry-run 测试**: 先把 `submit_form()` 里的 `page.click(submit_btn)` 临时改为 `print("would click submit")` , 跑一次确认填表无误后再改回.

或者更稳的测试方法 — 改 `config.yaml`:
```yaml
release_time:
  hour: 23     # 改成 23:55 测试用 (随便挑你测试时刻的几分钟后)
  minute: 55
```
跑 reserve.py, 看脚本能否:
- 打开浏览器
- 填好表
- 等到设定时间
- 试提交 (这里会失败因为不是真的 8AM 释放, 但能验证整流程)

### 🟢 Step 3 — 部署 launchd 自动触发

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/me.tennis-bot.plist
launchctl print gui/$(id -u)/me.tennis-bot     # 验证已加载
```

明天 (周四) 07:55 ET 会自动启动. 你应该:
- 07:54 收到 Telegram "🎾 court-bot 启动"
- 08:00:0X 收到 "✅ 抢到了!" 或 "❌ 没抢到"

---

## 抢位策略 (config.yaml 里调)

`preferences` 是有序的优先级列表. 第一个抢不到自动试下一个. 例:
```yaml
preferences:
  - { court: "Court 5", time_slot: "09:00 AM", duration_min: 60 }
  - { court: "Court 5", time_slot: "10:00 AM", duration_min: 60 }
  - { court: "Court 4", time_slot: "09:00 AM", duration_min: 60 }
```

`max_attempts: 40` + `retry_interval_ms: 500` → 8:00 后 20 秒内最多重试 40 次. 网站如果有 rate limit 自动节流.

---

## 故障排查

| 症状 | 排查 |
|---|---|
| Telegram 没收到任何消息 | `cat ~/projects/court-bot/.env` 看 TG_BOT_TOKEN 是否对; `tail logs/reserve_*.log` 看错误 |
| 一直 "fill_form NotImplementedError" | 还没做 Step 2 (b), 用 codegen 录 selector |
| 浏览器启动失败 | `.venv/bin/playwright install chromium` 重装 |
| cookies 过期 | 重跑 `login_persist.py` |
| 表单字段错位 / 抢错日期 | civicpermits 改版了, selector 失效. 重跑 codegen 更新 |
| launchd 没触发 | `launchctl list \| grep tennis` 看是否在; 注意 macOS Sleep 时可能错过, 关闭"自动 sleep" |

## 应急 manual override

如果脚本失败你想立刻手动跑:
```bash
cd ~/projects/court-bot
.venv/bin/python reserve.py
```
随时可执行, 内部会读 config.yaml 的 release_time, 已过则立即抢.

## ⚠️ ToS 提醒

civicpermits.com 是 RIOC 公共预约系统. 自动化"为自己抢一个时段"通常 OK, 但禁止:
- 同时抢多个名额转售
- 高频请求 (我们设了 max_attempts=40 + 0.5s 间隔, 不算 DDoS)
- 帮陌生人抢

如果被检测到异常封号, 联系 RIOC 人工解封.
