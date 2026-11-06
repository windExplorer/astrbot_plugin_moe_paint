<script setup lang="ts">
// 关联萌绘：只读萌绘插件配置里的 ComfyUI 服务器列表，用它的地址出图。
// 设计取舍：不调用萌绘的内部接口（那是 2 万行单体的私有契约，升级即碎），
// 只共用「同一台 ComfyUI 服务器」这个稳定事实。
import { computed, onMounted, ref } from "vue";
import { NButton, NEmpty, NSelect, NSpin, NTag, useMessage } from "naive-ui";
import { apiAnima, apiAnimaSync, apiDebugComfy, type AnimaInfo, type ComfyProbe } from "../api";
import { refreshStatus } from "../store";

const message = useMessage();
const info = ref<AnimaInfo | null>(null);
const loading = ref(false);
const acting = ref(false);
const pickKey = ref("");
const probe = ref<ComfyProbe | null>(null);
const testing = ref(false);

const serverOptions = computed(() =>
  (info.value?.servers || []).map((s) => ({
    label: `${s.name} · ${s.url}${s.enabled ? "（萌绘默认）" : ""}`,
    value: s.key || s.name,
  })),
);

const following = computed(() => !!info.value?.follow_server);

async function load() {
  loading.value = true;
  try {
    info.value = await apiAnima();
    pickKey.value = info.value.server_key || info.value.active_key || "";
  } catch (e: any) {
    message.error(`读取萌绘联动状态失败：${e?.message || e}`);
  } finally {
    loading.value = false;
  }
}

async function act(action: "follow" | "copy" | "off", key?: string) {
  acting.value = true;
  try {
    const r = await apiAnimaSync(action, key ?? (action === "follow" ? pickKey.value : undefined));
    message.success(r.message);
    await Promise.all([load(), refreshStatus()]);
    probe.value = null;
  } catch (e: any) {
    message.error(`操作失败：${e?.message || e}`);
  } finally {
    acting.value = false;
  }
}

async function useServer(key: string, url: string) {
  pickKey.value = key;
  await act("follow", key);
  await testUrl(url);
}

