# -*- coding: utf-8 -*-
"""
跨模块不变量测试（各模块自身的自测在其 __main__ 里，由 run_tests.sh 串跑）。
覆盖：对账锁定不变量、确认口径、补账历史月口径、公积金收入行、编辑器表单往返。
只用临时文件，不碰真实数据。
"""
import copy
import datetime
import json
import os
from pathlib import Path

BASE = Path(__file__).resolve().parent
PASS = []


def check(name, fn):
    fn()
    PASS.append(name)
    print(f"  ✅ {name}")


def _tmp_cfh():
    """经 storage 测试钩子把 cashflow_history 指到临时文件，返回清理函数。"""
    import storage
    import cashflow_history as cfh
    storage._FORCE_BACKEND = "file"
    storage.PATH_OVERRIDE["cashflow_history"] = BASE / "_tests_cfh.csv"

    def cleanup():
        for p in (BASE / "_tests_cfh.csv", BASE / "_tests_cfh.bak.csv"):
            if p.exists():
                os.remove(p)
        storage._FORCE_BACKEND = None
        storage.PATH_OVERRIDE.pop("cashflow_history", None)
    return cfh, cleanup


D_BASE = {"income": 74571, "fixedOut": 38641, "netCashflow": 35930,
          "savingsRate": 0.4818, "dca": {"month": 28744},
          "passiveMonth": 4020, "subsMonthly": 635}


def test_recon_lock():
    """核心不变量：已对账月拒绝被自动草稿覆盖；草稿月正常刷新。"""
    cfh, cleanup = _tmp_cfh()
    try:
        cfh.record_provisional(D_BASE, "2026-07")
        cfh.confirm_month("2026-07", 74571, 9500, 28744, 4020, 635, 38641, "t")
        cfh.record_provisional(dict(D_BASE, income=99999), "2026-07")
        h = {r["月份"]: r for r in cfh.load_history()}
        assert h["2026-07"]["税后收入"] == 74571 and h["2026-07"]["其他实际支出"] == 9500
        cfh.record_provisional(D_BASE, "2026-08")
        cfh.record_provisional(dict(D_BASE, income=80000), "2026-08")
        h = {r["月份"]: r for r in cfh.load_history()}
        assert h["2026-08"]["税后收入"] == 80000 and h["2026-08"]["已对账"] == "否"
    finally:
        cleanup()


def test_confirm_math():
    """确认口径：净结余 = 收入 − 固定支出 − 其他实际支出，储蓄率随之。"""
    cfh, cleanup = _tmp_cfh()
    try:
        row = cfh.confirm_month("2026-07", 60000, 8000, 0, 0, 0, 30000, "")
        assert row["净结余"] == 22000 and abs(row["储蓄率"] - 22000 / 60000) < 1e-3
        assert row["已对账"] == "是" and row["对账日"]
    finally:
        cleanup()


def test_recon_backfill_uses_frozen():
    """补账历史月：用该月草稿定格的固定支出/订阅数，而非当前配置。"""
    import cashflow_editor as ce
    cfh, cleanup = _tmp_cfh()
    try:
        cfh.record_provisional(dict(D_BASE, fixedOut=30000, subsMonthly=500), "2026-01")
        row = ce.parse_recon_save({"recon_month": ["2026-01"], "recon_income": ["70000"],
                                   "recon_other": ["8000"], "recon_note": ["补账"]})
        assert row["固定支出"] == 30000 and row["订阅月支"] == 500
        assert row["净结余"] == 70000 - 30000 - 8000
    finally:
        cleanup()


def test_storage_backends_equivalent():
    """file/sqlite 两后端对同一数据集读写完全等价（含备份与数值转字符串）。"""
    import storage
    orig_db = storage.DB_PATH
    storage.DB_PATH = BASE / "_tests_storage.db"
    storage.PATH_OVERRIDE["_t_doc"] = BASE / "_t_doc.json"
    storage.PATH_OVERRIDE["_t_tab"] = BASE / "_t_tab.csv"
    doc = {"名称": "测试", "嵌套": {"x": [1, 2.5, None]}, "开关": True}
    fields = ["date", "金额", "备注"]
    rows = [{"date": "2026-07-01", "金额": 123, "备注": "a,b\"引号\""},
            {"date": "2026-07-02", "金额": None}]
    want = [{"date": "2026-07-01", "金额": "123", "备注": 'a,b"引号"'},
            {"date": "2026-07-02", "金额": "", "备注": ""}]
    try:
        for bk in ("file", "sqlite"):
            storage._FORCE_BACKEND = bk
            assert not storage.doc_exists("_t_doc") or bk == "sqlite"
            storage.save_doc("_t_doc", doc)
            assert storage.load_doc("_t_doc") == doc
            storage.save_table("_t_tab", fields, rows)
            assert storage.load_table("_t_tab") == want, bk
            assert storage.doc_exists("_t_doc") and storage.table_exists("_t_tab")
            storage.save_doc("_t_doc", {"v": 2})           # 触发备份
            assert storage.load_doc("_t_doc") == {"v": 2}
        # file 后端的 .bak 双备份落盘且是上一版
        assert json.loads((BASE / "_t_doc.bak.json").read_text(encoding="utf-8")) == doc
        # 缺失数据集：default=None 抛 FileNotFoundError，否则返回 default
        try:
            storage.load_table("_t_missing")
            assert False
        except FileNotFoundError:
            pass
        assert storage.load_table("_t_missing", []) == []
    finally:
        storage._FORCE_BACKEND = None
        storage.DB_PATH = orig_db
        for n in ("_t_doc", "_t_tab"):
            storage.PATH_OVERRIDE.pop(n, None)
        for p in (BASE / "_t_doc.json", BASE / "_t_doc.bak.json",
                  BASE / "_t_tab.csv", BASE / "_t_tab.bak.csv",
                  BASE / "_tests_storage.db"):
            if p.exists():
                os.remove(p)


