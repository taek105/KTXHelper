"""KORAIL search and reservation using a regular Chrome window on macOS."""

import random
import time
from datetime import date

from service.exceptions import InvalidDateError, InvalidDateFormatError, LoginFailedError
from service.native_browser import (
    NativeChrome,
    StaleBrowserControlError,
    address,
    logged_in,
    page_state,
    schedule_rows,
    wait_for_results,
)

LOGIN_URL = "https://www.korail.com/ticket/login"
RESERVATION_DETAIL_PATH = "/ticket/reservation/detail"
LOGIN_WAIT_TIMEOUT = 60
MANUAL_LOGIN_WAIT_TIMEOUT = 300
RESULT_WAIT_TIMEOUT = 120


def _find_button(nodes, title, *, excluded_paths=()):
    candidates = [
        node for node in nodes
        if node["role"] == "AXButton"
        and title in node["title"]
        and node.get("enabled") != "0"
        and node["path"] not in excluded_paths
    ]
    exact = [node for node in candidates if node["title"] == title]
    return (exact or candidates or [None])[-1]


def _login_controls(nodes):
    member = next(
        (node for node in nodes if node["role"] == "AXTextField"
         and node["title"] == "회원번호"), None
    )
    password = next(
        (node for node in nodes if node["role"] in ("AXTextField", "AXSecureTextField")
         and node["title"] == "비밀번호"), None
    )
    if member is None or password is None:
        return None
    buttons = [node for node in nodes if node["role"] == "AXButton"
               and node["title"] == "로그인" and node.get("enabled") != "0"]
    form_path = password["path"].rsplit(".", 4)[0]
    button = next((node for node in buttons if node["path"] == f"{form_path}.2"), None)
    if button is None and buttons:
        password_parts = password["path"].split(".")
        def shared_depth(node):
            return next(
                (i for i, (left, right) in enumerate(
                    zip(password_parts, node["path"].split("."))
                ) if left != right),
                min(len(password_parts), len(node["path"].split("."))),
            )
        button = max(buttons, key=shared_depth)
    return (member, password, button) if button else None


def _login_message_texts(nodes):
    return {
        " ".join((str(node.get("title") or "") + str(node.get("value") or "")).split())
        for node in nodes
        if node["role"] in ("AXStaticText", "AXHeading", "AXAlert", "AXDialog")
    } - {""}


def _login_error(nodes, previous_texts):
    # The login page already shows a general warning about five failed attempts.
    # Only messages that appeared after submitting the form are login errors.
    new_texts = {
        text.replace("로그인 5회 실패 시 로그인할 수 없습니다", "")
        for text in _login_message_texts(nodes) - previous_texts
    }
    errors = (
        ("통신 중 오류", "코레일 로그인 중 통신 오류가 표시되었습니다."),
        ("로그인 5회 실패", "코레일 로그인 5회 실패 제한이 표시되었습니다."),
        ("등록되지 않는 회원번호", "코레일에서 회원번호 오류가 표시되었습니다."),
        ("회원번호를 확인", "코레일에서 회원번호 오류가 표시되었습니다."),
        ("비밀번호를 확인", "코레일에서 비밀번호 오류가 표시되었습니다."),
        ("비밀번호가 일치", "코레일에서 비밀번호 오류가 표시되었습니다."),
    )
    return next((message for marker, message in errors
                 if any(marker in text for text in new_texts)), None)


