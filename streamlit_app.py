"""
devicehub 모듈 관리 데모 — Streamlit 앱.

목적: 관계자들과 "모듈 단위 관리(사용량·교체·이력)" 요구사항을 맞춰가는 토론 도구.
가상 데이터(seed.py)를 채운 SQLite 를 읽고/쓰며 5개 화면으로 보여준다.

  1. 장비 상세      — 현재 모듈 구성 + 초기 설치부터의 설치/교체 이력
  2. 데이터 입력    — 실제 서비스처럼 모듈 장착/교체/탈거 입력(허용값·반영결과 확인)
  3. 모듈 상세      — 부품 한 개의 누적 사용량 + 고객사별 분해 + 장착 이력
  4. 모듈 Fleet 개요 — 전체 모듈 수명 소진 현황
  5. ERD            — 관계 테이블 다이어그램(기존/신규 색 구분)

Run:
    streamlit run streamlit_app.py    # DB 가 없으면 최초 실행 시 자동 생성
"""
from __future__ import annotations

import datetime as dt
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

# 형제 모듈 import 보장.
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import seed as seedmod  # noqa: E402  (재시드 버튼용)
from schema import DEMO_NOW, MODULE_TYPES, demo_db_path, slots_for  # noqa: E402

DB = str(demo_db_path())

# 탈거 사유 -> (라벨, 탈거 후 모듈 상태)
REMOVE_REASONS = {
    "fault": ("fault (고장)", "faulty"),
    "preventive": ("preventive (예방정비)", "removed"),
    "refurb": ("refurb (리퍼)", "refurbished"),
}


# ----------------------------- DB 접근 -----------------------------
def _db_version() -> float:
    """DB 파일 mtime. 캐시 키에 넣어, 재시드/쓰기로 DB 가 바뀌면 캐시를 자동 무효화한다."""
    try:
        return Path(DB).stat().st_mtime
    except OSError:
        return 0.0


@st.cache_data
def _q(sql: str, params: tuple, db_version: float) -> pd.DataFrame:
    # db_version 은 캐시 키 전용(언더스코어 없는 이름이라 해시됨). 본문에선 쓰지 않는다.
    con = sqlite3.connect(DB)
    try:
        return pd.read_sql_query(sql, con, params=params)
    finally:
        con.close()


def q(sql: str, params: tuple = ()) -> pd.DataFrame:
    return _q(sql, params, _db_version())


def write(statements: list[tuple[str, tuple]]) -> None:
    """여러 문장을 한 트랜잭션으로 실행 후 읽기 캐시 무효화."""
    con = sqlite3.connect(DB)
    try:
        cur = con.cursor()
        for sql, params in statements:
            cur.execute(sql, params)
        con.commit()
    finally:
        con.close()
    _q.clear()


USAGE_SQL = """
SELECT m.id, m.serial, m.module_type, m.status, m.rated_life,
       COUNT(co.id) AS total_cooks,
       ROUND(100.0*COUNT(co.id)/m.rated_life, 1) AS pct_used
FROM modules m
LEFT JOIN module_placements mp ON mp.module_id = m.id
LEFT JOIN cook_order co
  ON co.product_id = mp.product_id AND co.started_at >= mp.valid_from
 AND (mp.valid_to IS NULL OR co.started_at < mp.valid_to)
GROUP BY m.id ORDER BY pct_used DESC
"""


def current_slots(product_id: int) -> dict[str, tuple[int, str]]:
    """현재 장착(valid_to IS NULL) 중인 모듈 종류 -> (module_id, serial)."""
    cur = q(
        """SELECT mp.position_code, m.id, m.serial
           FROM module_placements mp JOIN modules m ON m.id = mp.module_id
           WHERE mp.product_id = ? AND mp.valid_to IS NULL""",
        (int(product_id),),
    )
    return {r.position_code: (int(r.id), r.serial) for r in cur.itertuples()}


# 장착 가능 상태. scrapped(폐기)/faulty(고장)는 재장착 불가.
INSTALLABLE_STATUS = ("in_stock", "refurbished", "removed")


