import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { createRequire } from 'node:module'

const require = createRequire(import.meta.url)

export default defineConfig({
  plugins: [react()],
  define: { global: 'globalThis' },
  resolve: {
    alias: {
      buffer: require.resolve('buffer/'),
      events: require.resolve('events/'),
      process: require.resolve('process/browser'),
      stream: require.resolve('stream-browserify'),
      util: require.resolve('util/'),
    },
  },
  server: { port: 5273, strictPort: true },
  preview: { port: 5273, strictPort: true },
})
