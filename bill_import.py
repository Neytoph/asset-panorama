# -*- coding: utf-8 -*-
"""
账单导入：解析 微信支付 / 支付宝 / 银行 导出的 CSV 流水，汇总某月消费净额。
供「月度对账」页把汇总数填进「其他实际支出」。

设计要点：
· 各家格式不一且常变，不做严格 schema——嗅探表头行(按关键列名)，列名映射后逐行提取。
· 编码：微信 UTF-8、支付宝 GBK、银行不定 → 依次试 utf-8-sig / gb18030。
· 只统计「支出」方向；剔除：状态含 退款/关闭/失败、收支列"不计收支"、
  以及关键词排除（转账/还款/理财等资金腾挪，非消费）。
· 已在 cashflow.json 固定支出里的项（房租/房贷…）若也出现在流水里会重复，
  由用户在排除关键词里自行补（页面有提示）。
"""
import csv
import io
import re

# 表头嗅探：一行里命中 ≥2 个关键列名即视为表头
_HEADER_KEYS = ("交易时间", "交易日期", "记账日期", "金额", "收/支", "收支",
                "收入", "支出", "交易对方", "交易类型", "交易分类", "商品")
# 默认排除关键词（资金腾挪非消费）；页面可编辑
# 保险/保费：保费已按月摊入固定支出（insurance.py），账单里的整笔扣费再计入会双计
DEFAULT_EXCLUDES = "转账,还款,信用卡,理财,基金,余额宝,零钱通,提现,充值,黄金,亲属卡,保险,保费"
# 状态列出现这些词 → 整行剔除
_BAD_STATUS = ("退款", "关闭", "失败", "撤销")

_AMT_RE = re.compile(r"[-+]?\d[\d,]*\.?\d*")


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, ValueError):
            continue
    return raw.decode("utf-8", errors="replace")


def _amount(s):
    """'¥1,234.56' / '1,234.56元' / '-88.00' → float；解析失败返回 None。"""
    if s is None:
        return None
    m = _AMT_RE.search(str(s).replace("，", ",").replace(" ", ""))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _norm_date(s):
    """'2026-07-03 12:01:02' / '2026/7/3' / '20260703' → '2026-07'（只要月份），失败 None。"""
    s = str(s or "").strip()
    m = re.search(r"(\d{4})[-/年.]?(\d{1,2})", s)
    if not m:
        return None
    return f"{m.group(1)}-{int(m.group(2)):02d}"


def _find_header(lines):
    """返回 (表头行号, 列名列表)；找不到返回 (None, None)。"""
    for i, ln in enumerate(lines[:40]):        # 前导说明一般在 20 行内
        hits = sum(1 for k in _HEADER_KEYS if k in ln)
        if hits >= 2:
            cols = next(csv.reader([ln]))
            return i, [c.strip().strip('"') for c in cols]
    return None, None


def _col(cols, *names):
    """按候选名找列号（含子串匹配），找不到返回 None。"""
    for n in names:
        for i, c in enumerate(cols):
            if n == c:
                return i
    for n in names:
        for i, c in enumerate(cols):
            if n in c:
                return i
    return None


def guess_source(text):
    head = text[:600]
    if "微信支付" in head or "微信昵称" in head:
        return "微信"
    if "支付宝" in head or "alipay" in head.lower():
        return "支付宝"
    return "银行/其他"


