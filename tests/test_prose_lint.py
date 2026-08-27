import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from prose_lint import check_participles  # noqa: E402


class ParticipleTests(unittest.TestCase):
    def test_configured_patterns_ignore_and_limits(self):
        paras = [SimpleNamespace(text="Проверенная, рассчитанная и привязанная величина.")]
        sents = ["one", "two", "three", "four"]
        cfg = {
            "participle_patterns": [r"(?:енн|анн)(?:ая)$"],
            "participle_ignore": [r"^проверенн"],
            "participles_per_100": 40,
            "participles_per_para": 2,
        }
        found = []

        check_participles(paras, sents, cfg,
                          lambda check, headline, excerpts:
                          found.append((check, headline, excerpts)))

        self.assertIn("2 matching forms / 4 sentences = 50.0 per 100", found[0][1])
        self.assertEqual([item[0] for item in found],
                         ["participles", "participles_limit", "participles_limit"])
        self.assertIn("рассчитанная, привязанная", found[2][1])

    def test_no_patterns_disables_the_check(self):
        found = []
        check_participles([], [], {
            "participle_patterns": [],
            "participle_ignore": [],
            "participles_per_100": None,
            "participles_per_para": None,
        }, lambda *args: found.append(args))
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
