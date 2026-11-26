"""旅行记账的数据层：只使用 Python 标准库，与图形界面相互独立。"""

from __future__ import annotations

import copy
import csv
import json
import os
import re
import tempfile
import uuid
from datetime import date as Date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from pathlib import Path


CURRENCIES = {
    "CNY": "人民币",
    "KRW": "韩元",
    "USD": "美元",
    "IDR": "印尼盾",
    "THB": "泰铢",
}
CATEGORIES = ["餐饮", "住宿", "交通", "购物", "门票", "娱乐", "其他"]
# 汇率含义：1 单位外币等于多少人民币。0 表示尚未设置。
DEFAULT_RATES = {code: Decimal("1") if code == "CNY" else Decimal("0") for code in CURRENCIES}
CENT = Decimal("0.01")


class LedgerError(Exception):
    """可以直接展示给用户的输入或文件错误。"""


def _decimal_value(value, label, *, allow_zero=False, places=2, maximum=Decimal("1000000000000")):
    """统一验证数值，避免浮点误差和 NaN、无限大等特殊值。"""
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise LedgerError(f"{label}必须是数字。")
    text = str(value).strip()
    if len(text) > 64:
        raise LedgerError(f"{label}过长。")
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise LedgerError(f"{label}必须是有效数字，例如 25.50。") from exc
    if not number.is_finite():
        raise LedgerError(f"{label}必须是有限数字。")
    if number < 0 or (number == 0 and not allow_zero):
        raise LedgerError(f"{label}必须{'大于或等于' if allow_zero else '大于'} 0。")
    if number > maximum:
        raise LedgerError(f"{label}不能超过 {maximum:,}。")
    # 直接检查有效小数位，不对极端指数做 normalize 运算，避免溢出。
    if number == 0:
        return Decimal("0")
    digits = number.as_tuple().digits
    trailing_zeroes = 0
    for digit in reversed(digits):
        if digit != 0:
            break
        trailing_zeroes += 1
    if number.as_tuple().exponent + trailing_zeroes < -places:
        raise LedgerError(f"{label}最多支持 {places} 位小数。")
    return number


def _clean_text(value, label, *, maximum=200):
    if not isinstance(value, str):
        raise LedgerError(f"{label}必须是文字。")
    cleaned = value.strip()
    if not cleaned:
        raise LedgerError(f"请填写{label}。")
    if len(cleaned) > maximum:
        raise LedgerError(f"{label}不能超过 {maximum} 个字符。")
    if any(ord(character) < 32 for character in cleaned):
        raise LedgerError(f"{label}不能包含换行或控制字符。")
    return cleaned


def _members(values):
    if not isinstance(values, list) or not values:
        raise LedgerError("请至少保留一名旅行成员。")
    if len(values) > 100:
        raise LedgerError("最多支持 100 名旅行成员。")
    result = [_clean_text(value, "成员姓名", maximum=30) for value in values]
    if len(set(result)) != len(result):
        raise LedgerError("成员姓名不能重复。")
    return result


def _rates(values):
    if not isinstance(values, dict) or set(values) != set(CURRENCIES):
        raise LedgerError("汇率必须包含人民币、韩元、美元、印尼盾和泰铢。")
    result = {
        code: _decimal_value(values[code], f"{name}汇率", allow_zero=True, places=12,
                             maximum=Decimal("1000000"))
        for code, name in CURRENCIES.items()
    }
    if result["CNY"] != 1:
        raise LedgerError("人民币汇率固定为 1，不能修改。")
    return result


