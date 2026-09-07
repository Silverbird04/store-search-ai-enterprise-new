# StoreSearch-KO v1 — 파이프라인 실행 순서

`scripts/01_*.py` ~ `scripts/16_*.py`를 어떤 순서·인자로 실행해야 하는지 정의합니다. 번호 = 실행 순서.

## 0. 환경 준비 (최초 1회)

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Python 3.11만 지원합니다 (`ir-measures`가 Python 3.12+에서 제거된 `ast.Num`을 사용).

## 1. 데이터 전처리 ~ Corpus 구축 (사람 개입 없음, 전부 재실행 가능)

```bash
python scripts/01_profile_data.py --input data/raw/stores_20260907.xlsx
python scripts/02_preprocess_data.py --input data/raw/stores_20260907.xlsx
python scripts/03_analyze_items.py
python scripts/04_build_corpus.py
```

- 01: raw xlsx 프로파일링 리포트만 생성 (`artifacts/reports/data_profile.json`)
- 02: raw → `data/interim/store_records_{dataset_version}.parquet` →
  `data/processed/stores_master_{dataset_version}.parquet`. 이 단계에서
  `data/registry/store_registry.parquet`에 매장별 영구 `store_id`(UUID)가 발급/누적됩니다.
  **레지스트리는 append-only이므로 재실행해도 기존 매장의 store_id가 바뀌지 않습니다.**
  단일 시트 데이터의 지역(시도)은 주소에서 자동 파생됩니다(`configs/data/default.yaml`의
  `region_aliases`, `src/store_search_ai/data/region.py`).
- 03: 취급품목 텍스트 품질 리포트 (`artifacts/reports/item_analysis_{dataset_version}/`) — 파이프라인
  진행에 필수는 아니지만 taxonomy 정비에 사용
- 04: `data/corpus/{corpus_version}.parquet` 생성 (T1/T2/T3 검색 텍스트 템플릿 포함). **본 프로젝트는
  T1(`search_text_t1_minimal` = 가맹점명+취급품목)을 공식 document representation으로 사용**

`dataset_version`(`configs/data/default.yaml`)/`corpus_version`(`configs/benchmark/storesearch_ko_v1.yaml`)만
올리면 파일명이 자동으로 갱신되므로, 데이터가 또 바뀌어도 스크립트 자체는 수정할 필요가 없습니다.

## 2. 벤치마크 Query Set 초기화

```bash
python scripts/05_init_benchmark.py
```

`configs/benchmark/query_families_v1.yaml`을 읽어 `benchmark/storesearch_ko_v1/queries.csv` +
`annotation_guideline.md`를 생성합니다. **query family를 직접 정의/수정하려면 이 yaml 파일을 편집하세요**
(자세한 방법은 `docs/EXTENDING_DATA.md` 참고).

## 3. Pooling (lexical) — 사람 개입 없음

```bash
python scripts/06_generate_lexical_runs.py
python scripts/07_build_annotation_pool.py --round lexical_v1
```

- 06: TF-IDF(char/word) + BM25 세 시스템으로 top-40 lexical run을 생성 (`benchmark/storesearch_ko_v1/runs/pooling/`)
- 07: 위 run들을 합쳐 애노테이션 대상 후보 pool을 만듭니다 (`candidate_pool_internal.csv`).
  **이 스크립트는 `--round` 인자로 여러 번 누적 호출 가능**합니다 (예: 나중에 dense retrieval 결과로 pool을
  확장하고 싶다면 `--round dense_round1`로 다시 실행).

## 4. Train qrels 자동 라벨링 (weak supervision) — 사람 개입 없음

```bash
python scripts/08_auto_label_train_qrels.py
```

07에서 만든 candidate pool의 provenance(`pool_sources_json`)를 그대로 읽어, query family의
`positive_terms`에 매치된 문서는 relevance=3, `boundary_terms`에만 매치된 문서는 relevance=1, 둘 다
아니면(=lexical run/random 채널로만 pool에 들어옴) relevance=0으로 라벨링합니다.
`benchmark/storesearch_ko_v1/qrels/provisional_v1/qrels_train_provisional.{csv,trec}`에 저장되며, 이
경로/스키마는 12번 스크립트의 `--train-qrels` 기본값과 동일합니다.

이 qrels는 **사람이 만든 gold가 아니라 규칙 기반 weak label(silver)**입니다. Train은 fine-tuning
signal로만 쓰이므로(모델 성능을 "보고"하는 데는 쓰지 않으므로) 이 방식을 씁니다 — GPL/E5/BGE/
Qwen3-Embedding 등 dense retrieval 논문들이 대규모 학습 데이터를 만들 때 쓰는 것과 같은 weak/distant
supervision 방식입니다.

