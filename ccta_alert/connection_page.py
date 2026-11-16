"""Màn "Kết nối HIS": điền thông tin kết nối Oracle, kiểm tra, lưu vào his.json. App chỉ đọc (SELECT) từ HIS."""
from __future__ import annotations

import tkinter as tk
from typing import TYPE_CHECKING

from . import his
from . import theme as T
from .pages import page_header
from .theme import px
from .widgets import Card, FlatButton, ScrollFrame, label

if TYPE_CHECKING:
    from .app import ManagerWindow

FIELDS = [  # khóa, nhãn, gợi ý
    ("host", "Máy chủ", "Địa chỉ IP hoặc tên máy chủ Oracle, ví dụ 10.0.0.10"),
    ("port", "Cổng", "Thường là 1521"),
    ("service_name", "Service name", "Tên service của database HIS"),
    ("dsn", "Chuỗi kết nối (DSN)", "Không bắt buộc. Điền thì bỏ qua 3 ô trên, ví dụ 10.0.0.10:1521/HISPRO"),
    ("user", "Tài khoản", "Nên dùng tài khoản chỉ có quyền SELECT"),
    ("password", "Mật khẩu", ""),
    ("poll_seconds", "Đọc lại mỗi (giây)", "Bao lâu đọc lại danh sách bệnh nhân một lần (tối thiểu 5). Càng nhỏ, khung càng sớm chuyển sang bệnh nhân bác sĩ vừa bắt đầu xử lý"),
    ("oracle_client_dir", "Thư mục Oracle Instant Client", "Không bắt buộc. Chỉ cần với Oracle cũ (11g)"),
]
NAMES = {k: t for k, t, _ in FIELDS}


