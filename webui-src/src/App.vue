<script setup lang="ts">
// 萌萌绘卷 · 图库 + 设置 WebUI（plugin Page，sandbox iframe 内运行）
// 硬约束（AGENTS.md 第八节 + 同族踩坑记录）：
//  - 不碰 localStorage/cookie（bridge.ts 有安全封装，本页干脆不用持久化）；
//  - 不用原生 confirm/alert（删除用 n-popconfirm 两段确认）；
//  - 图片一律走 /thumb 的 data URL（直链在沙箱 iframe 不可靠，anima 同款结论）；
//  - 配置保存走「扁平点号键 + 后端白名单」（user_gateway 模式），只提交本卡片字段，
//    绝不把整份配置回传——防 box v0.12.7「一次坏保存清空配置」。
import { computed, onMounted, onUnmounted, reactive, ref } from "vue";
import {
  NButton,
  NCard,
  NConfigProvider,
  NDescriptions,
  NDescriptionsItem,
  NDrawer,
  NDrawerContent,
  NEmpty,
  NInput,
  NInputNumber,
  NPagination,
  NPopconfirm,
  NSelect,
  NSpin,
  NSwitch,
  NTabPane,
  NTabs,
  NTag,
  createDiscreteApi,
  darkTheme,
  dateZhCN,
  zhCN,
} from "naive-ui";
import { getContext, onContext } from "./bridge";
import {
  apiControl,
  apiDeleteRecord,
  apiGetConfig,
  apiProviders,
  apiRecords,
  apiResend,
  apiSaveConfig,
  apiStar,
  apiStats,
  apiStatus,
  apiThumb,
  apiWorkflows,
  type GenRecord,
  type ProviderRow,
  type StatsPayload,
  type StatusPayload,
} from "./api";

// ---------- 主题（bridge context 下发，绝不读 localStorage） ----------
const dark = ref<boolean>(!!getContext()?.isDark);
onContext((ctx) => {
  dark.value = !!ctx?.isDark;
});
const theme = computed(() => (dark.value ? darkTheme : null));
const { message } = createDiscreteApi(["message"], {
  configProviderProps: computed(() => ({ theme: theme.value })),
});

// ---------- 状态 ----------
const activeTab = ref<string>("gallery");
const loading = ref(false);
const status = ref<StatusPayload | null>(null);
const stats = ref<StatsPayload | null>(null);
const records = ref<GenRecord[]>([]);
const total = ref(0);
const thumbs = reactive<Record<number, string>>({});
const page = ref(1);
const pageSize = ref(24);

const fStatus = ref("");
const fWorkflow = ref("");
const fCategory = ref("");
const fKeyword = ref("");
const fKeywordInput = ref("");
const fStarred = ref(false);

const detail = ref<GenRecord | null>(null);
const detailShow = ref(false);
const detailLoading = ref(false);
const detailBig = ref("");

const triggerTheme = ref("");
const triggerLoading = ref(false);
let pollTimer: ReturnType<typeof setInterval> | null = null;

const workflowOptions = computed(() => {
  const list = (status.value?.templates || [])
    .filter((t) => t.enabled && t.ok)
    .map((t) => ({ label: t.name, value: t.name }));
  return [{ label: "全部工作流", value: "" }, ...list];
});
const categoryOptions = computed(() => {
  const set = new Set<string>();
  for (const c of stats.value?.by_category || []) if (c.category) set.add(c.category);
  for (const r of records.value) if (r.category) set.add(r.category);
  return [{ label: "全部类别", value: "" }, ...[...set].sort().map((c) => ({ label: c, value: c }))];
});
const statusOptions = [
  { label: "全部状态", value: "" },
  { label: "成功", value: "success" },
  { label: "失败", value: "failed" },
  { label: "进行中", value: "running" },
];

const schedTag = computed(() => {
  const s = status.value;
  if (!s) return { text: "…", type: "default" as const };
  if (!s.enabled) return { text: "总开关关闭", type: "default" as const };
  if (s.auto_paused) return { text: "自动暂停", type: "error" as const };
  if (s.paused_manual) return { text: "已暂停", type: "warning" as const };
  return { text: "调度运行中", type: "success" as const };
});

// ---------- 工具 ----------
function fmtTs(ts: number | null | undefined): string {
  if (!ts) return "-";
  const d = new Date(ts * 1000);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
function fmtDur(ms: number | null | undefined): string {
  if (ms == null) return "-";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}
function parseBrief(brief: string): Record<string, string> {
  try {
    const v = JSON.parse(brief || "{}");
    return typeof v === "object" && v ? v : {};
  } catch {
    return {};
  }
}
function briefOf(rec: GenRecord): string {
  try {
    const b = JSON.parse(rec.brief || "{}");
    if (b && typeof b === "object") {
      return `题材=${b.subject ?? "-"}；风格=${b.style ?? "-"}；构图=${b.composition ?? "-"}；光色=${b.light ?? "-"}；氛围=${b.mood ?? "-"}`;
    }
  } catch {
    /* 原样展示 */
  }
  return rec.brief || "-";
}
function onPageSizeChange(n: number) {
  pageSize.value = n;
  page.value = 1;
  loadRecords();
}
async function copyText(text: string, label: string) {
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
    message.success(`已复制${label}`);
  } catch {
    // 沙箱 iframe 里 clipboard 可能被禁：退回手动复制（textarea 全选）
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try {
      const okNow = document.execCommand("copy");
      message[okNow ? "success" : "warning"](okNow ? `已复制${label}` : `复制失败，请手动选择${label}文本`);
    } catch {
      message.warning(`复制失败，请手动选择${label}文本`);
    }
    document.body.removeChild(ta);
  }
}

