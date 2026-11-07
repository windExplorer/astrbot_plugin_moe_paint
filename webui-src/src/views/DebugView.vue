<script setup lang="ts">
// 调试：环境快照 + ComfyUI 连通性 + LLM 试跑 + 投递测试 + 内存事件日志。
// 全部是「只读/一次性动作」，不改配置、不写记录（投递测试也只发一条文本）。
import { computed, onMounted, ref } from "vue";
import {
  NButton,
  NEmpty,
  NInput,
  NInputNumber,
  NSelect,
  NSpin,
  NSwitch,
  NTag,
  useMessage,
} from "naive-ui";
import {
  apiDebugComfy,
  apiDebugDelivery,
  apiDebugEnv,
  apiDebugLlm,
  apiDebugLogs,
  type ComfyProbe,
  type DebugEnv,
  type DebugLogRow,
  type LlmPreview,
} from "../api";
import { cfgBool, cfgGet, cfgNum, cfgSet, loadConfig, saveFields, saving } from "../config";
import { fmtTsFull, fmtDur } from "../utils";
import { useAutoRefresh } from "../polling";

const message = useMessage();

const env = ref<DebugEnv | null>(null);
const envLoading = ref(false);

const comfyUrl = ref("");
const comfyLoading = ref(false);
const comfyRes = ref<ComfyProbe | null>(null);

const theme = ref("");
const llmLoading = ref(false);
const llmRes = ref<LlmPreview | null>(null);
const SCOPE_LABEL: Record<string, string> = {
  anime: "只画动漫", photo: "只画真人", both: "两者皆可",
};
const PHASE_LABEL: Record<string, string> = {
  dawn: "黎明", morning: "上午", day: "白天", dusk: "黄昏", night: "入夜", deep_night: "深夜",
};

const umo = ref("");
const testText = ref("");
const delivLoading = ref(false);
const delivRes = ref<{ ok: boolean; text: string } | null>(null);

const logs = ref<DebugLogRow[]>([]);
const logsLoading = ref(false);
const kindFilter = ref("");

const umoOptions = computed(() =>
  (env.value?.delivery_sessions || []).map((u) => ({ label: u, value: u })),
);

const filteredLogs = computed(() =>
  kindFilter.value ? logs.value.filter((l) => l.kind === kindFilter.value) : logs.value,
);

const kinds = computed(() => [...new Set(logs.value.map((l) => l.kind))]);

async function loadEnv() {
  envLoading.value = true;
  try {
    env.value = await apiDebugEnv();
  } catch (e: any) {
    message.error(`读取环境失败：${e?.message || e}`);
  } finally {
    envLoading.value = false;
  }
}

async function loadLogs(silent = false) {
  if (!silent) logsLoading.value = true;
  try {
    const d = await apiDebugLogs(200);
    logs.value = d.rows;
  } catch (e: any) {
    if (!silent) message.error(`读取日志失败：${e?.message || e}`);
    throw e;
  } finally {
    if (!silent) logsLoading.value = false;
  }
}

// 日志自动刷新：3s（内存日志，请求很轻），页面不可见时自动跳过
const logsAuto = useAutoRefresh(() => loadLogs(true), 3000, { immediate: false });

const logsAutoText = computed(() => {
  if (!logsAuto.enabled.value) return "已关";
  return logsAuto.lastAt.value ? "3 秒 · 刚刚更新" : "3 秒";
});

async function testComfy() {
  comfyLoading.value = true;
  comfyRes.value = null;
  try {
    comfyRes.value = await apiDebugComfy(comfyUrl.value.trim());
    message[comfyRes.value.ok ? "success" : "error"](
      comfyRes.value.ok ? "ComfyUI 连接正常" : `连接失败：${comfyRes.value.error || "未知错误"}`,
    );
  } catch (e: any) {
    message.error(`测试失败：${e?.message || e}`);
  } finally {
    comfyLoading.value = false;
  }
}

