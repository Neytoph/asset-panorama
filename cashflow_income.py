#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""收入明细：工资走北京五险一金+累进个税；其他收入走固定比例。"""
import payroll_tax as pt


def income_pretax_mode(cf):
    return cf.get("收入明细口径", "税后") == "税前"


def _fmt_amt(v):
    v = float(v)
    return int(v) if v == int(v) else round(v, 2)


def _flat_ratio(cf):
    return cf.get("税后估算比例", 0.72) or 0.72


def _use_payroll(cf):
    mode = cf.get("收入计税模式", "薪酬")
    return mode != "固定比例"


def income_display_amount(item, cf):
    """编辑器表单：展示税前金额。"""
    amt = float(item.get("金额", 0))
    if income_pretax_mode(cf):
        return _fmt_amt(amt)
    ratio = _flat_ratio(cf)
    return _fmt_amt(amt / ratio) if ratio else amt


def _net_one_item(item, cf, cfg):
    """单行收入 → 税后金额与扣缴明细。"""
    pretax = float(item.get("金额", 0))
    if not income_pretax_mode(cf):
        return {"税前金额": None, "金额": pretax, "扣缴明细": None}

    if _use_payroll(cf) and pt.is_salary_item(item):
        detail = pt.estimate_salary_net(
            pretax, cfg, member=item.get("成员", ""))
        return {
            "税前金额": pretax,
            "金额": detail["实发"],
            "扣缴明细": detail,
            "计税方式": "工资薪金",
        }
    ratio = _flat_ratio(cf)
    net = round(pretax * ratio, 2)
    return {
        "税前金额": pretax,
        "金额": net,
        "扣缴明细": {"税前": pretax, "实发": net, "计税方式": "固定比例"},
        "计税方式": "固定比例",
    }


def _fund_as_income(cf):
    return bool((cf.get("薪酬计税") or {}).get("公积金计入收入"))


def income_items_net(cf):
    """收入明细行，金额统一为当月预计税后实发。
    若开启「公积金计入收入」，为每条工资额外追加一行「公积金到账」(=个人+单位，免税、不进个税引擎)，
    因个人公积金已在实发中扣除，这样 总可支配 = 实发 + 公积金到账，账才平。
    仅当公积金可自由取出时才应开启该开关。"""
    raw = cf.get("收入明细", [])
    if not raw:
        return []
    cfg = pt.payroll_config(cf)
    fund_in = _fund_as_income(cf) and income_pretax_mode(cf) and _use_payroll(cf)
    out = []
    for i in raw:
        row = {**i, **_net_one_item(i, cf, cfg)}
        out.append(row)
        if fund_in and pt.is_salary_item(i):
            arrive = (row.get("扣缴明细") or {}).get("公积金到账")
            if arrive:
                out.append({
                    "成员": i.get("成员", ""),
                    "项目": "公积金到账",
                    "类型": "公积金",
                    "税前金额": None,
                    "金额": round(float(arrive), 2),
                    "扣缴明细": None,
                    "计税方式": "公积金(免税·可取出)",
                })
    return out


def income_net_of(cf):
    items = income_items_net(cf)
    if items:
        return round(sum(i["金额"] for i in items), 2)
    ratio = _flat_ratio(cf)
    return cf.get("税后月收入") or cf.get("月收入税前", 0) * ratio


def income_summary(cf):
    """汇总税前、五险一金、个税、实发（供编辑器预览）。"""
    items = income_items_net(cf)
    if not items:
        return None
    pretax = sum(i.get("税前金额") or 0 for i in items)
    net = sum(i["金额"] for i in items)
    sf = sum((i.get("扣缴明细") or {}).get("五险一金", 0) for i in items)
    iit = sum((i.get("扣缴明细") or {}).get("个税", 0) for i in items)
    cfg = pt.payroll_config(cf)
    return {
        "税前合计": round(pretax, 2),
        "五险一金": round(sf, 2),
        "个税": round(iit, 2),
        "实发合计": round(net, 2),
        "计税月份": cfg["计税月份"],
    }
