import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

from selenium.webdriver.common.by import By

from service import ktx


class ChromeDriverInstallationTest(unittest.TestCase):
    @patch.object(ktx.uc, "Patcher")
    @patch.object(ktx, "ChromeDriverManager")
    def test_installs_and_patches_windows_chromedriver(
        self,
        driver_manager,
        patcher,
    ):
        driver_manager.return_value.install.return_value = "chromedriver.exe"

        result = ktx.install_chromedriver()

        self.assertEqual("chromedriver.exe", result)
        patcher.assert_called_once_with(executable_path="chromedriver.exe")
        patcher.return_value.auto.assert_called_once_with()


class KtxSearchUrlTest(unittest.TestCase):
    def test_builds_ktx_only_result_url(self):
        url = ktx.build_search_url("서울", "부산", "20260910", "08")
        parsed = urlparse(url)
        query = parse_qs(parsed.query)

        self.assertEqual("/ticket/search/list", parsed.path)
        self.assertEqual(["서울"], query["txtGoStart"])
        self.assertEqual(["부산"], query["txtGoEnd"])
        self.assertEqual(["20260910"], query["txtGoAbrdDt"])
        self.assertEqual(["080000"], query["txtGoHour"])
        self.assertEqual(["100"], query["txtTrnGpCd"])
        self.assertEqual(["00"], query["selGoTrain"])
        self.assertEqual(["0001"], query["txtGoStartCode"])
        self.assertEqual(["0020"], query["txtGoEndCode"])


class KtxScheduleParserTest(unittest.TestCase):
    def make_snapshot(self, *, seat_text, seat_classes):
        return {
            "number_text": "KTX 123",
            "route_text": "서울 → 부산 (08:01 ~ 10:42)",
            "seat_text": seat_text,
            "seat_classes": seat_classes,
        }

    def test_loads_all_schedule_rows_with_one_webdriver_command(self):
        driver = MagicMock()
        expected = [
            self.make_snapshot(
                seat_text="일반실 예매",
                seat_classes=["price_box", "fl-l", "gen"],
            )
        ]
        driver.execute_script.return_value = expected

        result = ktx._load_schedule_snapshots(driver)

        self.assertEqual(expected, result)
        driver.execute_script.assert_called_once()
        self.assertEqual(
            ktx.SCHEDULE_RESULT_SELECTOR,
            driver.execute_script.call_args.args[1],
        )

    def test_parses_available_general_seat(self):
        item = ktx._extract_schedule_item(
            self.make_snapshot(
                seat_text="일반실 예매",
                seat_classes=["price_box", "fl-l", "gen"],
            )
        )

        self.assertEqual(
            {
                "train": 123,
                "depart": "08:01",
                "arrive": "10:42",
                "status": "예약가능",
            },
            item,
        )

    def test_parses_reservation_waiting(self):
        item = ktx._extract_schedule_item(
            self.make_snapshot(
                seat_text="일반실 예약대기",
                seat_classes=["price_box", "fl-l", "wait"],
            )
        )

        self.assertEqual("대기신청", item["status"])


class KtxOptionalNoticeTest(unittest.TestCase):
    def setUp(self):
        self.ktx = ktx.KTX(
            dpt_stn="서울",
            arr_stn="부산",
            dpt_dt="20260910",
            dpt_tm="08",
            target_index=[1],
        )
        self.ktx.driver = MagicMock()

    def test_closes_train_notice_regardless_of_message(self):
        confirm_button = MagicMock()
        notice = MagicMock()
        notice.is_displayed.side_effect = [True, False]
        notice.find_element.return_value = confirm_button
        self.ktx.driver.find_elements.return_value = [notice]

        self.assertTrue(self.ktx._dismiss_optional_train_notice())
        notice.find_element.assert_called_once_with(
            By.XPATH,
            ".//button[normalize-space()='확인']",
        )
        confirm_button.click.assert_called_once_with()

    def test_continues_when_notice_is_absent(self):
        self.ktx.driver.find_elements.return_value = []

        self.assertFalse(self.ktx._dismiss_optional_train_notice(timeout=0))


if __name__ == "__main__":
    unittest.main()
