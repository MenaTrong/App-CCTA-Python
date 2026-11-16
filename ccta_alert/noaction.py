"""Khối "Không thao tác trên app" ở màn Thống kê.

Đọc từ HIS (chỉ SELECT) các lượt khám (bệnh nhân × phòng × ngày) trong kỳ đang xem, tại các phòng khám có dùng app
(hoặc phòng đang chọn), rồi so với dữ liệu trên máy: một lượt khám được tính là ĐÃ THAO TÁC nếu có quyết định
(alerts.jsonl) hoặc lần đánh giá Rất thấp (assessed.jsonl) cùng mã điều trị, hoặc cùng mã bệnh nhân trong cùng ngày.
Còn lại là KHÔNG THAO TÁC. Dữ liệu HIS chỉ đọc khi bấm nút; có quyết định mới thì tự so lại, không đọc lại HIS.
"""
from __future__ import annotations

import tkinter as tk
from collections import Counter
from datetime import date, datetime, timedelta
from tkinter import ttk

from . import theme as T
from .data import add_months, store
from .theme import px
from .widgets import Card, FlatButton, label, make_tree

COLS = [  # khóa, tiêu đề, độ rộng màn hình, độ rộng Excel
    ("stt", "STT", 40, 6),
    ("time", "Thời gian khám", 115, 18),
    ("clinic", "Phòng khám", 170, 32),
    ("pid", "Mã bệnh nhân", 95, 16),
    ("treat", "Mã điều trị", 105, 16),
    ("name", "Tên bệnh nhân", 160, 30),
]


def _n(v) -> str:
    return "" if v is None else str(v).strip().upper()


def period_range(period: int, ref: date) -> tuple[date, date]:
    """Kỳ đang xem ở màn Thống kê (0 ngày, 1 tháng, 2 năm), không vượt quá hôm nay."""
    if period == 0:
        a = b = ref
    elif period == 1:
        a = ref.replace(day=1)
        b = add_months(a, 1) - timedelta(days=1)
    else:
        a, b = date(ref.year, 1, 1), date(ref.year, 12, 31)
    return a, min(b, date.today())


def classify(visits: list[dict]) -> list[dict]:
    """Gắn r["_acted"] cho từng lượt khám: "decision" (có quyết định), "assessed" (đánh giá Rất thấp), "" (không)."""
    by_treat: dict[str, str] = {}
    by_pat_day: dict[tuple[str, date], str] = {}
    for kind, recs in (("assessed", store.assessed), ("decision", store.log)):   # quyết định ưu tiên
        for e in recs:
            if e.treatment_code:
                by_treat[_n(e.treatment_code)] = kind
            by_pat_day[(_n(e.patient_id), e.time.date())] = kind
    for r in visits:
        t = r.get("bat_dau_kham")
        d = t.date() if isinstance(t, datetime) else None
        treat, pid = _n(r.get("ma_dieu_tri")), _n(r.get("ma_benh_nhan"))
        acted = by_treat.get(treat, "") if treat else ""
        if not acted and d:
            acted = (by_pat_day.get((pid, d), "") if pid else "") or by_pat_day.get((treat, d), "")
        r["_acted"] = acted
    return visits