def free_inventory(module_type: str) -> pd.DataFrame:
    """현재 어디에도 장착되지 않고 장착 가능 상태인(=재고) 특정 종류 모듈."""
    placeholders = ",".join("?" * len(INSTALLABLE_STATUS))
    return q(
        f"""SELECT id, serial, status FROM modules
            WHERE module_type = ? AND status IN ({placeholders})
              AND id NOT IN (SELECT module_id FROM module_placements WHERE valid_to IS NULL)
            ORDER BY serial""",
        (module_type, *INSTALLABLE_STATUS),
    )


# ----------------------------- 페이지 -----------------------------
st.set_page_config(page_title="모듈 관리 데모", page_icon="🔧", layout="wide")

if not Path(DB).exists():
    # 최초 실행(예: Streamlit Cloud) 시 가상 데이터를 자동 생성한다.
    seedmod.build(DB)

PAGES = ["장비 상세", "데이터 입력", "모듈 상세", "모듈 Fleet 개요", "ERD"]
EDIT_PAGE = "데이터 입력"  # 유일하게 데이터를 입력/수정하는 화면


def _page_label(p: str) -> str:
    return ("✏️ " + p) if p == EDIT_PAGE else ("📄 " + p)


st.sidebar.caption("📄 조회 전용 화면 (4) · ✏️ 입력/수정 화면 (1)")
page = st.sidebar.radio("화면", PAGES, format_func=_page_label)
st.sidebar.caption(
    "모듈(부품) 단위 관리 데모.\n\n"
    "조리 1회 = `cook_order` 1건. 모듈 사용량 = 장착 기간 동안 그 장비에서 발생한 조리 횟수."
)
st.sidebar.divider()
if st.sidebar.button("데모 데이터 초기화(재시드)"):
    seedmod.build(DB)
    _q.clear()
    st.sidebar.success("재시드 완료")
    st.rerun()


def _red_over_100(v):
    return "color:red; font-weight:600" if isinstance(v, (int, float)) and v >= 100 else ""


# ================================ 모듈 Fleet 개요 ================================
if page == "모듈 Fleet 개요":
    st.title("📄 모듈 Fleet 개요")
    st.caption("전체 모듈의 수명 소진 현황을 한눈에. 정격 대비 100% 초과(빨강)는 교체 권장 대상.")

    usage = q(USAGE_SQL)
    c1, c2, c3 = st.columns(3)
    c1.metric("총 모듈 수", len(usage))
    c2.metric("수명 임박 (≥75%)", int((usage.pct_used >= 75).sum()))
    c3.metric("가동 장비 수", int(q("SELECT COUNT(*) n FROM products").n[0]))

    st.subheader("수명 임박 워치리스트")
    st.caption("사용률 내림차순. ≥100% 는 정격수명 초과 — 우선 교체 검토.")
    watch = usage[usage.pct_used >= 75].reset_index(drop=True)
    st.dataframe(watch.style.map(_red_over_100, subset=["pct_used"]), width="stretch")

    st.subheader("종류별 평균 사용률")
    st.bar_chart(usage.groupby("module_type").pct_used.mean())

