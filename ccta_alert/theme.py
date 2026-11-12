"""Màu sắc, font chữ và kiểu ttk dùng chung."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .rules import RiskLevel

BG = "#f3f6f9"
NAV_BG = "#eaeff3"
SURFACE = "#ffffff"
LINE = "#dae1e8"
HOVER = "#e2e8ee"
FG = "#17242e"
MUTED = "#606f7c"
ACCENT = "#0e6a8a"
ACCENT_HOVER = "#147ca0"
ACCENT_SOFT = "#e2f0f6"
DANGER = "#be2d1e"
DANGER_SOFT = "#fdeae7"
SUCCESS = "#1f8452"
WARN = "#a66006"
WARN_SOFT = "#fef3de"

# Màu mức nguy cơ theo chú giải bảng ESC: Rất thấp xanh dương, Thấp xanh lục, Vừa vàng
LEVEL_FG = {RiskLevel.VERY_LOW: "#286eb4", RiskLevel.LOW: "#127a60", RiskLevel.MODERATE: "#b25400"}
LEVEL_FILL = {RiskLevel.VERY_LOW: "#96c4ec", RiskLevel.LOW: "#48b896", RiskLevel.MODERATE: "#f6de46"}
LEVEL_SOFT = {RiskLevel.VERY_LOW: "#ddecfa", RiskLevel.LOW: "#d6f1e6", RiskLevel.MODERATE: "#fff3c4"}

UI = "Segoe UI"
SEMI = "Segoe UI Semibold"
FONT = (UI, 10)
SMALL = (UI, 9)
SMALL_BOLD = (SEMI, 9)
BOLD = (SEMI, 10)
H1 = (SEMI, 16)
H2 = (SEMI, 11)
TITLE = (UI, 14, "bold")
BIG = (SEMI, 28)
KPI = (SEMI, 20)
ICON = ("Segoe MDL2 Assets", 12)
ICON_SMALL = ("Segoe MDL2 Assets", 9)
ICON_BIG = ("Segoe MDL2 Assets", 20)

SCALE = 1.0  # đặt lại theo DPI khi tạo cửa sổ


def px(v: float) -> int:
    """Đổi kích thước thiết kế (ở 96 DPI) sang pixel thực."""
    return int(round(v * SCALE))


def setup(root: tk.Tk) -> None:
    global SCALE
    SCALE = root.winfo_fpixels("1i") / 96.0
    s = ttk.Style(root)
    s.theme_use("clam")
    s.configure("Treeview", background=SURFACE, fieldbackground=SURFACE, foreground=FG,
                rowheight=px(26), font=FONT, borderwidth=0, relief="flat")
    s.map("Treeview", background=[("selected", ACCENT_SOFT)], foreground=[("selected", FG)])
    s.configure("Treeview.Heading", background=SURFACE, foreground=MUTED, font=SMALL_BOLD,
                relief="flat", borderwidth=0, padding=(px(6), px(5)))
    s.map("Treeview.Heading", background=[("active", HOVER)])
    s.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
    s.configure("TCombobox", fieldbackground=SURFACE, background=SURFACE, foreground=FG,
                arrowcolor=MUTED, bordercolor=LINE, lightcolor=SURFACE, darkcolor=SURFACE, padding=px(4))
    s.map("TCombobox", fieldbackground=[("readonly", SURFACE)], selectbackground=[("readonly", SURFACE)],
          selectforeground=[("readonly", FG)], bordercolor=[("focus", ACCENT)])
    s.configure("Vertical.TScrollbar", troughcolor=BG, background=LINE, bordercolor=BG,
                arrowcolor=MUTED, lightcolor=LINE, darkcolor=LINE, gripcount=0)
    root.option_add("*TCombobox*Listbox.font", FONT)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
