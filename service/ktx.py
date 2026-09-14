import random
import re
import time
from datetime import date
from random import randint
from urllib.parse import urlencode

import undetected_chromedriver as uc
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    InvalidSessionIdException,
    NoSuchWindowException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

from service.exceptions import (
    BrowserWindowClosedError,
    InvalidDateError,
    InvalidDateFormatError,
    KorailAccessBlockedError,
    LoginFailedError,
)

KORAIL_BASE_URL = "https://www.korail.com"
LOGIN_URL = f"{KORAIL_BASE_URL}/ticket/login"
SEARCH_RESULT_URL = f"{KORAIL_BASE_URL}/ticket/search/list"
RESERVATION_DETAIL_PATH = "/ticket/reservation/detail"

WAITING_QUEUE_TIMEOUT = 1 * 60
WAITING_QUEUE_POLL_FREQUENCY = 0.5
LOGIN_WAIT_TIMEOUT = 15
RESULT_WAIT_TIMEOUT = 120

SCHEDULE_RESULT_SELECTOR = "#tab_tab_info1 .tckWrap > ul > li.tckList"
SCHEDULE_NO_DATA_SELECTOR = "#tab_tab_info1 .tck_confirm_no-data"
SCHEDULE_READY_SELECTOR = (
    f"{SCHEDULE_RESULT_SELECTOR}, {SCHEDULE_NO_DATA_SELECTOR}"
)
_SEAT_STATUS_BOOKABLE = "bookable"
_SEAT_STATUS_WAITING = "waiting"
_SEAT_STATUS_UNAVAILABLE = "unavailable"
WAITING_QUEUE_SELECTORS = (
    "#NetFunnel_Loading_Popup",
    "[id^='NetFunnel_Loading_Popup_']",
    "[id*='NetFunnel' i]",
    "[class*='NetFunnel' i]",
    "iframe[src*='netfunnel' i]",
)
WAITING_QUEUE_TEXT_MARKERS = (
    "현재 접속자가 많아",
    "접속 대기 중",
    "기다리시면",
    "예상 대기시간",
    "대기자 수",
    "나의 대기 순서",
)
AUTOMATION_BLOCK_MARKERS = (
    "미허가도구사용시이용이제한될수있습니다",
    "CODE:-4003",
)

# The result route accepts names without codes. Codes are supplied for the
# commonly used KTX stations so the query remains unambiguous.
KTX_STATION_CODES = {
    "행신": "0390",
    "서울": "0001",
    "용산": "0104",
    "수서": "0551",
    "영등포": "0002",
    "광명": "0501",
    "수원": "0003",
    "평택": "0004",
    "천안아산": "0502",
    "천안": "0005",
    "조치원": "0007",
    "오송": "0297",
    "대전": "0010",
    "서대전": "0025",
    "김천구미": "0507",
    "구미": "0013",
    "동대구": "0015",
    "대구": "0023",
    "서대구": "0506",
    "경산": "0024",
    "밀양": "0017",
    "구포": "0019",
    "부산": "0020",
    "경주": "0508",
    "울산(통도사)": "0509",
    "포항": "0515",
    "창원중앙": "0512",
    "마산": "0059",
    "논산": "0027",
    "익산": "0030",
    "정읍": "0033",
    "광주송정": "0036",
    "목포": "0041",
    "전주": "0045",
    "순천": "0051",
    "여수EXPO": "0053",
    "청량리": "0090",
    "강릉": "0115",
    "정동진": "0262",
    "동해": "0113",
}

KTX_STATIONS = [
    "서울",
    "용산",
    "광명",
    "수서",
    "영등포",
    "수원",
    "평택",
    "천안아산",
    "천안",
    "오송",
    "조치원",
    "대전",
    "서대전",
    "김천구미",
    "구미",
    "동대구",
    "대구",
    "경주",
    "울산(통도사)",
    "포항",
    "경산",
    "밀양",
    "부산",
    "구포",
    "창원중앙",
    "평창",
    "진부(오대산)",
    "강릉",
    "익산",
    "전주",
    "광주송정",
    "목포",
    "순천",
    "청량리",
    "여수EXPO",
    "동해",
    "정동진",
    "안동",
    "서원주",
    "원주",
    "마산",
    "행신",
    "나주",
    "정읍",
    "남원",
]


