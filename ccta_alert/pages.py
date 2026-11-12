"""Các màn hình quản lý: Thống kê, Quy tắc."""
from __future__ import annotations

import tkinter as tk
from collections import Counter
from datetime import date, datetime, timedelta
from tkinter import ttk
from typing import TYPE_CHECKING

from . import rules
from . import theme as T
from .data import MACHINE, add_months, store
from .rules import RiskLevel
from .theme import px
from .widgets import (Card, FlatButton, KpiCard, RfClChart, ScrollFrame, SegmentedControl, StackBarChart,
                      chip, label, make_tree)

if TYPE_CHECKING:
    from .app import ManagerWindow


def page_header(parent, title: str, sub: str) -> tuple[tk.Frame, tk.Label, tk.Frame]:
    head = tk.Frame(parent, bg=T.BG)
    head.pack(fill="x", pady=(0, px(14)))
    left = tk.Frame(head, bg=T.BG)
    left.pack(side="left", anchor="sw")
    label(left, title, T.H1).pack(anchor="w")
    sub_lbl = label(left, sub, T.FONT, T.MUTED)
    sub_lbl.pack(anchor="w")
    right = tk.Frame(head, bg=T.BG)
    right.pack(side="right", anchor="se")
    return head, sub_lbl, right


# ============================================================================ THỐNG KÊ
class StatsPage(tk.Frame):
    def __init__(self, parent, main: "ManagerWindow"):
        super().__init__(parent, bg=T.BG)
        self.main = main
        self.period = 0           # 0 ngày, 1 tháng, 2 năm
        self.ref = date.today()
        self.shown_count = -1
        sf = ScrollFrame(self)
        sf.pack(fill="both", expand=True)
        root = tk.Frame(sf.inner, bg=T.BG)
        root.pack(fill="both", expand=True, padx=px(24), pady=px(18))

        _, self.live, right = page_header(root, "Thống kê cảnh báo", "")
        self.live.configure(fg=T.SUCCESS)
        SegmentedControl(right, ["Ngày", "Tháng", "Năm"], self._set_period).pack(side="left", padx=(0, px(10)))
        nav = tk.Frame(right, bg=T.LINE, padx=1, pady=1)
        nav.pack(side="left", padx=(0, px(10)))
        FlatButton(nav, "", lambda: self._step(-1), font=T.ICON_SMALL, padx=8, pady=6).pack(side="left")
        self.ref_lbl = tk.Label(nav, text="", font=T.BOLD, bg=T.SURFACE, fg=T.FG, width=11, pady=px(4))
        self.ref_lbl.pack(side="left", padx=1)
        FlatButton(nav, "", lambda: self._step(1), font=T.ICON_SMALL, padx=8, pady=6).pack(side="left")
        FlatButton(right, "Hôm nay", self._today, padx=10, pady=4).pack(side="left", padx=(0, px(10)))
        self.cb_clinic = ttk.Combobox(right, state="readonly", width=20, font=T.FONT, values=["Tất cả phòng khám"] + store.clinic_names())
        self.cb_clinic.current(0)
        self.cb_clinic.bind("<<ComboboxSelected>>", lambda e: self.reload())
        self.cb_clinic.pack(side="left")

        # Nguồn dữ liệu + gộp dữ liệu từ các máy khác
        src = tk.Frame(root, bg=T.BG)
        src.pack(fill="x", pady=(0, px(12)))
        self.src_lbl = label(src, "", T.SMALL, T.MUTED)
        self.src_lbl.pack(side="left")
        FlatButton(src, "  Gộp dữ liệu từ máy khác…", self._merge, padx=10, pady=4).pack(side="right")

        kpis = tk.Frame(root, bg=T.BG)
        kpis.pack(fill="x", pady=(0, px(12)))
        self.k = [KpiCard(kpis, "Tổng cảnh báo"), KpiCard(kpis, "Được chụp", T.SUCCESS),
                  KpiCard(kpis, "Không được chụp", T.DANGER), KpiCard(kpis, "Tỷ lệ được chụp")]
        for i, k in enumerate(self.k):
            k.grid(row=0, column=i, sticky="nsew", padx=(0, px(12) if i < 3 else 0))
            kpis.columnconfigure(i, weight=1, uniform="kpi")

        self.c_chart = Card(root, "Biểu đồ")
        self.c_chart.pack(fill="x", pady=(0, px(12)))
        self.chart = StackBarChart(self.c_chart.body, px(230))
        self.chart.pack(fill="x")

        mid = tk.Frame(root, bg=T.BG)
        mid.pack(fill="x", pady=(0, px(12)))
        mid.columnconfigure(0, weight=58, uniform="mid")
        mid.columnconfigure(1, weight=42, uniform="mid")
        c1 = Card(mid, "Theo phòng khám", body_pad=False)
        c1.grid(row=0, column=0, sticky="nsew", padx=(0, px(12)))
        fr, self.t_clinic = make_tree(c1.body, [
            ("c", "Phòng khám", 160, "w", True), ("n", "Cảnh báo", 80, "e", False), ("a", "Được chụp", 90, "e", False),
            ("b", "Không được chụp", 125, "e", False), ("r", "Tỷ lệ chụp", 90, "e", False)], height=5)
        fr.pack(fill="both", expand=True, padx=px(6), pady=px(4))
        c2 = Card(mid, "Lý do không chụp", body_pad=False)
        c2.grid(row=0, column=1, sticky="nsew")
        fr, self.t_reason = make_tree(c2.body, [("r", "Lý do", 220, "w", True), ("n", "Số ca", 70, "e", False)], height=5)
        fr.pack(fill="both", expand=True, padx=px(6), pady=px(4))

        c3 = Card(root, "Nhật ký cảnh báo", body_pad=False)
        c3.pack(fill="x")
        fr, self.t_log = make_tree(c3.body, [
            ("t", "Thời gian", 130, "w", False), ("c", "Phòng khám", 140, "w", False), ("d", "Mã bác sĩ", 110, "w", False),
            ("i", "Mã BN", 105, "w", False), ("x", "Mã điều trị", 110, "w", False), ("p", "Bệnh nhân", 140, "w", False),
            ("l", "Mức cảnh báo", 105, "w", False),
            ("q", "Quyết định", 130, "w", False), ("r", "Lý do không chụp", 180, "w", True)], height=10)
        fr.pack(fill="both", expand=True, padx=px(6), pady=px(4))

        store.subscribe(self.reload)
        self.after(5000, self._tick)
        self.reload()

    # Làm mới định kỳ. Khi nối DB, đây là nơi đọc lại cảnh báo từ các máy trạm khác.
    def _tick(self):
        if len(store.log) != self.shown_count:
            self.reload()
        else:
            self.live.configure(text=self._live_text())
        self.after(5000, self._tick)

    def _live_text(self):
        clinic = self.cb_clinic.get() or "Tất cả phòng khám"
        return f"●  Cập nhật theo thời gian thực · {datetime.now():%H:%M:%S}  ·  {clinic}"

    def _set_period(self, i):
        self.period = i
        self.reload()

    def _today(self):
        self.ref = date.today()
        self.reload()

    def _step(self, n):
        if self.period == 0:
            self.ref += timedelta(days=n)
        elif self.period == 1:
            self.ref = add_months(self.ref, n)
        else:
            self.ref = add_months(self.ref, 12 * n)
        self.reload()

    def _in_period(self, t: datetime) -> bool:
        r = self.ref
        if self.period == 0:
            return t.date() == r
        if self.period == 1:
            return (t.year, t.month) == (r.year, r.month)
        return t.year == r.year

    def _merge(self):
        from tkinter import filedialog, messagebox
        paths = filedialog.askopenfilenames(
            parent=self, title="Chọn file alerts.jsonl của các máy khác (chọn được nhiều file)",
            filetypes=[("Dữ liệu CCTA Alert", "*.jsonl"), ("Tất cả", "*.*")])
        if not paths:
            return
        rep = store.merge_files(list(paths))
        added = sum(r[1] for r in rep)
        lines = []
        for name, a, d, b, err in rep:
            if err:
                lines.append(f"✖ {name}: {err}")
            else:
                lines.append(f"• {name}: {a} bản ghi mới, {d} trùng (bỏ qua)" + (f", {b} dòng hỏng" if b else ""))
        lines.append("")
        lines.append(f"Đã thêm {added} bản ghi vào thống kê của máy này." if added else "Không có bản ghi mới.")
        (messagebox.showwarning if any(r[4] for r in rep) else messagebox.showinfo)(
            "Gộp dữ liệu", "\n".join(lines), parent=self)
        self.reload()

    def reload(self):
        r = self.ref
        ms = store.machines()
        self.src_lbl.configure(text=f"Dữ liệu: {len(store.log)} quyết định"
                               + (f" từ {len(ms)} máy: {', '.join(ms)}" if ms and ms != [MACHINE] else " trên máy này"))
        names = ["Tất cả phòng khám"] + store.clinic_names()      # danh sách phòng có thể đổi (HIS, nhật ký)
        if list(self.cb_clinic.cget("values")) != names:
            cur = self.cb_clinic.get()
            self.cb_clinic.configure(values=names)
            self.cb_clinic.current(names.index(cur) if cur in names else 0)
        clinic = None if self.cb_clinic.current() <= 0 else self.cb_clinic.get()
        L = [e for e in store.log if clinic is None or e.clinic == clinic]
        P = [e for e in L if self._in_period(e.time)]
        done = sum(e.done for e in P)
        self.shown_count = len(store.log)

        self.ref_lbl.configure(text=[f"{r:%d/%m/%Y}", f"{r:%m/%Y}", f"{r:%Y}"][self.period])
        lbl = [f"ngày {r:%d/%m/%Y}", f"tháng {r:%m/%Y}", f"năm {r:%Y}"][self.period]
        self.k[0].set(str(len(P)), "Tổng cảnh báo " + lbl)
        self.k[1].set(str(done))
        self.k[2].set(str(len(P) - done))
        self.k[3].set(f"{round(100 * done / len(P))}%" if P else "—")
        self.live.configure(text=self._live_text())

        # Biểu đồ
        buckets = []
        if self.period == 0:
            self.c_chart.title.configure(text=f"14 ngày đến {r:%d/%m/%Y}")
            for i in range(13, -1, -1):
                d = r - timedelta(days=i)
                buckets.append((f"{d:%d/%m}", [e for e in L if e.time.date() == d]))
        elif self.period == 1:
            self.c_chart.title.configure(text=f"12 tháng đến {r:%m/%Y}")
            for i in range(11, -1, -1):
                d = add_months(r.replace(day=1), -i)
                buckets.append((f"T{d.month}/{d:%y}", [e for e in L if (e.time.year, e.time.month) == (d.year, d.month)]))
        else:
            self.c_chart.title.configure(text=f"5 năm đến {r:%Y}")
            for y in range(r.year - 4, r.year + 1):
                buckets.append((str(y), [e for e in L if e.time.year == y]))
        self.chart.set_data([b[0] for b in buckets], [sum(e.done for e in b[1]) for b in buckets],
                            [sum(not e.done for e in b[1]) for b in buckets])

        # Theo phòng khám
        self.t_clinic.delete(*self.t_clinic.get_children())
        for c in store.clinic_names():
            if clinic and c != clinic:
                continue
            s = [e for e in store.log if e.clinic == c and self._in_period(e.time)]
            if not s and c != clinic:                   # HIS có nhiều phòng: chỉ hiện phòng có cảnh báo
                continue
            a = sum(e.done for e in s)
            self.t_clinic.insert("", "end", values=(c, len(s), a, len(s) - a, f"{round(100 * a / len(s))}%" if s else "—"))

        # Lý do
        self.t_reason.delete(*self.t_reason.get_children())
        for reason, n in Counter(e.reason for e in P if not e.done).most_common():
            self.t_reason.insert("", "end", values=(reason, n))
        if not self.t_reason.get_children():
            self.t_reason.insert("", "end", values=("Chưa có ca nào.", ""), tags=("muted",))

        # Nhật ký
        self.t_log.delete(*self.t_log.get_children())
        for e in sorted(P, key=lambda x: x.time, reverse=True)[:300]:
            self.t_log.insert("", "end", tags=() if e.done else ("refused",), values=(
                f"{e.time:%d/%m/%Y %H:%M}", e.clinic, e.doctor, e.patient_id, e.treatment_code, e.patient_name,
                f"{rules.LEVEL_SHORT[e.level]} · {e.percent}%",
                "✔  Được chụp" if e.done else "✖  Không được chụp", e.reason))


