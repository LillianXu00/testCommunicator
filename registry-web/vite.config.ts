import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'node:path'

export default defineConfig({
  base: '/registry-ui/',
  plugins: [vue()],
  build: {
    outDir: resolve(__dirname, '../registry-service/registry_service/static'),
    emptyOutDir: true,
    sourcemap: false,
  },
  server: {
    port: 5173,
    proxy: {
      '/health': 'http://127.0.0.1:4200',
      '/registry': 'http://127.0.0.1:4200',
      '/onboarding': 'http://127.0.0.1:4200',
    },
  },
})
