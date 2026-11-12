"""Các thành phần giao diện dùng chung."""
from __future__ import annotations

import math
import tkinter as tk
from tkinter import ttk
from typing import Callable

from . import rules
from . import theme as T
from .rules import RiskLevel
from .theme import px


def label(parent, text="", font=T.FONT, fg=T.FG, bg=None, **kw) -> tk.Label:
    return tk.Label(parent, text=text, font=font, fg=fg, bg=bg or parent.cget("bg"), **kw)


def chip(parent, text, fg=T.ACCENT, bg=T.ACCENT_SOFT) -> tk.Label:
    return tk.Label(parent, text=text, font=T.SMALL, fg=fg, bg=bg, padx=px(8), pady=px(2))


class FlatButton(tk.Label):
    """Nút phẳng có màu, hỗ trợ chuột và phím Enter/Space."""

    def __init__(self, parent, text, command: Callable | None = None, primary=False, danger=False,
                 font=None, padx=16, pady=6):
        if primary:
            self._bg, self._hover, fg, border = T.ACCENT, T.ACCENT_HOVER, "white", T.ACCENT
        else:
            self._bg, self._hover, fg, border = T.SURFACE, T.HOVER, (T.DANGER if danger else T.FG), T.LINE
        super().__init__(parent, text=text, bg=self._bg, fg=fg, font=font or (T.BOLD if primary else T.FONT),
                         padx=px(padx), pady=px(pady), cursor="hand2", takefocus=1,
                         highlightthickness=1, highlightbackground=border, highlightcolor=T.FG)
        self.command = command
        self.bind("<Enter>", lambda e: self.configure(bg=self._hover))
        self.bind("<Leave>", lambda e: self.configure(bg=self._bg))
        self.bind("<ButtonRelease-1>", self._click)
        self.bind("<Return>", self._click)
        self.bind("<space>", self._click)

    def _click(self, _e=None):
        if self.command:
            self.command()


class Card(tk.Frame):
    """Khung nền trắng, viền mảnh, có tiêu đề và vùng nội dung `body`."""

    def __init__(self, parent, title: str, body_pad=True):
        super().__init__(parent, bg=T.SURFACE, highlightthickness=1, highlightbackground=T.LINE, highlightcolor=T.LINE)
        head = tk.Frame(self, bg=T.SURFACE)
        head.pack(fill="x", padx=px(14), pady=px(10))
        self.title = label(head, title, T.H2)
        self.title.pack(side="left")
        self.header_right = tk.Frame(head, bg=T.SURFACE)
        self.header_right.pack(side="right")
        tk.Frame(self, bg=T.LINE, height=1).pack(fill="x")
        self.body = tk.Frame(self, bg=T.SURFACE)
        if body_pad:
            self.body.pack(fill="both", expand=True, padx=px(14), pady=(px(8), px(12)))
        else:
            self.body.pack(fill="both", expand=True, padx=1, pady=(0, 1))


class NavButton(tk.Frame):
    def __init__(self, parent, text: str, glyph: str, command: Callable):
        super().__init__(parent, bg=T.NAV_BG, height=px(40), cursor="hand2")
        self.pack_propagate(False)
        self.selected = False
        self.command = command
        self.bar = tk.Frame(self, bg=T.NAV_BG, width=px(3))
        self.bar.pack(side="left", fill="y", pady=px(10))
        self.icon = tk.Label(self, text=glyph, font=T.ICON, fg=T.MUTED, bg=T.NAV_BG)
        self.icon.pack(side="left", padx=(px(12), px(12)))
        self.text = tk.Label(self, text=text, font=T.FONT, fg=T.FG, bg=T.NAV_BG)
        self.text.pack(side="left")
        self.badge = tk.Label(self, text="", font=T.SMALL_BOLD, fg="white", bg=T.DANGER, padx=px(6))
        for w in (self, self.bar, self.icon, self.text, self.badge):
            w.bind("<Button-1>", lambda e: self.command())
            w.bind("<Enter>", lambda e: self._paint(hover=True))
            w.bind("<Leave>", lambda e: self._paint(hover=False))

    def _paint(self, hover=False):
        bg = T.SURFACE if self.selected else (T.HOVER if hover else T.NAV_BG)
        for w in (self, self.icon, self.text):
            w.configure(bg=bg)
        self.bar.configure(bg=T.ACCENT if self.selected else bg)
        self.icon.configure(fg=T.ACCENT if self.selected else T.MUTED)
        self.text.configure(font=T.BOLD if self.selected else T.FONT)

    def set_selected(self, sel: bool):
        self.selected = sel
        self._paint()

    def set_badge(self, n: int):
        if n > 0:
            self.badge.configure(text=str(n))
            self.badge.pack(side="right", padx=px(10))
        else:
            self.badge.pack_forget()


