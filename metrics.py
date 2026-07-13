#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
投顾级指标引擎（零依赖，纯函数）：
- xirr：资金加权年化收益（对 holdings_history 现金流 + 当前市值）
- portfolio_perf：组合/大类/单持仓 的 累计资金加权收益 + XIRR
- attribution：净资产增长归因（储蓄 / 金融投资收益 / 房产净值变动），三块精确闭合
- fi_plan：财务自由线（固定支出×12/提取率）+ 进度 + 三情景到达年限（实际回报口径）
- rebalance_plan：再平衡执行单（5/25 阈值 + 增量定投定向分配，不卖出优先）
"""
import datetime
import math
from collections import defaultdict


# ── XIRR ──────────────────────────────────────────────────────────

def xirr(flows):
    """flows: [(date, amount)]，date 为 'YYYY-MM-DD' 或 date；投入为负、回收/终值为正。
    返回年化收益率（小数），无解/跨度为零返回 None。二分法，稳健优先。"""
    parsed = []
    for d, v in flows:
        if isinstance(d, str):
            d = datetime.date.fromisoformat(d[:10])
        parsed.append((d, float(v)))
    if len(parsed) < 2:
        return None
    parsed.sort(key=lambda x: x[0])
    t0 = parsed[0][0]
    span = (parsed[-1][0] - t0).days
    if span <= 0:
        return None
    times = [((d - t0).days / 365.0, v) for d, v in parsed]
    if not (any(v > 0 for _, v in times) and any(v < 0 for _, v in times)):
        return None

    def npv(r):
        return sum(v / (1.0 + r) ** t for t, v in times)

    lo, hi = -0.9999, 100.0
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = npv(mid)
        if abs(f_mid) < 1e-9:
            return mid
        if f_lo * f_mid < 0:
            hi = mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2


def _ledger_flows(rows, fx):
    """holdings_history 行 → {名称: [(date, ±成交额CNY)]}。期初/买入为负(投入)，卖出为正。"""
    flows = defaultdict(list)
    for r in rows:
        try:
            v = float((r.get("成交额") or "").strip())
        except ValueError:
            continue
        d = (r.get("日期") or "").strip()
        if not d:
            continue
        rate = fx.get((r.get("成交币种") or "").strip() or "CNY", 1.0)
        sign = 1.0 if r.get("动作") == "卖出" else -1.0
        flows[r.get("名称", "")].append((d, sign * v * rate))
    return dict(flows)


def portfolio_perf(ledger_rows, holdings, fx, asof=None):
    """组合与大类的资金加权收益。终值 = 当前市值（只统计台账里出现过的持仓）。
    大类按台账「资产类型」归口：黄金ETF→黄金，其余可定价证券→权益。
    返回 {total:{cum,xirr,pnl,invested,days}, byClass:{类:{...}}, byHolding:[...]}"""
    asof = asof or datetime.date.today().isoformat()
    flows = _ledger_flows(ledger_rows, fx)
    cls_of = {}
    for r in ledger_rows:
        n = r.get("名称", "")
        cls_of[n] = "黄金" if (r.get("资产类型") or "").startswith("黄金") else "权益"
    value = {h["name"]: h["value"] for h in holdings}

    def stat(names):
        fl, invested, term = [], 0.0, 0.0
        for n in names:
            if n not in flows or n not in value:
                continue
            fl += flows[n]
            invested += -sum(v for _, v in flows[n])   # 净投入
            term += value[n]
        if not fl or invested <= 0:
            return None
        first = min(d for d, _ in fl)
        days = (datetime.date.fromisoformat(asof) - datetime.date.fromisoformat(first)).days
        pnl = term - invested
        r = xirr(fl + [(asof, term)])
        return {"cum": pnl / invested, "xirr": r, "pnl": round(pnl),
                "invested": round(invested), "days": days}

    names_all = [n for n in flows if n in value]
    by_class = {}
    for cls in sorted({cls_of.get(n, "权益") for n in names_all}):
        s = stat([n for n in names_all if cls_of.get(n, "权益") == cls])
        if s:
            by_class[cls] = s
    by_holding = []
    for n in names_all:
        s = stat([n])
        if s:
            by_holding.append({"name": n, **s})
    by_holding.sort(key=lambda x: -x["invested"])
    return {"total": stat(names_all), "byClass": by_class, "byHolding": by_holding}


# ── 净资产增长归因 ─────────────────────────────────────────────────

def attribution(history_rows, cashflow_rows):
    """history.csv(date,总净资产,金融资产,房产=估值−负债) + cashflow_history(月份,净结余,已对账)
    → ΔNW = 储蓄 + 金融投资收益(推算) + 房产净值变动，按构造精确闭合。
    投资收益(推算) = Δ金融资产 − 期间月净结余合计（储蓄默认落在金融资产里）。"""
    rows = [r for r in history_rows if r.get("date")]
    if len(rows) < 2:
        return None

    def num(r, k):
        try:
            return float(r.get(k) or 0)
        except ValueError:
            return 0.0
    a, b = rows[0], rows[-1]
    d_nw = num(b, "总净资产") - num(a, "总净资产")
    d_fin = num(b, "金融资产") - num(a, "金融资产")
    d_prop = num(b, "房产") - num(a, "房产")
    m0, m1 = a["date"][:7], b["date"][:7]
    savings, draft = 0.0, False
    for r in cashflow_rows:
        m = (r.get("月份") or "").strip()
        if not m or not (m0 <= m <= m1):
            continue
        try:
            savings += float(r.get("净结余") or 0)
        except ValueError:
            continue
        if (r.get("已对账") or "").strip() != "是":
            draft = True
    return {"from": a["date"], "to": b["date"], "deltaNW": round(d_nw),
            "savings": round(savings), "invest": round(d_fin - savings),
            "property": round(d_prop), "draft": draft}


# ── 财务自由推演 ───────────────────────────────────────────────────

def fi_plan(financial, fixed_out, monthly_saving, cfg=None):
    """FI 线 = 年固定支出/提取率；三情景(实际回报,已扣通胀)到达年限。
    月储蓄取当前净结余(≤0 按 0，只靠存量复利)。"""
    cfg = cfg or {}
    swr = cfg.get("提取率", 0.035)
    scenarios = cfg.get("实际回报情景", [0.02, 0.04, 0.06])
    annual_out = fixed_out * 12
    if annual_out <= 0 or swr <= 0:
        return None
    number = annual_out / swr
    s = max(0.0, monthly_saving)
    years = []
    for r in scenarios:
        if financial >= number:
            years.append({"r": r, "years": 0.0})
            continue
        rm = (1 + r) ** (1 / 12.0) - 1
        if rm <= 0:
            y = (number - financial) / s / 12 if s > 0 else None
        elif s > 0:
            y = math.log((number * rm + s) / (financial * rm + s)) / math.log(1 + rm) / 12
        elif financial > 0:
            y = math.log(number / financial) / math.log(1 + rm) / 12
        else:
            y = None
        years.append({"r": r, "years": round(y, 1) if y is not None else None})
    return {"number": round(number), "swr": swr,
            "progress": financial / number, "annualOut": round(annual_out),
            "monthlySaving": round(s), "scenarios": years}


# ── 再平衡执行单 ───────────────────────────────────────────────────

# ── IPS 操作合规审计 ──────────────────────────────────────────────

def ips_check(ledger_rows, history_rows, target, band=0.05, big_trade_pct=0.05):
    """每笔台账操作(买入/卖出)对照投资纪律:
    R1 交易必须写原因(空=违纪);
    R2 方向纪律:卖出低配大类/买入超配大类(按交易日 history 权重 vs 目标;
       偏离超容忍带 band=违纪,带内=提示);
    R3 单笔金额 > 净资产×big_trade_pct → 提示(大额需冷静期/复核)。
    返回按日期倒序的 [{date,name,action,rule,level,msg}]。"""
    hist = sorted((r for r in history_rows if r.get("date")), key=lambda r: r["date"])

    def num(r, k):
        try:
            return float(r.get(k) or 0)
        except ValueError:
            return 0.0

    def row_at(d):
        prev = None
        for h in hist:
            if h["date"] <= d:
                prev = h
            else:
                break
        return prev or (hist[0] if hist else None)

    out = []
    for r in ledger_rows:
        act = r.get("动作")
        if act not in ("买入", "卖出"):
            continue
        d, name = r.get("日期") or "", r.get("名称") or ""
        cls = "黄金" if (r.get("资产类型") or "").startswith("黄金") else "权益"
        if not (r.get("原因/备注") or "").strip():
            out.append({"date": d, "name": name, "action": act, "rule": "R1",
                        "level": "违纪", "msg": "无交易原因——每笔操作必须写下为什么"})
        h = row_at(d)
        nw = num(h, "总净资产") if h else 0
        if h and nw > 0:
            w, tgt = num(h, cls) / nw, target.get(cls, 0)
            if act == "卖出" and w <= tgt:
                lvl = "违纪" if w < tgt - band else "提示"
                out.append({"date": d, "name": name, "action": act, "rule": "R2",
                            "level": lvl,
                            "msg": f"卖出低配类({cls} {w*100:.1f}% < 目标 {tgt*100:.0f}%)——与再平衡方向相反"})
            if act == "买入" and w >= tgt:
                lvl = "违纪" if w > tgt + band else "提示"
                out.append({"date": d, "name": name, "action": act, "rule": "R2",
                            "level": lvl,
                            "msg": f"买入超配类({cls} {w*100:.1f}% ≥ 目标 {tgt*100:.0f}%)——应定向到低配类"})
            amt = num(r, "成交额")
            if amt > nw * big_trade_pct:
                out.append({"date": d, "name": name, "action": act, "rule": "R3",
                            "level": "提示",
                            "msg": f"单笔 ¥{amt:,.0f} 超净资产 {big_trade_pct*100:.0f}%——大额操作建议冷静期后复核"})
    out.sort(key=lambda x: x["date"], reverse=True)
    return out


# ── SBBI 历史回放 ──────────────────────────────────────────────────

def sbbi_replay(classes, data):
    """当前金融配置(权益/债/现金/黄金 归一化权重) × SBBI 逐年收益 → 组合穿越 2005–2025。
    data = sbbi_returns.json 内容(_map 定义大类→年报序列的映射)。
    返回 perYear/名义与实际年化/最大回撤(年度路径)/负收益年数/最长水下/最差最好年份。"""
    mp = (data or {}).get("_map", {})
    years = (data or {}).get("years", {})
    w = {c: classes.get(c, 0.0) for c in mp}
    tot = sum(w.values())
    if tot <= 0 or not years:
        return None
    w = {c: v / tot for c, v in w.items()}
    per = []
    for y in sorted(years):
        ys = years[y]
        try:
            r = sum(w[c] * ys[mp[c]] for c in w)
        except (KeyError, TypeError):
            continue                      # 该年某序列缺数 → 跳过
        per.append({"year": int(y), "r": round(r, 4), "cpi": ys.get("通胀", 0.0)})
    if not per:
        return None
    nom, real, peak, maxdd = 1.0, 1.0, 1.0, 0.0
    under, longest = 0, 0
    for p in per:
        nom *= 1 + p["r"]
        real *= (1 + p["r"]) / (1 + (p["cpi"] or 0.0))
        if nom >= peak - 1e-12:
            peak, under = nom, 0
        else:
            under += 1
            longest = max(longest, under)
        maxdd = min(maxdd, nom / peak - 1)
    n = len(per)
    return {"weights": {k: round(v, 4) for k, v in w.items()},
            "perYear": per,
            "cagr": nom ** (1.0 / n) - 1, "realCagr": real ** (1.0 / n) - 1,
            "maxDD": maxdd, "negYears": sum(1 for p in per if p["r"] < 0),
            "longestUnder": longest,
            "worst": sorted(per, key=lambda x: x["r"])[:3],
            "best": sorted(per, key=lambda x: -x["r"])[:3]}


# ── 压力测试 ───────────────────────────────────────────────────────

def stress_test(classes, prop_gross, total_debt, gross_assets, ccy, networth):
    """标准情景冲击：ΔNW、冲击后净资产、冲击后杠杆率(负债/冲击后总资产)。
    房产按估值(prop_gross)打折——负债不变,这正是杠杆的放大效应。"""
    if not networth or not gross_assets:
        return None
    eq = classes.get("权益", 0.0)
    usd = ccy.get("USD", 0.0)
    scen = [
        ("权益 −30%", -0.30 * eq),
        ("房产估值 −20%", -0.20 * prop_gross),
        ("美元资产 −10%", -0.10 * usd),
        ("危机组合(权益−30%+房产−20%)", -0.30 * eq - 0.20 * prop_gross),
    ]
    out = []
    for name, d in scen:
        ga = gross_assets + d
        out.append({"name": name, "dNW": round(d), "nwAfter": round(networth + d),
                    "levAfter": total_debt / ga if ga > 0 else None,
                    "ddPct": d / networth})
    return out


# ── 保险缺口 ───────────────────────────────────────────────────────

def insurance_gap(income_items, ins_by_member, total_debt, fixed_out, cfg=None):
    """需求分析 vs 现有保额：
    寿险(家庭口径) 需求 = 负债余额 + 家庭固定支出×N年(默认10)；现有 = 各成员 寿险/定期寿/终身寿 保额合计。
    重疾(按成员)   需求 = 该成员月收入×12×倍数(默认3)；现有 = 该成员重疾保额。"""
    cfg = cfg or {}
    years = cfg.get("寿险支出年数", 10)
    ci_mult = cfg.get("重疾收入倍数", 3)
    life_need = total_debt + fixed_out * 12 * years
    LIFE_KEYS = ("寿险", "定期寿", "终身寿", "定期寿险")
    life_have = sum(v for g in (ins_by_member or {}).values()
                    for k, v in (g.get("保额") or {}).items() if k in LIFE_KEYS)
    inc_by_member = defaultdict(float)
    for it in income_items or []:
        inc_by_member[it.get("成员") or "未分组"] += it.get("金额") or 0
    ci = []
    for m, monthly in inc_by_member.items():
        if monthly <= 0:
            continue
        need = monthly * 12 * ci_mult
        have = ((ins_by_member or {}).get(m, {}).get("保额") or {}).get("重疾", 0.0)
        ci.append({"member": m, "need": round(need), "have": round(have),
                   "gap": round(max(0.0, need - have))})
    return {"life": {"need": round(life_need), "have": round(life_have),
                     "gap": round(max(0.0, life_need - life_have))},
            "ci": ci, "years": years, "ciMult": ci_mult}


REB_ADJUSTABLE = ("权益", "债券类固收", "黄金")   # 定投可定向的类；现金是弹药、房产不可调

def rebalance_plan(classes, target, networth, dca_month):
    """5/25 规则判定 + 增量定投定向分配。
    返回 {rows:[{cls,curPct,tgtPct,devPp,gap,act,adjustable}], plan:{months,alloc,...}}"""
    if not networth:
        return None
    rows, need = [], {}
    for cls, tgt in target.items():
        cur = classes.get(cls, 0.0)
        cur_pct, tgt_pct = cur / networth, tgt
        dev = cur_pct - tgt_pct
        act = abs(dev) >= 0.05 or (tgt_pct > 0 and abs(dev) / tgt_pct >= 0.25)
        adjustable = cls in REB_ADJUSTABLE
        gap = tgt * networth - cur                    # + = 低配需买入
        rows.append({"cls": cls, "curPct": cur_pct, "tgtPct": tgt_pct,
                     "devPp": dev, "gap": round(gap), "act": act,
                     "adjustable": adjustable})
        if adjustable and gap > 0:
            need[cls] = gap
    total_need = sum(need.values())
    plan = None
    if total_need > 0 and dca_month > 0:
        alloc = {cls: round(dca_month * g / total_need) for cls, g in need.items()}
        plan = {"months": round(total_need / dca_month, 1),
                "monthly": round(dca_month),
                "alloc": alloc, "totalNeed": round(total_need)}
    over_sell = [{"cls": r["cls"], "excess": -r["gap"]}
                 for r in rows if r["adjustable"] and r["act"] and r["gap"] < 0]
    return {"rows": rows, "plan": plan, "overweightSells": over_sell}
