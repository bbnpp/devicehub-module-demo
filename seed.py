"""
devicehub 모듈 관리 데모 — SQLite 시드 생성기.

cook_order(사용량)를 먼저 생성한 뒤, 슬롯마다 **마모 기반으로 모듈 교체 이력을 자동 생성**한다
(정격수명에 가까워지면 교체). 이렇게 하면 장착/교체 이력이 장비당 5~10행 규모로 풍성해지고,
placement 체인이 항상 빈틈없이 타일링되어(앞 placement.valid_to == 다음 valid_from) 모든 조리가
정확히 한 모듈에 귀속된다.

핵심 서사 보존: 1호기 Manipulator 는 **교체하지 않고 고정**(PINNED)한다. 장비가 강남(k1)->부산(k3)
으로 리퍼 이동해도 이 모듈은 계속 사용량을 누적 → 두 고객사에 걸쳐 쌓이고 정격을 초과한다.

검증은 고정 수치 대신 구조 불변식으로 한다(슬롯별 모듈 사용량 합 = 장비 총 조리수 / 핵심 모듈
교차고객·>100% / 장비당 행수 5~10 / 창고 재고 0%).

Run (선택 — 앱이 최초 실행 시 자동 생성하므로 보통 불필요):
    python seed.py
"""
from __future__ import annotations

import datetime as dt
import random
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

# 형제 모듈(schema.py) import 보장.
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from schema import DEMO_NOW, MODEL_SLOTS, MODULE_TYPES, demo_db_path  # noqa: E402

random.seed(42)
DB = str(demo_db_path())

TYPE_CODE = {"Top Griddle": "TG", "BOT Griddle": "BG", "Manipulator": "MN", "E-Box": "EB"}
GRIDDLE_TYPES = {"Top Griddle", "BOT Griddle"}
FAULT_MODES = ["overheat", "wear_limit", "sensor_fault", "thermal_runaway"]
REASON_STATUS = {"fault": "faulty", "preventive": "removed", "refurb": "refurbished"}

kitchens = [(1, "강남 본점", 1), (2, "판교 지점", 1), (3, "부산 해운대점", 2)]
# products: (id, name, serial(R-AG), model(single|dual), 현재 kitchen_id)
products = [
    (1, "1호기", "R-AG-00045", "single", 3),
    (2, "2호기", "R-AG-00046", "single", 2),
    (3, "3호기", "R-AG-00103", "dual", 3),
]
# product -> kitchen 타임라인 (cook_order 생성용). p1 은 리퍼로 k1 -> k3 이동.
timeline = {
    1: [(dt.date(2023, 6, 1), dt.date(2024, 9, 20), 1), (dt.date(2024, 9, 22), DEMO_NOW, 3)],
    2: [(dt.date(2023, 1, 1), DEMO_NOW, 2)],
    3: [(dt.date(2023, 1, 1), DEMO_NOW, 3)],
}
cook_rate = {1: 24, 2: 18, 3: 16}  # 하루 평균 조리 횟수
PINNED = {(1, "Manipulator")}      # 교체하지 않는 핵심 모듈(리퍼 누적 데모)
SPARES_PER_TYPE = 2                # 창고 재고(미장착) — 입력 시뮬레이션용


def _gen_cooks() -> list[tuple]:
    rows = []  # (product, kitchen, recipe, started_iso, ended_iso)
    for pid, segs in timeline.items():
        rate = cook_rate[pid]
        for (start, end, kid) in segs:
            day = start
            while day < end:
                for _ in range(max(0, int(random.gauss(rate, 3)))):
                    s = dt.datetime(day.year, day.month, day.day, random.randint(8, 21), random.randint(0, 59))
                    e = s + dt.timedelta(minutes=random.randint(5, 20))
                    rows.append((pid, kid, random.randint(1, 12), s.isoformat(), e.isoformat()))
                day += dt.timedelta(days=1)
    return rows


def _partition_starts(n_times: int, rated: int, pinned: bool) -> list[int]:
    """슬롯의 조리 시퀀스를 정격수명 단위로 나눈 placement 시작 인덱스 목록."""
    if pinned or n_times == 0:
        return [0]
    starts, last = [0], 0
    thr = rated * random.uniform(0.85, 1.08)
    for i in range(n_times):
        if (i - last + 1) >= thr and i + 1 < n_times:
            starts.append(i + 1)
            last = i + 1
            thr = rated * random.uniform(0.85, 1.08)
    return starts


