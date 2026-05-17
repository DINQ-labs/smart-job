import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import type { IncomingMessage } from 'http'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5174,
    proxy: {
      '/admin': {
        target: 'http://127.0.0.1:8767',
        changeOrigin: true,
        ws: true,
      },
      '/cli': {
        target: 'http://127.0.0.1:8767',
        changeOrigin: true,
      },
      '/status': {
        target: 'http://127.0.0.1:8767',
        changeOrigin: true,
      },
      '/agent-gw': {
        target: 'http://127.0.0.1:8769',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/agent-gw/, ''),
      },
      '/portal': {
        target: 'http://127.0.0.1:8771',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/portal/, ''),
      },
      '/dinq-gw': {
        target: 'http://127.0.0.1:8100',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/dinq-gw/, ''),
        configure: (proxy) => {
          // Disable response buffering for SSE streams
          proxy.on('proxyRes', (proxyRes: IncomingMessage) => {
            const ct = proxyRes.headers['content-type'] || ''
            if (ct.includes('text/event-stream')) {
              // @ts-ignore
              proxyRes.headers['cache-control'] = 'no-cache'
              // @ts-ignore
              proxyRes.headers['x-accel-buffering'] = 'no'
            }
          })
        },
      },
    },
  },
})
