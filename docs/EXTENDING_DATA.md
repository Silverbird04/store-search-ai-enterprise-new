# 데이터 확장 가이드

## 1. Raw 데이터를 새 파일로 교체하기

`configs/data/default.yaml`이 raw xlsx를 읽는 방식을 정의합니다.

```yaml
dataset_version: stores_v003

excel:
  region_source: address
  sheets:
    Sheet1: {}

region_aliases:
  서울특별시: 서울특별시
  서울: 서울특별시
  ...
```

- **행(매장)만 늘어나는 경우** (같은 시트에 행이 추가됨): 새 xlsx 파일을 `data/raw/`에 새 파일명으로
  추가하고(기존 파일은 그대로 둡니다 — raw는 수정하지 않습니다), `dataset_version`을 올린 뒤
  `--input`으로 새 파일 경로를 넘기면 됩니다. 컬럼 헤더(`가맹점명`, `사업자번호`, `주소`, `LAT`, `LOT`,
  `법정동코드`, `시장분류코드`, `시장명`, `취급품목`, `카드결제여부`, `모바일결제여부`)가 기존과 동일해야
  `column_map`이 그대로 맞습니다. `가맹점번호`, `No.`(행번호) 컬럼은 없어도 됩니다(옵션).
- **지역(시도) 판별**: 전국 데이터는 시트가 하나뿐이라 시트명으로 지역을 구분할 수 없으므로, **주소
  첫 토큰(시도)을 `region_aliases`로 정규화**해서 `source_region`을 만듭니다
  (`src/store_search_ai/data/region.py::normalize_region`). alias 테이블에 없는 표기가 나오면
  `source_region=None`, `source_region_status="UNMAPPED"`로 조용히 통과합니다(파이프라인이 죽지
  않음) — 새로운 약칭/오탈자가 보이면 `region_aliases`에 한 줄 추가하세요.
  - 과거처럼 지역별로 시트가 나뉜 파일을 다시 쓰려면, 해당 시트 아래에
    `source_region: <값>`을 직접 적으면 그 시트는 주소 파싱 대신 고정값을 씁니다(레거시 호환).
- 컬럼 이름이 바뀌었다면 `column_map`도 함께 수정해야 합니다. `required_raw_columns`에 있는 컬럼이
  없으면 `02_preprocess_data.py`가 즉시 에러를 냅니다(의도된 동작 — 결측 스키마를 조용히 통과시키지 않음).

교체 후 실행 순서는 그대로입니다:

```bash
python scripts/01_profile_data.py --input data/raw/<새파일>.xlsx
python scripts/02_preprocess_data.py --input data/raw/<새파일>.xlsx
python scripts/03_analyze_items.py
python scripts/04_build_corpus.py
```

**store_id는 append-only 레지스트리(`data/registry/store_registry.parquet`)를 통해 발급됩니다.** 기존
매장의 `entity_fingerprint`(사업자번호+가맹점명+주소 해시)가 그대로면 같은 `store_id`가 재사용되고,
신규 매장에는 새 UUID가 발급됩니다. 즉 데이터를 늘려도 **기존 매장의 store_id/doc_id는 바뀌지
않습니다** — 이미 만들어둔 qrels(query_id, doc_id 매핑)가 깨지지 않는다는 뜻입니다. 단, 레지스트리
파일을 지우고 재실행하면 전체 store_id가 새로 발급되므로 지우면 안 됩니다.

`dataset_version`(전처리 산출물 버전)과 `corpus_version`(`configs/benchmark/storesearch_ko_v1.yaml`,
corpus 산출물 버전)은 별도로 관리됩니다. 데이터가 바뀌면 최소 `dataset_version`을 올리고, 그 데이터로
만든 corpus가 이전과 스키마/내용이 다르면 `corpus_version`도 올리세요. 파일 경로는 두 버전 문자열에서
자동으로 파생되므로 스크립트를 고칠 필요는 없습니다.

## 2. Query family를 직접 정의하기

`configs/benchmark/query_families_v1.yaml`이 유일한 정의 파일입니다. `05_init_benchmark.py`가 이 파일만 읽어서
`queries.csv`를 만듭니다 (다른 데이터 소스 없음).

각 family는 이런 구조입니다:

```yaml
- family: chicken              # family 이름 (영문, snake_case, 전체 파일 내에서 유일해야 함)
  split: train                 # train / val / test 중 하나. 같은 family가 두 split에 걸치면 안 됨(leakage 방지, 13_validate_benchmark.py가 검사)
  intent_definition: 조리된 치킨·통닭류를 판매하는 음식점   # 애노테이터에게 보여줄 의도 설명 (모델 입력 아님)
  positive_terms: [치킨, 통닭, 치킨전문점, 닭강정]          # (a) pooling 시 targeted term-match 채널, (b) train qrels 자동 라벨링(relevance=3)에 사용
  boundary_terms: [생닭, 닭고기, 육계]                     # (a) 경계 사례 pooling 채널, (b) train qrels 자동 라벨링(relevance=1)에 사용
  queries:                     # 이 family에 속한 실제 query variant 목록. 최소 3개 이상 필요
    - type: exact
      text: 치킨
    - type: synonym
      text: 통닭
    - type: paraphrase
      text: 닭튀김 파는 곳
    - type: colloquial
      text: 치킨 먹을 데
```

**주의**: `positive_terms`/`boundary_terms`는 pooling 후보 발굴뿐 아니라(기존 역할) **train qrels
자동 라벨링에도 그대로 쓰입니다**(`08_auto_label_train_qrels.py`). val/test는 사람이 직접 판정하므로
이 두 필드가 relevance에 직접 영향을 주지 않지만, train은 이 필드가 곧 relevance 규칙이 됩니다 — 너무
느슨하거나(오탐 많음) 너무 좁은(recall 낮음) term을 넣지 않도록 주의하세요.

새 family를 추가하는 절차:

1. `family` 이름이 파일 전체에서 겹치지 않는지 확인 (겹치면 `05_init_benchmark.py`가 에러)
2. `split`을 정한다 — **한 family는 train/val/test 중 하나에만 속함** (같은 의도의 query를 여러 split에
   나눠 넣지 않는 것이 원칙; 이렇게 하면 train에서 본 것과 완전히 같은 개념이 test에 나오는 것을 방지)
3. `queries` 안에 최소 3개 이상 variant 작성 — `type`은 자유 문자열이지만 기존 관례는
   `exact`(정식 명칭) / `synonym`(동의어) / `paraphrase`(설명형) / `colloquial`(구어체) 4종
4. 같은 query 텍스트(공백 정규화 + casefold 기준)가 파일 전체에서 중복되면 안 됨 — 중복 시 에러
5. `query_id`는 `q_{family}_{순번:02d}` 형식으로 스크립트가 자동 생성 (직접 안 정해도 됨)

작성 후:

```bash
python scripts/05_init_benchmark.py
python scripts/13_validate_benchmark.py --stage pilot
```

`13_validate_benchmark.py`가 family당 split 하나만 있는지, query 텍스트 중복이 없는지 등을 검사해줍니다.
**주의**: query family를 추가/변경하면 pooling(06~07)부터 다시 실행해야 합니다. train은
`08_auto_label_train_qrels.py`를 다시 돌리면 자동으로 갱신되고, val/test는 새로 추가된 query가 애노테이션이
전혀 안 되어 있으므로(`docs/PIPELINE.md` 5~6절) 사람이 새로 라벨링해야 qrels에 반영됩니다.
