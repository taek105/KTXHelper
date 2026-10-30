import subprocess
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

from service import ktx
from service.exceptions import (
    BrowserControlError,
    BrowserSetupError,
    BrowserWindowClosedError,
    KorailAccessBlockedError,
    TrainNotFoundError,
)
from service.ktx_native import KTX
from service.native_browser import (
    NativeChrome,
    _bridge_command,
    logged_in,
    page_state,
    schedule_rows,
)


def result_nodes(seat_title="매진"):
    row = "root.0.1.2"
    return [
        {"path": row + ".0.1", "role": "AXStaticText", "title": "", "value": "121"},
        {"path": row + ".0.3", "role": "AXHeading",
         "title": "서울 → 부산(08:12 ~ 11:33)", "value": "3"},
        {"path": row + ".1", "role": "AXLink", "title": seat_title, "value": ""},
        {"path": row + ".2", "role": "AXLink", "title": "매진", "value": ""},
    ]


def train_nodes(*trains):
    """Search result rows for (train number, general seat title) pairs."""
    nodes = []
    for index, (train, seat_title) in enumerate(trains):
        row = f"root.0.1.{index}"
        nodes += [
            {"path": row + ".0.1", "role": "AXStaticText", "title": "", "value": str(train)},
            {"path": row + ".0.3", "role": "AXHeading",
             "title": f"서울 → 부산(0{8 + index}:12 ~ 11:33)", "value": "3"},
            {"path": row + ".1", "role": "AXLink", "title": seat_title, "value": ""},
        ]
    return nodes


def login_nodes():
    form = "root.0.1.0"
    return [
        {"path": "root.0.0", "role": "AXTextField",
         "title": "주소창 및 검색창", "value": "korail.com/ticket/login"},
        {"path": form + ".0.1.0.0", "role": "AXTextField",
         "title": "회원번호", "value": ""},
        {"path": form + ".0.1.1.0", "role": "AXTextField",
         "title": "비밀번호", "value": ""},
        {"path": form + ".2", "role": "AXButton",
         "title": "로그인", "value": ""},
    ]


