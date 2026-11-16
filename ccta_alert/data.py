"""Dữ liệu của app.

- Bệnh nhân, phòng khám: đọc từ HIS (Oracle, chỉ đọc, xem his.py) theo cấu hình his.json.
  Chưa cấu hình thì app không có bệnh nhân nào, và nhắc mở tab "Kết nối HIS".
- Quyết định của bác sĩ (Chỉ định / Không chỉ định): lưu vào alerts.jsonl, mỗi dòng một bản ghi JSON.
  Mở app sẽ đọc lại.
- Lần đánh giá mức Rất thấp (không cần quyết định): lưu riêng vào assessed.jsonl, chỉ để biết bệnh nhân đã được
  thao tác trên app (thống kê "Không thao tác"); không tính vào các thống kê cảnh báo.
- Nhiều máy: chép alerts.jsonl (và assessed.jsonl) của các máy về một máy, dùng "Gộp dữ liệu" ở màn Thống kê (Store.merge_files).
  Bản ghi trùng (đã có) tự bỏ qua, nên gộp lại nhiều lần cũng không đếm đôi.
- Nơi lưu: cùng thư mục với CctaAlert.exe (chạy từ mã nguồn thì là thư mục dist). Nếu thư mục đó
  không ghi được (ví dụ C:\\Program Files) thì lưu vào %APPDATA%\\CctaAlert.
"""
from __future__ import annotations

import json
import os
import re
import socket
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable

from .rules import RiskLevel

REFUSE_REASONS = [
    "Đã chẩn đoán",
    "Bệnh nhân từ chối",
    "Chống chỉ định thuốc cản quang",
    "Suy thận (eGFR thấp)",
    "Đã có kết quả CT gần đây",
    "Chuyển làm nghiệm pháp gắng sức / siêu âm tim",
    "Chuyển chụp mạch vành xâm lấn",
    "Lý do khác",
]


USER_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "CctaAlert")   # dự phòng


def _app_dir() -> str:
    """Thư mục chứa CctaAlert.exe; chạy từ mã nguồn thì là thư mục dist của dự án."""
    if getattr(sys, "frozen", False):           # bản đóng gói PyInstaller
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dist")


def _writable(d: str) -> bool:
    try:
        os.makedirs(d, exist_ok=True)
        with tempfile.TemporaryFile(dir=d):
            pass
        return True
    except OSError:
        return False


def _pick_data_dir() -> str:
    forced = os.environ.get("CCTA_DATA_DIR")      # chỉ định thư mục dữ liệu khác (kiểm tra, thư mục dùng chung...)
    if forced:
        os.makedirs(forced, exist_ok=True)
        return forced
    d = _app_dir()
    return d if _writable(d) else USER_DIR


DATA_DIR = _pick_data_dir()
MACHINE = (os.environ.get("COMPUTERNAME") or socket.gethostname() or "").upper()
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
ALERTS_FILE = os.path.join(DATA_DIR, "alerts.jsonl")      # quyết định của bác sĩ, mỗi dòng một bản ghi
ASSESSED_FILE = os.path.join(DATA_DIR, "assessed.jsonl")  # lần đánh giá mức Rất thấp (không cần quyết định)
HIS_CONFIG_FILE = os.path.join(DATA_DIR, "his.json")      # kết nối HIS (Oracle, chỉ đọc)


