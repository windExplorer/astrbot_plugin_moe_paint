<script setup lang="ts">
// 提示词：人物锚定 + 自定义规则（追加）+ 完全覆盖（高级）+ 生效预览。
// 只提交本页涉及的 prompt.* 扁平点号键，不整份回传配置。
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { NButton, NInput, NSelect, NSwitch, NTag, useMessage } from "naive-ui";
import { apiPromptPreview, type PromptPreviewStyle } from "../api";
import { cfgBool, cfgGet, cfgSet, loadConfig, saveFields, saving } from "../config";

const message = useMessage();
const router = useRouter();

const PROMPT_KEYS = [
  "prompt.persona_enabled",
  "prompt.persona_text",
  "prompt.persona_tags",
  "prompt.persona_negative",
  "prompt.persona_negative_zh",
  "prompt.extra_rules",
  "prompt.style_anchor_enabled",
  "prompt.override_danbooru",
  "prompt.override_natural",
  "prompt.override_natural_en",
];

/** 画风硬锚定总开关（关掉则退回「可画类型只筛题材」的旧行为）。 */
const styleAnchor = computed({
  get: () => cfgBool("prompt.style_anchor_enabled", true),
  set: (v: boolean) => cfgSet("prompt.style_anchor_enabled", v),
});

const styles = ref<PromptPreviewStyle[]>([]);
const personaOn = ref(false);
const picked = ref("");
const previewing = ref(false);
const previewError = ref("");

const styleOptions = computed(() => styles.value.map((s) => ({ label: s.label, value: s.key })));
const current = computed(() => styles.value.find((s) => s.key === picked.value)?.prompt || "");

function set(key: string, v: any) {
  cfgSet(key, v);
}

async function loadPreview() {
  previewing.value = true;
  previewError.value = "";
  try {
    const d = await apiPromptPreview();
    styles.value = d.styles || [];
    personaOn.value = !!d.persona_enabled;
    if (styles.value.length && !styles.value.some((s) => s.key === picked.value)) {
      picked.value = styles.value[0].key;
    }
  } catch (e: any) {
    previewError.value = e?.message || String(e);
  } finally {
    previewing.value = false;
  }
}

async function save() {
  const r = await saveFields(PROMPT_KEYS, "提示词");
  if (!r.ok) {
    message.error(`保存失败：${r.error}`);
    return;
  }
  message.success(`提示词已保存（${r.changed} 项），下一张起生效`);
  await loadPreview();
}

onMounted(async () => {
  await loadConfig();
  await loadPreview();
});
</script>

