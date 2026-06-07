from datetime import datetime, timezone
import unittest
from unittest.mock import Mock, patch

from evo_service import (
    EvoApiConfig,
    EvoConnectionError,
    EvoLoadTimeoutError,
    EvoRateLimitError,
    build_membership_analytics,
    fetch_members,
)


class MembersApiTests(unittest.TestCase):
    @patch("evo_service.requests.Session")
    def test_members_request_includes_memberships_for_correct_status(self, session_factory):
        response = Mock(status_code=200)
        response.json.return_value = [{"idMember": 1, "memberships": []}]
        session_factory.return_value.__enter__.return_value.get.return_value = response

        records = fetch_members(EvoApiConfig(username="server-user", password="server-password"))

        self.assertEqual(records, [{"idMember": 1, "memberships": []}])
        session_factory.return_value.__enter__.return_value.get.assert_called_once_with(
            "https://evo-integracao-api.w12app.com.br/api/v2/members",
            auth=("server-user", "server-password"),
            params={"showMemberships": "true", "take": 50, "skip": 0},
            timeout=20,
        )

    @patch("evo_service.time.sleep")
    @patch("evo_service.requests.Session")
    def test_paginated_requests_are_throttled_below_minute_limit(self, session_factory, sleep):
        first_page = Mock(status_code=200)
        first_page.json.return_value = [{"idMember": member_id} for member_id in range(50)]
        last_page = Mock(status_code=200)
        last_page.json.return_value = []
        session_factory.return_value.__enter__.return_value.get.side_effect = [first_page, last_page]

        progress = []
        records = fetch_members(
            EvoApiConfig(username="user", password="pass"),
            lambda pages, records, elapsed: progress.append((pages, records)),
        )

        self.assertEqual(len(records), 50)
        sleep.assert_called_once_with(1.55)
        self.assertEqual(progress, [(1, 50), (2, 50)])

    @patch("evo_service.requests.Session")
    def test_page_callback_receives_each_loaded_batch(self, session_factory):
        response = Mock(status_code=200)
        response.json.return_value = [{"idMember": 1, "memberships": []}]
        session_factory.return_value.__enter__.return_value.get.return_value = response
        batches = []

        records = fetch_members(
            EvoApiConfig(username="user", password="pass"),
            page_callback=lambda page, batch, elapsed: batches.append((page, batch)),
        )

        self.assertEqual(records, [{"idMember": 1, "memberships": []}])
        self.assertEqual(batches, [(1, [{"idMember": 1, "memberships": []}])])

    @patch("evo_service.time.sleep")
    @patch("evo_service.requests.Session")
    def test_rate_limit_retries_after_retry_after_header(self, session_factory, sleep):
        limited = Mock(status_code=429, headers={"Retry-After": "2"})
        success = Mock(status_code=200)
        success.json.return_value = []
        session_factory.return_value.__enter__.return_value.get.side_effect = [limited, success]

        records = fetch_members(EvoApiConfig(username="user", password="pass"))

        self.assertEqual(records, [])
        sleep.assert_called_once_with(2.0)

    @patch("evo_service.time.sleep")
    @patch("evo_service.requests.Session")
    def test_persistent_rate_limit_has_actionable_message(self, session_factory, sleep):
        limited = Mock(status_code=429, headers={})
        session_factory.return_value.__enter__.return_value.get.side_effect = [limited, limited]

        with self.assertRaisesRegex(EvoRateLimitError, "aguarde 1 hora"):
            fetch_members(EvoApiConfig(username="user", password="pass"))

        sleep.assert_called_once_with(60.0)

    @patch("evo_service.requests.Session")
    def test_load_is_interrupted_after_configured_deadline(self, session_factory):
        with self.assertRaisesRegex(EvoLoadTimeoutError, "evitar espera indefinida"):
            fetch_members(
                EvoApiConfig(username="user", password="pass", max_load_seconds=-1),
            )

        session_factory.return_value.__enter__.return_value.get.assert_not_called()

    @patch("evo_service.requests.Session")
    def test_windows_socket_permission_error_has_actionable_message(self, session_factory):
        session_factory.return_value.__enter__.return_value.get.side_effect = (
            __import__("requests").ConnectionError("Failed to establish connection: [WinError 10013]")
        )

        with self.assertRaisesRegex(EvoConnectionError, "nao possui permissao"):
            fetch_members(EvoApiConfig(username="user", password="pass"))


