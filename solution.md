# 数据清理方案（Solution）

---

## 1. 数据库分层架构

数据仓库分为三层，每一层职责明确，支持多次迭代清洗，同时保留完整的原始数据以备溯源与审计。

```
Excel / CSV 原始文件
        │
        ▼
┌──────────────┐
│   raw 层     │  原始数据落地，全部字段以 TEXT 存储，不做任何转换
└──────────────┘
        │  数据质量检测 (data_check.py) → Data Quality Report (Markdown)
        ▼
┌──────────────┐
│   core 层    │  清洗、标准化后的业务数据，字段类型正确，满足外键约束
└──────────────┘
        │
        ▼
┌──────────────┐
│  audit 层    │  ETL 运行日志，记录每次批次的起止时间、行数和错误信息
└──────────────┘
```

### 1.1 raw 层

| 表名 | 说明 |
|---|---|
| `raw.orders` | 订单原始数据，字段后缀均为 `_text` |
| `raw.customer` | 客户原始数据，字段后缀均为 `_text` |
| `raw.survey` | 问卷原始数据，字段后缀均为 `_text` |

每张表除业务字段外，均附带以下审计元数据：

| 审计字段 | 含义 |
|---|---|
| `source_file` | 来源文件名 |
| `sheet_name` | 来源 Sheet 名 |
| `load_time` | 数据加载时间 |
| `batch_id` | 批次 ID，用于区分不同批次的清洗运行 |
| `row_num_in_sheet` | 原始文件中的行号，便于回溯问题数据 |
| `ingested_at` | 写入数据库的时间戳 |

> **设计原则**：raw 层数据一旦写入不得修改，后续多次清洗均在 core 层进行，保证数据可追溯。

### 1.2 core 层

| 表名 | 说明 |
|---|---|
| `core.customer` | 清洗后的客户主数据 |
| `core.orders` | 清洗后的订单数据，通过 `customer_id` 外键关联客户 |
| `core.survey` | 清洗后的问卷数据，通过 `customer_id` / `order_id` 关联主数据 |

### 1.3 audit 层

`audit.etl_run_log` 记录每次 ETL 批次的运行状态，包括各表写入行数、开始/结束时间及异常信息，用于监控和故障排查。

---

## 2. Data Quality Report

数据质量报告由 `etl/data_check.py` 自动生成，输出为 Markdown 文件（默认路径 `/app/reports/data_quality_report.md`）。

### 2.1 报告结构

```
# Data Quality Report
- Generated at: <时间戳>

## Summary by Table
| table | total_records | total_issues |

## raw.customer
| column | description | invalid_count |

## raw.orders
| column | description | invalid_count |

## raw.survey
| column | description | invalid_count |
```

### 2.2 各表检测字段一览

#### `raw.orders`

| 字段 | 检测项 | 检测规则 |
|---|---|---|
| `order_date_text` | 日期格式合法性 | 必须为 `YYYY/M/D` 或 `YYYY-MM-DD`，不接受中文、斜线混用等格式 |
| `net_amount_text` | 金额格式合法性 | 必须为纯数字（允许小数点），不得含货币符号（€ $ £） |

#### `raw.customer`

| 字段 | 检测项 | 检测规则 |
|---|---|---|
| `birthday_text` | 日期格式合法性 | 必须为 `YYYY-MM-DD` |
| `gender_text` | 性别合法值 | 仅接受 `male` / `female`（大小写不敏感） |
| `country_text` | 国家合法值 | 仅接受 `DE`（大小写不敏感） |
| `zip_code_text` | 邮编合法性 | 不得为 NULL / 空字符串，且必须为 4–5 位数字 |
| `city_text` | 城市字段合法性 | 不得为 NULL / 空字符串，且不得混入邮编 |

#### `raw.survey`

| 字段 | 检测项 | 检测规则 |
|---|---|---|
| `respondent_key_text` | 键类型与可关联性 | `ORD\d+` 格式的订单号视为合法；若为 email，则必须是合法地址，且标准化后在 `customer` 中只能命中唯一一条记录 |
| `diet_pref_text` | 非空检测 | 不得为 NULL / 空字符串 |

### 2.3 通用空值定义

以下值均视为空值（`NULL_LIKE`），不计入格式错误，但在空值率统计中单独标记：

