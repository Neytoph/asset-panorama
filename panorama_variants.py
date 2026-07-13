#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全景视图生成器（数据驱动模板，零依赖）：
templates/*.html 里的占位符注入 panorama_data.collect() 的完整 JSON，
产出自包含的 panorama_<theme>.html。四套模板均为全模块（对标经典配色）。
复用 panorama_data.collect()。用法：python3 panorama_variants.py
"""
import json
from pathlib import Path
from panorama_data import collect

BASE = Path(__file__).resolve().parent
TEMPLATES = BASE / "templates"
PLACEHOLDER = "/*__PD__*/{}/*__PD_END__*/"
ECHARTS_CDN_TAG = '<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>'
ECHARTS_VENDOR = BASE / "vendor" / "echarts.min.js"

# (模板文件, 产出文件, 展示名)
VARIANTS = [
    ("terminal.html", "panorama_terminal.html", "交易终端"),
    ("poster.html", "panorama_poster.html", "色块海报"),
]


def render(tpl_name, out_name, label, pd_json):
    tpl_path = TEMPLATES / tpl_name
    if not tpl_path.exists():
        print(f"⏭️  {label}：模板 {tpl_name} 不存在，跳过")
        return
    tpl = tpl_path.read_text(encoding="utf-8")
    if PLACEHOLDER not in tpl:
        raise RuntimeError(f"{tpl_name} 缺少占位符 {PLACEHOLDER}")
    html = tpl.replace(PLACEHOLDER, pd_json, 1)
    # 内联 vendor 里的 echarts,产物离线可用;vendor 缺失时保留 CDN 标签兜底
    if ECHARTS_CDN_TAG in html and ECHARTS_VENDOR.exists():
        js = ECHARTS_VENDOR.read_text(encoding="utf-8").replace("</script", "<\\/script")
        html = html.replace(ECHARTS_CDN_TAG, "<script>\n" + js + "\n</script>", 1)
    out = BASE / out_name
    out.write_text(html, encoding="utf-8")
    print(f"✅ {label} → {out.name}")


if __name__ == "__main__":
    D = collect(persist_history=False)   # 只取一次数据；历史由 portfolio_tracker 统一记录
    payload = json.dumps(D, ensure_ascii=False)
    for tpl_name, out_name, label in VARIANTS:
        render(tpl_name, out_name, label, payload)
