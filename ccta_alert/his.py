"""Đọc dữ liệu từ HIS (Oracle). CHỈ ĐỌC — không bao giờ ghi, sửa hay xóa gì trong database.

Các lớp bảo vệ:
  1. Chỉ có các câu SELECT viết sẵn trong file này (SQL_CLINICS, SQL_PATIENTS, SQL_REPORT, SQL_HISTORY,
     SQL_VISITS). Không có chỗ nào
     ghép SQL từ dữ liệu người dùng; giá trị (ngày, mã phòng, mã dịch vụ) truyền bằng bind variable
     (:tu, :den, :phong, :c0, :c1...).
  2. Trước khi chạy, `ensure_select()` kiểm tra câu lệnh: phải bắt đầu bằng SELECT/WITH, không có dấu ';',
     không có từ khóa ghi/khóa (INSERT, UPDATE, DELETE, MERGE, ... , FOR UPDATE). Sai là không chạy.
  3. Không bao giờ gọi commit. Mỗi lần đọc mở kết nối mới rồi đóng ngay; đóng mà không commit thì Oracle
     tự rollback, nên kể cả có lỗi lập trình cũng không có gì được lưu.
  4. Giới hạn số dòng (MAX_ROWS) và thời gian chờ (call_timeout) để không làm nặng database.
  5. Khuyến nghị: dùng tài khoản Oracle chỉ được cấp quyền SELECT trên 3 bảng HIS_TREATMENT,
     HIS_SERVICE_REQ, HIS_EXECUTE_ROOM (và HIS_SERE_SERV nếu dùng tab Xuất báo cáo). Đây mới là bảo đảm chắc chắn nhất, từ phía database.

Cấu hình: file his.json cùng thư mục dữ liệu (cạnh CctaAlert.exe), xem his.example.json.
Không có his.json (hoặc "enabled": false) thì app không có bệnh nhân, nhắc cấu hình ở tab "Kết nối HIS".
"""
from __future__ import annotations

import base64
import ctypes
import json
import os
import queue
import re
import threading
from ctypes import wintypes
from datetime import date, datetime, time
from typing import Any, Callable

from . import _oracle_deps  # noqa: F401  (để exe đóng gói đủ module chuẩn oracledb cần, xem file đó)
from .data import HIS_CONFIG_FILE, Patient

MAX_ROWS = 5000            # chặn trên số dòng đọc mỗi lần
CALL_TIMEOUT_MS = 20000    # quá 20 giây thì bỏ, không treo app

# ---------------------------------------------------------------------------------------------- SQL
# Danh sách phòng khám (theo câu của bạn, thêm ORDER BY).
SQL_CLINICS = """
SELECT
    er.ROOM_ID AS ma_phong,
    er.EXECUTE_ROOM_NAME AS ten_phong
FROM HIS_EXECUTE_ROOM er
WHERE
    er.IS_ACTIVE = 1
    AND er.EXECUTE_ROOM_NAME LIKE '%Khám%'
ORDER BY er.EXECUTE_ROOM_NAME
"""

# Bệnh nhân khám tại một phòng trong một ngày (theo câu của bạn). Khác biệt:
#   - thêm t.TDL_PATIENT_CODE (mã BN) để nhận ra cùng một người giữa các lần khám — cần cho
#     dòng "đã được chỉ định chụp". Nếu HIS không có cột này (ORA-00904), app tự dùng
#     SQL_PATIENTS_NO_CODE bên dưới và lấy mã điều trị làm mã nhận diện;
#   - lấy ngày sinh đầy đủ (TDL_PATIENT_DOB) thay vì chỉ năm, để tính tuổi chính xác;
#   - lấy mã giới tính thô, đổi sang Nam/Nữ trong Python (1 = Nữ, 2 = Nam như câu gốc);
#   - ngày và mã phòng là bind variable thay vì viết cứng.
SQL_PATIENTS = """
SELECT
    t.TDL_PATIENT_CODE AS ma_benh_nhan,
    t.TREATMENT_CODE AS ma_dieu_tri,
    t.TDL_PATIENT_NAME AS ten_benh_nhan,
    t.TDL_PATIENT_DOB AS ngay_sinh,
    t.TDL_PATIENT_GENDER_ID AS gioi_tinh_id,
    sr.START_TIME AS bat_dau_kham,
    sr.FINISH_TIME AS ket_thuc_kham
FROM HIS_TREATMENT t
JOIN HIS_SERVICE_REQ sr ON t.ID = sr.TREATMENT_ID
WHERE
    sr.START_TIME BETWEEN :tu AND :den
    AND sr.EXECUTE_ROOM_ID = :phong
ORDER BY sr.START_TIME DESC
"""
# Dự phòng khi HIS không có cột TDL_PATIENT_CODE: đúng câu trên, bỏ dòng mã BN.
SQL_PATIENTS_NO_CODE = SQL_PATIENTS.replace("    t.TDL_PATIENT_CODE AS ma_benh_nhan,\n", "")

