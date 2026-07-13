# 订阅管理功能 · 实施文档

> **状态：已实现**（2026-07-03）。代码见 `subscriptions.py`、`cashflow_editor.py`（订阅 Tab）、`panorama_data.py` / `panorama_themes.py`（面板卡片）。

参照 [SubCal](https://subcal.onegai.app)，在 asset-panorama 项目内新增「订阅管理」模块。
> 本文是可独立执行的实施规格：拿到任意地方按此落地代码即可。
> 生成日期：2026-07-03

## 一、目标与范围

参照 SubCal（订阅日历 + 续费提醒 + 月度花费统计），实现 4 件事，并与现有现金流打通：

| # | 功能 | 说明 |
|---|---|---|
| 1 | 订阅台账 + 月/年汇总 | 名称/金额/周期/下次扣费日/分类，年付季付自动折算到月，汇总月度与年度总花费 |
| 2 | 日历视图 | 按扣费日把订阅排在当月月历上，一眼看清哪天扣哪笔 |
| 3 | 续费提醒 | 到期前 7/3/1 天在面板告警 |
| 4 | 多币种 + 分类图标 | USD/HKD→CNY 换算；按分类配 emoji/颜色 |
| 5 | **现金流打通** | 订阅按月归一并入固定支出 `fixed_out` → 自动拉低净结余、储蓄率、每周定投额 |

**设计约定**（沿用项目现有风格）：代码/数据同目录扁平放；结构化配置用 JSON、同类记录表用 CSV；金额一律 **CNY 浮点、支出记负**；写文件前先备份 `.bak` 孪生文件；汇率用 `fx[币种]→CNY`；告警用 `(emoji, message)` 元组；调度靠 launchd 每日 16:30 跑 `run_daily.sh` 重算重渲染。

---

## 二、数据模型：新增 `subscriptions.json`

单独文件（不塞进 `cashflow.json`，因字段更丰富且互不干扰）：

```json
{
  "_note": "订阅台账。金额按原币种记(正数)；月度归一后并入现金流固定支出。",
  "订阅": [
    {
      "名称": "Netflix", "金额": 12.99, "币种": "USD", "周期": "月",
      "下次扣费日": "2026-07-15", "开始日期": "2026-01-15",
      "分类": "流媒体", "图标": "🎬", "状态": "启用", "备注": ""
    },
    {
      "名称": "iCloud 2TB", "金额": 21.0, "币种": "CNY", "周期": "月",
      "下次扣费日": "2026-07-08", "开始日期": "2025-01-08",
      "分类": "工具", "图标": "☁️", "状态": "启用", "备注": ""
    }
  ]
}
```

字段约定：
- `周期` ∈ `月 / 季 / 年 / 周`
- `币种` ∈ `CNY / USD / HKD`
- `状态` ∈ `启用 / 暂停 / 试用`；只有 `启用` 计入现金流（`暂停` 排除，`试用` 记 0）
- `金额` 记**正数原币**；日期 ISO `YYYY-MM-DD`
- `分类` 自由文本，预置默认色/emoji：流媒体🎬 / 工具🛠 / 生活🏠 / 会员💳 / 其他📦

---

## 三、新增核心模块 `subscriptions.py`

净现金流公式在项目里被写了 3 遍（`cashflow_editor.compute()`、`panorama_data.collect()`、`panorama.py build()`）。为不让订阅逻辑也散三份，全部收进本模块，三处只调用它。

```python
BASE = Path(__file__).resolve().parent
SUBS = BASE / "subscriptions.json"

CAT_STYLE = {"流媒体": ("🎬", "#e11d48"), "工具": ("🛠", "#2563eb"),
             "生活": ("🏠", "#16a34a"), "会员": ("💳", "#a855f7"),
             "其他": ("📦", "#64748b")}
PER_TO_MONTH = {"月": 1.0, "季": 1/3, "年": 1/12, "周": 52/12}

def load_subs() -> list          # 读 subscriptions.json 的「订阅」数组，缺文件返回 []
def add_period(d, 周期) -> date   # 加一个周期，用 calendar.monthrange 处理月末/闰年
def next_charge(sub, today) -> date   # 把过期的「下次扣费日」滚到今天之后(内存态,不改文件)
def monthly_cny(sub, fx) -> float     # abs(金额)*fx[币种]*PER_TO_MONTH[周期]，非「启用」返回 0
def monthly_total(subs, fx) -> float  # 全部启用订阅的月度合计(CNY)
def yearly_total(subs, fx) -> float   # = monthly_total * 12
def by_category(subs, fx) -> dict     # {分类: 月度CNY}
def upcoming(subs, today, n=90) -> list   # 未来 n 天扣费[(date,名称,CNY,图标)]，按日期排序
def charges_in_month(subs, y, m) -> dict  # {日: [订阅…]}，供月历高亮
def reminders(subs, today, days=(7,3,1)) -> list  # [(emoji, msg)]
```

关键实现点：
- **月度归一**：年/季/周付统一折算到月，与现有「幼儿园(12w/年)=-10000/月」同口径。
- **换算**：`fx` 传入 `portfolio_tracker.get_fx()` 结果（`{"USD":cny,"HKD":cny,"CNY":1.0}`，失败回退 `FX_FALLBACK = {"USD":6.77,"HKD":0.87,"CNY":1.0}`）。
- **加周期**（不引 dateutil）：`add_months` 用 `calendar.monthrange` 取合法日，月末/2 月自动夹取。
- **提醒分级**：`≤1天→🔴`、`≤3天→🟠`、`≤7天→🟡`，msg 如 `"Netflix 将于 2 天后扣费 ¥93"`。
- 模块 `__main__` 放自测（见验证章）。

参考实现骨架：

```python
from calendar import monthrange
from datetime import date, timedelta

def add_months(d, n):
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, monthrange(y, m)[1]))

def add_period(d, 周期):
    return {"月": lambda: add_months(d, 1), "季": lambda: add_months(d, 3),
            "年": lambda: add_months(d, 12), "周": lambda: d + timedelta(7)}[周期]()

def next_charge(sub, today):
    d = date.fromisoformat(sub["下次扣费日"])
    while d < today:
        d = add_period(d, sub["周期"])
    return d

def monthly_cny(sub, fx):
    if sub.get("状态", "启用") != "启用":
        return 0.0
    return abs(sub["金额"]) * fx.get(sub["币种"], 1.0) * PER_TO_MONTH[sub["周期"]]
```

---

## 四、与现金流打通（改 3 处聚合点）

每个算 `fixed_out` 的地方，把订阅月度合计并进去：

```python
import subscriptions as subs
fx = portfolio_tracker.get_fx()
subs_monthly = subs.monthly_total(subs.load_subs(), fx)
fixed_out = -sum(i["金额"] for i in 月度收支 if i["金额"] < 0) + subs_monthly
```

- **`panorama_data.py` `collect()`**（fixed_out 计算处，约 139–153 行）：并入 `subs_monthly`；新增导出键：
  `subsMonthly / subsYearly / subsItems（含滚动后的下次扣费日与 CNY 金额）/ subsByCategory / subsUpcoming / subsCalendar（当月 charges_in_month）`。
  再把 `subs.reminders(...)` 合并进 `D["alerts"]`。
  下游 `netCashflow / savingsRate / dca / safetyMonths`（约 258 行）自动跟着变，无需再改。
- **`cashflow_editor.py` `compute()`**（47–57 行）：同样并入，让「现金流编辑」页实时预览也扣订阅。编辑器需 fx：服务端调一次 `get_fx()`（失败用 `FX_FALLBACK`），注入页面 JS 常量供前端预览。
- **`panorama.py` `build()`**（117–130 行）：老 SVG 渲染器，非 `run_daily.sh` 主链路，**低优先级**，为口径一致同样并入，可后置或跳过。

---

## 五、编辑器新增「订阅管理」Tab（改 `cashflow_editor.py`）

复用现有范式：`shell()` 的 iframe 多 Tab、`do_GET/do_POST` 路由、`parse_save` 的**平行列 zip** 解析、`page()` 的 `mkRow/addRow` 加行 + `recalc()` 前端实时预览。

1. **`shell()`**：nav 加第三 Tab `📆 订阅管理`，`srcFor()` 增 `tab==='subs' → /subs`。
2. **路由**：
   - `GET /subs → subs_page(load_subs())`
   - `POST /subs/save → parse_subs_save(fields)`，写前备份 `subscriptions.json → subscriptions.bak.json`，`json.dumps(..., ensure_ascii=False, indent=2)`；可选 `action=save_rebuild` 复用现有 `_run_daily()` 跑 `run_daily.sh`。
3. **`subs_page(subs)`**：每条订阅一个 `<div class="frow sub">`，平行重复的 `name=`：
   `sub_name / sub_amt / sub_ccy(select) / sub_period(select) / sub_next(type=date) / sub_cat / sub_status(select)`。
   顶部 `live` 实时显示：本月订阅合计 ¥ + 年度合计 + 条数；用注入的 `FX` 常量在 JS 里按币种/周期归一（镜像 `monthly_cny`）。加行 JS 仿现有 `addIncome()` 多字段版。
4. **`parse_subs_save(fields)`**：仿 `income_rows()` 的 `zip(fields.get("sub_name",[]), ...)`，跳过全空行，金额存正数原币，组回 `{"订阅":[…]}` 写文件。

---

## 六、面板新增订阅卡片（改 `panorama_themes.py`）

渲染 helper 均为 `(D, t)` 签名，用 `t["good"]/t["bad"]` 上色、`row(name, amt)` 成行、卡片是 `.grid` 里的 `<div class="card cN">`。新增两块，插在现金流卡（约 251 行）附近：

1. **`subs_summary_html(D, t)`** — 卡片「📆 订阅 · 月 ¥X / 年 ¥Y」：
   - 清单：`图标 名称 · 分类` 左，`¥月度CNY(原币 周期)` 右，如 `🎬 Netflix · 流媒体 …… ¥93/月 ($12.99)`
   - 分类小计（`by_category`），可挂一个 ECharts 饼图（复用 `palette`）
   - 底部：本月订阅合计 / 占固定支出比 / 占税后收入比
2. **`subs_calendar_html(D, t)`** — 卡片「本月扣费日历」：
   - 纯服务端 HTML 月历网格（7 列 × N 行），用 `D["subsCalendar"]` 在有扣费的日子塞 `图标 + ¥`，今天高亮
   - 下方「近 30 天待扣」列表用 `D["subsUpcoming"]`
3. 卡片用现有 `c3/c2` 跨列。`main()`（约 483 行）无需改，`collect → render → 写 panorama_origin.html` 链路不变。

> SubCal 的「App Store 自动抓图标」不做，用 emoji + 分类色替代。

---

## 七、续费提醒

- 逻辑在 `subscriptions.reminders()`，由 `collect()` 合并进 `D["alerts"]`，经现有 `alerts_html/banner_html` 显示在面板。
- 每日 16:30 launchd 跑 `run_daily.sh` 重渲染 → 提醒天然每天刷新。
- **本栈无推送**：面板是静态 HTML + 每日重生，「提醒」= 面板告警。真·推送（到期前 push）需额外一层（`osascript` 系统通知 / 邮件），列为可选扩展。

---

## 八、改动文件清单

| 文件 | 动作 | 要点 |
|---|---|---|
| `subscriptions.json` | 新增 | 订阅台账数据（先放 2 条示例） |
| `subscriptions.py` | 新增 | 全部订阅逻辑 + 自测 |
| `panorama_data.py` | 改 | `collect()` 并入 `subs_monthly`；导出 subs* 键；合并 reminders 到 alerts |
| `cashflow_editor.py` | 改 | 第三 Tab + `/subs`、`/subs/save` + `subs_page` + `parse_subs_save`；`compute()` 并入订阅；注入 FX |
| `panorama_themes.py` | 改 | 新增 `subs_summary_html` + `subs_calendar_html` 两卡片 |
| `panorama.py` | 改(低优先) | `build()` 并入订阅，口径一致；非主链路可后置 |

不需要改：`run_daily.sh`、`com.user.portfolio.plist`（调度不变，重算即自动带上订阅）。

---

## 九、验证（端到端）

1. **模块自测**：`python3 subscriptions.py` —— 断言 `monthly_cny` 对月/季/年/周折算正确、USD/HKD 换算正确；`next_charge` 把过期日滚到今天之后；`add_period` 月末/2 月不越界；各汇总函数结构正确。
2. **现金流打通**：造一条 ¥100/月订阅，调 `panorama_data.collect()`，对比加订阅前后 `fixedOut` +100、`netCashflow` −100、`dca.month` 相应变小 —— 证明影响定投。
3. **编辑器**：`python3 cashflow_editor.py` 后 `curl -s http://127.0.0.1:8765/subs` 返回 200 且含 `sub_name`；POST 一条到 `/subs/save`，确认写入且生成 `subscriptions.bak.json`。
4. **面板渲染**：`python3 panorama_themes.py` 重生 `panorama_origin.html`，`grep` 到「📆 订阅」「本月扣费日历」及某订阅名；浏览器确认清单/日历/分类小计正常，USD 订阅显示原币+CNY。
5. **提醒**：把某订阅 `下次扣费日` 设为 2 天后，重渲染后确认 alerts 出现「将于 2 天后扣费」🟠。

---

## 十、明确不做（简化 / 超范围）

- App Store 自动抓图标 → emoji + 分类色
- 真·推送通知 → 面板告警替代（真推送列为可选扩展）
- 汇率历史、订阅涨价曲线、试用转正提醒 → 后续可加（`history_full.csv` 的 long 表范式可复用）
