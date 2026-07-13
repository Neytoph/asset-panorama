# -*- coding: utf-8 -*-
"""
负债台账（轻量）：房贷/车贷等的余额按月推演、利息月耗、留尾测算。

余额不用手动更新：存「基准年月 + 基准本金」，按利率与月供逐月推演到当前
（bal = bal×(1+年利率/12) − 月供；每月还款日过后才算当月已还）。
年利率留空 → 不推演（余额=基准本金），并给「利率未填」提示。

口径约定：
· 月供已在 cashflow.json 月度收支里手动记支出，本模块不再计入 fixed_out（防双计）。
· 面板展示「利息月耗」= 余额×月利率——月供里只有利息是真消耗，本金是资产内转移。
· 净资产口径仍以 accounts.csv 负债行为准；本模块推演值与其偏差过大时面板提示校准。

CLI：
  python3 loans.py                     台账概览（推演余额/利息月耗）
  python3 loans.py tail 月供 年利率%    留尾测算：月供目标在各期限下对应的本金
"""
import sys
from datetime import date

import storage

TYPES = ("房贷", "公积金贷", "车贷", "消费贷", "其他")
STATUSES = ("在还", "已清")


def load_loans():
    try:
        return storage.load_doc("loans", {}).get("负债", [])
    except Exception:
        return []


def save_loans(loans):
    storage.save_doc("loans", {
        "_note": "负债台账。余额由 基准年月+基准本金+利率+月供 按月推演；月供支出仍手动记在月度收支。",
        "负债": loans})


def _months_elapsed(base_ym, today, pay_day):
    """基准年月（其还款日后的余额=基准本金）之后已发生的还款次数。"""
    try:
        by, bm = int(str(base_ym)[:4]), int(str(base_ym)[5:7])
    except (ValueError, TypeError, IndexError):
        return 0
    n = (today.year - by) * 12 + (today.month - bm)
    try:
        pd = int(pay_day or 1)
    except (ValueError, TypeError):
        pd = 1
    if today.day < pd:
        n -= 1
    return max(0, n)


def derive(l, today=None):
    """→ {**l, 余额, 利息月耗, 还清约, 提示}。已清/零本金直接归零。"""
    today = today or date.today()
    out = {**l, "余额": 0.0, "利息月耗": 0, "还清约": "", "提示": ""}
    bal = float(l.get("基准本金") or 0)
    pmt = float(l.get("月供") or 0)
    if l.get("状态", "在还") != "在还" or bal <= 0:
        return out
    rate_raw = l.get("年利率", "")
    if rate_raw in ("", None):
        out.update(余额=round(bal, 2), 提示="年利率未填，余额未按月推演")
        return out
    try:
        int(str(l.get("基准年月", ""))[:4]), int(str(l.get("基准年月", ""))[5:7])
    except (ValueError, TypeError, IndexError):
        out.update(余额=round(bal, 2), 提示="基准年月未填，余额未推演（填起贷月或对账单月份）")
        return out
    r = float(rate_raw)
    i = r / 12
    for _ in range(_months_elapsed(l.get("基准年月", ""), today, l.get("还款日", 1))):
        bal = bal * (1 + i) - pmt
        if bal <= 0:
            out["还清约"] = "已推演至 0"
            return {**out, "余额": 0.0}
    out["余额"] = round(bal, 2)
    out["利息月耗"] = round(bal * i)
    if pmt <= bal * i:
        out["提示"] = "月供不足以覆盖利息，余额只增不减"
        return out
    # 还清预估：继续模拟（上限 600 期）
    b, n = bal, 0
    while b > 0 and n < 600:
        b = b * (1 + i) - pmt
        n += 1
    if n < 600:
        y, m = (today.year * 12 + today.month - 1 + n) // 12, (today.year * 12 + today.month - 1 + n) % 12 + 1
        out["还清约"] = f"{y}-{m:02d}"
    return out


