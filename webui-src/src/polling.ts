// 轻量轮询：页面可见时才跑，切后台自动停；失败静默（不弹 toast，避免刷屏）。
// 用途：日志 / 图库 / 任务状态这类"会自己变"的内容，不该让用户手动点刷新。
import { onMounted, onUnmounted, ref } from "vue";

export interface AutoRefreshOptions {
  /** 初始是否开启（默认开） */
  enabled?: boolean;
  /** 挂载后是否立刻跑一次（默认跑） */
  immediate?: boolean;
}

export function useAutoRefresh(
  fn: () => unknown | Promise<unknown>,
  intervalMs = 10000,
  opts: AutoRefreshOptions = {},
) {
  const enabled = ref(opts.enabled !== false);
  const busy = ref(false);
  const lastAt = ref(0);
  const failed = ref(false);
  let timer: ReturnType<typeof setInterval> | null = null;

  async function tick(force = false): Promise<void> {
    if (!enabled.value || busy.value) return;
    if (!force && typeof document !== "undefined" && document.visibilityState === "hidden") return;
    busy.value = true;
    try {
      await fn();
      lastAt.value = Date.now();
      failed.value = false;
    } catch {
      failed.value = true;
    } finally {
      busy.value = false;
    }
  }

  function start(): void {
    stop();
    timer = setInterval(() => void tick(), Math.max(1000, intervalMs));
    if (opts.immediate !== false) void tick(true);
  }

  function stop(): void {
    if (timer) clearInterval(timer);
    timer = null;
  }

  onMounted(start);
  onUnmounted(stop);

  return { enabled, busy, lastAt, failed, tick, start, stop };
}
