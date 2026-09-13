import { resolve } from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

function katexWoff2Only(): Plugin {
  return {
    name: "katex-woff2-only",
    enforce: "pre",
    transform(code, id) {
      if (!id.includes("/katex/dist/katex.min.css")) return null;
      return code.replace(
        /,url\([^)]*\.(?:woff|ttf)\) format\("(?:woff|truetype)"\)/g,
        "",
      );
    },
  };
}

export default defineConfig({
  base: "./",
  publicDir: false,
  plugins: [katexWoff2Only(), react()],
  build: {
    outDir: "release/0.1.2",
    emptyOutDir: true,
    assetsInlineLimit: 0,
    cssCodeSplit: false,
    rollupOptions: {
      input: resolve(import.meta.dirname, "embed.tsx"),
      output: {
        format: "iife",
        name: "Paper2HTMLReader",
        entryFileNames: "reader.js",
        assetFileNames: (asset) => asset.names.some((name) => name.endsWith(".css"))
          ? "reader.css"
          : "fonts/[name][extname]",
      },
    },
  },
});