class KtxNativeTest(unittest.TestCase):
    def test_search_url_keeps_ktx_filter(self):
        query = parse_qs(urlparse(ktx.build_search_url("서울", "부산", "20261003", "08")).query)
        self.assertEqual(["100"], query["txtTrnGpCd"])
        self.assertEqual(["0001"], query["txtGoStartCode"])
        self.assertEqual(["0020"], query["txtGoEndCode"])

    def test_reads_first_general_seat_status(self):
        self.assertEqual("매진", schedule_rows(result_nodes())[0]["status"])
        self.assertEqual("예약가능", schedule_rows(result_nodes("예매"))[0]["status"])
        self.assertEqual("예약가능", schedule_rows(result_nodes("일반실 54,000원 5%적립"))[0]["status"])
        self.assertEqual("대기신청", schedule_rows(result_nodes("예약대기"))[0]["status"])
        self.assertEqual(121, schedule_rows(result_nodes())[0]["train"])

    def test_login_requires_logout_link(self):
        self.assertFalse(logged_in(result_nodes()))
        self.assertTrue(logged_in([{"role": "AXLink", "title": "로그아웃"}]))

    def test_detects_access_block(self):
        with self.assertRaises(KorailAccessBlockedError):
            page_state([{"role": "AXStaticText", "title": "", "value": "CODE: -4003"}])

    @patch("service.ktx_native.NativeChrome")
    def test_closed_browser_stops_login(self, make_browser):
        browser = make_browser.return_value
        browser.snapshot.side_effect = BrowserWindowClosedError("Chrome 창이 닫혔습니다.")
        helper = KTX("서울", "부산", "20261003", "08", [1])
        with self.assertRaises(BrowserWindowClosedError):
            helper._start_authenticated_session("member", "password")
        browser.close.assert_called_once()

    @patch("service.ktx_native.time.sleep")
    @patch("service.ktx_native.NativeChrome")
    def test_auto_login_then_proceeds_on_success(self, make_browser, _sleep):
        browser = make_browser.return_value
        success = [
            {"path": "root.0.0", "role": "AXTextField",
             "title": "주소창 및 검색창", "value": "korail.com/ticket/main"},
            {"path": "root.0.1", "role": "AXLink",
             "title": "로그아웃", "value": ""},
        ]
        browser.snapshot.side_effect = [login_nodes()] * 4 + [success]
        helper = KTX("서울", "부산", "20261003", "08", [1])

        helper._start_authenticated_session("member", "password")

        member, password, button = login_nodes()[1:]
        self.assertEqual(
            [(member, "member"), (password, "password")],
            [call.args for call in browser.type_into.call_args_list],
        )
        self.assertEqual({}, browser.type_into.call_args_list[0].kwargs)
        self.assertEqual({"paste": True}, browser.type_into.call_args_list[1].kwargs)
        browser.click.assert_called_once_with(button)

    @patch("service.ktx_native.time.sleep")
    @patch("service.ktx_native.NativeChrome")
    def test_login_button_is_found_after_form_redraw(self, make_browser, _sleep):
        browser = make_browser.return_value
        redrawn = [node.copy() for node in login_nodes()]
        for node in redrawn:
            node["path"] = node["path"].replace("root.0.1.0", "root.0.2.0")
        success = [
            {"path": "root.0.0", "role": "AXTextField",
             "title": "주소창 및 검색창", "value": "korail.com/ticket/main"},
            {"path": "root.0.1", "role": "AXLink", "title": "로그아웃", "value": ""},
        ]
        browser.snapshot.side_effect = [login_nodes(), login_nodes(), redrawn, redrawn, success]
        helper = KTX("서울", "부산", "20261003", "08", [1])

        helper._start_authenticated_session("member", "password")

        self.assertEqual(redrawn[2], browser.type_into.call_args_list[1].args[0])
        browser.click.assert_called_once_with(redrawn[-1])

    def test_bookable_row_is_rechecked_before_click(self):
        helper = KTX("서울", "부산", "20261003", "08", [1])
        helper.browser = MagicMock()
        helper.browser.snapshot.return_value = result_nodes("매진")
        row = schedule_rows(result_nodes("예매"))[0]
        self.assertFalse(helper._book_ticket(row))
        helper.browser.click.assert_not_called()

    def test_bookable_row_uses_detected_snapshot_and_fast_click(self):
        helper = KTX("서울", "부산", "20261003", "08", [1])
        helper.browser = MagicMock()
        nodes = result_nodes("예매")
        row = schedule_rows(nodes)[0]
        with patch.object(helper, "_wait_for_button", return_value=None):
            self.assertTrue(helper._book_ticket(row, before=nodes))
        helper.browser.snapshot.assert_not_called()
        helper.browser.click.assert_called_once_with(row["seat"], fast=True)

    def test_targets_selected_train_number_not_list_position(self):
        nodes = train_nodes((101, "예매"), (121, "예매"))
        helper = KTX("서울", "부산", "20261003", "08", [121])
        with patch.object(helper, "_book_ticket", return_value=True) as book:
            self.assertTrue(helper._check_result(nodes, schedule_rows(nodes)))
        book.assert_called_once()
        self.assertEqual(121, book.call_args.args[0]["train"])

    @patch("service.ktx_native.time.sleep")
    @patch("service.ktx_native.wait_for_results")
    def test_run_stops_when_selected_train_is_not_listed(self, wait_for_results, _sleep):
        nodes = train_nodes((101, "매진"))
        wait_for_results.return_value = (nodes, schedule_rows(nodes))
        helper = KTX("서울", "부산", "20261003", "08", [121])
        helper.browser = MagicMock()
        with patch.object(helper, "_start_authenticated_session"), \
                patch.object(helper, "_open_search_page", return_value=(nodes, schedule_rows(nodes))), \
                patch.object(helper, "_check_result") as check:
            with self.assertRaises(TrainNotFoundError) as raised:
                helper.run("member", "password")
        self.assertIn("121", str(raised.exception))
        check.assert_not_called()

    @patch("service.native_browser._bridge_command", return_value=["bridge"])
    @patch("service.native_browser.subprocess.run")
    def test_bridge_failures_have_specific_errors(self, run, _bridge_command):
        browser = NativeChrome.__new__(NativeChrome)
        browser.pid = 1
        browser.process = MagicMock()
        browser.process.poll.return_value = None
        run.return_value = MagicMock(returncode=3, stdout="", stderr="permission")
        with self.assertRaises(BrowserSetupError):
            browser.snapshot()
        run.return_value = MagicMock(
            returncode=1, stdout="", stderr="Chrome lost focus before keyboard input Code=15",
        )
        with self.assertRaises(BrowserControlError):
            browser.click({"path": "root", "role": "AXButton", "title": "예매"}, fast=True)
        run.side_effect = subprocess.TimeoutExpired("bridge", 30)
        with self.assertRaises(BrowserControlError):
            browser.snapshot()

    @patch("service.native_browser._build_bridge")
    def test_bridge_build_failure_shows_the_tool_error(self, build_bridge):
        build_bridge.side_effect = subprocess.CalledProcessError(
            1, ["xcrun"], stderr="xcrun: error: invalid active developer path\n",
        )
        with self.assertRaises(BrowserSetupError) as raised:
            _bridge_command()
        self.assertIn("invalid active developer path", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
