import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// In development the UI runs on Vite's dev server and talks to `poker serve`
// through this proxy. In production `poker serve` serves the built UI itself.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { '/api': 'http://127.0.0.1:8765' },
  },
})
