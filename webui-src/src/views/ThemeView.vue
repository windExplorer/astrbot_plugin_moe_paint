<script setup lang="ts">
// 主题词池：停用类别 + 词池扩充。
// 内置 6 大类；extra_words 的键可以是内置类别（追加题材）、新类别，
// 或「风格 / 光色 / 氛围 / 构图」这几个特殊层。
import { computed, onMounted } from "vue";
import { NButton, NInput, NSelect, useMessage } from "naive-ui";
import { cfgGet, cfgSet, loadConfig, saveFields, saving } from "../config";

const message = useMessage();

const BUILTIN = ["风景场景", "宇宙星空", "美少女", "人物肖像", "艺术创意", "自然生态"];

const extraMap = computed<Record<string, string>>(() => {
  const v = cfgGet("theme.extra_words", {});
  return v && typeof v === "object" ? (v as Record<string, string>) : {};
});

const extraText = computed({
  get: () =>
    Object.entries(extraMap.value)
      .map(([k, v]) => `${k}=${Array.isArray(v) ? v.join(",") : v}`)
      .join("\n"),
  set: (s: string) => {
    const out: Record<string, string> = {};
    for (const line of String(s).split(/\r?\n/)) {
      const i = line.indexOf("=");
      if (i > 0) {
        const k = line.slice(0, i).trim();
        const v = line.slice(i + 1).trim();
        if (k) out[k] = v;
      }
    }
    cfgSet("theme.extra_words", out);
  },
});

const disabled = computed<string[]>({
  get: () =>
    String(cfgGet("theme.disabled_categories", "") || "")
      .split(/[,，]/)
      .map((s) => s.trim())
      .filter(Boolean),
  set: (list: string[]) => cfgSet("theme.disabled_categories", list.join(",")),
});

const categoryOptions = computed(() => {
  const names = new Set<string>(BUILTIN);
  for (const k of Object.keys(extraMap.value)) {
    if (!["风格", "光色", "氛围", "构图"].includes(k)) names.add(k);
  }
  return [...names].map((n) => ({ label: n, value: n }));
});

const layerKeys = computed(() =>
  Object.keys(extraMap.value).filter((k) => ["风格", "光色", "氛围", "构图"].includes(k)),
);

async function save() {
  const r = await saveFields(["theme.disabled_categories", "theme.extra_words"], "主题词池");
  if (r.ok) message.success(`主题词池已保存（${r.changed} 项），下一张起生效`);
  else message.error(`保存失败：${r.error}`);
}

onMounted(loadConfig);
</script>

<template>
  <div class="page">
    <div class="mp-card">
      <div class="mp-card-head">
        <div class="mp-card-title">停用类别</div>
        <n-button size="small" type="primary" :loading="!!saving['主题词池']" @click="save">保存</n-button>
      </div>
      <div class="mp-hint" style="margin-bottom: 10px">
        抽签时整类跳过。比如只想要风景与星空，就把「美少女 / 人物肖像」停掉。
      </div>
      <n-select
        v-model:value="disabled"
        multiple
        :options="categoryOptions"
        size="small"
        placeholder="选择要停用的类别（不选 = 全部参与）"
      />
    </div>

    <div class="mp-card">
      <div class="mp-card-head">
        <div class="mp-card-title">词池扩充</div>
        <n-button size="small" type="primary" :loading="!!saving['主题词池']" @click="save">保存</n-button>
      </div>
      <div class="mp-hint" style="margin-bottom: 10px">
        每行一条 <code class="mp-mono">键=词1,词2,词3</code>。键的三种用法：<br />
        ① 内置类别名（如 <code class="mp-mono">宇宙星空=冰环行星的极夜</code>）→ 往该类别追加题材；<br />
        ② 新类别名（如 <code class="mp-mono">我的猫=橘猫打翻鱼缸</code>）→ 新增一个类别；<br />
        ③ 特殊层 <code class="mp-mono">风格 / 光色 / 氛围</code> → 往对应层追加词；
        <code class="mp-mono">构图=名称@landscape|portrait|square</code> → 追加构图项。
      </div>
      <n-input
        v-model:value="extraText"
        type="textarea"
        :rows="8"
        size="small"
        class="mp-mono"
        placeholder="宇宙星空=冰环行星的极夜,双月夜潮&#10;我的猫=橘猫打翻鱼缸&#10;风格=新海诚式光影,厚涂插画&#10;构图=教堂穹顶仰拍@portrait"
      />
      <div v-if="layerKeys.length" class="mp-sub" style="margin-top: 8px">
        已扩充的层：{{ layerKeys.join(" / ") }}
      </div>
    </div>

    <div class="mp-card mp-card--flat">
      <div class="mp-card-title">内置词池</div>
      <div class="pool">
        <span v-for="c in BUILTIN" :key="c" class="mp-tag" :class="{ off: disabled.includes(c) }">
          {{ c }}
        </span>
      </div>
      <div class="mp-hint" style="margin-top: 8px">
        内置只是「起点」：真正防模式塌缩的是每张图的题材 + 风格 + 构图 + 光色 + 氛围五层随机组合。
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

.pool {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 10px;
}

.mp-tag {
  font-size: 12px;
  padding: 4px 12px;
  border-radius: 999px;
  background: var(--mp-panel-soft);
  border: 1px solid var(--mp-line);
}

.mp-tag.off {
  opacity: 0.45;
  text-decoration: line-through;
}

code {
  padding: 1px 5px;
  border-radius: 5px;
  background: var(--mp-panel-soft);
}
</style>
