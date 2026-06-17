"""
devicehub 모듈 관리 — ERD SVG 렌더러.

DB 툴(DataGrip 등)의 일반적인 ERD 모습을 흉내 낸다:
- 다크 테마 카드, 헤더(테이블 아이콘 + 이름), 컬럼 행마다 타입 아이콘(123 / A-Z / 시계 / PK 배지)
- 컬럼↔컬럼 관계선에 ○(소스) ── ◇(타깃) 표기

색 구분: 🟩 신규 테이블(초록 테두리) / 🟦 기존 재사용(파랑 테두리).
app.py 가 erd_html() 을 components.html 로 임베드한다.
"""
from __future__ import annotations

CARD_W = 280
HEADER_H = 44
ROW_H = 30

CAT_COLOR = {"new": "#3fb950", "existing": "#4a86d8"}
BODY_BG = "#1b1f27"
HEADER_BG = "#0d1117"
PANEL_BG = "#13161b"
ICON = "#6f9fd8"
TXT = "#c9d1d9"
PK_TXT = "#ffffff"
LINE = "#7d8893"

# 테이블 정의: name -> (category, x, y, [(type, column)])  type: pk|num|text|ts
TABLES: dict[str, dict] = {
    "modules": dict(cat="new", x=60, y=80, cols=[
        ("pk", "id"), ("text", "module_serial"), ("text", "module_type"),
        ("text", "hardware_version"), ("num", "vendor_id"), ("ts", "received_date"),
        ("num", "batch_id"), ("text", "status"), ("num", "rated_life"),
        ("ts", "created_at"), ("text", "created_by"), ("ts", "updated_at"), ("text", "updated_by")]),
    "vendor": dict(cat="new", x=60, y=560, cols=[
        ("pk", "id"), ("text", "name"), ("text", "code")]),
    "upload_batch": dict(cat="new", x=60, y=730, cols=[
        ("pk", "id"), ("text", "file_hash"), ("text", "source_name"),
        ("ts", "uploaded_at"), ("num", "row_count")]),
    "module_audit": dict(cat="new", x=440, y=400, cols=[
        ("pk", "id"), ("num", "module_id"), ("text", "module_serial"),
        ("text", "action"), ("text", "detail"), ("text", "actor"), ("ts", "ts")]),
    "module_placements": dict(cat="new", x=440, y=80, cols=[
        ("pk", "id"), ("num", "module_id"), ("num", "product_id"),
        ("text", "position_code"), ("ts", "valid_from"), ("ts", "valid_to"),
        ("text", "removed_reason"), ("text", "fault_mode")]),
    "products": dict(cat="existing", x=820, y=120, cols=[
        ("pk", "id"), ("text", "serial"), ("text", "name"), ("text", "model"), ("num", "kitchen_id")]),
    "kitchen": dict(cat="existing", x=820, y=470, cols=[
        ("pk", "id"), ("text", "name"), ("num", "brand_id")]),
    "cook_order": dict(cat="existing", x=440, y=700, cols=[
        ("pk", "id"), ("num", "product_id"), ("num", "kitchen_id"),
        ("num", "recipe_id"), ("ts", "started_at"), ("ts", "ended_at")]),
}

