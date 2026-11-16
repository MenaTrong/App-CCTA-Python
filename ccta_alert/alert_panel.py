"""Khung cảnh báo nhỏ, luôn nằm trên cùng ở góc dưới bên phải màn hình (nổi trên HIS toàn màn hình).

Trạng thái:
  - Thu gọn: chỉ thanh tiêu đề (tên bệnh nhân hiện tại).
  - Nhập liệu: bác sĩ chọn điểm triệu chứng (0–3) và số yếu tố nguy cơ (0–5), bấm Xác nhận.
  - Kết quả: cảnh báo theo RF-CL, bác sĩ chọn Chỉ định / Không chỉ định.
  - Nhập mã bác sĩ: bắt buộc khi mở app, trước khi chọn phòng khám; ghi kèm mọi cảnh báo và quyết định.
  - Chọn phòng khám: bắt buộc khi mở app (gõ tìm, ↑↓ Enter), nhớ phòng khám lần trước.
  - HIS (nếu có his.json): đọc danh sách phòng khám và bệnh nhân khám hôm nay tại phòng, CHỈ ĐỌC,
    chạy nền và tự cập nhật định kỳ; bệnh nhân mới đến khi khung đang thu gọn thì tự mở ra.
  - Bệnh nhân đã được chỉ định chụp: hiện dòng báo (ngày, phòng khám, bác sĩ) để không chỉ định lại.
  - Chọn bệnh nhân: ô tìm nhanh (tên / mã BN, gõ không dấu được), ↑↓ Enter để mở, Esc để đóng.
"""
from __future__ import annotations

import ctypes
import time
import tkinter as tk
import unicodedata
from ctypes import wintypes
from pathlib import Path
from datetime import date, datetime
from typing import Callable

from . import rules
from . import theme as T
from .data import (REFUSE_REASONS, AlertRecord, Patient, load_settings, normalize_doctor_code,
                   save_settings, store)
from .rules import Evaluation, RiskLevel
from .theme import px
from .widgets import FlatButton, ScaleBar, SegmentedControl, chip, label

GLYPH = {RiskLevel.MODERATE: "", RiskLevel.LOW: "", RiskLevel.VERY_LOW: ""}
PICK_ROWS = 7          # số dòng tối đa trong danh sách chọn bệnh nhân


def fold(text: str) -> str:
    """Bỏ dấu tiếng Việt, chữ thường: 'Nguyễn Văn Hùng' -> 'nguyen van hung'."""
    text = unicodedata.normalize("NFD", text.replace("đ", "d").replace("Đ", "D"))
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def indication_text(r: AlertRecord) -> str:
    days = (date.today() - r.time.date()).days
    when = f"hôm nay {r.time:%H:%M}" if days == 0 else f"{r.time:%d/%m/%Y}, {days} ngày trước"
    title = "ĐÃ ĐƯỢC CHỈ ĐỊNH CHỤP CT MẠCH VÀNH" if r.done else "ĐÃ QUYẾT ĐỊNH KHÔNG CHỤP CT MẠCH VÀNH"
    meta = " · ".join(x for x in (when, r.clinic, r.doctor and f"mã BS {r.doctor}") if x)
    return f"{title}\n{meta}" + ("" if r.done or not r.reason else f"\nLý do: {r.reason}")


def decision_colors(r: AlertRecord) -> tuple[str, str]:
    """(màu chữ, màu nền) của dải quyết định: xanh = được chụp, đỏ = không chụp."""
    return (T.SUCCESS, "#e3f4ea") if r.done else (T.DANGER, T.DANGER_SOFT)


def ago(t: datetime) -> str:
    m = int((datetime.now() - t).total_seconds() // 60)
    if m < 1:
        return "vừa lưu"
    if m < 60:
        return f"{m} phút trước"
    if m < 24 * 60:
        return f"{m // 60} giờ trước"
    return f"{t:%d/%m %H:%M}"



def work_area() -> tuple[int, int, int, int]:
    """Vùng màn hình không tính thanh tác vụ."""
    try:
        r = wintypes.RECT()
        ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0)  # SPI_GETWORKAREA
        return r.left, r.top, r.right, r.bottom
    except Exception:
        return 0, 0, 1920, 1040


