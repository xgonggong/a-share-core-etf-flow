# A股核心宽基ETF资金流

这是一个可供支持 Skill 工作流的智能体或自动化工具集成的数据分析 Skill。它从上海证券交易所获取 ETF 日终份额和官方未复权收盘价，计算 ETF 一级市场份额变化对应的估算净申购和净赎回，并按核心宽基、科技、红利、黄金、国债和港股宽基等分组提供分析数据。

本项目主要用于观察 A 股核心 ETF 的资金配置方向、风格切换、同步申赎和价格与资金动作的关系。它只处理上交所 ETF 份额，不处理深交所 ETF 份额，也不提供场内成交额、盘口资金或实时净值数据。

## 功能

- 按日期区间下载上交所 ETF 日终份额；
- 下载上交所官方未复权日 K 线收盘价；
- 计算份额变化、份额变化率和估算净申购/净赎回金额；
- 支持默认观察池、版本化自定义池和指定 ETF 代码；
- 支持按观察分组筛选 ETF；
- 处理接口分页、请求重试、重复记录、缺失数据和有效截止日；
- 使用起始日前预热数据计算窗口首日份额变化；
- 输出逐只 ETF、每日分组和完整性检查文件；
- 生成分析 manifest，保证后续分析报告沿用同一数据窗口和分析规范。
- 从逐只资金流文件提取资金广度、集中度、持续性、组间相对流量和价格—份额象限等数据发现候选。

## 适用请求

以下请求通常适合使用本 Skill：

- 下载或拉取上交所 ETF 份额数据；
- 分析上交所 ETF 申购赎回和资金流向；
- 使用份额变化乘收盘价估算资金流入流出；
- 分析沪深300、科创50、红利 ETF 的资金配置；
- 比较核心宽基 ETF 的大额申购、赎回和价格变化；
- 找出 ETF 份额大幅增加或减少的日期。

在支持显式 Skill 调用的客户端中，可以使用以下技能名：

```text
a-share-core-etf-flow
```

## 快速开始

需要 Python 3.10 或更高版本。安装依赖：

```powershell
python -m pip install -r requirements.txt
```

在 Skill 目录下运行默认观察池：

```powershell
python scripts/download_sse_etf_data.py `
  --start 2026-08-14 `
  --end 2026-10-14 `
  --output-dir .\outputs\sse_etf_2m `
  --full-outputs
```

按分组运行：

```powershell
python scripts/download_sse_etf_data.py `
  --start 2026-01-01 `
  --end 2026-10-14 `
  --buckets A股宽基,科技宽基,红利风格 `
  --output-dir .\outputs\core_equity
```

指定 ETF：

```powershell
python scripts/download_sse_etf_data.py `
  --start 2026-01-01 `
  --end 2026-10-14 `
  --codes 510300,588000,510880 `
  --output-dir .\outputs\selected
```

使用自定义观察池：

```powershell
python scripts/download_sse_etf_data.py `
  --start 2026-01-01 `
  --end 2026-10-14 `
  --pool-file .\references\my_pool.json `
  --output-dir .\outputs\custom
```

`--codes`、`--pool-file` 和 `--buckets` 互斥。默认池和自定义池都需要包含 `preset_id`、`as_of`，以及每只基金的代码、名称、指数、观察分组和资产类别。

## 默认观察池

默认观察池是本项目的研究口径，不是上交所发布的官方名单。配置文件见 [`references/default_observation_pool.json`](references/default_observation_pool.json)，当前不纳入具体行业 ETF。

默认池按以下角色解读：

| 角色 | 分组 | 用途 |
| --- | --- | --- |
| 核心资金流向 | A股宽基、科技宽基、红利风格 | 判断 A 股核心 ETF 的申购赎回、风格切换和同步大额动作 |
| 风险偏好辅助 | 黄金、国债 | 观察避险或防御配置，单独解读 |
| 跨境辅助信号 | 港股宽基 | 观察跨境资产；当前默认池主要是510900，不能代表整个港股市场 |

如果研究目标是 A 股大资金方向，报告正文优先分析前三组，黄金、国债和港股宽基放在辅助部分。不同资产类别不能合并成一个“总资金流”。

## 数据来源和计算方法

份额数据来自上交所 `commonQuery.do`，使用 SQL ID：

```text
COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L
```

收盘价来自上交所官方日 K 线接口：

```text
https://yunhq.sse.com.cn:32042/v1/sh1/dayk/{代码}
```

计算公式如下：

