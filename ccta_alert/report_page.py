"""Màn "Xuất báo cáo": đọc các y lệnh theo danh sách mã dịch vụ từ HIS (chỉ SELECT) rồi xuất ra Excel/CSV.

Mỗi dòng được đối chiếu với quyết định đã lưu trên máy (alerts.jsonl, gồm cả dữ liệu đã gộp từ máy khác):
  - Không cảnh báo: không có quyết định nào của bác sĩ cho ca này (không màu);
  - Cảnh báo, được chụp: bác sĩ chọn Chỉ định (xanh lá);
  - Cảnh báo, không được chụp: bác sĩ chọn Không chỉ định (đỏ).
Cách nối: cùng mã điều trị; nếu không có thì cùng mã bệnh nhân với cảnh báo trong MATCH_DAYS ngày trước
thời gian bắt đầu y lệnh (đến hết ngày đó). Có nhiều quyết định thì lấy quyết định gần nhất.
"""
from __future__ import annotations

import csv
import re
import tkinter as tk
from tkinter import ttk
import zipfile
from collections import Counter
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING
from xml.sax.saxutils import escape

from . import his, rules
from . import theme as T
from .data import AlertRecord, add_months, load_settings, save_settings, store
from .pages import page_header
from .theme import px
from .widgets import Card, FlatButton, ScrollFrame, SegmentedControl, label, make_tree

if TYPE_CHECKING:
    from .app import ManagerWindow

MAX_DAYS = 366
MATCH_DAYS = 30            # nối theo mã bệnh nhân: cảnh báo trong 30 ngày trước y lệnh

# Trạng thái cảnh báo: (chữ hiển thị, tag màu trên bảng, style Excel)
ST_NONE, ST_DONE, ST_REFUSED = "none", "done", "refused"
STATUS = {ST_NONE: ("Không cảnh báo", "", 0),
          ST_DONE: ("✔ Cảnh báo – được chụp", "st_done", 3),
          ST_REFUSED: ("✖ Cảnh báo – không được chụp", "st_refused", 4)}
# Màu đậm, tương phản rõ (chữ tối trên nền đậm) để phân biệt nhanh khi nhìn lướt
GREEN_BG, GREEN_FG = "#a8dfba", "#0a3f22"
RED_BG, RED_FG = "#f5aea3", "#6e1206"
COLUMNS = [  # khóa trong dòng HIS, tiêu đề, độ rộng trên màn hình, độ rộng cột Excel
    ("stt", "STT", 40, 6),
    ("ma_dieu_tri", "Mã điều trị", 95, 16),
    ("ma_benh_nhan", "Mã bệnh nhân", 100, 16),
    ("ten_benh_nhan", "Tên bệnh nhân", 150, 28),
    ("ma_y_lenh", "Mã y lệnh", 95, 16),
    ("ma_dich_vu", "Mã dịch vụ", 105, 16),
    ("ten_dich_vu", "Tên dịch vụ", 200, 45),
    ("thoi_gian_bat_dau", "Thời gian bắt đầu", 125, 20),
    ("thoi_gian_ket_thuc", "Thời gian kết thúc", 125, 20),
    ("canh_bao", "Cảnh báo", 170, 26),
    ("muc_canh_bao", "Mức cảnh báo", 105, 14),
    ("thoi_gian_canh_bao", "Thời gian cảnh báo", 125, 18),
    ("bac_si", "Mã bác sĩ", 90, 12),
    ("ly_do", "Lý do không chụp", 180, 30),
]


def _norm(v) -> str:
    return "" if v is None else str(v).strip().upper()


def match_alerts(rows: list[dict], log: list[AlertRecord]) -> None:
    """Gắn quyết định cảnh báo (nếu có) vào từng dòng báo cáo: thêm khóa _status và các cột cảnh báo."""
    by_treat: dict[str, AlertRecord] = {}
    by_pat: dict[str, list[AlertRecord]] = {}
    for e in sorted(log, key=lambda x: x.time):         # bản ghi sau ghi đè bản trước: lấy quyết định gần nhất
        if e.treatment_code:
            by_treat[_norm(e.treatment_code)] = e
        by_pat.setdefault(_norm(e.patient_id), []).append(e)
    for r in rows:
        treat, pat = _norm(r.get("ma_dieu_tri")), _norm(r.get("ma_benh_nhan"))
        e = by_treat.get(treat) if treat else None
        if e is None:
            # bản ghi cũ chưa có mã điều trị: nối theo mã BN (hoặc mã điều trị dùng làm mã BN khi HIS thiếu cột mã BN)
            cands = (by_pat.get(pat, []) if pat else []) + (by_pat.get(treat, []) if treat else [])
            t = r.get("thoi_gian_bat_dau")
            if isinstance(t, datetime):
                lo, hi = t - timedelta(days=MATCH_DAYS), datetime.combine(t.date(), datetime.max.time())
                cands = [c for c in cands if lo <= c.time <= hi]
            e = max(cands, key=lambda c: c.time, default=None)
        st = ST_NONE if e is None else ST_DONE if e.done else ST_REFUSED
        r["_status"] = st
        r["canh_bao"] = STATUS[st][0]
        r["muc_canh_bao"] = f"{rules.LEVEL_SHORT[e.level]} · {e.percent}%" if e else ""
        r["thoi_gian_canh_bao"] = e.time if e else None
        r["bac_si"] = e.doctor if e else ""
        r["ly_do"] = e.reason if e and not e.done else ""



def parse_codes(text: str) -> list[str]:
    """Tách mã dịch vụ theo dấu phẩy, chấm phẩy, khoảng trắng hoặc xuống dòng; bỏ trùng, giữ thứ tự."""
    out: list[str] = []
    for c in re.split(r"[,;\s]+", text.strip()):
        c = c.strip().strip("'\"")
        if c and c not in out:
            out.append(c)
    return out


def parse_day(text: str) -> date:
    try:
        return datetime.strptime(text.strip(), "%d/%m/%Y").date()
    except ValueError:
        raise ValueError(f"Ngày không hợp lệ: \"{text.strip()}\" (nhập dạng ngày/tháng/năm, ví dụ 12/11/2026).")


