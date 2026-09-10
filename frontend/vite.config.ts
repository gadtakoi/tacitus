import {defineConfig} from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
    plugins: [react()],

    resolve: {
        alias: {
            "@": path.resolve(__dirname, "./src"),
        },
    },

    server: {
        host: true,
        port: 5173,
        proxy: {
            // Dev proxy: the SPA calls /api/*, the FastAPI backend serves /*
            // (nginx strips /api in production — mirror that here).
            "/api": {
                target: "http://localhost:8000",
                changeOrigin: true,
                rewrite: (path) => path.replace(/^\/api/, ""),
            },
        },
    },

    preview: {
        host: true,
        port: 4173,
    },

    build: {
        outDir: "dist",
        // Source maps ship inside the nginx image and are served from / next to
        // the bundle, handing any visitor the full frontend source and the
        // dependency tree it was built from. Nothing consumes them: Sentry is
        // wired on the backend only (app/core/sentry.py).
        sourcemap: false,
        target: "es2022",
        emptyOutDir: true,
    },
});
