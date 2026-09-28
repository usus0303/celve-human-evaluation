> 이 문서는 기존 파일을 직접 읽는 이전 공동 평가 모드입니다. 새 split/category 균형 180장
> Railway 배포는 `RAILWAY_KO.md`와 `DATA_KO.md`를 사용하세요.

# 기존 연구 서버 경로를 직접 연결하는 방법

기존 평가 화면을 유지하면서 원본 이미지와 패치를 서버의 기존 경로에서 읽습니다.
이미지를 웹사이트에 업로드하거나 새 폴더로 복사하지 않습니다. 접속한 평가자의
브라우저에는 그때 필요한 이미지가 HTTP로 전송됩니다.

Python 3.12 이상을 권장합니다. 프런트엔드는 빌드해서 포함했으므로 실행에 Node,
GPU, 모델 가중치, OpenAI API 키가 필요하지 않습니다.

## 1. 설치

`celve_server.zip`을 데이터가 있는 서버에 옮겨 실행합니다.

```bash
unzip celve_server.zip
cd celve-server
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## 2. 기존 데이터 등록

아래는 앞서 샘플을 추출할 때 사용한 실행 파일입니다. 최종 평가용 파일이 다르면
`--description-file`을 해당 파일의 정확한 경로로 바꾸세요. 파일 이름의 해시만으로
최종 실행 여부를 판별하지 않습니다.

```bash
python manage.py prepare \
  --description-file "/home/young/CONAN_FINAL_BACKUP_20260907/08_description/llama3_2_11b/patch_caption_en_v2/split_01/patch_descriptions_Q0.75_testall_en_5d0a92a4be4c.json"
```

기본값은 `--limit 0`으로, 지정한 실행 파일에서 `status=ok`이고 description이
비어 있지 않은 모든 이미지를 연결합니다. 모델의 추론을 다시 실행하지 않습니다.
샘플 생성 당시 해당 실행 파일에서 완료된 description은 148건이었습니다.
현재 몇 건인지는 `prepare` 결과로 확인해야 합니다. 이것이 곧 전체 9,000장 또는
해당 split 전체 1,800장과 같다는 뜻은 아닙니다.

등록 결과에는 연결한 이미지·전체 패치 수, 유효한 description 수, description이
준비되지 않은 이미지 수, 빈 refined set 수와 사용한 파일 경로가 표시됩니다.
빈 refined set 수와 description 미완료 수는 중복될 수 있으므로 합산하지 마세요.

2장만 먼저 확인하려면 같은 명령 끝에 `--limit 2`를 붙입니다. 이후 `--limit 0`으로
다시 등록하면 다른 study ID로 저장되어 시험 응답과 본 평가 응답이 섞이지 않습니다.

기본 입력 경로:

- 프로젝트: `/home/young/Vlm-interpretability`
- 백업: `/home/young/CONAN_FINAL_BACKUP_20260907`
- 모델: `llama3_2_11b`
- 전체 패치: `output/new/grounded_sam_patches/eval/{model}/batch_meta_eval.json`
- TF 결과: `06_patch_tf_scores/{model}/{split}/tf_scored_APS.json`
- 검증 결과: `07_verification/{model}/{split}/quantile_{q}/image_level_results.json`
- 임계값: `07_verification/best_quantile_selection_cov90_full_eval.json` 우선

모델이나 루트가 다르면 `--model-key`, `--project-root`, `--backup-root`를 지정합니다.
원본 메타데이터에 저장된 절대 이미지 경로가 실제 서버에서도 유효해야 합니다.
절대 패치 경로를 임의로 다른 파일에 대응시키지 않습니다.

여러 split은 `--description-file`을 반복 지정할 수 있습니다. 각 split당 실행 파일은
하나만 선택합니다. 여러 split에 같은 원본 이미지가 나타나면 기본적으로 중단합니다.
반복 제시가 실험 의도일 때만 `--allow-repeated-images`를 사용하세요.

```bash
python manage.py prepare \
  --description-file "/정확한/split_01/설명결과.json" \
  --description-file "/정확한/split_02/설명결과.json"
