import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const configDir = path.dirname(fileURLToPath(import.meta.url));

function localBackendUrl(): string {
  try {
    const text = fs.readFileSync(path.resolve(configDir, ".env.local"), "utf8");
    const match = text.match(/^\s*VITE_BACKEND_URL\s*=\s*(.+)\s*$/m);
    return match ? match[1].trim().replace(/^["']|["']$/g, "") : "";
  } catch {
    return "";
  }
}

// 后端地址：开发时通过 Vite 代理转发，避免跨域问题
// 注意：不要用 localhost:8000！Windows 上 127.0.0.1:8000 可能被其他进程
// （如 VS Code 端口转发）占用，请求会打到错误的服务导致卡在上传中。
// 未配置时使用 ZeroTier 内网地址 10.20.1.48:8010（后端监听 0.0.0.0:8010）。
const BACKEND_URL =
  process.env.VITE_BACKEND_URL ||
  localBackendUrl() ||
  "http://10.20.1.48:8010";

const proxyToBackend = {
  target: BACKEND_URL,
  changeOrigin: true,
  timeout: 30_000,
  proxyTimeout: 30_000,
};

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    host: "127.0.0.1",
    proxy: {
      "/api": proxyToBackend,
      "/ws": {
        ...proxyToBackend,
        ws: true,
      },
      "/static": proxyToBackend,
    },
    // 3DGS 渲染器需要 SharedArrayBuffer，需开启 COOP/COEP
    headers: {
      "Cross-Origin-Opener-Policy": "same-origin",
      "Cross-Origin-Embedder-Policy": "require-corp",
    },
  },
  build: {
    outDir: "dist",
    chunkSizeWarningLimit: 1500,
  },
});
