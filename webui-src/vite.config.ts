import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import cssInjectedByJs from "vite-plugin-css-injected-by-js";
import { fileURLToPath, URL } from "node:url";

// AstrBot 插件页面以静态资源方式从 pages/moe-paint/ 提供。
// 关键约束（user_gateway/anima 踩过的坑，勿改）：
//  1) base 必须 './'：AstrBot 会重写页面内相对资源引用并追加 asset_token；
//  2) 必须单文件产物（inlineDynamicImports）：跨 chunk import 会被 token 重写搞成 401 白屏；
//  3) cssCodeSplit=false + cssInjectedByJs：只加载一个 js，样式内联进去；
//  4) assetsInlineLimit=300KB：运行时才注入的小图走不了 token 重写，必须内联 data URI。
export default defineConfig({
  plugins: [vue(), cssInjectedByJs()],
  base: "./",
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  build: {
    outDir: "../pages/moe-paint",
    emptyOutDir: true,
    chunkSizeWarningLimit: 5000,
    cssCodeSplit: false,
    assetsInlineLimit: 300 * 1024,
    rollupOptions: {
      output: {
        inlineDynamicImports: true,
      },
    },
  },
  server: {
    port: 5180,
    proxy: {},
  },
});