```

## 3. 접속 코드 확인 및 실행

최초 등록 시 평가자 01·02·03 및 연구자 코드가 자동 생성됩니다. 재등록하거나
서버를 재시작해도 기존 코드를 덮어쓰지 않습니다.

```bash
cat state/access_codes.txt
python server.py --host 0.0.0.0 --port 8000
```

브라우저에서 **`http://서버IP:8000`**으로 접속합니다. `0.0.0.0`은 서버의 바인딩
주소이며 평가자에게 전달할 접속 주소가 아닙니다. 각 평가자에게는 해당 번호의
코드 하나만 전달하고, 연구자 코드는 연구자만 사용하세요. 국적과 평가자 번호의
대응은 연구자가 별도로 기록하면 됩니다.

서버가 실행 중인 다른 터미널에서 확인:

```bash
curl http://127.0.0.1:8000/healthz
```

응답은 `{"status":"ok"}`입니다. 이 확인이 성공해도 다른 컴퓨터에서 접속이 안 되면
서버의 외부 주소, 네트워크 접근 권한, 8000 포트 연결을 확인해야 합니다.

### 현재 서버가 Docker 컨테이너인 경우

`young@b0c9ceb9e0dc` 같은 쉘은 컨테이너 안일 수 있습니다. 컨테이너 내부에서
`0.0.0.0:8000`으로 실행하는 것만으로 호스트의 8000 포트가 연결되지는 않습니다.
호스트에서 컨테이너의 포트 매핑 또는 기존 역방향 프록시 경로를 확인하세요.
기존 학습 작업이 있는 컨테이너를 임의로 삭제하거나 재생성하지 마세요.

### SSH 접속으로 사용하는 경우

평가 웹이 SSH 서버와 같은 호스트에서 실행되고 있다면, 평가자의 컴퓨터에서:

```bash
ssh -N -L 8000:127.0.0.1:8000 young@서버IP
```

그 컴퓨터의 브라우저에서 `http://127.0.0.1:8000`을 엽니다. 웹이 별도 컨테이너에
있다면 이 SSH 명령의 도착 주소도 실제 연결 가능한 주소여야 합니다.

### HTTPS 프록시가 이미 있는 경우

도메인의 `/` 전체를 이 앱으로 연결하고 원래 Host 헤더를 유지하세요. 하위 경로
배포는 이 패키지의 기본 설정이 아닙니다. HTTPS로만 접근할 때는 서버에
`--secure-cookies`를 추가합니다. HTTP 주소로 테스트할 때 이 옵션을 켜면
브라우저가 로그인 쿠키를 보내지 않습니다.

## 4. 평가와 결과

- 원본을 보고 9개국을 복수 선택합니다.
- 선택한 국가마다 추출한 **전체 패치**를 제시합니다. 통과 패치만 제시하지 않습니다.
- 국가·패치 응답을 확정하기 전에는 API도 description을 반환하지 않습니다.
- 확정 후 국가·패치 응답은 수정할 수 없습니다.
- 이미지당 하나의 영어 description을 원문 그대로 1~5점으로 평가합니다.
- 화면 언어는 영어·한국어로 전환할 수 있습니다.
- 같은 코드를 다시 사용하면 저장된 단계부터 이어서 평가합니다.
- 번호를 눌러 자유롭게 이동합니다. 이미지 이동 시 작성 중인 응답을 저장합니다.
- 확정 버튼을 누르거나 다른 이미지로 이동하기 전에 창을 닫으면 마지막 선택은 저장되지 않을 수 있습니다.
- 완료된 이미지도 다시 열어 확인할 수 있으며 확정한 응답은 수정할 수 없습니다.
- 평가자는 번호를 바꿔 다른 사람의 응답에 접근할 수 없습니다.

연구자 코드로 로그인하면 세 사람의 진행 상황과 **전체 응답 JSON/CSV**를
내려받을 수 있습니다. 연구자 내보내기는 완료된 이미지의 응답을 포함하며,
평가자는 자신의 모든 이미지를 완료한 후 자신의 결과만 내려받습니다.

