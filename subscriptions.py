#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""订阅台账：月度归一、日历、续费提醒；与现金流 fixed_out 打通。"""
import json
import re
import urllib.request
from calendar import monthrange
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import storage

BASE = Path(__file__).resolve().parent
ICONS_DIR = BASE / "icons_cache"
ICON_META = ICONS_DIR / "_meta.json"
ICON_TTL_DAYS = 30
HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (asset-panorama)"}

# 名称关键词 → 域名（无「域名」字段时的兜底）
KNOWN_DOMAINS = {
    "netflix": "netflix.com",
    "icloud": "apple.com",
    "apple music": "music.apple.com",
    "spotify": "spotify.com",
    "youtube": "youtube.com",
    "disney": "disneyplus.com",
    "hbo": "max.com",
    "amazon prime": "amazon.com",
    "notion": "notion.so",
    "github": "github.com",
    "chatgpt": "openai.com",
    "openai": "openai.com",
    "claude code": "claude.ai",
    "claude": "claude.ai",
    "anthropic": "anthropic.com",
    "cursor": "cursor.com",
    "figma": "figma.com",
    "adobe": "adobe.com",
    "microsoft": "microsoft.com",
    "office": "microsoft.com",
    "dropbox": "dropbox.com",
    "google one": "google.com",
    "jetbrains": "jetbrains.com",
}

CAT_STYLE = {
    "流媒体": ("🎬", "#e11d48"),
    "工具": ("🛠", "#2563eb"),
    "生活": ("🏠", "#16a34a"),
    "会员": ("💳", "#a855f7"),
    "其他": ("📦", "#64748b"),
}
PER_TO_MONTH = {"月": 1.0, "季": 1 / 3, "年": 1 / 12, "周": 52 / 12}


def emoji_for(sub):
    return sub.get("图标") or CAT_STYLE.get(sub.get("分类", "其他"), ("📦",))[0]


def normalize_domain(raw):
    s = (raw or "").strip().lower()
    if not s:
        return ""
    s = re.sub(r"^https?://", "", s)
    s = s.split("/")[0].split("?")[0]
    return s.removeprefix("www.")


def guess_domain(sub):
    d = normalize_domain(sub.get("域名"))
    if d:
        return d
    name = (sub.get("名称") or "").lower()
    for key, domain in KNOWN_DOMAINS.items():
        if key in name:
            return domain
    return ""


def _icon_file(domain):
    safe = re.sub(r"[^a-z0-9._-]", "_", domain)
    return ICONS_DIR / f"{safe}.ico"


def icon_path_for(sub):
    """已缓存图标的相对路径（供 HTML img src），无则 None。"""
    domain = guess_domain(sub)
    if not domain:
        return None
    fp = _icon_file(domain)
    return f"icons_cache/{fp.name}" if fp.is_file() and fp.stat().st_size > 80 else None


