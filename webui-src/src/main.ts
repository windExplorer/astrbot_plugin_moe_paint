import { createApp } from "vue";
import App from "./App.vue";

// 无路由：图库是单页（网格 + 详情抽屉）。页内不用 localStorage（sandbox 无
// allow-same-origin，见 bridge.ts 头注），筛选状态留在内存即可。
createApp(App).mount("#app");
