"""生成适合ETF份额估算数据的资金流率图表。

图表只用于回答两个问题：不同分组的资金动作强弱如何比较，近期资金动作
是否与价格同步。绝对金额、极值日期和逐只ETF证据仍以CSV和信号文件为准。
"""
import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import TwoSlopeNorm

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

GROUP_COLORS = {
    "A股宽基": "#2563eb", "科技宽基": "#9333ea", "红利风格": "#d97706",
    "黄金": "#b45309", "国债": "#64748b", "港股宽基": "#0f766e",
}
POSITIVE = "#dc2626"
NEGATIVE = "#16a34a"


def load_flow(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, encoding="utf-8-sig")
    required = {"date", "code", "pool_bucket", "amount_billion", "close", "shares"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"CSV缺少字段：{', '.join(sorted(missing))}")
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for column in ["amount_billion", "close", "shares"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    df = df.dropna(subset=["date", "amount_billion"])
    df = df.sort_values(["code", "date"]).copy()
    if "previous_close" not in df.columns:
        # 兼容旧版逐只计算结果；新下载结果已经直接提供该字段。
        df["previous_close"] = df.groupby("code")["close"].shift(1)
    else:
        df["previous_close"] = pd.to_numeric(df["previous_close"], errors="coerce")
    df["previous_shares"] = df.groupby("code")["shares"].shift(1)
    # 估算期初资产，第一条记录没有可比的前一日份额，必须排除。
    df["previous_assets_billion"] = df["previous_shares"] * df["previous_close"] / 100000000
    df["flow_rate_pct"] = df["amount_billion"] / df["previous_assets_billion"] * 100
    return df


def choose_scope(df: pd.DataFrame, signals: dict) -> tuple[pd.DataFrame, str]:
    primary = signals.get("primary_scope")
    if primary == "核心股票合计":
        buckets = {"A股宽基", "科技宽基", "红利风格"}
        return df[df["pool_bucket"].isin(buckets)].copy(), primary
    if primary and primary in set(df["pool_bucket"].dropna()):
        return df[df["pool_bucket"].eq(primary)].copy(), str(primary)
    scope = df.groupby("pool_bucket")["amount_billion"].size().idxmax()
    return df[df["pool_bucket"].eq(scope)].copy(), str(scope)


def daily_group_rate(df: pd.DataFrame) -> pd.DataFrame:
    return (
        df.groupby(["date", "pool_bucket"], as_index=False)
        .agg(flow_billion=("amount_billion", "sum"), assets_billion=("previous_assets_billion", "sum"))
        .assign(flow_rate_pct=lambda x: x["flow_billion"] / x["assets_billion"] * 100)
    )


def normalized_price(df: pd.DataFrame) -> pd.Series:
    parts = []
    for _, item in df.groupby("code"):
        item = item.dropna(subset=["close"]).drop_duplicates("date").set_index("date")["close"]
        if item.empty or item.iloc[0] <= 0:
            continue
        parts.append(item / item.iloc[0] * 100)
    return pd.concat(parts, axis=1).mean(axis=1).sort_index() if parts else pd.Series(dtype=float)


def save_monthly_heatmap(df: pd.DataFrame, output: Path, title_scope: str) -> None:
    monthly = (
        df.assign(month=df["date"].dt.to_period("M").astype(str))
        .groupby(["month", "pool_bucket"], as_index=False)
        .agg(flow_billion=("amount_billion", "sum"), assets_billion=("previous_assets_billion", "sum"))
        .assign(flow_rate_pct=lambda x: x["flow_billion"] / x["assets_billion"] * 100)
    )
    table = monthly.pivot(index="pool_bucket", columns="month", values="flow_rate_pct")
    table = table.dropna(how="all")
    if table.empty:
        raise ValueError("没有足够的有效资产规模记录，无法绘制月度资金流率热力图")
    limit = max(abs(float(np.nanpercentile(table.values, 95))), abs(float(np.nanpercentile(table.values, 5))), 0.1)
    norm = TwoSlopeNorm(vmin=-limit, vcenter=0, vmax=limit)
    fig, ax = plt.subplots(figsize=(14, max(3.8, 1.0 + 0.65 * len(table.index))))
    fig.patch.set_facecolor("#f8fafc")
    ax.set_facecolor("#ffffff")
    image = ax.imshow(table.values, cmap="RdYlGn_r", norm=norm, aspect="auto")
    ax.set_xticks(range(len(table.columns)), table.columns, rotation=55, ha="right")
    ax.set_yticks(range(len(table.index)), table.index)
    ax.set_title(f"核心ETF分组月度资金流率（{title_scope}）", fontsize=16, fontweight="bold", pad=22)
    ax.set_xlabel("月份")
    ax.set_ylabel("观察分组")
    for row in range(table.shape[0]):
        for col in range(table.shape[1]):
            value = table.iloc[row, col]
            if pd.notna(value):
                ax.text(col, row, f"{value:+.2f}%", ha="center", va="center", fontsize=8, color="#111827")
    colorbar = fig.colorbar(image, ax=ax, pad=0.015)
    colorbar.set_label("月度估算资金流 ÷ 月内平均估算期初资产")
    fig.text(0.99, 0.015, "只用于比较各组相对自身规模的资金动作；绝对金额和极值日期见逐日表、信号文件。", ha="right", fontsize=8, color="#64748b")
    fig.subplots_adjust(left=0.10, right=0.94, top=0.87, bottom=0.20)
    fig.savefig(output, dpi=170, bbox_inches="tight")
    plt.close(fig)


def save_recent_flow_price(df: pd.DataFrame, output: Path, title_scope: str, recent_days: int) -> None:
    grouped = daily_group_rate(df)
    daily = grouped.groupby("date").agg(flow_billion=("flow_billion", "sum"), assets_billion=("assets_billion", "sum"))
    daily["flow_rate_pct"] = daily["flow_billion"] / daily["assets_billion"] * 100
    daily = daily.dropna(subset=["flow_rate_pct"]).sort_index().tail(recent_days)
    if daily.empty:
        raise ValueError("没有足够的有效日度资金流率记录，无法绘制近期资金—价格图")
    price = normalized_price(df).reindex(daily.index).ffill()
    rolling = daily["flow_rate_pct"].rolling(20, min_periods=1).sum()
    colors = [POSITIVE if x > 0 else NEGATIVE for x in daily["flow_rate_pct"]]
    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(14, 8), sharex=True, gridspec_kw={"height_ratios": [1.35, 1.0], "hspace": 0.08})
    fig.patch.set_facecolor("#f8fafc")
    fig.suptitle(f"近期ETF资金流率与价格（{title_scope}，最近{len(daily)}个交易日）", fontsize=16, fontweight="bold", y=0.97)
    fig.text(0.03, 0.925, f"区间资金流 {daily.flow_billion.sum():+.1f}亿元    |    区间资金流 ÷ 区间期初资产合计 {daily.flow_billion.sum() / daily.assets_billion.sum() * 100:+.2f}%    |    20日滚动资金流率末值 {rolling.iloc[-1]:+.2f}%", fontsize=10, color="#334155")
    for axis in (ax, ax2):
        axis.set_facecolor("#ffffff")
        axis.grid(axis="y", color="#e2e8f0", linewidth=0.7)
        axis.set_axisbelow(True)
        axis.spines["top"].set_visible(False)
        axis.spines["right"].set_visible(False)
    ax.bar(daily.index, daily["flow_rate_pct"], color=colors, width=1.0, alpha=0.85)
    ax.plot(daily.index, rolling, color="#f59e0b", linewidth=1.4, label="滚动20日资金流率")
    ax.axhline(0, color="#475569", linewidth=0.8)
    ax.set_ylabel("日度资金流率（%）")
    ax.legend(loc="upper left", frameon=False)
    extremes = pd.concat([daily["flow_rate_pct"].nlargest(2), daily["flow_rate_pct"].nsmallest(2)])
    for date, value in extremes.items():
        ax.annotate(f"{date:%m-%d}\n{value:+.1f}%", xy=(date, value), xytext=(0, 8 if value > 0 else -28), textcoords="offset points", ha="center", fontsize=7, color="#334155")
    if not price.dropna().empty:
        ax2.plot(price.index, price.values, color="#475569", linewidth=1.35)
    ax2.set_ylabel("等权价格篮子（起点=100）")
    ax2.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=5, maxticks=9))
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
    plt.setp(ax2.get_xticklabels(), rotation=35, ha="right")
    fig.text(0.99, 0.015, "资金流率为份额变化×收盘价除以估算期初资产；价格线为纳入ETF收盘价等权归一化篮子。", ha="right", fontsize=8, color="#64748b")
    fig.subplots_adjust(left=0.08, right=0.95, top=0.88, bottom=0.15, hspace=0.08)
    fig.savefig(output, dpi=170, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description="生成ETF资金流率热力图和近期资金—价格图")
    parser.add_argument("--flow", required=True, help="sse_etf_flow_by_code.csv路径")
    parser.add_argument("--signals", required=True, help="sse_etf_signals.json路径")
    parser.add_argument("--output-dir", required=True, help="图表输出目录")
    parser.add_argument("--recent-days", type=int, default=120, help="近期图使用的交易日数量，默认120")
    args = parser.parse_args()
    flow_path = Path(args.flow).resolve()
    signals_path = Path(args.signals).resolve()
    out = Path(args.output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    df = load_flow(flow_path)
    signals = json.loads(signals_path.read_text(encoding="utf-8"))
    scoped, scope = choose_scope(df, signals)
    if scoped.empty:
        raise ValueError("没有可绘图的资金流记录")
    save_monthly_heatmap(scoped, out / "monthly_flow_rate_heatmap.png", scope)
    save_recent_flow_price(scoped, out / "recent_flow_price.png", scope, max(20, args.recent_days))
    print(f"已生成图表：{out / 'monthly_flow_rate_heatmap.png'}、{out / 'recent_flow_price.png'}")


if __name__ == "__main__":
    main()