def _load_icon_meta():
    if not ICON_META.exists():
        return {}
    try:
        return json.loads(ICON_META.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_icon_meta(meta):
    ICONS_DIR.mkdir(parents=True, exist_ok=True)
    ICON_META.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def _http_get_bytes(url, timeout=12):
    req = urllib.request.Request(url, headers=HTTP_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _fetch_favicon(domain):
    """DuckDuckGo → Google favicon，返回 bytes 或 None。"""
    for url in (
        f"https://icons.duckduckgo.com/ip3/{domain}.ico",
        f"https://www.google.com/s2/favicons?domain={domain}&sz=64",
    ):
        try:
            data = _http_get_bytes(url)
            if data and len(data) > 80:
                return data
        except Exception:
            pass
    return None


def ensure_icon(sub, force=False):
    """拉取并缓存 favicon；成功返回相对路径，失败 None。"""
    domain = guess_domain(sub)
    if not domain:
        return None
    fp = _icon_file(domain)
    meta = _load_icon_meta()
    rec = meta.get(domain, {})
    if not force and fp.is_file():
        try:
            age = (date.today() - date.fromisoformat(rec.get("date", "2000-01-01"))).days
        except ValueError:
            age = ICON_TTL_DAYS + 1
        if age < ICON_TTL_DAYS:
            return f"icons_cache/{fp.name}"
    data = _fetch_favicon(domain)
    if not data:
        return f"icons_cache/{fp.name}" if fp.is_file() else None
    ICONS_DIR.mkdir(parents=True, exist_ok=True)
    fp.write_bytes(data)
    meta[domain] = {"date": date.today().isoformat(), "name": sub.get("名称", "")}
    _save_icon_meta(meta)
    return f"icons_cache/{fp.name}"


def sync_icons(subs_list, force=False):
    """为全部订阅尝试缓存图标，返回新拉取成功数。"""
    n = 0
    for s in subs_list:
        domain = guess_domain(s)
        if not domain:
            continue
        had = _icon_file(domain).is_file()
        if ensure_icon(s, force=force) and (force or not had):
            n += 1
    return n


def decorate_sub(sub):
    """附加 iconPath + 规范 emoji。"""
    out = dict(sub)
    out["图标"] = emoji_for(sub)
    out["iconPath"] = icon_path_for(sub)
    return out


def load_subs():
    return storage.load_doc("subscriptions", {}).get("订阅", [])


def save_subs(subs_list, note=None):
    """经 storage 写订阅台账，自动备份上一版。"""
    data = {"_note": note or "订阅台账。金额按原币种记(正数)；月度归一后并入现金流固定支出。", "订阅": subs_list}
    storage.save_doc("subscriptions", data)


def add_months(d, n):
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, monthrange(y, m)[1]))


def add_period(d, 周期):
    return {"月": lambda: add_months(d, 1),
            "季": lambda: add_months(d, 3),
            "年": lambda: add_months(d, 12),
            "周": lambda: d + timedelta(days=7)}[周期]()


def next_charge(sub, today):
    d = date.fromisoformat(sub["下次扣费日"])
    while d < today:
        d = add_period(d, sub["周期"])
    return d


def monthly_cny(sub, fx):
    st = sub.get("状态", "启用")
    if st == "暂停":
        return 0.0
    if st == "试用":
        return 0.0
    if st != "启用":
        return 0.0
    return abs(float(sub["金额"])) * fx.get(sub.get("币种", "CNY"), 1.0) * PER_TO_MONTH[sub["周期"]]


def monthly_total(subs, fx):
    return sum(monthly_cny(s, fx) for s in subs)


def yearly_total(subs, fx):
    return monthly_total(subs, fx) * 12


def by_category(subs, fx):
    out = defaultdict(float)
    for s in subs:
        if s.get("状态", "启用") != "启用":
            continue
        out[s.get("分类", "其他")] += monthly_cny(s, fx)
    return dict(out)


def charge_cny(sub, fx):
    """单次扣费金额(CNY)。"""
    return abs(float(sub["金额"])) * fx.get(sub.get("币种", "CNY"), 1.0)


def upcoming(subs, today, fx, n=90):
    rows = []
    for s in subs:
        if s.get("状态", "启用") != "启用":
            continue
        nc = next_charge(s, today)
        delta = (nc - today).days
        if 0 <= delta <= n:
            ds = decorate_sub(s)
            rows.append((nc, s["名称"], round(charge_cny(s, fx)), ds["图标"], delta, ds.get("iconPath")))
    rows.sort(key=lambda x: x[0])
    return rows


def charges_between(subs, start, end):
    """[start, end] 闭区间内所有扣费日 → {ISO日期: [订阅,...]}。
    从锚点「下次扣费日」按周期枚举，**含已发生的扣费**（区别于 next_charge 只看未来），
    日历上昨天已扣的也能看到。"""
    out = defaultdict(list)
    for s in subs:
        if s.get("状态", "启用") != "启用":
            continue
        d = date.fromisoformat(s["下次扣费日"])
        while d < start:
            d = add_period(d, s["周期"])
        while d <= end:
            out[d.isoformat()].append(s)
            d = add_period(d, s["周期"])
    return dict(out)


