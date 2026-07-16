#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
年度报告生成器（投顾"年度信"）：python3 annual_report.py [年份]
→ report_<年份>.html：纯静态自包含、无 JS、可打印归档。
覆盖:净资产与归因/真实收益/月度现金流账/配置变化 vs 目标/台账大事记/
风险快照(杠杆·保险缺口·FI)/SBBI 历史回放/再平衡执行单摘要。
年份缺省 = 当年(年中生成即"至今"口径)。run_daily.sh 在一月自动补生成上一年。
"""
import datetime
import sys
from pathlib import Path

import metrics
from portfolio_tracker import read_csv

BASE = Path(__file__).resolve().parent
CLASSES = ["房产", "权益", "债券类固收", "现金", "黄金"]


def _f(row, key):
    try:
        return float(row.get(key) or 0)
    except ValueError:
        return 0.0


def _wan(v):
    return f"{v / 1e4:,.1f}万"


def _pct(v, digits=1):
    return ("+" if v >= 0 else "−") + f"{abs(v) * 100:.{digits}f}%"


def _money(v):
    sign = "+" if v >= 0 else "−"
    return f"{sign}¥{abs(round(v)):,}"


def _kv(label, value, strong=False):
    b = ' style="font-weight:900"' if strong else ""
    return f'<div class="kv"><span{b}>{label}</span><span class="v">{value}</span></div>'


def _nw_svg(rows):
    """年内净资产路径 → 简单粗线 SVG(无 JS)。"""
    vals = [_f(r, "总净资产") / 1e4 for r in rows]
    if len(vals) < 2:
        return ""
    W, H, P = 860, 150, 14
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.15 or 1
    lo, hi = lo - pad, hi + pad
    x = lambda i: P + (W - 2 * P) * i / (len(vals) - 1)
    y = lambda v: P + (H - 2 * P) * (1 - (v - lo) / (hi - lo))
    pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(vals))
    return (f'<svg viewBox="0 0 {W} {H}" style="width:100%;display:block;margin:6px 0 2px">'
            f'<polyline points="{pts}" fill="none" stroke="#111" stroke-width="4" '
            f'stroke-linejoin="round" stroke-linecap="round"/>'
            f'<circle cx="{x(len(vals) - 1):.1f}" cy="{y(vals[-1]):.1f}" r="6" fill="#e34948" '
            f'stroke="#111" stroke-width="2.5"/></svg>')


def _goal_facts(D, links):
    """人生目标的「关联」→ 已有指标的一句话事实。未识别的关联原样列出(框架对自定义目标开放)。"""
    fi = D.get("fi") or {}
    R = D.get("reloc")
    A = D.get("attribution")
    t = (D.get("perf") or {}).get("total")
    gap = ((D.get("insGap") or {}).get("life") or {}).get("gap", 0)
    plats = D.get("platforms") or []
    known = {
        "换房": (f"启动还剩 {R['startMonthsLeft']} 个月 · 净释放 {_wan(R['released'])} 进金融资产" if R else None),
        "育儿储备": (f"现值 {_wan(fi['reserve'])}（孩子成年前必须供,已计入真·自由线）" if fi.get("reserve") else None),
        "目标态配置": (f"真·自由进度 {fi.get('freeProgress', 0) * 100:.0f}% · 配置偏离见「配置变化 vs 目标」" if fi else None),
        "增长归因": (f"今年 ΔNW 中储蓄贡献 {_money(A['savings'])} · 投资 {_money(A['invest'])}" if A else None),
        "真实收益": (f"组合累计 {_pct(t['cum'])}（净投入 {_wan(t['invested'])}）" if t else None),
        "保险缺口": (f"⚠ 寿险缺口 {_wan(gap)}" if gap > 0 else "家庭保障已覆盖 ✓"),
        "平台透明度": (f"金融资产分布 {len(plats)} 个平台,应急可查(见全景「应急参考」)" if plats else None),
        "家庭预算": (f"月固定支出 ¥{D.get('fixedOut', 0):,.0f} · 其中订阅 ¥{D.get('subsMonthly', 0):,.0f}"),
        "定投": "按真实结余×80% 自动,规则见 IPS 第三条",
    }
    out = []
    for k in links or []:
        v = known.get(k)
        out.append(f"<b>{k}</b>:{v}" if v else k)
    return out


def _life_goals_section(D, year, partial):
    """00 人生目标复盘。工具是仪式的主持人,不是人生的记分员:
    财务部分自动生成;非财务只提问,答案由用户写进 docs/annual-review-<年>.md,逐年存档对照。"""
    import html as _html
    from portfolio_tracker import load_json
    goals = load_json("goal.json").get("人生目标") or []
    if not goals:
        return ""
    blocks = ""
    for lg in goals:
        facts = "".join(f"<li>{f}</li>" for f in _goal_facts(D, lg.get("关联")))
        qs = "".join(f"<li>{_html.escape(q)}</li>" for q in (lg.get("年度问题") or []))
        blocks += (f"<div class='cell' style='flex:1 1 260px'>"
                   f"<div class='lab'>{_html.escape(lg.get('名称', ''))}</div>"
                   f"<div style='font-weight:800;margin:4px 0 8px'>{_html.escape(lg.get('叙事', ''))}</div>"
                   f"<ul style='margin-left:18px;font-size:13px'>{facts}</ul>"
                   + (f"<div class='lab' style='margin-top:10px'>年度问题</div>"
                      f"<ol style='margin-left:18px;font-size:13px'>{qs}</ol>" if qs else "")
                   + "</div>")
    prev_f = BASE / "docs" / f"annual-review-{year - 1}.md"
    cur_f = BASE / "docs" / f"annual-review-{year}.md"
    prev = ""
    if prev_f.exists():
        prev = (f"<details style='margin-top:10px'><summary style='font-weight:900;cursor:pointer'>"
                f"▸ 去年的回答（annual-review-{year - 1}.md）</summary>"
                f"<pre style='white-space:pre-wrap;font:12.5px/1.6 inherit;border:2.5px dashed #111;"
                f"padding:10px 14px;margin-top:8px'>{_html.escape(prev_f.read_text(encoding='utf-8'))}</pre></details>")
    status = ("✓ 今年已作答" if cur_f.exists()
              else f"今年的回答写入 <b>docs/annual-review-{year}.md</b>（每目标一节,手写或让 agent 代笔访谈皆可）")
    return (f"\n<h2>00 人生目标复盘{'（至今）' if partial else ''}</h2>"
            f"<div class='tri'>{blocks}</div>"
            f"<p class='note'>财务部分自动生成;问题只提不打分——工具是仪式的主持人,不是人生的记分员。{status}</p>{prev}")


def build(year=None):
    from panorama_data import collect
    D = collect(persist_history=False, fetch_klines=False)
    year = int(year or datetime.date.today().year)
    ystr = str(year)
    partial = year == datetime.date.today().year

    hist = [r for r in read_csv("history.csv", []) if (r.get("date") or "").startswith(ystr)]
    cfh = [r for r in read_csv("cashflow_history.csv", []) if (r.get("月份") or "").startswith(ystr)]
    ledger = [r for r in read_csv("holdings_history.csv", [])
              if (r.get("日期") or "").startswith(ystr) and r.get("动作") != "期初"]
    if len(hist) < 2:
        raise SystemExit(f"❌ {year} 年 history.csv 不足两行,无法出报告")
    attrib = metrics.attribution(hist, cfh)
    a0, a1 = hist[0], hist[-1]
    nw0, nw1 = _f(a0, "总净资产"), _f(a1, "总净资产")

    S = []  # 段落集合

    # ── 封面数字 ──
    S.append(f"""
