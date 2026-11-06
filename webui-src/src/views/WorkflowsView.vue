<script setup lang="ts">
// 工作流模板：来源（本插件库 / 跟随萌绘）、上传解析入库、清单编辑。
// 关键点：上传就解析——解析不过直接拒绝入库，不让坏工作流在出图时才炸。
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
import {
  apiAnimaWorkflows,
  apiWorkflows,
  apiWorkflowParse,
  apiWorkflowUpload,
  type AnimaWorkflowPayload,
  type TemplatePublic,
  type WorkflowAnalyze,
} from "../api";
import { cfgGet, cfgSet, loadConfig, saveFields, saving } from "../config";
import { refreshStatus, status } from "../store";
import { splitNames } from "../utils";

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
  nodes?: Record<string, any>;
  warnings?: string[];
}

const rows = ref<TmplRow[]>([]);
const workflowsDir = ref("");
const animaInfo = ref<AnimaWorkflowPayload | null>(null);
const loading = ref(false);

const source = computed({
  get: () => String(cfgGet("workflow_source", "local")),
  set: (v: string) => cfgSet("workflow_source", v),
});
/** 参与轮换的萌绘工作流（多选；存成逗号分隔字符串，空 = 全部可用）。 */
const animaPicked = computed<string[]>({
  get: () => splitNames(cfgGet("anima.workflow", "")),
  set: (v: string[]) => cfgSet("anima.workflow", (v || []).join(",")),
});
const animaPreset = computed({
  get: () => String(cfgGet("anima.size_preset", "follow")),
  set: (v: string) => cfgSet("anima.size_preset", v),
});
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
  { label: "跟随工作流（用 latent 默认尺寸）", value: "follow" },
  { label: "sdxl · 1216×832 / 832×1216 / 1024²", value: "sdxl" },
  { label: "flux · 1344×768 / 768×1344 / 1024²", value: "flux" },
  { label: "sd15 · 768×512 / 512×768 / 512²", value: "sd15" },
];
const localPresetOptions = presetOptions.filter((o) => o.value !== "follow");

// 上传
const jsonText = ref("");
const upName = ref("");
const upStyle = ref("danbooru");
const upPreset = ref("sdxl");
const upNegative = ref("");
const preview = ref<WorkflowAnalyze | null>(null);
const parsing = ref(false);
const uploading = ref(false);
const dragging = ref(false);
const fileInput = ref<HTMLInputElement | null>(null);

const animaRows = computed(() => animaInfo.value?.workflows || []);
const animaOptions = computed(() =>
  animaRows.value.map((w) => ({
    label:
      `${w.name}` +
      (w.model_file ? ` · ${w.model_file}` : "") +
      (w.supported ? "" : `（不可用：${w.reason || "未知"}）`),
    value: w.name,
  })),
);

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
    ok: r?.ok,
    problem: String(r?.problem || ""),
    nodes: r?.nodes || {},
    warnings: r?.warnings || [],
  };
}

function rowsFromCfg() {
  const raw = cfgGet("workflow_templates", []);
  rows.value = Array.isArray(raw) ? raw.map(normalizeRow) : [];
}

function mergeValidation(templates: TemplatePublic[]) {
  const byKey = new Map(templates.map((t) => [`${t.name}|${t.file}`, t]));
  for (const r of rows.value) {
    const t = byKey.get(`${r.name}|${r.file}`);
    if (t) {
      r.ok = t.ok;
      r.problem = t.problem;
      r.nodes = t.nodes;
      r.warnings = t.warnings || [];
    }
  }
}

async function reloadAll() {
  loading.value = true;
  try {
    await loadConfig(true);
    rowsFromCfg();
    const w = await apiWorkflows();
    workflowsDir.value = w.workflows_dir || "";
    mergeValidation(w.templates || []);
    if (source.value === "anima") {
      animaInfo.value = w.anima || (await apiAnimaWorkflows());
    } else {
      animaInfo.value = null;
    }
  } catch (e: any) {
    message.error(`加载工作流失败：${e?.message || e}`);
  } finally {
    loading.value = false;
  }
}

