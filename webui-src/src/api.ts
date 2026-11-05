// API 封装：通过 AstrBot 官方插件 Page 桥接调用插件后端（user_gateway 同款骨架）。
// 桥接会正确处理 /api/plug/ 路由与 asset_token 鉴权；**不能**用裸 fetch 相对路径。
// 后端信封 {status: ok|error, data, message}：正常情况下 Dashboard 已吃掉一层，
// 这里做防御式二次解包；只有「路由不存在」才换端点候选重试，业务错误立即抛。

import { whenBridgeReady } from "./bridge";

const PAGE_PLUGIN_NAME = "astrbot_plugin_moe_paint";

function endpointCandidates(routePath: string): string[] {
  const clean = routePath.replace(/^\/+/, "");
  const list = [clean, "/" + clean, `${PAGE_PLUGIN_NAME}/${clean}`, `/${PAGE_PLUGIN_NAME}/${clean}`];
  return [...new Set(list.map((s) => s.replace(/\/+/g, "/")).filter(Boolean))];
}

function unwrap(payload: any): any {
  if (payload && typeof payload === "object") {
    if (payload.status === "error") throw new Error(payload.message || "请求失败");
    if (payload.status === "ok" && "data" in payload) return payload.data;
  }
  return payload;
}

function withTimeout<T>(promise: Promise<T>, ms: number, label: string): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | null = null;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error(label + " 超时（" + ms / 1000 + "s 无响应）")), ms);
  });
  return Promise.race([promise, timeout]).finally(() => {
    if (timer) clearTimeout(timer);
  });
}

function isRouteMissingError(e: any): boolean {
  const text =
    String(e?.message || e?.toString?.() || e || "") +
    " " +
    String((e && e.message) || "");
  return /未找到.*路由|route.*not.*found|not.*found.*route|404|not found/i.test(text);
}

async function request(path: string, method: "GET" | "POST", body?: unknown, timeoutMs?: number): Promise<any> {
  const br = await whenBridgeReady();
  const url = new URL(path, "https://astrbot-plugin-page.local/");
  const candidates = endpointCandidates(url.pathname);
  const errors: string[] = [];
  const ms = timeoutMs ?? 15000;

  if (method === "GET") {
    const params = Object.fromEntries(url.searchParams.entries());
    for (const c of candidates) {
      try {
        const p = await withTimeout(br.apiGet(c, Object.keys(params).length ? params : undefined), ms, "GET " + c);
        if (p && p.status === "error" && /路由|route|404/i.test(String(p.message || ""))) {
          errors.push(p.message || "未找到该路由");
          continue;
        }
        return unwrap(p);
      } catch (e: any) {
        if (!isRouteMissingError(e)) throw e;
        errors.push(e?.message || String(e));
      }
    }
    throw new Error(errors[0] || "未找到可用的页面 API 路由");
  }

  let payload = body || {};
  try {
    payload = JSON.parse(JSON.stringify(payload));
  } catch {
    /* ignore */
  }
  for (const c of candidates) {
    try {
      const r = await withTimeout(br.apiPost(c, payload), ms, "POST " + c);
      if (r && r.status === "error" && /路由|route|404/i.test(String(r.message || ""))) {
        errors.push(r.message || "未找到该路由");
        continue;
      }
      return unwrap(r);
    } catch (e: any) {
      // POST 绝不因业务错误重试（会重复写库/重复触发）
      if (!isRouteMissingError(e)) throw e;
      errors.push(e?.message || e?.toString?.() || String(e));
    }
  }
  throw new Error(errors[0] || "未找到可用的页面 API 路由");
}

export function apiGet<T = any>(endpoint: string, timeoutMs?: number): Promise<T> {
  return request(endpoint, "GET", undefined, timeoutMs) as Promise<T>;
}

export function apiPost<T = any>(endpoint: string, body?: unknown, timeoutMs?: number): Promise<T> {
  return request(endpoint, "POST", body || {}, timeoutMs) as Promise<T>;
}

// ---------------------------------------------------------------- 类型