// ---------- 数据加载 ----------
async function refreshStatus() {
  try {
    status.value = await apiStatus();
  } catch (e: any) {
    console.warn("status", e);
  }
}
async function refreshStats() {
  try {
    stats.value = await apiStats();
  } catch (e: any) {
    console.warn("stats", e);
  }
}
async function loadThumbs(rows: GenRecord[]) {
  const queue = rows.filter((r) => r.status === "success" && r.image_relpath && !thumbs[r.id]);
  const workers = Array.from({ length: 6 }, async () => {
    while (queue.length) {
      const r = queue.shift()!;
      try {
        const d = await apiThumb(r.id, 320);
        thumbs[r.id] = d.data_url || "";
      } catch {
        thumbs[r.id] = "";
      }
    }
  });
  await Promise.all(workers);
}
async function loadRecords() {
  loading.value = true;
  try {
    const f = {
      offset: (page.value - 1) * pageSize.value,
      limit: pageSize.value,
      status: fStatus.value,
      workflow: fWorkflow.value,
      category: fCategory.value,
      keyword: fKeyword.value,
      starred: fStarred.value ? 1 : undefined,
    };
    const d = await apiRecords(f);
    total.value = d.total;
    records.value = d.rows;
    await loadThumbs(d.rows);
  } catch (e: any) {
    message.error(`加载记录失败：${e?.message || e}`);
  } finally {
    loading.value = false;
  }
}
async function refreshAll() {
  await Promise.all([refreshStatus(), refreshStats()]);
  await loadRecords();
}
function applyFilters() {
  page.value = 1;
  loadRecords();
}
function applyKeyword() {
  fKeyword.value = fKeywordInput.value.trim();
  applyFilters();
}

// ---------- 操作 ----------
async function triggerDraw() {
  triggerLoading.value = true;
  try {
    const d = await apiControl("trigger", triggerTheme.value.trim());
    if (d.started) {
      message.success("已开始生成，画完会自动出现在图库（也会投递到白名单会话）");
      setTimeout(refreshAll, 2500);
    } else {
      message.warning("当前已有生成任务在进行，稍后再试");
    }
  } catch (e: any) {
    message.error(`触发失败：${e?.message || e}`);
  } finally {
    triggerLoading.value = false;
  }
}
async function togglePause() {
  const paused = status.value?.paused_manual || status.value?.auto_paused;
  try {
    await apiControl(paused ? "resume" : "pause");
    message.success(paused ? "调度已恢复" : "调度已暂停");
    await refreshStatus();
  } catch (e: any) {
    message.error(`操作失败：${e?.message || e}`);
  }
}
async function openDetail(rec: GenRecord) {
  detailShow.value = true;
  detailLoading.value = true;
  detailBig.value = "";
  detail.value = rec;
  try {
    const d = await apiThumb(rec.id, 1600);
    detailBig.value = d.data_url || "";
  } catch {
    detailBig.value = "";
  } finally {
    detailLoading.value = false;
  }
}
async function starToggle(rec: GenRecord) {
  try {
    await apiStar(rec.id, !rec.starred);
    rec.starred = !rec.starred ? 1 : 0;
    if (detail.value?.id === rec.id && detail.value) detail.value.starred = rec.starred;
  } catch (e: any) {
    message.error(`收藏失败：${e?.message || e}`);
  }
}
async function resend(rec: GenRecord) {
  try {
    const d = await apiResend(rec.id);
    const okN = (d.results || []).filter((r) => r.ok).length;
    if (!d.results?.length) message.info("投递名单为空（先在设置页或指令里添加会话）");
    else if (okN === d.results.length) message.success(`已重投 ${okN} 个会话`);
    else message.warning(`重投完成：成功 ${okN} / ${d.results.length}（详情看投递状态）`);
    await loadRecords();
  } catch (e: any) {
    message.error(`重投失败：${e?.message || e}`);
  }
}
async function del(rec: GenRecord) {
  try {
    await apiDeleteRecord(rec.id);
    message.success("已删除记录");
    detailShow.value = false;
    delete thumbs[rec.id];
    await Promise.all([loadRecords(), refreshStats(), refreshStatus()]);
  } catch (e: any) {
    message.error(`删除失败：${e?.message || e}`);
  }
}

// ------------------------------------------------------------------ //
// 设置页（配置 / 模板编辑器）
// ------------------------------------------------------------------ //

const cfgLoading = ref(false);
const cfgLoaded = ref(false);
const cfgItems = ref<Record<string, any>>({});
const providers = ref<ProviderRow[]>([]);
const saving = reactive<Record<string, boolean>>({});

interface TmplRow {
  name: string;
  file: string;
  tag_style: string;
  size_preset: string;
  default_negative: string;
  enabled: boolean;
  positive_node: string;
  negative_node: string;
  latent_node: string;
  positive_field: string;
  negative_field: string;
  ok?: boolean;
  problem?: string;
}
const tmplRows = ref<TmplRow[]>([]);