def parse_bill(raw: bytes):
    """
    CSV bytes → {source, rows:[{month, amount(正数=支出额), counterparty, desc, kind}], warnings:[]}
    只保留判定为「支出」且状态正常的行（排除词不在此处理，留给 summarize 可见化）。
    """
    text = _decode(raw)
    source = guess_source(text)
    lines = text.splitlines()
    hi, cols = _find_header(lines)
    warnings = []
    if hi is None:
        return {"source": source, "rows": [],
                "warnings": ["未识别到表头行（需含 交易时间/金额/收支 等列名的 CSV）"]}

    i_date = _col(cols, "交易时间", "交易日期", "记账日期", "日期")
    i_dir = _col(cols, "收/支", "收支")
    i_amt = _col(cols, "金额(元)", "金额（元）", "金额", "交易金额")
    i_out = _col(cols, "支出")
    i_in = _col(cols, "收入")
    i_peer = _col(cols, "交易对方", "对方", "商户名称")
    i_desc = _col(cols, "商品说明", "商品", "交易备注", "备注", "摘要")
    i_kind = _col(cols, "交易分类", "交易类型", "类型")
    i_status = _col(cols, "当前状态", "交易状态", "状态")

    if i_date is None or (i_amt is None and i_out is None):
        return {"source": source, "rows": [],
                "warnings": [f"表头已找到但缺关键列(日期/金额)：{cols}"]}

    rows = []
    n_bad_status = 0
    body = "\n".join(lines[hi + 1:])
    for rec in csv.reader(io.StringIO(body)):
        if len(rec) < len(cols) - 2 or not any(x.strip() for x in rec):
            continue
        get = lambda i: rec[i].strip() if (i is not None and i < len(rec)) else ""
        month = _norm_date(get(i_date))
        if not month:
            continue
        status = get(i_status)
        if status and any(b in status for b in _BAD_STATUS):
            n_bad_status += 1
            continue
        direction = get(i_dir)
        if direction and ("不计" in direction):
            continue
        amt = None
        if i_out is not None and get(i_out):
            # 银行「收入/支出」双列式：支出列有值即支出
            amt = _amount(get(i_out))
            if amt is not None:
                amt = abs(amt)
        elif i_amt is not None:
            v = _amount(get(i_amt))
            if v is None:
                continue
            if direction:
                if "支" not in direction:      # 只要支出
                    continue
                amt = abs(v)
            else:
                # 单列带符号（银行）：负数为支出
                if v >= 0:
                    continue
                amt = -v
        if not amt:
            continue
        rows.append({
            "month": month, "amount": round(amt, 2),
            "counterparty": get(i_peer), "desc": get(i_desc), "kind": get(i_kind),
        })
    if n_bad_status:
        warnings.append(f"已剔除退款/关闭等异常状态 {n_bad_status} 笔")
    if not rows:
        warnings.append("没有解析到支出行（确认导出的是含支出的流水，且方向/金额列正常）")
    return {"source": source, "rows": rows, "warnings": warnings}


def coverage(total_out, classified):
    """支出去向 = 对总量的**切分**，不是加总(见 docs/2029-plan.md 2.2)。

    total_out  总量层的硬数字(真实支出，来自账户余额变化，不依赖导没导账单)
    classified 已归类金额(导了多少算多少)
    → {total, classified, unclassified, rate}

    关键:漏导账单只会让「未归类」变大，**总量永远正确**——储蓄率/FI/归因一个都不受影响。
    所以导账单是自愿的、增量的、可以偷懒的。
    """
    total_out = float(total_out or 0)
    classified = min(float(classified or 0), total_out)
    unclassified = max(0.0, total_out - classified)
    return {"total": round(total_out, 2), "classified": round(classified, 2),
            "unclassified": round(unclassified, 2),
            "rate": (classified / total_out) if total_out else 0.0}


def summarize(raw: bytes, month: str, excludes=None):
    """
    解析并按月汇总 → 给前端的 dict：
    {source, month, expense(计入合计), excluded(被排除词命中的合计), excludedCount,
     count, top:[[对方, 金额]…], otherMonths(该文件里非目标月的支出合计), warnings}

    注意:这里的 expense 是**已归类**金额,不是当月真实总支出——
    总支出由总量层给出(见 coverage())。没导的部分落进「未归类」,不是 bug。
    """
    if excludes is None:
        kw = [k.strip() for k in DEFAULT_EXCLUDES.split(",")]
    else:
        kw = [k.strip() for k in str(excludes).replace("，", ",").split(",") if k.strip()]
    p = parse_bill(raw)
    expense = excluded = other_months = 0.0
    exc_n = n = 0
    by_peer = {}
    for r in p["rows"]:
        if r["month"] != month:
            other_months += r["amount"]
            continue
        blob = f'{r["counterparty"]} {r["desc"]} {r["kind"]}'
        if any(k and k in blob for k in kw):
            excluded += r["amount"]
            exc_n += 1
            continue
        expense += r["amount"]
        n += 1
        peer = r["counterparty"] or r["desc"] or "(未知)"
        by_peer[peer] = by_peer.get(peer, 0) + r["amount"]
    top = sorted(by_peer.items(), key=lambda x: -x[1])[:10]
    return {
        "source": p["source"], "month": month,
        "expense": round(expense, 2), "count": n,
        "excluded": round(excluded, 2), "excludedCount": exc_n,
        "otherMonths": round(other_months, 2),
        "top": [[k, round(v, 2)] for k, v in top],
        "warnings": p["warnings"],
    }


