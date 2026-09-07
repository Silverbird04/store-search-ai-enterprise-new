# Legacy v002 benchmark (대전/세종, 13,832개 매장 기준)

이 폴더는 `stores_v002`(대전/세종 2개 시트, 13,832개 매장) 기준으로 만들어졌던
사람 이중 라벨링 + adjudication 결과물을 그대로 보존한 것입니다. `query_set_v003`
(전국 214,043개 매장, `stores_v003`)로 전환하면서 store_id 상당수가 바뀌어 이
qrels를 새 corpus에 그대로 재사용할 수 없어 옮겨 두었습니다.

- `queries.csv`, `annotation_guideline.md`, `qrels*.csv/.trec`, `benchmark_manifest.json`,
  `storesearch_ko_v1.yaml`: 당시 확정된 최종 벤치마크 산출물.
- `annotations/full_annotation_v1/analysis/adjudication_val_test_full_completed.csv`:
  val/test에 대해 사람 2명이 이중 라벨링하고 adjudication까지 마친 원자료.

새 전국 데이터로 val/test gold qrels를 다시 만들려면 `docs/PIPELINE.md`의 사람
개입 단계(09~11번 스크립트)를 그대로 다시 거치면 됩니다. 이 폴더는 참고/비교용으로만
두고 파이프라인 코드에서는 더 이상 읽지 않습니다.
