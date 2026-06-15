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
    "modules": dict(cat="new", x=60, y=120, cols=[
        ("pk", "id"), ("text", "serial"), ("text", "module_type"),
        ("text", "status"), ("num", "rated_life")]),
    "module_placements": dict(cat="new", x=440, y=70, cols=[
        ("pk", "id"), ("num", "module_id"), ("num", "product_id"),
        ("text", "position_code"), ("ts", "valid_from"), ("ts", "valid_to"),
        ("text", "removed_reason"), ("text", "fault_mode")]),
    "products": dict(cat="existing", x=820, y=140, cols=[
        ("pk", "id"), ("text", "serial"), ("text", "name"), ("text", "model"), ("num", "kitchen_id")]),
    "kitchen": dict(cat="existing", x=820, y=480, cols=[
        ("pk", "id"), ("text", "name"), ("num", "brand_id")]),
    "cook_order": dict(cat="existing", x=440, y=480, cols=[
        ("pk", "id"), ("num", "product_id"), ("num", "kitchen_id"),
        ("num", "recipe_id"), ("ts", "started_at"), ("ts", "ended_at")]),
}

# 관계: (src_table, src_col, src_side, dst_table, dst_col, dst_side)
RELS = [
    ("module_placements", "module_id", "left", "modules", "id", "right"),
    ("module_placements", "product_id", "right", "products", "id", "left"),
    ("products", "kitchen_id", "left", "kitchen", "id", "left"),
    ("cook_order", "product_id", "right", "products", "id", "left"),
    ("cook_order", "kitchen_id", "right", "kitchen", "id", "left"),
]

CANVAS_W = 1160
CANVAS_H = 740


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


ERD_HEIGHT = 660
