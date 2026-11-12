"""Paths shown to the player don't contain the Windows user name."""

import unittest

from assist.paths import shown

HOME = r"C:\Users\Jane Doe"


class ShownPathTests(unittest.TestCase):
    def test_home_folder_replaced(self):
        self.assertEqual(shown(HOME + r"\OneDrive\Documents\ASSIST", HOME), r"%USERPROFILE%\OneDrive\Documents\ASSIST")
        self.assertEqual(shown(HOME, HOME), "%USERPROFILE%")

    def test_case_doesnt_matter(self):
        self.assertEqual(shown(r"c:\users\jane doe\Saved Games", HOME), r"%USERPROFILE%\Saved Games")

    def test_other_paths_unchanged(self):
        self.assertEqual(shown(r"D:\Claude\ASSIST\recordings", HOME), r"D:\Claude\ASSIST\recordings")
        self.assertEqual(shown(r"C:\Users\Jane Doe2\x", HOME), r"C:\Users\Jane Doe2\x")  # a different user


if __name__ == "__main__":
    unittest.main()