class SegmentedControl(tk.Frame):
    def __init__(self, parent, options: list[str], command: Callable[[int], None], selected: int | None = 0,
                 font=T.FONT, padx=16, pady=4):
        super().__init__(parent, bg=T.LINE, padx=1, pady=1)
        self.command = command
        self.font = font
        self.bold = (T.SEMI, font[1])
        self.items: list[tk.Label] = []
        for i, text in enumerate(options):
            lb = tk.Label(self, text=text, font=font, padx=px(padx), pady=px(pady), cursor="hand2")
            lb.pack(side="left", padx=(0 if i == 0 else 1, 0))
            lb.bind("<Button-1>", lambda e, k=i: self.select(k, notify=True))
            self.items.append(lb)
        self.index = selected
        self.select(selected)

    def select(self, i: int | None, notify=False):
        """i = None: chưa chọn."""
        self.index = i
        for k, lb in enumerate(self.items):
            on = k == i
            lb.configure(bg=T.ACCENT if on else T.SURFACE, fg="white" if on else T.FG,
                         font=self.bold if on else self.font)
        if notify:
            self.command(i)


class KpiCard(tk.Frame):
    def __init__(self, parent, caption: str, color=T.FG):
        super().__init__(parent, bg=T.SURFACE, highlightthickness=1, highlightbackground=T.LINE, highlightcolor=T.LINE)
        self.cap = label(self, caption, T.SMALL, T.MUTED)
        self.cap.pack(anchor="w", padx=px(14), pady=(px(12), 0))
        self.val = label(self, "0", T.KPI, color)
        self.val.pack(anchor="w", padx=px(12), pady=(0, px(10)))

    def set(self, value: str, caption: str | None = None):
        self.val.configure(text=value)
        if caption is not None:
            self.cap.configure(text=caption)


class ScrollFrame(tk.Frame):
    """Khung cuộn dọc; đặt nội dung vào `inner`."""

    def __init__(self, parent, bg=T.BG):
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=bg)
        win = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(win, width=e.width))
        self.canvas.configure(yscrollcommand=self._on_scroll)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.bind("<Enter>", lambda e: self.bind_all("<MouseWheel>", self._wheel))
        self.bind("<Leave>", lambda e: self.unbind_all("<MouseWheel>"))

    def _on_scroll(self, first, last):
        if float(first) <= 0.0 and float(last) >= 1.0:
            self.vsb.pack_forget()
        else:
            if not self.vsb.winfo_ismapped():
                self.vsb.pack(side="right", fill="y")
        self.vsb.set(first, last)

    def _wheel(self, e):
        w = e.widget
        if isinstance(w, str) or w.winfo_class() in ("Treeview", "Listbox", "TCombobox"):
            return
        if self.vsb.winfo_ismapped():
            self.canvas.yview_scroll(int(-e.delta / 120), "units")


