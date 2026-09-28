# Railway + GitHub 배포

코드는 GitHub 저장소에서 관리하고 Railway가 실행합니다. 원본·전체 패치·description이
들어 있는 데이터 ZIP은 Railway Volume에 업로드합니다. 응답과 접속 코드도 같은
Volume에 저장합니다. 이 패키지에는 실제 연구용 180장과 계정 정보가 없습니다.

## 1. GitHub에 코드 올리기

GitHub에 빈 **비공개 저장소**를 만들고, 압축을 푼 `celve-server/`의 내용을 저장소
루트에 올립니다. ZIP 파일 자체를 올리는 것이 아닙니다. `Dockerfile`, `server.py`,
`railway_start.py`, `static/` 등이 저장소 루트에 있어야 합니다.

`frontend/src/`도 함께 보관합니다. 화면을 수정하면 `frontend/`에서 `npm install`과
`npm run build`를 실행한 뒤 소스와 `static/` 빌드 결과를 모두 커밋합니다.
Railway에서 실행할 때는 Python만 사용하므로 GPU나 모델 API 키가 필요 없습니다.

데이터 ZIP·`state/`·응답·접속 코드는 Git에 올리지 않습니다. `.gitignore`에
제외 규칙을 넣어 두었습니다. 아직 원격 GitHub 저장소는 연결하지 않았습니다.

## 2. Railway 서비스 설정

Railway에서 새 프로젝트를 만들고 GitHub 저장소를 연결합니다. GitHub 연동에
해당 저장소 접근 권한을 허용합니다. 다음 설정을 적용하고 배포합니다.
연결 직후 자동 빌드가 시작되었다면 Volume과 변수를 설정한 뒤 다시 배포하세요.

| 설정 | 값 |
|---|---|
| 저장소 루트 | `Dockerfile`이 있는 위치; 이 패키지 내용을 루트에 올렸다면 `/` |
| 빌드 | 포함된 `Dockerfile` 자동 감지 |
| 시작 명령 | `python railway_start.py` — Dockerfile 기본값도 동일 |
| Healthcheck Path | `/healthz` |
| Healthcheck Timeout | `300`초 |
| 인스턴스 수 | `1` |
| Volume | 이 앱 서비스에 연결, Mount Path `/data` |
| `CELVE_STATE_DIR` 변수 | `/data/state` |
| `CELVE_SELECTION_ZIP` 변수 | `/data/human_eval_180.zip` |
| `RAILWAY_RUN_UID` 변수 | `0` — Railway Volume 쓰기 권한 |

`PORT`와 `RAILWAY_VOLUME_MOUNT_PATH`는 Railway가 제공합니다. 직접 지정하지 않습니다.
데이터 import는 빌드·pre-deploy 명령에 넣지 않습니다. Volume은 실행 시 연결됩니다.
별도 PostgreSQL, Redis, Caddy 서비스는 필요 없습니다.

처음에는 데이터가 없어 사이트에 ‘평가 준비 중’이 표시됩니다. `/healthz`의
`awaiting_data`는 서버가 켜졌으나 아직 평가 데이터가 연결되지 않았다는 뜻입니다.
서비스의 Networking에서 **Generate Domain**으로 공개 HTTPS 주소를 생성합니다.

## 3. 먼저 웹사이트만 배포

실제 데이터를 아직 올리지 않아도 `/healthz`는 `awaiting_data`를 반환하고,
공개 URL에는 ‘평가 준비 중’ 화면이 표시됩니다. GitHub 코드 배포와 연구 데이터
선택·업로드는 따로 진행할 수 있습니다. 빈 Volume은 지금 연결해 둡니다.

## 4. 나중에 확정 데이터 ZIP 연결

연구 서버에서 `DATA_KO.md` 순서로 다섯 run을 지정하고 `selection_manifest.json`을
확정한 뒤 `human_eval_180.zip`을 생성합니다. manifest와 지역 배정은 ZIP 안에
포함됩니다. 이전 세 지역 ZIP/regions.json 방식은 새 균형 표본에 사용하지 않습니다.

데이터 ZIP이 있는 컴퓨터에서 최신 Railway CLI를 설치하고 평가 앱을 선택합니다.

```bash
npm install -g @railway/cli
railway login
railway link
railway service
railway status
```

브라우저가 없는 연구 서버에서는 `railway login --browserless`를 사용할 수 있습니다.
CLI 설치에만 Node가 필요하며 평가 서버에는 필요하지 않습니다.

