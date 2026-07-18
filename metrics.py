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
import re
from collections import defaultdict

# ── 期权敞口(2026-07-19 定案:期权=正股头寸的修饰,不是独立资产) ────────────
# 代码约定:{正股代码}{到期YYMMDD}{C/P}{行权价},如 RKLB261016P70 / HIMS261218C50。
OPTION_RE = re.compile(r"^([A-Z]+)(\d{6})([CP])(\d+(?:\.\d+)?)$")


def parse_option(code):
    """期权代码 → {und, expiry, cp, strike};不匹配返回 None。"""
    m = OPTION_RE.match((code or "").strip())
    if not m:
        return None
    return {"und": m.group(1), "expiry": m.group(2),
            "cp": m.group(3), "strike": float(m.group(4))}


def option_exposures(holdings_rows, fx_usd):
    """空头期权 → 正股敞口修正(名义口径,不做delta——半自动项目要零维护的诚实指标)。
    卖Put:接货名义(张数×100×行权价×汇率)计入正股集中度;
    卖Call:不加敞口,但正股可覆盖张数不足时=裸空,上涨亏损无上限 → 红色警告。
    返回 ({正股代码: 接货名义CNY}, [警告文案])。"""
    put_notional, warns = {}, []
    for r in holdings_rows:
        if (r.get("资产类型") or "") != "美股期权":
            continue
        opt = parse_option(r.get("代码"))
        try:
            qty = float(str(r.get("持有数量") or 0).replace(",", ""))
        except ValueError:
            continue
        if not opt or qty >= 0:      # 只处理空头(负张数);买入的期权风险=权利金,市值口径已覆盖
            continue
        contracts = -qty
        if opt["cp"] == "P":
            put_notional[opt["und"]] = put_notional.get(opt["und"], 0.0) \
                + contracts * 100 * opt["strike"] * fx_usd
        else:
            shares = sum(float(str(x.get("持有数量") or 0).replace(",", ""))
                         for x in holdings_rows
                         if (x.get("代码") or "") == opt["und"]
                         and (x.get("资产类型") or "") != "美股期权")
            covered = shares / 100.0
            if contracts > covered + 1e-9:
                warns.append(f"备兑Call缺口 {opt['und']}:卖出{contracts:g}张 > 正股{shares:g}股"
                             f"仅覆盖{covered:g}张——裸空{contracts-covered:g}张,上涨亏损无上限")
    return put_notional, warns


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


def attribution_monthly(history_rows, cashflow_rows, months=8):
    """按月切片的增长归因(口径同 attribution,逐月闭合)。
    每月最后一行为月末快照,ΔNW 相对上月末;储蓄取当月净结余(草稿月标记)。
    首个有快照的月份没有上月末基线,不出结果。"""
    rows = [r for r in history_rows if r.get("date")]
    if len(rows) < 2:
        return None

    def num(r, k):
        try:
            return float(r.get(k) or 0)
        except ValueError:
            return 0.0
    ends = {}
    for r in rows:                       # 升序追加,后写覆盖 → 每月留月末行
        ends[r["date"][:7]] = r
    keys = sorted(ends)
    sav, draft = {}, {}
    for r in cashflow_rows:
        m = (r.get("月份") or "").strip()
        if not m:
            continue
        try:
            sav[m] = sav.get(m, 0.0) + float(r.get("净结余") or 0)
        except ValueError:
            continue
        if (r.get("已对账") or "").strip() != "是":
            draft[m] = True
    out = []
    for prev, cur in zip(keys, keys[1:]):
        a, b = ends[prev], ends[cur]
        d_nw = round(num(b, "总净资产") - num(a, "总净资产"))
        s = round(sav.get(cur, 0.0))
        d_prop = round(num(b, "房产") - num(a, "房产"))
        # 投资项取残差:取整后三块仍精确闭合(ΔNW=Δ金融+Δ房产 按 history 构造成立)
        out.append({"month": cur, "deltaNW": d_nw, "savings": s,
                    "invest": d_nw - s - d_prop, "property": d_prop,
                    "draft": bool(draft.get(cur))})
    return out[-months:] or None


# ── 财务自由推演 ───────────────────────────────────────────────────

def _years_to(target, start, monthly_saving, r):
    """在实际回报 r 下，从 start 每月存 monthly_saving 攒到 target 需要几年。"""
    if start >= target:
        return 0.0
    s = max(0.0, monthly_saving)
    rm = (1 + r) ** (1 / 12.0) - 1
    if rm <= 0:
        return (target - start) / s / 12 if s > 0 else None
    if s > 0:
        return math.log((target * rm + s) / (start * rm + s)) / math.log(1 + rm) / 12
    if start > 0:
        return math.log(target / start) / math.log(1 + rm) / 12
    return None


