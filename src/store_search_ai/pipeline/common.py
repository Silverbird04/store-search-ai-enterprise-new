"""scripts/*.py 전반에서 반복되던 보일러플레이트를 모은 공통 유틸.

각 함수는 기존 스크립트들에 있던 코드를 그대로 옮긴 것이며 동작을 바꾸지 않았다
(리팩터링 전후로 산출물이 바이트 단위로 동일한지 재실행해서 확인함 —
docs/PIPELINE.md 및 각 스크립트 상단 주석 참고).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd
import yaml


def load_config(path: str | Path) -> dict:
    """벤치마크/데이터 설정 yaml을 로드한다.

    05, 06, 07, 08, 09, 10, 11, 12, 14, 15 스크립트에서 각자 구현하던
    `yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))` 패턴을 통합.
    """

    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def get_benchmark_dir(config: dict, create: bool = True) -> Path:
    """config["benchmark_dir"]를 Path로 반환하고, 필요하면 생성한다."""

    benchmark_dir = Path(config["benchmark_dir"])
    if create:
        benchmark_dir.mkdir(parents=True, exist_ok=True)
    return benchmark_dir


def sha256_file(path: str | Path) -> str:
    """파일 전체를 청크 단위로 읽어 SHA256 hexdigest를 반환한다.

    05_init_benchmark.py, 14_build_qrels.py에 각각 있던 동일 구현을 통합.
    (config/queries 변경 추적용 — data/corpus의 text_hash와는 별개 용도)
    """

    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_active_queries(benchmark_dir: str | Path) -> pd.DataFrame:
    """benchmark_dir/queries.csv를 읽어 status == "active" 인 행만 반환한다.

    05(생성 직후 제외)를 뺀 06, 07, 08, 10, 11, 14, 15에서 반복되던
    `queries = pd.read_csv(...); queries = queries[queries["status"] == "active"]`
    패턴을 통합.
    """

    queries = pd.read_csv(Path(benchmark_dir) / "queries.csv", encoding="utf-8-sig")
    return queries[queries["status"].astype(str).str.lower() == "active"].copy()


def write_trec_qrels(frame: pd.DataFrame, path: str | Path) -> None:
    """query_id/doc_id/relevance 컬럼을 가진 DataFrame을 TREC qrels 포맷으로 저장한다.

    포맷: "{query_id} 0 {doc_id} {relevance}"
    12_prepare_full_annotations.py, 14_build_qrels.py에 각각 있던 동일 구현을 통합.
    """

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in frame.itertuples(index=False):
            f.write(f"{row.query_id} 0 {row.doc_id} {int(row.relevance)}\n")