def make_tree(parent, cols: list[tuple], height=8):
    """cols: (id, tiêu đề, độ rộng, căn lề 'w'|'e'|'center', giãn: bool). Trả về (khung, tree)."""
    frame = tk.Frame(parent, bg=T.SURFACE)
    tree = ttk.Treeview(frame, columns=[c[0] for c in cols], show="headings", height=height, selectmode="browse")
    for cid, text, width, anchor, stretch in cols:
        tree.heading(cid, text=text, anchor=anchor)
        tree.column(cid, width=px(width), minwidth=px(40), anchor=anchor, stretch=stretch)
    vsb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)

    def on_scroll(first, last):  # chỉ hiện thanh cuộn khi cần
        if float(first) <= 0.0 and float(last) >= 1.0:
            vsb.pack_forget()
        elif not vsb.winfo_ismapped():
            vsb.pack(side="right", fill="y", before=tree)
        vsb.set(first, last)

    tree.configure(yscrollcommand=on_scroll)
    tree.pack(side="left", fill="both", expand=True)
    tree.tag_configure("refused", background="#fdf1ef")
    tree.tag_configure("muted", foreground=T.MUTED)
    tree.tag_configure("danger", foreground=T.DANGER)
    tree.tag_configure("warn", foreground=T.WARN)
    tree.tag_configure("ok", foreground=T.SUCCESS)
    return frame, tree


class ScaleBar(tk.Canvas):
    """Thang 0–50% với ba vùng màu và vạch chỉ vị trí."""

    def __init__(self, parent, value: int, width: int):
        super().__init__(parent, width=width, height=px(40), bg=parent.cget("bg"), highlightthickness=0)
        x0, w, y, h = 2, width - 4, px(9), px(10)
        X = lambda v: x0 + w * min(v, 50) / 50
        self.create_rectangle(X(0), y, X(5), y + h, fill=T.LEVEL_FILL[RiskLevel.VERY_LOW], width=0)
        self.create_rectangle(X(5), y, X(15), y + h, fill=T.LEVEL_FILL[RiskLevel.LOW], width=0)
        self.create_rectangle(X(15), y, X(50), y + h, fill=T.LEVEL_FILL[RiskLevel.MODERATE], width=0)
        mx = max(X(value), x0 + 2)
        self.create_polygon(mx - px(6), 0, mx + px(6), 0, mx, px(7), fill=T.FG, outline="")
        self.create_rectangle(mx - 1, y - 2, mx + 2, y + h + 2, fill=T.FG, width=0)
        ty = y + h + px(4)
        self.create_text(X(0), ty, text="0%", anchor="nw", font=T.SMALL, fill=T.MUTED)
        self.create_text(X(5), ty, text="5%", anchor="n", font=T.SMALL, fill=T.MUTED)
        self.create_text(X(15), ty, text="15%", anchor="n", font=T.SMALL, fill=T.MUTED)
        self.create_text(X(50), ty, text="50%", anchor="ne", font=T.SMALL, fill=T.MUTED)


class StackBarChart(tk.Canvas):
    """Biểu đồ cột chồng: Được chụp (dưới) / Không được chụp (trên)."""

    def __init__(self, parent, height: int):
        super().__init__(parent, height=height, bg=T.SURFACE, highlightthickness=0)
        self.labels: list[str] = []
        self.done: list[int] = []
        self.not_done: list[int] = []
        self.bind("<Configure>", lambda e: self.redraw())

    def set_data(self, labels, done, not_done):
        self.labels, self.done, self.not_done = labels, done, not_done
        self.redraw()

    @staticmethod
    def _nice_top(mx: int) -> int:
        if mx <= 4:
            return 4
        half = 10 ** math.floor(math.log10(mx)) / 2
        top = int(math.ceil(mx / half) * half)
        return top + (top % 2)

    def redraw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        u = px(16)
        left, top_y, right, bottom = u * 3, u + px(12), w - px(12), h - u - px(10)
        if right - left < 40 or bottom - top_y < 40 or not self.labels:
            return
        top = self._nice_top(max([1] + [a + b for a, b in zip(self.done, self.not_done)]))
        # Chú giải
        x = right
        for text, color in (("Không được chụp", T.DANGER), ("Được chụp", T.SUCCESS)):
            t = self.create_text(x, 0, text=text, anchor="ne", font=T.SMALL, fill=T.MUTED)
            x0 = self.bbox(t)[0]
            self.create_rectangle(x0 - px(15), px(4), x0 - px(5), px(14), fill=color, width=0)
            x = x0 - px(28)
        ph = bottom - top_y
        for f in (0, 0.5, 1):
            y = bottom - ph * f
            if f:
                self.create_line(left, y, right, y, fill=T.LINE, dash=(3, 3))
            self.create_text(left - px(6), y, text=str(round(top * f)), anchor="e", font=T.SMALL, fill=T.MUTED)
        self.create_line(left, bottom, right, bottom, fill=T.LINE)
        n = len(self.labels)
        slot = (right - left) / n
        bw = min(slot * 0.6, u * 3)
        for i, lab in enumerate(self.labels):
            x = left + slot * i + (slot - bw) / 2
            ha = ph * self.done[i] / top
            hb = ph * self.not_done[i] / top
            if ha:
                self.create_rectangle(x, bottom - ha, x + bw, bottom, fill=T.SUCCESS, width=0)
            if hb:
                self.create_rectangle(x, bottom - ha - hb, x + bw, bottom - ha, fill=T.DANGER, width=0)
            total = self.done[i] + self.not_done[i]
            if total:
                self.create_text(x + bw / 2, bottom - ha - hb - 2, text=str(total), anchor="s", font=T.SMALL, fill=T.MUTED)
            last = i == n - 1
            self.create_text(left + slot * i + slot / 2, bottom + px(5), text=lab, anchor="n",
                             font=T.SMALL_BOLD if last else T.SMALL, fill=T.FG if last else T.MUTED)


