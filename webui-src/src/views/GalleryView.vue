<script setup lang="ts">
// 图库：筛选 + 网格 + 详情抽屉 + 全屏看图。图片一律走 /thumb（data URL），
// 直链在 sandbox iframe 里不可靠（anima 同款结论）。
// v0.7.0：自动刷新（有新图静默插入）+ 全屏大图（缩放/拖动/左右切换）。
import { computed, onMounted, reactive, ref } from "vue";
import {
  NButton,
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
  useMessage,
} from "naive-ui";
import {
  apiDeleteRecord,
  apiRecords,
  apiResend,
  apiStar,
  apiThumb,
  type GenRecord,
} from "../api";
import { refreshStats, stats, status } from "../store";
import { useAutoRefresh } from "../polling";
import { briefText, copyText, fmtDur, fmtTs, parseBrief } from "../utils";
import ImageViewer from "../components/ImageViewer.vue";

const message = useMessage();

const loading = ref(false);
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
const acting = ref(false);

// 全屏看图
const viewerShow = ref(false);
const viewerSrc = ref("");
const viewerLoading = ref(false);
const viewerIndex = ref(0);

const viewerRecord = computed<GenRecord | null>(() => records.value[viewerIndex.value] || null);

const workflowOptions = computed(() => {
  const list = (status.value?.templates || []).map((t) => ({ label: t.name, value: t.name }));
  return [{ label: "全部工作流", value: "" }, ...list];
});

const statusOptions = [
  { label: "全部状态", value: "" },
  { label: "成功", value: "success" },
  { label: "失败", value: "failed" },
  { label: "进行中", value: "running" },
];

const categoryOptions = computed(() => {
  // 以全局统计为准（stats.by_category 覆盖全部历史），当前页记录只作兜底：
  // 只从本页 24 条里取的话，翻页/换筛选后下拉选项会变，老类别再也选不到。
  const set = new Set<string>();
  for (const c of stats.value?.by_category || []) if (c.category) set.add(c.category);
  for (const r of records.value) if (r.category) set.add(r.category);
  return [
    { label: "全部类别", value: "" },
    ...[...set].sort().map((c) => ({ label: c, value: c })),
  ];
});

async function loadThumbs(rows: GenRecord[]) {
  const queue = rows.filter((r) => r.status === "success" && r.image_relpath && !thumbs[r.id]);
  const workers = Array.from({ length: 6 }, async () => {
    while (queue.length) {
      const r = queue.shift()!;
      try {
        const d = await apiThumb(r.id, 360);
        thumbs[r.id] = d.data_url || "";
      } catch {
        thumbs[r.id] = "";
      }
    }
  });
  await Promise.all(workers);
}

async function loadRecords(silent = false) {
  if (!silent) loading.value = true;
  try {
    const d = await apiRecords({
      offset: (page.value - 1) * pageSize.value,
      limit: pageSize.value,
      status: fStatus.value,
      workflow: fWorkflow.value,
      category: fCategory.value,
      keyword: fKeyword.value,
      starred: fStarred.value ? 1 : undefined,
    });
    total.value = d.total;
    records.value = d.rows;
    await loadThumbs(d.rows);
  } catch (e: any) {
    // 自动刷新失败不打扰用户（网络抖动很常见），手动刷新才提示
    if (!silent) message.error(`加载记录失败：${e?.message || e}`);
    throw e;
  } finally {
    if (!silent) loading.value = false;
  }
}

// ---------------- 自动刷新（有新图静默插入） ----------------

const hasFilters = computed(
  () =>
    !!(fStatus.value || fWorkflow.value || fCategory.value || fKeyword.value || fStarred.value),
);

const auto = useAutoRefresh(
  async () => {
    // 仅在「第一页 + 无筛选 + 没打开详情/大图」时自动刷新，避免打断用户
    if (page.value !== 1 || hasFilters.value || detailShow.value || viewerShow.value) return;
    const d = await apiRecords({ limit: 1 });
    const newest = d.rows[0]?.id || 0;
    if (newest && newest !== (records.value[0]?.id || 0)) {
      await loadRecords(true);
    }
  },
  8000,
  { immediate: false },
);

