<script setup lang="ts">
// 主题词池：一张「题材清单」表统一管理 启用 / 权重 / 追加题材 / 新增类别，
// 不再让用户分别去「停用类别」「类别权重」「词池扩充」三个地方对着同一批名字填文本。
// 内置题材从 /theme/pool 读取（与后端 prompter.CATEGORIES 同源，不再前端硬编码）。
import { computed, onMounted, ref } from "vue";
import { NButton, NInput, NInputNumber, NPopconfirm, NSelect, NSwitch, NTag, useMessage } from "naive-ui";
import {
  apiCharacterDelete,
  apiCharacters,
  apiCharacterSave,
  apiThemePool,
  type CharacterCard,
  type ThemePoolPayload,
} from "../api";
import { cfgGet, cfgNum, cfgSet, loadConfig, saveFields, saving } from "../config";
import { parseWeights, splitNames, weightDistribution } from "../utils";

const message = useMessage();

const SAVE_KEYS = [
  "theme.disabled_categories",
  "theme.extra_words",
  "theme.category_weights",
  "theme.category_scopes",
  "theme.character_names",
  "theme.style_none_pct",
];
const LAYER_KEYS = ["风格", "光色", "氛围", "构图"];
const SCOPE_LABEL: Record<string, string> = {
  anime: "只画动漫", photo: "只画真人", both: "两者皆可",
};
const STYLE_LABEL: Record<string, string> = { anime: "动漫风格池", photo: "摄影风格池", all: "全局风格池" };
const GIRL_CATS = new Set(["美少女", "动漫少女", "真人少女", "写真摄影", "动漫角色", "游戏角色"]);

interface CatRow {
  name: string;
  subjects: string[];
  style_group: string;
  scope: string;
  custom: boolean;
}

const pool = ref<ThemePoolPayload>({ categories: [], layers: {} });
const poolError = ref("");
const loading = ref(false);
const expanded = ref<Record<string, boolean>>({});
const newCat = ref("");

const extraMap = computed<Record<string, string>>(() => {
  const v = cfgGet("theme.extra_words", {});
  return v && typeof v === "object" ? v : {};
});
const disabled = computed<string[]>({
  get: () => splitNames(cfgGet("theme.disabled_categories", "")),
  set: (list: string[]) => cfgSet("theme.disabled_categories", list.join(",")),
});
const weightsText = computed<string>({
  get: () => String(cfgGet("theme.category_weights", "")),
  set: (s: string) => cfgSet("theme.category_weights", s),
});
const scopesText = computed<string>({
  get: () => String(cfgGet("theme.category_scopes", "")),
  set: (s: string) => cfgSet("theme.category_scopes", s),
});
const characterNames = computed<string>({
  get: () => String(cfgGet("theme.character_names", "")),
  set: (s: string) => cfgSet("theme.character_names", s),
});

/** 可画类型覆盖文本 → map（与后端 prompter.parse_scopes 同语义）。 */
const scopesMap = computed<Record<string, string>>(() => {
  const out: Record<string, string> = {};
  for (const part of scopesText.value.split(/[,，;；\n\r]+/)) {
    const m = part.match(/^\s*([^=：:]+)\s*[=：:]\s*(.+?)\s*$/);
    if (!m) continue;
    const v = m[2].trim().toLowerCase();
    if (["anime", "photo", "both"].includes(v)) out[m[1].trim()] = v;
  }
  return out;
});

const builtinNames = computed(() => new Set(pool.value.categories.map((c) => c.name)));

// ---------------- 角色卡（v0.7.9） ----------------
const cards = ref<CharacterCard[]>([]);
const cardBusy = ref(false);
const cardPhase = ref("");

const PHASE_LABEL: Record<string, string> = {
  dawn: "黎明", morning: "上午", day: "白天", dusk: "黄昏", night: "入夜", deep_night: "深夜",
};

