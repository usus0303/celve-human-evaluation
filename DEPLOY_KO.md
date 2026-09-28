# 별도 Linux 서버 배포 — Railway의 대안

선택한 Railway 배포 절차는 `RAILWAY_KO.md`입니다. 아래는 Docker Compose를 직접
운영할 때의 대안이며 Railway에서는 실행할 필요가 없습니다.

이 설정은 Docker Compose를 실행할 수 있는 Linux 웹 서버 1대에 배포합니다.
연구 서버의 GPU·SSH 계정을 평가자에게 제공하지 않습니다. 평가자는 사이트 URL과
자신의 접속 코드만 사용합니다. 코드에는 외부 계정이나 ChatGPT 의존성이 없습니다.

배포 서버·도메인은 아직 지정하지 않았습니다. 아래 `eval.example.com`을 실제
도메인으로 바꾸세요. 서버 구매, DNS 변경, 원격 배포는 이 코드 묶음에 포함된 작업이 아닙니다.

## 1. 파일 준비

배포 서버에 Docker Engine과 Compose 플러그인이 설치되어 있어야 합니다.
Docker CLI는 학습용 컨테이너 안이 아닌 배포 서버 호스트에서 실행합니다.

```bash
docker --version
docker compose version
unzip celve_server.zip
cd celve-server
mkdir -p imports backups
cp .env.example .env
```

`DATA_KO.md`에서 생성한 `human_eval_180.zip`을 `imports/`에 복사합니다. 서버에 옮기는 방법은 SCP, SFTP
등 편한 방식을 쓰면 됩니다. **브라우저 업로드 화면은 없으며 운영자가 파일을 올리고
아래 명령으로 등록하는 방식입니다.** 이 폴더는 웹에 공개되지 않습니다.

## 2. 세 지역 180장 등록

```bash
docker compose build app
docker compose run --rm --no-deps app python manage.py import-selection \
  --zip /imports/human_eval_180.zip
docker compose run --rm --no-deps app cat /data/state/access_codes.txt
```

`DATA_KO.md`에서 먼저 selection manifest를 확정합니다. 국가별 20장, 지역별 60장,
총 180장을 등록합니다. 각 평가자는 자기 지역의 서로 다른 60장을 봅니다.
각 원본의 **전체 패치**와 description을 가져옵니다. 국가별 split·category당 4장 및 180개 고유 source_key를 확인하며 재추출하지 않습니다.
기존 2장 샘플만 테스트하려면 `import-zip --zip /imports/data.zip --limit 2`를 사용합니다.
코드 파일 전체를 공유하지 말고 평가자 01·02·03에게 각각 자신의 코드만 전달합니다.

처음 등록한 접속 코드는 이후 데이터 등록·코드 업데이트 시 유지됩니다.
서버에 등록한 데이터가 바뀌면 다른 study ID에 응답이 저장되어 기존 평가와 섞이지 않습니다.

## 3. 공개 전에 로컬 확인

```bash
docker compose -f compose.yaml -f compose.local.yaml up -d app
```

이 설정은 해당 컴퓨터의 `http://127.0.0.1:8000`에서 확인하는 용도입니다.
이 주소를 다른 평가자에게 공유하지 마세요. 내 컴퓨터에서 실행했다면 바로
브라우저로 열면 됩니다. 원격 서버라면 이 단계를 생략하고 HTTPS 공개로 진행할 수 있습니다.

평가자 코드로 로그인하여 이미지 번호 이동·국가 선택·근거 선택·설명 평가를 확인합니다.
샘플에서 본 평가 180장으로 바꿀 때는 import-selection 후 앱을 재시작하세요.

## 4. 도메인과 HTTPS 공개

실제 도메인의 DNS A 레코드를 배포 서버의 공인 IPv4 주소로 연결합니다. IPv6를
사용하지 않으면 해당 이름에 잘못된 AAAA 레코드가 남아 있지 않게 하세요.
서버·클라우드 방화벽에서 TCP 80과 443의 수신을 허용합니다.
이 포트를 이미 다른 웹 서버가 쓰고 있으면 기존 프록시에 통합해야 합니다.

`.env`의 값을 편집합니다. URL 전체가 아닌 호스트 이름만 적습니다.

```dotenv
SITE_DOMAIN=eval.example.com
```

로컬 테스트를 실행했다면 먼저 다음 명령으로 종료합니다. 데이터 볼륨은 유지됩니다.

```bash
docker compose -f compose.yaml -f compose.local.yaml down
```

공개용으로 시작합니다.

```bash
docker compose --profile public up -d --build
docker compose --profile public ps
docker compose --profile public logs --tail=60 app caddy
```

Caddy가 도메인 인증에 성공하면 `https://실제도메인`으로 접속합니다.
인증서 발급에는 올바른 DNS와 포트 연결이 필요합니다. `example.com`을 그대로
쓰거나 사설 주소만 있는 서버에서는 실제 공개 URL이 만들어지지 않습니다.
공개 설정은 HTTPS용 보안 쿠키를 사용하며 앱의 8000 포트는 외부에 게시하지 않습니다.
사이트는 도메인 루트 `/`에 배포합니다. `/evaluation/` 같은 하위 경로는 지원하지 않습니다.