def _record_values(*, date, description, category, amount, currency, rate, payer, participants, members):
    if not isinstance(date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise LedgerError("日期请使用 YYYY-MM-DD 格式，例如 2026-11-26。")
    try:
        Date.fromisoformat(date)
    except ValueError as exc:
        raise LedgerError("日期不存在，请检查年月日。") from exc
    description = _clean_text(description, "消费说明")
    if category not in CATEGORIES:
        raise LedgerError("请选择有效的消费分类。")
    if currency not in CURRENCIES:
        raise LedgerError("请选择支持的币种。")
    amount_value = _decimal_value(amount, "消费金额")
    rate_value = _decimal_value(rate, "汇率", places=12, maximum=Decimal("1000000"))
    if currency == "CNY" and rate_value != 1:
        raise LedgerError("人民币记录的汇率必须为 1。")
    if not isinstance(payer, str) or payer not in members:
        raise LedgerError("请选择已有的付款人。")
    if not isinstance(participants, list) or not participants:
        raise LedgerError("请至少选择一名分摊成员。")
    if any(not isinstance(person, str) or person not in members for person in participants):
        raise LedgerError("分摊成员必须属于旅行成员。")
    if len(set(participants)) != len(participants):
        raise LedgerError("同一成员不能重复参与分摊。")
    return {
        "date": date,
        "description": description,
        "category": category,
        "amount": format(amount_value, ".2f"),
        "currency": currency,
        "rate": format(rate_value, "f"),
        "payer": payer,
        "participants": list(participants),
    }


class LedgerStore:
    """保存成员、汇率和消费；每次成功修改后立即写入 JSON 文件。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.members = ["我", "同行人"]
        self.rates = DEFAULT_RATES.copy()
        self.records = []
        self.budget = Decimal("0")
        self.theme = "light"
        if self.path.exists():
            self._load()

    def _load(self):
        """先验证整个文件，全部通过后再更新内存；坏文件绝不会被覆盖。"""
        try:
            with self.path.open("r", encoding="utf-8-sig") as source:
                data = json.load(source)
            if (not isinstance(data, dict) or type(data.get("schema_version")) is not int
                    or data.get("schema_version") != 1):
                raise LedgerError("数据文件版本或结构无效。")
            new_members = _members(data.get("members"))
            new_rates = _rates(data.get("rates"))
            budget = _decimal_value(data.get("budget", "0"), "旅行预算", allow_zero=True)
            theme = data.get("theme", "light")
            if theme not in ("light", "dark"):
                raise LedgerError("主题设置无效。")
            raw_records = data.get("records")
            if not isinstance(raw_records, list):
                raise LedgerError("消费记录必须是列表。")
            new_records = []
            seen_ids = set()
            fields = {"id", "date", "description", "category", "amount", "currency", "rate", "payer", "participants"}
            for record in raw_records:
                if not isinstance(record, dict) or not fields.issubset(record):
                    raise LedgerError("消费记录缺少必要字段。")
                identifier = record["id"]
                if not isinstance(identifier, str):
                    raise LedgerError("消费记录编号无效。")
                try:
                    uuid.UUID(identifier)
                except (ValueError, AttributeError) as exc:
                    raise LedgerError("消费记录编号无效。") from exc
                if identifier in seen_ids:
                    raise LedgerError("消费记录编号重复。")
                seen_ids.add(identifier)
                values = _record_values(**{key: record[key] for key in fields - {"id"}}, members=new_members)
                new_records.append({"id": identifier, **values})
            self.members, self.rates, self.records = new_members, new_rates, new_records
            self.budget, self.theme = budget, theme
        except (OSError, UnicodeError, json.JSONDecodeError, LedgerError, TypeError, ValueError, InvalidOperation) as exc:
            raise LedgerError(f"无法读取数据文件：{self.path}\n{exc}\n原文件已保留，请先备份并检查文件。") from exc

    def save(self):
        """在同一目录写临时文件后整体替换，避免程序中断留下半个 JSON。"""
        self._write(self.path)

    def _write(self, path):
        data = {
            "schema_version": 1,
            "budget": format(self.budget, "f"),
            "theme": self.theme,
            "members": self.members,
            "rates": {code: format(value, "f") for code, value in self.rates.items()},
            "records": self.records,
        }
        temp_path = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=path.parent,
                                             prefix=path.name + ".", suffix=".tmp", delete=False) as target:
                temp_path = Path(target.name)
                json.dump(data, target, ensure_ascii=False, indent=2, allow_nan=False)
                target.write("\n")
                target.flush()
                os.fsync(target.fileno())
            os.replace(temp_path, path)
        except (OSError, TypeError, ValueError) as exc:
            raise LedgerError(f"保存失败：{exc}\n请检查文件夹是否可写，或磁盘是否有足够空间。") from exc
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def backup(self, path):
        path = Path(path)
        if path.suffix.lower() != ".json" or path.resolve() == self.path.resolve():
            raise LedgerError("请选择与当前账本不同的 .json 备份文件。")
        self._write(path)

    def restore(self, path):
        path = Path(path)
        if not path.is_file() or path.resolve() == self.path.resolve():
            raise LedgerError("请选择另一个有效的账本备份。")
        restored = LedgerStore(path)
        # 先留一份可恢复的副本，备份或写入失败都会保留当前账本。
        previous = self.path.parent / "backups" / f"恢复前-{uuid.uuid4().hex}.json"
        self.backup(previous)
        self._commit(records=restored.records, members=restored.members, rates=restored.rates,
                     budget=restored.budget, theme=restored.theme)
        return previous

    def set_budget(self, value):
        self._commit(budget=_decimal_value(value, "旅行预算", allow_zero=True))

    def set_theme(self, value):
        if value not in ("light", "dark"):
            raise LedgerError("请选择浅色或深色。")
        self._commit(theme=value)

    def period_totals(self, period):
        length = {"日": 10, "月": 7, "年": 4}[period]
        totals = {}
        for record in self.records:
            key = record["date"][:length]
            totals[key] = totals.get(key, Decimal("0.00")) + self.rmb_amount(record)
        return dict(sorted(totals.items()))

    def _commit(self, *, records=None, members=None, rates=None, budget=None, theme=None):
        """只有磁盘保存成功才接受修改；失败时恢复修改前的内存数据。"""
        previous = self.records, self.members, self.rates, self.budget, self.theme
        if budget is not None:
            self.budget = budget
        if theme is not None:
            self.theme = theme
        if records is not None:
            self.records = records
        if members is not None:
            self.members = members
        if rates is not None:
            self.rates = rates
        try:
            self.save()
        except Exception:
            self.records, self.members, self.rates, self.budget, self.theme = previous
            raise

    def _new_values(self, *, date, description, category, amount, currency, payer, participants, rate=None):
        if not isinstance(currency, str) or currency not in CURRENCIES:
            raise LedgerError("请选择支持的币种。")
        chosen_rate = self.rates[currency] if rate is None else rate
        if chosen_rate <= 0:
            raise LedgerError(f"请先在设置中填写{CURRENCIES[currency]}汇率：1 单位外币等于多少人民币。")
        return _record_values(date=date, description=description, category=category, amount=amount,
                              currency=currency, rate=chosen_rate, payer=payer,
                              participants=participants, members=self.members)

    def add_record(self, date, description, category, amount, currency, payer, participants):
        values = self._new_values(date=date, description=description, category=category, amount=amount,
                                  currency=currency, payer=payer, participants=participants)
        record = {"id": str(uuid.uuid4()), **values}
        self._commit(records=[*self.records, record])
        # 返回副本，防止界面无意中修改尚未保存的数据。
        return copy.deepcopy(record)

    def _find_record(self, record_id):
        for index, record in enumerate(self.records):
            if record["id"] == record_id:
                return index, record
        raise LedgerError("找不到这条消费记录，请刷新后重试。")

    def update_record(self, record_id, date, description, category, amount, currency, payer, participants):
        index, previous = self._find_record(record_id)
        # 同币种编辑沿用原来的汇率，修改币种时才使用设置中的新汇率。
        old_rate = Decimal(previous["rate"]) if currency == previous["currency"] else None
        values = self._new_values(date=date, description=description, category=category, amount=amount,
                                  currency=currency, payer=payer, participants=participants, rate=old_rate)
        record = {"id": previous["id"], **values}
        records = self.records.copy()
        records[index] = record
        self._commit(records=records)
        return copy.deepcopy(record)

    def delete_record(self, record_id):
        index, _ = self._find_record(record_id)
        records = self.records.copy()
        del records[index]
        self._commit(records=records)

    def update_settings(self, members, rates):
        new_members = _members(members)
        new_rates = _rates(rates)
        referenced = {person for record in self.records for person in [record["payer"], *record["participants"]]}
        removed = referenced - set(new_members)
        if removed:
            raise LedgerError("以下成员已用于消费记录，暂时不能删除或改名：" + "、".join(sorted(removed)) +
                              "。请先修改或删除相关消费。")
        self._commit(members=new_members, rates=new_rates)

    @staticmethod
    def rmb_amount(record):
        # 允许的金额和汇率乘积最多 34 位有效数字；先精确相乘，最后才按分取整。
        # 默认的 28 位精度可能让接近半分钱的大金额提前舍入，导致相差一分钱。
        with localcontext() as context:
            context.prec = 50
            return (Decimal(record["amount"]) * Decimal(record["rate"])).quantize(CENT, rounding=ROUND_HALF_UP)

    def totals(self):
        by_category = {category: Decimal("0.00") for category in CATEGORIES}
        by_currency = {currency: Decimal("0.00") for currency in CURRENCIES}
        for record in self.records:
            by_category[record["category"]] += self.rmb_amount(record)
            by_currency[record["currency"]] += Decimal(record["amount"])
        return {
            "total": sum(by_category.values(), Decimal("0.00")),
            "count": len(self.records),
            "by_category": by_category,
            "by_currency": by_currency,
        }

    def settlement(self):
        """每条记录先换算到人民币分，再等分，确保任何时候收支严格相等。"""
        balances = {member: Decimal("0.00") for member in self.members}
        for record in self.records:
            total_cents = int(self.rmb_amount(record) * 100)
            participants = record["participants"]
            base_cents, extra_cents = divmod(total_cents, len(participants))
            balances[record["payer"]] += Decimal(total_cents) / 100
            # 余下的几分钱按记录中参与人的顺序，每人依次多承担一分。
            for index, member in enumerate(participants):
                share = base_cents + (1 if index < extra_cents else 0)
                balances[member] -= Decimal(share) / 100
        creditors = [[member, amount] for member, amount in balances.items() if amount > 0]
        debtors = [[member, -amount] for member, amount in balances.items() if amount < 0]
        transfers = []
        credit_index = debt_index = 0
        while credit_index < len(creditors) and debt_index < len(debtors):
            creditor, credit = creditors[credit_index]
            debtor, debt = debtors[debt_index]
            amount = min(credit, debt)
            transfers.append({"from": debtor, "to": creditor, "amount": amount.quantize(CENT)})
            creditors[credit_index][1] -= amount
            debtors[debt_index][1] -= amount
            if creditors[credit_index][1] == 0:
                credit_index += 1
            if debtors[debt_index][1] == 0:
                debt_index += 1
        return {"balances": balances, "transfers": transfers}

    def export_csv(self, path):
        """UTF-8 BOM 便于 Excel 识别中文，文本加前缀避免被当成公式。"""
        target_path = Path(path)
        if target_path.resolve() == self.path.resolve():
            raise LedgerError("CSV 导出位置不能与正在使用的 JSON 数据文件相同。")

        def safe_text(value):
            text = str(value)
            return "'" + text if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r", "\n")) else text

        try:
            with target_path.open("w", newline="", encoding="utf-8-sig") as output:
                writer = csv.writer(output)
                writer.writerow(["日期", "消费说明", "分类", "原币金额", "币种", "记录时汇率（1原币=人民币）",
                                 "人民币金额", "付款人", "分摊成员", "记录编号"])
                for record in self.records:
                    writer.writerow([
                        record["date"], safe_text(record["description"]), safe_text(record["category"]),
                        record["amount"], f"{CURRENCIES[record['currency']]} ({record['currency']})",
                        record["rate"], format(self.rmb_amount(record), ".2f"), safe_text(record["payer"]),
                        safe_text("、".join(record["participants"])), record["id"],
                    ])
        except (OSError, UnicodeError, csv.Error) as exc:
            raise LedgerError(f"导出失败：{exc}\n如果 CSV 正在 Excel 中打开，请先关闭后重试。") from exc