const charMode = computed({
  get: () => String(cfgGet("character.mode", "off")),
  set: (v: string) => cfgSet("character.mode", v),
});
const charName = computed({
  get: () => String(cfgGet("character.name", "")),
  set: (v: string) => cfgSet("character.name", v),
});
const charModeOptions = [
  { label: "不用角色卡", value: "off" },
  { label: "每轮随机抽一张", value: "random" },
  { label: "固定用指定角色", value: "fixed" },
];
const charNameOptions = computed(() => [
  { label: "（未指定）", value: "" },
  ...cards.value.map((c) => ({ label: c.name, value: c.name })),
]);

async function loadCards() {
  try {
    const d = await apiCharacters();
    cards.value = d.items || [];
    cardPhase.value = d.phase || "";
  } catch (e: any) {
    message.error(`读取角色卡失败：${e?.message || e}`);
  }
}

function addCard() {
  cards.value.push({ name: "", aliases: [], tags: "", negative: "", note: "" });
}

function aliasesText(c: CharacterCard): string {
  return (c.aliases || []).join(",");
}

function setAliases(c: CharacterCard, v: string) {
  c.aliases = v.split(/[,，]/).map((s) => s.trim()).filter(Boolean);
}

async function saveCard(c: CharacterCard) {
  if (!c.name.trim()) {
    message.warning("先填角色名");
    return;
  }
  cardBusy.value = true;
  try {
    await apiCharacterSave({
      name: c.name.trim(),
      aliases: c.aliases || [],
      tags: c.tags || "",
      negative: c.negative || "",
      note: c.note || "",
    });
    message.success(`角色卡「${c.name.trim()}」已保存`);
    await loadCards();
  } catch (e: any) {
    message.error(`保存失败：${e?.message || e}`);
  } finally {
    cardBusy.value = false;
  }
}

async function removeCard(c: CharacterCard) {
  cardBusy.value = true;
  try {
    await apiCharacterDelete(c.name);
    message.success("已删除角色卡");
    await loadCards();
  } catch (e: any) {
    message.error(`删除失败：${e?.message || e}`);
  } finally {
    cardBusy.value = false;
  }
}

async function saveCharConfig() {
  const r = await saveFields(["character.mode", "character.name"], "角色卡");
  if (r.ok) {
    message.success(`角色卡设置已保存（${r.changed} 项）`);
  } else {
    message.error(`保存失败：${r.error}`);
  }
}

const rows = computed<CatRow[]>(() => {
  const out: CatRow[] = pool.value.categories.map((c) => ({
    name: c.name,
    subjects: c.subjects || [],
    style_group: c.style_group || "all",
    scope: scopesMap.value[c.name] || c.scope || "both",
    custom: false,
  }));
  for (const [k, v] of Object.entries(extraMap.value)) {
    if (LAYER_KEYS.includes(k) || builtinNames.value.has(k)) continue;
    out.push({
      name: k,
      subjects: splitNames(v),
      style_group: "all",
      scope: scopesMap.value[k] || "both",
      custom: true,
    });
  }
  return out;
});

const weightMap = computed(() => parseWeights(weightsText.value));
const activeNames = computed(() => rows.value.filter((r) => isOn(r.name)).map((r) => r.name));
const distribution = computed(() => weightDistribution(activeNames.value, weightMap.value));
const girlPct = computed(
  () => Math.round(distribution.value.filter((d) => GIRL_CATS.has(d.name))
    .reduce((s, d) => s + d.pct, 0) * 10) / 10,
);

