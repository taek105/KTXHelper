# KTX helper
<p>
    <!-- <img width="128" height="128" alt="image" src="https://github.com/user-attachments/assets/2f700449-a605-43fa-a2b2-bd3d7a2eb2e6"> -->
    <img width="128" height="128" alt="image" src="https://github.com/user-attachments/assets/8e9eba99-3ac3-4dff-9edb-296c5adf1db1" />

</p>

<p>
    <img alt="Static Badge" src="https://img.shields.io/badge/python-3.11-blue?style=flat&logo=python&logoColor=white">
    <img alt="Static Badge" src="https://img.shields.io/badge/google chrome-latest-white?style=flat&logo=googlechrome&logoColor=white">
</p>

![2025-06-07 23;48;31](https://github.com/user-attachments/assets/510d5d27-50a4-4cf3-803b-e15a774c2cc7)

매진된 KTX 표의 예매를 도와주는 파이썬 프로그램입니다.
mac 환경에서 실행 가능합니다.

  
## requirements
- ***Python 3.11.****
- 최신 버전의 크롬 브라우저
- Xcode Command Line Tools (`swift` 명령)

```
fastapi==0.141.1
uvicorn==0.52.4
jinja2==3.1.6
python-multipart==0.0.32
python-dotenv==1.2.3
```

## 웹 백오피스 사용법

- 백오피스 탭을 활성화하지 않으면 사이렌이 울리지 않습니다. (카톡 알림은 옵니다)
- 스케줄 조회 또는 매크로 실행 시 일반 Chrome 창이 열립니다. 입력한 코레일 회원번호·비밀번호를 macOS 마우스·키보드 입력으로 채워 로그인한 뒤 다음 단계로 진행합니다.
- macOS `시스템 설정 > 개인정보 보호 및 보안 > 손쉬운 사용`에서 앱을 실행하는 터미널의 제어 권한을 켜야 합니다.
- 조회와 새로고침은 Chrome 창에 키보드 입력을 보냅니다. 자동화 중에는 해당 창을 최소화하거나 다른 곳에 입력하지 마세요.
 

```py
0. 패키지의 루트 디렉토리로 이동
1. python -m venv .venv
2. (mac) source .venv/bin/activate or (window) ./.venv/scripts/activate.ps1
3. pip install -r requirements.txt
4. python app.py
5. localhost:8000 접속
```

## MCP 사용법

KTXHelper는 로컬 STDIO MCP 서버를 제공합니다. Codex CLI, Codex IDE 확장과
ChatGPT 데스크톱 앱에서 자연어로 KTX 스케줄을 조회하고 예약 매크로를 실행할
수 있습니다. ChatGPT 웹에서는 로컬 STDIO 서버를 직접 사용할 수 없습니다.

### 1. 설치

프로젝트를 내려받은 뒤 가상환경과 의존성을 설치합니다.

```bash
git clone https://github.com/taek105/KTXHelper.git
cd KTXHelper
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 코레일 로그인

도구에 코레일 회원번호와 비밀번호를 전달하면 일반 Chrome 창에서 자동으로
로그인합니다. 성공을 확인한 뒤 스케줄 조회나 예약 작업이 이어집니다.

### 3. Codex에 MCP 서버 등록

`~/.codex/config.toml` 또는 신뢰하는 프로젝트의 `.codex/config.toml`에 다음
설정을 추가합니다. 경로는 KTXHelper를 내려받은 실제 절대 경로로 변경하세요.

```toml
[mcp_servers.ktx_helper]
command = "/absolute/path/to/KTXHelper/.venv/bin/python"
args = ["-m", "mcp_server"]
cwd = "/absolute/path/to/KTXHelper"
startup_timeout_sec = 30
tool_timeout_sec = 120
default_tools_approval_mode = "writes"
```

Codex를 다시 시작한 뒤 MCP 연결 상태를 확인합니다.

```bash
codex mcp list
```

Codex CLI에서는 `/mcp` 명령으로 `ktx_helper` 서버와 사용 가능한 도구를 확인할
수 있습니다. 자세한 설정 방법은
[Codex MCP 공식 문서](https://developers.openai.com/codex/mcp/)를 참고하세요.

### 4. 제공 도구

| 도구 | 설명 |
| --- | --- |
| `list_stations` | KTXHelper가 지원하는 출발역과 도착역 목록을 조회합니다. |
| `search_schedules` | 코레일에 로그인한 뒤 조건에 맞는 KTX 스케줄을 조회합니다. |
| `start_reservation` | 선택한 열차 인덱스로 예약 매크로를 시작하고 작업 ID를 반환합니다. |
| `get_reservation_status` | 작업 ID로 예약 진행 상태와 결과를 조회합니다. |
| `stop_reservation` | 실행 중인 예약 작업을 중단하고 브라우저를 종료합니다. |

열차 인덱스는 스케줄 조회 결과를 기준으로 1부터 시작합니다. 예를 들어
`[1, 3]`은 첫 번째와 세 번째 열차를 의미합니다.

### 5. 자연어 사용 예시

```text
9월 10일 오전 8시 이후 서울에서 부산으로 가는 KTX를 조회해줘.

조회 결과의 첫 번째와 세 번째 열차로 예약 매크로를 시작해줘.
예약 대기도 포함해줘.

현재 예약 작업 상태를 확인해줘.

실행 중인 예약 작업을 중단해줘.
```

예약 작업은 외부 사이트에 예약을 생성할 수 있으므로 명시적인 사용자 요청이
있을 때만 실행됩니다. 하나의 Chrome 프로필은 동시에 한 작업에서만 사용되며,
이미 실행 중인 예약 작업이 있으면 새 작업은 시작되지 않습니다.

## 로그

- `logs/events.jsonl`: 매크로 실행, 카카오 연결·해제·메시지 발송 및 API 요청 결과
- `logs/error.log`: 처리되지 않은 예외 타입과 메시지를 한 줄로 기록
- 로그는 매일 자정 이후 첫 기록 시 날짜별 파일로 교체됩니다.
- 정확히 28일이 된 회전 로그부터 자동으로 삭제됩니다.
- 코레일 로그인 정보, 카카오 토큰, OAuth 인가 코드, 요청 Form 전체와 카카오 API 응답 원문은 기록하지 않습니다.
- 호출 IP는 기록하지 않습니다.


## 기타  
과도한 사용은 지양해 주십시오.
