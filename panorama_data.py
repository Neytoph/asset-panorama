# -*- coding: utf-8 -*-
"""
全景数据层：collect() 计算所有维度，返回 JS 友好的字典。
被 panorama_themes.py（多套视觉方案）复用，避免重复计算。
"""
import datetime
import json
import time
from collections import defaultdict
from pathlib import Path

import metrics
import storage
from portfolio_tracker import (compute, check_alerts, persist, TARGET_NETWORTH,
                               SINGLE_STOCK_TYPES, load_json, read_csv, http_get)
import subscriptions as subs
import cashflow_income as inc_mod
import cashflow_history as cfh
import insurance as ins
import loans as loans_mod

BASE = Path(__file__).resolve().parent
FINANCIAL = {"权益", "债券类固收", "现金", "黄金"}
KLINE_FETCH_GAP = 0.7    # 每天照常刷新K线，但控制对东财的请求频率：两次请求至少间隔这么多秒，避免被限流


def _kline(secid, lmt=120):
    """东财日K线 → [[日期, 收盘价], ...]。失败返回空。"""
    url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=" + secid +
           "&klt=101&fqt=1&lmt=" + str(lmt) + "&end=20500101&fields1=f1&fields2=f51,f53")
    try:
        txt = http_get(url, headers={"Referer": "https://quote.eastmoney.com/",
                                     "User-Agent": "Mozilla/5.0"})
        ks = ((json.loads(txt).get("data") or {}).get("klines")) or []
        out = []
        for k in ks:
            p = k.split(",")
            if len(p) >= 2:
                out.append([p[0], float(p[1])])
        return out
    except Exception:
        return []


def _kline_tx(code, lmt=120):
    """腾讯日K线 → [[日期, 收盘价], ...]。失败返回空。"""
    if not code:
        return []
    url = ("https://web.ifzq.gtimg.cn/appstuff/hq/kline/get?param=" +
           code + ",day,,," + str(lmt) + ",qfq")
    try:
        txt = http_get(url, headers={"User-Agent": "Mozilla/5.0",
                                     "Referer": "https://gu.qq.com/"})
        data = json.loads(txt)
        node = (data.get("data") or {}).get(code) or {}
        rows = node.get("qfqday") or node.get("day") or []
        return [[row[0], float(row[2])] for row in rows if len(row) >= 3]
    except Exception:
        return []


def platform_summary(holdings, accounts):
    """账户/平台维度汇总（金融资产口径，房产/负债除外）→ [(平台, 金额), ...] 降序。
    持仓按「账户」列分组（未标注归'未标注账户'）；accounts 行本身即平台。"""
    agg = defaultdict(float)
    for h in holdings:
        agg[h.get("账户") or "未标注账户"] += h["value"]
    for a in accounts:
        if a["class"] in ("房产", "负债"):
            continue
        agg[a["name"]] += a["value"]
    return sorted(agg.items(), key=lambda x: -x[1])


def cost_basis(history_rows, fx):
    """持仓成本（净投入口径）→ {名称: 成本CNY}。
    按名称聚合 成交额×汇率：期初/买入 记正，卖出 记负（净投入随落袋减少）。
    成交额为空或非数的行跳过；备兑期权权利金录负数成交额，天然算作降成本。"""
    cost = defaultdict(float)
    for r in history_rows:
        try:
            v = float((r.get("成交额") or "").strip())
        except ValueError:
            continue
        rate = fx.get((r.get("成交币种") or "").strip() or "CNY", 1.0)
        sign = -1.0 if r.get("动作") == "卖出" else 1.0
        cost[r.get("名称", "")] += sign * v * rate
    return dict(cost)


def ledger_qty_check(holdings, history_rows, eps=1e-6):
    """台账推演数量(期初/买入记正、卖出记负) vs 实际持仓数量 → 不一致警告列表。
    防绕过持仓管理页改数：缺笔/多笔都会让浮盈成本口径悄悄跑偏，这里兜底揭发。"""
    led = defaultdict(float)
    for r in history_rows:
        try:
            q = float((r.get("数量") or "").replace(",", "").strip())
        except ValueError:
            continue
        led[r.get("名称", "")] += -q if r.get("动作") == "卖出" else q
    cur = {h["name"]: h["qty"] for h in holdings if h.get("qty") is not None}
    return [f'📒 {n} 台账推演数量 {led.get(n, 0):g} ≠ 实际 {cur.get(n, 0):g}'
            f'（差 {cur.get(n, 0) - led.get(n, 0):+g}）——有变动没记 holdings_history，浮盈成本口径已不可信'
            for n in sorted(set(led) | set(cur))
            if abs(led.get(n, 0.0) - cur.get(n, 0.0)) > eps]


