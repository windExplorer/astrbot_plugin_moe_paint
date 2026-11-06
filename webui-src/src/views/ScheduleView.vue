<script setup lang="ts">
// 调度计划：把「什么时候画」讲清楚。
// 设计要点（用户反馈）：
//  1) 模式用两块大按钮选，只显示当前模式相关字段，不再把所有字段摊在一起；
//  2) 间隔 = 数字 + 单位（分钟/小时/天），不用自己换算分钟；
//  3) 安静时段、每日上限都给开关 —— 关掉是「停用」，不是「清空」；
//  4) 顶部实时显示计划摘要与下次生成时间。
import { computed, nextTick, onMounted, ref, watch } from "vue";
import {
  NButton,
  NInputNumber,
  NSelect,
  NSwitch,
  NTag,
  NTimePicker,
  NEmpty,
  useMessage,
} from "naive-ui";
import { apiControl } from "../api";
import { cfgBool, cfgGet, cfgLoaded, cfgNum, cfgSet, loadConfig, saveFields, saving } from "../config";
import { refreshStatus, status } from "../store";
import { useAutoRefresh } from "../polling";
import { fmtDue, joinQuiet, minutesHuman, scheduleSummary, splitQuiet, untilText } from "../utils";

const message = useMessage();

type Unit = "minute" | "hour" | "day";
const UNIT_MINUTES: Record<Unit, number> = { minute: 1, hour: 60, day: 1440 };
const unitOptions = [
  { label: "分钟", value: "minute" },
  { label: "小时", value: "hour" },
  { label: "天", value: "day" },
];

const intervalValue = ref(6);
const intervalUnit = ref<Unit>("hour");
const jitterOn = ref(false);
const jitterMinutes = ref(30);
const maxOn = ref(true);
const maxPerDay = ref(4);
const quietStart = ref("01:00");
const quietEnd = ref("08:00");
const newTime = ref<string | null>("12:00");
const hydrating = ref(false);
const pauseLoading = ref(false);

const mode = computed({
  get: () => String(cfgGet("schedule.mode", "interval")),
  set: (v: string) => cfgSet("schedule.mode", v),
});

const quietOn = computed({
  get: () => {
    const v = cfgGet("schedule.quiet_enabled", true);
    if (typeof v === "boolean") return v;
    return !["0", "false", "off", "no"].includes(String(v).toLowerCase());
  },
  set: (v: boolean) => cfgSet("schedule.quiet_enabled", v),
});

const dailyTimes = computed<string[]>(() => {
  const raw = String(cfgGet("schedule.daily_times", "") || "");
  return raw
    .split(/[,，\s]+/)
    .map((s) => s.trim())
    .filter((s) => /^\d{1,2}:\d{2}$/.test(s))
    .map((s) => {
      const [h, m] = s.split(":");
      return `${String(Number(h)).padStart(2, "0")}:${m}`;
    })
    .sort();
});

function setDailyTimes(list: string[]) {
  cfgSet("schedule.daily_times", list.join(","));
}

function addTime(v: string | null) {
  if (!v) return;
  if (!dailyTimes.value.includes(v)) setDailyTimes([...dailyTimes.value, v].sort());
  newTime.value = null;
}

function removeTime(v: string) {
  setDailyTimes(dailyTimes.value.filter((x) => x !== v));
}

/** 配置载入后把扁平值还原成组件状态（只做一次，避免覆盖用户正在编辑的值）。 */
function hydrate() {
  hydrating.value = true;
  const minutes = cfgNum("schedule.interval_minutes", 360) || 360;
  if (minutes % 1440 === 0) {
    intervalUnit.value = "day";
    intervalValue.value = minutes / 1440;
  } else if (minutes % 60 === 0) {
    intervalUnit.value = "hour";
    intervalValue.value = minutes / 60;
  } else {
    intervalUnit.value = "minute";
    intervalValue.value = minutes;
  }
  const jitter = cfgNum("schedule.jitter_minutes", 30);
  jitterOn.value = jitter > 0;
  jitterMinutes.value = jitter > 0 ? jitter : 30;
  const cap = cfgNum("schedule.max_per_day", 4);
  maxOn.value = cap > 0;
  maxPerDay.value = cap > 0 ? cap : 4;
  const [s, e] = splitQuiet(String(cfgGet("schedule.quiet_hours", "01:00-08:00")));
  quietStart.value = s;
  quietEnd.value = e;
  // 默认时刻只在「本来就是空」且用不到时补上，避免把用户自己清空的列表又写回去
  if (!dailyTimes.value.length && String(cfgGet("schedule.mode", "interval")) === "daily") {
    setDailyTimes(["09:30", "20:00"]);
  }
  // watcher 是异步（flush: pre）跑在下一个 tick，哨兵必须留到那时才放
  nextTick(() => {
    hydrating.value = false;
  });
}

