import importlib
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient


class MainFrontendMountTests(unittest.TestCase):
    def test_root_redirects_to_docs_when_backend_frontend_serving_is_disabled(self) -> None:
        from app.core import config as config_module
        from app import main as main_module

        with patch.dict(os.environ, {"BACKEND_SERVE_FRONTEND": "false"}, clear=False):
            reloaded_config = importlib.reload(config_module)
            try:
                reloaded_main = importlib.reload(main_module)
                response = TestClient(reloaded_main.app).get(
                    "/", follow_redirects=False
                )

                self.assertEqual(response.status_code, 307)
                self.assertEqual(response.headers["location"], "/docs")
                self.assertFalse(reloaded_config.settings.BACKEND_SERVE_FRONTEND)
            finally:
                importlib.reload(config_module)
                importlib.reload(main_module)
