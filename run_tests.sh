#!/bin/bash
# 串跑全部自测：各模块 __main__ 断言 + tests.py 跨模块不变量。改动后跑一遍防回归。
cd "$(dirname "$0")" || exit 1
PY="$(command -v python3)"
set -e
echo "── 模块自测 ──"
for m in subscriptions insurance bill_import loans; do
  echo "· $m"
  "$PY" "$m.py"
done
echo "── 跨模块不变量 ──"
"$PY" tests.py
echo "── 语法检查（其余模块）──"
"$PY" -m py_compile portfolio_tracker.py panorama_data.py panorama_themes.py \
  panorama_variants.py cashflow_editor.py cashflow_income.py cashflow_history.py \
  payroll_tax.py notify_alerts.py update_values.py metrics.py \
  annual_report.py ips_page.py rebuild_views.py
echo "✅ 全部通过"
