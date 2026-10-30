import json
import os
import time
from datetime import datetime
from pathlib import Path
from urllib import error, parse, request

from core.event_logging import log_error, log_event

KAKAO_AUTHORIZE_ENDPOINT = "https://kauth.kakao.com/oauth/authorize"
KAKAO_TOKEN_ENDPOINT = "https://kauth.kakao.com/oauth/token"
KAKAO_SCOPES_ENDPOINT = "https://kapi.kakao.com/v2/user/scopes"
KAKAO_MEMO_ENDPOINT = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
DEFAULT_REDIRECT_URI = "http://localhost:8000/auth/kakao/callback"
DEFAULT_MESSAGE_LINK_URL = "https://www.korail.com/ticket/reservation/list"
REQUEST_TIMEOUT_SECONDS = 10
TOKEN_EXPIRY_MARGIN_SECONDS = 60
MAX_FAILURE_REASON_LENGTH = 100


class KakaoError(Exception):
    pass


class KakaoConfigurationError(KakaoError):
    pass


class KakaoTokenExpiredError(KakaoConfigurationError):
    pass


class KakaoApiError(KakaoError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _token_file_path() -> Path:
    configured_path = os.getenv("KAKAO_TOKEN_FILE", "").strip()
    if configured_path:
        return Path(configured_path).expanduser()
    return Path(__file__).resolve().parents[1] / ".kakao_tokens.json"


def _get_rest_api_key() -> str:
    rest_api_key = os.getenv("KAKAO_REST_API_KEY", "").strip()
    if not rest_api_key:
        raise KakaoConfigurationError("KAKAO_REST_API_KEY가 설정되지 않았습니다.")
    return rest_api_key


def get_redirect_uri() -> str:
    redirect_uri = os.getenv("KAKAO_REDIRECT_URI", DEFAULT_REDIRECT_URI).strip()
    parsed_uri = parse.urlparse(redirect_uri)
    if (
        parsed_uri.scheme not in {"http", "https"}
        or not parsed_uri.netloc
        or parsed_uri.path != "/auth/kakao/callback"
    ):
        raise KakaoConfigurationError(
            "KAKAO_REDIRECT_URI는 KTXHelper의 카카오 콜백 주소여야 합니다: "
            f"{DEFAULT_REDIRECT_URI}"
        )
    return redirect_uri


def build_authorization_url(state: str) -> str:
    query = parse.urlencode(
        {
            "client_id": _get_rest_api_key(),
            "redirect_uri": get_redirect_uri(),
            "response_type": "code",
            "scope": "talk_message",
            "state": state,
        }
    )
    return f"{KAKAO_AUTHORIZE_ENDPOINT}?{query}"


def load_token_data() -> dict:
    token_file = _token_file_path()
    if not token_file.exists():
        return {}

    try:
        return json.loads(token_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log_error("저장된 카카오 토큰을 읽지 못했습니다.", exc_info=True)
        return {}


def save_token_data(token_response: dict, previous: dict | None = None) -> dict:
    now = int(time.time())
    token_data = dict(previous or {})
    token_data.update(token_response)

    if "expires_in" in token_response:
        token_data["access_token_expires_at"] = now + int(
            token_response["expires_in"]
        )
    if "refresh_token_expires_in" in token_response:
        token_data["refresh_token_expires_at"] = now + int(
            token_response["refresh_token_expires_in"]
        )

    token_file = _token_file_path()
    token_file.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = token_file.with_suffix(f"{token_file.suffix}.tmp")
    temporary_file.write_text(
        json.dumps(token_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary_file.chmod(0o600)
    temporary_file.replace(token_file)
    return token_data


def clear_token_data() -> None:
    token_file = _token_file_path()
    try:
        token_file.unlink(missing_ok=True)
    except OSError as exc:
        raise KakaoError("저장된 카카오 로그인 정보를 삭제하지 못했습니다.") from exc


def _post_form(
    endpoint: str,
    form: dict[str, str],
    *,
    headers: dict[str, str] | None = None,
) -> dict:
    request_headers = {
        "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
    }
    if headers:
        request_headers.update(headers)

    api_request = request.Request(
        endpoint,
        data=parse.urlencode(form).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )

    try:
        with request.urlopen(
            api_request,
            timeout=REQUEST_TIMEOUT_SECONDS,
        ) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        exc.read()
        raise KakaoApiError(
            f"카카오 API 요청에 실패했습니다 (HTTP {exc.code}).",
            status_code=exc.code,
        ) from exc
    except (error.URLError, TimeoutError) as exc:
        raise KakaoApiError("카카오 API에 연결하지 못했습니다.") from exc
    except json.JSONDecodeError as exc:
        raise KakaoApiError("카카오 API 응답을 처리하지 못했습니다.") from exc


def _get_json(
    endpoint: str,
    *,
    query: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> dict:
    if query:
        endpoint = f"{endpoint}?{parse.urlencode(query)}"
    api_request = request.Request(
        endpoint,
        headers=headers or {},
        method="GET",
    )

    try:
        with request.urlopen(
            api_request,
            timeout=REQUEST_TIMEOUT_SECONDS,
        ) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        exc.read()
        raise KakaoApiError(
            f"카카오 API 요청에 실패했습니다 (HTTP {exc.code}).",
            status_code=exc.code,
        ) from exc
    except (error.URLError, TimeoutError) as exc:
        raise KakaoApiError("카카오 API에 연결하지 못했습니다.") from exc
    except json.JSONDecodeError as exc:
        raise KakaoApiError("카카오 API 응답을 처리하지 못했습니다.") from exc


def _token_request_form(**values: str) -> dict[str, str]:
    form = dict(values)
    client_secret = os.getenv("KAKAO_CLIENT_SECRET", "").strip()
    if client_secret:
        form["client_secret"] = client_secret
    return form


def get_talk_message_consent(access_token: str) -> dict:
    consent_response = _get_json(
        KAKAO_SCOPES_ENDPOINT,
        query={"scopes": json.dumps(["talk_message"])},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    talk_message_scope = next(
        (
            scope
            for scope in consent_response.get("scopes", [])
            if scope.get("id") == "talk_message"
        ),
        None,
    )
    if not talk_message_scope or not talk_message_scope.get("agreed"):
        raise KakaoApiError(
            "카카오톡 메시지 전송(talk_message) 권한에 동의하지 않았습니다."
        )
    return consent_response


def exchange_authorization_code(code: str) -> dict:
    token_response = _post_form(
        KAKAO_TOKEN_ENDPOINT,
        _token_request_form(
            grant_type="authorization_code",
            client_id=_get_rest_api_key(),
            redirect_uri=get_redirect_uri(),
            code=code,
        ),
    )
    if not token_response.get("access_token"):
        raise KakaoApiError("카카오 토큰 응답에 access_token이 없습니다.")
    get_talk_message_consent(token_response["access_token"])
    token_response["talk_message_agreed"] = True
    return save_token_data(token_response)


def refresh_access_token() -> str:
    current_tokens = load_token_data()
    refresh_token = current_tokens.get("refresh_token", "")
    if not refresh_token:
        raise KakaoConfigurationError(
            "저장된 카카오 리프레시 토큰이 없습니다. 다시 로그인해 주세요."
        )

    token_response = _post_form(
        KAKAO_TOKEN_ENDPOINT,
        _token_request_form(
            grant_type="refresh_token",
            client_id=_get_rest_api_key(),
            refresh_token=refresh_token,
        ),
    )
    if not token_response.get("access_token"):
        raise KakaoApiError("카카오 토큰 갱신 응답에 access_token이 없습니다.")
    refreshed_tokens = save_token_data(token_response, previous=current_tokens)
    return refreshed_tokens["access_token"]


def get_valid_access_token() -> str:
    token_data = load_token_data()
    access_token = token_data.get("access_token", "")
    expires_at = token_data.get("access_token_expires_at")

    if access_token and (
        expires_at is None
        or int(expires_at) > int(time.time()) + TOKEN_EXPIRY_MARGIN_SECONDS
    ):
        return access_token

    if token_data.get("refresh_token"):
        return refresh_access_token()

    if access_token:
        raise KakaoTokenExpiredError(
            "카카오 로그인이 만료되었습니다. 다시 로그인해 주세요."
        )

    raise KakaoConfigurationError(
        "카카오 로그인이 필요합니다. KTXHelper 화면에서 카카오 로그인해 주세요."
    )


def is_kakao_connected() -> bool:
    token_data = load_token_data()
    return bool(
        (
            token_data.get("access_token")
            or token_data.get("refresh_token")
        )
        and token_data.get("talk_message_agreed") is not False
    )


def _format_departure_date(value: str) -> str:
    try:
        return datetime.strptime(value, "%Y%m%d").strftime("%Y/%m/%d")
    except ValueError:
        return value


def _format_trip(
    from_station: str,
    to_station: str,
    departure_date: str,
    departure_time: str,
) -> str:
    formatted_date = _format_departure_date(departure_date)
    formatted_time = (
        departure_time if ":" in departure_time else f"{departure_time}:00"
    )
    return (
        f"{from_station} → {to_station}\n"
        f"{formatted_date} {formatted_time} 이후 출발\n"
    )


def build_booking_success_message(
    from_station: str,
    to_station: str,
    departure_date: str,
    departure_time: str,
) -> str:
    """Build the text sent after KTX confirms a booking."""
    return (
        "🚄 KTX 예매에 성공했습니다!\n"
        + _format_trip(from_station, to_station, departure_date, departure_time)
        + "코레일에 표시된 결제기한 내에 결제해 주세요."
    )


def build_macro_failure_message(
    from_station: str,
    to_station: str,
    departure_date: str,
    departure_time: str,
    reason: str,
) -> str:
    """Build the text sent when the macro stops without a booking."""
    # The default text template accepts at most 200 characters.
    if len(reason) > MAX_FAILURE_REASON_LENGTH:
        reason = reason[: MAX_FAILURE_REASON_LENGTH - 1] + "…"
    return (
        "⚠️ KTX 매크로가 중단되었습니다.\n"
        + _format_trip(from_station, to_station, departure_date, departure_time)
        + f"사유: {reason}"
    )


def send_kakao_message(
    message: str,
    *,
    access_token: str | None = None,
    link_url: str | None = None,
    message_type: str = "GENERAL",
) -> bool:
    """Send a text message to the logged-in user's KakaoTalk chat."""
    try:
        token = access_token or get_valid_access_token()
        message_link = (
            link_url
            or os.getenv("KAKAO_MESSAGE_LINK_URL", DEFAULT_MESSAGE_LINK_URL)
        ).strip()
        template = {
            "object_type": "text",
            "text": message,
            "link": {
                "web_url": message_link,
                "mobile_web_url": message_link,
            },
            "button_title": "KTX 확인하기",
        }
        result = _post_form(
            KAKAO_MEMO_ENDPOINT,
            {
                "template_object": json.dumps(
                    template,
                    ensure_ascii=False,
                )
            },
            headers={"Authorization": f"Bearer {token}"},
        )
    except KakaoTokenExpiredError:
        log_event(
            "KAKAO_MESSAGE_FAILED",
            message_type=message_type,
            failure_code="TOKEN_EXPIRED",
            failure_reason="카카오 로그인이 만료되었습니다.",
        )
        log_error("카카오 액세스 토큰이 만료되었습니다.", exc_info=True)
        return False
    except KakaoConfigurationError:
        log_event(
            "KAKAO_MESSAGE_FAILED",
            message_type=message_type,
            failure_code="NOT_CONNECTED",
            failure_reason="카카오 로그인이 필요합니다.",
        )
        log_error("카카오톡 메시지를 보낼 연결 정보가 없습니다.", exc_info=True)
        return False
    except KakaoApiError as exc:
        failure_code = "TOKEN_EXPIRED" if exc.status_code == 401 else "API_ERROR"
        failure_reason = (
            "카카오 로그인이 만료되었습니다."
            if failure_code == "TOKEN_EXPIRED"
            else "카카오 API 요청에 실패했습니다."
        )
        log_event(
            "KAKAO_MESSAGE_FAILED",
            message_type=message_type,
            failure_code=failure_code,
            failure_reason=failure_reason,
            kakao_http_status=exc.status_code,
        )
        log_error(
            "카카오톡 메시지 발송 요청에 실패했습니다.",
            exc_info=True,
            kakao_http_status=exc.status_code,
        )
        return False
    except KakaoError:
        log_event(
            "KAKAO_MESSAGE_FAILED",
            message_type=message_type,
            failure_code="API_ERROR",
            failure_reason="카카오 메시지 발송에 실패했습니다.",
        )
        log_error("카카오톡 메시지 발송에 실패했습니다.", exc_info=True)
        return False
    except Exception:
        log_event(
            "KAKAO_MESSAGE_FAILED",
            message_type=message_type,
            failure_code="INTERNAL_ERROR",
            failure_reason="카카오 메시지 발송 중 서버 오류가 발생했습니다.",
        )
        log_error(
            "카카오톡 메시지 발송 중 처리되지 않은 오류가 발생했습니다.",
            exc_info=True,
        )
        return False

    if not isinstance(result, dict) or result.get("result_code") != 0:
        log_event(
            "KAKAO_MESSAGE_FAILED",
            message_type=message_type,
            failure_code="API_ERROR",
            failure_reason="카카오 API가 메시지 발송을 거부했습니다.",
        )
        log_error("카카오 API가 메시지 발송을 거부했습니다.")
        return False

    log_event(
        "KAKAO_MESSAGE_SENT",
        message_type=message_type,
        result="MESSAGE_SENT",
    )
    return True


def notify_booking_success(
    from_station: str,
    to_station: str,
    departure_date: str,
    departure_time: str,
) -> bool:
    message = build_booking_success_message(
        from_station,
        to_station,
        departure_date,
        departure_time,
    )
    return send_kakao_message(message, message_type="BOOKING_SUCCESS")


def notify_macro_failure(
    from_station: str,
    to_station: str,
    departure_date: str,
    departure_time: str,
    reason: str,
) -> bool:
    message = build_macro_failure_message(
        from_station,
        to_station,
        departure_date,
        departure_time,
        reason,
    )
    return send_kakao_message(message, message_type="MACRO_FAILED")