```bash
railway volume files upload ./human_eval_180.zip /human_eval_180.zip
railway ssh -- python manage.py import-selection --zip /data/human_eval_180.zip
railway restart
railway ssh -- cat /data/state/access_codes.txt
```

CLI 원격 경로 `/human_eval_180.zip`은 Volume 내부 경로이고, 앱에서는
`/data/human_eval_180.zip`으로 읽습니다. 업로드 완료 후 import를 실행합니다.
처음부터 ZIP이 있으면 `CELVE_SELECTION_ZIP`을 읽어 자동 등록할 수도 있지만,
위 수동 import는 큰 ZIP의 첫 등록 시간이 healthcheck 제한을 넘는 것을 피합니다.

로그의 `Images: 180`과 평가자별 `60 images`를 확인합니다. 서버는 manifest의
split/category 균형·고유 source_key·파일 해시를 검증하고 배정과 순서를 그대로
사용합니다. 파일 변경·패치 누락·description 불일치 시 기존 활성 평가를 바꾸지 않습니다.

연구자 코드로 로그인해 180장·전체 패치 수·지역별 진행 현황·selection ID를 확인하세요.
평가자에게는 공개 URL과 자신의 코드만 전달합니다. 평가자는 Railway·GitHub·연구 서버
계정 없이 담당 60장을 평가합니다. 지역·국가 라벨과 split/category는 평가 중 숨깁니다.

기존 활성 표본이 있으면 코드 재배포 때 자동으로 다른 표본을 등록하지 않습니다.
기존 단일 split 표본을 새 manifest로 바꾸려면 연구자 결과·백업을 보관한 후 위
`import-selection`과 재시작을 의도적으로 실행하세요. 이전 응답은 이전 study ID에 남습니다.

## 5. 업데이트와 데이터 보존

GitHub 연결 브랜치에 코드를 push하면 Railway가 자동 재배포하도록 설정할 수 있습니다.
`/data/state/`의 이미지·설명·SQLite 응답은 Volume에 남습니다. 평가 중 데이터나
배정 변경은 별도 작업입니다. 응답은 study ID에 묶이므로 새로운 데이터 세트가
활성화되면 화면에서는 새 평가로 표시되고 이전 응답은 DB에 보존됩니다.

SQLite를 쓰는 이 앱은 서비스 인스턴스 1개로 운영합니다. Volume을 삭제하거나
다른 Volume으로 바꾸면 기존 데이터가 연결되지 않습니다. 데이터 import와 백업은
동시에 실행하지 않습니다. 평가 시작 전과 종료 후에는 다음과 같이 백업합니다.

```bash
railway ssh -- python manage.py snapshot --output /data/backups/celve-20260928.tar.gz
railway volume files download /backups/celve-20260928.tar.gz ./celve-20260928.tar.gz
```

파일명의 날짜는 백업 날짜로 바꿉니다. 백업은 응답·원본·전체 패치·설명·접속 코드를
포함하므로 비공개로 보관합니다. 복원은 빈 state에 `manage.py restore`로 수행합니다.
실행 중인 SQLite 파일을 직접 다운로드하는 대신 위 일관된 스냅샷을 사용하세요.

Volume 용량은 **원본 ZIP + 압축 해제된 자산 + 백업**을 합쳐 확보해야 합니다.
실제 용량은 패치 수·해상도에 따라 달라지므로 180이라는 이미지 개수만으로 확정할 수 없습니다.

## 확인 범위와 공식 문서

로컬 API·5-split/category 동시 균형·180장/9개국/3계정 분리·출처 보존·백업 복원·프런트엔드 빌드를 검사했습니다.
실제 연구용 180장, GitHub/Railway 계정 연결과 외부 URL 검사는 아직 필요합니다.

- GitHub 자동 배포: https://docs.railway.com/deployments/github-autodeploys
- Docker 빌드: https://docs.railway.com/builds/dockerfiles
- Volume: https://docs.railway.com/volumes
- 파일 업로드: https://docs.railway.com/cli/volume
- 원격 명령: https://docs.railway.com/cli/ssh
- CLI 설치: https://docs.railway.com/cli
- Healthcheck: https://docs.railway.com/deployments/healthchecks

2026-09-28 확인 기준, 기존 `railway.json` 방식은 공식 문서에서 폐기 예정으로
안내하므로 이 패키지는 Dockerfile과 위 서비스 설정을 사용합니다.