`""` / `"null"` / `"none"` / `"nan"` / `"na"` / `"n/a"` / `"-"`

---

## 3. 各表具体清洗方案

### 3.1 `raw.orders` → `core.orders`

#### 已知数据问题

| 字段 | 问题描述 | 示例 |
|---|---|---|
| `order_date_text` | 日期格式混乱，包含多种分隔符和中文 | `2021/9/1`、`2/Aug/`、`2021年9月18日` |
| `net_amount_text` | 金额含欧元符号，部分含空格 | `€12.50`、`12.50`、`12 50` |
| `email_text` | 大小写不一致 | `User@Example.com` vs `user@example.com` |
| `order_number_text` | 格式基本统一，但可能存在前缀大小写差异 | `ORD001` vs `ord001` |

#### 清洗步骤

| 步骤 | 对应转换器 | 操作说明 |
|---|---|---|
| 1. 邮箱标准化 | `01_normalize_email.py` | `strip()` 去除首尾空格，`lower()` 统一小写；原始值保留在 `email`，标准化值写入 `email_norm` |
| 2. 日期解析 | `02_normalize_order_date.py` | 处理中文日期（年月日 → `-`），补全缺失年份（如 `2/Aug/` → `2/Aug/2021`），使用 `dateutil.parser` 宽松解析后写入 `order_date DATE` |
| 3. 金额清洗 | `03_normalize_net_amount.py` | 去除 `€` 符号和空格，`pd.to_numeric` 转换为 `NUMERIC(12,2)`，无法转换则写 NULL |
| 4. 订单号标准化 | `04_normalize_order_number.py` | 统一大小写，写入 `order_number TEXT UNIQUE` |

#### 核心字段约束（core.orders）

- `order_date DATE NOT NULL` — 无法解析的日期行丢弃
- `net_amount NUMERIC(12,2) NOT NULL` — 无法解析的金额行丢弃
- `customer_id BIGINT NOT NULL` — 必须能通过 `email_norm` 关联到 `core.customer`
- `order_number TEXT UNIQUE` — 去重，防止重复写入

---

### 3.2 `raw.customer` → `core.customer`

#### 已知数据问题

| 字段 | 问题描述 | 示例 |
|---|---|---|
| `birthday_text` | 日期格式多样，含中文 | `1990-01-01`、`1990年1月1日`、`01/01/1990` |
| `gender_text` | 缩写与全写混用 | `m`、`male`、`M`、`Female` |
| `country_text` | 中英文、缩写混用 | `DE`、`Deutschland`、`Germany` |
| `city_text` | 城市与邮编混写在同一字段 | `Düsseldorf 40239` |
| `zip_code_text` | 有时为空，邮编混在城市字段中 | （见上） |
| `loyalty_score_text` | 文本类型，需验证值域 1–3 | `"1"`、`"2"`、`"3"` |
| `email_text` | 大小写不一致，可能存在重复实体 | `Alice@example.com` vs `alice@example.com` |

#### 清洗步骤

| 步骤 | 对应转换器 | 操作说明 |
|---|---|---|
| 1. 邮箱标准化 | `01_normalize_email.py` | 同 orders，`strip()` + `lower()`，写入 `email` / `email_norm` |
| 2. 生日解析 | `02_normalize_birthday.py` | 中文日期替换（年月日 → `-`），`dateutil.parser` 解析，写入 `birthday DATE` |
| 3. 性别标准化 | `03_normalize_gender.py` | 映射表：`m/male → male`，`f/female → female`，无法识别则写 `unknown` |
| 4. 国家标准化 | `04_normalize_country.py` | `DE/Deutschland/Germany → "DE"`（ISO 3166-1 alpha-2），写入 `country_code CHAR(2)` |
| 5. 城市/邮编拆分 | `05_normalize_city_zip.py` | 正则提取 `city_text` 中的 4–5 位数字作为邮编，剩余部分作为城市名；同时将 `loyalty_score_text` 转为 `SMALLINT` |

#### 核心字段约束（core.customer）

- `email_norm TEXT` — 建有索引 `idx_customer_email_norm`，用于 orders/survey 的关联查询
- `loyalty_score SMALLINT` — 业务值域 1–3，超出范围写 NULL
- 城市与邮编分别存储，不再混写

---

