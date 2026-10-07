import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from orbit.config import create_api_client
from orbit.settings import Settings


class ApiProviderTests(unittest.TestCase):
    def test_custom_https_base_url_is_used_by_client(self):
        client = create_api_client("test-key", "https://api.example.test/v1")
        self.assertEqual(str(client.base_url), "https://api.example.test/v1/")
        client.close()

    def test_local_http_base_url_is_allowed(self):
        client = create_api_client("test-key", "http://localhost:11434/v1")
        self.assertEqual(str(client.base_url), "http://localhost:11434/v1/")
        client.close()

    def test_remote_http_base_url_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            create_api_client("test-key", "http://api.example.test/v1")

    def test_base_url_cannot_embed_credentials_or_query_parameters(self):
        for url in (
            "https://user:password@api.example.test/v1",
            "https://api.example.test/v1?token=secret",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                create_api_client("test-key", url)

    def test_custom_base_url_is_persisted_as_non_secret_setting(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            settings = Settings(api_base_url="https://api.example.test/v1")
            with patch("orbit.settings.settings_path", return_value=path):
                settings.save()
                reloaded = Settings.load()
            self.assertEqual(reloaded.api_base_url, "https://api.example.test/v1")
            self.assertEqual(
                reloaded.to_model_settings().api_base_url,
                "https://api.example.test/v1",
            )
            self.assertNotIn("api_key", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