<template>
  <div class="page">
    <!-- 这是什么 -->
    <div class="mp-card mp-card--flat">
      <div class="mp-card-title">这里管什么</div>
      <div class="mp-hint" style="margin-top: 8px">
        每画一张图，插件先抽签得到一句「创作简报」（题材 / 风格 / 构图 / 光色 / 氛围），
        再交给 LLM 按<b>系统提示词</b>写成正向与负向提示词。这一页就是改这套系统提示词的：
        <br />
        ① <b>人物锚定</b>固定「东亚面孔的年轻成年女性」并排除其他人种、中老年与未成年；
        ② <b>自定义规则</b>追加你自己的要求；③ <b>完全覆盖</b>给高级用户整段替换。
        输出契约（只返回一个 JSON）始终保留，改坏提示词也不会让整轮生成失败。
      </div>
    </div>

    <!-- 人物锚定 -->
    <div class="mp-card">
      <div class="mp-card-head">
        <div class="mp-card-title">人物锚定</div>
        <div class="mp-row">
          <n-tag size="small" :bordered="false" :type="personaOn ? 'success' : 'default'">
            {{ personaOn ? "已启用（已保存的状态）" : "已停用（已保存的状态）" }}
          </n-tag>
          <n-button size="small" type="primary" :loading="!!saving['提示词']" @click="save">保存本页</n-button>
        </div>
      </div>

      <div class="switch-row">
        <n-switch
          :value="cfgBool('prompt.persona_enabled', true)"
          @update:value="(v: boolean) => set('prompt.persona_enabled', v)"
        />
        <span>启用人物锚定</span>
        <span class="mp-sub">关掉后下面几项都不生效，人物完全交给题材词与底模自由发挥</span>
      </div>

      <div class="mp-hint" style="margin: 10px 0">
        硬约束只对「美少女 / 动漫少女 / 真人少女 / 写真摄影 / 动漫角色 / 游戏角色」这几类生效：
        风景、星空、生态是纯景物，不会被硬塞人物；人物肖像类里有老人与男性，也不套少女标签。
      </div>

      <div class="mp-fields">
        <label class="mp-field mp-field--wide">
          <span class="label">人物规则（写进三种系统提示词）</span>
          <n-input
            :value="String(cfgGet('prompt.persona_text', ''))"
            type="textarea"
            :rows="4"
            size="small"
            placeholder="人物一律为东亚面孔的年轻成年女性（18 岁以上）…"
            @update:value="(v: string) => set('prompt.persona_text', v)"
          />
          <span class="mp-sub">中文规则，三种提示词风格通用；留空则只靠下面的固定标签与负面词</span>
        </label>
        <label class="mp-field mp-field--wide">
          <span class="label">人物固定标签（仅动漫标签系 Danbooru）</span>
          <n-input
            :value="String(cfgGet('prompt.persona_tags', ''))"
            size="small"
            class="mp-mono"
            @update:value="(v: string) => set('prompt.persona_tags', v)"
          />
          <span class="mp-sub">自动加在提示词前部，已存在的标签不会重复</span>
        </label>
        <label class="mp-field mp-field--wide">
          <span class="label">排除负面词（英文标签系 / 英文自然语言）</span>
          <n-input
            :value="String(cfgGet('prompt.persona_negative', ''))"
            size="small"
            class="mp-mono"
            @update:value="(v: string) => set('prompt.persona_negative', v)"
          />
          <span class="mp-sub">固定追加到 LLM 给的 negative 后面，自动去重</span>
        </label>
        <label class="mp-field mp-field--wide">
          <span class="label">排除负面词（中文自然语言 · Qwen 等）</span>
          <n-input
            :value="String(cfgGet('prompt.persona_negative_zh', ''))"
            size="small"
            @update:value="(v: string) => set('prompt.persona_negative_zh', v)"
          />
          <span class="mp-sub">中文风格下改用这串，避免中英混杂</span>
        </label>
      </div>
    </div>

    <!-- 画风硬锚定 -->
    <div class="mp-card">
      <div class="mp-card-head">
        <div class="mp-card-title">画风硬锚定（可画类型 → 提示词）</div>
        <n-button size="small" type="primary" :loading="!!saving['提示词']" @click="save">保存本页</n-button>
      </div>
      <div class="switch-row">
        <n-switch v-model:value="styleAnchor" size="small" />
        <span>启用画风硬锚定</span>
        <span class="mp-sub">默认开启；关掉就退回 v0.7.5 行为（可画类型只筛题材、不改提示词）</span>
      </div>
      <div class="mp-hint" style="margin-top: 10px">
        开启后，工作流的「可画类型」会一路传到提示词，<b>不靠 LLM 自觉</b>：
        <br />① 创作简报带上「可画类型=…」；② 系统提示词追加画风硬规则（完全覆盖时也保留）；
        ③ LLM 返回后<b>直接改字符串</b>：动漫标签系前置
        <code class="mp-mono">anime style, 2d, illustration</code>，自然语言风格在句首点明
        「二次元动漫插画」，负向词一律追加
        <code class="mp-mono">photorealistic, photo, 3d render…</code>；只画真人则完全对称。
        <br />风景 / 星空 / 艺术 / 生态这类题材<b>不是中性画风</b>：只画动漫时它们也画成动漫插画，
        只画真人时画成写实摄影，风格池同步收窄，不会再让动漫工作流抽到「胶片摄影颗粒感」。
        <br />人物排除负面词（老人 / 男性 / 儿童等）对<b>所有类别</b>生效；景物类仍不会硬塞
        <code class="mp-mono">1girl</code>，避免毁掉风景。
      </div>
      <div class="mp-hint" style="margin-top: 8px">
        验证方法：去「调试与日志 → LLM 试跑一次」，看「可画类型」标签与最终正向 / 负向提示词里
        是否已经带上画风标签；展开「LLM 原始返回」可对照模型自己写了什么。
      </div>
    </div>

    <!-- 追加规则 -->
    <div class="mp-card">
      <div class="mp-card-head">
        <div class="mp-card-title">自定义规则（追加，最常用）</div>
        <n-button size="small" type="primary" :loading="!!saving['提示词']" @click="save">保存本页</n-button>
      </div>
      <div class="mp-hint" style="margin-bottom: 10px">
        写「画什么 / 别画什么」放这里最省事：内容会追加到三套内置系统提示词的规则段，
        输出契约永远保留。例如「画面里不要出现文字」「偏好竖幅人像」「不要阴天」。
      </div>
      <n-input
        :value="String(cfgGet('prompt.extra_rules', ''))"
        type="textarea"
        :rows="6"
        size="small"
        placeholder="画面里不要出现文字；偏好柔和的暖色调…"
        @update:value="(v: string) => set('prompt.extra_rules', v)"
      />
    </div>

    <!-- 完全覆盖（高级） -->
    <div class="mp-card">
      <div class="mp-card-head">
        <div class="mp-card-title">完全覆盖系统提示词（高级）</div>
        <n-button size="small" type="primary" :loading="!!saving['提示词']" @click="save">保存本页</n-button>
      </div>
      <div class="mp-hint" style="margin-bottom: 10px">
        留空 = 用内置提示词 + 上面的追加规则。填了内容则<b>整段替换</b>对应风格的系统提示词；
        若你的文本里没写 JSON 输出契约，插件会自动补上，但<b>请自己保证要求 LLM 返回</b>
        <code class="mp-mono">{"prompt","negative","caption"}</code> 这三个字段。
      </div>
      <details class="advanced">
        <summary>展开三个完全覆盖文本框</summary>
        <div class="mp-fields" style="margin-top: 10px">
          <label class="mp-field mp-field--wide">
            <span class="label">动漫标签系（Danbooru）</span>
            <n-input
              :value="String(cfgGet('prompt.override_danbooru', ''))"
              type="textarea" :rows="5" size="small"
              @update:value="(v: string) => set('prompt.override_danbooru', v)"
            />
          </label>
          <label class="mp-field mp-field--wide">
            <span class="label">中文自然语言（Qwen）</span>
            <n-input
              :value="String(cfgGet('prompt.override_natural', ''))"
              type="textarea" :rows="5" size="small"
              @update:value="(v: string) => set('prompt.override_natural', v)"
            />
          </label>
          <label class="mp-field mp-field--wide">
            <span class="label">英文自然语言（Flux / 写实）</span>
            <n-input
              :value="String(cfgGet('prompt.override_natural_en', ''))"
              type="textarea" :rows="5" size="small"
              @update:value="(v: string) => set('prompt.override_natural_en', v)"
            />
          </label>
        </div>
      </details>
    </div>

    <!-- 生效预览 -->
    <div class="mp-card">
      <div class="mp-card-head">
        <div class="mp-card-title">生效预览（LLM 实际收到的系统提示词）</div>
        <div class="mp-row">
          <n-select v-model:value="picked" :options="styleOptions" size="small" style="width: 230px" />
          <n-button size="small" quaternary :loading="previewing" @click="loadPreview">刷新</n-button>
          <n-button size="small" quaternary @click="router.push('/debug')">去调试试跑 →</n-button>
        </div>
      </div>
      <div class="mp-hint" style="margin-bottom: 10px">
        这里显示的是<b>已保存</b>的配置，且按「两者皆可」展示基础提示词——
        画风硬规则会在出图时按当次工作流的可画类型追加，所以这里看不到。
        在本页改动后先点「保存本页」再刷新。
        想看最终写出的正负向提示词，去「调试与日志」页跑一次 LLM 试跑。
      </div>
      <div v-if="previewError" class="mp-err">{{ previewError }}</div>
      <pre v-else class="sys-preview mp-mono">{{ current || "（暂无预览）" }}</pre>
    </div>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.switch-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  font-size: 13px;
}

.sys-preview {
  margin: 0;
  padding: 12px;
  max-height: 420px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
  line-height: 1.6;
  border: 1px solid var(--mp-line);
  border-radius: 8px;
  background: var(--mp-panel-soft);
}

code {
  padding: 1px 5px;
  border-radius: 5px;
  background: var(--mp-panel-soft);
}
</style>
