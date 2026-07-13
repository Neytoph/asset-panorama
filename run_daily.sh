#!/bin/bash
# 每日运行：估值 + 更新历史 + 重建全景 + 到期提醒通知。日志写入 run.log（自动轮转）。
cd "$(dirname "$0")" || exit 1
PY="$(command -v python3)"
{
  echo "===== $(date '+%Y-%m-%d %H:%M:%S') ====="
  "$PY" portfolio_tracker.py   # 估值 + 历史 + 告警(进日志)
  "$PY" panorama_themes.py     # 经典配色全景 → panorama_origin.html
  "$PY" panorama_variants.py   # 交易终端/色块海报全景 → panorama_terminal.html / panorama_poster.html
  "$PY" ips_page.py            # 投资政策声明 + 操作合规审计 → ips.html
  "$PY" notify_alerts.py       # 订阅/保险到期 + 管家待办 → macOS 系统通知

  # 年度报告:一月自动补生成上一年(幂等,已存在则跳过)
  LY=$(( $(date +%Y) - 1 ))
  if [ "$(date +%m)" = "01" ] && [ ! -f "report_${LY}.html" ]; then
    "$PY" annual_report.py "$LY" && echo "📕 已生成 ${LY} 年度报告"
  fi
  "$PY" storage.py export      # 数据库 → JSON/CSV 每日导出备份（.bak=上一日）

  # 数据库异地备份:iCloud 按日期一份,保留最近 30 份
  BK="$HOME/Library/Mobile Documents/com~apple~CloudDocs/asset-panorama-backup"
  mkdir -p "$BK" && cp panorama.db "$BK/panorama-$(date +%Y%m%d).db" \
    && echo "☁️ db 已备份到 iCloud ($(ls "$BK" | wc -l | tr -d ' ') 份)"
  ls -t "$BK"/panorama-*.db 2>/dev/null | tail -n +31 | while read -r f; do rm -f "$f"; done

  # 代码保底快照:有未提交改动则自动提交(post-commit hook 会顺带推送到私有仓库)
  if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
    git add -A && git commit -q -m "auto: $(date '+%Y-%m-%d') 代码快照" \
      && echo "📦 未提交改动已自动快照提交"
  fi
} >> run.log 2>&1
# 日志轮转：只保留最近 2000 行
if [ "$(wc -l < run.log)" -gt 2000 ]; then
  tail -n 2000 run.log > run.log.tmp && mv run.log.tmp run.log
fi
