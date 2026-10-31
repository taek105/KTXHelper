import subprocess
import unittest
from unittest.mock import MagicMock, call, patch
from urllib.parse import parse_qs, urlparse

from service import ktx
from service.exceptions import (
    BookingUnconfirmedError,
    BrowserControlError,
    BrowserSetupError,
    BrowserWindowClosedError,
    KorailAccessBlockedError,
    TrainNotFoundError,
)
from service.ktx_native import KTX, MAX_BOOKING_FAILURES, _notice
from service.native_browser import (
    NativeChrome,
    StaleBrowserControlError,
    _bridge_command,
    logged_in,
    page_state,
    schedule_rows,
)

WEB_AREA = {"path": "root.0.1", "role": "AXWebArea", "title": "", "value": ""}


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


def page_at(url):
    return [{"path": "root.0.0", "role": "AXTextField",
             "title": "주소창 및 검색창", "value": url}]


def selected_page(nodes, title):
    """The result page after a train is selected, with its bottom bar button."""
    button = {"path": "root.0.1.8.0", "role": "AXButton", "title": title, "value": ""}
    return page_at("korail.com/ticket/search/list") + [WEB_AREA] + nodes + [button], button


def dialog_nodes(*buttons, title="이용안내"):
    """An open Korail dialog. The site hides the page behind it."""
    layer = "root.0.1.9.0"
    nodes = page_at("korail.com/ticket/search/list") + [
        WEB_AREA,
        {"path": layer + ".0", "role": "AXHeading", "title": title, "value": "1"},
        {"path": layer + ".0.0", "role": "AXStaticText", "title": "", "value": title},
        {"path": layer + ".1", "role": "AXStaticText", "title": "",
         "value": "선택하신 열차는 2개 편성을 연결하여 운행하는 열차입니다."},
    ]
    nodes += [
        {"path": f"{layer}.{index + 2}", "role": "AXButton", "title": name, "value": ""}
        for index, name in enumerate(buttons)
    ]
    # Chrome's own buttons come after the page.
    return nodes + [{"path": "root.1", "role": "AXButton", "title": "닫기", "value": ""}]


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

    def test_failed_booking_attempt_reloads_results_and_retries(self):
        nodes = train_nodes((121, "예매"))
        rows = schedule_rows(nodes)
        helper = KTX("서울", "부산", "20261003", "08", [121])
        helper.browser = MagicMock()
        helper.browser.snapshot.return_value = page_at("korail.com/ticket/search/list?x=1")
        failure = BrowserControlError("Chrome window did not become active Code=11")
        with patch.object(helper, "_book_ticket", side_effect=[failure, True]) as book, \
                patch.object(helper, "_refresh_result", return_value=(nodes, rows)) as refresh:
            self.assertTrue(helper._check_result(nodes, rows))
        self.assertEqual(2, book.call_count)
        refresh.assert_called_once()

    def test_failed_attempt_on_another_page_opens_results_again(self):
        nodes = train_nodes((121, "대기신청"))
        rows = schedule_rows(nodes)
        helper = KTX("서울", "부산", "20261003", "08", [121], reserve_waiting=True)
        helper.browser = MagicMock()
        helper.browser.snapshot.return_value = page_at("korail.com/ticket/waiting")
        with patch.object(helper, "_book_ticket",
                          side_effect=[TimeoutError("'대기신청' 버튼을 찾지 못했습니다."), True]), \
                patch.object(helper, "_open_search_page", return_value=(nodes, rows)) as reopen:
            self.assertTrue(helper._check_result(nodes, rows))
        reopen.assert_called_once()

    def test_repeated_booking_failures_end_the_run(self):
        nodes = train_nodes((121, "예매"))
        rows = schedule_rows(nodes)
        helper = KTX("서울", "부산", "20261003", "08", [121])
        helper.browser = MagicMock()
        helper.browser.snapshot.return_value = page_at("korail.com/ticket/search/list")
        with patch.object(helper, "_book_ticket", side_effect=BrowserControlError("Code=11")) as book, \
                patch.object(helper, "_refresh_result", return_value=(nodes, rows)):
            with self.assertRaises(BrowserControlError):
                helper._check_result(nodes, rows)
        self.assertEqual(MAX_BOOKING_FAILURES, book.call_count)

    def test_reservation_page_after_failed_attempt_counts_as_booked(self):
        nodes = train_nodes((121, "예매"))
        helper = KTX("서울", "부산", "20261003", "08", [121])
        helper.browser = MagicMock()
        helper.browser.snapshot.return_value = page_at("korail.com/ticket/reservation/detail")
        with patch.object(helper, "_book_ticket",
                          side_effect=TimeoutError("'예매' 버튼을 찾지 못했습니다.")) as book:
            self.assertTrue(helper._check_result(nodes, schedule_rows(nodes)))
        book.assert_called_once()

    def test_failure_after_booking_click_is_not_retried(self):
        nodes = train_nodes((121, "예매"))
        helper = KTX("서울", "부산", "20261003", "08", [121])
        helper.browser = MagicMock()
        button = {"path": "root.9", "role": "AXButton", "title": "예매", "value": ""}
        with patch.object(helper, "_wait_for_button", return_value=button), \
                patch.object(helper, "_wait_for_reservation_detail",
                             side_effect=TimeoutError("예약 상세 화면으로 이동하지 못했습니다.")):
            with self.assertRaises(BookingUnconfirmedError):
                helper._check_result(nodes, schedule_rows(nodes))
        self.assertEqual(2, helper.browser.click.call_count)

    def test_only_a_notice_with_one_button_is_confirmed(self):
        button, message = _notice(dialog_nodes("확인"))
        self.assertEqual("확인", button["title"])
        self.assertEqual("선택하신 열차는 2개 편성을 연결하여 운행하는 열차입니다.", message)
        # Choices are the user's, for example leaving for the SR site.
        self.assertIsNone(_notice(dialog_nodes("취소", "확인")))
        self.assertIsNone(_notice(dialog_nodes("아니오", "네")))
        # Errors and the macro warning stay on screen.
        self.assertIsNone(_notice(dialog_nodes("확인", title="안내 메시지")))
        # A visible page has other buttons next to any 확인.
        page = dialog_nodes("확인") + [
            {"path": "root.0.1.0.0", "role": "AXButton", "title": "조회", "value": ""},
        ]
        self.assertIsNone(_notice(page))

    @patch("service.ktx_native.time.sleep")
    def test_seat_notice_is_confirmed_once_before_booking(self, _sleep):
        nodes = train_nodes((121, "예매"))
        row = schedule_rows(nodes)[0]
        helper = KTX("서울", "부산", "20261003", "08", [121])
        helper.browser = MagicMock()
        notice = dialog_nodes("확인")
        page, booking = selected_page(nodes, "예매")
        # The snapshot right after the click can still show the closed notice.
        helper.browser.snapshot.side_effect = [notice, notice, page]
        with patch.object(helper, "_wait_for_reservation_detail") as detail:
            self.assertTrue(helper._book_ticket(row, before=nodes))
        self.assertEqual(
            [call(row["seat"], fast=True), call(_notice(notice)[0], fast=True),
             call(booking, fast=True)],
            helper.browser.click.call_args_list,
        )
        detail.assert_called_once()

    @patch("service.ktx_native.time.sleep")
    def test_failed_notice_click_is_tried_again(self, _sleep):
        nodes = train_nodes((121, "예매"))
        row = schedule_rows(nodes)[0]
        helper = KTX("서울", "부산", "20261003", "08", [121])
        helper.browser = MagicMock()
        notice = dialog_nodes("확인")
        page, booking = selected_page(nodes, "예매")
        helper.browser.snapshot.side_effect = [notice, notice, page]
        helper.browser.click.side_effect = [None, StaleBrowserControlError("redrawn"), None, None]
        with patch.object(helper, "_wait_for_reservation_detail"):
            self.assertTrue(helper._book_ticket(row, before=nodes))
        confirm = _notice(notice)[0]
        self.assertEqual(
            [call(row["seat"], fast=True), call(confirm, fast=True),
             call(confirm, fast=True), call(booking, fast=True)],
            helper.browser.click.call_args_list,
        )

    @patch("service.ktx_native.time.sleep")
    def test_waiting_list_notice_is_confirmed_before_final_request(self, _sleep):
        nodes = train_nodes((121, "예약대기"))
        row = schedule_rows(nodes)[0]
        helper = KTX("서울", "부산", "20261003", "08", [121], reserve_waiting=True)
        helper.browser = MagicMock()
        page, waiting = selected_page(nodes, "예약대기신청")
        notice = dialog_nodes("확인")
        form = dialog_nodes("취소", "대기신청", title="예약대기 신청")
        helper.browser.snapshot.side_effect = [page, notice, form]

        self.assertTrue(helper._book_ticket(row, waiting=True, before=nodes))

        apply = next(node for node in form if node["title"] == "대기신청")
        self.assertEqual(
            [call(row["seat"], fast=True), call(waiting, fast=True),
             call(_notice(notice)[0], fast=True), call(apply, fast=True)],
            helper.browser.click.call_args_list,
        )

    @patch("service.ktx_native.time.sleep")
    def test_detail_wait_survives_a_transient_bridge_error(self, _sleep):
        helper = KTX("서울", "부산", "20261003", "08", [121])
        helper.browser = MagicMock()
        notice = {"path": "root.5", "role": "AXButton", "title": "확인", "value": ""}
        helper.browser.snapshot.side_effect = [
            page_at("korail.com/ticket/search/list") + [notice],
            page_at("korail.com/ticket/search/list") + [notice],
            page_at("korail.com/ticket/reservation/detail"),
        ]
        helper.browser.click.side_effect = [BrowserControlError("Code=11"), None]

        helper._wait_for_reservation_detail()

        self.assertEqual(2, helper.browser.click.call_count)

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
