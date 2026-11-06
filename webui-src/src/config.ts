// 配置编辑的共享层：schema 展平值 + 分区保存。
// 规则（AGENTS.md + box v0.12.7 教训）：只提交本卡片涉及的键（扁平点号键），
// 绝不整份回传；后端先整体校验再统一写入，未知键直接报错。
import { computed, reactive, ref } from "vue";
import {
  apiGetConfig,
  apiProviders,
  apiSaveConfig,
  type ProviderRow,
} from "./api";

export const cfgItems = ref<Record<string, any>>({});
export const cfgSchema = ref<Record<string, any>>({});
export const providers = ref<ProviderRow[]>([]);
export const cfgLoading = ref(false);
export const cfgLoaded = ref(false);
export const cfgError = ref("");
export const saving = reactive<Record<string, boolean>>({});

/** 所有页面已经显式覆盖的配置键；不在此表里的键会在「其他」卡片里兜底展示。 */
export const KNOWN_KEYS = new Set<string>([
  "enabled",
  "comfyui.url",
  "comfyui.draw_timeout",
  "comfyui.queue_extra_timeout",
  "comfyui.hard_cap",
  "comfyui.poll_interval",
  "comfyui.queue_defer_threshold",
  "llm.provider_id",
  "llm.timeout",
  "prompt.persona_enabled",
  "prompt.persona_text",
  "prompt.persona_tags",
  "prompt.persona_negative",
  "prompt.persona_negative_zh",
  "prompt.extra_rules",
  "prompt.style_anchor_enabled",
  "prompt.override_danbooru",
  "prompt.override_natural",
  "prompt.override_natural_en",
  "schedule.mode",
  "schedule.interval_minutes",
  "schedule.jitter_minutes",
  "schedule.daily_times",
  "schedule.quiet_enabled",
  "schedule.quiet_hours",
  "schedule.max_per_day",
  "schedule.paused_manual",
  "anima.follow_server",
  "anima.server_key",
  "anima.workflow",
  "anima.size_preset",
  "anima.prompt_profile",
  "anima.subject_scope",
  "anima.workflow_scopes",
  "workflow_source",
  "workflow_selection",
  "rotation_manual_advances",
  "manual_draw_admin_only",
  "manual_draw_cooldown_sec",
  "workflow_templates",
  "theme.disabled_categories",
  "theme.extra_words",
  "theme.category_weights",
  "theme.category_scopes",
  "theme.character_names",
  "delivery.sessions",
  "delivery.caption_on",
  "delivery.caption_template",
  "gallery.max_total_mb",
  "admin_notify_umo",
]);

export function cfgGet(key: string, dflt: any = ""): any {
  const v = cfgItems.value[key];
  return v === undefined || v === null ? dflt : v;
}

export function cfgSet(key: string, v: any): void {
  cfgItems.value[key] = v;
}

export function cfgNum(key: string, dflt: number): number {
  const n = Number(cfgItems.value[key]);
  return Number.isFinite(n) ? n : dflt;
}

/** 按点号键取 schema 节点（object 的子键挂在 items 下）。 */
export function schemaOf(key: string): any {
  const parts = String(key || "").split(".");
  let cur: any = cfgSchema.value?.[parts[0]];
  for (let i = 1; i < parts.length && cur; i += 1) {
    cur = cur?.items?.[parts[i]];
  }
  return cur || {};
}

export function cfgBool(key: string, dflt = false): boolean {
  const v = cfgItems.value[key];
  if (v === undefined || v === null) return dflt;
  if (typeof v === "boolean") return v;
  return ["1", "true", "on", "yes"].includes(String(v).toLowerCase());
}

export function linesToText(v: any): string {
  return Array.isArray(v) ? v.join("\n") : String(v ?? "");
}

export function splitLines(s: string): string[] {
  return String(s)
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean);
}

/** 未在本页显式编排的配置键（schema 里存在但 KNOWN_KEYS 没收录）。 */
export const unknownKeys = computed<string[]>(() => {
  const out: string[] = [];
  for (const [key, spec] of Object.entries(cfgSchema.value || {})) {
    const t = String((spec as any)?.type || "");
    if (t === "object") {
      for (const f of Object.keys((spec as any)?.items || {})) {
        const full = `${key}.${f}`;
        if (!KNOWN_KEYS.has(full)) out.push(full);
      }
    } else if (!KNOWN_KEYS.has(key)) {
      out.push(key);
    }
  }
  return out;
});

export async function loadConfig(force = false): Promise<void> {
  if (cfgLoading.value) return;
  if (cfgLoaded.value && !force) return;
  cfgLoading.value = true;
  cfgError.value = "";
  try {
    const d = await apiGetConfig();
    cfgItems.value = d.items || {};
    cfgSchema.value = d.schema || {};
    cfgLoaded.value = true;
  } catch (e: any) {
    cfgError.value = e?.message || String(e);
  } finally {
    cfgLoading.value = false;
  }
  try {
    const p = await apiProviders();
    providers.value = p.items || [];
  } catch {
    /* provider 下拉退化成手填 */
  }
}

/** 保存本分区的键；成功后按需刷新全局状态。 */
export async function saveFields(
  fields: string[],
  label: string,
): Promise<{ ok: boolean; changed: number; error: string }> {
  const items: Record<string, any> = {};
  for (const f of fields) items[f] = cfgItems.value[f];
  saving[label] = true;
  try {
    const d = await apiSaveConfig(items);
    return { ok: true, changed: d.changed?.length ?? 0, error: "" };
  } catch (e: any) {
    return { ok: false, changed: 0, error: e?.message || String(e) };
  } finally {
    saving[label] = false;
  }
}