function weightOf(name: string): number {
  const w = weightMap.value[name];
  return w === undefined ? 1 : w;
}
function setWeight(name: string, v: number | null) {
  const m: Record<string, number> = {
    ...weightMap.value,
    [name]: Math.max(0, Math.min(100, Number(v ?? 1))),
  };
  weightsText.value = rows.value.map((r) => `${r.name}=${m[r.name] ?? 1}`).join(",");
}
function isOn(name: string): boolean {
  return !disabled.value.includes(name);
}
function setOn(name: string, on: boolean) {
  const list = disabled.value.filter((n) => n !== name);
  if (!on) list.push(name);
  disabled.value = list;
}
function extraOf(name: string): string {
  return String(extraMap.value[name] ?? "");
}
function setExtra(name: string, text: string) {
  const m = { ...extraMap.value };
  const v = String(text || "").trim();
  if (v) m[name] = v;
  else delete m[name];
  cfgSet("theme.extra_words", m);
}
function addCat() {
  const name = newCat.value.trim();
  if (!name) {
    message.warning("先填类别名");
    return;
  }
  if (LAYER_KEYS.includes(name)) {
    message.warning("风格 / 光色 / 氛围 / 构图 是保留名");
    return;
  }
  if (rows.value.some((r) => r.name === name)) {
    message.warning("已有同名类别");
    return;
  }
  cfgSet("theme.extra_words", { ...extraMap.value, [name]: "" });
  expanded.value[name] = true;
  newCat.value = "";
  message.success(`已新增「${name}」，在题材框里填至少一个词才会参与抽签`);
}
function removeCat(name: string) {
  const m = { ...extraMap.value };
  delete m[name];
  cfgSet("theme.extra_words", m);
  disabled.value = disabled.value.filter((n) => n !== name);
}

const PRESET_GIRL: Record<string, number> = {
  美少女: 5, 动漫少女: 4, 真人少女: 3, 写真摄影: 2, 动漫角色: 1, 游戏角色: 1,
  人物肖像: 1, 风景场景: 1, 宇宙星空: 1, 艺术创意: 1, 自然生态: 1,
};
const PRESET_BALANCED: Record<string, number> = {
  美少女: 2, 动漫少女: 2, 真人少女: 1, 写真摄影: 2, 动漫角色: 1, 游戏角色: 1,
  人物肖像: 1, 风景场景: 2, 宇宙星空: 2, 艺术创意: 1, 自然生态: 1,
};
function applyPreset(kind: "girl" | "balanced") {
  const base = kind === "girl" ? PRESET_GIRL : PRESET_BALANCED;
  weightsText.value = rows.value.map((r) => `${r.name}=${base[r.name] ?? 1}`).join(",");
}

async function reload() {
  loading.value = true;
  poolError.value = "";
  try {
    await loadConfig(true);
    pool.value = await apiThemePool();
  } catch (e: any) {
    poolError.value = e?.message || String(e);
  } finally {
    loading.value = false;
  }
}

async function save() {
  const empties = rows.value.filter((r) => r.custom && !extraOf(r.name).trim()).map((r) => r.name);
  if (empties.length) {
    message.warning(`自定义类别「${empties.join("、")}」还没填题材，填好再保存`);
    return;
  }
  const r = await saveFields(SAVE_KEYS, "主题词池");
  if (!r.ok) {
    message.error(`保存失败：${r.error}`);
    return;
  }
  message.success(`主题词池已保存（${r.changed} 项），下一张起生效`);
  await reload();
}

onMounted(() => {
  void reload();
  void loadCards();
});
</script>

