# -*- coding: utf-8 -*-
"""
缴费/扣费到期提醒 → macOS 系统通知（osascript）。
由 run_daily.sh 每日触发一次；面板告警是每天都显示的「状态」，
系统通知只在**整点日**弹（订阅 7/3/1 天前、保险 30/7/1 天前），避免连续 30 天轰炸。
无网络依赖：金额换算用 FX_FALLBACK 即可（提醒的重点是日期不是精确金额）。
"""
import subprocess
from collections import defaultdict
from datetime import date, timedelta

import storage
import subscriptions as subs
import insurance
from portfolio_tracker import FX_FALLBACK

SUB_DAYS = (7, 3, 1)   # 保险档位按频率由 insurance.reminder_days 给（年/半年 30/7/1，季/月 7/1）
MANUAL_STALE_DAYS = 14   # 手动账户超过这么多天没更新 → 进待办
TODO_EVERY_DAYS = 3      # 待办通知每 3 天弹一次(避免连续轰炸,面板横幅是每天都在的)


def collect_notifications(today=None):
    today = today or date.today()
    out = []
    for s in subs.load_subs():
        if s.get("状态", "启用") != "启用":
            continue
        try:
            nd = subs.next_charge(s, today)
        except Exception:
            continue
        delta = (nd - today).days
        if delta in SUB_DAYS:
            cny = round(subs.charge_cny(s, FX_FALLBACK))
            out.append((f"订阅扣费 · {delta}天后",
                        f'{s["名称"]} {nd.isoformat()} 扣 ¥{cny:,}'))
    for p in insurance.load_policies():
        nd = insurance.next_due(p, today)
        if not nd:
            continue
        delta = (nd - today).days
        if delta in insurance.reminder_days(p):
            extra = " · 续保核对新费率" if insurance.is_renewable(p) else ""
            out.append((f"保险缴费 · {delta}天后",
                        f'{p.get("成员","")}·{p.get("产品","")} {nd.isoformat()} 应缴 '
                        f'¥{insurance.per_due(p):,.0f}/{insurance.freq_of(p)}{extra}'))
    return out


def collect_todos(today=None):
    """管家待办:手动值过期 / 上月未对账 / 台账数量不一致 → 合并为一条通知。"""
    today = today or date.today()
    todos = []
    # ① 手动账户估值过期
    stale = []
    for name, rec in storage.load_doc("manual_values", {}).items():
        try:
            days = (today - date.fromisoformat(rec.get("updated", ""))).days
        except ValueError:
            continue
        if days > MANUAL_STALE_DAYS:
            stale.append(f"{name}({days}天)")
    if stale:
        todos.append("手动值过期:" + "、".join(stale))
    # ② 上月尚未月度对账(月初前两天不催,账单未出全)
    prev = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    if today.day >= 3 and not any(
            r.get("月份") == prev and r.get("已对账") == "是"
            for r in storage.load_table("cashflow_history", [])):
        todos.append(f"{prev} 未对账(面板「月度对账」Tab)")
    # ③ 台账推演数量 vs 实际持仓
    led = defaultdict(float)
    for r in storage.load_table("holdings_history", []):
        try:
            q = float(str(r.get("数量", "")).replace(",", "").strip() or 0)
        except ValueError:
            continue
        led[r.get("名称", "")] += -q if r.get("动作") == "卖出" else q
    cur = {}
    for h in storage.load_table("holdings", []):
        try:
            cur[h["名称"]] = float(str(h.get("持有数量", "0")).replace(",", ""))
        except ValueError:
            continue
    bad = [n for n in sorted(set(led) | set(cur))
           if abs(led.get(n, 0.0) - cur.get(n, 0.0)) > 1e-6]
    if bad:
        todos.append("台账数量不一致:" + "、".join(bad))
    return todos


def notify(title, body):
    # osascript 字符串内不能有未转义双引号
    t, b = title.replace('"', "'"), body.replace('"', "'")
    subprocess.run(
        ["osascript", "-e", f'display notification "{b}" with title "{t}" sound name "Glass"'],
        capture_output=True, timeout=10)


if __name__ == "__main__":
    msgs = collect_notifications()
    for title, body in msgs:
        notify(title, body)
        print(f"🔔 {title}: {body}")
    if not msgs:
        print("今日无到期提醒")
    todos = collect_todos()
    if todos:
        if date.today().toordinal() % TODO_EVERY_DAYS == 0:
            notify(f"📋 面板待办 {len(todos)} 项", " ｜ ".join(todos)[:220])
        for t in todos:
            print(f"📋 待办: {t}")
