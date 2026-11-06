<script setup lang="ts">
// 总览：一眼看清「现在什么状态、今天画了几张、最近画得怎么样」。
import { computed, onMounted, reactive, ref } from "vue";
import { useRouter } from "vue-router";
import {
  NButton,
  NEmpty,
  NInput,
  NSpin,
  NTag,
  useMessage,
} from "naive-ui";
import {
  apiControl,
  apiDebugLogs,
  apiRecords,
  apiThumb,
  type DebugLogRow,
  type GenRecord,
} from "../api";
import { refreshStats, refreshStatus, stats, status, successRate } from "../store";
import { useAutoRefresh } from "../polling";
import { fmtDue, fmtDurationSec, fmtTs, parseBrief, scheduleSummary, untilText } from "../utils";

const message = useMessage();
const router = useRouter();

const recent = ref<GenRecord[]>([]);
const thumbs = reactive<Record<number, string>>({});
const logs = ref<DebugLogRow[]>([]);
const loading = ref(false);
const triggerTheme = ref("");
const triggerLoading = ref(false);
const pauseLoading = ref(false);

const pool = computed(() => (status.value?.templates || []).filter((t) => t.enabled && t.ok));
const badTemplates = computed(() =>
  (status.value?.templates || []).filter((t) => !t.ok || !t.enabled),
);

async function loadRecent(silent = false) {
  if (!silent) loading.value = true;
  try {
    const d = await apiRecords({ limit: 8 });
    recent.value = d.rows;
    for (const r of d.rows) {
      if (r.status === "success" && r.image_relpath && !thumbs[r.id]) {
        try {
          const t = await apiThumb(r.id, 380);
          thumbs[r.id] = t.data_url || "";
        } catch {
          thumbs[r.id] = "";
        }
      }
    }
  } catch (e: any) {
    if (!silent) message.error(`加载最近出图失败：${e?.message || e}`);
    throw e;
  } finally {
    if (!silent) loading.value = false;
  }
}

async function loadLogs() {
  try {
    const d = await apiDebugLogs(8);
    logs.value = d.rows;
  } catch {
    /* 日志是附加信息，失败不打扰 */
  }
}

async function refreshAll() {
  await Promise.all([refreshStatus(), refreshStats()]);
  await Promise.all([loadRecent(), loadLogs()]);
}

async function triggerDraw() {
  triggerLoading.value = true;
  try {
    const d = await apiControl("trigger", triggerTheme.value.trim());
    if (d.started) {
      message.success("已开始生成，稍后会自动出现在图库");
      triggerTheme.value = "";
      setTimeout(refreshAll, 3000);
    } else {
      message.warning("已有生成任务在进行，稍后再试");
    }
  } catch (e: any) {
    message.error(`触发失败：${e?.message || e}`);
  } finally {
    triggerLoading.value = false;
  }
}

async function togglePause() {
  const paused = status.value?.paused_manual || status.value?.auto_paused;
  pauseLoading.value = true;
  try {
    await apiControl(paused ? "resume" : "pause");
    message.success(paused ? "调度已恢复" : "调度已暂停");
    await refreshStatus();
  } catch (e: any) {
    message.error(`操作失败：${e?.message || e}`);
  } finally {
    pauseLoading.value = false;
  }
}

// 状态/统计 10s 自动刷新（页面不可见时自动跳过）；有 running 时顺带刷最近出图
useAutoRefresh(
  async () => {
    await refreshStatus();
    await refreshStats();
    if (recent.value.some((r) => r.status === "running")) await loadRecent(true);
  },
  10000,
  { immediate: false },
);

onMounted(refreshAll);
</script>

