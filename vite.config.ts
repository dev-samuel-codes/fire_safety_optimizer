import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0", // 외부(브라우저)에서 접속 가능하게
    allowedHosts: true, // 원격 IDE/터널 도메인 Host 허용(Vite 기본 차단 해제) — 빈 화면 원인
    proxy: {
      // 프론트의 /api → 서버 로컬 백엔드(FireVal 엔진)로 프록시
      // 브라우저는 :5173만 보고, Vite가 :8900으로 넘겨줌 (127.0.0.1 혼동·CORS 회피)
      "/api": "http://127.0.0.1:8900",
    },
  },
});