def test_fund_income_rows():
    """公积金计入收入：每条工资生成一行到账(=个人+单位)，总收入差额=公积金合计。"""
    import cashflow_income as inc
    import storage
    cf = storage.load_doc("cashflow", {})
    cf_on = copy.deepcopy(cf)
    cf_on["薪酬计税"]["公积金计入收入"] = True
    cf_off = copy.deepcopy(cf)
    cf_off["薪酬计税"]["公积金计入收入"] = False
    rows = inc.income_items_net(cf_on)
    fund_rows = [r for r in rows if r["项目"] == "公积金到账"]
    n_salary = sum(1 for r in cf["收入明细"] if (r.get("类型") or "工资") == "工资")
    assert len(fund_rows) == n_salary
    diff = inc.income_net_of(cf_on) - inc.income_net_of(cf_off)
    assert abs(diff - sum(r["金额"] for r in fund_rows)) < 0.01


def test_fixed_out_chokepoint():
    """fixed_out 唯一聚合点：手动负项 + 订阅 + 保险 三者都进来。"""
    import subscriptions as subs
    import insurance
    from portfolio_tracker import FX_FALLBACK
    cf = {"月度收支": [{"项目": "a", "金额": -1000.0}, {"项目": "b", "金额": 200.0}]}
    total = subs.cashflow_fixed_out(cf, FX_FALLBACK)
    expect = 1000 + subs.monthly_total(subs.load_subs(), FX_FALLBACK) + insurance.monthly_total()
    assert abs(total - expect) < 0.01


def test_editor_income_roundtrip():
    """编辑器 parse_save：收入行(成员/类型)与公积金开关正确持久化。"""
    import cashflow_editor as ce
    fields = {"income_pretax": ["0"], "income_aftertax": [""], "tax_ratio": ["0.72"],
              "pay_fund_rate": ["0.12"], "pay_fund_income": ["1"],
              "pay_spec_self": ["0"], "pay_spec_spouse": ["0"],
              "income_member": ["本人"], "income_name": ["工资"],
              "income_type": ["工资"], "income_amt": ["65000"],
              "flow_name": ["房租"], "flow_amt": ["19500"],
              "dca_mode": ["按结余比例"], "dca_ratio": ["0.8"], "dca_fixed": ["8000"],
              "dca_freq": ["每周"], "dca_name": ["沪深300ETF"], "dca_code": ["510300"],
              "prop_rent": ["0.025"], "yld_zhaohang": ["0.015"], "yld_changqian": ["0.03"],
              "yld_haiwai": ["0.04"], "yld_cash": ["0.018"], "yld_zenge": ["0.035"]}
    out = ce.parse_save(fields)
    assert out["薪酬计税"]["公积金计入收入"] is True
    assert out["收入明细"][0]["成员"] == "本人" and out["收入明细"][0]["金额"] == 65000
    fields["pay_fund_income"] = ["0"]
    assert ce.parse_save(fields)["薪酬计税"]["公积金计入收入"] is False


def test_insurance_parse_roundtrip():
    """保险表单平行列 zip 往返（含缴费频率/保障至）。"""
    import cashflow_editor as ce
    fields = {"ins_member": ["本人"], "ins_product": ["测试重疾"], "ins_kind": ["重疾"],
              "ins_amount": ["500000"], "ins_premium": ["8000"], "ins_freq": ["月"],
              "ins_next": ["2027-01-10"], "ins_years": ["30"], "ins_start": ["2026"],
              "ins_till": ["终身"], "ins_status": ["缴费中"], "ins_note": [""]}
    out = ce.parse_insurance_save(fields)
    assert len(out) == 1 and out[0]["年缴保费"] == 8000 and out[0]["缴费年限"] == 30
    assert out[0]["缴费频率"] == "月" and out[0]["保障至"] == "终身"
    fields["ins_freq"] = [""]     # 旧数据无频率 → 默认年缴
    assert ce.parse_insurance_save(fields)[0]["缴费频率"] == "年"


def test_ledger_debt_accounts():
    """净资产负债来源=负债台账：账户行取推演余额（负值），已清/零额不出行。"""
    import datetime
    import storage
    import portfolio_tracker as pt
    storage._FORCE_BACKEND = "file"
    storage.PATH_OVERRIDE["loans"] = BASE / "_tests_loans.json"
    try:
        storage.save_doc("loans", {"负债": [
            {"名称": "测试贷", "类型": "消费贷", "基准本金": 120000, "基准年月": "2026-05",
             "年利率": 0, "月供": 10000, "还款日": 1, "状态": "在还"},
            {"名称": "已清贷", "类型": "车贷", "基准本金": 50000, "基准年月": "2026-01",
             "年利率": 0, "月供": 1000, "还款日": 1, "状态": "已清"},
        ]}, backup=False)
        out = pt.ledger_debt_accounts(datetime.date(2026, 7, 4))
        assert len(out) == 1 and out[0]["class"] == "负债"
        assert out[0]["value"] == -(120000 - 2 * 10000)   # 6/1、7/1 两期已还
    finally:
        storage._FORCE_BACKEND = None
        storage.PATH_OVERRIDE.pop("loans", None)
        for p in (BASE / "_tests_loans.json", BASE / "_tests_loans.bak.json"):
            if p.exists():
                os.remove(p)


