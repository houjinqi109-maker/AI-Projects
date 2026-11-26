"""实际创建 Tkinter 控件，检查界面事件与数据层的连接。"""

import csv
from decimal import Decimal
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from ledger_core import LedgerStore
from travel_ledger import CURRENCY_LABELS, RecordDialog, SettingsDialog, TravelLedgerApp


class GuiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "test.json"
        self.store = LedgerStore(self.path)
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.app = TravelLedgerApp(self.root, self.store)
        self.root.update_idletasks()

    def fill_record(self, dialog, *, amount="100", description="晚餐"):
        dialog.values["date"].set("2026-11-26")
        dialog.values["description"].set(description)
        dialog.values["amount"].set(amount)
        return dialog

    def test_add_edit_delete_refresh_and_reload(self):
        # 通过表单真正保存，检查汇总和 AA 页同步刷新。
        dialog = self.fill_record(RecordDialog(self.app))
        dialog.submit()
        self.assertEqual(self.app.summary["total"].get(), "¥ 100.00")
        self.assertEqual(len(self.app.tree.get_children()), 1)
        self.assertEqual(len(self.store.settlement()["transfers"]), 1)
        self.assertIn("50.00", self.app.transfer_text.get("1.0", "end"))
        self.assertEqual(len(LedgerStore(self.path).records), 1)

        record = self.store.records[0]
        dialog = self.fill_record(RecordDialog(self.app, record), amount="80", description="修改后的晚餐")
        dialog.submit()
        self.assertEqual(self.app.summary["total"].get(), "¥ 80.00")
        self.assertIn("40.00", self.app.transfer_text.get("1.0", "end"))

        self.app.tree.selection_set(record["id"])
        with patch("travel_ledger.messagebox.askyesno", return_value=True):
            self.app.delete_record()
        self.assertEqual(self.app.summary["count"].get(), "0")
        self.assertEqual(LedgerStore(self.path).records, [])

    def test_settings_foreign_currency_and_rate_snapshot(self):
        settings = SettingsDialog(self.app)
        settings.rates["USD"].set("7.2")
        settings.members.delete("1.0", "end")
        settings.members.insert("1.0", "我\n同行人\n小王")
        settings.submit()
        self.assertEqual(self.app.summary["members"].get(), "3")

        dialog = self.fill_record(RecordDialog(self.app), amount="10", description="美元消费")
        dialog.values["currency"].set(CURRENCY_LABELS["USD"])
        dialog.submit()
        self.assertEqual(self.app.summary["total"].get(), "¥ 72.00")
        self.assertEqual(len(self.store.records[0]["participants"]), 3)

        settings = SettingsDialog(self.app)
        settings.rates["USD"].set("7.5")
        settings.submit()
        dialog = RecordDialog(self.app, self.store.records[0])
        self.assertIn("7.2", dialog.rate_text.get())
        self.assertIn("保留", dialog.rate_text.get())
        dialog.values["description"].set("只改说明")
        dialog.submit()
        self.assertEqual(self.app.summary["total"].get(), "¥ 72.00")

    def test_validation_keeps_dialog_open_and_writes_nothing(self):
        dialog = self.fill_record(RecordDialog(self.app), amount="-1")
        with patch("travel_ledger.messagebox.showerror") as error:
            dialog.submit()
        error.assert_called_once()
        self.assertTrue(dialog.winfo_exists())
        self.assertFalse(self.path.exists())
        self.assertEqual(self.store.records, [])
        dialog.destroy()

    def test_filter_charts_and_csv_export(self):
        dialog = self.fill_record(RecordDialog(self.app), description="酒店住宿")
        dialog.values["category"].set("住宿")
        dialog.submit()
        self.app.search_var.set("找不到")
        self.assertEqual(self.app.tree.get_children(), ())
        self.assertEqual(self.app.summary["total"].get(), "¥ 100.00")
        self.app.draw_charts()
        self.assertGreater(len(self.app.chart.find_all()), 4)
        self.app.search_var.set("")
        self.app.category_var.set("餐饮")
        self.assertEqual(self.app.tree.get_children(), ())

        target = Path(self.temp.name) / "report.csv"
        with patch("travel_ledger.filedialog.asksaveasfilename", return_value=str(target)), patch("travel_ledger.messagebox.showinfo"):
            self.app.export_csv()
        with target.open(encoding="utf-8-sig", newline="") as source:
            rows = list(csv.reader(source))
        self.assertEqual(len(rows), 2)
        self.assertIn("酒店住宿", rows[1])

    def test_csv_cannot_overwrite_ledger(self):
        dialog = self.fill_record(RecordDialog(self.app))
        dialog.submit()
        before = self.path.read_bytes()
        with patch("travel_ledger.filedialog.asksaveasfilename", return_value=str(self.path)), patch("travel_ledger.messagebox.showerror") as error:
            self.app.export_csv()
        error.assert_called_once()
        self.assertEqual(self.path.read_bytes(), before)

    def test_edit_after_reordering_members_preserves_the_extra_cent(self):
        self.fill_record(RecordDialog(self.app), amount="10.01").submit()
        before = self.store.settlement()
        self.store.update_settings(list(reversed(self.store.members)), self.store.rates)
        dialog = RecordDialog(self.app, self.store.records[0])
        dialog.values["description"].set("更正餐厅名")
        dialog.submit()
        self.assertEqual(LedgerStore(self.path).settlement(), before)

    def test_subcent_foreign_spending_is_visible_on_chart(self):
        self.store.update_settings(self.store.members, dict(self.store.rates, IDR="0.00045"))
        dialog = self.fill_record(RecordDialog(self.app), amount="1")
        dialog.values["currency"].set(CURRENCY_LABELS["IDR"])
        dialog.submit()
        self.assertEqual(self.app.summary["count"].get(), "1")
        self.assertEqual(self.app.summary["total"].get(), "¥ 0.00")
        self.assertIn("印尼盾 1.00", self.app.currency_hint.get())
        labels = [self.app.chart.itemcget(i, "text") for i in self.app.chart.find_all()
                  if self.app.chart.type(i) == "text"]
        self.assertIn("餐饮", labels)
        self.assertIn("0.00", labels)
        self.assertIn("11-26", labels)

    def test_five_currency_trip_reopened_and_exported(self):
        settings = SettingsDialog(self.app)
        for code, rate in {"KRW": "0.0052", "USD": "7.20", "IDR": "0.00045", "THB": "0.21"}.items():
            settings.rates[code].set(rate)
        settings.submit()
        # 原币金额、折合人民币、分类、付款人。前两笔在同一天。
        cases = [
            ("CNY", "10.01", "10.01", "餐饮", "我", "01"),
            ("KRW", "10000", "52.00", "交通", "同行人", "01"),
            ("USD", "10", "72.00", "住宿", "我", "02"),
            ("IDR", "100000", "45.00", "餐饮", "我", "03"),
            ("THB", "100", "21.00", "娱乐", "同行人", "04"),
        ]
        for code, amount, _, category, payer, day in cases:
            dialog = self.fill_record(RecordDialog(self.app), amount=amount, description=f"{category},{code}")
            dialog.values["date"].set(f"2026-11-{day}")
            dialog.values["currency"].set(CURRENCY_LABELS[code])
            dialog.values["category"].set(category)
            dialog.values["payer"].set(payer)
            dialog.submit()

        # 从磁盘重新创建账本和界面，再核对统计、结算与导出。
        reopened = LedgerStore(self.path)
        window = tk.Toplevel(self.root)
        app = TravelLedgerApp(window, reopened)
        self.assertEqual(app.summary["total"].get(), "¥ 200.01")
        self.assertEqual(len(app.tree.get_children()), 5)
        self.assertEqual(reopened.settlement()["transfers"],
                         [{"from": "同行人", "to": "我", "amount": Decimal("27.00")}])
        labels = [app.chart.itemcget(i, "text") for i in app.chart.find_all() if app.chart.type(i) == "text"]
        self.assertTrue({"55.01", "52.00", "72.00", "21.00", "11-01", "11-02", "11-03", "11-04"} <= set(labels))
        dots = [app.chart.coords(i) for i in app.chart.find_all() if app.chart.type(i) == "oval"]
        self.assertEqual(len(dots), 4)
        # 每日折线按 62.01、72、45、21 绘制。用后两天反推出刻度核对另外两点。
        y1, y2, y3, y4 = [dot[1] for dot in dots]
        step = (y4 - y3) / (45 - 21)
        self.assertGreater(step, 0)
        self.assertAlmostEqual(y1, y4 - (62.01 - 21) * step)
        self.assertAlmostEqual(y2, y4 - (72 - 21) * step)
        target = Path(self.temp.name) / "五币种.csv"
        with patch("travel_ledger.filedialog.asksaveasfilename", return_value=str(target)), patch("travel_ledger.messagebox.showinfo"):
            app.export_csv()
        with target.open(encoding="utf-8-sig", newline="") as source:
            rows = list(csv.DictReader(source))
        self.assertEqual(len(rows), 5)
        for row, (code, amount, expected, category, payer, _) in zip(rows, cases):
            self.assertIn(code, row["币种"])
            self.assertEqual(row["消费说明"], f"{category},{code}")
            self.assertEqual(row["付款人"], payer)
            self.assertEqual(Decimal(row["原币金额"]), Decimal(amount))
            self.assertEqual(row["人民币金额"], expected)


if __name__ == "__main__":
    unittest.main()