# 컬럼 설명 — 다이어그램 hover 툴팁 + ERD 페이지 '컬럼 설명' 표에서 쓰인다.
DESCRIPTIONS: dict[str, dict[str, str]] = {
    "modules": {
        "id": "내부 PK(서러게이트)",
        "module_serial": "모듈 시리얼번호(module_serial) — 전역 유니크",
        "module_type": "모듈 종류(=슬롯 위치)",
        "hardware_version": "하드웨어 버전(기록용, 예: 1.2/1.3) — 장착 판정엔 미반영",
        "vendor_id": "공급사 FK → vendor.id",
        "received_date": "입고일",
        "batch_id": "입고 배치 FK → upload_batch.id",
        "status": "컨디션: serviceable/refurbished/faulty/scrapped (장착 여부는 placement로 판정)",
        "rated_life": "정격수명(총 조리 횟수 기준)",
        "created_at": "등록 시각(감사)",
        "created_by": "등록 작업자(감사)",
        "updated_at": "최종 수정 시각(감사)",
        "updated_by": "최종 수정 작업자(감사)",
    },
    "vendor": {
        "id": "PK",
        "name": "공급사명 — 유니크",
        "code": "공급사 코드 — 유니크",
    },
    "upload_batch": {
        "id": "PK",
        "file_hash": "업로드 파일 해시 — 유니크(동일 파일 중복 제출 차단)",
        "source_name": "출처(파일명 / '개별 추가' / '초기 시드')",
        "uploaded_at": "업로드 시각",
        "row_count": "이 배치로 등록된 건수",
    },
    "module_audit": {
        "id": "PK(자동증가)",
        "module_id": "대상 모듈 id — FK 아님(삭제돼도 감사행 보존)",
        "module_serial": "당시 모듈 시리얼 스냅샷",
        "action": "insert / update / delete",
        "detail": "변경 내용 요약",
        "actor": "작업자",
        "ts": "발생 시각",
    },
    "module_placements": {
        "id": "PK",
        "module_id": "모듈 FK → modules.id",
        "product_id": "장비 FK → products.id",
        "position_code": "슬롯(=모듈 종류)",
        "valid_from": "장착 시각",
        "valid_to": "탈거 시각 — NULL이면 현재 장착 중",
        "removed_reason": "탈거 사유: fault/preventive/refurb/scrap",
        "fault_mode": "고장 모드(고장 탈거 시)",
    },
    "products": {
        "id": "PK",
        "serial": "장비 시리얼",
        "name": "장비명(예: 1호기)",
        "model": "모델(single/dual)",
        "kitchen_id": "현재 매장 FK → kitchen.id (NULL=출하대기/창고)",
    },
    "kitchen": {
        "id": "PK",
        "name": "매장(고객사)명",
        "brand_id": "브랜드 id",
    },
    "cook_order": {
        "id": "PK(자동증가)",
        "product_id": "장비 FK → products.id",
        "kitchen_id": "조리 발생 매장 FK → kitchen.id",
        "recipe_id": "레시피 id",
        "started_at": "조리 시작 시각",
        "ended_at": "조리 종료 시각",
    },
}

# 관계: (src_table, src_col, src_side, dst_table, dst_col, dst_side)
RELS = [
    ("module_placements", "module_id", "left", "modules", "id", "right"),
    ("module_placements", "product_id", "right", "products", "id", "left"),
    ("modules", "vendor_id", "left", "vendor", "id", "left"),
    ("modules", "batch_id", "left", "upload_batch", "id", "left"),
    ("module_audit", "module_id", "left", "modules", "id", "right"),
    ("products", "kitchen_id", "left", "kitchen", "id", "left"),
    ("cook_order", "product_id", "right", "products", "id", "left"),
    ("cook_order", "kitchen_id", "right", "kitchen", "id", "left"),
]

CANVAS_W = 1160
CANVAS_H = 960


def _card_h(t: dict) -> int:
    return HEADER_H + ROW_H * len(t["cols"])


def _col_y(t: dict, col: str) -> float:
    idx = [c for _, c in t["cols"]].index(col)
    return t["y"] + HEADER_H + idx * ROW_H + ROW_H / 2


def _edge_x(t: dict, side: str) -> int:
    return t["x"] + (CARD_W if side == "right" else 0)


