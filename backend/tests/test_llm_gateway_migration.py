from pathlib import Path
import unittest


class LLMGatewayMigrationTests(unittest.TestCase):
    def test_chat_llm_calls_are_centralized_in_gateway(self):
        backend_root = Path(__file__).resolve().parents[1]
        app_root = backend_root / "app"
        allowed = {
            app_root / "services" / "llm_gateway_service.py",
        }
        forbidden_patterns = (
            "chat.completions.create",
            "embeddings.create",
            "from openai import AsyncOpenAI",
            "import openai",
        )
        offenders: list[str] = []

        for path in app_root.rglob("*.py"):
            if path in allowed:
                continue
            text = path.read_text(encoding="utf-8")
            for pattern in forbidden_patterns:
                if pattern in text:
                    offenders.append(f"{path.relative_to(backend_root)}: {pattern}")

        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
