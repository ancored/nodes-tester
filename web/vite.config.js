import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Сборка кладётся в ../dashboard/static (её отдаёт Python-сервер как есть).
// base: './' — относительные пути к ассетам, чтобы работать из корня админки.
// Dev-режим (`npm run dev`) проксирует /api на встроенный сервер тестера/дашборда.
export default defineConfig({
  plugins: [vue()],
  base: '/',
  build: {
    outDir: '../dashboard/static',
    emptyOutDir: true,
  },
  server: {
    proxy: {
      '/api': 'http://localhost:8088',
    },
  },
})
