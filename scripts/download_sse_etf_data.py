"""下载并计算上交所 ETF 日终份额数据。

说明：上交所份额接口返回 TOT_VOL，单位为万份；收盘价来自上交所官方日K线接口。
"""
import argparse
import datetime as dt
import json
import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import requests

SSE_URL = "https://query.sse.com.cn/commonQuery.do"
SQL_ID = "COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L"
SSE_PRICE_URL = "https://yunhq.sse.com.cn:32042/v1/sh1/dayk/{code}"
DEFAULT_POOL_PATH = (
    Path(__file__).resolve().parents[1] / "references" / "default_observation_pool.json"
)
REPORT_CONTRACT_PATH = (
    Path(__file__).resolve().parents[1] / "references" / "report_contract.json"
)


def load_pool(path):
    """加载并校验版本化ETF池配置。"""
    pool_path = Path(path)
    try:
        payload = json.loads(pool_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"ETF池配置不存在：{pool_path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"ETF池配置不是有效JSON：{pool_path}，{exc}") from exc
    funds = payload.get("funds")
    if not isinstance(funds, list) or not funds:
        raise ValueError("ETF池配置必须包含非空 funds 数组")
    codes = []
    fund_meta = {}
    for index, fund in enumerate(funds, start=1):
        code = str(fund.get("code", "")).strip()
        if len(code) != 6 or not code.isdigit():
            raise ValueError(f"ETF池第 {index} 项代码无效：{code!r}")
        bucket = str(fund.get("bucket", "")).strip()
        asset_class = str(fund.get("asset_class", "")).strip()
        if not bucket or not asset_class:
            raise ValueError(f"ETF池第 {index} 项必须包含 bucket 和 asset_class")
        codes.append(code)
        fund_meta[code] = {
            "fund_name": str(fund.get("name", "")).strip(),
            "index_name": str(fund.get("index", "")).strip(),
            "pool_bucket": bucket,
            "asset_class": asset_class,
        }
    if len(codes) != len(set(codes)):
        raise ValueError("ETF池配置包含重复代码")
    preset_id = str(payload.get("preset_id", "")).strip()
    as_of = str(payload.get("as_of", "")).strip()
    if not preset_id or not as_of:
        raise ValueError("ETF池配置必须包含 preset_id 和 as_of")
    try:
        dt.date.fromisoformat(as_of)
    except ValueError as exc:
        raise ValueError("ETF池配置的 as_of 必须是 YYYY-MM-DD") from exc
    return set(codes), {
        "preset_id": preset_id,
        "as_of": as_of,
        "fund_meta": fund_meta,
    }


def load_report_contract():
    """加载报告契约，避免下载器与校验器各自维护章节清单。"""
    return json.loads(REPORT_CONTRACT_PATH.read_text(encoding="utf-8"))


def fetch_one(day, page_size=1000, timeout=20, retries=3):
    last_error = None
    for attempt in range(retries + 1):
        try:
            page_no = 1
            rows = []
            expected_total = None
            while True:
                params = {
                    "jsonCallBack": "x", "isPagination": "true",
                    "pageHelp.pageSize": page_size, "pageHelp.pageNo": page_no,
                    "pageHelp.beginPage": page_no, "pageHelp.cacheSize": 1,
                    "pageHelp.endPage": page_no, "sqlId": SQL_ID,
                    "STAT_DATE": day.isoformat(),
                }
                response = requests.get(
                    SSE_URL, params=params,
                    headers={"Referer": "https://www.sse.com.cn/"},
                    timeout=timeout,
                )
                response.raise_for_status()
                text = response.text.strip()
                if not (text.startswith("x(") and text.endswith(")")):
                    raise RuntimeError(f"上交所接口返回格式异常：{day}")
                payload = json.loads(text[2:-1])
                if payload.get("success") == "false":
                    raise RuntimeError(payload.get("error") or "上交所接口返回失败")
                page_help = payload.get("pageHelp", {})
                page_rows = page_help.get("data", [])
                if not isinstance(page_rows, list):
                    raise RuntimeError(f"上交所接口分页数据结构异常：{day}")
                rows.extend(page_rows)
                page_count = int(page_help.get("pageCount") or 1)
                expected_total = int(page_help.get("total") or len(rows))
                if page_no >= page_count:
                    break
                page_no += 1
            if len(rows) < expected_total:
                raise RuntimeError(
                    f"上交所接口分页不完整：{day}，应有{expected_total}条，实得{len(rows)}条"
                )
            return rows
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                print(f"份额请求失败，准备重试 {attempt + 1}/{retries}：{day}，原因：{exc}")
                time.sleep(1)
    raise RuntimeError(f"日期 {day} 首次请求及 {retries} 次重试均失败：{last_error}")


def get_prices(codes, start, end, retries=3):
    """从上交所官方日K线接口获取未复权收盘价；失败后重试，仍失败则留空。"""
    frames = []
    for code in codes:
        last_error = None
        for attempt in range(retries + 1):
            try:
                response = requests.get(
                    SSE_PRICE_URL.format(code=code),
                    params={"callback": "x", "begin": 0, "end": -1, "period": "day"},
                    headers={"Referer": "https://www.sse.com.cn/"},
                    timeout=20,
                )
                response.raise_for_status()
                text = response.text.strip()
                if not (text.startswith("x(") and text.endswith(")")):
                    raise RuntimeError("上交所行情接口返回格式异常")
                payload = json.loads(text[2:-1])
                kline = payload.get("kline")
                if not isinstance(kline, list) or not kline:
                    raise RuntimeError("上交所行情接口未返回日K线")
                if any(not isinstance(row, list) or len(row) < 5 for row in kline):
                    raise RuntimeError("上交所行情接口日K线字段结构异常")
                frame = pd.DataFrame({
                    "date": [row[0] for row in kline],
                    "close": [row[4] for row in kline],
                })
                frame["date"] = pd.to_datetime(
                    frame["date"].astype(str), format="%Y%m%d", errors="coerce"
                )
                frame["code"] = code
                frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
                if frame["date"].isna().any() or frame["close"].isna().any():
                    raise RuntimeError("上交所行情接口日期或收盘价无法解析")
                frames.append(frame[["date", "code", "close"]])
                break
            except Exception as exc:
                last_error = exc
                if attempt < retries:
                    print(f"价格下载失败，准备重试 {attempt + 1}/{retries}：{code}，原因：{exc}")
                    time.sleep(1)
        else:
            print(f"价格下载失败 {code}，首次请求及 {retries} 次重试均失败：{last_error}")
    if not frames:
        return pd.DataFrame(columns=["date", "code", "close"])
    prices = pd.concat(frames, ignore_index=True)
    return prices[(prices.date >= pd.Timestamp(start)) & (prices.date <= pd.Timestamp(end))]


def main():
    parser = argparse.ArgumentParser(description="下载上交所 ETF 份额并计算估算资金流")
    parser.add_argument("--start", required=True, help="开始日期 YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="结束日期 YYYY-MM-DD")
    selection_group = parser.add_mutually_exclusive_group()
    selection_group.add_argument(
        "--codes",
        help="ETF代码，逗号分隔",
    )
    selection_group.add_argument(
        "--pool-file",
        help="自定义ETF池JSON；与--codes、--buckets互斥",
    )
    selection_group.add_argument(
        "--buckets",
        help="从默认观察池选择分组，逗号分隔，如A股宽基,红利风格",
    )
    parser.add_argument("--output-dir", required=True, help="输出目录")
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument(
        "--full-outputs", action="store_true",
        help="以拆分CSV输出每日汇总、完整性、缺失和运行摘要，替代合并质量JSON",
    )
    parser.add_argument(
        "--min-coverage", type=float, default=0.80,
        help="允许输出部分数据的最低数据面板完整覆盖率，默认0.80",
    )
    args = parser.parse_args()
    start, end = dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end)
    if start > end:
        raise ValueError("开始日期不能晚于结束日期")
    if not 0 < args.min_coverage <= 1:
        raise ValueError("--min-coverage 必须在0到1之间")
    pool_meta = {"preset_id": None, "as_of": None, "fund_meta": {}}
    selected_buckets = None
    if args.codes:
        code_selection = "specified_codes"
        codes = {c.strip().zfill(6) for c in args.codes.split(",") if c.strip()}
    elif args.buckets:
        codes, pool_meta = load_pool(DEFAULT_POOL_PATH)
        requested_buckets = {
            value.strip() for value in args.buckets.split(",") if value.strip()
        }
        available_buckets = {
            values["pool_bucket"] for values in pool_meta["fund_meta"].values()
        }
        unknown_buckets = sorted(requested_buckets - available_buckets)
        if unknown_buckets:
            raise ValueError(
                "未知观察分组：" + ",".join(unknown_buckets)
                + "；可选分组：" + ",".join(sorted(available_buckets))
            )
        codes = {
            code for code in codes
            if pool_meta["fund_meta"][code]["pool_bucket"] in requested_buckets
        }
        pool_meta["fund_meta"] = {
            code: values for code, values in pool_meta["fund_meta"].items()
            if code in codes
        }
        selected_buckets = ",".join(sorted(requested_buckets))
        code_selection = "default_preset_buckets"
    else:
        pool_path = Path(args.pool_file) if args.pool_file else DEFAULT_POOL_PATH
        codes, pool_meta = load_pool(pool_path)
        code_selection = "custom_pool" if args.pool_file else "default_preset"
    if not codes:
        raise ValueError("至少需要指定一个ETF代码")
    invalid_codes = sorted(code for code in codes if len(code) != 6 or not code.isdigit())
    if invalid_codes:
        raise ValueError("ETF代码必须是六位数字：" + ",".join(invalid_codes))
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    # 多取14个自然日作为预热区间，确保请求窗口首个交易日可以与上一交易日比较。
    query_start = start - dt.timedelta(days=14)
    days = [
        query_start + dt.timedelta(days=i)
        for i in range((end - query_start).days + 1)
    ]
    rows = []
    request_errors = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch_one, day): day for day in days}
        for future in as_completed(futures):
            day = futures[future]
            try:
                result = future.result()
                rows.extend([r for r in result if str(r.get("SEC_CODE", "")).zfill(6) in codes])
            except Exception as exc:
                request_errors.append({
                    "date": day.isoformat(), "code": "", "issue_type": "request_failure",
                    "detail": str(exc),
                })
    raw_all = pd.DataFrame(rows)
    if raw_all.empty:
        raise RuntimeError("没有获取到上交所份额数据")
    required_columns = {"STAT_DATE", "SEC_CODE", "TOT_VOL"}
    missing_columns = required_columns - set(raw_all.columns)
    if missing_columns:
        raise RuntimeError(f"上交所接口字段发生结构性变化，缺少：{sorted(missing_columns)}")
    raw_all["SEC_CODE"] = raw_all["SEC_CODE"].astype(str).str.zfill(6)
    raw_all["_stat_date"] = pd.to_datetime(raw_all["STAT_DATE"], errors="coerce")
    if raw_all["_stat_date"].isna().any():
        raise RuntimeError("STAT_DATE存在无法解析的日期")
    # 保留未去重副本，便于在确定有效截止日后只统计正式请求窗口的重复记录。
    raw_with_duplicates = raw_all.copy()
    duplicate_groups = raw_all[
        raw_all.duplicated(["STAT_DATE", "SEC_CODE"], keep=False)
    ]
    if not duplicate_groups.empty:
        conflicts = duplicate_groups.groupby(["STAT_DATE", "SEC_CODE"])["TOT_VOL"].nunique()
        conflicts = conflicts[conflicts > 1]
        if not conflicts.empty:
            raise RuntimeError("同一日期和代码存在冲突份额记录：\n" + conflicts.head(20).to_string())
    raw_all = raw_all.sort_values(["SEC_CODE", "STAT_DATE"]).drop_duplicates(
        ["STAT_DATE", "SEC_CODE"]
    )
    latest_share_date = raw_all["_stat_date"].max().date()
    effective_end = min(end, latest_share_date)
    if effective_end < start:
        raise RuntimeError(
            f"请求窗口内没有已披露份额数据；最新可用日期为 {latest_share_date}"
        )
    raw = raw_all[
        raw_all["_stat_date"].between(pd.Timestamp(start), pd.Timestamp(effective_end))
    ].copy()
    duplicate_count = int(
        raw_with_duplicates[
            raw_with_duplicates["_stat_date"].between(
                pd.Timestamp(start), pd.Timestamp(effective_end)
            )
        ].duplicated(["STAT_DATE", "SEC_CODE"]).sum()
    )
    if raw.empty:
        raise RuntimeError("请求窗口内没有取得份额数据")
    raw = raw.drop(columns=["_stat_date"])
    data = raw_all[
        raw_all["_stat_date"] <= pd.Timestamp(effective_end)
    ].drop(columns=["_stat_date"]).copy()
    data["date"] = pd.to_datetime(data["STAT_DATE"])
    data["code"] = data["SEC_CODE"]
    data["tot_vol_wan"] = pd.to_numeric(data["TOT_VOL"], errors="coerce")
    if data["tot_vol_wan"].isna().any():
        bad = data.loc[data["tot_vol_wan"].isna(), ["STAT_DATE", "SEC_CODE", "TOT_VOL"]]
        raise RuntimeError("TOT_VOL存在无法解析的结构性错误：\n" + bad.head(20).to_string(index=False))
    data["shares"] = data["tot_vol_wan"] * 10000
    prices = get_prices(sorted(codes), query_start, effective_end)
    prices = prices.drop_duplicates(["date", "code"])
    requested_prices = prices[prices["date"] >= pd.Timestamp(start)]
    observed_codes = set(raw["SEC_CODE"]) | set(requested_prices["code"])
    codes_without_data = sorted(codes - observed_codes)
    if codes_without_data:
        raise RuntimeError(
            "以下请求代码在整个区间内既无份额也无价格数据，请检查代码或日期范围："
            + ",".join(codes_without_data)
        )

    # 用所有观测到的交易日建立稠密面板，并按各代码的首末观测日限定有效区间。
    # 这样能识别某只 ETF 在有效区间内份额和价格同时缺失的日期，同时避免把上市前日期算作缺失。
    observed_keys = pd.concat(
        [data[["date", "code"]], prices[["date", "code"]]], ignore_index=True
    ).drop_duplicates()
    master_dates = sorted(observed_keys["date"].unique())
    active_ranges = observed_keys.groupby("code")["date"].agg(["min", "max"])
    keys = pd.concat(
        [
            pd.DataFrame({
                "date": [day for day in master_dates if bounds["min"] <= day <= bounds["max"]],
                "code": code,
            })
            for code, bounds in active_ranges.iterrows()
        ],
        ignore_index=True,
    )
    data = keys.merge(data, on=["date", "code"], how="left")
    data = data.merge(prices, on=["date", "code"], how="left")
    data = data.sort_values(["code", "date"])
    for column in ["fund_name", "index_name", "pool_bucket", "asset_class"]:
        mapping = {
            code: values[column] for code, values in pool_meta["fund_meta"].items()
        }
        data[column] = data["code"].map(mapping)
    data["pool_bucket"] = data["pool_bucket"].fillna("未分组")
    data["asset_class"] = data["asset_class"].fillna("未分组")
    data["share_available"] = data["shares"].notna()
    data["price_available"] = data["close"].notna()
    data["row_complete"] = data["share_available"] & data["price_available"]
    data["has_prior_row"] = data.groupby("code").cumcount().gt(0)

    # 面板中保留缺失行。diff遇到缺失会返回NaN，避免把跨缺失日的多日变化错算到下一天。
    data["previous_shares"] = data.groupby("code")["shares"].shift()
    data["previous_close"] = data.groupby("code")["close"].shift()
    data["delta_shares"] = data.groupby("code")["shares"].diff()
    data["pct"] = data["delta_shares"] / data.groupby("code")["shares"].shift()
    data["price_change"] = data["close"] - data["previous_close"]
    data["price_change_rate"] = data["price_change"] / data["previous_close"].abs()
    data["price_change_pct"] = data["price_change_rate"] * 100
    data["amount_billion"] = data["delta_shares"] * data["close"] / 100000000
    data["previous_assets_billion"] = data["previous_shares"] * data["previous_close"] / 100000000
    data["flow_to_assets_pct"] = (
        data["amount_billion"] / data["previous_assets_billion"].abs() * 100
    )
    data["flow_available"] = data["amount_billion"].notna()
    data["tech_flag"] = (data["pct"].abs() > 0.2) & (data["delta_shares"].abs() > 1000000000)
    data["price_share_action"] = "无法判断"
    valid_action = data["price_change"].notna() & data["pct"].notna()
    data.loc[valid_action & data["pct"].eq(0), "price_share_action"] = "份额不变"
    data.loc[valid_action & data["price_change"].eq(0) & data["pct"].gt(0), "price_share_action"] = "价格持平增份额"
    data.loc[valid_action & data["price_change"].eq(0) & data["pct"].lt(0), "price_share_action"] = "价格持平减份额"
    for price_up, share_up, label in [
        (True, True, "涨价增份额"), (False, True, "跌价增份额"),
        (True, False, "涨价减份额"), (False, False, "跌价减份额"),
    ]:
        price_mask = data["price_change"].gt(0) if price_up else data["price_change"].lt(0)
        share_mask = data["pct"].gt(0) if share_up else data["pct"].lt(0)
        data.loc[valid_action & price_mask & share_mask, "price_share_action"] = label

    # 预热数据只用于计算窗口首日差值，不进入正式输出或覆盖率统计。
    data = data[
        data["date"].between(pd.Timestamp(start), pd.Timestamp(effective_end))
    ].copy()

    missing_rows = [
        issue for issue in request_errors
        if start <= dt.date.fromisoformat(issue["date"]) <= effective_end
    ]
    for row in data.loc[~data["share_available"], ["date", "code"]].itertuples(index=False):
        missing_rows.append({"date": row.date.date().isoformat(), "code": row.code,
                             "issue_type": "missing_share", "detail": "交易日缺少份额记录"})
    for row in data.loc[~data["price_available"], ["date", "code"]].itertuples(index=False):
        missing_rows.append({"date": row.date.date().isoformat(), "code": row.code,
                             "issue_type": "missing_close", "detail": "缺少未复权收盘价"})
    missing_report = pd.DataFrame(
        missing_rows, columns=["date", "code", "issue_type", "detail"]
    )

    daily = data.groupby(["date", "asset_class", "pool_bucket"], as_index=False).agg(
        expected_code_count=("code", "nunique"),
        share_code_count=("share_available", "sum"),
        price_code_count=("price_available", "sum"),
        complete_code_count=("row_complete", "sum"),
        expected_flow_code_count=("has_prior_row", "sum"),
        flow_code_count=("flow_available", "sum"),
        tech_flag_count=("tech_flag", "sum"),
    )
    available_amount = data.groupby(
        ["date", "asset_class", "pool_bucket"]
    )["amount_billion"].sum(min_count=1)
    daily = daily.merge(
        available_amount.rename("amount_available_billion"),
        on=["date", "asset_class", "pool_bucket"], how="left",
    )
    daily["share_coverage"] = daily["share_code_count"] / daily["expected_code_count"]
    daily["price_coverage"] = daily["price_code_count"] / daily["expected_code_count"]
    daily["complete_coverage"] = daily["complete_code_count"] / daily["expected_code_count"]
    daily["flow_coverage"] = (
        daily["flow_code_count"] / daily["expected_flow_code_count"].replace(0, pd.NA)
    )
    daily["is_complete_data_panel"] = daily["complete_code_count"].eq(daily["expected_code_count"])
    daily["is_complete_flow_panel"] = (
        daily["expected_flow_code_count"].gt(0)
        & daily["flow_code_count"].eq(daily["expected_flow_code_count"])
    )
    daily["amount_strict_billion"] = daily["amount_available_billion"].where(
        daily["is_complete_data_panel"] & daily["is_complete_flow_panel"]
    )

    coverage = data.groupby("code").agg(
        expected_rows=("date", "size"), start=("date", "min"), end=("date", "max"),
        share_rows=("share_available", "sum"), price_rows=("price_available", "sum"),
        complete_rows=("row_complete", "sum"), expected_flow_rows=("has_prior_row", "sum"),
        flow_rows=("flow_available", "sum"),
        missing_share=("shares", lambda s: int(s.isna().sum())),
        missing_close=("close", lambda s: int(s.isna().sum())),
    ).reset_index()
    coverage["share_coverage"] = coverage["share_rows"] / coverage["expected_rows"]
    coverage["price_coverage"] = coverage["price_rows"] / coverage["expected_rows"]
    coverage["complete_coverage"] = coverage["complete_rows"] / coverage["expected_rows"]
    coverage["flow_coverage"] = (
        coverage["flow_rows"] / coverage["expected_flow_rows"].replace(0, pd.NA)
    )
    coverage["fund_name"] = coverage["code"].map(
        {code: values["fund_name"] for code, values in pool_meta["fund_meta"].items()}
    )
    coverage["pool_bucket"] = coverage["code"].map(
        {code: values["pool_bucket"] for code, values in pool_meta["fund_meta"].items()}
    ).fillna("未分组")
    coverage["asset_class"] = coverage["code"].map(
        {code: values["asset_class"] for code, values in pool_meta["fund_meta"].items()}
    ).fillna("未分组")
    overall_coverage = float(data["row_complete"].mean())
    overall_expected_flow_rows = int(data["has_prior_row"].sum())
    overall_flow_rows = int(data["flow_available"].sum())
    overall_flow_coverage = (
        overall_flow_rows / overall_expected_flow_rows
        if overall_expected_flow_rows else float("nan")
    )
    if overall_coverage < args.min_coverage:
        raise RuntimeError(
            f"数据面板完整覆盖率仅 {overall_coverage:.2%}，低于阈值 "
            f"{args.min_coverage:.2%}；已终止，不覆盖正式结果。"
        )

    # 只有正式窗口的数据面板和应计算资金流都完整时才标记 complete。
    # 非交易日请求失败不改变状态，但仍在 issues 中披露。
    formal_request_failure_count = sum(
        1 for issue in request_errors
        if start <= dt.date.fromisoformat(issue["date"]) <= effective_end
    )
    flow_panel_complete = bool(
        (~data["has_prior_row"] | data["flow_available"]).all()
    )
    run_status = (
        "complete"
        if bool(data["row_complete"].all()) and flow_panel_complete
        else "partial"
    )
    summary = pd.DataFrame([{
        "status": run_status, "start": args.start,
        "requested_end": args.end, "effective_end": effective_end.isoformat(),
        "code_selection": code_selection,
        "pool_preset_id": pool_meta["preset_id"],
        "pool_as_of": pool_meta["as_of"],
        "selected_buckets": selected_buckets,
        "requested_codes": ",".join(sorted(codes)), "raw_rows": len(raw),
        "panel_rows": len(data), "overall_coverage": overall_coverage,
        "overall_flow_coverage": overall_flow_coverage,
        "request_failure_count": formal_request_failure_count,
        "duplicate_identical_count": duplicate_count,
        "missing_share_count": int((~data["share_available"]).sum()),
        "missing_close_count": int((~data["price_available"]).sum()),
        "flow_unavailable_count": int((data["has_prior_row"] & ~data["flow_available"]).sum()),
        "min_coverage_threshold": args.min_coverage,
    }])

    # 所有文件先在临时目录完整生成，再逐文件原子替换正式文件。
    detail_columns = [
        "date", "code", "fund_name", "index_name", "asset_class", "pool_bucket",
        "close", "previous_close", "price_change", "price_change_rate", "price_change_pct",
        "shares", "previous_shares", "delta_shares", "pct",
        "amount_billion", "previous_assets_billion", "flow_to_assets_pct",
        "price_share_action", "flow_available", "tech_flag",
    ]
    daily_detail = data[detail_columns].copy()
    daily_group_summary = data.groupby(
        ["date", "asset_class", "pool_bucket"], as_index=False
    ).agg(
        etf_count=("code", "nunique"),
        positive_etfs=("amount_billion", lambda s: int((s > 0).sum())),
        negative_etfs=("amount_billion", lambda s: int((s < 0).sum())),
        zero_flow_etfs=("amount_billion", lambda s: int((s == 0).sum())),
        flow_billion=("amount_billion", "sum"),
        assets_billion=("previous_assets_billion", "sum"),
        close_mean=("close", "mean"),
        price_change_pct_median=("price_change_pct", "median"),
        flow_to_assets_pct_sum=("flow_to_assets_pct", "sum"),
        tech_flag_count=("tech_flag", "sum"),
        flow_available_etfs=("flow_available", "sum"),
    )
    daily_group_summary["flow_coverage"] = (
        daily_group_summary["flow_available_etfs"] / daily_group_summary["etf_count"]
    )
    daily_group_summary["flow_to_assets_pct_weighted"] = (
        daily_group_summary["flow_billion"] / daily_group_summary["assets_billion"] * 100
    )
    compact_outputs = {
        "sse_etf_shares_raw.csv": raw,
        "sse_etf_flow_by_code.csv": data,
        "sse_etf_daily_detail.csv": daily_detail,
        "sse_etf_daily_group_summary.csv": daily_group_summary,
    }
    full_outputs = {
        **compact_outputs,
        "sse_etf_flow_daily.csv": daily,
        "sse_etf_integrity.csv": coverage,
        "sse_etf_missing_report.csv": missing_report,
        "sse_etf_run_summary.csv": summary,
    }
    quality_payload = {
        "run": json.loads(summary.to_json(orient="records", force_ascii=False))[0],
        "by_code": json.loads(
            coverage.to_json(orient="records", date_format="iso", force_ascii=False)
        ),
        "issues": json.loads(
            missing_report.to_json(orient="records", date_format="iso", force_ascii=False)
        ),
    }
    manifest_files = (
        list(full_outputs.keys())
        if args.full_outputs
        else list(compact_outputs.keys()) + ["sse_etf_quality.json"]
    )
    report_contract = load_report_contract()
    observed_buckets = sorted(set(data["pool_bucket"].dropna().astype(str)))
    core_buckets = [bucket for bucket in ["A股宽基", "科技宽基", "红利风格"] if bucket in observed_buckets]
    risk_buckets = [bucket for bucket in ["黄金", "国债"] if bucket in observed_buckets]
    cross_border_buckets = [bucket for bucket in ["港股宽基"] if bucket in observed_buckets]
    etf_count = int(data["code"].nunique())
    trading_day_count = int(data["date"].nunique())
    limited_scope = etf_count < 3 or trading_day_count < 10
    analysis_manifest = {
        "schema_version": report_contract["schema_version"],
        "analysis_status": "pending",
        "data_status": run_status,
        "signal_status": "pending",
        "signal_path": None,
        "data_files": manifest_files,
        "full_outputs": bool(args.full_outputs),
        "requested_start": args.start,
        "requested_end": args.end,
        "effective_end": effective_end.isoformat(),
        "pool_preset_id": pool_meta["preset_id"],
        "pool_as_of": pool_meta["as_of"],
        "selected_buckets": selected_buckets,
        "observed_buckets": observed_buckets,
        "etf_count": etf_count,
        "trading_day_count": trading_day_count,
        "analysis_roles": {
            "core": core_buckets,
            "risk_preference_auxiliary": risk_buckets,
            "cross_border_auxiliary": cross_border_buckets,
        },
        "report_expectations": {
            "limited_scope": limited_scope,
            "finding_count": "1-2" if limited_scope else "3-4",
            "visual_count_min": 2 if limited_scope else 3,
            "image_count_min": 0,
            "chart_recommended": bool(not limited_scope and trading_day_count >= 60),
            "chart_policy": "optional_when_informative",
            "paragraph_count_min": 3 if limited_scope else 5,
            "numeric_evidence_min": 6 if limited_scope else 12,
            "daily_detail_required": True,
            "daily_group_summary_required": True,
        },
        "required_data_outputs": [
            "sse_etf_daily_detail.csv",
            "sse_etf_daily_group_summary.csv",
        ],
        "required_comparisons": (
            report_contract["limited_required_comparisons"]
            if limited_scope else report_contract["required_comparisons"]
        ),
        "required_report_sections": report_contract["required_report_sections"],
        "report_path": None,
    }
    outputs = full_outputs if args.full_outputs else compact_outputs
    with tempfile.TemporaryDirectory(dir=out) as temp_dir:
        temp_path = Path(temp_dir)
        for filename, frame in outputs.items():
            frame.to_csv(temp_path / filename, index=False, encoding="utf-8-sig")
        if not args.full_outputs:
            with (temp_path / "sse_etf_quality.json").open("w", encoding="utf-8") as handle:
                json.dump(quality_payload, handle, ensure_ascii=False, indent=2)
        with (temp_path / "sse_etf_analysis_manifest.json").open("w", encoding="utf-8") as handle:
            json.dump(analysis_manifest, handle, ensure_ascii=False, indent=2)
        for filename in outputs:
            os.replace(temp_path / filename, out / filename)
        if not args.full_outputs:
            os.replace(temp_path / "sse_etf_quality.json", out / "sse_etf_quality.json")
        os.replace(temp_path / "sse_etf_analysis_manifest.json", out / "sse_etf_analysis_manifest.json")
    output_count = len(outputs) + 1 if args.full_outputs else len(outputs) + 2
    print(
        f"完成（{run_status}）：原始 {len(raw)} 条，面板 {len(data)} 条，"
        f"数据面板完整覆盖率 {overall_coverage:.2%}，输出 {output_count} 个文件，"
        f"有效截止日 {effective_end}，输出目录：{out.resolve()}"
    )


if __name__ == "__main__":
    main()