const autoText = computed(() => {
  if (!auto.enabled.value) return "自动刷新已关";
  if (!auto.lastAt.value) return "自动刷新：等待新图";
  const d = new Date(auto.lastAt.value);
  const p = (n: number) => String(n).padStart(2, "0");
  return `自动刷新 · ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
});

// ---------------- 全屏看图 ----------------

async function loadViewerImage(idx: number) {
  const rec = records.value[idx];
  if (!rec) return;
  viewerIndex.value = idx;
  viewerShow.value = true;
  viewerSrc.value = thumbs[rec.id] || "";
  if (rec.status !== "success") {
    viewerSrc.value = "";
    return;
  }
  viewerLoading.value = true;
  try {
    const d = await apiThumb(rec.id, 2400);
    if (viewerIndex.value === idx) viewerSrc.value = d.data_url || viewerSrc.value;
  } catch {
    /* 保留缩略图 */
  } finally {
    viewerLoading.value = false;
  }
}

function openViewer(rec: GenRecord) {
  const idx = records.value.findIndex((r) => r.id === rec.id);
  void loadViewerImage(idx >= 0 ? idx : 0);
}

function viewerPrev() {
  if (viewerIndex.value > 0) void loadViewerImage(viewerIndex.value - 1);
}

function viewerNext() {
  if (viewerIndex.value < records.value.length - 1) void loadViewerImage(viewerIndex.value + 1);
}

function applyFilters() {
  page.value = 1;
  loadRecords();
}

function applyKeyword() {
  fKeyword.value = fKeywordInput.value.trim();
  applyFilters();
}

function onPageSizeChange(n: number) {
  pageSize.value = n;
  page.value = 1;
  loadRecords();
}

async function openDetail(rec: GenRecord) {
  detail.value = rec;
  detailShow.value = true;
  detailBig.value = thumbs[rec.id] || "";
  if (rec.status !== "success") return;
  detailLoading.value = true;
  try {
    const d = await apiThumb(rec.id, 1600);
    detailBig.value = d.data_url || detailBig.value;
  } catch {
    /* 保留缩略图 */
  } finally {
    detailLoading.value = false;
  }
}

async function starToggle(rec: GenRecord) {
  try {
    await apiStar(rec.id, !rec.starred);
    rec.starred = rec.starred ? 0 : 1;
    if (detail.value?.id === rec.id) detail.value.starred = rec.starred;
    message.success(rec.starred ? "已收藏（不会被容量淘汰）" : "已取消收藏");
  } catch (e: any) {
    message.error(`收藏失败：${e?.message || e}`);
  }
}

async function resend(rec: GenRecord) {
  acting.value = true;
  try {
    const d = await apiResend(rec.id);
    const okN = (d.results || []).filter((r) => r.ok).length;
    if (!d.results?.length) message.info("投递名单为空（去「投递」页或指令里添加会话）");
    else if (okN === d.results.length) message.success(`已重投 ${okN} 个会话`);
    else message.warning(`重投完成：成功 ${okN} / ${d.results.length}`);
    await loadRecords();
  } catch (e: any) {
    message.error(`重投失败：${e?.message || e}`);
  } finally {
    acting.value = false;
  }
}

async function del(rec: GenRecord) {
  acting.value = true;
  try {
    await apiDeleteRecord(rec.id);
    message.success("已删除记录");
    detailShow.value = false;
    delete thumbs[rec.id];
    await loadRecords();
  } catch (e: any) {
    message.error(`删除失败：${e?.message || e}`);
  } finally {
    acting.value = false;
  }
}

function copy(text: string, label: string) {
  copyText(text, label, (m) => message.success(m));
}

onMounted(() => {
  void loadRecords();
  // 类别下拉依赖全局统计（stats.by_category），本页不一定刷过
  void refreshStats();
});
</script>

<template>
  <div class="page">
    <div class="mp-card filters">
      <div class="frow">
        <n-select v-model:value="fStatus" :options="statusOptions" size="small" style="width: 110px" @update:value="applyFilters" />
        <n-select v-model:value="fWorkflow" :options="workflowOptions" size="small" style="width: 165px" @update:value="applyFilters" />
        <n-select v-model:value="fCategory" :options="categoryOptions" size="small" style="width: 135px" filterable @update:value="applyFilters" />
        <n-input
          v-model:value="fKeywordInput"
          placeholder="搜提示词 / 文案 / 题材"
          size="small"
          style="width: 210px"
          clearable
          @keyup.enter="applyKeyword"
        />
        <n-button size="small" @click="applyKeyword">搜索</n-button>
        <label class="star-switch">
          <n-switch v-model:value="fStarred" size="small" @update:value="applyFilters" />
          <span>只看收藏</span>
        </label>
        <div class="spacer"></div>
        <span class="auto-tag">
          <n-switch v-model:value="auto.enabled.value" size="small" />
          <span class="mp-sub">{{ autoText }}</span>
        </span>
        <span class="mp-sub">共 {{ total }} 条</span>
        <n-button size="small" quaternary :loading="loading" @click="loadRecords(false)">刷新</n-button>
      </div>
    </div>

    <n-spin :show="loading">
      <n-empty
        v-if="!records.length && !loading"
        description="还没有记录 —— 去总览点「立即画一张」，或等调度到点"
        class="mp-empty"
      />
      <div v-else class="grid">
        <div
          v-for="r in records"
          :key="r.id"
          class="gcard"
          :class="{ failed: r.status === 'failed' }"
          @click="openDetail(r)"
        >
          <div class="thumb">
            <img v-if="r.status === 'success' && thumbs[r.id]" :src="thumbs[r.id]" alt="" loading="lazy" />
            <div v-else-if="r.pruned" class="ph ph-fail"><span>已按容量清理</span></div>
            <div v-else-if="r.status === 'success'" class="ph"><n-spin size="small" /></div>
            <div v-else-if="r.status === 'running'" class="ph"><n-spin size="small" /><span>生成中…</span></div>
            <div v-else class="ph ph-fail"><span>失败</span></div>
            <span v-if="r.starred" class="star">★</span>
            <button
              v-if="r.status === 'success'"
              class="zoom"
              title="查看大图"
              @click.stop="openViewer(r)"
            >
              ⤢
            </button>
            <div class="hover-veil">
              <span>{{ r.status === "success" ? "查看详情" : "查看失败原因" }}</span>
            </div>
          </div>
          <div class="meta">
            <div class="cap">{{ r.caption || parseBrief(r.brief).subject || `#${r.id}` }}</div>
            <div class="sub mp-sub">
              {{ r.workflow || "?" }}
              <template v-if="r.status === 'success'"> · {{ r.width }}×{{ r.height }}</template>
              · {{ fmtTs(r.ts) }}
            </div>
            <div v-if="r.status === 'failed'" class="errmsg">
              {{ r.error || "（无错误详情：可能是旧版本记录或进程中断）" }}
            </div>
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
        @update:page="() => loadRecords()"
        @update:page-size="onPageSizeChange"
      />
    </div>

    <n-drawer v-model:show="detailShow" :width="600" placement="right">
      <n-drawer-content :title="detail ? `记录 #${detail.id}` : ''" closable>
        <template v-if="detail">
          <div class="big-img" :class="{ clickable: detail.status === 'success' && detailBig }" @click="openViewer(detail)">
            <n-spin :show="detailLoading">
              <img v-if="detailBig" :src="detailBig" alt="" />
              <n-empty
                v-else-if="!detailLoading"
                :description="
                  detail.pruned
                    ? '图片已按容量上限清理（记录保留）'
                    : detail.status === 'failed'
                      ? '生成失败，无图片'
                      : '无图片'
                "
              />
            </n-spin>
            <div v-if="detail.status === 'success' && detailBig" class="big-hint">点击看大图</div>
          </div>

          <div class="detail-actions">
            <n-button size="small" @click="starToggle(detail)">
              {{ detail.starred ? "★ 已收藏" : "☆ 收藏（防淘汰）" }}
            </n-button>
            <n-button
              size="small"
              :disabled="detail.status !== 'success' || !detail.image_relpath"
              :loading="acting"
              @click="resend(detail)"
            >
              重投白名单
            </n-button>
            <n-button size="small" @click="copy(detail.prompt, '提示词')">复制提示词</n-button>
            <n-popconfirm @positive-click="del(detail)">
              <template #trigger>
                <n-button size="small" type="error" quaternary>删除</n-button>
              </template>
              删除记录并清理图片文件（收藏也会被删，谨慎操作）？
            </n-popconfirm>
          </div>

          <n-descriptions v-if="detail.status === 'failed'" :column="1" size="small" bordered>
            <n-descriptions-item label="错误">
              {{ detail.error || "（无错误详情：可能是旧版本记录或进程中断，可看「调试与日志」页）" }}
            </n-descriptions-item>
          </n-descriptions>

          <n-descriptions :column="2" size="small" bordered class="meta-table">
            <n-descriptions-item label="状态">
              <n-tag size="small" :type="detail.status === 'success' ? 'success' : detail.status === 'failed' ? 'error' : 'info'">
                {{ detail.status }}
              </n-tag>
              <n-tag v-if="detail.pruned" size="small" type="warning" style="margin-left: 6px">
                图已清理
              </n-tag>
            </n-descriptions-item>
            <n-descriptions-item label="时间">{{ fmtTs(detail.ts) }}</n-descriptions-item>
            <n-descriptions-item label="类别">{{ detail.category || "-" }}</n-descriptions-item>
            <n-descriptions-item label="工作流">{{ detail.workflow || "-" }}</n-descriptions-item>
            <n-descriptions-item label="尺寸">
              {{ detail.width && detail.height ? `${detail.width}×${detail.height}` : "-" }}
            </n-descriptions-item>
            <n-descriptions-item label="标签风格">{{ detail.tag_style || "-" }}</n-descriptions-item>
            <n-descriptions-item label="Seed">
              <span class="clickable" @click="copy(String(detail.seed ?? ''), 'seed')">{{ detail.seed ?? "-" }}</span>
            </n-descriptions-item>
            <n-descriptions-item label="耗时">
              {{ fmtDur(detail.gen_latency_ms) }}（LLM {{ fmtDur(detail.llm_latency_ms) }}）
            </n-descriptions-item>
            <n-descriptions-item label="LLM">{{ detail.llm_provider || "-" }}</n-descriptions-item>
            <n-descriptions-item label="触发">{{ detail.reason }}</n-descriptions-item>
            <n-descriptions-item :span="2" label="ComfyUI 任务">
              {{ detail.comfy_prompt_id || "-" }}
            </n-descriptions-item>
          </n-descriptions>

          <div class="sect">创作简报</div>
          <div class="brief">{{ briefText(detail.brief) }}</div>

          <div class="sect">正向提示词</div>
          <n-input :value="detail.prompt" type="textarea" readonly :autos="{ minRows: 3, maxRows: 8 }" />
          <div class="sect">负向提示词</div>
          <n-input :value="detail.negative" type="textarea" readonly :autos="{ minRows: 2, maxRows: 5 }" />
          <div class="sect">投递文案</div>
          <n-input :value="detail.caption" type="textarea" readonly :autos="{ minRows: 1, maxRows: 3 }" />

          <div class="sect">投递状态</div>
          <div v-if="!detail.delivered?.length" class="mp-sub">未投递（名单为空或失败前记录）</div>
          <div v-for="(d, i) in detail.delivered || []" :key="i" class="deliv">
            <n-tag size="small" :type="d.ok ? 'success' : 'error'">{{ d.ok ? "已送达" : "失败" }}</n-tag>
            <span class="mp-mono">{{ d.umo }}</span>
            <span v-if="!d.ok && d.error" class="mp-err">：{{ d.error }}</span>
          </div>
        </template>
      </n-drawer-content>
    </n-drawer>

    <ImageViewer
      v-model:show="viewerShow"
      :src="viewerSrc"
      :loading="viewerLoading"
      :title="viewerRecord?.caption || `记录 #${viewerRecord?.id ?? ''}`"
      :subtitle="
        viewerRecord
          ? `${viewerRecord.workflow || '?'} · ${viewerRecord.width || '?'}×${viewerRecord.height || '?'} · ${fmtTs(viewerRecord.ts)}`
          : ''
      "
      :index="viewerIndex"
      :total="records.length"
      @prev="viewerPrev"
      @next="viewerNext"
    />
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.filters {
  padding: 12px 14px;
}

.frow {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.spacer {
  flex: 1;
}

.auto-tag {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.zoom {
  position: absolute;
  left: 8px;
  bottom: 8px;
  width: 26px;
  height: 26px;
  border-radius: 8px;
  border: 1px solid rgba(255, 255, 255, 0.5);
  background: rgba(24, 20, 38, 0.5);
  color: #fff;
  font-size: 14px;
  line-height: 1;
  cursor: zoom-in;
  opacity: 0;
  transition: opacity 0.15s ease;
}

.gcard:hover .zoom {
  opacity: 1;
}

.star-switch {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12.5px;
  cursor: pointer;
  color: var(--mp-sub);
}

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 14px;
  min-height: 140px;
}

.gcard {
  border-radius: 14px;
  overflow: hidden;
  cursor: pointer;
  background: var(--mp-panel-solid);
  border: 1px solid var(--mp-line);
  box-shadow: var(--mp-shadow-sm);
  transition: transform 0.16s ease, box-shadow 0.16s ease;
}

.gcard:hover {
  transform: translateY(-3px);
  box-shadow: var(--mp-shadow);
}

.gcard.failed {
  border-color: rgba(217, 96, 95, 0.45);
}

.thumb {
  position: relative;
  background: var(--mp-panel-soft);
  aspect-ratio: 4 / 3;
  overflow: hidden;
}

.thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
  transition: transform 0.25s ease;
}

.gcard:hover .thumb img {
  transform: scale(1.04);
}

.ph {
  height: 100%;
  min-height: 140px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  font-size: 12px;
  color: var(--mp-sub);
}

.ph-fail span {
  color: var(--mp-err);
}

.star {
  position: absolute;
  top: 7px;
  right: 9px;
  color: var(--mp-accent-3);
  text-shadow: 0 1px 4px rgba(0, 0, 0, 0.5);
  font-size: 15px;
}

.hover-veil {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  background: rgba(28, 24, 44, 0.42);
  color: #fff;
  font-size: 12.5px;
  opacity: 0;
  transition: opacity 0.18s ease;
}

.gcard:hover .hover-veil {
  opacity: 1;
}

.meta {
  padding: 9px 11px 11px;
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
  margin-top: 5px;
  font-size: 11px;
  color: var(--mp-err);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.pager {
  display: flex;
  justify-content: center;
  margin-top: 6px;
}

.big-img {
  position: relative;
}

.big-img img {
  width: 100%;
  border-radius: 12px;
  display: block;
}

.big-img.clickable {
  cursor: zoom-in;
}

.big-hint {
  position: absolute;
  right: 10px;
  bottom: 10px;
  padding: 3px 9px;
  border-radius: 8px;
  font-size: 11px;
  color: #fff;
  background: rgba(24, 20, 38, 0.55);
  pointer-events: none;
}

.detail-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin: 12px 0;
}

.meta-table {
  margin-top: 8px;
}

.sect {
  font-size: 13px;
  font-weight: 700;
  margin: 16px 0 6px;
}

.brief {
  font-size: 12.5px;
  line-height: 1.7;
  padding: 9px 12px;
  border-radius: 10px;
  background: var(--mp-panel-soft);
  border: 1px dashed var(--mp-line-strong);
}

.deliv {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
  font-size: 12px;
  flex-wrap: wrap;
}

.clickable {
  cursor: pointer;
  color: var(--mp-accent);
}
</style>
