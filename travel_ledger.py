"""旅途账本：只使用 Python 标准库的个人旅行记账软件。"""

from __future__ import annotations

import argparse
from datetime import date, datetime
from queue import Empty, Queue
from threading import Thread
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from travel_reports import export_excel, fetch_rates
from ledger_core import CATEGORIES, CURRENCIES, LedgerError, LedgerStore


# 界面的颜色、字体集中放在这里，方便初学者修改。
@dataclass(frozen=True)
class Palette:
    background: str
    surface: str
    text: str
    muted: str
    accent: str
    inset: str
    selected: str
    warning: str


LIGHT = Palette("#f5f5f7", "#ffffff", "#1d1d1f", "#60606a", "#0066cc", "#e9e9ee", "#cfe6ff", "#ae3b14")
DARK = Palette("#161618", "#242426", "#f5f5f7", "#b7b7c0", "#65adff", "#343438", "#334d69", "#ff9f80")
COLORS = ["#2563eb", "#14b8a6", "#f59e0b", "#8b5cf6", "#f97316", "#06b6d4", "#94a3b8"]
FONT = "Microsoft YaHei UI"
CURRENCY_LABELS = {code: f"{name} ({code})" for code, name in CURRENCIES.items()}


def money(value: Decimal) -> str:
    """金额显示两位小数；计算仍交给 Decimal，避免浮点误差。"""
    return f"{value:,.2f}"