CONFIDENCE = ("合同", "计划", "猜测")   # 确信度:合同=写死 / 计划=自己定的 / 猜测=拍脑袋


def _months_between(a, b):
    return (b.year - a.year) * 12 + b.month - a.month


def event_ladder(events, base_saving, today=None, side="mid"):
    """重大事件 → 月储蓄阶梯 + 一次性资金注入。
    每个事件声明它对月度现金流的影响(正=每月多存下来)与一次性金额；系统只做加法，
    不理解因果(换房与房贷结清的日期一致性由用户保证——不一致会在阶梯曲线上显形)。
    side: 'lo'|'mid'|'hi' 取金额区间的哪一端(区间来自「猜测」类事件)。
    返回 [(月偏移, 月储蓄, 一次性注入, 事件名)]，按时间排序，含起点 (0, base, 0, None)。"""
    today = today or datetime.date.today()
    steps = []
    for e in events or []:
        try:
            d = datetime.date.fromisoformat(str(e.get("日期"))[:10] if len(str(e.get("日期"))) > 7
                                            else str(e.get("日期")) + "-01")
        except ValueError:
            continue
        m = _months_between(today, d)
        if m < 0:
            continue                        # 已发生的事件不再影响未来路径
        rng = e.get("月度影响区间")          # {"lo":.., "hi":..} 优先于「月度影响合计」
        if isinstance(rng, dict):
            dm = rng.get({"lo": "lo", "mid": "mid", "hi": "hi"}[side],
                         rng.get("mid", (rng.get("lo", 0) + rng.get("hi", 0)) / 2))
        else:
            dm = sum((e.get("月度影响") or {}).values())
        steps.append({"m": m, "dSave": float(dm or 0),
                      "lump": float(e.get("一次性") or 0),
                      "name": e.get("名称", ""), "conf": e.get("确信度", "计划")})
    steps.sort(key=lambda x: x["m"])
    ladder, save = [(0, base_saving, 0.0, None)], base_saving
    for s in steps:
        save += s["dSave"]
        ladder.append((s["m"], save, s["lump"], s["name"]))
    return ladder


def _grow(start, target, ladder, r, max_months=720):
    """沿事件阶梯逐月复利，返回到达 target 的月数(未达返回 None)。"""
    rm = (1 + r) ** (1 / 12.0) - 1
    bal, idx, save = start, 0, ladder[0][1]
    for m in range(max_months):
        while idx < len(ladder) and ladder[idx][0] == m:
            save = ladder[idx][1]
            bal += ladder[idx][2]          # 一次性注入
            idx += 1
        if bal >= target:
            return m / 12.0
        bal = bal * (1 + rm) + save
    return None


def childcare_reserve(events, r=0.04, today=None, side="mid"):
    """育儿储备:FI 线只算「孩子成年后的终身支出」，但孩子成年前的育儿开销仍要花钱——
    它不是消失了，是一笔**有限期的负债**。返回其现值(按实际回报折现)。
    读 events 里 类型=='育儿' 的项:{月额区间:{lo,hi}, 结束: 'YYYY-MM'}"""
    today = today or datetime.date.today()
    total = 0.0
    for e in events or []:
        if e.get("类型") != "育儿":
            continue
        try:
            end = datetime.date.fromisoformat(str(e["结束"]) + "-01"
                                              if len(str(e["结束"])) == 7 else str(e["结束"]))
        except (ValueError, KeyError):
            continue
        n = max(0, _months_between(today, end))
        rng = e.get("月额区间") or {}
        amt = rng.get(side, rng.get("mid", e.get("月额", 0)))
        rm = (1 + r) ** (1 / 12.0) - 1
        pv = amt * (1 - (1 + rm) ** -n) / rm if rm > 0 and n else amt * n
        total += pv
    return total


def lifelong_out(flow_items, subs_monthly=0.0, ins_monthly=0.0):
    """终身支出(月) = 月度收支里「终身」!=False 的项 + 订阅 + 保险摊月。
    有终点的支出(房贷/幼儿园/车贷)不抬高 FI 终点，但仍在路径上拖慢攒钱速度。
    返回 (终身月支出, [有终点的项 {项目,金额}])。"""
    lifelong, ending = 0.0, []
    for it in flow_items or []:
        amt = abs(it.get("金额") or 0)
        if it.get("终身") is False:
            ending.append({"item": it.get("项目", ""), "amt": round(amt)})
        else:
            lifelong += amt
    return lifelong + (subs_monthly or 0) + (ins_monthly or 0), ending