def test_platform_summary():
    """账户/平台汇总：持仓按账户列分组、accounts 并入、房产/负债剔除、降序。"""
    from panorama_data import platform_summary
    holdings = [{"账户": "A股券商", "value": 100.0}, {"账户": "海外券商", "value": 300.0},
                {"账户": "", "value": 7.0}]
    accounts = [{"name": "招行理财", "class": "债券类固收", "value": 50.0},
                {"name": "房产", "class": "房产", "value": 999.0},
                {"name": "房贷", "class": "负债", "value": -888.0}]
    ps = platform_summary(holdings, accounts)
    assert ps[0] == ("海外券商", 300.0) and ("未标注账户", 7.0) in ps
    assert all(p not in ("房产", "房贷") for p, _ in ps)


def test_cost_basis():
    """持仓成本：期初/买入记正、卖出记负、币种换算、空成交额跳过、权利金降成本。"""
    from panorama_data import cost_basis
    fx = {"USD": 7.0, "HKD": 0.9, "CNY": 1.0}
    rows = [
        {"名称": "甲", "动作": "期初", "成交额": "1000", "成交币种": "CNY"},
        {"名称": "甲", "动作": "买入", "成交额": "500", "成交币种": "CNY"},
        {"名称": "甲", "动作": "卖出", "成交额": "300", "成交币种": "CNY"},
        {"名称": "乙", "动作": "期初", "成交额": "100", "成交币种": "USD"},
        {"名称": "丙", "动作": "期初", "成交额": "", "成交币种": "CNY"},       # 空→跳过
        {"名称": "丁CALL", "动作": "期初", "成交额": "-50", "成交币种": "USD"},  # 权利金
    ]
    c = cost_basis(rows, fx)
    assert c["甲"] == 1200.0                 # 1000+500-300
    assert c["乙"] == 700.0                  # 100×7
    assert "丙" not in c                     # 无有效成交额不产生成本
    assert c["丁CALL"] == -350.0             # 收到权利金 → 负成本
    assert cost_basis([], fx) == {}


def test_loans_parse_roundtrip():
    """负债表单往返：利率按%录入存小数、空利率保留为空(不推演)。"""
    import cashflow_editor as ce
    fields = {"loan_name": ["房贷", "车贷"], "loan_type": ["公积金贷", "车贷"],
              "loan_base": ["996841.29", "83800"], "loan_baseym": ["2026-07", ""],
              "loan_rate": ["2.6", ""], "loan_pmt": ["4617", "3491.67"],
              "loan_payday": ["1", "1"], "loan_status": ["在还", "在还"],
              "loan_note": ["", ""], "loan_alert": ["卖房即结清·通道消失", ""]}
    out = ce.parse_loans_save(fields)
    assert len(out) == 2
    assert abs(out[0]["年利率"] - 0.026) < 1e-9 and out[0]["基准本金"] == 996841.29
    assert out[1]["年利率"] == "" and out[1]["基准年月"] == ""
    assert out[0]["提醒"].startswith("卖房") and out[1]["提醒"] == ""


def test_holdings_diff_trades():
    """持仓差异记账：增量=买入、减量/删行=卖出、不变不记；价取用户价→行情缓存→留空+警告。"""
    from holdings_manager import diff_trades
    old = [{"名称": "甲", "市场": "美股", "持有数量": "100", "腾讯查询代码": "usX"},
           {"名称": "乙", "市场": "沪市", "持有数量": "50", "腾讯查询代码": "sh1"},
           {"名称": "丙", "市场": "美股", "持有数量": "10", "腾讯查询代码": "usY"}]
    new = [{"名称": "甲", "市场": "美股", "持有数量": "120", "腾讯查询代码": "usX"},
           {"名称": "乙", "市场": "沪市", "持有数量": "50", "腾讯查询代码": "sh1"},
           {"名称": "丁", "市场": "沪市", "持有数量": "5", "腾讯查询代码": ""}]
    quotes = {"usY": {"price": 2.0}}
    recs, warns = diff_trades(old, new, {"甲": "10"}, quotes, today="2026-07-06",
                              note="迁移沪深300；第2批")
    by = {r["名称"]: r for r in recs}
    assert len(recs) == 3 and "乙" not in by                      # 不变不记
    assert all(r["原因/备注"] == "迁移沪深300；第2批" for r in recs)   # 原因进备注
    assert by["甲"]["动作"] == "买入" and by["甲"]["数量"] == "20"
    assert by["甲"]["成交额"] == "200" and by["甲"]["成交币种"] == "USD"  # 用户价优先
    assert by["丙"]["动作"] == "卖出" and by["丙"]["成交额"] == "20"      # 删行=全卖,价取行情缓存
    assert by["丁"]["动作"] == "买入" and by["丁"]["成交额"] == ""        # 无价→留空
    assert any("丁" in w for w in warns)
    assert diff_trades(old, old, {}, quotes) == ([], [])