def center_dialog(dialog: tk.Toplevel, parent: tk.Misc, width: int, height: int) -> None:
    parent.update_idletasks()
    width = min(width, dialog.winfo_screenwidth() - 60)
    height = min(height, dialog.winfo_screenheight() - 80)
    x = max(0, parent.winfo_rootx() + (parent.winfo_width() - width) // 2)
    y = max(0, parent.winfo_rooty() + (parent.winfo_height() - height) // 2)
    dialog.geometry(f"{width}x{height}+{x}+{y}")


def dialog_sections(dialog: tk.Toplevel) -> tuple[ttk.Frame, ttk.Frame]:
    """按钮固定在底部；小屏幕或高缩放时表单可滚动。"""
    actions = ttk.Frame(dialog, style="White.TFrame", padding=(24, 12))
    actions.pack(side="bottom", fill="x")
    viewport = ttk.Frame(dialog, style="White.TFrame")
    viewport.pack(fill="both", expand=True)
    canvas = tk.Canvas(viewport, background=dialog.app.palette.surface, highlightthickness=0)
    bar = ttk.Scrollbar(viewport, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=bar.set)
    bar.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    body = ttk.Frame(canvas, style="White.TFrame", padding=(24, 20, 16, 12))
    window_id = canvas.create_window((0, 0), window=body, anchor="nw")
    body.bind("<Configure>", lambda _: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window_id, width=event.width))
    dialog.bind("<MouseWheel>", lambda event: canvas.yview_scroll(-int(event.delta / 120), "units"))
    return body, actions


class RecordDialog(tk.Toplevel):
    """添加和修改共用一个表单；保存成功才关闭。"""

    def __init__(self, app: "TravelLedgerApp", record: dict | None = None):
        super().__init__(app.root)
        self.app = app
        self.record = record
        self.title("修改消费" if record else "添加消费")
        self.configure(bg=self.app.palette.surface)
        self.transient(app.root)
        self.resizable(False, True)
        self.result = None
        center_dialog(self, app.root, 580, 650)

        body, actions = dialog_sections(self)
        ttk.Label(body, text=self.title(), style="DialogTitle.TLabel").pack(anchor="w")
        ttk.Label(body, text="每笔消费选择一位付款人，可自由勾选分摊成员。", style="MutedWhite.TLabel").pack(anchor="w", pady=(6, 16))
        fields = ttk.Frame(body, style="White.TFrame")
        fields.pack(fill="x")
        fields.columnconfigure(1, weight=1)
        current = record or {}
        self.values = {
            "date": tk.StringVar(value=current.get("date", date.today().isoformat())),
            "description": tk.StringVar(value=current.get("description", "")),
            "category": tk.StringVar(value=current.get("category", CATEGORIES[0])),
            "amount": tk.StringVar(value=current.get("amount", "")),
            "currency": tk.StringVar(value=CURRENCY_LABELS[current.get("currency", "CNY")]),
            "payer": tk.StringVar(value=current.get("payer", app.store.members[0])),
        }
        labels = [("日期", "date"), ("消费说明", "description"), ("消费分类", "category"),
                  ("原币金额", "amount"), ("消费币种", "currency"), ("实际付款人", "payer")]
        self.entries = {}
        for row, (label, key) in enumerate(labels):
            ttk.Label(fields, text=label, style="White.TLabel").grid(row=row, column=0, sticky="w", pady=7, padx=(0, 18))
            choices = {"category": CATEGORIES, "currency": list(CURRENCY_LABELS.values()), "payer": app.store.members}
            if key in choices:
                widget = ttk.Combobox(fields, textvariable=self.values[key], values=choices[key], state="readonly")
            else:
                widget = ttk.Entry(fields, textvariable=self.values[key])
            widget.grid(row=row, column=1, sticky="ew", pady=7, ipady=3)
            self.entries[key] = widget
        ttk.Label(body, text="日期格式：2026-11-26；金额最多两位小数。", style="MutedWhite.TLabel").pack(anchor="w", pady=(6, 8))
        self.rate_text = tk.StringVar()
        ttk.Label(body, textvariable=self.rate_text, style="MutedWhite.TLabel", wraplength=505).pack(anchor="w", pady=(0, 14))
        self.values["currency"].trace_add("write", lambda *_: self.update_rate_text())
        self.update_rate_text()

        ttk.Label(body, text="参与分摊的成员（个人消费只勾选自己）", style="White.TLabel").pack(anchor="w")
        # 可滚动列表能容纳更多同行人；不把大量复选框挤出窗口。
        people_frame = ttk.Frame(body, style="White.TFrame")
        people_frame.pack(fill="both", expand=True, pady=(8, 12))
        self.people_canvas = tk.Canvas(people_frame, height=90, bg=self.app.palette.surface, highlightthickness=0)
        scrollbar = ttk.Scrollbar(people_frame, orient="vertical", command=self.people_canvas.yview)
        self.people_canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.people_canvas.pack(side="left", fill="both", expand=True)
        people = ttk.Frame(self.people_canvas, style="White.TFrame")
        self.people_canvas.create_window((0, 0), window=people, anchor="nw")
        people.bind("<Configure>", lambda _: self.people_canvas.configure(scrollregion=self.people_canvas.bbox("all")))
        selected = current.get("participants", app.store.members)
        self.participants = {}
        # 旧记录的顺序决定谁承担余下的一分钱，修改说明时应保留这个顺序。
        ordered_members = list(selected) + [name for name in app.store.members if name not in selected]
        for index, name in enumerate(ordered_members):
            var = tk.BooleanVar(value=name in selected)
            self.participants[name] = var
            ttk.Checkbutton(people, text=name, variable=var, style="White.TCheckbutton").grid(row=index, column=0, sticky="w", pady=3)

        ttk.Button(actions, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(actions, text="保存消费", style="Primary.TButton", command=self.submit).pack(side="right", padx=(0, 10))
        self.bind("<Escape>", lambda _: self.destroy())
        self.entries["description"].focus_set()
        self.grab_set()

    def currency_code(self) -> str:
        return next(code for code, label in CURRENCY_LABELS.items() if label == self.values["currency"].get())

    def update_rate_text(self) -> None:
        code = self.currency_code()
        rate = self.app.store.rates[code]
        if self.record and code == self.record["currency"]:
            rate = Decimal(self.record["rate"])
            note = "保留此笔原汇率"
        else:
            note = "保存时固定此汇率"
        self.rate_text.set(f"1 {CURRENCIES[code]} = {rate} 人民币 · {note}" if rate > 0 else "此币种尚未设置汇率，请先取消并打开“汇率与成员”。")

    def submit(self) -> None:
        try:
            data = {key: var.get().strip() for key, var in self.values.items() if key != "currency"}
            data["currency"] = self.currency_code()
            data["participants"] = [name for name, var in self.participants.items() if var.get()]
            if self.record:
                self.result = self.app.store.update_record(self.record["id"], **data)
            else:
                self.result = self.app.store.add_record(**data)
        except LedgerError as exc:
            messagebox.showerror("无法保存消费", str(exc), parent=self)
            return
        self.app.refresh("消费已自动保存")
        self.destroy()


class SettingsDialog(tk.Toplevel):
    def __init__(self, app: "TravelLedgerApp"):
        super().__init__(app.root)
        self.app = app
        self.title("汇率与成员")
        self.configure(bg=self.app.palette.surface)
        self.transient(app.root)
        self.resizable(False, True)
        center_dialog(self, app.root, 620, 690)
        body, actions = dialog_sections(self)
        ttk.Label(body, text="汇率与同行成员", style="DialogTitle.TLabel").pack(anchor="w")
        ttk.Label(body, text="汇率方向：1 单位外币 = 多少人民币", style="White.TLabel").pack(anchor="w", pady=(18, 6))
        ttk.Label(body, text="可手动填写或联网查询。点击保存设置后，新汇率用于之后的消费。", style="MutedWhite.TLabel", wraplength=550).pack(anchor="w", pady=(0, 10))
        self.query_button = ttk.Button(body, text="查询最新汇率", command=self.query_rates)
        self.query_button.pack(anchor="w")
        self.quote_status = tk.StringVar(master=self)
        ttk.Label(body, textvariable=self.quote_status, style="MutedWhite.TLabel").pack(anchor="w")
        grid = ttk.Frame(body, style="White.TFrame")
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)
        self.rates = {}
        for row, (code, label) in enumerate(CURRENCY_LABELS.items()):
            ttk.Label(grid, text=label, style="White.TLabel").grid(row=row, column=0, sticky="w", pady=6, padx=(0, 22))
            var = tk.StringVar(value=str(app.store.rates[code]))
            self.rates[code] = var
            ttk.Entry(grid, textvariable=var, state="readonly" if code == "CNY" else "normal").grid(row=row, column=1, sticky="ew", pady=6, ipady=3)
        ttk.Label(body, text="汇率变更用于新记录。旧记录保留记账时的汇率。", style="MutedWhite.TLabel").pack(anchor="w", pady=(8, 18))
        ttk.Label(body, text="同行成员：每行填写一个名字", style="White.TLabel").pack(anchor="w", pady=(0, 8))
        members_box = ttk.Frame(body, style="White.TFrame")
        members_box.pack(fill="both", expand=True)
        self.members = tk.Text(members_box, height=5, font=(FONT, 11), bg=app.palette.inset, fg=app.palette.text, insertbackground=app.palette.text, relief="solid", bd=1, wrap="none")
        self.members.insert("1.0", "\n".join(app.store.members))
        scroll = ttk.Scrollbar(members_box, command=self.members.yview)
        self.members.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.members.pack(fill="both", expand=True)
        ttk.Label(body, text="有消费记录引用的成员需要保留；名字请勿重复。", style="MutedWhite.TLabel").pack(anchor="w", pady=(8, 16))
        ttk.Button(actions, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(actions, text="保存设置", style="Primary.TButton", command=self.submit).pack(side="right", padx=(0, 10))
        self.bind("<Escape>", lambda _: self.destroy())
        self.grab_set()
        self.members.focus_set()

    def query_rates(self):
        # 网络请求在后台进行。界面只在主线程更新，关闭窗口即可取消使用查询结果。
        result = Queue()
        self.query_button.state(["disabled"])
        self.quote_status.set("正在查询 Coinbase 参考汇率…")

        def fetch():
            try:
                result.put(fetch_rates())
            except LedgerError as exc:
                result.put(exc)

        def collect():
            try:
                value = result.get_nowait()
            except Empty:
                self.after(100, collect)
                return
            self.query_button.state(["!disabled"])
            if isinstance(value, LedgerError):
                self.quote_status.set(str(value))
                return
            for code, rate in value.items():
                self.rates[code].set(format(rate, "f"))
            self.quote_status.set(f"Coinbase 参考汇率 · 查询于 {datetime.now():%Y-%m-%d %H:%M:%S}")

        Thread(target=fetch, daemon=True).start()
        self.after(100, collect)

    def submit(self) -> None:
        try:
            names = [line.strip() for line in self.members.get("1.0", "end").splitlines() if line.strip()]
            self.app.store.update_settings(members=names, rates={code: var.get().strip() for code, var in self.rates.items()})
        except LedgerError as exc:
            messagebox.showerror("无法保存设置", str(exc), parent=self)
            return
        self.app.refresh("汇率和成员已自动保存")
        self.destroy()


class TravelLedgerApp:
    def __init__(self, root: tk.Tk, store: LedgerStore):
        self.root = root
        self.store = store
        self.palette = DARK if store.theme == "dark" else LIGHT
        self.period = tk.StringVar(master=root, value="日")
        root.title("旅途账本 · 个人旅行记账")
        root.configure(bg=self.palette.background)
        width = min(1180, root.winfo_screenwidth() - 70)
        height = min(800, root.winfo_screenheight() - 100)
        root.geometry(f"{width}x{height}")
        root.minsize(min(980, width), min(650, height))
        self.make_styles()
        self.status = tk.StringVar()
        self.summary = {key: tk.StringVar() for key in ("total", "count", "members")}
        self.search_var = tk.StringVar()
        self.category_var = tk.StringVar(value="全部分类")
        self.make_layout()
        self.search_var.trace_add("write", lambda *_: self.refresh_records())
        self.category_var.trace_add("write", lambda *_: self.refresh_records())
        self.refresh("数据保存在本机 · 每次修改后自动保存")

    def make_styles(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", background=self.palette.surface, bordercolor=self.palette.inset, lightcolor=self.palette.surface, darkcolor=self.palette.surface, troughcolor=self.palette.inset, font=(FONT, 10), foreground=self.palette.text)
        style.configure("TFrame", background=self.palette.background)
        style.configure("White.TFrame", background=self.palette.surface)
        style.configure("TLabel", background=self.palette.background)
        style.configure("White.TLabel", background=self.palette.surface)
        style.configure("Muted.TLabel", foreground=self.palette.muted)
        style.configure("MutedWhite.TLabel", background=self.palette.surface, foreground=self.palette.muted, font=(FONT, 9))
        style.configure("Title.TLabel", font=(FONT, 25, "bold"))
        style.configure("DialogTitle.TLabel", background=self.palette.surface, font=(FONT, 17, "bold"))
        style.configure("CardValue.TLabel", background=self.palette.surface, foreground=self.palette.accent, font=(FONT, 24, "bold"))
        style.configure("TButton", background=self.palette.surface, relief="flat", borderwidth=0, padding=(14, 8))
        style.map("TButton", background=[("active", self.palette.selected)])
        style.configure("Primary.TButton", foreground="#ffffff", background="#0066cc", borderwidth=0)
        style.map("Primary.TButton", background=[("active", "#1d4ed8")], foreground=[("active", "#ffffff")])
        style.configure("TEntry", padding=5, fieldbackground=self.palette.surface)
        style.map("TEntry", fieldbackground=[("readonly", self.palette.inset)])
        style.configure("TEntry", insertcolor=self.palette.text)
        self.root.option_add("*TCombobox*Listbox.background", self.palette.surface)
        self.root.option_add("*TCombobox*Listbox.foreground", self.palette.text)
        style.configure("TCombobox", padding=5, fieldbackground=self.palette.surface)
        style.map("TCombobox", fieldbackground=[("readonly", self.palette.surface)])
        style.configure("TNotebook", background=self.palette.background, borderwidth=0)
        style.configure("TNotebook.Tab", background=self.palette.inset, padding=(22, 12))
        style.map("TNotebook.Tab", background=[("selected", self.palette.surface)], foreground=[("selected", self.palette.accent)])
        style.configure("Treeview", background=self.palette.surface, fieldbackground=self.palette.surface, rowheight=38, borderwidth=0)
        style.configure("Treeview.Heading", background=self.palette.inset, padding=(8, 10), font=(FONT, 10, "bold"))
        style.map("Treeview", background=[("selected", self.palette.selected)], foreground=[("selected", self.palette.text)])
        style.configure("White.TCheckbutton", background=self.palette.surface, padding=2)

    def make_layout(self) -> None:
        page = ttk.Frame(self.root, padding=(26, 22, 26, 12))
        self.page = page
        page.pack(fill="both", expand=True)
        heading = ttk.Frame(page)
        heading.pack(fill="x")
        title = ttk.Frame(heading)
        title.pack(side="left")
        ttk.Label(title, text="旅途账本", style="Title.TLabel").pack(anchor="w")
        self.budget_label = ttk.Label(title, style="Muted.TLabel")
        self.budget_label.pack(anchor="w", pady=(4, 0))
        ttk.Button(heading, text="汇率与成员", command=lambda: SettingsDialog(self)).pack(side="right", padx=4)
        self.make_tools(heading)
        cards = ttk.Frame(page)
        cards.pack(fill="x", pady=22)
        for index, (key, label) in enumerate((("total", "总支出 / 人民币"), ("count", "消费记录 / 笔"), ("members", "同行成员 / 人"))):
            cards.columnconfigure(index, weight=2 if index == 0 else 1, uniform="cards")
            tile = tk.Canvas(cards, bg=self.palette.background, highlightthickness=0, width=1, height=90)
            tile.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 10, 0))
            tile.bind("<Configure>", self.draw_card)
            card = ttk.Frame(tile, style="White.TFrame")
            tile.create_window(20, 45, window=card, anchor="w")
            ttk.Label(card, text=label, style="MutedWhite.TLabel").pack(anchor="w")
            ttk.Label(card, textvariable=self.summary[key], style="CardValue.TLabel").pack(anchor="w", pady=(6, 0))
        self.notebook = ttk.Notebook(page)
        # 先为底部状态预留空间，防止缩小窗口后被表格挤出。
        ttk.Label(page, textvariable=self.status, style="Muted.TLabel").pack(side="bottom", anchor="w", fill="x", pady=(12, 0))
        self.notebook.pack(fill="both", expand=True)
        records_page = ttk.Frame(self.notebook, style="White.TFrame", padding=16)
        stats_page = ttk.Frame(self.notebook, style="White.TFrame", padding=18)
        aa_page = ttk.Frame(self.notebook, style="White.TFrame", padding=18)
        self.notebook.add(records_page, text="消费明细")
        self.notebook.add(stats_page, text="支出统计")
        self.notebook.add(aa_page, text="AA 结算")
        self.make_records(records_page)
        self.make_stats(stats_page)
        self.make_aa(aa_page)

    def draw_card(self, event):
        """使用 Tk 自带的圆滑多边形绘制圆角卡片。"""
        tile = event.widget
        w, h, r = event.width, event.height, 18
        tile.delete("surface")
        tile.create_polygon(r, 0, w-r, 0, w, 0, w, r, w, h-r, w, h, w-r, h,
                            r, h, 0, h, 0, h-r, 0, r, 0, 0, smooth=True,
                            fill=self.palette.surface, outline="", tags="surface")
        tile.tag_lower("surface")

    def make_tools(self, heading):
        self.theme_button = ttk.Button(heading, text="切换浅色" if self.store.theme == "dark" else "切换深色",
                                       command=self.switch_theme)
        self.theme_button.pack(side="right", padx=4)
        tools = ttk.Menubutton(heading, text="账本工具 ▾")
        menu = tk.Menu(tools, tearoff=False, bg=self.palette.surface, fg=self.palette.text)
        for title, action in [("旅行预算", self.edit_budget), ("导出 Excel 报表", self.export_report),
                              ("备份账本", self.backup), ("从备份恢复", self.restore)]:
            menu.add_command(label=title, command=action)
        tools.configure(menu=menu)
        tools.pack(side="right", padx=4)

    def redraw(self):
        tab = self.notebook.index("current")
        self.page.destroy()
        self.palette = DARK if self.store.theme == "dark" else LIGHT
        self.root.configure(bg=self.palette.background)
        self.make_styles()
        self.make_layout()
        self.notebook.select(tab)
        self.refresh()

    def switch_theme(self):
        try:
            self.store.set_theme("light" if self.store.theme == "dark" else "dark")
        except LedgerError as exc:
            messagebox.showerror("无法保存外观", str(exc), parent=self.root)
            return
        self.redraw()

    def edit_budget(self):
        value = simpledialog.askstring("旅行预算", "整趟旅行的人民币预算（填 0 关闭提醒）",
                                       initialvalue=format(self.store.budget, "f"), parent=self.root)
        if value is None:
            return
        try:
            self.store.set_budget(value)
        except LedgerError as exc:
            messagebox.showerror("预算无效", str(exc), parent=self.root)
            return
        self.refresh("预算已保存")

    def export_report(self):
        path = filedialog.asksaveasfilename(parent=self.root, title="导出 Excel 报表", defaultextension=".xlsx",
                                          initialfile="旅行支出.xlsx", filetypes=[("Excel 工作簿", "*.xlsx")])
        if path:
            try:
                export_excel(self.store, path)
                self.status.set(f"已导出 Excel 报表 · {path}")
            except LedgerError as exc:
                messagebox.showerror("导出失败", str(exc), parent=self.root)

    def backup(self):
        path = filedialog.asksaveasfilename(parent=self.root, title="备份账本", defaultextension=".json",
                                           initialfile=f"旅途账本-{date.today()}.json", filetypes=[("账本备份", "*.json")])
        if path:
            try:
                self.store.backup(path)
                self.status.set(f"账本已备份 · {path}")
            except LedgerError as exc:
                messagebox.showerror("备份失败", str(exc), parent=self.root)

    def restore(self):
        path = filedialog.askopenfilename(parent=self.root, title="选择账本备份", filetypes=[("账本备份", "*.json")])
        if path and messagebox.askyesno("恢复账本", f"使用以下文件替换当前账本？\n{path}\n恢复前将自动备份当前数据。", parent=self.root):
            try:
                previous = self.store.restore(path)
                self.search_var.set("")
                self.category_var.set("全部分类")
                self.redraw()
                self.status.set(f"恢复成功 · 原账本已备份至 {previous}")
            except LedgerError as exc:
                messagebox.showerror("恢复失败", str(exc), parent=self.root)

    def period_details(self):
        period = self.period.get()
        window = tk.Toplevel(self.root)
        window.title(f"按{period}统计 · 人民币")
        window.transient(self.root)
        center_dialog(window, self.root, 440, 400)
        table = ttk.Treeview(window, columns=("period", "total"), show="headings")
        table.heading("period", text="期间")
        table.heading("total", text="支出（元）")
        bar = ttk.Scrollbar(window, command=table.yview)
        table.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        table.pack(fill="both", expand=True)
        for key, total in reversed(self.store.period_totals(period).items()):
            table.insert("", "end", values=(key, money(total)))
        window.grab_set()

    def make_records(self, parent: ttk.Frame) -> None:
        toolbar = ttk.Frame(parent, style="White.TFrame")
        toolbar.pack(fill="x", pady=(0, 12))
        ttk.Button(toolbar, text="＋ 添加消费", style="Primary.TButton", command=self.add_record).pack(side="left")
        ttk.Button(toolbar, text="修改", command=self.edit_record).pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="删除", command=self.delete_record).pack(side="left", padx=8)
        ttk.Button(toolbar, text="导出 CSV", command=self.export_csv).pack(side="right")
        filters = ttk.Frame(parent, style="White.TFrame")
        filters.pack(fill="x", pady=(0, 12))
        ttk.Label(filters, text="搜索说明", style="White.TLabel").pack(side="left", padx=(0, 8))
        ttk.Entry(filters, textvariable=self.search_var, width=25).pack(side="left", padx=(0, 12))
        ttk.Combobox(filters, textvariable=self.category_var, values=["全部分类", *CATEGORIES], width=12, state="readonly").pack(side="left")
        self.record_hint = tk.StringVar()
        ttk.Label(parent, textvariable=self.record_hint, style="MutedWhite.TLabel").pack(side="bottom", anchor="w", fill="x", pady=(10, 0))
        table = ttk.Frame(parent, style="White.TFrame")
        table.pack(fill="both", expand=True)
        cols = ("date", "description", "category", "amount", "currency", "rmb", "payer", "people")
        self.tree = ttk.Treeview(table, columns=cols, show="headings", selectmode="browse", height=6)
        labels = ("日期", "消费说明", "分类", "原币金额", "币种", "折合人民币", "付款人", "分摊成员")
        widths = (108, 180, 70, 115, 72, 120, 85, 150)
        for col, label, width in zip(cols, labels, widths):
            self.tree.heading(col, text=label)
            self.tree.column(col, width=width, minwidth=60, anchor="e" if col in ("amount", "rmb") else "w", stretch=col in ("description", "people"))
        ybar = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        xbar = ttk.Scrollbar(table, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        self.tree.bind("<Double-1>", lambda _: self.edit_record())

    def make_stats(self, parent: ttk.Frame) -> None:
        row = ttk.Frame(parent, style="White.TFrame")
        row.pack(fill="x")
        ttk.Label(row, text="支出分布", style="DialogTitle.TLabel").pack(side="left")
        for period in ("年", "月", "日"):
            ttk.Radiobutton(row, text=f"按{period}", value=period, variable=self.period, command=self.draw_charts).pack(side="right", padx=6)
        ttk.Button(row, text="全部期间明细", command=self.period_details).pack(side="right", padx=8)
        ttk.Label(parent, text="图表不受明细搜索影响；使用每笔消费保存的汇率。", style="MutedWhite.TLabel").pack(anchor="w", pady=(6, 12))
        self.currency_hint = tk.StringVar()
        ttk.Label(parent, textvariable=self.currency_hint, style="MutedWhite.TLabel", wraplength=950).pack(side="bottom", anchor="w", fill="x", pady=(12, 0))
        self.chart = tk.Canvas(parent, bg=self.palette.surface, highlightthickness=0, height=260)
        self.chart.pack(fill="both", expand=True)
        self.chart.bind("<Configure>", lambda _: self.draw_charts())

    def make_aa(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="AA 分摊与结算", style="DialogTitle.TLabel").pack(anchor="w")
        ttk.Label(parent, text="每笔只在勾选成员之间均分。净额 = 实际垫付 − 应承担；全部以人民币显示。", style="MutedWhite.TLabel", wraplength=950).pack(anchor="w", pady=(6, 16))
        ttk.Label(parent, text="转账方案仅供结算参考；付款后不会自动登记。尾差按参与人顺序分配到分。", style="MutedWhite.TLabel", wraplength=950).pack(side="bottom", anchor="w", fill="x", pady=(12, 0))
        content = ttk.Frame(parent, style="White.TFrame")
        content.pack(fill="both", expand=True)
        content.columnconfigure(0, weight=1)
        content.columnconfigure(1, weight=1)
        content.rowconfigure(1, weight=1)
        ttk.Label(content, text="成员净额", style="White.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 8))
        ttk.Label(content, text="建议转账方案", style="White.TLabel").grid(row=0, column=1, sticky="w", pady=(0, 8), padx=(22, 0))
        balances_frame = ttk.Frame(content, style="White.TFrame")
        balances_frame.grid(row=1, column=0, sticky="nsew")
        self.balance_tree = ttk.Treeview(balances_frame, columns=("member", "net", "state"), show="headings", height=5)
        for col, label, width in (("member", "成员", 110), ("net", "净额 / 元", 120), ("state", "结算状态", 90)):
            self.balance_tree.heading(col, text=label)
            self.balance_tree.column(col, width=width, anchor="e" if col == "net" else "w", minwidth=65)
        bar = ttk.Scrollbar(balances_frame, command=self.balance_tree.yview)
        self.balance_tree.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.balance_tree.pack(fill="both", expand=True)
        transfer_frame = ttk.Frame(content, style="White.TFrame")
        transfer_frame.grid(row=1, column=1, sticky="nsew", padx=(22, 0))
        self.transfer_text = tk.Text(transfer_frame, font=(FONT, 11), bg=self.palette.inset, fg=self.palette.text, relief="flat", padx=16, pady=16, wrap="word", width=32, height=5)
        scroll = ttk.Scrollbar(transfer_frame, command=self.transfer_text.yview)
        self.transfer_text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.transfer_text.pack(fill="both", expand=True)

    def refresh(self, status: str = "") -> None:
        totals = self.store.totals()
        self.summary["total"].set(f"¥ {money(totals['total'])}")
        delta = self.store.budget - totals["total"]
        if not self.store.budget:
            budget_text = "旅行预算未设置"
        elif delta < 0:
            budget_text = f"超出预算 ¥ {money(-delta)}   /   预算 ¥ {money(self.store.budget)}"
        else:
            budget_text = f"预算剩余 ¥ {money(delta)}   /   预算 ¥ {money(self.store.budget)}"
        self.budget_label.configure(text=budget_text, foreground=self.palette.warning if self.store.budget and delta < 0 else self.palette.muted)
        self.summary["count"].set(str(totals["count"]))
        self.summary["members"].set(str(len(self.store.members)))
        self.refresh_records()
        self.draw_charts()
        self.refresh_aa()
        original = "    |    ".join(f"{CURRENCIES[code]} {money(value)}" for code, value in totals["by_currency"].items() if value)
        self.currency_hint.set("原币合计：" + (original or "暂无支出"))
        if status:
            self.status.set(status)

    def refresh_records(self) -> None:
        selected = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        query = self.search_var.get().strip().casefold()
        category = self.category_var.get()
        rows = sorted(self.store.records, key=lambda record: record["date"], reverse=True)
        shown = 0
        for record in rows:
            if query and query not in record["description"].casefold():
                continue
            if category != "全部分类" and record["category"] != category:
                continue
            self.tree.insert("", "end", iid=record["id"], values=(record["date"], record["description"], record["category"], money(Decimal(record["amount"])), record["currency"], money(self.store.rmb_amount(record)), record["payer"], "、".join(record["participants"])))
            shown += 1
        if selected and self.tree.exists(selected[0]):
            self.tree.selection_set(selected[0])
        if not self.store.records:
            self.record_hint.set("还没有记录，点击“添加消费”开始；使用外币前请先设置汇率。")
        else:
            self.record_hint.set(f"显示 {shown} / {len(self.store.records)} 笔 · 双击记录可修改 · CSV 导出全部记录")

    def refresh_aa(self) -> None:
        aa = self.store.settlement()
        self.balance_tree.delete(*self.balance_tree.get_children())
        for member, net in aa["balances"].items():
            self.balance_tree.insert("", "end", values=(member, money(net), "应收" if net > 0 else "应付" if net < 0 else "已平衡"))
        self.transfer_text.configure(state="normal")
        self.transfer_text.delete("1.0", "end")
        if not self.store.records:
            text = "添加消费并选择分摊成员后，\n这里会显示结算方案。"
        elif not aa["transfers"]:
            text = "目前成员收支已平衡，\n无需转账。"
        else:
            text = "\n\n".join(f"{index}. {item['from']} → {item['to']}\n    ¥ {money(item['amount'])}" for index, item in enumerate(aa["transfers"], 1))
        self.transfer_text.insert("1.0", text)
        self.transfer_text.configure(state="disabled")

    def draw_charts(self) -> None:
        """用 Canvas 绘图，无需安装 matplotlib；数值来自 Decimal 统计。"""
        canvas = self.chart
        canvas.delete("all")
        width = max(canvas.winfo_width(), 600)
        height = max(canvas.winfo_height(), 220)
        if not self.store.records:
            canvas.create_text(width / 2, height / 2, text="暂无支出数据\n添加消费后显示分类与每日支出图表", font=(FONT, 13), fill=self.palette.muted, justify="center")
            return
        # 小额外币折合人民币可能为 0.00，仍需显示其分类和消费日期。
        used = {record["category"] for record in self.store.records}
        categories = {key: value for key, value in self.store.totals()["by_category"].items() if key in used}
        # 左右两个图区，重绘时使用实际可用尺寸。
        split = int(width * 0.56)
        canvas.create_text(0, 12, text="分类支出 / 元", anchor="w", font=(FONT, 11, "bold"), fill=self.palette.text)
        largest = max(categories.values())
        row_height = min(40, (height - 40) / max(len(categories), 1))
        bar_width = max(80, split - 190)
        for index, (category, amount) in enumerate(sorted(categories.items(), key=lambda pair: pair[1], reverse=True)):
            y = 40 + index * row_height
            canvas.create_text(0, y + 9, text=category, anchor="w", font=(FONT, 10), fill=self.palette.text)
            extent = float(amount / largest) * bar_width if largest else 0
            if amount:
                canvas.create_rectangle(50, y, 50 + max(extent, 2), y + 18, fill=COLORS[index % len(COLORS)], outline="")
            canvas.create_text(60 + extent, y + 9, text=money(amount), anchor="w", font=(FONT, 9), fill=self.palette.muted)
        start = split + 25
        period = self.period.get()
        canvas.create_text(start, 12, text=f"按{period}支出 / 元（最近 14 个有记录的期间）", anchor="w", font=(FONT, 10, "bold"), fill=self.palette.text)
        points = list(self.store.period_totals(period).items())[-14:]
        maximum = max(value for _, value in points)
        maximum = maximum if maximum > 0 else Decimal("1")
        left, right, top, bottom = start + 12, width - 15, 62, height - 38
        canvas.create_line(left, top, left, bottom, right, bottom, fill="#cbd5e1")
        canvas.create_text(left, top - 16, text=money(maximum), anchor="w", font=(FONT, 8), fill=self.palette.muted)
        canvas.create_text(left - 4, bottom, text="0", anchor="e", font=(FONT, 8), fill=self.palette.muted)
        xy = []
        for index, (day, amount) in enumerate(points):
            x = left + (right - left) * (index + 0.5) / len(points)
            y = bottom - float(amount / maximum) * (bottom - top)
            xy.extend((x, y))
            if len(points) <= (5 if period == "日" else 3) or index in (0, len(points) // 2, len(points) - 1):
                canvas.create_text(x, bottom + 18, text=day[5:] if period == "日" else day, font=(FONT, 8), fill=self.palette.muted)
        if len(xy) >= 4:
            canvas.create_line(*xy, fill=self.palette.accent, width=2)
        for x, y in zip(xy[::2], xy[1::2]):
            canvas.create_oval(x - 3, y - 3, x + 3, y + 3, fill=self.palette.accent, outline=self.palette.surface)

    def add_record(self) -> None:
        RecordDialog(self)

    def selected_record(self) -> dict | None:
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("请先选择记录", "请在消费明细中单击一条记录。", parent=self.root)
            return None
        return next((record for record in self.store.records if record["id"] == selected[0]), None)

    def edit_record(self) -> None:
        record = self.selected_record()
        if record:
            RecordDialog(self, record)

    def delete_record(self) -> None:
        record = self.selected_record()
        if not record:
            return
        if not messagebox.askyesno("删除消费", f"确定删除“{record['description']}”吗？\n删除后将自动保存。", parent=self.root):
            return
        try:
            self.store.delete_record(record["id"])
        except LedgerError as exc:
            messagebox.showerror("无法删除", str(exc), parent=self.root)
            return
        self.refresh("消费已删除并自动保存")

    def export_csv(self) -> None:
        path = filedialog.asksaveasfilename(parent=self.root, title="导出全部消费记录", defaultextension=".csv", filetypes=[("CSV 表格", "*.csv")], initialfile=f"旅行消费_{date.today().isoformat()}.csv")
        if not path:
            return
        # 避免误将 CSV 覆盖到正在使用的账本文件。
        if Path(path).resolve() == Path(self.store.path).resolve():
            messagebox.showerror("无法导出", "请选择与账本数据文件不同的位置。", parent=self.root)
            return
        try:
            self.store.export_csv(Path(path))
        except LedgerError as exc:
            messagebox.showerror("导出失败", str(exc), parent=self.root)
            return
        self.status.set(f"已导出全部 {len(self.store.records)} 笔记录：{path}")
        messagebox.showinfo("导出成功", f"CSV 文件已保存：\n{path}\n可用 Excel 或其他表格软件打开。", parent=self.root)


class InstanceLock:
    """避免同时打开同一本账造成彼此覆盖；退出后操作系统自动释放锁。"""

    def __init__(self, data_path: Path):
        self.handle = None
        self.path = data_path.with_suffix(data_path.suffix + ".lock")

    def __enter__(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.handle = self.path.open("a+b")
            if self.handle.tell() == 0:
                self.handle.write(b"0")
                self.handle.flush()
            self.handle.seek(0)
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if self.handle:
                self.handle.close()
                self.handle = None
            raise LedgerError("无法打开账本：它可能已在另一个窗口中打开，或此文件夹不可写。") from exc
        return self

    def __exit__(self, *_):
        if self.handle:
            self.handle.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="旅途账本：个人旅行记账软件")
    parser.add_argument("--data", type=Path, help="指定账本 JSON 路径（默认在程序旁的 data 文件夹）")
    args = parser.parse_args()
    # 单文件 EXE 会把程序资源临时解包，账本必须放在可持续保存的位置。
    program_dir = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
    data_path = (args.data or program_dir / "data" / "travel_ledger.json").resolve()
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(f"无法启动图形界面：{exc}\n请使用带有 Tkinter 的桌面版 Python。", file=sys.stderr)
        return 1
    root.withdraw()
    icon = Path(__file__).resolve().parent / "assets" / "travel-ledger.ico"
    if sys.platform == "win32" and icon.is_file():
        root.iconbitmap(default=str(icon))
    try:
        with InstanceLock(data_path):
            store = LedgerStore(data_path)
            TravelLedgerApp(root, store)
            root.deiconify()
            root.mainloop()
    except LedgerError as exc:
        messagebox.showerror("无法打开旅途账本", f"{exc}\n\n数据文件：{data_path}\n请保留原数据文件，检查文件权限或数据格式后重试。", parent=root)
        return 1
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
