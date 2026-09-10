from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from store_search_ai.pipeline.common import load_config


# Calibration v1에서 가장 큰 경계 문제가 발견된 family.
CALIBRATION_V2_FAMILIES = [
    "bedding",
    "books",
    "seafood",
    "fitness",
    "jewelry",
    "noodles",
    "snack_food",
    "chinese_food",
]

# v1에서는 paraphrase query를 사용했으므로,
# v2에서는 다른 표현형을 사용해 rubric이 query wording에도 안정적인지 확인.
PREFERRED_QUERY_TYPES = [
    "colloquial",
    "exact",
    "synonym",
]

CANDIDATES_PER_QUERY = 16


def deterministic_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def source_dict(value: object) -> dict:
    if value is None or pd.isna(value) or str(value).strip() == "":
        return {}
    return json.loads(str(value))


def choose_query(family_df: pd.DataFrame) -> pd.Series:
    for query_type in PREFERRED_QUERY_TYPES:
        hit = family_df[family_df["query_type"] == query_type]
        if len(hit):
            return hit.iloc[0]
    return family_df.iloc[0]


def select_candidates(
    group: pd.DataFrame,
    query_id: str,
    previously_judged: set[str],
    n: int,
) -> pd.DataFrame:
    x = group.copy()

    x["judgment_id"] = [
        hashlib.sha256(
            f"{query_id}|{doc_id}".encode("utf-8")
        ).hexdigest()[:20]
        for doc_id in x["doc_id"].astype(str)
    ]

    # Fresh docs only: do not reuse Calibration v1 judgments.
    x = x[~x["judgment_id"].isin(previously_judged)].copy()

    if x.empty:
        raise ValueError(f"No fresh candidates remain for {query_id}")

    x["_sources"] = x["pool_sources_json"].map(source_dict)

    x["_is_run"] = x["_sources"].map(
        lambda d: any(k.startswith("run:") for k in d)
    )
    x["_is_positive"] = x["_sources"].map(
        lambda d: any(k.startswith("target:positive:") for k in d)
    )
    x["_is_boundary"] = x["_sources"].map(
        lambda d: any(k.startswith("target:boundary:") for k in d)
    )
    x["_is_random"] = x["_sources"].map(
        lambda d: "random" in d
    )

    selected: list[str] = []

    # Retrieval-origin docs: 6
    run = x[x["_is_run"]].sort_values("best_run_rank", na_position="last")
    selected.extend(run["doc_id"].astype(str).head(6).tolist())

    # Positive coverage: 3
    pos = x[
        x["_is_positive"]
        & ~x["doc_id"].astype(str).isin(selected)
    ].copy()
    pos["_order"] = pos["doc_id"].astype(str).map(
        lambda d: deterministic_hash(f"{query_id}|pos|{d}")
    )
    selected.extend(pos.sort_values("_order")["doc_id"].astype(str).head(3).tolist())

    # Boundary: 4
    boundary = x[
        x["_is_boundary"]
        & ~x["doc_id"].astype(str).isin(selected)
    ].copy()
    boundary["_order"] = boundary["doc_id"].astype(str).map(
        lambda d: deterministic_hash(f"{query_id}|boundary|{d}")
    )
    selected.extend(
        boundary.sort_values("_order")["doc_id"].astype(str).head(4).tolist()
    )

    # Random: 3
    random_df = x[
        x["_is_random"]
        & ~x["doc_id"].astype(str).isin(selected)
    ].copy()
    random_df["_order"] = random_df["doc_id"].astype(str).map(
        lambda d: deterministic_hash(f"{query_id}|random|{d}")
    )
    selected.extend(
        random_df.sort_values("_order")["doc_id"].astype(str).head(3).tolist()
    )

    # Fill if a channel did not have enough docs.
    if len(selected) < n:
        remaining = x[~x["doc_id"].astype(str).isin(selected)].copy()
        remaining["_order"] = remaining["doc_id"].astype(str).map(
            lambda d: deterministic_hash(f"{query_id}|fill|{d}")
        )
        selected.extend(
            remaining.sort_values("_order")["doc_id"].astype(str)
            .head(n - len(selected))
            .tolist()
        )

    selected = selected[:n]
    result = x[x["doc_id"].astype(str).isin(selected)].copy()

    # Blind deterministic ordering.
    result["_blind_order"] = result["doc_id"].astype(str).map(
        lambda d: deterministic_hash(f"blind-v2|{query_id}|{d}")
    )
    return result.sort_values("_blind_order").head(n)


