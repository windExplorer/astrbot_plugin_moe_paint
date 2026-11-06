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
  apiResetRotation,
  apiWorkflows,
  apiWorkflowParse,
  apiWorkflowUpload,
  type AnimaWorkflowPayload,
  type RotationPayload,
  type TemplatePublic,
  type WorkflowAnalyze,
} from "../api";
import { cfgBool, cfgGet, cfgSet, loadConfig, saveFields, saving } from "../config";
import { refreshStatus, status } from "../store";
import { splitNames } from "../utils";

const message = useMessage();

interface TmplRow {
  name: string;
  file: string;
  tag_style: string;
  size_preset: string;
  params_preset: string;
  prompt_profile: string;
  subject_scope: string;
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
  profile?: Record<string, any>;
  quality_prefix?: string;
  eff_style?: string;
  eff_preset?: string;
  eff_scope?: string;
  rotation_no?: number;
}

const EMPTY_ROTATION: RotationPayload = {
  mode: "random", ordered: false, total: 0, items: [], next_index: 0,
  next_name: "", next_key: "", last_key: "", last_found: false, manual_advances: false,
};

const rows = ref<TmplRow[]>([]);
const workflowsDir = ref("");
const animaInfo = ref<AnimaWorkflowPayload | null>(null);
const rotation = ref<RotationPayload>({ ...EMPTY_ROTATION, items: [] });
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
const animaProfile = computed({
  get: () => String(cfgGet("anima.prompt_profile", "auto")),
  set: (v: string) => cfgSet("anima.prompt_profile", v),
});
/** 跟随萌绘时的整体可画类型：逐条没配的回落到它（默认 auto = 按底模推断）。 */
const animaScope = computed({
  get: () => String(cfgGet("anima.subject_scope", "auto")),
  set: (v: string) => cfgSet("anima.subject_scope", v),
});
/** 逐条可画类型：「名字或id=auto|anime|photo|both」，逗号分隔；空 = 回落到整体值。 */
const animaScopesText = computed({
  get: () => String(cfgGet("anima.workflow_scopes", "")),
  set: (v: string) => cfgSet("anima.workflow_scopes", v),
});
const selectionMode = computed({
  get: () => String(cfgGet("workflow_selection", "random")),
  set: (v: string) => cfgSet("workflow_selection", v),
});
/** 手动出图是否占用一个轮换位（默认不占）。 */
const manualAdvances = computed({
  get: () => cfgBool("rotation_manual_advances", false),
  set: (v: boolean) => cfgSet("rotation_manual_advances", v),
});

const modeOptions = [
  { label: "随机挑一个可用模板", value: "random" },
  { label: "按顺序轮换（清单从上到下 · 重启续轮）", value: "round_robin" },
];
const styleOptions = [
  { label: "danbooru（动漫英文标签）", value: "danbooru" },
  { label: "natural（中文自然语言 · Qwen）", value: "natural" },
  { label: "natural_en（英文自然语言 · Flux/写实）", value: "natural_en" },
];
const profileOptions = [
  { label: "自动（按底模判断）", value: "auto" },
  ...styleOptions,
];
const scopeOptions = [
  { label: "自动判断（推荐，按底模推断）", value: "auto" },
  { label: "只画动漫（二次元）", value: "anime" },
  { label: "只画真人（写实）", value: "photo" },
  { label: "两者皆可（不限制）", value: "both" },
];
/** 逐条可画类型下拉：空 = 不单独设置，回落到整体值。 */
const perScopeOptions = [
  { label: "跟随整体设置", value: "" },
  ...scopeOptions,
];
const SCOPE_LABEL: Record<string, string> = {
  anime: "只画动漫", photo: "只画真人", both: "两者皆可", auto: "自动判断",
};
const presetOptions = [
  { label: "跟随工作流（用 latent 默认尺寸）", value: "follow" },
  { label: "sdxl · 1216×832 / 832×1216 / 1024²", value: "sdxl" },
  { label: "flux · 1344×768 / 768×1344 / 1024²", value: "flux" },
  { label: "sd15 · 768×512 / 512×768 / 512²", value: "sd15" },
  { label: "qwen · 1664×928 / 928×1664 / 1328²", value: "qwen" },
];
const localPresetOptions = presetOptions.filter((o) => o.value !== "follow");
/** 采样参数档（v0.7.8）：keep = 什么都不改 */
const paramsPresetOptions = [
  { label: "沿用工作流原参数", value: "keep" },
  { label: "快出 · 20 步 / CFG 5", value: "fast" },
  { label: "标准 · 28 步 / CFG 6", value: "standard" },
  { label: "精修 · 36 步 / CFG 6.5", value: "high" },
];