class KTX:
    def __init__(
        self, dpt_stn, arr_stn, dpt_dt, dpt_tm, target_index,
        reserve_waiting=False,
    ):
        from service.ktx import build_search_url

        self.dpt_stn = dpt_stn
        self.arr_stn = arr_stn
        self.dpt_dt = dpt_dt
        self.dpt_tm = dpt_tm
        self.target_index = target_index
        self.reserve_waiting = reserve_waiting
        self.browser = None
        self.search_url = build_search_url(dpt_stn, arr_stn, dpt_dt, dpt_tm)
        self.is_booked = False
        self.cnt_refresh = 0
        self._check_input()

    def _check_input(self):
        value = str(self.dpt_dt)
        if not value.isnumeric() or len(value) != 8:
            raise InvalidDateFormatError("날짜는 YYYYMMDD 8자리로 입력해주세요.")
        try:
            date.fromisoformat(f"{value[:4]}-{value[4:6]}-{value[6:8]}")
        except ValueError as exc:
            raise InvalidDateError("날짜가 잘못 되었습니다. YYYYMMDD 형식으로 입력해주세요.") from exc

    def _type_login_field(self, title, value, *, paste=False):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            nodes = self.browser.snapshot()
            page_state(nodes)
            field = next(
                (node for node in nodes
                 if node["role"] in ("AXTextField", "AXSecureTextField")
                 and node["title"] == title),
                None,
            )
            if field:
                try:
                    if paste:
                        self.browser.type_into(field, value, paste=True)
                    else:
                        self.browser.type_into(field, value)
                    return
                except StaleBrowserControlError:
                    pass
            time.sleep(0.1)
        raise LoginFailedError(f"코레일 {title} 입력칸을 찾지 못했습니다.")

    def _start_manual_session(self, login_id=None, login_psw=None):
        self.browser = NativeChrome(LOGIN_URL)
        try:
            deadline = time.monotonic() + MANUAL_LOGIN_WAIT_TIMEOUT
            while time.monotonic() < deadline:
                nodes = self.browser.snapshot()
                page_state(nodes)
                if logged_in(nodes) and "/ticket/login" not in address(nodes):
                    return
                time.sleep(0.3)
            raise LoginFailedError("5분 안에 코레일 수동 로그인 완료를 확인하지 못했습니다.")
        except Exception:
            self._close_browser()
            raise

    def _start_authenticated_session(self, login_id=None, login_psw=None):
        if not login_id or not login_psw:
            raise LoginFailedError("코레일 회원번호와 비밀번호를 입력해주세요.")
        self.browser = NativeChrome(LOGIN_URL)
        try:
            deadline = time.monotonic() + 20
            controls = None
            while time.monotonic() < deadline:
                nodes = self.browser.snapshot()
                page_state(nodes)
                controls = _login_controls(nodes)
                if controls:
                    break
                time.sleep(0.5)
            if not controls:
                raise LoginFailedError("코레일 로그인 입력칸을 찾지 못했습니다.")
            self._type_login_field("회원번호", str(login_id))
            time.sleep(0.5)
            self._type_login_field("비밀번호", str(login_psw), paste=True)
            # Let the site's input handlers finish. The form may redraw after
            # typing, so refresh the button path without requiring the fields
            # to remain at their original accessibility paths.
            time.sleep(0.5)
            nodes = self.browser.snapshot()
            page_state(nodes)
            if logged_in(nodes) and "/ticket/login" not in address(nodes):
                return
            controls = _login_controls(nodes)
            button = controls[2] if controls else _find_button(nodes, "로그인")
            if button is None:
                raise LoginFailedError("코레일 로그인 버튼을 찾지 못했습니다.")
            previous_texts = _login_message_texts(nodes)
            self.browser.click(button)

            deadline = time.monotonic() + LOGIN_WAIT_TIMEOUT
            while time.monotonic() < deadline:
                nodes = self.browser.snapshot()
                page_state(nodes)
                if logged_in(nodes) and "/ticket/login" not in address(nodes):
                    return
                error = _login_error(nodes, previous_texts)
                if error:
                    time.sleep(3)
                    raise LoginFailedError(error)
                time.sleep(0.5)
            raise LoginFailedError("코레일 로그인 성공을 확인하지 못했습니다. 회원번호와 비밀번호를 확인해주세요.")
        except Exception:
            self._close_browser()
            raise

    def _close_browser(self):
        if self.browser:
            self.browser.close()
            self.browser = None

    def _open_search_page(self):
        self.browser.navigate(self.search_url)
        return wait_for_results(self.browser, RESULT_WAIT_TIMEOUT)

    def _refresh_result(self):
        time.sleep(0.8)
        self.browser.refresh()
        time.sleep(0.2)
        self.cnt_refresh += 1
        print(f"새로고침 {self.cnt_refresh}회")
        return wait_for_results(self.browser, RESULT_WAIT_TIMEOUT)

    def _wait_for_button(
        self, title, excluded_paths=(), timeout=15, success_path=None,
    ):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            nodes = self.browser.snapshot()
            page_state(nodes)
            if success_path and success_path in address(nodes):
                return None
            button = _find_button(nodes, title, excluded_paths=excluded_paths)
            if button:
                return button
            time.sleep(random.uniform(0.05, 0.1))
        raise TimeoutError(f"'{title}' 버튼을 찾지 못했습니다.")

    def _book_ticket(self, row, waiting=False, before=None):
        if before is None:
            before = self.browser.snapshot()
        current = next(
            (item for item in schedule_rows(before)
             if (item["train"], item["depart"], item["arrive"])
             == (row["train"], row["depart"], row["arrive"])
             and item["status"] == row["status"]),
            None,
        )
        if current is None:
            return False
        button_title = "예약대기신청" if waiting else "예매"
        old_enabled_paths = {
            node["path"] for node in before
            if node["role"] == "AXButton"
            and button_title in node["title"]
            and node.get("enabled") != "0"
        }
        self.browser.click(current["seat"], fast=True)
        button = self._wait_for_button(
            button_title,
            old_enabled_paths,
            success_path=RESERVATION_DETAIL_PATH if not waiting else None,
        )
        if button is None:
            self.is_booked = True
            print("KTX 예약 완료")
            return True
        self.browser.click(button, fast=True)
        if waiting:
            confirmation = self._wait_for_button("대기신청", timeout=20)
            self.browser.click(confirmation, fast=True)
        else:
            # Some trains show an information notice before the detail page.
            deadline = time.monotonic() + RESULT_WAIT_TIMEOUT
            while time.monotonic() < deadline:
                nodes = self.browser.snapshot()
                page_state(nodes)
                if RESERVATION_DETAIL_PATH in address(nodes):
                    self.is_booked = True
                    print("KTX 예약 완료")
                    return True
                notice = _find_button(nodes, "확인")
                if notice:
                    self.browser.click(notice, fast=True)
                time.sleep(random.uniform(0.05, 0.1))
            raise TimeoutError("예약 상세 화면으로 이동하지 못했습니다.")
        self.is_booked = True
        print("KTX 예약 대기 완료")
        return True

    def _check_result(self, nodes, rows):
        while not self.is_booked:
            for index in self.target_index:
                if not 1 <= index <= len(rows):
                    continue
                row = rows[index - 1]
                if row["status"] == "예약가능":
                    if self._book_ticket(row, before=nodes):
                        return True
                if self.reserve_waiting and row["status"] == "대기신청":
                    if self._book_ticket(row, waiting=True, before=nodes):
                        return True
            nodes, rows = self._refresh_result()
        return True

    def run(self, login_id=None, login_psw=None):
        self._start_authenticated_session(login_id, login_psw)
        print("KTX를 조회합니다")
        print(f"출발역:{self.dpt_stn} , 도착역:{self.arr_stn}\n"
              f"날짜:{self.dpt_dt}, 시간: {self.dpt_tm}시 이후\n")
        print(f"{', '.join(f'{i}번' for i in self.target_index)} KTX를 예매합니다.")
        print(f"예약 대기 사용: {self.reserve_waiting}")
        nodes, rows = self._open_search_page()
        return self._check_result(nodes, rows)


def get_schedule(login_id, login_psw, dpt_stn, arr_stn, date, tm):
    ktx = KTX(dpt_stn, arr_stn, date, tm, [])
    try:
        ktx._start_authenticated_session(login_id, login_psw)
        _, rows = ktx._open_search_page()
        return [
            {key: row[key] for key in ("train", "depart", "arrive", "status")}
            for row in rows
        ]
    finally:
        ktx._close_browser()
