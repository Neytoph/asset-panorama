# 现金流「月度对账确认」功能 · 实施文档

> **状态：已实现**（2026-07-03）。`cashflow_history.py` 草稿/确认、`cashflow_editor` 对账 Tab、`panorama_themes` 图表区分已落地。

> 在 asset-panorama 项目内，为现金流月度历史增加「对账后确认再记录」的机制。
> 本文是可独立执行的实施规格，交付给实现者按此落地。
> 生成日期：2026-07-03

## 一、Context（为什么做）

现在 `cashflow_history.py` 的 `record_month()` 被 `panorama_data.collect()` 每次运行**自动调用**，把当月现金流按 `cashflow.json` 的配置假设**静默写入历史**。问题：

- `cashflow.json` 只含「已知固定项」（房租/房贷/幼儿园/话费/订阅…），**不含真实变动开支**（吃饭/购物/其他育儿…）。
- 所以自动记录的每月历史只是「重复的假设值」，不是对账后的真实值，储蓄率趋势会系统性偏高、失真。

**目标**：每月现金流在写入历史前，必须由用户结合当月**银行 + 微信/支付宝账单**对账、并**显式确认**；记录的是对账后的真实数字，且确认后锁定、不被自动跑覆盖。

设计沿用项目既有约定：数据 CSV、金额 CNY 浮点、支出记正数（幅度）、写前备份 `.bak`；编辑器新 Tab 复用现有 `subs_page` + `/subs`、`/subs/save` 模式。

---

## 二、现状与关键代码锚点

- `cashflow_history.py`：`FIELDS = [月份,税后收入,固定支出,净结余,储蓄率,定投额,被动收入,订阅月支]`；`load_history()`、`record_month(D)`（当月 upsert）；`CFH=cashflow_history.csv`、`BAK=.bak.csv`。
- `panorama_data.py collect()` 末尾：`cfh.record_month(out); out["cashflowHistory"] = cfh.load_history()` —— **这是当前的自动记录点，需改造**。
- `cashflow_editor.py`：本地 HTTP 服务 `127.0.0.1:8765`；`shell()` 有 3 个 Tab（`view/edit/subs`，见 nav 的 `data-tab` 与 `srcFor()`）；路由 `do_GET`（`/edit`、`/subs`、`/panorama`）、`do_POST`（`/save`、`/subs/save`、`/rebuild`）；`subs_page()` + `parse_*` 是新 Tab 模板；页内已有 `compute()`/收入测算供估算复用。
- `panorama_themes.py`：`c_cf_trend` 图消费 `D.cashflowHistory` 画「净结余柱 + 储蓄率线」。

---

## 三、数据模型：扩展 `cashflow_history.csv`

新表头（原有基础上加 4 列，含一列真实变动开支）：

```
月份, 已对账, 对账日, 税后收入, 固定支出, 其他实际支出, 净结余, 储蓄率, 定投额, 被动收入, 订阅月支, 对账备注
```

字段语义：
- `已对账` ∈ `是 / 否`。`否` = 配置估算的**草稿**；`是` = 对账确认后的**权威值**。
- `对账日` = 确认当天 `YYYY-MM-DD`（草稿留空）。
- `固定支出` = `cashflow.json` 已知固定项 + 订阅月折算（= 现在 `fixedOut` 口径）。
- **`其他实际支出`（新）** = 对账时从账单录入的、配置里没有的变动开支。**这是对账的核心价值**：草稿默认 0，确认时补真值。
- `净结余` = `税后收入 − 固定支出 − 其他实际支出`。
- `储蓄率` = `净结余 / 税后收入`（比例 0~1）。
- `对账备注` = 自由文本（如「招行+微信+支付宝+工资卡已核对」）。

**向后兼容**：现有 `cashflow_history.csv`（2026-07 一行、无新列）读入时缺列按 `已对账=否`、`其他实际支出=0` 处理 → 7 月自动降级为「草稿」，等用户去对账确认。

---

## 四、逻辑变更：`cashflow_history.py`

把「自动 upsert」拆成**草稿**与**确认**两条路径，并加一条保护规则。

```python
FIELDS = ["月份","已对账","对账日","税后收入","固定支出","其他实际支出",
          "净结余","储蓄率","定投额","被动收入","订阅月支","对账备注"]

def load_history():
    # 解析新列；缺列默认 已对账="否"/其他实际支出=0/对账日=""/对账备注=""；数值转 float；按月份升序

def record_provisional(D, month=None):
    """自动草稿：仅当该月尚未『已对账=是』时，用配置估算刷新草稿行(已对账=否,其他实际支出=0)。
       已对账的月份一律不动 —— 保护对账结果不被每日自动跑覆盖。"""
    # income<=0 跳过；existing[month] 若 已对账=='是' → 直接 return，不写
    # 否则写/更新草稿行：净结余/储蓄率沿用 D 的估算，其他实际支出=0

def confirm_month(month, 实际税后收入, 其他实际支出, 定投额, 被动收入, 订阅月支, 固定支出, 备注):
    """对账确认：写入 已对账=是 + 对账日=today 的权威行，净结余=收入-固定支出-其他实际支出，
       储蓄率随之重算；备份 .bak 后写回。返回该行 dict 供页面回显。"""
```