# Báo cáo (tab "Xuất báo cáo", theo câu của bạn): các y lệnh có dịch vụ trong danh sách mã, trong khoảng ngày.
# Danh sách mã sửa được trên màn hình; mỗi mã là một bind variable (:c0, :c1, ...), chỉ số lượng chỗ
# giữ chỗ được sinh ra, giá trị không bao giờ ghép vào câu SQL.
SQL_REPORT = """
SELECT
    t.TREATMENT_CODE AS ma_dieu_tri,
    t.TDL_PATIENT_CODE AS ma_benh_nhan,
    t.TDL_PATIENT_NAME AS ten_benh_nhan,
    sr.SERVICE_REQ_CODE AS ma_y_lenh,
    ss.TDL_SERVICE_CODE AS ma_dich_vu,
    ss.TDL_SERVICE_NAME AS ten_dich_vu,
    sr.START_TIME AS thoi_gian_bat_dau,
    sr.FINISH_TIME AS thoi_gian_ket_thuc
FROM HIS_TREATMENT t
LEFT JOIN HIS_SERVICE_REQ sr ON sr.TREATMENT_ID = t.ID
LEFT JOIN HIS_SERE_SERV ss ON ss.SERVICE_REQ_ID = sr.ID
WHERE
    sr.START_TIME BETWEEN :tu AND :den
    AND ss.TDL_SERVICE_CODE IN ({codes})
    AND ss.IS_DELETE = 0
    AND sr.IS_DELETE = 0
    AND t.IS_DELETE = 0
ORDER BY sr.START_TIME, t.TREATMENT_CODE
"""
REPORT_MAX_ROWS = 20000

# Lịch sử dịch vụ của MỘT bệnh nhân (nhãn cạnh tên trên khung cảnh báo): bệnh nhân đã từng có y lệnh với các
# mã dịch vụ trong danh sách (cùng danh sách của tab Xuất báo cáo) hay chưa. Lọc theo mã bệnh nhân, mới nhất trước.
# {who} là cột nhận diện: t.TDL_PATIENT_CODE, hoặc t.TREATMENT_CODE khi HIS không có cột mã BN.
SQL_HISTORY = """
SELECT
    sr.START_TIME AS thoi_gian_bat_dau,
    sr.FINISH_TIME AS thoi_gian_ket_thuc,
    sr.SERVICE_REQ_CODE AS ma_y_lenh,
    ss.TDL_SERVICE_CODE AS ma_dich_vu,
    ss.TDL_SERVICE_NAME AS ten_dich_vu,
    t.TREATMENT_CODE AS ma_dieu_tri
FROM HIS_TREATMENT t
JOIN HIS_SERVICE_REQ sr ON sr.TREATMENT_ID = t.ID
JOIN HIS_SERE_SERV ss ON ss.SERVICE_REQ_ID = sr.ID
WHERE
    {who} = :ma
    AND ss.TDL_SERVICE_CODE IN ({codes})
    AND ss.IS_DELETE = 0
    AND sr.IS_DELETE = 0
    AND t.IS_DELETE = 0
ORDER BY sr.START_TIME DESC
"""
HISTORY_MAX_ROWS = 20