class MembershipAnalyticsTests(unittest.TestCase):
    def test_member_profiles_are_reduced_to_safe_operational_aggregates(self):
        analytics = build_membership_analytics(
            [
                {
                    "idMember": 6018,
                    "firstName": "Pessoa Sensivel",
                    "document": "00000000000",
                    "address": "Endereco privado",
                    "photoUrl": "https://private.example/photo.jpg",
                    "branchName": "BE.MOVE Cidade Jardim",
                    "status": "Active",
                    "accessBlocked": False,
                    "lastAccessDate": "2026-05-25T08:00:00",
                    "memberships": [
                        {
                            "idMemberMembership": 10,
                            "idMemberMembershipRenewed": None,
                            "name": "CLUB 16",
                            "startDate": "2026-04-01T00:00:00",
                            "endDate": "2026-04-30T00:00:00",
                            "membershipStatus": "expired",
                            "contractPrinting": "https://private.example/contract.jpg",
                        },
                        {
                            "idMemberMembership": 11,
                            "idMemberMembershipRenewed": 10,
                            "name": "CLUB 16",
                            "startDate": "2026-05-01T00:00:00",
                            "endDate": "2026-06-30T00:00:00",
                            "membershipStatus": "active",
                            "valueNextMonth": 279.0,
                            "signedTerms": False,
                        },
                    ],
                },
                {
                    "idMember": 9024,
                    "firstName": "Outro Nome Privado",
                    "document": "11111111111",
                    "branchName": "BE.MOVE Cidade Jardim",
                    "status": "Inactive",
                    "accessBlocked": True,
                    "lastAccessDate": "2026-02-01T08:00:00",
                    "memberships": [
                        {
                            "idMemberMembership": 20,
                            "idMemberMembershipRenewed": None,
                            "name": "CLUB 20",
                            "startDate": "2026-05-01T00:00:00",
                            "endDate": "2026-06-30T00:00:00",
                            "cancelDate": "2026-05-20T10:00:00",
                            "membershipStatus": "canceled",
                        }
                    ],
                },
            ],
            fetched_at=datetime(2026, 5, 31, tzinfo=timezone.utc),
        )

        may = analytics.monthly[analytics.monthly["periodo"] == "MAI/26"].iloc[0]
        self.assertEqual(may["novos_clientes"], 1)
        self.assertEqual(may["adesoes"], 1)
        self.assertEqual(may["renovacoes"], 1)
        self.assertEqual(may["cancelamentos"], 1)
        self.assertEqual(may["base_ativa"], 1)
        self.assertEqual(analytics.totals["membros_ativos"], 1)
        self.assertEqual(analytics.totals["membros_inativos"], 1)
        self.assertEqual(analytics.totals["matriculas_ativas"], 1)
        self.assertEqual(analytics.totals["termos_pendentes"], 1)
        self.assertEqual(analytics.totals["valor_proximo_mes"], 279.0)
        self.assertEqual(analytics.plans.iloc[0]["plano"], "CLUB 16")

        rendered_aggregate = " ".join(
            frame.to_string()
            for frame in [
                analytics.monthly,
                analytics.plans,
                analytics.member_statuses,
                analytics.contract_statuses,
                analytics.access_health,
                analytics.branches,
            ]
        )
        self.assertNotIn("Pessoa Sensivel", rendered_aggregate)
        self.assertNotIn("Outro Nome Privado", rendered_aggregate)
        self.assertNotIn("00000000000", rendered_aggregate)
        self.assertNotIn("Endereco privado", rendered_aggregate)
        self.assertNotIn("private.example", rendered_aggregate)

    def test_access_risk_counts_only_active_members_without_recent_access(self):
        analytics = build_membership_analytics(
            [
                {
                    "idMember": 1,
                    "status": "Active",
                    "lastAccessDate": "2026-04-01T00:00:00",
                    "memberships": [],
                },
                {
                    "idMember": 2,
                    "status": "Active",
                    "lastAccessDate": None,
                    "memberships": [],
                },
                {
                    "idMember": 3,
                    "status": "Inactive",
                    "lastAccessDate": None,
                    "memberships": [],
                },
            ],
            fetched_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        )

        self.assertEqual(analytics.totals["risco_evasao"], 2)


if __name__ == "__main__":
    unittest.main()
