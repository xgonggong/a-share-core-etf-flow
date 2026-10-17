"""从逐只ETF资金流文件提取可用于报告的数据发现候选。"""
import argparse
import json
from pathlib import Path

import pandas as pd


REPORT_CONTRACT_PATH = Path(__file__).resolve().parents[1] / "references" / "report_contract.json"


def load_flow(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, encoding="utf-8-sig")
    required = {"date", "code", "pool_bucket", "amount_billion", "delta_shares", "shares", "close"}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"CSV缺少字段：{', '.join(sorted(missing))}")
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["code"] = df["code"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
    for col in ["amount_billion", "delta_shares", "shares", "close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if "tech_flag" not in df.columns:
        df["tech_flag"] = False
    elif df["tech_flag"].dtype != bool:
        df["tech_flag"] = df["tech_flag"].astype(str).str.lower().isin({"true", "1", "yes"})
    if "fund_name" not in df.columns:
        df["fund_name"] = ""
    return df.dropna(subset=["date", "amount_billion", "delta_shares", "shares", "close"])


def atomic_write_json(path: Path, payload: dict) -> None:
    temp_path = path.with_name(path.name + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


def daily_breadth(df: pd.DataFrame, positive: bool) -> list[dict]:
    daily = df.groupby(["pool_bucket", "date"], as_index=False).agg(
        flow_billion=("amount_billion", "sum"),
        positive_etfs=("amount_billion", lambda s: int((s > 0).sum())),
        negative_etfs=("amount_billion", lambda s: int((s < 0).sum())),
        active_etfs=("amount_billion", lambda s: int(s.ne(0).sum())),
    )
    daily["breadth"] = daily.apply(
        lambda r: (r.positive_etfs if positive else r.negative_etfs) / r.active_etfs if r.active_etfs else 0,
        axis=1,
    )
    selected = daily[daily.flow_billion > 0] if positive else daily[daily.flow_billion < 0]
    selected = (selected.sort_values(["pool_bucket", "breadth", "flow_billion"], ascending=[True, False, not positive])
                .groupby("pool_bucket", as_index=False).head(3))
    return [
        {"pool_bucket": row.pool_bucket, "date": row.date.date().isoformat(), "flow_billion": round(float(row.flow_billion), 4),
         "positive_etfs": int(row.positive_etfs), "negative_etfs": int(row.negative_etfs),
         "active_etfs": int(row.active_etfs), "breadth": round(float(row.breadth), 4),
         "breadth_informative": bool(row.active_etfs >= 2)}
        for row in selected.itertuples()
    ]


def bucket_summary(df: pd.DataFrame) -> list[dict]:
    grouped = df.groupby("pool_bucket", as_index=False).agg(
        flow_billion=("amount_billion", "sum"), etfs=("code", "nunique"),
        positive_rows=("amount_billion", lambda s: int((s > 0).sum())),
        negative_rows=("amount_billion", lambda s: int((s < 0).sum())),
        zero_rows=("amount_billion", lambda s: int((s == 0).sum())),
        observation_rows=("amount_billion", "size"),
    )
    grouped["active_rows"] = grouped["positive_rows"] + grouped["negative_rows"]
    grouped["positive_ratio_all_rows"] = (
        grouped["positive_rows"] / grouped["observation_rows"].replace(0, pd.NA)
    )
    grouped["positive_ratio_active_rows"] = (
        grouped["positive_rows"] / grouped["active_rows"].replace(0, pd.NA)
    )
    return json.loads(grouped.round(6).to_json(orient="records", force_ascii=False))


def bucket_relative_size(df: pd.DataFrame) -> list[dict]:
    """用估算期初资产规模归一化累计流量，避免大基金天然占优。"""
    work = df.copy()
    work["previous_shares"] = work["shares"] - work["delta_shares"]
    work["previous_assets_yi"] = work["previous_shares"] * work["close"] / 100000000
    daily_assets = (
        work[work["previous_assets_yi"] > 0]
        .groupby(["pool_bucket", "date"], as_index=False)["previous_assets_yi"].sum()
    )
    average_assets = daily_assets.groupby("pool_bucket")["previous_assets_yi"].mean()
    flow = work.groupby("pool_bucket")["amount_billion"].sum()
    result = pd.concat(
        [flow.rename("flow_billion"), average_assets.rename("average_previous_assets_yi")],
        axis=1,
    ).reset_index()
    result["cumulative_flow_to_average_assets_ratio"] = (
        result["flow_billion"] / result["average_previous_assets_yi"].replace(0, pd.NA)
    )
    return json.loads(result.round(6).to_json(orient="records", force_ascii=False))


def persistence(df: pd.DataFrame) -> list[dict]:
    """按分组计算连续净流入/净流出，绝不跨资产相加。"""
    daily = df.groupby(["pool_bucket", "date"], as_index=False)["amount_billion"].sum().sort_values(["pool_bucket", "date"])
    signs = daily["amount_billion"].apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0)).tolist()
    runs, start, last, count = [], None, 0, 0
    previous_bucket = None
    for bucket, date, value in zip(daily["pool_bucket"].tolist(), daily["date"].tolist(), signs):
        if bucket != previous_bucket:
            if last:
                runs.append({"pool_bucket": previous_bucket, "direction": "inflow" if last > 0 else "outflow", "start": start.date().isoformat(), "end": previous.date().isoformat(), "trading_days": count})
            start, last, count = None, 0, 0
            previous_bucket = bucket
        if value != last:
            if last:
                runs.append({"pool_bucket": bucket, "direction": "inflow" if last > 0 else "outflow", "start": start.date().isoformat(), "end": previous.date().isoformat(), "trading_days": count})
            start, count, last = (date, 1, value) if value else (None, 0, 0)
        elif value:
            count += 1
        previous = date
    if last:
        runs.append({"pool_bucket": previous_bucket, "direction": "inflow" if last > 0 else "outflow", "start": start.date().isoformat(), "end": previous.date().isoformat(), "trading_days": count})
    return sorted(runs, key=lambda x: x["trading_days"], reverse=True)[:6]


def bucket_daily(df: pd.DataFrame) -> list[dict]:
    grouped = df.groupby(["date", "pool_bucket"], as_index=False)["amount_billion"].sum()
    grouped["rolling_5d_billion"] = grouped.sort_values("date").groupby("pool_bucket")["amount_billion"].transform(lambda s: s.rolling(5, min_periods=1).sum())
    return json.loads(grouped.sort_values(["date", "pool_bucket"]).round(6).to_json(orient="records", date_format="iso", force_ascii=False))


def product_concentration(df: pd.DataFrame) -> list[dict]:
    products = df.groupby(["pool_bucket", "code"], as_index=False)["amount_billion"].sum()
    products["absolute_product_flow"] = products["amount_billion"].abs()
    totals = products.groupby("pool_bucket")["absolute_product_flow"].transform("sum")
    products["group_etf_count"] = products.groupby("pool_bucket")["code"].transform("nunique")
    products["absolute_contribution_ratio"] = products["absolute_product_flow"] / totals.replace(0, pd.NA)
    products["concentration_informative"] = products["group_etf_count"] >= 2
    return json.loads(products.sort_values(["pool_bucket", "absolute_contribution_ratio"], ascending=[True, False]).round(6).to_json(orient="records", force_ascii=False))


def anomaly_candidates(df: pd.DataFrame) -> list[dict]:
    """用过去20个有效日识别异常，并给出随后5个交易日延续或反转。"""
    rows = []
    for (bucket, code), group in df.groupby(["pool_bucket", "code"]):
        group = group.sort_values("date").reset_index(drop=True)
        values = group["amount_billion"]
        for index, value in values.items():
            history = values.iloc[max(0, index - 20):index].dropna()
            if len(history) < 5:
                continue
            median = float(history.median())
            mad = float((history - median).abs().median())
            robust_score = abs(float(value) - median) / (1.4826 * mad) if mad > 0 else None
            percentile = float((history.abs() <= abs(float(value))).mean())
            if not ((robust_score is not None and robust_score >= 3) or percentile >= 0.95):
                continue
            following = values.iloc[index + 1:index + 6].dropna()
            if following.empty:
                follow_up, next_flow = "insufficient", None
            else:
                next_flow = float(following.sum())
                follow_up = "continuation" if value * next_flow > 0 else ("reversal" if value * next_flow < 0 else "mixed")
            rows.append({
                "pool_bucket": bucket, "code": str(code).zfill(6),
                "date": group.loc[index, "date"].date().isoformat(),
                "event_flow_billion": round(float(value), 6),
                "history_abs_percentile": round(percentile, 6),
                "robust_score": round(robust_score, 6) if robust_score is not None else None,
                "next_5d_flow_billion": round(next_flow, 6) if next_flow is not None else None,
                "follow_up": follow_up,
            })
    return sorted(rows, key=lambda row: (row["robust_score"] or 0, abs(row["event_flow_billion"])), reverse=True)[:12]


def extreme_points(df: pd.DataFrame) -> tuple[list[dict], list[dict], list[dict], list[dict], list[dict]]:
    """提取分组和核心股票合计的日度/月度极值榜，并计算极值前后5日表现。"""
    core_names = {"A股宽基", "科技宽基", "红利风格"}
    frames = [(bucket, group.copy()) for bucket, group in df.groupby("pool_bucket")]
    core = df[df["pool_bucket"].isin(core_names)].copy()
    if not core.empty:
        frames.append(("核心股票合计", core))
    daily_extremes = []
    daily_rankings = []
    monthly_rows = []
    monthly_extremes = []
    monthly_rankings = []
    for scope, frame in frames:
        daily = frame.groupby("date")["amount_billion"].sum().sort_index()
        # 只把严格为正/为负的日期分别纳入买卖榜，避免全流入样本把
        # “最小的正数”误写成流出，或全流出样本反向误写成流入。
        ranked_days = {
            "inflow": daily[daily > 0].nlargest(min(5, int((daily > 0).sum()))),
            "outflow": daily[daily < 0].nsmallest(min(5, int((daily < 0).sum()))),
        }
        for direction, series in ranked_days.items():
            for rank, (date, flow) in enumerate(series.items(), start=1):
                position = daily.index.get_loc(date)
                previous = daily.iloc[max(0, position - 5):position]
                following = daily.iloc[position + 1:position + 6]
                day_rows = frame[frame["date"].eq(date)]
                contributor_rows = (
                    day_rows.nlargest(3, "amount_billion")
                    if direction == "inflow"
                    else day_rows.nsmallest(3, "amount_billion")
                )
                daily_rankings.append({
                    "scope": scope, "direction": direction, "rank": rank,
                    "date": date.date().isoformat(), "flow_billion": round(float(flow), 6),
                    "positive_etfs": int((day_rows["amount_billion"] > 0).sum()),
                    "negative_etfs": int((day_rows["amount_billion"] < 0).sum()),
                    "tech_flag_count": int(day_rows["tech_flag"].sum()),
                    "top_contributors": [
                        {"code": str(row.code).zfill(6), "fund_name": row.fund_name,
                         "flow_billion": round(float(row.amount_billion), 6),
                         "tech_flag": bool(row.tech_flag)}
                        for row in contributor_rows.itertuples()
                    ],
                    "previous_5d_flow_billion": round(float(previous.sum()), 6) if len(previous) == 5 else None,
                    "next_5d_flow_billion": round(float(following.sum()), 6) if len(following) == 5 else None,
                })
        extreme_dates = []
        positive_days = daily[daily > 0]
        negative_days = daily[daily < 0]
        if not positive_days.empty:
            extreme_dates.append(("max_inflow", positive_days.idxmax()))
        if not negative_days.empty:
            extreme_dates.append(("max_outflow", negative_days.idxmin()))
        for direction, date in extreme_dates:
            position = daily.index.get_loc(date)
            previous = daily.iloc[max(0, position - 5):position]
            following = daily.iloc[position + 1:position + 6]
            day_rows = frame[frame["date"].eq(date)]
            direction = "inflow" if direction == "max_inflow" else "outflow"
            contributor_rows = (
                day_rows.nlargest(3, "amount_billion")
                if direction == "inflow"
                else day_rows.nsmallest(3, "amount_billion")
            )
            daily_extremes.append({
                "scope": scope, "direction": f"max_{direction}", "date": date.date().isoformat(),
                "flow_billion": round(float(daily.loc[date]), 6),
                "positive_etfs": int((day_rows["amount_billion"] > 0).sum()),
                "negative_etfs": int((day_rows["amount_billion"] < 0).sum()),
                "tech_flag_count": int(day_rows["tech_flag"].sum()),
                "top_contributors": [
                    {"code": str(row.code).zfill(6), "fund_name": row.fund_name,
                     "flow_billion": round(float(row.amount_billion), 6),
                     "tech_flag": bool(row.tech_flag)}
                    for row in contributor_rows.itertuples()
                ],
                "previous_5d_flow_billion": round(float(previous.sum()), 6) if len(previous) == 5 else None,
                "next_5d_flow_billion": round(float(following.sum()), 6) if len(following) == 5 else None,
            })
        month = frame.assign(month=frame["date"].dt.to_period("M").astype(str)).groupby("month")["amount_billion"].sum()
        for month_value, flow in month.items():
            monthly_rows.append({"scope": scope, "month": month_value, "flow_billion": round(float(flow), 6)})
        positive_months = month[month > 0]
        negative_months = month[month < 0]
        if not positive_months.empty:
            monthly_extremes.append({
                "scope": scope, "direction": "max_inflow", "month": positive_months.idxmax(),
                "flow_billion": round(float(positive_months.max()), 6),
            })
        if not negative_months.empty:
            monthly_extremes.append({
                "scope": scope, "direction": "max_outflow", "month": negative_months.idxmin(),
                "flow_billion": round(float(negative_months.min()), 6),
            })
        for direction, series in {
            "inflow": positive_months.nlargest(min(3, len(positive_months))),
            "outflow": negative_months.nsmallest(min(3, len(negative_months))),
        }.items():
            monthly_rankings.extend([
                {"scope": scope, "direction": direction, "rank": rank, "month": month_value,
                 "flow_billion": round(float(flow), 6)}
                for rank, (month_value, flow) in enumerate(series.items(), start=1)
            ])
    return daily_extremes, monthly_extremes, monthly_rows, daily_rankings, monthly_rankings


def main() -> None:
    parser = argparse.ArgumentParser(description="提取A股核心ETF资金流的数据发现候选")
    parser.add_argument("--flow", required=True, help="sse_etf_flow_by_code.csv路径")
    parser.add_argument("--output", required=True, help="JSON输出路径")
    parser.add_argument("--manifest", help="可选；分析manifest路径。省略时自动查找资金流文件同目录")
    args = parser.parse_args()
    df = load_flow(Path(args.flow)).sort_values(["code", "date"])
    df["price_change"] = df.groupby("code")["close"].pct_change()
    df["share_change_rate"] = df["delta_shares"] / df.groupby("code")["shares"].shift(1).abs()
    df["quadrant"] = "无法判断"
    valid = df["price_change"].notna() & df["share_change_rate"].notna()
    df.loc[valid & df["share_change_rate"].eq(0), "quadrant"] = "份额不变"
    df.loc[valid & df["price_change"].eq(0) & df["share_change_rate"].gt(0), "quadrant"] = "价格持平增份额"
    df.loc[valid & df["price_change"].eq(0) & df["share_change_rate"].lt(0), "quadrant"] = "价格持平减份额"
    for price_up, share_up, name in [(True, True, "涨价增份额"), (False, True, "跌价增份额"), (True, False, "涨价减份额"), (False, False, "跌价减份额")]:
        price_mask = df.price_change > 0 if price_up else df.price_change < 0
        share_mask = df.share_change_rate > 0 if share_up else df.share_change_rate < 0
        df.loc[valid & price_mask & share_mask, "quadrant"] = name
    top_products = (df.groupby(["pool_bucket", "code"], as_index=False)["amount_billion"].sum()
                    .assign(abs_flow=lambda x: x.amount_billion.abs()).sort_values("abs_flow", ascending=False).head(10))
    first_last = (df.sort_values("date").groupby(["pool_bucket", "code"], as_index=False)
                  .agg(first_shares=("shares", "first"), first_delta=("delta_shares", "first"), last_shares=("shares", "last")))
    first_last["opening_shares"] = first_last["first_shares"] - first_last["first_delta"]
    first_last["interval_share_change_rate"] = (first_last["last_shares"] - first_last["opening_shares"]) / first_last["opening_shares"].abs()
    relative = (first_last.sort_values("interval_share_change_rate", key=lambda s: s.abs(), ascending=False).head(10))
    daily_extremes, monthly_extremes, monthly_flow, daily_rankings, monthly_rankings = extreme_points(df)
    core_names = {"A股宽基", "科技宽基", "红利风格"}
    if df["pool_bucket"].isin(core_names).any():
        primary_scope = "核心股票合计"
    else:
        primary_scope = (
            df.groupby("pool_bucket")["code"].nunique().sort_values(ascending=False).index[0]
            if not df.empty else None
        )
    result = {
        "schema_version": 4,
        "window": {"start": df.date.min().date().isoformat(), "end": df.date.max().date().isoformat()},
        "rows": int(len(df)), "broadest_inflow_days": daily_breadth(df, True), "broadest_outflow_days": daily_breadth(df, False),
        "bucket_summary": bucket_summary(df),
        "bucket_relative_size": bucket_relative_size(df),
        "longest_net_runs": persistence(df),
        "bucket_daily_flow": bucket_daily(df),
        "product_concentration": product_concentration(df),
        "anomaly_candidates_with_5d_follow_up": anomaly_candidates(df),
        "daily_extreme_points": daily_extremes,
        "monthly_extreme_points": monthly_extremes,
        "daily_extreme_rankings": daily_rankings,
        "monthly_extreme_rankings": monthly_rankings,
        "primary_scope": primary_scope,
        "monthly_flow": monthly_flow,
        "top_products_by_absolute_flow": json.loads(top_products.drop(columns="abs_flow").round(6).to_json(orient="records", force_ascii=False)),
        "top_products_by_relative_share_change": json.loads(relative.round(6).to_json(orient="records", force_ascii=False)),
        "quadrant_rows": {str(k): int(v) for k, v in df.quadrant.value_counts().items()},
        "interpretation_prompt": "围绕广度、集中度、持续性/反转、组间替代和价格—份额象限挑选3至4个发现卡；每张卡写事实、解释、限制和证据强度。",
    }
    out = Path(args.output).resolve()
    manifest_path = Path(args.manifest) if args.manifest else Path(args.flow).resolve().parent / "sse_etf_analysis_manifest.json"
    if manifest_path.exists():
        if manifest_path.resolve().parent != out.resolve().parent:
            raise ValueError("信号文件必须与 analysis manifest 位于同一输出目录")
    out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(out, result)
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        contract = json.loads(REPORT_CONTRACT_PATH.read_text(encoding="utf-8"))
        manifest["schema_version"] = contract["schema_version"]
        manifest["required_report_sections"] = contract["required_report_sections"]
        manifest["required_comparisons"] = contract["required_comparisons"]
        manifest["signal_status"] = "completed"
        manifest["signal_path"] = out.name
        atomic_write_json(manifest_path, manifest)
    print(f"已生成信号候选：{out}")


if __name__ == "__main__":
    main()