def install_chromedriver():
    driver_path = ChromeDriverManager().install()
    uc.Patcher(executable_path=driver_path).auto()
    return driver_path


def build_search_url(dpt_stn, arr_stn, date, tm):
    normalized_time = str(tm).replace(":", "")
    if len(normalized_time) == 2:
        normalized_time += "0000"
    elif len(normalized_time) == 4:
        normalized_time += "00"

    params = {
        "txtMenuId": "11",
        "radJobId": "1",
        "searchType": "GENERAL",
        "txtGoStart": dpt_stn,
        "txtGoEnd": arr_stn,
        "txtGoAbrdDt": date,
        "txtGoHour": normalized_time,
        "txtPsgFlg_1": "1",
        "txtPsgFlg_2": "0",
        "txtPsgFlg_3": "0",
        "txtPsgFlg_4": "0",
        "txtPsgFlg_5": "0",
        "txtPsgFlg_8": "0",
        "txtPsgFlg_99": "0",
        "txtTrnGpCd": "100",
        "selGoTrain": "00",
        "selGoSeat1": "015",
        "txtSeatAttCd_4": "015",
        "rtYn": "N",
        "adjStnScdlOfrFlg": "N",
        "adjStnScdlOfrFlg2": "N",
        "srtCheckYn": "N",
        "ebizCrossCheck": "N",
    }
    dpt_code = KTX_STATION_CODES.get(dpt_stn)
    arr_code = KTX_STATION_CODES.get(arr_stn)
    if dpt_code:
        params["txtGoStartCode"] = dpt_code
    if arr_code:
        params["txtGoEndCode"] = arr_code
    return f"{SEARCH_RESULT_URL}?{urlencode(params)}"


def get_schedule_page_state(driver):
    """Return the KORAIL waiting-queue visibility and ready-element count."""
    queue_element_visible, visible_text, ready_element_count = driver.execute_script(
        """
        const selectors = arguments[0];
        const readySelector = arguments[1];
        const isVisible = (element) => {
            const style = window.getComputedStyle(element);
            return style.display !== 'none'
                && style.visibility !== 'hidden'
                && Number(style.opacity) !== 0
                && element.getClientRects().length > 0;
        };

        const queueElements = Array.from(document.querySelectorAll(selectors));
        return [
            queueElements.some(isVisible),
            document.body?.innerText || '',
            document.querySelectorAll(readySelector).length,
        ];
        """,
        ", ".join(WAITING_QUEUE_SELECTORS),
        SCHEDULE_READY_SELECTOR,
    )
    normalized_text = "".join(visible_text.split())
    if any(marker in normalized_text for marker in AUTOMATION_BLOCK_MARKERS):
        raise KorailAccessBlockedError(
            "코레일이 자동화 도구 사용을 제한했습니다(CODE -4003). "
            "브라우저를 완전히 종료한 뒤 일반 실행 환경에서 다시 시도해주세요."
        )
    has_queue_text = any(
        "".join(marker.split()) in normalized_text
        for marker in WAITING_QUEUE_TEXT_MARKERS
    )
    return bool(queue_element_visible or has_queue_text), ready_element_count


def is_waiting_queue_visible(driver):
    queue_visible, _ = get_schedule_page_state(driver)
    return queue_visible


def wait_for_waiting_queue(driver):
    """Wait until the queue clears and a KORAIL result (including no-data) exists."""
    queue_detected = False

    def _schedule_is_ready(current_driver):
        nonlocal queue_detected
        queue_visible, ready_element_count = get_schedule_page_state(current_driver)
        if queue_visible:
            queue_detected = True
            return False
        return ready_element_count > 0

    WebDriverWait(
        driver,
        WAITING_QUEUE_TIMEOUT,
        poll_frequency=WAITING_QUEUE_POLL_FREQUENCY,
    ).until(_schedule_is_ready)
    return queue_detected