def cell_text(key: str, r: dict) -> str:
    v = r.get(key)
    if isinstance(v, datetime):
        return f"{v:%d/%m/%Y %H:%M:%S}"
    return "" if v is None else str(v).strip()


# ------------------------------------------------------------------------------------- ghi file
_BAD_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _col(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def write_xlsx(path: str, title: str, headers: list[str], rows: list[list[str]], widths: list[int],
               row_styles: list[int] | None = None) -> None:
    """Ghi file .xlsx tối giản (không cần thư viện ngoài): 1 dòng tiêu đề báo cáo, 1 dòng tên cột in đậm,
    sau đó là dữ liệu. Mọi ô là chữ, nên mã có số 0 đứng đầu vẫn giữ nguyên.
    row_styles: style của từng dòng dữ liệu (0 thường, 3 nền xanh lá, 4 nền đỏ)."""
    def c(ref: str, v: str, style: int = 0) -> str:
        v = escape(_BAD_XML.sub("", v))
        return f'<c r="{ref}" t="inlineStr"{f" s={chr(34)}{style}{chr(34)}" if style else ""}><is><t xml:space="preserve">{v}</t></is></c>'

    n = len(headers)
    last = _col(n - 1)
    sheet_rows = [f'<row r="1"><c r="A1" t="inlineStr" s="2"><is><t>{escape(title)}</t></is></c></row>',
                  '<row r="2">' + "".join(c(f"{_col(j)}2", h, 1) for j, h in enumerate(headers)) + "</row>"]
    for i, r in enumerate(rows, start=3):
        st = row_styles[i - 3] if row_styles else 0
        sheet_rows.append(f'<row r="{i}">' + "".join(c(f"{_col(j)}{i}", v, st) for j, v in enumerate(r) if v or st)
                          + "</row>")
    cols = "".join(f'<col min="{j + 1}" max="{j + 1}" width="{w}" customWidth="1"/>' for j, w in enumerate(widths))
    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             '<sheetViews><sheetView workbookViewId="0"><pane ySplit="2" topLeftCell="A3" activePane="bottomLeft" '
             'state="frozen"/></sheetView></sheetViews>'
             f"<cols>{cols}</cols><sheetData>{''.join(sheet_rows)}</sheetData>"
             f'<autoFilter ref="A2:{last}{max(2, len(rows) + 2)}"/></worksheet>')
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
              '<fonts count="5"><font><sz val="11"/><name val="Calibri"/></font>'
              '<font><b/><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="14"/><name val="Calibri"/></font>'
              '<font><sz val="11"/><color rgb="FF0A3F22"/><name val="Calibri"/></font>'
              '<font><sz val="11"/><color rgb="FF6E1206"/><name val="Calibri"/></font></fonts>'
              '<fills count="5"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
              '<fill><patternFill patternType="solid"><fgColor rgb="FFE2F0F6"/></patternFill></fill>'
              '<fill><patternFill patternType="solid"><fgColor rgb="FFA8DFBA"/></patternFill></fill>'
              '<fill><patternFill patternType="solid"><fgColor rgb="FFF5AEA3"/></patternFill></fill></fills>'
              '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
              '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
              '<cellXfs count="5"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
              '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>'
              '<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
              '<xf numFmtId="0" fontId="3" fillId="3" borderId="0" xfId="0" applyFont="1" applyFill="1"/>'
              '<xf numFmtId="0" fontId="4" fillId="4" borderId="0" xfId="0" applyFont="1" applyFill="1"/></cellXfs>'
              '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
              '</styleSheet>')
    files = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/styles.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>'),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
            'officeDocument" Target="xl/workbook.xml"/></Relationships>'),
        "xl/workbook.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Bao cao" sheetId="1" r:id="rId1"/></sheets>'
            f'<definedNames><definedName name="_xlnm._FilterDatabase" localSheetId="0" hidden="1">'
            f"'Bao cao'!$A$2:${last}${max(2, len(rows) + 2)}</definedName></definedNames></workbook>"),
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
            'worksheet" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
            'styles" Target="styles.xml"/></Relationships>'),
        "xl/styles.xml": styles,
        "xl/worksheets/sheet1.xml": sheet,
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, content in files.items():
            z.writestr(name, content)


def write_csv(path: str, headers: list[str], rows: list[list[str]]) -> None:
    # UTF-8 có BOM để Excel đọc đúng tiếng Việt
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(headers)
        w.writerows(rows)


# ---------------------------------------------------------------------------------------- màn hình
class RangeMixin:
    """Ô "Từ … đến …" + nút chọn nhanh. Lớp dùng cần có _run() (Enter) và có thể ghi đè _range_changed()."""
    v_from: tk.StringVar
    v_to: tk.StringVar

    def _date_row(self, parent, a: date, b: date) -> tk.Frame:
        row = tk.Frame(parent, bg=T.SURFACE)
        self.v_from = tk.StringVar(value=f"{a:%d/%m/%Y}")
        self.v_to = tk.StringVar(value=f"{b:%d/%m/%Y}")
        label(row, "Từ", T.FONT, T.MUTED).pack(side="left", padx=(0, px(6)))
        self._entry(row, self.v_from, 12).pack(side="left")
        label(row, "đến", T.FONT, T.MUTED).pack(side="left", padx=px(8))
        self._entry(row, self.v_to, 12).pack(side="left", padx=(0, px(14)))
        for text, fn in (("Hôm nay", self._q_today), ("Hôm qua", self._q_yesterday),
                         ("Tháng này", self._q_month), ("Tháng trước", self._q_last_month)):
            FlatButton(row, text, fn, padx=10, pady=4).pack(side="left", padx=(0, px(6)))
        return row

    def _entry(self, parent, var, width) -> tk.Frame:
        box = tk.Frame(parent, bg=T.SURFACE, highlightthickness=1, highlightbackground=T.LINE, highlightcolor=T.ACCENT)
        e = tk.Entry(box, textvariable=var, width=width, font=T.FONT, bg=T.SURFACE, fg=T.FG, bd=0,
                     insertbackground=T.FG, justify="center")
        e.pack(padx=px(6), pady=px(4))
        e.bind("<Return>", lambda _e: self._run())
        return box

    def _read_range(self) -> tuple[date, date]:
        a, b = parse_day(self.v_from.get()), parse_day(self.v_to.get())
        if b < a:
            raise ValueError("Ngày kết thúc phải sau hoặc bằng ngày bắt đầu.")
        return a, b

    def _range_changed(self):
        pass

    def _set_range(self, a: date, b: date):
        self.v_from.set(f"{a:%d/%m/%Y}")
        self.v_to.set(f"{b:%d/%m/%Y}")
        self._range_changed()

    def _q_today(self):
        d = date.today()
        self._set_range(d, d)

    def _q_yesterday(self):
        d = date.today() - timedelta(days=1)
        self._set_range(d, d)

    def _q_month(self):
        d = date.today()
        self._set_range(d.replace(day=1), d)

    def _q_last_month(self):
        first = add_months(date.today().replace(day=1), -1)
        self._set_range(first, date.today().replace(day=1) - timedelta(days=1))