export interface GenRecord {
  id: number;
  ts: number;
  day: string;
  status: "running" | "success" | "failed" | string;
  reason: string;
  category: string;
  brief: string;
  prompt: string;
  negative: string;
  tag_style: string;
  workflow: string;
  width: number | null;
  height: number | null;
  seed: number | null;
  comfy_prompt_id: string;
  image_relpath: string;
  sha256: string;
  caption: string;
  llm_provider: string;
  llm_latency_ms: number | null;
  gen_latency_ms: number | null;
  error: string;
  starred: 0 | 1 | number;
  delivered?: { umo: string; ok: boolean; ts: number; error?: string; via?: string }[];
}

export interface TemplatePublic {
  name: string;
  file: string;
  tag_style: string;
  size_preset: string;
  enabled: boolean;
  ok: boolean;
  problem: string;
  nodes: Record<string, any>;
}

export interface StatusPayload {
  enabled: boolean;
  paused_manual: boolean;
  auto_paused: boolean;
  consecutive_failures: number;
  next_due: string | null;
  last_run_note: string;
  mode: string;
  interval_minutes: number;
  daily_times: string;
  quiet_hours: string;
  max_per_day: number;
  counts: { total: number; success: number; failed: number; running: number };
  today_success: number;
  templates: TemplatePublic[];
  selection_mode: string;
  comfy: { ok: boolean; elapsed_ms: number; error: string };
  llm: { provider_id: string; found: boolean; label: string };
  delivery_sessions: number;
}

export interface StatsPayload {
  total: number;
  success: number;
  failed: number;
  running: number;
  success_rate: number | null;
  by_category: { category: string; success: number; failed: number }[];
  by_workflow: { workflow: string; success: number; failed: number }[];
  days: { day: string; success: number }[];
}

function qs(params: Record<string, string | number | undefined>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== "") p.set(k, String(v));
  }
  return p.toString();
}

// ---------------------------------------------------------------- 调用

export function apiStatus() {
  return apiGet<StatusPayload>("/status", 12000);
}

export function apiStats() {
  return apiGet<StatsPayload>("/stats");
}

export function apiRecords(f: {
  offset?: number;
  limit?: number;
  status?: string;
  workflow?: string;
  category?: string;
  day?: string;
  keyword?: string;
  starred?: number;
}) {
  return apiGet<{ rows: GenRecord[]; total: number }>(`/records?${qs(f)}`, 20000);
}

/** 缩略图（data URL；内嵌页 <img> 的唯一可靠姿势）。 */
export function apiThumb(id: number, size = 320) {
  return apiGet<{ data_url: string; size: number }>(`/thumb?id=${id}&size=${size}`, 20000);
}

export function apiControl(action: "pause" | "resume" | "trigger", theme?: string) {
  return apiPost<{ started?: boolean }>(
    "/control",
    { action, theme: theme || undefined },
    15000,
  );
}

export function apiDeleteRecord(id: number) {
  return apiPost<{ deleted: boolean; file_removed: boolean }>("/record/delete", { id });
}

export function apiStar(id: number, starred: boolean) {
  return apiPost<{ id: number; starred: boolean }>("/record/star", { id, starred });
}

export function apiResend(id: number) {
  return apiPost<{ results: { umo: string; ok: boolean; error?: string }[] }>(
    "/record/resend",
    { id },
    60000,
  );
}

export function apiWorkflows() {
  return apiGet<{ templates: TemplatePublic[]; selection_mode: string; workflows_dir: string }>(
    "/workflows",
  );
}

// ---------------------------------------------------------------- 配置

export interface ProviderRow {
  id: string;
  model: string;
  label: string;
  is_default: boolean;
}

export function apiGetConfig() {
  return apiGet<{ items: Record<string, any>; schema: Record<string, any> }>("/config", 15000);
}

export function apiSaveConfig(items: Record<string, any>) {
  return apiPost<{ changed: string[] }>("/config", { items }, 15000);
}

export function apiProviders() {
  return apiGet<{ items: ProviderRow[]; default_id: string }>("/providers", 10000);
}
