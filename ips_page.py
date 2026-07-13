#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
投资政策声明(IPS)页面生成器:python3 ips_page.py → ips.html
把 TARGET_NETWORTH/容忍带/集中度红线/定投规则 渲染成一页"宪法",
并对 holdings_history 全部操作跑 metrics.ips_check 合规审计——
「变动原因进台账」的闭环:每笔操作在这里对照纪律被公开检视。
纯静态无 JS、零网络(只读配置与 CSV),rebuild_views 会自动重建。
"""
import datetime
from pathlib import Path

import metrics
from portfolio_tracker import (TARGET_NETWORTH, CLASS_BANDS, DEVIATION_ALERT,
                               SINGLE_STOCK_MAX, CLUSTER_MAX_OF_EQUITY,
                               load_json, read_csv)

BASE = Path(__file__).resolve().parent


def _f(row, key):
    try:
        return float(row.get(key) or 0)
    except (ValueError, AttributeError):
        return 0.0


def build():
    cf = load_json("cashflow.json")
    dca = cf.get("定投计划", {})
    hist = read_csv("history.csv", [])
    ledger = read_csv("holdings_history.csv", [])
    trades = [r for r in ledger if r.get("动作") in ("买入", "卖出")]
    findings = metrics.ips_check(ledger, hist, TARGET_NETWORTH)
    n_bad = sum(1 for x in findings if x["level"] == "违纪")
    n_warn = len(findings) - n_bad

    # 目标配置 + 容忍带
    bands = {c: [] for c in TARGET_NETWORTH}
    for cls, kind, lim in CLASS_BANDS:
        bands.setdefault(cls, []).append(("≤" if kind == "max" else "≥") + f" {lim*100:.0f}%")
    last = hist[-1] if hist else None
    nw = _f(last, "总净资产") if last else 0
    alloc_rows = ""
    for c, t in TARGET_NETWORTH.items():
        cur = _f(last, c) / nw if last and nw else None
        dev = (cur - t) if cur is not None else None
        ok = dev is not None and abs(dev) < DEVIATION_ALERT and not (
            dev / t >= 0.25 if t else False)
        status = "—" if cur is None else ("✓ 带内" if ok else f"⚠ 偏 {dev*100:+.1f}pp")
        alloc_rows += (f"<tr><td>{c}</td><td><b>{t*100:.0f}%</b></td>"
                       f"<td>{' · '.join(bands.get(c) or ['—'])}</td>"
                       f"<td>{f'{cur*100:.1f}%' if cur is not None else '—'}</td>"
                       f"<td>{status}</td></tr>")

    dca_txt = (f"模式「{dca.get('模式', '未设定')}」· 结余投入比例 {dca.get('结余投入比例', 0)*100:.0f}% · "
               f"频率 {dca.get('频率', '—')} · 标的 {'、'.join((dca.get('标的') or {}).keys()) or '—'}")

    if findings:
        audit_rows = "".join(
            f"<tr class='{ 'bad' if x['level']=='违纪' else '' }'><td>{x['date']}</td>"
            f"<td>{x['action']}</td><td>{x['name']}</td><td>{x['rule']}</td>"
            f"<td><b>{x['level']}</b></td><td class='msg'>{x['msg']}</td></tr>"
            for x in findings)
        audit = (f"<table><tr><th>日期</th><th>动作</th><th>标的</th><th>规则</th>"
                 f"<th>判定</th><th>说明</th></tr>{audit_rows}</table>")
    else:
        audit = "<p class='ok'>✓ 全部操作合规——没有一笔交易违反本声明。</p>"

    gen = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    html = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>投资政策声明 IPS</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>📜</text></svg>">
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ background:#f4f1ea; color:#111; font:14px/1.65 "Helvetica Neue","PingFang SC",system-ui,sans-serif;
    font-variant-numeric:tabular-nums; max-width:880px; margin:0 auto; padding:34px 26px 60px; }}
  h1 {{ font-size:32px; font-weight:900; border-bottom:5px solid #111; padding-bottom:12px; }}
  h1 small {{ font-size:12px; font-weight:700; float:right; margin-top:16px; letter-spacing:.15em; }}
  h2 {{ font-size:16px; font-weight:900; letter-spacing:.06em; margin:28px 0 10px;
    display:inline-block; background:#111; color:#f4f1ea; padding:4px 12px; transform:rotate(-.5deg); }}
  table {{ width:100%; border-collapse:collapse; background:#fff; border:3px solid #111; font-size:13px; }}
  th, td {{ border:1.5px solid #111; padding:6px 9px; text-align:center; }}
  th {{ background:#111; color:#f4f1ea; font-weight:900; letter-spacing:.08em; }}
  td:first-child {{ text-align:left; }}
  td.msg {{ text-align:left; font-size:12px; }}
  tr.bad td {{ background:#fdeceb; }}
  .law {{ border:3px solid #111; background:#fff; box-shadow:5px 5px 0 #111; padding:14px 18px;
    margin-top:10px; font-weight:700; }}
  .law li {{ margin:6px 0 6px 20px; }}
  .ok {{ border:3px solid #1baf7a; background:#fff; padding:12px 16px; font-weight:900; color:#0d7a52; }}
  .note {{ margin-top:10px; padding:9px 12px; border:2.5px dashed #111; font-weight:700; font-size:12.5px; }}
  footer {{ margin-top:40px; font-size:11px; font-weight:800; letter-spacing:.2em;
    display:flex; justify-content:space-between; }}
  @media print {{ * {{ -webkit-print-color-adjust:exact; print-color-adjust:exact; }} body {{ padding:0; }} }}
</style></head><body>
<h1>投资政策声明<small>INVESTMENT POLICY STATEMENT</small></h1>

<h2>第一条 总纲(不可谈判)</h2>
<div class="law"><ol>
<li>本组合为<b>长期(10年+)被动配置</b>:收益来自 Beta 与纪律,不来自预测。</li>
<li>市场下跌是计划的一部分(见全景「历史回放」),<b>下跌期间禁止恐慌性卖出权益</b>。</li>
<li>任何对本声明的修改须经 <b>7 天冷静期</b>,并在 docs/strategy-log.md 记录理由。</li>
<li>每笔买卖必须在台账写明原因——没有原因的交易被下方审计标为违纪。</li>
</ol></div>

<h2>第二条 目标配置与容忍带</h2>
<table><tr><th>大类</th><th>目标</th><th>硬性带</th><th>当前</th><th>状态</th></tr>{alloc_rows}</table>
<p class="note">触发再平衡:偏离 ≥ {DEVIATION_ALERT*100:.0f}pp 或相对偏离 ≥ 25%(5/25 规则)。手段优先级:<b>增量定投定向 › 分红/新钱 › 卖出</b>。</p>

<h2>第三条 定投规则</h2>
<div class="law">{dca_txt}<br>净结余 ≤ 0 的月份自动暂停;定投标的与金额跟随全景「再平衡执行单」定向,不做择时。</div>

<h2>第四条 集中度红线</h2>
<div class="law"><ol>
<li>单一个股 ≤ 净资产 <b>{SINGLE_STOCK_MAX*100:.0f}%</b>;超限分批减持(SBBI 第9章:非系统性风险无补偿)。</li>
<li>个股合计 ≤ 权益的 <b>{CLUSTER_MAX_OF_EQUITY*100:.0f}%</b>,其余走宽基指数。</li>
<li>单平台金融资产占比 > 50% 时列入观察(见全景「平台分布」)。</li>
</ol></div>

<h2>第五条 操作合规审计({len(trades)} 笔交易 · 违纪 {n_bad} · 提示 {n_warn})</h2>
{audit}
<p class="note">规则:R1 无原因 / R2 逆纪律方向(卖低配·买超配) / R3 单笔超净资产 5%。审计每次重渲自动更新,历史不可涂改。</p>

<footer><span>ASSET PANORAMA · IPS</span><span>重建 {gen}</span></footer>
</body></html>"""
    out = BASE / "ips.html"
    out.write_text(html, encoding="utf-8")
    print(f"✅ 投资政策声明 → {out.name}(交易 {len(trades)} 笔,违纪 {n_bad}/提示 {n_warn})")
    return out


if __name__ == "__main__":
    build()