<div class="hero">
  <div><div class="lab">期间</div><div class="big">{a0['date']} → {a1['date']}</div></div>
  <div><div class="lab">总净资产</div><div class="big">{_wan(nw0)} → <b>{_wan(nw1)}</b></div></div>
  <div><div class="lab">变化</div><div class="big" style="color:{'#1baf7a' if nw1 >= nw0 else '#e34948'}">{_money(nw1 - nw0)}（{_pct((nw1 - nw0) / nw0 if nw0 else 0)}）</div></div>
</div>{_nw_svg(hist)}""")

    # ── 人生目标复盘:财务基座自动汇总 + 年度提问(手答存 docs/annual-review-<年>.md) ──
    life = _life_goals_section(D, year, partial)
    if life:
        S.append(life)

    # ── 归因 ──
    if attrib:
        draft = ' <small>(含未对账草稿月)</small>' if attrib["draft"] else ""
        S.append(f"""
<h2>01 净资产增长归因{draft}</h2>
<div class="tri">
  <div class="cell"><div class="lab">储蓄贡献</div><div class="num">{_money(attrib['savings'])}</div><small>月度净结余合计</small></div>
  <div class="cell"><div class="lab">金融投资收益(推算)</div><div class="num">{_money(attrib['invest'])}</div><small>Δ金融资产 − 储蓄</small></div>
  <div class="cell"><div class="lab">房产净值变动</div><div class="num">{_money(attrib['property'])}</div><small>重估 + 还本合并</small></div>
