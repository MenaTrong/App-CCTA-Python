"""Điểm khởi chạy: khung cảnh báo góc dưới phải + cửa sổ quản lý mở khi cần."""
from __future__ import annotations

import argparse
import ctypes
import os
import sys
import tkinter as tk
from datetime import datetime

from . import theme as T
from .alert_panel import AlertPanel
from .data import DATA_DIR, store
from .theme import px
from .widgets import NavButton, label

WEEKDAYS = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]


def enable_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


class ManagerWindow(tk.Toplevel):
    """Cửa sổ quản lý: Thống kê, Xuất báo cáo, Quy tắc đánh giá, Kết nối HIS. Đóng = ẩn đi."""

    def __init__(self, panel: AlertPanel):
        super().__init__(panel)
        from .connection_page import ConnectionPage
        from .pages import RulesPage, StatsPage
        from .report_page import ReportPage

        self.panel = panel
        self.title("CCTA Alert – Quản lý")
        self.configure(bg=T.BG)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = min(px(1280), sw - px(80)), min(px(840), sh - px(120))
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{max(0, (sh - h) // 2 - px(30))}")
        self.minsize(min(px(1000), w), min(px(640), h))
        self.protocol("WM_DELETE_WINDOW", self.withdraw)
        self._flash_job = None

        status = tk.Frame(self, bg=T.SURFACE, highlightthickness=1, highlightbackground=T.LINE)
        status.pack(side="bottom", fill="x")
        self.st_his = label(status, "", T.SMALL, T.WARN)
        self.st_his.pack(side="left", padx=px(10), pady=px(4))
        self.st_clinic = label(status, "", T.SMALL_BOLD, T.ACCENT)
        self.st_clinic.pack(side="left", padx=px(10))
        self.st_clock = label(status, "", T.SMALL, T.MUTED)
        self.st_clock.pack(side="right", padx=px(10))
        self.st_msg = label(status, "", T.SMALL_BOLD)
        self.st_msg.pack(side="left", padx=px(10))

        nav = tk.Frame(self, bg=T.NAV_BG, width=px(220))
        nav.pack(side="left", fill="y")
        nav.pack_propagate(False)
        brand = tk.Frame(nav, bg=T.NAV_BG)
        brand.pack(fill="x", padx=px(18), pady=(px(16), px(18)))
        label(brand, "CCTA Alert", (T.SEMI, 14), T.ACCENT).pack(anchor="w")
        label(brand, "Quản lý cảnh báo CT mạch vành", T.SMALL, T.MUTED).pack(anchor="w")
        label(nav, "Bệnh nhân và phòng khám đọc từ HIS (chỉ đọc)." if store.his
              else "Chưa cấu hình kết nối HIS: vào tab Kết nối HIS.", T.SMALL, T.MUTED,
              justify="left", wraplength=px(184)).pack(side="bottom", anchor="w", padx=px(18), pady=(px(6), px(14)))
        label(nav, f"Dữ liệu lưu tại:\n{DATA_DIR}", T.SMALL, T.MUTED,
              justify="left", wraplength=px(184)).pack(side="bottom", anchor="w", padx=px(18))

        content = tk.Frame(self, bg=T.BG)
        content.pack(side="left", fill="both", expand=True)
        content.rowconfigure(0, weight=1)
        content.columnconfigure(0, weight=1)

        self.navs: list[NavButton] = []
        self.pages: list[tk.Frame] = []
        self.stats = StatsPage(content, self)
        self.report = ReportPage(content, self)
        self.rules = RulesPage(content, self)
        self.conn = ConnectionPage(content, self)
        for i, (text, glyph, page) in enumerate([
            ("Thống kê", "", self.stats),
            ("Xuất báo cáo", "", self.report),
            ("Quy tắc đánh giá", "", self.rules),
            ("Kết nối HIS", "", self.conn),
        ]):
            if i == 2:
                tk.Frame(nav, bg=T.LINE, height=1).pack(fill="x", padx=px(20), pady=px(10))
            b = NavButton(nav, text, glyph, lambda k=i: self.show_page(k))
            b.pack(fill="x", padx=px(12), pady=(0, px(2)))
            self.navs.append(b)
            page.grid(row=0, column=0, sticky="nsew")
            self.pages.append(page)
        self._clock()

    # --- API cho các màn hình
    def show_page(self, i: int):
        for k, b in enumerate(self.navs):
            b.set_selected(k == i)
        page = self.pages[i]
        page.tkraise()
        if page is self.stats:
            self.stats.reload()
        elif page is self.report:
            pass
        elif page is self.rules:
            self.rules.refresh()
        else:
            self.conn.load()
        self.st_clinic.configure(text="  ·  ".join(x for x in (
            self.panel.clinic and f"Phòng khám: {self.panel.clinic}",
            self.panel.doctor and f"Mã bác sĩ: {self.panel.doctor}") if x))
        self.deiconify()
        self.lift()
        self.focus_force()

    def flash(self, msg: str, color: str):
        self.st_msg.configure(text=msg, fg=color)
        if self._flash_job:
            self.after_cancel(self._flash_job)
        self._flash_job = self.after(8000, lambda: self.st_msg.configure(text=""))

    def _clock(self):
        n = datetime.now()
        self.st_clock.configure(text=f"{WEEKDAYS[n.weekday()]}, {n:%d/%m/%Y  %H:%M:%S}")
        self.st_his.configure(text=f"●  HIS: {store.his_status}",
                              fg=T.SUCCESS if store.his_ok else T.DANGER if store.his_ok is False else T.WARN)
        self.after(1000, self._clock)


def restart_app() -> None:
    """Mở lại app (để áp dụng cấu hình HIS mới) rồi thoát bản đang chạy."""
    import subprocess
    env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")   # bản exe: chạy như mở mới, không dùng lại thư mục tạm
    args = [sys.executable] + (sys.argv[1:] if getattr(sys, "frozen", False) else sys.argv)
    subprocess.Popen(args, env=env, close_fds=True)
    os._exit(0)


_manager: ManagerWindow | None = None


def open_manager(panel: AlertPanel, page: int) -> ManagerWindow:
    global _manager
    if _manager is None:
        _manager = ManagerWindow(panel)
    _manager.show_page(page)
    return _manager


def _snap(app: AlertPanel, path: str):
    """Chụp khung cảnh báo (và cửa sổ quản lý nếu mở) ra PNG rồi thoát. Cần Pillow."""
    try:
        from PIL import ImageGrab
        wins = [app] + ([_manager] if _manager is not None and _manager.winfo_viewable() else [])
        for k, w in enumerate(wins):
            w.update()
            x, y = w.winfo_rootx(), w.winfo_rooty()
            img = ImageGrab.grab(bbox=(x, y, x + w.winfo_width(), y + w.winfo_height()), all_screens=True)
            img.save(path if k == 0 else path.replace(".png", f"_{k}.png"))
    finally:
        sys.stdout.flush()
        os._exit(0)


def _check_his(out_path: str) -> None:
    """Chạy bước "Kiểm tra kết nối" không cần giao diện; ghi từng bước ra file (bản exe không có cửa sổ lệnh)."""
    lines = [f"CCTA Alert – kiểm tra kết nối HIS  {datetime.now():%d/%m/%Y %H:%M:%S}",
             f"Cấu hình: {DATA_DIR}\\his.json"]
    try:
        from . import his
        cfg = his.load_config()
        if cfg is None:
            lines.append("✖ Chưa có his.json hoặc kết nối đang tắt.")
        else:
            for ok, text in his.test_connection(cfg):
                lines.append(("✔ " if ok else "✖ ") + text)
    except Exception as e:                       # ví dụ thiếu module trong bản exe
        lines.append(f"✖ {type(e).__name__}: {e}")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="CCTA Alert")
    ap.add_argument("--page", type=int, help="mở cửa sổ quản lý ở màn 0 Thống kê, 1 Xuất báo cáo, 2 Quy tắc, 3 Kết nối HIS")
    ap.add_argument("--snap", help="tự chụp ra PNG rồi thoát (kiểm tra giao diện, cần Pillow)")
    ap.add_argument("--check-his", metavar="FILE",
                    help="chẩn đoán: kiểm tra kết nối HIS theo his.json (chỉ SELECT), ghi kết quả ra FILE rồi thoát")
    args = ap.parse_args(argv)

    if args.check_his:
        _check_his(args.check_his)
        return
    enable_dpi_awareness()
    app = AlertPanel(open_manager)
    app.open_doctor()                           # bắt đầu: nhập mã bác sĩ → chọn phòng khám
    if args.page is not None:
        app.after(300, lambda: open_manager(app, args.page))
    if args.snap:
        app.after(2500, lambda: _snap(app, args.snap))
    app.mainloop()


if __name__ == "__main__":
    main(sys.argv[1:])
