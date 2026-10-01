import subprocess
import sys
import unittest
from pathlib import Path


class CliTest(unittest.TestCase):
    def test_about(self):
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parents[1] / "harbor.py"), "--about"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("Harbor", result.stdout)