# ================================ 2. 장비 상세 ================================
elif page == "장비 상세":
    st.title("📄 장비 상세")
    st.caption("장비별 현재 모듈 구성과 설치/교체 이력. 이력은 초기 설치(인도 시점)부터 출발한다.")

    prods = q("SELECT id, name, serial, model, kitchen_id FROM products")
    pinfo = prods.set_index("id")
    pid = st.selectbox("장비", prods.id, format_func=lambda i: f"{pinfo.loc[i, 'name']} ({pinfo.loc[i, 'serial']})")
    model = pinfo.loc[pid, "model"]
    st.caption(f"장비 시리얼: **{pinfo.loc[pid, 'serial']}** · model: {model}")

    usage = q(USAGE_SQL).set_index("id")
    filled = current_slots(pid)
    st.subheader(f"현재 모듈 구성 (model={model})")
    rows = []
    for slot in slots_for(model):
        if slot in filled:
            mid, serial = filled[slot]
            rows.append({
                "모듈 종류": slot,
                "장착 모듈": serial,
                "사용률": f"{usage.loc[mid, 'pct_used']}%",
            })
        else:
            rows.append({"모듈 종류": slot, "장착 모듈": "— 비어있음 —", "사용률": "—"})
    st.table(pd.DataFrame(rows))

    st.subheader("설치 / 교체 이력")
    st.caption("초기 설치(valid_to 비어있는 현재 장착 포함)부터 전체. 고장 탈거는 고장 모드 포함.")
    hist = q(
        """SELECT mp.position_code AS "모듈 종류", m.serial AS 시리얼,
                  mp.valid_from AS 설치, mp.valid_to AS 탈거,
                  mp.removed_reason AS 탈거사유, mp.fault_mode AS 고장모드
           FROM module_placements mp JOIN modules m ON m.id = mp.module_id
           WHERE mp.product_id = ? ORDER BY mp.valid_from, mp.position_code""",
        (int(pid),),
    )
    hist = hist.copy()
    hist["탈거"] = hist["탈거"].fillna("— 현재 장착 중 —")
    st.dataframe(hist, width="stretch")

# ================================ 3. 모듈 상세 ================================
elif page == "모듈 상세":
    st.title("📄 모듈 상세")
    st.caption(
        "부품 한 개의 일생. **고객사별 사용량 분해**가 핵심 — 리퍼된 장비의 부품은 "
        "사용량이 여러 고객사에 걸쳐 쌓인다 (예: SN-MN-0001)."
    )

    mods = q("SELECT id, serial, module_type, rated_life FROM modules ORDER BY serial")
    mid = st.selectbox("모듈", mods.id, format_func=lambda i: mods.set_index("id").loc[i, "serial"])
    row = mods.set_index("id").loc[mid]

    usage = q(USAGE_SQL).set_index("id")
    used = int(usage.loc[mid, "total_cooks"])
    pct = float(usage.loc[mid, "pct_used"])
    st.metric(f"누적 사용량 / 정격 {int(row.rated_life)} ({row.module_type})", f"{used}회", f"{pct}%")
    st.progress(min(pct / 100, 1.0))
    if pct >= 100:
        st.warning("정격수명 초과 — 교체 권장.")

    st.subheader("고객사별 사용량 분해")
    st.caption("이 부품이 머문 각 고객사에서 누적된 조리 횟수.")
    bd = q(
        """SELECT k.name AS kitchen, COUNT(co.id) AS cooks
           FROM module_placements mp
           JOIN cook_order co ON co.product_id = mp.product_id
             AND co.started_at >= mp.valid_from
             AND (mp.valid_to IS NULL OR co.started_at < mp.valid_to)
           JOIN kitchen k ON k.id = co.kitchen_id
           WHERE mp.module_id = ? GROUP BY k.id""",
        (int(mid),),
    )
    if bd.empty:
        st.info("이 모듈은 아직 사용 기록이 없습니다(창고 재고이거나 신규 장착).")
    else:
        st.bar_chart(bd.set_index("kitchen"))

    st.subheader("장착 이력")
    st.caption("어느 장비의 어느 모듈 종류로 언제부터 언제까지 있었는지 (탈거가 비어있으면 현재 장착 중).")
    tl = q(
        """SELECT p.name AS 장비, mp.position_code AS "모듈 종류",
                  mp.valid_from AS 설치, mp.valid_to AS 탈거,
                  mp.removed_reason AS 탈거사유, mp.fault_mode AS 고장모드
           FROM module_placements mp JOIN products p ON p.id = mp.product_id
           WHERE mp.module_id = ? ORDER BY mp.valid_from""",
        (int(mid),),
    )
    if tl.empty:
        st.info("장착 이력 없음 (창고 재고).")
    else:
        tl = tl.copy()
        tl["탈거"] = tl["탈거"].fillna("— 현재 장착 중 —")
        st.dataframe(tl, width="stretch")

