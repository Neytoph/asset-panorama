#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全景视图生成器（ECharts，需联网渲染图表）：经典配色 → panorama_origin.html
复用 panorama_data.collect()。用法：python3 panorama_themes.py
"""
import storage
import json
from pathlib import Path
from panorama_data import collect
import subscriptions as subs

BASE = Path(__file__).resolve().parent
ECHARTS = "https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"


def wan(v):
    return f"{v/10000:,.1f}万"


# ── 主题定义 ──
THEMES = {
    "origin": {
        "file": "panorama_origin.html", "name": "经典配色",
        "fonts": "family=IBM+Plex+Mono:wght@400;500;600",
        "disp": '-apple-system,"PingFang SC","Microsoft YaHei",sans-serif',
        "body": '-apple-system,"PingFang SC","Microsoft YaHei",sans-serif',
        "mono": "'IBM Plex Mono'",
        "dark": False, "radius": "14px", "shadow": "0 1px 4px rgba(0,0,0,.06)",
        "palette": ["#f59e0b", "#2563eb", "#16a34a", "#64748b", "#eab308", "#a855f7", "#0ea5e9"],
        "bg": "#f4f5f7", "panel": "#ffffff", "ink": "#222222", "dim": "#8a8f99",
        "grid": "#ececf0", "line1": "#2563eb", "line2": "#16a34a",
        "good": "#16a34a", "warn": "#f59e0b", "bad": "#dc2626",
    },
}


def css(t):
    dark = t["dark"]
    disp, body, mono = t["disp"], t["body"], t["mono"]
    radius = t.get("radius", "16px")
    shadow = t.get("shadow", "0 6px 28px rgba(60,45,20,.07)" if not dark else "0 0 0 1px #1c2731")
    border = f"1px solid {t['grid']}"
    upper = "letter-spacing:.14em;text-transform:uppercase" if dark else "letter-spacing:.02em"
    return f"""
  *{{box-sizing:border-box}}
  body{{margin:0;background:{t['bg']};color:{t['ink']};font-family:{body},sans-serif;
       {'background-image:radial-gradient(circle at 20% 0%,#10171e,transparent 60%);' if dark else ''}}}
  .wrap{{max-width:1180px;margin:0 auto;padding:32px 24px 60px}}
  .top{{display:flex;justify-content:space-between;align-items:flex-end;
        border-bottom:{'2px solid '+t['ink'] if not dark else '1px solid '+t['grid']};padding-bottom:18px;margin-bottom:24px}}
  h1{{font-family:{disp},serif;font-weight:{'600' if not dark else '600'};margin:0;
      font-size:{'34px' if not dark else '20px'};{upper if dark else ''}}}
  .subtitle{{font-family:{disp},serif;color:{t['good']};font-size:14px;margin-top:8px;letter-spacing:.05em}}
  .meta{{font-family:{mono};font-size:12px;color:{t['dim']};text-align:right;line-height:1.7}}
  .kpis{{display:grid;grid-template-columns:repeat(6,1fr);gap:14px;margin-bottom:22px}}
  .kpi{{background:{t['panel']};border-radius:{radius};padding:16px 18px;{('border:'+border) if dark else 'box-shadow:'+shadow}}}
  .kpi .k{{font-size:11px;color:{t['dim']};{upper};font-family:{mono if dark else body}}}
  .kpi .v{{font-family:{mono};font-size:{'24px' if not dark else '21px'};font-weight:600;margin-top:6px}}
  .grid{{display:grid;grid-template-columns:repeat(6,1fr);gap:16px}}
  .card{{background:{t['panel']};border-radius:{radius};padding:20px 22px;
        {('border:'+border) if dark else 'box-shadow:'+shadow}}}
  .c3{{grid-column:span 3}} .c2{{grid-column:span 2}} .c6{{grid-column:span 6}} .c4{{grid-column:span 4}}
  .card h3{{margin:0 0 4px;font-family:{disp},serif;font-weight:600;
           font-size:{'17px' if not dark else '12px'};{upper if dark else ''}}}
  .card .hint{{font-size:11px;color:{t['dim']};margin-bottom:14px;font-family:{mono if dark else body}}}
  .chart{{width:100%;height:260px}} .chart.sm{{height:170px}} .chart.lg{{height:300px}}
  .row{{display:flex;justify-content:space-between;align-items:center;font-size:13px;
       padding:7px 0;border-bottom:1px solid {t['grid']}}}
  .row b{{font-family:{mono};font-weight:600}}
  .alert{{font-size:12.5px;padding:7px 0;border-bottom:1px solid {t['grid']};font-family:{body}}}
  .tag{{font-family:{mono};font-size:11px;color:{t['dim']};{upper}}}
  .big{{font-family:{disp},serif;font-size:{'30px' if not dark else '24px'};font-weight:700;font-feature-settings:'tnum'}}
  .pos{{display:flex;align-items:center;gap:10px;margin:6px 0;font-size:12px}}
  .pos .bar{{flex:1;height:{'8px' if dark else '10px'};background:{t['grid']};border-radius:6px;overflow:hidden}}
  .pos .bar i{{display:block;height:100%;border-radius:6px}}
  .pos .nm{{width:118px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex-shrink:0}}
  .pos .vv{{width:118px;text-align:right;font-family:{mono};color:{t['dim']};white-space:nowrap;flex-shrink:0}}
  .pos .pl{{width:100px;text-align:right;font-family:{mono};white-space:nowrap;flex-shrink:0}}
  #c_trend_range button{{font-family:{mono};font-size:11.5px;margin-left:6px;padding:3px 10px;
    border:1px solid {t['grid']};background:{t['panel']};color:{t['ink']};border-radius:6px;cursor:pointer}}
  #c_trend_acct{{font-family:{mono};font-size:11.5px;margin-right:12px;padding:3px 8px;
    border:1px solid {t['grid']};background:{t['panel']};color:{t['ink']};border-radius:6px;cursor:pointer}}
  .banner{{background:{t['panel']};border-radius:{radius};padding:11px 16px;margin-bottom:18px;
    font-size:12.5px;line-height:1.6;color:{t['ink']};{('border:'+border) if dark else 'box-shadow:'+shadow}}}
  .foot{{margin-top:26px;text-align:center;font-size:11px;color:{t['dim']};font-family:{mono}}}
  .cal-grid{{display:grid;grid-template-columns:repeat(7,1fr);gap:4px;font-size:10px}}
  .cal-h{{text-align:center;color:{t['dim']};font-family:{mono};padding:4px 0}}
  .cal-d{{min-height:52px;border:1px solid {t['grid']};border-radius:6px;padding:4px;background:{t['bg'] if not dark else t['panel']}}}
  .cal-d.cal-today{{border-color:{t['good']};box-shadow:0 0 0 1px {t['good']}44}}
  .cal-out{{opacity:.45}}
  .cal-n{{font-family:{mono};font-weight:600;font-size:11px;color:{t['dim']}}}
  .cal-ev{{font-size:9px;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;display:flex;align-items:center;gap:2px}}
  .sub-ico{{object-fit:contain;border-radius:4px;vertical-align:middle;flex-shrink:0}}
  @media(max-width:820px){{.kpis{{grid-template-columns:repeat(2,1fr)}}.grid{{grid-template-columns:1fr}}
    .c3,.c2,.c4,.c6{{grid-column:auto}}
    .pos .nm{{width:90px}}.pos .vv,.pos .pl{{width:auto}}}}