<template>
  <div class="page">
    <!-- 卷首 -->
    <section class="hero">
      <div class="hero-left">
        <div class="hero-kicker">萌萌绘卷 · 控制台</div>
        <h1>让 bot 自己挑题材、写提示词、画成卷</h1>
        <p class="hero-sub">
          {{ scheduleSummary(status) }}
        </p>
        <div class="hero-actions">
          <n-input
            v-model:value="triggerTheme"
            class="theme-input"
            placeholder="主题词（可空 = 随机抽签）"
            size="small"
            @keyup.enter="triggerDraw"
          />
          <n-button size="small" type="primary" :loading="triggerLoading" @click="triggerDraw">
            立即画一张
          </n-button>
          <n-button size="small" ghost :loading="pauseLoading" class="pause-btn" @click="togglePause">
            {{ status?.paused_manual || status?.auto_paused ? "恢复调度" : "暂停调度" }}
          </n-button>
        </div>
      </div>
      <div class="hero-next" :class="{ busy: status?.running }">
        <div class="next-label">{{ status?.running ? "正在生成" : "下次生成" }}</div>
        <div class="next-time">
          {{ status?.running ? fmtDurationSec(status?.running_for_sec) : fmtDue(status?.next_due) }}
        </div>
        <div class="next-until">
          <template v-if="status?.running">画完会自动排下一轮</template>
          <span v-else-if="(status?.next_due_overdue_sec || 0) > 60" class="warn">
            计划已过期 {{ fmtDurationSec(status?.next_due_overdue_sec) }}，调度可能卡住（看「调试与日志」）
          </span>
          <template v-else>{{ untilText(status?.next_due) }}</template>
        </div>
        <div class="next-split"></div>
        <div class="next-note">{{ status?.last_run_note || "还没有跑过" }}</div>
      </div>
    </section>

    <!-- 指标 -->
    <section class="stats">
      <div class="stat">
        <div class="stat-label">今日产出</div>
        <div class="stat-value">{{ status?.today_success ?? 0 }}</div>
        <div class="stat-foot">上限 {{ status?.max_per_day ? status.max_per_day : "不限" }}</div>
      </div>
      <div class="stat">
        <div class="stat-label">累计成功</div>
        <div class="stat-value">{{ status?.counts.success ?? 0 }}</div>
        <div class="stat-foot">失败 {{ status?.counts.failed ?? 0 }}</div>
      </div>
      <div class="stat">
        <div class="stat-label">成功率</div>
        <div class="stat-value">
          {{ successRate == null ? "-" : successRate.toFixed(0) + "%" }}
        </div>
        <div class="stat-foot">只算已结束的轮次</div>
      </div>
      <div class="stat">
        <div class="stat-label">可用工作流</div>
        <div class="stat-value">{{ pool.length }}</div>
        <div class="stat-foot">
          <span v-if="badTemplates.length" class="warn">{{ badTemplates.length }} 个不可用</span>
          <span v-else>全部健康</span>
        </div>
      </div>
      <div class="stat">
        <div class="stat-label">平均质检</div>
        <div class="stat-value">{{ stats?.score_avg != null ? stats.score_avg : "-" }}</div>
        <div class="stat-foot">
          已评分 {{ stats?.score_count ?? 0 }} 张 · 低分 {{ stats?.score_low ?? 0 }}
          <template v-if="(stats?.by_ab || []).length">
            ·
            <span v-for="x in stats?.by_ab || []" :key="x.ab">
              {{ x.ab }} {{ x.score_avg ?? "-" }}（{{ x.n }}）
            </span>
          </template>
        </div>
      </div>
    </section>

    <!-- 环境 -->
    <section class="env">
      <n-tag size="small" round :bordered="false" :type="status?.comfy?.ok ? 'success' : 'error'">
        ComfyUI {{ status?.comfy?.ok ? `已连接 ${status.comfy.elapsed_ms}ms` : "未连接" }}
      </n-tag>
      <n-tag size="small" round :bordered="false">
        {{ status?.comfy?.source === "anima" ? "🪄 跟随萌绘" : "🛠 自填地址" }} ·
        <span class="mp-mono">{{ status?.comfy?.url || "-" }}</span>
      </n-tag>
      <n-tag size="small" round :bordered="false" :type="status?.llm?.found ? 'success' : 'warning'">
        LLM：{{ status?.llm?.label || "-" }}
      </n-tag>
      <n-tag size="small" round :bordered="false">
        投递 {{ status?.delivery_sessions ?? 0 }} 个会话
      </n-tag>
      <n-tag size="small" round :bordered="false">
        {{ status?.selection_mode === "round_robin" ? "工作流轮换" : "工作流随机" }}
      </n-tag>
    </section>

    <div class="cols">
      <!-- 最近出图 -->
      <section class="mp-card">
        <div class="mp-card-head">
          <div class="mp-card-title">最近出图</div>
          <n-button size="tiny" quaternary @click="router.push('/gallery')">去图库 →</n-button>
        </div>
        <n-spin :show="loading">
          <n-empty v-if="!recent.length && !loading" description="还没有画过，点上面「立即画一张」试试" class="mp-empty" />
          <div v-else class="recent">
            <div
              v-for="r in recent"
              :key="r.id"
              class="recent-card"
              :class="{ failed: r.status === 'failed' }"
              @click="router.push('/gallery')"
            >
              <img v-if="thumbs[r.id]" :src="thumbs[r.id]" alt="" loading="lazy" />
              <div v-else class="ph">
                <span v-if="r.status === 'failed'">失败</span>
                <span v-else-if="r.pruned">已清理</span>
                <span v-else>{{ r.status === "running" ? "生成中…" : "无图" }}</span>
              </div>
              <div class="recent-meta">
                <div class="cap">{{ r.caption || parseBrief(r.brief).subject || `#${r.id}` }}</div>
                <div class="mp-sub">{{ fmtTs(r.ts) }} · {{ r.workflow || "?" }}</div>
              </div>
            </div>
          </div>
        </n-spin>
      </section>

      <!-- 日志 + 模板 -->
      <section class="side">
        <div class="mp-card">
          <div class="mp-card-head">
            <div class="mp-card-title">最近事件</div>
            <n-button size="tiny" quaternary @click="router.push('/debug')">全部 →</n-button>
          </div>
          <n-empty v-if="!logs.length" description="暂无事件" size="small" />
          <ul v-else class="loglist">
            <li v-for="(l, i) in logs" :key="i" :class="l.kind">
              <span class="lt">{{ fmtTs(l.ts).slice(6) }}</span>
              <span class="lm">{{ l.msg }}</span>
            </li>
          </ul>
        </div>

        <div class="mp-card">
          <div class="mp-card-head">
            <div class="mp-card-title">工作流健康</div>
            <n-button size="tiny" quaternary @click="router.push('/workflows')">管理 →</n-button>
          </div>
          <n-empty v-if="!status?.templates?.length" description="还没有模板" size="small" />
          <div v-for="t in status?.templates || []" :key="t.name + t.file" class="tmpl-row">
            <n-tag size="tiny" :bordered="false" :type="t.ok && t.enabled ? 'success' : 'error'">
              {{ t.ok ? (t.enabled ? "可用" : "已停用") : "校验失败" }}
            </n-tag>
            <span class="tmpl-name">{{ t.name }}</span>
            <span class="mp-mono mp-sub">{{ t.file }}</span>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.hero {
  position: relative;
  display: flex;
  justify-content: space-between;
  gap: 22px;
  padding: 22px 24px;
  border-radius: 22px;
  color: #fff;
  overflow: hidden;
  background: var(--mp-grad);
  box-shadow: 0 18px 40px rgba(107, 120, 224, 0.28);
}

