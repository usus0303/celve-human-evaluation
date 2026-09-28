# CELVE — 독립 배포용 평가 사이트 v1.3.0

사이트 화면·서버·배포 설정을 포함한 소스 코드입니다. ChatGPT나 원래 배포 링크에
의존하지 않고 운영할 수 있습니다. GPU, 모델 로딩, OpenAI API 키는 필요 없습니다.
웹 화면은 빌드해서 포함했으므로 실행에 Node가 필요하지 않습니다.

**선택한 Railway 배포는 `RAILWAY_KO.md`를 따라 하세요.**
GitHub에는 코드, Railway Volume에는 데이터 ZIP과 평가 응답을 저장합니다.
5-split/category 균형 표본 확정은 `DATA_KO.md`, 별도 Linux 서버의 Docker 배포는 `DEPLOY_KO.md`,
기존 연구 서버의 파일을 직접 읽는 이전 방식은 `DIRECT_SERVER_KO.md`를 참고하세요.

## 반영된 기능

- 이미지 번호를 클릭해 순서와 관계없이 이동, 이전·다음 버튼.
- 미시작·진행 중·완료 상태 표시. 현재 번호 강조.
- 이미지 이동 시 국가·패치 선택과 작성 중인 설명 점수를 서버에 저장.
- 완료된 이미지를 다시 열어 확인. 확정 이후 국가·패치와 제출 점수는 변경 불가.
- 원본 이미지 → 9개국 복수 선택 → 국가별 전체 패치 선택 → 영어 description 평가.
- 설명은 국가·근거를 확정한 후에만 공개. TF 결과는 평가 중 숨김.
- 평가자 01·02·03별 접속 코드, 영어·한국어 화면, 연구자 진행 현황과 JSON/CSV 내보내기.
- 국가별 split당 4장·category당 4장 동시 균형, 전역 source_key 중복 금지.
- COMPLETE 후보만 선택, 파일 해시와 선택 run을 manifest로 고정.
- 국가별 20장 × 지역당 3개국 × 3개 지역: 총 180장, 각 평가자에게 서로 다른 60장 배정.
- 각자 1~60번 탐색, 다른 지역 이미지 접근 차단. 국가·지역 라벨은 평가 화면에서 숨김.
- 연구자 화면의 지역별 진행 현황, 내보내기에 지역·개인 이미지 번호·split·category·run·selection ID 포함.
- 지속되는 응답 저장 공간, 전체 상태 백업 및 빈 서버로 복원.

화면에는 실제 등록된 이미지 수만 표시합니다. 60장을 등록하면 1~60번, 샘플 2장은
1~2번입니다. 이 코드 ZIP에는 연구 이미지나 접속 코드, 기존 평가 응답을 포함하지 않습니다.
브라우저 파일 업로드 기능은 포함하지 않았습니다. 운영자가 Railway CLI로 데이터
ZIP을 Volume에 올린 후 `manage.py import-selection` 명령으로 연결합니다.

## Docker 없이 내 컴퓨터에서 확인

Python 3.12 이상을 사용하세요. 기존 Qwen 환경을 쓴다면 가상환경 생성 두 줄을
생략하고 그 환경에서 의존성을 설치할 수 있습니다.

```bash
unzip celve_server.zip
cd celve-server
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
mkdir -p imports
```

`DATA_KO.md`의 선택·내보내기 절차로 생성한 `human_eval_180.zip`을
`imports/`에 복사합니다. 선택된 표본과 지역 배정은 ZIP에 함께 들어 있습니다.

```bash
python manage.py import-selection --zip imports/human_eval_180.zip
cat state/access_codes.txt
python server.py --host 127.0.0.1 --port 8000
```

기존 2장 샘플만 확인하려면 대신 `python manage.py import-zip --zip imports/data.zip --limit 2`를 사용합니다.
`import-zip`은 이전의 공동 평가 모드이고, 새 균형 표본 180장은 `import-selection`을 사용합니다. 브라우저에서
`http://127.0.0.1:8000`을 열고 평가자 접속 코드를 입력하세요.
이 로컬 주소는 다른 사람과 공유할 공개 URL이 아닙니다.

## 배포 파일

| 파일 | 역할 |
|---|---|
| `Dockerfile`, `railway_start.py` | Railway의 PORT와 지속 저장소를 사용해 실행 |
| `RAILWAY_KO.md`, `deploy/railway.env.example` | GitHub·Railway 배포 설정 |
| `DATA_KO.md`, `deploy/selection.example.json` | 다섯 run 지정·균형 표본·지역 배정 |
| `compose.yaml` | 앱·HTTPS 프록시·지속 저장 볼륨 구성 |
| `compose.local.yaml` | localhost에서 먼저 확인할 때 사용 |
| `.env.example` | 실제 도메인 설정 예시 |
| `deploy/Caddyfile` | 도메인 HTTPS 및 앱 연결 |
| `DEPLOY_KO.md` | 배포·업데이트·백업·복원 명령 |
| `frontend/src/` | 수정 가능한 React 화면 코드 |
| `static/` | 실행에 사용하는 화면 빌드 결과 |
| `server.py`, `storage.py` | 평가 API·접속 코드·SQLite 저장 |
| `manage.py`, `bundle_loader.py`, `region_loader.py`, `assignments.py` | 데이터 가져오기·지역 배정·백업·복원 |
| `selection_manifest.py`, `tools/select_human_eval.py` | COMPLETE·전역 중복·split/category 동시 균형 및 선택 고정 |
| `tools/export_human_eval_selection.py` | 확정 manifest의 180장과 전체 patch를 ZIP으로 생성 |
| `tools/export_human_eval_sample.py` | 기존 한 split 샘플 도구·새 exporter의 데이터 연결 함수 |

