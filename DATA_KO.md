# 5-split × category 균형 표본 180장

v1.3.0의 순서는 **선택 확정 → manifest 검증 → 데이터 ZIP 생성 → 서버 등록**입니다.
각 단계에서 이미 선택한 표본을 임의로 다시 추출하지 않습니다.

## 표본과 평가 설계

| 조건 | 정확한 수 |
|---|---|
| 전체 | 서로 다른 `source_key` 180개 |
| 국가 | 9개국, 국가당 20장 |
| 국가 × split | split_01~05 각각 4장 |
| 국가 × category | architecture/clothes/cuisine/game/tool_instrument 각각 4장 |
| split 전체 | 9개국 × 4장 = 36장 |
| 지역 | 3개국 × 20장 = 60장, 평가자 1명 |

동아시아 CN/KR/JP → 01, 남아시아 BD/PK/IN → 02,
동남아시아 ID/TH/SG → 03을 기본 배정으로 사용합니다.
평가자 번호를 바꾸려면 **선택 전에** 설정의 `regions`를 수정하세요.

동일 `source_key`가 여러 split에 있어도 한 번만 선택합니다. `/`와 `\` 구분,
대소문자, Unicode NFC 표기 차이를 정규화합니다. 파일명만 같고 전체 source_key가
다른 경우까지 같은 사진으로 추정하지는 않습니다.

최대 유량으로 split·category·중복 제약을 동시에 풉니다. 입력 후보 순서와 관계없이
같은 입력·seed에서 같은 결과와 순서가 나옵니다. 가능한 해를 seed로 선택하는
알고리즘이며 모든 가능한 표본을 같은 확률로 뽑는 추출법은 아닙니다. TF 점수나
모델의 예측 국가를 추출 우선순위로 사용하지 않습니다.

기본 `max_per_cell: 4`는 두 주변합만 강제합니다. 사용자 예시처럼 동일한
**국가 × split × category 조합에서 최대 한 장**을 원하면 설정을 `1`로 바꾸세요.
이 경우 국가당 25개 조합 중 20개 조합에서 한 장씩 선택합니다. 이 추가 조건이
불가능해도 자동으로 4로 완화하지 않습니다.

## 1. 연구 서버에서 입력 설정

실험 원본 파일이 있는 서버에 이 코드 폴더를 놓고 `celve-server/`에서 실행합니다.
데이터 준비 도구는 Python 표준 라이브러리만 사용하며 모델·GPU를 사용하지 않습니다.

```bash
cp deploy/selection.example.json selection_config.json
```

`selection_config.json`을 열고 **각 split의 정확한 description 실행 파일 1개씩**을
지정합니다. 다른 모델·동일 split의 여러 run을 자동으로 합치거나 최신 파일을
임의로 고르지 않습니다. `model_key`도 하나만 지정합니다.

예시에는 이전에 선택한 split_01 Llama 파일 경로를 넣었습니다. split_02~05의
`REPLACE_WITH_...`는 반드시 실제 경로로 바꾸세요. split_01도 이번 평가에서 사용할
실행이 맞는지 확인하세요. 이전 로그의 148장은 전체 5-split 후보 수가 아닙니다.

기본 경로는 다음과 같이 찾습니다.

- project: `/home/young/Vlm-interpretability`
- backup: `/home/young/CONAN_FINAL_BACKUP_20260907`
- TF: backup의 `06_patch_tf_scores/<model>/<split>/tf_scored_APS.json`
- verification: 선택된 quantile에 해당하는 `image_level_results.json`
- 전체 패치: project의 해당 모델 `batch_meta_eval.json`

다른 경로를 쓰면 설정 최상위 또는 해당 run에 `selection_file`, `verification_file`,
`tf_file`, `batch_meta`, `patch_root`, `image_resize_root`를 명시할 수 있습니다.
상대 경로는 설정 파일 위치를 기준으로 해석합니다.
국가 라벨은 verification/description의 `gt`와 source_key 국가 디렉터리를 교차
확인하고, category는 source_key의 두 번째 디렉터리로 읽습니다. `tool`은
`tool_instrument`로 정규화합니다. 서로 충돌하는 라벨은 오류입니다.

## 2. COMPLETE 판정

이전 integrity 검사 스크립트는 이 코드에 제공되지 않았으므로 그 판정과 완전히
동일하다고 가정하지 않습니다. 기본 `celve_complete_v1`은 다음 규칙을 명시적으로 사용합니다.

- `status == "ok"`, description이 비어 있지 않음.
- `generation_calls`의 성공한 호출과, 있으면 행 자체의 `finish_reason`을 검사.
- 종료 사유는 eos/stop/eos_token/eot/end_turn/stop_sequence만 허용.
- `length`, `unknown`, 누락된 종료 정보는 제외. description chunk에 성공한 호출이 없는 경우 제외.
- description의 `<unk>`/`<|unk|>` 토큰 제외. 일반 영어 단어 unknown은 자동 제외하지 않음.
- 최종 description 및 description_chunks의 끝이 문장부호 `. ! ?`로 끝나야 함.
  말줄임표, 끝의 and/or/but/because/with/such as/including/of/to/the/a/an 등은 제외.

마지막 규칙은 문장 끝의 기술적인 검사이며 의미·문법·사실 정확성을 판정하는
언어모델 검사가 아닙니다. 이 기준으로 `COMPLETE` 후보를 만들고 제외 이유를 보고서에 남깁니다.
생성된 description의 문구를 수정하거나 잘라서 검사에 통과시키지 않습니다.

**기존 COMPLETE 판정을 정확히 재사용하려면** run마다 `integrity_file`을 추가하고
기존 결과를 다음 계약 형식으로 매핑합니다. 이 JSON은 실제 평가 데이터이며
코드 저장소에 올리지 않습니다.

```json
{
  "schema_version": "celve_integrity_v1",
  "policy_id": "your_existing_integrity_version",
  "description_file_sha256": "해당 description JSON 파일의 실제 SHA-256",
  "items": [
    {
      "source_key": "korea/architecture/example.jpg",
      "description_sha256": "원문 description UTF-8 바이트의 실제 SHA-256",
      "classification": "COMPLETE",
      "unknown": false,
      "bad_ending": false
    }
  ]
}
```

외부 보고서 모드에서는 bad-ending 판정을 그 보고서에서 읽습니다. 기본 status·
비어 있지 않음·종료 정보·unknown token 검사도 계속 적용합니다. 보고서에 빠진
행은 제외하고, 파일/문구 해시가 다르면 오래된 보고서로 판단해 중단합니다.
기존 보고서의 실제 필드 이름은 확인되지 않았으므로 임의로 추측해 읽지 않습니다.

## 3. 선택을 먼저 확정

```bash
python3 tools/select_human_eval.py \
  --config selection_config.json \
  --output selection_manifest.json \
  --report selection_report.json