def read_completed(path: Path) -> pd.DataFrame:
    # Excel "Unicode Text" export may be UTF-16 TSV;
    # normal CSV may be UTF-8-sig comma-separated.
    try:
        return pd.read_csv(path, encoding="utf-8-sig")
    except Exception:
        return pd.read_csv(path, sep="\t", encoding="utf-16")


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--config",
        default="configs/benchmark/storesearch_ko_v1.yaml",
    )
    parser.add_argument(
        "--previous-a",
        required=True,
        help="Completed Calibration v1 A file, used only to exclude old judgments.",
    )
    parser.add_argument(
        "--round",
        default="calibration_v2",
    )

    args = parser.parse_args()

    config = load_config(args.config)
    benchmark_dir = Path(config["benchmark_dir"])

    queries = pd.read_csv(benchmark_dir / "queries.csv")
    pool = pd.read_csv(benchmark_dir / "candidate_pool_internal.csv")

    old = read_completed(Path(args.previous_a))
    previously_judged = set(old["judgment_id"].astype(str))

    selected_queries = []

    for family in CALIBRATION_V2_FAMILIES:
        family_df = queries[
            (queries["query_family"] == family)
            & (queries["status"] == "active")
        ].copy()

        if family_df.empty:
            raise ValueError(f"Unknown family: {family}")

        selected_queries.append(
            choose_query(family_df)
        )

    selected_query_df = pd.DataFrame(selected_queries)

    frames = []

    for q in selected_query_df.itertuples(index=False):
        group = pool[pool["query_id"] == q.query_id].copy()

        sampled = select_candidates(
            group=group,
            query_id=q.query_id,
            previously_judged=previously_judged,
            n=CANDIDATES_PER_QUERY,
        )

        sampled["intent_definition"] = q.intent_definition
        sampled["query"] = q.query
        sampled["split"] = q.split
        sampled["query_family"] = q.query_family

        frames.append(sampled)

    calibration = pd.concat(frames, ignore_index=True)

    calibration["annotation_round"] = args.round
    calibration["relevance"] = ""
    calibration["uncertain"] = ""
    calibration["annotator_note"] = ""

    visible = [
        "judgment_id",
        "annotation_round",
        "query_id",
        "query",
        "intent_definition",
        "split",
        "query_family",
        "doc_id",
        "store_name",
        "item_text",
        "market_name",
        "market_type",
        "source_region",
        "relevance",
        "uncertain",
        "annotator_note",
    ]

    output_dir = benchmark_dir / "calibration"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Human A
    a = calibration[visible].copy()
    a.insert(2, "annotator", "A")
    a.to_csv(
        output_dir / "calibration_A_v2.csv",
        index=False,
        encoding="utf-8-sig",
    )

    # Human B gets a different deterministic ordering.
    b = calibration.copy()
    b["_b_order"] = b.apply(
        lambda r: deterministic_hash(
            f"B-v2|{r['query_id']}|{r['doc_id']}"
        ),
        axis=1,
    )
    b = b.sort_values(["query_id", "_b_order"])[visible].copy()
    b.insert(2, "annotator", "B")
    b.to_csv(
        output_dir / "calibration_B_v2.csv",
        index=False,
        encoding="utf-8-sig",
    )

    manifest = {
        "round": args.round,
        "families": CALIBRATION_V2_FAMILIES,
        "query_types_selected": selected_query_df[
            ["query_family", "query_type", "query"]
        ].to_dict("records"),
        "queries": int(calibration["query_id"].nunique()),
        "candidates_per_query": CANDIDATES_PER_QUERY,
        "rows_per_annotator": int(len(calibration)),
        "fresh_vs_v1": True,
        "purpose": (
            "Verify revised annotation guideline v1.1 on fresh "
            "query-document pairs before full benchmark annotation."
        ),
    }

    (output_dir / "calibration_v2_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("========== CALIBRATION V2 SHEETS CREATED ==========")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