# ================================ 4. 데이터 입력 ================================
elif page == "데이터 입력":
    st.title("✏️ 데이터 입력 — 모듈 장착·교체·탈거")
    st.caption(
        "실제 현장 작업자가 모듈을 다루듯 입력해 본다. **어떤 값이 허용되는지**와 "
        "**입력이 어떻게 반영되는지**를 즉시 확인할 수 있다. (실제 데모 DB 에 기록되며, 사이드바 재시드로 복구)"
    )
    with st.expander("허용 규칙 (애플리케이션 레이어 검증, §2.4)"):
        st.markdown(
            "- 한 장비의 각 **모듈 종류 자리**에는 그 종류의 모듈만 장착할 수 있다.\n"
            "- 한 자리에는 동시에 **하나의 모듈만** 장착된다 (이미 장착돼 있으면 '교체' 사용).\n"
            "- 한 모듈은 동시에 **한 곳에만** 존재한다 → 다른 곳에 장착된 모듈은 선택 불가(재고만 선택 가능).\n"
            "- 장착 가능 상태(**in_stock / refurbished / removed**) 재고만 선택 가능 — scrapped/faulty 제외.\n"
            "- 그 모델에 정의된 **모듈 종류만** 장착 가능 (빈 자리 신규 장착)."
        )

    action = st.radio("작업", ["교체 (탈거 + 장착)", "신규 장착 (빈 자리)", "탈거"], horizontal=True)

    prods = q("SELECT id, name, serial, model FROM products")
    pinfo = prods.set_index("id")
    pid = int(st.selectbox("장비", prods.id, format_func=lambda i: f"{pinfo.loc[i, 'name']} ({pinfo.loc[i, 'serial']})"))
    model = pinfo.loc[pid, "model"]
    filled = current_slots(pid)

    eff_date = st.date_input("적용일", value=DEMO_NOW)
    eff = dt.datetime.combine(eff_date, dt.time(12, 0, 0)).isoformat()

    # --- 교체 ---
    if action.startswith("교체"):
        occupied = [s for s in slots_for(model) if s in filled]
        if not occupied:
            st.info("장착된 모듈이 없습니다.")
            st.stop()
        slot = st.selectbox("모듈 종류 (장착됨)", occupied)
        old_mid, old_serial = filled[slot]
        st.write(f"현재 장착: **{old_serial}** ({slot})")

        reason = st.selectbox("탈거 사유", list(REMOVE_REASONS), format_func=lambda r: REMOVE_REASONS[r][0])

        inv = free_inventory(slot)
        st.caption(f"이 자리에는 **{slot}** 종류 재고만 장착 가능. 사용 가능 재고: {len(inv)}개")
        if inv.empty:
            st.warning(f"장착 가능한 {slot} 재고가 없습니다. 재고를 먼저 확보하세요.")
        else:
            new_mid = int(st.selectbox(
                "교체품(재고)", inv.id,
                format_func=lambda i: f"{inv.set_index('id').loc[i, 'serial']} ({inv.set_index('id').loc[i, 'status']})",
            ))
            if st.button("교체 적용", type="primary"):
                new_status = REMOVE_REASONS[reason][1]
                write([
                    ("UPDATE module_placements SET valid_to=?, removed_reason=?, fault_mode=NULL "
                     "WHERE product_id=? AND position_code=? AND valid_to IS NULL",
                     (eff, reason, pid, slot)),
                    ("UPDATE modules SET status=? WHERE id=?", (new_status, old_mid)),
                    ("INSERT INTO module_placements(module_id,product_id,position_code,valid_from,valid_to,removed_reason,fault_mode) "
                     "VALUES (?,?,?,?,NULL,NULL,NULL)", (new_mid, pid, slot, eff)),
                    ("UPDATE modules SET status='installed' WHERE id=?", (new_mid,)),
                ])
                st.success(
                    f"반영됨: {old_serial} 탈거({REMOVE_REASONS[reason][0]}) → 상태 '{new_status}', "
                    f"{slot} 자리에 {inv.set_index('id').loc[new_mid, 'serial']} 신규 장착."
                )

    # --- 신규 장착 ---
    elif action.startswith("신규"):
        empty = [s for s in slots_for(model) if s not in filled]
        if not empty:
            st.info("빈 자리가 없습니다. 교체를 사용하세요.")
            st.stop()
        slot = st.selectbox("빈 자리 (모듈 종류)", empty)
        inv = free_inventory(slot)
        st.caption(f"이 자리에는 **{slot}** 종류 재고만 장착 가능. 사용 가능 재고: {len(inv)}개")
        if inv.empty:
            st.warning(f"장착 가능한 {slot} 재고가 없습니다.")
        else:
            new_mid = int(st.selectbox(
                "장착할 모듈(재고)", inv.id,
                format_func=lambda i: f"{inv.set_index('id').loc[i, 'serial']} ({inv.set_index('id').loc[i, 'status']})",
            ))
            if st.button("장착 적용", type="primary"):
                write([
                    ("INSERT INTO module_placements(module_id,product_id,position_code,valid_from,valid_to,removed_reason,fault_mode) "
                     "VALUES (?,?,?,?,NULL,NULL,NULL)", (new_mid, pid, slot, eff)),
                    ("UPDATE modules SET status='installed' WHERE id=?", (new_mid,)),
                ])
                st.success(f"반영됨: {slot} 자리에 {inv.set_index('id').loc[new_mid, 'serial']} 신규 장착.")

    # --- 탈거 ---
    else:
        occupied = [s for s in slots_for(model) if s in filled]
        if not occupied:
            st.info("장착된 모듈이 없습니다.")
            st.stop()
        slot = st.selectbox("모듈 종류 (장착됨)", occupied)
        old_mid, old_serial = filled[slot]
        st.write(f"현재 장착: **{old_serial}** ({slot})")
        reason = st.selectbox("탈거 사유", list(REMOVE_REASONS), format_func=lambda r: REMOVE_REASONS[r][0])
        if st.button("탈거 적용", type="primary"):
            new_status = REMOVE_REASONS[reason][1]
            write([
                ("UPDATE module_placements SET valid_to=?, removed_reason=?, fault_mode=NULL "
                 "WHERE product_id=? AND position_code=? AND valid_to IS NULL",
                 (eff, reason, pid, slot)),
                ("UPDATE modules SET status=? WHERE id=?", (new_status, old_mid)),
            ])
            st.success(f"반영됨: {old_serial} 탈거({REMOVE_REASONS[reason][0]}) → 상태 '{new_status}'. {slot} 자리 비었음.")

    # --- 반영 결과(현재 상태) ---
    st.divider()
    st.subheader("반영 결과 — 현재 모듈 구성")
    now_filled = current_slots(pid)
    st.table(pd.DataFrame([
        {"모듈 종류": s, "장착 모듈": now_filled.get(s, (None, "— 비어있음 —"))[1]}
        for s in slots_for(model)
    ]))
    st.subheader("반영 결과 — 이 장비의 설치/교체 이력")
    h = q(
        """SELECT mp.position_code AS "모듈 종류", m.serial AS 시리얼, mp.valid_from AS 설치,
                  mp.valid_to AS 탈거, mp.removed_reason AS 탈거사유, mp.fault_mode AS 고장모드
           FROM module_placements mp JOIN modules m ON m.id = mp.module_id
           WHERE mp.product_id = ? ORDER BY mp.valid_from, mp.position_code""",
        (pid,),
    ).copy()
    h["탈거"] = h["탈거"].fillna("— 현재 장착 중 —")
    st.dataframe(h, width="stretch")

# ================================ 5. ERD ================================
else:
    import streamlit.components.v1 as components
    from erd import ERD_HEIGHT, erd_html

    st.title("📄 ERD — 관계 테이블")
    st.caption("이 기능이 손대는 테이블 관계. 🟩 신규 테이블 / 🟦 기존(재사용) 을 테두리 색으로 구분한다.")
    components.html(erd_html(), height=ERD_HEIGHT, scrolling=True)
    st.caption(
        "사용량 분해 핵심: `cook_order` 가 product_id 와 kitchen_id 를 모두 들고 있어, "
        "`cook_order ⋈ module_placements` 만으로 '어느 장비 + 어느 고객사' 사용량이 풀린다. "
        "`devices` · `product_history` 는 건드리지 않으므로 표시하지 않았다."
    )