## 5. 유지·업데이트

`celve_evaluation_data`라는 고정 Docker 볼륨에 다음 항목이 남습니다.

| 위치 | 내용 |
|---|---|
| `/data/state/imports/` | ZIP에서 읽어 온 이미지·전체 패치 |
| `/data/state/studies/` | 고정된 데이터 목록·description·비교 정보 |
| `/data/state/responses.sqlite3` | 평가 응답·세션·계정 |
| `/data/state/access_codes.txt` | 최초 발급한 접속 코드 |

**코드를 업데이트할 때 import-selection를 다시 실행할 필요가 없습니다.** 새 코드로
교체하거나 Git 저장소에서 변경을 받은 다음 아래 명령을 실행합니다.

```bash
docker compose --profile public up -d --build
```

`frontend/src/`를 직접 수정했다면 먼저 `frontend/`에서 `npm install`과
`npm run build`를 실행하세요. Docker는 함께 제공되는 `static/`의 빌드 결과를 사용합니다.
Git으로 관리할 때 `static/`도 변경 사항에 포함해야 화면 수정이 배포됩니다.

새 이미지 목록으로 바꿀 때만 import-selection를 다시 실행하고 앱을 재시작합니다.

```bash
docker compose run --rm --no-deps app python manage.py import-selection \
  --zip /imports/human_eval_180.zip
docker compose restart app
```

평가자에게 새로고침을 안내하세요. 이전 데이터가 열린 탭에서 새 데이터에 응답을
잘못 저장하지 않도록 study ID를 검사합니다. 평가 중인 데이터는 의도 없이 바꾸지 마세요.
새 데이터가 활성화돼도 예전 응답은 삭제하지 않으며, 연구자 화면은 현재 데이터만 표시합니다.

`docker compose down`은 서버를 중지하면서 볼륨을 보존합니다.
**`docker compose down -v` 및 `docker volume rm celve_evaluation_data`는 평가 데이터를
삭제하므로 운영 중 사용하지 마세요.** 볼륨은 재배포를 견디지만 서버 디스크 고장까지
막아주지는 않으므로 별도 위치에 백업을 복사해야 합니다.

## 6. 백업과 복원

예시 파일명 날짜는 실제 백업 날짜로 바꾸세요. 기존 파일은 덮어쓰지 않습니다.

```bash
docker compose exec app python manage.py snapshot \
  --output /data/backups/celve-20260928.tar.gz
docker compose cp app:/data/backups/celve-20260928.tar.gz ./backups/
```

이미지·설명·응답·접속 코드가 포함됩니다. 평가 응답을 저장하는 중에도 SQLite
백업 API로 일관된 복사본을 만듭니다. 데이터 import 작업과 백업은 동시에 하지 마세요.
백업에는 접속 코드가 포함되므로 공개 저장소에 커밋하지 않습니다.

복원은 **평가 데이터가 없는 새 배포 서버/빈 볼륨**에서만 수행하세요.
백업을 `imports/backup.tar.gz`로 복사한 다음:

```bash
docker compose build app
docker compose run --rm --no-deps app python manage.py restore \
  --archive /imports/backup.tar.gz
docker compose --profile public up -d
```

복원 명령은 기존 state가 비어 있지 않으면 중단합니다. 기존 운영 데이터를
덮어쓰지 않습니다. ZIP으로 가져온 이미지는 상대 경로로 관리하므로 서버가 바뀌어도
복구할 수 있습니다. `prepare`로 연구 서버의 기존 경로를 직접 등록한 경우에는
해당 외부 이미지 파일도 별도로 복구해야 합니다.

## 7. 코드 보관

Git 저장소를 쓴다면 소스, Docker 설정, 문서, `static/`만 커밋합니다.
`.env`, 데이터 ZIP, `state/`, `imports/`, `backups/`는 제외 설정이 되어 있습니다.
이 패키지에 GitHub 계정이나 원격 저장소는 아직 연결하지 않았습니다.

## 확인 범위

Python API·ZIP 검증·응답 재개·백업 복원 테스트, 실제 첨부 샘플 2장/6패치,
TypeScript 검사와 프런트엔드 빌드를 확인했습니다. 65장짜리 테스트 자료에서
60장을 선택하는 이전 시나리오와 국가별 20장·총 180장 분리를 확인했습니다.
실제 연구용 180장 데이터는 포함되어 있지 않습니다.

제작 환경에는 Docker 엔진·Caddy가 없어 컨테이너 기동, 인증서 발급, 외부 접속은
실제 배포 서버에서 확인해야 합니다. 이 묶음이 이미 원격 서버에 배포된 것은 아닙니다.

설정 참고 문서:
- https://docs.docker.com/reference/compose-file/services/
- https://docs.docker.com/engine/storage/volumes/
- https://caddyserver.com/docs/automatic-https
- https://caddyserver.com/docs/quick-starts/reverse-proxy
