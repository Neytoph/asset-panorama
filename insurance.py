# -*- coding: utf-8 -*-
"""
保险保单台账：录入重疾/医疗/意外/寿险等保单，年缴保费摊月计入固定支出，缴费日前提醒。

与增额寿的分工：增额寿有现金价值，作为资产在 accounts.csv/insurance_cashvalue.csv 里；
本模块管的是「保障型保单」的台账与保费现金流（无资产价值，纯支出）。
若把增额寿也录进来（状态=已缴清），只作台账展示，不摊月、不提醒。

保费只对 状态=缴费中 的保单摊月：年缴保费/12，与幼儿园(12w/年)摊月同口径。
数据集 insurance，经 storage 统一存储层读写（file/sqlite 二选一），写前自动备份。
"""
from calendar import monthrange
from datetime import date

import storage

KINDS = ("重疾", "医疗", "意外", "寿险", "年金", "增额寿", "其他")
STATUSES = ("缴费中", "已缴清", "失效")
KIND_ICON = {"重疾": "🏥", "医疗": "💊", "意外": "🦺", "寿险": "🕊️",
             "年金": "📮", "增额寿": "🌱", "其他": "📄"}
# 缴费频率 → 每年期数；「年缴保费」字段始终存年度总额，频率只影响缴费日滚动与单期应缴
FREQS = ("年", "半年", "季", "月")
FREQ_PER_YEAR = {"年": 1, "半年": 2, "季": 4, "月": 12}


def freq_of(p):
    f = (p.get("缴费频率") or "年").strip()
    return f if f in FREQ_PER_YEAR else "年"


def per_due(p):
    """单期应缴金额 = 年缴保费 / 每年期数。"""
    return float(p.get("年缴保费", 0)) / FREQ_PER_YEAR[freq_of(p)]


def is_renewable(p):
    """缴费年限 空/0 = 续保型（一年期医疗/意外），永续摊月、续保时费率会变。"""
    try:
        return int(p.get("缴费年限") or 0) <= 0
    except (ValueError, TypeError):
        return True


def load_policies():
    try:
        return storage.load_doc("insurance", {}).get("保单", [])
    except Exception:
        return []


def save_policies(policies):
    storage.save_doc("insurance", {
        "_note": "保障型保单台账。年缴保费按状态=缴费中摊月计入固定支出。",
        "保单": policies})


def _add_years(d, n):
    try:
        return d.replace(year=d.year + n)
    except ValueError:                      # 2/29 → 2/28
        return d.replace(year=d.year + n,
                         day=monthrange(d.year + n, d.month)[1])


def _add_months(d, n):
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, monthrange(y, m)[1]))


def paying(p):
    return p.get("状态", "缴费中") == "缴费中"


def next_due(p, today=None):
    """下次缴费日（过期自动按缴费频率滚动，内存态）；已缴清/失效或超缴费年限返回 None。"""
    if not paying(p):
        return None
    today = today or date.today()
    try:
        d = date.fromisoformat(p.get("下次缴费日", ""))
    except (ValueError, TypeError):
        return None
    # 缴费期终点：起保年份 + 缴费年限，超出即视为缴清。
    # 缴费年限留空/0 = 续保型（如一年期医疗/意外），永续摊月不判缴清。
    try:
        n_years = int(p["缴费年限"])
        end_year = (int(p["起保年份"]) + n_years) if n_years > 0 else None
    except (KeyError, ValueError, TypeError):
        end_year = None
    step = 12 // FREQ_PER_YEAR[freq_of(p)]
    while d < today:
        d = _add_months(d, step)
    if end_year is not None and d.year >= end_year:
        return None
    return d


def monthly_premium(p):
    """月摊保费(CNY)：仅缴费中。"""
    if not paying(p):
        return 0.0
    return float(p.get("年缴保费", 0)) / 12.0


def monthly_total(policies=None):
    if policies is None:
        policies = load_policies()
    return sum(monthly_premium(p) for p in policies)


def yearly_total(policies=None):
    if policies is None:
        policies = load_policies()
    return sum(float(p.get("年缴保费", 0)) for p in policies if paying(p))


def by_member(policies):
    """{成员: {"保单数", "年缴合计", "保额":{险种:合计保额}}}"""
    out = {}
    for p in policies:
        if p.get("状态") == "失效":
            continue
        m = p.get("成员", "未分组")
        g = out.setdefault(m, {"保单数": 0, "年缴合计": 0.0, "保额": {}})
        g["保单数"] += 1
        if paying(p):
            g["年缴合计"] += float(p.get("年缴保费", 0))
        k = p.get("险种", "其他")
        g["保额"][k] = g["保额"].get(k, 0) + float(p.get("保额", 0))
    return out


