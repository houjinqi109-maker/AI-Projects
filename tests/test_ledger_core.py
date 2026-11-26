"""旅行记账的业务测试：不需要打开窗口，也不会改动真实账本。"""

import csv
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from decimal import Decimal
from unittest.mock import patch

# 允许直接执行 python tests/test_ledger_core.py。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ledger_core import CATEGORIES, CURRENCIES, DEFAULT_RATES, LedgerError, LedgerStore


class LedgerStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "data" / "ledger.json"
        self.store = LedgerStore(self.path)

    def record_values(self, **changes):
        values = {
            "date": "2026-11-26",
            "description": "旅行午餐",
            "category": CATEGORIES[0],
            "amount": "100.00",
            "currency": "CNY",
            "payer": self.store.members[0],
            "participants": list(self.store.members),
        }
        values.update(changes)
        return values

    def add(self, **changes):
        return self.store.add_record(**self.record_values(**changes))

    def set_rates(self, **changes):
        rates = dict(self.store.rates)
        rates.update(changes)
        self.store.update_settings(list(self.store.members), rates)

    def test_new_ledger_has_five_currencies_and_no_expenses(self):
        self.assertEqual(set(CURRENCIES), {"CNY", "KRW", "USD", "IDR", "THB"})
        self.assertEqual(self.store.members, ["我", "同行人"])
        self.assertEqual(self.store.rates["CNY"], Decimal("1"))
        for code in ("KRW", "USD", "IDR", "THB"):
            self.assertEqual(self.store.rates[code], Decimal("0"))
        self.assertEqual(self.store.totals()["total"], Decimal("0"))
        self.assertEqual(self.store.settlement()["transfers"], [])

    def test_add_modify_delete_are_saved_and_reloaded(self):
        first = self.add()
        self.assertTrue(self.path.is_file())
        self.assertEqual(len(LedgerStore(self.path).records), 1)
        updated = self.store.update_record(
            first["id"], **self.record_values(amount="125.50", description="两人晚餐")
        )
        self.assertEqual(updated["id"], first["id"])
        loaded = LedgerStore(self.path)
        self.assertEqual(loaded.records[0]["description"], "两人晚餐")
        self.assertEqual(loaded.rmb_amount(loaded.records[0]), Decimal("125.50"))
        self.store.delete_record(first["id"])
        self.assertEqual(LedgerStore(self.path).records, [])

    def test_conversion_uses_decimal_and_freezes_existing_rate(self):
        self.set_rates(USD="7.2")
        original = self.add(amount="10.01", currency="USD")
        self.assertEqual(self.store.rmb_amount(original), Decimal("72.07"))
        self.set_rates(USD="8")
        self.assertEqual(self.store.rmb_amount(original), Decimal("72.07"))
        changed = self.store.update_record(
            original["id"], **self.record_values(amount="10.01", currency="USD", description="更正备注")
        )
        self.assertEqual(self.store.rmb_amount(changed), Decimal("72.07"))
        fresh = self.add(amount="10.01", currency="USD")
        self.assertEqual(self.store.rmb_amount(fresh), Decimal("80.08"))

    def test_changing_currency_uses_current_rate(self):
        original = self.add(amount="100")
        self.set_rates(USD="7.2")
        changed = self.store.update_record(
            original["id"], **self.record_values(amount="10", currency="USD")
        )
        self.assertEqual(self.store.rmb_amount(changed), Decimal("72.00"))

    def test_all_foreign_currencies_are_convertible(self):
        self.set_rates(KRW="0.0052", USD="7.2", IDR="0.00045", THB="0.21")
        amounts = {"CNY": "10", "KRW": "10000", "USD": "10", "IDR": "100000", "THB": "100"}
        expected = {"CNY": "10.00", "KRW": "52.00", "USD": "72.00", "IDR": "45.00", "THB": "21.00"}
        for code, amount in amounts.items():
            record = self.add(amount=amount, currency=code)
            self.assertEqual(self.store.rmb_amount(record), Decimal(expected[code]))
        totals = self.store.totals()
        self.assertEqual(totals["total"], Decimal("200.00"))
        self.assertEqual(totals["count"], 5)
        self.assertEqual(totals["by_currency"]["KRW"], Decimal("10000"))
        self.assertEqual(totals["by_category"][CATEGORIES[0]], Decimal("200.00"))

    def test_half_cent_is_rounded_half_up(self):
        self.set_rates(USD="1.005")
        record = self.add(amount="1", currency="USD")
        self.assertEqual(self.store.rmb_amount(record), Decimal("1.01"))

    def test_large_allowed_conversion_rounds_only_after_exact_multiplication(self):
        # 这个仍在输入范围内的乘积需要超过默认的 28 位有效数字。
        # 如果在乘法时先舍入，会错误地由 .00 变成 .01。
        self.set_rates(USD="999999.500000000001")
        record = self.add(amount="999999999999.99", currency="USD")
        expected = Decimal("999999499999990001.00")
        self.assertEqual(self.store.rmb_amount(record), expected)
        self.assertEqual(self.store.totals()["total"], expected)
        loaded = LedgerStore(self.path)
        self.assertEqual(loaded.rmb_amount(loaded.records[0]), expected)

    def test_extreme_decimal_exponents_cannot_expand_saved_files(self):
        self.set_rates(USD="0E-999999999")
        self.assertEqual(self.store.rates["USD"], Decimal("0"))
        self.assertLess(self.path.stat().st_size, 2000)
        for number in ("1E-999999999", "1E+999999999"):
            with self.subTest(rate=number), self.assertRaises(LedgerError):
                self.set_rates(USD=number)
        self.assertLess(self.path.stat().st_size, 2000)

    def test_foreign_currency_requires_rate(self):
        with self.assertRaises(LedgerError):
            self.add(currency="KRW", amount="10000")
        self.assertEqual(self.store.records, [])

    def test_bad_record_values_are_rejected_without_mutation(self):
        invalid = [
            {"amount": "0"}, {"amount": "-1"}, {"amount": "NaN"},
            {"amount": "Infinity"}, {"amount": "abc"}, {"amount": "1.234"},
            {"date": "2026-02-30"}, {"date": "26/11/2026"},
            {"currency": "EUR"}, {"payer": "不存在的人"},
            {"participants": []}, {"participants": ["陌生人"]},
            {"participants": ["我", "我"]},
        ]
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(LedgerError):
                self.add(**changes)
            self.assertEqual(self.store.records, [])

    def test_invalid_settings_are_rejected(self):
        for members in ([], [""], ["我", "我"]):
            with self.subTest(members=members), self.assertRaises(LedgerError):
                self.store.update_settings(members, dict(DEFAULT_RATES))
        for invalid in ("-1", "NaN", "Infinity", "abc"):
            rates = dict(DEFAULT_RATES)
            rates["USD"] = invalid
            with self.subTest(rate=invalid), self.assertRaises(LedgerError):
                self.store.update_settings(list(self.store.members), rates)

    def test_referenced_member_cannot_be_removed(self):
        self.add()
        with self.assertRaises(LedgerError):
            self.store.update_settings(["我"], dict(self.store.rates))
        self.assertEqual(self.store.members, ["我", "同行人"])
        self.assertEqual(LedgerStore(self.path).members, ["我", "同行人"])

    def test_unreferenced_member_can_be_removed(self):
        self.store.update_settings(["我", "同行人", "未参加者"], dict(self.store.rates))
        self.add(participants=["我", "同行人"])
        self.store.update_settings(["我", "同行人"], dict(self.store.rates))
        self.assertEqual(self.store.members, ["我", "同行人"])

    def assert_transfers_clear_balances(self, result):
        remaining = dict(result["balances"])
        self.assertEqual(sum(remaining.values(), Decimal("0")), Decimal("0"))
        for transfer in result["transfers"]:
            amount = transfer["amount"]
            self.assertGreater(amount, Decimal("0"))
            self.assertEqual(amount, amount.quantize(Decimal("0.01")))
            remaining[transfer["from"]] += amount
            remaining[transfer["to"]] -= amount
        self.assertTrue(all(balance == Decimal("0") for balance in remaining.values()))

    def test_aa_two_people_and_only_selected_participants(self):
        self.add(amount="100")
        result = self.store.settlement()
        self.assertEqual(result["balances"], {"我": Decimal("50.00"), "同行人": Decimal("-50.00")})
        self.assert_transfers_clear_balances(result)
        self.add(amount="30", payer="同行人", participants=["我"])
        result = self.store.settlement()
        self.assertEqual(result["balances"], {"我": Decimal("20.00"), "同行人": Decimal("-20.00")})
        self.assert_transfers_clear_balances(result)

    def test_aa_remainder_is_deterministic_and_conserves_cents(self):
        self.store.update_settings(["甲", "乙", "丙"], dict(self.store.rates))
        self.add(amount="100", payer="甲", participants=["甲", "乙", "丙"])
        result = self.store.settlement()
        self.assert_transfers_clear_balances(result)
        self.assertEqual(result, self.store.settlement())
        self.assertEqual(len(result["transfers"]), 2)
        shares = [Decimal("100") - result["balances"]["甲"], -result["balances"]["乙"], -result["balances"]["丙"]]
        self.assertEqual(sorted(shares), [Decimal("33.33"), Decimal("33.33"), Decimal("33.34")])

    def test_missing_record_is_reported(self):
        with self.assertRaises(LedgerError):
            self.store.delete_record("missing")
        with self.assertRaises(LedgerError):
            self.store.update_record("missing", **self.record_values())

    def test_save_failure_rolls_back_record_and_setting_changes(self):
        first = self.add()
        before_records = json.loads(json.dumps(self.store.records))
        before_members = list(self.store.members)
        before_rates = dict(self.store.rates)
        before_disk = self.path.read_bytes()
        changes = [
            lambda: self.add(amount="20"),
            lambda: self.store.update_record(first["id"], **self.record_values(amount="20")),
            lambda: self.store.delete_record(first["id"]),
            lambda: self.store.update_settings(["我", "同行人", "新成员"], dict(self.store.rates)),
        ]
        for operation in changes:
            with patch.object(self.store, "save", side_effect=LedgerError("模拟保存失败")):
                with self.assertRaises(LedgerError):
                    operation()
            self.assertEqual(self.store.records, before_records)
            self.assertEqual(self.store.members, before_members)
            self.assertEqual(self.store.rates, before_rates)
            self.assertEqual(self.path.read_bytes(), before_disk)

    def test_corrupt_data_is_not_overwritten(self):
        self.path.parent.mkdir(parents=True)
        for content in ("{not-json", "[]", '{"schema_version": 999}', '{"schema_version": 1, "members": "bad", "rates": {}, "records": []}'):
            self.path.write_text(content, encoding="utf-8")
            with self.subTest(content=content), self.assertRaises(LedgerError):
                LedgerStore(self.path)
            self.assertEqual(self.path.read_text(encoding="utf-8"), content)

    def test_tampered_record_is_rejected(self):
        self.add()
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        raw["records"][0]["rate"] = "NaN"
        self.path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(LedgerError):
            LedgerStore(self.path)

    def test_csv_has_bom_chinese_and_safe_user_cells(self):
        self.store.update_settings(["我", "@旅行者"], dict(self.store.rates))
        self.add(description="=2+3", payer="@旅行者", participants=["我", "@旅行者"])
        output = Path(self.temp.name) / "旅行账本.csv"
        self.store.export_csv(output)
        self.assertTrue(output.read_bytes().startswith(b"\xef\xbb\xbf"))
        rows = list(csv.reader(io.StringIO(output.read_text(encoding="utf-8-sig"))))
        self.assertEqual(len(rows), 2)
        self.assertTrue(any("人民币" in label for label in rows[0]))
        cells = rows[1]
        formula = next(cell for cell in cells if "=2+3" in cell)
        self.assertTrue(formula.startswith("'"))
        for cell in cells:
            self.assertFalse(cell.startswith(("=", "+", "-", "@", "\t", "\r")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
