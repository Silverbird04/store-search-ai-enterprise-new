# StoreSearch-KO v1 — 파이프라인 실행 순서

이 문서는 `scripts/01_*.py` ~ `scripts/16_*.py` 를 어떤 순서·인자로 실행해야 하는지 정의합니다.
번호는 실행 순서를 뜻하며, **09번(`09_merge_calibration_annotations.py`)만 파이프라인에서 두 번 재사용**됩니다
(calibration v1 병합, calibration v2 병합). 그 외에는 번호=실행 순서=1회 실행입니다.

move 프로젝트에 있던 다음 4개 스크립트는 **최종 산출물 생성에 실제로 쓰이지 않는 것으로 코드를 추적해 확인**했고,
이번 정리에서 제외했습니다 (이유는 각 항목 참고):

| 제외된 스크립트 | 이유 |
|---|---|
| `06_build_annotation_pool.py` (구버전) | lexical run 기반 pooling(`06_generate_lexical_runs.py`+`07_build_annotation_pool.py`)으로 대체됨 |
| `09b_finalize_provisional_qrels.py` | `14_build_qrels.py`가 adjudication 완료 파일에서 val/test qrels를 자체적으로 재계산(`build_split_qrels`)하므로 이 스크립트의 출력은 최종 빌드에 입력되지 않음 |
| `10a_pre_build_qrels.py` | `--freeze` 플래그와 이름으로 볼 때 애노테이션 완료 전 중간 스냅샷용. 최종 스키마(=`split` 컬럼 포함)가 아님 |
| `12a_evaluate_run2.py` | 실험용 스크립트. 실제 채택된 evaluator는 `12_evaluate_run.py`(현재 `16_evaluate_run.py`, 팀 공유 `scripts/evaluate_run.py`와 byte-identical 확인됨) |

## 구조 개선 사항 (Makefile / 공통 모듈)

- **`Makefile`**: 01~07(사람 개입 없는 구간)을 파일 의존관계로 자동 재실행합니다. `make corpus`, `make pool`,
  `make validate` 등. 사람 개입이 필요한 08~14는 필요한 사람 산출물이 없으면 안내 메시지와 함께 실패하는
  가드 타겟(`make prepare-full-annotations` 등)으로 제공합니다. 자세한 목록은 `make help`.
- **`src/store_search_ai/pipeline/common.py`**: 여러 스크립트에 중복돼 있던 yaml 설정 로드, active query 필터링,
  sha256 해시, TREC qrels 저장 로직을 통합했습니다. 05~15의 스크립트를 리팩터링 후 재실행해서 리팩터링 전과
  산출물이 바이트 단위로 동일함을 확인했고, `14_build_qrels.py`는 실제 adjudication 완료본으로도 재검증했습니다
  (아래 참고).

## 0. 환경 준비 (최초 1회)

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## 1. 데이터 전처리 ~ Corpus 구축 (사람 개입 없음, 전부 재실행 가능)

```bash
python scripts/01_profile_data.py --input data/raw/stores.xlsx
python scripts/02_preprocess_data.py --input data/raw/stores.xlsx
python scripts/03_analyze_items.py
python scripts/04_build_corpus.py
```

- 01: raw xlsx 프로파일링 리포트만 생성 (`artifacts/reports/data_profile.json`)
- 02: raw → `data/interim/store_records_v002.parquet` → `data/processed/stores_master_v002.parquet`.
  이 단계에서 `data/registry/store_registry.parquet`에 매장별 영구 `store_id`(UUID)가 발급/누적됩니다.
  **레지스트리는 append-only이므로 재실행해도 기존 매장의 store_id가 바뀌지 않습니다.**
- 03: 취급품목 텍스트 품질 리포트 (`artifacts/reports/item_analysis_v002/`) — 파이프라인 진행에 필수는 아니지만 taxonomy 정비에 사용
- 04: `data/corpus/store_corpus_v001.parquet` 생성 (T1/T2/T3 검색 텍스트 템플릿 포함). **본 프로젝트는 T1(`search_text_t1_minimal` = 가맹점명+취급품목)을 공식 document representation으로 사용**

