import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  server: {
    // En desarrollo el API corre aparte (uvicorn en el puerto 8000)
    proxy: { '/api': 'http://localhost:8000' },
  },
})
