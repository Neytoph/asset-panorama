#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
持仓管理（编辑器「持仓管理」Tab 的领域逻辑）
==========================================
持仓数量的任何变动（改数量/新增/删除）都强制联动 holdings_history 一笔
买入/卖出记录——成本(净投入口径)与持仓不再依赖手工保持一致。
手动账户金额写 manual_values 并盖更新日期戳，替代 update_values.py 终端问答。
"""
import datetime

import storage

HOLDING_FIELDS = ["名称", "代码", "市场", "资产类型", "账户", "持有数量",
                  "新浪查询代码", "腾讯查询代码", "东财secid", "流动性", "股息率"]
HISTORY_FIELDS = ["日期", "动作", "名称", "代码", "市场", "资产类型", "账户",
                  "数量", "成交价", "成交币种", "成交额", "原因/备注"]
ACCOUNT_FIELDS = ["账户名称", "资产类型", "金额或估值", "穿透结构", "备注", "流动性"]
MARKET_CCY = {"港股": "HKD", "美股": "USD"}
TRADE_NOTE = "持仓管理页录入"
QTY_EPS = 1e-6


def load_holdings():
    return storage.load_table("holdings", [])


def load_accounts():
    return storage.load_table("accounts", [])


def load_manual():
    return storage.load_doc("manual_values", {})


def quote_map():
    """{腾讯查询代码: {price, date, ...}}，取不到缓存则空表。"""
    return (storage.load_doc("quotes_cache", {}) or {}).get("quotes", {})


def _f(v, default=None):
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def diff_trades(old_rows, new_rows, price_map, quotes=None, today=None, note=""):
    """新旧持仓按「名称」对比 → (台账记录列表, 警告列表)。
    price_map: {名称: 成交价字符串}（用户录入，空则退最新行情价）。
    note: 本批记录的原因/备注（定投/迁移/再平衡…），空则用缺省文案。
    找不到任何价格时仍记这笔（成交额留空，cost_basis 会跳过），并给出警告。"""
    quotes = quotes or {}
    today = today or datetime.date.today().isoformat()
    note = (note or "").strip() or TRADE_NOTE
    old = {r["名称"]: r for r in old_rows}
    new = {r["名称"]: r for r in new_rows}
    records, warnings = [], []

    def price_of(row, name):
        px = _f(price_map.get(name))
        if px is not None:
            return px
        q = quotes.get((row.get("腾讯查询代码") or "").strip())
        return q.get("price") if q else None

    def record(row, action, qty, name):
        px = price_of(row, name)
        amount = round(qty * px, 2) if px is not None else ""
        if px is None:
            warnings.append(f"{name} 无成交价也无行情缓存，成交额留空——浮盈成本需手补 holdings_history")
        records.append({
            "日期": today, "动作": action, "名称": name,
            "代码": row.get("代码", ""), "市场": row.get("市场", ""),
            "资产类型": row.get("资产类型", ""), "账户": row.get("账户", ""),
            "数量": f"{qty:g}", "成交价": "" if px is None else f"{px:g}",
            "成交币种": MARKET_CCY.get(row.get("市场", ""), "CNY"),
            "成交额": amount if amount == "" else f"{amount:g}",
            "原因/备注": note,
        })

    for name, row in new.items():
        qty_new = _f(row.get("持有数量"), 0.0)
        if name in old:
            qty_old = _f(old[name].get("持有数量"), 0.0)
            delta = qty_new - qty_old
            if abs(delta) > QTY_EPS:
                record(row, "买入" if delta > 0 else "卖出", abs(delta), name)
        elif qty_new > QTY_EPS:
            record(row, "买入", qty_new, name)
    for name, row in old.items():
        if name not in new:
            qty_old = _f(row.get("持有数量"), 0.0)
            if qty_old > QTY_EPS:
                record(row, "卖出", qty_old, name)
    return records, warnings


def save_holdings(new_rows, price_map, note=""):
    """保存持仓表并把数量差异追加进 holdings_history。返回 (记录, 警告)。"""
    old_rows = load_holdings()
    records, warnings = diff_trades(old_rows, new_rows, price_map, quote_map(), note=note)
    fields = list(old_rows[0].keys()) if old_rows else HOLDING_FIELDS
    storage.save_table("holdings", fields, new_rows)
    if records:
        hist = storage.load_table("holdings_history", [])
        hfields = list(hist[0].keys()) if hist else HISTORY_FIELDS
        storage.save_table("holdings_history", hfields, hist + records)
    return records, warnings


def save_manual(updates, today=None):
    """更新手动账户金额 → manual_values；只有数值真变了才盖日期戳。返回变更名单。"""
    today = today or datetime.date.today().isoformat()
    mv = load_manual()
    changed = []
    for name, raw in updates.items():
        val = _f(raw)
        if val is None or name not in mv:
            continue
        if abs(val - float(mv[name].get("value", 0))) > 0.005:
            mv[name]["value"] = val
            mv[name]["updated"] = today
            changed.append(name)
    if changed:
        storage.save_doc("manual_values", mv)
    return changed


def add_account(name, atype, value, liquidity, note, today=None):
    """新增手动账户：accounts 表加行 + manual_values 建条目（估值走手动值）。"""
    today = today or datetime.date.today().isoformat()
    name = name.strip()
    val = _f(value)
    if not name or val is None:
        return False
    accounts = load_accounts()
    if any(a["账户名称"] == name for a in accounts):
        return False
    fields = list(accounts[0].keys()) if accounts else ACCOUNT_FIELDS
    accounts.append({"账户名称": name, "资产类型": atype.strip() or "现金存款",
                     "金额或估值": f"{val:g}", "穿透结构": "",
                     "备注": note.strip(), "流动性": liquidity.strip() or "数日"})
    storage.save_table("accounts", fields, accounts)
    mv = load_manual()
    mv[name] = {"value": val, "updated": today, "note": note.strip()}
    storage.save_doc("manual_values", mv)
    return True