def enrich(loans=None, today=None):
    if loans is None:
        loans = load_loans()
    return [derive(l, today) for l in loans]


def totals(items):
    """(余额合计, 利息月耗合计) —— 仅在还。"""
    live = [x for x in items if x.get("状态", "在还") == "在还"]
    return (round(sum(x["余额"] for x in live), 2), round(sum(x["利息月耗"] for x in live)))


def principal_for(pmt, r, months):
    """月供 pmt、年利率 r、期限 months 对应的可支撑本金（年金现值）。"""
    i = r / 12
    if i == 0:
        return pmt * months
    return pmt * (1 - (1 + i) ** -months) / i


def tail_table(pmt, r, years=(5, 7, 10, 15, 20, 25)):
    """留尾测算：[(年限, 本金), ...]。"""
    return [(y, round(principal_for(pmt, r, y * 12))) for y in years]


def _cli(argv):
    if len(argv) > 1 and argv[1] == "tail":
        pmt, rate = float(argv[2]), float(argv[3]) / 100
        print(f"月供 ¥{pmt:,.0f} · 年利率 {rate:.2%} 可支撑的尾巴本金：")
        for y, p in tail_table(pmt, rate):
            print(f"  {y:>2d} 年  ¥{p:,.0f}")
        return
    items = enrich()
    if not items:
        print("负债台账为空（编辑器「💳 负债」Tab 录入）")
        return
    for x in items:
        tag = "✅已清" if x.get("状态") != "在还" or x["余额"] == 0 else "💳"
        hint = f"  ⚠ {x['提示']}" if x.get("提示") else ""
        end = f"  还清约{x['还清约']}" if x.get("还清约") else ""
        print(f"  {tag} {x.get('名称','')}({x.get('类型','')})  余额 ¥{x['余额']:,.0f}"
              f"  利息月耗 ¥{x['利息月耗']:,}{end}{hint}")
    bal, ints = totals(items)
    print(f"  合计：余额 ¥{bal:,.0f} · 利息月耗 ¥{ints:,}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        _cli(sys.argv)
        sys.exit(0)
    # ── 自测 ──
    today = date(2026, 7, 4)
    # 推演：2026-05 基准 10 万、1.2%、月供 5000 → 已还 2 期（6/1、7/1）
    l = {"名称": "t", "类型": "消费贷", "基准本金": 100000, "基准年月": "2026-05",
         "年利率": 0.012, "月供": 5000, "还款日": 1, "状态": "在还"}
    d = derive(l, today)
    b = 100000 * (1 + 0.001) - 5000
    b = b * (1 + 0.001) - 5000
    assert abs(d["余额"] - round(b, 2)) < 0.01, d
    assert d["还清约"] and d["利息月耗"] == round(b * 0.001)
    # 还款日未到 → 少算一期
    d2 = derive({**l, "还款日": 15}, today)
    assert abs(d2["余额"] - round(100000 * 1.001 - 5000, 2)) < 0.01
    # 利率未填 → 不推演 + 提示
    d3 = derive({**l, "年利率": ""}, today)
    assert d3["余额"] == 100000 and "未填" in d3["提示"]
    # 月供覆盖不了利息 → 提示
    d4 = derive({**l, "月供": 50}, today)
    assert "不减" in d4["提示"]
    # 已清归零
    assert derive({**l, "状态": "已清"}, today)["余额"] == 0.0
    # 留尾测算：0 利率=线性；年金现值单调
    assert principal_for(1000, 0, 120) == 120000
    tt = dict(tail_table(14000, 0.026))
    assert 0 < tt[5] < tt[10] < tt[25]
    assert abs(principal_for(14000, 0.026, 84) - 14000 * (1 - (1 + 0.026 / 12) ** -84) / (0.026 / 12)) < 1
    print("✅ loans 自测通过：按月推演/还款日边界/利率缺失降级/覆盖不了利息/留尾年金")
