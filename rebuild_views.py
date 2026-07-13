#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
轻量重渲染:一次 collect()(不写历史、不发通知、不导出备份)→ 渲染全部主题产物。
编辑器各 Tab 的「保存并刷新全景」走这里(数秒,行情走当日缓存/快速批量接口);
完整重估(拉行情+写历史+到期通知+导出备份)仍由 run_daily.sh / 面板「↻ 重新估值」承担。
"""
import json

import ips_page
import panorama_themes
import panorama_variants
from panorama_data import collect


def main():
    D = collect(persist_history=False, fetch_klines=False)
    panorama_themes.render("origin", D)
    payload = json.dumps(D, ensure_ascii=False)
    for tpl, out, label in panorama_variants.VARIANTS:
        panorama_variants.render(tpl, out, label, payload)
    ips_page.build()   # IPS 纪律页(零网络,顺带重跑操作合规审计)


if __name__ == "__main__":
    main()
