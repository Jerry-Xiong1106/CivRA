"""Regression tests for layout corruption and silent article loss."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from parse_legal_pdf import clean_text, extract_lines, parse
from validate_provisions import validate


class LegalParserTests(unittest.TestCase):
    def run_parse(self, lines):
        with patch("parse_legal_pdf.extract_lines", return_value=lines):
            return parse(Path("law.pdf"), {"title": "测试法律", "publication_date": "2020-01-01"})

    def test_heading_reference_is_body_not_new_book(self):
        rows = self.run_parse([(1, "第三编 合同"), (1, "第一分编 通则"), (1, "第一章 一般规定"), (1, "第一条 合同效力依照本法"), (1, "第一编第六章第三节的规定确定。"), (1, "第二条 后续正文。")])
        self.assertIn("第一编第六章第三节的规定", rows[0]["text"])
        self.assertEqual(rows[1]["hierarchy_path"][0], "第三编 合同")

    def test_multiline_heading_and_supplementary_provisions(self):
        rows = self.run_parse([(1, "第一章 一般规定"), (1, "第一条 正文。"), (1, "第二节 认定公民无民事行为能力、"), (1, "限制民事行为能力案件"), (2, "第二条 正文。"), (2, "附 则"), (2, "第三条 施行日期。")])
        self.assertEqual(rows[1]["hierarchy_path"][-1], "第二节 认定公民无民事行为能力、限制民事行为能力案件")
        self.assertNotIn("限制民事行为能力案件", rows[0]["text"])
        self.assertEqual(rows[2]["hierarchy_path"], ["附则"])

    def test_cross_page_reference_retained(self):
        rows = self.run_parse([(1, "第一章 总则"), (1, "第一条 规定。"), (1, "第二条 依照"), (2, "第一条、有关规定执行。"), (2, "第三条 正文。")])
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1]["text"], "依照第一条、有关规定执行。")
        self.assertEqual((rows[1]["page_start"], rows[1]["page_end"]), (1, 2))

    def test_numbering_gap_fails_instead_of_swallowing(self):
        with self.assertRaisesRegex(ValueError, "expected article 2"):
            self.run_parse([(1, "第一章 总则"), (1, "第一条 正文。"), (1, "第三条 正文。")])

    def test_whitespace_and_latin_words(self):
        self.assertEqual(clean_text("预防和减\n少事故。 ISO standard"), "预防和减少事故。ISO standard")

    def test_layout_title_fragments_merge_and_edge_furniture_removed(self):
        def line(text, bbox):
            return {"bbox": bbox, "spans": [{"text": text}]}
        page = MagicMock()
        page.rect.height = 740
        page.get_text.return_value = {"blocks": [{"lines": [
            line("第二章 自", (100, 22, 160, 37)),
            line("然", (175, 22, 188, 35)),
            line("人", (203, 22, 216, 35)),
            line("第一条 正文。", (10, 100, 180, 115)),
            line("—", (400, 722, 410, 733)),
            line("９", (420, 722, 430, 733)),
            line("全国人民代表大会常务委员会公报２０２３·６", (10, 11, 200, 22)),
        ]}]}
        document = MagicMock()
        document.__enter__.return_value = document
        document.__iter__.side_effect = lambda: iter([page])
        document.__len__.return_value = 1
        with patch("parse_legal_pdf.pymupdf.open", return_value=document):
            lines = extract_lines(Path("law.pdf"))
        self.assertEqual(lines, [(1, "第二章 自然人"), (1, "第一条 正文。")])

    def test_validator_rejects_empty_dataset(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "empty.jsonl"
            path.write_text("", encoding="utf-8")
            self.assertEqual(validate(path), ["empty dataset"])

    def test_validator_rejects_hash_page_and_missing_source(self):
        row = self.run_parse([(1, "第一章 总则"), (1, "第一条 正文。")])[0]
        row.update(page_start=0, content_hash="wrong")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.jsonl"
            path.write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")
            errors = validate(path, Path(directory))
            self.assertTrue(any("invalid page range" in e for e in errors))
            self.assertTrue(any("content_hash mismatch" in e for e in errors))
            self.assertTrue(any("source PDF missing" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
