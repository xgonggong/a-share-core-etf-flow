# 输出字段与校验规则

## 默认观察池的分析角色

默认池的 `pool_bucket` 具有以下研究含义：

- `A股宽基`、`科技宽基`、`红利风格`：核心资金流向，报告正文优先分析。
- `黄金`、`国债`：风险偏好辅助信号，单列解读，不与股票ETF净额抵消。
- `港股宽基`：跨境辅助信号；当前默认池只有510900，不能代表整个港股市场。

如果用户只要求判断A股大资金方向，可重点交付前三组，并将黄金、国债和港股宽基放入辅助部分。

## 原始份额 `sse_etf_shares_raw.csv`

- `STAT_DATE`：上交所统计日期。
- `SEC_CODE`：六位基金代码。
- `TOT_VOL`：上交所公布的总份额，单位为万份。
- 其余字段按上交所接口原样保留。

## 逐只计算 `sse_etf_flow_by_code.csv`

- `date`、`code`：标准化日期和六位代码。
- `fund_name`、`index_name`：预设池中的基金名称和跟踪指数；直接使用 `--codes` 时为空。
- `asset_class`、`pool_bucket`：资产类别和观察分组；直接使用 `--codes` 时为“未分组”。
- `tot_vol_wan`：总份额，单位万份。
- `shares`：总份额，单位份。
- `share_available`、`price_available`、`row_complete`：份额、价格和整行是否可用。
- `has_prior_row`：该品种是否已有上一交易行；起始日前14个自然日作为预热区间，预热行不进入正式输出。
- `delta_shares`：与上一交易日相比的份额变化；遇到缺失日时不跨日计算。
- `pct`：份额变化率。
- `close`：上交所官方日K线接口返回的未复权收盘价。
- `amount_billion`：份额变化乘收盘价，单位亿元。
- `flow_available`：当日该品种的资金流是否可计算。缺失日后的下一交易日也可能为否。
- `tech_flag`：技术性异常候选标记，不代表已经确认异常。

## 日度明细 `sse_etf_daily_detail.csv`

这是面向人工筛选和复核的精简表，一行对应一个交易日和一只ETF，同时保留价格、份额和估算资金动作：

- `date`、`code`、`fund_name`、`index_name`、`asset_class`、`pool_bucket`：日期、品种和分组；
- `close`、`previous_close`、`price_change`、`price_change_rate`、`price_change_pct`：当日收盘价、价差、价格变化率（小数）和价格变化百分比（如 `0.285` 表示 `0.285%`）；
- `shares`、`previous_shares`、`delta_shares`、`pct`：当日总份额、上一份额、份额变化和份额变化率；
- `amount_billion`：份额变化乘当日收盘价的估算金额，单位亿元；
- `previous_assets_billion`、`flow_to_assets_pct`：估算期初资产和资金流相对期初资产比例；
- `price_share_action`：严格区分涨价/跌价与增份额/减份额，价格持平、份额不变和无法判断单列；
- `flow_available`、`tech_flag`：资金流是否可计算及技术性异常候选。

## 日度分组汇总 `sse_etf_daily_group_summary.csv`

一行对应一个交易日和一个观察分组，用于先定位整体流入流出，再回到日度明细展开具体ETF：

- `etf_count`、`positive_etfs`、`negative_etfs`、`zero_flow_etfs`：当日品种数及正、负、零资金流品种数；
- `flow_billion`：分组当日估算资金流，单位亿元；
- `close_mean`、`price_change_pct_median`：组内平均收盘价和价格变化百分比中位数；
- `assets_billion`：分组内可用于计算资金流的估算期初资产合计；
- `flow_to_assets_pct_sum`：逐只ETF资金流相对期初资产比例的简单相加，只作组内记录参考，不能用于跨规模比较；
- `flow_to_assets_pct_weighted`：分组估算资金流合计 ÷ 分组估算期初资产合计，是跨规模比较优先使用的分组资金流率；
- `tech_flag_count`、`flow_available_etfs`、`flow_coverage`：异常候选数、可计算资金流品种数和覆盖率。

面板交易日取所有请求代码观测日期的并集；每只 ETF 的应有区间按该代码首末观测日限定。该规则能识别有效区间内部“份额和价格同时缺失”的日期，并避免把上市前日期当作缺失。若某代码在整个请求区间份额和价格均无数据，脚本直接终止。

## 默认质量报告 `sse_etf_quality.json`

默认质量报告将原来的运行摘要、逐代码完整性和缺失清单合并为三个JSON节点：

- `run`：运行状态、请求开始日、请求结束日、按最新披露日调整后的有效截止日、代码来源、所选分组、ETF池版本及日期、总体覆盖率、缺失数量和阈值。
- `by_code`：每只ETF的有效区间、行数、缺失数和覆盖率。
- `issues`：请求失败、份额缺失和价格缺失明细；没有问题时为空数组。

## 分析钩子 `sse_etf_analysis_manifest.json`

每次成功生成数据都会同时写出一个分析 manifest，避免“先交表格和一句话、后续生成文档时丢失分析规范”。