<template>
  <div class="page">
    <!-- 词池是什么 -->
    <div class="mp-card mp-card--flat">
      <div class="mp-card-title">词池是什么（一句话）</div>
      <div class="mp-hint" style="margin-top: 8px">
        每画一张图，插件先分 <b>6 层抽签</b>：
        <code class="mp-mono">类别 → 题材 → 风格 → 构图 → 光色 → 氛围</code>，
        拼成一句「创作简报」再交给 LLM 写成提示词。<b>词池就是这 6 层候选词的总和</b>。
        之所以分层，是因为直接让 LLM「画张好看的图」会塌缩成千篇一律的美少女半身像。
        下面这张表就是「类别 + 题材」那一层：关开关 = 不画这类，调权重 = 改抽中概率，
        展开 = 看内置题材并追加自己的词。
      </div>
      <div class="mp-hint" style="margin-top: 8px">
        「可画类型」标签说明该题材会交给哪种工作流：<b>只画动漫</b>只交给动漫底模，
        <b>只画真人</b>只交给写实底模，<b>两者皆可</b>不限。这样动漫底模不会被派去画泳装写真、
        二次元角色也不会被塞给写实底模。工作流那边的「可画类型」在「工作流模板」页设置。
      </div>
    </div>

    <!-- 题材清单 -->
    <div class="mp-card">
      <div class="mp-card-head">
        <div class="mp-card-title">题材清单（{{ rows.length }} 类）</div>
        <div class="mp-row">
          <n-button size="small" quaternary @click="applyPreset('girl')">少女向预设</n-button>
          <n-button size="small" quaternary @click="applyPreset('balanced')">均衡预设</n-button>
          <n-button size="small" quaternary :loading="loading" @click="reload">刷新</n-button>
          <n-button size="small" type="primary" :loading="!!saving['主题词池']" @click="save">保存本页</n-button>
        </div>
      </div>

      <div v-if="poolError" class="mp-err">读不到内置词池：{{ poolError }}（表里的内置类别可能为空）</div>

      <div class="dist">
        <div class="dist-head">
          <span class="mp-sub">当前抽中概率（少女相关合计 {{ girlPct }}%）</span>
        </div>
        <div v-for="d in distribution" :key="d.name" class="dist-row">
          <span class="dist-name">{{ d.name }}</span>
          <div class="dist-bar"><i :style="{ width: d.pct + '%' }"></i></div>
          <span class="dist-pct mp-mono">{{ d.pct }}%</span>
        </div>
        <div v-if="!distribution.length" class="mp-sub">没有可抽的类别（都被停用了？）</div>
      </div>

      <div v-for="r in rows" :key="r.name" class="cat" :class="{ off: !isOn(r.name) }">
        <div class="cat-head">
          <n-switch :value="isOn(r.name)" size="small" @update:value="(v: boolean) => setOn(r.name, v)" />
          <b class="cat-name">{{ r.name }}</b>
          <n-tag size="tiny" :bordered="false" :type="r.scope === 'anime' ? 'info' : r.scope === 'photo' ? 'warning' : 'default'">
            {{ SCOPE_LABEL[r.scope] }}
          </n-tag>
          <n-tag v-if="r.custom" size="tiny" :bordered="false" type="success">自定义</n-tag>
          <span class="mp-sub cat-meta">{{ r.subjects.length }} 个题材 · {{ STYLE_LABEL[r.style_group] }}</span>
          <div class="spacer"></div>
          <span class="mp-sub">权重</span>
          <n-input-number
            :value="weightOf(r.name)"
            :min="0" :max="100" :step="1" size="small" class="weight-input"
            @update:value="(v: number | null) => setWeight(r.name, v)"
          />
          <n-button size="tiny" quaternary @click="expanded[r.name] = !expanded[r.name]">
            {{ expanded[r.name] ? "收起" : "题材" }}
          </n-button>
          <n-popconfirm v-if="r.custom" @positive-click="removeCat(r.name)">
            <template #trigger><n-button size="tiny" quaternary type="error">删除</n-button></template>
            删除自定义类别「{{ r.name }}」？
          </n-popconfirm>
        </div>

        <div v-if="expanded[r.name]" class="cat-body">
          <div v-if="r.subjects.length" class="mp-sub">内置题材（只读，抽签时从中选一条）</div>
          <div v-if="r.subjects.length" class="words">
            <span v-for="(s, i) in r.subjects" :key="i" class="mp-tag">{{ s }}</span>
          </div>
          <label class="mp-field mp-field--wide" style="margin-top: 10px">
            <span class="label">{{ r.custom ? "题材词（至少一个，逗号分隔）" : "追加题材（叠加到内置题材上，逗号分隔）" }}</span>
            <n-input
              :value="extraOf(r.name)"
              type="textarea" :rows="2" size="small"
              :placeholder="r.custom ? '如 雨天屋檐下的猫, 雪地里的红灯笼' : '如 冰环行星的极夜, 双月夜潮'"
              @update:value="(v: string) => setExtra(r.name, v)"
            />
          </label>
          <div class="mp-hint" style="margin-top: 8px">
            可画类型：{{ SCOPE_LABEL[r.scope] }}。想改这个标记，去下面「高级」里的「类别可画类型覆盖」写
            <code class="mp-mono">{{ r.name }}=anime|photo|both</code>。
          </div>
        </div>
      </div>

      <div class="new-cat">
        <n-input v-model:value="newCat" size="small" placeholder="新类别名，如「我的猫」" style="max-width: 260px" />
        <n-button size="small" @click="addCat">新增类别</n-button>
        <span class="mp-sub">新增后记得在展开的题材框里填词，再点右上角保存</span>
      </div>
    </div>

    <!-- 角色名单 -->
    <div class="mp-card">
      <div class="mp-card-head">
        <div class="mp-card-title">动漫 / 游戏角色名单（可删改）</div>
        <n-button size="small" type="primary" :loading="!!saving['主题词池']" @click="save">保存本页</n-button>
      </div>
      <div class="mp-hint" style="margin-bottom: 10px">
        每行一条 <code class="mp-mono">类别=角色名,角色名…</code>，名字会并入对应类别的题材池；
        删掉整行就停用该类角色名，改成自己想要的名字也可以。
        内置类别里还留着「魔法少女 / 巫女 / 弓手 / 法师」这类<b>通用角色原型</b>，删名单也不会没题材。
        角色名只作画风标签，人物锚定仍会强制成年化（见「提示词」页），请勿用于生成未成年人不当内容。
      </div>
      <n-input v-model:value="characterNames" type="textarea" :rows="5" size="small" class="mp-mono" />
    </div>

    <!-- 角色卡 -->
    <div class="mp-card">
      <div class="mp-card-head">
        <div class="mp-card-title">角色卡（同一角色跨图一致）</div>
        <div class="mp-row">
          <n-tag v-if="cardPhase" size="small" :bordered="false">
            当前时段 {{ PHASE_LABEL[cardPhase] || cardPhase }}
          </n-tag>
          <n-button size="small" @click="addCard">新增</n-button>
          <n-button size="small" type="primary" :loading="!!saving['角色卡']" @click="saveCharConfig">
            保存设置
          </n-button>
        </div>
      </div>
      <div class="mp-hint" style="margin-bottom: 10px">
        一张卡 = 一组固定标签（danbooru 风格会前置进提示词）+ 排除词，让同一个人物每次长相一致。
        自然语言风格不插标签串（会破坏句子），但角色名会写进简报、排除词照样生效。
        指令里也可以用 <code class="mp-mono">--char 名字</code> 临时指定。
      </div>
      <div class="mp-fields" style="margin-bottom: 12px">
        <label class="mp-field">
          <span class="label">使用方式</span>
          <n-select v-model:value="charMode" :options="charModeOptions" size="small" />
        </label>
        <label class="mp-field">
          <span class="label">固定角色</span>
          <n-select
            v-model:value="charName"
            :options="charNameOptions"
            size="small"
            :disabled="charMode !== 'fixed'"
          />
        </label>
      </div>

      <div v-if="!cards.length" class="mp-sub" style="margin-bottom: 10px">
        还没有角色卡，点右上角「新增」建一张。
      </div>
      <div v-for="(c, i) in cards" :key="`${c.name}-${i}`" class="char-card">
        <div class="mp-fields">
          <label class="mp-field">
            <span class="label">角色名</span>
            <n-input v-model:value="c.name" size="small" placeholder="如 初音未来" />
          </label>
          <label class="mp-field">
            <span class="label">别名（逗号分隔）</span>
            <n-input
              :value="aliasesText(c)"
              size="small"
              placeholder="如 miku, 初音"
              @update:value="(v: string) => setAliases(c, v)"
            />
          </label>
          <label class="mp-field mp-field--wide">
            <span class="label">固定标签（danbooru）</span>
            <n-input
              v-model:value="c.tags"
              size="small"
              placeholder="hatsune miku, twintails, aqua hair"
            />
          </label>
          <label class="mp-field mp-field--wide">
            <span class="label">排除词</span>
            <n-input
              v-model:value="c.negative"
              size="small"
              placeholder="wrong character, blonde hair"
            />
          </label>
          <label class="mp-field mp-field--wide">
            <span class="label">备注</span>
            <n-input v-model:value="c.note" size="small" placeholder="给自己看的说明（可选）" />
          </label>
        </div>
        <div class="char-actions">
          <n-button size="tiny" type="primary" :loading="cardBusy" @click="saveCard(c)">保存</n-button>
          <n-popconfirm @positive-click="removeCard(c)">
            <template #trigger>
              <n-button size="tiny" quaternary>删除</n-button>
            </template>
            删除角色卡「{{ c.name || "未命名" }}」？已生成的图不受影响。
          </n-popconfirm>
        </div>
      </div>
    </div>

    <!-- 高级 -->
    <div class="mp-card">
      <div class="mp-card-head">
        <div class="mp-card-title">高级：其余 4 层与可画类型覆盖</div>
        <n-button size="small" type="primary" :loading="!!saving['主题词池']" @click="save">保存本页</n-button>
      </div>
      <div class="mp-hint" style="margin-bottom: 10px">
        平时不用改。这四层分别给简报追加候选词；构图项必须带朝向，格式
        <code class="mp-mono">名称@landscape|portrait|square</code>。
      </div>
      <div class="mp-fields">
        <label v-for="k in LAYER_KEYS" :key="k" class="mp-field mp-field--wide">
          <span class="label">{{ k }}（追加，逗号分隔）</span>
          <n-input
            :value="extraOf(k)"
            type="textarea" :rows="2" size="small"
            :placeholder="k === '构图' ? '如 教堂穹顶仰拍@portrait' : '如 柔光薄雾, 晨曦微蓝'"
            @update:value="(v: string) => setExtra(k, v)"
          />
        </label>
        <label class="mp-field">
          <span class="label">不指定画风的比例（%）</span>
          <n-input-number
            :value="cfgNum('theme.style_none_pct', 40)"
            :min="0" :max="100" :step="5" size="small"
            @update:value="(v: number | null) => cfgSet('theme.style_none_pct', v ?? 40)"
          />
          <span class="mp-sub">
            每轮有多大比例不抽画风词（让底模走默认审美）。默认 40%，觉得风格太浓/太同质就调高
          </span>
        </label>
        <label class="mp-field mp-field--wide">
          <span class="label">类别可画类型覆盖（类别=anime|photo|both，逗号分隔）</span>
          <n-input
            v-model:value="scopesText"
            type="textarea" :rows="2" size="small" class="mp-mono"
            placeholder="如 我的猫=anime, 写真摄影=both"
          />
          <span class="mp-sub">内置类别已标好；只给自定义类别或想改内置标记时才需要写</span>
        </label>
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

