<script setup lang="ts">
// 萌萌绘卷 · 图库 WebUI（plugin Page，sandbox iframe 内运行）
// 硬约束（AGENTS.md 第八节 + 同族踩坑记录）：
//  - 不碰 localStorage/cookie（bridge.ts 有安全封装，本页干脆不用持久化）；
//  - 不用原生 confirm/alert（删除用 n-popconfirm 两段确认）；
//  - 图片一律走 /thumb 的 data URL（直链在沙箱 iframe 不可靠，anima 同款结论）。
import { computed, onMounted, onUnmounted, reactive, ref } from "vue";
import {
  NButton,
  NConfigProvider,
  NDescriptions,
  NDescriptionsItem,
  NDrawer,
  NDrawerContent,
  NEmpty,
  NInput,
  NPagination,
  NPopconfirm,
  NSelect,
  NSpin,
  NSwitch,
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
  apiRecords,
  apiResend,
  apiStar,
  apiStats,
  apiStatus,
  apiThumb,
  type GenRecord,
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
    if (!d.results?.length) message.info("投递名单为空（先在配置或指令里添加会话）");
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

      <!-- 筛选条 -->
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

      <!-- 网格 -->
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
.mono {
  font-family: ui-monospace, monospace;
  font-size: 11.5px;
}
.clickable {
  cursor: pointer;
}
.spin-box {
  min-height: 120px;
}
</style>