class ConnectionPage(tk.Frame):
    def __init__(self, parent, main: "ManagerWindow"):
        super().__init__(parent, bg=T.BG)
        self.main = main
        self._worker: his.Worker | None = None
        self._busy = False
        sf = ScrollFrame(self)
        sf.pack(fill="both", expand=True)
        root = tk.Frame(sf.inner, bg=T.BG)
        root.pack(fill="both", expand=True, padx=px(24), pady=px(18))
        page_header(root, "Kết nối HIS",
                    "Đọc danh sách phòng khám và bệnh nhân khám hôm nay từ HIS (Oracle). "
                    "App chỉ đọc (SELECT), không ghi, sửa hay xóa gì trong database.")

        card = Card(root, "Thông tin kết nối")
        card.pack(fill="x", pady=(0, px(12)))
        g = card.body
        g.columnconfigure(1, weight=1)
        self.enabled = tk.BooleanVar(value=True)
        tk.Checkbutton(g, text="Bật kết nối HIS (tắt thì app không đọc bệnh nhân)", variable=self.enabled, font=T.FONT,
                       bg=T.SURFACE, activebackground=T.SURFACE, fg=T.FG, selectcolor=T.SURFACE, bd=0,
                       highlightthickness=0, anchor="w", cursor="hand2").grid(row=0, column=1, sticky="w",
                                                                              pady=(0, px(6)))
        self.vars: dict[str, tk.StringVar] = {}
        for i, (key, text, hint) in enumerate(FIELDS, start=1):
            label(g, text, T.BOLD).grid(row=i, column=0, sticky="nw", padx=(0, px(18)), pady=(px(7), 0))
            cell = tk.Frame(g, bg=T.SURFACE)
            cell.grid(row=i, column=1, sticky="we", pady=px(3))
            v = tk.StringVar()
            self.vars[key] = v
            box = tk.Frame(cell, bg=T.SURFACE, highlightthickness=1, highlightbackground=T.LINE,
                           highlightcolor=T.ACCENT)
            box.pack(anchor="w")
            e = tk.Entry(box, textvariable=v, font=T.FONT, relief="flat", bd=0, bg=T.SURFACE, fg=T.FG, width=48,
                         insertbackground=T.FG, show="•" if key == "password" else "")
            e.pack(padx=px(6), ipady=px(4))
            e.bind("<FocusIn>", lambda ev, b=box: b.configure(highlightbackground=T.ACCENT))
            e.bind("<FocusOut>", lambda ev, b=box: b.configure(highlightbackground=T.LINE))
            h = label(cell, hint, T.SMALL, T.MUTED, justify="left", wraplength=px(560))
            if hint or key == "password":
                h.pack(anchor="w")
            if key == "password":
                self.pw_hint = h

        act = tk.Frame(g, bg=T.SURFACE)
        act.grid(row=len(FIELDS) + 1, column=1, sticky="w", pady=(px(12), 0))
        FlatButton(act, "Kiểm tra kết nối", self._test).pack(side="left", padx=(0, px(8)))
        FlatButton(act, "Lưu", self._save, primary=True).pack(side="left", padx=(0, px(8)))
        self.restart_btn = FlatButton(act, "Khởi động lại app để áp dụng", self._restart)
        self.result = label(g, "", T.FONT, justify="left", wraplength=px(720))
        self.result.grid(row=len(FIELDS) + 2, column=1, sticky="w", pady=(px(10), 0))

        safe = Card(root, "Chỉ đọc: app bảo đảm thế nào")
        safe.pack(fill="x", pady=(0, px(12)))
        for k, v in [
            ("Câu lệnh", "Chỉ có 5 câu SELECT viết sẵn trong app (xem bên dưới). Ngày, mã phòng, mã bệnh nhân, mã dịch vụ "
                        "truyền bằng tham số."),
            ("Kiểm tra", "Trước khi chạy, app từ chối mọi câu không phải SELECT, có dấu ';', hoặc có INSERT, UPDATE, "
                         "DELETE, MERGE, DDL, FOR UPDATE…"),
            ("Giao dịch", "Không bao giờ commit. Mỗi lần đọc mở kết nối rồi đóng ngay. Tối đa 5000 dòng (báo cáo 20000), 20 giây."),
            ("Mật khẩu", "Mã hóa bằng Windows (DPAPI) trước khi lưu, chỉ giải mã được trên máy này."),
            ("Khuyến nghị", "Dùng tài khoản Oracle riêng chỉ có quyền SELECT trên HIS_TREATMENT, HIS_SERVICE_REQ, "
                            "HIS_EXECUTE_ROOM, HIS_SERE_SERV. Đây là lớp bảo vệ chắc nhất, do chính database bảo đảm."),
        ]:
            r = tk.Frame(safe.body, bg=T.SURFACE)
            r.pack(fill="x", pady=px(2))
            label(r, k, T.BOLD, width=12, anchor="w").pack(side="left", anchor="n")
            label(r, v, T.FONT, justify="left", wraplength=px(720)).pack(side="left", anchor="w")

        sql = Card(root, "Câu truy vấn đang dùng")
        sql.pack(fill="x")
        text = ("-- Danh sách phòng khám" + his.SQL_CLINICS + "\n-- Bệnh nhân khám hôm nay tại phòng" + his.SQL_PATIENTS
                + "\n-- Dự phòng, chỉ dùng nếu HIS không có cột TDL_PATIENT_CODE" + his.SQL_PATIENTS_NO_CODE
                + "\n-- Đã thực hiện dịch vụ chưa (nhãn cạnh tên bệnh nhân; :c0, :c1… là mã dịch vụ)"
                + his.SQL_HISTORY.format(who="t.TDL_PATIENT_CODE", codes=":c0, :c1, …")
                + "\n-- Tab Xuất báo cáo" + his.SQL_REPORT.format(codes=":c0, :c1, …")
                + "\n-- Thống kê: lượt khám để đếm bệnh nhân không thao tác (:r0, :r1… là mã phòng)"
                + his.SQL_VISITS.format(pcode="t.TDL_PATIENT_CODE AS ma_benh_nhan,\n    ",
                                        pgroup="t.TDL_PATIENT_CODE, ", rooms=":r0, :r1, …"))
        txt = tk.Text(sql.body, font=("Consolas", 9), height=text.count("\n") + 1, bg=T.BG, fg=T.FG, relief="flat",
                      bd=0, padx=px(10), pady=px(8), wrap="none")
        txt.insert("1.0", text)
        txt.configure(state="disabled")
        txt.pack(fill="x")
        self.load()

    # ------------------------------------------------------------------ form
    def load(self):
        raw = his.read_raw()
        self.enabled.set(bool(raw.get("enabled", True)))
        defaults = {"port": "1521", "poll_seconds": "10"}
        for key, v in self.vars.items():
            v.set("" if key == "password" else str(raw.get(key, defaults.get(key, ""))))
        has_pw = bool(raw.get("password_encrypted") or raw.get("password"))
        self.pw_hint.configure(text="Đã lưu (đã mã hóa). Để trống nếu không đổi." if has_pw else "Chưa lưu mật khẩu.")

    def _fields(self) -> tuple[dict, str | None]:
        """Giá trị trên form đã kiểm tra. Ném ValueError kèm lời nhắc nếu thiếu hoặc sai."""
        f = {k: v.get().strip() for k, v in self.vars.items()}
        pw = f.pop("password") or None
        if not f["dsn"] and not (f["host"] and f["service_name"]):
            raise ValueError("Điền máy chủ và service name (hoặc chuỗi kết nối DSN).")
        if not f["user"]:
            raise ValueError("Điền tài khoản.")
        for key, lo in (("port", 1), ("poll_seconds", 5)):
            if f[key]:
                try:
                    n = int(f[key])
                except ValueError:
                    raise ValueError(f"“{NAMES[key]}” phải là số.") from None
                if n < lo:
                    raise ValueError(f"“{NAMES[key]}” tối thiểu là {lo}.")
                f[key] = n
        f["enabled"] = self.enabled.get()
        return f, pw

    def _show(self, text: str, color=T.FG):
        self.result.configure(text=text, fg=color)

    # ------------------------------------------------------------------ nút
    def _test(self):
        if self._busy:
            return
        try:
            f, pw = self._fields()
            pw = pw or his.saved_password()
        except ValueError as e:
            self._show(str(e), T.DANGER)
            return
        except OSError as e:
            self._show(f"Không đọc được mật khẩu đã lưu ({e}). Nhập lại mật khẩu.", T.DANGER)
            return
        if not pw:
            self._show("Nhập mật khẩu.", T.DANGER)
            return
        cfg = dict(f, password=pw)
        p = self.main.panel
        room = (p.clinic_id, p.clinic) if getattr(p, "clinic_id", None) is not None else None
        if self._worker is None:
            self._worker = his.Worker()
        self._busy = True
        self._show("Đang kiểm tra… (chỉ chạy 2 câu SELECT bên dưới)", T.MUTED)
        self._worker.submit(lambda: his.test_connection(cfg, room), self._tested)
        self._pump()

    def _pump(self):
        self._worker.pump()
        if self._busy:
            self.after(150, self._pump)

    def _tested(self, steps, err):
        self._busy = False
        if err:
            self._show(f"✖  {his.error_text(err)}", T.DANGER)
            return
        ok = all(s for s, _ in steps)
        lines = [("✔  " if s else "✖  ") + t for s, t in steps]
        self._show("\n".join(lines) + ("\n\nKết nối tốt. Bấm Lưu để dùng." if ok else ""),
                   T.SUCCESS if ok else T.DANGER)

    def _save(self):
        try:
            f, pw = self._fields()
            raw = his.read_raw()
            if not pw and not (raw.get("password_encrypted") or raw.get("password")):
                raise ValueError("Nhập mật khẩu.")
            his.save_config(f, pw)
        except ValueError as e:
            self._show(str(e), T.DANGER)
            return
        except OSError as e:
            self._show(f"Không lưu được: {e}", T.DANGER)
            return
        self.load()
        self._show(f"Đã lưu vào {his.HIS_CONFIG_FILE}\nKhởi động lại app để áp dụng"
                   + (" (app sẽ đọc bệnh nhân từ HIS)." if f["enabled"] else " (kết nối HIS đang tắt)."), T.SUCCESS)
        self.restart_btn.pack(side="left")

    def _restart(self):
        from .app import restart_app
        restart_app()