class RfClChart(tk.Canvas):
    """Bảng RF-CL vẽ lại theo bố cục của ESC (vòng tròn màu theo mức nguy cơ)."""

    def __init__(self, parent):
        self.u = px(17)
        self.cell = self.u * 2.1
        self.label_w = self.u * 6.2
        self.sex_gap = self.u * 0.8
        self.group_gap = self.u * 1.4
        self.group_w = self.cell * 6 + self.sex_gap
        w = int(self.label_w + self.group_w * 3 + self.group_gap * 2 + 8)
        h = int(self.u * 5.2 + self.cell * 5 + 8)
        super().__init__(parent, width=w, height=h, bg=T.SURFACE, highlightthickness=0)
        self.redraw()

    def redraw(self):
        self.delete("all")
        u, cell = self.u, self.cell
        y0 = u * 5.2
        hit = rules.last_evaluation
        self.create_text(0, u * 4.3, text="Số lượng yếu tố\nnguy cơ", anchor="w", font=T.SMALL, fill=T.MUTED)
        for a, band in enumerate(rules.AGE_BANDS):
            self.create_text(0, y0 + cell * a + cell / 2, text="Tuổi " + band, anchor="w", font=T.BOLD, fill=T.FG)
        for s in range(3):
            gx = self.label_w + s * (self.group_w + self.group_gap)
            self.create_rectangle(gx, 0, gx + self.group_w, u * 1.5, fill=T.ACCENT_SOFT, width=0)
            self.create_text(gx + self.group_w / 2, u * 0.75, text="Điểm triệu chứng " + rules.SYM_GROUPS[s],
                             font=T.BOLD, fill=T.ACCENT)
            for sex in range(2):
                sx = gx + sex * (cell * 3 + self.sex_gap)
                self.create_text(sx + cell * 1.5, u * 2.6, text="Nữ" if sex == 0 else "Nam", font=T.BOLD,
                                 fill="#be3c78" if sex == 0 else "#286eb4")
                for r in range(3):
                    cx = sx + cell * r
                    self.create_text(cx + cell / 2, u * 4.3, text=rules.RF_GROUPS[r], font=T.SMALL, fill=T.MUTED)
                    for a in range(5):
                        v = rules.TABLE[s][sex][a][r]
                        lv = rules.level_of(v)
                        m = cell * 0.08
                        x1, y1 = cx + m, y0 + cell * a + m
                        x2, y2 = cx + cell - m, y0 + cell * (a + 1) - m
                        self.create_oval(x1, y1, x2, y2, fill=T.LEVEL_FILL[lv], width=0)
                        self.create_text((x1 + x2) / 2, (y1 + y2) / 2, text=str(v), font=T.BOLD,
                                         fill=T.FG if lv == RiskLevel.MODERATE else "white")
                        if hit and (hit.sym_group, int(hit.male), hit.age_band, hit.rf_group) == (s, sex, a, r):
                            self.create_oval(x1 - 3, y1 - 3, x2 + 3, y2 + 3, outline=T.FG, width=2.5)
