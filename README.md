# 모듈(부품) 단위 관리 데모

여러 모듈로 구성된 IoT 조리 장비에서 **모듈 단위로 사용량을 집계하고 교체·이력을 관리**하는
기능의 Streamlit 데모. 가상 데이터를 채운 SQLite 를 읽고/쓰며 5개 화면으로 보여준다.

이 폴더는 **자족형 배포 번들**이다 — 외부 의존(사내 monorepo `shared/` 등) 없이 단독으로 돈다.
데모 DB 는 **최초 실행 시 자동 생성**되므로 커밋할 필요가 없다.

## 화면 (📄 조회 4 · ✏️ 입력/수정 1)

1. 📄 장비 상세 — 현재 모듈 구성 + 초기 설치부터의 설치/교체 이력
2. ✏️ 데이터 입력 — 모듈 장착/교체/탈거(허용값·반영결과 확인)
3. 📄 모듈 상세 — 누적 사용량 + 고객사별 분해 + 장착 이력
4. 📄 모듈 Fleet 개요 — 전체 수명 소진 현황
5. 📄 ERD — 관계 테이블 다이어그램

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

- '데이터 입력' 탭과 '재시드' 버튼은 SQLite 파일에 **쓰기**를 한다.
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