## 평가용 데이터 생성

연구 서버에서 정확한 다섯 description 파일을 설정한 뒤 실행합니다.

```bash
python3 tools/select_human_eval.py --config selection_config.json --output selection_manifest.json --report selection_report.json
python3 tools/export_human_eval_selection.py --selection selection_manifest.json --output human_eval_180.zip
```

설정 예시와 COMPLETE 정책은 `DATA_KO.md`에 있습니다. 국가별 20장 안에서 각
split·category에 4장씩 배정합니다. 동일 source_key는 전체에서 한 번만 선택합니다.
후보 부족이나 중복 충돌로 제약을 만족할 수 없으면 진단 보고서를 남기고 중단합니다.
표본을 임의로 줄이거나 빈 description으로 채우지 않습니다.
실제 다섯 run과 연구 이미지가 아직 제공되지 않았으므로 실제 연구용 180장을
선택한 상태는 아닙니다. 코드 ZIP에도 연구 데이터가 없습니다.

## 응답과 분석

같은 코드로 재접속하면 저장된 응답을 이어갈 수 있습니다. 작성 중인 응답은
단계 버튼이나 이미지 이동으로 저장됩니다. 그 전에 창을 닫으면 마지막 선택은
저장되지 않을 수 있으며 브라우저에서 경고합니다.

연구자 코드로 로그인하면 세 명의 진행 상황과 완료된 응답 JSON/CSV를 내려받습니다.
평가자는 배정된 모든 이미지 완료 후 자신의 결과만 내려받습니다.

이미지당 한 모델의 description 하나를 평가합니다. 평가자별 이미지가 서로 달라
사람 간 일치도는 측정하지 않습니다. 아래 일치도는 사람–모델 TF patch 비교이며
사람이 선택한 국가에 대해 계산합니다. `precision`은 교집합/모델 패치 수,
`recall`은 교집합/사람 패치 수, `jaccard`는 교집합/합집합입니다.
CP 미후보 국가는 모델 집합 `null`, CP 후보지만 통과 패치가 없으면 `[]`입니다.
‘근거 없음’은 의도적인 빈 인간 집합, ‘잘 모르겠음’·‘원본에만 근거가 있음’은
계산 제외입니다. 분모 0은 `null`, 양쪽 빈 집합은 `both_empty`로 별도 기록합니다.

## 업데이트 시 데이터 유지

Railway에서는 `/data` Volume을 유지한 채 GitHub 코드를 재배포합니다.
Docker Compose에서는 `celve_evaluation_data` 볼륨을 유지한 채 새 코드만 다시 빌드합니다.
직접 실행에서는 `state/` 폴더를 유지합니다. 새로운 코드 ZIP을 별도 폴더에 풀고
기존 state를 지정하려면 두 명령에서 같은 `--state /절대경로/state`를 사용하세요.
`manage.py`는 `--state`를 하위 명령보다 앞에 둡니다.

새 균형 표본은 manifest에 선택과 순서가 고정됩니다. 동일 데이터 ZIP을 다시
등록하면 같은 데이터 버전을 사용합니다. 기존 단일 split 모드에서는 선택 수·seed·이름도
버전 식별에 영향을 줍니다. 코드를 업데이트하는 것만으로 기존 활성 표본을 바꾸지 않습니다.
다른 ZIP이나 선정 조건을 쓰면 새 데이터 버전으로 분리되며 예전 응답은 삭제하지 않습니다.
현재 활성 데이터만 화면에 보입니다. 연구자 내보내기와 백업 후 의도적으로 변경하세요.
현재 ChatGPT Sites 링크에 저장된 응답은 이 SQLite로 자동 이전되지 않습니다.

## 화면 개발과 검사

화면 수정에만 Node 22.13 이상이 필요합니다.

```bash
cd frontend
npm install
npm run build
```

빌드 결과 `static/`도 같이 배포합니다. 서버·ZIP 검사는 프로젝트 루트에서:

```bash
python -m unittest discover -s tests -v
```

Python 테스트 23개(동시 균형·전역 중복·불가능 조건·COMPLETE·출처 고정·파일 변경 차단·접근 분리·재시작·복원 포함),
프런트엔드 빌드, 이전 실제 샘플 ZIP의 2장·6패치 연결을 확인했습니다.
Docker/Caddy 실행과 외부 HTTPS 연결은 실제 배포 서버에서 확인해야 합니다.
Railway 실제 배포와 공개 URL 연결은 `RAILWAY_KO.md`의 절차로 진행해야 합니다.