def collect(persist_history=True, fetch_klines=True):
    """fetch_klines=False:K线只用本地缓存、绝不现场拉(轻量重渲染用——
    失败过的标的会带节流退避重试,一次能耗掉半分钟)。"""
    R = compute()
    if persist_history:
        persist(R)
    classes, nw, fin = R["classes"], R["networth"], R["financial"]
    holdings, accounts = R["holdings"], R["accounts"]
    pt = load_json("passthrough.json")
    cf = load_json("cashflow.json")
    history = read_csv("history.csv", [])

    region_all, region_eq, ccy, liq = (defaultdict(float) for _ in range(4))
    tree = defaultdict(lambda: defaultdict(list))   # 大类 -> 子类 -> [持仓]
    SUB_BY_TYPE = {"A股权益ETF": "A股宽基", "A股个股": "A股个股", "港股个股": "港股",
                   "美股个股": "美股", "美股ETF": "美股", "美股期权": "美股",
                   "美股杠杆ETF": "美股", "黄金ETF": "黄金"}
    for h in holdings:
        v = h["value"]
        if h["type"] == "黄金ETF":
            ra, ceq, cc = "黄金/商品", None, "黄金"
        elif h["market"] == "港股":
            ra = ceq = "香港"; cc = "HKD"
        elif h["market"] == "美股":
            ra = ceq = "美国"; cc = "USD"
        else:
            ra = ceq = "中国大陆"; cc = "CNY"
        region_all[ra] += v
        if h["class"] == "权益" and ceq:
            region_eq[ceq] += v
        ccy[cc] += v
        liq[h["流动性"]] += v
        tree[h["class"]][SUB_BY_TYPE.get(h["type"], h["class"])].append(
            {"name": h["name"], "value": round(v)})

    for a in accounts:
        v, name = a["value"], a["name"]
        ptv = pt.get(name)
        liq[a["流动性"]] += v
        if ptv:
            for r, w in ptv["地域"].items():
                region_all[r] += v * w
            eqv = v * ptv["大类"]["权益"]
            for r, w in ptv.get("权益地域", {}).items():
                region_eq[r] += eqv * w
            for c, w in ptv["币种"].items():
                ccy[c] += v * w
            for cls, w in ptv["大类"].items():
                sub = {"权益": "投顾", "债券类固收": "投顾债", "现金": "投顾现金"}.get(cls, "投顾")
                tree[cls][sub].append({"name": f"{name}·{cls}", "value": round(v * w)})
        elif a["class"] == "负债":
            continue
        elif a["class"] == "房产":
            continue
        else:
            region_all["中国大陆"] += v
            if a["class"] in FINANCIAL:
                ccy["CNY"] += v
            sub = {"招行理财": "理财", "微众银行": "活期"}.get(
                name, "保险" if name.startswith("增额寿") else a["class"])
            tree[a["class"]][sub].append({"name": name, "value": round(v)})
    tree["房产"]["房产"].append({"name": "房产净值", "value": round(classes.get("房产", 0))})

    total_debt = -sum(a["value"] for a in accounts if a["class"] == "负债")
    prop_gross = sum(a["value"] for a in accounts if a["class"] == "房产")
    gross_assets = nw + total_debt
    ltv = total_debt / prop_gross if prop_gross else 0
    leverage = total_debt / gross_assets if gross_assets else 0

    yld = cf.get("年化收益率假设", {})
    div = sum(h["value"] * h["股息率"] for h in holdings)
    fixed_income = 0.0
    for a in accounts:
        ptv = pt.get(a["name"])
        if ptv:
            by = yld.get("海外债", 0) if a["name"] == "海外长钱" else yld.get("长钱债", 0)
            fixed_income += a["value"] * ptv["大类"].get("债券类固收", 0) * by
            fixed_income += a["value"] * ptv["大类"].get("现金", 0) * yld.get("货币现金", 0)
        elif a["name"] == "招行理财":
            fixed_income += a["value"] * yld.get("招行理财", 0)
        elif a["name"].startswith("增额寿"):
            fixed_income += a["value"] * yld.get("增额寿", 0)
        elif a["class"] == "现金":
            fixed_income += a["value"] * yld.get("货币现金", 0)
    annual_income = div + fixed_income
    passive_month = annual_income / 12

    inc_items = inc_mod.income_items_net(cf)
    income_net = inc_mod.income_net_of(cf)
    fx = R["fx"]
    today = datetime.date.today()
    subs_list = subs.load_subs()
    subs_monthly = subs.monthly_total(subs_list, fx)
    fixed_out = subs.cashflow_fixed_out(cf, fx)
    net_cf = income_net - fixed_out
    liquid = liq.get("即时", 0) + liq.get("数日", 0)

    # 定投：按真实月结余自动(净结余为负则本月暂停)
    dca_cfg = cf.get("定投计划", {})
    ratio = dca_cfg.get("结余投入比例", 0)
    if dca_cfg.get("模式") == "按结余比例":
        dca_month = max(0.0, net_cf) * ratio
    else:
        dca_month = dca_cfg.get("固定月额", 0)
    per_n = 4 if dca_cfg.get("频率") == "每周" else 2

    # 浮盈：成本来自 holdings_history（净投入口径）；无成本记录的条目 pnl=None
    history_rows = read_csv("holdings_history.csv", [])
    cost_map = cost_basis(history_rows, fx)
    R["warnings"] += [("warn", w) for w in ledger_qty_check(holdings, history_rows)]
    positions = []
    for h in holdings:
        c = cost_map.get(h["name"])
        pnl = round(h["value"] - c) if c else None
        pct = (h["value"] - c) / abs(c) if c else None
        positions.append([h["name"], round(h["value"]),
                          h["type"] in SINGLE_STOCK_TYPES, pnl, pct])
    positions += [[a["name"], round(a["value"]), False, None, None] for a in accounts
                  if a["value"] > 0 and a["class"] != "负债"]
    positions.sort(key=lambda x: -x[1])
    pnl_total = sum(h["value"] - cost_map[h["name"]]
                    for h in holdings if cost_map.get(h["name"]))

    # 投顾指标：资金加权收益(XIRR)/净资产增长归因/FI 推演/再平衡执行单
    perf = metrics.portfolio_perf(history_rows, holdings, fx)
    perf["byHolding"] = perf["byHolding"][:10]
    cfh_rows = read_csv("cashflow_history.csv", [])
    attrib = metrics.attribution(history, cfh_rows)
    attrib_m = metrics.attribution_monthly(history, cfh_rows)

    # ── 2029 目标态导航(见 docs/2029-plan.md):换房是未来数年量级最大的一次财务动作 ──
    goal = load_json("goal.json")
    ins_monthly = ins.monthly_total(ins.load_policies())
    lifelong_m, ending_items = metrics.lifelong_out(cf.get("月度收支", []), subs_monthly, ins_monthly)
    reloc = metrics.relocation_plan(goal, classes, nw, prop_gross, total_debt, liquid)
    # FI 走事件阶梯:月储蓄不是恒定的(增额寿缴清/换房/车贷还清都会改变它),
    # 且育儿在孩子成年前必须继续供 → 三个数字(Coast/育儿储备/真·自由线)
    events = goal.get("重大事件") or []
    fi = metrics.fi_plan(fin, fixed_out, net_cf, goal.get("FI") or cf.get("FI", {}),
                         lifelong_month=lifelong_m, ending_items=ending_items,
                         events=events)
    # 真实储蓄:投资收益取逐日浮盈序列的**期间增量**(不能用累计浮盈——时间窗对不上,残差会是垃圾),
    # 储蓄作为残差(自动吸收未记录的生活开支);数据不足时返回 insufficient,宁可不给数也不给错数
    real_sav = metrics.true_savings(
        history, cfh_rows,
        metrics.pnl_series_from(read_csv("history_full.csv", []), history_rows, fx))
    reb = metrics.rebalance_plan(classes, TARGET_NETWORTH, nw, dca_month)
    stress = metrics.stress_test(classes, prop_gross, total_debt, gross_assets, ccy, nw, reloc)
    policy_loan = 0.8 * sum(a["value"] for a in accounts if a["type"] == "类固收保险")
    # SBBI 序列是仓库静态参考数据,不进 storage——直接读文件
    _sbbi_p = BASE / "sbbi_returns.json"
    _sbbi_data = json.loads(_sbbi_p.read_text(encoding="utf-8")) if _sbbi_p.exists() else {}
    sbbi = metrics.sbbi_replay(classes, _sbbi_data)
    # 目标态回放:换房后的权重才是要长期持有的那个组合(当前权重 2.5 年后作废)
    tgt_cls = ((goal.get("目标态") or {}).get("大类") or {})
    sbbi_goal = metrics.sbbi_replay({k: v for k, v in tgt_cls.items()}, _sbbi_data) if tgt_cls else None

    equity = classes.get("权益", 0)
    concentrated = sum(h["value"] for h in holdings if h["type"] in SINGLE_STOCK_TYPES)
    pt_equity = sum(a["value"] * pt[a["name"]]["大类"]["权益"] for a in accounts if a["name"] in pt)
    broad = sum(h["value"] for h in holdings if h["type"] == "A股权益ETF") + pt_equity

    tree_list = [{"name": c, "children": [{"name": s, "children": tree[c][s]} for s in tree[c]]}
                 for c in ["房产", "权益", "债券类固收", "现金", "黄金"] if tree.get(c)]

    # 走势：大类用每日快照(history.csv)，上市持仓拉东财K线×当前份数×汇率
    # fx 已在现金流段取用 R["fx"]
    trends = {}
    for cc in ["房产", "权益", "债券类固收", "现金", "黄金"]:
        if history and cc in history[0]:
            trends["cat:" + cc] = [[r["date"], float(r[cc])] for r in history]
    # 账户级历史走势（来自 history_full.csv 长表）
    full = read_csv("history_full.csv", [])
    acct = {}
    holdrec = {}   # 持仓级自记收盘市值(来自 history_full 类型=持仓)，K线的补丁/兜底
    for r in full:
        if r.get("类型") == "账户":
            acct.setdefault(r["名称"], []).append([r["date"], float(r["金额"])])
        elif r.get("类型") == "持仓":
            holdrec.setdefault(r["名称"], {})[r["date"]] = float(r["金额"])
    for name, series in acct.items():
        series.sort()
        trends["acct:" + name] = series
    # K线带本地缓存：当天已取则复用；取失败则回退到旧缓存，避免被限流时丢数据
    cache = load_json("klines_cache.json")
    meta = cache.get("_meta", {})
    today_iso = datetime.date.today().isoformat()
    changed = False
    _last = [0.0]   # 上次东财请求时刻(节流用)

    def _fetch_throttled(sid, tx):
        """节流抓取：两次请求间至少隔 KLINE_FETCH_GAP 秒；失败退避后重试一次。"""
        for attempt in range(2):
            wait = KLINE_FETCH_GAP - (time.monotonic() - _last[0])
            if wait > 0:
                time.sleep(wait)
            _last[0] = time.monotonic()
            ks = _kline(sid) or _kline_tx(tx)
            if ks:
                return ks
            time.sleep(KLINE_FETCH_GAP * (attempt + 2))   # 被限流则退避后再试
        return []

    for h in read_csv("holdings.csv"):
        sid = h.get("东财secid")
        if not sid:
            continue
        rate = fx["HKD"] if h["市场"] == "港股" else fx["USD"] if h["市场"] == "美股" else 1.0
        ks = None
        if meta.get(sid) == today_iso and sid in cache:
            ks = cache[sid]                # 今天已抓过 → 复用，不重复请求
        elif not fetch_klines:
            ks = cache.get(sid)            # 轻量模式 → 只用旧缓存，不发请求
        else:
            fetched = _fetch_throttled(sid, h.get("腾讯查询代码", ""))
            if fetched:
                ks = cache[sid] = fetched
                meta[sid] = today_iso
                changed = True
            elif sid in cache:
                ks = cache[sid]            # 限流/失败 → 用旧缓存(尾部仍由自记市值补齐)
        # K线打底 + 自记收盘市值覆盖/补尾：任一天没抓到 K线也不丢，用我们每天记的市值顶上
        name = h["名称"]
        qty = float(h["持有数量"])
        by_date = {d: round(px * qty * rate) for d, px in (ks or [])}
        by_date.update(holdrec.get(name, {}))   # 自记市值优先(实际估值口径)，且能延伸到 K线尾部之后
        if by_date:
            trends["hold:" + name] = sorted([d, v] for d, v in by_date.items())
    if changed:
        cache["_meta"] = meta
        storage.save_doc("klines_cache", cache, backup=False)

    # 杠铃视图分桶：安全腿(亏不了) / 核心(多元化beta) / 冒险腿(非对称) / 中间(待审视)
    LOWVOL_SINGLE = {"长江电力"}
    barbell = {"安全腿": 0.0, "核心": 0.0, "冒险腿": 0.0, "中间": 0.0}
    for h in holdings:
        v = h["value"]; t = h["type"]
        if t == "美股杠杆ETF":
            barbell["冒险腿"] += v
        elif t in ("A股个股", "港股个股", "美股个股"):
            barbell["核心" if h["name"] in LOWVOL_SINGLE else "冒险腿"] += v
        else:
            barbell["核心"] += v
    for a in accounts:
        v = a["value"]; ptv = pt.get(a["name"])
        if ptv:
            barbell["核心"] += v * ptv["大类"].get("权益", 0)
            barbell["安全腿"] += v * (ptv["大类"].get("债券类固收", 0) + ptv["大类"].get("现金", 0))
        elif a["class"] == "负债":
            continue
        elif a["class"] == "房产":
            continue
        elif a["class"] in ("债券类固收", "现金"):
            barbell["安全腿"] += v
    barbell["中间"] += classes.get("房产", 0)
    barbell = {k: round(v) for k, v in barbell.items()}
    safety_months = round(barbell["安全腿"] / fixed_out, 1) if fixed_out else 0

    # 负债台账：推演余额与利息月耗（净资产负债即来源于此，无需校准比对）
    loan_items = loans_mod.enrich(today=today)
    loan_bal, loan_int = loans_mod.totals(loan_items)
    for x in loan_items:
        if x.get("提示"):
            R["warnings"].append(("info", f'负债 {x.get("名称","")}：{x["提示"]}'))
        # 用户自设的常驻提醒（如：卖房将强制结清→公积金提取通道消失），每日红色横幅
        if x.get("提醒") and x.get("状态", "在还") == "在还":
            R["warnings"].append(("warn", f'💳 {x.get("名称","")}：{x["提醒"]}'))

    subs_yearly = round(subs_monthly * 12)
    subs_by_cat = {k: round(v) for k, v in subs.by_category(subs_list, fx).items()}
    subs.sync_icons(subs_list)
    # 日历窗口 = 当月按整周补齐（周一起），前后月补位日也带扣费，且含已发生的扣费
    first = today.replace(day=1)
    last = subs.add_months(first, 1) - datetime.timedelta(days=1)
    cal_start = first - datetime.timedelta(days=first.weekday())
    cal_end = last + datetime.timedelta(days=6 - last.weekday())
    subs_calendar = {}
    for iso, items in subs.charges_between(subs_list, cal_start, cal_end).items():
        subs_calendar[iso] = []
        for s in items:
            ds = subs.decorate_sub(s)
            subs_calendar[iso].append({
                "名称": ds["名称"], "图标": ds["图标"], "iconPath": ds.get("iconPath"),
                "chargeCny": round(subs.charge_cny(s, fx)),
            })
    subs_upcoming = [
        {"date": d.isoformat(), "名称": name, "chargeCny": cny, "图标": icon,
         "iconPath": ipath, "days": delta}
        for d, name, cny, icon, delta, ipath in subs.upcoming(subs_list, today, fx, 30)]
    policies = ins.load_policies()
    _icfg = load_json("insurance.json")
    ins_gap = metrics.insurance_gap(inc_items, ins.by_member(policies), total_debt, fixed_out,
                                    _icfg.get("缺口假设", {}) if isinstance(_icfg, dict) else {})
    alerts = (list(check_alerts(R)) + subs.reminders(subs_list, today, fx)
              + ins.reminders(policies, today))

    out = {
        "date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "fxUSD": R["fx"]["USD"], "fxHKD": R["fx"]["HKD"],
        "networth": round(nw), "financial": round(fin), "equity": round(equity),
        "classes": {k: round(v) for k, v in classes.items()},
        "target": TARGET_NETWORTH,
        "regionAll": {k: round(v) for k, v in region_all.items() if v > 0},
        "regionEq": {k: round(v) for k, v in region_eq.items() if v > 0},
        "ccy": {k: round(v) for k, v in ccy.items() if v > 0},
        "liq": {k: round(liq.get(k, 0)) for k in ["即时", "数日", "锁定", "极低"] if liq.get(k, 0) > 0},
        "grossAssets": round(gross_assets), "totalDebt": round(total_debt),
        "ltv": ltv, "leverage": leverage,
        "income": round(income_net), "fixedOut": round(fixed_out), "netCashflow": round(net_cf),
        "incomeItems": inc_items,
        "savingsRate": net_cf / income_net if income_net else 0,
        "dca": {"month": round(dca_month), "per": round(dca_month / per_n) if per_n else 0,
                "freq": dca_cfg.get("频率", "每周"), "ratio": ratio,
                "mode": dca_cfg.get("模式", ""),
                "targets": dca_cfg.get("标的", {})},
        "passiveMonth": round(passive_month),
        "coverage": passive_month / fixed_out if fixed_out else 0,
        "runway": liquid / fixed_out if fixed_out else 0,
        "liquid": round(liquid), "hypoRent": round(prop_gross * cf.get("投资房假设毛租金回报率", 0) / 12),
        "flowItems": cf.get("月度收支", []),
        "broad": round(broad), "concentrated": round(concentrated),
        "positions": positions[:12],
        "pnlTotal": round(pnl_total),
        "perf": perf, "attribution": attrib, "attributionMonthly": attrib_m,
        "fi": fi, "rebalance": reb,
        "stress": stress, "insGap": ins_gap, "policyLoan": round(policy_loan),
        "demo": storage.DEMO,     # 演示模式 → 面板挂「虚构人物」横幅,别被当成真人数据
        "goal": goal, "reloc": reloc, "trueSavings": real_sav,
        # 近 30 天有没有真的买入过?——用来揭穿「执行单显示在纠偏,实际一笔没投」
        "buys30d": sum(1 for r in history_rows if r.get("动作") == "买入"
                       and (r.get("日期") or "") >= (today - datetime.timedelta(days=30)).isoformat()),
        "sbbi": sbbi, "sbbiGoal": sbbi_goal,
        "alerts": alerts,
        "warnings": R["warnings"],
        "insMonthly": round(ins.monthly_total(policies)),
        "insYearly": round(ins.yearly_total(policies)),
        "insItems": ins.enrich(policies, today),
        "insByMember": ins.by_member(policies),
        "platforms": [[p, round(v)] for p, v in platform_summary(holdings, accounts)],
        "loans": {"items": [{k: x.get(k) for k in
                             ("名称", "类型", "余额", "利息月耗", "还清约", "提示", "状态")}
                            for x in loan_items],
                  "totalBal": loan_bal, "totalInt": loan_int},
        "subsMonthly": round(subs_monthly),
        "subsYearly": subs_yearly,
        "subsItems": subs.enrich_items(subs_list, fx, today, fetch_icons=False),
        "subsByCategory": subs_by_cat,
        "subsUpcoming": subs_upcoming,
        "subsCalendar": subs_calendar,
        "fxLive": R["fx"].get("_live", True),
        "history": [{"date": h["date"], "总净资产": float(h["总净资产"]),
                     "金融资产": float(h["金融资产"])} for h in history],
        "tree": tree_list,
        "trends": trends,
        "barbell": barbell,
        "safetyMonths": safety_months,
    }
    # 现金流月度历史：用刚算好的汇总数 upsert 当月一行，再回读(含当月)供面板画走势
    cfh.record_provisional(out)
    out["cashflowHistory"] = cfh.load_history()
    return out