def build(db_path: str):
    """시드 DB 생성. (cook_rows, placements) 반환(검증용)."""
    cook_rows = _gen_cooks()
    times_by_pid: dict[int, list[str]] = defaultdict(list)
    for (pid, _kid, _rec, s_iso, _e_iso) in cook_rows:
        times_by_pid[pid].append(s_iso)
    for pid in times_by_pid:
        times_by_pid[pid].sort()  # ISO 문자열 정렬 = 시간순

    install_iso = {
        pid: dt.datetime(segs[0][0].year, segs[0][0].month, segs[0][0].day).isoformat()
        for pid, segs in timeline.items()
    }

    modules: list[list] = []    # [id, serial, type, status]
    placements: list[tuple] = []  # (id, module_id, product_id, position_code, vf, vt, reason, fault_mode)
    type_count: dict[str, int] = defaultdict(int)
    mid = 0
    pl_id = 0

    def add_module(mtype: str, status: str) -> int:
        nonlocal mid
        type_count[mtype] += 1
        mid += 1
        modules.append([mid, f"SN-{TYPE_CODE[mtype]}-{type_count[mtype]:04d}", mtype, status])
        return mid

    for (pid, _n, _s, model, _k) in products:
        times = times_by_pid[pid]
        for slot in MODEL_SLOTS[model]:
            pinned = (pid, slot) in PINNED
            starts = _partition_starts(len(times), MODULE_TYPES[slot], pinned)
            for k, sidx in enumerate(starts):
                vf = install_iso[pid] if k == 0 else times[sidx]
                vt = times[starts[k + 1]] if k + 1 < len(starts) else None
                if vt is None:
                    status, reason, fmode = "installed", None, None
                else:
                    if slot in GRIDDLE_TYPES:
                        reason = random.choices(["fault", "preventive"], weights=[0.6, 0.4])[0]
                    else:
                        reason = "preventive"
                    fmode = random.choice(FAULT_MODES) if reason == "fault" else None
                    status = REASON_STATUS[reason]
                m_id = add_module(slot, status)
                pl_id += 1
                placements.append((pl_id, m_id, pid, slot, vf, vt, reason, fmode))

    # 창고 재고(미장착)
    for mtype in MODULE_TYPES:
        for _ in range(SPARES_PER_TYPE):
            add_module(mtype, "in_stock")

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    for t in ["cook_order", "module_placements", "modules", "products", "kitchen"]:
        cur.execute(f"DROP TABLE IF EXISTS {t}")
    cur.executescript(
        """
        CREATE TABLE kitchen (id INTEGER PRIMARY KEY, name TEXT, brand_id INTEGER);
        CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT, serial TEXT, model TEXT,
          kitchen_id INTEGER REFERENCES kitchen(id));
        CREATE TABLE modules (id INTEGER PRIMARY KEY, serial TEXT UNIQUE, module_type TEXT,
          status TEXT, rated_life INTEGER);
        CREATE TABLE module_placements (id INTEGER PRIMARY KEY, module_id INTEGER REFERENCES modules(id),
          product_id INTEGER REFERENCES products(id), position_code TEXT,
          valid_from TEXT, valid_to TEXT, removed_reason TEXT, fault_mode TEXT);
        CREATE TABLE cook_order (id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER REFERENCES products(id),
          kitchen_id INTEGER REFERENCES kitchen(id), recipe_id INTEGER, started_at TEXT, ended_at TEXT);
        """
    )
    cur.executemany("INSERT INTO kitchen VALUES (?,?,?)", kitchens)
    cur.executemany("INSERT INTO products VALUES (?,?,?,?,?)", products)
    cur.executemany(
        "INSERT INTO modules VALUES (?,?,?,?,?)",
        [(i, s, t, st, MODULE_TYPES[t]) for (i, s, t, st) in modules],
    )
    cur.executemany("INSERT INTO module_placements VALUES (?,?,?,?,?,?,?,?)", placements)
    cur.executemany(
        "INSERT INTO cook_order (product_id,kitchen_id,recipe_id,started_at,ended_at) VALUES (?,?,?,?,?)",
        cook_rows,
    )
    con.commit()
    con.close()
    return cook_rows, placements