# Lượt khám (bệnh nhân × phòng × ngày) tại các phòng trong khoảng ngày, cho thống kê "Không thao tác trên app".
# Cùng nguồn với SQL_PATIENTS (bệnh nhân hiện trên khung cảnh báo), gộp các y lệnh cùng ngày cùng phòng.
# {pcode}: cột mã BN, để trống khi HIS không có cột TDL_PATIENT_CODE.
SQL_VISITS = """
SELECT
    {pcode}t.TREATMENT_CODE AS ma_dieu_tri,
    t.TDL_PATIENT_NAME AS ten_benh_nhan,
    sr.EXECUTE_ROOM_ID AS ma_phong,
    MIN(sr.START_TIME) AS bat_dau_kham
FROM HIS_TREATMENT t
JOIN HIS_SERVICE_REQ sr ON t.ID = sr.TREATMENT_ID
WHERE
    sr.START_TIME BETWEEN :tu AND :den
    AND sr.EXECUTE_ROOM_ID IN ({rooms})
GROUP BY {pgroup}t.TREATMENT_CODE, t.TDL_PATIENT_NAME, sr.EXECUTE_ROOM_ID, TRUNC(sr.START_TIME / 1000000)
ORDER BY MIN(sr.START_TIME)
"""
DEFAULT_REPORT_CODES = ["HA-N0300-043", "HA-N0200-001", "HA-N0300-027", "HA-N0300-042"]
SERVICE_CODE = re.compile(r"^[A-Za-z0-9._/-]{1,40}$")

_WRITE_WORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|UPSERT|DROP|ALTER|CREATE|TRUNCATE|RENAME|GRANT|REVOKE|COMMIT|ROLLBACK|"
    r"SAVEPOINT|LOCK|CALL|EXEC|EXECUTE|BEGIN|DECLARE|COMMENT|PURGE|FLASHBACK|ANALYZE|AUDIT)\b", re.I)


def ensure_select(sql: str) -> str:
    """Chỉ cho phép một câu SELECT thuần. Ném lỗi nếu có bất cứ dấu hiệu ghi/khóa dữ liệu."""
    body = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    body = re.sub(r"--[^\n]*", " ", body)
    no_str = re.sub(r"'(?:[^']|'')*'", "''", body)            # bỏ nội dung chuỗi như '%Khám%'
    head = no_str.strip().split(None, 1)[0].upper() if no_str.strip() else ""
    if head not in ("SELECT", "WITH"):
        raise PermissionError("Chỉ cho phép câu lệnh SELECT.")
    if ";" in no_str:
        raise PermissionError("Không cho phép nhiều câu lệnh.")
    m = _WRITE_WORDS.search(no_str)
    if m:
        raise PermissionError(f"Câu lệnh có từ khóa không được phép: {m.group(1).upper()}")
    if re.search(r"\bFOR\s+UPDATE\b", no_str, re.I):
        raise PermissionError("Không cho phép SELECT ... FOR UPDATE (khóa dữ liệu).")
    return sql


# -------------------------------------------------------------------------------- mật khẩu (DPAPI)
class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data: bytes, protect: bool) -> bytes:
    """Mã hóa / giải mã bằng Windows DPAPI, phạm vi máy (chỉ giải mã được trên chính máy này)."""
    buf = ctypes.create_string_buffer(data, len(data))
    inp = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = _Blob()
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    UI_FORBIDDEN, LOCAL_MACHINE = 0x1, 0x4
    if protect:
        ok = crypt32.CryptProtectData(ctypes.byref(inp), None, None, None, None,
                                      UI_FORBIDDEN | LOCAL_MACHINE, ctypes.byref(out))
    else:
        ok = crypt32.CryptUnprotectData(ctypes.byref(inp), None, None, None, None, UI_FORBIDDEN, ctypes.byref(out))
    if not ok:
        raise OSError(f"DPAPI lỗi {ctypes.GetLastError()}")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