SUBS = [("Y lệnh dịch vụ (HIS)", "Danh sách y lệnh có dịch vụ trong danh sách mã, đọc trực tiếp từ HIS (chỉ SELECT), "
                                 "đối chiếu với cảnh báo trên máy, xuất ra Excel."),
        ("Danh sách cảnh báo", "Các quyết định cảnh báo lưu trên máy này (gồm dữ liệu đã gộp từ máy khác), "
                               "không cần kết nối HIS, xuất ra Excel.")]


class ReportPage(RangeMixin, tk.Frame):
    def __init__(self, parent, main: "ManagerWindow"):
        super().__init__(parent, bg=T.BG)
        self.main = main
        self._worker: his.Worker | None = None
        self._busy = False
        self.rows: list[dict] = []
        self.range: tuple[date, date] | None = None
        self.codes_used: list[str] = []

        sf = ScrollFrame(self)
        sf.pack(fill="both", expand=True)
        root = tk.Frame(sf.inner, bg=T.BG)
        root.pack(fill="both", expand=True, padx=px(24), pady=px(18))
        _, self.sub_lbl, _ = page_header(root, "Xuất báo cáo", SUBS[0][1])
        self.seg = SegmentedControl(root, [t for t, _ in SUBS], self.show_sub, padx=18, pady=6)
        self.seg.pack(anchor="w", pady=(0, px(12)))
        self.f_his = tk.Frame(root, bg=T.BG)
        self.f_his.pack(fill="both", expand=True)
        self.f_alert = AlertExport(root, self)

        # --- Điều kiện
        card = Card(self.f_his, "Điều kiện")
        card.pack(fill="x", pady=(0, px(12)))
        g = card.body
        g.columnconfigure(1, weight=1)

        label(g, "Khoảng ngày", T.BOLD).grid(row=0, column=0, sticky="nw", padx=(0, px(18)), pady=(px(6), 0))
        today = date.today()
        self._date_row(g, today, today).grid(row=0, column=1, sticky="w", pady=px(3))

        label(g, "Mã dịch vụ", T.BOLD).grid(row=1, column=0, sticky="nw", padx=(0, px(18)), pady=(px(10), 0))
        cell = tk.Frame(g, bg=T.SURFACE)
        cell.grid(row=1, column=1, sticky="we", pady=(px(7), px(3)))
        box = tk.Frame(cell, bg=T.SURFACE, highlightthickness=1, highlightbackground=T.LINE, highlightcolor=T.ACCENT)
        box.pack(fill="x")
        self.t_codes = tk.Text(box, height=3, font=T.FONT, bg=T.SURFACE, fg=T.FG, bd=0, wrap="word",
                               insertbackground=T.FG, padx=px(6), pady=px(4))
        self.t_codes.pack(fill="x")
        self.t_codes.bind("<<Modified>>", self._codes_modified)
        hint = tk.Frame(cell, bg=T.SURFACE)
        hint.pack(fill="x", pady=(px(4), 0))
        self.codes_info = label(hint, "", T.SMALL, T.MUTED, justify="left")
        self.codes_info.pack(side="left")
        FlatButton(hint, "Khôi phục mặc định", self._codes_default, padx=10, pady=3).pack(side="right")
        FlatButton(hint, "Lưu danh sách mã", self._codes_save, padx=10, pady=3).pack(side="right", padx=(0, px(6)))

        act = tk.Frame(g, bg=T.SURFACE)
        act.grid(row=2, column=1, sticky="w", pady=(px(10), 0))
        self.btn_run = FlatButton(act, "Lấy dữ liệu từ HIS", self._run, primary=True)
        self.btn_run.pack(side="left", padx=(0, px(8)))
        self.btn_xlsx = FlatButton(act, "Xuất Excel…", lambda: self._export("xlsx"))
        self.btn_xlsx.pack(side="left", padx=(0, px(8)))
        FlatButton(act, "Xuất CSV…", lambda: self._export("csv")).pack(side="left")
        self.status = label(g, "", T.FONT, T.MUTED, justify="left", wraplength=px(900))
        self.status.grid(row=3, column=1, sticky="w", pady=(px(8), 0))

        # --- Kết quả
        mid = tk.Frame(self.f_his, bg=T.BG)
        mid.pack(fill="x", pady=(0, px(12)))
        c1 = Card(mid, "Theo dịch vụ", body_pad=False)
        c1.pack(fill="x")
        fr, self.t_sum = make_tree(c1.body, [("c", "Mã dịch vụ", 130, "w", False), ("n", "Tên dịch vụ", 280, "w", True),
                                             ("k", "Số y lệnh", 80, "e", False), ("b", "Số bệnh nhân", 100, "e", False),
                                             ("x", "Không cảnh báo", 115, "e", False),
                                             ("d", "CB – được chụp", 115, "e", False),
                                             ("r", "CB – không chụp", 120, "e", False)],
                                   height=4)
        fr.pack(fill="both", expand=True, padx=px(6), pady=px(4))

        self.c_rows = Card(self.f_his, "Chi tiết", body_pad=False)
        self.c_rows.pack(fill="x")
        self.legend: dict[str, tk.Label] = {}
        for st, (bg, fg) in ((ST_NONE, (T.SURFACE, T.FG)), (ST_DONE, (GREEN_BG, GREEN_FG)),
                             (ST_REFUSED, (RED_BG, RED_FG))):
            lb = tk.Label(self.c_rows.header_right, text="", font=T.SMALL_BOLD, fg=fg, bg=bg, padx=px(8), pady=px(2),
                          highlightthickness=1, highlightbackground=fg if bg != T.SURFACE else T.LINE)
            lb.pack(side="left", padx=(px(6), 0))
            self.legend[st] = lb
        fr, self.t_rows = make_tree(self.c_rows.body, [(k, t, w, "e" if k == "stt" else "w", False)
                                                        for k, t, w, _ in COLUMNS], height=14)
        self.t_rows.tag_configure("st_done", background=GREEN_BG, foreground=GREEN_FG)
        self.t_rows.tag_configure("st_refused", background=RED_BG, foreground=RED_FG)
        xsb = ttk.Scrollbar(self.c_rows.body, orient="horizontal", command=self.t_rows.xview)
        self.t_rows.configure(xscrollcommand=xsb.set)
        fr.pack(fill="both", expand=True, padx=px(6), pady=(px(4), 0))
        xsb.pack(fill="x", padx=px(6), pady=(0, px(4)))
        store.subscribe(self._local_changed)

        self._set_codes(load_settings().get("report_service_codes") or his.DEFAULT_REPORT_CODES)
        self._show_rows()

    def show_sub(self, i: int):
        """0: Y lệnh dịch vụ (HIS), 1: Danh sách cảnh báo."""
        self.seg.select(i)
        self.sub_lbl.configure(text=SUBS[i][1])
        for k, f in enumerate((self.f_his, self.f_alert)):
            if k == i:
                f.pack(fill="both", expand=True)
            else:
                f.pack_forget()
        if i == 1:
            self.f_alert.refresh()

    # ------------------------------------------------------------------ ô nhập
    def _codes(self) -> list[str]:
        return parse_codes(self.t_codes.get("1.0", "end"))

    def _set_codes(self, codes: list[str]):
        self.t_codes.delete("1.0", "end")
        self.t_codes.insert("1.0", ", ".join(codes))
        self._codes_info()

    def _codes_modified(self, _e=None):
        if self.t_codes.edit_modified():
            self.t_codes.edit_modified(False)
            self._codes_info()

    def _codes_info(self):
        codes = self._codes()
        saved = load_settings().get("report_service_codes") or his.DEFAULT_REPORT_CODES
        bad = [c for c in codes if not his.SERVICE_CODE.match(c)]
        text = f"{len(codes)} mã. Cách nhau bằng dấu phẩy, khoảng trắng hoặc xuống dòng."
        if bad:
            text += f"  Mã không hợp lệ: {', '.join(bad)}"
        elif codes != list(saved):
            text += "  (chưa lưu: chỉ dùng cho lần lấy dữ liệu này)"
        self.codes_info.configure(text=text, fg=T.DANGER if bad else T.MUTED)

    def _codes_save(self):
        codes = self._codes()
        bad = [c for c in codes if not his.SERVICE_CODE.match(c)]
        if not codes or bad:
            self._show("Danh sách mã trống hoặc có mã không hợp lệ: chưa lưu.", T.DANGER)
            return
        save_settings(report_service_codes=codes)
        self._set_codes(codes)
        self._show(f"Đã lưu {len(codes)} mã dịch vụ. Lần sau mở app sẽ dùng danh sách này.", T.SUCCESS)

    def _codes_default(self):
        self._set_codes(his.DEFAULT_REPORT_CODES)
        self._show("Đã điền lại 4 mã mặc định. Bấm \"Lưu danh sách mã\" nếu muốn giữ.", T.MUTED)

    def _show(self, text: str, color=T.FG):
        self.status.configure(text=text, fg=color)

    # ------------------------------------------------------------------ lấy dữ liệu
    def _run(self):
        if self._busy:
            return
        try:
            a, b = self._read_range()
            if (b - a).days + 1 > MAX_DAYS:
                raise ValueError(f"Khoảng ngày tối đa {MAX_DAYS} ngày (để không làm nặng database HIS).")
            codes = self._codes()
            if not codes:
                raise ValueError("Nhập ít nhất một mã dịch vụ.")
            bad = [c for c in codes if not his.SERVICE_CODE.match(c)]
            if bad:
                raise ValueError(f"Mã dịch vụ không hợp lệ: {', '.join(bad)}")
        except ValueError as e:
            self._show(str(e), T.DANGER)
            return
        if not store.his:
            self._show("Chưa cấu hình kết nối HIS: vào tab Kết nối HIS.", T.DANGER)
            return
        if self._worker is None:
            self._worker = his.Worker()

        def job():
            cfg = his.load_config()
            if cfg is None:
                raise RuntimeError("Chưa có his.json hoặc kết nối HIS đang tắt.")
            return his.HisClient(cfg).fetch_report(a, b, codes)

        self._busy = True
        self.btn_run.configure(text="Đang lấy dữ liệu…")
        self._show(f"Đang đọc HIS từ {a:%d/%m/%Y} đến {b:%d/%m/%Y}, {len(codes)} mã dịch vụ…", T.MUTED)
        self._worker.submit(job, lambda res, err: self._done(res, err, (a, b), codes))
        self._pump()

    def _pump(self):
        self._worker.pump()
        if self._busy:
            self.after(150, self._pump)

    def _done(self, res, err, rng, codes):
        self._busy = False
        self.btn_run.configure(text="Lấy dữ liệu từ HIS")
        if err:
            h = his.hint_for(err)
            self._show(f"✖  {his.error_text(err)}" + (f"\n→ {h}" if h else ""), T.DANGER)
            return
        self.rows, cut = res
        self.range, self.codes_used = rng, codes
        self._show_rows()
        a, b = rng
        msg = f"✔  {len(self.rows)} dòng, từ {a:%d/%m/%Y} đến {b:%d/%m/%Y} (đọc lúc {datetime.now():%H:%M:%S})."
        if cut:
            msg += f"\nChỉ lấy {his.REPORT_MAX_ROWS} dòng đầu: thu hẹp khoảng ngày để lấy đủ."
        self._show(msg, T.WARN if cut else T.SUCCESS)

    def _local_changed(self):
        if self.rows:                       # có quyết định mới / vừa gộp dữ liệu: tô màu lại
            self._show_rows()

    def _show_rows(self):
        match_alerts(self.rows, store.log)
        self.t_rows.delete(*self.t_rows.get_children())
        self.t_sum.delete(*self.t_sum.get_children())
        for i, r in enumerate(self.rows[:3000], start=1):
            tag = STATUS[r["_status"]][1]
            self.t_rows.insert("", "end", tags=(tag,) if tag else (),
                               values=[str(i)] + [cell_text(k, r) for k, *_ in COLUMNS[1:]])
        cnt = Counter(r["_status"] for r in self.rows)
        for st, lb in self.legend.items():
            lb.configure(text=f"{STATUS[st][0]}: {cnt.get(st, 0)}")
        self.c_rows.title.configure(text=f"Chi tiết ({len(self.rows)} dòng"
                                         + (", hiện 3000 dòng đầu, file xuất có đủ" if len(self.rows) > 3000 else "") + ")")
        if not self.rows:
            self.t_sum.insert("", "end", values=("", "Chưa có dữ liệu. Chọn ngày, bấm Lấy dữ liệu từ HIS.", "", "", "", "", ""),
                              tags=("muted",))
            return
        n = Counter(cell_text("ma_dich_vu", r) for r in self.rows)
        names: dict[str, str] = {}
        pats: dict[str, set] = {}
        for r in self.rows:
            c = cell_text("ma_dich_vu", r)
            names.setdefault(c, cell_text("ten_dich_vu", r))
            pats.setdefault(c, set()).add(cell_text("ma_benh_nhan", r) or cell_text("ma_dieu_tri", r))
        order = [c for c in self.codes_used if c in n] + sorted(c for c in n if c not in self.codes_used)
        for c in order:
            k = Counter(r["_status"] for r in self.rows if cell_text("ma_dich_vu", r) == c)
            self.t_sum.insert("", "end", values=(c, names[c], n[c], len(pats[c]),
                                                 k.get(ST_NONE, 0), k.get(ST_DONE, 0), k.get(ST_REFUSED, 0)))
        for c in self.codes_used:
            if c not in n:
                self.t_sum.insert("", "end", values=(c, "(không có y lệnh trong khoảng ngày này)", 0, 0, 0, 0, 0),
                                  tags=("muted",))

    # ------------------------------------------------------------------ xuất file
    def _export(self, kind: str):
        from tkinter import filedialog, messagebox
        if not self.rows or not self.range:
            self._show("Chưa có dữ liệu để xuất: bấm Lấy dữ liệu từ HIS trước.", T.DANGER)
            return
        a, b = self.range
        span = f"{a:%Y%m%d}" if a == b else f"{a:%Y%m%d}-{b:%Y%m%d}"
        ext = ".xlsx" if kind == "xlsx" else ".csv"
        path = filedialog.asksaveasfilename(
            parent=self, title="Lưu báo cáo", defaultextension=ext, initialfile=f"BaoCao_DichVu_{span}{ext}",
            filetypes=[("Excel", "*.xlsx")] if kind == "xlsx" else [("CSV (UTF-8)", "*.csv")])
        if not path:
            return
        match_alerts(self.rows, store.log)
        headers = [t for _, t, _, _ in COLUMNS]
        rows = [[str(i)] + [cell_text(k, r) for k, *_ in COLUMNS[1:]] for i, r in enumerate(self.rows, start=1)]
        try:
            if kind == "xlsx":
                when = f"ngày {a:%d/%m/%Y}" if a == b else f"từ {a:%d/%m/%Y} đến {b:%d/%m/%Y}"
                write_xlsx(path, f"Báo cáo dịch vụ {when} – mã: {', '.join(self.codes_used)}", headers, rows,
                           [w for *_, w in COLUMNS], [STATUS[r["_status"]][2] for r in self.rows])
            else:
                write_csv(path, headers, rows)
        except OSError as e:
            messagebox.showerror("Xuất báo cáo", f"Không ghi được file (đang mở trong Excel?):\n{e}", parent=self)
            return
        self._show(f"✔  Đã xuất {len(rows)} dòng ra {path}", T.SUCCESS)
        if messagebox.askyesno("Xuất báo cáo", f"Đã lưu {len(rows)} dòng.\nMở file ngay?", parent=self):
            import os
            try:
                os.startfile(path)
            except OSError:
                pass