def his_enabled() -> bool:
    try:
        with open(HIS_CONFIG_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
        return isinstance(cfg, dict) and bool(cfg.get("enabled", True))
    except Exception:
        return False


def load_settings() -> dict:
    """Cài đặt trên máy này (mã bác sĩ, phòng khám lần trước). Lỗi đọc thì coi như trống."""
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_settings(**kw) -> None:
    try:
        d = load_settings()
        d.update(kw)
        os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


DOCTOR_CODE = re.compile(r"^[A-Z0-9._-]{2,30}$")


def normalize_doctor_code(text: str) -> str:
    """Mã bác sĩ: bỏ khoảng trắng, viết hoa. Chỉ chữ không dấu, số, '.', '_', '-' (2–30 ký tự)."""
    code = text.strip().upper()
    if not code:
        raise ValueError("Nhập mã bác sĩ.")
    if not DOCTOR_CODE.match(code):
        raise ValueError("Mã bác sĩ chỉ gồm chữ không dấu, số, dấu chấm, gạch dưới, gạch ngang (2–30 ký tự).")
    return code


def add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    for day in (d.day, 30, 29, 28):
        try:
            return d.replace(year=y, month=m, day=day)
        except ValueError:
            continue
    raise ValueError(d)


@dataclass
class Patient:
    id: str
    name: str
    sex: str                                      # "Nam" / "Nữ" / "Không xác định"
    dob: date | None                              # None: HIS không có ngày sinh
    clinic: str
    symptom_score: int | None = None              # 0–3, bác sĩ chọn (None = chưa chọn)
    risk_factor_count: int | None = None          # 0–5, bác sĩ chọn
    saved_at: datetime = field(default_factory=datetime.now)               # giờ bắt đầu khám trên HIS
    treatment_code: str = ""                      # mã điều trị (HIS_TREATMENT.TREATMENT_CODE)

    @property
    def age(self) -> int | None:
        if self.dob is None:
            return None
        t = date.today()
        return t.year - self.dob.year - ((t.month, t.day) < (self.dob.month, self.dob.day))

    @property
    def codes_text(self) -> str:
        """Mã BN và mã điều trị để hiển thị, ví dụ 'BN24051287  ·  Mã ĐT 26111000123'."""
        if self.treatment_code and self.treatment_code != self.id:
            return f"{self.id}  ·  Mã ĐT {self.treatment_code}"
        if self.treatment_code:                   # HIS không có mã BN: mã nhận diện chính là mã điều trị
            return f"Mã ĐT {self.treatment_code}"
        return self.id

    @property
    def age_text(self) -> str:
        return "chưa rõ tuổi" if self.age is None else f"{self.age} tuổi"

    @property
    def missing(self) -> str:
        """Thiếu thông tin nào để tính RF-CL (rỗng = đủ)."""
        miss = [k for k, bad in (("ngày sinh", self.dob is None), ("giới tính", self.sex not in ("Nam", "Nữ"))) if bad]
        return " và ".join(miss)

    @property
    def male(self) -> bool:
        return self.sex == "Nam"


@dataclass
class AlertRecord:
    time: datetime
    clinic: str
    doctor: str           # mã bác sĩ nhập khi mở app
    patient_id: str
    patient_name: str
    level: RiskLevel
    percent: int
    done: bool            # True = Được chụp, False = Không được chụp
    reason: str = ""
    treatment_code: str = ""   # mã điều trị lúc ra quyết định
    machine: str = ""          # tên máy ghi bản ghi (bản ghi cũ để trống)
    kind: str = ""             # "" = quyết định (alerts.jsonl); "assessed" = đánh giá Rất thấp (assessed.jsonl)

    @property
    def key(self) -> tuple:
        """Nhận diện một quyết định, để bỏ bản ghi trùng khi gộp dữ liệu nhiều máy."""
        return (self.time.isoformat(timespec="seconds"), self.patient_id, self.doctor, self.clinic, self.done)

    def to_json(self) -> str:
        return json.dumps({
            "time": self.time.isoformat(timespec="seconds"), "clinic": self.clinic, "doctor": self.doctor,
            "patient_id": self.patient_id, "patient_name": self.patient_name, "level": self.level.name,
            "percent": self.percent, "done": self.done, "reason": self.reason,
            "treatment_code": self.treatment_code, "machine": self.machine,
            **({"kind": self.kind} if self.kind else {})}, ensure_ascii=False)

    @classmethod
    def from_json(cls, line: str) -> "AlertRecord":
        d = json.loads(line)
        return cls(datetime.fromisoformat(d["time"]), d["clinic"], d["doctor"], d["patient_id"], d["patient_name"],
                   RiskLevel[d["level"]], int(d["percent"]), bool(d["done"]), d.get("reason", ""),
                   d.get("treatment_code", ""), d.get("machine", ""), d.get("kind", ""))


class Store:
    def __init__(self, his: bool = False) -> None:
        self.his = his                # True: đã cấu hình và bật kết nối HIS
        self.patients: list[Patient] = []
        self.clinics: list[tuple[int, str]] = []        # (mã phòng, tên) đọc từ HIS
        self.his_status = "Đang kết nối HIS…" if his else "chưa cấu hình kết nối"
        self.his_ok: bool | None = None if his else False
        self.log: list[AlertRecord] = []
        self.assessed: list[AlertRecord] = []          # đánh giá Rất thấp (không phải quyết định)
        self._listeners: list[Callable[[], None]] = []
        self.saved_count = 0          # số bản ghi đọc được từ file trên máy
        self.bad_lines = 0            # dòng hỏng trong file (bỏ qua)
        self.save_error = ""          # lỗi ghi file gần nhất, rỗng nếu ghi được
        self._load_saved()

    def update_patients(self, fresh: list[Patient]) -> list[Patient]:
        """Thay danh sách bệnh nhân bằng dữ liệu mới từ HIS. Giữ nguyên lựa chọn bác sĩ đang nhập dở
        (điểm triệu chứng, yếu tố nguy cơ) của người đã có. Trả về những bệnh nhân mới xuất hiện."""
        old = {p.id: p for p in self.patients}
        merged, new = [], []
        for p in fresh:
            o = old.get(p.id)
            if o:
                o.name, o.sex, o.dob, o.clinic, o.saved_at = p.name, p.sex, p.dob, p.clinic, p.saved_at
                o.treatment_code = p.treatment_code
                merged.append(o)
            else:
                merged.append(p)
                new.append(p)
        self.patients = merged
        return new

    def clinic_names(self) -> list[str]:
        """Phòng khám cho màn Thống kê: danh sách đang có cộng những phòng đã có trong nhật ký."""
        names = [n for _, n in self.clinics]
        return names + sorted({e.clinic for e in self.log} - set(names))

    def subscribe(self, fn: Callable[[], None]) -> None:
        self._listeners.append(fn)

    def add(self, rec: AlertRecord) -> bool:
        """Thêm bản ghi và lưu ngay vào file trên máy. Trả về False nếu không ghi được file."""
        rec.machine = rec.machine or MACHINE
        self.log.append(rec)
        ok = self._append(rec)
        for fn in self._listeners:
            fn()
        return ok

    def add_assessed(self, rec: AlertRecord) -> bool:
        """Ghi lần đánh giá mức Rất thấp (bác sĩ đã thao tác nhưng không cần quyết định)."""
        rec.machine = rec.machine or MACHINE
        rec.kind = "assessed"
        self.assessed.append(rec)
        ok = self._append(rec, ASSESSED_FILE)
        for fn in self._listeners:
            fn()
        return ok

    # ------------------------------------------------------------------ lưu trên máy
    def _append(self, rec: AlertRecord, path: str = ALERTS_FILE) -> bool:
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            broken_tail = False          # dòng cuối bị cắt dở (mất điện lúc ghi): xuống dòng trước khi ghi tiếp
            try:
                with open(path, "rb") as r:
                    r.seek(-1, os.SEEK_END)
                    broken_tail = r.read(1) != b"\n"
            except OSError:              # chưa có file hoặc file rỗng
                pass
            with open(path, "a", encoding="utf-8", newline="\n") as f:
                f.write(("\n" if broken_tail else "") + rec.to_json() + "\n")
                f.flush()
                os.fsync(f.fileno())
            self.save_error = ""
            return True
        except OSError as e:
            self.save_error = str(e)
            return False

    def merge_files(self, paths: list[str]) -> list[tuple[str, int, int, int, str]]:
        """Gộp alerts.jsonl (và assessed.jsonl) của máy khác vào máy này. Bản ghi đánh giá Rất thấp
        (kind = "assessed") được ghi vào assessed.jsonl, còn lại vào alerts.jsonl.
        Trả về mỗi file: (tên file, số bản ghi mới, số trùng, số dòng hỏng, lỗi đọc file).
        Bản ghi mới được ghi thêm vào alerts.jsonl của máy này (một lần ghi), nên lần sau mở app vẫn còn."""
        seen = {r.key for r in self.log} | {("a",) + r.key for r in self.assessed}
        report, fresh, fresh_a = [], [], []
        for path in paths:
            added = dup = bad = 0
            err = ""
            try:
                with open(path, encoding="utf-8-sig") as f:
                    lines = f.readlines()
            except (OSError, UnicodeDecodeError) as e:
                report.append((os.path.basename(path), 0, 0, 0, str(e)))
                continue
            for line in lines:
                if not line.strip():
                    continue
                try:
                    r = AlertRecord.from_json(line)
                except (ValueError, KeyError, TypeError):
                    bad += 1
                    continue
                k = ("a",) + r.key if r.kind == "assessed" else r.key
                if k in seen:
                    dup += 1
                    continue
                seen.add(k)
                (fresh_a if r.kind == "assessed" else fresh).append(r)
                added += 1
            report.append((os.path.basename(path), added, dup, bad, err))
        if fresh or fresh_a:
            for recs, path in ((fresh, ALERTS_FILE), (fresh_a, ASSESSED_FILE)):
                if not recs:
                    continue
                recs.sort(key=lambda r: r.time)
                try:
                    os.makedirs(DATA_DIR, exist_ok=True)
                    with open(path, "a", encoding="utf-8", newline="\n") as f:
                        f.write("".join(r.to_json() + "\n" for r in recs))
                        f.flush()
                        os.fsync(f.fileno())
                    self.save_error = ""
                except OSError as e:          # không ghi được: không đưa vào thống kê, báo lỗi
                    self.save_error = str(e)
                    return [(n, 0, d, b, f"Không ghi được vào {path}: {e}") for n, _, d, b, _ in report]
                target = self.assessed if path == ASSESSED_FILE else self.log
                target.extend(recs)
                target.sort(key=lambda r: r.time)
            for fn in self._listeners:
                fn()
        return report

    def machines(self) -> list[str]:
        """Các máy có trong dữ liệu (bản ghi cũ không có tên máy thì không tính)."""
        return sorted({r.machine for r in self.log if r.machine})

    def _load_saved(self) -> None:
        try:
            with open(ALERTS_FILE, encoding="utf-8") as f:
                lines = f.readlines()
        except FileNotFoundError:
            return
        except OSError as e:
            self.save_error = str(e)
            return
        keys: set = set()
        for line in lines:
            if not line.strip():
                continue
            try:
                r = AlertRecord.from_json(line)
                if r.key in keys:
                    continue
                keys.add(r.key)
                self.log.append(r)
                self.saved_count += 1
            except (ValueError, KeyError, TypeError):
                self.bad_lines += 1     # ví dụ dòng cuối bị cắt do mất điện: bỏ qua, giữ phần còn lại
        self.log.sort(key=lambda r: r.time)
        try:
            with open(ASSESSED_FILE, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        try:
                            self.assessed.append(AlertRecord.from_json(line))
                        except (ValueError, KeyError, TypeError):
                            pass
        except OSError:
            pass
        self.assessed.sort(key=lambda r: r.time)

    def last_indication(self, patient_id: str) -> AlertRecord | None:
        """Lần gần nhất bệnh nhân được chỉ định chụp CT mạch vành (để không chỉ định lại)."""
        return next((r for r in reversed(self.log) if r.done and r.patient_id == patient_id), None)

    def last_decision(self, patient_id: str) -> AlertRecord | None:
        """Quyết định gần nhất của bác sĩ cho bệnh nhân (Chỉ định hoặc Không chỉ định)."""
        return next((r for r in reversed(self.log) if r.patient_id == patient_id), None)


store = Store(his=his_enabled())
