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

export interface DeliveredItem {
  umo: string;
  ok: boolean;
  ts: number;
  error?: string;
  via?: string;
}

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
  /** 库内相对路径；被 LRU 淘汰/清理后为空串 */
  image_relpath: string;
  sha256: string;
  caption: string;
  llm_provider: string;
  llm_latency_ms: number | null;
  gen_latency_ms: number | null;
  error: string;
  starred: 0 | 1 | number;
  /** 1 = 图片已按容量上限清理（记录仍在，可看提示词/简报） */
  pruned?: 0 | 1 | number;
  /** 成图自检分数（0~100；没开自检时为 null） */
  score?: number | null;
  score_reason?: string;
  delivered?: DeliveredItem[];
}

export interface PromptPreset {
  name: string;
  prompt: string;
  width: number;
  height: number;
  workflow: string;
}

export interface TemplatePublic {
  name: string;
  file: string;
  tag_style: string;
  size_preset: string;
  default_negative: string;
  enabled: boolean;
  ok: boolean;
  problem: string;
  nodes: Record<string, any>;
  warnings?: string[];
  source?: string;
  prompt_profile?: string;
  profile?: Record<string, any>;
  quality_prefix?: string;
  subject_scope?: string;
  scope?: string;
  key?: string;
  /** 实际生效的采样参数覆盖（steps/cfg/sampler_name/scheduler） */
  params?: Record<string, any>;
}

export interface RotationItem {
  index: number;
  key: string;
  name: string;
  file: string;
  source: string;
  scope: string;
}

export interface RotationPayload {
  mode: string;
  ordered: boolean;
  total: number;
  items: RotationItem[];
  next_index: number;
  next_name: string;
  next_key: string;
  last_key: string;
  last_found: boolean;
  manual_advances: boolean;
}

export interface AnimaBrief {
  installed: boolean;
  version: string;
  follow_server: boolean;
  server_key: string;
  active_url: string;
  active_name: string;
}

export interface StatusPayload {
  enabled: boolean;
  paused_manual: boolean;
  auto_paused: boolean;
  consecutive_failures: number;
  next_due: string | null;
  /** 是否正在生成（生成期间 next_due 仍是过期值，别拿它当「下次」显示） */
  running?: boolean;
  running_since?: string | null;
  running_for_sec?: number;
  running_note?: string;
  /** 计划已过期多少秒但没在生成（>60 说明调度可能卡住） */
  next_due_overdue_sec?: number;
  last_run_note: string;
  last_run_ts: number;
  mode: string;
  interval_minutes: number;
  jitter_minutes: number;
  daily_times: string;
  quiet_enabled: boolean;
  quiet_hours: string;
  max_per_day: number;
  counts: { total: number; success: number; failed: number; running: number };
  today_success: number;
  templates: TemplatePublic[];
  selection_mode: string;
  workflow_source?: string;
  comfy: { ok: boolean; elapsed_ms: number; error: string; url?: string; source?: string };
  llm: { provider_id: string; found: boolean; label: string };
  delivery_sessions: number;
  anima: AnimaBrief & { workflow?: string; size_preset?: string };
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
  /** 成图自检：平均分 / 有分数的张数 / 低分（<60）张数 */
  score_avg?: number | null;
  score_count?: number;
  score_low?: number;
}

export interface AnimaServer {
  key: string;
  name: string;
  url: string;
  enabled: boolean;
  device: string;
}

export interface AnimaInfo {
  installed: boolean;
  activated: boolean;
  version: string;
  servers: AnimaServer[];
  active_key: string;
  active_url: string;
  active_name: string;
  error: string;
  follow_server: boolean;
  server_key: string;
  effective_url: string;
  url_source: "manual" | "anima" | string;
  manual_url: string;
  anima_plugin_name: string;
}

export interface DebugEnv {
  comfy_url: string;
  comfy_url_source: string;
  manual_url: string;
  gallery_dir: string;
  workflows_dir: string;
  db_path: string;
  delivery_sessions: string[];
  templates: TemplatePublic[];
  selection_mode: string;
  queue_defer_threshold: number;
  log_capacity: number;
}

export interface DebugLogRow {
  ts: number;
  kind: string;
  msg: string;
}

export interface ComfyProbe {
  ok: boolean;
  url: string;
  source?: string;
  elapsed_ms?: number;
  error?: string;
  queue?: { running: number; pending: number } | null;
  queue_error?: string;
}

export interface LlmPreview {
  ok: boolean;
  error: string;
  brief: Record<string, string> | null;
  template: string;
  tag_style: string;
  /** 本轮工作流生效的可画类型：anime / photo / both */
  scope: string;
  size: { width: number; height: number } | null;
  prompt: string;
  negative: string;
  caption: string;
  raw: string;
  llm_provider: string;
  llm_ms: number;
  total_ms: number;
  /** 正向标签数（danbooru 系；超过 max_tags 会被截断） */
  tag_count?: number;
  /** 指令参数解析问题（有值说明某个开关被忽略） */
  args_note?: string;
}

export interface ProviderRow {
  id: string;
  model: string;
  label: string;
  is_default: boolean;
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
  return apiGet<{ rows: GenRecord[]; total: number }>(`/records?${qs(f)}`, 25000);
}

export function apiRecord(id: number) {
  return apiGet<GenRecord>(`/record?id=${id}`, 15000);
}

