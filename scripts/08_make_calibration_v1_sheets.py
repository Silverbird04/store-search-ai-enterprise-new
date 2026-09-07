from __future__ import annotations

import argparse
import hashlib
import json

from pathlib import Path

import pandas as pd

from store_search_ai.pipeline.common import load_config


# --------------------------------------------------
# Calibration에서 볼 Family
#
# 경계가 명확한 것 + 애매한 것을 일부러 섞음
# --------------------------------------------------

CALIBRATION_FAMILIES = [

    # Validation
    "fruit",
    "seafood",
    "eyewear",
    "fitness",
    "flowers",
    "books",

    # Test
    "chinese_food",
    "snack_food",
    "dental",
    "jewelry",
    "bedding",
    "noodles",
]


CANDIDATES_PER_QUERY = 24


def source_dict(
    value: object,
) -> dict:

    if (
        value is None
        or pd.isna(value)
        or str(value).strip() == ""
    ):
        return {}

    return json.loads(
        str(value)
    )


def deterministic_hash(
    value: str,
) -> str:

    return hashlib.sha256(
        value.encode(
            "utf-8"
        )
    ).hexdigest()


def select_query(
    family_df: pd.DataFrame,
) -> pd.Series:

    """
    Calibration에서는 가능하면 paraphrase query를 선택.

    exact lexical matching보다
    semantic boundary 판단이 잘 드러나기 때문.
    """

    paraphrase = family_df[
        family_df["query_type"]
        == "paraphrase"
    ]

    if len(paraphrase):

        return paraphrase.iloc[0]

    return family_df.iloc[0]


def sample_candidates(
    group: pd.DataFrame,
    query_id: str,
    n: int,
) -> pd.DataFrame:

    x = group.copy()

    x["_sources"] = (
        x[
            "pool_sources_json"
        ]
        .map(
            source_dict
        )
    )

    x["_is_run"] = (
        x["_sources"]
        .map(
            lambda d:
            any(
                key.startswith(
                    "run:"
                )
                for key in d
            )
        )
    )

    x["_is_positive"] = (
        x["_sources"]
        .map(
            lambda d:
            any(
                key.startswith(
                    "target:positive:"
                )
                for key in d
            )
        )
    )

    x["_is_boundary"] = (
        x["_sources"]
        .map(
            lambda d:
            any(
                key.startswith(
                    "target:boundary:"
                )
                for key in d
            )
        )
    )

    x["_is_random"] = (
        x["_sources"]
        .map(
            lambda d:
            "random" in d
        )
    )

    selected_ids = []

    # ----------------------------------------------
    # Retrieval candidates
    # ----------------------------------------------

    run = (
        x[
            x["_is_run"]
        ]
        .sort_values(
            "best_run_rank",
            na_position="last",
        )
    )

    selected_ids.extend(
        run[
            "doc_id"
        ]
        .head(10)
        .tolist()
    )

    # ----------------------------------------------
    # Positive coverage
    # ----------------------------------------------

    positive = x[
        x["_is_positive"]
        & ~x[
            "doc_id"
        ].isin(
            selected_ids
        )
    ]

    positive = positive.assign(
        _order=positive[
            "doc_id"
        ].astype(str)
        .map(
            lambda value:
            deterministic_hash(
                query_id
                + "|positive|"
                + value
            )
        )
    ).sort_values(
        "_order"
    )

    selected_ids.extend(
        positive[
            "doc_id"
        ]
        .head(4)
        .tolist()
    )

    # ----------------------------------------------
    # Boundary coverage
    # ----------------------------------------------

    boundary = x[
        x["_is_boundary"]
        & ~x[
            "doc_id"
        ].isin(
            selected_ids
        )
    ]

    boundary = boundary.assign(
        _order=boundary[
            "doc_id"
        ].astype(str)
        .map(
            lambda value:
            deterministic_hash(
                query_id
                + "|boundary|"
                + value
            )
        )
    ).sort_values(
        "_order"
    )

    selected_ids.extend(
        boundary[
            "doc_id"
        ]
        .head(5)
        .tolist()
    )

    # ----------------------------------------------
    # Explicit random negatives
    # ----------------------------------------------

    random_df = x[
        x["_is_random"]
        & ~x[
            "doc_id"
        ].isin(
            selected_ids
        )
    ]

    random_df = random_df.assign(
        _order=random_df[
            "doc_id"
        ].astype(str)
        .map(
            lambda value:
            deterministic_hash(
                query_id
                + "|random|"
                + value
            )
        )
    ).sort_values(
        "_order"
    )

    selected_ids.extend(
        random_df[
            "doc_id"
        ]
        .head(5)
        .tolist()
    )

    # ----------------------------------------------
    # 부족하면 나머지 pool에서 채움
    # ----------------------------------------------

    remaining = x[
        ~x[
            "doc_id"
        ].isin(
            selected_ids
        )
    ].copy()

    remaining["_order"] = (
        remaining[
            "doc_id"
        ]
        .astype(str)
        .map(
            lambda value:
            deterministic_hash(
                query_id
                + "|fill|"
                + value
            )
        )
    )

    remaining = remaining.sort_values(
        "_order"
    )

    need = max(
        0,
        n - len(
            selected_ids
        ),
    )

    selected_ids.extend(
        remaining[
            "doc_id"
        ]
        .head(
            need
        )
        .tolist()
    )

    selected_ids = (
        selected_ids[
            :n
        ]
    )

    result = x[
        x[
            "doc_id"
        ].isin(
            selected_ids
        )
    ].copy()

    # ----------------------------------------------
    # Annotation 화면에서는 ranking/source 숨김
    # ----------------------------------------------

    result["_blind_order"] = (
        result[
            "doc_id"
        ]
        .astype(str)
        .map(
            lambda value:
            deterministic_hash(
                "blind|"
                + query_id
                + "|"
                + value
            )
        )
    )

    result = (
        result
        .sort_values(
            "_blind_order"
        )
        .head(
            n
        )
    )

    return result


