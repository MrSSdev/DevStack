import { fileURLToPath } from 'node:url'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  // Keep the shared SVG as a self-hosted asset: CSP intentionally blocks data: images.
  build: { outDir: '../backend/public', emptyOutDir: false, assetsInlineLimit: 0 },
})