def fi_plan(financial, fixed_out, monthly_saving, cfg=None,
            lifelong_month=None, ending_items=None, events=None, today=None):
    """财务自由需要**三个**数字，不是一个(见 docs/2029-plan.md 2.6)：

      coastNumber  Coast FI 线 = 终身月支出×12 / 提取率
                   —— 「可以不再为退休存钱了」。但孩子没成年，还得工作供他。
      reserve      育儿储备 = 孩子成年前育儿开销的现值
                   —— FI 线把育儿剔除了(它会结束)，但**在结束前仍要花钱**：它是一笔有限期负债。
      freeNumber   真·自由线 = coastNumber + reserve
                   —— 「可以不上班了」。这才是那个能对应真实决策的数字。

    路径推演沿 event_ladder 走**阶梯**(月储蓄会随事件变化，一次性注入直接进本金)，
    而不是假设「月储蓄恒定 30 年」。带区间的「猜测」类事件 → 输出 lo/hi 两端。
    """
    cfg = cfg or {}
    swr = cfg.get("提取率", 0.035)
    scenarios = cfg.get("实际回报情景", [0.02, 0.04, 0.06])
    base_month = lifelong_month if lifelong_month is not None else fixed_out
    annual_out = base_month * 12
    if annual_out <= 0 or swr <= 0:
        return None
    coast = annual_out / swr
    s = max(0.0, monthly_saving)

    def pack(target, side):
        lad = event_ladder(events, s, today, side)
        return [{"r": r, "years": (lambda y: round(y, 1) if y is not None else None)(
            _grow(financial, target, lad, r))} for r in scenarios]

    res_mid = childcare_reserve(events, 0.04, today, "mid")
    res_lo = childcare_reserve(events, 0.04, today, "lo")
    res_hi = childcare_reserve(events, 0.04, today, "hi")
    free_mid = coast + res_mid

    ladder = event_ladder(events, s, today, "mid")
    out = {
        "swr": swr, "lifelongMonth": round(base_month), "annualOut": round(annual_out),
        "monthlySaving": round(s),
        "coastNumber": round(coast), "coastProgress": financial / coast,
        "reserve": round(res_mid), "reserveLo": round(res_lo), "reserveHi": round(res_hi),
        "freeNumber": round(free_mid), "freeProgress": financial / free_mid if free_mid else 0,
        "freeLo": round(coast + res_lo), "freeHi": round(coast + res_hi),
        "coastScenarios": pack(coast, "mid"),
        "freeScenarios": pack(free_mid, "mid"),
        "freeScenariosLo": pack(coast + res_lo, "lo"),   # 育儿花得少 → 目标低且月储蓄高
        "freeScenariosHi": pack(coast + res_hi, "hi"),
        "endingItems": ending_items or [],
        "endingMonth": round(sum(x["amt"] for x in (ending_items or []))),
        "ladder": [{"m": m, "save": round(sv), "lump": round(lp), "name": nm}
                   for m, sv, lp, nm in ladder],
        # 兼容旧字段(面板过渡期):number/progress 指向真·自由线
        "number": round(free_mid), "progress": financial / free_mid if free_mid else 0,
        "scenarios": pack(free_mid, "mid"),
    }
    return out


# ── 再平衡执行单 ───────────────────────────────────────────────────

# ── IPS 操作合规审计 ──────────────────────────────────────────────

