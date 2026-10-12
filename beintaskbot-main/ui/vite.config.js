import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"
import { resolve } from "node:path"

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: resolve(import.meta.dirname, "../docs"),
    emptyOutDir: false,
    cssCodeSplit: false,
    rollupOptions: {
      input: resolve(import.meta.dirname, "src/main.jsx"),
      output: {
        entryFileNames: "tenant-chakra-ui.js",
        assetFileNames: "tenant-chakra-ui.[ext]",
      },
    },
  },
})
