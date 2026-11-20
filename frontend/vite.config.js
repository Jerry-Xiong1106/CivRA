import { defineConfig, loadEnv } from 'vite';
import vue from '@vitejs/plugin-vue';
import { fileURLToPath, URL } from 'node:url';
export default defineConfig(({ mode }) => {
  const directory = fileURLToPath(new URL('.', import.meta.url));
  const root = fileURLToPath(new URL('..', import.meta.url));
  const env = { ...loadEnv(mode, root, 'CIVRA_'), ...loadEnv(mode, directory, 'CIVRA_'), ...process.env };
  const proxy = {
    '/api': {
      target: env.CIVRA_API_TARGET || 'http://127.0.0.1:8000',
      changeOrigin: true,
      // A server-only workspace credential. Never use a VITE_* variable for it.
      configure(server) {
        server.on('proxyReq', (request) => {
          if (env.CIVRA_API_TOKEN) request.setHeader('Authorization', 'Bearer ' + env.CIVRA_API_TOKEN);
        });
      }
    }
  };
  return {
  plugins: [vue()],
  server: { host: '127.0.0.1', port: 8782, strictPort: true, proxy },
  preview: { host: '127.0.0.1', port: 8782, strictPort: true, proxy },
  build: {
    rollupOptions: {
      input: {
        workspace: fileURLToPath(new URL('./index.html', import.meta.url)),
        agentPreview: fileURLToPath(new URL('./agent-preview.html', import.meta.url))
      }
    }
  }
  };
});
