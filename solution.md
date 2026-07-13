# 数据清理方案（Solution）

---

## 1. 目标与设计原则

本项目围绕一份包含 `orders`、`customer`、`survey` 三个 Sheet 的 Excel 数据，构建一个可重复执行的 ETL 流程，并补充数据质量报告与 Grafana 监控能力。

设计原则如下：

1. **原始数据可追溯**：raw 层保留 Excel 读入时的展示值，不在入库阶段做业务清洗。
2. **清洗逻辑可重跑**：core 层由插件链生成，允许在不重采原始文件的前提下重复执行。
3. **质量问题可量化**：`etl/data_check.py` 输出 Markdown 报告，并将质量统计落到 PostgreSQL。
4. **运行状态可观测**：通过 `audit` 表和视图向 Grafana 暴露批次状态、质量指标、表新鲜度和数据量。

---

## 2. 分层架构

```
Excel / CSV
    │
    ▼
raw 层         原始落地，保留单元格展示文本 + 批次审计字段
    │
    ├── data_check.py      生成 Data Quality Report，并写入 audit 质量统计表
    │
    ▼
core 层        通过 transformers 做标准化、关联、去除无法入 core 的记录
    │
    ▼
audit 层       记录 ETL 运行日志、质量汇总，并提供 Grafana 查询视图
```

### 2.1 raw 层

| 表名 | 说明 |
|---|---|
| `raw.orders` | 订单原始数据，字段名以 `_text` 结尾 |
| `raw.customer` | 客户原始数据，字段名以 `_text` 结尾 |
| `raw.survey` | 问卷原始数据，字段名以 `_text` 结尾 |

每张 raw 表都带有以下审计字段：

| 字段 | 说明 |
|---|---|
| `source_file` | 来源文件名 |
| `sheet_name` | 来源 Sheet 名 |
| `load_time` | 载入时间 |
| `batch_id` | 本次摄取批次 ID |
| `row_num_in_sheet` | Excel 中的原始行号 |
| `ingested_at` | 写入数据库时间 |

### 2.2 core 层

| 表名 | 说明 |
|---|---|
| `core.customer` | 标准化后的客户主数据 |
| `core.orders` | 标准化后的订单数据，依赖 `customer_id` 外键 |
| `core.survey` | 标准化后的问卷数据，可关联 `customer_id` 或 `order_id` |

### 2.3 audit 层

| 表/视图 | 说明 |
|---|---|
| `audit.etl_run_log` | 每次 ETL 批次的开始时间、结束时间、状态、行数与错误信息 |
| `audit.data_quality_issue_summary` | 字段级质量问题统计 |
| `audit.data_quality_table_summary` | 表级总记录数与异常记录数 |
| `audit.v_etl_run_metrics` | 单次运行时长与状态指标 |
| `audit.v_etl_health_kpis` | 最近状态、成功率、平均时长、P95 时长 |
| `audit.v_data_quality_batch_metrics` | 每批次每表异常率 |
| `audit.v_data_quality_issue_metrics` | 字段级质量问题时序 |
| `audit.v_latest_data_quality_summary` | 最新批次质量快照 |
| `audit.v_table_volume` | raw/core 当前表行数 |
| `audit.v_table_freshness` | raw/core 最新时间戳与 freshness lag |

---

## 3. 原始摄取方案

`etl/ingest_raw.py` 的职责是把 Excel 单元格内容按“展示值”尽量原样写入 raw 层，而不是提前标准化。

### 3.1 摄取特点

1. 使用 `openpyxl` 读取单元格值和 `number_format`
2. 尽量保留 Excel 展示效果，例如：
   - 中文日期：`2021年8月8日`
   - 本地化短日期：`2/ Aug/`
   - 货币后缀：`43.58 €`
3. 跳过整行全空的记录
4. 为同一次导入生成统一 `batch_id`

### 3.2 为什么 raw 层不做清洗

- 便于追溯源数据问题
- 避免摄取阶段把格式问题“洗掉”
- 支持后续只重跑清洗链，不重新导入文件

---

## 4. Data Quality Report 设计

数据质量报告由 `etl/data_check.py` 生成，默认输出路径为 `/app/reports/data_quality_report.md`。默认只检查最新一个 `batch_id`，同时把统计结果写入 `audit.data_quality_issue_summary` 与 `audit.data_quality_table_summary`。

### 4.1 报告结构

报告包含两部分：

1. **Summary by Table**
   - `table`
   - `total_records`
   - `total_issues`（按“存在任一问题的 distinct 行数”统计）
2. **各表字段问题明细**
   - `column`
   - `description`
   - `invalid_count`

### 4.2 空值定义

以下值统一按空值处理：

