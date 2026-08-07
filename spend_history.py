# -*- coding: utf-8 -*-
"""
支出构成历史：按月存「品类汇总」和「渠道覆盖」，供面板画构成与偏离归因。

与 cashflow_history 的分工：
· cashflow_history = 月度总量层（收入/固定/其他/结余/储蓄率），对账锁定的权威行。
· 本模块         = 「其他实际支出」那一块的**切分**（钱花在哪、从哪个口子出去）。
  切分永远只是对总量的再分配，导漏了只会让「未归类」变大，**总量不受影响**
  ——所以导账单是自愿的、增量的、可以偷懒的（同 bill_import.coverage() 的设计）。

只存月度汇总，**不存逐笔**：逐笔含商户名/人名/医疗信息，而 panorama.db 每天备份进
iCloud，隐私面积不划算；要查明细翻原始账单即可。

金额 CNY 正数。读写经 storage 统一存储层，写前自动备份上一版。
"""
import datetime

import storage

CAT_DATASET = "spend_categories"
CHAN_DATASET = "spend_channels"

CAT_FIELDS = ["月份", "品类", "刚性", "金额", "笔数"]
CHAN_FIELDS = ["月份", "层", "渠道", "金额", "笔数", "已导入"]

# 趋势/中位数类分析至少要这么多个已对账月份才有意义；不足时面板不渲染对应模块
MIN_MONTHS = 3


# ── 品类 ──
def load_categories(month=None):
    rows = []
    for r in storage.load_table(CAT_DATASET, []):
        if not r.get("月份"):
            continue
        if month and r["月份"] != month:
            continue
        rows.append({"月份": r["月份"], "品类": r.get("品类") or "未归类",
                     "刚性": r.get("刚性") or "未归类",
                     "金额": float(r.get("金额") or 0), "笔数": int(float(r.get("笔数") or 0))})
    rows.sort(key=lambda r: (r["月份"], -r["金额"]))
    return rows


def save_categories(month, rows):
    """覆盖某月的品类汇总。rows: [{品类, 刚性, 金额, 笔数}]"""
    keep = [r for r in load_categories() if r["月份"] != month]
    for r in rows:
        keep.append({"月份": month, "品类": r["品类"], "刚性": r.get("刚性") or "未归类",
                     "金额": float(r["金额"]), "笔数": int(r.get("笔数") or 0)})
    keep.sort(key=lambda r: (r["月份"], -r["金额"]))
    storage.save_table(CAT_DATASET, CAT_FIELDS, [
        {"月份": r["月份"], "品类": r["品类"], "刚性": r["刚性"],
         "金额": f"{r['金额']:.2f}", "笔数": str(r["笔数"])} for r in keep])
    return keep


def rigidity_split(month):
    """某月按刚性三档汇总 → {刚性: 金额}。刚性映射变了这里自动跟着变。"""
    import bill_import as bi
    rig = bi.rigidity_map()
    out = {}
    for r in load_categories(month):
        k = rig.get(r["品类"], r["刚性"])
        out[k] = out.get(k, 0.0) + r["金额"]
    return {k: round(v, 2) for k, v in out.items()}


# ── 渠道 ──
def load_channels(month=None):
    rows = []
    for r in storage.load_table(CHAN_DATASET, []):
        if not r.get("月份"):
            continue
        if month and r["月份"] != month:
            continue
        rows.append({"月份": r["月份"], "层": r.get("层") or "支付工具",
                     "渠道": r.get("渠道") or "其他",
                     "金额": float(r.get("金额") or 0), "笔数": int(float(r.get("笔数") or 0)),
                     "已导入": (r.get("已导入") or "是")})
    rows.sort(key=lambda r: (r["月份"], r["层"], -r["金额"]))
    return rows


def save_channels(month, rows):
    """覆盖某月的渠道记录。rows: [{层, 渠道, 金额, 笔数, 已导入}]"""
    keep = [r for r in load_channels() if r["月份"] != month]
    for r in rows:
        keep.append({"月份": month, "层": r.get("层") or "支付工具", "渠道": r["渠道"],
                     "金额": float(r.get("金额") or 0), "笔数": int(r.get("笔数") or 0),
                     "已导入": r.get("已导入") or "是"})
    keep.sort(key=lambda r: (r["月份"], r["层"], -r["金额"]))
    storage.save_table(CHAN_DATASET, CHAN_FIELDS, [
        {"月份": r["月份"], "层": r["层"], "渠道": r["渠道"],
         "金额": f"{r['金额']:.2f}", "笔数": str(r["笔数"]),
         "已导入": r["已导入"]} for r in keep])
    return keep