### 3.3 `raw.survey` → `core.survey`

#### 已知数据问题

| 字段 | 问题描述 | 示例 |
|---|---|---|
| `respondent_key_text` | 若使用 email 作为答卷标识，标准化后在客户表中可能命中多条记录，导致关联歧义 | `user@example.com` |
| `diet_pref_text` | 可能存在空值 | NULL / `""` |
| `taste_pref_text` | 可能存在空值或不规范值 | NULL / `""` |

#### 清洗步骤

| 步骤 | 对应转换器 | 操作说明 |
|---|---|---|
| 1. 键类型识别 | `01_normalize_key_type.py` | 正则判断 `respondent_key` 是 `email`、`order_number` 还是 `invalid`，写入 `key_type TEXT` |
| 2. 饮食偏好标准化 | `02_normalize_diet_pref.py` | 去除首尾空格，NULL 保留为 NULL |
| 3. 口味偏好标准化 | `03_normalize_taste_pref.py` | 同上 |

#### 关联逻辑（core.survey）

- `key_type = 'email'` → 通过 `email_norm` 匹配 `core.customer`；仅当标准化后的 email 在客户表中唯一时才填入 `customer_id`
- `key_type = 'order_number'` → 通过 `order_number` 匹配 `core.orders`，填入 `order_id`
- `key_type = 'invalid'`，或 email 命中多个客户 → 两个外键均为 NULL，数据保留但标记为无法关联

---

## 4. 清洗流程总览

```
原始 Excel
    │
    ▼
[ingest_raw.py]          写入 raw 层（全量 TEXT，带批次元数据）
    │
    ▼
[data_check.py]          扫描 raw 层，生成 Data Quality Report（Markdown）
    │
    ▼
[plugin_engine.py]       按序执行各表 transformers/
    │   customer: 01~05
    │   orders:   01~04
    │   survey:   01~03
    ▼
[core 层写入]            清洗结果落地 core.customer / core.orders / core.survey
    │
    ▼
[audit.etl_run_log]      记录本次批次的行数统计与运行状态
```

> **多次清洗支持**：raw 层数据不变，每次修改 transformer 逻辑后重新运行 `plugin_engine.py` 即可覆盖 core 层，无需重新摄取原始文件。新批次通过 `batch_id` 区分，历史 raw 数据完整保留。

---

## 5. ETL Monitoring Dashboard Design

为更好地监控 ETL Routines 的运行状态，可以在 Grafana 中设计一个统一 dashboard，同时观察 **ETL 流程健康度、数据时效性、数据质量以及数据库健康度**。重点不在可视化样式，而在监控指标是否能够及时暴露失败、延迟、积压、质量下降和数据库瓶颈。

### 5.1 Dashboard 分区

#### A. ETL Process Health

用于判断任务是否稳定、是否按时完成、是否出现积压。

| KPI | 含义 | 监控目的 |
|---|---|---|
| End-to-End Runtime | 每个 ETL 批次从 `started_at` 到 `ended_at` 的总耗时 | 判断 ETL 是否变慢 |
| Job Success Rate | 成功批次数 / 总批次数 | 判断流程稳定性 |
| In-time Completion Rate | 在 SLA 时间内完成的批次数 / 总批次数 | 判断是否满足业务时效要求 |
| Backlog Size | 待处理文件数、待处理批次数、失败未重跑任务数 | 判断是否存在积压 |
| Running / Failed / Pending Job Count | 当前运行中、失败、等待中的任务数量 | 了解当前队列状态 |

其中 `audit.etl_run_log` 可直接提供批次开始时间、结束时间、状态以及各表加载行数，是该区域的核心数据来源。

#### B. Latency / Freshness

用于判断数据是否“新鲜”，是否已经成功送达下游。

| KPI | 含义 | 监控目的 |
|---|---|---|
| Last Successful Load Time | 最近一次成功加载时间 | 直观看最新成功批次 |
| Data Freshness Lag | 当前时间 - 最近一次成功加载时间 | 判断数据是否过旧 |
| Average Load Time | 历史平均加载耗时 | 观察整体性能趋势 |
| P95 Load Time | 95 分位加载耗时 | 发现长尾慢任务 |
| Per-table Load Delay | `orders` / `customer` / `survey` 各表延迟 | 精确定位慢点 |

