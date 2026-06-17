"""
devicehub 모듈 관리 데모 — 공유 상수 / 경로 (단일 출처).

핸드오프 §2.4 의 확정 결정 + 관계자 피드백 반영:
- model(하드웨어 버전) -> 그 모델에 존재하는 모듈 목록을 코드 상수로 관리한다.
- "슬롯"과 "모듈 종류(허용타입)"는 같은 개념이므로 합쳤다. position_code = 모듈 종류.
  (한 모델에 같은 종류가 둘이면 종류명에 접미사를 붙이면 되지만, 현재 라인업은 종류별 1개씩이다.)
- 슬롯 검증은 애플리케이션 레이어 책임.

seed.py 와 app.py 가 이 파일을 함께 import 하여 중복을 없앤다.
"""
from __future__ import annotations

import datetime as dt
import tempfile
from pathlib import Path

# 데모 기준 "현재" 시각. cook_order 데이터의 끝점이자, 입력 시뮬레이션의 기본 적용일.
DEMO_NOW = dt.date(2025, 6, 1)

# 모듈 종류별 정격수명(rated_life). 단위 = 총 조리 횟수(cook_order 건수).
MODULE_TYPES = {
    "Top Griddle": 5000,
    "BOT Griddle": 5000,
    "Manipulator": 15000,
    "E-Box": 20000,
}

# 모듈 컨디션(condition). status 는 "물건의 상태"만 표현한다 — 위치(장착 여부·어느 장비)는 여기
# 없다. 위치는 module_placements 의 열린 행(valid_to IS NULL)으로만 판정한다(단일 소스).
#   serviceable : 정상 양품 — 장착 가능
#   refurbished : 수리/리퍼 후 재사용 가능 — 장착 가능
#   faulty      : 고장, 수리 전 — 장착 불가
#   scrapped    : 폐기(수명 도과 등) 종착 — 장착 불가
# "재고(in stock)"는 저장값이 아니라 파생이다: 위치=창고 ∧ status ∈ INSTALLABLE_STATUSES.
# (기존 'removed' = 예방탈거된 양품 = 다시 가용 → serviceable 로 흡수. 탈거 사실은
#  module_placements.removed_reason 이력에 남는다.)
MODULE_STATUSES = ("serviceable", "refurbished", "faulty", "scrapped")
# 장착 가능 컨디션(여기에 더해 "현재 열린 placement 없음" 조건은 쿼리에서 확인한다).
INSTALLABLE_STATUSES = ("serviceable", "refurbished")

# 등록 벤더 마스터(데모용). 실서비스에선 외부 시스템에서 관리되며, 업로드 시 이 목록에 없는
# 벤더는 거부한다. (id, 이름, 코드)
VENDORS = [
    (1, "Acme Components", "ACM"),
    (2, "한화정밀", "HWP"),
    (3, "대양로보틱스", "DYR"),
]

# 하드웨어 버전(hardware_version) — 인스턴스에 기록만 한다(장착 가능 판정엔 미반영). 예시값 1.2 / 1.3. §결정
HW_VERSIONS = ["1.2", "1.3"]

# model 별로 "존재하는 모듈 종류" 목록. 이 종류명이 곧 위치 코드(position_code)다.
# model 은 single / dual 두 종류(라벨). 모듈 구성은 동일하며(Top/BOT Griddle·Manipulator·E-Box
# 각 1개), 구분은 모듈 구성이 아닌 다른 속성(처리량 등)이다. 종류별 1개씩이라 position_code = 모듈 종류.
MODEL_SLOTS = {
    "single": ["Top Griddle", "BOT Griddle", "Manipulator", "E-Box"],
    "dual": ["Top Griddle", "BOT Griddle", "Manipulator", "E-Box"],
}


def slots_for(model: str) -> list[str]:
    """해당 model 에 존재하는 슬롯(=모듈 종류) 목록."""
    return MODEL_SLOTS[model]


def demo_db_path() -> Path:
    """SQLite 데모 DB 경로 (자족형).

    앱 폴더 하위 `demo_data/` 에 둔다. 쓰기 불가 환경이면 임시 폴더로 폴백.
    데모 DB 는 최초 실행 시 seed 가 자동 생성한다(커밋 불필요).
    """
    candidate = Path(__file__).resolve().parent / "demo_data"
    try:
        candidate.mkdir(parents=True, exist_ok=True)
    except OSError:
        candidate = Path(tempfile.gettempdir()) / "devicehub_module_mgmt_demo"
        candidate.mkdir(parents=True, exist_ok=True)
    return candidate / "devicehub_demo.db"
