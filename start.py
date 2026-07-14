#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
首次上手向导：python3 start.py

两条路，选一条：
  1) 先看效果 —— 用演示数据(虚构人物「张小满」一家)跑一遍，1 分钟看到成品；
  2) 录入自己的数据 —— 从演示配置起步(它就是最好的填写参照)，逐个文件改成你的。

不覆盖已有数据：任何已存在的文件都会跳过并提示。
"""
import shutil
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
DEMO = BASE / "demo"

# (文件, 是否必需, 一句话说明)
FILES = [
    ("holdings.csv", True, "可实时报价的证券(股票/ETF)——没有就留空表头"),
    ("accounts.csv", True, "没有行情的账户:理财/存款/保险/房产/日常现金"),
    ("manual_values.json", True, "上面这些账户的最新金额(房产建议标 kind=anchor 季度重估)"),
    ("cashflow.json", True, "收入与月度支出。⚠ 日常生活开销一定要记进来,否则储蓄率虚高"),
    ("passthrough.json", False, "投顾组合的穿透权重(没有投顾组合可跳过)"),
    ("goal.json", False, "目标态与重大事件(换房/还清贷款/子女教育…)——面板的导航中心"),
    ("subscriptions.json", False, "订阅台账"),
    ("insurance.json", False, "保单台账"),
    ("loans.json", False, "贷款台账(余额自动按月推演)"),
    ("insurance_cashvalue.csv", False, "增额寿现金价值表(有储蓄险才需要)"),
]


def _run(cmd, env=None):
    return subprocess.call([sys.executable] + cmd, cwd=BASE, env=env)


def try_demo():
    import os
    print("\n🎭 用演示数据跑一遍(虚构人物「张小满」一家,不碰你的任何文件)…\n")
    env = {**os.environ, "PANORAMA_DEMO": "1"}
    if _run(["rebuild_views.py"], env) != 0:
        print("❌ 渲染失败"); return
    out = DEMO / "panorama_poster.html"
    print(f"\n✅ 完成。打开看看:\n   open {out}\n")
    print("   demo/panorama_origin.html    经典配色")
    print("   demo/panorama_terminal.html  交易终端(暗色)")
    print("   demo/ips.html                投资政策声明")
    print("\n看完想填自己的数据 → 再跑一次 python3 start.py 选 2。")


def init_mine():
    print("\n📋 从演示配置起步——它就是最好的填写参照(字段该填什么样,照着改)。\n")
    created, skipped = [], []
    for name, required, desc in FILES:
        dst, src = BASE / name, DEMO / name
        if dst.exists():
            skipped.append(name); continue
        if not src.exists():
            continue
        shutil.copy(src, dst)
        created.append((name, required, desc))
    for name, required, desc in created:
        print(f"  ✅ {name:26} {'[必填]' if required else '[可选]'} {desc}")
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
    print("  资产全景 · 首次上手")
    print("=" * 66)
    has_own = (BASE / "accounts.csv").exists() or (BASE / "panorama.db").exists()
    if has_own:
        print("\n检测到已有数据。")
    print("""
  1) 先看效果      用演示数据跑一遍(虚构人物,不碰你的文件)
  2) 录入我的数据  从演示配置起步,改成你自己的
  3) 退出
""")
    try:
        c = input("选择 [1/2/3]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print(); return
    if c == "1":
        try_demo()
    elif c == "2":
        init_mine()
    else:
        print("bye")


if __name__ == "__main__":
    main()
