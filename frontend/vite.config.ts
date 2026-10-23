import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { fileURLToPath, URL } from 'node:url';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  return {
    plugins: [react(), tailwindcss()],
    // 用 --mode brand / --mode creator 启两个端口，各自固定成品牌端或达人端，方便同时开着看。
    define: { __APP_ROLE__: JSON.stringify(mode === 'brand' || mode === 'creator' ? mode : '') },
    resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
    server: { port: mode === 'creator' ? 5174 : 5173, strictPort: true, proxy: { '/api': { target: env.API_PROXY_TARGET || 'http://127.0.0.1:8000', changeOrigin: true } } },
    preview: { port: 4173, strictPort: true },
  };
});
