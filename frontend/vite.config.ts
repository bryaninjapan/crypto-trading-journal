import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 构建产物输出到 FastAPI 托管目录（trading_journal/static），生产由 api.py 提供。
// 开发时 /api 代理到本地 FastAPI（uvicorn :8000）。
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../trading_journal/static",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
