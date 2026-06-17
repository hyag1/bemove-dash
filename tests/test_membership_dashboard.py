from datetime import date, datetime, timezone
import unittest

import pandas as pd

from dashboards.membership import _active_members_for_period, _default_period, _filter_by_date, _selector_date_max


class MembershipDashboardTests(unittest.TestCase):
    def test_active_members_uses_latest_month_in_selected_period(self):
        monthly = pd.DataFrame(
            [
                {"periodo_date": pd.Timestamp("2026-04-01"), "base_ativa": 380},
                {"periodo_date": pd.Timestamp("2026-05-01"), "base_ativa": 395},
                {"periodo_date": pd.Timestamp("2026-06-01"), "base_ativa": 410},
            ]
        )

        selected = _filter_by_date(monthly, date(2026, 4, 1), date(2026, 5, 1))

        self.assertEqual(_active_members_for_period(selected), 395)

    def test_active_members_returns_zero_without_monthly_rows(self):
        monthly = pd.DataFrame(columns=["periodo_date", "base_ativa"])

        self.assertEqual(_active_members_for_period(monthly), 0)

    def test_selector_allows_current_sync_day_for_latest_month(self):
        latest_period_start = date(2026, 6, 1)

        self.assertEqual(
            _selector_date_max(latest_period_start, datetime(2026, 6, 17, 21, 24, tzinfo=timezone.utc)),
            date(2026, 6, 17),
        )

    def test_default_period_uses_selector_end_date(self):
        default_start, default_end = _default_period(
            date(2026, 4, 1),
            date(2026, 6, 1),
            date(2026, 6, 17),
            today=date(2026, 6, 17),
        )

        self.assertEqual(default_start, date(2026, 6, 1))
        self.assertEqual(default_end, date(2026, 6, 17))


if __name__ == "__main__":
    unittest.main()