async function refreshAnima() {
  try {
    animaInfo.value = await apiAnimaWorkflows();
    if (animaInfo.value.error) message.warning(animaInfo.value.error);
  } catch (e: any) {
    message.error(`读取萌绘工作流失败：${e?.message || e}`);
  }
}

async function saveSource() {
  const r = await saveFields(
    ["workflow_source", "workflow_selection", "anima.workflow", "anima.size_preset"],
    "工作流来源",
  );
  if (!r.ok) {
    message.error(`保存失败：${r.error}`);
    return;
  }
  message.success("已保存，下一张起生效");
  await Promise.all([refreshStatus(), reloadAll()]);
}

async function switchSource(v: string) {
  if (source.value === v) return;
  source.value = v;
  await saveSource();
}

const animaUsable = computed(() => animaRows.value.filter((w) => w.supported && w.enabled));

/** 实际参与轮换的名单：显式选择优先，留空 = 全部可用。 */
const animaEffective = computed<string[]>(() =>
  animaPicked.value.length ? animaPicked.value : animaUsable.value.map((w) => w.name),
);

function isPicked(name: string): boolean {
  return animaEffective.value.includes(name);
}

function togglePick(name: string) {
  const cur = animaEffective.value.slice();
  const next = cur.includes(name) ? cur.filter((n) => n !== name) : [...cur, name];
  animaPicked.value = next;
}

async function resetPicks() {
  animaPicked.value = [];
  await saveSource();
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
  const r1 = await saveFields(["workflow_templates"], "工作流");
  if (!r1.ok) {
    message.error(`保存失败：${r1.error}`);
    return;
  }
  message.success("模板清单已保存，已重新校验");
  await Promise.all([refreshStatus(), reloadAll()]);
}

function addRow() {
  rows.value.push(normalizeRow({ name: "新模板", file: "", enabled: true }));
}

function removeRow(i: number) {
  rows.value.splice(i, 1);
}

// ---------------- 上传 ----------------

function readFile(f: File | undefined | null) {
  if (!f) return;
  const reader = new FileReader();
  reader.onload = () => {
    jsonText.value = String(reader.result || "");
    if (!upName.value) upName.value = f.name.replace(/\.json$/i, "");
    preview.value = null;
    message.info(`已读入 ${f.name}（${(f.size / 1024).toFixed(1)} KB），点「解析预览」检查`);
  };
  reader.onerror = () => message.error("读取文件失败");
  reader.readAsText(f);
}

function onPick(e: Event) {
  const input = e.target as HTMLInputElement;
  readFile(input.files?.[0]);
  input.value = "";
}

function onDrop(e: DragEvent) {
  dragging.value = false;
  readFile(e.dataTransfer?.files?.[0]);
}

async function doParse() {
  if (!jsonText.value.trim()) {
    message.warning("先选择或粘贴一份工作流 JSON");
    return;
  }
  parsing.value = true;
  preview.value = null;
  try {
    preview.value = await apiWorkflowParse(jsonText.value);
    if (preview.value.ok) message.success(`解析通过：${preview.value.node_count} 个节点`);
    else message.warning(preview.value.problems?.[0] || preview.value.errors?.[0] || "解析未通过");
  } catch (e: any) {
    message.error(`解析失败：${e?.message || e}`);
  } finally {
    parsing.value = false;
  }
}

async function doUpload() {
  if (!upName.value.trim()) {
    message.warning("给这个工作流起个名字");
    return;
  }
  uploading.value = true;
  try {
    const r = await apiWorkflowUpload({
      name: upName.value.trim(),
      json_text: jsonText.value,
      tag_style: upStyle.value,
      size_preset: upPreset.value,
      default_negative: upNegative.value,
      enabled: true,
    });
    message.success(`已入库「${r.name}」→ ${r.file}${r.replaced ? "（覆盖了同名模板）" : ""}`);
    jsonText.value = "";
    upName.value = "";
    upNegative.value = "";
    preview.value = null;
    await Promise.all([refreshStatus(), reloadAll()]);
  } catch (e: any) {
    message.error(`入库失败：${e?.message || e}`);
  } finally {
    uploading.value = false;
  }
}

