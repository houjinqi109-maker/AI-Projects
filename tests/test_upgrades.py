"""检查升级功能与旧账本、历史汇率及原有界面的配合。"""
import json
import tempfile
import tkinter as tk
import unittest
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile
from xml.etree import ElementTree as ET

from ledger_core import LedgerError, LedgerStore
from travel_ledger import TravelLedgerApp
from travel_reports import fetch_rates, export_excel


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "ledger.json"
        self.store = LedgerStore(self.path)
        for day, amount in [("2025-12-31", "10"), ("2026-01-01", "20"), ("2026-01-02", "30")]:
            self.store.add_record(day, "=车票", "交通", amount, "CNY", "我", ["我"])

    def test_periods_and_excel(self):
        self.assertEqual(self.store.period_totals("月"), {"2025-12": 10, "2026-01": 50})
        self.assertEqual(self.store.period_totals("年"), {"2025": 10, "2026": 50})
        self.assertEqual(len(self.store.period_totals("日")), 3)
        target = self.path.with_suffix(".xlsx")
        export_excel(self.store, target)
        with ZipFile(target) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(len(ET.fromstring(archive.read("xl/workbook.xml")).findall(".//{*}sheet")), 5)
            rows = ET.fromstring(archive.read("xl/worksheets/sheet2.xml"))
            self.assertEqual(rows.find(".//{*}c[@r='B2']/{*}is/{*}t").text, "=车票")
            self.assertEqual(rows.find(".//{*}c[@r='G2']/{*}v").text, "10.00")

    def test_budget_theme_and_switching_tabs(self):
        root = tk.Tk()
        self.addCleanup(root.destroy)
        app = TravelLedgerApp(root, self.store)
        app.notebook.select(1)
        self.store.set_budget("45")
        app.switch_theme()
        root.update_idletasks()
        self.assertIn("超出预算 ¥ 15.00", app.budget_label.cget("text"))
        self.assertEqual(app.chart.cget("background"), app.palette.surface)
        self.assertEqual(app.notebook.index("current"), 1)
        self.assertEqual(LedgerStore(self.path).theme, "dark")
        app.period.set("年")
        app.draw_charts()
        labels = [app.chart.itemcget(i,"text") for i in app.chart.find_all() if app.chart.type(i)=="text"]
        self.assertIn("2025", labels)
        app.switch_theme()
        self.assertEqual(LedgerStore(self.path).theme, "light")

    def test_restore_preserves_previous_and_reads_legacy(self):
        legacy = json.loads(self.path.read_text("utf-8"))
        del legacy["theme"], legacy["budget"]
        old = self.path.with_name("old.json")
        old.write_text(json.dumps(legacy), encoding="utf-8")
        self.store.set_budget("100")
        self.store.set_theme("dark")
        self.store.delete_record(self.store.records[-1]["id"])
        original = self.store.restore(old)
        self.assertEqual(LedgerStore(original).totals()["total"], 30)
        self.assertEqual((self.store.theme, self.store.budget, self.store.totals()["total"]), ("light", 0, 60))
        self.store.restore(original)
        self.assertEqual((self.store.theme, self.store.budget, self.store.totals()["total"]), ("dark", 100, 30))
        old.write_text("{", encoding="utf-8")
        before = self.path.read_bytes()
        with self.assertRaises(LedgerError):
            self.store.restore(old)
        self.assertEqual(self.path.read_bytes(), before)

    def test_lookup_direction_and_failure(self):
        data = {"data": {"currency": "CNY", "rates": {"USD": "0.125", "KRW": "200", "THB": "5", "IDR": "2500"}}}
        with patch("travel_reports.urlopen", return_value=BytesIO(json.dumps(data).encode())):
            rates = fetch_rates()
        self.assertEqual(rates["USD"], 8)
        self.assertEqual(rates["IDR"], Decimal("0.0004"))
        with patch("travel_reports.urlopen", side_effect=TimeoutError), self.assertRaises(LedgerError):
            fetch_rates()