# ------------------------------------------------------------------------- danh sách cảnh báo (local)
ALERT_COLUMNS = [  # khóa, tiêu đề, độ rộng màn hình, độ rộng Excel
    ("stt", "STT", 40, 6),
    ("time", "Thời gian", 125, 18),
    ("patient_name", "Tên bệnh nhân", 160, 28),
    ("patient_id", "Mã bệnh nhân", 95, 16),
    ("treatment_code", "Mã điều trị", 100, 16),
    ("decision", "Quyết định", 115, 18),
    ("compare", "Đối chiếu HIS", 270, 44),
    ("his_order", "Mã y lệnh HIS", 105, 18),
    ("his_time", "Thời gian y lệnh", 125, 20),
    ("his_service", "Dịch vụ HIS", 200, 40),
    ("level", "Mức cảnh báo", 100, 14),
    ("clinic", "Phòng khám", 170, 30),
    ("doctor", "Mã bác sĩ", 95, 12),
    ("reason", "Lý do không chụp", 190, 32),
    ("machine", "Máy", 100, 14),
]
DECISIONS = ["Tất cả", "Được chụp", "Không được chụp"]
COMPARES = ["Tất cả", "Khớp", "Lệch"]

# Kết quả đối chiếu: (chữ hiển thị, lệch?)
CMP_NOT_YET = ("Chưa đối chiếu", None)
CMP = {(True, True): ("✔ Khớp: được chụp, có y lệnh trên HIS", False),
       (True, False): ("⚠ Lệch: được chụp nhưng HIS không có y lệnh", True),
       (False, True): ("⚠ Lệch: không được chụp nhưng HIS có y lệnh", True),
       (False, False): ("✔ Khớp: không được chụp, HIS không có y lệnh", False)}