def test_holdings_parse_roundtrip():
    """持仓管理表单往返：空名跳过、重名保首个、成交价/手动值/新账户各归各位。"""
    import cashflow_editor as ce
    n = ["沪深300ETF", "", "沪深300ETF", "新股"]
    fields = {"h_name": n, "h_qty": ["47400", "1", "9", "100"], "h_px": ["", "", "", "3.5"],
              "h_acct": ["海通", "", "", "富途"], "h_code": ["510300", "", "", "XYZ"],
              "h_mkt": ["沪市", "", "", "美股"], "h_type": ["A股权益ETF", "", "", "美股个股"],
              "h_sina": ["sh510300", "", "", ""], "h_tx": ["sh510300", "", "", "usXYZ"],
              "h_em": ["1.510300", "", "", ""], "h_liq": ["数日", "", "", ""], "h_div": ["2.5", "", "", ""],
              "m_name": ["长钱账户", "房产"], "m_value": ["700000", ""],
              "a_name": ["新存单"], "a_type": ["固收理财"], "a_value": ["100000"],
              "a_liq": ["锁定"], "a_note": ["2027到期"],
              "t_reason": ["定投"], "t_note": ["第27周"]}
    rows, prices, manual, acct, note = ce.parse_holdings_save(fields)
    assert [r["名称"] for r in rows] == ["沪深300ETF", "新股"]
    assert rows[0]["持有数量"] == "47400" and rows[1]["流动性"] == "数日"
    assert prices == {"新股": "3.5"}
    assert manual == {"长钱账户": "700000"}          # 空值不更新
    assert acct == ("新存单", "固收理财", "100000", "锁定", "2027到期")
    assert note == "定投；第27周"


def test_ledger_qty_check():
    """台账推演 vs 实际数量：一致不报、缺笔/清仓未销户都要揭发。"""
    from panorama_data import ledger_qty_check
    hist = [{"名称": "甲", "动作": "期初", "数量": "100"},
            {"名称": "甲", "动作": "买入", "数量": "20"},
            {"名称": "乙", "动作": "期初", "数量": "50"},
            {"名称": "丙", "动作": "期初", "数量": "10"},
            {"名称": "丙", "动作": "卖出", "数量": "10"},   # 清仓且已从持仓移除 → 一致
            {"名称": "空行", "动作": "期初", "数量": ""}]    # 非数跳过
    ok = [{"name": "甲", "qty": 120.0}, {"name": "乙", "qty": 50.0}]
    assert ledger_qty_check(ok, hist) == []
    # 甲实际 130(有 10 份没记账)、丁完全没台账 → 各报一条
    bad = [{"name": "甲", "qty": 130.0}, {"name": "乙", "qty": 50.0}, {"name": "丁", "qty": 5.0}]
    warns = ledger_qty_check(bad, hist)
    assert len(warns) == 2
    assert any("甲" in w and "+10" in w for w in warns)
    assert any("丁" in w for w in warns)


def test_xirr():
    """XIRR 基准：一年翻 1.1 倍=10%；半年 +5% 年化≈10.25%；无正负混合返回 None。"""
    from metrics import xirr
    r = xirr([("2025-01-01", -1000), ("2026-01-01", 1100)])
    assert abs(r - 0.10) < 1e-3, r
    r = xirr([("2025-01-01", -1000), ("2025-07-02", 1050)])
    assert abs(r - (1.05 ** (365 / 182.0) - 1)) < 1e-3, r
    assert xirr([("2025-01-01", -1000), ("2025-06-01", -500)]) is None
    assert xirr([("2025-01-01", -1000)]) is None


def test_portfolio_perf():
    """组合口径：两笔投入+当前市值 → 累计=浮盈/净投入；大类归口黄金/权益。"""
    from metrics import portfolio_perf
    ledger = [
        {"日期": "2025-01-01", "动作": "期初", "名称": "甲ETF", "资产类型": "A股权益ETF",
         "成交额": "10000", "成交币种": "CNY"},
        {"日期": "2025-06-01", "动作": "买入", "名称": "甲ETF", "资产类型": "A股权益ETF",
         "成交额": "5000", "成交币种": "CNY"},
        {"日期": "2025-03-01", "动作": "期初", "名称": "金ETF", "资产类型": "黄金ETF",
         "成交额": "8000", "成交币种": "CNY"},
    ]
    holdings = [{"name": "甲ETF", "value": 16500.0}, {"name": "金ETF", "value": 8800.0}]
    p = portfolio_perf(ledger, holdings, {"CNY": 1.0}, asof="2026-01-01")
    assert p["total"]["invested"] == 23000
    assert abs(p["total"]["cum"] - 2300 / 23000) < 1e-9
    assert set(p["byClass"]) == {"权益", "黄金"}
    assert p["byClass"]["黄金"]["pnl"] == 800
    assert p["total"]["xirr"] > 0
    # 台账有但持仓已清 → 不计入；全部没有交集 → total=None
    assert portfolio_perf(ledger, [], {"CNY": 1.0})["total"] is None


def test_life_goals_roundtrip():
    """人生目标表单往返：空名整行丢弃、关联中英逗号皆拆、问题按行拆空行剔除。"""
    import cashflow_editor as ce
    form = {"lg_name": ["孩子培养", "  ", "资产增值"],
            "lg_story": ["教育钱提前到位", "x", "纪律换复利"],
            "lg_links": ["换房,育儿储备", "x", "定投，增长归因"],
            "lg_qs": ["今年陪孩子做成了什么?\n\n明年最重要的投入?", "x", "最好的一笔决定?"],
            # parse_goal 还会读 FI/换房字段,给空值走默认
            "swr": [""], "hx_on": ["0"]}
    g = ce.parse_goal(form)
    lgs = g["人生目标"]
    assert [x["名称"] for x in lgs] == ["孩子培养", "资产增值"]      # 空名行丢弃
    assert lgs[0]["关联"] == ["换房", "育儿储备"]
    assert lgs[1]["关联"] == ["定投", "增长归因"]                    # 中文逗号也拆
    assert lgs[0]["年度问题"] == ["今年陪孩子做成了什么?", "明年最重要的投入?"]  # 空行剔除


