"""KORAIL search and reservation using a regular Chrome window on macOS."""

import random
import time
from datetime import date

from service.exceptions import (
    BookingUnconfirmedError,
    BrowserControlError,
    InvalidDateError,
    InvalidDateFormatError,
    LoginFailedError,
    RefreshButtonNotFoundError,
    TrainNotFoundError,
)
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
SEARCH_RESULT_PATH = "/ticket/search/list"
RESERVATION_DETAIL_PATH = "/ticket/reservation/detail"
LOGIN_WAIT_TIMEOUT = 60
RESULT_WAIT_TIMEOUT = 120
MAX_BOOKING_FAILURES = 3
NOTICE_TITLE = "이용안내"
NOTICE_CLICK_INTERVAL = 1


def _find_button(nodes, title, *, excluded_paths=(), exact=False, roles=("AXButton",)):
    candidates = [
        node for node in nodes
        if node["role"] in roles
        and (node["title"] == title if exact else title in node["title"])
        and node.get("enabled") != "0"
        and node["path"] not in excluded_paths
    ]
    exact = [node for node in candidates if node["title"] == title]
    return (exact or candidates or [None])[-1]


def _notice(nodes):
    """Return the 확인 button and message of an open 이용안내 notice."""
    # Korail hides the page behind an open dialog, so the web area then holds
    # only the dialog. A dialog with another button, such as the one that
    # leaves for the SR site, needs the user's answer.
    web_area = next((node["path"] for node in nodes if node["role"] == "AXWebArea"), None)
    if web_area is None:
        return None
    page = [node for node in nodes if node["path"].startswith(web_area + ".")]
    buttons = [node for node in page
               if node["role"] == "AXButton" and node.get("enabled") != "0"]
    if len(buttons) != 1 or buttons[0]["title"] != "확인":
        return None
    if not any(node["role"] == "AXHeading" and NOTICE_TITLE in node["title"] for node in page):
        return None
    message = " ".join(
        node["value"] for node in page
        if node["role"] == "AXStaticText" and node["value"] not in (NOTICE_TITLE, "확인")
    )
    return buttons[0], message


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
        self, dpt_stn, arr_stn, dpt_dt, dpt_tm, target_trains,
        reserve_waiting=False,
    ):
        from service.ktx import build_search_url

        self.dpt_stn = dpt_stn
        self.arr_stn = arr_stn
        self.dpt_dt = dpt_dt
        self.dpt_tm = dpt_tm
        self.target_trains = target_trains
        self.reserve_waiting = reserve_waiting
        self.browser = None
        self.search_url = build_search_url(dpt_stn, arr_stn, dpt_dt, dpt_tm)
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
        for attempt in range(4):  # Initial attempt plus up to three retries.
            time.sleep(0.8)
            try:
                self.browser.refresh()
                break
            except RefreshButtonNotFoundError as exc:
                if attempt == 3:
                    raise RefreshButtonNotFoundError(
                        "Chrome 새로고침 버튼을 4회 시도했지만 찾지 못했습니다."
                    ) from exc
                time.sleep(0.2)
        time.sleep(0.2)
        self.cnt_refresh += 1
        print(f"새로고침 {self.cnt_refresh}회")
        return wait_for_results(self.browser, RESULT_WAIT_TIMEOUT)

    def _wait_for_button(
        self, title, excluded_paths=(), timeout=15, success_path=None,
        exact=False, roles=("AXButton",),
    ):
        deadline = time.monotonic() + timeout
        next_notice_click = 0
        while time.monotonic() < deadline:
            nodes = self.browser.snapshot()
            page_state(nodes)
            if success_path and success_path in address(nodes):
                return None
            button = _find_button(
                nodes, title, excluded_paths=excluded_paths, exact=exact,
                roles=roles,
            )
            if button:
                return button
            # Some trains show a notice that holds the next step until 확인.
            # A snapshot right after the click can still show the closed
            # notice, and clicking it again would hit the page behind it.
            notice = _notice(nodes)
            if notice and time.monotonic() >= next_notice_click:
                notice_button, message = notice
                try:
                    self.browser.click(notice_button, fast=True)
                except BrowserControlError as exc:
                    print(f"안내창 확인 중 오류, 다시 시도합니다: {exc}")
                else:
                    next_notice_click = time.monotonic() + NOTICE_CLICK_INTERVAL
                    print(f"안내창 확인: {message}")
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
            print("KTX 예약 완료")
            return True
        self.browser.click(button, fast=True)
        # The booking request may already be sent. Retrying could book twice,
        # so a failure from here asks the user to check the reservation list.
        if waiting:
            try:
                # Match the final "대기신청" button exactly so the previous
                # "예약대기신청" button cannot be selected again.
                confirmation = self._wait_for_button(
                    "대기신청", timeout=RESULT_WAIT_TIMEOUT, exact=True,
                    roles=("AXButton", "AXLink"),
                )
                self.browser.click(confirmation, fast=True)
            except (BrowserControlError, TimeoutError) as exc:
                raise BookingUnconfirmedError(
                    "예약대기신청 버튼을 누른 뒤 결과를 확인하지 못했습니다. 코레일 예약 내역을 확인해주세요."
                ) from exc
            print("KTX 예약 대기 완료")
            return True
        try:
            self._wait_for_reservation_detail()
        except TimeoutError as exc:
            raise BookingUnconfirmedError(
                "예매 버튼을 누른 뒤 결과를 확인하지 못했습니다. 코레일 예약 내역을 확인해주세요."
            ) from exc
        print("KTX 예약 완료")
        return True

    def _wait_for_reservation_detail(self):
        # Some trains show an information notice before the detail page.
        deadline = time.monotonic() + RESULT_WAIT_TIMEOUT
        while time.monotonic() < deadline:
            try:
                nodes = self.browser.snapshot()
                page_state(nodes)
                if RESERVATION_DETAIL_PATH in address(nodes):
                    return
                notice = _find_button(nodes, "확인")
                if notice:
                    self.browser.click(notice, fast=True)
            except BrowserControlError as exc:
                # Safe to repeat: this loop never presses the booking button again.
                print(f"예약 상세 화면 확인 중 오류, 다시 시도합니다: {exc}")
            time.sleep(random.uniform(0.05, 0.1))
        raise TimeoutError("예약 상세 화면으로 이동하지 못했습니다.")

    def _book_targets(self, nodes, rows):
        by_train = {row["train"]: row for row in rows}
        for train in self.target_trains:
            row = by_train.get(train)
            if row is None:
                continue
            if row["status"] == "예약가능" and self._book_ticket(row, before=nodes):
                return True
            if (self.reserve_waiting and row["status"] == "대기신청"
                    and self._book_ticket(row, waiting=True, before=nodes)):
                return True
        return False

    def _return_to_results(self, nodes):
        # A failed attempt can leave a dialog or another page open.
        if SEARCH_RESULT_PATH in address(nodes):
            return self._refresh_result()
        return self._open_search_page()

    def _check_result(self, nodes, rows):
        failures = 0
        while True:
            try:
                if nodes is None:
                    nodes = self.browser.snapshot()
                    # A slow reservation can finish after the attempt timed out.
                    if RESERVATION_DETAIL_PATH in address(nodes):
                        print("KTX 예약 완료")
                        return True
                    nodes, rows = self._return_to_results(nodes)
                if self._book_targets(nodes, rows):
                    return True
                failures = 0
            except (BrowserControlError, TimeoutError) as exc:
                # One missed click must not end a long wait.
                failures += 1
                if failures >= MAX_BOOKING_FAILURES:
                    raise
                print(f"예매 시도 실패 {failures}회, 다시 조회합니다: {exc}")
                nodes = rows = None
                continue
            nodes, rows = self._refresh_result()

    def _missing_trains(self, rows):
        found = {row["train"] for row in rows}
        return [train for train in self.target_trains if train not in found]

    def run(self, login_id=None, login_psw=None):
        self._start_authenticated_session(login_id, login_psw)
        print("KTX를 조회합니다")
        print(f"출발역:{self.dpt_stn} , 도착역:{self.arr_stn}\n"
              f"날짜:{self.dpt_dt}, 시간: {self.dpt_tm}시 이후\n")
        print(f"{', '.join(map(str, self.target_trains))}번 열차를 예매합니다.")
        print(f"예약 대기 사용: {self.reserve_waiting}")
        nodes, rows = self._open_search_page()
        if self._missing_trains(rows):
            time.sleep(1)  # The list may still be rendering.
            nodes, rows = wait_for_results(self.browser, RESULT_WAIT_TIMEOUT)
        missing = self._missing_trains(rows)
        if missing:
            raise TrainNotFoundError(
                f"선택한 열차({', '.join(map(str, missing))})가 조회 결과에 없습니다. "
                "스케줄을 다시 조회해주세요."
            )
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