// 上传
const jsonText = ref("");
const upName = ref("");
const upStyle = ref("danbooru");
const upPreset = ref("sdxl");
const upProfile = ref("auto");
const upScope = ref("auto");
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
    params_preset: String(r?.params_preset || "keep"),
    prompt_profile: String(r?.prompt_profile || "auto"),
    subject_scope: String(r?.subject_scope || "auto"),
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

function mergeValidation(templates: TemplatePublic[], rot: RotationPayload) {
  const byKey = new Map(templates.map((t) => [`${t.name}|${t.file}`, t]));
  // 后端轮换名单里的 file 一定带 .json，清单里手填可能省略，先归一化再对号
  const norm = (f: string) => (String(f || "").endsWith(".json") ? String(f) : `${f}.json`);
  const orderByFile = new Map((rot?.items || []).map((i) => [norm(i.file), i.index]));
  for (const r of rows.value) {
    r.rotation_no = orderByFile.get(norm(r.file)) || 0;
    const t = byKey.get(`${r.name}|${r.file}`);
    if (t) {
      r.ok = t.ok;
      r.problem = t.problem;
      r.nodes = t.nodes;
      r.warnings = t.warnings || [];
      r.profile = (t as any).profile || {};
      r.quality_prefix = (t as any).quality_prefix || "";
      r.eff_style = t.tag_style;
      r.eff_preset = t.size_preset;
      r.eff_scope = (t as any).scope || "";
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
    rotation.value = w.rotation || { ...EMPTY_ROTATION, items: [] };
    mergeValidation(w.templates || [], rotation.value);
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
    [
      "workflow_source",
      "workflow_selection",
      "rotation_manual_advances",
      "anima.workflow",
      "anima.size_preset",
      "anima.prompt_profile",
      "anima.subject_scope",
      "anima.workflow_scopes",
    ],
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

/** 名单是否显式固定：空名单 = 跟随萌绘列表顺序，此时不能在这边排序。 */
const animaPinned = computed(() => animaPicked.value.length > 0);

function parseScopesText(raw: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const part of String(raw || "").split(/[,，;；\n\r]+/)) {
    const m = part.match(/^\s*([^=：:]+?)\s*[=：:]\s*(.+?)\s*$/);
    if (!m) continue;
    const v = m[2].trim().toLowerCase();
    if (["auto", "anime", "photo", "both"].includes(v)) out[m[1].trim()] = v;
  }
  return out;
}

const scopeMap = computed(() => parseScopesText(animaScopesText.value));

function scopeOf(name: string): string {
  return scopeMap.value[name] || "";
}

function setScopeOf(name: string, v: string) {
  const m = { ...scopeMap.value };
  if (!v) delete m[name];
  else m[name] = v;
  animaScopesText.value = Object.entries(m).map(([k, val]) => `${k}=${val}`).join(",");
}

/** 后端算出的实际生效类型（逐条覆盖 + 整体回落后，再由底模推断的结果）。 */
function effectiveScopeOf(name: string): string {
  return rotation.value.items.find((x) => x.name === name)?.scope || "both";
}

function movePick(name: string, delta: number) {
  const list = animaEffective.value.slice();
  const i = list.indexOf(name);
  const j = i + delta;
  if (i < 0 || j < 0 || j >= list.length) return;
  [list[i], list[j]] = [list[j], list[i]];
  animaPicked.value = list;
}

/** 把「全部可用」固化成显式名单，之后才能上下调整顺序。 */
function pinOrder() {
  animaPicked.value = animaUsable.value.map((w) => w.name);
  message.success("已固定当前顺序，现在可以上下调整；记得点保存");
}

async function doResetRotation() {
  try {
    const r = await apiResetRotation();
    if (!r?.ok) {
      message.error("重置失败");
      return;
    }
    message.success("顺序轮换已重置到 #1");
    await reloadAll();
  } catch (e: any) {
    message.error(`重置失败：${e?.message || e}`);
  }
}

async function save() {
  const entries = rows.value.map((r) => ({
    name: r.name.trim(),
    file: r.file.trim(),
    tag_style: r.tag_style,
    size_preset: r.size_preset,
    params_preset: r.params_preset,
    prompt_profile: r.prompt_profile,
    subject_scope: r.subject_scope,
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
  renumberLocal();
}

/** 清单行顺序就是轮换顺序；移动后立刻本地重编号，保存后以后端校验结果为准。 */
function moveRow(i: number, delta: number) {
  const j = i + delta;
  if (j < 0 || j >= rows.value.length) return;
  const list = rows.value.slice();
  [list[i], list[j]] = [list[j], list[i]];
  rows.value = list;
  renumberLocal();
}

function renumberLocal() {
  let n = 0;
  for (const r of rows.value) {
    r.rotation_no = r.enabled && r.ok !== false ? ++n : 0;
  }
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
      prompt_profile: upProfile.value,
      subject_scope: upScope.value,
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

/** 生效的提示词/尺寸（auto 时可能与模板里手填的不同）。 */
function effHint(r: TmplRow): string {
  const p = r.profile || {};
  const bits: string[] = [];
  if (p.label) bits.push(`底模画像 ${p.label}`);
  bits.push(`提示词 ${r.eff_style || r.tag_style}`);
  bits.push(`尺寸 ${r.eff_preset || r.size_preset}`);
  bits.push(`可画类型 ${SCOPE_LABEL[r.eff_scope || r.subject_scope] || "自动判断"}`);
  if (r.quality_prefix) bits.push("自动加质量词");
  // 可画类型不会换底模：标了只画动漫却用写实底模，硬锚定也变不出二次元
  const sc = r.eff_scope || "both";
  const st = r.eff_style || r.tag_style;
  if (sc === "anime" && st !== "danbooru") {
    bits.push("⚠ 当前底模用自然语言提示词，要画二次元建议换动漫底模");
  } else if (sc === "photo" && st === "danbooru") {
    bits.push("⚠ 当前是动漫标签系底模，要画写实建议换写实底模");
  }
  return bits.join(" · ");
}

onMounted(reloadAll);
</script>

<template>
  <div class="page">
    <!-- 轮换顺序：放第一屏，直接回答「顺序是什么 / 下一张是谁」 -->
    <div class="mp-card">
      <div class="mp-card-head">
        <div class="mp-card-title">轮换顺序</div>
        <div class="mp-row">
          <n-tag size="small" :bordered="false" :type="rotation.ordered ? 'info' : 'default'">
            {{ rotation.ordered ? "顺序轮换" : "随机挑选（无顺序）" }}
          </n-tag>
          <n-button size="small" quaternary :loading="loading" @click="reloadAll">刷新</n-button>
          <n-popconfirm @positive-click="doResetRotation">
            <template #trigger>
              <n-button size="small" quaternary :disabled="!rotation.ordered || !rotation.total">
                从 #1 重置
              </n-button>
            </template>
            清空轮换进度，下一张从 #1 开始？
          </n-popconfirm>
        </div>
      </div>

      <div class="mp-hint" style="margin-bottom: 10px">
        顺序 = <b>{{ source === "anima" ? "下面「参与名单」从上到下" : "「模板清单」从上到下" }}</b>，
        只数<b>已启用且校验通过</b>的模板；出图成功才推进，重启续轮。
        停用或新增模板不会让顺序错位：插件记的是「上一张是谁」，不是第几张。
      </div>

      <div v-if="!rotation.ordered" class="mp-hint">
        当前是随机模式，没有固定顺序；把下面「模板轮换」改成「按顺序轮换」即可看到编号。
      </div>
      <template v-else>
        <div v-if="!rotation.total" class="mp-err">没有可用模板（检查启用状态与校验结果）</div>
        <template v-else>
          <div class="rot-next">
            下一张：<b>#{{ rotation.next_index }} {{ rotation.next_name }}</b>
            <span class="mp-sub">
              （共 {{ rotation.total }} 张{{ rotation.last_key && !rotation.last_found ? "；上一张已不在名单里，从 #1 重新开始" : "" }}）
            </span>
          </div>
          <div class="rot-chain">
            <span
              v-for="it in rotation.items"
              :key="it.key"
              class="rot-item"
              :class="{ next: it.index === rotation.next_index }"
            >
              <i>#{{ it.index }}</i>{{ it.name }}
            </span>
            <span class="rot-loop mp-sub">↻ 回到 #1</span>
          </div>
        </template>
      </template>

      <div class="switch-row" style="margin-top: 12px">
        <n-switch v-model:value="manualAdvances" size="small" />
        <span>手动出图也占一个轮换位</span>
        <n-button size="small" type="primary" :loading="!!saving['工作流来源']" @click="saveSource">
          保存开关
        </n-button>
        <span class="mp-sub">
          关闭（默认）：「立即画一张」与 /绘卷 画一张 不推进轮换；失败轮一律不占位
        </span>
      </div>
    </div>

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
          <label class="mp-field">
            <span class="label">提示词适配</span>
            <n-select v-model:value="animaProfile" :options="profileOptions" size="small" />
            <span class="mp-sub">自动 = 萌绘底模库判定优先，其次看底模文件名</span>
          </label>
          <label class="mp-field">
            <span class="label">可画类型（整体默认）</span>
            <n-select v-model:value="animaScope" :options="scopeOptions" size="small" />
            <span class="mp-sub">没在下面「轮换顺序」里逐条设置的工作流，都用这个值</span>
          </label>
        </div>
        <div class="mp-row" style="margin-top: 10px">
          <n-button size="small" quaternary :loading="loading" @click="refreshAnima">刷新萌绘工作流列表</n-button>
          <n-button size="small" quaternary :disabled="!animaPicked.length" @click="resetPicks">
            恢复「全部参与」
          </n-button>
          <span v-if="animaInfo?.error" class="mp-err">{{ animaInfo.error }}</span>
        </div>

        <div class="order-box">
          <div class="mp-row">
            <div class="mp-card-title" style="font-size: 13px">
              轮换顺序（{{ animaEffective.length }} 条）
            </div>
            <n-button v-if="!animaPinned" size="small" @click="pinOrder">固定当前顺序以便排序</n-button>
          </div>
          <div class="mp-hint" style="margin: 6px 0">
            {{ animaPinned
              ? "名单从上到下就是出图顺序，用 ↑↓ 调整后点「保存」。"
              : "当前未显式选择，按萌绘列表顺序参与；点右上按钮固定成名单后即可排序。" }}
            每行还能单独设「可画类型」；选「跟随整体设置」即用上面的整体默认（默认按底模自动判断）。
          </div>
          <div v-for="(n, idx) in animaEffective" :key="n" class="order-row">
            <span class="mp-mono">#{{ idx + 1 }}</span>
            <b class="order-name">{{ n }}</b>
            <n-select
              :value="scopeOf(n)"
              :options="perScopeOptions"
              size="tiny"
              class="scope-select"
              @update:value="(v: string) => setScopeOf(n, v)"
            />
            <n-tag
              size="tiny"
              :bordered="false"
              :type="effectiveScopeOf(n) === 'anime' ? 'info' : effectiveScopeOf(n) === 'photo' ? 'warning' : 'default'"
            >
              生效 {{ SCOPE_LABEL[effectiveScopeOf(n)] || effectiveScopeOf(n) }}
            </n-tag>
            <div class="spacer"></div>
            <n-button size="tiny" quaternary :disabled="!animaPinned || idx === 0" @click="movePick(n, -1)">↑</n-button>
            <n-button size="tiny" quaternary :disabled="!animaPinned || idx === animaEffective.length - 1" @click="movePick(n, 1)">↓</n-button>
            <n-button size="tiny" quaternary :disabled="!animaPinned" @click="togglePick(n)">移出</n-button>
          </div>
          <div v-if="!animaEffective.length" class="mp-sub">没有可用的工作流</div>
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
          <span class="label">标签风格（兜底）</span>
          <n-select
            v-model:value="upStyle"
            :options="styleOptions"
            size="small"
            :disabled="upProfile === 'auto'"
          />
        </label>
        <label class="mp-field">
          <span class="label">提示词适配</span>
          <n-select v-model:value="upProfile" :options="profileOptions" size="small" />
        </label>
        <label class="mp-field">
          <span class="label">可画类型（真人 / 动漫 / 两者皆可）</span>
          <n-select v-model:value="upScope" :options="scopeOptions" size="small" />
          <span class="mp-sub">动漫底模别选「只画真人」，否则会被派去画写真；一般保持「自动判断」</span>
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
        <b>清单从上到下就是顺序轮换的顺序</b>（只数已启用且校验通过的模板），用每行右侧的 ↑↓ 调整，保存后生效。
        节点定位自动完成（沿采样器的 positive / negative / latent_image 引用找）。
        异形链路（CFGGuider、SamplerCustomAdvanced、非 text 输入框）定位失败时才填下面的覆盖项。
      </div>

      <n-empty v-if="!rows.length" description="还没有模板，先上传一份" class="mp-empty" />

      <div v-for="(r, i) in rows" :key="i" class="tmpl" :class="{ bad: r.ok === false }">
        <div class="tmpl-head">
          <n-tag v-if="r.rotation_no && rotation.ordered" size="small" :bordered="false" type="info">
            轮换 #{{ r.rotation_no }}
          </n-tag>
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
          <n-button size="tiny" quaternary :disabled="i === 0" title="上移（提前轮换）" @click="moveRow(i, -1)">↑</n-button>
          <n-button size="tiny" quaternary :disabled="i === rows.length - 1" title="下移（推后轮换）" @click="moveRow(i, 1)">↓</n-button>
          <n-popconfirm @positive-click="removeRow(i)">
            <template #trigger>
              <n-button size="tiny" type="error" quaternary>移除</n-button>
            </template>
            从清单里移除「{{ r.name }}」？（保存后生效，JSON 文件不会删）
          </n-popconfirm>
        </div>

        <div v-if="r.ok === false" class="mp-err" style="margin-bottom: 8px">{{ r.problem }}</div>
        <template v-else>
          <div class="node-line mp-mono">{{ nodeHint(r) }}</div>
          <div class="node-line eff">生效：{{ effHint(r) }}</div>
        </template>
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
            <span class="label">标签风格（兜底）</span>
            <n-select
              v-model:value="r.tag_style"
              :options="styleOptions"
              size="small"
              :disabled="r.prompt_profile === 'auto'"
            />
          </label>
          <label class="mp-field">
            <span class="label">提示词适配</span>
            <n-select v-model:value="r.prompt_profile" :options="profileOptions" size="small" />
            <span class="mp-sub">自动 = 按底模文件名判断风格与尺寸</span>
          </label>
          <label class="mp-field">
            <span class="label">可画类型（真人 / 动漫 / 两者皆可）</span>
            <n-select v-model:value="r.subject_scope" :options="scopeOptions" size="small" />
            <span class="mp-sub">抽签时只派匹配的题材，避免动漫工作流被派去画写真；它不改提示词语言，也不给底模加能力</span>
          </label>
          <label class="mp-field">
            <span class="label">尺寸预设</span>
            <n-select v-model:value="r.size_preset" :options="localPresetOptions" size="small" />
            <span class="mp-sub">自动识别到底模时会被覆盖</span>
          </label>
          <label class="mp-field">
            <span class="label">采样参数档</span>
            <n-select v-model:value="r.params_preset" :options="paramsPresetOptions" size="small" />
            <span class="mp-sub">
              只覆盖工作流采样器上真有的字段（步数/CFG）；带 turbo 的图别调到「精修」
            </span>
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

.node-line.eff {
  color: var(--mp-accent);
  margin-top: -6px;
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

/* v0.7.4 轮换顺序可视化 */
.switch-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  font-size: 13px;
}

.rot-next {
  font-size: 13px;
  margin-bottom: 10px;
}
.rot-next b {
  color: var(--mp-accent);
}
.rot-chain {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.rot-item {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  padding: 5px 10px;
  border-radius: 6px;
  border: 1px solid var(--mp-line);
  background: var(--mp-panel-soft);
}
.rot-item i {
  font-style: normal;
  color: var(--mp-sub);
  font-family: monospace;
}
.rot-item.next {
  border-color: var(--mp-accent);
  box-shadow: 0 0 0 1px var(--mp-accent) inset;
}
.rot-loop {
  font-size: 11px;
}
.order-box {
  margin: 10px 0 14px;
  padding: 10px 12px;
  border: 1px solid var(--mp-line);
  border-radius: 8px;
  background: var(--mp-panel-soft);
}
.order-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 0;
  border-top: 1px solid var(--mp-line);
  font-size: 12px;
}
.order-name {
  min-width: 90px;
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.scope-select {
  width: 172px;
  flex: 0 0 172px;
}
</style>