class OrderIndex:
    """Các y lệnh lấy từ HIS (theo danh sách mã dịch vụ), tra theo mã điều trị và mã bệnh nhân."""

    def __init__(self, rows: list[dict], start: date, end: date, codes: list[str]):
        self.start, self.end, self.codes = start, end, codes
        self.fetched_at = datetime.now()
        self.by_treat: dict[str, list[dict]] = {}
        self.by_pat: dict[str, list[dict]] = {}
        for r in rows:
            t, p = _norm(r.get("ma_dieu_tri")), _norm(r.get("ma_benh_nhan"))
            if t:
                self.by_treat.setdefault(t, []).append(r)
            if p:
                self.by_pat.setdefault(p, []).append(r)

    def covers(self, e: AlertRecord) -> bool:
        """Đã đọc HIS đủ khoảng cần cho cảnh báo này: từ ngày cảnh báo đến MATCH_DAYS ngày sau (hoặc đến hôm nay)."""
        d = e.time.date()
        return self.start <= d and (d + timedelta(days=MATCH_DAYS) <= self.end or self.end >= self.fetched_at.date())

    def orders_for(self, e: AlertRecord) -> list[dict]:
        """Y lệnh của ca này: cùng mã điều trị; nếu không có thì cùng mã bệnh nhân, bắt đầu trong khoảng
        từ ngày cảnh báo đến MATCH_DAYS ngày sau."""
        found = self.by_treat.get(_norm(e.treatment_code), []) if e.treatment_code else []
        if not found:
            lo = datetime.combine(e.time.date(), datetime.min.time())
            hi = lo + timedelta(days=MATCH_DAYS + 1)
            pid = _norm(e.patient_id)
            seen = set()
            for r in self.by_pat.get(pid, []) + self.by_treat.get(pid, []):   # mã BN có thể là mã điều trị
                t = r.get("thoi_gian_bat_dau")
                if id(r) not in seen and (not isinstance(t, datetime) or lo <= t < hi):
                    seen.add(id(r))
                    found.append(r)
        return sorted(found, key=lambda r: r.get("thoi_gian_bat_dau") or datetime.min)


