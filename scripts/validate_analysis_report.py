"""Validate a generated analysis report against the analysis manifest."""
import argparse
import json
import re
from pathlib import Path

REPORT_CONTRACT_PATH = Path(__file__).resolve().parents[1] / "references" / "report_contract.json"


def atomic_write_json(path: Path, payload: dict) -> None:
    temp_path = path.with_name(path.name + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


def is_valid_local_image(path: Path) -> bool:
    """用文件签名确认引用目标确实是可识别的本地图像。"""
    if not path.exists() or not path.is_file() or path.stat().st_size == 0:
        return False
    header = path.read_bytes()[:512]
    suffix = path.suffix.lower()
    if suffix == ".png":
        return header.startswith(b"\x89PNG\r\n\x1a\n")
    if suffix in {".jpg", ".jpeg"}:
        return header.startswith(b"\xff\xd8\xff")
    if suffix == ".webp":
        return len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP"
    if suffix == ".svg":
        return b"<svg" in header.lower()
    return False


def main():
    parser = argparse.ArgumentParser(description="校验A股核心ETF资金流分析报告")
    parser.add_argument("--manifest", required=True, help="sse_etf_analysis_manifest.json路径")
    parser.add_argument("--report", required=True, help="Markdown报告路径")
    args = parser.parse_args()

    manifest_path = Path(args.manifest).resolve()
    report_path = Path(args.report).resolve()
    if not manifest_path.exists():
        raise SystemExit(f"manifest不存在：{manifest_path}")
    if not report_path.exists():
        raise SystemExit(f"报告不存在：{report_path}")
    if manifest_path.parent != report_path.parent:
        raise SystemExit("报告必须与 manifest 位于同一输出目录")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    report = report_path.read_text(encoding="utf-8")
    contract = json.loads(REPORT_CONTRACT_PATH.read_text(encoding="utf-8"))
    current_sections = contract["required_report_sections"]
    current_comparisons = contract["required_comparisons"]
    # v1 manifest 使用旧报告结构；生成新版报告时迁移到当前结构，避免旧钩子强迫旧模板。
    if int(manifest.get("schema_version", 1)) < int(contract["schema_version"]):
        sections, comparisons = current_sections, current_comparisons
    else:
        sections = manifest.get("required_report_sections", current_sections)
        comparisons = manifest.get("required_comparisons", current_comparisons)
    missing_sections = [section for section in sections if section not in report]
    comparison_terms = contract["comparison_terms"]
    missing_comparisons = [
        item for item in comparisons
        if item in comparison_terms and not all(term in report for term in comparison_terms[item])
    ]
    table_count = sum(
        1 for line in report.splitlines()
        if re.match(r"^\s*\|\s*:?-{3,}", line)
    )
    image_entries = re.findall(r"!\[([^\]]*)\]\((<[^>]+>|[^)\s]+)", report)
    valid_image_paths = set()
    valid_image_labels = []
    missing_image_targets = []
    absolute_image_targets = []
    for alt_text, target in image_entries:
        normalized = target.strip("<>")
        if normalized.startswith(("http://", "https://", "data:")):
            missing_image_targets.append(normalized)
            continue
        if Path(normalized).is_absolute():
            absolute_image_targets.append(normalized)
        image_path = Path(normalized)
        if not image_path.is_absolute():
            image_path = report_path.parent / image_path
        image_path = image_path.resolve()
        if is_valid_local_image(image_path):
            valid_image_paths.add(image_path)
            valid_image_labels.append(alt_text)
        else:
            missing_image_targets.append(normalized)
    image_count = len(valid_image_paths)
    paragraph_count = sum(
        1 for block in re.split(r"\n\s*\n", report)
        if len(block.strip()) >= 50 and not block.lstrip().startswith(("#", "|"))
    )
    explanation_terms = ["说明", "意味着", "可能", "更像", "一致", "未发现稳定"]
    explanation_hits = sum(report.count(term) for term in explanation_terms)
    limitation_terms = ["不能", "仅", "不足", "有限", "代表性", "无法判断"]
    limitation_hits = sum(report.count(term) for term in limitation_terms)
    numeric_evidence = len(re.findall(r"[+-]?\d+(?:\.\d+)?%?", report))
    expectations = manifest.get("report_expectations", {})
    visual_count_min = int(expectations.get("visual_count_min", 3))
    image_count_min = int(expectations.get(
        "image_count_min",
        0,
    ))
    paragraph_count_min = int(expectations.get("paragraph_count_min", 5))
    numeric_evidence_min = int(expectations.get("numeric_evidence_min", 12))
    explanation_min = 2 if expectations.get("limited_scope") else 3
    limitation_min = 1 if expectations.get("limited_scope") else 2
    errors = []
    if absolute_image_targets:
        errors.append("报告中的图表必须使用相对报告目录的路径，不能嵌入本机绝对路径：" + "、".join(absolute_image_targets))
    # 图表要么以图片嵌入，要么完全不出现在报告中；普通 PNG 链接和裸文件名都会导致交付歧义。
    chart_suffixes = {".png", ".jpg", ".jpeg", ".webp", ".svg"}
    bare_links = re.findall(r"(?<!!)\[[^\]]*\]\((<[^>]+>|[^)\s]+)\)", report)
    bare_chart_links = [target.strip("<>") for target in bare_links if Path(target.strip("<>")).suffix.lower() in chart_suffixes]
    if bare_chart_links:
        errors.append("图表必须使用 Markdown 图片语法嵌入，不能只保留普通文件链接：" + "、".join(bare_chart_links))
    embedded_basenames = {Path(target.strip("<>")).name for _, target in image_entries}
    known_chart_names = {"monthly_flow_rate_heatmap.png", "recent_flow_price.png", "daily_flow_extremes.png", "monthly_flow.png"}
    unembedded_chart_mentions = [name for name in known_chart_names if name in report and name not in embedded_basenames]
    if unembedded_chart_mentions:
        errors.append("报告提到了图表文件但没有嵌入图片：" + "、".join(sorted(unembedded_chart_mentions)))
    required_data_outputs = manifest.get("required_data_outputs") or [
        "sse_etf_daily_detail.csv",
        "sse_etf_daily_group_summary.csv",
    ]
    if expectations.get("daily_detail_required") and "sse_etf_daily_detail.csv" not in required_data_outputs:
        required_data_outputs.append("sse_etf_daily_detail.csv")
    if expectations.get("daily_group_summary_required") and "sse_etf_daily_group_summary.csv" not in required_data_outputs:
        required_data_outputs.append("sse_etf_daily_group_summary.csv")
    for required_output in required_data_outputs:
        output_path = (manifest_path.parent / required_output).resolve()
        if output_path.parent != manifest_path.parent:
            errors.append(f"标准数据交付物必须位于 manifest 同目录：{required_output}")
        elif not output_path.exists():
            errors.append(f"manifest声明的标准数据交付物不存在：{required_output}")
    signal_path = manifest.get("signal_path") or "sse_etf_signals.json"
    signal_file = (manifest_path.parent / signal_path).resolve()
    if signal_file.parent != manifest_path.parent:
        errors.append("信号文件必须与 manifest 位于同一输出目录")
    if not signal_file.exists():
        errors.append("缺少信号挖掘文件：请先运行 mine_flow_signals.py")
        signal = {}
    else:
        signal = json.loads(signal_file.read_text(encoding="utf-8"))
    if missing_sections:
        errors.append("缺少报告章节：" + "、".join(missing_sections))
    if missing_comparisons:
        errors.append("缺少必做比较：" + "、".join(missing_comparisons))
    if table_count + image_count < visual_count_min:
        errors.append(f"图表或结构化表格不足{visual_count_min}项：tables={table_count}, images={image_count}")
    if image_count < image_count_min:
        errors.append(f"长窗口实际图表不足{image_count_min}张：images={image_count}")
    if image_count_min >= 1 and not any(
        any(term in label for term in ["资金流率", "资金", "价格", "近期"])
        for label in valid_image_labels
    ):
        errors.append("缺少替代文字标明‘资金流率、价格或近期窗口’的图")
    if image_count_min >= 2 and not any(
        any(term in label for term in ["分组", "月度", "热力"])
        for label in valid_image_labels
    ):
        errors.append("缺少替代文字标明‘分组、月度或热力图’的图")
    if missing_image_targets:
        errors.append("报告引用了不存在或不可验收的图表：" + "、".join(missing_image_targets))
    if paragraph_count < paragraph_count_min:
        errors.append(f"解释性正文不足：有效分析段落={paragraph_count}，至少需要{paragraph_count_min}段")
    if explanation_hits < explanation_min or limitation_hits < limitation_min or numeric_evidence < numeric_evidence_min:
        errors.append(
            f"缺少完整的证据链：至少需要{numeric_evidence_min}项数字/日期证据、"
            f"{explanation_min}处含义解释和{limitation_min}处限制说明"
        )
    if not any(term in report for term in ["净申购", "流入", "买入"]):
        errors.append("缺少买入或净申购一侧的分析")
    if not any(term in report for term in ["净赎回", "流出", "卖出"]):
        errors.append("缺少卖出或净赎回一侧的分析")
    if signal.get("bucket_summary") and not any(
        term in report for term in ["全部记录", "非零记录", "有效申赎记录", "比例分母"]
    ):
        errors.append("正流比例未说明分母是全部记录还是非零申赎记录")
    quadrant_patterns = {
        "涨价增份额": ("上涨", "增加"),
        "跌价增份额": ("下跌", "增加"),
        "涨价减份额": ("上涨", "减少"),
        "跌价减份额": ("下跌", "减少"),
    }
    report_lines = report.splitlines()
    for label, expected in signal.get("quadrant_rows", {}).items():
        if label not in quadrant_patterns:
            continue
        price_word, share_word = quadrant_patterns[label]
        matching = [line for line in report_lines if price_word in line and share_word in line]
        if not matching or not any(str(expected) in line for line in matching):
            errors.append(f"价格—份额四象限数据与信号文件不一致：{label} 应为 {expected}")
    long_window = int(manifest.get("trading_day_count", 0)) >= 60 and int(manifest.get("etf_count", 0)) >= 3
    primary_scope = signal.get("primary_scope") or "核心股票合计"
    if long_window and signal.get("daily_extreme_rankings"):
        ranked_dates = [
            item.get("date") for item in signal.get("daily_extreme_rankings", [])
            if item.get("scope") == primary_scope and item.get("date")
        ]
        missing_ranked_dates = [value for value in ranked_dates if value not in report]
        if missing_ranked_dates:
            errors.append(f"缺少{primary_scope}日度流入/流出前5日期：" + "、".join(missing_ranked_dates))
        if not any(term in report for term in ["此前5日", "此前5个交易日", "前5日", "前5个交易日"]):
            errors.append("缺少最大日此前5个交易日的资金表现")
        if not any(term in report for term in ["此后5日", "此后5个交易日", "后续5日", "后5日", "后5个交易日"]):
            errors.append("缺少最大日此后5个交易日的资金表现")
    else:
        for item in signal.get("daily_extreme_points", []):
            if item.get("scope") == primary_scope and item.get("date") not in report:
                errors.append(f"缺少{primary_scope}{item.get('direction')}日期：{item.get('date')}")
    if long_window and signal.get("monthly_extreme_rankings"):
        ranked_months = [
            item.get("month") for item in signal.get("monthly_extreme_rankings", [])
            if item.get("scope") == primary_scope and item.get("month")
        ]
        missing_ranked_months = [value for value in ranked_months if value not in report]
        if missing_ranked_months:
            errors.append(f"缺少{primary_scope}月度流入/流出前3月份：" + "、".join(missing_ranked_months))
    else:
        for item in signal.get("monthly_extreme_points", []):
            if item.get("scope") == primary_scope and item.get("month") not in report:
                errors.append(f"缺少{primary_scope}{item.get('direction')}月份：{item.get('month')}")
    if "用一句话概括" not in report:
        errors.append("缺少‘用一句话概括’章节")
    if errors:
        raise SystemExit("报告校验失败：\n- " + "\n- ".join(errors))

    data_status = manifest.get("data_status")
    if data_status is None:
        quality_path = manifest_path.parent / "sse_etf_quality.json"
        if quality_path.exists():
            quality = json.loads(quality_path.read_text(encoding="utf-8"))
            data_status = quality.get("run", {}).get("status")
        else:
            run_summary_path = manifest_path.parent / "sse_etf_run_summary.csv"
            if run_summary_path.exists():
                import csv
                with run_summary_path.open("r", encoding="utf-8-sig", newline="") as handle:
                    first_row = next(csv.DictReader(handle), {})
                data_status = first_row.get("status")
    manifest["schema_version"] = contract["schema_version"]
    manifest["required_report_sections"] = sections
    manifest["required_comparisons"] = comparisons
    manifest["analysis_status"] = "partial" if data_status == "partial" else "completed"
    manifest["report_path"] = report_path.name
    manifest["report_validation"] = {
        "status": "passed",
        "section_count": len(sections),
        "comparison_count": len(comparisons),
        "table_count": table_count,
        "image_count": image_count,
    }
    atomic_write_json(manifest_path, manifest)
    print(f"报告校验通过：{report_path}")


if __name__ == "__main__":
    main()