def _get_standard_seat_info(driver, row):
    """Find and classify the standard-seat box in one WebDriver command."""
    if row is None:
        return {"box": None, "status": _SEAT_STATUS_UNAVAILABLE}

    return driver.execute_script(
        """
        const boxes = Array.from(arguments[0].querySelectorAll('.price_box'));
        const box = boxes.find(
            (candidate) => (candidate.textContent || '').includes('일반실')
        ) || boxes.find(
            (candidate) => candidate.classList.contains('wait')
                || candidate.classList.contains('sold_out_wait')
                || candidate.classList.contains('yms_wait')
                || (candidate.textContent || '').includes('예약대기')
        ) || boxes[0] || null;

        if (!box) {
            return {box: null, status: 'unavailable'};
        }

        const classes = box.classList;
        const text = box.textContent || '';
        const waitingClasses = ['wait', 'sold_out_wait', 'yms_wait'];
        if (
            waitingClasses.some((className) => classes.contains(className))
            || text.includes('예약대기')
        ) {
            return {box, status: 'waiting'};
        }

        const blockedClasses = [
            'sold_out',
            'sold_out_wait',
            'sold_out_seat',
            'lack_seat',
            'btn-disabled',
            'no-data',
            'wait',
            'yms_wait',
        ];
        if (
            classes.contains('gen')
            && !blockedClasses.some(
                (className) => classes.contains(className)
            )
        ) {
            return {box, status: 'bookable'};
        }
        return {box, status: 'unavailable'};
        """,
        row,
    )


def _load_schedule_snapshots(driver):
    """Load all displayed schedule values with a single WebDriver command."""
    return driver.execute_script(
        r"""
        const rowSelector = arguments[0];
        const textOf = (element) => (element?.textContent || '')
            .replace(/\s+/g, ' ')
            .trim();

        return Array.from(document.querySelectorAll(rowSelector)).map((row) => {
            const boxes = Array.from(row.querySelectorAll('.price_box'));
            const standardBox = boxes.find(
                (box) => textOf(box).includes('일반실')
            ) || boxes[0] || null;

            return {
                number_text: textOf(row.querySelector('.flag_wrap .num')),
                route_text: textOf(row.querySelector('.data_box .txt_bk')),
                seat_text: textOf(standardBox),
                seat_classes: standardBox
                    ? Array.from(standardBox.classList)
                    : [],
            };
        });
        """,
        SCHEDULE_RESULT_SELECTOR,
    )


def _extract_schedule_item(snapshot):
    number_match = re.search(r"\d+", snapshot.get("number_text", ""))
    if number_match is None:
        raise ValueError("KTX 열차번호를 찾을 수 없습니다.")

    route_text = snapshot.get("route_text", "")
    times = re.findall(r"\d{2}:\d{2}", route_text)
    if len(times) < 2:
        times = re.findall(r"\d{4}", route_text)
        times = [f"{value[:2]}:{value[2:]}" for value in times]
    if len(times) < 2:
        raise ValueError("KTX 출발/도착 시간을 찾을 수 없습니다.")

    seat_text = snapshot.get("seat_text", "")
    seat_classes = set(snapshot.get("seat_classes") or [])
    blocked = {
        "sold_out",
        "sold_out_wait",
        "sold_out_seat",
        "lack_seat",
        "btn-disabled",
        "no-data",
    }
    if "gen" in seat_classes and not seat_classes.intersection(blocked):
        status = "예약가능"
    elif (
        seat_classes.intersection({"wait", "sold_out_wait", "yms_wait"})
        or "예약대기" in seat_text
    ):
        status = "대기신청"
    else:
        status = "매진"

    return {
        "train": int(number_match.group()),
        "depart": times[0],
        "arrive": times[1],
        "status": status,
    }


