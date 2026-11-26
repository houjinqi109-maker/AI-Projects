"""联网查询与 Excel 导出。记账应用运行时只需要 Python 标准库。"""
import json
import os
import tempfile
from decimal import Decimal, DecimalException
from pathlib import Path
from urllib.request import urlopen
from xml.etree import ElementTree as ET
from zipfile import ZipFile, ZIP_DEFLATED

from ledger_core import CURRENCIES, LedgerError


def fetch_rates():
    """接口以人民币为基准，取倒数后得到记账表单需要的方向。"""
    try:
        with urlopen("https://api.coinbase.com/v2/exchange-rates?currency=CNY", timeout=10) as response:
            data = json.load(response)["data"]
        if data["currency"] != "CNY":
            raise ValueError("基准币种错误")
        rates = {"CNY": Decimal("1")}
        for code in CURRENCIES.keys() - {"CNY"}:
            quote = Decimal(str(data["rates"][code]))
            if not quote.is_finite() or quote <= 0:
                raise ValueError("报价无效")
            rate = (1 / quote).quantize(Decimal("0.000000000001")).normalize()
            if not 0 < rate <= 1000000:
                raise ValueError("报价超出范围")
            rates[code] = rate
        return rates
    except (OSError, ValueError, KeyError, TypeError, DecimalException) as exc:
        raise LedgerError("汇率查询失败，请检查网络后重试，或手动填写汇率。") from exc


def export_excel(store, destination):
    """写出标准 XLSX。文本使用字符串单元格，金额使用可求和的数字单元格。"""
    path = Path(destination)
    if path.suffix.lower() != ".xlsx" or path.resolve() == store.path.resolve():
        raise LedgerError("请选择独立的 .xlsx 文件。")
    totals = store.totals()
    summary = [("项目", "人民币（元）"), ("总支出", totals["total"])]
    if store.budget:
        summary.extend([("预算", store.budget), ("预算余额", store.budget - totals["total"])])
    summary.extend(totals["by_category"].items())
    expenses = [("日期", "说明", "分类", "原币金额", "币种", "汇率", "人民币金额", "付款人", "分摊成员")]
    for r in store.records:
        expenses.append((r["date"], r["description"], r["category"], Decimal(r["amount"]), r["currency"],
                         Decimal(r["rate"]), store.rmb_amount(r), r["payer"], "、".join(r["participants"])))
    sheets = {"旅行汇总": summary, "消费明细": expenses}
    for period in ("日", "月", "年"):
        sheets[f"按{period}统计"] = [("期间", "人民币（元）"), *store.period_totals(period).items()]

    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    pack = "http://schemas.openxmlformats.org/package/2006/relationships"
    mime = "application/vnd.openxmlformats-officedocument.spreadsheetml"
    types = ET.Element("Types", xmlns="http://schemas.openxmlformats.org/package/2006/content-types")
    ET.SubElement(types, "Default", Extension="rels", ContentType="application/vnd.openxmlformats-package.relationships+xml")
    ET.SubElement(types, "Override", PartName="/xl/workbook.xml", ContentType=mime + ".sheet.main+xml")
    book = ET.Element("workbook", {"xmlns": main, "xmlns:r": rel})
    names = ET.SubElement(book, "sheets")
    links = ET.Element("Relationships", xmlns=pack)
    root_links = ET.Element("Relationships", xmlns=pack)
    ET.SubElement(root_links, "Relationship", Id="rId1", Type=rel+"/officeDocument", Target="xl/workbook.xml")
    temp = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
            temp = Path(f.name)
        with ZipFile(temp, "w", ZIP_DEFLATED) as archive:
            for i, (title, rows) in enumerate(sheets.items(), 1):
                target = f"worksheets/sheet{i}.xml"
                rid = f"rId{i}"
                ET.SubElement(names, "sheet", {"name": title, "sheetId": str(i), "r:id": rid})
                ET.SubElement(links, "Relationship", Id=rid, Type=rel+"/worksheet", Target=target)
                ET.SubElement(types, "Override", PartName="/xl/"+target, ContentType=mime+".worksheet+xml")
                sheet = ET.Element("worksheet", xmlns=main)
                cols = ET.SubElement(sheet, "cols")
                for j, heading in enumerate(rows[0], 1):
                    width = "38" if heading in ("说明", "分摊成员") else "19"
                    ET.SubElement(cols, "col", min=str(j), max=str(j), width=width, customWidth="1")
                grid = ET.SubElement(sheet, "sheetData")
                for n, values in enumerate(rows, 1):
                    row = ET.SubElement(grid, "row", r=str(n))
                    for j, value in enumerate(values):
                        cell = ET.SubElement(row, "c", r=f"{chr(65+j)}{n}")
                        if isinstance(value, Decimal):
                            ET.SubElement(cell, "v").text = format(value, "f")
                        else:
                            cell.set("t", "inlineStr")
                            ET.SubElement(ET.SubElement(cell, "is"), "t").text = value
                archive.writestr("xl/"+target, ET.tostring(sheet, encoding="utf-8"))
            for name, element in [("xl/workbook.xml", book), ("_rels/.rels", root_links),
                                  ("xl/_rels/workbook.xml.rels", links), ("[Content_Types].xml", types)]:
                archive.writestr(name, ET.tostring(element, encoding="utf-8"))
        os.replace(temp, path)
    except OSError as exc:
        raise LedgerError("Excel 导出失败，请检查目录权限并关闭已打开的同名表格。") from exc
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