</div>
<p class="note">三块精确闭合:ΔNW {_money(attrib['deltaNW'])} = 储蓄 + 投资 + 房产净值。增长靠攒还是靠赚,答案在中间那格。</p>""")

    # ── 收益 ──
    t = (D.get("perf") or {}).get("total")
    if t:
        xirr_txt = (_pct(t["xirr"]) if t["days"] >= 90 and t["xirr"] is not None and abs(t["xirr"]) < 2
                    else f"—（台账 {t['days']} 天,期初=历史成本,满 90 天后显示）")
        cls_rows = "".join(_kv(f"{c}（投入 {_wan(s['invested'])}）", _pct(s["cum"]))
                           for c, s in ((D.get("perf") or {}).get("byClass") or {}).items())
        S.append(f"""
<h2>02 真实收益（资金加权 · 自台账基线）</h2>
{_kv("组合累计（浮盈/净投入）", f"<b>{_pct(t['cum'])}</b>（{_wan(t['invested'])} → {_wan(t['invested'] + t['pnl'])}）", True)}
{_kv("年化 XIRR", xirr_txt)}
{cls_rows}""")

    # ── 月度现金流 ──
    if cfh:
        rows = "".join(
            f"<tr><td>{r.get('月份')}</td><td>{_money(_f(r, '税后收入'))}</td>"
            f"<td>{_money(-_f(r, '固定支出'))}</td><td>{_money(-_f(r, '其他实际支出'))}</td>"
            f"<td><b>{_money(_f(r, '净结余'))}</b></td><td>{_f(r, '储蓄率') * 100:.0f}%</td>"
            f"<td>{'✓ 已对账' if (r.get('已对账') or '').strip() == '是' else '草稿'}</td></tr>"
            for r in cfh)
        tot_net = sum(_f(r, "净结余") for r in cfh)
        avg_sr = (sum(_f(r, "储蓄率") for r in cfh) / len(cfh)) if cfh else 0
        S.append(f"""
<h2>03 月度现金流账</h2>
<table><tr><th>月份</th><th>税后收入</th><th>固定支出</th><th>其他支出</th><th>净结余</th><th>储蓄率</th><th>状态</th></tr>{rows}</table>
<p class="note">全年净结余合计 <b>{_money(tot_net)}</b> · 平均储蓄率 {avg_sr * 100:.0f}%</p>""")

    # ── 配置变化 vs 目标 ──
    tgt = D.get("target") or {}
    rows = ""
    for c in CLASSES:
        p0 = _f(a0, c) / nw0 if nw0 else 0
        p1 = _f(a1, c) / nw1 if nw1 else 0
        tv = tgt.get(c, 0)
        flag = " ⚠" if abs(p1 - tv) >= 0.05 else ""
        rows += (f"<tr><td>{c}</td><td>{p0 * 100:.1f}%</td><td>{p1 * 100:.1f}%</td>"
                 f"<td>{tv * 100:.0f}%</td><td>{_pct(p1 - tv)}pp{flag}</td></tr>")
    S.append(f"""
