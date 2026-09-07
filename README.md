# Store Search AI — StoreSearch-KO v1

한국어 매장 검색 Dense Retrieval 프로젝트. 대전/세종 지역 가맹점 데이터(현재 13,832개)를 기반으로
자연어 query("통닭", "고기 파는 곳", "머리 자르는 곳" 등)에 대한 매장 검색 벤치마크와 파이프라인을 제공합니다.

이 프로젝트는 기존 프로젝트(`store-search-ai-enterprise-move`)에서 실제로 최종 산출물을 만드는 정본
스크립트만 추려서, 번호 중복 없이 실행 순서대로 재정렬한 버전입니다. 자세한 정리 기준은
`docs/PIPELINE.md` 상단을 참고하세요.

## 핵심 원칙 (원본 프로젝트에서 유지)

1. Raw 파일(`data/raw/stores.xlsx`)은 수정하지 않습니다.
2. 사업자번호/가맹점번호/법정동코드는 문자열로 취급합니다.
3. 가맹점번호는 결측·전화번호 형태가 섞여 있어 PK로 사용하지 않습니다.
4. 매장 PK(`store_id`)는 `data/registry/store_registry.parquet`에서 append-only로 발급되는 UUID이며,
   raw의 어떤 컬럼과도 1:1로 단정하지 않습니다.
5. 취급품목 NULL은 삭제·기타로 채우지 않고 그대로 결측 처리합니다.
6. 전처리/벤치마크 구축/평가를 단계별 스크립트로 분리합니다 (아래 파이프라인 참고).
7. **Document representation은 T1(`search_text_t1_minimal` = 가맹점명 + 취급품목)을 공식으로 사용합니다.**
   예: `가맹점명: 열매서점 / 취급품목: 서적`

## 설치

```bash
python -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

`pip install -e ".[embedding]"`는 dense encoder(torch/transformers/sentence-transformers)가 필요할 때만
추가로 설치하세요.

## 폴더 구조

```
configs/
  data/default.yaml              raw xlsx 스키마·컬럼 매핑·검증 규칙
  benchmark/storesearch_ko_v1.yaml   벤치마크 경로/파라미터(pooling, annotation, validation, evaluation)
  benchmark/query_families_v1.yaml   query family 정의 (직접 편집 대상, docs/EXTENDING_DATA.md 참고)
  evaluation/default.yaml        공식 metric 목록 (문서화용 — 16_evaluate_run.py는 metric을 자체 상수로 갖고 있음)
  models/*.yaml                  비교 대상 임베딩 모델 설정 (zero-shot 비교/파인튜닝 후보)
src/store_search_ai/
  common/io.py                   yaml/엑셀 로딩 유틸
  data/                          전처리·registry·corpus·search_text 핵심 로직
  pipeline/common.py             scripts/*.py 공통 보일러플레이트 (yaml 로드, TREC 저장 등)
  models/                        임베딩 인코더 (BEIR 스타일, docs/MODELING.md 참고)
  retrieval/exact_search.py      exact cosine 검색 (ANN 이전 단계 모델 비교용)
scripts/01_*.py ~ scripts/17_*.py   파이프라인 본체 (docs/PIPELINE.md 참고)
data/
  raw/stores.xlsx                원본 (수정 금지)
  interim/ processed/ registry/  전처리 중간/최종 산출물
  corpus/store_corpus_v001.parquet   검색 대상 corpus (T1/T2/T3 템플릿 포함)
benchmark/storesearch_ko_v1/
  queries.csv, annotation_guideline.md, qrels*.csv/.trec, benchmark_manifest.json
tests/                           단위 테스트 (pytest)
colab/                          Colab(GPU)에서 임베딩 모델 인코딩하는 스크립트 (colab/README.md 참고)
docs/
  PIPELINE.md                    16단계 실행 순서·인자·사람 개입 지점 (필독)
  EXTENDING_DATA.md               stores.xlsx 확장 / query family 재정의 방법
  MODELING.md                    임베딩 모델 zero-shot 비교 구조 (BEIR 스타일)
```

## 빠른 시작

```bash
pytest tests/ -q

make corpus   # 01~04를 필요한 만큼만 자동 재실행 (Makefile 참고)
```

또는 직접:

```bash
python scripts/01_profile_data.py --input data/raw/stores.xlsx
python scripts/02_preprocess_data.py --input data/raw/stores.xlsx
python scripts/03_analyze_items.py
python scripts/04_build_corpus.py
```

여기까지는 사람 개입 없이 100% 재실행 가능하며, 실제로 이 순서로 재실행해서 매장 13,832건,
`search_text_t1_minimal` 포맷이 기존 벤치마크와 정확히 일치함을 확인했습니다.

벤치마크(query/qrels) 재구축, calibration/애노테이션/adjudication 등 **사람이 개입해야 하는 나머지 단계**는
`docs/PIPELINE.md`를 참고하세요.

## 현재 벤치마크 상태

`benchmark/storesearch_ko_v1/`에는 기존 프로젝트에서 이미 완료된 애노테이션 기반 최종 qrels
(query 144개, judgments 11,277개)를 그대로 가져와 두었습니다. 아직 이번 저장소에는 그 qrels를 만든
원본 사람 판정 원자료(calibration/annotation 완료 시트)가 없으므로, **데이터를 늘리거나 query family를
바꾸면 그 부분만큼은 `docs/PIPELINE.md` 4~7단계를 사람이 실제로 다시 거쳐야 새 qrels가 나옵니다.**

## 평가

```bash
python scripts/16_evaluate_run.py --run <run.csv> --tag <experiment_name>
```

Run CSV 스키마: `query_id,doc_id,rank,score,system`. Primary metric은 `nDCG@10`
(부트스트랩 95% CI 포함). 이번 정리 과정에서 `.trec` qrels/run 사용 시 항상 실패하던 버그를
발견해 수정했습니다 (상세: `docs/PIPELINE.md` 7절).