def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--config",
        default=(
            "configs/benchmark/"
            "storesearch_ko_v1.yaml"
        ),
    )

    parser.add_argument(
        "--round",
        default="calibration_v1",
    )

    args = parser.parse_args()

    config = load_config(args.config)

    benchmark_dir = Path(
        config[
            "benchmark_dir"
        ]
    )

    queries = pd.read_csv(
        benchmark_dir
        / "queries.csv"
    )

    pool = pd.read_csv(
        benchmark_dir
        / "candidate_pool_internal.csv"
    )

    selected_queries = []

    for family in CALIBRATION_FAMILIES:

        family_df = queries[
            (
                queries[
                    "query_family"
                ]
                == family
            )
            &
            (
                queries[
                    "status"
                ]
                == "active"
            )
        ]

        if family_df.empty:

            raise ValueError(
                f"Unknown calibration family: "
                f"{family}"
            )

        query_row = select_query(
            family_df
        )

        selected_queries.append(
            query_row
        )

    selected_query_df = (
        pd.DataFrame(
            selected_queries
        )
    )

    frames = []

    for q in selected_query_df.itertuples(
        index=False
    ):

        group = pool[
            pool[
                "query_id"
            ]
            == q.query_id
        ]

        if group.empty:

            raise ValueError(
                f"No pool candidates for "
                f"{q.query_id}"
            )

        sampled = sample_candidates(
            group,
            q.query_id,
            CANDIDATES_PER_QUERY,
        )

        sampled[
            "intent_definition"
        ] = (
            q.intent_definition
        )

        frames.append(
            sampled
        )

    calibration = pd.concat(
        frames,
        ignore_index=True,
    )

    calibration[
        "judgment_id"
    ] = calibration.apply(
        lambda r:
        hashlib.sha256(
            (
                str(
                    r[
                        "query_id"
                    ]
                )
                + "|"
                + str(
                    r[
                        "doc_id"
                    ]
                )
            ).encode(
                "utf-8"
            )
        ).hexdigest()[:20],
        axis=1,
    )

    calibration[
        "annotation_round"
    ] = args.round

    calibration[
        "relevance"
    ] = ""

    calibration[
        "uncertain"
    ] = ""

    calibration[
        "annotator_note"
    ] = ""

    visible_columns = [

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

    output_dir = (
        benchmark_dir
        / "calibration"
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    # ----------------------------------------------
    # Human A
    # ----------------------------------------------

    a = calibration[
        visible_columns
    ].copy()

    a.insert(
        2,
        "annotator",
        "A",
    )

    a.to_csv(
        output_dir
        / "calibration_A_v1.csv",
        index=False,
        encoding="utf-8-sig",
    )

    # ----------------------------------------------
    # Human B
    #
    # order를 다시 섞어 A와 다른 순서 제공
    # ----------------------------------------------

    b = calibration.copy()

    b["_b_order"] = (
        b.apply(
            lambda r:
            deterministic_hash(
                "B|"
                + str(
                    r[
                        "query_id"
                    ]
                )
                + "|"
                + str(
                    r[
                        "doc_id"
                    ]
                )
            ),
            axis=1,
        )
    )

    b = b.sort_values(
        [
            "query_id",
            "_b_order",
        ]
    )

    b = b[
        visible_columns
    ].copy()

    b.insert(
        2,
        "annotator",
        "B",
    )

    b.to_csv(
        output_dir
        / "calibration_B_v1.csv",
        index=False,
        encoding="utf-8-sig",
    )

    manifest = {

        "calibration_queries":
            int(
                calibration[
                    "query_id"
                ].nunique()
            ),

        "candidates_per_query":
            CANDIDATES_PER_QUERY,

        "rows_per_annotator":
            int(
                len(
                    calibration
                )
            ),

        "query_families":
            selected_query_df[
                "query_family"
            ].tolist(),

        "purpose":
            (
                "Calibrate relevance guideline "
                "before full benchmark annotation."
            ),
    }

    (
        output_dir
        / "calibration_manifest.json"
    ).write_text(
        json.dumps(
            manifest,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(
        "========== "
        "CALIBRATION SHEETS CREATED "
        "=========="
    )

    print(
        json.dumps(
            manifest,
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()