def compare_info(e: AlertRecord, idx: OrderIndex | None) -> dict:
    if idx is None or not idx.covers(e):
        return {"compare": CMP_NOT_YET[0], "mismatch": None, "his_order": "", "his_service": "", "his_time": ""}
    orders = idx.orders_for(e)
    text, bad = CMP[(e.done, bool(orders))]

    def join(key):
        vals = []
        for r in orders:
            v = cell_text(key, r)
            if v and v not in vals:
                vals.append(v)
        return "; ".join(vals)
    return {"compare": text, "mismatch": bad, "his_order": join("ma_y_lenh"),
            "his_service": "; ".join(dict.fromkeys(f"{cell_text('ma_dich_vu', r)} {cell_text('ten_dich_vu', r)}".strip()
                                                   for r in orders)),
            "his_time": join("thoi_gian_bat_dau")}


def alert_values(i: int, e: AlertRecord, info: dict) -> list[str]:
    v = {"stt": str(i), "time": f"{e.time:%d/%m/%Y %H:%M:%S}", "patient_name": e.patient_name,
         "patient_id": e.patient_id, "treatment_code": e.treatment_code,
         "decision": "✔ Được chụp" if e.done else "✖ Không được chụp", "level": f"{rules.LEVEL_SHORT[e.level]} · {e.percent}%",
         "clinic": e.clinic, "doctor": e.doctor, "reason": e.reason if not e.done else "", "machine": e.machine, **info}
    return [str(v.get(k) or "") for k, *_ in ALERT_COLUMNS]


