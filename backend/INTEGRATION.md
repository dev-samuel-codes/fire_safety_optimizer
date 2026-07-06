# FireVal/FireOpt 엔진 ↔ fire_safety_optimizer 프론트엔드 통합

## 1. 구성 — 프론트 + 엔진(상보적)
| | 프론트(React+WASM) | 엔진(Python) |
|---|---|---|
| 역할 | DWG/DXF 뷰어·대시보드 UI | 도면 파싱·방추출·NFTC 판정·보고서 |

현재 상태: 프론트가 `backend/` 엔진을 실제 호출(`/api/analyze`) → **업로드 도면의 사실 추출(레이어·소방레이어·실명)은 작동.** NFTC 적정성 판정은 방 면적 추출 신뢰성 확보 후 연결 예정(§3 참고).

## 2. 서로 다른 환경이 어떻게 협업하나 — **API 계약(contract)**
React(TypeScript/브라우저)와 Python(shapely/scipy)은 **코드를 공유하지 않습니다.** 오직 **HTTP+JSON 인터페이스**로만 대화해요. 그래서 환경이 완전히 달라도 상관없음 — 이게 API의 존재 이유.

```
[브라우저] DWG 읽기(LibreDWG WASM)                        [우리 서버] Python
    │  파일 or 기하 JSON  ──POST /api/analyze──▶  ezdxf 파싱
    │                                              → 방추출 + NFTC검사 + 충돌 + 최적화
    │  ◀──────  JSON(violations/conflicts/…)  ──   → 결과
    ▼
  대시보드에 표시(목업 → 실제 API로 교체)
```

프론트는 "무엇을 보내고 무엇을 받는지"만 알면 되고, 엔진 내부(방 추출이 EXCLUDE 레시피든 DL이든)는 몰라도 됩니다. **계약이 경계**입니다.

## 3. API 계약 (v2 — 현재 구현, 정직성 원칙)
### `GET /api/health` → `{ "status":"ok", "engine":"FireVal+FireOpt", "rules": <int>, "dwg2dxfAvailable": true, "dwgreadAvailable": true }`

### `POST /api/analyze` (multipart: `file`=DWG/DXF, 없으면 사실 없음)
응답(JSON):
```jsonc
{
  // 업로드 도면에서 '확실히 추출되는 사실'(파일 있을 때만; 없으면 null).
  // DWG는 서버가 dwg2dxf로 변환 후 ezdxf 파싱.
  "drawingInfo": { "fileName":"...", "layerCount":326, "entityCount":20135,
                   "fireLayers":["FIRE-01","SP_HEAD-08"], "roomNames":["보육실","유희실"],
                   "analysisStatus":"ok", "analysisSource":"dwg2dxf" },
                   // 파싱 실패 시: { "fileName":"...", "error":"..." }
                   // 기본 DWG→DXF가 깨졌지만 복구 가능하면:
                   // { "analysisStatus":"recovered", "analysisSource":"dwgread-json",
                   //   "analysisWarnings":["기본 DWG→DXF 변환 결과를 ezdxf가 읽지 못했습니다: ..."] }
  "violations":       [],   // NFTC 적정성 판정: 인식 파이프라인 연결 후(현재 빈 배열)
  "recommendations":  [],
  "judgmentStatus":   "pending-recognition"
}
```
⚠️ **가짜 판정을 내보내지 않는다(정직).** 업로드 도면의 NFTC 적정성 판정(방별 필요 감지기 수)은
설비 심볼 인식 + 방 면적 자동추출(R³의 Recognize 단계)이 필요하다. 실제 도면엔 방 경계 폴리라인·
면적표기가 도형으로 없고 FIRE 레이어 심볼 카운트는 노이즈가 커서, 신뢰 가능한 판정에는 인식
파이프라인이 필요하다. 그 전까지는 `drawingInfo`(확실한 사실)만 반환한다.

프론트 '보고서'·'내보내기'는 이 `drawingInfo`로 **클라이언트에서** 사실 요약(.md)을 만든다(별도 엔드포인트 없음).

## 4. Git 협업 (모노레포)
- **레포**: `dev-samuel-codes/fire_safety_optimizer` — 프론트(`src/`) + 엔진(`backend/`, vendored)이 한 레포.
  (엔진 원본은 `소방 설계 자동화/`, `backend/fireval/`로 복사되어 함께 배포됨.)
- 각자 브랜치 → PR → main. 계약(§3)이 바뀌면 이 문서도 같은 PR로 갱신.

### 표준 흐름
```bash
git pull
git checkout -b feat/xxx
# ...수정...
git add -A && git commit -m "feat: ..."
git push -u origin feat/xxx    # → GitHub에서 PR → 리뷰 → main 병합
```
환경별 파일은 커밋 안 함(`.gitignore`: `node_modules/`·`.venv/`·`__pycache__/`). 의존성 목록만 공유: 프론트 `package.json`, 백엔드 `backend/requirements.txt`.

## 5. 실행 (모노레포, 로컬)
```bash
# 백엔드(엔진 API) — backend/에서
cd backend && PYTHONPATH=. python -m fireval.api.server   # :8900
#   DWG 지원: dwg2dxf(libredwg)가 PATH에 있거나 DWG2DXF 환경변수로 지정

# 프론트 — 루트에서
npm install && npm run dev                                # :5173, /api → :8900 프록시
```
