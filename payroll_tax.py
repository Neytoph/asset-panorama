#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""北京薪酬：五险一金个人扣缴 + 工资薪金累计预扣个税 → 当月实发估算。"""
import datetime

# 综合所得累计预扣税率表（年度应纳税所得额档位，按月累计）
_IIT_BRACKETS = (
    (36_000, 0.03, 0),
    (144_000, 0.10, 2_520),
    (300_000, 0.20, 16_920),
    (420_000, 0.25, 31_920),
    (660_000, 0.30, 52_920),
    (960_000, 0.35, 85_920),
    (float("inf"), 0.45, 181_920),
)

# 北京职工五险个人比例（约 2024–2025 口径）
_BEIJING_RATES = {
    "养老": 0.08,
    "医疗": 0.02,
    "失业": 0.005,
    "医疗固定": 3.0,  # 元/月（大病/互助等简化合并）
}

BEIJING_DEFAULTS = {
    "城市": "北京",
    "社保基数下限": 7162,
    "社保基数上限": 35283,
    "公积金基数下限": 2540,
    "公积金基数上限": 35283,
    "公积金比例": 0.12,
    "基本减除费用": 5000,  # 元/月
}


def current_tax_month():
    """累计预扣所用月份：始终为当前自然月。"""
    return datetime.date.today().month


def payroll_config(cf):
    """合并 cashflow.json 中的「薪酬计税」与默认参数。"""
    raw = cf.get("薪酬计税") or {}
    cfg = {**BEIJING_DEFAULTS, **raw}
    cfg["计税月份"] = current_tax_month()
    cfg["成员专项附加"] = raw.get("成员专项附加") or {}
    return cfg


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def social_fund_deduction(gross, cfg):
    """个人五险一金当月扣缴（北京）。"""
    sb = _clamp(gross, cfg["社保基数下限"], cfg["社保基数上限"])
    fb = _clamp(gross, cfg["公积金基数下限"], cfg["公积金基数上限"])
    fund_rate = float(cfg.get("公积金比例", 0.12))
    social = (
        sb * _BEIJING_RATES["养老"]
        + sb * _BEIJING_RATES["医疗"]
        + sb * _BEIJING_RATES["失业"]
        + _BEIJING_RATES["医疗固定"]
    )
    fund = fb * fund_rate
    return {
        "社保基数": round(sb, 2),
        "公积金基数": round(fb, 2),
        "社保个人": round(social, 2),
        "公积金个人": round(fund, 2),
        "五险一金合计": round(social + fund, 2),
    }


def _cumulative_iit(taxable):
    if taxable <= 0:
        return 0.0
    for limit, rate, quick in _IIT_BRACKETS:
        if taxable <= limit:
            return taxable * rate - quick
    return 0.0


def monthly_iit_withholding(gross, month, social_fund, special_deduction, basic=5000):
    """累计预扣法：当月应预扣个税。"""
    month = max(1, min(12, int(month)))
    sf = float(social_fund)
    spec = float(special_deduction)
    cum_taxable = gross * month - sf * month - basic * month - spec * month
    cum_tax = _cumulative_iit(cum_taxable)
    if month <= 1:
        return max(0.0, round(cum_tax, 2))
    prev_taxable = gross * (month - 1) - sf * (month - 1) - basic * (month - 1) - spec * (month - 1)
    prev_tax = _cumulative_iit(prev_taxable)
    return max(0.0, round(cum_tax - prev_tax, 2))


def estimate_salary_net(gross, cfg, member="", item_overrides=None):
    """
    估算当月工资实发。
    item_overrides: 行级覆盖 {专项附加扣除, 公积金比例}
    返回 dict：税前、五险一金、个税、实发及明细。
    """
    gross = float(gross)
    ov = item_overrides or {}
    cfg = {**cfg}
    if ov.get("公积金比例") is not None:
        cfg["公积金比例"] = float(ov["公积金比例"])
    if ov.get("公积金单位比例") is not None:
        cfg["公积金单位比例"] = float(ov["公积金单位比例"])
    sf = social_fund_deduction(gross, cfg)
    # 单位公积金：北京规定单位比例=个人比例，故默认取公积金比例；可用「公积金单位比例」覆盖。
    unit_rate = cfg.get("公积金单位比例")
    if unit_rate is None:
        unit_rate = cfg.get("公积金比例", 0.12)
    fund_unit = round(sf["公积金基数"] * float(unit_rate), 2)
    fund_arrive = round(sf["公积金个人"] + fund_unit, 2)   # 每月公积金账户到账 = 个人 + 单位
    member_spec = (cfg.get("成员专项附加") or {}).get(member, cfg.get("专项附加扣除", 0))
    special = float(ov.get("专项附加扣除", member_spec) or 0)
    iit = monthly_iit_withholding(
        gross, cfg["计税月份"], sf["五险一金合计"], special, cfg["基本减除费用"])
    net = gross - sf["五险一金合计"] - iit
    return {
        "税前": round(gross, 2),
        "社保个人": sf["社保个人"],
        "公积金个人": sf["公积金个人"],
        "公积金单位": fund_unit,
        "公积金到账": fund_arrive,
        "五险一金": sf["五险一金合计"],
        "个税": iit,
        "专项附加扣除": special,
        "计税月份": cfg["计税月份"],
        "实发": round(net, 2),
    }


def is_salary_item(item):
    """工资薪金走个税引擎；其他收入走固定比例。"""
    t = item.get("类型") or item.get("计税") or "工资"
    return t in ("工资", "工资薪金", "薪金")


def js_calc_snippet():
    """供编辑器内联的 JS 计算片段（与 Python 同口径）。"""
    return """
const IIT_BRACKETS=[[36000,.03,0],[144000,.10,2520],[300000,.20,16920],[420000,.25,31920],[660000,.30,52920],[960000,.35,85920],[1e12,.45,181920]];
function cumIIT(t){if(t<=0)return 0;for(const [lim,r,q] of IIT_BRACKETS)if(t<=lim)return t*r-q;return 0;}
function beijingSF(gross,cfg){
  const sb=Math.max(cfg.sbLo,Math.min(cfg.sbHi,gross));
  const fb=Math.max(cfg.fbLo,Math.min(cfg.fbHi,gross));
  const social=sb*(0.08+0.02+0.005)+3;
  const fund=fb*cfg.fundRate;
  return {social,fund,total:social+fund};
}
function monthIIT(gross,month,sf,spec,basic){
  const cumTaxable=gross*month-sf*month-basic*month-spec*month;
  const cum=cumIIT(cumTaxable);
  if(month<=1)return Math.max(0,cum);
  const prevTaxable=gross*(month-1)-sf*(month-1)-basic*(month-1)-spec*(month-1);
  return Math.max(0,cum-cumIIT(prevTaxable));
}
function estSalaryNet(gross,member,type,cfg){
  if(type!=='工资')return null;
  const spec=(cfg.memberSpec[member]??cfg.spec)||0;
  const sf=beijingSF(gross,cfg);
  const iit=monthIIT(gross,cfg.month,sf.total,spec,cfg.basic);
  const fb=Math.max(cfg.fbLo,Math.min(cfg.fbHi,gross));
  const unitRate=(cfg.fundUnitRate!=null?cfg.fundUnitRate:cfg.fundRate);
  const fundArrive=fb*(cfg.fundRate+unitRate);  // 公积金月到账=个人+单位
  return {gross, sf:sf.total, iit, net:gross-sf.total-iit, fundArrive};
}
"""