검증 완료: 이 4단계는 실제 `data/raw/stores.xlsx`로 재실행해서 매장 13,832건, T1 포맷이 기존 산출물과 동일함을 확인했습니다.

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
  **이 스크립트는 `--round` 인자로 여러 번 누적 호출 가능**합니다 (예: 나중에 dense retrieval 결과로 pool을 확장하고 싶다면
  `--round dense_round1`로 다시 실행 — 단, 이번 정리 범위(01~16)에는 dense pooling 확장 라운드 스크립트는 포함하지 않았습니다).

## 4. Calibration (애노테이터 간 신뢰도 보정) — **사람 개입 필요**

이 단계는 실제로 2명 이상의 사람이 라벨을 매겨야 합니다. 자동화되지 않습니다.

```bash
# v1
python scripts/08_make_calibration_v1_sheets.py
# → benchmark/storesearch_ko_v1/calibration/calibration_A_v1.csv, calibration_B_v1.csv 를
#   두 명의 애노테이터에게 전달 → 각자 relevance 채워서 회수

python scripts/09_merge_calibration_annotations.py \
  --a <완료된 calibration_A_v1.csv> \
  --b <완료된 calibration_B_v1.csv>
# → Cohen's kappa 등 합치도(agreement) 리포트 생성. 기준 미달이면 가이드라인을 보완하고 v2로 진행.

# v2 (v1에서 경계가 불명확했던 family들로 재검증)
python scripts/10_make_calibration_v2_sheets.py --previous-a <완료된 calibration_A_v1.csv>
# → calibration_A_v2.csv, calibration_B_v2.csv 배포/회수

python scripts/09_merge_calibration_annotations.py \
  --a <완료된 calibration_A_v2.csv> \
  --b <완료된 calibration_B_v2.csv>
```

## 5. 본 애노테이션 (Full annotation) — **사람 개입 필요**

```bash
python scripts/11_make_full_annotation_sheets.py
```

- train: 애노테이터 A만 단일 라벨링 (`annotation_A_train.csv`)
- val/test: 애노테이터 A, B 모두 라벨링 (`annotation_A_val.csv`/`_test.csv`, `annotation_B_val.csv`/`_test.csv`)

각 시트를 사람이 채운 뒤 `*_completed.csv`로 저장:

```
benchmark/storesearch_ko_v1/annotations/full_annotation_v1/completed/
  annotation_A_train_completed.csv
  annotation_A_val_completed.csv
  annotation_A_test_completed.csv
  annotation_B_val_completed.csv
  annotation_B_test_completed.csv
```

```bash
python scripts/12_prepare_full_annotations.py
```

이 스크립트가 하는 일:
- train: 단일 라벨을 그대로 provisional qrels로 변환 (`qrels/provisional_v1/qrels_train_provisional.csv`)
- val/test: A·B 라벨을 비교해서 **일치하는 것은 자동 확정**, **불일치(`needs_adjudication=True`)는 사람이 봐야 할 목록**을
  `analysis/adjudication_val_test_needed_only.csv`로 분리 저장

## 6. Adjudication (이견 조정) — **사람 개입 필요**

`adjudication_val_test_needed_only.csv`를 3rd adjudicator(또는 원 애노테이터 협의)가 검토하여
`final_relevance`, `human_adjudication_note`, (제외할 경우) `exclude_from_gold`를 채운 뒤
`adjudication_val_test_needed_only_completed.csv`로 저장합니다.

```bash
python scripts/13_apply_adjudication_patch.py \
  --patch benchmark/storesearch_ko_v1/annotations/full_annotation_v1/analysis/adjudication_val_test_needed_only_completed.csv
```

→ `adjudication_val_test_full_completed.csv` (전체 val/test 최종 판정: 자동 합의분 + 조정분 병합) 생성.