.dist {
  margin: 6px 0 14px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.dist-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}
.dist-name { flex: 0 0 88px; }
.dist-bar {
  flex: 1;
  height: 6px;
  border-radius: 4px;
  background: var(--mp-line);
  overflow: hidden;
}
.dist-bar i {
  display: block;
  height: 100%;
  background: var(--mp-grad);
  border-radius: 4px;
}
.dist-pct {
  flex: 0 0 48px;
  text-align: right;
  color: var(--mp-sub);
}

.cat {
  border: 1px solid var(--mp-line);
  border-radius: 8px;
  margin-bottom: 8px;
  overflow: hidden;
}
.cat.off { opacity: 0.5; }
.cat-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  flex-wrap: wrap;
}
.cat-name { min-width: 76px; }
.cat-meta { flex: 1 1 auto; }
.spacer { flex: 1 1 auto; }
.weight-input { width: 92px; }
.cat-body {
  padding: 10px 12px 12px;
  border-top: 1px solid var(--mp-line);
  background: var(--mp-panel-soft);
}
.words {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
  max-height: 150px;
  overflow: auto;
}
.mp-tag {
  font-size: 12px;
  padding: 3px 9px;
  border-radius: 999px;
  background: var(--mp-panel-soft);
  border: 1px solid var(--mp-line);
}
.new-cat {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-top: 12px;
}
code {
  padding: 1px 5px;
  border-radius: 5px;
  background: var(--mp-panel-soft);
}

.char-card {
  padding: 10px 12px 8px;
  margin-bottom: 10px;
  border-radius: 12px;
  border: 1px dashed var(--mp-line-strong);
  background: var(--mp-panel-soft);
}

.char-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
}
</style>
