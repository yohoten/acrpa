# -*- coding: utf-8 -*-
"""九页设置窗口：导航、搜索、保存冲刷与本页重置回归。"""
import os
import sys
import tkinter
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import state
from ui import theme as ui_theme
import ui.settings.window as sw
from ui.settings.navigation import PAGE_ORDER, search_records


class SettingsNavigationTests(unittest.TestCase):
    def setUp(self):
        self.root = tkinter.Tk()
        self.root.withdraw()
        import utils
        utils.init_fonts(self.root)
        state._sched_enabled_var = None
        self._old_retry = state.RETRY_MAX
        self._old_check_update = state.CHECK_UPDATE
        self._old_reduce_motion = state.REDUCE_MOTION
        self.save_patch = mock.patch.object(state, "save_config", return_value=None)
        self.save_mock = self.save_patch.start()
        sw.init_ctx(root_win=self.root, colors=ui_theme.colors(False),
                    app_root=ROOT)
        sw.open_settings_window()
        self.root.update()

    def tearDown(self):
        state.RETRY_MAX = self._old_retry
        state.CHECK_UPDATE = self._old_check_update
        state.REDUCE_MOTION = self._old_reduce_motion
        try:
            sw._close(force=True)
        except Exception:
            pass
        self.save_patch.stop()
        self.root.destroy()

    def test_nine_pages_and_card_state_survive_switch(self):
        self.assertEqual(9, len(PAGE_ORDER))
        self.assertEqual(9, len(sw._page_hosts))
        self.assertEqual(12, len(sw._nav_cards))
        self.assertEqual(["general"], [key for key, host in sw._page_hosts.items()
                                       if host.winfo_manager()])
        retry = sw._handles_map["exec"]["retry_var"]
        retry.set("7")
        sw._on_nav_click("browser")
        sw._on_nav_click("execution")
        self.root.update()
        self.assertEqual("7", retry.get())
        self.assertEqual("execution", sw._active_page)
        self.assertEqual(["execution"], [key for key, host in sw._page_hosts.items()
                                         if host.winfo_manager()])

    def test_global_search_locates_advanced_section(self):
        sw._navigation.query_var.set("OCR")
        self.root.update()
        self.assertTrue(sw._navigation.matches)
        record = next(item for item in sw._navigation.matches
                      if item["page"] == "ai_ocr")
        sw._locate_search_result(record)
        self.root.update()
        self.assertEqual("ai_ocr", sw._active_page)
        sw._navigation.clear_search()
        self.assertEqual("", sw._navigation.query_var.get())
        self.assertFalse(sw._navigation.results.winfo_manager())

    def test_close_flushes_pending_save(self):
        retry = sw._handles_map["exec"]["retry_var"]
        retry.set("9")
        self.assertIn("exec", sw._debounce_after)
        sw._close()
        self.assertIsNone(sw._win)
        self.assertEqual(9, state.RETRY_MAX)
        self.assertFalse(sw._debounce_after)
        self.assertGreaterEqual(self.save_mock.call_count, 1)

    def test_save_failure_keeps_window_open(self):
        retry = sw._handles_map["exec"]["retry_var"]
        self.save_patch.stop()
        with mock.patch.object(state, "save_config", side_effect=OSError("disk full")), \
                mock.patch.object(sw.messagebox, "askyesnocancel", return_value=None) as ask:
            retry.set("8")
            sw._close()
            self.assertIsNotNone(sw._win)
            self.assertIn("exec", sw._last_error)
            ask.assert_called_once()
        self.save_patch = mock.patch.object(state, "save_config", return_value=None)
        self.save_mock = self.save_patch.start()
        sw._close(force=True)

    def test_about_reset_only_changes_declared_field(self):
        state.RETRY_MAX = 7
        state.CHECK_UPDATE = False
        sw._handles_map["exec"]["retry_var"].set("7")
        sw._handles_map["exec"]["check_update_var"].set(False)
        self.assertTrue(sw._reset_current_page("about"))
        self.assertTrue(state.CHECK_UPDATE)
        self.assertEqual(7, state.RETRY_MAX)
        self.assertFalse(sw._debounce_after)

    def test_dark_theme_and_reduced_motion_refresh(self):
        sw._on_nav_click("recording")
        state.REDUCE_MOTION = True
        sw.on_theme_publish(True, ui_theme.colors(True), ui_theme.colors(False))
        self.root.update()
        self.assertEqual("recording", sw._active_page)
        self.assertIs(sw._navigation.C, sw.C)
        for _module, _handles, ctx in sw._apply_map.values():
            self.assertIs(ctx.C, sw.C)


class PureSearchTests(unittest.TestCase):
    def test_case_insensitive_multiword_query(self):
        records = [{"search": "ai 与 ocr ocr_backend 后端", "title": "OCR 后端"}]
        self.assertEqual(records, search_records(records, "ocr backend"))
        self.assertEqual([], search_records(records, "不存在"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
