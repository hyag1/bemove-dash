import unittest

from supabase_store import SupabaseConfig


class SupabaseConfigTests(unittest.TestCase):
    def test_config_is_loaded_from_supabase_section(self):
        config = SupabaseConfig.from_secrets(
            {
                "supabase": {
                    "project_url": "https://example.supabase.co",
                    "publishable_key": "sb_publishable_example",
                    "database_url": "postgresql://postgres:pass@example/postgres",
                }
            }
        )

        self.assertIsNotNone(config)
        self.assertEqual(config.project_url, "https://example.supabase.co")
        self.assertEqual(config.publishable_key, "sb_publishable_example")
        self.assertEqual(config.database_url, "postgresql://postgres:pass@example/postgres")

    def test_missing_database_url_disables_supabase(self):
        config = SupabaseConfig.from_secrets({"supabase": {"project_url": "https://example.supabase.co"}})

        self.assertIsNone(config)


if __name__ == "__main__":
    unittest.main()