/** 缩略图（data URL；内嵌页 <img> 的唯一可靠姿势）。 */
export function apiThumb(id: number, size = 320) {
  return apiGet<{ data_url: string; size: number }>(`/thumb?id=${id}&size=${size}`, 25000);
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

/** 按已有记录重画：same = 同 seed，variant = 换 seed。 */
export function apiRedraw(id: number, mode: "same" | "variant" = "variant") {
  return apiPost<{ started: boolean; same_seed: boolean }>("/record/redraw", { id, mode });
}

/** 提示词收藏列表（不含负向词）。 */
export function apiPromptPresets() {
  return apiGet<{ items: PromptPreset[] }>("/prompt/presets", 15000);
}

export function apiPromptPresetSave(id: number, name: string) {
  return apiPost<{ name: string }>("/prompt/preset", { action: "save", id, name });
}

export function apiPromptPresetDelete(name: string) {
  return apiPost<{ ok: boolean }>("/prompt/preset", { action: "delete", name });
}

export function apiPromptPresetDraw(name: string) {
  return apiPost<{ started: boolean }>("/prompt/preset", { action: "draw", name });
}

/** 对已有记录跑一次成图自检（用于校准阈值，不改状态）。 */
export function apiDebugScore(id: number) {
  return apiPost<{ score: number; issues: string }>("/debug/score", { id }, 120000);
}

export interface AnimaWorkflowRow {
  key: string;
  name: string;
  enabled: boolean;
  base_id: string;
  base_name: string;
  model_file: string;
  parse_ok: boolean;
  supported: boolean;
  reason: string;
}

export interface AnimaWorkflowPayload {
  installed: boolean;
  activated: boolean;
  version: string;
  error: string;
  workflows: AnimaWorkflowRow[];
  selected: string;
  source: string;
  /** 参与轮换的名字（空名单时后端返回全部可用） */
  picked?: string[];
  usable?: number;
}

export interface WorkflowAnalyze {
  ok: boolean;
  errors: string[];
  problems: string[];
  warnings: string[];
  node_count: number;
  nodes: {
    positive: string;
    negative: string;
    latent: string;
    latent_class: string;
    save: string;
    save_class: string;
    model_file: string;
    default_width: number;
    default_height: number;
    seed_nodes: number;
    kind: string;
  };
}

export function apiWorkflows() {
  return apiGet<{
    templates: TemplatePublic[];
    selection_mode: string;
    workflows_dir: string;
    source: string;
    anima?: AnimaWorkflowPayload;
    rotation: RotationPayload;
  }>("/workflows");
}

/** 把顺序轮换重置到 #1（清空名字游标与老整数游标）。 */
export function apiResetRotation() {
  return apiPost<{ ok: boolean }>("/workflows/rotation/reset");
}

/** 只解析不入库（上传预览）。 */
export function apiWorkflowParse(jsonText: string) {
  return apiPost<WorkflowAnalyze>("/workflows/parse", { json_text: jsonText }, 30000);
}

/** 上传入库：落盘 + 写入模板清单。 */
export function apiWorkflowUpload(payload: {
  name: string;
  json_text: string;
  tag_style?: string;
  size_preset?: string;
  prompt_profile?: string;
  subject_scope?: string;
  default_negative?: string;
  enabled?: boolean;
}) {
  return apiPost<{
    ok: boolean;
    name: string;
    file: string;
    replaced: boolean;
    nodes: Record<string, any>;
    warnings: string[];
  }>("/workflows/upload", payload, 30000);
}

export function apiAnimaWorkflows() {
  return apiGet<AnimaWorkflowPayload>("/anima/workflows", 15000);
}

// ---------------------------------------------------------------- 词池 / 提示词

export interface ThemePoolCategory {
  name: string;
  subjects: string[];
  style_group: string;
  scope: string;
}

export interface ThemePoolPayload {
  categories: ThemePoolCategory[];
  layers: Record<string, any>;
}

export interface PromptPreviewStyle {
  key: string;
  label: string;
  prompt: string;
}

export interface PromptPreviewPayload {
  styles: PromptPreviewStyle[];
  persona_enabled: boolean;
}

/** 内置词池全貌（题材 / 风格组 / 可画类型 / 四个附加层）。 */
export function apiThemePool() {
  return apiGet<ThemePoolPayload>("/theme/pool", 15000);
}

/** 三种风格下当前真正生效的系统提示词（含追加规则与完全覆盖）。 */
export function apiPromptPreview() {
  return apiGet<PromptPreviewPayload>("/prompt/preview", 15000);
}

// ---------------------------------------------------------------- 配置

export function apiGetConfig() {
  return apiGet<{ items: Record<string, any>; schema: Record<string, any> }>("/config", 15000);
}

export function apiSaveConfig(items: Record<string, any>) {
  return apiPost<{ changed: string[] }>("/config", { items }, 20000);
}

export function apiProviders() {
  return apiGet<{ items: ProviderRow[]; default_id: string }>("/providers", 10000);
}

// ---------------------------------------------------------------- 关联萌绘

export function apiAnima() {
  return apiGet<AnimaInfo>("/anima", 15000);
}

export function apiAnimaSync(action: "follow" | "copy" | "off", serverKey?: string) {
  return apiPost<{ message: string; follow_server: boolean; server_key: string; effective_url: string; url_source: string }>(
    "/anima/sync",
    { action, server_key: serverKey || "" },
    15000,
  );
}

// ---------------------------------------------------------------- 调试

export function apiDebugEnv() {
  return apiGet<DebugEnv>("/debug/env", 15000);
}

export function apiDebugComfy(url = "") {
  return apiPost<ComfyProbe>("/debug/comfy", { url }, 30000);
}

export function apiDebugLlm(theme = "") {
  return apiPost<LlmPreview>("/debug/llm", { theme }, 120000);
}

export function apiDebugDelivery(umo: string, text = "") {
  return apiPost<{ ok: boolean; error: string }>("/debug/delivery", { umo, text }, 30000);
}

export function apiDebugLogs(limit = 120) {
  return apiGet<{ rows: DebugLogRow[] }>(`/debug/logs?limit=${limit}`, 15000);
}