CONFIG_KEYS = ("enabled", "host", "port", "service_name", "dsn", "user", "poll_seconds", "oracle_client_dir")


def read_raw() -> dict:
    """Nội dung his.json như trên đĩa (không giải mã mật khẩu), cho màn cài đặt. Không có file: {}."""
    try:
        with open(HIS_CONFIG_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def saved_password() -> str | None:
    """Mật khẩu đã lưu (giải mã), None nếu chưa có."""
    raw = read_raw()
    if raw.get("password"):
        return raw["password"]
    if raw.get("password_encrypted"):
        return _dpapi(base64.b64decode(raw["password_encrypted"]), False).decode("utf-8")
    return None


def save_config(fields: dict, new_password: str | None) -> None:
    """Ghi his.json. Mật khẩu mới được mã hóa (DPAPI); None = giữ mật khẩu đã lưu."""
    cfg = {k: fields[k] for k in CONFIG_KEYS if fields.get(k) not in (None, "")}
    cfg["enabled"] = bool(fields.get("enabled"))
    old = read_raw()
    if new_password:
        cfg["password_encrypted"] = base64.b64encode(_dpapi(new_password.encode("utf-8"), True)).decode("ascii")
    elif old.get("password_encrypted"):
        cfg["password_encrypted"] = old["password_encrypted"]
    elif old.get("password"):
        cfg["password_encrypted"] = base64.b64encode(_dpapi(old["password"].encode("utf-8"), True)).decode("ascii")
    os.makedirs(os.path.dirname(HIS_CONFIG_FILE), exist_ok=True)
    tmp = HIS_CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, HIS_CONFIG_FILE)


_HINTS = [   # mã lỗi Oracle thường gặp -> gợi ý tiếng Việt
    ("ORA-01017", "Sai tài khoản hoặc mật khẩu."),
    ("ORA-28000", "Tài khoản Oracle đang bị khóa."),
    ("ORA-12514", "Sai service name (máy chủ không biết service này)."),
    ("ORA-12505", "Sai service name / SID."),
    ("DPY-6005", "Không kết nối được tới máy chủ: kiểm tra địa chỉ, cổng, mạng, tường lửa."),
    ("DPY-6001", "Sai service name (máy chủ không biết service này)."),
    ("DPY-3010", "Oracle quá cũ cho chế độ thin: cài Oracle Instant Client và điền thư mục của nó."),
    ("DPY-3015", "Oracle dùng kiểu mật khẩu cũ: cài Oracle Instant Client và điền thư mục của nó."),
    ("ORA-00942", "Không thấy bảng: tài khoản này chưa được cấp quyền SELECT, hoặc bảng nằm ở schema khác "
                  "(cần synonym hoặc tên schema)."),
    ("ORA-00904", "Sai tên cột trong câu truy vấn (ví dụ TDL_PATIENT_CODE): cần sửa câu SQL cho đúng HIS."),
]


def hint_for(e: Exception) -> str:
    s = str(e)
    return next((h for code, h in _HINTS if code in s), "")


def test_connection(cfg: dict, room: tuple[int, str] | None = None) -> list[tuple[bool, str]]:
    """Thử 2 câu SELECT có sẵn, trả về từng bước (đạt?, mô tả). Chỉ đọc như mọi lần đọc khác."""
    steps: list[tuple[bool, str]] = []
    client = HisClient(cfg)
    try:
        clinics = client.fetch_clinics()
    except Exception as e:
        h = hint_for(e)
        return [(False, f"Đọc danh sách phòng khám: {error_text(e)}" + (f"\n    → {h}" if h else ""))]
    steps.append((True, "Kết nối được tới HIS."))
    steps.append((True, f"Danh sách phòng khám: {len(clinics)} phòng."))
    room = room or (clinics[0] if clinics else None)
    if room is None:
        steps.append((False, "Không có phòng khám nào (IS_ACTIVE = 1, tên có chữ 'Khám') để thử câu truy vấn bệnh nhân."))
        return steps
    try:
        pats = client.fetch_patients(room[0], room[1])
        steps.append((True, f"Câu truy vấn bệnh nhân chạy được: {len(pats)} bệnh nhân hôm nay ở {room[1]}."))
        if no_patient_code:
            steps.append((True, "HIS không có cột TDL_PATIENT_CODE: app dùng mã điều trị (TREATMENT_CODE) để nhận ra "
                                "bệnh nhân. Dòng \"đã được chỉ định chụp\" khi đó chỉ nhận ra trong cùng đợt điều trị."))
    except Exception as e:
        h = hint_for(e)
        steps.append((False, f"Câu truy vấn bệnh nhân: {error_text(e)}" + (f"\n    → {h}" if h else "")))
    return steps


