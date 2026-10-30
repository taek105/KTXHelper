import unittest
from unittest.mock import patch

from fastapi import HTTPException

from api.srt.endpoint import srt_router
from service.exceptions import (
    BrowserControlError,
    BrowserSetupError,
    BrowserWindowClosedError,
    KorailAccessBlockedError,
)

SCHEDULE_FORM = dict(
    login_id=None, login_psw=None, date="20261003", time="08",
    from_station="서울", to_station="부산",
)



class BrowserWindowApiTest(unittest.TestCase):
    @patch.object(
        srt_router,
        "run_macro_logic",
        side_effect=BrowserWindowClosedError("브라우저 창이 닫혔습니다."),
    )
    def test_post_run_returns_conflict_for_closed_browser(self, _run_macro):
        with self.assertRaises(HTTPException) as raised:
            srt_router.post_run(
                login_id=None, login_psw=None, from_station="서울", to_station="부산",
                date="20261003", time="08", reserve=False, seats=[1],
            )
        self.assertEqual(409, raised.exception.status_code)

    @patch.object(
        srt_router,
        "run_get_schedule",
        side_effect=KorailAccessBlockedError("코레일 자동화 제한"),
    )
    def test_get_schedule_reports_access_block(self, _get_schedule):
        with self.assertRaises(HTTPException) as raised:
            srt_router.get_schedule(**SCHEDULE_FORM)
        self.assertEqual(429, raised.exception.status_code)

    def test_get_schedule_explains_setup_and_timeout_errors(self):
        for error, status_code in (
            (BrowserSetupError("손쉬운 사용에서 이 앱의 제어 권한을 켜주세요."), 503),
            (TimeoutError("Chrome 창이 열리지 않았습니다."), 504),
            (BrowserControlError("Chrome 조작 실패: Code=11"), 502),
        ):
            with self.subTest(error=type(error).__name__), \
                    patch.object(srt_router, "run_get_schedule", side_effect=error), \
                    patch.object(srt_router, "log_event") as log_event:
                with self.assertRaises(HTTPException) as raised:
                    srt_router.get_schedule(**SCHEDULE_FORM)
                self.assertEqual(status_code, raised.exception.status_code)
                self.assertEqual(str(error), raised.exception.detail)
                self.assertEqual("SCHEDULE_FAILED", log_event.call_args.args[0])
                self.assertEqual(str(error), log_event.call_args.kwargs["failure_reason"])



if __name__ == "__main__":
    unittest.main()
