"""
devicehub 모듈 관리 데모 — Streamlit 앱.

목적: 관계자들과 "모듈 단위 관리(사용량·교체·이력)" 요구사항을 맞춰가는 토론 도구.
가상 데이터(seed.py)를 채운 SQLite 를 읽고/쓰며 6개 화면으로 보여준다.

  1. 매장 상세      — 매장(kitchen)에 현재/과거 설치된 장비 + 각 장비의 현재 모듈 구성
  2. 장비 상세      — 현재 위치(매장/창고) + 현재 모듈 구성 + 초기 설치부터의 설치/교체 이력
  3. 모듈 상세      — 부품 한 개의 누적 사용량 + 고객사별 분해 + 장착 이력
  4. 장비 정비    — 실제 서비스처럼 모듈 장착/교체/탈거 입력(허용값·반영결과 확인)
  5. 모듈 Fleet 개요 — 전체 모듈 수명 소진 현황
  6. ERD            — 관계 테이블 다이어그램(기존/신규 색 구분)

매장→장비→모듈 사이는 화면 안의 버튼으로 상호 이동한다(요구사항: 메뉴 간 연동).

Run:
    streamlit run streamlit_app.py    # DB 가 없으면 최초 실행 시 자동 생성
"""
from __future__ import annotations

import datetime as dt
import hashlib
import io
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
from module_schema import (  # noqa: E402  (PyPI 'schema' 패키지와 충돌 방지로 모듈명 변경)
    DEMO_NOW, HW_VERSIONS, INSTALLABLE_STATUSES, MODEL_SLOTS, MODULE_TYPES, demo_db_path, slots_for,
)

DB = str(demo_db_path())

