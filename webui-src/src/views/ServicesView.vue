<script setup lang="ts">
// 生成服务：ComfyUI 服务器 + 提示词模型 + 图库容量/通知。
// 关联萌绘的「跟随地址」在单独的「关联萌绘」页，这里只管本插件自己的地址。
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import {
  NButton,
  NInput,
  NInputNumber,
  NSelect,
  NSwitch,
  NTag,
  useMessage,
} from "naive-ui";
import { apiDebugComfy, type ComfyProbe } from "../api";
import {
  cfgBool,
  cfgGet,
  cfgNum,
  cfgSet,
  loadConfig,
  providers,
  saveFields,
  saving,
  schemaOf,
  unknownKeys,
} from "../config";
import { refreshStatus, status } from "../store";

const message = useMessage();
const router = useRouter();

const testing = ref(false);
const probe = ref<ComfyProbe | null>(null);

const providerOptions = computed(() => [
  { label: "全局默认提供商", value: "" },
  ...providers.value.map((p) => ({
    label: p.label + (p.is_default ? "（AstrBot 默认）" : ""),
    value: p.id,
  })),
]);

const following = computed(() => !!status.value?.anima?.follow_server);

async function testComfy() {
  testing.value = true;
  try {
    probe.value = await apiDebugComfy("");
    if (probe.value.ok) {
      const q = probe.value.queue;
      message.success(
        `连接正常（${probe.value.elapsed_ms}ms）` +
          (q ? `，队列：执行 ${q.running} / 排队 ${q.pending}` : ""),
      );
    } else {
      message.error(`连接失败：${probe.value.error || "未知错误"}`);
    }
  } catch (e: any) {
    message.error(`测试失败：${e?.message || e}`);
  } finally {
    testing.value = false;
  }
}

async function save(fields: string[], label: string) {
  const r = await saveFields(fields, label);
  if (r.ok) {
    message.success(`${label}已保存（${r.changed} 项）`);
    await refreshStatus();
    if (label === "ComfyUI") probe.value = null;
  } else {
    message.error(`保存失败：${r.error}`);
  }
}

onMounted(loadConfig);
</script>