async function testLlm() {
  llmLoading.value = true;
  llmRes.value = null;
  try {
    llmRes.value = await apiDebugLlm(theme.value.trim());
    if (llmRes.value.ok) message.success("LLM 试跑成功，结果见下");
    else message.error(`试跑失败：${llmRes.value.error}`);
    await loadLogs();
  } catch (e: any) {
    message.error(`试跑失败：${e?.message || e}`);
  } finally {
    llmLoading.value = false;
  }
}

async function testDelivery() {
  delivLoading.value = true;
  delivRes.value = null;
  try {
    const r = await apiDebugDelivery(umo.value.trim(), testText.value.trim());
    delivRes.value = { ok: r.ok, text: r.ok ? "已发送，去会话里确认" : r.error || "发送失败" };
    message[r.ok ? "success" : "error"](delivRes.value.text);
    await loadLogs();
  } catch (e: any) {
    delivRes.value = { ok: false, text: e?.message || String(e) };
    message.error(delivRes.value.text);
  } finally {
    delivLoading.value = false;
  }
}

/** 工作流留档设置（v0.7.11） */
async function saveWfCfg() {
  const r = await saveFields(
    ["debug.save_workflows", "debug.keep_workflows"],
    "工作流留档",
  );
  if (r.ok) {
    message.success(`已保存（${r.changed} 项）`);
  } else {
    message.error(`保存失败：${r.error}`);
  }
}

onMounted(async () => {
  await loadConfig();
  await Promise.all([loadEnv(), loadLogs()]);
});
</script>