.hero::after {
  content: "";
  position: absolute;
  right: -60px;
  top: -80px;
  width: 260px;
  height: 260px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.14);
}

.hero-left {
  min-width: 0;
  flex: 1;
}

.hero-kicker {
  font-size: 12px;
  letter-spacing: 1.2px;
  opacity: 0.82;
}

.hero h1 {
  margin: 6px 0 8px;
  font-size: 21px;
  line-height: 1.35;
  font-weight: 700;
}

.hero-sub {
  margin: 0 0 14px;
  font-size: 12.5px;
  opacity: 0.9;
}

.hero-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.theme-input {
  width: 230px;
}

.pause-btn {
  color: #fff;
  border-color: rgba(255, 255, 255, 0.55) !important;
}

.hero-next {
  flex: 0 0 190px;
  position: relative;
  z-index: 1;
  background: rgba(255, 255, 255, 0.16);
  border: 1px solid rgba(255, 255, 255, 0.3);
  border-radius: 16px;
  padding: 14px 16px;
  backdrop-filter: blur(4px);
}

.next-label {
  font-size: 11.5px;
  opacity: 0.82;
}

.next-time {
  font-size: 20px;
  font-weight: 700;
  margin-top: 2px;
}

.next-until {
  font-size: 12px;
  opacity: 0.9;
}