```

성공하면 180개 선택과 평가 순서를 고정한 manifest, 후보 수·제외 사유·국가별
split/category 분포 보고서를 만듭니다. manifest에는 다음이 포함됩니다.

- 이미지 ID·전역 source_key·국가·category·선택된 split/run ID.
- 입력 description/verification/TF/batch/threshold 파일의 경로와 SHA-256.
- description 원문·행, verification 행, 원본 이미지·전체 패치의 해시.
- COMPLETE 판정과 정책 ID, 지역·평가자 배정, seed와 정확한 제약.
- 내용과 순서를 묶는 `selection_id`.

원본과 패치 파일을 읽어 해시와 연결을 검증하지만 복사하지 않습니다.
선택 결과를 담은 manifest를 직접 수정하지 마세요. 조건을 바꾸려면 새 manifest를
새 파일명으로 생성해야 합니다. 기존 파일은 덮어쓰지 않습니다.

불가능하면 종료 코드 1로 끝나고 **manifest를 생성하지 않습니다**.
`selection_report.json`의 국가별 `selected`, `available_by_split`,
`available_unique_by_category`, `available_by_cell`과 제외 사유를 확인하세요.
개별 후보 수가 충분해도 split 간 source_key 중복 때문에 동시에 맞출 수 없을 수 있습니다.
그때도 중복 허용, 수량 축소, 불완전 description 포함을 자동으로 하지 않습니다.
파일 누락·잘못된 run 지정은 별도 입력 오류로 표시됩니다.

## 4. 확정된 선택으로 데이터 ZIP 생성

```bash
python3 tools/export_human_eval_selection.py \
  --selection selection_manifest.json \
  --output human_eval_180.zip \
  --max-mib 4096
```

다섯 run에서 **manifest에 적힌 행만** 가져옵니다. 해당 run의 description과
verification/TF 결과를 연결하고 각 원본의 전체 추출 patch를 포함합니다.
입력 파일이나 이미지 바이트가 바뀌면 중단합니다. 재추출하거나 다른 description으로 대체하지 않습니다.

| ZIP 내부 | 내용 |
|---|---|
| `study.json` | 확정된 순서의 180개 원본과 전체 patch |
| `researcher/descriptions.json` | 이미지별 단일 모델의 원문 description |
| `researcher/analysis.json` | 선택된 split/run의 CP·TF 결과와 출처 |
| `researcher/selection_manifest.json` | 선택 확정본 |
| `assets/` | 실제 이미지와 전체 patch 파일 |

## 5. 서버 연결

```bash
python manage.py import-selection --zip imports/human_eval_180.zip
```

서버는 180개 ID·source_key·국가별 20장·split/category별 4장·전체 patch 목록·
파일 해시를 검증하고 manifest의 지역 배정과 순서를 그대로 적용합니다.
`--limit 60` 같은 재추출은 허용하지 않습니다. 실제 서버의 study ID는 import한
ZIP 바이트도 반영하므로 평가 시작 후에는 같은 ZIP을 보관하고 재사용하세요.

Railway에서는 `RAILWAY_KO.md`의 단일 ZIP 업로드 절차를 사용합니다.
기존 단일 split 방식은 `LEGACY_DATA_KO.md`에 남겼지만 새 실험은 위 절차를 사용합니다.

## 해석 범위

현재는 **한 모델, 이미지당 description 하나**를 평가합니다. 네 모델을 같은
이미지에서 비교하는 실험이 아닙니다. 평가자마다 서로 다른 60장을 보므로
동일 이미지에 대한 사람 간 일치도를 계산하지 않습니다.
JSON/CSV의 precision/recall/jaccard는 **사람이 고른 patch와 모델 TF retained patch**의
일치도입니다. 결과에는 source_country, split, category, run_id, selection_id,
description_sha256도 포함됩니다.

표본의 모집단은 지정한 다섯 run의 COMPLETE 후보입니다. 생성 실패·빈 설명·
불완전 종료 등으로 제외된 이미지는 자동으로 모집단에 포함되지 않습니다.
논문에는 이 적격 기준, seed, 균형 제약과 제외 수를 함께 보고할 수 있습니다.
