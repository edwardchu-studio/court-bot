# 🎾 court-bot — RIOC 网球场自动抢位

> civicpermits.com (RIOC) 在 T-2 天 08:00:00 ET 释放预约窗口. 此 bot 提前 5 分钟启动, 到点毫秒级提交.

## 部署清单 (按顺序做完就 ready)

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