class NoActionCard(Card):
    def __init__(self, parent, stats):
        super().__init__(parent, "Thống kê chi tiết", body_pad=False)
        self.stats = stats
        self.visits: list[dict] | None = None
        self.key = None                 # (từ ngày, đến ngày, phòng) của dữ liệu đã đọc
        self.cut = False
        self.fetched_at: datetime | None = None
        self._worker = None
        self._busy = False
        self.not_acted: list[dict] = []

        self.btn = FlatButton(self.header_right, "Đọc từ HIS", self.fetch, primary=True, padx=12, pady=4)
        self.btn.pack(side="left", padx=(0, px(8)))
        FlatButton(self.header_right, "Xuất Excel…", self.export, padx=12, pady=4).pack(side="left")

        b = self.body
        self.info = label(b, "", T.FONT, T.MUTED, justify="left", wraplength=px(900))
        self.info.pack(anchor="w", padx=px(14), pady=(px(10), px(4)))
        self.kpi = tk.Frame(b, bg=T.SURFACE)
        self.kpi.pack(fill="x", padx=px(14), pady=(0, px(6)))
        self.k = {}
        for key, text, fg, bg in (("all", "Lượt khám", T.FG, T.BG), ("dec", "Có quyết định", T.SUCCESS, "#e3f4ea"),
                                  ("ass", "Đánh giá Rất thấp", T.ACCENT, T.ACCENT_SOFT),
                                  ("no", "Không thao tác", T.DANGER, T.DANGER_SOFT)):
            box = tk.Frame(self.kpi, bg=bg, padx=px(12), pady=px(6))
            box.pack(side="left", padx=(0, px(8)))
            v = tk.Label(box, text="—", font=(T.SEMI, 16), fg=fg, bg=bg)
            v.pack(anchor="w")
            tk.Label(box, text=text, font=T.SMALL, fg=fg, bg=bg).pack(anchor="w")
            self.k[key] = v

        row = tk.Frame(b, bg=T.SURFACE)
        row.pack(fill="x", padx=px(6), pady=(0, px(6)))
        row.columnconfigure(0, weight=42, uniform="na")
        row.columnconfigure(1, weight=58, uniform="na")
        left = tk.Frame(row, bg=T.SURFACE)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, px(8)))
        label(left, "Theo phòng khám", T.SMALL_BOLD, T.MUTED).pack(anchor="w", padx=px(8))
        fr, self.t_clinic = make_tree(left, [("c", "Phòng khám", 150, "w", True), ("n", "Lượt khám", 70, "e", False),
                                             ("a", "Đã thao tác", 80, "e", False), ("x", "Không", 55, "e", False),
                                             ("r", "Tỷ lệ không", 80, "e", False)], height=8)
        fr.pack(fill="both", expand=True)
        right = tk.Frame(row, bg=T.SURFACE)
        right.grid(row=0, column=1, sticky="nsew")
        label(right, "Bệnh nhân không thao tác", T.SMALL_BOLD, T.MUTED).pack(anchor="w", padx=px(8))
        fr, self.t_list = make_tree(right, [(k, t, w, "e" if k == "stt" else "w", k == "name")
                                            for k, t, w, _ in COLS], height=8)
        fr.pack(fill="both", expand=True)
        self.render()

    # ------------------------------------------------------------------ kỳ / phòng đang xem
    def _scope(self) -> tuple[date, date, str | None]:
        a, b = period_range(self.stats.period, self.stats.ref)
        clinic = None if self.stats.cb_clinic.current() <= 0 else self.stats.cb_clinic.get()
        return a, b, clinic

    def _rooms(self, clinic: str | None) -> list[tuple[int, str]]:
        """Phòng cần đọc: phòng đang chọn, hoặc mọi phòng có dùng app (có trong dữ liệu trên máy)."""
        if clinic:
            return [(i, n) for i, n in store.clinics if n == clinic]
        used = {e.clinic for e in store.log} | {e.clinic for e in store.assessed}
        return [(i, n) for i, n in store.clinics if n in used]

    # ------------------------------------------------------------------ đọc HIS
    def fetch(self):
        if self._busy:
            return
        a, b, clinic = self._scope()
        if not store.his:
            self._msg("Chưa cấu hình kết nối HIS: vào tab Kết nối HIS.", T.DANGER)
            return
        if not store.clinics:
            self._msg("Chưa có danh sách phòng khám từ HIS (khung cảnh báo chưa kết nối được HIS).", T.DANGER)
            return
        if a > date.today():
            self._msg("Kỳ đang xem ở tương lai.", T.DANGER)
            return
        rooms = self._rooms(clinic)
        if not rooms:
            self._msg("Không tìm thấy mã phòng trên HIS cho phòng khám đang chọn." if clinic else
                      "Chưa có phòng khám nào dùng app (chưa có dữ liệu trên máy).", T.DANGER)
            return
        from . import his
        if self._worker is None:
            self._worker = his.Worker()

        def job():
            cfg = his.load_config()
            if cfg is None:
                raise RuntimeError("Chưa có his.json hoặc kết nối HIS đang tắt.")
            return his.HisClient(cfg).fetch_visits(a, b, [i for i, _ in rooms])

        self._busy = True
        self.btn.configure(text="Đang đọc…")
        self._msg(f"Đang đọc lượt khám từ {a:%d/%m/%Y} đến {b:%d/%m/%Y} tại {len(rooms)} phòng…", T.MUTED)
        names = dict(rooms)
        self._worker.submit(job, lambda res, err: self._done(res, err, (a, b, clinic), names))
        self._pump()

    def _pump(self):
        self._worker.pump()
        if self._busy:
            self.after(150, self._pump)

    def _done(self, res, err, key, names):
        from . import his
        self._busy = False
        self.btn.configure(text="Đọc từ HIS")
        if err:
            h = his.hint_for(err)
            self._msg(f"✖  {his.error_text(err)}" + (f"\n→ {h}" if h else ""), T.DANGER)
            return
        rows, self.cut = res
        for r in rows:
            try:
                r["_clinic"] = names.get(int(r.get("ma_phong")), str(r.get("ma_phong")))
            except (TypeError, ValueError):
                r["_clinic"] = str(r.get("ma_phong") or "")
        self.visits, self.key, self.fetched_at = rows, key, datetime.now()
        self.render()

    # ------------------------------------------------------------------ hiển thị
    def _msg(self, text, color=T.MUTED):
        self.info.configure(text=text, fg=color)

    def render(self):
        """Gọi mỗi lần màn Thống kê làm mới: so lại với dữ liệu trên máy, không đọc lại HIS."""
        if self._busy:
            return
        a, b, clinic = self._scope()
        for t in (self.t_clinic, self.t_list):
            t.delete(*t.get_children())
        self.not_acted = []
        if self.visits is None or self.key != (a, b, clinic):
            for v in self.k.values():
                v.configure(text="—")
            when = f"ngày {a:%d/%m/%Y}" if a == b else f"từ {a:%d/%m/%Y} đến {b:%d/%m/%Y}"
            self._msg(f"Bấm \"Đọc từ HIS\" để đếm bệnh nhân khám {when} ({clinic or 'các phòng có dùng app'}) "
                      "mà bác sĩ không thao tác trên app."
                      + ("" if self.visits is None else " Dữ liệu đang có là của kỳ/phòng khác."), T.MUTED)
            return
        classify(self.visits)
        V = self.visits
        dec = sum(r["_acted"] == "decision" for r in V)
        ass = sum(r["_acted"] == "assessed" for r in V)
        self.not_acted = [r for r in V if not r["_acted"]]
        no = len(self.not_acted)
        self.k["all"].configure(text=str(len(V)))
        self.k["dec"].configure(text=str(dec))
        self.k["ass"].configure(text=str(ass))
        self.k["no"].configure(text=f"{no}" + (f"  ({round(100 * no / len(V))}%)" if V else ""))
        tot, act = Counter(r["_clinic"] for r in V), Counter(r["_clinic"] for r in V if r["_acted"])
        for c, n in tot.most_common():
            x = n - act[c]
            self.t_clinic.insert("", "end", values=(c, n, act[c], x, f"{round(100 * x / n)}%"))
        for i, r in enumerate(self.not_acted[:2000], start=1):
            self.t_list.insert("", "end", values=self._values(i, r))
        if not V:
            self.t_clinic.insert("", "end", tags=("muted",), values=("Không có lượt khám nào.", "", "", "", ""))
        msg = (f"Đọc HIS lúc {self.fetched_at:%H:%M:%S}: {len(V)} lượt khám (bệnh nhân × phòng × ngày). "
               "Đã thao tác = có quyết định hoặc đã đánh giá Rất thấp, cùng mã điều trị hoặc cùng mã BN trong ngày.")
        if self.cut:
            msg += "\nHIS trả quá nhiều dòng, chỉ lấy phần đầu: xem theo tháng hoặc từng phòng để đủ."
        self._msg(msg, T.WARN if self.cut else T.MUTED)

    @staticmethod
    def _values(i: int, r: dict) -> list[str]:
        t = r.get("bat_dau_kham")
        return [str(i), f"{t:%d/%m/%Y %H:%M}" if isinstance(t, datetime) else "", r.get("_clinic", ""),
                str(r.get("ma_benh_nhan") or ""), str(r.get("ma_dieu_tri") or ""),
                str(r.get("ten_benh_nhan") or "").strip()]

    # ------------------------------------------------------------------ xuất
    def export(self):
        from tkinter import filedialog, messagebox
        from .report_page import write_xlsx
        self.render()
        if self.visits is None or not self.key:
            self._msg("Chưa có dữ liệu: bấm \"Đọc từ HIS\" trước.", T.DANGER)
            return
        a, b, clinic = self.key
        span = f"{a:%Y%m%d}" if a == b else f"{a:%Y%m%d}-{b:%Y%m%d}"
        path = filedialog.asksaveasfilename(parent=self, title="Lưu danh sách không thao tác", defaultextension=".xlsx",
                                            initialfile=f"KhongThaoTac_{span}.xlsx", filetypes=[("Excel", "*.xlsx")])
        if not path:
            return
        when = f"ngày {a:%d/%m/%Y}" if a == b else f"từ {a:%d/%m/%Y} đến {b:%d/%m/%Y}"
        title = (f"Bệnh nhân không thao tác trên app {when} – {clinic or 'các phòng có dùng app'} – "
                 f"{len(self.not_acted)}/{len(self.visits)} lượt khám")
        try:
            write_xlsx(path, title, [t for _, t, _, _ in COLS],
                       [self._values(i, r) for i, r in enumerate(self.not_acted, start=1)], [w for *_, w in COLS])
        except OSError as e:
            messagebox.showerror("Xuất Excel", f"Không ghi được file (đang mở trong Excel?):\n{e}", parent=self)
            return
        if messagebox.askyesno("Xuất Excel", f"Đã lưu {len(self.not_acted)} bệnh nhân.\nMở file ngay?", parent=self):
            import os
            try:
                os.startfile(path)
            except OSError:
                pass
