import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

from service import ktx
from service.exceptions import BrowserWindowClosedError, KorailAccessBlockedError
from service.ktx_native import KTX
from service.native_browser import logged_in, page_state, schedule_rows


def result_nodes(seat_title="매진"):
    row = "root.0.1.2"
    return [
        {"path": row + ".0.1", "role": "AXStaticText", "title": "", "value": "121"},
        {"path": row + ".0.3", "role": "AXHeading",
         "title": "서울 → 부산(08:12 ~ 11:33)", "value": "3"},
        {"path": row + ".1", "role": "AXLink", "title": seat_title, "value": ""},
        {"path": row + ".2", "role": "AXLink", "title": "매진", "value": ""},
    ]


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
    def test_manual_login_waits_without_using_credentials(self, make_browser, _sleep):
        browser = make_browser.return_value
        success = [
            {"path": "root.0.0", "role": "AXTextField",
             "title": "주소창 및 검색창", "value": "korail.com/ticket/main"},
            {"path": "root.0.1", "role": "AXLink", "title": "로그아웃", "value": ""},
        ]
        browser.snapshot.side_effect = [login_nodes(), success]
        helper = KTX("서울", "부산", "20261003", "08", [1])

        helper._start_manual_session("member", "password")

        browser.type_into.assert_not_called()
        browser.click.assert_not_called()

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
        helper.browser.press.assert_not_called()

    def test_bookable_row_uses_detected_snapshot_and_fast_click(self):
        helper = KTX("서울", "부산", "20261003", "08", [1])
        helper.browser = MagicMock()
        nodes = result_nodes("예매")
        row = schedule_rows(nodes)[0]
        with patch.object(helper, "_wait_for_button", return_value=None):
            self.assertTrue(helper._book_ticket(row, before=nodes))
        helper.browser.snapshot.assert_not_called()
        helper.browser.click.assert_called_once_with(row["seat"], fast=True)


if __name__ == "__main__":
    unittest.main()