```text
份额 = TOT_VOL × 10000
份额变化 = 当日份额 - 上一交易日份额
估算金额（亿元） = 份额变化 × 当日未复权收盘价 ÷ 100000000
```

正值表示估算净申购，负值表示估算净赎回。接口字段 `TOT_VOL` 的单位是万份，不能直接当作份数使用。

为计算窗口首日的份额变化，脚本会在用户起始日前额外下载14个自然日作为预热区间。预热数据只用于计算，不进入正式输出。

## 分析要求

当用户要求生成分析报告时，报告至少要能回答五件事：

- 本次实际纳入的核心组整体是增配、减配、结构性切换，还是分化；
- 最大买入和最大卖出是多只 ETF 同步，还是由单只产品主导；
- 日度流入/流出前5名、月度流入/流出前3名分别是什么；最大日的前后5日是延续还是孤立跳变；
- 价格与份额是追涨、逢跌配置、上涨兑现还是风险收缩；
- 数据覆盖和估算口径是否足以支持上述判断。

这些判断必须用实际纳入核心组的累计流量、相对规模、广度、集中度、持续性、主要 ETF 贡献和价格—份额动作支撑；“正流比例”需要说明是除以全部记录还是非零申赎记录。黄金、国债和港股宽基只作辅助说明，不得喧宾夺主。指定少量ETF或短窗口时，报告按 manifest 中的范围和最低要求缩减。

完整报告不是指标清单。生成报告时应先对完整窗口做一次数据挖掘，再选择 3～4 个最有证据的发现，用“事实—解释—限制”讲清楚它们的含义。优先回答：资金是否广泛同步、是否由单只 ETF 主导、实际纳入的核心组是否发生替代、流入或流出是否持续、异常日后是否反转、价格下跌时是否仍在增份额。金额、相对规模流量、同步性和持续性必须交叉验证；只有一个指标支持的结论应标为弱信号，数据无法支持时应明确写“未发现稳定信号”。短窗口或少量ETF只回答 manifest 精简清单中的问题，不强行补写月度极值、持续性或组间替代。

报告开头先给一页通俗总结，正文用 3～4 张数据发现卡解释“发生了什么、这可能意味着什么、不能推出什么”，结尾用一句话概括样本期资金风格。报告以结论为主，表格和图表只保留能支撑结论的证据，不能代替解释段落。

可用以下命令生成分析中间产物：

```powershell
python scripts/mine_flow_signals.py `
  --flow .\outputs\sse_etf_flow_by_code.csv `
  --output .\outputs\sse_etf_signals.json
```

价格与资金动作至少区分以下四种情况：

| 价格变化 | 份额变化 | 观察含义 |
| --- | --- | --- |
| 上涨 | 增加 | 可能是顺势申购或新增配置 |
| 下跌 | 增加 | 可能是逆向申购或逢低配置 |
| 上涨 | 减少 | 可能是获利赎回或资金兑现 |
| 下跌 | 减少 | 可能是风险收缩或赎回压力 |

这些关系用于描述现象，不能单独证明因果关系。

## 输出文件

默认输出：

- `sse_etf_shares_raw.csv`：上交所原始份额字段；
- `sse_etf_flow_by_code.csv`：逐只 ETF 的份额、价格、份额变化、估算金额和 `tech_flag`；
- `sse_etf_daily_detail.csv`：一行一只ETF一天，合并收盘价、价格变化、份额变化、估算金额、资金流率和价格—份额动作，适合直接筛选极端日期和品种；
- `sse_etf_daily_group_summary.csv`：一行一个分组一天，汇总当天流量、正负ETF数量、价格变化和覆盖率，适合先定位整体动作日期；
- `sse_etf_quality.json`：运行摘要、逐代码完整性和问题明细；
- `sse_etf_analysis_manifest.json`：后续分析钩子，记录有效窗口、池版本、分析角色和必做分析清单。

其中前两张按日表是每次运行都必须生成的标准交付物。分组汇总表同时提供分组估算期初资产和加权资金流率，跨规模比较优先使用加权资金流率，不要把各只 ETF 的百分比简单相加。月度资金流率图使用“月度资金流合计 ÷ 月内平均估算期初资产”。两张表把资金和价格放到同一观察粒度，便于直接筛选某日、某只 ETF 或某个分组；图表只作概览，不能替代逐日表。

图表是可选交付物，不为凑数量强行生成。需要图表时，长窗口优先生成 `monthly_flow_rate_heatmap.png`（分组月度资金流率热力图）和 `recent_flow_price.png`（最近60～120个交易日资金流率与价格分面图）。这样可以比较不同规模分组的资金强弱，也能观察近期资金动作是否与价格同步。价格线是归一化观察指标，资金仍是份额变化乘收盘价的估算值，不等同于实时主力资金。

