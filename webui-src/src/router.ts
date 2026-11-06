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