const modeOptions = [
  { label: "固定间隔（每 N 分钟）", value: "interval" },
  { label: "每日时刻表", value: "daily" },
];
const styleOptions = [
  { label: "danbooru（动漫英文标签）", value: "danbooru" },
  { label: "natural（写实中文自然语言）", value: "natural" },
];
const presetOptions = [
  { label: "sdxl（1216×832 / 832×1216 / 1024²）", value: "sdxl" },
  { label: "flux（1344×768 / 768×1344 / 1024²）", value: "flux" },
  { label: "sd15（768×512 / 512×768 / 512²）", value: "sd15" },
];
const providerOptions = computed(() => [
  { label: "全局默认提供商", value: "" },
  ...providers.value.map((p) => ({
    label: p.label + (p.is_default ? "（AstrBot 默认）" : ""),
    value: p.id,
  })),
]);

function cfgGet(key: string, dflt: any = ""): any {
  const v = cfgItems.value[key];
  return v === undefined || v === null ? dflt : v;
}
function cfgSet(key: string, v: any) {
  cfgItems.value[key] = v;
}
function cfgNum(key: string, dflt: number): number {
  const n = Number(cfgItems.value[key]);
  return Number.isFinite(n) ? n : dflt;
}
function linesToText(v: any): string {
  return Array.isArray(v) ? v.join("\n") : String(v ?? "");
}
const sessionsText = computed({
  get: () => linesToText(cfgItems.value["delivery.sessions"]),
  set: (s: string) => {
    cfgItems.value["delivery.sessions"] = String(s)
      .split("\n")
      .map((l) => l.trim())
      .filter(Boolean);
  },
});
const extraWordsText = computed({
  get: () => {
    const d = cfgItems.value["theme.extra_words"];
    if (!d || typeof d !== "object") return "";
    return Object.entries(d)
      .map(([k, v]) => `${k}=${v}`)
      .join("\n");
  },
  set: (s: string) => {
    const out: Record<string, string> = {};
    for (const line of String(s).split("\n")) {
      const i = line.indexOf("=");
      if (i > 0) {
        const k = line.slice(0, i).trim();
        const v = line.slice(i + 1).trim();
        if (k) out[k] = v;
      }
    }
    cfgItems.value["theme.extra_words"] = out;
  },
});

function normalizeRow(r: any): TmplRow {
  return {
    name: String(r.name || ""),
    file: String(r.file || ""),
    tag_style: String(r.tag_style || "danbooru"),
    size_preset: String(r.size_preset || "sdxl"),
    default_negative: String(r.default_negative || ""),
    enabled: r.enabled !== false,
    positive_node: String(r.positive_node || ""),
    negative_node: String(r.negative_node || ""),
    latent_node: String(r.latent_node || ""),
    positive_field: String(r.positive_field || ""),
    negative_field: String(r.negative_field || ""),
    ok: undefined,
    problem: "",
  };
}
async function mergeTmplValidation() {
  try {
    const w = await apiWorkflows();
    const byKey = new Map(w.templates.map((t) => [`${t.name}|${t.file}`, t]));
    for (const r of tmplRows.value) {
      const t = byKey.get(`${r.name}|${r.file}`);
      if (t) {
        r.ok = t.ok;
        r.problem = t.problem || "";
      }
    }
  } catch {
    /* 校验信息拿不到就不显示 */
  }
}
async function loadConfig() {
  cfgLoading.value = true;
  try {
    const d = await apiGetConfig();
    cfgItems.value = d.items || {};
    tmplRows.value = ((d.items || {}).workflow_templates || []).map(normalizeRow);
    await mergeTmplValidation();
    try {
      const p = await apiProviders();
      providers.value = p.items || [];
    } catch {
      /* provider 下拉退化成手填 */
    }
    cfgLoaded.value = true;
  } catch (e: any) {
    message.error(`加载配置失败：${e?.message || e}`);
  } finally {
    cfgLoading.value = false;
  }
}
function onTabChange(name: string) {
  if (name === "settings" && !cfgLoaded.value && !cfgLoading.value) loadConfig();
}
async function saveSection(fields: string[], label: string) {
  const items: Record<string, any> = {};
  for (const f of fields) items[f] = cfgItems.value[f];
  saving[label] = true;
  try {
    const d = await apiSaveConfig(items);
    message.success(`${label}已保存（${d.changed?.length ?? 0} 项）`);
    await refreshStatus();
  } catch (e: any) {
    message.error(`保存失败：${e?.message || e}`);
  } finally {
    saving[label] = false;
  }
}
async function saveTemplates() {
  const entries = tmplRows.value.map((r) => ({
    name: r.name.trim(),
    file: r.file.trim(),
    tag_style: r.tag_style,
    size_preset: r.size_preset,
    default_negative: r.default_negative,
    enabled: r.enabled,
    positive_node: r.positive_node.trim(),
    negative_node: r.negative_node.trim(),
    latent_node: r.latent_node.trim(),
    positive_field: r.positive_field.trim(),
    negative_field: r.negative_field.trim(),
  }));
  saving["模板"] = true;
  try {
    await apiSaveConfig({ workflow_templates: entries });
    message.success("模板清单已保存");
    await Promise.all([loadConfig(), refreshStatus()]);
  } catch (e: any) {
    message.error(`保存失败：${e?.message || e}`);
  } finally {
    saving["模板"] = false;
  }
}
function addTemplate() {
  tmplRows.value.push(normalizeRow({ name: "新模板", file: "", enabled: true }));
}
function removeTemplate(i: number) {
  tmplRows.value.splice(i, 1);
}