async function testUrl(url?: string) {
  testing.value = true;
  try {
    probe.value = await apiDebugComfy(url || "");
    const ok = probe.value.ok;
    message[ok ? "success" : "error"](
      ok
        ? `连接正常（${probe.value.elapsed_ms}ms）`
        : `连接失败：${probe.value.error || "未知错误"}`,
    );
  } catch (e: any) {
    message.error(`测试失败：${e?.message || e}`);
  } finally {
    testing.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div class="page">
    <n-spin :show="loading">
      <!-- 总览 -->
      <div class="mp-card head">
        <div class="head-main">
          <div class="mp-card-title">关联萌绘（{{ info?.anima_plugin_name || "astrbot_plugin_comfyui_anima" }}）</div>
          <div class="mp-hint">
            绘卷不依赖萌绘：它只是<b>复用萌绘里已经配好的 ComfyUI 服务器地址</b>，省掉两处填同一个 URL。
            两边共用同一台服务器时，绘卷的「队列避让」会自动给萌绘的交互出图让路。
            工作流、图库、模型列表各自独立管理（不互相读写）。
          </div>
        </div>
        <n-tag size="small" round :bordered="false" :type="info?.installed ? 'success' : 'warning'">
          {{ info?.installed ? `已安装 ${info.version || ""}` : "未安装萌绘" }}
        </n-tag>
      </div>

      <div v-if="!info?.installed" class="mp-card">
        <n-empty description="没有检测到 astrbot_plugin_comfyui_anima，装好并启用后再回来看这一页" class="mp-empty" />
        <div class="mp-hint" style="text-align: center">
          没装也没关系：在「生成服务」页直接填 ComfyUI 地址即可正常出图。
        </div>
      </div>

      <template v-else>
        <!-- 生效地址 -->
        <div class="mp-card">
          <div class="mp-card-head">
            <div class="mp-card-title">当前生效的服务器</div>
            <div class="mp-row">
              <n-button size="small" :loading="testing" @click="testUrl(info?.effective_url)">测试连接</n-button>
              <n-button size="small" quaternary @click="load">刷新</n-button>
            </div>
          </div>

          <div class="effective">
            <div>
              <div class="eff-url mp-mono">{{ info?.effective_url || "—" }}</div>
              <div class="mp-sub">
                来源：{{ info?.url_source === "anima" ? "🪄 跟随萌绘" : "🛠 本插件手动配置" }}
                <template v-if="info?.active_name"> · 萌绘服务器「{{ info.active_name }}」</template>
              </div>
            </div>
            <n-tag size="small" :bordered="false" :type="following ? 'success' : 'default'">
              {{ following ? "跟随已开启" : "跟随已关闭" }}
            </n-tag>
          </div>

          <div v-if="probe" class="probe" :class="{ bad: !probe.ok }">
            <n-tag size="small" :type="probe.ok ? 'success' : 'error'" :bordered="false">
              {{ probe.ok ? "连通" : "失败" }}
            </n-tag>
            <span class="mp-mono">{{ probe.url }}</span>
            <span v-if="probe.ok" class="mp-sub">
              延迟 {{ probe.elapsed_ms }}ms
              <template v-if="probe.queue"> · 执行 {{ probe.queue.running }} / 排队 {{ probe.queue.pending }}</template>
            </span>
            <span v-else class="mp-err">{{ probe.error }}</span>
          </div>

          <div class="actions">
            <div class="follow-box">
              <n-select
                v-model:value="pickKey"
                :options="serverOptions"
                size="small"
                placeholder="选择萌绘的一台服务器"
                style="min-width: 300px"
              />
              <n-button size="small" type="primary" :loading="acting" @click="act('follow')">
                跟随这台
              </n-button>
            </div>
            <div class="mp-row">
              <n-button size="small" :loading="acting" @click="act('copy')">复制地址到本插件</n-button>
              <n-button size="small" quaternary :disabled="!following" :loading="acting" @click="act('off')">
                关闭跟随
              </n-button>
            </div>
          </div>

          <div class="mp-hint" style="margin-top: 10px">
            「跟随」= 每次出图都现读萌绘当前启用的服务器，萌绘换了地址绘卷自动跟上；
            「复制」= 把地址抄一份到本插件，之后各配各的（萌绘再改不会同步）。
          </div>
        </div>

        <!-- 服务器列表 -->
        <div class="mp-card">
          <div class="mp-card-head">
            <div class="mp-card-title">萌绘的服务器列表（只读）</div>
            <span class="mp-sub">共 {{ info?.servers?.length || 0 }} 台</span>
          </div>
          <n-empty v-if="!info?.servers?.length" :description="info?.error || '萌绘里还没有配置服务器'" />
          <div v-for="s in info?.servers || []" :key="s.key || s.name" class="server">
            <div class="server-main">
              <div class="server-name">
                {{ s.name }}
                <n-tag v-if="s.enabled" size="tiny" :bordered="false" type="success">萌绘默认</n-tag>
                <n-tag v-if="s.key && s.key === info?.active_key" size="tiny" :bordered="false" type="info">当前生效</n-tag>
              </div>
              <div class="mp-mono mp-sub">{{ s.url }}</div>
              <div v-if="s.device" class="mp-sub">{{ s.device }}</div>
            </div>
            <div class="mp-row">
              <n-button size="small" :loading="acting" @click="useServer(s.key || s.name, s.url)">用这台</n-button>
            </div>
          </div>
        </div>

        <!-- 联动边界 -->
        <div class="mp-card mp-card--flat">
          <div class="mp-card-title">联动的边界</div>
          <ul class="notes">
            <li>只读取萌绘的 <code class="mp-mono">comfyui_servers[].url</code>，不写萌绘的任何配置。</li>
            <li>不调用萌绘的绘图接口（<code class="mp-mono">comfyui_draw</code>）——绘卷直接连 ComfyUI HTTP API，萌绘升级不影响这里。</li>
            <li>共用服务器时靠「队列避让阈值」协调：定时出图发现前方有萌绘的任务就顺延，不抢队列。</li>
            <li>工作流模板、图库、提示词风格都是各自独立的一套。</li>
          </ul>
        </div>
      </template>
    </n-spin>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
}

.head-main {
  min-width: 0;
}

.effective {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  padding: 12px 14px;
  border-radius: 12px;
  background: var(--mp-panel-soft);
  border: 1px dashed var(--mp-line-strong);
}

.eff-url {
  font-size: 14px;
  font-weight: 700;
  word-break: break-all;
}

.actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  margin-top: 12px;
}

.follow-box {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.probe {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 10px;
  padding: 9px 12px;
  border-radius: 10px;
  background: rgba(63, 157, 118, 0.1);
  font-size: 12px;
}

.probe.bad {
  background: rgba(217, 96, 95, 0.1);
}

.server {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 12px;
  border-radius: 12px;
  border: 1px solid var(--mp-line);
  margin-bottom: 8px;
  background: var(--mp-panel-solid);
}

.server-main {
  min-width: 0;
}

.server-name {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 700;
  font-size: 13px;
}

.notes {
  margin: 10px 0 0;
  padding-left: 18px;
  font-size: 12.5px;
  line-height: 1.9;
  color: var(--mp-sub);
}

code {
  padding: 1px 5px;
  border-radius: 5px;
  background: var(--mp-panel-soft);
}
</style>