def test_attribution():
    """归因三块精确闭合：ΔNW = 储蓄 + 投资(推算) + 房产净值变动。"""
    from metrics import attribution
    hist = [{"date": "2026-06-24", "总净资产": "8470553", "金融资产": "3870553", "房产": "4600000"},
            {"date": "2026-07-11", "总净资产": "8600000", "金融资产": "3980000", "房产": "4620000"}]
    cfh = [{"月份": "2026-07", "净结余": "32732", "已对账": "否"},
           {"月份": "2026-05", "净结余": "99999", "已对账": "是"}]   # 期间外,不计
    a = attribution(hist, cfh)
    assert a["savings"] == 32732 and a["draft"] is True
    assert a["deltaNW"] == a["savings"] + a["invest"] + a["property"]
    assert a["property"] == 20000
    assert attribution(hist[:1], cfh) is None
    # 逐月切片:每月留月末行、相对上月末闭合;首个快照月无基线不出结果
    from metrics import attribution_monthly
    hist2 = hist + [{"date": "2026-07-31", "总净资产": "8700000", "金融资产": "4050000", "房产": "4650000"},
                    {"date": "2026-08-15", "总净资产": "8750000", "金融资产": "4100000", "房产": "4650000"}]
    m = attribution_monthly(hist2, cfh + [{"月份": "2026-08", "净结余": "30000", "已对账": "是"}])
    assert [x["month"] for x in m] == ["2026-07", "2026-08"]
    assert m[0]["savings"] == 32732 and m[0]["draft"] is True      # 7月取月末行(31日)
    assert m[0]["property"] == 50000 and m[1]["draft"] is False
    for x in m:
        assert x["deltaNW"] == x["savings"] + x["invest"] + x["property"]
    assert attribution_monthly(hist[:1], cfh) is None


def test_fi_plan():
    """FI 线与到达年限：r=0 退化为线性；已达标 years=0。"""
    from metrics import fi_plan
    f = fi_plan(financial=1_000_000, fixed_out=40000, monthly_saving=30000,
                cfg={"提取率": 0.04, "实际回报情景": [0.0, 0.04]})
    assert f["number"] == 12_000_000
    y0 = f["scenarios"][0]["years"]
    assert abs(y0 - (12_000_000 - 1_000_000) / 30000 / 12) < 0.1
    assert f["scenarios"][1]["years"] < y0          # 有回报更快
    f2 = fi_plan(financial=13_000_000, fixed_out=40000, monthly_saving=0,
                 cfg={"提取率": 0.04})
    assert all(s["years"] == 0.0 for s in f2["scenarios"])


def test_rebalance_plan():
    """5/25 判定 + 定投定向：低配类按缺口比例分配,房产不可调。"""
    from metrics import rebalance_plan
    classes = {"房产": 5_500_000, "权益": 2_400_000, "债券类固收": 1_300_000,
               "现金": 700_000, "黄金": 100_000}
    target = {"房产": 0.45, "权益": 0.34, "债券类固收": 0.13, "现金": 0.04, "黄金": 0.04}
    nw = 10_000_000
    r = rebalance_plan(classes, target, nw, dca_month=30000)
    rows = {x["cls"]: x for x in r["rows"]}
    assert rows["房产"]["act"] and not rows["房产"]["adjustable"]      # 55% vs 45%
    assert rows["权益"]["act"] and rows["权益"]["gap"] == 1_000_000    # 24% vs 34%
    assert rows["黄金"]["act"]                                        # 1% vs 4%,相对偏 75%
    assert not rows["债券类固收"]["act"]                               # 正中目标
    alloc = r["plan"]["alloc"]
    assert set(alloc) == {"权益", "黄金"}
    assert abs(alloc["权益"] / alloc["黄金"] - 1_000_000 / 300_000) < 0.05
    assert abs(r["plan"]["months"] - 1_300_000 / 30000) < 0.1
    assert rebalance_plan(classes, target, 0, 30000) is None


def test_stress_test():
    """压力测试：ΔNW 与杠杆放大方向正确;房产打折按估值口径。"""
    from metrics import stress_test
    r = stress_test(classes={"权益": 1_000_000, "房产": 4_500_000},
                    prop_gross=5_500_000, total_debt=1_000_000,
                    gross_assets=7_500_000, ccy={"USD": 500_000}, networth=6_500_000)
    by = {x["name"]: x for x in r}
    assert by["权益 −30%"]["dNW"] == -300_000
    assert by["房产估值 −20%"]["dNW"] == -1_100_000
    assert by["美元资产 −10%"]["dNW"] == -50_000
    assert by["危机组合(权益−30%+房产−20%)"]["dNW"] == -1_400_000
    # 冲击后杠杆率上升(分母缩小)
    assert by["房产估值 −20%"]["levAfter"] > 1_000_000 / 7_500_000
    assert stress_test({}, 0, 0, 0, {}, 0) is None


