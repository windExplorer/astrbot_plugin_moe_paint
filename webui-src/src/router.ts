// hash 路由（AstrBot 按真实文件路径提供静态资源，history 模式刷新会 404）。
// 静态 import 而非懒加载：产物必须是单文件（inlineDynamicImports），
// 懒加载在这里没有收益，反而多一层 chunk 解析。
import { createRouter, createWebHashHistory } from "vue-router";
import OverviewView from "./views/OverviewView.vue";
import GalleryView from "./views/GalleryView.vue";
import ScheduleView from "./views/ScheduleView.vue";
import ServicesView from "./views/ServicesView.vue";
import DeliveryView from "./views/DeliveryView.vue";
import WorkflowsView from "./views/WorkflowsView.vue";
import ThemeView from "./views/ThemeView.vue";
import PromptView from "./views/PromptView.vue";
import AnimaView from "./views/AnimaView.vue";
import DebugView from "./views/DebugView.vue";

export interface MenuItem {
  key: string;
  label: string;
  icon: string;
  group: string;
}

export const MENU: MenuItem[] = [
  { key: "/overview", label: "总览", icon: "🌅", group: "运行" },
  { key: "/gallery", label: "图库", icon: "🖼️", group: "运行" },
  { key: "/schedule", label: "调度计划", icon: "⏰", group: "运行" },
  { key: "/delivery", label: "投递", icon: "📮", group: "运行" },
  { key: "/services", label: "生成服务", icon: "🎛️", group: "配置" },
  { key: "/workflows", label: "工作流模板", icon: "🧩", group: "配置" },
  { key: "/theme", label: "主题词池", icon: "🎨", group: "配置" },
  { key: "/prompt", label: "提示词", icon: "✍️", group: "配置" },
  { key: "/anima", label: "关联萌绘", icon: "🪄", group: "联动" },
  { key: "/debug", label: "调试与日志", icon: "🧪", group: "联动" },
];

export const menuOptions = [
  {
    type: "group",
    label: "运行",
    key: "g-run",
    children: MENU.filter((m) => m.group === "运行").map((m) => ({
      label: `${m.icon} ${m.label}`,
      key: m.key,
    })),
  },
  {
    type: "group",
    label: "配置",
    key: "g-config",
    children: MENU.filter((m) => m.group === "配置").map((m) => ({
      label: `${m.icon} ${m.label}`,
      key: m.key,
    })),
  },
  {
    type: "group",
    label: "联动与排查",
    key: "g-debug",
    children: MENU.filter((m) => m.group === "联动").map((m) => ({
      label: `${m.icon} ${m.label}`,
      key: m.key,
    })),
  },
];

// --------------------------------------------------------------------------- //
// 记住上次停留的页面（v0.7.17）
//
// 现象：在「图库」刷新一下又回到「总览」。
// 原因：AstrBot 的插件页是 iframe，仪表盘重建 iframe 时用的是原始 src，
// 不带 `#/gallery` —— hash 丢了，路由自然回落到 `/` → `/overview`。
// 做法：把最后一次真实页面存 localStorage，创建 router **之前**把 hash 补回去
// （顺序很关键：createWebHashHistory 会读取创建时刻的 location）。
// --------------------------------------------------------------------------- //
const LAST_ROUTE_KEY = "moe_paint:last-route";
const VALID_PATHS = new Set(MENU.map((m) => m.key));

function readLastRoute(): string {
  try {
    const saved = localStorage.getItem(LAST_ROUTE_KEY) || "";
    return VALID_PATHS.has(saved) ? saved : "";
  } catch {
    return ""; // 隐私模式 / 禁用 localStorage
  }
}

try {
  const hash = window.location.hash.replace(/^#/, "");
  const saved = readLastRoute();
  if ((!hash || hash === "/") && saved) {
    window.location.hash = saved;
  }
} catch {
  /* 拿不到 location 就按默认走 */
}

export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: "/", redirect: "/overview" },
    { path: "/overview", component: OverviewView },
    { path: "/gallery", component: GalleryView },
    { path: "/schedule", component: ScheduleView },
    { path: "/services", component: ServicesView },
    { path: "/delivery", component: DeliveryView },
    { path: "/workflows", component: WorkflowsView },
    { path: "/theme", component: ThemeView },
    { path: "/prompt", component: PromptView },
    { path: "/anima", component: AnimaView },
    { path: "/debug", component: DebugView },
    { path: "/:pathMatch(.*)*", redirect: "/overview" },
  ],
});

router.afterEach((to) => {
  if (!VALID_PATHS.has(to.path)) return;
  try {
    localStorage.setItem(LAST_ROUTE_KEY, to.path);
  } catch {
    /* 存不下就退化成「每次都回总览」，不影响其它功能 */
  }
});
