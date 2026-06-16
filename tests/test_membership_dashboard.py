from datetime import date
import unittest

import pandas as pd

from dashboards.membership import _active_members_for_period, _filter_by_date


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


if __name__ == "__main__":
    unittest.main()
