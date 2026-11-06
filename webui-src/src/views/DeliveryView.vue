<script setup lang="ts">
// 投递：显式 opt-in 的会话名单 + 文案。空名单 = 只入库不投递。
import { computed, onMounted, ref } from "vue";
import { NButton, NInput, NSelect, NSwitch, NTag, useMessage } from "naive-ui";
import { apiDebugDelivery } from "../api";
import { cfgBool, cfgGet, cfgSet, loadConfig, saveFields, saving, splitLines } from "../config";
import { refreshStatus, status } from "../store";

const message = useMessage();
const testing = ref(false);
const testUmo = ref("");
const testResult = ref<{ ok: boolean; text: string } | null>(null);

const sessionsText = computed({
  get: () => (Array.isArray(cfgGet("delivery.sessions", [])) ? cfgGet("delivery.sessions", []).join("\n") : String(cfgGet("delivery.sessions", ""))),
  set: (s: string) => cfgSet("delivery.sessions", splitLines(s)),
});

const sessionCount = computed(() => splitLines(sessionsText.value).length);

const umoOptions = computed(() =>
  splitLines(sessionsText.value).map((u) => ({ label: u, value: u })),
);

const template = computed(() => String(cfgGet("delivery.caption_template", "{caption}")));

const previewText = computed(() =>
  template.value
    .replace("{caption}", "银河在湖面落下一条光的路")
    .replace("{category}", "宇宙星空")
    .replace("{subject}", "银河拱桥下的湖面")
    .replace("{workflow}", "anima 默认"),
);

async function save() {
  const r = await saveFields(
    ["delivery.sessions", "delivery.caption_on", "delivery.caption_template"],
    "投递",
  );
  if (r.ok) {
    message.success(`投递设置已保存（${r.changed} 项）`);
    await refreshStatus();
  } else {
    message.error(`保存失败：${r.error}`);
  }
}

async function testSend() {
  const umo = testUmo.value.trim();
  if (!umo) {
    message.warning("请选择或填写目标会话 UMO");
    return;
  }
  testing.value = true;
  testResult.value = null;
  try {
    const r = await apiDebugDelivery(umo, "萌萌绘卷 · 投递自检消息（收到说明投递链路正常）");
    testResult.value = { ok: r.ok, text: r.ok ? "已发送，去会话里看看有没有收到" : r.error || "发送失败" };
    message[r.ok ? "success" : "error"](testResult.value.text);
  } catch (e: any) {
    testResult.value = { ok: false, text: e?.message || String(e) };
    message.error(testResult.value.text);
  } finally {
    testing.value = false;
  }
}

onMounted(loadConfig);
</script>

<template>
  <div class="page">
    <div class="mp-card">
      <div class="mp-card-head">
        <div class="mp-card-title">投递会话</div>
        <div class="mp-row">
          <n-tag size="small" round :bordered="false" :type="sessionCount ? 'success' : 'default'">
            {{ sessionCount ? `${sessionCount} 个会话` : "未配置（只入库）" }}
          </n-tag>
          <n-button size="small" type="primary" :loading="!!saving['投递']" @click="save">保存</n-button>
        </div>
      </div>

      <div class="mp-hint" style="margin-bottom: 10px">
        每行一个会话 UMO。<b>留空 = 只存图库、不往任何会话发</b>（显式 opt-in，绝不「空 = 全放行」）。
        也可以在群里直接发指令让 bot 把当前会话加进来：<code>/绘卷 投递 开启</code>。
      </div>

      <n-input
        :value="sessionsText"
        type="textarea"
        :rows="5"
        size="small"
        placeholder="aiocqhttp:GroupMessage:123456&#10;aiocqhttp:FriendMessage:654321"
        @update:value="(v: string) => (sessionsText = v)"
      />

      <div class="test">
        <span class="mp-sub">连通性自检</span>
        <n-select
          v-model:value="testUmo"
          :options="umoOptions"
          size="small"
          filterable
          tag
          placeholder="选一个已有会话，或直接输入 UMO"
          style="min-width: 280px; flex: 1"
        />
        <n-button size="small" :loading="testing" @click="testSend">发测试消息</n-button>
      </div>
      <div v-if="testResult" class="mp-sub" :class="testResult.ok ? 'ok-text' : 'bad-text'">
        {{ testResult.text }}
      </div>
    </div>

    <div class="mp-card">
      <div class="mp-card-head">
        <div class="mp-card-title">投递文案</div>
        <n-button size="small" type="primary" :loading="!!saving['投递']" @click="save">保存</n-button>
      </div>

      <div class="mp-fields">
        <label class="mp-field">
          <span class="label">随图附带一句话</span>
          <n-switch
            :value="cfgBool('delivery.caption_on', true)"
            size="small"
            @update:value="(v: boolean) => cfgSet('delivery.caption_on', v)"
          />
          <span class="mp-sub">关闭 = 只发图，不带任何文字</span>
        </label>
        <label class="mp-field mp-field--wide">
          <span class="label">文案模板</span>
          <n-input
            :value="template"
            size="small"
            placeholder="{caption}"
            @update:value="(v: string) => cfgSet('delivery.caption_template', v)"
          />
          <span class="mp-sub">占位符：{caption} 一句话文案 · {category} 类别 · {subject} 题材 · {workflow} 工作流名</span>
        </label>
      </div>

      <div class="preview">
        <span class="mp-sub">效果预览：</span>
        <span>{{ cfgBool("delivery.caption_on", true) ? previewText : "（只发图，无文案）" }}</span>
      </div>
    </div>

    <div class="mp-card mp-card--flat">
      <div class="mp-card-title">当前投递状态</div>
      <div class="mp-hint">
        运行时读到的名单数量：<b>{{ status?.delivery_sessions ?? 0 }}</b> 个。
        重投单张图请到「图库」详情抽屉点「重投白名单」。
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

.test {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px dashed var(--mp-line);
}

.ok-text {
  color: var(--mp-ok);
  margin-top: 6px;
}

.bad-text {
  color: var(--mp-err);
  margin-top: 6px;
  word-break: break-all;
}

.preview {
  margin-top: 12px;
  padding: 10px 12px;
  border-radius: 10px;
  background: var(--mp-panel-soft);
  border: 1px dashed var(--mp-line-strong);
  font-size: 12.5px;
}

code {
  font-family: ui-monospace, monospace;
  font-size: 11.5px;
  padding: 1px 5px;
  border-radius: 5px;
  background: var(--mp-panel-soft);
}
</style>