<template>
  <div class="page">
    <!-- ComfyUI -->
    <div class="mp-card">
      <div class="mp-card-head">
        <div class="mp-card-title">ComfyUI 服务器</div>
        <div class="mp-row">
          <n-button size="small" :loading="testing" @click="testComfy">测试连接</n-button>
          <n-button size="small" type="primary" :loading="!!saving['ComfyUI']" @click="save([
            'comfyui.url', 'comfyui.draw_timeout', 'comfyui.queue_extra_timeout',
            'comfyui.hard_cap', 'comfyui.poll_interval', 'comfyui.queue_defer_threshold',
          ], 'ComfyUI')">保存</n-button>
        </div>
      </div>

      <div v-if="following" class="notice">
        🪄 当前已开启「跟随萌绘」：实际用的是萌绘里的地址
        <span class="mp-mono">{{ status?.comfy?.url }}</span>
        ，下面的地址不会生效。
        <n-button text type="primary" size="small" @click="router.push('/anima')">去调整 →</n-button>
      </div>

      <div class="mp-fields">
        <label class="mp-field mp-field--wide">
          <span class="label">服务器地址（手动）</span>
          <n-input
            :value="String(cfgGet('comfyui.url', 'http://127.0.0.1:8188'))"
            size="small"
            placeholder="http://127.0.0.1:8188"
            :disabled="following"
            @update:value="(v: string) => cfgSet('comfyui.url', v)"
          />
          <span class="mp-sub">需带 http:// 或 https://；与萌绘共用同一台时建议在「关联萌绘」页开启跟随</span>
        </label>
        <label class="mp-field">
          <span class="label">单张基础等待（秒）</span>
          <n-input-number
            :value="cfgNum('comfyui.draw_timeout', 180)"
            :min="30" :max="3600" size="small"
            @update:value="(v: number | null) => cfgSet('comfyui.draw_timeout', v ?? 180)"
          />
          <span class="mp-sub">不含排队的基础预算</span>
        </label>
        <label class="mp-field">
          <span class="label">每任务排队加时（秒）</span>
          <n-input-number
            :value="cfgNum('comfyui.queue_extra_timeout', 60)"
            :min="0" :max="3600" size="small"
            @update:value="(v: number | null) => cfgSet('comfyui.queue_extra_timeout', v ?? 60)"
          />
          <span class="mp-sub">前方每个任务多等这么久</span>
        </label>
        <label class="mp-field">
          <span class="label">等待硬上限（秒）</span>
          <n-input-number
            :value="cfgNum('comfyui.hard_cap', 600)"
            :min="30" :max="14400" size="small"
            @update:value="(v: number | null) => cfgSet('comfyui.hard_cap', v ?? 600)"
          />
          <span class="mp-sub">超过就记失败，不会无限挂着</span>
        </label>
        <label class="mp-field">
          <span class="label">轮询间隔（秒）</span>
          <n-input-number
            :value="cfgNum('comfyui.poll_interval', 2)"
            :min="1" :max="30" size="small"
            @update:value="(v: number | null) => cfgSet('comfyui.poll_interval', v ?? 2)"
          />
          <span class="mp-sub">查历史的频率</span>
        </label>
        <label class="mp-field">
          <span class="label">队列避让阈值（0 = 关）</span>
          <n-input-number
            :value="cfgNum('comfyui.queue_defer_threshold', 2)"
            :min="0" :max="100" size="small"
            @update:value="(v: number | null) => cfgSet('comfyui.queue_defer_threshold', v ?? 0)"
          />
          <span class="mp-sub">共用服务器时，定时出图发现前方 ≥ 该任务数就顺延 5 分钟，给交互出图让路</span>
        </label>
      </div>

      <div v-if="probe" class="probe" :class="{ bad: !probe.ok }">
        <n-tag size="small" :type="probe.ok ? 'success' : 'error'" :bordered="false">
          {{ probe.ok ? "连通" : "失败" }}
        </n-tag>
        <span class="mp-mono">{{ probe.url }}</span>
        <span v-if="probe.ok" class="mp-sub">
          延迟 {{ probe.elapsed_ms }}ms
          <template v-if="probe.queue">· 执行 {{ probe.queue.running }} / 排队 {{ probe.queue.pending }}</template>
        </span>
        <span v-else class="mp-err">{{ probe.error }}</span>
      </div>
    </div>

    <!-- LLM -->
    <div class="mp-card">
      <div class="mp-card-head">
        <div class="mp-card-title">提示词模型（LLM）</div>
        <n-button size="small" type="primary" :loading="!!saving['提示词模型']" @click="save(['llm.provider_id', 'llm.timeout'], '提示词模型')">
          保存
        </n-button>
      </div>
      <div class="mp-fields">
        <label class="mp-field mp-field--wide">
          <span class="label">提供商（一提供商 = 一模型）</span>
          <n-select
            :value="String(cfgGet('llm.provider_id', ''))"
            :options="providerOptions"
            size="small"
            filterable
            tag
            :consistent-menu-width="false"
            @update:value="(v: string) => cfgSet('llm.provider_id', v)"
          />
          <span class="mp-sub">留空 = AstrBot 全局默认对话模型；名单外的 id 可直接输入</span>
        </label>
        <label class="mp-field">
          <span class="label">调用超时（秒）</span>
          <n-input-number
            :value="cfgNum('llm.timeout', 45)"
            :min="10" :max="300" size="small"
            @update:value="(v: number | null) => cfgSet('llm.timeout', v ?? 45)"
          />
          <span class="mp-sub">超时本轮记失败，等下一轮调度</span>
        </label>
      </div>
      <div class="mp-hint" style="margin-top: 10px">
        写提示词用的是纯文本对话接口。想验证模型是否听话、输出契约是否合规，
        去「调试与日志」页点「LLM 试跑」，会显示简报、原始返回与耗时。
      </div>
    </div>

    <!-- 图库与通知 -->
    <div class="mp-card">
      <div class="mp-card-head">
        <div class="mp-card-title">图库与通知</div>
        <n-button size="small" type="primary" :loading="!!saving['图库与通知']" @click="save(['gallery.max_total_mb', 'admin_notify_umo'], '图库与通知')">
          保存
        </n-button>
      </div>
      <div class="mp-fields">
        <label class="mp-field">
          <span class="label">容量上限（MB，0 = 不淘汰）</span>
          <n-input-number
            :value="cfgNum('gallery.max_total_mb', 1024)"
            :min="0" :max="1048576" size="small"
            @update:value="(v: number | null) => cfgSet('gallery.max_total_mb', v ?? 1024)"
          />
          <span class="mp-sub">超限按最旧优先淘汰未收藏的图（★ 收藏永不淘汰）</span>
        </label>
        <label class="mp-field">
          <span class="label">管理员通知 UMO</span>
          <n-input
            :value="String(cfgGet('admin_notify_umo', ''))"
            size="small"
            placeholder="留空不通知，如 aiocqhttp:FriendMessage:123456"
            @update:value="(v: string) => cfgSet('admin_notify_umo', v)"
          />
          <span class="mp-sub">连续失败自动暂停时往这里发提醒</span>
        </label>
      </div>
    </div>

    <!-- 兜底：schema 里新增但页面还没编排的键 -->
    <div v-if="unknownKeys.length" class="mp-card">
      <div class="mp-card-head">
        <div class="mp-card-title">其他配置</div>
        <n-button size="small" type="primary" :loading="!!saving['其他']" @click="save(unknownKeys, '其他')">
          保存
        </n-button>
      </div>
      <div class="mp-hint" style="margin-bottom: 10px">
        这些键还没有专属界面（通常是升级后新增的），先在这里兜底编辑。
      </div>
      <div class="mp-fields">
        <label v-for="k in unknownKeys" :key="k" class="mp-field">
          <span class="label">{{ schemaOf(k).description || k }}</span>
          <n-switch
            v-if="schemaOf(k).type === 'bool'"
            :value="cfgBool(k, false)"
            size="small"
            @update:value="(v: boolean) => cfgSet(k, v)"
          />
          <n-input-number
            v-else-if="['int', 'float'].includes(schemaOf(k).type)"
            :value="Number(cfgGet(k, 0))"
            size="small"
            @update:value="(v: number | null) => cfgSet(k, v ?? 0)"
          />
          <n-input
            v-else
            :value="String(cfgGet(k, ''))"
            size="small"
            @update:value="(v: string) => cfgSet(k, v)"
          />
          <span class="mp-mono mp-sub">{{ k }}</span>
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

.notice {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  font-size: 12.5px;
  padding: 8px 12px;
  border-radius: 10px;
  background: rgba(107, 120, 224, 0.1);
  border: 1px dashed rgba(107, 120, 224, 0.4);
  margin-bottom: 12px;
}

.probe {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 12px;
  padding: 9px 12px;
  border-radius: 10px;
  background: rgba(63, 157, 118, 0.1);
  font-size: 12px;
}

.probe.bad {
  background: rgba(217, 96, 95, 0.1);
}
</style>
