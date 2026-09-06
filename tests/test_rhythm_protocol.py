import tempfile
import unittest
from pathlib import Path

from video_editor import parse_script
from viral_validator import ViralScriptValidator


def rhythm_script(with_gap=False):
    starts = [0, 10, 20, 30, 40, 50]
    if with_gap:
        starts[3] = 34
    emotions = [
        "平静1→疑惑3", "疑惑3→好奇4", "好奇4→理解3",
        "放心2→警觉5", "警觉5→确信4", "确信4→回味2",
    ]
    visuals = ["人物近景", "小区全景", "数据卡", "问题特写", "户型图", "人物中景"]
    sounds = ["裸声", "BGM淡入", "BGM稳定", "BGM抽低", "BGM恢复", "BGM淡出"]
    speeds = ["快→正常", "正常", "稍快", "正常→慢", "慢→正常", "正常"]
    blocks = ["---", "duration_sec: 60", "---", "# 测试脚本"]
    for index, start in enumerate(starts):
        end = (index + 1) * 10
        blocks.extend([
            f"## 节奏拍 {index + 1:02d}｜测试",
            f"[时间: 00:{start:02d}-00:{end:02d}]",
            f"[叙事动作: 推进第{index + 1}个信息]",
            f"[情绪: {emotions[index]}]",
            f"[画面: {visuals[index]}]",
            f"[语速: {speeds[index]}]",
            "[停顿: 末句后0.5秒]",
            f"[声音: {sounds[index]}]",
            "[转场理由: 用下一条证据继续推进]",
            f"[主强调: {'台词' if index % 2 == 0 else '画面'}]",
            f"这是第{index + 1}拍真正需要说出口的测试台词。",
        ])
    return "\n".join(blocks)


class RhythmProtocolTests(unittest.TestCase):
    def setUp(self):
        self.validator = ViralScriptValidator()

    def test_complete_timeline_gets_full_rhythm_score(self):
        script = rhythm_script()
        beats = self.validator._extract_rhythm_beats(script)
        score, issues = self.validator._score_rhythm(beats, 60)
        self.assertEqual(3, score)
        self.assertEqual([], issues)

    def test_timeline_gap_loses_timing_point(self):
        script = rhythm_script(with_gap=True)
        beats = self.validator._extract_rhythm_beats(script)
        score, issues = self.validator._score_rhythm(beats, 60)
        self.assertEqual(2, score)
        self.assertTrue(any("时间轴" in issue for issue in issues))

    def test_legacy_sections_no_longer_fake_a_rhythm_score(self):
        beats = self.validator._extract_rhythm_beats(
            "## 开场\n一句话\n## 冲突\n一句话\n## 解决\n一句话\n## 金句\n一句话"
        )
        score, issues = self.validator._score_rhythm(beats, 60)
        self.assertEqual(0, score)
        self.assertTrue(any("章节齐全不等于节奏成立" in issue for issue in issues))

    def test_editor_parser_removes_production_annotations(self):
        script = rhythm_script()
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "script.txt"
            path.write_text(script, encoding="utf-8")
            segments = parse_script(str(path))
        self.assertEqual(6, len(segments))
        self.assertEqual("00:00-00:10", segments[0]["timecode"])
        self.assertEqual("平静1→疑惑3", segments[0]["emotion"])
        self.assertEqual("台词", segments[0]["primary_emphasis"])
        self.assertNotIn("[情绪:", segments[0]["text"])


if __name__ == "__main__":
    unittest.main()