국가별 Precision / Recall / Jaccard를 포함합니다.

- 사람과 모델 모두 선택한 패치 수를 각각의 선택 수 또는 합집합 수로 나눕니다.
- CP에 없는 국가는 `tf_evaluated=false`, 모델 패치 집합은 `null`입니다.
- CP에 있으나 통과 패치가 없는 경우 모델 패치 집합은 `[]`입니다.
- ‘잘 모르겠음’, ‘원본에는 있지만 패치에는 없음’은 일치도 계산에서 제외합니다.
- ‘근거 패치 없음’은 의도적인 빈 인간 패치 집합으로 기록합니다.
- 분모 0 및 두 집합이 모두 빈 Jaccard는 `null`이며, `both_empty`로 별도 표시합니다.
- 사람이 선택하지 않은 국가는 미평가이며 자동으로 부정 응답 처리하지 않습니다.

## 5. 저장, 데이터 갱신, 백업

`state/` 전체를 지속되는 디스크에 보관하세요.

- `responses.sqlite3`: 응답, 접속 코드의 해시, 로그인 세션
- `access_codes.txt`: 최초 발급한 원문 코드 (다른 평가자에게 전체 파일을 주지 마세요)
- `active_study.json`: 현재 실행 대상 study ID
- `studies/`: 고정한 데이터 목록, 설명, 비교 정보, 원래 이미지 경로

`prepare`는 데이터 파일의 현재 내용을 읽어 study ID를 생성합니다. 생성 모델이
나중에 description을 추가해도 실행 중인 평가 대상은 자동으로 늘어나지 않습니다.
변경된 결과를 반영하려면 `prepare`를 다시 실행한 후 서버를 재시작합니다. 데이터 갱신 후 평가자의 브라우저도 새로고침해야 합니다. 이전 study ID의
응답 요청은 서버에서 거부합니다. 데이터가 달라지면 새 study ID를 쓰며, 이전 응답은 삭제하지 않습니다. 과거 study를 다시
조회하려면 서버를 정지하고 `active_study.json`에 해당 기존 ID를 지정한 뒤
재시작합니다. 원본 경로의 이미지 파일이 등록 후 변경되면 혼합 평가를 피하기 위해
이미지 요청이 중단됩니다.

실행 중인 SQLite의 일관된 백업:

```bash
python manage.py backup --output backups/responses_2026-09-24.sqlite3
```

기존 백업 파일은 덮어쓰지 않습니다. `state/studies/`, `active_study.json`,
`access_codes.txt`도 별도로 함께 보관해야 같은 데이터를 복구할 수 있습니다.

백그라운드 실행은 서버에 있는 `tmux` 등의 세션 관리 도구를 사용할 수 있습니다.
기본 실행 명령은 터미널을 점유하며, 종료하면 웹 접속도 중단됩니다.

## 검증 및 개발

구현 검증: 원본 경로 읽기, 전체 패치 보존, 실제 첨부 샘플 2장·6패치 연결,
응답 저장·재접속, 확정 후 변경 차단, 로그인·권한 분리, JSON/CSV, 빈 집합·미평가
처리, 데이터 오류 시 기존 활성 목록 보존을 확인했습니다. 사용자의 실제 서버
네트워크와 브라우저 화면 검사는 이 환경에서 수행하지 않았습니다.

```bash
python -m unittest discover -s tests -v
```

화면을 수정할 때만 `frontend/`에서 Node를 사용합니다. 소스와 빌드 설정을 포함했습니다.
Vite 8 빌드를 위해 Node 22.13 이상을 권장합니다.

```bash
cd frontend
npm install
npm run build
```

일반 실행에는 이 빌드 단계를 반복할 필요가 없습니다.

사용한 배포 서버 참고: Flask의 Waitress 배포 안내
https://flask.palletsprojects.com/en/stable/deploying/waitress/