USAGE_SQL = """
SELECT m.id, m.serial, m.module_type, m.status, m.rated_life,
       COUNT(co.id) AS total_cooks,
       ROUND(100.0*COUNT(co.id)/m.rated_life, 1) AS pct_used
FROM modules m
LEFT JOIN module_placements mp ON mp.module_id = m.id
LEFT JOIN cook_order co
  ON co.product_id = mp.product_id AND co.started_at >= mp.valid_from
 AND (mp.valid_to IS NULL OR co.started_at < mp.valid_to)
GROUP BY m.id
"""
BREAKDOWN_SQL = """
SELECT k.name AS kitchen, COUNT(co.id) AS cooks
FROM modules m
JOIN module_placements mp ON mp.module_id = m.id
JOIN cook_order co
  ON co.product_id = mp.product_id
 AND co.started_at >= mp.valid_from
 AND (mp.valid_to IS NULL OR co.started_at < mp.valid_to)
JOIN kitchen k ON k.id = co.kitchen_id
WHERE m.id = ?
GROUP BY k.id
"""


def validate(db_path: str, cook_rows: list, placements: list) -> None:
    """구조 불변식 검증. 어긋나면 AssertionError 로 즉시 보고."""
    con = sqlite3.connect(db_path)
    cur = con.cursor()

    n = len(cook_rows)
    assert 25000 <= n <= 60000, f"cook_order 행 수 비정상: {n}"

    # 장비당 placement(설치/교체 이력) 5~10행
    per_product = Counter(p[2] for p in placements)
    for pid, c in sorted(per_product.items()):
        assert 5 <= c <= 11, f"product {pid} placements={c} (5~10 기대)"

    usage = {r[0]: (r[5], r[6]) for r in cur.execute(USAGE_SQL)}  # mid -> (cooks, pct)
    ptot = dict(cur.execute("SELECT product_id, COUNT(*) FROM cook_order GROUP BY product_id"))

    # 슬롯별 모듈 사용량 합 == 장비 총 조리수 (빈틈/겹침 없는 타일링 확인)
    slot_sum: dict[tuple, int] = defaultdict(int)
    placed_ids = set()
    for (_plid, m_id, pid, slot, _vf, _vt, _rr, _fm) in placements:
        slot_sum[(pid, slot)] += usage[m_id][0]
        placed_ids.add(m_id)
    for (pid, slot), s in slot_sum.items():
        assert s == ptot[pid], f"{pid}/{slot} 사용량 합 {s} != 장비 총 {ptot[pid]}"

    # 창고 재고(미장착) 0%
    spares = [mid for mid in usage if mid not in placed_ids]
    for mid in spares:
        assert usage[mid] == (0, 0.0), f"창고 재고 {mid} 사용량 {usage[mid]}"

    # 핵심 모듈: 단일 placement, 두 고객사 누적, 정격 초과
    star = [p for p in placements if (p[2], p[3]) in PINNED]
    assert len(star) == 1, f"핵심 모듈 placement 가 1개가 아님: {len(star)}"
    star_mid = star[0][1]
    bd = dict(cur.execute(BREAKDOWN_SQL, (star_mid,)))
    assert len(bd) >= 2 and all(v > 0 for v in bd.values()), f"핵심 모듈 고객사 분해 {bd}"
    assert usage[star_mid][1] > 100, f"핵심 모듈 사용률 {usage[star_mid][1]} (>100 기대)"

    con.close()
    print(
        f"검증 통과: cook_order={n}, 장비별 이력행={dict(sorted(per_product.items()))}, "
        f"창고재고 {len(spares)}개 0%, 핵심모듈 사용률 {usage[star_mid][1]}% 분해 {bd}"
    )


if __name__ == "__main__":
    cook_rows, placements = build(DB)
    print(f"DB: {DB}")
    print(f"cook_order rows: {len(cook_rows)}  |  placements: {len(placements)}")
    validate(DB, cook_rows, placements)