<template>
  <div class="page">
    <!-- 工作流留档 -->
    <div class="mp-card">
      <div class="mp-card-head">
        <div class="mp-card-title">工作流留档（排障）</div>
        <n-button
          size="small"
          type="primary"
          :loading="!!saving['工作流留档']"
          @click="saveWfCfg"
        >
          保存
        </n-button>
      </div>
      <div class="mp-hint" style="margin-bottom: 10px">
        每轮都会把<b>真正提交给 ComfyUI</b> 的完整工作流存一份。图不对时去图库点开那张图 →
        「查看工作流」：实际注入的正负向、尺寸、种子、采样参数都在图里，能区分是提示词写错、
        模板注入错，还是模型本身画不出来。
      </div>
      <div class="mp-fields">
        <label class="mp-field">
          <span class="label">保存提交的工作流</span>
          <n-switch
            :value="cfgBool('debug.save_workflows', true)"
            size="small"
            @update:value="(v: boolean) => cfgSet('debug.save_workflows', v)"
          />
          <span class="mp-sub">关掉后不再留档（已有快照保留）</span>
        </label>
        <label class="mp-field">
          <span class="label">保留条数（0 = 不限）</span>
          <n-input-number
            :value="cfgNum('debug.keep_workflows', 200)"
            :min="0" :max="100000" size="small"
            @update:value="(v: number | null) => cfgSet('debug.keep_workflows', v ?? 200)"
          />
          <span class="mp-sub">200 条约 2~10MB；超出按最旧清理</span>
        </label>
      </div>
    </div>

    <!-- 环境 -->
    <div class="mp-card">
      <div class="mp-card-head">
        <div class="mp-card-title">运行环境</div>
        <n-button size="small" quaternary :loading="envLoading" @click="loadEnv">刷新</n-button>
      </div>
      <n-spin :show="envLoading">
        <div v-if="env" class="env-grid">
          <div class="env-item">
            <div class="mp-sub">生效的 ComfyUI 地址</div>
            <div class="mp-mono break">{{ env.comfy_url }}</div>
            <n-tag size="tiny" :bordered="false" :type="env.comfy_url_source === 'anima' ? 'success' : 'default'">
              {{ env.comfy_url_source === "anima" ? "跟随萌绘" : "本插件手动" }}
            </n-tag>
          </div>
          <div class="env-item">
            <div class="mp-sub">手动配置地址</div>
            <div class="mp-mono break">{{ env.manual_url || "—" }}</div>
          </div>
          <div class="env-item">
            <div class="mp-sub">图库目录</div>
            <div class="mp-mono break">{{ env.gallery_dir || "—" }}</div>
          </div>
          <div class="env-item">
            <div class="mp-sub">工作流目录</div>
            <div class="mp-mono break">{{ env.workflows_dir || "—" }}</div>
          </div>
          <div class="env-item">
            <div class="mp-sub">记录库</div>
            <div class="mp-mono break">{{ env.db_path || "—" }}</div>
          </div>
          <div class="env-item">
            <div class="mp-sub">投递会话</div>
            <div>{{ env.delivery_sessions.length }} 个</div>
            <div class="mp-mono break">{{ env.delivery_sessions.join("，") || "（空 = 只入库）" }}</div>
          </div>
          <div class="env-item">
            <div class="mp-sub">模板 / 轮换</div>
            <div>
              {{ env.templates.length }} 个 · {{ env.selection_mode === "round_robin" ? "轮换" : "随机" }}
            </div>
            <div class="mp-sub">队列避让阈值 {{ env.queue_defer_threshold }}</div>
          </div>
          <div class="env-item">
            <div class="mp-sub">内存日志</div>
            <div>{{ env.log_capacity }} 条</div>
            <div class="mp-sub">重启插件即清空</div>
          </div>
        </div>
      </n-spin>
    </div>

    <div class="two">
      <!-- ComfyUI -->
      <div class="mp-card">
        <div class="mp-card-head">
          <div class="mp-card-title">ComfyUI 连通性</div>
          <n-button size="small" type="primary" :loading="comfyLoading" @click="testComfy">测试</n-button>
        </div>
        <div class="mp-hint" style="margin-bottom: 10px">
          留空 = 测当前生效地址；填地址可临时测另一台（不会保存）。
        </div>
        <n-input
          v-model:value="comfyUrl"
          size="small"
          placeholder="http://127.0.0.1:8188（留空 = 当前生效）"
          class="mp-mono"
          @keyup.enter="testComfy"
        />
        <div v-if="comfyRes" class="result" :class="{ bad: !comfyRes.ok }">
          <div class="mp-row">
            <n-tag size="small" :type="comfyRes.ok ? 'success' : 'error'" :bordered="false">
              {{ comfyRes.ok ? "连通" : "失败" }}
            </n-tag>
            <span class="mp-mono">{{ comfyRes.url }}</span>
            <span v-if="comfyRes.ok" class="mp-sub">延迟 {{ comfyRes.elapsed_ms }}ms</span>
          </div>
          <div v-if="comfyRes.ok && comfyRes.queue" class="mp-sub">
            队列：执行中 {{ comfyRes.queue.running }} · 排队 {{ comfyRes.queue.pending }}
          </div>
          <div v-if="comfyRes.error" class="mp-err">{{ comfyRes.error }}</div>
          <div v-if="comfyRes.queue_error" class="mp-err">队列查询失败：{{ comfyRes.queue_error }}</div>
        </div>
      </div>

      <!-- 投递 -->
      <div class="mp-card">
        <div class="mp-card-head">
          <div class="mp-card-title">投递自检</div>
          <n-button size="small" type="primary" :loading="delivLoading" @click="testDelivery">发送</n-button>
        </div>
        <div class="mp-hint" style="margin-bottom: 10px">
          只发一条文本，不进投递名单、不写记录。用来确认 UMO 与平台适配器是否对得上。
        </div>
        <div class="deliv-form">
          <n-select
            v-model:value="umo"
            :options="umoOptions"
            size="small"
            filterable
            tag
            placeholder="选已有会话，或直接输入 UMO"
          />
          <n-input
            v-model:value="testText"
            size="small"
            placeholder="自定义文本（可空 = 默认自检文案）"
            @keyup.enter="testDelivery"
          />
        </div>
        <div v-if="delivRes" class="mp-sub" :class="delivRes.ok ? 'mp-ok' : 'mp-err'" style="margin-top: 8px">
          {{ delivRes.text }}
        </div>
      </div>
    </div>

    <!-- LLM 试跑 -->
    <div class="mp-card">
      <div class="mp-card-head">
        <div class="mp-card-title">LLM 试跑（不提交 ComfyUI、不写库）</div>
        <div class="mp-row">
          <n-input
            v-model:value="theme"
            size="small"
            placeholder="主题词（可空 = 随机抽签）"
            style="width: 220px"
            @keyup.enter="testLlm"
          />
          <n-button size="small" type="primary" :loading="llmLoading" @click="testLlm">试跑一次</n-button>
        </div>
      </div>

      <div class="mp-hint" style="margin-bottom: 10px">
        走的是真实链路：分层抽签 → LLM 按 JSON 契约写提示词 → 解析 → 人物/画风硬锚定 → 按模板白名单落宽高。
        下面「正向 / 负向提示词」是**最终注入 ComfyUI 的文本**（含硬拼接的画风标签与排除词），
        「LLM 原始返回」才是模型自己写的。图库里只能看到结果，这里能看到全过程与耗时。
      </div>

      <n-empty v-if="!llmRes && !llmLoading" description="点「试跑一次」看看模型写得怎么样" />
      <n-spin :show="llmLoading">
        <div v-if="llmRes" class="llm-result">
          <div v-if="!llmRes.ok" class="mp-err" style="margin-bottom: 10px">
            {{ llmRes.error || "（没有拿到错误详情：先看 AstrBot 控制台日志）" }}
          </div>
          <div class="mp-row" style="margin-bottom: 10px">
            <n-tag size="small" :bordered="false" :type="llmRes.ok ? 'success' : 'error'">
              {{ llmRes.ok ? "成功" : "失败" }}
            </n-tag>
            <n-tag size="small" :bordered="false">模板 {{ llmRes.template || "-" }}</n-tag>
            <n-tag size="small" :bordered="false">
              尺寸 {{ llmRes.size ? `${llmRes.size.width}×${llmRes.size.height}` : "-" }}
            </n-tag>
            <n-tag size="small" :bordered="false">风格 {{ llmRes.tag_style || "-" }}</n-tag>
            <n-tag
              size="small"
              :bordered="false"
              :type="llmRes.scope === 'anime' ? 'info' : llmRes.scope === 'photo' ? 'warning' : 'default'"
            >
              可画类型 {{ SCOPE_LABEL[llmRes.scope] || "-" }}
            </n-tag>
            <n-tag size="small" :bordered="false">LLM {{ fmtDur(llmRes.llm_ms) }}</n-tag>
            <n-tag size="small" :bordered="false">总 {{ fmtDur(llmRes.total_ms) }}</n-tag>
            <n-tag
              v-if="llmRes.tag_count"
              size="small"
              :bordered="false"
              :type="llmRes.tag_count > 75 ? 'warning' : 'default'"
            >
              标签 {{ llmRes.tag_count }}
            </n-tag>
            <n-tag v-if="llmRes.phase" size="small" :bordered="false">
              时段 {{ PHASE_LABEL[llmRes.phase] || llmRes.phase }}
            </n-tag>
            <n-tag v-if="llmRes.character" size="small" :bordered="false" type="info">
              角色 {{ llmRes.character }}
            </n-tag>
            <n-tag v-if="llmRes.time_fixed" size="small" :bordered="false" type="warning">
              已按时段纠偏
            </n-tag>
            <n-tag
              v-if="(llmRes.dropped || []).length"
              size="small"
              :bordered="false"
              type="warning"
              :title="(llmRes.dropped || []).join('、')"
            >
              压掉 {{ (llmRes.dropped || []).length }} 个冲突词
            </n-tag>
            <span class="mp-sub">{{ llmRes.llm_provider }}</span>
          </div>
          <div v-if="llmRes.args_note" class="mp-err" style="margin-top: 6px">
            指令参数：{{ llmRes.args_note }}
          </div>

          <div v-if="llmRes.brief" class="brief">
            <span v-for="(v, k) in llmRes.brief" :key="k" class="chip">
              {{ k }}：{{ v }}
            </span>
          </div>

          <div class="sect">正向提示词</div>
          <n-input :value="llmRes.prompt" type="textarea" readonly :autos="{ minRows: 3, maxRows: 8 }" />
          <div class="sect">负向提示词</div>
          <n-input :value="llmRes.negative" type="textarea" readonly :autos="{ minRows: 2, maxRows: 5 }" />
          <div class="sect">文案</div>
          <n-input :value="llmRes.caption" type="textarea" readonly :autos="{ minRows: 1, maxRows: 3 }" />
          <details class="raw">
            <summary>LLM 原始返回</summary>
            <pre class="mp-mono">{{ llmRes.raw || "（空）" }}</pre>
          </details>
        </div>
      </n-spin>
    </div>

    <!-- 日志 -->
    <div class="mp-card">
      <div class="mp-card-head">
        <div class="mp-card-title">事件日志（内存，最新 {{ logs.length }} 条）</div>
        <div class="mp-row">
          <label class="auto-tag">
            <n-switch v-model:value="logsAuto.enabled.value" size="small" />
            <span class="mp-sub">自动刷新 · {{ logsAutoText }}</span>
          </label>
          <n-select
            v-model:value="kindFilter"
            :options="[{ label: '全部类型', value: '' }, ...kinds.map((k) => ({ label: k, value: k }))]"
            size="small"
            style="width: 130px"
          />
          <n-button size="small" quaternary :loading="logsLoading" @click="loadLogs(false)">刷新</n-button>
        </div>
      </div>
      <n-empty v-if="!filteredLogs.length" description="还没有事件（出图/试跑/改配置后会出现在这里）" class="mp-empty" />
      <div v-else class="logs">
        <div v-for="(l, i) in filteredLogs" :key="i" class="logline" :class="l.kind">
          <span class="lt mp-mono">{{ fmtTsFull(l.ts).slice(5) }}</span>
          <n-tag size="tiny" :bordered="false">{{ l.kind }}</n-tag>
          <span class="lm">{{ l.msg }}</span>
        </div>
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