def reminders(subs, today, fx=None, days=(7, 3, 1)):
    fx = fx or {"CNY": 1.0}
    out = []
    max_d = max(days) if days else 7
    for s in subs:
        if s.get("状态", "启用") != "启用":
            continue
        nc = next_charge(s, today)
        delta = (nc - today).days
        if delta < 0 or delta > max_d:
            continue
        if delta <= 1:
            emoji = "🔴"
        elif delta <= 3:
            emoji = "🟠"
        else:
            emoji = "🟡"
        cny = round(charge_cny(s, fx))
        out.append((emoji, f'{s["名称"]} 将于 {delta} 天后扣费 ¥{cny:,}'))
    return out


def cashflow_fixed_out(cf, fx):
    """月度固定支出(CNY) = 现金流负项支出 + 订阅月折算 + 保险保费摊月。
    全项目 fixed_out 的唯一聚合点（collect / 编辑器 compute 都走这里）。"""
    import insurance
    manual = -sum(i["金额"] for i in cf.get("月度收支", []) if i["金额"] < 0)
    return manual + monthly_total(load_subs(), fx) + insurance.monthly_total()


def enrich_items(subs, fx, today=None, fetch_icons=True):
    """供全景导出：含滚动后下次扣费日、月度 CNY、iconPath。"""
    if fetch_icons:
        sync_icons(subs)
    today = today or date.today()
    items = []
    for s in subs:
        nc = next_charge(s, today)
        ds = decorate_sub(s)
        items.append({
            **ds,
            "下次扣费日": nc.isoformat(),
            "monthlyCny": round(monthly_cny(s, fx)),
            "chargeCny": round(charge_cny(s, fx)),
        })
    return items


def _self_test():
    fx = {"CNY": 1.0, "USD": 7.0, "HKD": 0.9}
    assert abs(monthly_cny({"金额": 100, "币种": "CNY", "周期": "月", "状态": "启用"}, fx) - 100) < 0.01
    assert abs(monthly_cny({"金额": 1200, "币种": "CNY", "周期": "年", "状态": "启用"}, fx) - 100) < 0.01
    assert abs(monthly_cny({"金额": 300, "币种": "CNY", "周期": "季", "状态": "启用"}, fx) - 100) < 0.01
    assert abs(monthly_cny({"金额": 10, "币种": "USD", "周期": "月", "状态": "启用"}, fx) - 70) < 0.01
    assert monthly_cny({"金额": 10, "币种": "USD", "周期": "月", "状态": "暂停"}, fx) == 0

    sub = {"下次扣费日": "2020-01-15", "周期": "月"}
    today = date(2026, 7, 3)
    assert next_charge(sub, today) >= today

    d = date(2026, 1, 31)
    assert add_period(d, "月") == date(2026, 2, 28)
    assert add_period(date(2024, 1, 31), "月") == date(2024, 2, 29)

    subs = load_subs()
    assert isinstance(monthly_total(subs, fx), float)
    assert isinstance(by_category(subs, fx), dict)
    # charges_between：含已发生的扣费（月周期锚点 7/3，窗口 6/29~8/2 → 7/3 在列，8/3 不在）
    s_m = {"名称": "t", "下次扣费日": "2026-07-03", "周期": "月", "金额": 10}
    got = charges_between([s_m], date(2026, 6, 29), date(2026, 8, 2))
    assert "2026-07-03" in got and "2026-08-03" not in got
    # 周周期在窗口内枚举多次
    s_w = {"名称": "w", "下次扣费日": "2026-06-01", "周期": "周", "金额": 5}
    assert len(charges_between([s_w], date(2026, 7, 1), date(2026, 7, 14))) == 2
    assert normalize_domain("https://www.Netflix.com/path") == "netflix.com"
    assert guess_domain({"名称": "Netflix"}) == "netflix.com"
    print("subscriptions.py: 全部自测通过 ✓")


if __name__ == "__main__":
    _self_test()
