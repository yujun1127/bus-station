# 울산 버스 노선도 Docker Compose 자동화 프로젝트

> **행정안전부 버스 정류장 API** × **Docker Compose** × **Nginx**  
> 버스의 위치를 5분마다 자동 갱신

---

## 📁 프로젝트 구조

```
bus-station/
├── docker-compose.yml          # 전체 서비스 정의
│
├── data-generator/             # 컨테이너 1: 데이터 수집 + HTML 생성
│   ├── Dockerfile
│   ├── requirements.txt        # Python 패키지 (requests)
│   ├── fetch_data.py           # API 호출 → HTML 생성 메인 스크립트
│   ├── entrypoint.sh           # 컨테이너 시작 스크립트
│   └── crontab.txt             # cron 스케줄 (매 5분)
│
└── web-server/                 # 컨테이너 2: Nginx 정적 파일 서버
    ├── Dockerfile
    ├── nginx.conf              # Nginx 가상 호스트 설정
    └── loading.html            # 초기 로딩 화면
```

---

## 🏗️ 아키텍처

```
┌─────────────────────────────────────────────────────────────┐
│                       DOCKER NETWORK                        │
│                                                             │
│  ┌──────────────────────┐      ┌──────────────────────────┐ │
│  │    DATA-GENERATOR    │      │        WEB-SERVER        │ │
│  │   (Python + Cron)    │      │     (Nginx: Port 80)     │ │
│  │ ┌──────────────────┐ │      │ ┌──────────────────────┐ │ │
│  │ │  fetch_data.py   │ │      │ │     /usr/share/      │ │ │
│  │ │  (API REQUEST)   │ │      │ │     nginx/html/      │ │ │
│  │ │        ↓         │ │      │ │     index.html       │ │ │
│  │ │   Create File    │ │      │ └─────────▲────────────┘ │ │
│  │ └────────┬─────────┘ │      │           │              │ │
│  └──────────┼───────────┘      └───────────┼──────────────┘ │
│             │                              │                │
│             └──────── Docker Volume ───────┘                │
│                       (html-volume)                         │
└─────────────────────────────┬───────────────────────────────┘
                              │
                        Port 80 → 8080
                              │
                    http://localhost:8080
                  (브라우저에서 확인 가능)
```

---

## 🚀 실행 방법

### 사전 요구사항
- Docker Desktop (또는 Docker Engine + Docker Compose) 설치
- 인터넷 연결 (Open-Meteo API 호출용)  
**※중요※ KAKAO_API_KEY, KAKAO_JS_KEY 입력 필요**

### 1. 프로젝트 폴더로 이동

```bash
cd bus-station
```

### 2. 빌드 및 실행 (명령어 한 줄)

```bash
docker-compose up --build
```

> **`-d` 옵션으로 백그라운드 실행:**
> ```bash
> docker-compose up --build -d
> ```

### 3. 브라우저에서 확인

```
http://localhost:8080
```

- 처음 시작 시 로딩 화면이 표시되며, **약 10~20초** 후 버스 대시보드가 나타납니다.
- 이후 **5분마다** 자동으로 갱신됩니다.

---

## 🔍 상태 확인 명령어

```bash
# 실행 중인 컨테이너 확인
docker-compose ps

# data-generator 로그 확인 (cron 실행 기록)
docker-compose logs -f data-generator

# web-server 로그 확인 (Nginx 액세스 로그)
docker-compose logs -f web-server

# 두 컨테이너 로그를 동시에 확인
docker-compose logs -f
```

---

## 🛠️ 수동 갱신 (즉시 재실행)

```bash
# data-generator 컨테이너 안에서 스크립트 직접 실행
docker-compose exec data-generator python fetch_data.py
```

---

## ⚙️ 설정 변경

### 갱신 주기 변경 (`crontab.txt`)

```
# 매 1분마다
* * * * * cd /app && /usr/local/bin/python fetch_data.py >> /var/log/cron.log 2>&1

# 매 10분마다
*/10 * * * * cd /app && /usr/local/bin/python fetch_data.py >> /var/log/cron.log 2>&1
```

### 도시 추가 (`fetch_data.py`)

`CITIES` 리스트에 항목 추가:
```python
{"name": "울산", "emoji": "KR", "lat": 37.5388, "lon": 127.0827, "tz": "Asia/Ulsan"},
```

### 포트 변경 (`docker-compose.yml`)

```yaml
ports:
  - "80:80"   # http://localhost 로 접근
```

---

## 🧹 종료 및 정리

```bash
# 컨테이너 중지
docker-compose down

# 컨테이너 + 볼륨 완전 삭제
docker-compose down -v

# 이미지까지 삭제
docker-compose down -v --rmi all
```

---

## 📡 사용 API

| 항목 | 내용 |
|------|------|
| API 이름 | [행정안전부_버스 실시간 위치 정보](https://www.data.go.kr/data/15157601/openapi.do#/API%20%EB%AA%A9%EB%A1%9D/mst_info) |
| 인증키 | **불필요** |
| 요금 | **완전 무료** (비상업적 이용) |
| 제한 | 분당 10,000 요청 |
| 데이터 | 버스 번호, 위치, 정류장의 위치, 버스 종류 |

---

## 🐛 문제해결

| 증상 | 해결법 |
|------|--------|
| 페이지가 로딩 화면에서 멈춤 | `docker-compose logs data-generator` 로 오류 확인 |
| `port is already allocated` 오류 | `docker-compose.yml`에서 포트 번호 변경 (예: `8090:80`) |
| API 호출 실패 | 인터넷 연결 확인, 방화벽 설정 확인 |
| 변경사항이 반영 안 됨 | `docker-compose up --build` 로 재빌드 |