onMounted(async () => {
  await refreshAll();
  pollTimer = setInterval(async () => {
    await refreshStatus();
    await refreshStats();
    // 有进行中的记录时把列表也刷一下（让「生成中」变成结果）
    if (records.value.some((r) => r.status === "running")) await loadRecords();
  }, 30000);
});
onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer);
});
</script>

<template>
  <n-config-provider :theme="theme" :locale="zhCN" :date-locale="dateZhCN">
    <div class="page" :class="{ dark }">
      <!-- 顶栏 -->
      <header class="head">
        <div class="brand">
          <span class="logo-dot"></span>
          <h1>萌萌绘卷</h1>
          <n-tag :type="schedTag.type" size="small" round>{{ schedTag.text }}</n-tag>
        </div>
        <div class="actions">
          <n-input
            v-model:value="triggerTheme"
            placeholder="主题词（可空 = 随机抽签）"
            size="small"
            style="width: 200px"
            @keyup.enter="triggerDraw"
          />
          <n-button size="small" type="primary" :loading="triggerLoading" @click="triggerDraw">
            立即画一张
          </n-button>
          <n-button size="small" @click="togglePause">
            {{ status?.paused_manual || status?.auto_paused ? "恢复调度" : "暂停调度" }}
          </n-button>
        </div>
      </header>

      <!-- 状态条 -->
      <section class="statusbar">
        <span v-if="status?.next_due">下次生成：{{ status.next_due.slice(5, 16).replace("T", " ") }}</span>
        <span>今日 {{ status?.today_success ?? 0 }} / 上限 {{ status?.max_per_day ? status.max_per_day : "∞" }}</span>
        <span>成功 {{ status?.counts.success ?? 0 }} · 失败 {{ status?.counts.failed ?? 0 }}</span>
        <n-tag size="small" :type="status?.comfy?.ok ? 'success' : 'error'" round>
          ComfyUI {{ status?.comfy?.ok ? `已连接 ${status.comfy.elapsed_ms}ms` : "未连接" }}
        </n-tag>
        <n-tag size="small" :type="status?.llm?.found ? 'success' : 'warning'" round>
          LLM：{{ status?.llm?.label || "-" }}
        </n-tag>
        <n-tag size="small" round>投递会话 {{ status?.delivery_sessions ?? 0 }}</n-tag>
        <span v-if="status?.last_run_note" class="muted">{{ status.last_run_note }}</span>
      </section>

      <n-tabs v-model:value="activeTab" type="line" @update:value="onTabChange">
        <!-- ============ 图库 ============ -->
        <n-tab-pane name="gallery" tab="图库">
          <section class="filters">
            <n-select v-model:value="fStatus" :options="statusOptions" size="small" style="width: 110px" @update:value="applyFilters" />
            <n-select v-model:value="fWorkflow" :options="workflowOptions" size="small" style="width: 160px" @update:value="applyFilters" />
            <n-select v-model:value="fCategory" :options="categoryOptions" size="small" style="width: 140px" filterable @update:value="applyFilters" />
            <n-input v-model:value="fKeywordInput" placeholder="搜提示词/文案" size="small" style="width: 170px" @keyup.enter="applyKeyword" />
            <n-button size="small" @click="applyKeyword">搜索</n-button>
            <label class="star-switch">
              <n-switch v-model:value="fStarred" size="small" @update:value="applyFilters" />
              <span>只看收藏</span>
            </label>
            <n-button size="small" quaternary @click="refreshAll">刷新</n-button>
          </section>

          <n-spin :show="loading">
            <n-empty
              v-if="!records.length && !loading"
              description="还没有记录 —— 点右上角「立即画一张」开张，或等调度到点"
              style="margin: 60px 0"
            />
            <div v-else class="grid">
              <div
                v-for="r in records"
                :key="r.id"
                class="gcard"
                :class="{ failed: r.status === 'failed', running: r.status === 'running' }"
                @click="openDetail(r)"
              >
                <div class="thumb">
                  <img v-if="r.status === 'success' && thumbs[r.id]" :src="thumbs[r.id]" alt="" loading="lazy" />
                  <div v-else-if="r.status === 'success'" class="ph spin-box"><n-spin size="small" /></div>
                  <div v-else-if="r.status === 'running'" class="ph"><n-spin size="small" /><span>生成中…</span></div>
                  <div v-else class="ph ph-fail"><span>失败</span></div>
                  <span v-if="r.starred" class="star">★</span>
                </div>
                <div class="meta">
                  <div class="cap">{{ r.caption || parseBrief(r.brief).subject || `#${r.id}` }}</div>
                  <div class="sub muted">
                    {{ r.workflow || "?" }}
                    <template v-if="r.status === 'success'"> · {{ r.width }}×{{ r.height }} · {{ fmtTs(r.ts) }}</template>
                    <template v-else-if="r.status === 'failed'"> · {{ fmtTs(r.ts) }}</template>
                  </div>
                  <div v-if="r.status === 'failed'" class="errmsg">{{ r.error }}</div>
                </div>
              </div>
            </div>
          </n-spin>

          <div class="pager">
            <n-pagination
              v-model:page="page"
              :item-count="total"
              :page-size="pageSize"
              :page-sizes="[24, 48, 96]"
              show-size-picker
              @update:page="loadRecords"
              @update:page-size="onPageSizeChange"
            />
            <span class="muted">共 {{ total }} 条</span>
          </div>
        </n-tab-pane>

        <!-- ============ 设置 ============ -->
        <n-tab-pane name="settings" tab="设置">
          <n-spin :show="cfgLoading">
            <div class="cards">
              <!-- 调度 -->
              <n-card title="调度" size="small">
                <template #header-extra>
                  <n-button size="small" type="primary" :loading="!!saving['调度']" @click="saveSection(['enabled', 'schedule.mode', 'schedule.interval_minutes', 'schedule.jitter_minutes', 'schedule.daily_times', 'schedule.quiet_hours', 'schedule.max_per_day'], '调度')">
                    保存
                  </n-button>
                </template>
                <div class="fgrid">
                  <label class="field"><span>总开关</span><n-switch :value="!!cfgGet('enabled', true)" @update:value="(v) => cfgSet('enabled', v)" /></label>
                  <label class="field"><span>模式</span>
                    <n-select :value="cfgGet('schedule.mode', 'interval')" :options="modeOptions" size="small" @update:value="(v) => cfgSet('schedule.mode', v)" />
                  </label>
                  <label class="field"><span>间隔分钟（最低 1）</span>
                    <n-input-number :value="cfgNum('schedule.interval_minutes', 360)" :min="1" :max="43200" size="small" @update:value="(v) => cfgSet('schedule.interval_minutes', v ?? 360)" />
                  </label>
                  <label class="field"><span>抖动分钟</span>
                    <n-input-number :value="cfgNum('schedule.jitter_minutes', 30)" :min="0" :max="1440" size="small" @update:value="(v) => cfgSet('schedule.jitter_minutes', v ?? 0)" />
                  </label>
                  <label class="field"><span>每日时刻表（逗号分隔）</span>
                    <n-input :value="String(cfgGet('schedule.daily_times', '09:30,20:00'))" size="small" placeholder="09:30,20:00" @update:value="(v) => cfgSet('schedule.daily_times', v)" />
                  </label>
                  <label class="field"><span>安静时段</span>
                    <n-input :value="String(cfgGet('schedule.quiet_hours', '01:00-08:00'))" size="small" placeholder="01:00-08:00，留空关闭" @update:value="(v) => cfgSet('schedule.quiet_hours', v)" />
                  </label>
                  <label class="field"><span>每日上限（0 = 不限）</span>
                    <n-input-number :value="cfgNum('schedule.max_per_day', 4)" :min="0" :max="1000" size="small" @update:value="(v) => cfgSet('schedule.max_per_day', v ?? 0)" />
                  </label>
                </div>
                <div class="hint">手动「立即画一张」不受每日上限约束；间隔低于单张耗时时自然退化为连画，不会堆积并发。</div>
              </n-card>

              <!-- ComfyUI -->
              <n-card title="ComfyUI 服务器" size="small">
                <template #header-extra>
                  <n-button size="small" type="primary" :loading="!!saving['ComfyUI']" @click="saveSection(['comfyui.url', 'comfyui.draw_timeout', 'comfyui.queue_extra_timeout', 'comfyui.hard_cap', 'comfyui.poll_interval', 'comfyui.queue_defer_threshold'], 'ComfyUI')">
                    保存
                  </n-button>
                </template>
                <div class="fgrid">
                  <label class="field wide"><span>服务器地址</span>
                    <n-input :value="String(cfgGet('comfyui.url', 'http://127.0.0.1:8188'))" size="small" @update:value="(v) => cfgSet('comfyui.url', v)" />
                  </label>
                  <label class="field"><span>队列避让阈值（0 = 关）</span>
                    <n-input-number :value="cfgNum('comfyui.queue_defer_threshold', 2)" :min="0" :max="100" size="small" @update:value="(v) => cfgSet('comfyui.queue_defer_threshold', v ?? 0)" />
                  </label>
                  <label class="field"><span>基础等待（秒）</span>
                    <n-input-number :value="cfgNum('comfyui.draw_timeout', 180)" :min="30" :max="3600" size="small" @update:value="(v) => cfgSet('comfyui.draw_timeout', v ?? 180)" />
                  </label>
                  <label class="field"><span>每任务排队加时（秒）</span>
                    <n-input-number :value="cfgNum('comfyui.queue_extra_timeout', 60)" :min="0" :max="3600" size="small" @update:value="(v) => cfgSet('comfyui.queue_extra_timeout', v ?? 60)" />
                  </label>
                  <label class="field"><span>等待硬上限（秒）</span>
                    <n-input-number :value="cfgNum('comfyui.hard_cap', 600)" :min="30" :max="14400" size="small" @update:value="(v) => cfgSet('comfyui.hard_cap', v ?? 600)" />
                  </label>
                  <label class="field"><span>轮询间隔（秒）</span>
                    <n-input-number :value="cfgNum('comfyui.poll_interval', 2)" :min="1" :max="30" size="small" @update:value="(v) => cfgSet('comfyui.poll_interval', v ?? 2)" />
                  </label>
                </div>
                <div class="hint">与萌绘共用服务器时，定时出图提交前查远程队列：前方 ≥ 阈值个任务就顺延 5 分钟，给交互出图让路。</div>
              </n-card>

              <!-- 提示词模型 -->
              <n-card title="提示词模型（LLM）" size="small">
                <template #header-extra>
                  <n-button size="small" type="primary" :loading="!!saving['提示词模型']" @click="saveSection(['llm.provider_id', 'llm.timeout'], '提示词模型')">
                    保存
                  </n-button>
                </template>
                <div class="fgrid">
                  <label class="field"><span>提供商（一提供商 = 一模型）</span>
                    <n-select :value="String(cfgGet('llm.provider_id', ''))" :options="providerOptions" size="small" filterable tag :consistent-menu-width="false" @update:value="(v) => cfgSet('llm.provider_id', v)" />
                  </label>
                  <label class="field"><span>调用超时（秒）</span>
                    <n-input-number :value="cfgNum('llm.timeout', 45)" :min="10" :max="300" size="small" @update:value="(v) => cfgSet('llm.timeout', v ?? 45)" />
                  </label>
                </div>
                <div class="hint">下拉来自 AstrBot 已加载的提供商；不在名单里的 id 可手输（tag 模式）。留空 = 全局默认提供商。</div>
              </n-card>

              <!-- 投递 -->
              <n-card title="投递" size="small">
                <template #header-extra>
                  <n-button size="small" type="primary" :loading="!!saving['投递']" @click="saveSection(['delivery.sessions', 'delivery.caption_on', 'delivery.caption_template'], '投递')">
                    保存
                  </n-button>
                </template>
                <div class="fgrid">
                  <label class="field wide"><span>投递会话 UMO（每行一个；空 = 只入库不投递）</span>
                    <n-input :value="sessionsText" type="textarea" :rows="3" size="small" placeholder="aiocqhttp:GroupMessage:123456" @update:value="(v) => (sessionsText = v)" />
                  </label>
                  <label class="field"><span>附带文案</span><n-switch :value="!!cfgGet('delivery.caption_on', true)" @update:value="(v) => cfgSet('delivery.caption_on', v)" /></label>
                  <label class="field"><span>文案模板</span>
                    <n-input :value="String(cfgGet('delivery.caption_template', '{caption}'))" size="small" placeholder="{caption}｜{category}" @update:value="(v) => cfgSet('delivery.caption_template', v)" />
                  </label>
                </div>
                <div class="hint">推荐在群里用指令管理：/绘卷 投递 开启。占位符可用 {caption} {category} {subject} {workflow}。</div>
              </n-card>

              <!-- 主题词池 -->
              <n-card title="主题词池" size="small">
                <template #header-extra>
                  <n-button size="small" type="primary" :loading="!!saving['主题词池']" @click="saveSection(['theme.disabled_categories', 'theme.extra_words'], '主题词池')">
                    保存
                  </n-button>
                </template>
                <div class="fgrid">
                  <label class="field"><span>停用类别（逗号分隔）</span>
                    <n-input :value="String(cfgGet('theme.disabled_categories', ''))" size="small" placeholder="人物肖像,自然生态" @update:value="(v) => cfgSet('theme.disabled_categories', v)" />
                  </label>
                  <label class="field wide"><span>词池扩充（每行一条：键=词1,词2…）</span>
                    <n-input :value="extraWordsText" type="textarea" :rows="3" size="small" placeholder="宇宙星空=冰环行星的极夜,双月夜潮&#10;我的猫=橘猫打翻鱼缸&#10;风格=新海诚式光影" @update:value="(v) => (extraWordsText = v)" />
                  </label>
                </div>
                <div class="hint">键可以是内置类别名（追加题材）、新类别名，或 风格/光色/氛围；构图项格式 名称@landscape|portrait|square。</div>
              </n-card>

              <!-- 图库与管理 -->
              <n-card title="图库与管理" size="small">
                <template #header-extra>
                  <n-button size="small" type="primary" :loading="!!saving['图库与管理']" @click="saveSection(['gallery.max_total_mb', 'admin_notify_umo'], '图库与管理')">
                    保存
                  </n-button>
                </template>
                <div class="fgrid">
                  <label class="field"><span>图库容量上限（MB，0 = 不淘汰）</span>
                    <n-input-number :value="cfgNum('gallery.max_total_mb', 1024)" :min="0" :max="1048576" size="small" @update:value="(v) => cfgSet('gallery.max_total_mb', v ?? 1024)" />
                  </label>
                  <label class="field"><span>管理员通知 UMO（连续失败暂停时）</span>
                    <n-input :value="String(cfgGet('admin_notify_umo', ''))" size="small" placeholder="留空不通知" @update:value="(v) => cfgSet('admin_notify_umo', v)" />
                  </label>
                </div>
                <div class="hint">容量超限按最旧优先淘汰非收藏图；收藏（★）永不淘汰。</div>
              </n-card>

              <!-- 工作流模板 -->
              <n-card title="工作流模板" size="small">
                <template #header-extra>
                  <div class="row-gap">
                    <n-button size="small" @click="addTemplate">添加模板</n-button>
                    <n-button size="small" quaternary @click="mergeTmplValidation(); message.info('已刷新校验状态')">刷新校验</n-button>
                    <n-button size="small" type="primary" :loading="!!saving['模板']" @click="saveTemplates">保存模板清单</n-button>
                  </div>
                </template>
                <div class="hint" style="margin-bottom: 8px">
                  模板 = 一份 ComfyUI API 格式 JSON（放 data/plugin_data/astrbot_plugin_moe_paint/workflows/）+ 下面这些元信息。节点定位自动进行（KSampler 引用），定位不了的才填覆盖项；保存后自动重载并校验。
                </div>
                <n-empty v-if="!tmplRows.length" description="还没有模板" style="margin: 20px 0" />
                <div v-for="(r, i) in tmplRows" :key="i" class="tmpl">
                  <div class="tmpl-head">
                    <n-tag size="small" :type="r.ok === undefined ? 'default' : r.ok ? 'success' : 'error'">
                      {{ r.ok === undefined ? '未校验' : r.ok ? '校验通过' : '校验失败' }}
                    </n-tag>
                    <span class="mono">{{ r.file || '（未填文件名）' }}</span>
                    <n-popconfirm @positive-click="removeTemplate(i)">
                      <template #trigger>
                        <n-button size="tiny" type="error" quaternary>删除</n-button>
                      </template>
                      从清单里移除模板「{{ r.name }}」？（保存后生效，文件不会删）
                    </n-popconfirm>
                  </div>
                  <div v-if="r.ok === false" class="errmsg">{{ r.problem }}</div>
                  <div class="fgrid">
                    <label class="field"><span>名称</span>
                      <n-input v-model:value="r.name" size="small" />
                    </label>
                    <label class="field"><span>工作流文件名</span>
                      <n-input v-model:value="r.file" size="small" placeholder="anima_t2i.json" />
                    </label>
                    <label class="field"><span>标签风格</span>
                      <n-select v-model:value="r.tag_style" :options="styleOptions" size="small" />
                    </label>
                    <label class="field"><span>尺寸预设</span>
                      <n-select v-model:value="r.size_preset" :options="presetOptions" size="small" />
                    </label>
                    <label class="field wide"><span>默认负面词（留空用 JSON 里的）</span>
                      <n-input v-model:value="r.default_negative" size="small" />
                    </label>
                    <label class="field"><span>启用</span><n-switch v-model:value="r.enabled" size="small" /></label>
                    <label class="field"><span>正向节点 ID（可选）</span><n-input v-model:value="r.positive_node" size="small" placeholder="自动定位失败才填" /></label>
                    <label class="field"><span>负向节点 ID（可选）</span><n-input v-model:value="r.negative_node" size="small" /></label>
                    <label class="field"><span>Latent 节点 ID（可选）</span><n-input v-model:value="r.latent_node" size="small" /></label>
                    <label class="field"><span>正向输入框名（可选）</span><n-input v-model:value="r.positive_field" size="small" placeholder="非 text 时必填（如 prompt）" /></label>
                    <label class="field"><span>负向输入框名（可选）</span><n-input v-model:value="r.negative_field" size="small" /></label>
                  </div>
                </div>
              </n-card>
            </div>
          </n-spin>
        </n-tab-pane>
      </n-tabs>

      <!-- 详情抽屉 -->
      <n-drawer v-model:show="detailShow" :width="560" placement="right">
        <n-drawer-content :title="detail ? `记录 #${detail.id}` : ''" closable>
          <template v-if="detail">
            <div class="big-img">
              <n-spin :show="detailLoading">
                <img v-if="detailBig" :src="detailBig" alt="" />
                <n-empty v-else-if="!detailLoading" :description="detail.status === 'failed' ? '生成失败，无图片' : '无图片'" />
              </n-spin>
            </div>

            <div class="detail-actions">
              <n-button size="small" @click="starToggle(detail)">
                {{ detail.starred ? "★ 已收藏" : "☆ 收藏（防淘汰）" }}
              </n-button>
              <n-button size="small" :disabled="detail.status !== 'success'" @click="resend(detail)">
                重投白名单
              </n-button>
              <n-button size="small" @click="copyText(detail.prompt, '提示词')">复制提示词</n-button>
              <n-popconfirm @positive-click="del(detail)">
                <template #trigger>
                  <n-button size="small" type="error" quaternary>删除</n-button>
                </template>
                删除记录并清理图片文件（收藏也会被删，谨慎操作）？
              </n-popconfirm>
            </div>

            <n-descriptions v-if="detail.status === 'failed'" :column="1" size="small" bordered>
              <n-descriptions-item label="错误">{{ detail.error || "-" }}</n-descriptions-item>
            </n-descriptions>

            <n-descriptions :column="2" size="small" bordered>
              <n-descriptions-item label="状态">
                <n-tag size="small" :type="detail.status === 'success' ? 'success' : detail.status === 'failed' ? 'error' : 'info'">
                  {{ detail.status }}
                </n-tag>
              </n-descriptions-item>
              <n-descriptions-item label="时间">{{ fmtTs(detail.ts) }}</n-descriptions-item>
              <n-descriptions-item label="类别">{{ detail.category || "-" }}</n-descriptions-item>
              <n-descriptions-item label="工作流">{{ detail.workflow || "-" }}</n-descriptions-item>
              <n-descriptions-item label="尺寸">{{ detail.width && detail.height ? `${detail.width}×${detail.height}` : "-" }}</n-descriptions-item>
              <n-descriptions-item label="标签风格">{{ detail.tag_style }}</n-descriptions-item>
              <n-descriptions-item label="Seed">
                <span class="clickable" @click="copyText(String(detail.seed ?? ''), 'seed')">{{ detail.seed ?? "-" }}</span>
              </n-descriptions-item>
              <n-descriptions-item label="耗时">{{ fmtDur(detail.gen_latency_ms) }}（LLM {{ fmtDur(detail.llm_latency_ms) }}）</n-descriptions-item>
              <n-descriptions-item label="LLM">{{ detail.llm_provider || "-" }}</n-descriptions-item>
              <n-descriptions-item label="触发">{{ detail.reason }}</n-descriptions-item>
              <n-descriptions-item :span="2" label="ComfyUI 任务">{{ detail.comfy_prompt_id || "-" }}</n-descriptions-item>
            </n-descriptions>

            <div class="sect">创作简报</div>
            <div class="brief">{{ briefOf(detail) }}</div>

            <div class="sect">正向提示词</div>
            <n-input :value="detail.prompt" type="textarea" readonly :autos="{ minRows: 3, maxRows: 8 }" />
            <div class="sect">负向提示词</div>
            <n-input :value="detail.negative" type="textarea" readonly :autos="{ minRows: 2, maxRows: 4 }" />
            <div class="sect">投递文案</div>
            <n-input :value="detail.caption" type="textarea" readonly :autos="{ minRows: 1, maxRows: 3 }" />

            <div class="sect">投递状态</div>
            <div v-if="!detail.delivered?.length" class="muted">未投递（名单为空或失败前记录）</div>
            <div v-for="(d, i) in detail.delivered || []" :key="i" class="deliv">
              <n-tag size="small" :type="d.ok ? 'success' : 'error'">{{ d.ok ? "已送达" : "失败" }}</n-tag>
              <span class="mono">{{ d.umo }}</span>
              <span class="muted" v-if="!d.ok && d.error">：{{ d.error }}</span>
            </div>
          </template>
        </n-drawer-content>
      </n-drawer>
    </div>
  </n-config-provider>
