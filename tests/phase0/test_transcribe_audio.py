"""本地音频转写结果格式测试。"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[2] / "scripts" / "phase0" / "transcribe_audio.py"
SPEC = importlib.util.spec_from_file_location("transcribe_audio", MODULE_PATH)
assert SPEC and SPEC.loader
transcribe_audio = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = transcribe_audio
SPEC.loader.exec_module(transcribe_audio)


class TranscribeAudioTest(unittest.TestCase):
    """验证时间定位与未校对标记不会丢失。"""

    def test_format_timestamp(self) -> None:
        self.assertEqual(transcribe_audio.format_timestamp(62.345), "00:01:02.345")

    def test_render_markdown_marks_unverified_transcript(self) -> None:
        result = {"segments": [{"start": 1.0, "end": 2.5, "text": "测试原话"}]}
        markdown = transcribe_audio.render_markdown(result, "local-model", "abc")
        self.assertIn("未经人工校对", markdown)
        self.assertIn("00:00:01.000 - 00:00:02.500", markdown)
        self.assertIn("测试原话", markdown)


if __name__ == "__main__":
    unittest.main()
