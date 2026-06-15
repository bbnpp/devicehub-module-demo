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