- `analysis_status`：初始为 `pending`；完成完整报告后更新为 `completed`，存在数据缺口但仍交付报告时更新为 `partial`。
- `schema_version`：当前为4；报告校验器会把旧版 manifest 的报告章节迁移到当前结构。
- `data_status`：下载阶段的数据状态；报告校验时保留 `partial`，不会错误升级为 `completed`。
- `signal_status`、`signal_path`：信号挖掘阶段的状态和文件位置；报告校验前必须存在对应信号文件。
- `data_files`：本次运行实际生成的数据文件。
- `requested_start`、`requested_end`、`effective_end`：用户请求窗口和实际有效截止日。
- `pool_preset_id`、`pool_as_of`、`selected_buckets`：观察池版本和选择范围。
- `analysis_roles`：核心资金流向、风险偏好辅助、跨境辅助分组。
- `observed_buckets`、`etf_count`、`trading_day_count`：本次实际样本范围，报告只能分析已纳入分组。
- `report_expectations`：按样本规模给出的发现、图表、正文和数字证据最低要求；少量ETF或短窗口自动缩减。`image_count_min=0` 表示图表可选，`chart_recommended` 只表示长窗口建议评估，不代表必须生成。
- `required_data_outputs`：每次运行必须存在的标准数据交付物。当前至少包括 `sse_etf_daily_detail.csv` 和 `sse_etf_daily_group_summary.csv`；报告或图表不得替代它们。
- `required_comparisons`：本次样本必须覆盖的比较项；长窗口使用完整比较清单，少量ETF或短窗口使用精简清单，避免要求不存在的月度极值或持续性证据。
- `required_report_sections`：完整报告必须包含一页总结、关键发现与证据、核心资金流向、品种与价格动作、辅助信号、数据质量与口径限制及一句话结论；报告不要求把每个指标单独扩成章节。
- `report_path`：生成报告后写回报告文件名或相对于 manifest 所在输出目录的路径；仅数据模式保持为空。报告必须与 manifest 位于同一输出目录。

完整报告模板见 [`report_template.md`](report_template.md)。生成报告后应运行 `scripts/validate_analysis_report.py`，校验通过后才更新 `analysis_status` 和 `report_path`。

## 信号候选 `sse_etf_signals.json`

`mine_flow_signals.py` 生成可复核的分析候选，供报告写作和验收使用。长窗口相关字段包括：

- `daily_extreme_rankings`：每个分组及“核心股票合计”的日度流入/流出前5名，含金额、正负ETF数、主要贡献品种、`tech_flag` 数、此前5日和此后5日净流量；
- `monthly_extreme_rankings`：每个分组及“核心股票合计”的月度流入/流出前3名；
- `primary_scope`：长窗口报告的极值主分析范围；有默认核心股票组时为“核心股票合计”，指定代码或自定义池没有这些组时为实际纳入ETF最多的分组；
- `daily_extreme_points`、`monthly_extreme_points`：为兼容校验器保留的第一名摘要；
- `monthly_flow`：绘制月度资金流率热力图时可复用的完整月度流量序列；热力图的分母应使用月内平均估算期初资产，资产规模可从逐只明细重新按分组聚合。
- `bucket_summary`：分组累计金额及正流、负流、零变化记录数；分别提供“正流/全部记录”和“正流/非零记录”，不得混用分母；
- `bucket_relative_size`：分组累计流量、日均估算期初资产，以及两者的比率；用于比较不同规模分组的资金动作强度；
- `quadrant_rows`：价格和份额均严格大于或小于零时进入四象限；价格持平、份额不变和无法计算分别单列。

需要图表时由 `scripts/plot_flow_charts.py` 生成：`monthly_flow_rate_heatmap.png` 为分组月度资金流率热力图，`recent_flow_price.png` 为最近60～120个交易日资金流率与价格分面图。图表不代表实时主力资金，价格线是纳入 ETF 收盘价的等权归一化篮子，资金流率是估算资金流除以估算期初资产规模。

## 完整输出模式

只有使用 `--full-outputs` 时才生成以下四份拆分CSV。

### 每日汇总 `sse_etf_flow_daily.csv`

每日汇总按 `date`、`asset_class`、`pool_bucket` 分组，禁止用跨资产净额相互抵消。

- `expected_code_count`：当日应有品种数。
- `share_code_count`、`price_code_count`、`complete_code_count`：份额、价格、完整行的可用数量。
- `share_coverage`、`price_coverage`、`complete_coverage`：相应覆盖率。
- `flow_coverage`：在应计算差值的品种中，实际可计算资金流的覆盖率。
- `amount_available_billion`：按当日实际可计算品种汇总的金额。
- `amount_strict_billion`：仅在完整面板日给出的严格口径金额，否则为空。
- `is_complete_data_panel`：份额和价格是否构成完整数据面板。
- `is_complete_flow_panel`：所有应计算差值的品种是否均可计算资金流。

### 完整性、缺失报告与运行摘要

`sse_etf_integrity.csv` 按代码给出：

- `start`、`end`：该代码首末观测日。
- `expected_rows`：有效区间内预期交易日行数。
- `share_rows`、`price_rows`、`complete_rows`：份额、价格、完整行数量。
- `expected_flow_rows`、`flow_rows`：应计算和实际可计算资金流的行数。
- `missing_share`、`missing_close`：份额和价格缺失行数。
- `share_coverage`、`price_coverage`、`complete_coverage`、`flow_coverage`：对应覆盖率。

`sse_etf_missing_report.csv` 的 `issue_type` 包括 `request_failure`、`missing_share`、`missing_close`。

`sse_etf_run_summary.csv` 给出 `status`、数据面板总覆盖率、资金流总覆盖率、各类缺失数量和最低数据面板覆盖率阈值。`partial` 表示数据仍可分析，但结论必须披露覆盖范围。

## 终止条件

- 数据面板完整覆盖率低于 `--min-coverage`；
- 上交所关键字段发生结构性变化；
- `TOT_VOL` 无法解析；
- 没有取得任何份额数据。

终止时不得覆盖上一次成功生成的正式结果。