"""


def kpis_html(D, t):
    sr = D["savingsRate"]
    items = [
        ("总净资产", "¥" + wan(D["networth"]), t["ink"]),
        ("金融资产", "¥" + wan(D["financial"]), t["ink"]),
        ("权益/金融", f"{D['equity']/D['financial']:.0%}", t["palette"][1]),
        ("整体杠杆", f"{D['leverage']:.1%}", t["warn"]),
        ("月净现金流", f"{'+' if D['netCashflow']>=0 else ''}¥{D['netCashflow']:,.0f}",
         t["good"] if D["netCashflow"] >= 0 else t["bad"]),
        ("储蓄率", f"{sr:.0%}", t["good"] if sr >= 0.3 else t["warn"]),
    ]
    return "".join(f'<div class="kpi"><div class="k">{k}</div>'
                   f'<div class="v" style="color:{c}">{v}</div></div>' for k, v, c in items)


def banner_html(D, t):
    """数据健康/手动账户过期 提示横条；无问题则不显示。"""
    msgs = []
    if not D.get("fxLive", True):
        msgs.append(("warn", "汇率为兜底值（非实时），美股/港股市值可能略有偏差"))
    for lv, m in D.get("warnings", []):
        msgs.append(("warn" if lv == "warn" else "info", m))
    if not msgs:
        return ""
    color = t["bad"] if any(lv == "warn" for lv, _ in msgs) else t["warn"]
    items = "　·　".join(("⚠️ " if lv == "warn" else "ℹ️ ") + m for lv, m in msgs)
    return (f'<div class="banner" style="border-left:4px solid {color}">'
            f'<b style="color:{color}">数据提示</b>　{items}</div>')


def alerts_html(D):
    out = [f'<div class="alert">{lvl} {msg}</div>' for lvl, msg in D["alerts"]]
    out += [f'<div class="alert">{"⚠️" if lv=="warn" else "ℹ️"} {m}</div>' for lv, m in D["warnings"]]
    return "".join(out) or '<div class="alert">✅ 无告警</div>'


def positions_html(D, t):
    out = []
    for name, val, single, *pl in D["positions"][:10]:
        p = val / D["networth"]
        col = t["bad"] if (single and p > 0.10) else (t["warn"] if single else t["palette"][3])
        pnl_txt = ""
        if pl and pl[0] is not None:
            pnl, pct = pl
            pc = t["good"] if pnl >= 0 else t["bad"]
            sign = "+" if pnl >= 0 else ""
            pnl_txt = (f'<span style="color:{pc}">{sign}{wan(pnl)}'
                       f' {sign}{pct:.0%}</span>')
        out.append(f'<div class="pos"><div class="nm">{name}</div>'
                   f'<div class="bar"><i style="width:{p*100:.1f}%;background:{col}"></i></div>'
                   f'<div class="vv">{p:.1%} · ¥{wan(val)}</div>'
                   f'<div class="pl">{pnl_txt}</div></div>')
    return "".join(out)


def dca_row(D, t):
    dca = D.get("dca", {})
    tgt = "、".join(dca.get("targets", {}).keys()) or "沪深300ETF"
    if dca.get("month", 0) <= 0:
        return (f'<div class="row" style="border-bottom:none"><span>定投计划</span>'
                f'<b style="color:{t["bad"]}">结余≤0，本月暂停</b></div>')
    freq = dca.get("freq", "每周")
    pct = f"结余×{dca.get('ratio',0):.0%}" if dca.get("mode") == "按结余比例" else "固定月额"
    return (f'<div class="row" style="border-bottom:none"><span>定投({pct})</span>'
            f'<b style="color:{t["good"]}">¥{dca["month"]:,.0f}/月 · {freq}¥{dca["per"]:,.0f} → {tgt}</b></div>')


def cashflow_html(D, t):
    def row(name, amt, bold=False):
        nm = f'<b>{name}</b>' if bold else name
        return (f'<div class="row"><span>{nm}</span>'
                f'<b style="color:{t["good"] if amt>=0 else t["bad"]}">'
                f'{"+" if amt>=0 else ""}¥{amt:,.0f}</b></div>')
    inc = D.get("incomeItems", [])
    if inc:
        groups = {}
        for i in inc:
            groups.setdefault(i.get("成员", "未分组"), []).append(i)
        multi = len(groups) > 1
        parts = []
        for m, items in groups.items():
            for i in items:
                label = f'{m}·{i["项目"]}' if multi else i["项目"]
                det = i.get("扣缴明细") or {}
                if det.get("五险一金"):
                    label += (f' <span style="font-size:11px;color:{t["dim"]}">'
                              f'(五险一金¥{det["五险一金"]:,.0f} 个税¥{det["个税"]:,.0f})</span>')
                parts.append(row(label, i["金额"]))
            if multi:
                parts.append(row(f'{m} 小计', sum(x["金额"] for x in items), bold=True))
        income_html = "".join(parts) + row("当月实发合计", D["income"], bold=True)
    else:
        income_html = row("当月实发(估)", D["income"])
    rows = "".join(row(i["项目"], i["金额"]) for i in D["flowItems"])
    if D.get("subsMonthly", 0) > 0:
        rows += row("订阅(月折算)", -D["subsMonthly"])
    return (f'{income_html}'
            f'{rows}'
            f'<div class="row"><span><b>月净现金流</b></span>'
            f'<b style="color:{t["good"] if D["netCashflow"]>=0 else t["bad"]}">'
            f'{"+" if D["netCashflow"]>=0 else ""}¥{D["netCashflow"]:,.0f}</b></div>'
            f'{dca_row(D, t)}'
            f'<div class="tag" style="margin-top:12px">被动收入≈¥{D["passiveMonth"]:,.0f}/月 · 覆盖固定支出 {D["coverage"]:.0%} · '
            f'应急可支撑 {D["runway"]:.0f} 个月 · 投资房若出租≈+¥{D["hypoRent"]:,.0f}/月</div>')


def platforms_html(D, t):
    """账户/平台分布（金融资产口径）：钱在哪 + 单平台集中度。"""
    ps = D.get("platforms", [])
    if not ps:
        return '<div class="tag">持仓表加「账户」列后显示</div>'
    fin = D.get("financial", 0) or 1
    lines = []
    for name, v in ps:
        pct = v / fin
        lines.append(f'<div class="row"><span>{name}</span>'
                     f'<b>¥{wan(v)} <span style="font-weight:400;color:{t["dim"]}">{pct:.0%}</span></b></div>')
    top_name, top_v = ps[0]
    foot = ""
    if top_v / fin > 0.5:
        foot = (f'<div class="tag" style="margin-top:10px;color:{t["warn"]}">'
                f'⚠ {top_name} 集中 {top_v/fin:.0%} 金融资产——单平台风险留意</div>')
    return "".join(lines) + foot


def lever_html(D, t):
    out = (f'<div class="row"><span>总资产(含房产)</span><b>¥{wan(D["grossAssets"])}</b></div>'
           f'<div class="row"><span>总负债</span><b style="color:{t["bad"]}">-¥{wan(D["totalDebt"])}</b></div>'
           f'<div class="row"><span>净资产</span><b>¥{wan(D["networth"])}</b></div>'
           f'<div class="row"><span>房产 LTV</span><b>{D["ltv"]:.1%}</b></div>'
           f'<div class="row"><span>整体杠杆率</span><b>{D["leverage"]:.1%}</b></div>')
    ln = D.get("loans", {})
    for x in ln.get("items", []):
        if x.get("状态") != "在还" or not x.get("余额"):
            continue
        end = f' · 还清约{x["还清约"]}' if x.get("还清约") else ""
        out += (f'<div class="row" style="opacity:.85"><span>💳 {x["名称"]}{end}</span>'
                f'<b>¥{x["余额"]:,.0f}</b></div>')
    if ln.get("items"):
        out += (f'<div class="tag" style="margin-top:10px">利息月耗合计 ¥{ln.get("totalInt",0):,}'
                f'（月供中的本金部分是资产内转移，非消耗）</div>')
    return out


def sub_icon_html(item, size=18):
    p = item.get("iconPath")
    em = item.get("图标", "📦")
    if p:
        return (f'<img class="sub-ico" src="{p}" width="{size}" height="{size}" '
                f'title="{em}" alt="">')
    return em


def ins_summary_html(D, t):
    items = D.get("insItems", [])
    if not items:
        return '<div class="tag">暂无保单 · 在「保险」Tab 录入</div>'
    lines = []
    for m, g in D.get("insByMember", {}).items():
        cover = " · ".join(f'{k} {v/10000:.0f}万' for k, v in g["保额"].items() if v > 0)
        lines.append(f'<div class="row"><span><b>{m}</b>（{g["保单数"]}单）{cover or "—"}</span>'
                     f'<b>¥{g["年缴合计"]:,.0f}/年</b></div>')
    dues = sorted((i for i in items if i.get("nextDue")), key=lambda x: x["nextDue"])
    for i in dues[:4]:
        till = f' · 保至{i["保障至"]}' if i.get("保障至") else ""
        lines.append(f'<div class="row" style="opacity:.85"><span>{i["图标"]} {i.get("成员","")}·{i.get("产品","")}'
                     f' 下次缴费 {i["nextDue"]}{till}</span>'
                     f'<b>¥{i.get("perDue",0):,}/{i.get("频率","年")}</b></div>')
    foot = (f'<div class="tag" style="margin-top:10px">缴费中年缴合计 ¥{D.get("insYearly",0):,} · '
            f'摊月 ¥{D.get("insMonthly",0):,}（已计入固定支出）</div>')
    return "".join(lines) + foot


def subs_summary_html(D, t):
    sm, sy = D.get("subsMonthly", 0), D.get("subsYearly", 0)
    if not sm and not D.get("subsItems"):
        return '<div class="tag">暂无订阅 · 在「订阅管理」添加</div>'
    lines = []
    for s in D.get("subsItems", []):
        if s.get("状态", "启用") != "启用":
            continue
        orig = f'{s["币种"]}{s["金额"]:.2f}' if s.get("币种") != "CNY" else f'¥{s["金额"]:.0f}'
        lines.append(
            f'<div class="row"><span>{sub_icon_html(s)} {s["名称"]} · {s.get("分类","其他")}</span>'
            f'<b>¥{s.get("monthlyCny",0):,}/月 <span style="font-weight:400;color:{t["dim"]}">({orig}/{s["周期"]})</span></b></div>')
    for cat, amt in sorted(D.get("subsByCategory", {}).items(), key=lambda x: -x[1]):
        icon = subs.CAT_STYLE.get(cat, ("📦",))[0]
        lines.append(f'<div class="row" style="opacity:.85"><span>{icon} {cat}</span><b>¥{amt:,}/月</b></div>')
    pct_fix = sm / D["fixedOut"] if D.get("fixedOut") else 0
    pct_inc = sm / D["income"] if D.get("income") else 0
    foot = (f'<div class="tag" style="margin-top:10px">本月合计 ¥{sm:,}/月 · 全年 ¥{sy:,} · '
            f'占固定支出 {pct_fix:.0%} · 占税后收入 {pct_inc:.0%}</div>')
    return "".join(lines) + foot + '<div id="c_subs_pie" class="chart sm" style="margin-top:12px"></div>'


def subs_calendar_html(D, t):
    import calendar
    import datetime
    today = datetime.date.today()
    y, m = today.year, today.month
    cal = D.get("subsCalendar", {})
    wd_names = ["一", "二", "三", "四", "五", "六", "日"]
    # 与 panorama_data 相同的窗口：当月按整周补齐，前后月补位日灰显（能看到昨天/下月初的扣费）
    first = datetime.date(y, m, 1)
    last = datetime.date(y, m, calendar.monthrange(y, m)[1])
    start = first - datetime.timedelta(days=first.weekday())
    end = last + datetime.timedelta(days=6 - last.weekday())
    cells = [f'<div class="cal-h">{w}</div>' for w in wd_names]
    d = start
    while d <= end:
        items = cal.get(d.isoformat(), [])
        cls = "cal-d"
        if d.month != m:
            cls += " cal-out"
        if d == today:
            cls += " cal-today"
        body = "".join(
            f'<div class="cal-ev">{sub_icon_html(it, 12)}¥{it["chargeCny"]}</div>' for it in items)
        cells.append(f'<div class="{cls}"><div class="cal-n">{d.day}</div>{body}</div>')
        d += datetime.timedelta(days=1)
    grid = "".join(cells)
    upcoming = D.get("subsUpcoming", [])
    up_lines = "".join(
        f'<div class="row"><span>{sub_icon_html(u)} {u["名称"]} · {u["days"]}天后</span>'
        f'<b>¥{u["chargeCny"]:,}</b></div>' for u in upcoming[:8])
    if not up_lines:
        up_lines = '<div class="tag">近 30 天无待扣</div>'
    return (f'<div class="cal-grid">{grid}</div>'
            f'<div style="margin-top:14px;font-size:12px;color:{t["dim"]}">近 30 天待扣</div>{up_lines}')


def render(theme_key, D=None):
    t = THEMES[theme_key]
    if D is None:
        D = collect()
    data_js = json.dumps(D, ensure_ascii=False)
    theme_js = json.dumps({k: t[k] for k in ("dark", "palette", "ink", "dim", "grid", "panel",
                                              "line1", "line2", "good", "warn", "bad")}, ensure_ascii=False)
    body = f"""
  <div class="wrap">
    <div class="top">
      <div><h1>资产全景</h1>{f'<div class="subtitle">{t["subtitle"]}</div>' if t.get('subtitle') else ''}</div>
      <div class="meta">{D['date']}<br>USD/CNY {D['fxUSD']:.3f} · HKD/CNY {D['fxHKD']:.3f}</div>
    </div>
    <div class="kpis">{kpis_html(D, t)}</div>
    {banner_html(D, t)}
    <div class="grid">
      <div class="card c3"><h3>资产构成</h3><div class="hint">净资产 ¥{wan(D['networth'])} · 环形=大类占比</div><div id="c_donut" class="chart"></div></div>
      <div class="card c3"><h3>净值走势</h3><div class="hint">总净资产 vs 金融资产</div><div id="c_line" class="chart"></div></div>
      <div class="card c6"><h3>持仓地图 · 旭日图</h3><div class="hint">大类 → 子类 → 持仓 · 点击逐层下钻（点中心返回）· 悬停看明细</div>
        <div style="display:flex;gap:18px;align-items:center;flex-wrap:wrap">
          <div id="c_tree" class="chart" style="height:440px;flex:1;min-width:340px;position:relative"></div>
          <div id="c_tree_legend" style="width:160px"></div>
        </div></div>
      <div class="card c6"><h3>选中资产走势<span id="c_trend_range" style="float:right;font-weight:400"><button data-r="1m">近1月</button><button data-r="3m">近3月</button><button data-r="all">全部</button></span><select id="c_trend_acct" style="float:right;font-weight:400"></select></h3><div class="hint" id="c_trend_hint">点击上方图表的大类/子类/持仓，查看其走势</div><div id="c_trend" class="chart" style="height:240px"></div></div>
      <div class="card c6"><h3>🏋️ 杠铃视图</h3><div class="hint">安全腿(亏不了) · 核心(多元化β) · 冒险腿(非对称) · 中间(待审视) — 安全腿可覆盖现金流缺口 {D['safetyMonths']} 个月</div><div id="c_barbell" class="chart sm"></div></div>
      <div class="card c2"><h3>地域敞口</h3><div class="hint">全资产</div><div id="c_region" class="chart sm"></div></div>
      <div class="card c2"><h3>币种敞口</h3><div class="hint">金融资产·底层FX</div><div id="c_ccy" class="chart sm"></div></div>
      <div class="card c2"><h3>流动性分层</h3><div class="hint">变现速度</div><div id="c_liq" class="chart sm"></div></div>
      <div class="card c4"><h3>个股集中度</h3><div class="hint">多元化 ¥{wan(D['broad'])} vs 集中(个股+杠杆) ¥{wan(D['concentrated'])} · 最大持仓Top10 · 累计浮盈 <b style="color:{t['good'] if D.get('pnlTotal',0)>=0 else t['bad']}">{'+' if D.get('pnlTotal',0)>=0 else ''}{wan(D.get('pnlTotal',0))}</b></div>{positions_html(D, t)}</div>
      <div class="card c2"><h3>再平衡告警</h3><div class="hint">规则触发</div>{alerts_html(D)}</div>
      <div class="card c3"><h3>现金流 & 可持续性</h3><div class="hint">收入 − 固定支出(含订阅月折算)</div>{cashflow_html(D, t)}</div>
      <div class="card c3"><h3>📆 订阅 · 月 ¥{D.get("subsMonthly",0):,} / 年 ¥{D.get("subsYearly",0):,}</h3><div class="hint">年/季/周付已折算为月</div>{subs_summary_html(D, t)}</div>
      <div class="card c3"><h3>本月扣费日历</h3><div class="hint">{D['date'][:7] if D.get('date') else ''} · 含前后月补位日(灰显) · 已扣的也显示</div>{subs_calendar_html(D, t)}</div>
      <div class="card c3"><h3>储蓄率</h3><div class="hint">固定支出后结余/收入</div><div id="g_save" class="chart sm"></div></div>
      <div class="card c3"><h3>现金流月度走势</h3><div class="hint">净结余(柱) + 储蓄率(线) · ✅已对账实心 ⏳草稿空心 · 去「月度对账」确认</div><div id="c_cf_trend" class="chart sm"></div></div>
      <div class="card c3"><h3>🛡️ 保险保障</h3><div class="hint">按成员 · 保额与年缴 · 近期缴费</div>{ins_summary_html(D, t)}</div>
      <div class="card c2"><h3>负债与杠杆</h3><div class="hint">含房产口径</div>{lever_html(D, t)}</div>
      <div class="card c2"><h3>账户/平台分布</h3><div class="hint">金融资产在哪个平台 · 应急可查</div>{platforms_html(D, t)}</div>
      <div class="card c4"><h3>目标 vs 实际</h3><div class="hint">柱=实际，虚线=目标</div><div id="c_target" class="chart sm"></div></div>
    </div>
    <div class="foot">本地生成 · 数据源 新浪+腾讯行情 + open.er-api 汇率 · ECharts 渲染需联网 · 仅供个人参考，非投资建议</div>
  </div>"""

    html = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>资产全景 · {t['name']}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?{t['fonts']}&display=swap" rel="stylesheet">
<script src="{ECHARTS}"></script>
<style>{css(t)}</style></head><body>{body}
<script>
const D={data_js}, T={theme_js};
const fmtWan=v=>'¥'+(v/1e4).toFixed(1)+'万';
const axisText=T.dim, gridc=T.grid;
function mkBar(id,obj,color){{
  const ks=Object.keys(obj).sort((a,b)=>obj[b]-obj[a]);
  echarts.init(document.getElementById(id)).setOption({{
    grid:{{left:64,right:54,top:8,bottom:8}},
    xAxis:{{type:'value',show:false,max:Math.max(...ks.map(k=>obj[k]))*1.18}},
    yAxis:{{type:'category',inverse:true,data:ks,axisLine:{{show:false}},axisTick:{{show:false}},
      axisLabel:{{color:T.ink,fontSize:11}}}},
    series:[{{type:'bar',data:ks.map((k,i)=>({{value:obj[k],itemStyle:{{color:T.palette[i%T.palette.length],borderRadius:3}}}})),
      barWidth:'56%',label:{{show:true,position:'right',color:axisText,fontFamily:'IBM Plex Mono',fontSize:10,
        formatter:p=>fmtWan(p.value)}}}}]
  }});
}}
// 环形
(()=>{{
  const order=['房产','权益','债券类固收','现金','黄金'];
  const data=order.filter(k=>D.classes[k]).map((k,i)=>({{name:k,value:D.classes[k],
    itemStyle:{{color:T.palette[i%T.palette.length]}}}}));
  echarts.init(document.getElementById('c_donut')).setOption({{
    tooltip:{{trigger:'item',formatter:p=>`${{p.name}}<br>${{fmtWan(p.value)}} · ${{p.percent}}%`}},
    legend:{{orient:'vertical',right:0,top:'center',textStyle:{{color:T.ink,fontSize:11}},itemWidth:10,itemHeight:10}},
    series:[{{type:'pie',radius:['56%','82%'],center:['38%','50%'],avoidLabelOverlap:false,
      label:{{show:true,position:'center',formatter:fmtWan(D.networth),color:T.ink,fontSize:15,fontFamily:'IBM Plex Mono'}},
      labelLine:{{show:false}},data}}]
  }});
}})();
// 净值线
(()=>{{
  const h=D.history;
  // 大额交易标记(单笔≥金融资产5%):小额定投不标
  const di={{}};h.forEach((r,i)=>{{di[r.date]=i;}});
  const bigByDate={{}};
  (D.bigTrades||[]).forEach(t=>{{(bigByDate[t[0]]=bigByDate[t[0]]||[]).push(t);}});
  const bmk=Object.keys(bigByDate).filter(d=>di[d]!=null).map(d=>({{
    coord:[di[d],h[di[d]].总净资产],symbol:'diamond',symbolSize:13,
    itemStyle:{{color:T.warn,borderColor:T.dim,borderWidth:1}},
    info:bigByDate[d].map(t=>t[0]+' '+t[2]+' '+t[1]+' '+fmtWan(t[3])).join('<br>')}}));
  echarts.init(document.getElementById('c_line')).setOption({{
    tooltip:{{trigger:'axis',valueFormatter:fmtWan}},
    legend:{{data:['总净资产','金融资产'],textStyle:{{color:T.ink,fontSize:11}},top:0}},
    grid:{{left:54,right:14,top:34,bottom:24}},
    xAxis:{{type:'category',data:h.map(r=>r.date),axisLine:{{lineStyle:{{color:gridc}}}},
      axisLabel:{{color:axisText,fontSize:10}}}},
    yAxis:{{type:'value',scale:true,axisLabel:{{color:axisText,fontSize:10,formatter:v=>(v/1e4).toFixed(0)+'万'}},
      splitLine:{{lineStyle:{{color:gridc}}}}}},
    series:[
      {{name:'总净资产',type:'line',smooth:true,symbol:'circle',symbolSize:6,data:h.map(r=>r.总净资产),
        lineStyle:{{color:T.line1,width:2.5}},itemStyle:{{color:T.line1}},
        areaStyle:{{color:new echarts.graphic.LinearGradient(0,0,0,1,[{{offset:0,color:T.line1+'44'}},{{offset:1,color:T.line1+'05'}}])}},
        markPoint:{{data:bmk,label:{{show:false}},
          tooltip:{{trigger:'item',formatter:p=>(p.data&&p.data.info)||''}}}}}},
      {{name:'金融资产',type:'line',smooth:true,symbol:'circle',symbolSize:6,data:h.map(r=>r.金融资产),
        lineStyle:{{color:T.line2,width:2.5}},itemStyle:{{color:T.line2}}}}
    ]
  }});
}})();
// 持仓旭日图 Sunburst（三层可下钻）
(()=>{{
  const palette=T.palette, total=D.networth;
  const lighten=(hex,a)=>{{const n=parseInt(hex.slice(1),16),r=(n>>16)&255,g=(n>>8)&255,b=n&255,
    m=v=>Math.round(v+(255-v)*a);return 'rgb('+m(r)+','+m(g)+','+m(b)+')';}};
  const CCOL={{'房产':palette[0],'权益':palette[1],'债券类固收':palette[2],'现金':palette[3],'黄金':palette[4]}};
  const data=D.tree.map((c,i)=>{{
    const base=CCOL[c.name]||palette[i%palette.length], mid=lighten(base,0.30), leaf=lighten(base,0.55);
    return {{name:c.name,itemStyle:{{color:base}},
      children:(c.children||[]).map(s=>({{name:s.name,itemStyle:{{color:mid}},
        children:(s.children||[]).map(x=>({{name:x.name,value:x.value,itemStyle:{{color:leaf}}}}))}}))}};
  }});
  const el=document.getElementById('c_tree');
  const chart=echarts.init(el);
  el.style.position='relative';
  const lbl=document.createElement('div');
  lbl.title='点击返回最上层';
  lbl.style.cssText='position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);'
    +'width:132px;height:132px;border-radius:50%;display:flex;flex-direction:column;'
    +'align-items:center;justify-content:center;text-align:center;cursor:pointer;'
    +'z-index:5;line-height:1.4;font-family:monospace;background:'+T.panel+';color:'+T.ink;
  el.appendChild(lbl);
  const setLbl=(name,val)=>{{lbl.innerHTML='<div style="font-size:12px;color:'+T.dim+'">'+name
    +'</div><div style="font-size:17px;font-weight:600">'+fmtWan(val)
    +'</div><div style="font-size:12px;color:'+T.dim+'">'+(val/total*100).toFixed(1)+'%</div>';}};
  let curRoot={{name:'净资产',val:total}};
  const resetLbl=()=>setLbl(curRoot.name,curRoot.val);
  resetLbl();
  const opt={{
    tooltip:{{trigger:'item',formatter:p=>`${{p.name}}<br>${{fmtWan(p.value)}} · ${{(p.value/total*100).toFixed(1)}}%`}},
    series:[{{type:'sunburst',data,radius:['30%','99%'],center:['50%','50%'],
      sort:null,nodeClick:false,emphasis:{{focus:'ancestor'}},
      itemStyle:{{borderColor:T.panel,borderWidth:2,borderRadius:3}},
      levels:[
        {{}},
        {{r0:'30%',r:'50%',label:{{rotate:'tangential',color:'#ffffff',fontSize:12,fontWeight:'600',
          textBorderColor:T.dark?'rgba(0,0,0,.65)':'rgba(0,0,0,0)',textBorderWidth:T.dark?2:0,
          fontFamily:'IBM Plex Sans',formatter:p=>p.name,minAngle:8}},itemStyle:{{borderWidth:2}}}},
        {{r0:'50%',r:'72%',label:{{rotate:'tangential',color:T.dark?'#0a0e12':'#33332d',fontSize:10,
          fontFamily:'IBM Plex Sans',formatter:p=>p.value/total>=0.03?p.name:'',minAngle:8}},itemStyle:{{borderWidth:1.5}}}},
        {{r0:'72%',r:'99%',label:{{rotate:'radial',color:T.dark?'#12121a':'#4a4a44',fontSize:9.5,fontWeight:T.dark?'600':'normal',
          fontFamily:'IBM Plex Sans',formatter:p=>p.value/total>=0.025?p.name:'',minAngle:6}},itemStyle:{{borderWidth:1}}}}
      ]
    }}]
  }};
  chart.setOption(opt);
  const renderAt=(arr)=>chart.setOption({{series:[{{data:arr}}]}});
  lbl.addEventListener('click',()=>{{renderAt(data);curRoot={{name:'净资产',val:total}};resetLbl();}});
  chart.on('mouseover',p=>{{if(p.data&&p.value!=null)setLbl(p.name,p.value);}});
  chart.on('mouseout',resetLbl);
  chart.on('click',p=>{{const nm=p.name,nd=p.data;
    if(nd&&nd.children&&nd.children.length){{curRoot={{name:nm,val:p.value}};resetLbl();renderAt(nd.children);}}
    if(window._catNames&&window._catNames.has(nm)&&D.trends['cat:'+nm])window._showTrend&&window._showTrend('cat:'+nm,nm);
    else if(window._subMembers&&window._subMembers[nm]){{
      const mem=window._subMembers[nm].filter(n=>D.trends['hold:'+n]);
      if(mem.length){{const dset=new Set();
        const maps=mem.map(n=>{{const o={{}};D.trends['hold:'+n].forEach(a=>{{o[a[0]]=a[1];dset.add(a[0]);}});return o;}});
        const ax=Array.from(dset).sort(),last=new Array(maps.length).fill(null);
        const series=ax.map(d=>{{let s=0;for(let i=0;i<maps.length;i++){{if(maps[i][d]!=null)last[i]=maps[i][d];if(last[i]!=null)s+=last[i];}}return [d,s];}});
        window._renderTrend(series,nm);}}
      else window._renderTrend&&window._renderTrend(null,nm);}}
    else if(D.trends['hold:'+nm])window._showTrend&&window._showTrend('hold:'+nm,nm);
    else window._showTrend&&window._showTrend(null,nm);}});
  const lg=document.getElementById('c_tree_legend');
  if(lg){{lg.innerHTML=D.tree.map((c,i)=>{{
    const val=D.classes[c.name]||0,col=palette[i%palette.length];
    return '<div style="display:flex;align-items:center;gap:7px;margin:9px 0;font-size:12.5px">'
      +'<i style="width:12px;height:12px;border-radius:3px;background:'+col+';flex:none"></i>'
      +'<span style="flex:1">'+c.name+'</span>'
      +'<b style="font-family:monospace">'+(val/total*100).toFixed(1)+'%</b></div>';
  }}).join('');}}
}})();
mkBar('c_region',D.regionAll);
mkBar('c_ccy',D.ccy);
mkBar('c_liq',D.liq);
(()=>{{
  const seg=[['安全腿',D.barbell['安全腿'],T.good],['核心',D.barbell['核心'],T.line1],
    ['冒险腿',D.barbell['冒险腿'],T.bad],['中间',D.barbell['中间'],T.warn]];
  const tot=seg.reduce((s,x)=>s+x[1],0)||1;
  echarts.init(document.getElementById('c_barbell')).setOption({{
    grid:{{left:64,right:88,top:8,bottom:8}},
    xAxis:{{type:'value',show:false,max:Math.max(...seg.map(x=>x[1]))*1.2}},
    yAxis:{{type:'category',inverse:true,data:seg.map(x=>x[0]),axisLine:{{show:false}},
      axisTick:{{show:false}},axisLabel:{{color:T.ink,fontSize:11}}}},
    series:[{{type:'bar',barWidth:'56%',data:seg.map(x=>({{value:x[1],itemStyle:{{color:x[2],borderRadius:3}}}})),
      label:{{show:true,position:'right',color:T.dim,fontFamily:'IBM Plex Mono',fontSize:10,
        formatter:p=>'¥'+(p.value/1e4).toFixed(0)+'万 '+(p.value/tot*100).toFixed(0)+'%'}}}}]
  }});
}})();
// 储蓄率 gauge
(()=>{{
  const v=D.savingsRate*100;
  echarts.init(document.getElementById('g_save')).setOption({{
    series:[{{type:'gauge',startAngle:210,endAngle:-30,min:0,max:60,radius:'92%',center:['50%','62%'],
      progress:{{show:true,width:12,itemStyle:{{color:v>=30?T.good:T.warn}}}},
      axisLine:{{lineStyle:{{width:12,color:[[1,T.grid]]}}}},
      axisTick:{{show:false}},splitLine:{{show:false}},axisLabel:{{show:false}},pointer:{{show:false}},
      anchor:{{show:false}},title:{{show:false}},
      detail:{{valueAnimation:true,fontFamily:'IBM Plex Mono',fontSize:24,color:T.ink,offsetCenter:[0,0],
        formatter:v.toFixed(0)+'%'}},
      data:[{{value:v}}]}}]
  }});
}})();
// 现金流月度走势(净结余柱 + 储蓄率线，双轴)
(()=>{{
  const h=D.cashflowHistory||[];
  const el=document.getElementById('c_cf_trend');
  if(!el) return;
  if(!h.length){{el.innerHTML='<div style="color:'+T.dim+';font-size:12px;padding:20px 4px">尚无月度记录 · 每次重新估值会记下当月草稿，对账确认后锁定</div>';return;}}
  const barData=h.map(r=>{{
    const ok=r.已对账==='是';
    return {{value:r.净结余,itemStyle:{{color:ok?T.line1:'rgba(0,0,0,0)',borderColor:T.line1,
      borderWidth:ok?0:2,borderType:ok?'solid':'dashed',borderRadius:3}}}};
  }});
  const lineData=h.map(r=>({{
    value:+(r.储蓄率*100).toFixed(1),
    symbol:r.已对账==='是'?'circle':'emptyCircle',
    itemStyle:{{color:T.warn,borderColor:T.warn}}
  }}));
  echarts.init(el).setOption({{
    tooltip:{{trigger:'axis',formatter:params=>{{
      const i=params[0].dataIndex, r=h[i];
      const tag=r.已对账==='是'?'✅已对账':'⏳草稿';
      return r.月份+' '+tag+'<br>净结余 ¥'+r.净结余.toLocaleString()+
        '<br>储蓄率 '+(r.储蓄率*100).toFixed(1)+'%'+
        (r.其他实际支出?'<br>其他支出 ¥'+r.其他实际支出.toLocaleString():'');
    }}}},
    legend:{{data:['净结余','储蓄率'],textStyle:{{color:T.ink,fontSize:10}},top:0}},
    grid:{{left:52,right:44,top:28,bottom:22}},
    xAxis:{{type:'category',data:h.map(r=>r.月份),axisLabel:{{color:axisText,fontSize:10}},
      axisLine:{{lineStyle:{{color:gridc}}}}}},
    yAxis:[
      {{type:'value',axisLabel:{{color:axisText,fontSize:9,formatter:v=>(v/1e4).toFixed(1)+'万'}},
        splitLine:{{lineStyle:{{color:gridc}}}}}},
      {{type:'value',min:0,max:60,axisLabel:{{color:axisText,fontSize:9,formatter:'{{value}}%'}},
        splitLine:{{show:false}}}}
    ],
    series:[
      {{name:'净结余',type:'bar',yAxisIndex:0,barWidth:'46%',data:barData}},
      {{name:'储蓄率',type:'line',yAxisIndex:1,smooth:true,symbolSize:6,
        data:lineData,lineStyle:{{color:T.warn,width:2.5}}}}
    ]
  }});
}})();
// 目标 vs 实际
(()=>{{
  const order=['房产','权益','债券类固收','现金','黄金'];
  const cats=order.filter(k=>D.classes[k]);
  const act=cats.map(k=>+(D.classes[k]/D.networth*100).toFixed(1));
  const tgt=cats.map(k=>(D.target[k]||0)*100);
  echarts.init(document.getElementById('c_target')).setOption({{
    tooltip:{{trigger:'axis',valueFormatter:v=>v+'%'}},
    grid:{{left:64,right:16,top:8,bottom:20}},
    xAxis:{{type:'value',axisLabel:{{color:axisText,fontSize:10,formatter:'{{value}}%'}},splitLine:{{lineStyle:{{color:gridc}}}}}},
    yAxis:{{type:'category',inverse:true,data:cats,axisLabel:{{color:T.ink,fontSize:11}},
      axisLine:{{show:false}},axisTick:{{show:false}}}},
    series:[
      {{type:'bar',data:act.map((v,i)=>({{value:v,itemStyle:{{color:T.palette[i%T.palette.length],borderRadius:3}}}})),barWidth:'46%'}},
      {{type:'scatter',data:tgt.map((v,i)=>[v,i]),symbol:'rect',symbolSize:[3,22],
        itemStyle:{{color:T.ink}},tooltip:{{show:false}}}}
    ]
  }});
}})();
// 订阅分类饼图
(()=>{{
  const obj=D.subsByCategory||{{}};
  const ks=Object.keys(obj);
  if(!ks.length) return;
  const data=ks.map((k,i)=>({{name:k,value:obj[k],itemStyle:{{color:T.palette[i%T.palette.length]}}}}));
  echarts.init(document.getElementById('c_subs_pie')).setOption({{
    tooltip:{{trigger:'item',formatter:p=>p.name+'<br>¥'+p.value.toLocaleString()+'/月'}},
    series:[{{type:'pie',radius:['42%','68%'],center:['50%','50%'],label:{{fontSize:10,color:T.ink}},
      data}}]
  }});
}})();
// 选中资产走势(联动面板：区间切换 + 子类聚合)
(()=>{{
  const tc=echarts.init(document.getElementById('c_trend'));
  const hint=document.getElementById('c_trend_hint');
  // 三模式:mv=市值+成本双线(个股默认) / px=价格曲线(切换) / val=市值线(期权/账户/大类兜底)
  let curTitle='',curRange='all',curName=null,curMode='val',curData=null;
  const RNG={{'all':1e9,'3m':66,'1m':22}};
  const CSYM={{USD:'$',HKD:'HK$',CNY:'¥'}};
  const fmtPx=v=>v>=100?v.toFixed(0):v>=10?v.toFixed(2):v.toFixed(3);
  const AXIS=s=>({{type:'category',data:s.map(p=>p[0]),boundaryGap:false,
    axisLabel:{{color:T.dim,fontSize:10,formatter:(v,i)=>i%Math.ceil(s.length/6)===0?String(v).slice(5):''}},
    axisLine:{{lineStyle:{{color:T.grid}}}}}});
  const marksOf=(s,yOf)=>{{
    const di={{}};s.forEach((p,i)=>{{di[p[0]]=i;}});
    const mpts=[];
    (curName?((D.tradeMarks||{{}})[curName]||[]):[]).forEach(m=>{{
      const i=di[m[0]];if(i==null)return;
      const buy=m[1]==='买入';
      mpts.push({{coord:[i,yOf(i,m)],name:m[1],symbol:'triangle',symbolRotate:buy?0:180,
        symbolSize:12,itemStyle:{{color:buy?T.bad:T.good,borderColor:T.dim,borderWidth:1}},
        info:m[0]+' '+m[1]+' '+m[2]+'@'+(m[3]||'?')+(m[4]!=null?' · '+fmtWan(m[4]):'')}});
    }});
    return {{data:mpts,label:{{show:false}},tooltip:{{trigger:'item',formatter:p=>(p.data&&p.data.info)||''}}}};
  }};
  function draw(){{
    let s=curData?(curMode==='mv'?curData.mv:curMode==='px'?curData.px:curData.val):null;
    if(!s||!s.length){{hint.textContent=curTitle?(curTitle+'：暂无走势数据（无行情或未记录）'):'点击上方图表的大类/子类/持仓，查看其走势';tc.clear();return;}}
    if(curRange!=='all')s=s.slice(-RNG[curRange]);
    if(curMode==='mv'){{
      // 市值面积+净投入成本阶梯线:台阶=加减仓,间距=浮盈(盈浅红/亏浅绿)
      const mv=s.map(p=>p[1]),cost=s.map(p=>p[2]);
      const lm=mv[mv.length-1],lc=cost[cost.length-1],pnl=lm-lc,pct=lc?pnl/lc:0;
      hint.textContent=curTitle+' · 市值'+fmtWan(lm)+' · 净投入'+fmtWan(lc)
        +' · 浮盈'+(pnl>=0?'+':'−')+fmtWan(Math.abs(pnl))
        +'('+(pct>=0?'+':'')+(pct*100).toFixed(1)+'%) · 期初06/24起';
      const bandBase=s.map(p=>Math.min(p[1],p[2]));
      const bandUp=s.map(p=>Math.max(p[1]-p[2],0));
      const bandDn=s.map(p=>Math.max(p[2]-p[1],0));
      const mute={{symbol:'none',lineStyle:{{opacity:0}},silent:true,emphasis:{{disabled:true}},tooltip:{{show:false}},stack:'pnl'}};
      tc.setOption({{
        tooltip:{{trigger:'axis',formatter:ps=>{{const i=ps[0].dataIndex,m0=s[i][1],c0=s[i][2],d0=m0-c0;
          return s[i][0]+'<br>市值 '+fmtWan(m0)+' · 净投入 '+fmtWan(c0)
            +'<br>浮盈 '+(d0>=0?'+':'−')+fmtWan(Math.abs(d0))+'('+(c0?(d0/c0*100).toFixed(1):'0.0')+'%)';}}}},
        grid:{{left:58,right:18,top:16,bottom:26}},
        xAxis:AXIS(s),
        yAxis:{{type:'value',scale:true,axisLabel:{{color:T.dim,fontSize:10,formatter:v=>(v/1e4).toFixed(0)+'万'}},
          splitLine:{{lineStyle:{{color:T.grid}}}}}},
        series:[
          Object.assign({{type:'line',data:bandBase,areaStyle:{{opacity:0}}}},mute),
          Object.assign({{type:'line',data:bandUp,areaStyle:{{color:'rgba(220,38,38,.14)'}}}},mute),
          Object.assign({{type:'line',data:bandDn,areaStyle:{{color:'rgba(22,163,74,.14)'}}}},mute),
          {{type:'line',data:cost,symbol:'none',step:'end',z:3,lineStyle:{{color:T.dim,width:2,type:'dashed'}}}},
          {{type:'line',data:mv,symbol:'none',z:4,lineStyle:{{color:T.line1,width:2.5}},
            markPoint:marksOf(s,i=>s[i][1])}}
        ]
      }});
      return;
    }}
    const isPx=curMode==='px';
    const meta=isPx?((D.pxMeta||{{}})[curName]||{{}}):{{}};
    const csym=CSYM[meta.ccy||'CNY']||'¥';
    const FXR={{USD:D.fxUSD||1,HKD:D.fxHKD||1,CNY:1}}[meta.ccy||'CNY']||1;
    const qat=d=>{{let q=0;(meta.qty||[]).forEach(e=>{{if(e[0]<=d)q+=e[1];}});return q;}};
    // 价格曲线:区间=纯价格涨跌;市值曲线:简单Dietz剔除窗口内台账净投入
    let nbSum=0;
    if(!isPx&&curName){{const nbd=(D.tradeNet||{{}})[curName]||{{}};
      for(const d in nbd)if(d>s[0][0]&&d<=s[s.length-1][0])nbSum+=nbd[d];}}
    const f=s[0][1],l=s[s.length-1][1],base=f+nbSum/2,chg=base?(l-f-nbSum)/base:0;
    hint.textContent=curTitle+' · '+s.length+'点 · '+(isPx?'价格'+csym+fmtPx(l)+' · ':'')
      +'区间'+(chg>=0?'+':'')+(chg*100).toFixed(1)+'%'
      +(nbSum?' · 已剔除净投入'+fmtWan(nbSum):'');
    tc.setOption({{
      tooltip:{{trigger:'axis',
        formatter:isPx?(ps=>{{const p=ps[0];const d=s[p.dataIndex][0],q=qat(d);
            return d+' · '+csym+fmtPx(p.value)+(q>0?'<br>市值 '+fmtWan(p.value*q*FXR)+'（'+(q%1?q.toFixed(2):q)+'份）':'');}})
          :(ps=>{{const p=ps[0];return s[p.dataIndex][0]+' · '+fmtWan(p.value);}})}},
      grid:{{left:58,right:18,top:16,bottom:26}},
      xAxis:AXIS(s),
      yAxis:{{type:'value',scale:true,axisLabel:{{color:T.dim,fontSize:10,
        formatter:isPx?(v=>fmtPx(v)):(v=>(v/1e4).toFixed(0)+'万')}},
        splitLine:{{lineStyle:{{color:T.grid}}}}}},
      series:[{{type:'line',smooth:true,symbol:'none',data:s.map(p=>p[1]),
        lineStyle:{{color:T.line1,width:2.5}},
        areaStyle:{{color:new echarts.graphic.LinearGradient(0,0,0,1,[{{offset:0,color:T.line1+'33'}},{{offset:1,color:T.line1+'05'}}])}},
        markPoint:marksOf(s,(i,m)=>isPx?(parseFloat(m[3])||s[i][1]):s[i][1])}}]
    }});
  }}
  window._renderTrend=(series,title,name)=>{{curData={{val:series||null}};curTitle=title;curName=name||null;curMode='val';draw();}};
  window._showTrend=(key,title)=>{{
    const nm=(key&&key.indexOf('hold:')===0)?key.slice(5):null;
    curData={{mv:nm?(D.trends['mvc:'+nm]||null):null,
             px:nm?(D.trends['px:'+nm]||null):null,
             val:key?(D.trends[key]||null):null}};
    curTitle=title;curName=nm;
    curMode=curData.mv?'mv':(curData.px?'px':'val');
    draw();
  }};
  window._subMembers={{}};D.tree.forEach(c=>(c.children||[]).forEach(s=>{{window._subMembers[s.name]=(s.children||[]).map(x=>x.name);}}));
  window._catNames=new Set(D.tree.map(c=>c.name));
  const rb=document.getElementById('c_trend_range');
  if(rb)rb.querySelectorAll('button').forEach(b=>{{b.style.opacity=b.dataset.r==='all'?'1':'.5';
    b.addEventListener('click',()=>{{curRange=b.dataset.r;
      rb.querySelectorAll('button').forEach(x=>x.style.opacity=x===b?'1':'.5');draw();}});}});
  const sel=document.getElementById('c_trend_acct');
  if(sel){{
    const accts=Object.keys(D.trends).filter(k=>k.indexOf('acct:')===0).map(k=>k.slice(5));
    sel.innerHTML='<option value="">— 看账户历史 —</option>'+accts.map(n=>'<option value="'+n+'">'+n+'</option>').join('');
    sel.addEventListener('change',()=>{{if(sel.value)window._showTrend('acct:'+sel.value,sel.value);}});
  }}
  window._showTrend('cat:权益','权益');
}})();
window.addEventListener('resize',()=>document.querySelectorAll('.chart').forEach(e=>{{const c=echarts.getInstanceByDom(e);c&&c.resize();}}));
</script></body></html>"""
    out = storage.DATA_ROOT / t["file"]
    out.write_text(html, encoding="utf-8")
    print(f"✅ {t['name']} → {out.name}")


if __name__ == "__main__":
    D = collect(persist_history=False)   # 只取一次数据；历史由 portfolio_tracker 统一记录
    render("origin", D)                  # 经典配色主题；交易终端/色块海报见 panorama_variants.py