function nodeHint(r: TmplRow): string {
  const n = r.nodes || {};
  const bits: string[] = [];
  if (n.model_file) bits.push(String(n.model_file));
  bits.push(`正/负 ${n.positive || "?"}/${n.negative || "?"}`);
  bits.push(`尺寸 ${n.latent || "?"}${n.default_width ? `（${n.default_width}×${n.default_height}）` : ""}`);
  bits.push(`seed ${n.seed_nodes ?? 0} 处`);
  return bits.join(" · ");
}

onMounted(reloadAll);
</script>

<template>
  <div class="page">
    <!-- 来源 -->
    <div class="mp-card">
      <div class="mp-card-head">
        <div class="mp-card-title">工作流来源</div>
        <div class="mp-row">
          <n-tag size="small" :bordered="false">
            {{ status?.templates?.length ? `当前可用 ${status.templates.filter((t) => t.ok && t.enabled).length} 个` : "无可用模板" }}
          </n-tag>
          <n-button size="small" type="primary" :loading="!!saving['工作流来源']" @click="saveSource">保存</n-button>
        </div>
      </div>

      <div class="modes">
        <button class="mode" :class="{ active: source === 'local' }" @click="switchSource('local')">
          <b>📚 本插件工作流库</b>
          <span class="mp-sub">上传 ComfyUI「导出 (API)」JSON 到本插件，独立于萌绘</span>
        </button>
        <button class="mode" :class="{ active: source === 'anima' }" @click="switchSource('anima')">
          <b>🪄 跟随萌绘的工作流</b>
          <span class="mp-sub">直接用萌绘控制台里配好的工作流（含它设的采样器/放大/保存覆盖）</span>
        </button>
      </div>

      <div class="mp-fields block">
        <label class="mp-field">
          <span class="label">模板轮换</span>
          <n-select v-model:value="selectionMode" :options="modeOptions" size="small" />
          <span class="mp-sub">真正出图时从参与名单里挑：随机 / 顺序轮换</span>
        </label>
        <label v-if="source === 'local'" class="mp-field mp-field--wide">
          <span class="label">工作流目录</span>
          <div class="dir mp-mono">{{ workflowsDir || "（加载中）" }}</div>
          <span class="mp-sub">上传会自动写到这里；也可以手动丢 JSON 进来再登记</span>
        </label>
      </div>

      <div v-if="source === 'anima'" class="block">
        <div class="mp-fields">
          <label class="mp-field mp-field--wide">
            <span class="label">参与轮换的萌绘工作流（可多选）</span>
            <n-select
              v-model:value="animaPicked"
              :options="animaOptions"
              size="small"
              multiple
              clearable
              filterable
              placeholder="留空 = 萌绘里所有可用的新版工作流都参与"
            />
            <span class="mp-sub">
              已选 {{ animaPicked.length || "（全部）" }} 条；实际参与
              {{ animaEffective.length }} 条（可用 {{ animaUsable.length }} 条）。
              不勾选任何一条 = 全部可用都参与轮换；改完记得点右上角「保存」。
            </span>
          </label>
          <label class="mp-field">
            <span class="label">尺寸策略</span>
            <n-select v-model:value="animaPreset" :options="presetOptions" size="small" />
            <span class="mp-sub">follow = 直接用各自基础工作流 latent 的宽高</span>
          </label>
        </div>
        <div class="mp-row" style="margin-top: 10px">
          <n-button size="small" quaternary :loading="loading" @click="refreshAnima">刷新萌绘工作流列表</n-button>
          <n-button size="small" quaternary :disabled="!animaPicked.length" @click="resetPicks">
            恢复「全部参与」
          </n-button>
          <span v-if="animaInfo?.error" class="mp-err">{{ animaInfo.error }}</span>
        </div>

        <div v-if="animaRows.length" class="anima-list">
          <div v-for="w in animaRows" :key="w.key || w.name" class="anima-row" :class="{ bad: !w.supported, off: !isPicked(w.name) }">
            <div class="anima-main">
              <div class="anima-name">
                {{ w.name }}
                <n-tag v-if="!w.enabled" size="tiny" :bordered="false">萌绘里已停用</n-tag>
                <n-tag v-if="w.supported" size="tiny" :bordered="false" type="success">可用</n-tag>
                <n-tag v-else size="tiny" :bordered="false" type="error">不可用</n-tag>
                <n-tag v-if="w.supported && isPicked(w.name)" size="tiny" :bordered="false" type="info">参与中</n-tag>
              </div>
              <div class="mp-sub">
                基础工作流：{{ w.base_name || "—" }}
                <template v-if="w.model_file"> · 底模 {{ w.model_file }}</template>
              </div>
              <div v-if="!w.supported && w.reason" class="mp-err">{{ w.reason }}</div>
            </div>
            <n-button
              size="small"
              :disabled="!w.supported"
              :type="isPicked(w.name) ? 'default' : 'primary'"
              quaternary
              @click="togglePick(w.name)"
            >
              {{ isPicked(w.name) ? "移出轮换" : "加入轮换" }}
            </n-button>
          </div>
        </div>
        <n-empty v-else description="没读到萌绘的工作流（萌绘未安装 / 未激活 / 还没建工作流）" size="small" />
      </div>
    </div>

    <!-- 上传（仅本插件库） -->
    <div v-if="source === 'local'" class="mp-card">
      <div class="mp-card-head">
        <div class="mp-card-title">上传工作流（自动解析）</div>
        <span class="mp-sub">只认 ComfyUI「导出 (API)」格式</span>
      </div>

      <div
        class="dropzone"
        :class="{ hot: dragging }"
        @dragover.prevent="dragging = true"
        @dragleave.prevent="dragging = false"
        @drop.prevent="onDrop"
      >
        <div class="dz-main">把 .json 拖进来，或</div>
        <n-button size="small" @click="fileInput?.click()">选择文件</n-button>
        <input ref="fileInput" type="file" accept=".json,application/json" hidden @change="onPick" />
        <div class="mp-sub">也可以直接把 JSON 内容粘贴到下面</div>
      </div>

      <n-input
        v-model:value="jsonText"
        type="textarea"
        :rows="5"
        size="small"
        class="mp-mono"
        placeholder='{"3": {"class_type": "CLIPTextEncode", "inputs": {"text": "..."}}}'
        @update:value="preview = null"
      />

      <div class="mp-fields up-fields">
        <label class="mp-field">
          <span class="label">模板名称</span>
          <n-input v-model:value="upName" size="small" placeholder="如 anima 动漫 / 写实 SDXL" />
        </label>
        <label class="mp-field">
          <span class="label">标签风格</span>
          <n-select v-model:value="upStyle" :options="styleOptions" size="small" />
        </label>
        <label class="mp-field">
          <span class="label">尺寸预设</span>
          <n-select v-model:value="upPreset" :options="localPresetOptions" size="small" />
        </label>
        <label class="mp-field">
          <span class="label">默认负面词（可空）</span>
          <n-input v-model:value="upNegative" size="small" placeholder="留空 = 用工作流自带负向" />
        </label>
      </div>

      <div class="mp-row up-actions">
        <n-button size="small" :loading="parsing" @click="doParse">解析预览</n-button>
        <n-button size="small" type="primary" :loading="uploading" :disabled="!jsonText.trim()" @click="doUpload">
          确认入库
        </n-button>
        <span class="mp-sub">入库会覆盖同名模板（文件同名直接覆盖）</span>
      </div>

      <div v-if="preview" class="preview" :class="{ bad: !preview.ok }">
        <div class="mp-row">
          <n-tag size="small" :type="preview.ok ? 'success' : 'error'" :bordered="false">
            {{ preview.ok ? "解析通过" : "解析未通过" }}
          </n-tag>
          <span class="mp-sub">{{ preview.node_count }} 个节点</span>
          <span v-if="preview.nodes.model_file" class="mp-mono">{{ preview.nodes.model_file }}</span>
        </div>
        <div class="pnode-grid">
          <span>正向节点 <b>{{ preview.nodes.positive || "—" }}</b></span>
          <span>负向节点 <b>{{ preview.nodes.negative || "—" }}</b></span>
          <span>尺寸节点 <b>{{ preview.nodes.latent || "—" }}</b> {{ preview.nodes.latent_class }}</span>
          <span>默认尺寸 <b>{{ preview.nodes.default_width || "?" }}×{{ preview.nodes.default_height || "?" }}</b></span>
          <span>保存节点 <b>{{ preview.nodes.save || "—" }}</b> {{ preview.nodes.save_class }}</span>
          <span>seed 节点 <b>{{ preview.nodes.seed_nodes }}</b> 处</span>
        </div>
        <div v-for="p in preview.problems" :key="p" class="mp-err">× {{ p }}</div>
        <div v-for="w in preview.warnings" :key="w" class="mp-sub">· {{ w }}</div>
      </div>
    </div>

    <!-- 清单（仅本插件库） -->
    <div v-if="source === 'local'" class="mp-card">
      <div class="mp-card-head">
        <div class="mp-card-title">模板清单（{{ rows.length }}）</div>
        <div class="mp-row">
          <n-button size="small" @click="addRow">手动添加</n-button>
          <n-button size="small" quaternary :loading="loading" @click="reloadAll">刷新校验</n-button>
          <n-button size="small" type="primary" :loading="!!saving['工作流']" @click="save">保存清单</n-button>
        </div>
      </div>

      <div class="mp-hint" style="margin-bottom: 12px">
        节点定位自动完成（沿采样器的 positive / negative / latent_image 引用找）。
        异形链路（CFGGuider、SamplerCustomAdvanced、非 text 输入框）定位失败时才填下面的覆盖项。
      </div>

      <n-empty v-if="!rows.length" description="还没有模板，先上传一份" class="mp-empty" />

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
        <div v-else class="node-line mp-mono">{{ nodeHint(r) }}</div>
        <div v-for="w in r.warnings || []" :key="w" class="mp-sub warnline">· {{ w }}</div>

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
            <n-select v-model:value="r.size_preset" :options="localPresetOptions" size="small" />
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

