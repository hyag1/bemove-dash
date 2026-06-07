import unittest

from auth import authenticate, hash_password, load_users, verify_password


class AuthenticationTests(unittest.TestCase):
    def test_hash_verifies_only_the_original_password(self):
        encoded = hash_password("correct horse battery staple", salt=b"fixed-test-salt")

        self.assertTrue(verify_password("correct horse battery staple", encoded))
        self.assertFalse(verify_password("wrong", encoded))

    def test_user_permissions_are_loaded_from_server_config(self):
        encoded = hash_password("secret", salt=b"fixed-test-salt")
        users = load_users(
            {
                "auth": {
                    "users": [
                        {
                            "username": " Analyst ",
                            "display_name": "Analista",
                            "password_hash": encoded,
                            "clients": ["bemove"],
                        }
                    ]
                }
            }
        )

        user = authenticate(users, "analyst", "secret")

        self.assertIsNotNone(user)
        self.assertEqual(user.client_ids, frozenset({"bemove"}))
        self.assertIsNone(authenticate(users, "analyst", "invalid"))

    def test_role_and_dashboard_permissions_are_loaded_from_server_config(self):
        encoded = hash_password("secret", salt=b"fixed-test-salt")
        users = load_users(
            {
                "auth": {
                    "users": [
                        {
                            "username": "Admin",
                            "display_name": "Administrador",
                            "password_hash": encoded,
                            "role": "admin",
                            "clients": ["bemove"],
                            "dashboards": {"bemove": ["membership-flow"]},
                        }
                    ]
                }
            }
        )

        user = authenticate(users, "admin", "secret")

        self.assertIsNotNone(user)
        self.assertTrue(user.is_admin)
        self.assertEqual(user.dashboard_ids, frozenset({"bemove:membership-flow"}))


if __name__ == "__main__":
    unittest.main()