def _type_icon(kind: str, cx: float, cy: float) -> str:
    if kind == "num":
        return f'<text x="{cx}" y="{cy + 3.5}" fill="{ICON}" font-size="9" font-family="monospace" text-anchor="middle">123</text>'
    if kind == "text":
        return f'<text x="{cx}" y="{cy + 3.5}" fill="{ICON}" font-size="9" font-family="monospace" text-anchor="middle">A-Z</text>'
    if kind == "ts":
        return (f'<circle cx="{cx}" cy="{cy}" r="6" fill="none" stroke="{ICON}" stroke-width="1.2"/>'
                f'<line x1="{cx}" y1="{cy}" x2="{cx}" y2="{cy - 3.5}" stroke="{ICON}" stroke-width="1.2"/>'
                f'<line x1="{cx}" y1="{cy}" x2="{cx + 3}" y2="{cy}" stroke="{ICON}" stroke-width="1.2"/>')
    # pk: id 배지
    return (f'<rect x="{cx - 8}" y="{cy - 6}" width="16" height="12" rx="3" fill="none" stroke="{ICON}" stroke-width="1.2"/>'
            f'<circle cx="{cx - 3.5}" cy="{cy - 1}" r="2" fill="{ICON}"/>'
            f'<line x1="{cx + 1}" y1="{cy - 2.5}" x2="{cx + 5}" y2="{cy - 2.5}" stroke="{ICON}" stroke-width="1.1"/>'
            f'<line x1="{cx + 1}" y1="{cy + 0.5}" x2="{cx + 5}" y2="{cy + 0.5}" stroke="{ICON}" stroke-width="1.1"/>'
            f'<line x1="{cx - 6}" y1="{cy + 3.5}" x2="{cx + 6}" y2="{cy + 3.5}" stroke="{ICON}" stroke-width="1.1"/>')


def _table_icon(cx: float, cy: float, color: str) -> str:
    return (f'<rect x="{cx - 8}" y="{cy - 6}" width="16" height="13" rx="2" fill="none" stroke="{color}" stroke-width="1.4"/>'
            f'<line x1="{cx - 8}" y1="{cy - 1.5}" x2="{cx + 8}" y2="{cy - 1.5}" stroke="{color}" stroke-width="1.4"/>'
            f'<line x1="{cx - 2}" y1="{cy - 1.5}" x2="{cx - 2}" y2="{cy + 7}" stroke="{color}" stroke-width="1.4"/>')


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _card(name: str, t: dict) -> str:
    x, y = t["x"], t["y"]
    h = _card_h(t)
    color = CAT_COLOR[t["cat"]]
    cid = f"clip_{name}"
    tag = "신규" if t["cat"] == "new" else "기존"
    parts = [
        f'<clipPath id="{cid}"><rect x="{x}" y="{y}" width="{CARD_W}" height="{h}" rx="7"/></clipPath>',
        f'<g clip-path="url(#{cid})">',
        f'<rect x="{x}" y="{y}" width="{CARD_W}" height="{h}" fill="{BODY_BG}"/>',
        f'<rect x="{x}" y="{y}" width="{CARD_W}" height="{HEADER_H}" fill="{HEADER_BG}"/>',
        '</g>',
        # header
        _table_icon(x + 22, y + HEADER_H / 2, color),
        f'<text x="{x + 40}" y="{y + HEADER_H / 2 + 5}" fill="{PK_TXT}" font-size="14" font-weight="700" '
        f'font-family="-apple-system, Helvetica, Arial, sans-serif">{_esc(name)}</text>',
        f'<text x="{x + CARD_W - 14}" y="{y + HEADER_H / 2 + 4}" fill="{color}" font-size="10" '
        f'font-weight="600" text-anchor="end" font-family="-apple-system, Helvetica, Arial, sans-serif">{tag}</text>',
        f'<line x1="{x}" y1="{y + HEADER_H}" x2="{x + CARD_W}" y2="{y + HEADER_H}" stroke="{color}" stroke-width="1" opacity="0.5"/>',
    ]
    for i, (kind, col) in enumerate(t["cols"]):
        ry = y + HEADER_H + i * ROW_H + ROW_H / 2
        is_pk = kind == "pk"
        parts.append(_type_icon(kind, x + 22, ry))
        parts.append(
            f'<text x="{x + 40}" y="{ry + 4.5}" fill="{PK_TXT if is_pk else TXT}" '
            f'font-size="13" font-weight="{700 if is_pk else 400}" '
            f'font-family="-apple-system, Helvetica, Arial, sans-serif">{_esc(col)}</text>'
        )
        desc = DESCRIPTIONS.get(name, {}).get(col, "")
        if desc:
            rtop = y + HEADER_H + i * ROW_H
            parts.append(
                f'<rect x="{x}" y="{rtop}" width="{CARD_W}" height="{ROW_H}" fill="transparent">'
                f'<title>{_esc(col)} — {_esc(desc)}</title></rect>'
            )
    # card border on top
    parts.append(f'<rect x="{x}" y="{y}" width="{CARD_W}" height="{h}" rx="7" fill="none" stroke="{color}" stroke-width="1.6"/>')
    return "".join(parts)