<h2>04 配置变化 vs 目标</h2>
<table><tr><th>大类</th><th>期初</th><th>期末</th><th>目标</th><th>期末偏离</th></tr>{rows}</table>""")

    # ── 台账大事记 ──
    if ledger:
        buys = sum(_f(r, "成交额") for r in ledger if r.get("动作") == "买入")
        sells = sum(_f(r, "成交额") for r in ledger if r.get("动作") == "卖出")
        rows = "".join(
            f"<tr><td>{r.get('日期')}</td><td>{r.get('动作')}</td><td>{r.get('名称')}</td>"
            f"<td>{r.get('数量')} @ {r.get('成交价')}</td><td>{_money(_f(r, '成交额') * (-1 if r.get('动作') == '买入' else 1))}</td>"
            f"<td class='dim'>{(r.get('原因/备注') or '')[:40]}</td></tr>"
            for r in ledger[:40])
        S.append(f"""
<h2>05 操作大事记（{len(ledger)} 笔 · 买入 ¥{buys:,.0f} / 卖出 ¥{sells:,.0f}）</h2>
<table><tr><th>日期</th><th>动作</th><th>标的</th><th>数量@价</th><th>金额</th><th>原因</th></tr>{rows}</table>
<p class="note">纪律检查:每一笔都应有「原因」——没有原因的交易明年不该再出现。</p>""")
    else:
        S.append(f"<h2>05 操作大事记</h2><p class='note'>{year} 年无台账交易记录（期初建仓除外）。零操作对长期投资者常是好消息。</p>")

    # ── 风险快照 ──
    gap = ((D.get("insGap") or {}).get("life") or {}).get("gap", 0)
    fi = D.get("fi") or {}
    S.append(f"""
<h2>06 风险快照（期末）</h2>
{_kv("整体杠杆率", f"{(D.get('leverage') or 0) * 100:.1f}%")}
{_kv("按揭 LTV", f"{(D.get('ltv') or 0) * 100:.1f}%")}
{_kv("应急可撑", f"{D.get('runway', 0):.0f} 个月（另有保单贷款额度 {_wan(D.get('policyLoan') or 0)}）")}
{_kv("寿险缺口", f"<b style='color:#e34948'>{_wan(gap)}</b>" if gap > 0 else "已覆盖 ✓")}
{_kv("财务自由进度", f"{(fi.get('progress') or 0) * 100:.1f}%（FI 线 {_wan(fi.get('number') or 0)}）")}
{_kv("当前告警", f"{len(D.get('alerts') or [])} 条")}""")

    # ── SBBI 回放 ──
    sb = D.get("sbbi")
    if sb:
        per = sb["perYear"]
        cells = "".join(
            f"<td style='color:{'#1baf7a' if p['r'] >= 0 else '#e34948'}'>{p['year']}<br><b>{_pct(p['r'], 0)}</b></td>"
            + ("</tr><tr>" if i % 7 == 6 else "")
            for i, p in enumerate(per))
        wtxt = " / ".join(f"{c} {v * 100:.0f}%" for c, v in sb["weights"].items() if v > 0.001)
        S.append(f"""
<h2>07 历史回放（SBBI {per[0]['year']}–{per[-1]['year']} · 当前权重 {wtxt}）</h2>
<table class="grid"><tr>{cells}</tr></table>
{_kv("全期年化（名义 / 实际）", f"<b>{_pct(sb['cagr'])}</b> / {_pct(sb['realCagr'])}")}
{_kv("最大回撤（年度路径）", f"<b style='color:#e34948'>{sb['maxDD'] * 100:.1f}%</b>")}
{_kv("负收益年数 / 最长水下", f"{sb['negYears']} / {len(per)} 年 · 最长 {sb['longestUnder']} 年未创新高")}
<p class="note">口径:权益→A股整体、固收→长期国债、现金→短债、黄金→上海金;不含房产与杠杆。历史不预示未来,但它标定了"正常波动"的范围。</p>""")

    # ── 执行单摘要 ──
    reb = D.get("rebalance") or {}
    if reb.get("rows"):
        rows = "".join(
            f"<tr><td>{r['cls']}</td><td>{r['curPct'] * 100:.1f}%</td><td>{r['tgtPct'] * 100:.0f}%</td>"
            f"<td>{'行动' if r['act'] and r['adjustable'] else ('超限' if r['act'] else '—')}</td></tr>"
            for r in reb["rows"])
        plan = reb.get("plan")
        ptxt = ""
        if plan:
            alloc = " · ".join(f"{c} ¥{v:,}" for c, v in (plan.get("alloc") or {}).items())
            ptxt = (f"<p class='note'>明年纪律:每月定投 <b>¥{plan['monthly']:,}</b> 定向 {alloc},"
                    f"约 <b>{plan['months']}</b> 个月不卖出回到目标带。</p>")
        S.append(f"""