**核心不变量**：`record_provisional` 遇到 `已对账=是` 的月份必须跳过（这条决定了「确认后锁定」）。

---

## 五、编辑器新增「月度对账」Tab（`cashflow_editor.py`）

照搬 `subs` Tab 的三件套：`shell()` 加按钮 + `srcFor()` 加分支、`GET /recon` 渲染、`POST /recon/save` 保存。

1. **`shell()`**：nav 加 `<button class="tab" data-tab="recon">🧾 月度对账</button>`；`srcFor()` 加 `tab==='recon' ? '/recon?_='+Date.now()`。
2. **`GET /recon → recon_page()`**：一页含
   - **月份选择**（默认当前月；可选历史月补账）。
   - **配置估算区（只读）**：复用现有 `compute()`/收入测算，显示 当月实发 / 固定支出(含订阅) / 估算净结余 / 估算储蓄率 —— 对账起点。
   - **对账录入区**：`实际税后收入`（预填估算值、可改）、`其他实际支出`（账单里配置外的变动开支，默认 0）、`对账备注`。
   - **实时预览**（JS）：`实际净结余 = 实际收入 − 固定支出 − 其他实际支出`、`实际储蓄率`；与估算并排显示差额，直观看出「配置漏了多少变动开支」。
   - **对账清单提示**（纯 UI 勾选，帮助记忆）：☐招行 ☐微信 ☐支付宝 ☐工资卡。
   - **历史表**：每月一行 + 状态徽章（`✅已对账` / `⏳草稿`）+ 对账日/备注。
   - **按钮「✅ 确认并记录本月」**。
3. **确认提示**（本功能要点）：按钮 `onclick` 先弹 JS `confirm('确认本月已按银行/微信账单对账无误？记录后将覆盖本月草稿并锁定该月。')`，取消则不提交。
4. **`POST /recon/save → parse_recon_save()`**：读月份/实际收入/其他实际支出/备注，调 `confirm_month(...)`，备份 `cashflow_history.bak.csv` 后写回；可选 `action=save_rebuild` 复用现有 `_run_daily()` 刷新全景。

---

## 六、`panorama_data.collect()` 与面板图

- **`collect()`**：`cfh.record_month(out)` → 改为 `cfh.record_provisional(out)`（草稿刷新，且不覆盖已对账月）。`out["cashflowHistory"]` 不变。
- **`panorama_themes.py` `c_cf_trend`**：区分草稿/已对账 —— 已对账点实心、草稿点空心（或加 `⏳` 标记与虚线段），hint 补一句「⏳=未对账草稿，去『月度对账』确认」。每行已带 `已对账` 字段可直接判定。

---

## 七、改动文件清单

| 文件 | 动作 | 要点 |
|---|---|---|
| `cashflow_history.py` | 改 | 扩展 `FIELDS`（+已对账/对账日/其他实际支出/对账备注）；`record_month`→拆成 `record_provisional`(跳过已对账) + `confirm_month`；`load_history` 兼容旧表缺列 |
| `panorama_data.py` | 改 | `collect()` 里 `record_month`→`record_provisional` |
| `cashflow_editor.py` | 改 | 新增 `recon` Tab + `/recon`、`/recon/save` 路由 + `recon_page()` + `parse_recon_save()`；确认按钮加 `confirm()` 弹窗；估算区复用现有 compute/收入测算 |
| `panorama_themes.py` | 改 | `c_cf_trend` 区分草稿/已对账点位与 hint |

不需要改：`run_daily.sh`、launchd（调度不变；自动跑只刷新草稿，绝不动已对账月）。

---

## 八、验证（端到端）

1. **保护规则（最关键）**：造 `cashflow_history.csv` 一行 `2026-07 已对账=是`，跑 `record_provisional(D)` → 断言该行**未被改写**；再造 `2026-08 已对账=否`，跑 → 断言 8 月草稿被刷新。
2. **confirm 口径**：`confirm_month('2026-07', 实际收入=60000, 其他实际支出=8000, …)` → 断言 `净结余=收入-固定支出-8000`、`储蓄率` 相应下降、`已对账=是`、`对账日=今天`、生成 `.bak`。
3. **编辑器**：起服务后 `curl -s http://127.0.0.1:8765/recon` 返回 200 且含 `其他实际支出`、`确认并记录本月`；POST 一条到 `/recon/save`，确认写入 `已对账=是` 且备份生成。
4. **自动跑不再定稿**：跑 `python3 panorama_themes.py`（内部 `collect`）→ 断言当月仍为 `已对账=否` 草稿（除非已被确认过）。
5. **面板**：重渲染后确认 `c_cf_trend` 里已对账月实心、当前草稿月带 `⏳`。

---

## 九、交接注意点

- **核心不变量**：`record_provisional` 永不覆盖 `已对账=是` 的月份——保证「对账即定稿、自动跑不破坏」。验证 1 必须过。
- 保留「草稿」是刻意的：让面板中途不空、对账页有估算预填。若想更严格（**确认前完全不写任何历史行**），把 `collect()` 里对 `record_provisional` 的调用去掉即可，其余不变。
- `其他实际支出` 是对账相对配置估算的**唯一新增真实信息**，务必让它进 `净结余`，否则对账没意义。
- 现有 2026-07 那行会自动变成 `⏳草稿`，属预期；用户去 `/recon` 确认一次即转正。