class AlertPanel(tk.Tk):
    WIDTH = 384

    def __init__(self, open_manager: Callable[["AlertPanel", int], None]):
        super().__init__()
        T.setup(self)
        self.open_manager = open_manager
        self.title("CCTA Alert")
        try:   # icon chung cho mọi cửa sổ (Quản lý, hộp thoại); trong exe nằm ở _MEIPASS/ccta_alert/assets
            self.iconbitmap(default=str(Path(__file__).with_name("assets") / "ccta.ico"))
        except tk.TclError:
            pass
        self.overrideredirect(True)         # không viền, không chiếm thanh tác vụ
        self.attributes("-topmost", True)
        self.configure(bg=T.ACCENT)          # viền 1px
        self.patient: Patient | None = None
        self.doctor: str | None = None       # mã bác sĩ, bắt buộc nhập khi mở app (trước khi chọn phòng khám)
        self.clinic: str | None = None       # phòng khám đang làm việc, bắt buộc chọn khi mở app
        self.clinic_id: int | None = None    # mã phòng trên HIS
        self._overlay_prev: tk.Frame | None = None
        self.ev: Evaluation | None = None
        self.expanded = True
        self.margin = (px(16), px(16))       # khoảng cách tới mép phải / mép dưới vùng làm việc
        self._drag = None
        self._job = None
        self.wrap = px(self.WIDTH - 36)

        self.frame = tk.Frame(self, bg=T.SURFACE)
        self.frame.pack(fill="both", expand=True, padx=1, pady=1)
        self._build_header()
        self.body = tk.Frame(self.frame, bg=T.SURFACE)
        self.body.pack(fill="both", expand=True)
        self._build_form()
        self._build_picker()
        self._build_clinic()
        self._build_doctor()
        self.view: tk.Frame = self.form
        self.form.pack(fill="both", expand=True)
        self._keep_on_top()
        self._his_init()

    # ================================================================== tiêu đề
    def _build_header(self):
        h = tk.Frame(self.frame, bg=T.ACCENT)
        h.pack(fill="x")
        self.header = h
        icon = tk.Label(h, text="", font=T.ICON, fg="white", bg=T.ACCENT)
        icon.pack(side="left", padx=(px(12), px(8)), pady=px(8))
        txt = tk.Frame(h, bg=T.ACCENT)
        txt.pack(side="left", fill="x", expand=True)
        self.h_title = tk.Label(txt, text="CCTA Alert", font=T.BOLD, fg="white", bg=T.ACCENT, anchor="w", width=1)
        self.h_title.pack(fill="x")
        self.h_sub = tk.Label(txt, text="", font=T.SMALL, fg="#cfe7f0", bg=T.ACCENT, anchor="w", width=1)
        self.h_sub.pack(fill="x")
        self.h_toggle = self._hbtn(h, "", self.toggle)
        self.h_menu = self._hbtn(h, "", self._menu)
        self.h_pick = self._hbtn(h, "\uE716", self.open_picker)   # chọn bệnh nhân
        for w in (h, icon, txt, self.h_title, self.h_sub):
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
            w.bind("<ButtonRelease-1>", self._drag_end)
            w.configure(cursor="fleur")

    def _hbtn(self, parent, glyph, cmd):
        b = tk.Label(parent, text=glyph, font=T.ICON_SMALL, fg="white", bg=T.ACCENT, width=4, cursor="hand2")
        b.pack(side="right", fill="y")
        b.bind("<Enter>", lambda e: b.configure(bg=T.ACCENT_HOVER))
        b.bind("<Leave>", lambda e: b.configure(bg=T.ACCENT))
        b.bind("<ButtonRelease-1>", lambda e: cmd())
        return b

    def _drag_start(self, e):
        self._drag = (e.x_root, e.y_root, self.winfo_x(), self.winfo_y(), False)

    def _drag_move(self, e):
        if not self._drag:
            return
        x0, y0, wx, wy, _ = self._drag
        dx, dy = e.x_root - x0, e.y_root - y0
        if abs(dx) + abs(dy) > 3:
            self._drag = (x0, y0, wx, wy, True)
            self.geometry(f"+{wx + dx}+{wy + dy}")

    def _drag_end(self, e):
        if self._drag and self._drag[4]:          # đã kéo: ghi nhớ vị trí theo góc dưới phải
            _, _, r, b = work_area()
            self.margin = (r - self.winfo_x() - self.winfo_width(), b - self.winfo_y() - self.winfo_height())
        elif self._drag:                          # bấm, không kéo: thu gọn / mở rộng
            self.toggle()
        self._drag = None

    def _menu(self):
        m = tk.Menu(self, tearoff=0, font=T.FONT)
        m.add_command(label="Chọn bệnh nhân…", command=self.open_picker)
        m.add_command(label=f"Đổi bác sĩ ({self.doctor})…", command=self.open_doctor)
        m.add_command(label=f"Đổi phòng khám ({self.clinic})…", command=self.open_clinic)
        m.add_separator()
        m.add_command(label="Thống kê", command=lambda: self.open_manager(self, 0))
        m.add_command(label="Xuất báo cáo", command=lambda: self.open_manager(self, 1))
        m.add_command(label="Quy tắc đánh giá", command=lambda: self.open_manager(self, 2))
        m.add_command(label="Kết nối HIS", command=lambda: self.open_manager(self, 3))
        m.add_separator()
        m.add_command(label="Thoát", command=self.destroy)

        # Đặt menu sát bên trái khung (ngang thanh tiêu đề) để không đè lên khung;
        # nếu bên trái không đủ chỗ thì đặt sang bên phải.
        m.update_idletasks()
        mw = m.winfo_reqwidth()
        left, _, right, _ = work_area()
        x = self.winfo_rootx() - mw - px(20)
        if x < left:
            x = min(self.winfo_rootx() + self.winfo_width() + px(4), right - mw)
        y = self.winfo_rooty()

        # Tạm bỏ "luôn trên cùng" khi menu đang mở, đóng menu thì bật lại.
        self._menu_open = True
        self.attributes("-topmost", False)
        try:
            m.tk_popup(x, y)
        finally:
            m.grab_release()
            self._menu_open = False
            self.after(200, lambda: self.attributes("-topmost", True))

    # ================================================================== nhập liệu
    def _build_form(self):
        f = tk.Frame(self.body, bg=T.SURFACE)
        self.form = f
        pad = px(16)
        top = tk.Frame(f, bg=T.SURFACE)
        top.pack(fill="x", padx=pad, pady=(px(12), px(4)))
        name_row = tk.Frame(top, bg=T.SURFACE)
        name_row.pack(fill="x")
        self.p_name = label(name_row, "", (T.SEMI, 12))
        self.p_name.pack(side="left")
        sw = label(name_row, "Đổi bệnh nhân", T.SMALL, T.ACCENT, cursor="hand2")
        sw.pack(side="right")
        sw.bind("<Button-1>", lambda e: self.open_picker())
        self.name_row = name_row
        # nhãn "đã thực hiện dịch vụ" (theo danh sách mã của tab Xuất báo cáo): cạnh tên, hẹp thì xuống dòng
        self.svc_row = tk.Frame(top, bg=T.SURFACE)
        self.p_svc = tk.Label(top, text="", font=T.SMALL_BOLD, padx=px(6), pady=px(1), cursor="hand2")
        self.p_svc.bind("<Button-1>", lambda e: self._svc_toggle())
        self.p_meta = label(top, "", T.SMALL, T.MUTED)
        self.p_meta.pack(anchor="w")
        self.p_svc_detail = label(top, "", T.SMALL, T.FG, justify="left", anchor="w", wraplength=self.wrap)
        self._svc_open = False
        self.p_done = self._done_banner(top)

        self.sym_chip = self._section(f, "ĐIỂM TRIỆU CHỨNG")
        self.sym_pick = self._picker(f, 4)
        self.rf_chip = self._section(f, "SỐ YẾU TỐ NGUY CƠ")
        self.rf_pick = self._picker(f, 6)

        self.err = label(f, "", T.SMALL, T.DANGER, justify="left", wraplength=self.wrap)
        self._err_kind = ""                  # "counts": chưa chọn đủ; "missing": HIS thiếu ngày sinh / giới tính
        act = tk.Frame(f, bg=T.SURFACE)
        act.pack(fill="x", padx=pad, pady=(px(14), px(14)), side="bottom")
        self.err_anchor = act
        FlatButton(act, "Xác nhận", self.confirm, primary=True, font=(T.SEMI, 11), pady=7).pack(side="right")
        clr = label(act, "Chọn lại", T.SMALL, T.ACCENT, cursor="hand2")
        clr.pack(side="left")
        clr.bind("<Button-1>", lambda e: self._set_counts(None, None))

    def _done_banner(self, parent, r: AlertRecord | None = None) -> tk.Label:
        """Dải báo quyết định gần nhất (được chụp / không chụp). Không gói vào layout; người gọi tự pack."""
        fg, bg = decision_colors(r) if r else (T.SUCCESS, "#e3f4ea")
        return tk.Label(parent, text=indication_text(r) if r else "", font=T.SMALL_BOLD, fg=fg,
                        bg=bg, justify="left", anchor="w", wraplength=self.wrap - px(12),
                        padx=px(8), pady=px(4))

    def _section(self, parent, text) -> tk.Label:
        row = tk.Frame(parent, bg=T.SURFACE)
        row.pack(fill="x", padx=px(16), pady=(px(12), px(4)))
        label(row, text, T.SMALL_BOLD, T.MUTED).pack(side="left")
        c = chip(row, "")
        c.pack(side="right")
        return c

    def _picker(self, parent, n: int) -> SegmentedControl:
        box = tk.Frame(parent, bg=T.SURFACE)
        box.pack(fill="x", padx=px(16))
        seg = SegmentedControl(box, [str(i) for i in range(n)], lambda i: self._changed(), selected=None,
                               font=(T.SEMI, 12), padx=16 if n <= 4 else 12, pady=4)
        seg.pack(anchor="w")
        return seg

    def _set_counts(self, sym, rf):
        self.sym_pick.select(sym)
        self.rf_pick.select(rf)
        self._changed()

    def _changed(self):
        p = self.patient
        if not p:
            return
        p.symptom_score, p.risk_factor_count = self.sym_pick.index, self.rf_pick.index
        s, n = p.symptom_score, p.risk_factor_count
        self.sym_chip.configure(text="Chưa chọn" if s is None else f"Cột {rules.SYM_GROUPS[rules.sym_group(s)]}")
        self.rf_chip.configure(text="Chưa chọn" if n is None else f"Nhóm {rules.RF_GROUPS[rules.rf_group(n)]}")
        if s is not None and n is not None and self.err.winfo_ismapped() and self._err_kind == "counts":
            self.err.pack_forget()
            self._place()

    def _search_box(self, parent, var: tk.StringVar, on_move, on_enter, on_esc, icon="\uE721") -> tk.Entry:
        box = tk.Frame(parent, bg=T.SURFACE, highlightthickness=1, highlightbackground=T.LINE, highlightcolor=T.ACCENT)
        box.pack(fill="x", padx=px(16), pady=(px(4), px(4)))
        tk.Label(box, text=icon, font=T.ICON_SMALL, fg=T.MUTED, bg=T.SURFACE).pack(side="left", padx=(px(8), 0))
        e = tk.Entry(box, textvariable=var, font=T.FONT, relief="flat", bd=0, bg=T.SURFACE, fg=T.FG,
                     insertbackground=T.FG)
        e.pack(side="left", fill="x", expand=True, padx=px(6), ipady=px(5))
        e.bind("<Down>", lambda ev: on_move(1))
        e.bind("<Up>", lambda ev: on_move(-1))
        e.bind("<Return>", lambda ev: on_enter())
        e.bind("<Escape>", lambda ev: on_esc())
        e.bind("<FocusIn>", lambda ev: box.configure(highlightbackground=T.ACCENT))
        e.bind("<FocusOut>", lambda ev: box.configure(highlightbackground=T.LINE))
        return e

    # ================================================================== chọn bệnh nhân
    def _build_picker(self):
        f = tk.Frame(self.body, bg=T.SURFACE)
        self.picker = f
        self._pick_rows: list[tuple[Patient, tk.Frame]] = []
        self._pick_i = 0
        pad = px(16)

        tk.Frame(f, bg=T.SURFACE, height=px(8)).pack(fill="x")
        self.q = tk.StringVar()
        self.q_entry = self._search_box(f, self.q, self._pick_move, self._pick_enter, self.close_picker)
        self.q.trace_add("write", lambda *_: self._filter())

        hint = tk.Frame(f, bg=T.SURFACE)
        hint.pack(fill="x", padx=pad, pady=(0, px(6)))
        label(hint, "Gõ tên, mã BN, mã điều trị  ·  ↑↓  ·  Enter", T.SMALL, T.MUTED).pack(side="left")
        close = label(hint, "Đóng", T.SMALL, T.ACCENT, cursor="hand2")
        close.pack(side="right")
        close.bind("<Button-1>", lambda e: self.close_picker())

        self.pick_his = label(f, "", T.SMALL, T.MUTED, justify="left", wraplength=self.wrap)   # trạng thái HIS
        self.pick_sep = tk.Frame(f, bg=T.LINE, height=1)
        self.pick_sep.pack(fill="x")
        self.pick_list = tk.Frame(f, bg=T.SURFACE)
        self.pick_list.pack(fill="x")
        self.pick_more = label(f, "", T.SMALL, T.MUTED, justify="left", wraplength=self.wrap)
        self.pick_more.bind("<Button-1>", lambda e: self._pick_more_click())
        self.pick_more.pack(anchor="w", padx=pad, pady=(px(4), px(10)))

    def _filter(self):
        words = fold(self.q.get()).split()
        rows = sorted(store.patients, key=lambda p: p.saved_at, reverse=True)   # vào khám gần nhất lên đầu
        if words:
            rows = [p for p in rows if all(w in fold(f"{p.name} {p.id} {p.treatment_code}") for w in words)]
        for w in self.pick_list.winfo_children():
            w.destroy()
        self._pick_rows = [(p, self._pick_row(p, k)) for k, p in enumerate(rows[:PICK_ROWS])]
        rest = len(rows) - PICK_ROWS
        setup = not store.his                   # chưa cấu hình HIS: bấm để mở tab Kết nối HIS
        msg = ("Chưa cấu hình kết nối HIS. Bấm vào đây để mở Kết nối HIS  ›" if setup
               else "Không tìm thấy bệnh nhân." if not rows and words
               else "Chưa có bệnh nhân nào khám tại phòng này hôm nay." if not rows
               else f"Còn {rest} bệnh nhân khác, gõ thêm để lọc." if rest > 0 else "")
        if store.his and store.his_ok is not True:
            self.pick_his.configure(text=store.his_status, fg=T.DANGER if store.his_ok is False else T.MUTED)
            self.pick_his.pack(anchor="w", padx=px(16), pady=(0, px(6)), before=self.pick_sep)
        else:
            self.pick_his.pack_forget()
        self.pick_more.configure(text=msg, fg=T.ACCENT if setup else T.MUTED, cursor="hand2" if setup else "")
        cur = next((k for k, (p, _) in enumerate(self._pick_rows) if p is self.patient), None)
        self._pick_hl(0 if words or cur is None else cur)
        if self.view is self.picker:
            self._place()

    def _pick_row(self, p: Patient, k: int) -> tk.Frame:
        r = tk.Frame(self.pick_list, bg=T.SURFACE, cursor="hand2")
        r.pack(fill="x")
        r.bar = tk.Frame(r, bg=T.SURFACE, width=px(3))
        r.bar.pack(side="left", fill="y")
        left = tk.Frame(r, bg=T.SURFACE)
        left.pack(side="left", fill="x", expand=True, padx=(px(13), 0), pady=px(5))
        label(left, p.name, T.BOLD, anchor="w").pack(fill="x")
        label(left, f"{p.codes_text}  ·  {p.age_text}  ·  {p.sex}", T.SMALL, T.MUTED, anchor="w").pack(fill="x")
        right = tk.Frame(r, bg=T.SURFACE)
        right.pack(side="right", padx=(0, px(16)))
        label(right, ago(p.saved_at), T.SMALL, T.MUTED).pack(anchor="e")
        if p is self.patient:
            label(right, "Đang mở", T.SMALL_BOLD, T.ACCENT).pack(anchor="e")
        elif (dec := store.last_decision(p.id)) is not None:
            label(right, "Đã chỉ định chụp" if dec.done else "Không chụp", T.SMALL_BOLD,
                  T.SUCCESS if dec.done else T.DANGER).pack(anchor="e")
        stack = [r]
        while stack:
            w = stack.pop()
            w.bind("<Button-1>", lambda e, p=p: self.load_patient(p, pop=True))
            w.bind("<Enter>", lambda e, k=k: self._pick_hl(k))
            stack.extend(w.winfo_children())
        return r

    @staticmethod
    def _paint(rows: list[tk.Frame], i: int):
        """Tô nền dòng đang chọn (vạch màu bên trái) trong một danh sách dòng."""
        for k, r in enumerate(rows):
            bg = T.ACCENT_SOFT if k == i else T.SURFACE
            stack = [r]
            while stack:
                w = stack.pop()
                w.configure(bg=bg)
                stack.extend(w.winfo_children())
            r.bar.configure(bg=T.ACCENT if k == i else bg)

    def _pick_hl(self, i: int):
        if not self._pick_rows:
            return
        self._pick_i = max(0, min(i, len(self._pick_rows) - 1))
        self._paint([r for _, r in self._pick_rows], self._pick_i)

    def _pick_more_click(self):
        if not store.his:
            self.open_manager(self, 3)          # tab Kết nối HIS

    def _pick_move(self, d: int):
        self._pick_hl(self._pick_i + d)
        return "break"

    def _pick_enter(self):
        if self._pick_rows:
            self.load_patient(self._pick_rows[self._pick_i][0], pop=True)

    def open_picker(self):
        """Mở danh sách chọn bệnh nhân ngay trong khung, con trỏ đặt sẵn ở ô tìm."""
        if self.doctor is None:
            self.open_doctor()
            return
        if self.clinic is None:
            self.open_clinic()
            return
        self._overlay(self.picker)
        self.q.set("")
        self._filter()
        self._his_refresh(min_age=10)          # mở danh sách thì đọc lại HIS nếu đã hơn 10 giây
        self.expand()
        self.focus_force()
        self.q_entry.focus_set()

    def close_picker(self):
        if self.view is self.picker:
            self._leave_overlay()
            self._place()

    # ------------------------------------------------------------------ lớp phủ (chọn BN / chọn phòng khám)
    def _overlay(self, frame: tk.Frame):
        """Che màn đang xem bằng `frame`, giữ nguyên màn cũ để quay lại khi đóng."""
        if self.view is frame:
            return
        if self.view in (self.picker, self.clinic_view, self.doctor_view):
            self._leave_overlay()
        self._overlay_prev = self.view
        self.view.pack_forget()
        self.view = frame
        frame.pack(fill="both", expand=True)

    def _leave_overlay(self):
        self.view.pack_forget()
        self.view = self._overlay_prev or self.form
        self.view.pack(fill="both", expand=True)
        self._overlay_prev = None

    # ================================================================== chọn phòng khám
    def _build_clinic(self):
        f = tk.Frame(self.body, bg=T.SURFACE)
        self.clinic_view = f
        pad = px(16)
        top = tk.Frame(f, bg=T.SURFACE)
        top.pack(fill="x", padx=pad, pady=(px(12), px(6)))
        label(top, "Chọn phòng khám", (T.SEMI, 12)).pack(anchor="w")
        self.c_sub = label(top, "", T.SMALL, T.MUTED, justify="left", wraplength=self.wrap)
        self.c_sub.pack(anchor="w")
        self.cq = tk.StringVar()
        self.cq_entry = self._search_box(f, self.cq, lambda d: self._clinic_hl(self._clinic_i + d),
                                         self._clinic_enter, self.close_clinic)
        self.cq.trace_add("write", lambda *_: self._clinic_filter())
        tk.Frame(f, bg=T.LINE, height=1).pack(fill="x", pady=(px(4), 0))
        self.c_list = tk.Frame(f, bg=T.SURFACE)
        self.c_list.pack(fill="x")
        self.c_msg = label(f, "", T.SMALL, T.MUTED, justify="left", wraplength=self.wrap)
        self.c_msg.bind("<Button-1>", lambda e: self._clinic_retry())
        foot = tk.Frame(f, bg=T.SURFACE)
        foot.pack(fill="x", padx=pad, pady=(px(8), px(12)), side="bottom")
        label(foot, "Gõ tên phòng  ·  ↑↓ chọn  ·  Enter", T.SMALL, T.MUTED).pack(side="left")
        self.c_cancel = label(foot, "Hủy", T.SMALL, T.ACCENT, cursor="hand2")
        self.c_cancel.bind("<Button-1>", lambda e: self.close_clinic())
        st = load_settings()
        self.last_clinic, self.last_clinic_id = st.get("clinic"), st.get("clinic_id")
        self.clinic_rows: list[tuple[tuple[int | None, str], tk.Frame]] = []
        self._clinic_i = 0

    def _is_last(self, rid, name) -> bool:
        return rid == self.last_clinic_id if self.last_clinic_id is not None else name == self.last_clinic

    def _is_current(self, rid, name) -> bool:
        return self.clinic is not None and (rid, name) == (self.clinic_id, self.clinic)

    def _clinic_filter(self):
        words = fold(self.cq.get()).split()
        # đang chọn / lần trước lên đầu, còn lại giữ thứ tự (theo tên)
        items = sorted(store.clinics, key=lambda c: 0 if self._is_current(*c) else 1 if self._is_last(*c) else 2)
        if words:
            items = [c for c in items if all(w in fold(f"{c[1]} {c[0] if c[0] is not None else ''}") for w in words)]
        for w in self.c_list.winfo_children():
            w.destroy()
        self.clinic_rows = [(c, self._clinic_row(c, k)) for k, c in enumerate(items[:PICK_ROWS])]
        rest = len(items) - PICK_ROWS
        setup = not store.his                   # chưa cấu hình HIS: bấm để mở tab Kết nối HIS
        retry = store.his and not store.clinics and store.his_ok is False
        if setup:
            msg = "Chưa cấu hình kết nối HIS nên chưa có danh sách phòng khám.\nBấm vào đây để mở Kết nối HIS  ›"
        elif not store.clinics:
            msg = store.his_status + ("\nBấm vào đây để thử lại." if retry else "")
        elif not items:
            msg = "Không tìm thấy phòng khám."
        else:
            msg = f"Còn {rest} phòng khác, gõ thêm để lọc." if rest > 0 else ""
        self.c_msg.configure(text=msg, fg=T.DANGER if retry else T.ACCENT if setup else T.MUTED,
                             cursor="hand2" if retry or setup else "")
        if msg:
            self.c_msg.pack(anchor="w", padx=px(16), pady=(px(6), 0), after=self.c_list)
        else:
            self.c_msg.pack_forget()
        self._clinic_hl(0)
        if self.view is self.clinic_view:
            self._place()

    def _clinic_row(self, c: tuple[int | None, str], k: int) -> tk.Frame:
        rid, name = c
        r = tk.Frame(self.c_list, bg=T.SURFACE, cursor="hand2")
        r.pack(fill="x")
        r.bar = tk.Frame(r, bg=T.SURFACE, width=px(3))
        r.bar.pack(side="left", fill="y")
        mid = tk.Frame(r, bg=T.SURFACE)
        mid.pack(side="left", fill="x", expand=True, padx=(px(13), 0), pady=px(5))
        label(mid, name, T.BOLD, anchor="w").pack(fill="x")
        sub = f"Mã phòng {rid}" if rid is not None else ""
        if sub:
            label(mid, sub, T.SMALL, T.MUTED, anchor="w").pack(fill="x")
        tag = "Đang chọn" if self._is_current(rid, name) else "Lần trước" if self._is_last(rid, name) else ""
        label(r, tag, T.SMALL, T.MUTED).pack(side="right", padx=(0, px(16)))
        stack = [r]
        while stack:
            w = stack.pop()
            w.bind("<Button-1>", lambda e, c=c: self.set_clinic(c[1], c[0]))
            w.bind("<Enter>", lambda e, k=k: self._clinic_hl(k))
            stack.extend(w.winfo_children())
        return r

    def _clinic_hl(self, i: int):
        if not self.clinic_rows:
            return "break"
        self._clinic_i = max(0, min(i, len(self.clinic_rows) - 1))
        self._paint([r for _, r in self.clinic_rows], self._clinic_i)
        return "break"

    def _clinic_enter(self):
        if self.clinic_rows:
            rid, name = self.clinic_rows[self._clinic_i][0]
            self.set_clinic(name, rid)

    def _clinic_retry(self):
        if not store.his:
            self.open_manager(self, 3)          # tab Kết nối HIS
        elif not store.clinics and store.his_ok is False:
            self._his_load_clinics()

    def open_clinic(self):
        """Màn chọn phòng khám. Lần đầu mở app thì bắt buộc (không hủy, không thu gọn được)."""
        if self.doctor is None:                 # nhập mã bác sĩ trước
            self.open_doctor()
            return
        first = self.clinic is None
        self._overlay(self.clinic_view)
        self.c_sub.configure(text="Bắt buộc trước khi bắt đầu. Cảnh báo và quyết định sẽ được ghi cho phòng khám này."
                             if first else f"Đang làm việc tại {self.clinic}.")
        if first:
            self.h_sub.configure(text="Chưa chọn phòng khám")
        self.c_cancel.pack_forget()
        if not first:
            self.c_cancel.pack(side="right")
        self.cq.set("")
        self._clinic_filter()
        if store.his and not store.clinics:
            self._his_load_clinics()
        self.expand()
        self.focus_force()
        self.cq_entry.focus_set()

    def close_clinic(self):
        if self.view is self.clinic_view and self.clinic is not None:
            self._leave_overlay()
            self._place()

    def set_clinic(self, clinic: str, room_id: int | None = None):
        first = self.clinic is None
        changed = (clinic, room_id) != (self.clinic, self.clinic_id)
        self.clinic, self.clinic_id = clinic, room_id
        self.last_clinic, self.last_clinic_id = clinic, room_id
        save_settings(clinic=clinic, clinic_id=room_id)
        self._update_title()
        if self.view is self.clinic_view:
            self._leave_overlay()
        if changed:                             # đổi phòng: bỏ danh sách của phòng cũ, đọc lại từ HIS
            store.patients = []
            self._his_seen = None
            self._his_latest = None
            self._his_refresh(force=True)
        if first or self.patient is None:
            self.open_picker()                  # danh sách hiện ra khi HIS trả kết quả
        self._place()

    # ================================================================== nhập mã bác sĩ
    def _build_doctor(self):
        f = tk.Frame(self.body, bg=T.SURFACE)
        self.doctor_view = f
        pad = px(16)
        top = tk.Frame(f, bg=T.SURFACE)
        top.pack(fill="x", padx=pad, pady=(px(12), px(6)))
        label(top, "Nhập mã bác sĩ", (T.SEMI, 12)).pack(anchor="w")
        self.d_sub = label(top, "", T.SMALL, T.MUTED, justify="left", wraplength=self.wrap)
        self.d_sub.pack(anchor="w")
        self.dq = tk.StringVar()
        self.d_entry = self._search_box(f, self.dq, lambda d: "break", self._doctor_submit, self.close_doctor,
                                        icon="\uE77B")
        self.dq.trace_add("write", lambda *_: self._doctor_typed())
        self.d_err = label(f, "", T.SMALL, T.DANGER, justify="left", wraplength=self.wrap)
        self.d_last = label(f, "", T.SMALL, T.ACCENT, cursor="hand2")
        self.d_last.bind("<Button-1>", lambda e: self.last_doctor and self.set_doctor(self.last_doctor))
        foot = tk.Frame(f, bg=T.SURFACE)
        foot.pack(fill="x", padx=pad, pady=(px(12), px(14)), side="bottom")
        self.d_anchor = foot
        FlatButton(foot, "Tiếp tục", self._doctor_submit, primary=True).pack(side="right")
        self.d_cancel = label(foot, "Hủy", T.SMALL, T.ACCENT, cursor="hand2")
        self.d_cancel.bind("<Button-1>", lambda e: self.close_doctor())
        self.last_doctor = load_settings().get("doctor")

    def _doctor_typed(self):
        if self.d_err.winfo_ismapped():
            self.d_err.pack_forget()
            self._place()

    def open_doctor(self):
        """Màn nhập mã bác sĩ. Lần đầu mở app thì bắt buộc (không hủy, không thu gọn được)."""
        first = self.doctor is None
        self._overlay(self.doctor_view)
        self.d_sub.configure(text="Bắt buộc khi mở app. Mỗi cảnh báo và quyết định sẽ được ghi kèm mã bác sĩ này."
                             if first else f"Đang dùng mã {self.doctor}. Nhập mã khác để đổi bác sĩ.")
        if first:
            self.h_sub.configure(text="Chưa nhập mã bác sĩ")
        self.dq.set("")
        self.d_err.pack_forget()
        # không điền sẵn mã lần trước (máy dùng chung): bác sĩ phải gõ, hoặc bấm chọn mã lần trước
        if self.last_doctor and self.last_doctor != self.doctor:
            self.d_last.configure(text=f"Dùng mã lần trước: {self.last_doctor}  ›")
            self.d_last.pack(anchor="w", padx=px(16), pady=(px(2), 0), before=self.d_anchor)
        else:
            self.d_last.pack_forget()
        self.d_cancel.pack_forget()
        if not first:
            self.d_cancel.pack(side="left")
        self.expand()
        self.focus_force()
        self.d_entry.focus_set()

    def close_doctor(self):
        if self.view is self.doctor_view and self.doctor is not None:
            self._leave_overlay()
            self._place()

    def _doctor_submit(self):
        try:
            code = normalize_doctor_code(self.dq.get())
        except ValueError as e:
            self.d_err.configure(text=str(e))
            self.d_err.pack(anchor="w", padx=px(16), pady=(px(2), 0), before=self.d_anchor)
            self._place()
            self.bell()
            return
        self.set_doctor(code)

    def set_doctor(self, code: str):
        self.doctor = self.last_doctor = code
        save_settings(doctor=code)
        self._update_title()
        if self.view is self.doctor_view:
            self._leave_overlay()
        if self.clinic is None:
            self.open_clinic()                  # bước tiếp theo: chọn phòng khám
        elif self.patient:
            self.h_sub.configure(text=f"{self.patient.name} · {self.patient.age_text} · {self.patient.sex}")
        self._place()

    def _update_title(self):
        parts = [x for x in (self.clinic, self.doctor) if x]
        self.h_title.configure(text="  ·  ".join(parts) if parts else "CCTA Alert")

    # ================================================================== HIS (chỉ đọc, chạy nền)
    def _his_init(self):
        self.his_client = None
        self._his_busy = False
        self._his_last = 0.0
        self._his_seen: set[str] | None = None     # mã BN đã thấy ở phòng hiện tại; None = chưa đọc lần nào
        self._his_latest: datetime | None = None   # START_TIME mới nhất đã thấy ở phòng hiện tại
        self._clinics_loading = False
        if not store.his:
            return
        from . import his
        self._his = his
        try:
            cfg = his.load_config()
            if cfg is None:
                raise ValueError("his.json không bật")
            self.his_client = his.HisClient(cfg)
            self.his_poll_ms = max(5, int(cfg.get("poll_seconds", 10))) * 1000
        except Exception as e:
            store.his_ok, store.his_status = False, f"Lỗi cấu hình his.json: {his.error_text(e)}"
            return
        self.his_worker = his.Worker()
        self._his_pump()
        self.after(self.his_poll_ms, self._his_tick)

    def _his_pump(self):
        try:
            self.his_worker.pump()
        finally:
            self.after(200, self._his_pump)

    def _his_tick(self):
        self._his_refresh()
        self.after(self.his_poll_ms, self._his_tick)

    def _his_load_clinics(self):
        if not self.his_client or self._clinics_loading:
            if store.his and not self.his_client and self.view is self.clinic_view:
                self._clinic_filter()           # hiện lỗi cấu hình
            return
        self._clinics_loading = True
        store.his_ok, store.his_status = None, "Đang tải danh sách phòng khám từ HIS…"
        self._clinic_filter()
        self.his_worker.submit(self.his_client.fetch_clinics, self._his_clinics_done)

    # ---------------------------------------------------- đã thực hiện dịch vụ (lịch sử theo mã bệnh nhân)
    SVC_CACHE_SECONDS = 120

    def _svc_codes(self) -> list[str]:
        from .his import DEFAULT_REPORT_CODES
        return list(load_settings().get("report_service_codes") or DEFAULT_REPORT_CODES)

    def _svc_check(self, p: Patient):
        """Hỏi HIS (chạy nền) bệnh nhân đã từng có y lệnh với mã dịch vụ trong danh sách chưa; có kết quả trong
        SVC_CACHE_SECONDS giây gần đây thì dùng lại, không đọc lại HIS."""
        self._svc_open = False
        if not getattr(self, "his_client", None) or not getattr(self, "his_worker", None):
            self._svc_show(None)
            return
        if not hasattr(self, "_svc_cache"):
            self._svc_cache: dict[tuple, tuple[float, list | None, Exception | None]] = {}
        codes = self._svc_codes()
        key = (p.id, tuple(codes))
        hit = self._svc_cache.get(key)
        if hit and time.monotonic() - hit[0] < self.SVC_CACHE_SECONDS:
            self._svc_render(hit[1], hit[2])
            return
        self._svc_show("Đang kiểm tra…", T.MUTED, T.BG)
        client = self.his_client

        def done(res, err, pid=p.id):
            self._svc_cache[key] = (time.monotonic(), res, err)
            if self.patient is not None and self.patient.id == pid:
                self._svc_render(res, err)
        self.his_worker.submit(lambda: client.fetch_history(p.id, codes), done)

    def _svc_render(self, rows: list | None, err: Exception | None):
        if err is not None:
            self._svc_show("Không kiểm tra được", T.MUTED, T.BG,
                           f"Không đọc được lịch sử dịch vụ từ HIS: {self._his.error_text(err)}")
            return
        if not rows:
            self._svc_show("Chưa thực hiện", T.MUTED, T.BG,
                           "HIS không có y lệnh nào của bệnh nhân này với các mã dịch vụ trong danh sách "
                           "(tab Xuất báo cáo).")
            return
        last = rows[0]
        t = last.get("thoi_gian_bat_dau")
        finished = last.get("thoi_gian_ket_thuc") is not None
        word = "Đã thực hiện" if finished else "Đã chỉ định"
        recent = isinstance(t, datetime) and (datetime.now() - t).days < 730      # trong 2 năm
        fg, bg = (T.WARN, T.WARN_SOFT) if recent else (T.ACCENT, T.ACCENT_SOFT)
        text = f"{'⚠ ' if recent else ''}{word} {t:%d/%m/%Y}" if isinstance(t, datetime) else word
        lines = [f"{len(rows)}{'+' if len(rows) >= 20 else ''} lần, gần nhất trước:"]
        for r in rows[:5]:
            rt = r.get("thoi_gian_bat_dau")
            lines.append(" · ".join(x for x in (
                f"{rt:%d/%m/%Y}" if isinstance(rt, datetime) else "",
                str(r.get("ma_dich_vu") or ""), str(r.get("ten_dich_vu") or "").strip(),
                "" if r.get("thoi_gian_ket_thuc") is not None else "chưa kết thúc") if x))
        if recent:
            lines.append("Trong vòng 2 năm: cân nhắc trước khi chỉ định chụp lại.")
        self._svc_show(text, fg, bg, "\n".join(lines))

    def _svc_show(self, text: str | None, fg=T.MUTED, bg=T.BG, detail: str = ""):
        """text=None: ẩn nhãn (chưa nối HIS)."""
        self.p_svc.pack_forget()
        self.p_svc_detail.pack_forget()
        if text is None:
            return
        self.p_svc.configure(text=text + ("  ▾" if detail else ""), fg=fg, bg=bg,
                             highlightthickness=1, highlightbackground=fg)
        self.p_svc_detail.configure(text=detail)
        self.p_svc.pack(in_=self.name_row, side="left", padx=(px(8), 0), pady=(px(2), 0))
        self.update_idletasks()
        if self.name_row.winfo_reqwidth() > self.wrap:           # tên dài: nhãn xuống dòng dưới tên
            self.p_svc.pack_forget()
            self.p_svc.pack(in_=self.svc_row, side="left")
            self.svc_row.pack(anchor="w", pady=(px(2), 0), after=self.name_row)
        else:
            self.svc_row.pack_forget()
        if self._svc_open and detail:
            self.p_svc_detail.pack(anchor="w", fill="x", pady=(px(4), 0), after=self.p_meta)
        self._place()

    def _svc_toggle(self):
        if not self.p_svc_detail.cget("text"):
            return
        self._svc_open = not self._svc_open
        if self._svc_open:
            self.p_svc_detail.pack(anchor="w", fill="x", pady=(px(4), 0), after=self.p_meta)
        else:
            self.p_svc_detail.pack_forget()
        self._place()

    def _his_clinics_done(self, res, err):
        self._clinics_loading = False
        if err:
            store.his_ok, store.his_status = False, f"Không tải được danh sách phòng khám: {self._his.error_text(err)}"
        else:
            store.clinics = res
            store.his_ok, store.his_status = True, f"đã kết nối · {len(res)} phòng khám"
        if self.view is self.clinic_view:
            self._clinic_filter()

    def _his_refresh(self, force=False, min_age=0.0):
        if not self.his_client or self.clinic_id is None or self._his_busy:
            return
        if not force and time.monotonic() - self._his_last < min_age:
            return
        self._his_busy, self._his_last = True, time.monotonic()
        rid, name, client = self.clinic_id, self.clinic, self.his_client
        if self._his_seen is None:
            store.his_ok, store.his_status = None, "Đang đọc danh sách bệnh nhân từ HIS…"
        self.his_worker.submit(lambda: client.fetch_patients(rid, name),
                               lambda res, err: self._his_patients_done(rid, res, err))

    def _his_patients_done(self, rid, res, err):
        self._his_busy = False
        if rid != self.clinic_id:                 # bác sĩ đã đổi phòng trong lúc chờ
            self._his_refresh(force=True)
            return
        if err:
            store.his_ok, store.his_status = False, f"Không đọc được bệnh nhân từ HIS: {self._his.error_text(err)}"
        else:
            new = store.update_patients(res)
            store.his_ok, store.his_status = True, f"đã kết nối · cập nhật {datetime.now():%H:%M:%S}"
            first_load = self._his_seen is None
            self._his_seen = {p.id for p in store.patients}
            top = max(store.patients, key=lambda p: p.saved_at, default=None)
            newer = top is not None and (self._his_latest is None or top.saved_at > self._his_latest)
            menu_open = getattr(self, "_menu_open", False)   # đang mở danh sách/menu: để lần đọc sau mới chuyển
            if newer and not menu_open:
                self._his_latest = top.saved_at
            if top is None or menu_open:
                pass
            elif first_load:
                if self.patient is None:
                    self.load_patient(top, pop=True)
            elif newer and (self.patient is None or top.id != self.patient.id) \
                    and self.view not in (self.clinic_view, self.doctor_view):
                # START_TIME mới nhất = bác sĩ vừa bấm bắt đầu xử lý bệnh nhân này trên HIS: chuyển sang ngay.
                # Lựa chọn đang nhập dở của bệnh nhân trước vẫn giữ, mở lại bệnh nhân đó là thấy.
                self.load_patient(top, pop=True)
        if self.view is self.picker:
            self._filter()

    # ================================================================== API
    def load_patient(self, p: Patient, pop=False):
        """Gọi khi HIS lưu hồ sơ bệnh nhân. pop=True: mở rộng khung để bác sĩ nhập."""
        if self.doctor is None or self.clinic is None:   # chưa nhập mã BS / chọn phòng khám
            self.open_clinic()
            return
        self.patient = p
        self.p_name.configure(text=p.name)
        self.p_meta.configure(text=f"{p.codes_text}  ·  {p.age_text}  ·  {p.sex}")
        done = store.last_decision(p.id)
        if done:
            fg, bg = decision_colors(done)
            self.p_done.configure(text=indication_text(done), fg=fg, bg=bg)
            self.p_done.pack(anchor="w", fill="x", pady=(px(6), 0))
        else:
            self.p_done.pack_forget()
        self.err.pack_forget()
        self._err_kind = ""
        if p.missing:                           # HIS thiếu dữ liệu: báo ngay, không tính được RF-CL
            self._show_err("missing", f"HIS chưa có {p.missing} của bệnh nhân này nên chưa tính được RF-CL. "
                                      "Cập nhật trên HIS rồi mở lại bệnh nhân.", ring=False)
        self._set_counts(p.symptom_score, p.risk_factor_count)
        self._svc_check(p)
        self.h_sub.configure(text=f"{p.name} · {p.age_text} · {p.sex}")
        self._show(self.form)
        if pop:
            self.expand()
        self._place()

    def confirm(self):
        p = self.patient
        if not p:
            return
        if p.missing:
            self._show_err("missing", f"HIS chưa có {p.missing} của bệnh nhân này nên chưa tính được RF-CL. "
                                      "Cập nhật trên HIS rồi mở lại bệnh nhân.")
            return
        if p.symptom_score is None or p.risk_factor_count is None:
            self._show_err("counts", "Chọn điểm triệu chứng và số yếu tố nguy cơ trước khi xác nhận.")
            return
        self.ev = rules.evaluate(p.age, p.male, p.symptom_score, p.risk_factor_count)
        if self.ev.level == RiskLevel.VERY_LOW:     # không cần quyết định, nhưng ghi lại là bác sĩ đã đánh giá
            store.add_assessed(AlertRecord(datetime.now(), self.clinic, self.doctor, p.id, p.name, self.ev.level,
                                           self.ev.percent, False, "", p.treatment_code))
        self._show(self._build_result(self.ev))
        self._sound(self.ev.level)

    def _show_err(self, kind: str, text: str, ring=True):
        self._err_kind = kind
        self.err.configure(text=text)
        self.err.pack(anchor="w", padx=px(16), pady=(px(10), 0), before=self.err_anchor)
        self._place()
        if ring:
            self.bell()

    # ================================================================== kết quả
    def _build_result(self, ev: Evaluation) -> tk.Frame:
        p = self.patient
        color, soft = T.LEVEL_FG[ev.level], T.LEVEL_SOFT[ev.level]
        f = tk.Frame(self.body, bg=T.SURFACE)
        band = tk.Frame(f, bg=soft)
        band.pack(fill="x")
        tk.Frame(band, bg=color, width=px(5)).pack(side="left", fill="y")
        d = px(38)
        ic = tk.Canvas(band, width=d, height=d, bg=soft, highlightthickness=0)
        ic.create_oval(1, 1, d - 1, d - 1, fill=color, width=0)
        ic.create_text(d / 2, d / 2, text=GLYPH[ev.level], font=(T.ICON[0], 15), fill="white")
        ic.pack(side="left", padx=(px(12), px(10)), pady=px(12))
        tx = tk.Frame(band, bg=soft)
        tx.pack(side="left", fill="x", expand=True, pady=px(10))
        label(tx, rules.TITLE[ev.level], (T.UI, 11, "bold"), color, justify="left").pack(anchor="w")
        label(tx, f"{p.name} · {p.age_text} · {p.sex}", T.SMALL, T.FG).pack(anchor="w")

        body = tk.Frame(f, bg=T.SURFACE)
        body.pack(fill="x", padx=px(16), pady=(px(10), 0))
        row = tk.Frame(body, bg=T.SURFACE)
        row.pack(anchor="w", fill="x")
        label(row, f"{ev.percent}%", (T.SEMI, 24), color).pack(side="left", padx=(0, px(10)))
        info = tk.Frame(row, bg=T.SURFACE)
        info.pack(side="left")
        label(info, "Khả năng mắc BMV (RF-CL, ESC 2024)", T.SMALL, T.MUTED).pack(anchor="w")
        label(info, "Nguy cơ: " + rules.LEVEL_NAME[ev.level], T.BOLD).pack(anchor="w")
        ScaleBar(body, ev.percent, self.wrap).pack(anchor="w", pady=(px(6), px(4)))

        basis = (f"{p.sex} {rules.AGE_BANDS[ev.age_band]} tuổi  ·  triệu chứng {ev.symptom_score} điểm  ·  "
                 f"{ev.risk_factor_count} yếu tố nguy cơ")
        label(body, basis, T.SMALL, T.MUTED, justify="left", wraplength=self.wrap).pack(anchor="w")
        for n in ev.notes:
            label(body, n, T.SMALL, T.WARN, justify="left", wraplength=self.wrap).pack(anchor="w")
        label(body, rules.ADVICE[ev.level], T.FONT, justify="left", wraplength=self.wrap).pack(anchor="w", pady=(px(6), 0))
        done = store.last_decision(p.id)
        if done:
            self._done_banner(body, done).pack(fill="x", pady=(px(8), 0))
        self.reason_var = tk.StringVar(value=REFUSE_REASONS[0])
        self.reason_row = tk.Frame(body, bg=T.SURFACE)
        label(self.reason_row, "Lý do không chỉ định", T.SMALL_BOLD, T.FG).pack(anchor="w")
        # Không dùng tk.OptionMenu: trên Windows nó mở danh sách đè mục đang chọn lên ô, nên ở khung sát đáy
        # màn hình danh sách bị nhảy lệch. Ở đây danh sách luôn mở ngay dưới ô (hoặc ngay trên nếu thiếu chỗ).
        box = tk.Frame(self.reason_row, bg=T.SURFACE, highlightthickness=1, highlightbackground=T.LINE,
                       cursor="hand2")
        box.pack(fill="x", pady=(px(3), 0))
        val = tk.Label(box, textvariable=self.reason_var, font=T.FONT, bg=T.SURFACE, fg=T.FG, anchor="w")
        val.pack(side="left", fill="x", expand=True, padx=(px(8), 0), pady=px(4))
        arrow = tk.Label(box, text="▾", font=T.FONT, bg=T.SURFACE, fg=T.MUTED)
        arrow.pack(side="right", padx=px(8))
        menu = tk.Menu(box, tearoff=0, font=T.FONT)
        for r in REFUSE_REASONS:
            menu.add_radiobutton(label=r, variable=self.reason_var, value=r)

        def open_menu(_e=None):
            menu.update_idletasks()
            x, y, h = box.winfo_rootx(), box.winfo_rooty(), box.winfo_height()
            mh = menu.winfo_reqheight()
            below = y + h
            y = below if below + mh <= box.winfo_vrooty() + box.winfo_screenheight() - px(40) else y - mh
            # Như menu ⋯: tạm bỏ "luôn trên cùng" (_keep_on_top nâng khung mỗi 2 giây sẽ đóng mất danh sách)
            self._menu_open = True
            self.attributes("-topmost", False)
            try:
                menu.tk_popup(x, max(0, y))
            finally:
                menu.grab_release()
                self._menu_open = False
                self.after(200, lambda: self.attributes("-topmost", True))

        for w in (box, val, arrow):
            w.bind("<Button-1>", open_menu)
            w.bind("<Enter>", lambda e: [c.configure(bg=T.HOVER) for c in (box, val, arrow)])
            w.bind("<Leave>", lambda e: [c.configure(bg=T.SURFACE) for c in (box, val, arrow)])

        self.result_foot = tk.Frame(f, bg=T.SURFACE)
        self.result_foot.pack(fill="x", padx=px(16), pady=(px(12), px(14)))
        self._result_buttons()
        return f

    def _result_buttons(self, refusing=False):
        for w in self.result_foot.winfo_children():
            w.destroy()
        edit = tk.Frame(self.result_foot, bg=T.SURFACE, cursor="hand2")
        tk.Label(edit, text="", font=T.ICON_SMALL, fg=T.ACCENT, bg=T.SURFACE).pack(side="left")
        tk.Label(edit, text=" Sửa lại", font=T.SMALL, fg=T.ACCENT, bg=T.SURFACE).pack(side="left")
        for w in [edit, *edit.winfo_children()]:
            w.bind("<Button-1>", lambda e: self._show(self.form))
        edit.pack(side="left")
        if self.ev.level == RiskLevel.VERY_LOW:
            FlatButton(self.result_foot, "Đã hiểu", self.collapse, primary=True).pack(side="right")
        elif refusing:
            self.reason_row.pack(fill="x", pady=(px(10), 0))
            FlatButton(self.result_foot, "Lưu", lambda: self._decide(False, self.reason_var.get()),
                       primary=True).pack(side="right")
            FlatButton(self.result_foot, "Quay lại", self._result_buttons).pack(side="right", padx=(0, px(8)))
        else:
            self.reason_row.pack_forget()
            FlatButton(self.result_foot, "Chỉ định chụp", lambda: self._decide(True), primary=True).pack(side="right")
            FlatButton(self.result_foot, "Không chỉ định", lambda: self._result_buttons(True),
                       danger=True).pack(side="right", padx=(0, px(8)))
        self._place()

    def _decide(self, done: bool, reason: str = ""):
        p, ev = self.patient, self.ev
        p.clinic = self.clinic                  # phòng khám gần nhất của bệnh nhân
        saved = store.add(AlertRecord(datetime.now(), self.clinic, self.doctor, p.id, p.name, ev.level,
                                      ev.percent, done, "" if done else reason, p.treatment_code))
        f = tk.Frame(self.body, bg=T.SURFACE)
        row = tk.Frame(f, bg=T.SURFACE)
        row.pack(fill="x", padx=px(16), pady=px(16))
        tk.Label(row, text="" if done else "", font=(T.ICON[0], 18),
                 fg=T.SUCCESS if done else T.DANGER, bg=T.SURFACE).pack(side="left", padx=(0, px(12)))
        tx = tk.Frame(row, bg=T.SURFACE)
        tx.pack(side="left", fill="x")
        label(tx, "Đã ghi nhận: " + ("ĐƯỢC CHỤP" if done else "KHÔNG ĐƯỢC CHỤP"), T.BOLD,
              T.SUCCESS if done else T.DANGER).pack(anchor="w")
        label(tx, "Dịch vụ CT mạch vành đã được đề xuất sang HIS." if done else f"Lý do: {reason}",
              T.SMALL, T.MUTED, justify="left", wraplength=self.wrap - px(40)).pack(anchor="w")
        if not saved:
            tk.Label(f, text=f"Chưa lưu được vào máy này: {store.save_error}", font=T.SMALL_BOLD, fg=T.DANGER,
                     bg=T.DANGER_SOFT, justify="left", anchor="w", wraplength=self.wrap - px(12), padx=px(8),
                     pady=px(4)).pack(fill="x", padx=px(16), pady=(0, px(14)))
        self._show(f)

        def done_later():
            if self.view is f:          # bác sĩ chưa chuyển sang bệnh nhân khác
                self.load_patient(p)
                self.collapse()
        self.after(3500 if saved else 10000, done_later)

    # ================================================================== hiển thị
    def _show(self, frame: tk.Frame):
        if self.view in (self.picker, self.clinic_view, self.doctor_view) and frame is not self.view:
            self._leave_overlay()
        if self.view is not frame:
            self.view.pack_forget()
            if self.view is not self.form:
                self.view.destroy()
            self.view = frame
            frame.pack(fill="both", expand=True)
        self._place()

    def toggle(self):
        self.collapse() if self.expanded else self.expand()

    def expand(self):
        if not self.expanded:
            self.body.pack(fill="both", expand=True)
            self.expanded = True
            self.h_toggle.configure(text="")
        self._place()
        self.lift()

    def collapse(self):
        if self.doctor is None or self.clinic is None:   # phải nhập mã bác sĩ, chọn phòng khám trước
            return
        if self.expanded:
            self.body.pack_forget()
            self.expanded = False
            self.h_toggle.configure(text="")
        self._place()

    def _place(self):
        self.update_idletasks()
        w = px(self.WIDTH)
        h = self.frame.winfo_reqheight() + 2
        l, t, r, b = work_area()
        x = min(max(l, r - self.margin[0] - w), r - w)
        y = min(max(t, b - self.margin[1] - h), b - h)
        self.geometry(f"{w}x{h}+{x}+{y}")

    def _keep_on_top(self):
        # HIS toàn màn hình có thể chiếm lớp trên cùng; nhắc lại định kỳ.
        try:
            if not getattr(self, "_menu_open", False):   # không đè lên menu đang mở
                self.attributes("-topmost", True)
                self.lift()
        except tk.TclError:
            return
        self.after(2000, self._keep_on_top)

    @staticmethod
    def _sound(level: RiskLevel):
        try:
            import winsound
            if level == RiskLevel.MODERATE:
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            elif level == RiskLevel.LOW:
                winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:
            pass