<h2>08 明年怎么做（再平衡执行单）</h2>
<table><tr><th>大类</th><th>当前</th><th>目标</th><th>5/25 判定</th></tr>{rows}</table>{ptxt}""")

    title = f"资产年度报告 {year}" + ("（至今）" if partial else "")
    gen = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    html = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>📕</text></svg>">
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ background:#f4f1ea; color:#111; font:14px/1.6 "Helvetica Neue","PingFang SC",system-ui,sans-serif;
    font-variant-numeric:tabular-nums; max-width:920px; margin:0 auto; padding:34px 26px 60px; }}
  h1 {{ font-size:34px; font-weight:900; letter-spacing:.02em; border-bottom:5px solid #111; padding-bottom:12px; }}
  h1 small {{ font-size:13px; font-weight:700; float:right; margin-top:18px; }}
  h2 {{ font-size:17px; font-weight:900; letter-spacing:.06em; margin:30px 0 10px;
    display:inline-block; background:#111; color:#f4f1ea; padding:4px 12px; transform:rotate(-.5deg); }}
  .hero {{ display:flex; gap:18px; flex-wrap:wrap; margin-top:18px; }}
  .hero > div {{ flex:1 1 200px; border:3px solid #111; background:#fff; padding:12px 16px; box-shadow:5px 5px 0 #111; }}
  .lab {{ font-size:11px; font-weight:800; letter-spacing:.15em; }}
  .big {{ font-size:19px; font-weight:900; }}
  .tri {{ display:flex; gap:16px; flex-wrap:wrap; }}
  .tri .cell {{ flex:1 1 180px; border:3px solid #111; background:#fff; padding:12px 16px; box-shadow:5px 5px 0 #111; }}
  .tri .num {{ font-size:24px; font-weight:900; }}
  .kv {{ display:flex; justify-content:space-between; gap:10px; padding:7px 2px; border-bottom:2px solid #ddd8c9; font-weight:700; }}
  .kv .v {{ font-weight:900; text-align:right; }}
  table {{ width:100%; border-collapse:collapse; background:#fff; border:3px solid #111; font-size:12.5px; }}
  th, td {{ border:1.5px solid #111; padding:5px 8px; text-align:right; }}
  th {{ background:#111; color:#f4f1ea; font-weight:900; letter-spacing:.08em; }}
  td:first-child, th:first-child {{ text-align:left; }}
  table.grid td {{ text-align:center; font-size:11.5px; padding:6px 2px; }}
  .dim {{ opacity:.65; font-weight:400; text-align:left; }}
  .note {{ margin-top:10px; padding:9px 12px; border:2.5px dashed #111; font-weight:700; font-size:12.5px; }}
  footer {{ margin-top:40px; font-size:11px; font-weight:800; letter-spacing:.2em; display:flex; justify-content:space-between; }}
  @media print {{ * {{ -webkit-print-color-adjust:exact; print-color-adjust:exact; }}
    body {{ padding:0; max-width:none; }} h2 {{ break-after:avoid; }}
    table, .tri, .hero {{ break-inside:avoid; }} }}
</style></head><body>
<h1>{title}<small>ANNUAL REPORT · 仅供个人参考</small></h1>
{''.join(S)}
<footer><span>ASSET PANORAMA · ANNUAL REPORT</span><span>生成 {gen}</span></footer>
</body></html>"""
    out = BASE / f"report_{year}.html"
    out.write_text(html, encoding="utf-8")
    print(f"✅ 年度报告 → {out.name}（期间 {a0['date']} → {a1['date']}{'，至今口径' if partial else ''}）")
    return out


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else None)