def _rel(src, scol, sside, dst, dcol, dside) -> str:
    st, dt = TABLES[src], TABLES[dst]
    sx, sy = _edge_x(st, sside), _col_y(st, scol)
    dx, dy = _edge_x(dt, dside), _col_y(dt, dcol)
    off = max(50, abs(dx - sx) * 0.5)
    c1x = sx + off if sside == "right" else sx - off
    c2x = dx + off if dside == "right" else dx - off
    path = f'<path d="M {sx} {sy} C {c1x} {sy}, {c2x} {dy}, {dx} {dy}" fill="none" stroke="{LINE}" stroke-width="1.5" stroke-dasharray="2 3"/>'
    src_marker = f'<circle cx="{sx}" cy="{sy}" r="4" fill="none" stroke="{LINE}" stroke-width="1.5"/>'
    dpts = f'{dx - 7},{dy} {dx},{dy - 5} {dx + 7},{dy} {dx},{dy + 5}'
    dst_marker = f'<polygon points="{dpts}" fill="{PANEL_BG}" stroke="{LINE}" stroke-width="1.5"/>'
    return path + src_marker + dst_marker


def erd_svg() -> str:
    rels = "".join(_rel(*r) for r in RELS)
    cards = "".join(_card(n, t) for n, t in TABLES.items())
    # 범례
    legend = (
        f'<rect x="40" y="20" width="14" height="14" rx="3" fill="none" stroke="{CAT_COLOR["new"]}" stroke-width="2"/>'
        f'<text x="62" y="32" fill="{TXT}" font-size="13" font-family="-apple-system, Helvetica, Arial, sans-serif">신규 테이블</text>'
        f'<rect x="170" y="20" width="14" height="14" rx="3" fill="none" stroke="{CAT_COLOR["existing"]}" stroke-width="2"/>'
        f'<text x="192" y="32" fill="{TXT}" font-size="13" font-family="-apple-system, Helvetica, Arial, sans-serif">기존 테이블 (재사용)</text>'
        f'<text x="350" y="32" fill="{LINE}" font-size="12" font-family="-apple-system, Helvetica, Arial, sans-serif">'
        f'devices · product_history 는 건드리지 않음(미표시)</text>'
    )
    return (
        f'<svg viewBox="0 0 {CANVAS_W} {CANVAS_H}" width="100%" xmlns="http://www.w3.org/2000/svg" '
        f'font-family="-apple-system, Helvetica, Arial, sans-serif">'
        f'<rect x="0" y="0" width="{CANVAS_W}" height="{CANVAS_H}" fill="{PANEL_BG}" rx="10"/>'
        f'{legend}{rels}{cards}</svg>'
    )


def erd_html() -> str:
    return (
        f'<div style="background:{PANEL_BG};border-radius:10px;overflow:auto;">{erd_svg()}</div>'
    )


ERD_HEIGHT = 840
