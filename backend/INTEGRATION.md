# FireVal/FireOpt 엔진 ↔ fire_safety_optimizer 프론트엔드 통합

## 1. 왜 통합이 쉬운가 — 상보적
| | 프론트(Samuel, React+WASM) | 엔진(우리, Python) |
|---|---|---|
| 있는 것 | DWG 뷰어·대시보드 UI | 방추출·NFTC검사·충돌감지·최적화·보고서 |
| 없는 것 | 실제 엔진(전부 목업) | 프론트엔드 |

→ 프론트의 `ui-only` 5개(방/면적·법규검토·충돌감지·재최적화·내보내기) = 우리가 가진 5개. **프론트 + 엔진 = 완성 제품.**

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
### `GET /api/health` → `{ "status":"ok", "engine":"FireVal+FireOpt", "rules": <int> }`

### `POST /api/analyze` (multipart: `file`=DWG/DXF, 없으면 사실 없음)
응답(JSON):
```jsonc
{
  // 업로드 도면에서 '확실히 추출되는 사실'(파일 있을 때만; 없으면 null).
  // DWG는 서버가 dwg2dxf로 변환 후 ezdxf 파싱.
  "drawingInfo": { "fileName":"...", "layerCount":326, "entityCount":20135,
                   "fireLayers":["FIRE-01","SP_HEAD-08"], "roomNames":["보육실","유희실"] },
                   // 파싱 실패 시: { "fileName":"...", "error":"..." }
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

## 4. Git 협업 워크플로 (두 팀, 두 레포)
- **프론트 레포**: `dev-samuel-codes/fire_safety_optimizer` (Samuel 소유).
- **엔진 레포**: 우리 `소방 설계 자동화/`를 **깃 레포로 만들어 push** 필요(현재 비-git).
- 연결점 = **위 API 계약(이 문서)**. 각자 자기 레포에서 독립 개발, 계약만 맞추면 됨.
- 계약 변경 시: 이 문서를 PR로 갱신 → 양쪽 합의 → 각자 반영.

### 표준 흐름 (각자)
```bash
git pull                      # 최신 받기
git checkout -b feat/analyze  # 작업 브랜치
# ...코드 수정...
git add -A && git commit -m "feat: /api/analyze 실제 엔진 연결"
git push -u origin feat/analyze
# GitHub에서 Pull Request → 리뷰 → main 병합
```
환경별 파일은 **공유 안 함**(`.gitignore`에 `node_modules/`·`.venv/`·`__pycache__/`). 대신 `package.json`(프론트)·`requirements.txt`(엔진)로 **의존성 목록만 공유** → 각자 `npm install` / `pip install -r`로 자기 환경 재현.

## 5. 실행
```bash
# 엔진 서버(우리)
cd "소방 설계 자동화" && ./.venv/bin/python -m fireval.api.server   # :8900
# 프론트(Samuel)
cd fire_safety_optimizer && npm install && npm run dev             # :5173, /api → :8900
```