def test_insurance_gap():
    """保险缺口：无寿险保单时缺口=全部需求;重疾按成员收入倍数。"""
    from metrics import insurance_gap
    income = [{"成员": "本人", "金额": 50000}, {"成员": "本人", "金额": 8000},
              {"成员": "配偶", "金额": 16000}]
    ins = {"本人": {"保额": {"重疾": 940_000}},
           "配偶": {"保额": {"重疾": 1_140_000}},
           "孩子": {"保额": {"重疾": 700_000, "增额寿": 22_365}}}
    g = insurance_gap(income, ins, total_debt=1_000_000, fixed_out=40_000,
                      cfg={"寿险支出年数": 10, "重疾收入倍数": 3})
    assert g["life"]["need"] == 1_000_000 + 40_000 * 12 * 10
    assert g["life"]["have"] == 0 and g["life"]["gap"] == g["life"]["need"]
    ci = {x["member"]: x for x in g["ci"]}
    assert ci["本人"]["need"] == 58000 * 36
    assert ci["本人"]["gap"] == 58000 * 36 - 940_000
    assert ci["配偶"]["gap"] == max(0, 16000 * 36 - 1_140_000)  # 保额充足 → 0
    assert "孩子" not in ci                                     # 无收入不算重疾收入缺口


def test_bill_coverage():
    """支出去向是对总量的切分:漏导账单只让未归类变大,总量不变。"""
    from bill_import import coverage
    c = coverage(22400, 15800)
    assert c["total"] == 22400 and c["classified"] == 15800
    assert c["unclassified"] == 6600
    assert abs(c["rate"] - 15800 / 22400) < 1e-9
    zero = coverage(22400, 0)                 # 一条没导 → 归类率 0,但总量照旧
    assert zero["total"] == 22400 and zero["unclassified"] == 22400
    over = coverage(10000, 12000)             # 导入超过总量 → 截断,不产生负的未归类
    assert over["classified"] == 10000 and over["unclassified"] == 0


def test_lifelong_out():
    """终身支出口径:有终点的项(房贷/幼儿园)不进 FI 线分母,但被单列出来。"""
    from metrics import lifelong_out
    items = [{"项目": "房租", "金额": -19500, "终身": True},
             {"项目": "房贷月供", "金额": -4800, "终身": False},
             {"项目": "幼儿园", "金额": -10000, "终身": False},
             {"项目": "话费", "金额": -206}]              # 缺字段 → 默认终身
    m, ending = lifelong_out(items, subs_monthly=635, ins_monthly=4077)
    assert m == 19500 + 206 + 635 + 4077
    assert {e["item"] for e in ending} == {"房贷月供", "幼儿园"}
    assert sum(e["amt"] for e in ending) == 14800


def test_event_ladder():
    """事件阶梯:月储蓄随事件变化,一次性注入独立;已过期的事件不再影响未来。"""
    from metrics import event_ladder
    today = datetime.date(2026, 7, 14)
    events = [
        {"名称": "增额寿缴清", "日期": "2028-11", "月度影响": {"保费": 8333}},
        {"名称": "换房", "日期": "2029-09", "一次性": 2_706_000, "月度影响": {"房贷": 4800}},
        {"名称": "兴趣班", "日期": "2029-09", "月度影响区间": {"lo": 2000, "mid": 0, "hi": -2000}},
        {"名称": "已过去的事", "日期": "2025-01", "月度影响": {"x": 99999}},   # 忽略
    ]
    lad = event_ladder(events, 32_732, today, "mid")
    assert lad[0] == (0, 32_732, 0.0, None)
    assert lad[1][0] == 28 and lad[1][1] == 32_732 + 8333        # 增额寿
    assert lad[2][2] == 2_706_000                                 # 一次性注入
    assert lad[-1][1] == 32_732 + 8333 + 4800 + 0                 # mid 区间 = 0
    lo = event_ladder(events, 32_732, today, "lo")
    hi = event_ladder(events, 32_732, today, "hi")
    assert lo[-1][1] > hi[-1][1]                                  # 兴趣班便宜 → 存得多
    assert not any(s[3] == "已过去的事" for s in lad)


def test_childcare_reserve():
    """育儿储备:FI 线剔除了育儿,但孩子成年前仍要供 —— 它是一笔有限期负债的现值。"""
    from metrics import childcare_reserve
    today = datetime.date(2026, 9, 1)
    ev = [{"类型": "育儿", "结束": "2036-09", "月额区间": {"lo": 8000, "mid": 10000, "hi": 12000}}]
    mid = childcare_reserve(ev, 0.04, today, "mid")
    lo = childcare_reserve(ev, 0.04, today, "lo")
    hi = childcare_reserve(ev, 0.04, today, "hi")
    assert lo < mid < hi
    assert 0 < mid < 10000 * 120                    # 现值必然小于名义总额(有折现)
    assert abs(mid - 10000 * 120 * 0.83) < 10000 * 120 * 0.1   # 10年@4% 折现约 83%
    assert childcare_reserve([], 0.04, today) == 0