`""`、`null`、`none`、`nan`、`na`、`n/a`、`-`

这些值通常不计入“格式错误”，但会在需要非空的规则中被统计为问题。

### 4.3 各表检查规则

#### `raw.orders`

| 字段 | 检查项 | 规则 |
|---|---|---|
| `order_date_text` | 订单日期格式 | 必须是可解析的年在前日期，且原始格式需满足 `YYYY/M/D` 或 `YYYY-MM-DD` |
| `net_amount_text` | 金额格式 | 必须是不带货币符号的纯数字，允许小数，不允许 `€/$/£/¥/₩` |

#### `raw.customer`

| 字段 | 检查项 | 规则 |
|---|---|---|
| `birthday_text` | 生日格式 | 必须是合法的 `YYYY-MM-DD` 日期 |
| `gender_text` | 性别值 | 仅接受 `male` / `female` |
| `country_text` | 国家值 | 仅接受 `DE` |
| `zip_code_text` | 邮编格式 | 必须为 4–5 位数字，且不能为空 |
| `city_text` | 城市格式 | 不得为空，且不得夹杂邮编 |

#### `raw.survey`

| 字段 | 检查项 | 规则 |
|---|---|---|
| `respondent_key_text` | 引用键格式 | 必须是合法 email 或 `ORD` + 数字 |
| `respondent_key_text` | 客户唯一性 | 若为 email，标准化后在 customer 原始数据中不能命中多个客户 |
| `diet_pref_text` | 非空检查 | 不得为 NULL / 空字符串 |

---

## 5. 清洗与入 core 方案

`etl/main.py` 会先读取最新批次 raw 数据，再按表发现并执行 `etl/transformers/<table>/` 下的插件，最后重建 core 层结果。

### 5.1 `raw.orders` → `core.orders`

#### 已知问题

| 字段 | 问题 |
|---|---|
| `email_text` | 大小写、首尾空格不一致 |
| `order_date_text` | 日期格式混杂，含中文、英文月份、缺少年份等 |
| `net_amount_text` | 可能含 `€` 或空格 |
| `order_number_text` | 可能不满足 `ORD\d+` |

#### 转换链

| 顺序 | 插件 | 实际处理 |
|---|---|---|
| 1 | `01_normalize_email.py` | `strip()` 后保留到 `email`，并生成小写 `email_norm` |
| 2 | `02_normalize_order_date.py` | 解析中文日期；对 `2/Aug/` 这类值补默认年份 `2021`；成功后写入 `order_date` |
| 3 | `03_normalize_net_amount.py` | 去除 `€` 与空格，再转数值 |
| 4 | `04_normalize_order_number.py` | `strip()` 后校验 `^ORD\d+$`，不合法则置空 |

#### 入 core 规则

- 通过 `email_norm` 关联 `core.customer`
- 无法关联客户的订单会被丢弃
- `order_date` / `net_amount` / `order_number` 为空的订单会被丢弃
- `order_number` 重复时仅保留第一条
- `currency_code` 使用表默认值 `EUR`

### 5.2 `raw.customer` → `core.customer`

#### 已知问题

| 字段 | 问题 |
|---|---|
| `email_text` | 大小写、首尾空格不一致 |
| `birthday_text` | 可能是中文日期或其他可解析格式 |
| `gender_text` | 存在 `m/f` 等缩写 |
| `country_text` | 存在 `Deutschland`、`Germany` 等写法 |
| `city_text` / `zip_code_text` | 城市和邮编可能混写 |
| `loyalty_score_text` | 文本类型，需要转数值 |

#### 转换链

| 顺序 | 插件 | 实际处理 |
|---|---|---|
| 1 | `01_normalize_email.py` | 生成 `email` 与 `email_norm` |
| 2 | `02_normalize_birthday.py` | 解析生日并写入 `birthday` |
| 3 | `03_normalize_gender.py` | `m/male → male`，`f/female → female`，其他值置为 `unknown` |
| 4 | `04_normalize_country.py` | `de/deutschland/germany → DE`，其他非空值取前两位大写 |
| 5 | `05_normalize_city_zip.py` | 当 `zip_code_text` 为空时，尝试从 `city_text` 抽取 4–5 位邮编；同时写入 `loyalty_score` 数值列 |

#### 入 core 规则

- customer 全量写入 `core.customer`
- 允许重复 `email_norm`
- `loyalty_score` 当前仅做数值转换，**未在实现中限制 1–3 值域**（见下文 8.2）

### 5.3 `raw.survey` → `core.survey`

#### 已知问题

| 字段 | 问题 |
|---|---|
| `respondent_key_text` | 可能是 email、订单号，也可能是无效值 |
| `diet_pref_text` | 可能为空，且存在德文值 |
| `taste_pref_text` | 可能为空，且存在拼写错误 |

