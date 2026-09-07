from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
from sklearn.metrics import cohen_kappa_score

from store_search_ai.pipeline.common import load_config


def truthy(value: object, tokens: set[str]) -> bool:
    if value is None or pd.isna(value):
        return False
    return str(value).strip().upper() in tokens


def parse_rel(series: pd.Series, grades: set[int], label: str) -> pd.Series:
    raw = series.astype("string").str.strip()
    numeric = pd.to_numeric(raw.where(raw != ""), errors="coerce")
    invalid = numeric.notna() & ~numeric.isin(grades)
    if invalid.any():
        raise ValueError(f"{label}: invalid relevance labels: {series[invalid].tolist()[:10]}")
    return numeric.astype("Int64")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/benchmark/storesearch_ko_v1.yaml")
    parser.add_argument("--a", required=True)
    parser.add_argument("--b", required=True)
    parser.add_argument("--output", default="benchmark/storesearch_ko_v1/adjudication_sheet.csv")
    args = parser.parse_args()

    config = load_config(args.config)
    grades = set(map(int, config["relevance"]["grades"]))
    threshold = int(config["relevance"]["binary_threshold"])
    double_splits = set(config["annotation"]["double_annotate_splits"])
    uncertain_tokens = {str(x).strip().upper() for x in config["annotation"]["uncertain_tokens"]}

    a = pd.read_csv(args.a)
    b = pd.read_csv(args.b)
    if a["judgment_id"].duplicated().any() or b["judgment_id"].duplicated().any():
        raise ValueError("Duplicate judgment_id in annotation sheet")

    a["rel_A"] = parse_rel(a["relevance"], grades, "A")
    b["rel_B"] = parse_rel(b["relevance"], grades, "B")
    a["uncertain_A"] = a["uncertain"].map(lambda x: truthy(x, uncertain_tokens))
    b["uncertain_B"] = b["uncertain"].map(lambda x: truthy(x, uncertain_tokens))

    aa = a[[
        "judgment_id","annotation_round","query_id","query","intent_definition","split",
        "query_family","doc_id","store_name","item_text","market_name","market_type",
        "rel_A","uncertain_A","annotator_note"
    ]].rename(columns={"annotator_note":"note_A"})
    bb = b[["judgment_id","rel_B","uncertain_B","annotator_note"]].rename(columns={"annotator_note":"note_B"})
    merged = aa.merge(bb, on="judgment_id", how="left")

    needs_double = merged["split"].isin(double_splits)
    valid_a = merged["rel_A"].notna() & ~merged["uncertain_A"]
    valid_b = merged["rel_B"].notna() & ~merged["uncertain_B"].fillna(False)

    merged["needs_adjudication"] = False
    merged["final_relevance"] = pd.Series(pd.NA, index=merged.index, dtype="Int64")

    train_ok = ~needs_double & valid_a
    merged.loc[train_ok, "final_relevance"] = merged.loc[train_ok, "rel_A"]

    agree = needs_double & valid_a & valid_b & (merged["rel_A"] == merged["rel_B"])
    merged.loc[agree, "final_relevance"] = merged.loc[agree, "rel_A"]
    merged.loc[needs_double & ~agree, "needs_adjudication"] = True
    merged.loc[~valid_a, "needs_adjudication"] = True

    merged["assistant_suggested_relevance"] = ""
    merged["assistant_rationale"] = ""
    merged["adjudication_note"] = ""
    merged["exclude_from_gold"] = ""

    agreement_mask = needs_double & valid_a & valid_b
    ar = merged.loc[agreement_mask, "rel_A"].astype(int)
    br = merged.loc[agreement_mask, "rel_B"].astype(int)

    if len(ar) >= 2 and ar.nunique() > 1 and br.nunique() > 1:
        weighted = float(cohen_kappa_score(ar, br, weights="quadratic"))
        unweighted = float(cohen_kappa_score(ar, br))
    else:
        weighted = None
        unweighted = None

    required_double = int(needs_double.sum())
    completed_double = int(agreement_mask.sum())
    report = {
        "human_double_annotation_required_rows": required_double,
        "human_double_annotation_completed_rows": completed_double,
        "double_annotation_coverage": completed_double / required_double if required_double else 1.0,
        "exact_agreement": float((ar.to_numpy() == br.to_numpy()).mean()) if len(ar) else None,
        "binary_agreement_rel_ge_threshold": float(((ar.to_numpy() >= threshold) == (br.to_numpy() >= threshold)).mean()) if len(ar) else None,
        "unweighted_cohen_kappa": unweighted,
        "quadratic_weighted_cohen_kappa": weighted,
        "rows_needing_adjudication": int(merged["needs_adjudication"].sum()),
        "binary_relevance_threshold": threshold,
        "important": "AI suggestions are excluded from human agreement statistics; A/B must be independent humans if reported as human IAA.",
    }

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(out, index=False, encoding="utf-8-sig")
    (out.parent / "agreement_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("========== ANNOTATIONS MERGED ==========")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
