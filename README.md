# 모듈(부품) 단위 관리 데모

여러 모듈로 구성된 IoT 조리 장비에서 **모듈 단위로 사용량을 집계하고 교체·이력을 관리**하는
기능의 Streamlit 데모. 가상 데이터를 채운 SQLite 를 읽고/쓰며 5개 화면으로 보여준다.

이 폴더는 **자족형 배포 번들**이다 — 외부 의존(사내 monorepo `shared/` 등) 없이 단독으로 돈다.
데모 DB 는 **최초 실행 시 자동 생성**되므로 커밋할 필요가 없다.

## 화면 (📄 조회 5 · ✏️ 입력/수정 3)

1. 📄 매장 상세 — 매장의 현재/과거 장비 + 각 장비의 현재 모듈 구성
2. 📄 장비 상세 — 현재 위치(매장/창고) + 현재 모듈 구성 + 설치/교체 이력
3. 📄 모듈 상세 — 공급사·hardware_version·입고일·현재상태 + 누적 사용량 + 고객사별 분해 + 장착 이력
4. ✏️ 재고 현황 및 관리 — 재고 테이블 + 추가/수정/삭제/벌크 업로드 + **수리·폐기**(faulty→재고복귀/폐기) + 감사 로그 + 정책 제약
5. ✏️ 제품 조립 — 신규 제품 생성 + 모든 슬롯에 재고 모듈 매핑(출하 대기로 생성)
6. ✏️ 장비 정비 — 기존 장비의 모듈 교체·탈거(+빈 슬롯 장착, 빈 슬롯 있을 때만)
7. 📄 모듈 Fleet 개요 — 전체 수명 소진 현황
8. 📄 ERD — 관계 테이블 다이어그램

### 재고 모델(요약)
- `modules` = 시리얼 단위 재고 마스터(1행=물리 모듈 1개). 시리얼 **전역 유니크**.
- **장착 여부(위치)는 `module_placements`(열린 행)로만 판정**(단일 소스). `status`는 **컨디션만** 표현:
  `{serviceable, refurbished, faulty, scrapped}`. 가용재고 = 위치=창고 ∧ status ∈ {serviceable, refurbished}.
- 벤더는 `vendor`(외부 마스터 대역)에서만 선택/검증. 미등록 벤더 업로드는 거부.
- 벌크 업로드는 파일 해시(`upload_batch.file_hash` UNIQUE)로 중복 제출 차단 + all-or-nothing 검증.
- 삭제는 **미장착·미사용 모듈만**(업로드 실수 정정용). 수명 도과 폐기는 삭제가 아니라 `scrapped`.
- **재고 활용**: `제품 조립`(신규 제품 생성+매핑), `장비 정비`(기존 장비 교체/탈거/빈슬롯 장착), `수리·폐기`(회수된
  faulty 모듈을 `refurbished` 재고 복귀 또는 `scrapped` 폐기) — 조립·수리 루프가 재고/감사에 일관 반영.

매장 → 장비 → 모듈 사이는 화면 안의 버튼으로 상호 이동한다(메뉴 간 연동). 장비↔매장 이동
이력은 전용 테이블 대신 `cook_order` 에서 파생한다(실제 시스템의 `product_history` 대역).

## 로컬 실행

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py      # DB 없으면 최초 실행 시 자동 생성
```

## Streamlit Community Cloud 배포

1. 이 폴더(`deploy/`)의 내용을 **새 public GitHub repo** 의 루트로 올린다.
2. [share.streamlit.io](https://share.streamlit.io) → **New app** → repo·branch 선택
   → **Main file path = `streamlit_app.py`** → Deploy.

`requirements.txt` 는 streamlit·pandas 만 필요로 한다.

## 알아둘 점 — 클라우드 파일시스템

- 입력/수정 화면(재고 관리·제품 조립·장비 정비)과 '재시드' 버튼은 SQLite 파일에 **쓰기**를 한다.
- Streamlit Cloud 의 파일시스템은 **휘발성·공동**이다: 한 컨테이너를 모든 방문자가 공유하고,
  앱이 재시작되면 초기화된다. 즉 한 사람의 입력이 다른 방문자에게도 보이고, 재부팅 시 사라진다.
- 데모/요구사항 정렬 용도로는 충분하다. 방문자별 독립 샌드박스가 필요하면 세션별 DB 분리가 필요하다.

## 구성 파일

| 파일 | 역할 |
|---|---|
| `streamlit_app.py` | Streamlit 앱(메인) |
| `schema.py` | 상수(`MODULE_TYPES`, `MODEL_SLOTS`) + DB 경로 |
| `seed.py` | 가상 데이터 생성 + 검증 |
| `erd.py` | ERD SVG 렌더러 |
