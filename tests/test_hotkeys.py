"""Desk tests for registering hotkeys only while the game is in front. A fake user32 stands in for Windows."""

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication

from assist.hotkeys import HotkeyManager

app = QApplication.instance() or QApplication([])


class FakeUser32:
    def __init__(self):
        self.registered = set()
        self.taken = set()  # virtual keys another program holds

    def RegisterHotKey(self, hwnd, hotkey_id, mods, vk):
        if vk in self.taken:
            return False
        self.registered.add(hotkey_id)
        return True

    def UnregisterHotKey(self, hwnd, hotkey_id):
        self.registered.discard(hotkey_id)
        return True


class HotkeyGateTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.settings = QSettings(str(Path(folder.name) / "settings.ini"), QSettings.Format.IniFormat)
        self.front = False
        self.problems = []

    def manager(self, gated=True):
        manager = HotkeyManager([("next_point", "Next point", lambda: None),
                                 ("mark_deposit", "Remote Mark Deposit", lambda: None)], self.settings,
                                in_front=(lambda: self.front) if gated else None, on_problem=self.problems.append)
        manager._timer.stop()  # the tests call _sync() themselves
        self.addCleanup(app.removeNativeEventFilter, manager)
        manager._user32 = self.fake = FakeUser32()
        manager.set_binding("next_point", QKeySequence("F7"))
        manager.set_binding("mark_deposit", QKeySequence("F8"))
        return manager

    def test_only_registered_while_the_game_is_in_front(self):
        manager = self.manager()
        self.assertEqual(manager.apply(), [])
        self.assertEqual(self.fake.registered, set())  # checked, then released: the game is behind
        self.front = True
        manager._sync()
        self.assertEqual(self.fake.registered, {1, 2})
        self.front = False
        manager._sync()
        self.assertEqual(self.fake.registered, set())

    def test_problem_found_at_apply_even_with_the_game_behind(self):
        manager = self.manager()
        self.fake.taken.add(0x77)  # F8
        problems = manager.apply()
        self.assertEqual(len(problems), 1)
        self.assertIn("taken by another program", problems[0])
        self.front = True
        manager._sync()
        self.assertEqual(self.problems, [])  # already reported by apply()

    def test_new_problem_on_coming_to_front_reported_once(self):
        manager = self.manager()
        manager.apply()
        self.fake.taken.add(0x77)
        for _ in range(2):
            self.front = True
            manager._sync()
            self.front = False
            manager._sync()
        self.assertEqual(len(self.problems), 1)

    def test_released_while_rebinding(self):
        manager = self.manager()
        manager.apply()
        self.front = True
        manager._sync()
        manager.release()
        manager._sync()
        self.assertEqual(self.fake.registered, set())
        manager.apply()
        self.assertEqual(self.fake.registered, {1, 2})

    def test_replay_keeps_them_registered(self):
        manager = self.manager(gated=False)
        manager.apply()
        manager._sync()
        self.assertEqual(self.fake.registered, {1, 2})


if __name__ == "__main__":
    unittest.main()