watch(cfgLoaded, (v) => {
  if (v) hydrate();
});

watch([intervalValue, intervalUnit], () => {
  if (hydrating.value) return;
  const unit = UNIT_MINUTES[intervalUnit.value] || 60;
  const minutes = Math.max(1, Math.round((Number(intervalValue.value) || 1) * unit));
  cfgSet("schedule.interval_minutes", minutes);
});

watch(jitterOn, (v) => {
  if (hydrating.value) return;
  cfgSet("schedule.jitter_minutes", v ? Math.max(1, jitterMinutes.value || 30) : 0);
});

watch(jitterMinutes, (v) => {
  if (hydrating.value) return;
  if (jitterOn.value) cfgSet("schedule.jitter_minutes", Math.max(1, Number(v) || 30));
});

watch(maxOn, (v) => {
  if (hydrating.value) return;
  cfgSet("schedule.max_per_day", v ? Math.max(1, maxPerDay.value || 4) : 0);
});

watch(maxPerDay, (v) => {
  if (hydrating.value) return;
  if (maxOn.value) cfgSet("schedule.max_per_day", Math.max(1, Number(v) || 4));
});

watch([quietStart, quietEnd], () => {
  if (hydrating.value) return;
  cfgSet("schedule.quiet_hours", joinQuiet(quietStart.value, quietEnd.value));
});

const preview = computed(() =>
  scheduleSummary({
    mode: mode.value,
    interval_minutes: previewMinutes.value,
    jitter_minutes: jitterOn.value ? jitterMinutes.value : 0,
    daily_times: dailyTimes.value.join(","),
    quiet_enabled: quietOn.value,
    quiet_hours: joinQuiet(quietStart.value, quietEnd.value),
  }),
);

const previewMinutes = computed(() => {
  const unit = UNIT_MINUTES[intervalUnit.value] || 60;
  return Math.max(1, Math.round((Number(intervalValue.value) || 1) * unit));
});

const todayPct = computed(() => {
  const cap = status.value?.max_per_day || 0;
  const used = status.value?.today_success || 0;
  if (!cap) return 0;
  return Math.min(100, Math.round((used / cap) * 100));
});

async function save() {
  if (mode.value === "daily" && !dailyTimes.value.length) {
    message.warning("每天定时模式至少要有一个时刻");
    return;
  }
  const r = await saveFields(
    [
      "enabled",
      "schedule.mode",
      "schedule.interval_minutes",
      "schedule.jitter_minutes",
      "schedule.daily_times",
      "schedule.quiet_enabled",
      "schedule.quiet_hours",
      "schedule.max_per_day",
    ],
    "调度计划",
  );
  if (r.ok) {
    message.success(`调度已保存（${r.changed} 项），下次计划已按新配置重算`);
    await refreshStatus();
  } else {
    message.error(`保存失败：${r.error}`);
  }
}

async function togglePause() {
  const paused = status.value?.paused_manual || status.value?.auto_paused;
  pauseLoading.value = true;
  try {
    await apiControl(paused ? "resume" : "pause");
    message.success(paused ? "调度已恢复" : "调度已暂停（到期也不会画）");
    await refreshStatus();
  } catch (e: any) {
    message.error(`操作失败：${e?.message || e}`);
  } finally {
    pauseLoading.value = false;
  }
}

// 下次生成时间 / 今日张数每 15s 自动刷新（页面不可见时自动跳过）
useAutoRefresh(() => refreshStatus(), 15000, { immediate: false });

onMounted(async () => {
  await loadConfig();
  if (cfgLoaded.value) hydrate();
});
</script>

