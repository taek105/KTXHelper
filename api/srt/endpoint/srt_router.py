from time import perf_counter
from uuid import uuid4

from fastapi import APIRouter, Form, HTTPException

from api.srt.controller.srt_controller import run_get_schedule, run_macro_logic
from core.event_logging import log_error, log_event, reset_run_id, set_run_id
from service.kakao import notify_booking_success
from service.exceptions import (
    BrowserControlError,
    BrowserSetupError,
    BrowserWindowClosedError,
    InvalidDateError,
    InvalidDateFormatError,
    InvalidStationNameError,
    InvalidTimeFormatError,
    KorailAccessBlockedError,
    LoginFailedError,
    RefreshButtonNotFoundError,
    TrainNotFoundError,
)

router = APIRouter()

# Failures with a known cause: (exception types, HTTP status, failure code).
KNOWN_ERRORS = (
    (BrowserWindowClosedError, 409, "BROWSER_CLOSED"),
    (TrainNotFoundError, 409, "TRAIN_NOT_FOUND"),
    (RefreshButtonNotFoundError, 503, "REFRESH_BUTTON_NOT_FOUND"),
    (KorailAccessBlockedError, 429, "KORAIL_ACCESS_BLOCKED"),
    (LoginFailedError, 401, "INVALID_LOGIN"),
    (
        (
            InvalidDateError,
            InvalidDateFormatError,
            InvalidStationNameError,
            InvalidTimeFormatError,
        ),
        422,
        "INVALID_INPUT",
    ),
    (BrowserSetupError, 503, "BROWSER_SETUP_FAILED"),
    (BrowserControlError, 502, "BROWSER_CONTROL_FAILED"),
    (TimeoutError, 504, "TIMEOUT"),
)


def _known_error(exc: Exception) -> tuple[int, str] | None:
    return next(
        (
            (status_code, failure_code)
            for types, status_code, failure_code in KNOWN_ERRORS
            if isinstance(exc, types)
        ),
        None,
    )


@router.post("/run", response_model=bool)
def post_run(
    login_id: str = Form(...),
    login_psw: str = Form(...),
    from_station: str = Form(...),
    to_station: str = Form(...),
    date: str = Form(...),
    time: str = Form(...),
    reserve: bool = Form(False),
    trains: list[int] = Form(default_factory=list),  # noqa: B008
) -> bool:
    # A tab opened before an update can still send the old form fields.
    if not trains:
        raise HTTPException(
            status_code=422,
            detail="선택한 열차가 없습니다. 페이지를 새로고침한 뒤 스케줄을 다시 조회해주세요.",
        )
    run_id = uuid4().hex
    run_context_token = set_run_id(run_id)
    started_at = perf_counter()
    log_event(
        "MACRO_STARTED",
        from_station=from_station,
        to_station=to_station,
        departure_date=date,
        departure_time=time,
        reserve_requested=reserve,
        target_trains=trains,
    )
    try:
        run_macro_logic(
            login_id=login_id,
            login_psw=login_psw,
            dpt_stn=from_station,
            arr_stn=to_station,
            dpt_dt=date,
            dpt_tm=time,
            target=trains,
            want_reserve=reserve,
        )
    except Exception as exc:
        known = _known_error(exc)
        reason = str(exc) if known else "매크로 실행 중 서버 오류가 발생했습니다."
        _log_macro_failure(known[1] if known else "INTERNAL_ERROR", reason, started_at)
        if known is None:
            raise
        raise HTTPException(status_code=known[0], detail=reason) from exc
    else:
        log_event(
            "MACRO_COMPLETED",
            result="BOOKED",
            duration_ms=round((perf_counter() - started_at) * 1000),
        )
        try:
            notify_booking_success(
                from_station=from_station,
                to_station=to_station,
                departure_date=date,
                departure_time=time,
            )
        except Exception:
            log_event(
                "KAKAO_MESSAGE_FAILED",
                message_type="BOOKING_SUCCESS",
                failure_code="INTERNAL_ERROR",
                failure_reason="카카오 메시지 발송 중 서버 오류가 발생했습니다.",
            )
            log_error(
                "카카오 메시지 발송 중 처리되지 않은 오류가 발생했습니다.",
                exc_info=True,
            )
        return True
    finally:
        reset_run_id(run_context_token)


def _log_macro_failure(
    failure_code: str,
    failure_reason: str,
    started_at: float,
) -> None:
    log_event(
        "MACRO_FAILED",
        failure_code=failure_code,
        failure_reason=failure_reason,
        duration_ms=round((perf_counter() - started_at) * 1000),
    )


@router.post("/schedule", response_model=None)
def get_schedule(
    login_id: str = Form(...),
    login_psw: str = Form(...),
    date: str = Form(...),
    time: str = Form(...),
    from_station: str = Form(...),
    to_station: str = Form(...),
):
    try:
        return run_get_schedule(
            login_id,
            login_psw,
            from_station,
            to_station,
            date,
            time,
        )
    except Exception as exc:
        known = _known_error(exc)
        if known is None:
            raise
        log_event(
            "SCHEDULE_FAILED",
            failure_code=known[1],
            failure_reason=str(exc),
        )
        raise HTTPException(status_code=known[0], detail=str(exc)) from exc