if __name__ == "__main__":
    # 三种格式的最小自测样例
    wechat = ("微信支付账单明细\n微信昵称: x\n----------------------\n"
              "交易时间,交易类型,交易对方,商品,收/支,金额(元),支付方式,当前状态,交易单号,商户单号,备注\n"
              '2026-07-02 12:00:00,商户消费,永辉超市,食品,支出,¥123.40,零钱,支付成功,10001,,"/"\n'
              '2026-07-03 08:00:00,商户消费,滴滴出行,打车,支出,¥45.60,零钱,支付成功,10002,,"/"\n'
              '2026-07-04 09:00:00,转账,张三,转账,支出,¥1000.00,零钱,支付成功,10003,,"/"\n'
              '2026-07-05 10:00:00,商户消费,肯德基,餐饮,支出,¥58.00,零钱,已全额退款,10004,,"/"\n'
              '2026-06-30 10:00:00,商户消费,便利店,饮料,支出,¥8.00,零钱,支付成功,10005,,"/"\n'
              '2026-07-06 11:00:00,商户消费,某某保险,保费,支出,¥8400.00,零钱,支付成功,10006,,"/"\n'
              ).encode("utf-8")
    alipay = ("支付宝交易明细\n起始时间: [2026-07-01]\n---------------------\n"
              "交易时间,交易分类,交易对方,对方账号,商品说明,收/支,金额,收/付款方式,交易状态,交易订单号,商家订单号,备注\n"
              "2026-07-02 13:00:00,餐饮美食,美团,mt@x,外卖订单,支出,35.50,余额宝,交易成功,20001,,\n"
              "2026-07-03 14:00:00,投资理财,蚂蚁基金,f@x,基金申购,支出,2000.00,余额宝,交易成功,20002,,\n"
              "2026-07-04 15:00:00,数码电器,京东,jd@x,耳机,支出,299.00,花呗,交易成功,20003,,\n"
              "2026-07-05 16:00:00,退款,美团,mt@x,外卖退款,不计收支,35.50,余额宝,退款成功,20004,,\n"
              ).encode("gb18030")
    bank = ("招商银行交易流水\n账号: ****1234\n"
            "记账日期,交易日期,交易类型,交易备注,支出,收入,余额\n"
            "2026-07-02,2026-07-02,消费,超市购物,200.00,,50000.00\n"
            "2026-07-03,2026-07-03,代扣,房租支出,19500.00,,30500.00\n"
            "2026-07-04,2026-07-04,工资,工资入账,,46800.00,77300.00\n"
            ).encode("gb18030")

    s = summarize(wechat, "2026-07")
    assert s["source"] == "微信" and s["expense"] == 169.0, s      # 123.4+45.6；转账被排除、退款被剔、6月不算
    # 保费 8400 被默认排除词剔除（已摊月入固定支出，防对账双计），和转账 1000 一起进 excluded
    assert s["excluded"] == 9400.0 and s["otherMonths"] == 8.0, s
    s2 = summarize(alipay, "2026-07")
    assert s2["source"] == "支付宝" and s2["expense"] == 334.5, s2  # 35.5+299；基金被排除、不计收支剔除
    assert s2["excluded"] == 2000.0, s2
    s3 = summarize(bank, "2026-07", excludes="房租")
    assert s3["expense"] == 200.0 and s3["excluded"] == 19500.0, s3  # 收入列不算；房租按用户排除词剔除
    print("✅ bill_import 自测通过：微信/支付宝/银行 三种格式、排除词、退款剔除、跨月过滤")