.auto-tag {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.env-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 12px;
}

.env-item {
  padding: 10px 12px;
  border-radius: 12px;
  background: var(--mp-panel-soft);
  border: 1px solid var(--mp-line);
  font-size: 12.5px;
  line-height: 1.6;
}

.break {
  word-break: break-all;
}

.two {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 14px;
}

.result {
  margin-top: 10px;
  padding: 10px 12px;
  border-radius: 10px;
  background: rgba(63, 157, 118, 0.1);
  font-size: 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  word-break: break-all;
}

.result.bad {
  background: rgba(217, 96, 95, 0.1);
}

.deliv-form {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.llm-result {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.brief {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  padding: 10px 12px;
  border-radius: 10px;
  background: var(--mp-panel-soft);
  border: 1px dashed var(--mp-line-strong);
}

.chip {
  font-size: 12px;
  padding: 2px 9px;
  border-radius: 999px;
  background: var(--mp-panel-solid);
  border: 1px solid var(--mp-line);
}

.sect {
  font-size: 12.5px;
  font-weight: 700;
  margin: 12px 0 6px;
}

.raw {
  margin-top: 12px;
}

.raw summary {
  cursor: pointer;
  font-size: 12px;
  color: var(--mp-sub);
}

.raw pre {
  margin: 8px 0 0;
  padding: 10px 12px;
  border-radius: 10px;
  background: var(--mp-panel-soft);
  border: 1px solid var(--mp-line);
  white-space: pre-wrap;
  word-break: break-all;
  font-size: 11.5px;
  max-height: 240px;
  overflow: auto;
}

.logs {
  display: flex;
  flex-direction: column;
  gap: 5px;
  max-height: 420px;
  overflow-y: auto;
}

.logline {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  font-size: 12px;
  line-height: 1.55;
  padding: 5px 8px;
  border-radius: 8px;
  border-left: 2px solid var(--mp-line-strong);
  background: var(--mp-panel-soft);
}

.logline.error {
  border-left-color: var(--mp-err);
  color: var(--mp-err);
}

.logline.generate {
  border-left-color: var(--mp-accent);
}

.logline.llm {
  border-left-color: var(--mp-accent-3);
}

.logline.delivery {
  border-left-color: var(--mp-ok);
}

.logline .lt {
  flex: 0 0 128px;
  color: var(--mp-sub);
}

.logline .lm {
  min-width: 0;
  word-break: break-all;
}
</style>