# ============================================================================ QUY TẮC
class RulesPage(tk.Frame):
    def __init__(self, parent, main: "ManagerWindow"):
        super().__init__(parent, bg=T.BG)
        sf = ScrollFrame(self)
        sf.pack(fill="both", expand=True)
        root = tk.Frame(sf.inner, bg=T.BG)
        root.pack(fill="both", expand=True, padx=px(24), pady=px(18))
        page_header(root, "Quy tắc đánh giá",
                    "Khả năng mắc bệnh mạch vành dựa theo nguy cơ lâm sàng (RF-CL), ESC 2024. "
                    "Ô có viền đậm là lần đánh giá gần nhất.")

        card = Card(root, "Bảng RF-CL (%)")
        card.pack(fill="x", pady=(0, px(12)))
        for lv, text in ((RiskLevel.VERY_LOW, "Rất thấp ≤5%"), (RiskLevel.LOW, "Thấp >5–15%"), (RiskLevel.MODERATE, "Vừa >15–50%")):
            chip(card.header_right, text, T.LEVEL_FG[lv], T.LEVEL_SOFT[lv]).pack(side="left", padx=(px(6), 0))
        self.chart = RfClChart(card.body)
        self.chart.pack(anchor="w", pady=(px(4), px(4)))

        how = Card(root, "Cách tính")
        how.pack(fill="x")
        rows = [
            ("Điểm triệu chứng", "Đau ngực: mỗi đặc điểm 1 điểm (vị trí sau xương ức; khởi phát khi gắng sức hoặc xúc động; "
                                 "giảm khi nghỉ hoặc dùng nitrate trong 5 phút). Khó thở là triệu chứng chính: 2 điểm. Lấy điểm cao hơn."),
            ("Yếu tố nguy cơ", "Tăng huyết áp, đái tháo đường, rối loạn lipid máu, hút thuốc lá, tiền sử gia đình mắc bệnh "
                               "mạch vành sớm. Đếm số yếu tố, chia nhóm 0–1, 2–3, 4–5."),
            ("Tuổi, giới", "Lấy từ HIS. Dưới 30 tuổi dùng nhóm 30–39, trên 80 tuổi dùng nhóm 70–80 và ghi chú trong cảnh báo."),
            ("Vừa (>15%)", "Cảnh báo KHUYẾN NGHỊ CHỤP CT MẠCH VÀNH. Bác sĩ chọn Chỉ định hoặc Không chỉ định (kèm lý do)."),
            ("Thấp (>5–15%)", "Cảnh báo CÂN NHẮC CHỤP CT MẠCH VÀNH. Bác sĩ chọn Chỉ định hoặc Không chỉ định (kèm lý do)."),
            ("Rất thấp (≤5%)", "Thông báo CHƯA CẦN CHỤP. Không tính vào thống kê cảnh báo."),
        ]
        for i, (k, v) in enumerate(rows):
            label(how.body, k, T.BOLD).grid(row=i, column=0, sticky="nw", padx=(0, px(18)), pady=px(4))
            label(how.body, v, T.FONT, justify="left", wraplength=px(780)).grid(row=i, column=1, sticky="w", pady=px(4))

    def refresh(self):
        self.chart.redraw()