class AlertExport(RangeMixin, tk.Frame):
    """Mục "Danh sách cảnh báo": lọc nhật ký cảnh báo trên máy theo ngày, phòng khám, quyết định rồi xuất file.
    Xanh lá = được chụp, đỏ = không được chụp. "Đối chiếu với HIS" đọc y lệnh (theo danh sách mã dịch vụ ở mục
    Y lệnh dịch vụ) để so: được chụp mà HIS không có y lệnh, hoặc không được chụp mà HIS lại có y lệnh, là ca lệch."""

    def __init__(self, parent, page: ReportPage):
        super().__init__(parent, bg=T.BG)
        self.page = page
        self.decision = 0
        self.cmp_filter = 0
        self.rows: list[tuple[AlertRecord, dict]] = []
        self.idx: OrderIndex | None = None
        self._worker: his.Worker | None = None
        self._busy = False
        self._job = None

        card = Card(self, "Điều kiện")
        card.pack(fill="x", pady=(0, px(12)))
        g = card.body
        g.columnconfigure(1, weight=1)
        today = date.today()
        label(g, "Khoảng ngày", T.BOLD).grid(row=0, column=0, sticky="nw", padx=(0, px(18)), pady=(px(6), 0))
        self._date_row(g, today.replace(day=1), today).grid(row=0, column=1, sticky="w", pady=px(3))
        for v in (self.v_from, self.v_to):
            v.trace_add("write", lambda *_: self._later())

        label(g, "Phòng khám", T.BOLD).grid(row=1, column=0, sticky="nw", padx=(0, px(18)), pady=(px(9), 0))
        row = tk.Frame(g, bg=T.SURFACE)
        row.grid(row=1, column=1, sticky="w", pady=(px(6), px(3)))
        self.cb_clinic = ttk.Combobox(row, state="readonly", width=34, font=T.FONT)
        self.cb_clinic.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        self.cb_clinic.pack(side="left", padx=(0, px(18)))
        label(row, "Quyết định", T.BOLD).pack(side="left", padx=(0, px(10)))
        SegmentedControl(row, DECISIONS, self._set_decision, padx=12).pack(side="left")

        label(g, "Đối chiếu HIS", T.BOLD).grid(row=2, column=0, sticky="nw", padx=(0, px(18)), pady=(px(9), 0))
        row = tk.Frame(g, bg=T.SURFACE)
        row.grid(row=2, column=1, sticky="w", pady=(px(6), px(3)))
        self.seg_cmp = SegmentedControl(row, COMPARES, self._set_cmp, padx=12)
        self.seg_cmp.pack(side="left", padx=(0, px(14)))
        self.cmp_info = label(row, "", T.SMALL, T.MUTED, justify="left")
        self.cmp_info.pack(side="left")

        act = tk.Frame(g, bg=T.SURFACE)
        act.grid(row=3, column=1, sticky="w", pady=(px(10), 0))
        self.btn_cmp = FlatButton(act, "Đối chiếu với HIS", self._compare, primary=True)
        self.btn_cmp.pack(side="left", padx=(0, px(8)))
        FlatButton(act, "Xuất Excel…", lambda: self._export("xlsx")).pack(side="left", padx=(0, px(8)))
        FlatButton(act, "Xuất CSV…", lambda: self._export("csv")).pack(side="left")
        self.status = label(g, "", T.FONT, T.MUTED, justify="left", wraplength=px(900))
        self.status.grid(row=4, column=1, sticky="w", pady=(px(8), 0))

        self.card = Card(self, "Danh sách cảnh báo", body_pad=False)
        self.card.pack(fill="x")
        self.legend: dict[str, tk.Label] = {}
        for key, bg, fg in (("done", GREEN_BG, GREEN_FG), ("refused", RED_BG, RED_FG),
                            ("ok", T.SURFACE, T.SUCCESS), ("bad", T.WARN_SOFT, T.WARN)):
            lb = tk.Label(self.card.header_right, text="", font=T.SMALL_BOLD, fg=fg, bg=bg, padx=px(8), pady=px(2),
                          highlightthickness=1, highlightbackground=fg if bg != T.SURFACE else T.LINE)
            self.legend[key] = lb
        fr, self.tree = make_tree(self.card.body, [(k, t, w, "e" if k == "stt" else "w", False)
                                                   for k, t, w, _ in ALERT_COLUMNS], height=16)
        self.tree.tag_configure("st_done", background=GREEN_BG, foreground=GREEN_FG)
        self.tree.tag_configure("st_refused", background=RED_BG, foreground=RED_FG)
        self.tree.tag_configure("mismatch", font=T.BOLD)
        xsb = ttk.Scrollbar(self.card.body, orient="horizontal", command=self.tree.xview)
        self.tree.configure(xscrollcommand=xsb.set)
        fr.pack(fill="both", expand=True, padx=px(6), pady=(px(4), 0))
        xsb.pack(fill="x", padx=px(6), pady=(0, px(4)))
        store.subscribe(self._store_changed)

    # ------------------------------------------------------------------ lọc
    def _run(self):
        self.refresh()

    def _range_changed(self):
        self.refresh()

    def _later(self):                          # đang gõ ngày: đợi gõ xong mới lọc
        if self._job:
            self.after_cancel(self._job)
        self._job = self.after(400, lambda: self.refresh(quiet=True))

    def _set_decision(self, i):
        self.decision = i
        self.refresh()

    def _set_cmp(self, i):
        self.cmp_filter = i
        self.refresh()

    def _store_changed(self):
        if self.winfo_ismapped():
            self.refresh(quiet=True)

    def _clinic(self) -> str | None:
        return None if self.cb_clinic.current() <= 0 else self.cb_clinic.get()

    def refresh(self, quiet=False):
        self._job = None
        names = ["Tất cả phòng khám"] + sorted({e.clinic for e in store.log if e.clinic})
        if list(self.cb_clinic.cget("values")) != names:
            cur = self.cb_clinic.get()
            self.cb_clinic.configure(values=names)
            self.cb_clinic.current(names.index(cur) if cur in names else 0)
        try:
            a, b = self._read_range()
        except ValueError as e:
            if not quiet:
                self.status.configure(text=str(e), fg=T.DANGER)
            return
        clinic = self._clinic()
        self.range = (a, b)
        rows = []
        for e in sorted(store.log, key=lambda x: x.time):
            if not (a <= e.time.date() <= b and (clinic is None or e.clinic == clinic)
                    and (self.decision == 0 or e.done == (self.decision == 1))):
                continue
            info = compare_info(e, self.idx)
            if self.cmp_filter and info["mismatch"] is not (self.cmp_filter == 2):
                continue
            rows.append((e, info))
        self.rows = rows

        self.tree.delete(*self.tree.get_children())
        for i, (e, info) in enumerate(rows[:3000], start=1):
            tags = ["st_done" if e.done else "st_refused"] + (["mismatch"] if info["mismatch"] else [])
            self.tree.insert("", "end", tags=tags, values=alert_values(i, e, info))
        if not rows:
            self.tree.insert("", "end", tags=("muted",), values=("", "Không có cảnh báo nào trong điều kiện này."))

        done = sum(e.done for e, _ in rows)
        ok = sum(info["mismatch"] is False for _, info in rows)
        bad = sum(info["mismatch"] is True for _, info in rows)
        not_yet = sum(info["mismatch"] is None for _, info in rows)
        self.legend["done"].configure(text=f"✔ Được chụp: {done}")
        self.legend["refused"].configure(text=f"✖ Không được chụp: {len(rows) - done}")
        self.legend["ok"].configure(text=f"✔ Khớp HIS: {ok}")
        self.legend["bad"].configure(text=f"⚠ Lệch HIS: {bad}")
        for k, lb in self.legend.items():
            lb.pack_forget()
            if k in ("done", "refused") or self.idx is not None:
                lb.pack(side="left", padx=(px(6), 0))
        self.card.title.configure(text=f"Danh sách cảnh báo ({len(rows)} ca"
                                       + (", hiện 3000 dòng đầu, file xuất có đủ" if len(rows) > 3000 else "") + ")")

        if self.idx is None:
            self.cmp_info.configure(text="Chưa đối chiếu: bấm \"Đối chiếu với HIS\".", fg=T.MUTED)
        else:
            i = self.idx
            self.cmp_info.configure(
                text=f"Đã đọc HIS lúc {i.fetched_at:%H:%M:%S}, y lệnh từ {i.start:%d/%m/%Y} đến {i.end:%d/%m/%Y}, "
                     f"{len(i.codes)} mã dịch vụ · {bad} ca lệch" + (f" · {not_yet} ca ngoài khoảng đã đọc: bấm đối chiếu lại"
                                                      if not_yet else ""),
                fg=T.WARN if not_yet else T.MUTED)
        if not self._busy:
            self.status.configure(text=f"{len(rows)} cảnh báo, từ {a:%d/%m/%Y} đến {b:%d/%m/%Y}"
                                       f" · {clinic or 'tất cả phòng khám'} · {DECISIONS[self.decision].lower()}"
                                       + (f" · đối chiếu: {COMPARES[self.cmp_filter].lower()}" if self.cmp_filter else "")
                                       + ".", fg=T.MUTED)

    # ------------------------------------------------------------------ đối chiếu HIS
    def _compare(self):
        if self._busy:
            return
        try:
            a, b = self._read_range()
            end = b + timedelta(days=MATCH_DAYS)          # y lệnh có thể ra sau ngày cảnh báo
            if (end - a).days + 1 > MAX_DAYS + MATCH_DAYS:
                raise ValueError(f"Khoảng ngày tối đa {MAX_DAYS} ngày (để không làm nặng database HIS).")
            codes = self.page._codes()
            bad = [c for c in codes if not his.SERVICE_CODE.match(c)]
            if not codes or bad:
                raise ValueError("Danh sách mã dịch vụ (ở mục Y lệnh dịch vụ) trống hoặc có mã không hợp lệ.")
        except ValueError as e:
            self.status.configure(text=str(e), fg=T.DANGER)
            return
        if not store.his:
            self.status.configure(text="Chưa cấu hình kết nối HIS: vào tab Kết nối HIS.", fg=T.DANGER)
            return
        if self._worker is None:
            self._worker = his.Worker()

        def job():
            cfg = his.load_config()
            if cfg is None:
                raise RuntimeError("Chưa có his.json hoặc kết nối HIS đang tắt.")
            return his.HisClient(cfg).fetch_report(a, end, codes)

        self._busy = True
        self.btn_cmp.configure(text="Đang đọc HIS…")
        self.status.configure(text=f"Đang đọc y lệnh từ {a:%d/%m/%Y} đến {end:%d/%m/%Y} "
                                   f"({MATCH_DAYS} ngày sau ngày cuối), {len(codes)} mã dịch vụ…", fg=T.MUTED)
        self._worker.submit(job, lambda res, err: self._compared(res, err, a, end, codes))
        self._pump()

    def _pump(self):
        self._worker.pump()
        if self._busy:
            self.after(150, self._pump)

    def _compared(self, res, err, a, end, codes):
        self._busy = False
        self.btn_cmp.configure(text="Đối chiếu với HIS")
        if err:
            h = his.hint_for(err)
            self.status.configure(text=f"✖  {his.error_text(err)}" + (f"\n→ {h}" if h else ""), fg=T.DANGER)
            return
        rows, cut = res
        self.idx = OrderIndex(rows, a, end, codes)
        self.refresh()
        bad = sum(info["mismatch"] is True for _, info in self.rows)
        msg = f"✔  Đã đối chiếu với {len(rows)} y lệnh trên HIS: {bad} ca lệch."
        if cut:
            msg += f"\nHIS trả quá {his.REPORT_MAX_ROWS} dòng, chỉ lấy phần đầu: thu hẹp khoảng ngày để đối chiếu đủ."
        self.status.configure(text=msg, fg=T.WARN if cut or bad else T.SUCCESS)

    # ------------------------------------------------------------------ xuất file
    def _export(self, kind: str):
        from tkinter import filedialog, messagebox
        self.refresh()
        if not self.rows:
            self.status.configure(text="Không có cảnh báo nào để xuất.", fg=T.DANGER)
            return
        a, b = self.range
        span = f"{a:%Y%m%d}" if a == b else f"{a:%Y%m%d}-{b:%Y%m%d}"
        ext = ".xlsx" if kind == "xlsx" else ".csv"
        path = filedialog.asksaveasfilename(
            parent=self, title="Lưu danh sách cảnh báo", defaultextension=ext,
            initialfile=f"DanhSachCanhBao_{span}{ext}",
            filetypes=[("Excel", "*.xlsx")] if kind == "xlsx" else [("CSV (UTF-8)", "*.csv")])
        if not path:
            return
        headers = [t for _, t, _, _ in ALERT_COLUMNS]
        rows = [alert_values(i, e, info) for i, (e, info) in enumerate(self.rows, start=1)]
        try:
            if kind == "xlsx":
                when = f"ngày {a:%d/%m/%Y}" if a == b else f"từ {a:%d/%m/%Y} đến {b:%d/%m/%Y}"
                done = sum(e.done for e, _ in self.rows)
                bad = sum(info["mismatch"] is True for _, info in self.rows)
                title = (f"Danh sách cảnh báo CT mạch vành {when} – {self._clinic() or 'tất cả phòng khám'}"
                         f" – {len(rows)} ca: {done} được chụp, {len(rows) - done} không được chụp"
                         + (f"; đối chiếu HIS: {bad} ca lệch" if self.idx is not None else ""))
                write_xlsx(path, title, headers, rows, [w for *_, w in ALERT_COLUMNS],
                           [3 if e.done else 4 for e, _ in self.rows])
            else:
                write_csv(path, headers, rows)
        except OSError as e:
            messagebox.showerror("Xuất báo cáo", f"Không ghi được file (đang mở trong Excel?):\n{e}", parent=self)
            return
        self.status.configure(text=f"✔  Đã xuất {len(rows)} cảnh báo ra {path}", fg=T.SUCCESS)
        if messagebox.askyesno("Xuất báo cáo", f"Đã lưu {len(rows)} cảnh báo.\nMở file ngay?", parent=self):
            import os
            try:
                os.startfile(path)
            except OSError:
                pass
