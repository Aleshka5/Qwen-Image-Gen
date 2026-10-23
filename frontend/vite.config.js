import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const AUTH_USER = '11111111-1111-4111-8111-111111111111';
const AUTH_NAME = 'Ada Lovelace';

function proxyOptions() {
  return {
    target: 'http://127.0.0.1:8000',
    changeOrigin: true,
    headers: {
      'X-Auth-User-Id': AUTH_USER,
      'X-Auth-Name': AUTH_NAME,
    },
    configure(proxy) {
      proxy.on('proxyReq', (proxyReq) => {
        proxyReq.setHeader('X-Auth-User-Id', AUTH_USER);
        proxyReq.setHeader('X-Auth-Name', AUTH_NAME);
      });
    },
  };
}

export default defineConfig({
  plugins: [react()],
  base: '/ui/',
  build: {
    outDir: '../app/static/ui',
    emptyOutDir: true,
    manifest: true,
  },
  server: {
    port: 5173,
    proxy: {
      '/api': proxyOptions(),
      '/outputs': proxyOptions(),
      '/healthz': proxyOptions(),
    },
  },
});