# 탈거 사유 -> (라벨, 탈거 후 모듈 상태)
REMOVE_REASONS = {
    "fault": ("fault (고장)", "faulty"),
    "preventive": ("preventive (예방정비)", "serviceable"),
    "refurb": ("refurb (리퍼)", "refurbished"),
    "scrap": ("scrap (수명도과/폐기)", "scrapped"),
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
        # 두 그룹 라디오 중 해당 그룹을 선택하고 반대 그룹은 해제한다.
        if page in EDIT_PAGES:
            st.session_state["nav_edit"], st.session_state["nav_view"] = page, None
        else:
            st.session_state["nav_view"], st.session_state["nav_edit"] = page, None
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
SELECT m.id, m.serial, m.module_type,
       CASE WHEN EXISTS (SELECT 1 FROM module_placements o
                         WHERE o.module_id = m.id AND o.valid_to IS NULL)
            THEN 'installed' ELSE m.status END AS status,
       m.rated_life,
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


def free_inventory(module_type: str) -> pd.DataFrame:
    """어디에도 장착되지 않고(=열린 placement 없음) 장착 가능 상태인(=재고) 특정 종류 모듈.

    '장착 여부'는 placement 로만 판정한다(단일 소스). status 는 장착 가능 처분만 본다.
    """
    placeholders = ",".join("?" * len(INSTALLABLE_STATUSES))
    return q(
        f"""SELECT id, serial, status FROM modules
            WHERE module_type = ? AND status IN ({placeholders})
              AND id NOT IN (SELECT module_id FROM module_placements WHERE valid_to IS NULL)
            ORDER BY serial""",
        (module_type, *INSTALLABLE_STATUSES),
    )


def is_installed(module_id: int) -> bool:
    """현재 장착 중(열린 placement 존재) 여부 — 장착 여부의 단일 소스."""
    return not q(
        "SELECT 1 FROM module_placements WHERE module_id = ? AND valid_to IS NULL LIMIT 1",
        (int(module_id),),
    ).empty


def has_placement_history(module_id: int) -> bool:
    """장착/사용 이력이 한 번이라도 있는지 — 삭제 가능 여부 판정에 쓴다."""
    return not q(
        "SELECT 1 FROM module_placements WHERE module_id = ? LIMIT 1", (int(module_id),)
    ).empty


def now_iso() -> str:
    """감사 타임스탬프(실제 작업 시각, 초 단위)."""
    return dt.datetime.now().isoformat(timespec="seconds")


def audit_stmt(module_id: int | None, serial: str, action: str, detail: str, actor: str):
    """append-only 감사 로그 INSERT 문. write() statements 리스트에 끼워 같은 트랜잭션으로 기록한다.

    module_id 는 FK 가 아니다 — 삭제된 모듈의 감사행도 보존되도록.
    """
    return (
        "INSERT INTO module_audit(module_id, serial, action, detail, actor, ts) VALUES (?,?,?,?,?,?)",
        (module_id, serial, action, detail, actor, now_iso()),
    )


def insert_modules(file_hash: str | None, source: str, rows: list[tuple], actor: str) -> int:
    """입고 배치 1건 + 그 배치의 모듈들을 한 트랜잭션으로 등록(상태 serviceable). batch_id 반환.

    rows: (serial, module_type, hardware_version, vendor_id, received_date) 의 리스트.
    actor: 감사 기록용 작업자 — created_by/updated_by 에 남는다.
    """
    ts = now_iso()
    con = sqlite3.connect(DB)
    try:
        cur = con.cursor()
        cur.execute(
            "INSERT INTO upload_batch(file_hash, source_name, uploaded_at, row_count) VALUES (?,?,?,?)",
            (file_hash, source, DEMO_NOW.isoformat(), len(rows)),
        )
        bid = cur.lastrowid
        cur.executemany(
            "INSERT INTO modules(serial, module_type, hardware_version, vendor_id, received_date, "
            "batch_id, status, rated_life, created_at, created_by, updated_at, updated_by) "
            "VALUES (?,?,?,?,?,?, 'serviceable', ?, ?, ?, ?, ?)",
            [(s, t, hw, vid, rcv, bid, MODULE_TYPES[t], ts, actor, ts, actor)
             for (s, t, hw, vid, rcv) in rows],
        )
        cur.executemany(
            "INSERT INTO module_audit(module_id, serial, action, detail, actor, ts) "
            "SELECT id, serial, 'insert', ?, ?, ? FROM modules WHERE serial = ?",
            [(f"입고 (배치 #{bid}, {source})", actor, ts, s) for (s, t, hw, vid, rcv) in rows],
        )
        con.commit()
    finally:
        con.close()
    _q.clear()
    return bid


def assemble_product(name: str, serial: str, model: str,
                     slot_modules: dict[str, int], assemble_date) -> int:
    """새 product 생성(출하 대기/창고) + 각 슬롯에 재고 모듈 장착(placement). product_id 반환.

    설치는 단일 소스대로 placement 만 만든다(modules.status 불변). 장착 이력은 module_placements 에 남는다.
    """
    eff = dt.datetime.combine(assemble_date, dt.time(12, 0, 0)).isoformat()
    con = sqlite3.connect(DB)
    try:
        cur = con.cursor()
        cur.execute(
            "INSERT INTO products(name, serial, model, kitchen_id) VALUES (?,?,?,NULL)",
            (name, serial, model),
        )
        pid = cur.lastrowid
        for slot, mid in slot_modules.items():
            cur.execute(
                "INSERT INTO module_placements(module_id, product_id, position_code, valid_from, "
                "valid_to, removed_reason, fault_mode) VALUES (?,?,?,?,NULL,NULL,NULL)",
                (mid, pid, slot, eff),
            )
        con.commit()
    finally:
        con.close()
    _q.clear()
    return pid


# ----------------------------- 페이지 -----------------------------
st.set_page_config(page_title="모듈 관리 데모", page_icon="🔧", layout="wide")

if not Path(DB).exists():
    # 최초 실행(예: Streamlit Cloud) 시 가상 데이터를 자동 생성한다.
    seedmod.build(DB)

# 화면을 두 그룹으로 분리한다.
#   ✏️ 입력/수정 — 업무 flow 순서(재고 → 조립 → 정비). 이 데모의 핵심.
#   📄 조회/분석 — 분석·참고용(보조).
EDIT_PAGES = ["재고 현황 및 관리", "제품 조립", "장비 정비"]
VIEW_PAGES = ["장비 상세", "매장 상세", "모듈 상세", "모듈 Fleet 개요", "ERD"]
PAGES = EDIT_PAGES + VIEW_PAGES

# 예약된 화면 이동을 라디오/셀렉트박스가 만들어지기 전에 반영한다(위젯 key 충돌 방지).
_apply_pending_nav()
if "page" not in st.session_state:
    st.session_state["page"] = EDIT_PAGES[0]


def _pick_edit() -> None:
    st.session_state["page"] = st.session_state["nav_edit"]
    st.session_state["nav_view"] = None  # 반대 그룹 선택 해제


def _pick_view() -> None:
    st.session_state["page"] = st.session_state["nav_view"]
    st.session_state["nav_edit"] = None


_cur = st.session_state["page"]
st.sidebar.markdown("### ✏️ 입력 / 수정")
st.sidebar.caption("재고 → 조립 → 정비 (업무 순서)")
st.sidebar.radio(
    "입력/수정 화면", EDIT_PAGES, key="nav_edit",
    index=EDIT_PAGES.index(_cur) if _cur in EDIT_PAGES else None,
    on_change=_pick_edit, label_visibility="collapsed",
)
st.sidebar.markdown("### 📄 조회 / 분석")
st.sidebar.caption("분석·참고용 (보조)")
st.sidebar.radio(
    "조회/분석 화면", VIEW_PAGES, key="nav_view",
    index=VIEW_PAGES.index(_cur) if _cur in VIEW_PAGES else None,
    on_change=_pick_view, label_visibility="collapsed",
)
page = st.session_state["page"]
st.sidebar.divider()
st.sidebar.caption(
    "모듈(부품) 단위 관리 데모.\n\n"
    "조리 1회 = `cook_order` 1건. 모듈 사용량 = 장착 기간 동안 그 장비에서 발생한 조리 횟수."
)
st.sidebar.divider()
actor = (st.sidebar.text_input("작업자 (감사 기록용)", value="demo").strip() or "demo")
st.sidebar.caption("입력·수정·탈거 시 modules.created_by / updated_by 에 기록됩니다.")
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

    mods = q(
        """SELECT m.id, m.serial, m.module_type, m.hardware_version, m.received_date,
                  m.status, m.rated_life, v.name AS vendor,
                  m.created_at, m.created_by, m.updated_at, m.updated_by
           FROM modules m LEFT JOIN vendor v ON v.id = m.vendor_id ORDER BY m.serial"""
    )
    minfo = mods.set_index("id")
    mid = int(st.selectbox("모듈", mods.id, key="sel_module",
                           format_func=lambda i: minfo.loc[i, "serial"]))
    row = minfo.loc[mid]

    # 현재 상태는 단일 소스(placement)에서 파생: 열린 placement 있으면 '장착 중', 아니면 status.
    loc = q(
        """SELECT p.name AS dev FROM module_placements mp JOIN products p ON p.id = mp.product_id
           WHERE mp.module_id = ? AND mp.valid_to IS NULL LIMIT 1""",
        (mid,),
    )
    cur_state = f"🔧 장착 중 · {loc.dev[0]}" if not loc.empty else f"📦 재고 · {row.status}"
    ic = st.columns(4)
    ic[0].metric("공급사", row.vendor or "—")
    ic[1].metric("hardware_version", row.hardware_version or "—")
    ic[2].metric("입고일", str(row.received_date)[:10] if pd.notna(row.received_date) else "—")
    ic[3].metric("현재 상태", cur_state)
    st.caption(
        f"🧾 감사 — 등록 {str(row.created_at)[:16]} by **{row.created_by or '—'}** · "
        f"최종수정 {str(row.updated_at)[:16]} by **{row.updated_by or '—'}**"
    )
    with st.expander("🧾 감사 이력 (이 모듈의 전체 변경 로그)"):
        alog = q(
            "SELECT ts AS 시각, action AS 동작, actor AS 작업자, detail AS 내용 "
            "FROM module_audit WHERE module_id = ? ORDER BY id DESC", (mid,)
        )
        if alog.empty:
            st.write("기록 없음")
        else:
            st.dataframe(alog, width="stretch", hide_index=True)

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

# ================================ 4. 재고 현황 및 관리 ================================
elif page == "재고 현황 및 관리":
    st.title("✏️ 재고 현황 및 관리")
    st.caption(
        "모듈 재고를 한눈에 보고(기본 테이블), 추가·수정·삭제·벌크 업로드까지 한 화면에서. "
        "정책상 **막힌 행동**은 '🚫 정책·제약' 탭에서 글/시뮬레이션으로 확인할 수 있다."
    )
    vdf = q("SELECT id, name, code FROM vendor ORDER BY id")
    name2id = {r.name: int(r.id) for r in vdf.itertuples()}
    code2id = {r.code: int(r.id) for r in vdf.itertuples()}
    vid2label = {int(r.id): f"{r.name} ({r.code})" for r in vdf.itertuples()}
    vendor_help = " · ".join(f"{n}({c})" for n, c in zip(vdf.name, vdf.code))

    tab_view, tab_bulk, tab_one, tab_edit, tab_del, tab_fix, tab_audit, tab_pol = st.tabs(
        ["📊 재고 현황", "📥 벌크 업로드", "➕ 개별 추가", "✏️ 수정", "🗑️ 삭제",
         "🔧 수리·폐기", "🧾 감사 로그", "🚫 정책·제약"]
    )

    # ----- 재고 현황(기본 테이블 뷰) -----
    with tab_view:
        inv = q(
            """SELECT m.id, m.serial, m.module_type AS 종류, m.hardware_version AS hardware_version,
                      v.code AS 공급사, m.received_date AS 입고일, m.batch_id AS 배치,
                      m.status AS 상태, p.name AS 장착장비
               FROM modules m
               LEFT JOIN vendor v ON v.id = m.vendor_id
               LEFT JOIN module_placements op ON op.module_id = m.id AND op.valid_to IS NULL
               LEFT JOIN products p ON p.id = op.product_id
               ORDER BY m.serial"""
        )
        inv = inv.merge(q(USAGE_SQL)[["id", "pct_used"]], on="id", how="left")
        inv["설치"] = inv["장착장비"].notna()
        # 상태(condition) 와 위치(location) 는 직교한다 → 컬럼을 분리한다.
        #   상태 = modules.status 에 저장된 처분값 (serviceable/refurbished/faulty/scrapped)
        #   위치 = module_placements(열린 행)에서 파생 — 저장하지 않는다(단일 소스).
        inv["위치"] = inv.apply(lambda r: f"🔧 {r['장착장비']}" if r["설치"] else "📦 창고", axis=1)
        inv["사용률%"] = inv["pct_used"]

        installable = inv["상태"].isin(list(INSTALLABLE_STATUSES)) & ~inv["설치"]
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("총 모듈", len(inv))
        m2.metric("가용 재고", int(installable.sum()))
        m3.metric("장착 중", int(inv["설치"].sum()))
        m4.metric("고장(faulty)", int((inv["상태"] == "faulty").sum()))
        m5.metric("폐기(scrapped)", int((inv["상태"] == "scrapped").sum()))

        f1, f2, f3, f4 = st.columns(4)
        f_type = f1.multiselect("종류", list(MODULE_TYPES))
        f_status = f2.multiselect("상태", ["serviceable", "refurbished", "faulty", "scrapped"])
        f_loc = f3.multiselect("위치", ["창고", "장착"])
        f_q = f4.text_input("serial 검색")
        view = inv
        if f_type:
            view = view[view["종류"].isin(f_type)]
        if f_status:
            view = view[view["상태"].isin(f_status)]
        if f_loc and not ({"창고", "장착"} <= set(f_loc)):
            view = view[view["설치"]] if "장착" in f_loc else view[~view["설치"]]
        if f_q:
            view = view[view["serial"].str.contains(f_q, case=False, na=False)]
        st.caption(
            "**상태**=컨디션(condition) · **위치**=`module_placements` 파생(장착 여부·장비) · "
            "**hardware_version**=하드웨어 버전(기록용 — 장착/호환 판정엔 미반영)"
        )
        st.caption(f"{len(view)} / {len(inv)} 건")
        st.dataframe(
            view[["serial", "종류", "hardware_version", "공급사", "입고일", "배치", "상태", "위치", "사용률%"]]
            .style.map(_red_over_100, subset=["사용률%"]),
            width="stretch", hide_index=True,
        )

    # ----- 벌크 업로드 -----
    with tab_bulk:
        st.markdown(
            "CSV 컬럼: **serial · module_type · vendor**(이름 또는 코드) · *hardware_version*(선택) · "
            "*received_date*(선택, `YYYY-MM-DD`, 비우면 오늘)"
        )
        st.caption(f"허용 종류: {', '.join(MODULE_TYPES)}  |  등록 벤더: {vendor_help}")
        st.download_button(
            "CSV 템플릿",
            "serial,module_type,vendor,hardware_version,received_date\n"
            "SN-TG-9001,Top Griddle,ACM,1.2,2025-06-10\n",
            "module_upload_template.csv", "text/csv",
        )
        up = st.file_uploader("모듈 CSV 업로드", type=["csv"])
        if up is not None:
            raw = up.getvalue()
            fhash = hashlib.sha256(raw).hexdigest()
            dup = q("SELECT id FROM upload_batch WHERE file_hash = ?", (fhash,))
            if not dup.empty:
                st.error(f"이미 업로드된 파일입니다 (배치 #{int(dup.id[0])}). 중복 제출은 차단됩니다.")
            else:
                df = None
                try:
                    df = pd.read_csv(io.BytesIO(raw), dtype=str).fillna("")
                    df.columns = [c.strip().lower() for c in df.columns]
                except Exception as exc:  # noqa: BLE001
                    st.error(f"CSV 파싱 실패: {exc}")
                missing = {"serial", "module_type", "vendor"} - set(df.columns) if df is not None else set()
                if df is not None and missing:
                    st.error(f"필수 컬럼 누락: {', '.join(sorted(missing))}")
                elif df is not None:
                    existing = set(q("SELECT serial FROM modules").serial)
                    rows, errs, seen = [], [], set()
                    for i, r in df.iterrows():
                        serial = str(r.get("serial", "")).strip()
                        mtype = str(r.get("module_type", "")).strip()
                        vend = str(r.get("vendor", "")).strip()
                        hw = str(r.get("hardware_version", "")).strip()
                        rcv = str(r.get("received_date", "")).strip()
                        e = []
                        if not serial:
                            e.append("serial 비어있음")
                        elif serial in seen:
                            e.append("파일 내 serial 중복")
                        elif serial in existing:
                            e.append("이미 존재하는 serial (전역 유니크)")
                        if mtype not in MODULE_TYPES:
                            e.append(f"미등록 종류: {mtype or '(공백)'}")
                        vid = name2id.get(vend) or code2id.get(vend)
                        if vid is None:
                            e.append(f"미등록 벤더: {vend or '(공백)'}")
                        if rcv:
                            try:
                                rcv = pd.to_datetime(rcv).date().isoformat()
                            except Exception:  # noqa: BLE001
                                e.append(f"received_date 형식 오류: {rcv}")
                        else:
                            rcv = DEMO_NOW.isoformat()
                        if e:
                            errs.append({"행(헤더=1)": i + 2, "serial": serial, "오류": "; ".join(e)})
                        else:
                            seen.add(serial)
                            rows.append((serial, mtype, hw, vid, rcv))
                    st.write(f"총 {len(df)}행 · 정상 {len(rows)} · 오류 {len(errs)}")
                    if errs:
                        st.error("오류가 있어 **전체 업로드를 취소**했습니다(all-or-nothing). 파일 수정 후 다시 올리세요.")
                        st.dataframe(pd.DataFrame(errs), width="stretch")
                    elif not rows:
                        st.warning("처리할 행이 없습니다.")
                    else:
                        prev = pd.DataFrame(
                            rows, columns=["serial", "module_type", "hardware_version", "vendor_id", "received_date"]
                        )
                        prev["vendor"] = prev.vendor_id.map(vid2label)
                        st.dataframe(
                            prev[["serial", "module_type", "hardware_version", "vendor", "received_date"]],
                            width="stretch",
                        )
                        if st.button(f"{len(rows)}개 입고 등록", type="primary"):
                            bid = insert_modules(fhash, up.name, rows, actor)
                            st.success(f"배치 #{bid} 로 {len(rows)}개 모듈 입고 완료 (status=serviceable).")

    # ----- 개별 추가 -----
    with tab_one:
        c1, c2 = st.columns(2)
        one_serial = c1.text_input("serial (전역 유니크)")
        one_type = c2.selectbox("module_type", list(MODULE_TYPES))
        c3, c4, c5 = st.columns(3)
        one_vlabel = c3.selectbox("vendor", list(vid2label.values()))
        one_vid = next(k for k, v in vid2label.items() if v == one_vlabel)
        one_hw = c4.selectbox("hardware_version", [""] + HW_VERSIONS)
        one_rcv = c5.date_input("received_date", value=DEMO_NOW)
        if st.button("개별 입고 등록", type="primary"):
            s = one_serial.strip()
            if not s:
                st.error("serial 을 입력하세요.")
            elif not q("SELECT 1 FROM modules WHERE serial = ?", (s,)).empty:
                st.error(f"이미 존재하는 serial: {s}")
            else:
                bid = insert_modules(None, "개별 추가", [(s, one_type, one_hw, one_vid, one_rcv.isoformat())], actor)
                st.success(f"배치 #{bid} 로 {s} 입고 완료 (status=serviceable).")

    # ----- 수정 (인플레이스 정정) -----
    with tab_edit:
        st.caption(
            "업로드 실수 정정. vendor·입고일·hardware_version·serial 은 장착 중이어도 수정 가능, "
            "**종류(type)는 미장착일 때만** 수정 가능. 상태/장착은 여기서 못 바꾼다(장착/탈거는 '장비 정비')."
        )
        emods = q(
            "SELECT id, serial, module_type, hardware_version, vendor_id, received_date, status "
            "FROM modules ORDER BY serial"
        )
        einfo = emods.set_index("id")
        emid = int(st.selectbox(
            "모듈", emods.id, key="edit_mid",
            format_func=lambda i: f"{einfo.loc[i, 'serial']} ({einfo.loc[i, 'module_type']})",
        ))
        er = einfo.loc[emid]
        installed = is_installed(emid)
        if installed:
            st.info("현재 **장착 중** — 종류(type)는 수정 불가(현재 슬롯과 모순). 메타데이터만 정정.")
        c1, c2 = st.columns(2)
        new_serial = c1.text_input("serial", value=er.serial, key="edit_serial")
        if installed:
            c2.text_input("module_type (장착 중 — 잠김)", value=er.module_type, disabled=True)
            new_type = er.module_type
        else:
            new_type = c2.selectbox(
                "module_type", list(MODULE_TYPES),
                index=list(MODULE_TYPES).index(er.module_type), key="edit_type",
            )
        c3, c4, c5 = st.columns(3)
        cur_vlabel = vid2label.get(int(er.vendor_id), list(vid2label.values())[0])
        new_vlabel = c3.selectbox(
            "vendor", list(vid2label.values()),
            index=list(vid2label.values()).index(cur_vlabel), key="edit_vendor",
        )
        new_vid = next(k for k, v in vid2label.items() if v == new_vlabel)
        hw_opts = [""] + HW_VERSIONS
        cur_hw = er.hardware_version if er.hardware_version in hw_opts else ""
        new_hw = c4.selectbox("hardware_version", hw_opts, index=hw_opts.index(cur_hw), key="edit_hw")
        try:
            rcv_default = dt.date.fromisoformat(str(er.received_date)[:10])
        except Exception:  # noqa: BLE001
            rcv_default = DEMO_NOW
        new_rcv = c5.date_input("received_date", value=rcv_default, key="edit_rcv")
        if st.button("정정 적용", type="primary"):
            ns = new_serial.strip()
            if not ns:
                st.error("serial 은 비울 수 없습니다.")
            elif ns != er.serial and not q(
                "SELECT 1 FROM modules WHERE serial = ? AND id <> ?", (ns, emid)
            ).empty:
                st.error(f"이미 존재하는 serial: {ns}")
            else:
                chg = []
                if ns != er.serial:
                    chg.append(f"serial:{er.serial}→{ns}")
                if new_type != er.module_type:
                    chg.append(f"type:{er.module_type}→{new_type}")
                if (new_hw or "") != (er.hardware_version or ""):
                    chg.append(f"hw:{er.hardware_version}→{new_hw}")
                if new_vid != int(er.vendor_id):
                    chg.append(f"vendor:{int(er.vendor_id)}→{new_vid}")
                if new_rcv.isoformat() != str(er.received_date)[:10]:
                    chg.append(f"received:{str(er.received_date)[:10]}→{new_rcv.isoformat()}")
                write([
                    ("UPDATE modules SET serial=?, module_type=?, hardware_version=?, vendor_id=?, "
                     "received_date=?, rated_life=?, updated_at=?, updated_by=? WHERE id=?",
                     (ns, new_type, new_hw, new_vid, new_rcv.isoformat(), MODULE_TYPES[new_type],
                      now_iso(), actor, emid)),
                    audit_stmt(emid, ns, "update", "정정 " + (", ".join(chg) if chg else "(변경 없음)"), actor),
                ])
                st.success(f"정정 완료: {ns} ({new_type}).")

    # ----- 삭제 (미장착·미사용만) -----
    with tab_del:
        st.caption(
            "**장착되었거나 사용 이력이 있는 모듈은 목록에 없다 — 삭제 불가.** 삭제는 업로드 실수 "
            "정정용이며, 한 번이라도 장착/사용된 모듈은 이력 보존을 위해 status(scrapped 등)로만 관리한다."
        )
        dmods = q(
            "SELECT id, serial, module_type, status, received_date FROM modules "
            "WHERE id NOT IN (SELECT module_id FROM module_placements) ORDER BY serial"
        )
        if dmods.empty:
            st.info("삭제 가능한 모듈이 없습니다(모두 장착/사용 이력 있음).")
        else:
            dinfo = dmods.set_index("id")
            ddel = int(st.selectbox(
                "삭제할 모듈(미장착·미사용)", dmods.id, key="del_mid",
                format_func=lambda i: f"{dinfo.loc[i, 'serial']} ({dinfo.loc[i, 'module_type']}, {dinfo.loc[i, 'status']})",
            ))
            st.dataframe(dmods, width="stretch")
            confirm = st.checkbox("이 모듈을 영구 삭제합니다(되돌릴 수 없음).", key="del_confirm")
            if st.button("삭제", type="primary", disabled=not confirm):
                if has_placement_history(ddel):  # 버튼 시점 안전망
                    st.error("이 모듈은 장착/사용 이력이 생겨 삭제할 수 없습니다.")
                else:
                    write([
                        audit_stmt(ddel, dinfo.loc[ddel, "serial"], "delete", "업로드 실수 정정 삭제", actor),
                        ("DELETE FROM modules WHERE id = ?", (ddel,)),
                    ])
                    st.success(f"삭제 완료: {dinfo.loc[ddel, 'serial']}")

    # ----- 수리·폐기 (미장착 모듈의 컨디션 전이) -----
    with tab_fix:
        st.caption(
            "수리센터에서 회수된 **미장착** 모듈을 처리한다 — **수리 완료** 시 `refurbished`로 **재고 복귀**"
            "(다시 장착 가능), 수리 불가 시 **폐기** `scrapped`(종착). 장착 중 모듈은 여기서 못 다룬다"
            "(탈거는 '장비 정비'). 결과는 감사 로그에 남는다."
        )
        cand = q(
            """SELECT id, serial, module_type, status FROM modules
               WHERE status <> 'scrapped'
                 AND id NOT IN (SELECT module_id FROM module_placements WHERE valid_to IS NULL)
               ORDER BY (status = 'faulty') DESC, serial"""
        )
        n_faulty = int((cand["status"] == "faulty").sum()) if not cand.empty else 0
        st.caption(f"미장착 처리 대상 {len(cand)}개 (faulty {n_faulty}개)")
        if cand.empty:
            st.info("처리할 미장착 모듈이 없습니다.")
        else:
            cinfo = cand.set_index("id")
            fmid = int(st.selectbox(
                "모듈 (미장착)", cand.id, key="fix_mid",
                format_func=lambda i: f"{cinfo.loc[i, 'serial']} ({cinfo.loc[i, 'module_type']} · 상태={cinfo.loc[i, 'status']})",
            ))
            cur_status = cinfo.loc[fmid, "status"]
            act = st.radio("처리", ["수리 완료 (→ refurbished, 재고 복귀)", "폐기 (→ scrapped)"], key="fix_act")
            repair = act.startswith("수리")
            if repair and cur_status != "faulty":
                st.warning(f"수리 완료는 faulty 모듈에만 적용됩니다(현재: {cur_status}). 폐기만 가능합니다.")
            if st.button("처리 적용", type="primary"):
                serial = cinfo.loc[fmid, "serial"]
                if repair and cur_status != "faulty":
                    st.error("수리 완료는 faulty 모듈만 가능합니다.")
                else:
                    new_status = "refurbished" if repair else "scrapped"
                    detail = (f"수리 완료: {cur_status}→{new_status} (재고 복귀)" if repair
                              else f"폐기: {cur_status}→{new_status}")
                    write([
                        ("UPDATE modules SET status=?, updated_at=?, updated_by=? WHERE id=?",
                         (new_status, now_iso(), actor, fmid)),
                        audit_stmt(fmid, serial, "update", detail, actor),
                    ])
                    if repair:
                        st.success(f"{serial} 수리 완료 → refurbished. 재고로 복귀(장착 가능).")
                    else:
                        st.success(f"{serial} 폐기 → scrapped (종착, 재장착 불가).")

    # ----- 감사 로그 (append-only) -----
    with tab_audit:
        st.caption(
            "modules 변경 이력(append-only). insert·update·**delete**(삭제된 모듈도) 가 한 줄씩 남는다 "
            "— 행 컬럼(created/updated)이 못 잡는 삭제·중간 이력을 여기서 추적."
        )
        log = q(
            "SELECT ts AS 시각, action AS 동작, serial, module_id, actor AS 작업자, detail AS 내용 "
            "FROM module_audit ORDER BY id DESC"
        )
        a1, a2 = st.columns([1, 2])
        f_act = a1.multiselect("동작", ["insert", "update", "delete"])
        f_q = a2.text_input("serial / 작업자 검색", key="audit_q")
        v = log
        if f_act:
            v = v[v["동작"].isin(f_act)]
        if f_q:
            v = v[v["serial"].str.contains(f_q, case=False, na=False)
                  | v["작업자"].str.contains(f_q, case=False, na=False)]
        st.caption(f"{len(v)} / {len(log)} 건")
        st.dataframe(v, width="stretch", hide_index=True)

    # ----- 정책·제약 (글 + 시뮬레이터) -----
    with tab_pol:
        st.markdown("#### 정책적으로 막힌 행동 (이 화면에서 강제)")
        st.table(pd.DataFrame([
            {"행동": "장착·사용된 모듈 삭제", "결과": "❌ 차단", "사유": "이력 보존 — 폐기는 status=scrapped 로만"},
            {"행동": "이미 있는 serial 추가/정정", "결과": "❌ 차단", "사유": "serial 전역 유니크"},
            {"행동": "미등록 벤더로 입고", "결과": "❌ 차단", "사유": "vendor 외부 마스터에 없는 값"},
            {"행동": "벌크 동일 파일 재업로드", "결과": "❌ 차단", "사유": "upload_batch.file_hash 중복"},
            {"행동": "벌크 일부 행 오류", "결과": "❌ 전체 취소", "사유": "all-or-nothing (거부행 리포트)"},
            {"행동": "장착 중 모듈의 종류(type) 변경", "결과": "❌ 차단", "사유": "현재 슬롯과 모순"},
            {"행동": "임의 status 직접 편집(수정 탭)", "결과": "❌ 불가", "사유": "단일 소스 — 정해진 전이만 허용"},
            {"행동": "장착 중 모듈 수리/폐기", "결과": "❌ 차단", "사유": "미장착(회수)된 모듈만 처리"},
        ]))

        st.markdown("#### 🧪 시뮬레이터 — 막힌 행동을 직접 시도 (검증만, **DB 기록 안 함**)")
        # 모듈 선택 라벨에 현재 위치(🔧 장착·장비 / 📦 미장착·상태)를 같이 보여준다 — 장착/미장착을
        # 헷갈리지 않도록(예: 폐기된 SN-BG-0002 는 미장착이라 종류 변경이 허용된다).
        simm = q(
            """SELECT m.id, m.serial, m.module_type, m.status, p.name AS dev
               FROM modules m
               LEFT JOIN module_placements op ON op.module_id = m.id AND op.valid_to IS NULL
               LEFT JOIN products p ON p.id = op.product_id ORDER BY m.serial"""
        )
        sinfo = simm.set_index("id")

        def _simlabel(i):
            r = sinfo.loc[i]
            loc = f"🔧 장착·{r['dev']}" if pd.notna(r["dev"]) else f"📦 미장착·{r['status']}"
            return f"{r['serial']} · {r['module_type']} · {loc}"

        scenario = st.radio("시나리오", [
            "장착·사용된 모듈 삭제",
            "이미 있는 serial 로 추가",
            "미등록 벤더로 입고",
            "장착 중 모듈의 종류(type) 변경",
        ], key="sim_scenario")

        if scenario == "장착·사용된 모듈 삭제":
            sid = int(st.selectbox("모듈", simm.id, key="sim_del", format_func=_simlabel))
            if st.button("삭제 시도", key="sim_del_btn"):
                if has_placement_history(sid):
                    st.error(f"❌ 차단: {sinfo.loc[sid, 'serial']} 는 장착/사용 이력이 있어 삭제 불가 (status 로만 관리).")
                else:
                    st.success(f"✅ 허용: {sinfo.loc[sid, 'serial']} 는 미장착·미사용 → 삭제 가능.")

        elif scenario == "이미 있는 serial 로 추가":
            ex = q("SELECT serial FROM modules ORDER BY serial LIMIT 1").serial[0]
            s = st.text_input("추가할 serial", value=ex, key="sim_serial")
            if st.button("추가 시도", key="sim_serial_btn"):
                if not q("SELECT 1 FROM modules WHERE serial = ?", (s.strip(),)).empty:
                    st.error(f"❌ 차단: '{s}' 는 이미 존재 (serial 전역 유니크).")
                else:
                    st.success(f"✅ 허용: '{s}' 는 신규 serial → 추가 가능.")

        elif scenario == "미등록 벤더로 입고":
            v = st.text_input("벤더(이름 또는 코드)", value="XYZ", key="sim_vendor")
            if st.button("입고 시도", key="sim_vendor_btn"):
                if (name2id.get(v.strip()) or code2id.get(v.strip())) is None:
                    st.error(f"❌ 차단: '{v}' 는 미등록 벤더. 등록 벤더: {vendor_help}")
                else:
                    st.success(f"✅ 허용: '{v}' 는 등록 벤더.")

        else:  # 장착 중 모듈의 종류(type) 변경
            sid = int(st.selectbox("모듈", simm.id, key="sim_type", format_func=_simlabel))
            if st.button("종류 변경 시도", key="sim_type_btn"):
                if is_installed(sid):
                    st.error(f"❌ 차단: {sinfo.loc[sid, 'serial']} 는 장착 중 → 종류(type) 변경 불가 (먼저 탈거 필요).")
                else:
                    st.success(f"✅ 허용: {sinfo.loc[sid, 'serial']} 는 미장착 → 종류 변경 가능.")

# ================================ 제품 조립 ================================
elif page == "제품 조립":
    st.title("✏️ 제품 조립 — 신규 제품에 재고 모듈 매핑")
    st.caption(
        "공급된 재고 모듈을 모아 **새 제품(장비)을 조립**한다. 모델의 **모든 슬롯을 채워야** 완성되며, "
        "조립된 제품은 **출하 대기(창고)** 상태로 생성된다(고객 인도는 별도 단계). "
        "장착은 placement 로만 기록(재고 status 불변), 장착된 모듈은 재고에서 빠진다."
    )
    c1, c2, c3 = st.columns(3)
    a_name = c1.text_input("제품명", value="신규호기")
    a_serial = c2.text_input("제품 serial", placeholder="예: R-AG-00200")
    a_model = c3.selectbox("model", list(MODEL_SLOTS))
    a_date = st.date_input("조립일", value=DEMO_NOW)

    st.subheader(f"모듈 구성 (model={a_model}) — 모든 슬롯 필수")
    slot_choice: dict[str, int] = {}
    all_ok = True
    for slot in slots_for(a_model):
        inv = free_inventory(slot)
        cc = st.columns([2, 5])
        cc[0].write(f"🔩 **{slot}**")
        if inv.empty:
            cc[1].warning(f"가용 재고 없음 — '재고 현황 및 관리'에서 입고 후 조립 가능")
            all_ok = False
        else:
            iset = inv.set_index("id")
            with cc[1]:
                mid = st.selectbox(
                    f"{slot} 모듈", inv.id, key=f"asm_{slot}", label_visibility="collapsed",
                    format_func=lambda i, s=iset: f"{s.loc[i, 'serial']} ({s.loc[i, 'status']})",
                )
            slot_choice[slot] = int(mid)

    s = a_serial.strip()
    serial_dup = bool(s) and not q("SELECT 1 FROM products WHERE serial = ?", (s,)).empty
    if serial_dup:
        st.error(f"이미 존재하는 제품 serial: {s}")
    if not all_ok:
        st.info("재고가 없는 슬롯이 있어 아직 조립할 수 없습니다.")
    ready = all_ok and bool(s) and not serial_dup
    if st.button("조립 완료", type="primary", disabled=not ready):
        pid = assemble_product(a_name.strip() or "신규호기", s, a_model, slot_choice, a_date)
        st.success(
            f"제품 #{pid} ({s}, {a_model}) 조립 완료 — {len(slot_choice)}개 모듈 장착, 출하 대기(창고) 상태."
        )
        nav_button(f"🍳 방금 조립한 {s} 보기", "장비 상세", key=f"asm{pid}",
                   ctx="assemble", sel_product=int(pid), help="장비 상세로 이동")

# ================================ 5. 장비 정비 ================================
elif page == "장비 정비":
    st.title("✏️ 장비 정비 — 기존 장비 모듈 교체·탈거·장착")
    st.caption(
        "실제 현장 작업자가 모듈을 다루듯 입력해 본다. **어떤 값이 허용되는지**와 "
        "**입력이 어떻게 반영되는지**를 즉시 확인할 수 있다. (실제 데모 DB 에 기록되며, 사이드바 재시드로 복구)"
    )
    with st.expander("허용 규칙 (애플리케이션 레이어 검증, §2.4)"):
        st.markdown(
            "- 한 장비의 각 **모듈 종류 자리**에는 그 종류의 모듈만 장착할 수 있다.\n"
            "- 한 자리에는 동시에 **하나의 모듈만** 장착된다 (이미 장착돼 있으면 '교체' 사용).\n"
            "- 한 모듈은 동시에 **한 곳에만** 존재한다 → 다른 곳에 장착된 모듈은 선택 불가(재고만 선택 가능).\n"
            "- 장착 가능 컨디션(**serviceable / refurbished**) 재고만 선택 가능 — scrapped/faulty 제외.\n"
            "- 교체품은 그 **모듈 종류(슬롯)에 맞는** 재고만 선택 가능. (빈 자리 채우기·신규 조립은 '제품 조립')"
        )

    prods = q("SELECT id, name, serial, model FROM products")
    pinfo = prods.set_index("id")
    pid = int(st.selectbox("장비", prods.id, format_func=lambda i: f"{pinfo.loc[i, 'name']} ({pinfo.loc[i, 'serial']})"))
    model = pinfo.loc[pid, "model"]
    filled = current_slots(pid)
    empty_slots = [s for s in slots_for(model) if s not in filled]

    eff_date = st.date_input("적용일", value=DEMO_NOW)
    eff = dt.datetime.combine(eff_date, dt.time(12, 0, 0)).isoformat()

    # 작업 선택 — '빈 슬롯 장착'은 이 장비에 빈 슬롯이 있을 때만(예외적) 노출한다.
    acts = ["교체 (탈거 + 장착)", "탈거"] + (["빈 슬롯 장착"] if empty_slots else [])
    action = st.radio("작업", acts, horizontal=True)
    if empty_slots:
        st.caption(f"⚠️ 빈 슬롯: {', '.join(empty_slots)} — 예외적으로 '빈 슬롯 장착' 가능")

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
                    ("UPDATE modules SET status=?, updated_at=?, updated_by=? WHERE id=?", (new_status, now_iso(), actor, old_mid)),
                    audit_stmt(old_mid, old_serial, "update", f"교체 탈거({reason}) → status={new_status}", actor),
                    ("INSERT INTO module_placements(module_id,product_id,position_code,valid_from,valid_to,removed_reason,fault_mode) "
                     "VALUES (?,?,?,?,NULL,NULL,NULL)", (new_mid, pid, slot, eff)),
                ])
                st.success(
                    f"반영됨: {old_serial} 탈거({REMOVE_REASONS[reason][0]}) → 상태 '{new_status}', "
                    f"{slot} 자리에 {inv.set_index('id').loc[new_mid, 'serial']} 신규 장착."
                )

    # --- 빈 슬롯 장착 (예외: 빈 슬롯이 있을 때만 노출) ---
    elif action.startswith("빈"):
        slot = st.selectbox("빈 자리 (모듈 종류)", empty_slots)
        inv = free_inventory(slot)
        st.caption(f"이 자리에는 **{slot}** 종류 재고만 장착 가능. 사용 가능 재고: {len(inv)}개")
        if inv.empty:
            st.warning(f"장착 가능한 {slot} 재고가 없습니다.")
        else:
            iset = inv.set_index("id")
            new_mid = int(st.selectbox(
                "장착할 모듈(재고)", inv.id,
                format_func=lambda i, s=iset: f"{s.loc[i, 'serial']} ({s.loc[i, 'status']})",
            ))
            if st.button("장착 적용", type="primary"):
                write([
                    ("INSERT INTO module_placements(module_id,product_id,position_code,valid_from,valid_to,removed_reason,fault_mode) "
                     "VALUES (?,?,?,?,NULL,NULL,NULL)", (new_mid, pid, slot, eff)),
                ])
                st.success(f"반영됨: {slot} 빈 자리에 {iset.loc[new_mid, 'serial']} 장착.")

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
                ("UPDATE modules SET status=?, updated_at=?, updated_by=? WHERE id=?", (new_status, now_iso(), actor, old_mid)),
                audit_stmt(old_mid, old_serial, "update", f"탈거({reason}) → status={new_status}", actor),
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

    st.subheader("컬럼 설명")
    st.caption("컬럼별 설명. 다이어그램에서 컬럼 행에 마우스를 올리면 같은 내용이 **툴팁**으로도 뜬다.")
    from erd import DESCRIPTIONS as ERD_DESC, TABLES as ERD_TABLES

    _desc_rows = [
        {"테이블": tname, "컬럼": col, "설명": ERD_DESC.get(tname, {}).get(col, "")}
        for tname, t in ERD_TABLES.items() for _, col in t["cols"]
    ]
    _sel = st.multiselect("테이블 필터", list(ERD_TABLES), key="erd_desc_filter")
    _ddf = pd.DataFrame(_desc_rows)
    if _sel:
        _ddf = _ddf[_ddf["테이블"].isin(_sel)]
    st.dataframe(_ddf, width="stretch", hide_index=True)
