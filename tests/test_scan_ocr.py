import importlib.util
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('scan_under_test',ROOT/'plugins/official/document-convert/scan_ocr.py')
scan=importlib.util.module_from_spec(spec);spec.loader.exec_module(scan)

class ScanTests(unittest.TestCase):
    def test_page_selection_preserves_physical_page_numbers(self):
        self.assertEqual(scan.select_pages('16,2-4,3',423),[2,3,4,16])
        self.assertEqual(scan.select_pages('all',3),[1,2,3])
    def test_invalid_ranges_rejected_before_ocr(self):
        for value in ['0','4-2','424','-1','1,','1-999999999','1;exit',None]:
            with self.subTest(value=value),self.assertRaises(ValueError):scan.select_pages(value,423)
    def test_math_comparison_keeps_semantic_differences(self):
        self.assertNotEqual(scan.normalized_math('p'),scan.normalized_math('P'))
        self.assertNotEqual(scan.normalized_math('v_{ix}'),scan.normalized_math('v_i'))
        self.assertEqual(scan.normalized_math(r'\[ x + y \]'),scan.normalized_math('x+y'))

if __name__=='__main__':unittest.main()