#### C. Data Quality

用于判断 ETL 产出的数据是否可靠、是否可用于分析。

| KPI | 含义 | 监控目的 |
|---|---|---|
| 数据异常率 | 异常记录数 / 总记录数 | 整体质量健康度 |
| Null Rate | 关键字段空值率 | 发现缺失问题 |
| Duplicate Rate | 主键或候选键重复率 | 发现去重失败或源数据异常 |
| Format Error Rate | 日期、金额、email 等格式错误率 | 发现标准化问题 |
| Referential Integrity Error Rate | 无法关联 `customer` / `orders` 的比例 | 判断数据是否可关联 |
| Rejected Rows Count | 清洗后被丢弃的记录数 | 判断损耗规模 |
| Rule Violation Count | 按规则分类统计异常数 | 快速定位问题类型 |

当前项目中的 `etl/data_check.py` 与 `reports/data_quality_report.md` 已经提供了异常扫描基础，可进一步将各类 issue count 写入监控表或直接暴露给 Grafana。

#### D. Database Health

用于判断 ETL 问题是否由数据库资源压力导致。

| KPI | 含义 | 监控目的 |
|---|---|---|
| DB CPU / Memory / IOPS / Connections | 数据库资源使用情况 | 判断资源是否紧张 |
| Slow Query Count | 慢查询数量 | 判断数据库是否成为瓶颈 |
| Avg Query Time | 平均查询耗时 | 观察查询性能变化 |
| Deadlock Count | 死锁数量 | 判断并发写入是否存在冲突 |
| Lock Wait Time / Blocking Sessions | 锁等待时间、阻塞会话数 | 发现卡顿原因 |
| Disk Usage / Table Growth | 磁盘使用和表增长 | 监控容量风险 |
| Replication Lag | 主从延迟（如有） | 判断副本同步情况 |
| Transaction Rollback Rate | 回滚比例 | 判断写入稳定性 |

### 5.2 Dashboard 首页核心 KPI

首页建议展示最关键的 6–8 个指标，用于快速判断当前是否健康：

- 当前 ETL 状态（Healthy / Warning / Critical）
- 最近一次成功加载时间
- 今日 Job Success Rate
- 今日 In-time Completion Rate
- 当前 Backlog Size
- Average Runtime / P95 Runtime
- 当前数据异常率
- 当前 DB Connections / Slow Query Count

### 5.3 告警策略

Dashboard 应结合告警规则，避免只“看板可见”而无法及时响应。以下阈值仅作为起始示例，实际项目中应根据 **ETL 调度频率、历史基线和业务 SLA** 做参数化配置：

- 30 分钟内没有成功 load （适用于高频任务；若为日批或小时批，应按调度周期调整）
- Backlog Size 超过阈值
- Job Success Rate 低于 95% （示例阈值，应结合业务容忍度调整）
- 数据异常率高于 3% （示例阈值，应结合历史质量基线调整）
- DB Connections 使用率超过 80%
- Slow Query Count 持续升高
- Deadlock Count 大于 0

### 5.4 Grafana 实现思路

若采用 Grafana，建议拆分为两个数据源：

1. **ETL audit / quality 数据源**
   - 来源：`audit.etl_run_log`、数据质量检查结果表或质量统计表
   - 用途：展示 Runtime、Success Rate、Freshness、异常率、Rejected Rows 等 ETL 指标

2. **数据库监控数据源**
   - 来源：数据库系统视图或 exporter
   - 用途：展示 CPU、内存、IOPS、连接数、慢查询、死锁、锁等待等数据库指标

Grafana 只负责统一展示与告警，推荐使用：

- **Stat**：展示核心 KPI
- **Time Series**：展示 Runtime、异常率、慢查询等趋势
- **Table**：展示失败批次、异常规则明细、慢 SQL 明细
- **Alert Rules**：对时延、失败率、资源压力做自动告警

### 5.5 设计目标总结

该 dashboard 不应只监控“任务是否执行成功”，而应同时回答以下问题：

1. **流程是否正常跑完**  
2. **数据是否按时到达**  
3. **数据质量是否可接受**  
4. **数据库是否成为瓶颈**

只有同时覆盖这四类问题，dashboard 才能真实反映 **ETL Routines 的当前状态** 与 **数据库当前状态**。