def coverage_gap(month):
    """某月未导入的渠道 → [渠道名]。用于「账单导全了没」自检。"""
    return [r["渠道"] for r in load_channels(month) if r["已导入"] != "是"]


# ── 中位数与偏离归因 ──
def _median(xs):
    s = sorted(xs)
    n = len(s)
    if not n:
        return None
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def other_median(months=MIN_MONTHS, exclude_month=None):
    """近 N 个**已对账**月份的「其他实际支出」中位数；不足 N 个返回 None。

    用中位数不用均值：一次性大额（本月买了台设备/一张演唱会门票）会把均值拉偏，
    中位数天然把它挤掉——这样就不需要每月人工标注「这笔算不算一次性」。
    """
    import cashflow_history as cfh
    vals = [r["其他实际支出"] for r in cfh.load_history()
            if r.get("已对账") == "是" and r["月份"] != exclude_month
            and r.get("其他实际支出")]
    if len(vals) < months:
        return None
    return _median(vals[-months:])


def deviation(month, months=MIN_MONTHS):
    """本月 vs 近 N 月中位数的偏离 + 主要贡献品类。数据不足返回 None。

    回答「这个月为什么花超了」而不需要任何人工标注：拿本月每个品类和该品类的历史
    中位数比，差得最多的几个就是原因。
    """
    import cashflow_history as cfh
    cur = next((r for r in cfh.load_history() if r["月份"] == month), None)
    if not cur or cur.get("已对账") != "是":
        return None
    med = other_median(months, exclude_month=month)
    if med is None:
        return None
    hist = {}
    for r in load_categories():
        if r["月份"] == month:
            continue
        hist.setdefault(r["品类"], []).append(r["金额"])
    contrib = []
    for r in load_categories(month):
        base = _median(hist.get(r["品类"], [])) or 0.0
        contrib.append({"品类": r["品类"], "金额": r["金额"],
                        "基线": round(base, 2), "偏离": round(r["金额"] - base, 2)})
    contrib.sort(key=lambda c: -abs(c["偏离"]))
    return {"月份": month, "本月": cur["其他实际支出"], "中位数": round(med, 2),
            "偏离": round(cur["其他实际支出"] - med, 2), "样本月数": months,
            "贡献": contrib[:5]}


def fi_gap(lifelong_month, swr, financial, reserve=0.0):
    """FI 分母漏项：现行「终身月支出」不含日常生活开销，这里把缺口算出来给面板标注。

    口径：日常开销取近 N 月中位数；不足 N 个月就用**最近一个已对账月**的实测值，
    并把 basis 标成 'single' 让面板说清楚这是单月样本。
    补进去用**全额**、不按刚性打折——"弹性"是可压缩不是归零，
    拿它调小 FI 分母等于假设退休后不吃饭（决策见 docs/monthly-review-2026-07.md）。
    """
    import cashflow_history as cfh
    med, basis = other_median(), "median"
    if med is None:
        done = [r for r in cfh.load_history() if r.get("已对账") == "是" and r.get("其他实际支出")]
        if not done:
            return None
        med, basis = done[-1]["其他实际支出"], "single"
    lifelong_month = float(lifelong_month or 0)
    swr = float(swr or 0.035) or 0.035
    cur_coast = lifelong_month * 12 / swr
    new_month = lifelong_month + med
    new_coast = new_month * 12 / swr
    fin = float(financial or 0)
    res = float(reserve or 0)
    return {
        "basis": basis, "dailyMonth": round(med, 2),
        "curMonth": round(lifelong_month), "newMonth": round(new_month),
        "curCoast": round(cur_coast), "newCoast": round(new_coast),
        "curProgress": (fin / cur_coast) if cur_coast else 0,
        "newProgress": (fin / new_coast) if new_coast else 0,
        "curFree": round(cur_coast + res), "newFree": round(new_coast + res),
        "curFreeProgress": (fin / (cur_coast + res)) if (cur_coast + res) else 0,
        "newFreeProgress": (fin / (new_coast + res)) if (new_coast + res) else 0,
        "applied": False,   # 攒满 MIN_MONTHS 后再正式接进分母；在此之前只标注不改数
    }


def reconciled_months():
    import cashflow_history as cfh
    return sum(1 for r in cfh.load_history() if r.get("已对账") == "是")


def enough_history(n=MIN_MONTHS):
    """趋势/中位数类模块的渲染开关：已对账月份够了才显示，不足时面板不画单点折线。"""
    return reconciled_months() >= n