def enrich(policies, today=None):
    """供面板/编辑器展示：附 下次缴费日(滚动后)/月摊/单期应缴/频率/图标。"""
    today = today or date.today()
    items = []
    for p in policies:
        nd = next_due(p, today)
        items.append({**p,
                      "图标": KIND_ICON.get(p.get("险种", "其他"), "📄"),
                      "nextDue": nd.isoformat() if nd else None,
                      "monthly": round(monthly_premium(p)),
                      "频率": freq_of(p),
                      "perDue": round(per_due(p))})
    return items


def reminder_days(p):
    """提醒档位按频率：年/半年 金额大 30 天起；季/月 期次密 7 天起（避免月缴常年霸屏）。"""
    return (30, 7, 1) if freq_of(p) in ("年", "半年") else (7, 1)


def reminders(policies=None, today=None):
    """缴费提醒 [(emoji, msg)]；续保型附带核对新费率提示。"""
    if policies is None:
        policies = load_policies()
    today = today or date.today()
    out = []
    for p in policies:
        nd = next_due(p, today)
        if not nd:
            continue
        days = reminder_days(p)
        delta = (nd - today).days
        if delta > max(days):
            continue
        emoji = "🔴" if delta <= min(days) else ("🟠" if delta <= sorted(days)[len(days) // 2] else "🟡")
        extra = "（续保型：核对新费率并更新台账）" if is_renewable(p) else ""
        out.append((emoji, f'保险缴费 {p.get("成员","")}·{p.get("产品","")} '
                           f'{delta} 天后({nd.isoformat()})应缴 ¥{per_due(p):,.0f}'
                           f'/{freq_of(p)}{extra}'))
    return out


if __name__ == "__main__":
    today = date(2026, 7, 4)
    ps = [
        {"成员": "本人", "产品": "达尔文重疾", "险种": "重疾", "保额": 500000,
         "年缴保费": 8400, "下次缴费日": "2026-07-20", "缴费年限": 30,
         "起保年份": 2021, "状态": "缴费中"},
        {"成员": "配偶", "产品": "好医保", "险种": "医疗", "保额": 4000000,
         "年缴保费": 600, "下次缴费日": "2026-03-01", "缴费年限": 0,
         "起保年份": 2026, "状态": "缴费中"},
        {"成员": "本人", "产品": "某某增额寿", "险种": "增额寿", "保额": 0,
         "年缴保费": 100000, "下次缴费日": "2026-11-01", "缴费年限": 5,
         "起保年份": 2021, "状态": "已缴清"},
    ]
    # 摊月：只有缴费中的两单 (8400+600)/12=750
    assert abs(monthly_total(ps) - 750.0) < 0.01, monthly_total(ps)
    assert yearly_total(ps) == 9000
    # 已缴清不提醒不摊月
    assert next_due(ps[2], today) is None and monthly_premium(ps[2]) == 0
    # 医疗险为续保型(缴费年限0)：2026-03-01 已过 → 滚到 2027-03-01，持续摊月
    assert next_due(ps[1], today) == date(2027, 3, 1)
    # 重疾 7/20，16 天后 → 🟡30天档；医疗为续保型 → 提醒会带核对费率提示（但3/1不在窗口）
    r = reminders(ps, today)
    assert len(r) == 1 and r[0][0] == "🟡" and "达尔文" in r[0][1], r
    # 2/29 边界
    assert _add_years(date(2024, 2, 29), 1) == date(2025, 2, 28)
    assert _add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    bm = by_member(ps)
    assert bm["本人"]["保额"]["重疾"] == 500000 and bm["本人"]["保单数"] == 2
    # 缴费频率：月缴 → 缴费日按月滚动、单期=年缴/12、提醒档 7/1
    pm = {"成员": "配偶", "产品": "月缴重疾", "险种": "重疾", "保额": 300000,
          "年缴保费": 6000, "缴费频率": "月", "下次缴费日": "2026-06-10",
          "缴费年限": 20, "起保年份": 2026, "状态": "缴费中"}
    assert next_due(pm, today) == date(2026, 7, 10)      # 6/10 过 → 滚一个月
    assert per_due(pm) == 500 and reminder_days(pm) == (7, 1)
    assert abs(monthly_premium(pm) - 500.0) < 0.01        # 摊月始终=年缴/12，与频率无关
    r2 = reminders([pm], today)                           # 6 天后 → 命中 7 天档
    assert len(r2) == 1 and "¥500" in r2[0][1] and "/月" in r2[0][1], r2
    # 续保型提醒带费率核对提示
    p_renew = dict(ps[1], 下次缴费日="2026-07-08")
    r3 = reminders([p_renew], today)
    assert len(r3) == 1 and "续保型" in r3[0][1], r3
    print("✅ insurance 自测通过：摊月/频率滚动/缴清判定/提醒分级/续保提示/成员汇总/闰年边界")