class KTX:
    def __init__(
        self,
        dpt_stn,
        arr_stn,
        dpt_dt,
        dpt_tm,
        target_index,
        reserve_waiting=False,
    ):
        self.login_id = None
        self.login_psw = None
        self.dpt_stn = dpt_stn
        self.arr_stn = arr_stn
        self.dpt_dt = dpt_dt
        self.dpt_tm = dpt_tm
        self.target_index = target_index
        self.reserve_waiting = reserve_waiting
        self.driver = None
        self.search_url = build_search_url(dpt_stn, arr_stn, dpt_dt, dpt_tm)
        self.is_booked = False
        self.cnt_refresh = 0
        self._check_input()

    def _check_input(self):
        if not str(self.dpt_dt).isnumeric():
            raise InvalidDateFormatError("날짜는 숫자로만 이루어져야 합니다.")
        if len(str(self.dpt_dt)) != 8:
            raise InvalidDateFormatError("날짜는 YYYYMMDD 8자리로 입력해주세요.")
        try:
            value = str(self.dpt_dt)
            date.fromisoformat(f"{value[:4]}-{value[4:6]}-{value[6:8]}")
        except ValueError as exc:
            raise InvalidDateError(
                "날짜가 잘못 되었습니다. YYYYMMDD 형식으로 입력해주세요."
            ) from exc

    def _set_log_info(self, login_id, login_psw):
        self.login_id = login_id
        self.login_psw = login_psw

    def _start_authenticated_session(self, login_id, login_psw):
        self._run_driver()
        self._set_log_info(login_id, login_psw)
        self._login()
        self._check_login()

    def _run_driver(self):
        driver_path = install_chromedriver()
        try:
            self.driver = uc.Chrome(
                driver_executable_path=driver_path,
                headless=False,
            )
        except WebDriverException:
            self.driver = uc.Chrome(
                driver_executable_path=driver_path,
                headless=False,
            )

    def _login(self):
        self.driver.get(LOGIN_URL)
        wait = WebDriverWait(self.driver, LOGIN_WAIT_TIMEOUT)
        login_id = wait.until(EC.element_to_be_clickable((By.ID, "id")))
        password = wait.until(EC.element_to_be_clickable((By.ID, "password")))
        login_id.clear()
        slow_send_keys(login_id, str(self.login_id))
        password.clear()
        slow_send_keys(password, str(self.login_psw))
        wait.until(
            EC.element_to_be_clickable(
                (By.CSS_SELECTOR, "button.btn_bn-depblue")
            )
        ).click()
        return self.driver

    def _check_login(self):
        def _is_logged_in(driver):
            logout_elements = driver.find_elements(
                By.XPATH,
                "//a[normalize-space()='로그아웃'] | "
                "//button[normalize-space()='로그아웃']",
            )
            return bool(logout_elements) and "/login" not in driver.current_url

        try:
            WebDriverWait(self.driver, LOGIN_WAIT_TIMEOUT).until(_is_logged_in)
        except TimeoutException as exc:
            raise LoginFailedError(
                "코레일 로그인 성공 여부를 확인할 수 없습니다. "
                "회원번호 로그인 탭과 로그인 정보를 확인해주세요."
            ) from exc
        return True

    def _open_search_page(self):
        self.driver.get(self.search_url)
        wait_for_waiting_queue(self.driver)

    def _go_search(self):
        self._open_search_page()
        print("KTX를 조회합니다")
        print(
            f"출발역:{self.dpt_stn} , 도착역:{self.arr_stn}\n"
            f"날짜:{self.dpt_dt}, 시간: {self.dpt_tm}시 이후\n"
        )
        target_indexes = ", ".join(f"{i}번" for i in self.target_index)
        print(f"{target_indexes} KTX를 예매합니다.")
        print(f"예약 대기 사용: {self.reserve_waiting}")

    def _get_result_row(self, index):
        rows = self.driver.find_elements(By.CSS_SELECTOR, SCHEDULE_RESULT_SELECTOR)
        if index < 1 or index > len(rows):
            return None
        return rows[index - 1]

    def _click_seat_box(self, box):
        clickable = box.find_element(By.CSS_SELECTOR, ".inner a, .inner button")
        try:
            clickable.click()
        except ElementClickInterceptedException:
            self.driver.execute_script("arguments[0].click();", clickable)

    def _click_popup_button(self, button_text, timeout):
        popup_xpath = "//div[contains(@class, 'layerWrap')]"

        def _visible_button(driver):
            for popup in driver.find_elements(By.XPATH, popup_xpath):
                if not popup.is_displayed():
                    continue
                for button in popup.find_elements(By.TAG_NAME, "button"):
                    if (
                        button.is_displayed()
                        and button.is_enabled()
                        and button.text.strip() == button_text
                    ):
                        return button
            return False

        button = WebDriverWait(self.driver, timeout).until(_visible_button)
        try:
            button.click()
        except ElementClickInterceptedException:
            self.driver.execute_script("arguments[0].click();", button)

        def _popup_closed(_driver):
            try:
                return not button.is_displayed()
            except StaleElementReferenceException:
                return True

        WebDriverWait(self.driver, 5).until(_popup_closed)

    def _dismiss_optional_train_notice(self, timeout=2):
        try:
            self._click_popup_button("확인", timeout)
        except TimeoutException:
            return False

        print("열차 이용안내 확인")
        return True

    def _click_reservation_button(self, button_text):
        def _matching_button(driver):
            return driver.execute_script(
                """
                const buttonText = arguments[0];
                return Array.from(
                    document.querySelectorAll('button.reservbtn')
                ).find((button) => {
                    const style = window.getComputedStyle(button);
                    const isVisible = style.display !== 'none'
                        && style.visibility !== 'hidden'
                        && Number(style.opacity) !== 0
                        && button.getClientRects().length > 0;
                    return isVisible
                        && !button.disabled
                        && (button.textContent || '').includes(buttonText);
                }) || null;
                """,
                button_text,
            )

        button = WebDriverWait(self.driver, 15).until(_matching_button)
        try:
            button.click()
        except ElementClickInterceptedException:
            self.driver.execute_script("arguments[0].click();", button)

    def _wait_for_reservation_detail(self):
        WebDriverWait(self.driver, RESULT_WAIT_TIMEOUT).until(
            lambda driver: RESERVATION_DETAIL_PATH in driver.current_url
        )

    def _submit_reservation_wait(self):
        self._click_popup_button("대기신청", 20)

    def _book_ticket(self, standard_box):
        print("KTX 일반실 예매 가능 클릭")
        self._click_seat_box(standard_box)
        self._click_reservation_button("예매")
        self._dismiss_optional_train_notice()
        self._wait_for_reservation_detail()
        self.is_booked = True
        print("KTX 예약 완료")
        return True

    def _refresh_result(self):
        try:
            time.sleep(random.uniform(0.3, 0.6))
            self.driver.refresh()
            wait_for_waiting_queue(self.driver)
            self.cnt_refresh += 1
            print(f"새로고침 {self.cnt_refresh}회")
        except StaleElementReferenceException:
            print("요소가 더 이상 유효하지 않음. 다시 조회합니다.")
            self.driver.get(self.search_url)
            wait_for_waiting_queue(self.driver)

    def _apply_for_reservation_wait(self, standard_box):
        print("KTX 예약 대기 신청")
        self._click_seat_box(standard_box)
        self._click_reservation_button("예약대기신청")
        self._submit_reservation_wait()
        self.is_booked = True
        print("KTX 예약 대기 완료")
        return True

    def _check_result(self):
        wait = WebDriverWait(self.driver, RESULT_WAIT_TIMEOUT)
        while not self.is_booked:
            wait.until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, SCHEDULE_READY_SELECTOR)
                )
            )
            for i in self.target_index:
                try:
                    row = self._get_result_row(i)
                    seat_info = _get_standard_seat_info(
                        self.driver,
                        row,
                    )
                    standard_box = seat_info["box"]
                    seat_status = seat_info["status"]
                except StaleElementReferenceException:
                    standard_box = None
                    seat_status = _SEAT_STATUS_UNAVAILABLE

                if seat_status == _SEAT_STATUS_BOOKABLE:
                    return self._book_ticket(standard_box)
                if (
                    self.reserve_waiting
                    and seat_status == _SEAT_STATUS_WAITING
                ):
                    return self._apply_for_reservation_wait(standard_box)

            self._refresh_result()

        return True

    def run(self, login_id, login_psw):
        try:
            self._start_authenticated_session(login_id, login_psw)
            self._go_search()
            self._check_result()
            return self.is_booked
        except (NoSuchWindowException, InvalidSessionIdException) as exc:
            error_message = "브라우저 창이 닫혀 매크로를 종료합니다."
            print(f"[매크로 종료] {error_message}")
            raise BrowserWindowClosedError(error_message) from exc


def get_schedule(login_id, login_psw, dpt_stn, arr_stn, date, tm):
    items = []
    ktx = KTX(
        dpt_stn=dpt_stn,
        arr_stn=arr_stn,
        dpt_dt=date,
        dpt_tm=tm,
        target_index=[],
    )

    try:
        ktx._start_authenticated_session(login_id, login_psw)
        ktx._open_search_page()
        snapshots = _load_schedule_snapshots(ktx.driver)
        for snapshot in snapshots:
            try:
                items.append(_extract_schedule_item(snapshot))
            except ValueError:
                continue
    finally:
        time.sleep(0.3)
        if ktx.driver is not None:
            ktx.driver.quit()
    return items


def slow_send_keys(element, text, delay_range=(0.05, 0.1)):
    for character in text:
        element.send_keys(character)
        time.sleep(random.uniform(*delay_range))