</template>

<style scoped>
.page {
  padding: 16px 20px 32px;
  max-width: 1280px;
  margin: 0 auto;
}
.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
}
.brand {
  display: flex;
  align-items: center;
  gap: 10px;
}
.brand h1 {
  font-size: 18px;
  margin: 0;
  font-weight: 700;
}
.logo-dot {
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: linear-gradient(135deg, #f7c8dd, #bcd8f7);
  display: inline-block;
}
.actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.statusbar {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin: 10px 0 4px;
  font-size: 12.5px;
  opacity: 0.92;
}
.filters {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin: 8px 0 14px;
}
.star-switch {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12.5px;
  cursor: pointer;
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(190px, 1fr));
  gap: 12px;
  min-height: 120px;
}
.gcard {
  border-radius: 12px;
  overflow: hidden;
  cursor: pointer;
  background: rgba(255, 255, 255, 0.86);
  border: 1px solid rgba(190, 190, 205, 0.35);
  transition: transform 0.15s ease, box-shadow 0.15s ease;
}
.gcard:hover {
  transform: translateY(-2px);
  box-shadow: 0 6px 18px rgba(120, 120, 150, 0.18);
}
.dark .gcard {
  background: rgba(40, 40, 48, 0.9);
  border-color: rgba(90, 90, 105, 0.5);
}
.gcard.failed {
  border-color: rgba(230, 120, 120, 0.5);
}
.thumb {
  position: relative;
  background: rgba(0, 0, 0, 0.04);
  min-height: 120px;
}
.dark .thumb {
  background: rgba(255, 255, 255, 0.05);
}
.thumb img {
  width: 100%;
  display: block;
}
.ph {
  min-height: 120px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  font-size: 12px;
  color: rgba(128, 128, 140, 0.9);
}
.ph-fail span {
  color: #d88;
}
.star {
  position: absolute;
  top: 6px;
  right: 8px;
  color: #f6b73c;
  text-shadow: 0 1px 3px rgba(0, 0, 0, 0.4);
}
.meta {
  padding: 8px 10px 10px;
}
.cap {
  font-size: 12.5px;
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.sub {
  margin-top: 4px;
  font-size: 11.5px;
}
.errmsg {
  margin-top: 4px;
  font-size: 11px;
  color: #d88;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.muted {
  color: rgba(128, 128, 140, 0.9);
  font-size: 12px;
}
.pager {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 16px;
  justify-content: center;
}
/* ---------- 设置页 ---------- */
.cards {
  display: flex;
  flex-direction: column;
  gap: 14px;
  margin-top: 6px;
}
.fgrid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 10px 14px;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
}
.field.wide {
  grid-column: 1 / -1;
}
.hint {
  margin-top: 8px;
  font-size: 11.5px;
  color: rgba(128, 128, 140, 0.95);
  line-height: 1.6;
}
.tmpl {
  border: 1px solid rgba(190, 190, 205, 0.4);
  border-radius: 10px;
  padding: 10px 12px;
  margin-bottom: 10px;
}
.dark .tmpl {
  border-color: rgba(90, 90, 105, 0.55);
}
.tmpl-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
  font-size: 12px;
}
.row-gap {
  display: flex;
  gap: 8px;
}
.mono {
  font-family: ui-monospace, monospace;
  font-size: 11.5px;
}
.big-img img {
  max-width: 100%;
  border-radius: 10px;
  display: block;
}
.detail-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin: 12px 0;
}
.sect {
  font-size: 13px;
  font-weight: 600;
  margin: 14px 0 6px;
}
.brief {
  font-size: 12.5px;
  line-height: 1.6;
  padding: 8px 10px;
  border-radius: 8px;
  background: rgba(127, 127, 145, 0.08);
}
.deliv {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
  font-size: 12px;
}
.clickable {
  cursor: pointer;
}
.spin-box {
  min-height: 120px;
}
</style>
