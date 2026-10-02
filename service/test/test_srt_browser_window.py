import unittest
from unittest.mock import patch

from fastapi import HTTPException

from api.srt.endpoint import srt_router
from service.exceptions import BrowserWindowClosedError, KorailAccessBlockedError


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
            srt_router.get_schedule(
                login_id=None, login_psw=None, date="20261003", time="08",
                from_station="서울", to_station="부산",
            )
        self.assertEqual(429, raised.exception.status_code)


if __name__ == "__main__":
    unittest.main()
