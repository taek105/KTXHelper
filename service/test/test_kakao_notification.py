import json
import os
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from urllib import error, parse
from unittest.mock import MagicMock, patch

from service import kakao


class KakaoNotificationTest(unittest.TestCase):
    def test_builds_booking_success_message(self):
        message = kakao.build_booking_success_message(
            "서울",
            "부산",
            "20260821",
            "08",
        )

        self.assertIn("KTX 예매에 성공", message)
        self.assertIn("서울 → 부산", message)
        self.assertIn("2026/08/21 08:00 이후 출발", message)
        self.assertIn("코레일에 표시된 결제기한 내에 결제", message)

    def test_builds_macro_failure_message_within_template_limit(self):
        message = kakao.build_macro_failure_message(
            "울산(통도사)",
            "여수EXPO",
            "20260821",
            "08",
            "사유" * 200,
        )

        self.assertIn("KTX 매크로가 중단", message)
        self.assertIn("2026/08/21 08:00 이후 출발", message)
        self.assertLessEqual(len(message), 200)

    @patch.object(kakao, "log_event")
    @patch.object(kakao.request, "urlopen")
    def test_sends_default_text_template_to_memo_api(self, urlopen, log_event):
        response = MagicMock()
        response.read.return_value = b'{"result_code": 0}'
        response.__enter__.return_value = response
        urlopen.return_value = response

        sent = kakao.send_kakao_message(
            "예매 성공",
            access_token="test-access-token",
            link_url="https://example.com",
        )

        self.assertTrue(sent)
        api_request = urlopen.call_args.args[0]
        self.assertEqual(kakao.KAKAO_MEMO_ENDPOINT, api_request.full_url)
        self.assertEqual("POST", api_request.method)
        self.assertEqual(
            "Bearer test-access-token",
            api_request.get_header("Authorization"),
        )
        form = parse.parse_qs(api_request.data.decode("utf-8"))
        template = json.loads(form["template_object"][0])
        self.assertEqual("text", template["object_type"])
        self.assertEqual("예매 성공", template["text"])
        self.assertEqual("https://example.com", template["link"]["web_url"])
        urlopen.assert_called_once_with(
            api_request,
            timeout=kakao.REQUEST_TIMEOUT_SECONDS,
        )
        log_event.assert_called_once_with(
            "KAKAO_MESSAGE_SENT",
            message_type="GENERAL",
            result="MESSAGE_SENT",
        )

    @patch.object(kakao, "log_event")
    @patch.object(kakao.request, "urlopen")
    def test_skips_request_when_access_token_is_missing(
        self,
        urlopen,
        log_event,
    ):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(kakao, "load_token_data", return_value={}),
        ):
            sent = kakao.send_kakao_message("예매 성공")

        self.assertFalse(sent)
        urlopen.assert_not_called()
        log_event.assert_called_once_with(
            "KAKAO_MESSAGE_FAILED",
            message_type="GENERAL",
            failure_code="NOT_CONNECTED",
            failure_reason="카카오 로그인이 필요합니다.",
        )

    @patch.object(kakao.request, "urlopen")
    def test_does_not_include_kakao_response_body_in_api_error(self, urlopen):
        urlopen.side_effect = error.HTTPError(
            kakao.KAKAO_TOKEN_ENDPOINT,
            401,
            "Unauthorized",
            {},
            BytesIO(b'{"refresh_token":"must-not-be-logged"}'),
        )

        with self.assertRaises(kakao.KakaoApiError) as raised:
            kakao._post_form(
                kakao.KAKAO_TOKEN_ENDPOINT,
                {"refresh_token": "another-secret"},
            )

        message = str(raised.exception)
        self.assertIn("HTTP 401", message)
        self.assertNotIn("must-not-be-logged", message)
        self.assertNotIn("another-secret", message)


