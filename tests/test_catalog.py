import unittest

from catalog import _normalize_evo_endpoint


class EvoEndpointConfigurationTests(unittest.TestCase):
    def test_query_string_is_removed_because_service_controls_pagination(self):
        endpoint = _normalize_evo_endpoint(
            "https://evo-integracao-api.w12app.com.br/api/v2/members?showMemberships=true"
        )

        self.assertEqual(endpoint, "https://evo-integracao-api.w12app.com.br/api/v2/members")

    def test_non_https_endpoint_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            _normalize_evo_endpoint("http://example.com/api/v2/members")


if __name__ == "__main__":
    unittest.main()
