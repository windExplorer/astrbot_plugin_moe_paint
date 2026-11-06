<script setup lang="ts">
// 工作流模板：清单编辑 + 校验状态 + 选择方式。
// 模板本体是 ComfyUI「导出 (API)」格式的 JSON，放在 workflows 目录里；
// 这里只编辑元信息，节点定位由后端自动完成（定位失败才填覆盖项）。
import { computed, onMounted, ref } from "vue";
import {
  NButton,
  NEmpty,
  NInput,
  NPopconfirm,
  NSelect,
  NSwitch,
  NTag,
  useMessage,
} from "naive-ui";
import { apiWorkflows } from "../api";
import { cfgGet, cfgLoaded, cfgSet, loadConfig, saveFields, saving } from "../config";
import { refreshStatus } from "../store";

const message = useMessage();

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

const rows = ref<TmplRow[]>([]);
const workflowsDir = ref("");
const selectionMode = computed({
  get: () => String(cfgGet("workflow_selection", "random")),
  set: (v: string) => cfgSet("workflow_selection", v),
});

const modeOptions = [
  { label: "随机挑一个可用模板", value: "random" },
  { label: "按顺序轮换（重启续轮）", value: "round_robin" },
];
const styleOptions = [
  { label: "danbooru（动漫英文标签）", value: "danbooru" },
  { label: "natural（写实中文自然语言）", value: "natural" },
];
const presetOptions = [
  { label: "sdxl · 1216×832 / 832×1216 / 1024²", value: "sdxl" },
  { label: "flux · 1344×768 / 768×1344 / 1024²", value: "flux" },
  { label: "sd15 · 768×512 / 512×768 / 512²", value: "sd15" },
];

function normalizeRow(r: any): TmplRow {
  return {
    name: String(r?.name || ""),
    file: String(r?.file || ""),
    tag_style: String(r?.tag_style || "danbooru"),
    size_preset: String(r?.size_preset || "sdxl"),
    default_negative: String(r?.default_negative || ""),
    enabled: r?.enabled !== false,
    positive_node: String(r?.positive_node || ""),
    negative_node: String(r?.negative_node || ""),
    latent_node: String(r?.latent_node || ""),
    positive_field: String(r?.positive_field || ""),
    negative_field: String(r?.negative_field || ""),
  };
}

function rowsFromCfg() {
  const raw = cfgGet("workflow_templates", []);
  rows.value = Array.isArray(raw) ? raw.map(normalizeRow) : [];
}

async function mergeValidation() {
  try {
    const w = await apiWorkflows();
    workflowsDir.value = w.workflows_dir || "";
    const byKey = new Map(w.templates.map((t) => [`${t.name}|${t.file}`, t]));
    for (const r of rows.value) {
      const t = byKey.get(`${r.name}|${r.file}`);
      r.ok = t?.ok;
      r.problem = t?.problem || "";
    }
  } catch {
    /* 校验信息拿不到就不显示 */
  }
}

async function reload() {
  await loadConfig(true);
  rowsFromCfg();
  await mergeValidation();
}

