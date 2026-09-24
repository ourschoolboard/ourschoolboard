import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.tools.source_text import extract


class SourceTextTests(unittest.TestCase):
    def test_extracts_powerpoint_slide_text(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "presentation.pptx"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr(
                    "ppt/slides/slide1.xml",
                    '<p:sld xmlns:p="p" xmlns:a="a"><a:t>Budget update</a:t></p:sld>',
                )
            self.assertEqual(extract(path), "Budget update\n")

    def test_rejects_unknown_zip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "unknown.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("data.txt", "hello")
            with self.assertRaisesRegex(RuntimeError, "unsupported ZIP"):
                extract(path)


if __name__ == "__main__":
    unittest.main()