## 5. 본 애노테이션 (Val/Test, Full annotation) — **사람 개입 필요**

```bash
python scripts/09_make_full_annotation_sheets.py
```

- val/test만 대상으로, 애노테이터 A, B가 각각 독립적으로 라벨링 (`annotation_A_val.csv`/`_test.csv`,
  `annotation_B_val.csv`/`_test.csv`)
- train 시트는 생성하지 않습니다(4단계에서 이미 자동 라벨링됨).

각 시트를 사람이 채운 뒤 `*_completed.csv`로 저장:

```
benchmark/storesearch_ko_v1/annotations/full_annotation_v1/completed/
  annotation_A_val_completed.csv
  annotation_A_test_completed.csv
  annotation_B_val_completed.csv
  annotation_B_test_completed.csv
```

```bash
python scripts/10_prepare_full_annotations.py
```

A·B 라벨을 비교해서 **일치하는 것은 자동 확정**, **불일치(`needs_adjudication=True`)는 사람이 봐야 할
목록**을 `analysis/adjudication_val_test_needed_only.csv`로 분리 저장합니다.

## 6. Adjudication (이견 조정) — **사람 개입 필요**

`adjudication_val_test_needed_only.csv`를 3rd adjudicator(또는 원 애노테이터 협의)가 검토하여
`final_relevance`, `human_adjudication_note`, (제외할 경우) `exclude_from_gold`를 채운 뒤
`adjudication_val_test_needed_only_completed.csv`로 저장합니다.

```bash
python scripts/11_apply_adjudication_patch.py \
  --patch benchmark/storesearch_ko_v1/annotations/full_annotation_v1/analysis/adjudication_val_test_needed_only_completed.csv
```

→ `adjudication_val_test_full_completed.csv` (전체 val/test 최종 판정: 자동 합의분 + 조정분 병합) 생성.

**주의**: "A/B 점수를 평균 내지 않는다"가 원칙입니다. `needs_adjudication=True`인 모든 행을 사람이
확정한 뒤 다음 단계로 넘어가야 합니다.

## 7. 최종 Qrels 빌드 + 검증 + 평가

```bash
python scripts/12_build_qrels.py \
  --adjudication benchmark/storesearch_ko_v1/annotations/full_annotation_v1/analysis/adjudication_val_test_full_completed.csv

python scripts/13_validate_benchmark.py --stage final
python scripts/14_evaluate_run.py --run <모델_run.csv> --tag <실험명>
```

- 12: `queries.csv`(active만) + adjudication 완료본(val/test) + 08번이 만든 train qrels를 합쳐
  `qrels_train.csv/.trec`, `qrels_val.csv/.trec`, `qrels_test.csv/.trec`, `qrels.csv/.trec`,
  `benchmark_manifest.json`을 `benchmark/storesearch_ko_v1/` 바로 아래(서브폴더 없이) 생성합니다.
- 13: 중복/미판정 쿼리/스키마 무결성 검증. `--stage final`에서는 최소 쿼리 수, double annotation
  coverage 등도 검사합니다.
- 14: 공식 evaluator. `--qrels`(기본값 `qrels_val.trec`), `--run`, `--tag` 필요. Primary metric은
  `nDCG@10`, bootstrap 95% CI 포함.

## 8. Zero-shot 모델 비교 (선택)

```bash
python scripts/15_run_zero_shot_eval.py --model-config configs/models/bge_m3.yaml --split val
python scripts/16_score_zero_shot_runs.py --split val
```

`docs/MODELING.md` 참고.

## 사람 개입이 필요한 범위

01~08(전처리~corpus~pooling~train 자동 라벨링)까지는 데이터가 바뀌어도 사람 개입 없이 끝까지
재실행됩니다. **09~11(val/test 사람 이중 라벨링 + adjudication)만 실제 사람이 거쳐야** 최종
`qrels_val.csv`/`qrels_test.csv`가 나옵니다. 이 과정을 건너뛰고 자동으로 생성하는 방법은 없습니다 —
TREC 스타일 pooling + double annotation + adjudication 방식을 그대로 따른 설계입니다.

`archive/legacy_v002_benchmark/`에는 이전 데이터(대전/세종 13,832개 매장) 기준으로 이미 완료된 val/test
gold qrels가 참고용으로 보존되어 있습니다. 새 corpus(`stores_v003`, 214,043개 매장)에는 store_id가
상당수 달라 그대로 재사용할 수 없습니다.
