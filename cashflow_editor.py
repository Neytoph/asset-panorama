#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
资产面板本地应用（持仓全景 + 现金流编辑，一页切换）
====================================================
顶部标签栏切换两块：
  · 持仓全景：直接复用已生成的 panorama_*.html（可切 3 套主题），带"重新估值"按钮跑 run_daily.sh 刷新
  · 现金流编辑：可视化增删改收入 / 月度收支 / 定投计划，经 storage 存回 cashflow 数据集
定投额按真实月结余自动：定投额 = max(0, 净结余) × 结余投入比例。
所以请把真实生活开支（吃饭/水电/其他育儿…）补进"月度收支"，净结余才准。

用法：python3 cashflow_editor.py  →  浏览器自动打开 http://127.0.0.1:8765
仅标准库、仅绑定本机；保存时经统一存储层写入（file/sqlite 二选一，自动备份上一版）。
"""
import base64
import json
import os
import html
import subprocess
import sys
import threading
import time
import webbrowser
import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import storage
from portfolio_tracker import get_fx, FX_FALLBACK
import subscriptions as subs_mod
import cashflow_income as inc_mod
import payroll_tax as pay_mod
import cashflow_history as cfh_mod
import bill_import
import insurance as ins_mod
import loans as loans_mod
import holdings_manager as hold_mod
import metrics

BASE = Path(__file__).resolve().parent
HOST = "127.0.0.1"
# 端口可配:PANORAMA_PORT=8766 python3 cashflow_editor.py
# (想同时开演示面板和自己的面板时用得上)
PORT = int(os.environ.get("PANORAMA_PORT") or 8765)
_REBUILD_LOCK = threading.Lock()

# 持仓全景主题 → 已生成的静态文件（run_daily.sh / panorama_themes.py + panorama_variants.py 产出）
THEMES = {
    "origin": ("经典配色", "panorama_origin.html"),
    "terminal": ("交易终端", "panorama_terminal.html"),
    "poster": ("色块海报", "panorama_poster.html"),
}

# 表单提交即时反馈:禁用按钮防连点,「保存并刷新」重估要跑 1-3 分钟,没提示会像没反应
SUBMIT_GUARD = """
<script>
document.querySelectorAll('form').forEach(f=>f.addEventListener('submit',e=>{
  const sub=e.submitter, rebuild=sub&&sub.value==='save_rebuild';
  setTimeout(()=>{f.querySelectorAll('button.save').forEach(b=>{
    b.disabled=true; b.style.opacity=.55;
    if(b===sub) b.textContent=rebuild?'⏳ 已保存,正在刷新全景(约数秒,完成后自动跳转)…':'⏳ 保存中…';
  });},0);
}));
</script>"""

# skin=poster(cookie)时注入所有管理页的覆盖样式:跟随「色块海报」新粗野主义外观。
# 注入点在各页自带 <style> 之后,同特异性后者生效;其他主题暂不做皮肤。
POSTER_PAGE_CSS = """
<style>
body{background:#f4f1ea;color:#111;font-family:"Helvetica Neue","PingFang SC",system-ui,sans-serif}
h1{font-weight:900;letter-spacing:.02em}
h2{font-weight:900}
.sub,.hint{color:#555}
.card{border:3px solid #111;border-radius:0;box-shadow:6px 6px 0 #111}
input,select,textarea{border:2px solid #111;border-radius:0;font-weight:600;background:#fff}
button.save{border:3px solid #111;border-radius:0;font-weight:900}
.primary{background:#2a78d6;color:#fff;box-shadow:4px 4px 0 #111}
.ghost{background:#f4f1ea;color:#111;box-shadow:4px 4px 0 #111}
.del{border:2px solid #111;border-radius:0;background:#fff;color:#e34948;font-weight:900}
.add{border:2.5px dashed #111;border-radius:0;background:#fff;color:#111;font-weight:800}
.ok{background:#1baf7a;color:#fff;border:3px solid #111;border-radius:0;font-weight:800;box-shadow:4px 4px 0 #111}
.live{background:#fff;border:2.5px dashed #111;border-radius:0;font-weight:800}
.live b{color:#2a78d6}
th{color:#111;font-weight:900;border-bottom:3px solid #111}
td{border-bottom:2px solid #e4e0d4}
</style>"""


def load_cf():
    return storage.load_doc("cashflow", {})


_FX_CACHE = {"fx": None, "at": 0.0}
_FX_TTL = 1800   # 汇率进程内缓存 30 分钟：切 Tab 不再每次现场请求，页面才不卡


def _editor_fx():
    now = time.monotonic()
    if _FX_CACHE["fx"] and now - _FX_CACHE["at"] < _FX_TTL:
        return _FX_CACHE["fx"]
    try:
        fx = get_fx()
    except Exception:
        fx = {**FX_FALLBACK, "CNY": 1.0, "_live": False}
    if fx.get("_live"):                 # 只缓存真汇率；兜底值下次仍尝试拉取
        _FX_CACHE.update(fx=fx, at=now)
    return fx


def income_of(cf):
    return inc_mod.income_net_of(cf)


def compute(cf):
    """与 panorama_data.collect() 同口径：净结余 + 定投额（含订阅月折算）。"""
    fx = _editor_fx()
    income = income_of(cf)
    fixed_out = subs_mod.cashflow_fixed_out(cf, fx)
    subs_monthly = subs_mod.monthly_total(subs_mod.load_subs(), fx)
    net_cf = income - fixed_out
    d = cf.get("定投计划", {})
    ratio = d.get("结余投入比例", 0)
    month = max(0.0, net_cf) * ratio if d.get("模式") == "按结余比例" else d.get("固定月额", 0)
    per_n = 4 if d.get("频率") == "每周" else 2
    return {"income": income, "fixed_out": fixed_out, "net_cf": net_cf,
            "dca_month": month, "dca_per": month / per_n if per_n else 0, "per_n": per_n,
            "subs_monthly": subs_monthly}


def esc(v):
    return html.escape(str(v), quote=True)


def expense_display(amt):
    """支出在表单中显示为正数（JSON 内仍为负数）。"""
    return abs(amt) if amt else 0


def page(cf, msg="", rebuilt=False, reward=""):
    c = compute(cf)
    d = cf.get("定投计划", {})
    tgt = next(iter(d.get("标的", {}).items()), ("沪深300ETF", {"代码": "510300"}))
    tgt_name, tgt_code = tgt[0], tgt[1].get("代码", "510300")
    aftertax = cf.get("税后月收入")
    yld = cf.get("年化收益率假设", {})
    # 保存并重建后，通知外壳自动切到刚更新的持仓全景
    notify = "if(window.parent!==window)window.parent.postMessage('rebuilt','*');" if rebuilt else ""

    tax_ratio = cf.get("税后估算比例", 0.72)
    pay_cfg = pay_mod.payroll_config(cf)
    tax_month = pay_cfg["计税月份"]
    member_spec = pay_cfg.get("成员专项附加") or {}
    pay_js = json.dumps({
        "month": pay_cfg["计税月份"],
        "fundRate": pay_cfg["公积金比例"],
        "sbLo": pay_cfg["社保基数下限"], "sbHi": pay_cfg["社保基数上限"],
        "fbLo": pay_cfg["公积金基数下限"], "fbHi": pay_cfg["公积金基数上限"],
        "basic": pay_cfg["基本减除费用"],
        "memberSpec": member_spec,
        "fundAsIncome": bool(pay_cfg.get("公积金计入收入")),
        "fundUnitRate": pay_cfg.get("公积金单位比例"),
    })

    income_rows = "".join(
        f'''<div class="frow inc">
          <input name="income_member" value="{esc(i.get("成员","本人"))}" placeholder="成员">
          <input name="income_name" value="{esc(i["项目"])}" placeholder="来源">
          <select name="income_type">{_opt(["工资", "其他"], i.get("类型", "工资"))}</select>
          <input name="income_amt" type="number" step="any" value="{esc(inc_mod.income_display_amount(i, cf))}" placeholder="税前/毛收入">
          <button type="button" class="del" onclick="this.parentNode.remove();recalc()">✕</button>
        </div>''' for i in cf.get("收入明细", []))

    flow_rows = "".join(
        f'''<div class="frow">
          <input name="flow_name" value="{esc(i["项目"])}" placeholder="项目">
          <input name="flow_amt" type="number" step="1" min="0" value="{esc(expense_display(i["金额"]))}" placeholder="金额">
          <button type="button" class="del" onclick="this.parentNode.remove();recalc()">✕</button>
        </div>''' for i in cf.get("月度收支", []))

    banner = f'<div class="ok">{esc(msg)}</div>' if msg else ""
    banner += reward or ""
    ratio_pct = d.get("结余投入比例", 0.8)
    fx = _editor_fx()
    subs_m = c.get("subs_monthly", 0)
    ins_m = round(ins_mod.monthly_total())
    fx_js = json.dumps({"USD": fx["USD"], "HKD": fx["HKD"], "CNY": 1.0})

    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>现金流编辑器</title>
<style>
 body{{font-family:-apple-system,"PingFang SC",system-ui,sans-serif;background:#f4f5f7;color:#222;margin:0;padding:24px}}
 .wrap{{max-width:720px;margin:0 auto}}
 h1{{font-size:20px;margin:0 0 4px}} .sub{{color:#888;font-size:13px;margin-bottom:18px}}
 .card{{background:#fff;border-radius:14px;padding:18px 20px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,.06)}}
 h2{{font-size:15px;margin:0 0 12px}}
 label{{display:block;font-size:12px;color:#666;margin:10px 0 4px}}
 input,select{{width:100%;box-sizing:border-box;padding:8px 10px;border:1px solid #ddd;border-radius:8px;font-size:14px}}
 .grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
 .frow{{display:flex;gap:8px;margin:8px 0;align-items:center}}
 .frow input:first-child{{flex:2}} .frow input:nth-child(2){{flex:1}}
 .frow.inc input[name=income_member]{{flex:0.9;min-width:56px}}
 .frow.inc input[name=income_name]{{flex:1.4;min-width:72px}}
 .frow.inc select[name=income_type]{{flex:0.8;min-width:64px;width:auto}}
 .frow.inc input[name=income_amt]{{flex:1;min-width:72px}}
 .del{{border:none;background:#fbeaea;color:#c0392b;border-radius:8px;width:34px;height:34px;cursor:pointer}}
 .add{{border:1px dashed #bbb;background:#fafafa;color:#555;border-radius:8px;padding:8px;width:100%;cursor:pointer;margin-top:6px}}
 .live{{background:#eef4ff;border:1px solid #d6e4ff;border-radius:12px;padding:14px 16px;font-size:14px;line-height:1.9}}
 .live b{{color:#1554d1}} .neg b{{color:#c0392b}}
 .btns{{display:flex;gap:12px;margin-top:6px}}
 button.save{{flex:1;padding:12px;border:none;border-radius:10px;font-size:15px;cursor:pointer}}
 .primary{{background:#1554d1;color:#fff}} .ghost{{background:#eee;color:#333}}
 details summary{{cursor:pointer;color:#666;font-size:13px}} .ok{{background:#e7f7ec;color:#1a7f37;padding:10px 14px;border-radius:10px;margin-bottom:16px}}
 .hint{{font-size:12px;color:#999;margin-top:6px}}
{OB_CSS}
</style></head><body><div class="wrap">
{onboard_hint("flow", onboard_state())}
<h1>💰 现金流编辑器</h1>
 <div class="sub">定投额按真实结余自动算 · 月度支出 + 订阅月费一并计入固定支出</div>
{banner}
<form method="post" action="/save">
 <div class="live" id="live"></div>

 <div class="card"><h2>收入明细<span class="hint"> · 工资按北京五险一金+累进个税估算当月实发</span></h2>
  <div id="incomes">{income_rows}</div>
  <button type="button" class="add" onclick="addIncome()">+ 增加一项收入</button>
  <div class="grid" style="margin-top:12px">
   <div><label>公积金比例</label><input name="pay_fund_rate" type="number" step="0.01" min="0.05" max="0.12" value="{esc(pay_cfg.get('公积金比例',0.12))}" oninput="recalc()"></div>
   <div><label>公积金计入收入(仅当可取出)</label><select name="pay_fund_income" onchange="recalc()"><option value="1"{' selected' if pay_cfg.get('公积金计入收入') else ''}>是·个人+单位到账</option><option value="0"{'' if pay_cfg.get('公积金计入收入') else ' selected'}>否</option></select></div>
   <div><label>计税月份</label><div class="hint" style="margin:8px 0 0;padding:8px 10px;background:#f4f5f7;border-radius:8px">自动跟当前月 · 现为 <b>{tax_month}</b> 月（累计预扣）</div></div>
   <div><label>本人专项附加扣除/月</label><input name="pay_spec_self" type="number" step="100" min="0" value="{esc(member_spec.get('本人',0))}" oninput="recalc()"></div>
   <div><label>配偶专项附加扣除/月</label><input name="pay_spec_spouse" type="number" step="100" min="0" value="{esc(member_spec.get('配偶',0))}" oninput="recalc()"></div>
  </div>
  <label>其他收入税后比例（类型=其他时使用）</label>
  <input name="tax_ratio" type="number" step="0.01" min="0" max="1" value="{esc(tax_ratio)}" oninput="recalc()">
  <div class="hint">工资薪金：五险个人约 10.5%+3元，公积金按基数封顶；个税按累计预扣法。非工资收入（奖金/劳务等）选「其他」。</div>
 </div>

 <div class="card"><h2>月度支出<span class="hint"> · 填正数即可</span></h2>
  <div id="flows">{flow_rows}</div>
  <button type="button" class="add" onclick="addRow()">+ 增加一项</button>
 </div>

 <div class="card"><h2>定投计划</h2>
  <div class="grid">
   <div><label>模式</label><select name="dca_mode" onchange="recalc()">
     <option value="按结余比例" {'selected' if d.get('模式')=='按结余比例' else ''}>按结余比例（推荐）</option>
     <option value="固定月额" {'selected' if d.get('模式')=='固定月额' else ''}>固定月额</option>
   </select></div>
   <div><label>频率</label><select name="dca_freq" onchange="recalc()">
     <option value="每周" {'selected' if d.get('频率')=='每周' else ''}>每周（4份/月）</option>
     <option value="双周" {'selected' if d.get('频率')=='双周' else ''}>双周（2份/月）</option>
   </select></div>
   <div><label>结余投入比例（模式=按结余比例时生效）</label><input name="dca_ratio" type="number" step="0.05" min="0" max="1" value="{esc(ratio_pct)}" oninput="recalc()"></div>
   <div><label>固定月额（模式=固定月额时生效）</label><input name="dca_fixed" type="number" step="100" value="{esc(d.get('固定月额',8000))}" oninput="recalc()"></div>
   <div><label>标的名称</label><input name="dca_name" value="{esc(tgt_name)}"></div>
   <div><label>标的代码</label><input name="dca_code" value="{esc(tgt_code)}"></div>
  </div>
 </div>

 <div class="card"><details><summary>高级：收入兜底估算 & 被动收入假设</summary>
  <div class="hint" style="margin-top:8px">下面两项仅在「收入明细」为空时作为兜底（比例见上方收入区）：</div>
  <div class="grid" style="margin-top:6px">
   <div><label>月收入(税前)</label><input name="income_pretax" type="number" step="1" value="{esc(cf.get('月收入税前',0))}" oninput="recalc()"></div>
   <div><label>税后月收入(留空=税前×比例)</label><input name="income_aftertax" type="number" step="1" value="{esc(aftertax) if aftertax is not None else ''}" oninput="recalc()"></div>
  </div>
  <label style="margin-top:14px">投资房假设毛租金回报率</label><input name="prop_rent" type="number" step="0.001" value="{esc(cf.get('投资房假设毛租金回报率',0.025))}">
  <div class="grid" style="margin-top:8px">
   <div><label>招行理财</label><input name="yld_zhaohang" type="number" step="0.001" value="{esc(yld.get('招行理财',0.015))}"></div>
   <div><label>长钱债</label><input name="yld_changqian" type="number" step="0.001" value="{esc(yld.get('长钱债',0.03))}"></div>
   <div><label>海外债</label><input name="yld_haiwai" type="number" step="0.001" value="{esc(yld.get('海外债',0.04))}"></div>
   <div><label>货币现金</label><input name="yld_cash" type="number" step="0.001" value="{esc(yld.get('货币现金',0.018))}"></div>
   <div><label>增额寿</label><input name="yld_zenge" type="number" step="0.001" value="{esc(yld.get('增额寿',0.035))}"></div>
  </div>
 </details></div>

 <div class="btns">
  <button class="save primary" name="action" value="save_rebuild">💾 保存并刷新全景</button>
  <button class="save ghost" name="action" value="save">仅保存</button>
 </div>
 <div class="hint" style="text-align:center">「保存并刷新全景」会重算并自动跳到更新后的持仓全景；「仅保存」只写文件、不重建。</div>
</form>
</div>
<script>
const FX={fx_js};
const SUBS_MONTHLY={subs_m};
const INS_MONTHLY={ins_m};
function mkRow(container,n1,n2,ph1,ph2,name,amt){{
 const d=document.createElement('div');d.className='frow';
 d.innerHTML='<input name="'+n1+'" placeholder="'+ph1+'"><input name="'+n2+'" type="number" step="1" placeholder="'+ph2+'"><button type="button" class="del">✕</button>';
 d.querySelector('input').value=name; d.querySelectorAll('input')[1].value=amt;
 d.querySelector('.del').onclick=()=>{{d.remove();recalc()}};
 d.querySelectorAll('input').forEach(i=>i.oninput=recalc);
 document.getElementById(container).appendChild(d);
}}
function addRow(name="",amt=""){{ mkRow('flows','flow_name','flow_amt','项目','金额',name,amt); }}
const PAYROLL={pay_js};
{pay_mod.js_calc_snippet()}
function payrollCfg(){{
 return {{
   month:new Date().getMonth()+1,
   fundRate:+document.querySelector('[name=pay_fund_rate]').value||PAYROLL.fundRate,
   sbLo:PAYROLL.sbLo,sbHi:PAYROLL.sbHi,fbLo:PAYROLL.fbLo,fbHi:PAYROLL.fbHi,
   basic:PAYROLL.basic,
   fundAsIncome:(document.querySelector('[name=pay_fund_income]')?document.querySelector('[name=pay_fund_income]').value==='1':PAYROLL.fundAsIncome),
   fundUnitRate:PAYROLL.fundUnitRate,
   memberSpec:{{
     '本人':+document.querySelector('[name=pay_spec_self]').value||0,
     '配偶':+document.querySelector('[name=pay_spec_spouse]').value||0,
     ...PAYROLL.memberSpec
   }}
 }};
}}
function addIncome(member="",name="",amt="",type="工资"){{
 const d=document.createElement('div');d.className='frow inc';
 d.innerHTML='<input name="income_member" placeholder="成员"><input name="income_name" placeholder="来源">'
   +'<select name="income_type"><option>工资</option><option>其他</option></select>'
   +'<input name="income_amt" type="number" step="any" placeholder="税前/毛收入"><button type="button" class="del">✕</button>';
 const ins=d.querySelectorAll('input'); const sel=d.querySelector('[name=income_type]');
 ins[0].value=member; ins[1].value=name; ins[2].value=amt; sel.value=type;
 d.querySelector('.del').onclick=()=>{{d.remove();recalc()}};
 d.querySelectorAll('input,select').forEach(i=>i.oninput=recalc);
 document.getElementById('incomes').appendChild(d);
}}
function recalc(){{
 const ratio=+document.querySelector('[name=tax_ratio]').value||0;
 const cfg=payrollCfg();
 const incRows=document.querySelectorAll('#incomes .frow');
 let income=0, pretaxSum=0, sfSum=0, iitSum=0, fundSum=0; const byMember={{}}; const order=[];
 if(incRows.length){{
   incRows.forEach(r=>{{
     const m=(r.querySelector('[name=income_member]').value||'').trim()||'未分组';
     const p=+r.querySelector('[name=income_amt]').value||0;
     const type=r.querySelector('[name=income_type]').value||'工资';
     pretaxSum+=p;
     let a=p*ratio, sf=0, iit=0;
     const est=estSalaryNet(p,m,type,cfg);
     if(est){{a=est.net;sf=est.sf;iit=est.iit;}}
     const fund=(est&&cfg.fundAsIncome)?est.fundArrive:0;
     sfSum+=sf;iitSum+=iit;fundSum+=fund;income+=a+fund;
     if(!(m in byMember)){{byMember[m]=0;order.push(m);}}
     byMember[m]+=a+fund;
   }});
 }}
 else {{
   const pretax=+document.querySelector('[name=income_pretax]').value||0;
   const after=document.querySelector('[name=income_aftertax]').value;
   pretaxSum=pretax;
   income = after!=='' ? +after : pretax*ratio;
 }}
 let out=0;
 document.querySelectorAll('#flows .frow').forEach(r=>{{const a=+r.querySelectorAll('input')[1].value||0; out+=Math.abs(a);}});
 out += SUBS_MONTHLY + INS_MONTHLY;
 const net=income-out;
 const mode=document.querySelector('[name=dca_mode]').value;
 const dr=+document.querySelector('[name=dca_ratio]').value||0;
 const fixed=+document.querySelector('[name=dca_fixed]').value||0;
 const month = mode==='按结余比例' ? Math.max(0,net)*dr : fixed;
 const pn = document.querySelector('[name=dca_freq]').value==='每周'?4:2;
 const fmt=x=>'¥'+Math.round(x).toLocaleString();
 const live=document.getElementById('live');
 let memStr='';
 if(order.length>1) memStr=' （'+order.map(m=>m+'实发 <b>'+fmt(byMember[m])+'</b>').join(' · ')+'）';
 let s='税前 <b>'+fmt(pretaxSum)+'</b>';
 if(sfSum>0||iitSum>0) s+=' · 五险一金 <b>'+fmt(sfSum)+'</b> · 个税 <b>'+fmt(iitSum)+'</b>';
 s+=' → 当月实发 <b>'+fmt(income-fundSum)+'</b>';
 if(fundSum>0) s+=' · 公积金到账 <b>+'+fmt(fundSum)+'</b>';
 s+=memStr+' · 固定支出 <b>'+fmt(out)+'</b>（含订阅 <b>'+fmt(SUBS_MONTHLY)+'</b> · 保险摊月 <b>'+fmt(INS_MONTHLY)+'</b>）<br>真实月结余 <b>'+fmt(net)+'</b>';
 if(net<=0){{live.className='live neg';s+='<br>结余≤0 → 本月暂停定投';}}
 else{{live.className='live';s+='<br>本月定投 <b>'+fmt(month)+'</b>（'+(mode==='按结余比例'?'结余×'+Math.round(dr*100)+'%':'固定')+'）→ 每份 <b>'+fmt(month/pn)+'</b> × '+pn+'次';}}
 live.innerHTML=s;
}}
document.querySelectorAll('#flows input,#incomes input,#incomes select,[name=tax_ratio],[name=pay_fund_rate],[name=pay_spec_self],[name=pay_spec_spouse]').forEach(i=>i.oninput=recalc);
recalc();
{notify}
</script>
{SUBMIT_GUARD}</body></html>"""


def _opt(vals, cur):
    return "".join(f'<option value="{v}"{" selected" if v == cur else ""}>{v}</option>' for v in vals)


def subs_page(subs_list, msg="", rebuilt=False, reward=""):
    fx = _editor_fx()
    subs_mod.sync_icons(subs_list)
    sub_cats = list(subs_mod.CAT_STYLE.keys())
    cat_opts_add = "".join(
        f'<option value="{c}"{" selected" if c == "其他" else ""}>{c}</option>' for c in sub_cats)
    notify = "if(window.parent!==window)window.parent.postMessage('rebuilt','*');" if rebuilt else ""
    banner = f'<div class="ok">{esc(msg)}</div>' if msg else ""
    banner += reward or ""
    fx_js = json.dumps({"USD": fx["USD"], "HKD": fx["HKD"], "CNY": 1.0})

    def row_html(s):
        ds = subs_mod.decorate_sub(s)
        ico = (f'<img src="/{ds["iconPath"]}" width="20" height="20" style="border-radius:4px;object-fit:contain">'
               if ds.get("iconPath") else f'<span style="width:20px;text-align:center">{ds["图标"]}</span>')
        return f'''<div class="frow sub">
          <span class="ico">{ico}</span>
          <input name="sub_name" value="{esc(s.get("名称", ""))}" placeholder="名称">
          <input name="sub_domain" value="{esc(s.get("域名", ""))}" placeholder="域名(可选)">
          <input name="sub_amt" type="number" step="0.01" min="0" value="{esc(s.get("金额", ""))}" placeholder="金额">
          <select name="sub_ccy">{_opt(["CNY", "USD", "HKD"], s.get("币种", "CNY"))}</select>
          <select name="sub_period">{_opt(["月", "季", "年", "周"], s.get("周期", "月"))}</select>
          <input name="sub_next" type="date" value="{esc(s.get("下次扣费日", ""))}">
          <select name="sub_cat">{_opt(sub_cats, s.get("分类", "其他"))}</select>
          <select name="sub_status">{_opt(["启用", "暂停", "试用"], s.get("状态", "启用"))}</select>
          <button type="button" class="del" onclick="this.parentNode.remove();recalcSubs()">✕</button>
        </div>'''

    sub_rows = "".join(row_html(s) for s in subs_list)

    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>订阅管理</title>
<style>
 body{{font-family:-apple-system,"PingFang SC",system-ui,sans-serif;background:#f4f5f7;color:#222;margin:0;padding:24px}}
 .wrap{{max-width:1020px;margin:0 auto}}
 h1{{font-size:20px;margin:0 0 4px}} .sub{{color:#888;font-size:13px;margin-bottom:18px}}
 .card{{background:#fff;border-radius:14px;padding:18px 20px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,.06)}}
 h2{{font-size:15px;margin:0 0 12px}}
 input,select{{box-sizing:border-box;padding:8px 10px;border:1px solid #ddd;border-radius:8px;font-size:14px}}
 .frow{{display:flex;gap:6px;margin:8px 0;align-items:center;flex-wrap:wrap}}
 .frow.sub .ico{{width:22px;flex-shrink:0;display:flex;align-items:center;justify-content:center}}
 .frow.sub input[name=sub_name]{{flex:1.6;min-width:88px}}
 .frow.sub input[name=sub_domain]{{flex:1.2;min-width:88px}}
 .frow.sub input[name=sub_amt]{{flex:1;min-width:70px}}
 .frow.sub select{{flex:1;min-width:58px}}
 .frow.sub input[name=sub_next]{{flex:0 0 152px;width:152px;min-width:152px;padding-right:6px}}
 .frow.sub select[name=sub_cat]{{flex:1;min-width:80px}}
 .del{{border:none;background:#fbeaea;color:#c0392b;border-radius:8px;width:34px;height:34px;cursor:pointer;flex-shrink:0}}
 .add{{border:1px dashed #bbb;background:#fafafa;color:#555;border-radius:8px;padding:8px;width:100%;cursor:pointer;margin-top:6px}}
 .live{{background:#eef4ff;border:1px solid #d6e4ff;border-radius:12px;padding:14px 16px;font-size:14px;line-height:1.9;margin-bottom:16px}}
 .live b{{color:#1554d1}}
 .btns{{display:flex;gap:12px;margin-top:6px}}
 button.save{{flex:1;padding:12px;border:none;border-radius:10px;font-size:15px;cursor:pointer}}
 .primary{{background:#1554d1;color:#fff}} .ghost{{background:#eee;color:#333}}
 .ok{{background:#e7f7ec;color:#1a7f37;padding:10px 14px;border-radius:10px;margin-bottom:16px}}
 .hint{{font-size:12px;color:#999}}
</style></head><body><div class="wrap">
<h1>📆 订阅管理</h1>
<div class="sub">年/季/周付自动折算月费 · 填域名自动拉图标(或按名称猜测) · 失败回退 emoji</div>
{banner}
<div class="live" id="live"></div>
<form method="post" action="/subs/save">
 <div class="card"><h2>订阅台账<span class="hint"> · 金额填原币正数 · 域名如 netflix.com</span></h2>
  <div id="subs">{sub_rows}</div>
  <button type="button" class="add" onclick="addSub()">+ 增加一项订阅</button>
 </div>
 <div class="btns">
  <button class="save primary" name="action" value="save_rebuild">💾 保存并刷新全景</button>
  <button class="save ghost" name="action" value="save">仅保存</button>
 </div>
</form>
</div>
<script>
const FX={fx_js};
const PER_TO_MONTH={{"月":1,"季":1/3,"年":1/12,"周":52/12}};
function monthlyCny(amt,ccy,period,status){{
 if(status!=='启用') return 0;
 return Math.abs(+amt||0)*(FX[ccy]||1)*(PER_TO_MONTH[period]||1);
}}
function recalcSubs(){{
 let monthly=0, yearly=0, n=0;
 document.querySelectorAll('#subs .frow').forEach(r=>{{
   const m=monthlyCny(r.querySelector('[name=sub_amt]').value,
     r.querySelector('[name=sub_ccy]').value,
     r.querySelector('[name=sub_period]').value,
     r.querySelector('[name=sub_status]').value);
   if(m>0){{monthly+=m;n++;}}
 }});
 yearly=monthly*12;
 const fmt=x=>'¥'+Math.round(x).toLocaleString();
 document.getElementById('live').innerHTML='本月订阅合计 <b>'+fmt(monthly)+'</b> · 全年 <b>'+fmt(yearly)+'</b> · 启用 <b>'+n+'</b> 项';
}}
function addSub(){{
 const d=document.createElement('div');d.className='frow sub';
 d.innerHTML='<span class="ico">📦</span><input name="sub_name" placeholder="名称"><input name="sub_domain" placeholder="域名(可选)">'
   +'<input name="sub_amt" type="number" step="0.01" min="0" placeholder="金额">'
   +'<select name="sub_ccy"><option>CNY</option><option>USD</option><option>HKD</option></select>'
   +'<select name="sub_period"><option>月</option><option>季</option><option>年</option><option>周</option></select>'
   +'<input name="sub_next" type="date"><select name="sub_cat">{cat_opts_add}</select>'
   +'<select name="sub_status"><option>启用</option><option>暂停</option><option>试用</option></select>'
   +'<button type="button" class="del">✕</button>';
 d.querySelector('.del').onclick=()=>{{d.remove();recalcSubs()}};
 d.querySelectorAll('input,select').forEach(i=>i.oninput=recalcSubs);
 document.getElementById('subs').appendChild(d);
 recalcSubs();
}}
document.querySelectorAll('#subs input,#subs select').forEach(i=>i.oninput=recalcSubs);
recalcSubs();
{notify}
</script>
{SUBMIT_GUARD}</body></html>"""


def parse_subs_save(fields):
    out = []
    for name, domain, amt, ccy, period, nxt, cat, st in zip(
            fields.get("sub_name", []), fields.get("sub_domain", []), fields.get("sub_amt", []),
            fields.get("sub_ccy", []), fields.get("sub_period", []), fields.get("sub_next", []),
            fields.get("sub_cat", []), fields.get("sub_status", [])):
        name, domain, amt, ccy, period, nxt, cat, st = [
            x.strip() for x in (name, domain, amt, ccy, period, nxt, cat, st)]
        if not name and not amt:
            continue
        cat = cat or "其他"
        icon = subs_mod.CAT_STYLE.get(cat, subs_mod.CAT_STYLE["其他"])[0]
        today = datetime.date.today().isoformat()
        row = {
            "名称": name,
            "金额": float(amt) if amt else 0,
            "币种": ccy or "CNY",
            "周期": period or "月",
            "下次扣费日": nxt or today,
            "开始日期": nxt or today,
            "分类": cat,
            "图标": icon,
            "状态": st or "启用",
            "备注": "",
        }
        if domain:
            row["域名"] = subs_mod.normalize_domain(domain)
        out.append(row)
    return out


def parse_save(fields):
    """把表单字段拼回 cashflow.json 结构（保留原有 _note 等未编辑字段）。"""
    cf = load_cf()

    def num(key, default=0.0):
        v = fields.get(key, [""])[0].strip()
        return float(v) if v != "" else default

    cf["月收入税前"] = num("income_pretax", 0)
    at = fields.get("income_aftertax", [""])[0].strip()
    cf["税后月收入"] = float(at) if at != "" else None
    cf["税后估算比例"] = num("tax_ratio", 0.72)
    cf["收入计税模式"] = "薪酬"
    cf["薪酬计税"] = {
        "城市": "北京",
        "公积金比例": num("pay_fund_rate", 0.12),
        "公积金计入收入": fields.get("pay_fund_income", ["0"])[0] == "1",
        "成员专项附加": {
            k: v for k, v in (
                ("本人", num("pay_spec_self", 0)),
                ("配偶", num("pay_spec_spouse", 0)),
            ) if v > 0
        },
    }

    def income_rows():
        out = []
        members = fields.get("income_member", [])
        names = fields.get("income_name", [])
        types = fields.get("income_type", [])
        amts = fields.get("income_amt", [])
        for m, n, t, a in zip(members, names, types, amts):
            m, n, t, a = m.strip(), n.strip(), (t or "工资").strip(), a.strip()
            if not m and not n and a == "":
                continue
            row = {"成员": m or "未分组", "项目": n, "金额": float(a) if a != "" else 0, "类型": t or "工资"}
            out.append(row)
        return out

    def expense_rows():
        out = []
        for n, a in zip(fields.get("flow_name", []), fields.get("flow_amt", [])):
            n, a = n.strip(), a.strip()
            if not n and a == "":
                continue
            amt = float(a) if a != "" else 0
            if amt != 0:
                amt = -abs(amt)
            out.append({"项目": n, "金额": amt})
        return out

    cf["收入明细"] = income_rows()
    cf["收入明细口径"] = "税前"
    cf["月度收支"] = expense_rows()

    cf["投资房假设毛租金回报率"] = num("prop_rent", 0.025)
    cf["年化收益率假设"] = {
        "招行理财": num("yld_zhaohang", 0.015), "长钱债": num("yld_changqian", 0.03),
        "海外债": num("yld_haiwai", 0.04), "货币现金": num("yld_cash", 0.018),
        "增额寿": num("yld_zenge", 0.035),
    }

    d = cf.get("定投计划", {})
    d["模式"] = fields.get("dca_mode", ["按结余比例"])[0]
    d["结余投入比例"] = num("dca_ratio", 0.8)
    d["固定月额"] = num("dca_fixed", 8000)
    d["频率"] = fields.get("dca_freq", ["每周"])[0]
    d["标的"] = {fields.get("dca_name", ["沪深300ETF"])[0]:
                 {"代码": fields.get("dca_code", ["510300"])[0], "占比": 1.0}}
    cf["定投计划"] = d
    return cf


def _recon_dca(cf, actual_net):
    d = cf.get("定投计划", {})
    if d.get("模式") == "按结余比例":
        return max(0.0, actual_net) * d.get("结余投入比例", 0)
    return d.get("固定月额", 0)


def recon_page(msg="", rebuilt=False, month=None, reward=""):
    cf = load_cf()
    est = compute(cf)
    month = month or datetime.date.today().strftime("%Y-%m")
    income, fixed = est["income"], est["fixed_out"]
    est_net = est["net_cf"]
    est_rate = est_net / income if income else 0
    history = cfh_mod.load_history()
    cur = next((r for r in history if r["月份"] == month), None)
    locked = bool(cur and cur.get("已对账") == "是")
    prefill_income = cur["税后收入"] if locked else income
    prefill_other = cur.get("其他实际支出", 0) if cur else 0
    prefill_note = (cur.get("对账备注") or "") if cur else ""
    notify = "if(window.parent!==window)window.parent.postMessage('rebuilt','*');" if rebuilt else ""
    banner = f'<div class="ok">{esc(msg)}</div>' if msg else ""
    banner += reward or ""

    hist_rows = ""
    for r in reversed(history):
        tag = "✅已对账" if r.get("已对账") == "是" else "⏳草稿"
        cls = "ok" if r.get("已对账") == "是" else "draft"
        extra = f"其他¥{r['其他实际支出']:,.0f}" if r.get("其他实际支出") else ""
        note = esc(r.get("对账备注") or "")
        day = esc(r.get("对账日") or "—")
        hist_rows += (
            f'<tr class="{cls}"><td>{esc(r["月份"])}</td><td>{tag}</td>'
            f'<td>¥{r["税后收入"]:,.0f}</td><td>¥{r["固定支出"]:,.0f}</td>'
            f'<td>{extra or "—"}</td><td>¥{r["净结余"]:,.0f}</td>'
            f'<td>{r["储蓄率"]*100:.1f}%</td><td>{day}</td>'
            f'<td class="note">{note or "—"}</td></tr>')

    lock_hint = '<div class="warn">该月已对账锁定；再次提交将覆盖并更新对账日。</div>' if locked else ""
    btn_label = "✅ 重新确认并记录" if locked else "✅ 确认并记录本月"

    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>月度对账</title>
<style>
 body{{font-family:-apple-system,"PingFang SC",system-ui,sans-serif;background:#f4f5f7;color:#222;margin:0;padding:24px}}
 .wrap{{max-width:900px;margin:0 auto}}
 h1{{font-size:20px;margin:0 0 4px}} .sub{{color:#888;font-size:13px;margin-bottom:18px}}
 .card{{background:#fff;border-radius:14px;padding:18px 20px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,.06)}}
 h2{{font-size:15px;margin:0 0 12px}}
 label{{display:block;font-size:12px;color:#666;margin:10px 0 4px}}
 input,select,textarea{{width:100%;box-sizing:border-box;padding:8px 10px;border:1px solid #ddd;border-radius:8px;font-size:14px}}
 .grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
 .est{{background:#f8fafc;border:1px solid #e8edf5;border-radius:10px;padding:12px 14px;font-size:14px;line-height:1.9}}
 .est b{{color:#1554d1}}
 .live{{background:#eef4ff;border:1px solid #d6e4ff;border-radius:12px;padding:14px 16px;font-size:14px;line-height:1.9;margin-bottom:16px}}
 .live b{{color:#1554d1}} .diff b{{color:#c0392b}}
 .checks{{display:flex;flex-wrap:wrap;gap:14px;font-size:13px;color:#555;margin-top:8px}}
 .checks label{{display:flex;align-items:center;gap:6px;margin:0;width:auto}}
 table{{width:100%;border-collapse:collapse;font-size:13px}}
 th,td{{padding:8px 6px;border-bottom:1px solid #eee;text-align:left}}
 th{{color:#888;font-weight:500;font-size:12px}}
 tr.draft td:nth-child(2){{color:#b45309}}
 tr.ok td:nth-child(2){{color:#16a34a}}
 td.note{{max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
 .btns{{display:flex;gap:12px;margin-top:6px}}
 button.save{{flex:1;padding:12px;border:none;border-radius:10px;font-size:15px;cursor:pointer}}
 .primary{{background:#1554d1;color:#fff}} .ghost{{background:#eee;color:#333}}
 .ok{{background:#e7f7ec;color:#1a7f37;padding:10px 14px;border-radius:10px;margin-bottom:16px}}
 .warn{{background:#fff8e6;color:#92400e;padding:8px 12px;border-radius:8px;margin-bottom:12px;font-size:13px}}
 .hint{{font-size:12px;color:#999}}
</style></head><body><div class="wrap">
<h1>🧾 月度对账</h1>
<div class="sub">结合银行/微信/支付宝账单确认真实收入与变动开支 · 确认后锁定该月历史</div>
{banner}
<form method="post" action="/recon/save" id="reconForm" onsubmit="return confirmSubmit()">
 <div class="card">
  <label>对账月份</label>
  <input type="month" name="recon_month" value="{esc(month)}" onchange="location.href='/recon?month='+this.value">
  <h2 style="margin-top:16px">配置估算（只读）</h2>
  <div class="est" id="estBox">
   当月实发 <b>¥{income:,.0f}</b> · 固定支出(含订阅) <b>¥{fixed:,.0f}</b><br>
   估算净结余 <b>¥{est_net:,.0f}</b> · 估算储蓄率 <b>{est_rate*100:.1f}%</b>
   <div class="hint" style="margin-top:6px">未含吃饭/购物等配置外变动开支，请在下方补「其他实际支出」</div>
  </div>
 </div>
 <div class="live" id="live"></div>
 <div class="card">
  <h2>账单导入<span class="hint"> · 微信/支付宝/银行导出的 CSV，自动汇总本月支出</span></h2>
  <input type="file" id="billFiles" accept=".csv,.CSV,text/csv" multiple onchange="importBills(this.files)">
  <label style="margin-top:10px">排除关键词（资金腾挪非消费；已在固定支出里的项也要排除避免重复计）</label>
  <input id="billExcludes" value="{esc(bill_import.DEFAULT_EXCLUDES)},房租,房贷,幼儿园,停车,车贷,话费">
  <div id="billResults" style="margin-top:10px;font-size:13px;line-height:1.8"></div>
  <div class="btns" id="billApplyWrap" style="display:none;margin-top:10px">
   <button type="button" class="save ghost" onclick="applyBills()">⬇ 填入其他实际支出（合计 <span id="billTotal">¥0</span>）</button>
  </div>
  <div class="hint" style="margin-top:8px">⚠ 别重复计：微信/支付宝若绑的银行卡，同一笔会同时出现在钱包账单和银行流水——建议只导钱包账单 + 银行卡的直接代扣消费。<br>
  ⚠ 保费勿计：保险保费已按月摊入固定支出（保险 Tab），账单里的整笔保费扣款已被默认排除词「保险/保费」剔除，别手动加回。</div>
 </div>
 <div class="card">
  <h2>对账录入</h2>
  {lock_hint}
  <div class="grid">
   <div><label>实际税后收入</label>
    <input name="recon_income" type="number" step="any" value="{esc(prefill_income)}" oninput="recalcRecon()"></div>
   <div><label>其他实际支出（配置外变动开支）</label>
    <input name="recon_other" type="number" step="any" min="0" value="{esc(prefill_other)}" oninput="recalcRecon()"></div>
  </div>
  <label>对账备注</label>
  <textarea name="recon_note" rows="2" placeholder="如：招行+微信+支付宝+工资卡已核对">{esc(prefill_note)}</textarea>
  <div class="checks">
   <span style="color:#888">核对清单：</span>
   <label><input type="checkbox"> 招行</label>
   <label><input type="checkbox"> 微信</label>
   <label><input type="checkbox"> 支付宝</label>
   <label><input type="checkbox"> 工资卡</label>
  </div>
  <div class="btns" style="margin-top:14px">
   <button class="save primary" name="action" value="save_rebuild">{btn_label}并刷新全景</button>
   <button class="save ghost" name="action" value="save">{btn_label}</button>
  </div>
 </div>
</form>
 <div class="card"><h2>历史记录</h2>
  <table><thead><tr>
   <th>月份</th><th>状态</th><th>收入</th><th>固定</th><th>其他</th><th>结余</th><th>储蓄率</th><th>对账日</th><th>备注</th>
  </tr></thead><tbody>{hist_rows or '<tr><td colspan="9" class="hint">尚无记录</td></tr>'}</tbody></table>
 </div>
</div>
<script>
const EST={{income:{income},fixed:{fixed},net:{est_net},rate:{est_rate}}};
function recalcRecon(){{
 const inc=+document.querySelector('[name=recon_income]').value||0;
 const other=+document.querySelector('[name=recon_other]').value||0;
 const net=inc-EST.fixed-other;
 const rate=inc?net/inc:0;
 const fmt=x=>'¥'+Math.round(x).toLocaleString();
 const dNet=net-EST.net, dRate=(rate-EST.rate)*100;
 let s='实际净结余 <b>'+fmt(net)+'</b> · 实际储蓄率 <b>'+(rate*100).toFixed(1)+'%</b>';
 s+='<br><span class="diff">较估算：结余 <b>'+(dNet>=0?'+':'')+fmt(dNet)+'</b> · 储蓄率 <b>'+(dRate>=0?'+':'')+dRate.toFixed(1)+'pp</b></span>';
 if(other>0) s+='<br>变动开支占收入 <b>'+(other/inc*100).toFixed(1)+'%</b>';
 document.getElementById('live').innerHTML=s;
}}
function confirmSubmit(){{
 return confirm('确认本月已按银行/微信账单对账无误？\\n记录后将覆盖本月草稿并锁定该月。');
}}
let billSum=0, billSrcs=[];
function importBills(files){{
 const month=document.querySelector('[name=recon_month]').value;
 const excludes=document.getElementById('billExcludes').value;
 const box=document.getElementById('billResults');
 [...files].forEach(f=>{{
  const rd=new FileReader();
  rd.onload=async()=>{{
   const b64=btoa(String.fromCharCode(...new Uint8Array(rd.result)));
   try{{
    const rsp=await fetch('/recon/import',{{method:'POST',headers:{{'Content-Type':'application/json'}},
      body:JSON.stringify({{b64:b64,month:month,excludes:excludes}})}});
    const s=await rsp.json();
    const fmt=x=>'¥'+Math.round(x).toLocaleString();
    let line='<div style="border-bottom:1px solid #eee;padding:6px 0"><b>'+f.name+'</b>（'+s.source+'）: '
      +'本月支出 <b style="color:#1554d1">'+fmt(s.expense)+'</b>（'+s.count+'笔）';
    if(s.excluded>0) line+=' · 已排除 '+fmt(s.excluded)+'（'+s.excludedCount+'笔）';
    if(s.otherMonths>0) line+=' · 非'+month+'月 '+fmt(s.otherMonths)+'未计';
    if(s.warnings&&s.warnings.length) line+='<br><span style="color:#b45309">'+s.warnings.join('；')+'</span>';
    if(s.top&&s.top.length){{
      line+='<br><span style="color:#888;font-size:12px">Top: '
        +s.top.slice(0,5).map(t=>t[0]+' '+fmt(t[1])).join(' · ')+'</span>';
    }}
    line+='</div>';
    box.insertAdjacentHTML('beforeend',line);
    if(s.expense>0){{
      billSum+=s.expense; billSrcs.push(f.name+'('+s.source+')');
      document.getElementById('billTotal').textContent=fmt(billSum);
      document.getElementById('billApplyWrap').style.display='flex';
    }}
   }}catch(e){{ box.insertAdjacentHTML('beforeend','<div style="color:#c0392b">'+f.name+' 解析失败: '+e+'</div>'); }}
  }};
  rd.readAsArrayBuffer(f);
 }});
}}
function applyBills(){{
 document.querySelector('[name=recon_other]').value=Math.round(billSum);
 const note=document.querySelector('[name=recon_note]');
 note.value=(note.value?note.value+' · ':'')+'账单导入: '+billSrcs.join('+');
 recalcRecon();
}}
recalcRecon();
{notify}
</script>
{SUBMIT_GUARD}</body></html>"""


def parse_recon_save(fields):
    def num(key, default=0.0):
        v = fields.get(key, [""])[0].strip()
        return float(v) if v != "" else default

    month = fields.get("recon_month", [""])[0].strip() or datetime.date.today().strftime("%Y-%m")
    income = num("recon_income", 0)
    other = num("recon_other", 0)
    note = fields.get("recon_note", [""])[0].strip()
    cf = load_cf()
    c = compute(cf)
    hist = {r["月份"]: r for r in cfh_mod.load_history()}
    cur_month = datetime.date.today().strftime("%Y-%m")
    stored = hist.get(month)
    # 补账历史月：用该月草稿定格的固定支出/订阅数（配置后来可能改过）；当月用实时值
    if month != cur_month and stored:
        fixed, subs_m = stored["固定支出"], stored["订阅月支"]
    else:
        fixed, subs_m = c["fixed_out"], c["subs_monthly"]
    actual_net = income - fixed - other
    dca = _recon_dca(cf, actual_net)
    passive = (stored or {}).get("被动收入", 0)
    return cfh_mod.confirm_month(
        month, income, other, dca, passive, subs_m, fixed, note)


def insurance_page(policies, msg="", rebuilt=False, reward=""):
    notify = "if(window.parent!==window)window.parent.postMessage('rebuilt','*');" if rebuilt else ""
    banner = f'<div class="ok">{esc(msg)}</div>' if msg else ""
    banner += reward or ""
    kind_opts = "".join(f'<option>{k}</option>' for k in ins_mod.KINDS)
    status_opts = "".join(f'<option>{s}</option>' for s in ins_mod.STATUSES)
    freq_opts = "".join(f'<option>{f}</option>' for f in ins_mod.FREQS)

    rows = ""
    for p in ins_mod.enrich(policies):
        due_hint = (f'下次缴费 {p["nextDue"]} 应缴 ¥{p["perDue"]:,}/{p["频率"]}'
                    if p.get("nextDue") else
                    ("已缴清/无后续缴费" if p.get("状态") != "失效" else "已失效"))
        rows += f'''<div class="pol">
  <div class="pgrid">
   <div><label>成员</label><input name="ins_member" value="{esc(p.get("成员",""))}" oninput="recalcIns()"></div>
   <div><label>产品名</label><input name="ins_product" value="{esc(p.get("产品",""))}"></div>
   <div><label>险种</label><select name="ins_kind">{kind_opts.replace(f">{esc(p.get('险种','其他'))}<", f" selected>{esc(p.get('险种','其他'))}<", 1)}</select></div>
   <div><label>保额</label><input name="ins_amount" type="number" step="any" value="{esc(p.get("保额",0))}"></div>
   <div><label>年缴保费(年度总额)</label><input name="ins_premium" type="number" step="any" value="{esc(p.get("年缴保费",0))}" oninput="recalcIns()"></div>
   <div><label>缴费频率</label><select name="ins_freq">{freq_opts.replace(f">{esc(p.get('缴费频率','年'))}<", f" selected>{esc(p.get('缴费频率','年'))}<", 1)}</select></div>
   <div><label>下次缴费日</label><input name="ins_next" type="date" value="{esc(p.get("下次缴费日",""))}"></div>
   <div><label>缴费年限(0=续保型)</label><input name="ins_years" type="number" step="1" value="{esc(p.get("缴费年限",0))}"></div>
   <div><label>起保年份</label><input name="ins_start" type="number" step="1" value="{esc(p.get("起保年份",""))}"></div>
   <div><label>保障至(终身/70岁/年份)</label><input name="ins_till" value="{esc(p.get("保障至",""))}"></div>
   <div><label>状态</label><select name="ins_status" onchange="recalcIns()">{status_opts.replace(f">{esc(p.get('状态','缴费中'))}<", f" selected>{esc(p.get('状态','缴费中'))}<", 1)}</select></div>
   <div><label>备注</label><input name="ins_note" value="{esc(p.get("备注",""))}"></div>
  </div>
  <div class="prow"><span class="hint">{p["图标"]} {due_hint} · 月摊 ¥{p["monthly"]:,}</span>
   <button type="button" class="del" onclick="this.closest('.pol').remove();recalcIns()">✕ 删除</button></div>
 </div>'''

    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>保险台账</title>
<style>
 body{{font-family:-apple-system,"PingFang SC",system-ui,sans-serif;background:#f4f5f7;color:#222;margin:0;padding:24px}}
 .wrap{{max-width:900px;margin:0 auto}}
 h1{{font-size:20px;margin:0 0 4px}} .sub{{color:#888;font-size:13px;margin-bottom:18px}}
 .card{{background:#fff;border-radius:14px;padding:18px 20px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,.06)}}
 label{{display:block;font-size:12px;color:#666;margin:8px 0 4px}}
 input,select{{width:100%;box-sizing:border-box;padding:8px 10px;border:1px solid #ddd;border-radius:8px;font-size:14px}}
 .pol{{border:1px solid #eee;border-radius:12px;padding:12px 14px;margin-bottom:12px;background:#fafbfc}}
 .pgrid{{display:grid;grid-template-columns:repeat(5,1fr);gap:8px 12px}}
 .prow{{display:flex;justify-content:space-between;align-items:center;margin-top:8px}}
 .del{{border:none;background:#fbeaea;color:#c0392b;border-radius:8px;padding:6px 12px;cursor:pointer;font-size:13px}}
 .add{{border:1px dashed #bbb;background:#fafafa;color:#555;border-radius:8px;padding:10px;width:100%;cursor:pointer;margin-top:6px}}
 .live{{background:#eef4ff;border:1px solid #d6e4ff;border-radius:12px;padding:14px 16px;font-size:14px;line-height:1.9;margin-bottom:16px}}
 .live b{{color:#1554d1}}
 .btns{{display:flex;gap:12px;margin-top:14px}}
 button.save{{flex:1;padding:12px;border:none;border-radius:10px;font-size:15px;cursor:pointer}}
 .primary{{background:#1554d1;color:#fff}} .ghost{{background:#eee;color:#333}}
 .ok{{background:#e7f7ec;color:#1a7f37;padding:10px 14px;border-radius:10px;margin-bottom:16px}}
 .hint{{font-size:12px;color:#999}}
 @media(max-width:760px){{.pgrid{{grid-template-columns:repeat(2,1fr)}}}}
 /* ── 手机(≤480px):一律单列,不留横向溢出 ── */
 @media(max-width:480px){{
  body{{padding:12px}}
  .pgrid,.g3,.g4,.mrow,.kpis{{grid-template-columns:1fr !important;gap:6px}}
  .card{{padding:14px 13px;border-radius:12px}}
  h1{{font-size:18px}}
  input,select,button{{font-size:16px}}   /* iOS ≥16px 才不会自动放大页面 */
  .btns{{flex-direction:column}}
  table{{display:block;overflow-x:auto;-webkit-overflow-scrolling:touch}}
 }}

</style></head><body><div class="wrap">
<h1>🛡️ 保险台账</h1>
<div class="sub">保障型保单录入 · 年缴保费自动摊月计入固定支出 · 缴费日前 30/7/1 天面板提醒<br>
增额寿现金价值已作为资产在账户里，若录入请把状态设为「已缴清」（只台账展示，不摊月）</div>
{banner}
<form method="post" action="/insurance/save">
 <div class="live" id="live"></div>
 <div class="card">
  <div id="pols">{rows}</div>
  <button type="button" class="add" onclick="addPolicy()">+ 新增保单</button>
  <div class="btns">
   <button class="save primary" name="action" value="save_rebuild">💾 保存并刷新全景</button>
   <button class="save ghost" name="action" value="save">仅保存</button>
  </div>
 </div>
</form>
</div>
<script>
function addPolicy(){{
 const t=document.createElement('div');t.className='pol';
 t.innerHTML=`<div class="pgrid">
  <div><label>成员</label><input name="ins_member"></div>
  <div><label>产品名</label><input name="ins_product"></div>
  <div><label>险种</label><select name="ins_kind">{kind_opts}</select></div>
  <div><label>保额</label><input name="ins_amount" type="number" step="any" value="0"></div>
  <div><label>年缴保费(年度总额)</label><input name="ins_premium" type="number" step="any" value="0"></div>
  <div><label>缴费频率</label><select name="ins_freq">{freq_opts}</select></div>
  <div><label>下次缴费日</label><input name="ins_next" type="date"></div>
  <div><label>缴费年限(0=续保型)</label><input name="ins_years" type="number" step="1" value="0"></div>
  <div><label>起保年份</label><input name="ins_start" type="number" step="1"></div>
  <div><label>保障至(终身/70岁/年份)</label><input name="ins_till"></div>
  <div><label>状态</label><select name="ins_status"><option>缴费中</option><option>已缴清</option><option>失效</option></select></div>
  <div><label>备注</label><input name="ins_note"></div>
 </div>
 <div class="prow"><span class="hint">新保单</span>
  <button type="button" class="del">✕ 删除</button></div>`;
 t.querySelector('.del').onclick=()=>{{t.remove();recalcIns()}};
 t.querySelectorAll('input,select').forEach(i=>i.oninput=recalcIns);
 document.getElementById('pols').appendChild(t);
}}
function recalcIns(){{
 let yearly=0,n=0,total=0;
 document.querySelectorAll('.pol').forEach(p=>{{
  total++;
  const st=p.querySelector('[name=ins_status]').value;
  const prem=+p.querySelector('[name=ins_premium]').value||0;
  if(st==='缴费中'){{yearly+=prem;n++;}}
 }});
 const fmt=x=>'¥'+Math.round(x).toLocaleString();
 document.getElementById('live').innerHTML=
  '共 <b>'+total+'</b> 张保单，缴费中 <b>'+n+'</b> 张 · 年缴合计 <b>'+fmt(yearly)+'</b> → 摊月 <b>'+fmt(yearly/12)+'</b>（计入固定支出，拉低净结余与定投）';
}}
document.querySelectorAll('.pol input,.pol select').forEach(i=>i.oninput=recalcIns);
recalcIns();
{notify}
</script>
{SUBMIT_GUARD}</body></html>"""


def parse_insurance_save(fields):
    """平行列 zip → 保单列表。"""
    def lst(k):
        return fields.get(k, [])
    out = []
    for member, product, kind, amount, premium, freq, nxt, years, start, till, status, note in zip(
            lst("ins_member"), lst("ins_product"), lst("ins_kind"), lst("ins_amount"),
            lst("ins_premium"), lst("ins_freq"), lst("ins_next"), lst("ins_years"),
            lst("ins_start"), lst("ins_till"), lst("ins_status"), lst("ins_note")):
        member, product = member.strip(), product.strip()
        if not member and not product:
            continue
        def _f(v, d=0.0):
            try:
                return float(v)
            except ValueError:
                return d
        out.append({
            "成员": member, "产品": product, "险种": kind.strip() or "其他",
            "保额": _f(amount), "年缴保费": _f(premium),
            "缴费频率": freq.strip() or "年",
            "下次缴费日": nxt.strip(), "缴费年限": int(_f(years)),
            "起保年份": int(_f(start)) if start.strip() else "",
            "保障至": till.strip(),
            "状态": status.strip() or "缴费中", "备注": note.strip(),
        })
    return out


GOAL_CSS = """
 body{font-family:-apple-system,"PingFang SC",system-ui,sans-serif;background:#f4f5f7;color:#222;margin:0;padding:24px}
 .wrap{max-width:900px;margin:0 auto}
 h1{font-size:20px;margin:0 0 4px} .sub{color:#888;font-size:13px;margin-bottom:18px}
 .card{background:#fff;border-radius:14px;padding:18px 20px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,.06)}
 .card h2{font-size:15px;margin:0 0 10px}
 label{display:block;font-size:12px;color:#666;margin:8px 0 4px}
 input,select{width:100%;box-sizing:border-box;padding:8px 10px;border:1px solid #ddd;border-radius:8px;font-size:14px}
 .g3{display:grid;grid-template-columns:repeat(3,1fr);gap:8px 12px}
 .g4{display:grid;grid-template-columns:repeat(4,1fr);gap:8px 12px}
 .ev{border:1px solid #eee;border-radius:12px;padding:12px 14px;margin-bottom:12px;background:#fafbfc}
 .ev .row{display:flex;justify-content:space-between;align-items:center;margin-top:8px}
 .del{border:none;background:#fbeaea;color:#c0392b;border-radius:8px;padding:6px 12px;cursor:pointer;font-size:13px}
 .add{border:1px dashed #bbb;background:#fafafa;color:#555;border-radius:8px;padding:10px;width:100%;cursor:pointer;margin-top:6px}
 .btns{display:flex;gap:12px;margin-top:14px}
 button.save{flex:1;padding:12px;border:none;border-radius:10px;font-size:15px;cursor:pointer}
 .primary{background:#1554d1;color:#fff} .ghost{background:#eee;color:#333}
 .ok{background:#e7f7ec;color:#1a7f37;padding:10px 14px;border-radius:10px;margin-bottom:16px}
 .hint{font-size:12px;color:#999}
 .tip{background:#fffbe9;border:1px solid #f0dc9a;border-radius:10px;padding:10px 14px;font-size:13px;margin-bottom:12px;line-height:1.7}
 @media(max-width:760px){.g3,.g4{grid-template-columns:repeat(2,1fr)}}
 /* ── 手机(≤480px):一律单列,不留横向溢出 ── */
 @media(max-width:480px){
  body{padding:12px}
  .pgrid,.g3,.g4,.mrow,.kpis{grid-template-columns:1fr !important;gap:6px}
  .card{padding:14px 13px;border-radius:12px}
  h1{font-size:18px}
  input,select,button{font-size:16px}   /* iOS ≥16px 才不会自动放大页面 */
  .btns{flex-direction:column}
  table{display:block;overflow-x:auto;-webkit-overflow-scrolling:touch}
 }

"""


def goal_page(msg="", rebuilt=False, reward=""):
    """目标与重大事件:面板的导航中心。没有这一步,系统只会说「现在怎样」,不会说「要去哪」。"""
    import storage
    g = storage.load_doc("goal", {})
    hx = g.get("换房") or {}
    tgt = (g.get("目标态") or {}).get("大类") or {}
    fi = g.get("FI") or {}
    events = g.get("重大事件") or []
    notify = "if(window.parent!==window)window.parent.postMessage('rebuilt','*');" if rebuilt else ""
    banner = f'<div class="ok">{esc(msg)}</div>' if msg else ""
    banner += reward or ""
    ob = onboard_state()
    hint = onboard_hint("goal", ob)

    conf_opts = "".join(f'<option>{c}</option>' for c in metrics.CONFIDENCE)
    ev_rows = ""
    for e in events:
        conf = e.get("确信度", "计划")
        sel = conf_opts.replace(f">{conf}<", f" selected>{conf}<", 1)
        rng = e.get("月度影响区间") or {}
        dm = "" if rng else str(sum((e.get("月度影响") or {}).values()) or "")
        ev_rows += f'''<div class="ev">
  <div class="g4">
   <div><label>名称</label><input name="ev_name" value="{esc(e.get("名称",""))}"></div>
   <div><label>日期(YYYY-MM)</label><input name="ev_date" value="{esc(e.get("日期",""))}" placeholder="2029-09"></div>
   <div><label>确信度</label><select name="ev_conf">{sel}</select></div>
   <div><label>一次性金额(+进资产)</label><input name="ev_lump" type="number" step="any" value="{esc(e.get("一次性","") or "")}"></div>
   <div><label>月储蓄影响(+=每月多存)</label><input name="ev_dm" type="number" step="any" value="{esc(dm)}"></div>
   <div><label>区间下限(可空)</label><input name="ev_lo" type="number" step="any" value="{esc(rng.get("lo","") if rng else "")}"></div>
   <div><label>区间上限(可空)</label><input name="ev_hi" type="number" step="any" value="{esc(rng.get("hi","") if rng else "")}"></div>
   <div><label>说明</label><input name="ev_note" value="{esc(e.get("说明",""))}"></div>
  </div>
  <div class="row"><span class="hint">{"⏳ 育儿类:填「结束年月」和「月额区间」" if e.get("类型")=="育儿" else "确信度决定时间轴上的实线/虚线/点线,猜测类会输出区间"}</span>
   <button type="button" class="del" onclick="this.closest('.ev').remove()">✕ 删除</button></div>
 </div>'''

    child = next((e for e in events if e.get("类型") == "育儿"), {})
    crng = child.get("月额区间") or {}
    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>目标与大事</title>
<style>{GOAL_CSS}</style></head><body>
<div class="wrap">
{hint}
<h1>🎯 目标与重大事件</h1>
<div class="sub">面板的导航中心 —— 没有它,系统只会告诉你「现在怎样」,不会告诉你「要去哪」。</div>
{banner}
<form method="post" action="/save-goal">

<div class="card">
  <h2>财务自由口径</h2>
  <div class="tip">FI 线只算<b>终身支出</b>(现金流里「终身」=否的项,如房贷、幼儿园,不计入)。
   但育儿在孩子成年前<b>仍要供</b> —— 那笔钱单列为「育儿储备」,加进真·自由线。</div>
  <div class="g3">
    <div><label>安全提取率</label><input name="swr" type="number" step="0.001" value="{fi.get("提取率",0.035)}"></div>
    <div><label>育儿月额下限</label><input name="child_lo" type="number" step="any" value="{esc(crng.get("lo",""))}" placeholder="8000"></div>
    <div><label>育儿月额上限</label><input name="child_hi" type="number" step="any" value="{esc(crng.get("hi",""))}" placeholder="12000"></div>
    <div><label>育儿结束(孩子成年,YYYY-MM)</label><input name="child_end" value="{esc(child.get("结束",""))}" placeholder="2040-09"></div>
  </div>
</div>

<div class="card">
  <h2>换房计划(可选)</h2>
  <div class="tip">卖掉现有房产 → 买一套更便宜的 → 净释放的钱进入金融资产。
   量级通常<b>远超日常再平衡</b>,是面板 00 区的头号事件。</div>
  <div class="g4">
    <div><label>启用</label><select name="hx_on"><option value="1"{" selected" if hx.get("启用") else ""}>是</option><option value="0"{"" if hx.get("启用") else " selected"}>否</option></select></div>
    <div><label>目标日</label><input name="hx_date" type="date" value="{esc(hx.get("目标日",""))}"></div>
    <div><label>启动截止</label><input name="hx_start" type="date" value="{esc(hx.get("启动截止",""))}"></div>
    <div><label>卖出账户名</label><input name="hx_sell" value="{esc(hx.get("卖出账户",""))}" placeholder="与 accounts.csv 一致"></div>
    <div><label>新房预算上限(占净资产)</label><input name="hx_pct" type="number" step="0.01" value="{hx.get("买入预算上限占比",0.2)}"></div>
    <div><label>预算封顶(元)</label><input name="hx_cap" type="number" step="any" value="{esc(hx.get("买入预算封顶",""))}"></div>
    <div><label>交易成本率</label><input name="hx_cost" type="number" step="0.001" value="{hx.get("交易成本率",0.02)}"></div>
    <div><label>过桥需自筹(元)</label><input name="hx_bridge" type="number" step="any" value="{esc(hx.get("过桥需自筹",""))}"></div>
  </div>
  <div class="hint" style="margin-top:8px">⚠ 过桥资金是<b>短期内必须拿得出的钱</b> —— 它不该放在权益里。</div>
</div>

<div class="card">
  <h2>目标态大类配置(换房后要长期持有的组合)</h2>
  <div class="g4">
    <div><label>房产</label><input name="t_prop" type="number" step="0.01" value="{tgt.get("房产",0.2)}"></div>
    <div><label>权益</label><input name="t_eq" type="number" step="0.01" value="{tgt.get("权益",0.5)}"></div>
    <div><label>债券类固收</label><input name="t_bond" type="number" step="0.01" value="{tgt.get("债券类固收",0.2)}"></div>
    <div><label>现金</label><input name="t_cash" type="number" step="0.01" value="{tgt.get("现金",0.05)}"></div>
    <div><label>黄金</label><input name="t_gold" type="number" step="0.01" value="{tgt.get("黄金",0.05)}"></div>
  </div>
  <div class="hint" style="margin-top:8px">SBBI 历史回放会用<b>这套权重</b>穿越 2005–2025(当前权重换房后就作废了)。</div>
</div>

<div class="card">
  <h2>重大事件(会改变月储蓄或资产的事)</h2>
  <div class="tip">系统<b>只做加法</b>,不理解因果 —— 换房和房贷结清的日期一致性由你保证
   (不一致会在「月储蓄阶梯」上一眼看出来)。<br>
   确信度:<b>合同</b>=写死的(还清贷款/保费缴清) · <b>计划</b>=自己定的(换房) · <b>猜测</b>=拍脑袋(兴趣班,可填区间)。</div>
  <div id="evs">{ev_rows}</div>
  <button type="button" class="add" onclick="addEv()">+ 加一个事件</button>
</div>

<div class="btns">
  <button class="save ghost" name="act" value="save">保存</button>
  <button class="save primary" name="act" value="save_rebuild">保存并刷新全景</button>
</div>
</form>
</div>
<script>
function addEv(){{
  const d=document.createElement('div'); d.className='ev';
  d.innerHTML=`<div class="g4">
   <div><label>名称</label><input name="ev_name"></div>
   <div><label>日期(YYYY-MM)</label><input name="ev_date" placeholder="2029-09"></div>
   <div><label>确信度</label><select name="ev_conf">{conf_opts}</select></div>
   <div><label>一次性金额(+进资产)</label><input name="ev_lump" type="number" step="any"></div>
   <div><label>月储蓄影响(+=每月多存)</label><input name="ev_dm" type="number" step="any"></div>
   <div><label>区间下限(可空)</label><input name="ev_lo" type="number" step="any"></div>
   <div><label>区间上限(可空)</label><input name="ev_hi" type="number" step="any"></div>
   <div><label>说明</label><input name="ev_note"></div>
  </div>
  <div class="row"><span class="hint">确信度决定时间轴上的实线/虚线/点线</span>
   <button type="button" class="del" onclick="this.closest('.ev').remove()">✕ 删除</button></div>`;
  document.getElementById('evs').appendChild(d);
}}
{notify}
</script>{SUBMIT_GUARD}</body></html>"""


def parse_goal(form):
    """表单 → goal doc。育儿(类型=育儿)由「财务自由口径」那几个字段单独生成。"""
    import storage
    g = storage.load_doc("goal", {})

    def one(k, cast=float, default=None):
        v = (form.get(k, [""])[0] or "").strip()
        if not v:
            return default
        try:
            return cast(v)
        except ValueError:
            return default

    g["FI"] = {"提取率": one("swr", float, 0.035),
               "实际回报情景": (g.get("FI") or {}).get("实际回报情景", [0.02, 0.04, 0.06])}
    g["换房"] = {
        "启用": form.get("hx_on", ["0"])[0] == "1",
        "目标日": form.get("hx_date", [""])[0], "启动截止": form.get("hx_start", [""])[0],
        "卖出账户": form.get("hx_sell", [""])[0],
        "买入预算上限占比": one("hx_pct", float, 0.2),
        "买入预算封顶": one("hx_cap", float),
        "交易成本率": one("hx_cost", float, 0.02),
        "过桥需自筹": one("hx_bridge", float, 0),
        "说明": (g.get("换房") or {}).get("说明", ""),
    }
    g["目标态"] = {"日期": form.get("hx_date", [""])[0], "无房贷": True, "大类": {
        "房产": one("t_prop", float, 0.2), "权益": one("t_eq", float, 0.5),
        "债券类固收": one("t_bond", float, 0.2), "现金": one("t_cash", float, 0.05),
        "黄金": one("t_gold", float, 0.05)}}

    events = []
    names = form.get("ev_name", [])
    for i, nm in enumerate(names):
        nm = (nm or "").strip()
        if not nm:
            continue

        def f(key, cast=float):
            vals = form.get(key, [])
            v = (vals[i] if i < len(vals) else "") or ""
            v = v.strip()
            if not v:
                return None
            try:
                return cast(v)
            except ValueError:
                return None
        e = {"名称": nm, "日期": (form.get("ev_date", [""] * len(names))[i] or "").strip(),
             "确信度": (form.get("ev_conf", [""] * len(names))[i] or "计划").strip(),
             "说明": (form.get("ev_note", [""] * len(names))[i] or "").strip()}
        lump = f("ev_lump")
        if lump:
            e["一次性"] = lump
        lo, hi = f("ev_lo"), f("ev_hi")
        if lo is not None and hi is not None:
            e["月度影响区间"] = {"lo": lo, "mid": (lo + hi) / 2, "hi": hi}
        else:
            dm = f("ev_dm")
            if dm:
                e["月度影响"] = {"合计": dm}
        events.append(e)

    # 育儿事件由专门的字段生成(它不是时间点,是一段持续期)
    c_lo, c_hi = None, None
    try:
        c_lo = float(form.get("child_lo", [""])[0] or 0) or None
        c_hi = float(form.get("child_hi", [""])[0] or 0) or None
    except ValueError:
        pass
    c_end = (form.get("child_end", [""])[0] or "").strip()
    if c_lo and c_hi and c_end:
        events.append({"名称": "育儿开销(至孩子成年)", "类型": "育儿", "结束": c_end,
                       "确信度": "猜测",
                       "月额区间": {"lo": c_lo, "mid": (c_lo + c_hi) / 2, "hi": c_hi},
                       "说明": "不计入 FI 线(会结束),但结束前必须供 → 育儿储备"})
    g["重大事件"] = events
    return g


def loans_page(items, msg="", rebuilt=False, reward=""):
    """负债台账：余额按月推演（基准年月+基准本金+利率+月供），含留尾测算器。"""
    notify = "if(window.parent!==window)window.parent.postMessage('rebuilt','*');" if rebuilt else ""
    banner = f'<div class="ok">{esc(msg)}</div>' if msg else ""
    banner += reward or ""
    type_opts = "".join(f'<option>{k}</option>' for k in loans_mod.TYPES)
    status_opts = "".join(f'<option>{s}</option>' for s in loans_mod.STATUSES)

    rows = ""
    for x in items:
        rate_pct = "" if x.get("年利率") in ("", None) else f'{float(x["年利率"])*100:g}'
        hint = f'当前余额 ¥{x["余额"]:,.0f} · 利息月耗 ¥{x["利息月耗"]:,}'
        if x.get("还清约"):
            hint += f' · 还清约 {x["还清约"]}'
        if x.get("提示"):
            hint += f' · ⚠ {x["提示"]}'
        rows += f'''<div class="pol">
  <div class="pgrid">
   <div><label>名称</label><input name="loan_name" value="{esc(x.get("名称",""))}"></div>
   <div><label>类型</label><select name="loan_type">{type_opts.replace(f">{esc(x.get('类型','其他'))}<", f" selected>{esc(x.get('类型','其他'))}<", 1)}</select></div>
   <div><label>基准本金</label><input name="loan_base" type="number" step="any" value="{esc(x.get("基准本金",0))}"></div>
   <div><label>基准年月</label><input name="loan_baseym" type="month" value="{esc(x.get("基准年月",""))}"></div>
   <div><label>年利率%(空=不推演)</label><input name="loan_rate" type="number" step="any" value="{esc(rate_pct)}"></div>
   <div><label>月供</label><input name="loan_pmt" type="number" step="any" value="{esc(x.get("月供",0))}"></div>
   <div><label>还款日</label><input name="loan_payday" type="number" step="1" min="1" max="28" value="{esc(x.get("还款日",1))}"></div>
   <div><label>状态</label><select name="loan_status">{status_opts.replace(f">{esc(x.get('状态','在还'))}<", f" selected>{esc(x.get('状态','在还'))}<", 1)}</select></div>
   <div><label>备注</label><input name="loan_note" value="{esc(x.get("备注",""))}"></div>
   <div><label>常驻提醒(面板每日红色警示)</label><input name="loan_alert" value="{esc(x.get("提醒",""))}"></div>
  </div>
  <div class="prow"><span class="hint">💳 {hint}</span>
   <button type="button" class="del" onclick="this.closest('.pol').remove()">✕ 删除</button></div>
 </div>'''

    bal, ints = loans_mod.totals(items)
    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>负债台账</title>
<style>
 body{{font-family:-apple-system,"PingFang SC",system-ui,sans-serif;background:#f4f5f7;color:#222;margin:0;padding:24px}}
 .wrap{{max-width:900px;margin:0 auto}}
 h1{{font-size:20px;margin:0 0 4px}} .sub{{color:#888;font-size:13px;margin-bottom:18px}}
 .card{{background:#fff;border-radius:14px;padding:18px 20px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,.06)}}
 label{{display:block;font-size:12px;color:#666;margin:8px 0 4px}}
 input,select{{width:100%;box-sizing:border-box;padding:8px 10px;border:1px solid #ddd;border-radius:8px;font-size:14px}}
 .pol{{border:1px solid #eee;border-radius:12px;padding:12px 14px;margin-bottom:12px;background:#fafbfc}}
 .pgrid{{display:grid;grid-template-columns:repeat(5,1fr);gap:8px 12px}}
 .prow{{display:flex;justify-content:space-between;align-items:center;margin-top:8px}}
 .del{{border:none;background:#fbeaea;color:#c0392b;border-radius:8px;padding:6px 12px;cursor:pointer;font-size:13px}}
 .add{{border:1px dashed #bbb;background:#fafafa;color:#555;border-radius:8px;padding:10px;width:100%;cursor:pointer;margin-top:6px}}
 .live{{background:#eef4ff;border:1px solid #d6e4ff;border-radius:12px;padding:14px 16px;font-size:14px;line-height:1.9;margin-bottom:16px}}
 .live b{{color:#1554d1}}
 .btns{{display:flex;gap:12px;margin-top:14px}}
 button.save{{flex:1;padding:12px;border:none;border-radius:10px;font-size:15px;cursor:pointer}}
 .primary{{background:#1554d1;color:#fff}} .ghost{{background:#eee;color:#333}}
 .ok{{background:#e7f7ec;color:#1a7f37;padding:10px 14px;border-radius:10px;margin-bottom:16px}}
 .hint{{font-size:12px;color:#999}}
 table.tt{{border-collapse:collapse;margin-top:8px;font-size:13px}}
 table.tt td,table.tt th{{border:1px solid #eee;padding:5px 14px;text-align:right}}
 @media(max-width:760px){{.pgrid{{grid-template-columns:repeat(2,1fr)}}}}
 /* ── 手机(≤480px):一律单列,不留横向溢出 ── */
 @media(max-width:480px){{
  body{{padding:12px}}
  .pgrid,.g3,.g4,.mrow,.kpis{{grid-template-columns:1fr !important;gap:6px}}
  .card{{padding:14px 13px;border-radius:12px}}
  h1{{font-size:18px}}
  input,select,button{{font-size:16px}}   /* iOS ≥16px 才不会自动放大页面 */
  .btns{{flex-direction:column}}
  table{{display:block;overflow-x:auto;-webkit-overflow-scrolling:touch}}
 }}

</style></head><body><div class="wrap">
<h1>💳 负债台账</h1>
<div class="sub">余额按「基准年月+基准本金+利率+月供」自动按月推演，无需手动更新 ·
月供支出仍记在「现金流编辑→月度收支」（此处不重复计入固定支出）·
净资产口径以 accounts.csv 负债行为准，偏差大时全景会提示校准</div>
{banner}
<div class="live">在还余额合计 <b>¥{bal:,.0f}</b> · 利息月耗合计 <b>¥{ints:,}</b>（月供中只有利息是真消耗，本金是资产内转移）</div>
<form method="post" action="/loans/save">
 <div class="card">
  <div id="pols">{rows}</div>
  <button type="button" class="add" onclick="addLoan()">+ 新增负债</button>
  <div class="btns">
   <button class="save primary" name="action" value="save_rebuild">💾 保存并刷新全景</button>
   <button class="save ghost" name="action" value="save">仅保存</button>
  </div>
 </div>
</form>
<div class="card">
 <h2 style="font-size:16px;margin:0 0 6px">🧮 留尾测算</h2>
 <div class="hint">给定目标月供与利率，各期限能支撑的贷款本金（年金现值）——用于「留多大尾巴让公积金全额对冲」</div>
 <div style="display:flex;gap:12px;margin-top:10px;max-width:420px">
  <div style="flex:1"><label>目标月供</label><input id="tailPmt" type="number" value="14000"></div>
  <div style="flex:1"><label>年利率%</label><input id="tailRate" type="number" step="any" value="2.6"></div>
 </div>
 <div id="tailOut"></div>
</div>
</div>
<script>
function addLoan(){{
 const t=document.createElement('div');t.className='pol';
 t.innerHTML=`<div class="pgrid">
  <div><label>名称</label><input name="loan_name"></div>
  <div><label>类型</label><select name="loan_type">{type_opts}</select></div>
  <div><label>基准本金</label><input name="loan_base" type="number" step="any" value="0"></div>
  <div><label>基准年月</label><input name="loan_baseym" type="month"></div>
  <div><label>年利率%(空=不推演)</label><input name="loan_rate" type="number" step="any"></div>
  <div><label>月供</label><input name="loan_pmt" type="number" step="any" value="0"></div>
  <div><label>还款日</label><input name="loan_payday" type="number" step="1" min="1" max="28" value="1"></div>
  <div><label>状态</label><select name="loan_status"><option>在还</option><option>已清</option></select></div>
  <div><label>备注</label><input name="loan_note"></div>
  <div><label>常驻提醒(面板每日红色警示)</label><input name="loan_alert"></div>
 </div>
 <div class="prow"><span class="hint">新负债</span><button type="button" class="del">✕ 删除</button></div>`;
 t.querySelector('.del').onclick=()=>t.remove();
 document.getElementById('pols').appendChild(t);
}}
function tailCalc(){{
 const pmt=+document.getElementById('tailPmt').value||0;
 const r=(+document.getElementById('tailRate').value||0)/100, i=r/12;
 let html='<table class="tt"><tr><th>期限</th><th>可支撑本金</th></tr>';
 [5,7,10,15,20,25].forEach(y=>{{
  const n=y*12, p=i===0?pmt*n:pmt*(1-Math.pow(1+i,-n))/i;
  html+='<tr><td>'+y+' 年</td><td>¥'+Math.round(p).toLocaleString()+'</td></tr>';
 }});
 document.getElementById('tailOut').innerHTML=html+'</table>';
}}
document.getElementById('tailPmt').oninput=tailCalc;
document.getElementById('tailRate').oninput=tailCalc;
tailCalc();
{notify}
</script>
{SUBMIT_GUARD}</body></html>"""


def parse_loans_save(fields):
    """平行列 zip → 负债列表；年利率按 % 录入存小数，空=不推演。"""
    def lst(k):
        return fields.get(k, [])
    out = []
    for name, typ, base, ym, rate, pmt, payday, status, note, alert in zip(
            lst("loan_name"), lst("loan_type"), lst("loan_base"), lst("loan_baseym"),
            lst("loan_rate"), lst("loan_pmt"), lst("loan_payday"),
            lst("loan_status"), lst("loan_note"), lst("loan_alert")):
        name = name.strip()
        if not name:
            continue
        def _f(v, d=0.0):
            try:
                return float(v)
            except ValueError:
                return d
        out.append({
            "名称": name, "类型": typ.strip() or "其他",
            "基准本金": _f(base), "基准年月": ym.strip(),
            "年利率": (_f(rate) / 100) if rate.strip() else "",
            "月供": _f(pmt), "还款日": int(_f(payday, 1)) or 1,
            "状态": status.strip() or "在还", "备注": note.strip(),
            "提醒": alert.strip(),
        })
    return out


def holdings_page(msg="", rebuilt=False, reward=""):
    """持仓管理：自动估值持仓（数量变动自动记 holdings_history）+ 手动账户金额。
    引导第①步(还没有任何账户)时,「新增手动账户」自动展开并给常见账户示例——
    这个页面本来是为「改已有账户」设计的,新用户没有账户可改,会卡住。"""
    new_acct = not onboard_state()["done"].get("acct")
    notify = "if(window.parent!==window)window.parent.postMessage('rebuilt','*');" if rebuilt else ""
    banner = f'<div class="ok">{esc(msg)}</div>' if msg else ""
    banner += reward or ""
    holdings = hold_mod.load_holdings()
    quotes = hold_mod.quote_map()
    manual = hold_mod.load_manual()
    accounts = hold_mod.load_accounts()
    fx = _editor_fx()
    today = datetime.date.today()

    def hold_row(h, is_new=False):
        name = h.get("名称", "")
        q = quotes.get((h.get("腾讯查询代码") or "").strip()) or {}
        px, qty = q.get("price"), float(h.get("持有数量") or 0)
        ccy = hold_mod.MARKET_CCY.get(h.get("市场", ""), "CNY")
        if px is not None:
            hint = (f'最新价 {px:g} {ccy}({q.get("date","?")}) · '
                    f'现市值 ≈ ¥{px*qty*fx.get(ccy,1.0):,.0f}')
        elif name in manual:
            hint = f'无行情代码 · 手动市值 ¥{float(manual[name]["value"]):,.0f}（在下方手动账户区更新）'
        else:
            hint = '无行情缓存'
        return f'''<div class="pol">
  <div class="pgrid">
   <div><label>名称</label><input name="h_name" value="{esc(name)}"></div>
   <div><label>持有数量</label><input name="h_qty" type="number" step="any" value="{esc(h.get("持有数量",""))}"></div>
   <div><label>本次成交价(数量变动时记账用)</label><input name="h_px" type="number" step="any" placeholder="{'' if px is None else f'空=最新价 {px:g}'}"></div>
   <div><label>账户</label><input name="h_acct" value="{esc(h.get("账户",""))}"></div>
  </div>
  <details{' open' if is_new else ''}><summary class="hint">行情代码 / 分类</summary>
   <div class="pgrid" style="margin-top:6px">
    <div><label>代码</label><input name="h_code" value="{esc(h.get("代码",""))}"></div>
    <div><label>市场</label><input name="h_mkt" value="{esc(h.get("市场",""))}" list="mkts"></div>
    <div><label>资产类型</label><input name="h_type" value="{esc(h.get("资产类型",""))}" list="types"></div>
    <div><label>新浪查询代码</label><input name="h_sina" value="{esc(h.get("新浪查询代码",""))}"></div>
    <div><label>腾讯查询代码</label><input name="h_tx" value="{esc(h.get("腾讯查询代码",""))}"></div>
    <div><label>东财secid</label><input name="h_em" value="{esc(h.get("东财secid",""))}"></div>
    <div><label>流动性</label><input name="h_liq" value="{esc(h.get("流动性","数日"))}"></div>
    <div><label>股息率%</label><input name="h_div" type="number" step="any" value="{esc(h.get("股息率",""))}"></div>
   </div>
  </details>
  <div class="prow"><span class="hint">📈 {hint}</span>
   <button type="button" class="del" onclick="delHolding(this)">✕ 删除(自动记一笔全部卖出)</button></div>
 </div>'''

    rows = "".join(hold_row(h) for h in holdings)
    mkts = "".join(f'<option>{esc(m)}</option>' for m in sorted({h.get("市场", "") for h in holdings} - {""}))
    types = "".join(f'<option>{esc(t)}</option>' for t in sorted({h.get("资产类型", "") for h in holdings} - {""}))

    mrows = ""
    for name, info in manual.items():
        upd, note = info.get("updated", "?"), info.get("note", "")
        try:
            age = (today - datetime.date.fromisoformat(upd)).days
            stale = f'<b style="color:#c0392b">{age} 天前</b>' if age > 14 else f'{age} 天前'
        except ValueError:
            stale = "?"
        mrows += f'''<div class="mrow">
  <input name="m_name" value="{esc(name)}" readonly class="ro">
  <input name="m_value" type="number" step="any" value="{esc(info.get("value",""))}">
  <span class="hint">更新于 {esc(upd)}（{stale}）{('· ' + esc(note)) if note else ''}</span>
 </div>'''
    autos = "".join(f'<div class="mrow"><span class="ro" style="padding:8px 10px">{esc(a["账户名称"])}</span>'
                    f'<span class="hint">¥{float(a["金额或估值"] or 0):,.0f} · 自动推演/固定值，不在此编辑</span></div>'
                    for a in accounts
                    if a["账户名称"] not in manual and a.get("资产类型") != "负债")

    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>持仓管理</title>
<style>
 body{{font-family:-apple-system,"PingFang SC",system-ui,sans-serif;background:#f4f5f7;color:#222;margin:0;padding:24px}}
 .wrap{{max-width:900px;margin:0 auto}}
 h1{{font-size:20px;margin:0 0 4px}} h2{{font-size:16px;margin:0 0 6px}}
 .sub{{color:#888;font-size:13px;margin-bottom:18px}}
 .card{{background:#fff;border-radius:14px;padding:18px 20px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,.06)}}
 label{{display:block;font-size:12px;color:#666;margin:8px 0 4px}}
 input,select{{width:100%;box-sizing:border-box;padding:8px 10px;border:1px solid #ddd;border-radius:8px;font-size:14px}}
 .pol{{border:1px solid #eee;border-radius:12px;padding:12px 14px;margin-bottom:12px;background:#fafbfc}}
 .pgrid{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px 12px}}
 .prow{{display:flex;justify-content:space-between;align-items:center;margin-top:8px}}
 details summary{{cursor:pointer;margin-top:8px}}
 .del{{border:none;background:#fbeaea;color:#c0392b;border-radius:8px;padding:6px 12px;cursor:pointer;font-size:13px}}
 .add{{border:1px dashed #bbb;background:#fafafa;color:#555;border-radius:8px;padding:10px;width:100%;cursor:pointer;margin-top:6px}}
 .live{{background:#eef4ff;border:1px solid #d6e4ff;border-radius:12px;padding:14px 16px;font-size:14px;line-height:1.9;margin-bottom:16px}}
 .live b{{color:#1554d1}}
 .btns{{display:flex;gap:12px;margin-top:14px}}
 button.save{{flex:1;padding:12px;border:none;border-radius:10px;font-size:15px;cursor:pointer}}
 .primary{{background:#1554d1;color:#fff}} .ghost{{background:#eee;color:#333}}
 .ok{{background:#e7f7ec;color:#1a7f37;padding:10px 14px;border-radius:10px;margin-bottom:16px}}
 .hint{{font-size:12px;color:#999}}
 .mrow{{display:grid;grid-template-columns:180px 160px 1fr;gap:10px;align-items:center;
        padding:7px 0;border-bottom:1px solid #f0f0f0}}
 .ro{{background:#f4f5f7;color:#555;border-color:#eee}}
 @media(max-width:760px){{.pgrid{{grid-template-columns:repeat(2,1fr)}}.mrow{{grid-template-columns:1fr 1fr}}}}
 /* ── 手机(≤480px):一律单列,不留横向溢出 ── */
 @media(max-width:480px){{
  body{{padding:12px}}
  .pgrid,.g3,.g4,.mrow,.kpis{{grid-template-columns:1fr !important;gap:6px}}
  .card{{padding:14px 13px;border-radius:12px}}
  h1{{font-size:18px}}
  input,select,button{{font-size:16px}}   /* iOS ≥16px 才不会自动放大页面 */
  .btns{{flex-direction:column}}
  table{{display:block;overflow-x:auto;-webkit-overflow-scrolling:touch}}
 }}

{OB_CSS}
</style></head><body><div class="wrap">
{onboard_hint(onboard_state()["cur"] if onboard_state()["cur"] in ("acct","hold") else "", onboard_state())}
<h1>📦 持仓管理</h1>
<div class="sub">改数量/新增/删除 → 保存时自动往 holdings_history 记一笔买入/卖出（成交价空则取最新行情），
浮盈成本口径不会跑偏 · 手动账户金额在下方更新（写 manual_values 并盖日期戳）· 保存后当晚 19:30 定时任务照常重估</div>
{banner}
<datalist id="mkts">{mkts}</datalist><datalist id="types">{types}</datalist>
<form method="post" action="/holdings/save">
 <div class="card">
  <h2>自动估值持仓（{len(holdings)} 项）</h2>
  <div id="pols">{rows}</div>
  <button type="button" class="add" onclick="addHolding()">+ 新增持仓（自动记一笔买入）</button>
  <div class="pgrid" style="margin-top:10px;grid-template-columns:1fr 2fr">
   <div><label>本次变动原因（写进台账备注，日后复盘用）</label>
    <select name="t_reason"><option value="">（无变动/缺省）</option><option>定投</option>
    <option>迁移沪深300</option><option>再平衡</option><option>其他</option></select></div>
   <div><label>补充备注（可选，如“第2批”“回执价”）</label><input name="t_note"></div>
  </div>
 </div>
 <div class="card">
  <h2>手动账户金额（{len(manual)} 项）</h2>
  <div class="hint">投顾/理财/存款/房产/期权等无法自动取价的条目；改了数值才盖新日期戳</div>
  {mrows}{autos}
  <details style="margin-top:10px"{" open" if new_acct else ""}>
   <summary class="hint">+ 新增手动账户（accounts 加行 + 建手动值）</summary>
   {'''<div class="tip" style="background:#fffbe9;border:1px solid #f0dc9a;border-radius:10px;
     padding:9px 12px;font-size:12.5px;margin:8px 0;line-height:1.7">
     常见账户,照着建:<b>日常现金</b>(工资进/开销出,类型=现金存款,流动性=即时) ·
     <b>银行理财</b>(固收理财,数日) · <b>房产</b>(不动产,极低) · <b>增额寿</b>(类固收保险,锁定)。
     <br>一次填一个,保存后再回来加下一个。</div>''' if new_acct else ""}
   <div class="pgrid" style="margin-top:6px">
    <div><label>账户名称</label><input name="a_name"></div>
    <div><label>资产类型</label><input name="a_type" value="现金存款" list="atypes"></div>
    <div><label>金额</label><input name="a_value" type="number" step="any"></div>
    <div><label>流动性</label><input name="a_liq" value="即时" list="aliq"></div>
    <div><label>备注</label><input name="a_note"></div>
   </div>
   <datalist id="atypes"><option>现金存款</option><option>固收理财</option>
    <option>类固收保险</option><option>不动产</option><option>投顾组合</option></datalist>
   <datalist id="aliq"><option>即时</option><option>数日</option><option>锁定</option>
    <option>极低</option></datalist>
  </details>
  <div class="btns">
   <button class="save primary" name="action" value="save_rebuild">💾 保存并刷新全景</button>
   <button class="save ghost" name="action" value="save">仅保存</button>
  </div>
 </div>
</form>
</div>
<script>
function delHolding(btn){{
 const row=btn.closest('.pol'), nm=(row.querySelector('input[name=h_name]')||{{}}).value||'该持仓';
 if(confirm('确认删除「'+nm+'」？\\n保存时将按最新价自动记一笔全部卖出进台账。')) row.remove();
}}
function addHolding(){{
 const t=document.createElement('div');t.className='pol';
 t.innerHTML=`<div class="pgrid">
  <div><label>名称</label><input name="h_name"></div>
  <div><label>持有数量</label><input name="h_qty" type="number" step="any" value="0"></div>
  <div><label>本次成交价(记账用)</label><input name="h_px" type="number" step="any"></div>
  <div><label>账户</label><input name="h_acct"></div>
 </div>
 <details open><summary class="hint">行情代码 / 分类</summary>
  <div class="pgrid" style="margin-top:6px">
   <div><label>代码</label><input name="h_code"></div>
   <div><label>市场</label><input name="h_mkt" list="mkts"></div>
   <div><label>资产类型</label><input name="h_type" list="types"></div>
   <div><label>新浪查询代码</label><input name="h_sina"></div>
   <div><label>腾讯查询代码</label><input name="h_tx"></div>
   <div><label>东财secid</label><input name="h_em"></div>
   <div><label>流动性</label><input name="h_liq" value="数日"></div>
   <div><label>股息率%</label><input name="h_div" type="number" step="any"></div>
  </div>
 </details>
 <div class="prow"><span class="hint">新持仓</span><button type="button" class="del">✕ 删除</button></div>`;
 t.querySelector('.del').onclick=()=>t.remove();
 document.getElementById('pols').appendChild(t);
}}
{notify}
</script>
{SUBMIT_GUARD}</body></html>"""


def parse_holdings_save(fields):
    """平行列 zip → (持仓行, {名称:成交价}, {名称:手动金额}, 新账户元组)。重名行只保留首个。"""
    def lst(k):
        return fields.get(k, [])
    rows, prices, seen = [], {}, set()
    for name, qty, px, acct, code, mkt, typ, sina, tx, em, liq, div in zip(
            lst("h_name"), lst("h_qty"), lst("h_px"), lst("h_acct"),
            lst("h_code"), lst("h_mkt"), lst("h_type"), lst("h_sina"),
            lst("h_tx"), lst("h_em"), lst("h_liq"), lst("h_div")):
        name = name.strip()
        if not name or name in seen:
            continue
        seen.add(name)
        rows.append({"名称": name, "代码": code.strip(), "市场": mkt.strip(),
                     "资产类型": typ.strip(), "账户": acct.strip(),
                     "持有数量": qty.strip() or "0",
                     "新浪查询代码": sina.strip(), "腾讯查询代码": tx.strip(),
                     "东财secid": em.strip(), "流动性": liq.strip() or "数日",
                     "股息率": div.strip()})
        if px.strip():
            prices[name] = px.strip()
    manual_updates = {n.strip(): v for n, v in zip(lst("m_name"), lst("m_value"))
                      if n.strip() and v.strip()}
    acct_new = (lst("a_name") or [""])[0], (lst("a_type") or [""])[0], \
               (lst("a_value") or [""])[0], (lst("a_liq") or [""])[0], \
               (lst("a_note") or [""])[0]
    note = "；".join(x.strip() for x in [(lst("t_reason") or [""])[0],
                                         (lst("t_note") or [""])[0]] if x.strip())
    return rows, prices, manual_updates, acct_new, note


def _has_data():
    """有没有真实数据?——判断是不是第一次用。演示模式永远算「有」。"""
    import storage
    if storage.DEMO:
        return True
    try:
        if storage.load_table("accounts", []):
            return True
        if storage.load_table("holdings", []):
            return True
    except Exception:
        pass
    return False


# ── 引导:四步,每步点亮一部分面板 ──────────────────────────────────
# 设计原则(见 docs/2029-plan.md 与会话中的 grilling):
#   · 不新建一套向导 UI —— 复用现有 Tab 的表单,只加「顺序 + 说明 + 下一步」
#   · 每步保存后先给成果反馈(「净资产 30 万」),让填数据这件苦差事有即时回报
#   · 没数据时全景 Tab 显示 demo(可点可下钻),但挂甩不掉的角标提醒「这不是你的数据」
ONBOARD_STEPS = [
    {"k": "acct", "tab": "hold", "n": "账户资产",
     "why": "先把「你有多少钱」填对 —— 理财、存款、房产、日常现金。",
     "get": "净资产、大类配置、资产全景",
     "warn": "日常现金账户(工资进、开销出)一定要建 —— 它的月度余额变化是唯一诚实的储蓄信号。"},
    {"k": "flow", "tab": "edit", "n": "收入支出",
     "why": "你每月赚多少、花多少。",
     "get": "储蓄率、现金流月账、财务自由推演",
     "warn": "最容易犯的错:只填房贷房租,不填吃饭购物 → 储蓄率虚高一倍,后面所有推演跟着错。"},
    {"k": "hold", "tab": "hold", "n": "持仓",
     "why": "股票/ETF —— 能实时报价的那些。没有可以跳过。",
     "get": "实时行情、持仓地图、浮盈、再平衡执行单",
     "warn": ""},
    {"k": "goal", "tab": "goal", "n": "目标与大事",
     "why": "未来几年会改变你财务结构的事:换房、还清贷款、子女教育。",
     "get": "未来大事时间轴、真·自由线、换房路线图",
     "warn": "没有这一步,面板只会告诉你「现在怎样」,不会告诉你「要去哪」。"},
]


def onboard_state():
    """→ {done:{k:bool}, cur:步骤k或None, all_done:bool}。判定只看「有没有实质内容」。"""
    import storage
    done = {}
    try:
        done["acct"] = bool(storage.load_table("accounts", []))
    except Exception:
        done["acct"] = False
    try:
        cf = storage.load_doc("cashflow", {})
        done["flow"] = bool(cf.get("收入明细") and cf.get("月度收支"))
    except Exception:
        done["flow"] = False
    try:
        done["hold"] = bool(storage.load_table("holdings", []))
    except Exception:
        done["hold"] = False
    try:
        g = storage.load_doc("goal", {})
        done["goal"] = bool(g.get("重大事件") or g.get("目标态"))
    except Exception:
        done["goal"] = False
    cur = next((s["k"] for s in ONBOARD_STEPS if not done.get(s["k"])), None)
    return {"done": done, "cur": cur, "all_done": cur is None}


def onboard_bar(state, skipped=False):
    """壳页顶部进度条。全部完成或用户点了跳过 → 不显示(引导退化为健康检查)。"""
    if skipped or state["all_done"]:
        return ""
    cells = []
    for i, s in enumerate(ONBOARD_STEPS):
        k = s["k"]
        if state["done"].get(k):
            mark, cls = "✅", "ok"
        elif k == state["cur"]:
            mark, cls = "◉", "cur"
        else:
            mark, cls = "⬜", ""
        cells.append(f'<button class="ob-step {cls}" data-tab="{s["tab"]}" data-step="{k}">'
                     f'{mark} <b>{["①","②","③","④"][i]}</b> {s["n"]}</button>')
    return ('<div id="obbar"><span class="ob-t">开始使用</span>'
            + '<span class="ob-sep">—</span>'.join(cells)
            + '<a class="ob-skip" href="/skip-onboard">跳过引导</a></div>')


def onboard_hint(step_key, state):
    """插在各 Tab 顶部的引导说明条。不在引导中 → 空。"""
    if state["all_done"] or state["cur"] != step_key:
        return ""
    s = next((x for x in ONBOARD_STEPS if x["k"] == step_key), None)
    if not s:
        return ""
    i = [x["k"] for x in ONBOARD_STEPS].index(step_key)
    nxt = ONBOARD_STEPS[i + 1]["n"] if i + 1 < len(ONBOARD_STEPS) else None
    warn = f'<div class="ob-warn">⚠ {esc(s["warn"])}</div>' if s["warn"] else ""
    return f'''<div class="ob-hint">
  <div class="ob-hd">第{["①","②","③","④"][i]}步 · {esc(s["n"])}</div>
  <div class="ob-why">{esc(s["why"])}</div>
  <div class="ob-get">填完你会解锁:<b>{esc(s["get"])}</b></div>
  {warn}
  <div class="ob-nx">保存后{"自动进入「"+esc(nxt)+"」" if nxt else "引导结束,进入完整面板"}</div>
</div>'''


def onboard_reward(step_key):
    """保存后的即时成果:「你刚解锁了什么」——填数据这件苦差事必须有回报,
    否则用户填完第一步会觉得「什么都没发生」然后流失。
    返回一段追加到保存提示后面的文案(不在引导中 → 空)。"""
    st = onboard_state()
    if st["all_done"] and step_key != "goal":
        # 全部填完后不再报喜(除了最后一步的收尾)
        pass
    try:
        from portfolio_tracker import compute
        R = compute()
        nw, fin = R["networth"], R["financial"]
    except Exception:
        return ""
    parts = []
    if step_key == "acct" and nw:
        parts.append(f"你的净资产:<b>¥{nw/1e4:,.1f}万</b>")
        parts.append("大类配置和资产全景已点亮")
    elif step_key == "flow":
        try:
            import cashflow_income as inc
            cf = storage.load_doc("cashflow", {})
            items = inc.income_items_net(cf)
            income = sum(i["金额"] for i in items)
            out = subs_mod.cashflow_fixed_out(cf, {"USD": 7.0, "HKD": 0.9, "CNY": 1.0})
            net = income - out
            if income:
                parts.append(f"储蓄率:<b>{net/income*100:.0f}%</b>(月结余 ¥{net:,.0f})")
                parts.append("现金流月账 + 财务自由推演已点亮")
        except Exception:
            pass
    elif step_key == "hold" and fin:
        parts.append(f"金融资产:<b>¥{fin/1e4:,.1f}万</b> · 实时行情已接上")
        parts.append("持仓地图 / 浮盈 / 再平衡执行单已点亮")
    elif step_key == "goal":
        try:
            g = storage.load_doc("goal", {})
            if g.get("重大事件"):
                parts.append(f"{len(g['重大事件'])} 个重大事件已进入时间轴")
                parts.append("真·自由线 + 未来大事已点亮")
        except Exception:
            pass
    if not parts:
        return ""
    nxt = ""
    ks = [s["k"] for s in ONBOARD_STEPS]
    if step_key in ks:
        i = ks.index(step_key)
        if i + 1 < len(ONBOARD_STEPS) and not st["done"].get(ONBOARD_STEPS[i + 1]["k"]):
            nxt = f'<br>👉 下一步:<b>{ONBOARD_STEPS[i+1]["n"]}</b>'
        elif st["all_done"]:
            nxt = "<br>🎉 引导完成 —— 面板已全部点亮,进度条会消失。"
    return f'<div class="ob-reward">🎁 解锁:{" · ".join(parts)}{nxt}</div>'


OB_CSS = """
.ob-reward{border:3px solid #1baf7a;background:#e7f7ec;padding:11px 14px;margin-top:10px;
  font-weight:800;font-size:13.5px;line-height:1.7;border-radius:0}
.ob-reward b{color:#0d7a52;font-size:15px}
#obbar{display:flex;align-items:center;gap:0;flex-wrap:wrap;padding:9px 14px;
  background:#eda100;border-bottom:3px solid #111;font-weight:800;font-size:12.5px}
#obbar .ob-t{font-weight:900;letter-spacing:.1em;margin-right:12px}
#obbar .ob-sep{opacity:.45;margin:0 2px}
.ob-step{border:2px solid #111;background:#fff;padding:4px 10px;font-weight:800;
  font-size:12.5px;cursor:pointer;font-family:inherit}
.ob-step.cur{background:#111;color:#f4f1ea}
.ob-step.ok{background:#1baf7a;color:#fff}
.ob-skip{margin-left:auto;font-weight:800;color:#111;opacity:.7;font-size:12px}
.ob-hint{border:3px solid #111;background:#fffbe9;box-shadow:5px 5px 0 #eda100;
  padding:13px 16px;margin:0 0 16px}
.ob-hint .ob-hd{font-weight:900;font-size:15px;margin-bottom:4px}
.ob-hint .ob-why{font-weight:700;font-size:13.5px}
.ob-hint .ob-get{font-weight:700;font-size:13px;margin-top:5px}
.ob-hint .ob-warn{border:2px dashed #e34948;padding:7px 10px;margin-top:8px;
  font-weight:800;font-size:12.5px}
.ob-hint .ob-nx{font-size:11.5px;font-weight:700;opacity:.6;margin-top:7px}
"""


def welcome_page():
    """空数据时的上手页:一片空白的面板毫无意义,给两条明确的路。"""
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>资产全景 · 上手</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>📊</text></svg>">
<style>
 *{{margin:0;padding:0;box-sizing:border-box}}
 body{{background:#f4f1ea;color:#111;font:15px/1.6 "Helvetica Neue","PingFang SC",system-ui,sans-serif;
   max-width:820px;margin:0 auto;padding:48px 26px}}
 h1{{font-size:36px;font-weight:900;border-bottom:5px solid #111;padding-bottom:12px;margin-bottom:8px}}
 .sub{{font-weight:700;opacity:.65;margin-bottom:30px}}
 .card{{border:3px solid #111;background:#fff;box-shadow:6px 6px 0 #111;padding:20px 24px;margin-bottom:22px}}
 .card h2{{font-size:19px;font-weight:900;margin-bottom:6px}}
 .card p{{font-weight:600;font-size:14px;margin-bottom:10px}}
 .card.hero{{box-shadow:6px 6px 0 #2a78d6}}
 code{{background:#111;color:#f4f1ea;padding:2px 8px;font-weight:800;font-size:13px;display:inline-block;margin:3px 0}}
 ol{{margin:8px 0 0 20px;font-weight:700;font-size:14px}} ol li{{margin:5px 0}}
 .warn{{border:2.5px dashed #e34948;padding:10px 13px;margin-top:12px;font-weight:800;font-size:13px}}
</style></head><body>
<h1>资产全景</h1>
<div class="sub">还没有数据 —— 选一条路开始。</div>

<div class="card hero">
  <h2>① 先看效果(1 分钟)</h2>
  <p>用演示数据(虚构人物「张小满」一家)跑一遍,看看这东西长什么样、值不值得你花时间填。</p>
  <code>PANORAMA_DEMO=1 python3 rebuild_views.py &amp;&amp; open demo/panorama_poster.html</code>
  <p style="margin-top:8px">或者交互式:<code>python3 start.py</code> → 选 1</p>
</div>

<div class="card">
  <h2>② 录入我的数据</h2>
  <p>从演示配置起步——<b>它就是最好的填写参照</b>(每个字段该填什么样,照着改)。</p>
  <code>python3 start.py</code> → 选 2
  <ol>
    <li><b>accounts.csv + manual_values.json</b> —— 先把「你有多少钱」填对,这步做完就有净资产和大类配置了</li>
    <li><b>cashflow.json</b> —— 收入 + 支出</li>
    <li><b>holdings.csv</b> —— 有股票/ETF 才需要</li>
    <li><b>goal.json</b> —— 你未来几年的大事(换房/还清贷款/子女教育)。没有它,面板只会说「现在怎样」,不会说「要去哪」</li>
    <li>订阅/保险/负债按需填,不填不影响主流程</li>
  </ol>
  <div class="warn">⚠ 最容易犯的错:月度支出只填房贷房租、不填吃饭购物 —— 储蓄率会虚高一倍,
  后面所有的财务自由推演都跟着错。</div>
</div>

<div class="card">
  <h2>③ 填完之后</h2>
  <p>刷新本页即可进入面板;或在终端跑:</p>
  <code>python3 portfolio_tracker.py</code> 终端看数字<br>
  <code>python3 rebuild_views.py</code> 生成全景面板
</div>
</body></html>"""


def shell(default="origin", skin="", ob=None, ob_skipped=False):
    """外壳页：顶部标签栏 + iframe。持仓全景 / 现金流编辑 一页切换。
    skin='poster' 时壳页换新粗野主义外观(选中「色块海报」主题即全面板跟随)。
    ob: onboard_state() —— 引导中会多一条进度条;第①步没填完时全景 Tab 预览 demo。"""
    if default not in THEMES:
        default = next(iter(THEMES))
    ob = ob or onboard_state()
    show_ob = not ob_skipped and not ob["all_done"]
    preview = not ob["done"].get("acct")      # 还没填账户 → 全景位先给 demo 看(可点可下钻)
    ob_bar = onboard_bar(ob, ob_skipped)
    first_tab = next((s["tab"] for s in ONBOARD_STEPS if s["k"] == ob["cur"]), "view") \
        if show_ob else "view"
    opts = "".join(f'<option value="{k}"{" selected" if k==default else ""}>{name}</option>'
                   for k, (name, _) in THEMES.items())
    # 只有一个主题时不显示下拉
    sel_html = f'<select id="theme" title="全景主题">{opts}</select>' if len(THEMES) > 1 else ''
    skin_cls = "skin-poster" if skin == "poster" else ""
    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>资产面板</title>
<style>
 html,body{{margin:0;height:100%;font-family:-apple-system,"PingFang SC",system-ui,sans-serif;background:#f4f5f7}}
 nav{{display:flex;align-items:center;gap:14px;padding:8px 16px;background:#fff;border-bottom:1px solid #e8e8e8;
      position:sticky;top:0;z-index:10}}
 .tab{{border:none;background:transparent;font-size:15px;color:#666;padding:8px 14px;border-radius:9px;cursor:pointer}}
 .tab.active{{background:#eef2ff;color:#1554d1;font-weight:600}}
 .right{{margin-left:auto;display:flex;align-items:center;gap:10px}}
 select{{padding:7px 9px;border:1px solid #ddd;border-radius:8px;font-size:13px}}
 #rebuild{{border:1px solid #1554d1;background:#1554d1;color:#fff;padding:8px 14px;border-radius:9px;cursor:pointer;font-size:13px}}
 #rebuild:disabled{{opacity:.6;cursor:default}}
 iframe{{border:0;width:100%;height:calc(100vh - 53px);display:block;background:#f4f5f7}}
 /* ── skin=poster:壳页跟随色块海报 ── */
 body.skin-poster{{background:#f4f1ea;font-family:"Helvetica Neue","PingFang SC",system-ui,sans-serif}}
 .skin-poster nav{{background:#111;border-bottom:3px solid #111;gap:8px}}
 .skin-poster .tab{{color:#cfc9bb;border-radius:0;font-weight:800;letter-spacing:.02em}}
 .skin-poster .tab.active{{background:#f4f1ea;color:#111;font-weight:900}}
 .skin-poster select{{border:2px solid #111;border-radius:0;font-weight:800;background:#f4f1ea}}
 .skin-poster #rebuild{{background:#2a78d6;border:2px solid #f4f1ea;border-radius:0;font-weight:800}}
 .skin-poster iframe{{background:#f4f1ea}}
 /* 演示预览:页面保持原色(降饱和会毁掉配色,而配色正是要展示的东西),
    只靠角标提醒「这是样板间,不是你家」 */
 #wrap{{position:relative}}
 #pvtag{{display:none;position:absolute;right:18px;top:16px;z-index:20;background:#111;color:#f4f1ea;
   border:3px solid #eda100;padding:10px 14px;font-weight:900;font-size:13px;line-height:1.5;
   box-shadow:5px 5px 0 rgba(0,0,0,.35);max-width:260px}}
 #wrap.preview #pvtag{{display:block}}
 #pvtag b{{color:#eda100}}
 #pvtag button{{margin-top:8px;width:100%;border:2px solid #eda100;background:#eda100;color:#111;
   font-weight:900;padding:6px;cursor:pointer;font-family:inherit;font-size:12.5px}}
 {OB_CSS}
 /* 手机:角标压在右上角会挡住卡片 → 改为贴底通栏,不遮内容 */
 @media(max-width:720px){{
   nav{{gap:6px;padding:6px 10px;overflow-x:auto;flex-wrap:nowrap;-webkit-overflow-scrolling:touch}}
   .tab{{font-size:13px;padding:7px 10px;white-space:nowrap;flex:none}}
   .right{{position:sticky;right:0;background:inherit}}
   #pvtag{{position:fixed;left:8px;right:8px;top:auto;bottom:8px;max-width:none;
     font-size:12.5px}}
   #obbar{{overflow-x:auto;flex-wrap:nowrap;-webkit-overflow-scrolling:touch}}
   .ob-step{{white-space:nowrap;flex:none}}
   .ob-skip{{position:sticky;right:0;background:#eda100;padding-left:8px}}
 }}
</style></head><body class="{skin_cls}">
<nav>
 <button class="tab active" data-tab="view">📊 持仓全景{'（预览样板）' if preview else ''}</button>
 <button class="tab" data-tab="hold">📦 持仓管理</button>
 <button class="tab" data-tab="edit">💰 现金流编辑</button>
 <button class="tab" data-tab="goal">🎯 目标与大事</button>
 <button class="tab" data-tab="subs">📆 订阅管理</button>
 <button class="tab" data-tab="recon">🧾 月度对账</button>
 <button class="tab" data-tab="ins">🛡️ 保险</button>
 <button class="tab" data-tab="loans">💳 负债</button>
 <div class="right">
  {sel_html}
  <button id="rebuild" title="拉最新行情重算并刷新全景">↻ 重新估值</button>
 </div>
</nav>
{ob_bar}
<div id="wrap" class="{'preview' if preview else ''}">
  <div id="pvtag">🎭 <b>预览样板间</b><br>这是虚构人物「张小满」一家的数据,<b>不是你的</b>。<br>
    可以随便点、下钻、看每张卡。<button id="pvgo">开始填我的数据 →</button></div>
  <iframe id="frame" src="{'/demo-panorama' if preview else '/panorama?theme=' + default}"></iframe>
</div>
<script>
 const frame=document.getElementById('frame'), themeSel=document.getElementById('theme'), DEF='{default}';
 const WRAP=document.getElementById('wrap'), PREVIEW={'true' if preview else 'false'};
 let tab='{first_tab}';
 function theme(){{ return themeSel ? themeSel.value : DEF; }}
 function srcFor(){{ return tab==='view' ? (PREVIEW ? '/demo-panorama?_='+Date.now()
                                                    : '/panorama?theme='+theme()+'&_='+Date.now())
                                          : tab==='subs' ? '/subs?_='+Date.now()
                                          : tab==='recon' ? '/recon?_='+Date.now()
                                          : tab==='ins' ? '/insurance?_='+Date.now()
                                          : tab==='loans' ? '/loans?_='+Date.now()
                                          : tab==='goal' ? '/goal?_='+Date.now()
                                          : tab==='hold' ? '/holdings?_='+Date.now()
                                          : '/edit?_='+Date.now(); }}
 function show(t){{ tab=t;
   document.querySelectorAll('.tab').forEach(b=>b.classList.toggle('active',b.dataset.tab===t));
   WRAP.classList.toggle('preview', PREVIEW && t==='view');   // 角标只在预览全景时出现
   frame.src=srcFor(); }}
 document.querySelectorAll('.tab').forEach(b=>b.onclick=()=>show(b.dataset.tab));
 document.querySelectorAll('.ob-step').forEach(b=>b.onclick=()=>show(b.dataset.tab));
 const pvgo=document.getElementById('pvgo');
 if(pvgo) pvgo.onclick=()=>show('hold');     // 「开始填我的数据」→ 第①步(账户)
 show(tab);
 // 主题即全面板皮肤:写 cookie 让所有管理页/壳页跟随(暂只有海报有皮肤)
 if(themeSel) themeSel.onchange=()=>{{
   const poster = themeSel.value==='poster';
   document.cookie='skin='+(poster?'poster':'')+';path=/;max-age=31536000';
   document.body.classList.toggle('skin-poster',poster);
   frame.src=srcFor();
 }};
 // 编辑器保存并重建后，自动切到刚更新的持仓全景
 window.addEventListener('message',e=>{{ if(e.data==='rebuilt') show('view'); }});
 document.getElementById('rebuild').onclick=async()=>{{
   const btn=document.getElementById('rebuild'), old=btn.textContent;
   btn.textContent='重新估值中…'; btn.disabled=true;
   try{{ await fetch('/rebuild',{{method:'POST'}}); }}catch(e){{}}
   btn.textContent=old; btn.disabled=false; frame.src=srcFor();
 }};
</script>{SUBMIT_GUARD}</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _skin(self):
        return "poster" if "skin=poster" in (self.headers.get("Cookie") or "") else ""

    def _send(self, body, code=200):
        # 管理页跟随全景皮肤:cookie skin=poster 时注入海报覆盖样式(壳页自带皮肤逻辑,跳过)
        if (isinstance(body, str) and self._skin() == "poster"
                and "</head>" in body and 'id="frame"' not in body):
            body = body.replace("</head>", POSTER_PAGE_CSS + "</head>", 1)
        b = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _send_file(self, fp, content_type):
        data = fp.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        route = urlparse(self.path)
        path, q = route.path, parse_qs(route.query)
        if path in ("/", "/index.html"):
            skin = self._skin()
            skipped = "ob_skip=1" in (self.headers.get("Cookie") or "")
            self._send(shell(default="poster" if skin == "poster" else "origin", skin=skin,
                             ob=onboard_state(), ob_skipped=skipped))
        elif path == "/skip-onboard":
            self.send_response(302)
            self.send_header("Set-Cookie", "ob_skip=1;path=/;max-age=31536000")
            self.send_header("Location", "/")
            self.end_headers()
        elif path == "/demo-panorama":
            # 引导期的样板间:渲染演示数据的海报页(缺就现渲一次),整页可点可下钻
            fp = BASE / "demo" / "panorama_poster.html"
            if not fp.exists():
                import os
                subprocess.run([sys.executable, str(BASE / "rebuild_views.py")], cwd=BASE,
                               env={**os.environ, "PANORAMA_DEMO": "1"},
                               capture_output=True, timeout=180)
            self._send(fp.read_bytes() if fp.exists()
                       else b'<body style="font-family:sans-serif;padding:40px">'
                            b'\xe6\xbc\x94\xe7\xa4\xba\xe9\xa1\xb5\xe7\x94\x9f\xe6\x88\x90\xe4\xb8\xad\xe2\x80\xa6</body>')
        elif path == "/goal":
            self._send(goal_page())
        elif path == "/edit":
            self._send(page(load_cf()))
        elif path == "/subs":
            self._send(subs_page(subs_mod.load_subs()))
        elif path == "/recon":
            month = q.get("month", [None])[0]
            self._send(recon_page(month=month))
        elif path == "/insurance":
            self._send(insurance_page(ins_mod.load_policies()))
        elif path == "/loans":
            self._send(loans_page(loans_mod.enrich()))
        elif path == "/holdings":
            self._send(holdings_page())
        elif path == "/panorama":
            key = q.get("theme", ["origin"])[0]
            fname = THEMES.get(key, THEMES["origin"])[1]
            fp = BASE / fname
            if fp.exists():
                self._send(fp.read_bytes())
            else:
                self._send(f'<body style="font-family:sans-serif;padding:40px;color:#666">'
                           f'尚未生成 {fname}，点右上角「↻ 重新估值」即可生成。</body>')
        elif path.startswith("/icons_cache/"):
            fp = BASE / path.lstrip("/")
            if fp.is_file() and fp.resolve().is_relative_to(subs_mod.ICONS_DIR.resolve()):
                ctype = "image/png" if fp.suffix == ".png" else "image/x-icon"
                self._send_file(fp, ctype)
            else:
                self._send("not found", 404)
        else:
            self._send("not found", 404)

    def do_POST(self):
        route = urlparse(self.path).path
        if route == "/sketch-assets":
            # 资产板块「换成我的」:五大类约数 → 速写账户,替换样板(引导 v2)
            import sketch
            length = int(self.headers.get("Content-Length", 0))
            try:
                vals = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            except json.JSONDecodeError:
                vals = {}
            sketch.apply_asset_sketch(vals)
            self._rebuild_views()
            self._send(b'{"ok":true}')
            return
        if route == "/save-goal":
            length = int(self.headers.get("Content-Length", 0))
            fields = parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
            storage.save_doc("goal", parse_goal(fields))
            msg = "✅ 已保存目标与重大事件"
            rebuilt = fields.get("act", [""])[0] == "save_rebuild"
            if rebuilt:
                ok = self._rebuild_views()
                msg += " · 已刷新全景，正在跳转…" if ok else " · ⚠️ 刷新失败，仅保存成功"
                rebuilt = ok
            self._send(goal_page(msg, rebuilt=rebuilt, reward=onboard_reward("goal")))
        elif route == "/save":
            length = int(self.headers.get("Content-Length", 0))
            fields = parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
            cf = parse_save(fields)
            storage.save_doc("cashflow", cf)
            msg = "✅ 已保存现金流配置（上一版已自动备份）"
            rebuilt = fields.get("action", [""])[0] == "save_rebuild"
            if rebuilt:
                ok = self._rebuild_views()
                msg += " · 已刷新全景(行情走当日缓存)，正在跳转…" if ok else " · ⚠️ 刷新失败(行情/网络)，仅保存成功"
                rebuilt = ok
            self._send(page(cf, msg, rebuilt=rebuilt, reward=onboard_reward("flow")))
        elif route == "/subs/save":
            length = int(self.headers.get("Content-Length", 0))
            fields = parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
            subs_list = parse_subs_save(fields)
            subs_mod.save_subs(subs_list)
            synced = subs_mod.sync_icons(subs_list)
            msg = "✅ 已保存订阅台账（上一版已自动备份）"
            if synced:
                msg += f" · 已拉取 {synced} 个新图标"
            rebuilt = fields.get("action", [""])[0] == "save_rebuild"
            if rebuilt:
                ok = self._rebuild_views()
                msg += " · 已刷新全景(行情走当日缓存)，正在跳转…" if ok else " · ⚠️ 刷新失败，仅保存成功"
                rebuilt = ok
            self._send(subs_page(subs_mod.load_subs(), msg, rebuilt=rebuilt))
        elif route == "/recon/save":
            length = int(self.headers.get("Content-Length", 0))
            fields = parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
            row = parse_recon_save(fields)
            month = row["月份"]
            msg = f'✅ 已确认 {month} 对账记录（已对账锁定，上一版已自动备份）'
            rebuilt = fields.get("action", [""])[0] == "save_rebuild"
            if rebuilt:
                ok = self._rebuild_views()
                msg += " · 已刷新全景(行情走当日缓存)，正在跳转…" if ok else " · ⚠️ 刷新失败，仅保存成功"
                rebuilt = ok
            self._send(recon_page(msg, rebuilt=rebuilt, month=month))
        elif route == "/loans/save":
            length = int(self.headers.get("Content-Length", 0))
            fields = parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
            loans_mod.save_loans(parse_loans_save(fields))
            msg = "✅ 已保存负债台账（上一版已自动备份）"
            rebuilt = fields.get("action", [""])[0] == "save_rebuild"
            if rebuilt:
                ok = self._rebuild_views()
                msg += " · 已刷新全景(行情走当日缓存)，正在跳转…" if ok else " · ⚠️ 刷新失败，仅保存成功"
                rebuilt = ok
            self._send(loans_page(loans_mod.enrich(), msg, rebuilt=rebuilt))
        elif route == "/insurance/save":
            length = int(self.headers.get("Content-Length", 0))
            fields = parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
            policies = parse_insurance_save(fields)
            ins_mod.save_policies(policies)
            msg = "✅ 已保存保险台账（上一版已自动备份）"
            rebuilt = fields.get("action", [""])[0] == "save_rebuild"
            if rebuilt:
                ok = self._rebuild_views()
                msg += " · 已刷新全景(行情走当日缓存)，正在跳转…" if ok else " · ⚠️ 刷新失败，仅保存成功"
                rebuilt = ok
            self._send(insurance_page(ins_mod.load_policies(), msg, rebuilt=rebuilt))
        elif route == "/holdings/save":
            length = int(self.headers.get("Content-Length", 0))
            fields = parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
            _st_before = onboard_state()      # 保存前的状态:决定这次算完成了哪一步
            rows, prices, manual_updates, acct_new, note = parse_holdings_save(fields)
            records, warns = hold_mod.save_holdings(rows, prices, note)
            changed = hold_mod.save_manual(manual_updates)
            added = hold_mod.add_account(*acct_new) if acct_new[0].strip() else False
            msg = "✅ 已保存持仓管理（上一版已自动备份）"
            # 这一页同时承载第①步(账户)与第③步(持仓):按**用户这次提交了什么**判定,
            # 而不是按状态——否则「已有账户后再加一个账户」会被误报成第③步
            _step = "hold" if rows else "acct"
            reward = "" if _st_before["all_done"] else onboard_reward(_step)
            if records:
                brief = "、".join(f'{r["动作"]} {r["名称"]} {r["数量"]}@{r["成交价"] or "?"}'
                                  for r in records[:4])
                msg += f' · 自动记台账 {len(records)} 笔：{brief}{"…" if len(records) > 4 else ""}'
            if changed:
                msg += f' · 手动值更新：{"、".join(changed)}'
            if added:
                msg += f' · 新增账户：{acct_new[0].strip()}'
            if warns:
                msg += " · ⚠️ " + "；".join(warns)
            rebuilt = fields.get("action", [""])[0] == "save_rebuild"
            if rebuilt:
                ok = self._rebuild_views()
                msg += " · 已刷新全景(行情走当日缓存)，正在跳转…" if ok else " · ⚠️ 刷新失败，仅保存成功"
                rebuilt = ok
            self._send(holdings_page(msg, rebuilt=rebuilt, reward=reward))
        elif route == "/recon/import":
            length = int(self.headers.get("Content-Length", 0))
            try:
                req = json.loads(self.rfile.read(length).decode("utf-8"))
                raw = base64.b64decode(req.get("b64", ""))
                summary = bill_import.summarize(
                    raw, req.get("month", ""), req.get("excludes"))
                body = json.dumps(summary, ensure_ascii=False)
            except Exception as e:
                body = json.dumps({"expense": 0, "count": 0, "excluded": 0,
                                   "excludedCount": 0, "otherMonths": 0, "top": [],
                                   "source": "?", "warnings": [f"解析失败: {e}"]},
                                  ensure_ascii=False)
            b = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
        elif route == "/rebuild":
            ok = self._run_daily()
            self._send("ok" if ok else "fail")
        else:
            self._send("not found", 404)

    def _rebuild_views(self):
        # 轻量重渲染:模块保存专用——只重算+重画全景(行情走当日缓存),
        # 不写历史/不发通知/不导备份;与完整重估共用一把锁,不并发打架
        with _REBUILD_LOCK:
            try:
                subprocess.run(["python3", str(BASE / "rebuild_views.py")],
                               cwd=str(BASE), timeout=90)
                return True
            except Exception:
                return False

    def _run_daily(self):
        # 完整重估(拉行情+写历史+通知+导出),仅右上「↻ 重新估值」走这里;串行化防双写竞态
        with _REBUILD_LOCK:
            try:
                subprocess.run(["bash", str(BASE / "run_daily.sh")], cwd=str(BASE), timeout=180)
                return True
            except Exception:
                return False

    def log_message(self, *a):
        pass  # 静音访问日志


def main():
    try:
        # 多线程：重新估值(最长3分钟)期间其他页面仍可响应，不再整站卡死
        srv = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as e:
        print(f"⚠️ 端口 {PORT} 无法绑定（可能已在运行）：{e}", flush=True)
        return
    url = f"http://{HOST}:{PORT}"
    print(f"资产面板已启动 → {url}\n"
          f"  在浏览器打开上面地址；顶部标签切换「持仓全景 / 现金流 / 订阅 / 对账」，右上「↻ 重新估值」拉最新行情。\n"
          f"  服务需保持运行，关掉本终端/按 Ctrl+C 即停止。", flush=True)
    threading.Timer(0.6, lambda: _try_open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出。")
        srv.shutdown()


def _try_open(url):
    try:
        webbrowser.open(url)
    except Exception:
        pass  # 无 GUI 浏览器时忽略，手动访问即可


if __name__ == "__main__":
    main()