**주의**: "A/B 점수를 평균 내지 않는다"가 원칙입니다 (`12_prepare_full_annotations.py`가 요약에 명시). 반드시
`needs_adjudication=True`인 모든 행을 사람이 확정한 뒤 다음 단계로 넘어가야 합니다.

## 7. 최종 Qrels 빌드 + 검증 + 평가

```bash
python scripts/14_build_qrels.py \
  --adjudication benchmark/storesearch_ko_v1/annotations/full_annotation_v1/analysis/adjudication_val_test_full_completed.csv

python scripts/15_validate_benchmark.py --stage final
python scripts/16_evaluate_run.py --run <모델_run.csv> --tag <실험명>
```

- 14: `queries.csv`(active만) + adjudication 완료본(val/test) + provisional train qrels를 합쳐
  `qrels_train.csv/.trec`, `qrels_val.csv/.trec`, `qrels_test.csv/.trec`, `qrels.csv/.trec`, `benchmark_manifest.json`을
  **`benchmark/storesearch_ko_v1/` 바로 아래(서브폴더 없이)** 생성합니다.
- 15: 중복/미판정 쿼리/스키마 무결성 검증. `--stage final`에서는 최소 쿼리 수, double annotation coverage 등도 검사합니다.
- 16: 공식 evaluator. `--qrels`(기본값 `qrels_val.trec`), `--run`, `--tag` 필요.
  Primary metric은 `nDCG@10`, bootstrap 95% CI 포함.

> **이번에 발견/수정한 버그**: `ir_measures.read_trec_qrels()`/`read_trec_run()`은 1회용 generator를 반환합니다.
> 원본 스크립트는 이를 그대로 반환해서, 집계(aggregate) 계산 후 per-query 계산 시 generator가 이미 소진되어
> **`.trec` 형식 qrels/run을 쓰면(기본값이 `.trec`) 항상 "No per-query metric results" 오류로 실패**했습니다.
> `list(...)`로 감싸도록 수정했고, 더미 run으로 CSV qrels/TREC qrels 양쪽 다 정상 동작을 확인했습니다.
> 이 수정은 팀 공유용 `scripts/evaluate_run.py`에도 동일하게 반영했습니다.

## 지금 재실행이 필요 없는 이유

`store-search-ai-enterprise-share`의 최종 산출물(corpus, queries.csv, qrels_train/val/test.csv, qrels.csv/.trec,
benchmark_manifest.json)은 이미 이 파이프라인의 결과물이며, 위 4~6단계(calibration/애노테이션/adjudication)에
해당하는 **원본 사람 판정 데이터 중 val/test의 최종 adjudication 완료본은 이번 전달 범위에 포함되어 있어서**
`benchmark/storesearch_ko_v1/annotations/full_annotation_v1/analysis/adjudication_val_test_full_completed.csv`로
가져와 두었습니다. `14_build_qrels.py`를 이 실제 파일로 재실행해서 **qrels_val.csv/qrels_test.csv가 share
최종본과 바이트 단위로 완전히 일치함을 확인**했습니다 — 리팩터링이 실제 프로덕션 데이터에 대해서도
정확히 같은 결과를 낸다는 뜻입니다.

다만 **train 쪽 원본 애노테이션 완료본(`annotation_A_train_completed.csv` 등 5개 파일 중 4개, `12_prepare_full_annotations.py`
입력)은 이번 전달 범위에 없어서** 5~6단계(calibration, 본 애노테이션 회수)와 `12_prepare_full_annotations.py`
실행 자체는 검증하지 못했습니다. 데이터를 늘리고 query family를 재정의하면, 4~6단계는 **반드시 이 순서대로
사람이 직접 라벨링/조정을 다시 거쳐야** 새 qrels_train/val/test.csv가 나옵니다. 이 과정을 건너뛰고 자동으로
생성하는 방법은 없습니다 (관련 논문들도 gold-standard IR 벤치마크는 이렇게 사람이 판정한 값을 씁니다 — TREC
스타일 pooling + double annotation + adjudication 방식을 그대로 따른 설계입니다).
