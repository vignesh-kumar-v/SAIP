import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// The build is committed to src/saip/ui/dist so `pip install -e .` + `saip run` works without Node.
// Fixed file names: a rebuild changes files instead of piling up hashed copies in git.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: "/",
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://127.0.0.1:5610", changeOrigin: false } },
  },
  build: {
    outDir: "../src/saip/ui/dist",
    emptyOutDir: true,
    assetsDir: "assets",
    sourcemap: false,
    chunkSizeWarningLimit: 900,
    rollupOptions: {
      output: {
        entryFileNames: "assets/app.js",
        chunkFileNames: "assets/[name].js",
        assetFileNames: "assets/[name][extname]",
      },
    },
  },
  test: { environment: "node" },
} as any);
