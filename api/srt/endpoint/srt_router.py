from time import perf_counter
from uuid import uuid4

from fastapi import APIRouter, Form, HTTPException

from api.srt.controller.srt_controller import run_get_schedule, run_macro_logic
from core.event_logging import log_error, log_event, reset_run_id, set_run_id
from service.kakao import notify_booking_success
from service.exceptions import (
    BrowserWindowClosedError,
    InvalidDateError,
    InvalidDateFormatError,
    InvalidStationNameError,
    InvalidTimeFormatError,
    KorailAccessBlockedError,
    LoginFailedError,
)

router = APIRouter()


@router.post("/run", response_model=bool)
def post_run(
    login_id: str = Form(...),
    login_psw: str = Form(...),
    from_station: str = Form(...),
    to_station: str = Form(...),
    date: str = Form(...),
    time: str = Form(...),
    reserve: bool = Form(False),
    seats: list[int] = Form(default_factory=list),  # noqa: B008
) -> bool:
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
    )
    try:
        result = run_macro_logic(
            login_id=login_id,
            login_psw=login_psw,
            dpt_stn=from_station,
            arr_stn=to_station,
            dpt_dt=date,
            dpt_tm=time,
            target=seats,
            want_reserve=reserve,
        )
    except BrowserWindowClosedError as exc:
        _log_macro_failure("BROWSER_CLOSED", str(exc), started_at)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KorailAccessBlockedError as exc:
        _log_macro_failure("KORAIL_ACCESS_BLOCKED", str(exc), started_at)
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except LoginFailedError as exc:
        _log_macro_failure("INVALID_LOGIN", str(exc), started_at)
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except (
        InvalidDateError,
        InvalidDateFormatError,
        InvalidStationNameError,
        InvalidTimeFormatError,
    ) as exc:
        _log_macro_failure("INVALID_INPUT", str(exc), started_at)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:
        _log_macro_failure(
            "INTERNAL_ERROR",
            "매크로 실행 중 서버 오류가 발생했습니다.",
            started_at,
        )
        raise
    else:
        log_event(
            "MACRO_COMPLETED",
            result="BOOKED" if result else "NOT_BOOKED",
            duration_ms=round((perf_counter() - started_at) * 1000),
        )
        if result:
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
        return result
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
    except KorailAccessBlockedError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except LoginFailedError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except BrowserWindowClosedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
