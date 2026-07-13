# -*- coding: utf-8 -*-
"""
现金流月度历史：草稿自动刷新 + 对账确认锁定。

· record_provisional(D)：配置估算草稿（已对账=否，其他实际支出=0）；已对账月份不覆盖。
· confirm_month(...)：用户对账后写入权威行（已对账=是）。
金额 CNY；固定支出/其他实际支出/被动收入/订阅月支记正数；净结余可负；储蓄率 0~1。
读写经 storage 统一存储层（file/sqlite 二选一），写前自动备份上一版。
"""
import datetime

import storage

DATASET = "cashflow_history"

FIELDS = [
    "月份", "已对账", "对账日", "税后收入", "固定支出", "其他实际支出",
    "净结余", "储蓄率", "定投额", "被动收入", "订阅月支", "对账备注",
]
_NUM = [
    "税后收入", "固定支出", "其他实际支出", "净结余", "储蓄率",
    "定投额", "被动收入", "订阅月支",
]


def _normalize_row(r):
    """兼容旧表缺列。"""
    return {
        "月份": (r.get("月份") or "").strip(),
        "已对账": (r.get("已对账") or "否").strip() or "否",
        "对账日": (r.get("对账日") or "").strip(),
        "对账备注": (r.get("对账备注") or "").strip(),
        **{k: float(r.get(k) or 0) for k in _NUM},
    }


def load_history():
    """读月度历史 → 行 dict 列表，按月份升序。"""
    rows = [_normalize_row(r) for r in storage.load_table(DATASET, [])
            if r.get("月份")]
    rows.sort(key=lambda x: x["月份"])
    return rows


def _row_to_csv(row):
    out = {k: row.get(k, "") for k in FIELDS}
    out["储蓄率"] = f"{float(row['储蓄率']):.4f}"
    for k in _NUM:
        if k != "储蓄率":
            out[k] = str(int(round(float(row[k]))))
    return out


def _write_all(existing):
    storage.save_table(DATASET, FIELDS,
                       [_row_to_csv(existing[m]) for m in sorted(existing)])


def record_provisional(D, month=None):
    """
    自动草稿：仅当该月尚未『已对账=是』时刷新估算行。
    返回 (月份, 是否新增)；跳过则 (month, False) 或 (None, False)。
    """
    income = D.get("income", 0)
    if income <= 0:
        return (None, False)
    month = month or datetime.date.today().strftime("%Y-%m")
    existing = {r["月份"]: r for r in load_history()}
    if month in existing and existing[month].get("已对账") == "是":
        return (month, False)
    is_new = month not in existing
    existing[month] = {
        "月份": month,
        "已对账": "否",
        "对账日": "",
        "税后收入": round(income),
        "固定支出": round(D.get("fixedOut", 0)),
        "其他实际支出": 0,
        "净结余": round(D.get("netCashflow", 0)),
        "储蓄率": round(D.get("savingsRate", 0), 4),
        "定投额": round(D.get("dca", {}).get("month", 0)),
        "被动收入": round(D.get("passiveMonth", 0)),
        "订阅月支": round(D.get("subsMonthly", 0)),
        "对账备注": "",
    }
    _write_all(existing)
    return (month, is_new)


def confirm_month(month, 实际税后收入, 其他实际支出, 定投额, 被动收入, 订阅月支, 固定支出, 备注=""):
    """对账确认：写入已对账=是 的权威行。返回该行 dict。"""
    month = (month or "").strip()
    inc = float(实际税后收入)
    other = max(0.0, float(其他实际支出))
    fix = float(固定支出)
    net = inc - fix - other
    rate = net / inc if inc else 0.0
    row = {
        "月份": month,
        "已对账": "是",
        "对账日": datetime.date.today().isoformat(),
        "税后收入": round(inc),
        "固定支出": round(fix),
        "其他实际支出": round(other),
        "净结余": round(net),
        "储蓄率": round(rate, 4),
        "定投额": round(float(定投额)),
        "被动收入": round(float(被动收入)),
        "订阅月支": round(float(订阅月支)),
        "对账备注": (备注 or "").strip(),
    }
    existing = {r["月份"]: r for r in load_history()}
    existing[month] = row
    _write_all(existing)
    return row


# 兼容旧调用
record_month = record_provisional


if __name__ == "__main__":
    h = load_history()
    if not h:
        print("现金流历史尚无数据（跑一次 panorama_themes.py 即会写入当月草稿）")
    else:
        print(f"共 {len(h)} 个月：")
        for r in h:
            tag = "✅" if r["已对账"] == "是" else "⏳"
            print(f"  {tag} {r['月份']}  收入¥{r['税后收入']:,.0f}  固支¥{r['固定支出']:,.0f}  "
                  f"其他¥{r['其他实际支出']:,.0f}  结余¥{r['净结余']:,.0f}  "
                  f"储蓄率{r['储蓄率']*100:.1f}%")
