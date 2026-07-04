# FireOpt — 소방 설계 자동 검증

건축 도면(DWG/DXF)을 업로드하면 한국 화재안전기술기준(NFTC) 관점에서 소방 설비를 검토하는 웹앱.
프론트(React) + 엔진(Python, `backend/`)이 한 레포에 있습니다.

## 실행 (로컬)

```bash
# 1) 백엔드(엔진 API) — backend/에서
cd backend
pip install -r requirements.txt
PYTHONPATH=. python -m fireval.api.server        # http://127.0.0.1:8900
#   DWG 지원: dwg2dxf(libredwg)가 PATH에 있거나 DWG2DXF 환경변수로 지정

# 2) 프론트 — 루트에서 (새 터미널)
npm install
npm run dev                                       # http://localhost:5173  (/api → :8900 프록시)
```

브라우저에서 http://localhost:5173 접속.

## 지금 무엇이 작동하나 (정직하게)

**🟢 작동**
- DWG/DXF 업로드 + 브라우저 렌더(뷰어)
- 업로드 도면의 **실제 사실 추출**: 레이어·엔티티 수·소방 설비 레이어·방 이름 (`POST /api/analyze`)
- 사실 요약 `.md` 다운로드 (보고서 / 내보내기)

**🟡 준비 중**
- **NFTC 적정성 판정**(방별 필요 감지기 수 등) — 방 면적·설비 심볼 자동 인식의 신뢰성 확보 후 연결
- 저장 · 프로젝트 관리 · 좌측 네비 · DWG 주석 내보내기

## 구조
```
src/               프론트 (React + Vite + TypeScript)
backend/fireval/   엔진 (Python): api(Flask) · ingest(도면 파싱) · engine(NFTC 규칙) · schema · report
```

API 계약·통합 설계는 [`backend/INTEGRATION.md`](backend/INTEGRATION.md) 참고.