def test_fi_three_numbers():
    """FI 需要三个数字:Coast(不再为退休存钱) / 育儿储备 / 真·自由线(可以不上班)。
    关键不变量:真·自由线 = Coast + 育儿储备,且它比 Coast 晚到达。"""
    from metrics import fi_plan
    today = datetime.date(2026, 7, 14)
    cfg = {"提取率": 0.035, "实际回报情景": [0.04]}
    events = [
        {"名称": "换房", "日期": "2029-09", "一次性": 2_706_000, "月度影响": {"房贷": 4800}},
        {"类型": "育儿", "结束": "2040-09", "月额区间": {"lo": 8000, "mid": 10000, "hi": 12000}},
    ]
    f = fi_plan(4_114_000, 41_839, 32_732, cfg, lifelong_month=25_656,
                ending_items=[{"item": "房贷", "amt": 4800}], events=events, today=today)
    assert f["coastNumber"] == round(25_656 * 12 / 0.035)
    assert f["reserve"] > 0
    assert f["freeNumber"] == f["coastNumber"] + f["reserve"]      # 三者的核心关系
    assert f["freeLo"] < f["freeNumber"] < f["freeHi"]             # 兴趣班区间 → 自由线区间
    assert f["coastProgress"] > f["freeProgress"]                  # Coast 更容易达到
    cy = f["coastScenarios"][0]["years"]
    fy = f["freeScenarios"][0]["years"]
    assert cy < fy, "达到 Coast FI 后仍需继续工作供孩子 → 真·自由更晚"
    # 阶梯生效:换房的一次性注入让到达时间显著早于「恒定储蓄」的朴素算法
    assert any(s["lump"] > 0 for s in f["ladder"])


def test_relocation_plan():
    """换房路线图:预算上限由目标态房产占比反推;净释放 = 卖出 − 税费 − 结清房贷 − 学位房。"""
    from metrics import relocation_plan
    goal = {"换房": {"启用": True, "目标日": "2029-09-01", "启动截止": "2028-12-31",
                     "买入预算上限占比": 0.20, "交易成本率": 0.02, "过桥需自筹": 300_000}}
    r = relocation_plan(goal, {"权益": 2_000_000}, networth=8_600_000,
                        prop_gross=5_600_000, total_debt=1_077_000, liquid=2_500_000,
                        today=datetime.date(2026, 7, 14))
    assert r["monthsLeft"] == 38
    assert r["cost"] == 112_000
    assert abs(r["budget"] - (8_600_000 - 112_000) * 0.20) < 1
    assert r["released"] == round(5_600_000 - 112_000 - 1_077_000 - r["budget"])
    assert r["released"] > 2_000_000                        # 净释放量级远超日常定投
    assert abs(r["propPctAfter"] - 0.20) < 0.001            # 换房后房产回到目标上限
    assert r["bridgeGap"] == 0                              # 可变现资产盖得住过桥
    assert relocation_plan({}, {}, 1, 1, 0, 0) is None      # 未启用


def test_true_savings_guard():
    """真实储蓄:数据不足时明确返回 insufficient(宁可不给数也不给错数);够了才倒推。"""
    from metrics import true_savings
    hist = [{"date": "2026-01-01", "金融资产": "3000000"},
            {"date": "2026-04-01", "金融资产": "3300000"}]   # 90天,Δ金融 +30万
    cfh = [{"月份": "2026-02", "净结余": "30000"}, {"月份": "2026-03", "净结余": "30000"}]
    short = true_savings(hist[:1] + [{"date": "2026-01-20", "金融资产": "3100000"}], cfh, [])
    assert short["insufficient"] and short["days"] < 60
    # 期间投资收益 +10万 → 真实储蓄 = 30万 − 10万 = 20万;计划 6万 → 说明另有 14万 净流入未记
    pnl = [("2026-01-01", 50_000), ("2026-04-01", 150_000)]
    ts = true_savings(hist, cfh, pnl)
    assert ts["investPnl"] == 100_000
    assert ts["real"] == 200_000
    assert ts["planned"] == 60_000
    assert ts["unrecorded"] == -140_000     # 计划 − 真实 < 0:实际存下的比计划多


def test_stress_relocation():
    """压测新增换房情景:房价下跌真正伤的是「净释放缩水」。"""
    from metrics import stress_test
    reloc = {"sell": 5_600_000, "cost": 112_000, "payoff": 1_077_000,
             "budget": 1_700_000, "released": 2_711_000}
    out = stress_test({"权益": 2_000_000}, 5_600_000, 1_077_000, 9_700_000,
                      {"USD": 500_000}, 8_600_000, reloc)
    rel = [x for x in out if x.get("reloc")]
    assert len(rel) == 3
    d20 = next(x for x in rel if "−20%" in x["name"])
    assert d20["dReleased"] < 0 and d20["ok"]      # 缩水但计划仍成立
    assert d20["released"] < reloc["released"]


def test_ips_check():
    """IPS 审计:无原因=违纪;卖低配/买超配按交易日权重判;大额提示;期初豁免。"""
    from metrics import ips_check
    hist = [{"date": "2026-06-24", "总净资产": "1000000", "权益": "300000", "黄金": "40000"},
            {"date": "2026-07-01", "总净资产": "1000000", "权益": "420000", "黄金": "10000"}]
    tgt = {"权益": 0.34, "黄金": 0.04}
    ledger = [
        {"日期": "2026-06-24", "动作": "期初", "名称": "甲", "资产类型": "A股权益ETF",
         "成交额": "300000", "原因/备注": ""},                       # 期初不审计
        {"日期": "2026-06-25", "动作": "卖出", "名称": "甲", "资产类型": "A股权益ETF",
         "成交额": "10000", "原因/备注": ""},                        # R1 无原因 + R2 卖低配(30%<34%,带内)提示
        {"日期": "2026-07-02", "动作": "买入", "名称": "甲", "资产类型": "A股权益ETF",
         "成交额": "80000", "原因/备注": "再平衡"},                  # R2 买超配(42%>39%)违纪 + R3 大额(8%>5%)
        {"日期": "2026-07-02", "动作": "买入", "名称": "金", "资产类型": "黄金ETF",
         "成交额": "5000", "原因/备注": "定投"},                     # 黄金 1%<4% 低配买入 → 合规
    ]
    out = ips_check(ledger, hist, tgt)
    rules = [(x["rule"], x["level"], x["action"]) for x in out]
    assert ("R1", "违纪", "卖出") in rules
    assert ("R2", "提示", "卖出") in rules          # 带内低配卖出=提示
    assert ("R2", "违纪", "买入") in rules          # 超带买入=违纪
    assert ("R3", "提示", "买入") in rules
    assert not any(x["name"] == "金" for x in out)   # 低配买入合规
    assert out[0]["date"] >= out[-1]["date"]         # 日期倒序


