> 이전 단일 split/지역별 추출 방식입니다. 새 5-split 균형 표본은 `DATA_KO.md`를 사용하세요.

# 국가별 20장, 지역별 60장 데이터 준비

국가 라벨 기준 20장 × 3개국 × 3개 지역 = **서로 다른 원본 이미지 180장**입니다.
각 원본에 전체 추출 패치, 수정하지 않은 영어 description 하나, TF 비교 결과가
함께 연결됩니다. 패치는 TF 통과 여부와 관계없이 모두 포함합니다.

스크립트는 모델을 실행하지 않습니다. 이미 description 생성과 TF 계산이 끝난
연구 데이터가 있는 서버에서 실행해야 합니다. `gt`가 있으면 이를 국가 라벨로 쓰고,
없으면 `source_key`의 국가 디렉터리명을 사용합니다. 모델의 예측 국가로 추출하지 않습니다.

## 기존 실행 결과에서 추출

다음은 **동아시아로 CN/KR/JP를 배정하는 경우의 예시**입니다. `--description-file`은
실제로 사용할 실행 결과를 정확히 지정합니다. `--per-country 20`은 `--limit`보다
우선하며 국가 하나라도 20장에 모자라면 실패합니다.

```bash
python3 tools/export_human_eval_sample.py \
  --split split_01 --countries CN KR JP --per-country 20 \
  --seed 42 --max-mib 4096 \
  --description-file "/home/young/CONAN_FINAL_BACKUP_20260907/08_description/llama3_2_11b/patch_caption_en_v2/split_01/patch_descriptions_Q0.75_testall_en_5d0a92a4be4c.json" \
  --output region_a.zip
```

남아시아는 `--countries BD PK IN --output region_b.zip`, 동남아시아는
`--countries ID TH SG --output region_c.zip`으로 바꾸고, **각 지역의 모든 국가가
20장 이상 준비된 split과 description 실행 파일**을 지정합니다.

이전에 확인한 `split_01` 실행에는 성공한 description이 **148개**였습니다.
그 파일 하나로 서로 다른 이미지 180장을 만들 수는 없습니다. 각 국가의 수가
부족하면 다른 준비된 split/실행 결과가 필요합니다. 여러 run을 무작정 합치거나
description이 없는 이미지를 빈 설명으로 대체하지 않습니다. 지역별로 한 run에서
60장을 구성할 수 없다면 그 데이터 구성을 먼저 확인하고 병합 절차를 정해야 합니다.

## ZIP 구조와 가져오기

| 항목 | 내용 |
|---|---|
| `study.json` | 이미지 ID, 원본 경로, 전체 패치 ID·경로·좌표, 9개국 보기 |
| `researcher/descriptions.json` | 이미지 ID별 description |
| `researcher/analysis.json` | 원본 식별자, 국가 라벨·CP/TF 비교·실행 출처 |
| `assets/` | 원본 이미지와 전체 패치 파일 |

웹 서버에서 세 ZIP과 `regions.json`을 같은 폴더에 놓고 실행합니다.

```bash
python manage.py import-regions --config imports/regions.json
```

지역별 ZIP에 60장보다 많은 이미지가 있어도 국가별 20장씩 고릅니다.
원본 식별자와 이미지 ID의 지역 간 중복을 거부합니다. 패치·설명 ID가 맞지 않거나
파일이 누락돼도 등록하지 않습니다. 같은 ZIP·설정·seed는 같은 배정과 순서를 만듭니다.

브라우저는 로그인한 평가자의 원본·패치만 요청할 수 있고, 설명은 국가와 근거를
확정한 후 공개됩니다. 원래 국가 라벨과 TF 결과는 평가 화면에 전달하지 않습니다.
