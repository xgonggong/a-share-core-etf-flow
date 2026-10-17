# 数据下载、质量控制与交付

仅在下载或处理数据时读取本文件。

## 数据来源与公式

- 份额：上交所 `commonQuery.do`，SQL ID 为 `COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L`；`TOT_VOL` 单位为万份。
- 价格：上交所日K线接口 `https://yunhq.sse.com.cn:32042/v1/sh1/dayk/{代码}`，使用未复权收盘价。
- `份额 = TOT_VOL × 10000`。
- `估算金额（亿元） = 份额变化 × 收盘价 ÷ 100000000`。

## 选择范围

默认池从 `default_observation_pool.json` 加载，不得硬编码在脚本中。`--codes`、`--pool-file` 和 `--buckets` 互斥。更新默认池时同时更新 `preset_id` 和 `as_of`。长周期研究必须披露名单版本；历史时点成分研究应使用对应日期名单，避免幸存者偏差。

## 下载与计算不变量

1. 份额接口必须读取 `pageCount` 和 `total` 并拉取全部分页。
2. 每次请求首次失败后重试3次，每次间隔1秒；仍失败则记录到质量报告。
3. 按 `(日期, 基金代码)` 去重；同键份额冲突时终止。
4. 缺失份额或价格不得填0、不得前值填充，也不得跨缺口计算差值。
5. 起始日前多取14个自然日作为预热，只用于计算窗口首日差值。
6. `tech_flag` 只表示异常候选，不能自动认定为拆分、合并、分红、数据修订或真实资金动作。
7. 先在临时目录生成文件，通过校验后原子替换正式文件；失败时不得覆盖上次成功结果。

## 缺失数据

默认数据面板最低完整覆盖率为80%，可用 `--min-coverage` 调整。低于阈值、关键字段变化、`TOT_VOL` 无法解析或完全无数据时终止。达到阈值但存在缺失时允许部分分析，状态为 `partial`；必须披露数据覆盖率和资金流覆盖率。请求结束日晚于最新披露日时，将有效截止日裁到最新披露日，同时保留请求截止日。

## 输出

默认输出：

- `sse_etf_shares_raw.csv`：原始份额；
- `sse_etf_flow_by_code.csv`：逐只ETF计算结果；
- `sse_etf_daily_detail.csv`：面向人工筛选的日度价格、份额和估算资金明细；
- `sse_etf_daily_group_summary.csv`：按日、按分组汇总资金、资产规模、广度和覆盖率；
- `sse_etf_quality.json`：运行、逐代码质量和问题明细；
- `sse_etf_analysis_manifest.json`：分析状态、数据状态、观察池版本、报告契约和信号钩子。

其中 `sse_etf_daily_detail.csv` 和 `sse_etf_daily_group_summary.csv` 是每次运行都必须生成的标准交付表。前者回答“哪一天、哪只 ETF、价格和份额如何变化”，后者回答“哪一天、哪个分组整体在流入还是流出”；即使用户只要求分析报告，也不能省略这两张表。图表不能替代它们。

用户要求拆分审计文件时使用 `--full-outputs`；此时用每日汇总、完整性、缺失和运行摘要CSV替代合并质量JSON。字段见 `schema.md`。

## 状态流转

下载成功后：`analysis_status=pending`、`signal_status=pending`。信号脚本成功后写入 `signal_status=completed` 和 `signal_path`。报告校验通过后写入 `report_path`；完整数据为 `completed`，部分数据仍为 `partial`。
