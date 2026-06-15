"""
devicehub 모듈 관리 데모 — Streamlit 앱.

목적: 관계자들과 "모듈 단위 관리(사용량·교체·이력)" 요구사항을 맞춰가는 토론 도구.
가상 데이터(seed.py)를 채운 SQLite 를 읽고/쓰며 6개 화면으로 보여준다.

  1. 매장 상세      — 매장(kitchen)에 현재/과거 설치된 장비 + 각 장비의 현재 모듈 구성
  2. 장비 상세      — 현재 위치(매장/창고) + 현재 모듈 구성 + 초기 설치부터의 설치/교체 이력
  3. 모듈 상세      — 부품 한 개의 누적 사용량 + 고객사별 분해 + 장착 이력
  4. 데이터 입력    — 실제 서비스처럼 모듈 장착/교체/탈거 입력(허용값·반영결과 확인)
  5. 모듈 Fleet 개요 — 전체 모듈 수명 소진 현황
  6. ERD            — 관계 테이블 다이어그램(기존/신규 색 구분)

매장→장비→모듈 사이는 화면 안의 버튼으로 상호 이동한다(요구사항: 메뉴 간 연동).

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


# ----------------------------- 화면 간 이동(라우팅) -----------------------------
# Streamlit 위젯 key 규칙 때문에, "버튼 클릭 → 콜백에서 이동 예약 → 다음 런 최상단에서 반영"
# 패턴을 쓴다. 콜백이 설정하는 "_nav" 는 위젯 key 가 아니라 언제 써도 안전하고,
# 라디오("page")·셀렉트박스("sel_*") key 는 위젯이 만들어지기 전(최상단)에만 바꾼다.
def _request_nav(page: str, **entity) -> None:
    """버튼 콜백: 다음 런에서 이동할 페이지와 미리 선택할 대상을 예약한다."""
    st.session_state["_nav"] = (page, entity)


def _apply_pending_nav() -> None:
    """런 최상단(위젯 생성 전)에 호출 — 예약된 이동을 session_state 에 반영한다."""
    if "_nav" in st.session_state:
        page, entity = st.session_state.pop("_nav")
        st.session_state["page"] = page
        for k, v in entity.items():
            st.session_state[k] = v


def nav_button(label: str, page: str, *, key: str, ctx: str,
               help: str | None = None, **entity) -> None:
    """클릭하면 `page` 로 이동하며 entity(예: sel_product=3)를 미리 선택하는 버튼."""
    st.button(
        label, key=f"nav_{ctx}_{key}", help=help,
        on_click=_request_nav, args=(page,), kwargs=entity,
        use_container_width=True,
    )


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

PAGES = ["매장 상세", "장비 상세", "모듈 상세", "데이터 입력", "모듈 Fleet 개요", "ERD"]
EDIT_PAGE = "데이터 입력"  # 유일하게 데이터를 입력/수정하는 화면

# 예약된 화면 이동을 라디오/셀렉트박스가 만들어지기 전에 반영한다(위젯 key 충돌 방지).
_apply_pending_nav()
if "page" not in st.session_state:
    st.session_state["page"] = PAGES[0]


def _page_label(p: str) -> str:
    return ("✏️ " + p) if p == EDIT_PAGE else ("📄 " + p)


st.sidebar.caption("📄 조회 전용 화면 (5) · ✏️ 입력/수정 화면 (1)")
page = st.sidebar.radio("화면", PAGES, key="page", format_func=_page_label)
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


# ================================ 1. 매장 상세 ================================
if page == "매장 상세":
    st.title("📄 매장 상세")
    st.caption(
        "매장(kitchen) 기준 조회. 현재 설치된 장비와 각 장비의 모듈 구성, 그리고 과거 이 매장에 "
        "있다가 이동/교체된 장비. **장비를 클릭하면 장비 상세로, 모듈을 클릭하면 모듈 상세로** 이동합니다."
    )

    kitchens = q("SELECT id, name, brand_id FROM kitchen ORDER BY id")
    kinfo = kitchens.set_index("id")
    kid = int(st.selectbox(
        "매장", kitchens.id, key="sel_kitchen",
        format_func=lambda i: f"{kinfo.loc[i, 'name']} (kitchen #{i})",
    ))
    usage = q(USAGE_SQL).set_index("id")
    kname = dict(q("SELECT id, name FROM kitchen").itertuples(index=False, name=None))

    # --- 현재 설치된 장비 ---
    st.subheader("현재 설치된 장비")
    cur_dev = q("SELECT id, name, serial, model FROM products WHERE kitchen_id = ? ORDER BY id", (kid,))
    if cur_dev.empty:
        st.info("이 매장에 현재 설치된 장비가 없습니다.")
    else:
        for r in cur_dev.itertuples():
            with st.container(border=True):
                c1, c2 = st.columns([3, 4])
                with c1:
                    nav_button(
                        f"🍳 {r.name} ({r.serial})", "장비 상세",
                        key=f"dev{r.id}", ctx="store_curdev",
                        sel_product=int(r.id), help="장비 상세로 이동",
                    )
                    st.caption(f"model: {r.model}")
                with c2:
                    st.caption("현재 모듈 구성 (클릭 → 모듈 상세)")
                    filled = current_slots(r.id)
                    for slot in slots_for(r.model):
                        if slot in filled:
                            mid, serial = filled[slot]
                            pct = usage.loc[mid, "pct_used"] if mid in usage.index else "—"
                            nav_button(
                                f"🔩 {slot}: {serial} · {pct}%", "모듈 상세",
                                key=f"dev{r.id}-{slot}", ctx="store_mod",
                                sel_module=int(mid), help="모듈 상세로 이동",
                            )
                        else:
                            st.write(f"🔩 {slot}: — 비어있음 —")

    # --- 과거(이동/교체) 장비: cook_order 에서 파생 ---
    st.subheader("과거 이 매장에 있던 장비 (이동/교체)")
    st.caption(
        "이 매장에서 조리 기록은 있으나 현재는 다른 위치에 있는 장비. 전용 이력 테이블 대신 "
        "`cook_order` 에서 파생하며, 기간은 이 매장에서의 첫/마지막 조리 시각 기준이다."
    )
    past = q(
        """SELECT p.id AS id, p.name AS name, p.serial AS serial, p.kitchen_id AS cur_kitchen,
                  MIN(co.started_at) AS first_cook, MAX(co.started_at) AS last_cook,
                  COUNT(*) AS cooks
           FROM cook_order co JOIN products p ON p.id = co.product_id
           WHERE co.kitchen_id = ? AND (p.kitchen_id IS NULL OR p.kitchen_id <> ?)
           GROUP BY p.id ORDER BY last_cook DESC""",
        (kid, kid),
    )
    if past.empty:
        st.info("과거 이 매장에 있다가 이동한 장비 기록이 없습니다.")
    else:
        for r in past.itertuples():
            with st.container(border=True):
                c1, c2 = st.columns([3, 4])
                with c1:
                    nav_button(
                        f"🍳 {r.name} ({r.serial})", "장비 상세",
                        key=f"pastdev{r.id}", ctx="store_pastdev",
                        sel_product=int(r.id), help="장비 상세로 이동",
                    )
                with c2:
                    cur_loc = kname.get(int(r.cur_kitchen)) if pd.notna(r.cur_kitchen) else None
                    st.write(f"기간: {str(r.first_cook)[:10]} ~ {str(r.last_cook)[:10]} · 조리 {int(r.cooks):,}회")
                    st.write(f"현재 위치: **{cur_loc or '출하 대기(창고)'}**")

# ================================ 모듈 Fleet 개요 ================================
elif page == "모듈 Fleet 개요":
    st.title("📄 모듈 Fleet 개요")
    st.caption("전체 모듈의 수명 소진 현황을 한눈에. 정격 대비 100% 초과(빨강)는 교체 권장 대상.")

    usage = q(USAGE_SQL)
    c1, c2, c3 = st.columns(3)
    c1.metric("총 모듈 수", len(usage))
    c2.metric("수명 임박 (≥75%)", int((usage.pct_used >= 75).sum()))
    c3.metric("가동 장비 수", int(q("SELECT COUNT(*) n FROM products WHERE kitchen_id IS NOT NULL").n[0]))

    st.subheader("수명 임박 워치리스트")
    st.caption("사용률 내림차순. ≥100% 는 정격수명 초과 — 우선 교체 검토.")
    watch = usage[usage.pct_used >= 75].reset_index(drop=True)
    st.dataframe(watch.style.map(_red_over_100, subset=["pct_used"]), width="stretch")

    st.subheader("종류별 평균 사용률")
    st.bar_chart(usage.groupby("module_type").pct_used.mean())

# ================================ 2. 장비 상세 ================================
elif page == "장비 상세":
    st.title("📄 장비 상세")
    st.caption(
        "장비별 현재 위치(매장/창고), 현재 모듈 구성, 설치/교체 이력. "
        "**매장을 클릭하면 매장 상세로, 모듈 시리얼을 클릭하면 모듈 상세로** 이동합니다."
    )

    prods = q("SELECT id, name, serial, model, kitchen_id FROM products ORDER BY id")
    pinfo = prods.set_index("id")
    pid = int(st.selectbox(
        "장비", prods.id, key="sel_product",
        format_func=lambda i: f"{pinfo.loc[i, 'name']} ({pinfo.loc[i, 'serial']})",
    ))
    model = pinfo.loc[pid, "model"]

    # --- 현재 위치: 매장(클릭 이동) 또는 출하 대기 창고 ---
    st.subheader("현재 위치")
    cur_kid = pinfo.loc[pid, "kitchen_id"]
    if pd.notna(cur_kid):
        kid_int = int(cur_kid)
        knm = q("SELECT name FROM kitchen WHERE id = ?", (kid_int,))
        nm = knm.name[0] if not knm.empty else f"kitchen #{kid_int}"
        c1, c2 = st.columns([1, 3])
        c1.caption("설치 매장")
        with c2:
            nav_button(f"🏬 {nm}", "매장 상세", key=f"loc{pid}", ctx="dev_loc",
                       sel_kitchen=kid_int, help="매장 상세로 이동")
    else:
        st.info("🏭 출하 대기(창고) — 아직 매장에 설치되지 않은 장비입니다.")
    st.caption(f"장비 시리얼: **{pinfo.loc[pid, 'serial']}** · model: {model}")

    usage = q(USAGE_SQL).set_index("id")
    filled = current_slots(pid)
    st.subheader(f"현재 모듈 구성 (model={model})")
    hc = st.columns([2, 4, 1])
    for col, t in zip(hc, ["모듈 종류", "장착 모듈", "사용률"]):
        col.markdown(f"**{t}**")
    for slot in slots_for(model):
        c = st.columns([2, 4, 1])
        c[0].write(slot)
        if slot in filled:
            mid, serial = filled[slot]
            with c[1]:
                nav_button(f"🔩 {serial}", "모듈 상세", key=f"cfg-{slot}", ctx="dev_cfg",
                           sel_module=int(mid), help="모듈 상세로 이동")
            c[2].write(f"{usage.loc[mid, 'pct_used']}%" if mid in usage.index else "—")
        else:
            c[1].write("— 비어있음 —")
            c[2].write("—")

    st.subheader("설치 / 교체 이력")
    st.caption("초기 설치(현재 장착 포함)부터 전체. 시리얼을 클릭하면 모듈 상세로 이동합니다.")
    hist = q(
        """SELECT mp.id AS pl_id, m.id AS module_id, mp.position_code AS slot, m.serial AS serial,
                  mp.valid_from AS vf, mp.valid_to AS vt,
                  mp.removed_reason AS reason, mp.fault_mode AS fmode
           FROM module_placements mp JOIN modules m ON m.id = mp.module_id
           WHERE mp.product_id = ? ORDER BY mp.valid_from, mp.position_code""",
        (pid,),
    )
    if hist.empty:
        st.info("이력이 없습니다.")
    else:
        hc = st.columns([2, 2.5, 2, 2, 1.5, 1.5])
        for col, t in zip(hc, ["모듈 종류", "시리얼", "설치", "탈거", "탈거사유", "고장모드"]):
            col.markdown(f"**{t}**")
        for r in hist.itertuples():
            c = st.columns([2, 2.5, 2, 2, 1.5, 1.5])
            c[0].write(r.slot)
            with c[1]:
                nav_button(f"🔩 {r.serial}", "모듈 상세", key=f"hist{r.pl_id}", ctx="dev_hist",
                           sel_module=int(r.module_id), help="모듈 상세로 이동")
            c[2].write(str(r.vf)[:10])
            c[3].write(str(r.vt)[:10] if pd.notna(r.vt) else "— 현재 장착 중 —")
            c[4].write(r.reason if pd.notna(r.reason) else "—")
            c[5].write(r.fmode if pd.notna(r.fmode) else "—")

# ================================ 3. 모듈 상세 ================================
elif page == "모듈 상세":
    st.title("📄 모듈 상세")
    st.caption(
        "부품 한 개의 일생. **고객사별 사용량 분해**가 핵심 — 리퍼된 장비의 부품은 "
        "사용량이 여러 고객사에 걸쳐 쌓인다 (예: SN-MN-0001). "
        "**매장을 클릭하면 매장 상세로, 장비를 클릭하면 장비 상세로** 이동합니다."
    )

    mods = q("SELECT id, serial, module_type, rated_life FROM modules ORDER BY serial")
    minfo = mods.set_index("id")
    mid = int(st.selectbox("모듈", mods.id, key="sel_module",
                           format_func=lambda i: minfo.loc[i, "serial"]))
    row = minfo.loc[mid]

    usage = q(USAGE_SQL).set_index("id")
    used = int(usage.loc[mid, "total_cooks"])
    pct = float(usage.loc[mid, "pct_used"])
    st.metric(f"누적 사용량 / 정격 {int(row.rated_life)} ({row.module_type})", f"{used}회", f"{pct}%")
    st.progress(min(pct / 100, 1.0))
    if pct >= 100:
        st.warning("정격수명 초과 — 교체 권장.")

    st.subheader("고객사별 사용량 분해")
    st.caption("이 부품이 머문 각 고객사에서 누적된 조리 횟수. 매장을 클릭하면 매장 상세로 이동합니다.")
    bd = q(
        """SELECT k.id AS kitchen_id, k.name AS kitchen, COUNT(co.id) AS cooks
           FROM module_placements mp
           JOIN cook_order co ON co.product_id = mp.product_id
             AND co.started_at >= mp.valid_from
             AND (mp.valid_to IS NULL OR co.started_at < mp.valid_to)
           JOIN kitchen k ON k.id = co.kitchen_id
           WHERE mp.module_id = ? GROUP BY k.id ORDER BY cooks DESC""",
        (mid,),
    )
    if bd.empty:
        st.info("이 모듈은 아직 사용 기록이 없습니다(창고 재고이거나 신규 장착).")
    else:
        st.bar_chart(bd.set_index("kitchen")["cooks"])
        st.caption("매장 바로가기")
        bcols = st.columns(len(bd))
        for col, r in zip(bcols, bd.itertuples()):
            with col:
                nav_button(f"🏬 {r.kitchen} · {int(r.cooks):,}회", "매장 상세",
                           key=f"bd{r.kitchen_id}", ctx="mod_bd",
                           sel_kitchen=int(r.kitchen_id), help="매장 상세로 이동")

    st.subheader("장착 이력")
    st.caption("어느 장비에 언제부터 언제까지 있었는지. 장비를 클릭하면 장비 상세로 이동합니다.")
    tl = q(
        """SELECT p.id AS product_id, p.name AS dev, mp.position_code AS slot,
                  mp.valid_from AS vf, mp.valid_to AS vt,
                  mp.removed_reason AS reason, mp.fault_mode AS fmode
           FROM module_placements mp JOIN products p ON p.id = mp.product_id
           WHERE mp.module_id = ? ORDER BY mp.valid_from""",
        (mid,),
    )
    if tl.empty:
        st.info("장착 이력 없음 (창고 재고).")
    else:
        hc = st.columns([2.5, 2, 2, 2, 1.5, 1.5])
        for col, t in zip(hc, ["장비", "모듈 종류", "설치", "탈거", "탈거사유", "고장모드"]):
            col.markdown(f"**{t}**")
        for i, r in enumerate(tl.itertuples()):
            c = st.columns([2.5, 2, 2, 2, 1.5, 1.5])
            with c[0]:
                nav_button(f"🍳 {r.dev}", "장비 상세", key=f"tl{i}", ctx="mod_hist",
                           sel_product=int(r.product_id), help="장비 상세로 이동")
            c[1].write(r.slot)
            c[2].write(str(r.vf)[:10])
            c[3].write(str(r.vt)[:10] if pd.notna(r.vt) else "— 현재 장착 중 —")
            c[4].write(r.reason if pd.notna(r.reason) else "—")
            c[5].write(r.fmode if pd.notna(r.fmode) else "—")

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