生成完整分析报告时还会产生 `sse_etf_signals.json`，它保存资金广度、集中度、持续性、日/月极值榜和价格—份额象限等候选信号，属于分析中间产物，不替代报告。

使用 `--full-outputs` 时，在上述明细表基础上额外生成以下拆分CSV：

- `sse_etf_flow_daily.csv`：按日期、资产类别和观察分组汇总；
- `sse_etf_integrity.csv`：逐只 ETF 的行数、缺失数和覆盖率；
- `sse_etf_missing_report.csv`：请求失败、份额缺失和价格缺失明细；
- `sse_etf_run_summary.csv`：运行状态、有效截止日、总体覆盖率和缺失统计。

字段定义见 [`references/schema.md`](references/schema.md)。

## 数据质量和异常处理

脚本会：

- 按接口 `pageCount` 拉取全部分页，并校验返回数量；
- 单次请求失败后重试3次，每次间隔1秒；
- 不用0填充缺失份额，不以前值填充，也不跨缺失日期计算差值；
- 默认要求份额和价格同时可用的面板覆盖率至少为80%，可用 `--min-coverage` 调整；
- 请求结束日晚于最新份额披露日时，记录 `requested_end` 和 `effective_end`；
- 对大幅跳变设置 `tech_flag`，但不自动认定为拆分、合并、分红、修订或真实资金流；
- 生成失败时不覆盖上一次成功的正式结果。

## 数据模式与分析模式

Skill 支持两种交付方式：

1. **仅数据模式**：输出数据文件、质量报告和 manifest。manifest 的 `analysis_status` 为 `pending`，后续可以据此生成报告。
2. **数据加分析模式**：用户要求“分析”“比较”“判断资金方向”或“生成报告”时，先读取 manifest，再按其中记录的窗口、观察池版本、比较项目和报告章节生成完整报告。报告完成后将 `analysis_status` 更新为 `completed` 或 `partial`，并写入 `report_path`。

后续单独要求“生成文档”时，应重新读取 CSV、质量文件和 manifest，不能只根据之前的一段摘要扩写。

完整报告必须遵循 [`references/report_template.md`](references/report_template.md)，先使用 `scripts/validate_analysis_report.py` 做结构验收，再按 [`references/report_review_checklist.md`](references/report_review_checklist.md) 做内容复核。报告应与 `sse_etf_analysis_manifest.json` 保存在同一输出目录；任一验收失败时不得交付报告。

报告验收还会检查 manifest 声明的标准数据交付物是否存在；缺少 `sse_etf_daily_detail.csv` 或 `sse_etf_daily_group_summary.csv` 时，即使报告正文完整也不能通过。图表只有在正文中以 Markdown 图片语法嵌入才算交付；不需要图表时不要生成或引用 PNG。

## 结果边界

本项目估算的是 **ETF 一级市场份额变化对应的净申购/净赎回**。它不等于 ETF 场内成交资金、不等于基金精确 NAV，也不等于 A 股全市场资金流。

收盘价替代 NAV 会产生估算误差。黄金、国债、港股和境内股票应分开解释，不能相互抵消。`tech_flag` 只是异常候选，确认拆分、合并、分红或数据修订仍需核对基金公告。

## 目录结构

```text
a-share-core-etf-flow/
├── .gitignore
├── SKILL.md
├── README.md
├── requirements.txt
├── references/
│   ├── default_observation_pool.json
│   ├── analysis_method.md
│   ├── data_pipeline.md
│   ├── report_contract.json
│   ├── report_review_checklist.md
│   ├── report_template.md
│   └── schema.md
└── scripts/
    ├── download_sse_etf_data.py
    ├── mine_flow_signals.py
    ├── plot_flow_charts.py
    └── validate_analysis_report.py
```

## 本地校验

可以使用以下命令检查 Python 脚本语法：

```powershell
python -m py_compile scripts\download_sse_etf_data.py scripts\mine_flow_signals.py scripts\validate_analysis_report.py
```

同时建议检查 `references/default_observation_pool.json` 是否为有效 JSON，并确认输出目录中包含 `sse_etf_analysis_manifest.json`。

## 数据责任

本目录提供代码、配置和研究口径。上交所接口、ETF 产品和指数的权利归各自提供方所有。使用者应遵守相关网站服务条款，并在发布研究结果时注明数据来源、查询区间、默认池版本和估算方法。