def load_config() -> dict | None:
    """Đọc his.json. Mật khẩu viết thường ("password") sẽ được mã hóa lại thành "password_encrypted"."""
    try:
        with open(HIS_CONFIG_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        return None
    if not isinstance(cfg, dict) or not cfg.get("enabled", True):
        return None
    pw = cfg.get("password")
    if pw:
        try:
            cfg["password_encrypted"] = base64.b64encode(_dpapi(pw.encode("utf-8"), True)).decode("ascii")
            disk = dict(cfg)
            disk.pop("password")
            tmp = HIS_CONFIG_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(disk, f, ensure_ascii=False, indent=2)
            os.replace(tmp, HIS_CONFIG_FILE)
        except OSError:
            pass                    # không mã hóa/ghi được thì vẫn dùng mật khẩu thường trong lần này
    elif cfg.get("password_encrypted"):
        cfg["password"] = _dpapi(base64.b64decode(cfg["password_encrypted"]), False).decode("utf-8")
    return cfg


def read_raw() -> dict:
    """Nội dung his.json như trên đĩa (không giải mã), {} nếu chưa có. Dùng cho tab Kết nối HIS."""
    try:
        with open(HIS_CONFIG_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def saved_password(raw: dict | None = None) -> str:
    """Mật khẩu đã lưu (giải mã), "" nếu chưa có."""
    raw = read_raw() if raw is None else raw
    if raw.get("password_encrypted"):
        return _dpapi(base64.b64decode(raw["password_encrypted"]), False).decode("utf-8")
    return raw.get("password") or ""


def save_config(fields: dict, new_password: str = "") -> None:
    """Ghi his.json từ tab Kết nối HIS. Mật khẩu chỉ lưu dạng mã hóa; để trống = giữ mật khẩu cũ."""
    raw = read_raw()
    out = {k: v for k, v in fields.items() if v not in ("", None)}
    pw = new_password or saved_password(raw)
    if pw:
        out["password_encrypted"] = base64.b64encode(_dpapi(pw.encode("utf-8"), True)).decode("ascii")
    os.makedirs(os.path.dirname(HIS_CONFIG_FILE), exist_ok=True)
    tmp = HIS_CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    os.replace(tmp, HIS_CONFIG_FILE)


# ------------------------------------------------------------------------------------- đọc Oracle
def _his_time(v: Any) -> datetime | None:
    """HIS lưu thời gian dạng số YYYYMMDDHH24MISS, ví dụ 20261110083015."""
    if v is None:
        return None
    s = str(int(v)) if isinstance(v, (int, float)) else str(v).strip()
    try:
        return datetime.strptime(s[:14].ljust(14, "0"), "%Y%m%d%H%M%S")
    except ValueError:
        return None


def _his_num(d: datetime) -> int:
    return int(d.strftime("%Y%m%d%H%M%S"))


SEX = {1: "Nữ", 2: "Nam"}


_thick_dir: str | None = None     # init_oracle_client chỉ gọi được một lần trong mỗi lần chạy app
no_patient_code = False           # True: HIS không có cột TDL_PATIENT_CODE, mã điều trị làm mã nhận diện


class HisClient:
    def __init__(self, cfg: dict):
        self.cfg = cfg

    def _connect(self):
        global _thick_dir
        import oracledb
        lib = self.cfg.get("oracle_client_dir")       # chỉ cần nếu Oracle cũ (11g) không hỗ trợ chế độ thin
        if lib and _thick_dir is None:
            oracledb.init_oracle_client(lib_dir=lib)
            _thick_dir = lib
        elif lib and lib != _thick_dir:
            raise RuntimeError("Đã đổi thư mục Oracle Instant Client: khởi động lại app để áp dụng.")
        dsn = self.cfg.get("dsn") or f'{self.cfg["host"]}:{self.cfg.get("port", 1521)}/{self.cfg["service_name"]}'
        conn = oracledb.connect(user=self.cfg["user"], password=self.cfg["password"], dsn=dsn)
        conn.call_timeout = CALL_TIMEOUT_MS
        return conn

    def query(self, sql: str, params: dict | None = None, max_rows: int = MAX_ROWS) -> list[dict]:
        ensure_select(sql)
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.arraysize = 500
            cur.execute(sql, params or {})
            cols = [c[0].lower() for c in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchmany(max_rows)]
        finally:
            conn.close()            # không commit: Oracle tự rollback

    def fetch_clinics(self) -> list[tuple[int, str]]:
        return [(int(r["ma_phong"]), str(r["ten_phong"])) for r in self.query(SQL_CLINICS)
                if r.get("ma_phong") is not None and r.get("ten_phong")]

    def fetch_patients(self, room_id: int, room_name: str, day: date | None = None) -> list[Patient]:
        global no_patient_code
        day = day or date.today()
        params = {"tu": _his_num(datetime.combine(day, time(0, 0, 0))),
                  "den": _his_num(datetime.combine(day, time(23, 59, 59))), "phong": room_id}
        if no_patient_code:
            rows = self.query(SQL_PATIENTS_NO_CODE, params)
        else:
            try:
                rows = self.query(SQL_PATIENTS, params)
            except Exception as e:                       # HIS không có cột TDL_PATIENT_CODE: dùng câu dự phòng
                if "ORA-00904" not in str(e) or "TDL_PATIENT_CODE" not in str(e).upper():
                    raise
                no_patient_code = True
                rows = self.query(SQL_PATIENTS_NO_CODE, params)
        out: dict[str, Patient] = {}
        for r in rows:                                   # đã sắp START_TIME giảm dần: giữ lần khám mới nhất
            name = (r.get("ten_benh_nhan") or "").strip()
            dob = _his_time(r.get("ngay_sinh"))
            treat = str(r.get("ma_dieu_tri") or "").strip()
            pid = (str(r.get("ma_benh_nhan") or "").strip() or treat
                   or (f"{name}|{dob:%Y%m%d}" if dob else name))
            if not name or pid in out:
                continue
            try:
                sex = SEX.get(int(r.get("gioi_tinh_id")), "Không xác định")
            except (TypeError, ValueError):
                sex = "Không xác định"
            out[pid] = Patient(pid, name, sex, dob.date() if dob else None, room_name,
                               saved_at=_his_time(r.get("bat_dau_kham")) or datetime.now(), treatment_code=treat)
        return list(out.values())


    def fetch_report(self, start: date, end: date, codes: list[str]) -> tuple[list[dict], bool]:
        """Dòng báo cáo từ ngày start đến hết ngày end. Trả về (các dòng, có bị cắt bớt vì quá REPORT_MAX_ROWS)."""
        if not codes:
            raise ValueError("Chưa có mã dịch vụ nào.")
        bad = [c for c in codes if not SERVICE_CODE.match(c)]
        if bad:
            raise ValueError(f"Mã dịch vụ không hợp lệ: {', '.join(bad)}")
        params: dict[str, Any] = {"tu": _his_num(datetime.combine(start, time(0, 0, 0))),
                                  "den": _his_num(datetime.combine(end, time(23, 59, 59)))}
        params.update({f"c{i}": c for i, c in enumerate(codes)})
        sql = SQL_REPORT.format(codes=", ".join(f":c{i}" for i in range(len(codes))))
        rows = self.query(sql, params, REPORT_MAX_ROWS + 1)
        for r in rows:
            for k in ("thoi_gian_bat_dau", "thoi_gian_ket_thuc"):
                r[k] = _his_time(r.get(k))
        return rows[:REPORT_MAX_ROWS], len(rows) > REPORT_MAX_ROWS


    def fetch_visits(self, start: date, end: date, room_ids: list[int]) -> tuple[list[dict], bool]:
        """Lượt khám tại các phòng từ ngày start đến hết ngày end. Trả về (các dòng, có bị cắt bớt)."""
        room_ids = [int(r) for r in room_ids]
        if not room_ids:
            return [], False
        params: dict[str, Any] = {"tu": _his_num(datetime.combine(start, time(0, 0, 0))),
                                  "den": _his_num(datetime.combine(end, time(23, 59, 59)))}
        params.update({f"r{i}": r for i, r in enumerate(room_ids)})
        rooms = ", ".join(f":r{i}" for i in range(len(room_ids)))

        def sql(with_code: bool) -> str:
            return SQL_VISITS.format(pcode="t.TDL_PATIENT_CODE AS ma_benh_nhan,\n    " if with_code else "",
                                     pgroup="t.TDL_PATIENT_CODE, " if with_code else "", rooms=rooms)
        global no_patient_code
        try:
            rows = self.query(sql(not no_patient_code), params, REPORT_MAX_ROWS + 1)
        except Exception as e:                       # HIS không có cột TDL_PATIENT_CODE
            if no_patient_code or "ORA-00904" not in str(e) or "TDL_PATIENT_CODE" not in str(e).upper():
                raise
            no_patient_code = True
            rows = self.query(sql(False), params, REPORT_MAX_ROWS + 1)
        for r in rows:
            r["bat_dau_kham"] = _his_time(r.get("bat_dau_kham"))
        return rows[:REPORT_MAX_ROWS], len(rows) > REPORT_MAX_ROWS

    def fetch_history(self, patient_id: str, codes: list[str]) -> list[dict]:
        """Các lần bệnh nhân có y lệnh với mã dịch vụ trong danh sách, mới nhất trước (tối đa HISTORY_MAX_ROWS).
        patient_id là mã BN; nếu HIS không có cột mã BN thì app dùng mã điều trị làm mã nhận diện."""
        codes = [c for c in codes if SERVICE_CODE.match(c)]
        if not patient_id or not codes:
            return []
        params: dict[str, Any] = {"ma": patient_id}
        params.update({f"c{i}": c for i, c in enumerate(codes)})
        sql = SQL_HISTORY.format(who="t.TREATMENT_CODE" if no_patient_code else "t.TDL_PATIENT_CODE",
                                 codes=", ".join(f":c{i}" for i in range(len(codes))))
        rows = self.query(sql, params, HISTORY_MAX_ROWS)
        for r in rows:
            for k in ("thoi_gian_bat_dau", "thoi_gian_ket_thuc"):
                r[k] = _his_time(r.get(k))
        return rows


# ------------------------------------------------------------------------------- chạy nền (thread)
class Worker:
    """Một luồng nền chạy lần lượt các việc đọc HIS, để giao diện không bị đứng khi mạng chậm.
    Kết quả được đưa về luồng giao diện qua `pump()` (gọi định kỳ bằng Tk.after)."""

    def __init__(self):
        self._tasks: queue.Queue = queue.Queue()
        self._done: queue.Queue = queue.Queue()
        threading.Thread(target=self._run, daemon=True, name="his-worker").start()

    def submit(self, fn: Callable[[], Any], on_done: Callable[[Any, Exception | None], None]) -> None:
        self._tasks.put((fn, on_done))

    def _run(self):
        while True:
            fn, cb = self._tasks.get()
            try:
                self._done.put((cb, fn(), None))
            except Exception as e:      # lỗi mạng / sai mật khẩu / sai cột...: báo lên giao diện
                self._done.put((cb, None, e))

    def pump(self) -> None:
        while True:
            try:
                cb, res, err = self._done.get_nowait()
            except queue.Empty:
                return
            cb(res, err)


def error_text(e: Exception) -> str:
    s = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
    return s[:200]
