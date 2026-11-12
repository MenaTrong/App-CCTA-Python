"""Khả năng mắc bệnh mạch vành dựa theo nguy cơ lâm sàng (RF-CL) — ESC 2024.

Tra bảng theo: điểm triệu chứng (0–1 / 2 / 3), giới, nhóm tuổi, số yếu tố nguy cơ (0–1 / 2–3 / 4–5).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


class RiskLevel(IntEnum):
    VERY_LOW = 0
    LOW = 1
    MODERATE = 2


AGE_BANDS = ["30–39", "40–49", "50–59", "60–69", "70–80"]
SYM_GROUPS = ["0–1 điểm", "2 điểm", "3 điểm"]
RF_GROUPS = ["0–1", "2–3", "4–5"]

# TABLE[nhóm điểm triệu chứng][giới: 0 = Nữ, 1 = Nam][nhóm tuổi][nhóm số yếu tố nguy cơ] -> %
TABLE = [
    [  # Điểm triệu chứng 0–1
        [[0, 1, 2], [1, 1, 3], [1, 2, 5], [2, 4, 7], [4, 7, 11]],          # Nữ
        [[1, 2, 5], [2, 4, 8], [4, 7, 12], [8, 12, 17], [15, 19, 24]],     # Nam
    ],
    [  # Điểm triệu chứng 2
        [[0, 1, 3], [1, 2, 5], [2, 3, 7], [3, 6, 11], [6, 10, 16]],
        [[2, 4, 8], [3, 6, 12], [6, 11, 17], [12, 17, 25], [22, 27, 34]],
    ],
    [  # Điểm triệu chứng 3
        [[2, 5, 10], [4, 7, 12], [6, 10, 15], [10, 14, 19], [16, 19, 23]],
        [[9, 14, 22], [14, 20, 27], [21, 27, 33], [32, 35, 39], [44, 44, 45]],
    ],
]


@dataclass
class Evaluation:
    age: int
    male: bool
    symptom_score: int
    risk_factor_count: int
    age_band: int
    sym_group: int
    rf_group: int
    percent: int
    level: RiskLevel
    notes: list[str] = field(default_factory=list)


# Lần đánh giá gần nhất, dùng để tô viền ô trên màn hình Quy tắc.
last_evaluation: Evaluation | None = None


def age_band(age: int) -> int:
    if age < 40:
        return 0
    if age < 50:
        return 1
    if age < 60:
        return 2
    if age < 70:
        return 3
    return 4


def sym_group(score: int) -> int:
    return 0 if score <= 1 else (1 if score == 2 else 2)


def rf_group(count: int) -> int:
    return 0 if count <= 1 else (1 if count <= 3 else 2)


def level_of(percent: int) -> RiskLevel:
    if percent <= 5:
        return RiskLevel.VERY_LOW
    if percent <= 15:
        return RiskLevel.LOW
    return RiskLevel.MODERATE


LEVEL_NAME = {
    RiskLevel.VERY_LOW: "Rất thấp (≤5%)",
    RiskLevel.LOW: "Thấp (>5–15%)",
    RiskLevel.MODERATE: "Vừa (>15–50%)",
}
LEVEL_SHORT = {RiskLevel.VERY_LOW: "Rất thấp", RiskLevel.LOW: "Thấp", RiskLevel.MODERATE: "Vừa"}

TITLE = {
    RiskLevel.MODERATE: "KHUYẾN NGHỊ CHỤP CT MẠCH VÀNH",
    RiskLevel.LOW: "CÂN NHẮC CHỤP CT MẠCH VÀNH",
    RiskLevel.VERY_LOW: "CHƯA CẦN CHỤP CT MẠCH VÀNH",
}

ADVICE = {
    RiskLevel.MODERATE: "Khả năng lâm sàng mức Vừa. Khuyến cáo làm chẩn đoán hình ảnh: "
                        "CT mạch vành (CCTA) hoặc chẩn đoán hình ảnh chức năng.",
    RiskLevel.LOW: "Khả năng lâm sàng mức Thấp. Cân nhắc CT mạch vành hoặc chấm điểm vôi hóa "
                   "mạch vành (CACS) để phân tầng thêm.",
    RiskLevel.VERY_LOW: "Khả năng lâm sàng mức Rất thấp. Có thể trì hoãn xét nghiệm chẩn đoán, "
                        "theo dõi và kiểm soát yếu tố nguy cơ.",
}


def symptom_score(location: bool, exertion: bool, relief: bool, dyspnea: bool) -> int:
    """Đau ngực: mỗi đặc điểm 1 điểm (tối đa 3). Khó thở là triệu chứng chính: 2 điểm. Lấy điểm cao hơn."""
    chest = int(location) + int(exertion) + int(relief)
    return max(chest, 2 if dyspnea else 0)


def evaluate(age: int, male: bool, sym_score: int, rf_count: int) -> Evaluation:
    global last_evaluation
    ab, sg, rg = age_band(age), sym_group(sym_score), rf_group(rf_count)
    pct = TABLE[sg][1 if male else 0][ab][rg]
    ev = Evaluation(age, male, sym_score, rf_count, ab, sg, rg, pct, level_of(pct))
    if age < 30:
        ev.notes.append("Tuổi dưới 30 nằm ngoài bảng, tạm dùng nhóm 30–39.")
    if age > 80:
        ev.notes.append("Tuổi trên 80 nằm ngoài bảng, tạm dùng nhóm 70–80.")
    last_evaluation = ev
    return ev
