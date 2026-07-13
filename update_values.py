#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
半自动账户更新器
================
长钱账户/海外长钱/招行理财等没有公开 API 的账户，用这个脚本快速更新最新金额。
从 App / 银行查到净值后，跑一遍、按提示输入即可；回车则保留原值。
更新经 storage 写入 manual_values 数据集，portfolio_tracker.py 自动采用最新值。

用法：python3 update_values.py
"""
import datetime

import storage


def main():
    data = storage.load_doc("manual_values", {})
    today = datetime.date.today().isoformat()
    print("更新账户最新金额（直接回车=保留原值，输入数字=更新）\n")
    for name, info in data.items():
        cur = info.get("value", 0)
        upd = info.get("updated", "?")
        note = info.get("note", "")
        raw = input(f"  {name}（当前 ¥{cur:,.2f}，更新于 {upd}）{('- '+note) if note else ''}\n    新金额 = ").strip()
        if raw:
            try:
                info["value"] = float(raw.replace(",", ""))
                info["updated"] = today
                print(f"    ✅ 已更新为 ¥{info['value']:,.2f}")
            except ValueError:
                print("    ⚠️ 非数字，跳过")
    storage.save_doc("manual_values", data)
    print("\n已保存 manual_values（上一版已自动备份）。运行 python3 portfolio_tracker.py 查看最新估值。")


if __name__ == "__main__":
    main()