#### 转换链

| 顺序 | 插件 | 实际处理 |
|---|---|---|
| 1 | `01_normalize_key_type.py` | 识别 `email`、`order_number`、`invalid`，并保留 `respondent_key` |
| 2 | `02_normalize_diet_pref.py` | 统一小写，将 `vegetarisch` 映射为 `vegetarian` |
| 3 | `03_normalize_taste_pref.py` | 统一小写，将 `sweeet` 修正为 `sweet` |

#### 入 core 规则

- `key_type = order_number`：通过 `order_number` 关联 `core.orders.order_id`
- `key_type = email`：通过 `email_norm` 关联 `core.customer.customer_id`
- 只有 **唯一 email** 才会写入 `customer_id`，重复 email 不关联
- 无法关联的 survey 记录仍保留，只是外键为空

---

## 6. ETL 全流程

```
Excel 文件
   │
   ▼
ingest_raw.py
   │   读取 Sheet -> 写入 raw.* -> 打上 batch_id
   ▼
data_check.py
   │   生成 Markdown 质量报告
   │   回写 audit.data_quality_* 统计表
   ▼
main.py
   │   读取最新 batch
   │   发现并执行 transformers
   │   TRUNCATE core.survey/core.orders/core.customer
   │   重新写入 core
   ▼
audit.etl_run_log
   │   记录 success/failed、行数、错误信息
   ▼
Grafana
```

### 6.1 当前实现的特点

1. **以最新 batch 为准**：`main.py` 和 `data_check.py` 都默认基于最新 `raw.orders.batch_id`
2. **core 层是重建式写入**：每次运行会先清空 core，再写回本次结果
3. **审计完整**：成功与失败都会记录到 `audit.etl_run_log`

---

## 7. Grafana 监控设计

当前仓库已经提供 Grafana provisioning 文件，并直接查询 PostgreSQL 中的 `audit` 视图。

### 7.1 已落地的监控主题

根据 README 与 SQL 视图设计，当前 dashboard 主要覆盖：

- 最新 ETL 状态
- Job Success Rate
- Average Runtime
- Latest Batch Quality Issue Count
- ETL Runtime Trend
- Quality Issue Trend
- Per-table Freshness
- Raw/Core Table Volume

### 7.2 指标来源映射

| 监控主题 | 数据来源 |
|---|---|
| 运行状态、成功率、平均/P95 时长 | `audit.v_etl_health_kpis` |
| 单次运行详情 | `audit.v_etl_run_metrics` |
| 批次质量异常率 | `audit.v_data_quality_batch_metrics` |
| 字段级质量趋势 | `audit.v_data_quality_issue_metrics` |
| 最新批次质量总览 | `audit.v_latest_data_quality_summary` |
| 表体量 | `audit.v_table_volume` |
| 表新鲜度 | `audit.v_table_freshness` |

### 7.3 设计价值

这一套 dashboard 可以回答四类核心问题：

1. **任务有没有正常跑完**
2. **数据是不是按批次持续进入系统**
3. **最新一批数据质量有没有下降**
4. **raw/core 表的数据量与时效性是否异常**

---

## 8. 当前方案的边界与可优化点

为保证方案说明和代码实现一致，需要明确当前实现仍有以下边界：

1. **Data Quality 规则比清洗规则更严格**
   - 例如 `birthday_text` 质检只接受 `YYYY-MM-DD`
   - 但清洗阶段仍会尝试解析中文日期或其他可解析格式

2. **`loyalty_score` 只做数值化，没有做业务值域约束**
   - 若需要限制在 1–3，应在 transformer 或入 core 前新增规则

3. **订单号没有统一大小写**
   - 当前只是 `strip()` 后校验 `^ORD\d+$`
   - 若源数据可能出现 `ord001`，可增加显式大写标准化

4. **customer 允许重复 email**
   - 这与 survey 的唯一关联策略形成了业务折中：保留原始客户记录，但只对唯一 email 做 survey 关联

5. **core 采用全量重建**
   - 当前适合作业场景和小数据量
   - 若进入生产，应考虑增量合并、幂等写入和更细粒度回滚策略

---

## 9. 总结

本方案的核心不是“把脏数据一次性修好”，而是建立一条 **可追溯、可重跑、可观测** 的 ETL 链路：

- raw 层保留原貌
- core 层承载可复用的清洗结果
- audit 层提供运行与质量监控
- Grafana 将 ETL 健康度、质量和表时效统一展示

这样既能支撑当前作业要求，也为后续继续补充规则、增强监控和演进生产化方案留下了空间。