async function save() {
  const entries = rows.value.map((r) => ({
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
  if (entries.some((e) => !e.name || !e.file)) {
    message.warning("每个模板都要填名称与文件名");
    return;
  }
  cfgSet("workflow_templates", entries);
  const r1 = await saveFields(["workflow_selection", "workflow_templates"], "工作流");
  if (!r1.ok) {
    message.error(`保存失败：${r1.error}`);
    return;
  }
  message.success("工作流清单已保存，已重新校验");
  await refreshStatus();
  await reload();
}

function addRow() {
  rows.value.push(normalizeRow({ name: "新模板", file: "", enabled: true }));
}

function removeRow(i: number) {
  rows.value.splice(i, 1);
}

onMounted(async () => {
  await loadConfig();
  if (cfgLoaded.value) rowsFromCfg();
  await mergeValidation();
});
</script>

<template>
  <div class="page">
    <div class="mp-card">
      <div class="mp-card-head">
        <div class="mp-card-title">选择方式</div>
        <n-button size="small" type="primary" :loading="!!saving['工作流']" @click="save">保存</n-button>
      </div>
      <div class="mp-fields">
        <label class="mp-field">
          <span class="label">模板轮换</span>
          <n-select v-model:value="selectionMode" :options="modeOptions" size="small" />
          <span class="mp-sub">只有「启用 + 校验通过」的模板参与</span>
        </label>
        <label class="mp-field mp-field--wide">
          <span class="label">工作流目录</span>
          <div class="dir mp-mono">{{ workflowsDir || "（加载中）" }}</div>
          <span class="mp-sub">把 ComfyUI「导出 (API)」格式的 JSON 丢进这个目录，再在上面的清单里登记</span>
        </label>
      </div>
    </div>

    <div class="mp-card">
      <div class="mp-card-head">
        <div class="mp-card-title">模板清单（{{ rows.length }}）</div>
        <div class="mp-row">
          <n-button size="small" @click="addRow">添加模板</n-button>
          <n-button size="small" quaternary @click="mergeValidation().then(() => message.info('校验状态已刷新'))">
            刷新校验
          </n-button>
          <n-button size="small" type="primary" :loading="!!saving['工作流']" @click="save">保存清单</n-button>
        </div>
      </div>

      <div class="mp-hint" style="margin-bottom: 12px">
        节点定位是自动的：沿采样器的 positive / negative / latent_image 引用找到文本与尺寸节点。
        异形链路（CFGGuider、SamplerCustomAdvanced、非 text 输入框）定位失败时才填下面的覆盖项。
      </div>

      <n-empty v-if="!rows.length" description="还没有模板" class="mp-empty" />

      <div v-for="(r, i) in rows" :key="i" class="tmpl" :class="{ bad: r.ok === false }">
        <div class="tmpl-head">
          <n-tag size="small" :bordered="false" :type="r.ok === undefined ? 'default' : r.ok ? 'success' : 'error'">
            {{ r.ok === undefined ? "未校验" : r.ok ? "校验通过" : "校验失败" }}
          </n-tag>
          <span class="tmpl-name">{{ r.name || "（未命名）" }}</span>
          <span class="mp-mono mp-sub">{{ r.file || "（未填文件名）" }}</span>
          <div class="spacer"></div>
          <label class="enable">
            <span class="mp-sub">启用</span>
            <n-switch v-model:value="r.enabled" size="small" />
          </label>
          <n-popconfirm @positive-click="removeRow(i)">
            <template #trigger>
              <n-button size="tiny" type="error" quaternary>移除</n-button>
            </template>
            从清单里移除「{{ r.name }}」？（保存后生效，JSON 文件不会删）
          </n-popconfirm>
        </div>

        <div v-if="r.ok === false" class="mp-err" style="margin-bottom: 8px">{{ r.problem }}</div>

        <div class="mp-fields">
          <label class="mp-field">
            <span class="label">显示名称</span>
            <n-input v-model:value="r.name" size="small" />
          </label>
          <label class="mp-field">
            <span class="label">工作流文件名</span>
            <n-input v-model:value="r.file" size="small" placeholder="anima_t2i.json" />
          </label>
          <label class="mp-field">
            <span class="label">标签风格</span>
            <n-select v-model:value="r.tag_style" :options="styleOptions" size="small" />
          </label>
          <label class="mp-field">
            <span class="label">尺寸预设</span>
            <n-select v-model:value="r.size_preset" :options="presetOptions" size="small" />
          </label>
          <label class="mp-field mp-field--wide">
            <span class="label">默认负面词</span>
            <n-input v-model:value="r.default_negative" size="small" placeholder="留空 = 用工作流 JSON 里的负向" />
          </label>
        </div>

        <details class="advanced">
          <summary>节点覆盖（一般不用填）</summary>
          <div class="mp-fields" style="margin-top: 10px">
            <label class="mp-field">
              <span class="label">正向节点 ID</span>
              <n-input v-model:value="r.positive_node" size="small" placeholder="如 3" />
            </label>
            <label class="mp-field">
              <span class="label">负向节点 ID</span>
              <n-input v-model:value="r.negative_node" size="small" placeholder="如 4" />
            </label>
            <label class="mp-field">
              <span class="label">Latent 节点 ID</span>
              <n-input v-model:value="r.latent_node" size="small" placeholder="如 5" />
            </label>
            <label class="mp-field">
              <span class="label">正向输入框名</span>
              <n-input v-model:value="r.positive_field" size="small" placeholder="默认 text，如 Qwen 用 prompt" />
            </label>
            <label class="mp-field">
              <span class="label">负向输入框名</span>
              <n-input v-model:value="r.negative_field" size="small" placeholder="默认 text" />
            </label>
          </div>
        </details>
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

.dir {
  padding: 6px 10px;
  border-radius: 8px;
  background: var(--mp-panel-soft);
  border: 1px dashed var(--mp-line-strong);
  word-break: break-all;
}

.tmpl {
  border: 1px solid var(--mp-line);
  border-radius: 14px;
  padding: 12px 14px;
  margin-bottom: 12px;
  background: var(--mp-panel-solid);
}

.tmpl.bad {
  border-color: rgba(217, 96, 95, 0.45);
}

.tmpl-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}

.tmpl-name {
  font-weight: 700;
  font-size: 13px;
}

.spacer {
  flex: 1;
}

.enable {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.advanced {
  margin-top: 10px;
}

.advanced summary {
  cursor: pointer;
  font-size: 12px;
  color: var(--mp-sub);
  user-select: none;
}

.advanced summary:hover {
  color: var(--mp-accent);
}
</style>
