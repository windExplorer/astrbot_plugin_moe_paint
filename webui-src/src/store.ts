// 全局运行态（跨页面共享）：状态 / 统计 / 连接 / 主题。
// 硬约束：sandbox iframe 里 localStorage 会抛 SecurityError，主题只能走 bridge 的
// context 或 bridge.ts 的安全封装（storageGet/Set）。
import { computed, ref } from "vue";
import { apiStats, apiStatus, type StatsPayload, type StatusPayload } from "./api";
import { getContext, onContext, storageGet, storageSet } from "./bridge";

export const isDark = ref<boolean>(!!getContext()?.isDark);
export const status = ref<StatusPayload | null>(null);
export const stats = ref<StatsPayload | null>(null);
export const conn = ref<"idle" | "ok" | "error">("idle");
export const connError = ref("");
export const bootAt = ref<number>(Date.now());

const THEME_KEY = "moe-paint.theme";

export function initTheme(): void {
  const saved = storageGet(THEME_KEY);
  if (saved === "dark") isDark.value = true;
  else if (saved === "light") isDark.value = false;
  else isDark.value = !!getContext()?.isDark;
  onContext((ctx) => {
    if (!storageGet(THEME_KEY) && typeof ctx?.isDark === "boolean") isDark.value = ctx.isDark;
  });
}

export function toggleTheme(): void {
  isDark.value = !isDark.value;
  storageSet(THEME_KEY, isDark.value ? "dark" : "light");
}

export async function refreshStatus(): Promise<void> {
  try {
    status.value = await apiStatus();
    conn.value = "ok";
    connError.value = "";
  } catch (e: any) {
    conn.value = "error";
    connError.value = e?.message || String(e);
  }
}

export async function refreshStats(): Promise<void> {
  try {
    stats.value = await apiStats();
  } catch {
    /* 统计失败不影响主流程 */
  }
}

export const schedTag = computed(() => {
  const s = status.value;
  if (!s) return { text: "读取中…", type: "default" as const };
  if (s.running) return { text: "生成中…", type: "info" as const };
  if (!s.enabled) return { text: "总开关关闭", type: "default" as const };
  if (s.auto_paused) return { text: "失败自动暂停", type: "error" as const };
  if (s.paused_manual) return { text: "已暂停", type: "warning" as const };
  return { text: "调度运行中", type: "success" as const };
});

export const successRate = computed(() => {
  const s = stats.value;
  if (!s) return null;
  const done = (s.success || 0) + (s.failed || 0);
  return done > 0 ? (s.success / done) * 100 : null;
});
