"""Zero-shot 임베딩 모델 비교: 인코딩 → exact cosine 검색 → 공식 evaluator 채점.

BEIR 스타일로 관심사를 분리한다:
  1. `store_search_ai.models.*`      — 텍스트를 벡터로 바꾸는 것만 안다 (모델 교체 지점)
  2. `store_search_ai.retrieval.exact_search` — 벡터로 top-k run을 만드는 것만 안다 (ANN으로 교체될 지점)
  3. `scripts/16_evaluate_run.py`    — run을 채점하는 것만 안다 (모든 모델·모든 실험이 공유하는 단일 기준)

이 스크립트는 1, 2를 호출해 run.csv를 만든 뒤, **채점 로직을 재구현하지 않고**
`16_evaluate_run.py`를 그대로 서브프로세스로 호출한다. 원본 프로젝트의 `evaluation/metrics.py`는
`ir_measures` 기반 공식 evaluator와 독립적으로 nDCG/Recall 등을 재구현하고 있어서, 두 계산이
언젠가 어긋나면(예: binary threshold를 한쪽만 바꾸는 실수) 어느 쪽이 맞는지 알 수 없는 위험이
있었다. 그래서 이번 정리에서는 그 중복 구현을 가져오지 않고, 평가는 항상 이 한 경로로만
지나가게 했다 — `docs/DATA_AND_BENCHMARK_PROTOCOL.md`의 "모든 모델의 run은 동일 evaluator로
평가한다" 원칙 그대로다.

사용 예:
    # 실제 모델
    python scripts/17_run_zero_shot_eval.py --model-config configs/models/bge_m3.yaml --split val

    # 네트워크/GPU 없이 배관만 검증 (숫자는 무의미)
    python scripts/17_run_zero_shot_eval.py --dummy --split val
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import pandas as pd

from store_search_ai.pipeline.common import load_active_queries, load_config
from store_search_ai.retrieval.exact_search import ExactCosineSearch

TEMPLATE_COLUMNS = {
    "t1_minimal": "search_text_t1_minimal",
    "t2_market": "search_text_t2_market",
    "t3_market_type": "search_text_t3_market_type",
}


def build_encoder(args: argparse.Namespace):
    if args.dummy:
        from store_search_ai.models.random_encoder import RandomEncoder

        return RandomEncoder()

    if not args.model_config:
        raise SystemExit("--model-config이 필요합니다 (또는 --dummy로 배관만 검증)")

    from store_search_ai.models.sentence_transformer_encoder import (
        SentenceTransformerEncoder,
    )

    return SentenceTransformerEncoder.from_yaml(args.model_config)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/benchmark/storesearch_ko_v1.yaml")
    parser.add_argument("--model-config", help="configs/models/*.yaml")
    parser.add_argument("--dummy", action="store_true", help="RandomEncoder로 배관만 검증 (모델 다운로드 없음)")
    parser.add_argument("--split", choices=["train", "val", "test"], default="val")
    parser.add_argument("--template", choices=list(TEMPLATE_COLUMNS), default="t1_minimal")
    parser.add_argument("--top-k", type=int, default=100)
    parser.add_argument("--tag", help="기본값: 모델 이름 (또는 dummy_smoke_test)")
    parser.add_argument("--output-dir", default="results/zero_shot")
    parser.add_argument("--skip-evaluate", action="store_true", help="run.csv만 만들고 채점은 생략")
    args = parser.parse_args()

    config = load_config(args.config)
    benchmark_dir = Path(config["benchmark_dir"])
    corpus = pd.read_parquet(config["corpus_path"])
    queries = load_active_queries(benchmark_dir)
    queries = queries[queries["split"] == args.split].copy()

    text_col = TEMPLATE_COLUMNS[args.template]

    encoder = build_encoder(args)
    tag = args.tag or encoder.name

    print(f"[INFO] encoding corpus ({len(corpus)} docs, template={args.template}) ...")
    corpus_embeddings = encoder.encode_corpus(corpus[text_col].fillna("").tolist())

    print(f"[INFO] encoding {len(queries)} {args.split} queries ...")
    query_embeddings = encoder.encode_queries(queries["query"].tolist())

    searcher = ExactCosineSearch(
        corpus_embeddings=corpus_embeddings,
        doc_ids=corpus["doc_id"].tolist(),
    )
    run = searcher.search(
        query_embeddings=query_embeddings,
        query_ids=queries["query_id"].tolist(),
        top_k=args.top_k,
        system=tag,
    )

    output_dir = Path(args.output_dir) / tag
    output_dir.mkdir(parents=True, exist_ok=True)
    run_path = output_dir / f"run_{args.template}_{args.split}.csv"
    run.to_csv(run_path, index=False, encoding="utf-8-sig")
    print(f"[INFO] run 저장: {run_path} ({len(run)} rows)")

    if args.skip_evaluate:
        return

    qrels_path = benchmark_dir / f"qrels_{args.split}.trec"
    eval_tag = f"{tag}_{args.split}"
    cmd = [
        sys.executable,
        str(Path(__file__).with_name("16_evaluate_run.py")),
        "--qrels", str(qrels_path),
        "--run", str(run_path),
        "--tag", eval_tag,
    ]
    print(f"[INFO] 공식 evaluator 호출: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


if __name__ == "__main__":
    main()