class KakaoOAuthTest(unittest.TestCase):
    def test_builds_authorization_url_with_message_scope_and_state(self):
        with patch.dict(
            os.environ,
            {
                "KAKAO_REST_API_KEY": "rest-api-key",
                "KAKAO_REDIRECT_URI": "http://localhost:8000/auth/kakao/callback",
            },
            clear=True,
        ):
            authorization_url = kakao.build_authorization_url("csrf-state")

        parsed_url = parse.urlparse(authorization_url)
        query = parse.parse_qs(parsed_url.query)
        self.assertEqual("https", parsed_url.scheme)
        self.assertEqual("kauth.kakao.com", parsed_url.netloc)
        self.assertEqual(["rest-api-key"], query["client_id"])
        self.assertEqual(["talk_message"], query["scope"])
        self.assertEqual(["csrf-state"], query["state"])

    def test_rejects_redirect_uri_without_callback_path(self):
        with patch.dict(
            os.environ,
            {
                "KAKAO_REST_API_KEY": "rest-api-key",
                "KAKAO_REDIRECT_URI": "http://localhost:8000",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(
                kakao.KakaoConfigurationError,
                "/auth/kakao/callback",
            ):
                kakao.build_authorization_url("csrf-state")

    @patch.object(kakao, "save_token_data")
    @patch.object(kakao, "get_talk_message_consent")
    @patch.object(kakao, "_post_form")
    def test_exchanges_authorization_code_and_saves_tokens(
        self,
        post_form,
        get_consent,
        save_token_data,
    ):
        token_response = {
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "expires_in": 21600,
        }
        post_form.return_value = token_response
        get_consent.return_value = {"id": 123456789}
        expected_tokens = {
            **token_response,
            "talk_message_agreed": True,
        }
        save_token_data.return_value = expected_tokens

        with patch.dict(
            os.environ,
            {
                "KAKAO_REST_API_KEY": "rest-api-key",
                "KAKAO_CLIENT_SECRET": "client-secret",
                "KAKAO_REDIRECT_URI": "http://localhost:8000/auth/kakao/callback",
            },
            clear=True,
        ):
            result = kakao.exchange_authorization_code("authorization-code")

        self.assertEqual(expected_tokens, result)
        post_form.assert_called_once_with(
            kakao.KAKAO_TOKEN_ENDPOINT,
            {
                "grant_type": "authorization_code",
                "client_id": "rest-api-key",
                "redirect_uri": "http://localhost:8000/auth/kakao/callback",
                "code": "authorization-code",
                "client_secret": "client-secret",
            },
        )
        get_consent.assert_called_once_with("access-token")
        save_token_data.assert_called_once_with(expected_tokens)

    @patch.object(kakao, "_get_json")
    def test_verifies_talk_message_consent(self, get_json):
        get_json.return_value = {
            "id": 123456789,
            "scopes": [
                {
                    "id": "talk_message",
                    "using": True,
                    "agreed": True,
                    "revocable": True,
                }
            ],
        }

        result = kakao.get_talk_message_consent("access-token")

        self.assertEqual(123456789, result["id"])
        get_json.assert_called_once_with(
            kakao.KAKAO_SCOPES_ENDPOINT,
            query={"scopes": '["talk_message"]'},
            headers={"Authorization": "Bearer access-token"},
        )

    @patch.object(kakao, "_get_json")
    def test_rejects_login_without_talk_message_consent(self, get_json):
        get_json.return_value = {
            "id": 123456789,
            "scopes": [
                {
                    "id": "talk_message",
                    "using": True,
                    "agreed": False,
                }
            ],
        }

        with self.assertRaisesRegex(kakao.KakaoApiError, "talk_message"):
            kakao.get_talk_message_consent("access-token")

    @patch.object(kakao, "save_token_data")
    @patch.object(kakao, "_post_form")
    @patch.object(kakao, "load_token_data")
    def test_refreshes_an_expired_access_token(
        self,
        load_token_data,
        post_form,
        save_token_data,
    ):
        old_tokens = {
            "access_token": "expired-token",
            "refresh_token": "refresh-token",
            "access_token_expires_at": 1,
        }
        load_token_data.return_value = old_tokens
        post_form.return_value = {"access_token": "new-access-token", "expires_in": 21600}
        save_token_data.return_value = {
            **old_tokens,
            "access_token": "new-access-token",
        }

        with patch.dict(
            os.environ,
            {"KAKAO_REST_API_KEY": "rest-api-key"},
            clear=True,
        ):
            access_token = kakao.get_valid_access_token()

        self.assertEqual("new-access-token", access_token)
        save_token_data.assert_called_once_with(
            {"access_token": "new-access-token", "expires_in": 21600},
            previous=old_tokens,
        )

    def test_persists_tokens_in_private_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            token_file = Path(temp_dir) / "tokens.json"
            with patch.dict(
                os.environ,
                {"KAKAO_TOKEN_FILE": str(token_file)},
                clear=True,
            ):
                kakao.save_token_data(
                    {
                        "access_token": "access-token",
                        "refresh_token": "refresh-token",
                        "expires_in": 21600,
                    }
                )
                saved = kakao.load_token_data()

            self.assertEqual("access-token", saved["access_token"])
            self.assertEqual("refresh-token", saved["refresh_token"])
            self.assertEqual(0o600, token_file.stat().st_mode & 0o777)


if __name__ == "__main__":
    unittest.main()
