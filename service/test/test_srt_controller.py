import unittest
from unittest.mock import patch

from api.srt.controller import srt_controller


class KtxControllerTest(unittest.TestCase):
    @patch.object(srt_controller, "KTX")
    def test_returns_booking_success(self, ktx_class):
        ktx_class.return_value.run.return_value = True

        result = srt_controller.run_macro_logic(
            login_id="login-id",
            login_psw="login-password",
            dpt_stn="서울",
            arr_stn="부산",
            dpt_dt="20260821",
            dpt_tm="08",
            target=[1],
            want_reserve=False,
        )

        self.assertTrue(result)
        ktx_class.return_value.run.assert_called_once_with(
            "login-id",
            "login-password",
        )


if __name__ == "__main__":
    unittest.main()
