import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from fastapi import Request
from fastapi.responses import JSONResponse

import app as app_module
from api.srt.endpoint import srt_router
from core import event_logging


class EventLoggingTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.log_dir = Path(self.temporary_directory.name)
        event_logging.configure_logging(self.log_dir)

    def tearDown(self):
        event_logging.shutdown_logging()
        self.temporary_directory.cleanup()

    def _events(self) -> list[dict]:
        for handler in event_logging.event_logger.handlers:
            handler.flush()
        contents = (self.log_dir / "events.jsonl").read_text(
            encoding="utf-8"
        )
        return [json.loads(line) for line in contents.splitlines()]

    @patch.object(srt_router, "notify_booking_success", return_value=False)
    @patch.object(srt_router, "run_macro_logic", return_value=True)
    async def test_correlates_macro_and_request_without_sensitive_values(
        self,
        _run_macro,
        notify_booking,
    ):
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/run",
                "headers": [],
                "query_string": b"",
                "scheme": "http",
                "server": ("localhost", 8000),
                "client": ("127.0.0.1", 12345),
            }
        )

        async def run_endpoint(_request: Request) -> JSONResponse:
            result = srt_router.post_run(
                login_id="sensitive-login-id",
                login_psw="sensitive-password",
                from_station="서울",
                to_station="부산",
                date="20260905",
                time="0900",
                reserve=True,
                trains=[121],
            )
            return JSONResponse(result)

        response = await app_module.add_request_logging(request, run_endpoint)

        self.assertEqual(200, response.status_code)
        events = self._events()
        self.assertEqual(
            [
                "MACRO_STARTED",
                "MACRO_COMPLETED",
                "HTTP_REQUEST_FINISHED",
            ],
            [event["event"] for event in events],
        )
        self.assertEqual(
            response.headers["X-Request-ID"],
            events[0]["request_id"],
        )
        self.assertEqual(events[0]["request_id"], events[1]["request_id"])
        self.assertEqual(events[0]["run_id"], events[1]["run_id"])
        self.assertNotIn("ip", events[0])

        raw_log = (self.log_dir / "events.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("sensitive-login-id", raw_log)
        self.assertNotIn("sensitive-password", raw_log)
        notify_booking.assert_called_once()

    def test_removes_only_rotated_logs_older_than_28_days(self):
        expired = self.log_dir / "events.jsonl.2026-08-06"
        boundary_expired = self.log_dir / "events.jsonl.2026-08-07"
        retained = self.log_dir / "events.jsonl.2026-08-08"
        unrelated = self.log_dir / "keep-me.txt"
        expired.touch()
        boundary_expired.touch()
        retained.touch()
        unrelated.touch()

        event_logging._remove_expired_logs(
            self.log_dir,
            today=date(2026, 9, 4),
        )

        self.assertFalse(expired.exists())
        self.assertFalse(boundary_expired.exists())
        self.assertTrue(retained.exists())
        self.assertTrue(unrelated.exists())

    def test_logs_exception_summary_without_full_traceback(self):
        request_token = event_logging.set_request_id("request-123")
        run_token = event_logging.set_run_id("run-456")
        try:
            try:
                raise AttributeError("element is missing")
            except AttributeError:
                event_logging.log_error(
                    "매크로 실행 중 오류가 발생했습니다.",
                    exc_info=True,
                )
        finally:
            event_logging.reset_run_id(run_token)
            event_logging.reset_request_id(request_token)

        for handler in event_logging.error_logger.handlers:
            handler.flush()
        contents = (self.log_dir / "error.log").read_text(encoding="utf-8")

        self.assertIn("request_id=request-123 run_id=run-456", contents)
        self.assertIn(
            "exception=AttributeError: element is missing",
            contents,
        )
        self.assertNotIn("Traceback (most recent call last)", contents)
        self.assertNotIn('File "', contents)


if __name__ == "__main__":
    unittest.main()
