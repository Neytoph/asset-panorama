#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
资产组合实时估值引擎
====================
compute() 返回结构化结果(holdings/accounts/classes/...)，供 tracker 打印、
panorama_data.py 复用。
  - 实时行情(新浪主 + 腾讯备 + quotes_cache 兜底) + 实时汇率(open.er-api)
  - 手动账户 manual_values.json 优先(update_values.py 维护)
  - 增额寿按保单年度从现金价值表插值
  - 大类穿透权重来自 passthrough.json
依赖：仅标准库(Python 3.8+)。用法：python3 portfolio_tracker.py
"""

import json
import re
import sys
import datetime
import urllib.request
from pathlib import Path

import storage

BASE = Path(__file__).resolve().parent

# ───────────────────────── 配置 ─────────────────────────
FX_FALLBACK = {"USD": 6.77, "HKD": 0.87, "CNY": 1.0}
MANUAL_STALE_DAYS = 14
# 锚定值(manual_values 里 kind=anchor):房产这类本就不该每两周动一次的估值。
# 取值须来自可验证外部锚(同小区近3月成交均价×面积;无成交则用买入价,宁可保守),每季度重估。
ANCHOR_STALE_DAYS = 100

HOLDING_CLASS = {
    "A股权益ETF": "权益", "A股个股": "权益", "港股个股": "权益",
    "美股个股": "权益", "美股杠杆ETF": "权益", "黄金ETF": "黄金",
}
SINGLE_STOCK_TYPES = {"A股个股", "港股个股", "美股个股", "美股杠杆ETF"}
MARKET_CCY = {"港股": "HKD", "美股": "USD"}
ACCOUNT_CLASS = {"固收理财": "债券类固收", "类固收保险": "债券类固收",
                 "现金存款": "现金", "不动产": "房产", "负债": "负债"}

TARGET_NETWORTH = {"房产": 0.45, "权益": 0.34, "债券类固收": 0.13, "现金": 0.04, "黄金": 0.04}

DEVIATION_ALERT = 0.05
CLASS_BANDS = [("房产", "max", 0.50), ("权益", "min", 0.25),
               ("黄金", "min", 0.03), ("现金", "max", 0.10)]
SINGLE_STOCK_MAX = 0.10
CLUSTER_MAX_OF_EQUITY = 0.20

HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (portfolio-tracker)"}
QUOTES_CACHE_FILE = "quotes_cache.json"
SINA_FAILURE_THRESHOLD = 3
SINA_BREAKER_MINUTES = 5
QUOTE_STALE_DAYS = 7
SINA_REFERER = "https://finance.sina.com.cn"
SINA_LINE_RE = re.compile(r'var hq_str_([a-zA-Z0-9_]+)="([^"]*)";')


# ───────────────────────── 工具 ─────────────────────────
def http_get(url, headers=None, encoding="utf-8"):
    req = urllib.request.Request(url, headers={**HTTP_HEADERS, **(headers or {})})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read().decode(encoding, errors="replace")


def read_csv(name, default=None):
    """经 storage 统一存取（file/sqlite 二选一），语义同 csv.DictReader。"""
    return storage.load_table(name, default)


def load_json(name, default=None):
    return storage.load_doc(name, {} if default is None else default)


def stale_warning(name, mv, today=None):
    """手动值/锚定值过期提醒。anchor 类(如房产)按季度口径,不参与 14 天催更。
    返回提醒文案或 None。"""
    today = today or datetime.date.today()
    try:
        age = (today - datetime.date.fromisoformat(mv.get("updated", ""))).days
    except ValueError:
        return None
    if (mv.get("kind") or "") == "anchor":
        if age > ANCHOR_STALE_DAYS:
            return f"{name} 锚定值已 {age} 天未重估(季度口径,取同小区近3月成交均价)"
        return None
    if age > MANUAL_STALE_DAYS:
        return f"{name} 手动值已 {age} 天未更新(运行 update_values.py)"
    return None


def insurance_policy_starts():
    """储蓄型保单(增额寿/终身寿)起保日：账户名 → date。
    账户名与起保日属个人信息，只存配置（insurance.json 的「储蓄型起保日」），不进代码。
    现价表 insurance_cashvalue.csv 按保单年度插值出当前资产值，起保日决定「现在是第几个保单年度」。"""
    out = {}
    for name, d in (load_json("insurance.json").get("储蓄型起保日") or {}).items():
        try:
            out[name] = datetime.date.fromisoformat(str(d))
        except ValueError:
            continue
    return out


INSURANCE_POLICY_STARTS = insurance_policy_starts()


def ledger_debt_accounts(today=None):
    """负债台账 → 账户行列表（value 为负）。台账为空时返回 []（退回 accounts.csv 负债行）。"""
    import loans
    out = []
    for x in loans.enrich(today=today):
        if x.get("状态", "在还") == "在还" and x["余额"] > 0:
            out.append({"name": x["名称"], "type": "负债", "value": -x["余额"],
                        "class": "负债", "流动性": "数日", "passthrough": None})
    return out


def passthrough_class():
    """{账户名: {大类: 权重}}，来自 passthrough.json。"""
    pt = load_json("passthrough.json")
    return {k: v["大类"] for k, v in pt.items() if isinstance(v, dict) and "大类" in v}


# ───────────────────────── 行情 / 汇率 ─────────────────────────
def _load_quotes_cache():
    raw = load_json(QUOTES_CACHE_FILE, {})
    raw.setdefault("sina_breaker", {"failures": 0, "opened_at": None})
    raw.setdefault("quotes", {})
    return raw


def _save_quotes_cache(cache):
    storage.save_doc(QUOTES_CACHE_FILE, cache, backup=False)


def _sina_breaker_open(cache):
    br = cache["sina_breaker"]
    if br.get("failures", 0) < SINA_FAILURE_THRESHOLD:
        return False
    opened = br.get("opened_at")
    if not opened:
        return False
    try:
        opened_dt = datetime.datetime.fromisoformat(opened)
    except ValueError:
        return False
    return datetime.datetime.now() < opened_dt + datetime.timedelta(minutes=SINA_BREAKER_MINUTES)


def _record_sina_failure(cache):
    br = cache["sina_breaker"]
    br["failures"] = br.get("failures", 0) + 1
    if br["failures"] >= SINA_FAILURE_THRESHOLD:
        br["opened_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        print(f"  ⚠️ 新浪行情熔断 {SINA_BREAKER_MINUTES} 分钟", file=sys.stderr)


def _reset_sina_breaker(cache):
    br = cache["sina_breaker"]
    if br.get("failures", 0) > 0:
        br["failures"] = 0
        br["opened_at"] = None


def _sina_price_idx(sina_key):
    k = sina_key.lower()
    if k.startswith("gb_"):
        return 1
    if k.startswith(("hk", "rt_hk")):
        return 6
    return 3


def _parse_sina(text, sina_to_tx):
    """解析新浪响应 → {腾讯代码: price}"""
    out = {}
    if not text:
        return out
    for m in SINA_LINE_RE.finditer(text):
        sina_key, payload = m.group(1), m.group(2)
        if not payload.strip():
            continue
        tx = sina_to_tx.get(sina_key)
        if not tx:
            continue
        fields = payload.split(",")
        idx = _sina_price_idx(sina_key)
        if len(fields) <= idx:
            continue
        try:
            price = float(fields[idx].strip())
            if price > 0:
                out[tx] = price
        except ValueError:
            pass
    return out


def _fetch_sina(sina_syms):
    if not sina_syms:
        return None
    url = "https://hq.sinajs.cn/list=" + ",".join(sina_syms)
    try:
        return http_get(url, headers={"Referer": SINA_REFERER}, encoding="gb18030")
    except Exception as e:
        print(f"  ⚠️ 新浪行情获取失败：{e}", file=sys.stderr)
        return None


def _fetch_tencent(tx_syms):
    prices = {}
    if not tx_syms:
        return prices
    try:
        text = http_get("https://qt.gtimg.cn/q=" + ",".join(tx_syms), encoding="gbk")
    except Exception as e:
        print(f"  ⚠️ 腾讯行情获取失败：{e}", file=sys.stderr)
        return prices
    for line in text.split(";"):
        if "=" not in line:
            continue
        key, _, payload = line.strip().partition("=")
        sym = key.replace("v_", "").strip()
        f = payload.strip().strip('"').split("~")
        if len(f) > 3:
            try:
                prices[sym] = float(f[3])
            except ValueError:
                pass
    return prices


def fetch_quotes(holdings):
    """
    新浪主 + 腾讯备 + quotes_cache 兜底。
    返回 (prices: {腾讯代码: float}, meta: {腾讯代码: {source, stale, date, age_days?}})
    """
    pairs = []
    for h in holdings:
        tx = (h.get("腾讯查询代码") or "").strip()
        if not tx:
            continue
        sina = (h.get("新浪查询代码") or tx).strip()
        pairs.append((tx, sina))
    if not pairs:
        return {}, {}

    tx_syms = [p[0] for p in pairs]
    sina_to_tx = {sina: tx for tx, sina in pairs}
    sina_syms = list(dict.fromkeys(sina_to_tx))

    cache = _load_quotes_cache()
    today = datetime.date.today().isoformat()
    now = datetime.datetime.now().isoformat(timespec="seconds")
    prices, meta = {}, {}

    if not _sina_breaker_open(cache):
        body = _fetch_sina(sina_syms)
        if body is not None:
            sina_prices = _parse_sina(body, sina_to_tx)
            if sina_prices:
                _reset_sina_breaker(cache)
                for tx, price in sina_prices.items():
                    prices[tx] = price
                    meta[tx] = {"source": "sina", "stale": False, "date": today}
            else:
                _record_sina_failure(cache)
        else:
            _record_sina_failure(cache)
    else:
        print("  ℹ️ 新浪熔断中，跳过主源", file=sys.stderr)

    missing_tx = [tx for tx in tx_syms if tx not in prices]
    if missing_tx:
        for tx, price in _fetch_tencent(missing_tx).items():
            if tx not in prices and price > 0:
                prices[tx] = price
                meta[tx] = {"source": "tencent", "stale": False, "date": today}

    for tx, price in prices.items():
        m = meta.get(tx, {})
        cache["quotes"][tx] = {
            "price": price,
            "source": m.get("source", "live"),
            "date": today,
            "updated_at": now,
        }

    for tx in [t for t in tx_syms if t not in prices]:
        cached = cache["quotes"].get(tx)
        if not cached or not cached.get("price"):
            continue
        try:
            age = (datetime.date.today() - datetime.date.fromisoformat(cached.get("date", ""))).days
        except ValueError:
            age = 999
        prices[tx] = float(cached["price"])
        meta[tx] = {
            "source": cached.get("source", "cache"),
            "stale": age > QUOTE_STALE_DAYS,
            "date": cached.get("date"),
            "age_days": age,
        }

    _save_quotes_cache(cache)
    return prices, meta


def get_fx():
    try:
        d = json.loads(http_get("https://open.er-api.com/v6/latest/USD"))
        cny, hkd = d["rates"]["CNY"], d["rates"]["HKD"]
        return {"USD": cny, "HKD": cny / hkd, "CNY": 1.0, "_live": True}
    except Exception as e:
        print(f"  ⚠️ 汇率获取失败，使用兜底值：{e}", file=sys.stderr)
        return {**FX_FALLBACK, "_live": False}


def resolve_insurance(name, value_str):
    """按保单年度在现价表里线性插值。表可含多张保单(账户列);无账户列的旧行归第一张保单。"""
    try:
        table = {int(r["保单年度"]): float(r["现金价值"])
                 for r in read_csv("insurance_cashvalue.csv")
                 if (r.get("账户") or name) == name}
    except Exception:
        return float(value_str)
    if not table:
        return float(value_str)
    elapsed = (datetime.date.today() - INSURANCE_POLICY_STARTS[name]).days / 365.25
    yr = max(1, int(elapsed))
    frac = max(0.0, min(1.0, elapsed - yr))
    lo = table.get(yr, float(value_str))
    hi = table.get(yr + 1, lo)
    return lo + frac * (hi - lo)


ACCOUNT_RESOLVERS = {n: (lambda nm: lambda v: resolve_insurance(nm, v))(n)
                     for n in INSURANCE_POLICY_STARTS}


# ───────────────────────── 计算 ─────────────────────────
def compute():
    fx = get_fx()
    manual = load_json("manual_values.json")
    pt_class = passthrough_class()
    holdings_csv = read_csv("holdings.csv")
    quotes, quote_meta = fetch_quotes(holdings_csv)

    classes, holdings, accounts, warnings = {}, [], [], []

    for h in holdings_csv:
        sym, qty = (h.get("腾讯查询代码") or "").strip(), float(h["持有数量"])
        ccy = MARKET_CCY.get(h["市场"], "CNY")
        cls = HOLDING_CLASS.get(h["资产类型"], "权益")
        qm = {}
        if not sym:
            # 无行情代码 → 手动估值持仓(如期权,行情源无法报价)，取值自 manual_values.json(人民币口径)
            mv = manual.get(h["名称"])
            if mv is not None:
                price, value, missing = None, float(mv["value"]), False
                classes[cls] = classes.get(cls, 0.0) + value
                w = stale_warning(h["名称"], mv)
                if w:
                    warnings.append(("info", w))
            else:
                price, value, missing = None, 0.0, True
                warnings.append(("warn", f"{h['名称']} 无行情代码且缺手动值(manual_values.json)"))
        else:
            price = quotes.get(sym)
            qm = quote_meta.get(sym, {})
            if price is None:
                warnings.append(("warn", f"{h['名称']}({sym}) 行情缺失"))
                value, missing = 0.0, True
            else:
                value, missing = price * qty * fx[ccy], False
                classes[cls] = classes.get(cls, 0.0) + value
                if qm.get("stale"):
                    age = qm.get("age_days", "?")
                    warnings.append(("info", f"{h['名称']} 使用缓存行情({qm.get('date')}，{age}天前)"))
        holdings.append({
            "name": h["名称"], "market": h["市场"], "type": h["资产类型"],
            "账户": h.get("账户", ""),
            "class": cls, "ccy": ccy, "qty": qty, "price": price, "value": value,
            "missing": missing,
            "quote_source": qm.get("source") if sym and price is not None else None,
            "quote_stale": qm.get("stale", False) if sym and price is not None else False,
            "流动性": h.get("流动性", "数日"),
            "股息率": float(h.get("股息率") or 0) / 100,
        })

    today = datetime.date.today()
    # 负债唯一来源 = 负债台账（loans，余额按月自动推演）；accounts.csv 负债行不再生效
    ledger_debts = ledger_debt_accounts(today)
    for a in read_csv("accounts.csv"):
        name, atype = a["账户名称"], a["资产类型"]
        if ledger_debts and ACCOUNT_CLASS.get(atype) == "负债":
            if abs(float(a["金额或估值"] or 0)) > 0.01:
                warnings.append(("info", f"accounts 负债行「{name}」已由负债台账接管，该行被忽略（可删除）"))
            continue
        if name in manual:
            value = float(manual[name]["value"])
            w = stale_warning(name, manual[name], today)
            if w:
                warnings.append(("info", w))
        elif name in ACCOUNT_RESOLVERS:
            value = ACCOUNT_RESOLVERS[name](a["金额或估值"])
        else:
            value = float(a["金额或估值"])

        pt = pt_class.get(name)
        if pt:
            for cls, w in pt.items():
                classes[cls] = classes.get(cls, 0.0) + value * w
            cls_label = "组合"
        else:
            cls_label = ACCOUNT_CLASS.get(atype, "其他")
            classes[cls_label] = classes.get(cls_label, 0.0) + value
        accounts.append({"name": name, "type": atype, "value": value,
                         "class": cls_label, "流动性": a.get("流动性", "数日"),
                         "passthrough": pt})

    for d in ledger_debts:
        accounts.append(d)
        classes["负债"] = classes.get("负债", 0.0) + d["value"]

    classes["房产"] = classes.get("房产", 0.0) + classes.pop("负债", 0.0)
    networth = sum(classes.values())
    financial = networth - classes.get("房产", 0.0)
    return {"fx": fx, "holdings": holdings, "accounts": accounts,
            "classes": classes, "networth": networth, "financial": financial,
            "warnings": warnings}


# ───────────────────────── 告警 ─────────────────────────
def check_alerts(R):
    classes, networth = R["classes"], R["networth"]
    alerts = []
    for cls, kind, thr in CLASS_BANDS:
        pct = classes.get(cls, 0.0) / networth
        if kind == "max" and pct > thr:
            alerts.append(("🔴", f"{cls} 占比 {pct:.1%} 超上限 {thr:.0%}"))
        if kind == "min" and pct < thr:
            alerts.append(("🟡", f"{cls} 占比 {pct:.1%} 低于下限 {thr:.0%}"))
    for cls, tgt in TARGET_NETWORTH.items():
        dev = classes.get(cls, 0.0) / networth - tgt
        if abs(dev) > DEVIATION_ALERT:
            alerts.append(("🟠", f"{cls} 偏离目标 {dev:+.1%}(目标{tgt:.0%})，建议再平衡"))
    equity = classes.get("权益", 0.0)
    cluster = 0.0
    for h in R["holdings"]:
        if h["type"] in SINGLE_STOCK_TYPES:
            cluster += h["value"]
            if h["value"] / networth > SINGLE_STOCK_MAX:
                alerts.append(("🔴", f"个股 {h['name']} 占净资产 {h['value']/networth:.1%}，超 10% 红线"))
        if h["type"] == "美股杠杆ETF":
            alerts.append(("🔴", f"杠杆持仓 {h['name']}：长期持有有波动损耗，建议了结"))
    if equity and cluster / equity > CLUSTER_MAX_OF_EQUITY:
        alerts.append(("🟠", f"个股+杠杆合计占权益 {cluster/equity:.0%}，超 {CLUSTER_MAX_OF_EQUITY:.0%}，特质风险偏高"))
    return alerts


# ───────────────────────── 持久化 ─────────────────────────
def persist(R):
    """记录每日历史。带健康检查：行情缺失(市值=0空洞)时拒写 history，避免污染净值曲线；
    写入前自动备份上一版到 history.bak.csv。latest_snapshot 总是更新并带降级标记。"""
    today = datetime.date.today().isoformat()
    classes, networth, financial = R["classes"], R["networth"], R["financial"]

    missing = [h["name"] for h in R["holdings"] if h.get("missing")]
    stale = [h["name"] for h in R["holdings"] if h.get("quote_stale")]
    fx_fallback = not R["fx"].get("_live", True)
    degraded = []
    if missing:
        degraded.append(f"{len(missing)}只行情缺失")
    if stale:
        degraded.append(f"{len(stale)}只陈旧行情")
    if fx_fallback:
        degraded.append("汇率兜底")

    # latest_snapshot：当前状态总是写，带降级标记供 UI 提示
    storage.save_doc("latest_snapshot", {
        "date": today, "networth": networth, "financial": financial,
        "classes": classes, "target": TARGET_NETWORTH, "degraded": degraded,
    }, backup=False)

    # 行情缺失会让相关持仓市值记为 0（空洞）→ 拒写历史，避免永久污染
    if missing:
        return {"recorded": False, "reason": f"行情缺失 {len(missing)} 只，跳过历史写入", "degraded": degraded}

    cols = ["date", "总净资产", "金融资产", "房产", "权益", "债券类固收", "现金", "黄金"]
    row = {"date": today, "总净资产": round(networth), "金融资产": round(financial)}
    for c in ["房产", "权益", "债券类固收", "现金", "黄金"]:
        row[c] = round(classes.get(c, 0.0))
    data = {r["date"]: r for r in read_csv("history.csv", [])}
    data[today] = row
    storage.save_table("history", cols, [data[d] for d in sorted(data)])

    # 明细历史(长表 date,类型,名称,金额)：账户/持仓/大类/汇总 级别，便于画各账户曲线
    debt = -sum(a["value"] for a in R["accounts"] if a["class"] == "负债")
    rows = [r for r in read_csv("history_full.csv", []) if r.get("date") != today]

    def _add(typ, name, val):
        rows.append({"date": today, "类型": typ, "名称": name, "金额": round(val)})
    _add("汇总", "总净资产", networth)
    _add("汇总", "金融资产", financial)
    _add("汇总", "总负债", debt)
    for c in ["房产", "权益", "债券类固收", "现金", "黄金"]:
        if c in classes:
            _add("大类", c, classes[c])
    for a in R["accounts"]:
        _add("账户", a["name"], a["value"])
    for h in R["holdings"]:
        _add("持仓", h["name"], h["value"])
    storage.save_table("history_full", ["date", "类型", "名称", "金额"], rows)
    return {"recorded": True, "degraded": degraded}


# ───────────────────────── 主 ─────────────────────────
def main():
    R = compute()
    fx, classes = R["fx"], R["classes"]
    networth, financial = R["networth"], R["financial"]

    print(f"\n{'='*72}")
    print(f"  资产组合估值  {datetime.datetime.now():%Y-%m-%d %H:%M}"
          f"   USD/CNY={fx['USD']:.4f}  HKD/CNY={fx['HKD']:.4f}")
    print(f"{'='*72}\n")
    print(f"{'名称':<22}{'现价':>12}{'数量':>14}{'大类':>10}{'市值¥':>14}")
    print("-" * 72)
    for h in R["holdings"]:
        if h["price"] is not None:
            ps = f"{h['price']:.3f}{h['ccy']}"
            if h.get("quote_stale"):
                ps += "(缓存)"
            elif h.get("quote_source") == "tencent":
                ps += "(腾)"
        else:
            ps = "行情缺失" if h.get("missing") else "手动估值"
        q = f"{h['qty']:,.4f}".rstrip("0").rstrip(".")
        print(f"{h['name']:<22}{ps:>12}{q:>14}{h['class']:>10}{h['value']:>14,.0f}")
    for a in R["accounts"]:
        print(f"{a['name']:<22}{'':>12}{'':>14}{a['class']:>10}{a['value']:>14,.0f}")

    print(f"\n{'大类配置(净资产口径)':<22}{'金额¥':>14}{'占比':>9}{'目标':>8}{'偏离':>9}")
    print("-" * 64)
    for cls in ["房产", "权益", "债券类固收", "现金", "黄金", "其他"]:
        if cls not in classes:
            continue
        amt, pct = classes[cls], classes[cls] / networth
        tgt = TARGET_NETWORTH.get(cls)
        ts = f"{tgt:>7.0%}" if tgt is not None else "      —"
        ds = f"{pct-tgt:>+8.1%}" if tgt is not None else "       —"
        print(f"{cls:<22}{amt:>14,.0f}{pct:>9.1%}{ts}{ds}")
    print("-" * 64)
    print(f"{'金融资产合计':<22}{financial:>14,.0f}")
    print(f"{'总净资产':<22}{networth:>14,.0f}")
    print(f"  权益占金融资产：{classes.get('权益',0)/financial:.1%}")

    alerts = check_alerts(R)
    print(f"\n{'再平衡告警':<22}\n" + "-" * 64)
    if not alerts and not R["warnings"]:
        print("  ✅ 无告警，配置在目标区间内")
    for lvl, msg in alerts:
        print(f"  {lvl} {msg}")
    for lvl, msg in R["warnings"]:
        print(f"  {'⚠️' if lvl=='warn' else 'ℹ️'} {msg}")

    res = persist(R)
    if res.get("recorded"):
        warn = ("（降级：" + "、".join(res["degraded"]) + "）") if res.get("degraded") else ""
        print(f"\n  ✅ 已记录 history{warn} + latest_snapshot（上一版已自动备份）")
    else:
        print(f"\n  ⚠️ 未记录历史：{res.get('reason')}；latest_snapshot 已更新并标记降级")
    print(f"  全景渲染：python3 panorama_themes.py\n")


if __name__ == "__main__":
    main()