def ips_check(ledger_rows, history_rows, target, band=0.05, big_trade_pct=0.05, fx=None):
    """每笔台账操作(买入/卖出)对照投资纪律:
    R1 交易必须写原因(空=违纪);
    R2 方向纪律:卖出低配大类/买入超配大类(按交易日 history 权重 vs 目标;
       偏离超容忍带 band=违纪,带内=提示)。期权按敞口方向:卖Put=买入方向,
       其余期权操作豁免R2(收权利金≠减权益敞口);
    R3 单笔金额 > 净资产×big_trade_pct → 提示(大额需冷静期/复核)。
       金额按成交币种×fx折CNY;期权按接货/交割名义(张数×100×行权价),风险在名义不在权利金。
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
        opt = parse_option(r.get("代码")) if (r.get("资产类型") or "") == "美股期权" else None
        rate = (fx or {}).get((r.get("成交币种") or "").strip() or "CNY", 1.0)
        h = row_at(d)
        nw = num(h, "总净资产") if h else 0
        if h and nw > 0:
            w, tgt = num(h, cls) / nw, target.get(cls, 0)
            # 期权按敞口方向:卖Put=承接买入义务→按买入审;其余期权操作豁免R2
            eff_act, opt_note = act, ""
            if opt:
                if act == "卖出" and opt["cp"] == "P":
                    eff_act, opt_note = "买入", "(卖Put=买入方向敞口)"
                else:
                    eff_act = None
            if eff_act == "卖出" and w <= tgt:
                lvl = "违纪" if w < tgt - band else "提示"
                out.append({"date": d, "name": name, "action": act, "rule": "R2",
                            "level": lvl,
                            "msg": f"卖出低配类({cls} {w*100:.1f}% < 目标 {tgt*100:.0f}%)——与再平衡方向相反"})
            if eff_act == "买入" and w >= tgt:
                lvl = "违纪" if w > tgt + band else "提示"
                out.append({"date": d, "name": name, "action": act, "rule": "R2",
                            "level": lvl,
                            "msg": f"买入超配类({cls} {w*100:.1f}% ≥ 目标 {tgt*100:.0f}%)——应定向到低配类{opt_note}"})
            if opt:
                amt = abs(num(r, "数量")) * 100 * opt["strike"] * (fx or {}).get("USD", 1.0)
                amt_label = "名义"
            else:
                amt = num(r, "成交额") * rate
                amt_label = ""
            if amt > nw * big_trade_pct:
                out.append({"date": d, "name": name, "action": act, "rule": "R3",
                            "level": "提示",
                            "msg": f"单笔{amt_label} ¥{amt:,.0f} 超净资产 {big_trade_pct*100:.0f}%——大额操作建议冷静期后复核"})
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


# ── 换房路线图(2029 目标态的核心动作) ─────────────────────────────

def relocation_plan(goal, classes, networth, prop_gross, total_debt, liquid, today=None):
    """卖掉现有投资房 → 买一套学位房 → 净释放的钱进入金融资产。
    预算上限由目标态「房产 ≤X%」反推(取占比上限与封顶的较小值)。
    过桥资金 = 卖房款到账前必须先付出去的钱 —— 2.5 年内要用，不该在权益里。
    返回 None 表示未启用。"""
    g = (goal or {}).get("换房") or {}
    if not g.get("启用") or not networth:
        return None
    today = today or datetime.date.today()

    def d(key):
        try:
            return datetime.date.fromisoformat(str(g.get(key)))
        except (ValueError, TypeError):
            return None
    target_d, start_by = d("目标日"), d("启动截止")
    months_left = ((target_d.year - today.year) * 12 + target_d.month - today.month) if target_d else None
    start_months = ((start_by.year - today.year) * 12 + start_by.month - today.month) if start_by else None

    sell = prop_gross                                   # 卖出价 = 房产锚定值
    cost = sell * (g.get("交易成本率") or 0)             # 中介/税费
    payoff = total_debt                                 # 结清房贷(负债台账口径)
    # 预算上限:换房后房产不超过目标态占比 → 以「换房后净资产」为基数反推
    cap_pct = g.get("买入预算上限占比") or 0.20
    nw_after_costs = networth - cost
    by_pct = nw_after_costs * cap_pct / (1 - 0) if cap_pct else 0
    budget = min(by_pct, g.get("买入预算封顶") or by_pct)
    released = sell - cost - payoff - budget            # 净释放进金融资产
    bridge_need = g.get("过桥需自筹") or 0
    bridge_gap = max(0.0, bridge_need - liquid)         # 可变现资产盖不住的部分

    prop_after = budget
    nw_after = networth - cost                          # 卖房本身不改变净资产(除税费)
    return {
        "targetDate": g.get("目标日"), "why": g.get("说明", ""),
        "startBy": g.get("启动截止"), "monthsLeft": months_left,
        "startMonthsLeft": start_months,
        "sell": round(sell), "cost": round(cost), "payoff": round(payoff),
        "budget": round(budget), "budgetPct": cap_pct,
        "capped": bool(g.get("买入预算封顶") and g["买入预算封顶"] < by_pct),
        "released": round(released),
        "bridgeNeed": round(bridge_need), "bridgeGap": round(bridge_gap),
        "propPctNow": prop_gross / networth if networth else 0,
        "propPctAfter": prop_after / nw_after if nw_after else 0,
        "finAfter": round(classes.get("权益", 0) + classes.get("债券类固收", 0)
                          + classes.get("现金", 0) + classes.get("黄金", 0) + released),
    }


def pnl_series_from(history_full_rows, ledger_rows, fx):
    """逐日累计浮盈 [(date, pnl)]：每日持仓市值(history_full 的「持仓」行) − 截至当日的净投入(台账)。
    **只覆盖可定价持仓**；长钱/海外长钱这类手动账户的收益无法从余额里分离
    (更新余额时「市场涨了」和「我又转进去了」混在一起)——要精确倒推储蓄，
    需要额外记录「向投顾账户的转入」。见 docs/2029-plan.md 2.2 的限制说明。"""
    by_day = defaultdict(float)
    for r in history_full_rows or []:
        if r.get("类型") != "持仓":
            continue
        try:
            by_day[r["date"][:10]] += float(r.get("金额") or 0)
        except (ValueError, KeyError):
            continue
    flows = []      # (date, 净投入增量)
    for r in ledger_rows or []:
        try:
            v = float((r.get("成交额") or "").strip())
        except ValueError:
            continue
        d = (r.get("日期") or "").strip()
        if not d:
            continue
        rate = (fx or {}).get((r.get("成交币种") or "").strip() or "CNY", 1.0)
        sign = -1.0 if r.get("动作") == "卖出" else 1.0
        flows.append((d[:10], sign * v * rate))
    out = []
    for d in sorted(by_day):
        cost = sum(v for fd, v in flows if fd <= d)
        out.append((d, by_day[d] - cost))
    return out


def true_savings(history_rows, cashflow_rows, pnl_series=None, min_days=60):
    """真实储蓄(总量层) = Δ金融资产 − **期间内**投资收益。
    储蓄作为残差 —— 它就是「账户里多出来的、不能用市场解释的钱」，自动吸收掉所有
    未记录的生活开支(见 docs/2029-plan.md 2.2)。与计划口径(净结余)对比，差额=没记的开销。

    pnl_series: [(date, 累计浮盈)] —— 期间投资收益 = 末点累计 − 首点累计。
      **不能用「台账基线以来的累计浮盈」直接当期间收益**：时间窗对不上，残差会是垃圾。
    数据不足(跨度 < min_days 或缺 pnl 序列)时返回 {"insufficient": ...}，宁可不给数也不给错数。"""
    rows = [r for r in history_rows if r.get("date")]
    if len(rows) < 2:
        return None
    d0, d1 = rows[0]["date"][:10], rows[-1]["date"][:10]
    days = (datetime.date.fromisoformat(d1) - datetime.date.fromisoformat(d0)).days

    def pnl_at(d):
        best = None
        for pd_, v in (pnl_series or []):
            if pd_[:10] <= d:
                best = v
        return best
    p0, p1 = pnl_at(d0), pnl_at(d1)
    if days < min_days or p0 is None or p1 is None:
        return {"insufficient": True, "days": days, "needDays": min_days,
                "why": ("需要至少两个月的净值历史 + 逐日浮盈序列才能把「市场涨跌」和「你存进去的钱」"
                        "分开；在此之前储蓄率只能用计划口径(会高估)")}

    def num(r, k):
        try:
            return float(r.get(k) or 0)
        except ValueError:
            return 0.0
    d_fin = num(rows[-1], "金融资产") - num(rows[0], "金融资产")
    real = d_fin - (p1 - p0)
    m0, m1 = d0[:7], d1[:7]
    planned = 0.0
    for r in cashflow_rows or []:
        m = (r.get("月份") or "").strip()
        if m and m0 <= m <= m1:
            try:
                planned += float(r.get("净结余") or 0)
            except ValueError:
                pass
    months = max(1.0, days / 30.44)
    return {"from": d0, "to": d1, "days": days, "months": round(months, 1),
            "real": round(real), "planned": round(planned),
            "investPnl": round(p1 - p0),
            "unrecorded": round(planned - real),        # 计划 − 真实 = 没记的生活开支
            "realMonthly": round(real / months)}


# ── 压力测试 ───────────────────────────────────────────────────────

def stress_test(classes, prop_gross, total_debt, gross_assets, ccy, networth, reloc=None):
    """标准情景冲击：ΔNW、冲击后净资产、冲击后杠杆率(负债/冲击后总资产)。
    房产按估值(prop_gross)打折——负债不变,这正是杠杆的放大效应。
    reloc: 有换房计划时，房价下跌的真实伤害是「净释放的钱变少」——这才是未来数年的头号风险。"""
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
    if reloc:                      # 换房专属:房价跌 → 净释放缩水(学位房预算也同步走低,部分对冲)
        for drop in (0.10, 0.20, 0.30):
            sell = reloc["sell"] * (1 - drop)
            rel = sell - sell * 0 - reloc["cost"] * (1 - drop) - reloc["payoff"] - reloc["budget"]
            out.append({"name": f"换房时房价 −{int(drop*100)}%", "reloc": True,
                        "released": round(rel),
                        "dReleased": round(rel - reloc["released"]),
                        "ok": rel > 0})
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
