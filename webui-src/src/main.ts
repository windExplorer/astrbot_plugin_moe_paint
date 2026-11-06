import { createApp } from "vue";
import App from "./App.vue";
import { router } from "./router";
import "./styles.css";

// 左右结构 + hash 路由（见 router.ts / bridge.ts 头注）。
createApp(App).use(router).mount("#app");