def test_sbbi_transcription():
    """sbbi_returns.json 转录校验:各序列 2005-2025 年化必须与年报声明值吻合(±0.5pp)。
    抄错任何一个年度数字这里都会炸。"""
    import json
    d = json.loads((BASE / "sbbi_returns.json").read_text(encoding="utf-8"))["years"]

    def cagr(key, years):
        prod = 1.0
        for y in years:
            prod *= 1 + d[str(y)][key]
        return prod ** (1.0 / len(list(years))) - 1
    full = range(2005, 2026)
    for key, expect, rng in [("A股整体", .1040, full), ("大盘股", .0971, full),
                             ("小盘股", .1087, full), ("长期国债", .0436, full),
                             ("长期信用债", .0519, range(2007, 2026)),
                             ("短期国债", .0243, full), ("上海金", .1057, full),
                             ("通胀", .0213, full)]:
        got = cagr(key, rng)
        assert abs(got - expect) < 0.005, f"{key}: {got:.4f} vs {expect}"


def test_sbbi_replay():
    """回放:纯权益=A股序列;50/50 单年算术;负收益年数与年化合理。"""
    import json
    from metrics import sbbi_replay
    data = json.loads((BASE / "sbbi_returns.json").read_text(encoding="utf-8"))
    pure = sbbi_replay({"权益": 100.0}, data)
    assert abs(pure["cagr"] - 0.1040) < 0.005
    assert pure["perYear"][3]["year"] == 2008 and abs(pure["perYear"][3]["r"] - (-0.6377)) < 1e-6
    mix = sbbi_replay({"权益": 50.0, "现金": 50.0}, data)
    y08 = next(p for p in mix["perYear"] if p["year"] == 2008)
    assert abs(y08["r"] - (-0.6377 + 0.0439) / 2) < 1e-6
    assert 0 < mix["negYears"] < pure["negYears"] + 1
    assert mix["maxDD"] > pure["maxDD"]              # 掺现金回撤更浅(数值更接近0)
    assert sbbi_replay({}, data) is None
    assert sbbi_replay({"权益": 1.0}, {}) is None


if __name__ == "__main__":
    print("tests.py 跨模块不变量：")
    check("存储层 file/sqlite 双后端等价", test_storage_backends_equivalent)
    check("对账锁定：已对账月拒绝自动覆盖", test_recon_lock)
    check("确认口径：净结余/储蓄率", test_confirm_math)
    check("补账历史月用定格口径", test_recon_backfill_uses_frozen)
    check("公积金收入行生成与合计", test_fund_income_rows)
    check("fixed_out 聚合点含订阅+保险", test_fixed_out_chokepoint)
    check("编辑器收入表单往返", test_editor_income_roundtrip)
    check("保险表单往返", test_insurance_parse_roundtrip)
    check("负债表单往返", test_loans_parse_roundtrip)
    check("净资产负债来源=台账推演", test_ledger_debt_accounts)
    check("账户/平台维度汇总", test_platform_summary)
    check("持仓成本聚合(净投入口径)", test_cost_basis)
    check("持仓差异自动记账", test_holdings_diff_trades)
    check("持仓管理表单往返", test_holdings_parse_roundtrip)
    check("人生目标表单往返", test_life_goals_roundtrip)
    check("台账推演vs实际数量校验", test_ledger_qty_check)
    check("XIRR 基准值", test_xirr)
    check("组合/大类资金加权收益", test_portfolio_perf)
    check("净资产增长归因闭合", test_attribution)
    check("FI 线与到达年限", test_fi_plan)
    check("再平衡执行单 5/25+定向", test_rebalance_plan)
    check("压力测试情景冲击", test_stress_test)
    check("保险缺口需求分析", test_insurance_gap)
    check("SBBI 数据转录校验", test_sbbi_transcription)
    check("SBBI 组合回放", test_sbbi_replay)
    check("IPS 操作合规审计", test_ips_check)
    check("支出去向=对总量的切分", test_bill_coverage)
    check("终身支出口径(FI 分母)", test_lifelong_out)
    check("事件阶梯(月储蓄随事件变化)", test_event_ladder)
    check("育儿储备(有限期负债的现值)", test_childcare_reserve)
    check("FI 三个数字:Coast/育儿储备/真·自由", test_fi_three_numbers)
    check("换房路线图(预算反推/净释放)", test_relocation_plan)
    check("真实储蓄倒推与数据不足守卫", test_true_savings_guard)
    check("压测:换房房价情景", test_stress_relocation)
    print(f"全部 {len(PASS)} 项通过")