.next-split {
  height: 1px;
  background: rgba(255, 255, 255, 0.28);
  margin: 10px 0 8px;
}

.hero-next.busy {
  box-shadow: 0 0 0 2px rgba(255, 255, 255, 0.5);
  animation: heroPulse 1.8s ease-in-out infinite;
}

@keyframes heroPulse {
  0%,
  100% {
    box-shadow: 0 0 0 2px rgba(255, 255, 255, 0.32);
  }
  50% {
    box-shadow: 0 0 0 4px rgba(255, 255, 255, 0.55);
  }
}

.hero-next .warn {
  font-weight: 700;
  color: #ffe6b8;
}

.next-note {
  font-size: 11.5px;
  line-height: 1.5;
  opacity: 0.86;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 12px;
}

.stat {
  background: var(--mp-panel);
  border: 1px solid var(--mp-line);
  border-radius: 14px;
  padding: 14px 16px;
  box-shadow: var(--mp-shadow-sm);
}

.stat-label {
  font-size: 12px;
  color: var(--mp-sub);
}

.stat-value {
  font-size: 26px;
  font-weight: 700;
  line-height: 1.25;
  background: var(--mp-grad);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}

.stat-foot {
  font-size: 11.5px;
  color: var(--mp-sub);
}

.stat-foot .warn {
  color: var(--mp-warn);
}

.env {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.cols {
  display: grid;
  grid-template-columns: minmax(0, 1.55fr) minmax(280px, 1fr);
  gap: 14px;
  align-items: start;
}

.side {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.recent {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 10px;
}

.recent-card {
  border-radius: 12px;
  overflow: hidden;
  border: 1px solid var(--mp-line);
  background: var(--mp-panel-solid);
  cursor: pointer;
  transition: transform 0.16s ease, box-shadow 0.16s ease;
}

.recent-card:hover {
  transform: translateY(-2px);
  box-shadow: var(--mp-shadow);
}

.recent-card.failed {
  border-color: rgba(217, 96, 95, 0.4);
}

.recent-card img {
  width: 100%;
  aspect-ratio: 4 / 3;
  object-fit: cover;
  display: block;
}

.recent-card .ph {
  aspect-ratio: 4 / 3;
  display: grid;
  place-items: center;
  font-size: 12px;
  color: var(--mp-sub);
  background: var(--mp-panel-soft);
}

.recent-meta {
  padding: 7px 9px 9px;
}

.recent-meta .cap {
  font-size: 12px;
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.loglist {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.loglist li {
  display: flex;
  gap: 8px;
  font-size: 12px;
  line-height: 1.5;
  padding-left: 8px;
  border-left: 2px solid var(--mp-line-strong);
}

.loglist li.error {
  border-left-color: var(--mp-err);
  color: var(--mp-err);
}

.loglist li.generate {
  border-left-color: var(--mp-accent);
}

.loglist .lt {
  color: var(--mp-sub);
  flex: 0 0 42px;
  font-variant-numeric: tabular-nums;
}

.loglist .lm {
  min-width: 0;
  word-break: break-all;
}

.tmpl-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 0;
  border-bottom: 1px dashed var(--mp-line);
  font-size: 12.5px;
}

.tmpl-row:last-child {
  border-bottom: none;
}

.tmpl-name {
  font-weight: 600;
}

@media (max-width: 900px) {
  .cols {
    grid-template-columns: 1fr;
  }
  .hero {
    flex-direction: column;
  }
  .hero-next {
    flex: none;
  }
  .theme-input {
    width: 100%;
  }
}
</style>
