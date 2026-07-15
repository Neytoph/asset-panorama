#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
建数据脚本：python3 start.py

从演示配置起步(demo/ 就是最好的填写参照)，逐个文件生成成你自己的账本。
台账类(负债/保险/订阅/持仓)给空模板，不带别人的余额进来。
不覆盖已有数据：任何已存在的文件都会跳过并提示。
"""
import json
import shutil
from pathlib import Path

BASE = Path(__file__).resolve().parent
DEMO = BASE / "demo"

# (文件, 是否必需, 来源, 一句话说明)
#   来源 "demo" = 拷贝演示数据当填写参照(你会照着改,数字是别人的没关系)
#   来源 "empty" = 给**空模板**。台账类(负债/保险/订阅/持仓)绝不能带别人的余额进来——
#   否则「只填了资产、还没填负债」的中间状态会算出别人的房贷 → 负净资产(旧 *.example 的老毛病)
FILES = [
    ("holdings.csv", True, "empty", "可实时报价的证券(股票/ETF)——没有就留着空表头"),
    ("accounts.csv", True, "demo", "没有行情的账户:理财/存款/保险/房产/日常现金"),
    ("manual_values.json", True, "demo", "上面这些账户的最新金额(房产建议标 kind=anchor 季度重估)"),
    ("cashflow.json", True, "demo", "收入与月度支出。⚠ 日常生活开销一定要记进来,否则储蓄率虚高"),
    ("passthrough.json", False, "demo", "投顾组合的穿透权重(没有投顾组合可跳过)"),
    ("goal.json", False, "demo", "目标态与重大事件(换房/还清贷款/子女教育…)——面板的导航中心"),
    ("subscriptions.json", False, "empty", "订阅台账"),
    ("insurance.json", False, "empty", "保单台账"),
    ("loans.json", False, "empty", "贷款台账(余额自动按月推演)"),
    ("insurance_cashvalue.csv", False, "empty", "增额寿现金价值表(有储蓄险才需要)"),
]

# 空模板:结构齐全但没有任何余额/条目 —— 参照请看 demo/ 里的同名文件
EMPTY = {
    "holdings.csv": ("名称,代码,市场,资产类型,账户,持有数量,新浪查询代码,腾讯查询代码,"
                     "东财secid,流动性,股息率\n"),
    "insurance_cashvalue.csv": "保单年度,现金价值,账户\n",
    "subscriptions.json": {"_note": "订阅台账。参照 demo/subscriptions.json", "订阅": []},
    "insurance.json": {"_note": "保单台账。参照 demo/insurance.json", "保单": [],
                       "_note2": "储蓄型起保日:账户名 → 起保日(有增额寿/终身寿才填)",
                       "储蓄型起保日": {}},
    "loans.json": {"_note": "负债台账。余额由 基准本金+利率+月供 按月推演。"
                            "参照 demo/loans.json", "负债": []},
}


def init_mine():
    print("\n📋 必填项从演示配置起步(照着改);台账类给空模板——")
    print("   不能把别人的房贷/保单带进你的账本,否则「填了资产还没填负债」会算出负净资产。\n")
    created, skipped = [], []
    for name, required, source, desc in FILES:
        dst = BASE / name
        if dst.exists():
            skipped.append(name); continue
        if source == "empty" and name in EMPTY:
            body = EMPTY[name]
            dst.write_text(body if isinstance(body, str)
                           else json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            src = DEMO / name
            if not src.exists():
                continue
            shutil.copy(src, dst)
        created.append((name, required, source, desc))
    for name, required, source, desc in created:
        tag = "[必填]" if required else "[可选]"
        origin = "参照演示" if source == "demo" else "空模板"
        print(f"  ✅ {name:26} {tag} {origin:5} {desc}")
    if skipped:
        print(f"\n  ⏭️  已存在,未覆盖:{', '.join(skipped)}")
    if not created:
        print("  (所有配置都已存在,没有新建任何文件)")

    print("\n" + "─" * 66)
    print("下一步——按这个顺序改,每改完一个就能跑一次看效果:\n")
    print("  1. accounts.csv + manual_values.json  → 先把「你有多少钱」填对")
    print("     这一步做完就能看到净资产和大类配置了。")
    print("  2. cashflow.json                      → 收入 + 支出")
    print("     ⚠ 最容易犯的错:只填房贷房租,不填吃饭购物 → 储蓄率虚高一倍")
    print("  3. holdings.csv                       → 有股票/ETF 才需要")
    print("  4. goal.json                          → 你未来几年的大事(换房/还贷/教育)")
    print("     没有这个,面板只会告诉你「现在怎样」,不会告诉你「要去哪」。")
    print("  5. 其余(订阅/保险/负债)按需填,不填不影响主流程。")
    print("\n改完跑:")
    print("  python3 portfolio_tracker.py    # 终端看一眼数字对不对")
    print("  python3 rebuild_views.py        # 生成全景面板")
    print("  python3 cashflow_editor.py      # 或者直接开面板,在网页里改 → :8765")
    print("─" * 66)


def main():
    print("=" * 66)
    print("  资产全景 · 建数据")
    print("=" * 66)
    if (BASE / "accounts.csv").exists() or (BASE / "panorama.db").exists():
        print("\n检测到已有数据(已存在的文件不会被覆盖)。")
    init_mine()


if __name__ == "__main__":
    main()