<template>
  <div class="page">
    <!-- 现况 -->
    <div class="mp-card now">
      <div class="now-item">
        <div class="mp-sub">下次生成</div>
        <div class="now-big">{{ fmtDue(status?.next_due) }}</div>
        <div class="mp-sub">{{ untilText(status?.next_due) }}</div>
      </div>
      <div class="now-item">
        <div class="mp-sub">今日已画</div>
        <div class="now-big">{{ status?.today_success ?? 0 }}<span class="unit">张</span></div>
        <div class="bar"><i :style="{ width: todayPct + '%' }"></i></div>
        <div class="mp-sub">上限 {{ status?.max_per_day ? status.max_per_day + " 张" : "不限" }}</div>
      </div>
      <div class="now-item grow">
        <div class="mp-sub">当前计划</div>
        <div class="now-desc">{{ scheduleSummary(status) }}</div>
        <div class="mp-sub">{{ status?.last_run_note || "还没有跑过" }}</div>
      </div>
      <n-button size="small" :loading="pauseLoading" @click="togglePause">
        {{ status?.paused_manual || status?.auto_paused ? "恢复调度" : "暂停调度" }}
      </n-button>
    </div>

    <!-- 模式 -->
    <div class="mp-card">
      <div class="mp-card-head">
        <div class="mp-card-title">什么时候画</div>
        <n-tag size="small" round :bordered="false">总开关
          <n-switch
            size="small"
            :value="cfgBool('enabled', true)"
            style="margin-left: 6px"
            @update:value="(v: boolean) => cfgSet('enabled', v)"
          />
        </n-tag>
      </div>

      <div class="modes">
        <button class="mode" :class="{ active: mode === 'interval' }" @click="mode = 'interval'">
          <span class="mode-icon">⏳</span>
          <b>隔一段时间画一张</b>
          <span class="mp-sub">例如每 6 小时一张，可加随机浮动避免整点机械感</span>
        </button>
        <button class="mode" :class="{ active: mode === 'daily' }" @click="mode = 'daily'">
          <span class="mode-icon">🗓️</span>
          <b>每天固定时刻画</b>
          <span class="mp-sub">例如每天 09:30 与 20:00 各一张</span>
        </button>
      </div>

      <!-- interval -->
      <!-- 注意：一行里有多个控件时**必须用 div**，不能用 label 包。
           label 会把点击转发给它关联的第一个表单控件（这里是左边的数字框），
           于是点「单位」下拉的瞬间焦点被抢走、Naive 的 select 立即收起，
           表现为「下拉一打开就自动关闭，选不了分钟/天」。 -->
      <div v-if="mode === 'interval'" class="mp-fields block">
        <div class="mp-field">
          <span class="label">生成间隔</span>
          <div class="inline">
            <n-input-number v-model:value="intervalValue" :min="1" :max="9999" size="small" style="width: 110px" />
            <n-select v-model:value="intervalUnit" :options="unitOptions" size="small" style="width: 92px" />
          </div>
          <span class="mp-sub">约等于「每 {{ minutesHuman(previewMinutes) }}画一张」</span>
        </div>
        <div class="mp-field">
          <span class="label">随机浮动</span>
          <div class="inline">
            <n-switch v-model:value="jitterOn" size="small" />
            <n-input-number
              v-model:value="jitterMinutes"
              :min="1"
              :max="1440"
              size="small"
              style="width: 120px"
              :disabled="!jitterOn"
            />
            <span class="mp-sub">分钟</span>
          </div>
          <span class="mp-sub">在间隔上随机多加 0~N 分钟；关掉即准点</span>
        </div>
      </div>

      <!-- daily -->
      <div v-else class="block">
        <div class="mp-field">
          <span class="label">每日时刻</span>
          <div class="times">
            <n-tag
              v-for="t in dailyTimes"
              :key="t"
              closable
              size="small"
              round
              @close="removeTime(t)"
            >
              {{ t }}
            </n-tag>
            <n-empty v-if="!dailyTimes.length" description="还没有时刻" size="small" />
            <div class="time-add">
              <n-time-picker
                :formatted-value="newTime"
                value-format="HH:mm"
                format="HH:mm"
                size="small"
                placeholder="选时刻"
                style="width: 118px"
                @update:formatted-value="(v: string | null) => (newTime = v)"
              />
              <n-button size="small" @click="addTime(newTime)">添加</n-button>
            </div>
          </div>
          <span class="mp-sub">24 小时制；到点画一张，一天按列表跑完为止</span>
        </div>
      </div>
    </div>

    <!-- 安静时段 + 每日上限 -->
    <div class="two">
      <div class="mp-card">
        <div class="mp-card-head">
          <div class="mp-card-title">安静时段</div>
          <n-switch v-model:value="quietOn" size="small" />
        </div>
        <div class="mp-hint" style="margin-bottom: 10px">
          开启后，这段时间内到点会<b>跳过本次并顺延</b>，不影响其他时段的正常出图。
          想临时停用它，关掉右上角开关即可 —— 时间设置会原样保留，随时再打开。
        </div>
        <div class="inline" :class="{ dim: !quietOn }">
          <n-time-picker
            :formatted-value="quietStart"
            value-format="HH:mm"
            format="HH:mm"
            size="small"
            style="width: 118px"
            :disabled="!quietOn"
            @update:formatted-value="(v: string | null) => (quietStart = v || '01:00')"
          />
          <span class="mp-sub">到</span>
          <n-time-picker
            :formatted-value="quietEnd"
            value-format="HH:mm"
            format="HH:mm"
            size="small"
            style="width: 118px"
            :disabled="!quietOn"
            @update:formatted-value="(v: string | null) => (quietEnd = v || '08:00')"
          />
        </div>
        <div class="mp-sub" style="margin-top: 8px">
          {{ quietOn ? `每天 ${quietStart} 至 ${quietEnd} 不生成（支持跨零点，如 23:00-07:00）` : "安静时段已关闭，全天都可能到点出图" }}
        </div>
      </div>

      <div class="mp-card">
        <div class="mp-card-head">
          <div class="mp-card-title">每日上限</div>
          <n-switch v-model:value="maxOn" size="small" />
        </div>
        <div class="mp-hint" style="margin-bottom: 10px">
          防止间隔太小时刷屏。只统计<b>定时</b>出图；手动「立即画一张」不受此限制。
          关闭开关 = 不限制。
        </div>
        <div class="inline" :class="{ dim: !maxOn }">
          <n-input-number
            v-model:value="maxPerDay"
            :min="1"
            :max="1000"
            size="small"
            style="width: 110px"
            :disabled="!maxOn"
          />
          <span class="mp-sub">张 / 天</span>
        </div>
        <div class="mp-sub" style="margin-top: 8px">
          {{ maxOn ? `每天最多 ${maxPerDay} 张，达上限后当日不再定时出图` : "不限张数（注意过于频繁会占用 ComfyUI 队列）" }}
        </div>
      </div>
    </div>

    <!-- 保存 -->
    <div class="savebar">
      <div class="save-summary">
        <div class="mp-sub">保存后立即生效</div>
        <div class="save-text">{{ preview }}</div>
      </div>
      <div class="mp-row">
        <n-button size="small" quaternary @click="loadConfig(true).then(hydrate)">还原本地编辑</n-button>
        <n-button size="small" type="primary" :loading="!!saving['调度计划']" @click="save">
          保存调度设置
        </n-button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.now {
  display: flex;
  align-items: center;
  gap: 26px;
  flex-wrap: wrap;
}