.modes {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 12px;
}

.mode {
  display: flex;
  flex-direction: column;
  gap: 4px;
  text-align: left;
  padding: 14px 16px;
  border-radius: 14px;
  border: 1.5px solid var(--mp-line);
  background: var(--mp-panel-solid);
  color: var(--mp-text);
  cursor: pointer;
  font: inherit;
  transition: box-shadow 0.15s ease, transform 0.15s ease;
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
  box-shadow: 0 8px 22px rgba(107, 120, 224, 0.18);
}

.mode b {
  font-size: 13.5px;
}

.block {
  margin-top: 14px;
}

.dir {
  padding: 6px 10px;
  border-radius: 8px;
  background: var(--mp-panel-soft);
  border: 1px dashed var(--mp-line-strong);
  word-break: break-all;
}

.dropzone {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  padding: 16px;
  margin-bottom: 10px;
  border-radius: 12px;
  border: 1.5px dashed var(--mp-line-strong);
  background: var(--mp-panel-soft);
  transition: border-color 0.15s ease, background 0.15s ease;
}

.dropzone.hot {
  border-color: var(--mp-accent);
  background: rgba(107, 120, 224, 0.08);
}

.dz-main {
  font-size: 13px;
  font-weight: 600;
}

.up-fields {
  margin-top: 12px;
}

.up-actions {
  margin-top: 12px;
}

.preview {
  margin-top: 12px;
  padding: 12px 14px;
  border-radius: 12px;
  background: rgba(63, 157, 118, 0.08);
  border: 1px solid rgba(63, 157, 118, 0.3);
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.preview.bad {
  background: rgba(217, 96, 95, 0.08);
  border-color: rgba(217, 96, 95, 0.35);
}

.pnode-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 4px 12px;
  font-size: 12px;
  color: var(--mp-sub);
}

.pnode-grid b {
  color: var(--mp-text);
}

.anima-list {
  margin-top: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.anima-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 12px;
  border-radius: 12px;
  border: 1px solid var(--mp-line);
  background: var(--mp-panel-solid);
}

.anima-row.bad {
  border-color: rgba(217, 96, 95, 0.35);
}

.anima-row.off {
  opacity: 0.6;
}

.anima-name {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 700;
  font-size: 13px;
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
  margin-bottom: 8px;
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

.node-line {
  font-size: 11.5px;
  color: var(--mp-sub);
  margin-bottom: 10px;
  word-break: break-all;
}

.warnline {
  font-size: 11.5px;
  margin-bottom: 4px;
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