.now-item {
  min-width: 130px;
}

.now-item.grow {
  flex: 1;
  min-width: 240px;
}

.now-big {
  font-size: 22px;
  font-weight: 700;
  color: var(--mp-text);
  line-height: 1.3;
}

.now-big .unit {
  font-size: 12px;
  color: var(--mp-sub);
  margin-left: 4px;
}

.now-desc {
  font-size: 13px;
  font-weight: 600;
  margin: 2px 0;
}

.bar {
  height: 5px;
  border-radius: 4px;
  background: var(--mp-line);
  overflow: hidden;
  margin: 4px 0 2px;
  width: 120px;
}

.bar i {
  display: block;
  height: 100%;
  background: var(--mp-grad);
  border-radius: 4px;
  transition: width 0.3s ease;
}

.modes {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 12px;
  margin-bottom: 6px;
}

.mode {
  display: flex;
  flex-direction: column;
  gap: 3px;
  text-align: left;
  padding: 14px 16px;
  border-radius: 14px;
  border: 1.5px solid var(--mp-line);
  background: var(--mp-panel-solid);
  color: var(--mp-text);
  cursor: pointer;
  transition: border-color 0.15s ease, box-shadow 0.15s ease, transform 0.15s ease;
  font: inherit;
}

.mode:hover {
  transform: translateY(-1px);
  box-shadow: var(--mp-shadow-sm);
}

.mode.active {
  border-color: transparent;
  background:
    linear-gradient(var(--mp-panel-solid), var(--mp-panel-solid)) padding-box,
    var(--mp-grad) border-box;
  border: 1.5px solid transparent;
  box-shadow: 0 8px 22px rgba(107, 120, 224, 0.18);
}

.mode-icon {
  font-size: 18px;
}

.mode b {
  font-size: 13.5px;
}

.block {
  margin-top: 14px;
}

.inline {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.dim {
  opacity: 0.5;
}

.times {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.time-add {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding-left: 4px;
  border-left: 1px dashed var(--mp-line-strong);
}

.two {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 14px;
}

.savebar {
  position: sticky;
  bottom: 12px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  padding: 12px 16px;
  border-radius: 14px;
  background: var(--mp-panel);
  border: 1px solid var(--mp-line);
  box-shadow: var(--mp-shadow);
  backdrop-filter: blur(8px);
}

.save-text {
  font-size: 13px;
  font-weight: 600;
}
</style>
