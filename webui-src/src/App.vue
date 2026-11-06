<script setup lang="ts">
// 控制台外壳：左侧导航 + 右侧内容（参考「萌萌星语」的左右结构）。
// 硬约束（sandbox iframe）：不直接碰 localStorage、不用原生 confirm/alert、
// 图片一律走 /thumb 的 data URL；主题只从 bridge context / 安全封装里取。
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  NButton,
  NConfigProvider,
  NDialogProvider,
  NDrawer,
  NDrawerContent,
  NMenu,
  NMessageProvider,
  NSpin,
  NTag,
  darkTheme,
  dateZhCN,
  zhCN,
} from "naive-ui";
import { PLUGIN_VERSION } from "./version";
import { menuOptions, MENU } from "./router";
import {
  conn,
  connError,
  initTheme,
  isDark,
  refreshStats,
  refreshStatus,
  schedTag,
  status,
  toggleTheme,
} from "./store";
import { fmtDue, untilText } from "./utils";

const route = useRoute();
const router = useRouter();
const drawerOpen = ref(false);
const isMobile = ref(false);
const refreshing = ref(false);

const activeKey = computed(() => route.path);
const current = computed(() => MENU.find((m) => m.key === route.path));
const version = PLUGIN_VERSION || "dev";

const overrides = {
  common: {
    primaryColor: "#6b78e0",
    primaryColorHover: "#8290ea",
    primaryColorPressed: "#5a67cc",
    primaryColorSuppl: "#9b6fd8",
    borderRadius: "10px",
    borderRadiusSmall: "8px",
  },
  Card: { borderRadius: "16px" },
  Tag: { borderRadius: "8px" },
};

const connType = computed(() =>
  conn.value === "ok" ? "success" : conn.value === "error" ? "error" : "default",
);
const connText = computed(() =>
  conn.value === "ok" ? "已连接" : conn.value === "error" ? "连接异常" : "连接中…",
);

function go(key: string) {
  drawerOpen.value = false;
  if (key !== route.path) router.push(key);
}

async function refresh() {
  refreshing.value = true;
  try {
    await Promise.all([refreshStatus(), refreshStats()]);
  } finally {
    refreshing.value = false;
  }
}

let media: MediaQueryList | null = null;
function onResize(e: MediaQueryList | MediaQueryListEvent) {
  isMobile.value = e.matches;
  if (!e.matches) drawerOpen.value = false;
}

onMounted(async () => {
  initTheme();
  await refresh();
  media = window.matchMedia("(max-width: 900px)");
  onResize(media);
  media.addEventListener("change", onResize);
});

onBeforeUnmount(() => {
  media?.removeEventListener("change", onResize);
});

// immediate：首屏就是暗色时也要打上 class（否则自定义变量不切换，只有 naive 组件是暗的）
watch(
  isDark,
  (v) => {
    document.documentElement.classList.toggle("mp-dark", v);
  },
  { immediate: true },
);
</script>

<template>
  <n-config-provider
    :theme="isDark ? darkTheme : null"
    :theme-overrides="overrides"
    :locale="zhCN"
    :date-locale="dateZhCN"
  >
    <n-message-provider>
      <n-dialog-provider>
        <div class="shell">
          <header class="topbar">
            <div class="brand">
              <span class="seal">绘</span>
              <div class="titles">
                <b>萌萌绘卷</b>
                <span class="mp-sub">定时自主作画 · 图库成卷</span>
              </div>
            </div>
            <div class="tools">
              <n-tag size="small" round :bordered="false" :type="schedTag.type">
                {{ schedTag.text }}
              </n-tag>
              <n-tag size="small" round :bordered="false" :type="connType" :title="connError">
                {{ connText }}
              </n-tag>
              <n-tag size="small" round :bordered="false">v{{ version }}</n-tag>
              <n-button size="small" quaternary :loading="refreshing" @click="refresh">刷新</n-button>
              <n-button size="small" quaternary @click="toggleTheme">
                {{ isDark ? "🌞 亮色" : "🌙 暗色" }}
              </n-button>
            </div>
          </header>

          <div class="body">
            <aside v-if="!isMobile" class="sider">
              <n-menu
                :value="activeKey"
                :options="menuOptions"
                :indent="16"
                :default-expanded-keys="['g-run', 'g-config', 'g-debug']"
                @update:value="go"
              />
              <div class="sider-foot">
                <div class="mp-sub">下次生成</div>
                <div class="due">{{ status?.next_due ? fmtDue(status.next_due) : "未安排" }}</div>
                <div class="mp-sub">{{ status?.next_due ? untilText(status.next_due) : "等待调度" }}</div>
                <div class="foot-split"></div>
                <div class="mp-sub">
                  今日 {{ status?.today_success ?? 0 }} 张 · 成功
                  {{ status?.counts.success ?? 0 }} / 失败 {{ status?.counts.failed ?? 0 }}
                </div>
              </div>
            </aside>

            <n-drawer v-model:show="drawerOpen" :width="240" placement="left">
              <n-drawer-content title="导航">
                <n-menu
                  :value="activeKey"
                  :options="menuOptions"
                  :indent="16"
                  :default-expanded-keys="['g-run', 'g-config', 'g-debug']"
                  @update:value="go"
                />
              </n-drawer-content>
            </n-drawer>

            <main class="content">
              <div v-if="isMobile" class="mobile-bar">
                <n-button size="small" @click="drawerOpen = true">☰ 导航</n-button>
                <span class="mp-sub">{{ current?.icon }} {{ current?.label }}</span>
              </div>
              <router-view v-slot="{ Component }">
                <n-spin :show="conn === 'idle'" size="small">
                  <component :is="Component" />
                </n-spin>
              </router-view>
            </main>
          </div>
        </div>
      </n-dialog-provider>
    </n-message-provider>
  </n-config-provider>
</template>

<style scoped>
.shell {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}

.topbar {
  position: sticky;
  top: 0;
  z-index: 20;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 20px;
  background: var(--mp-panel);
  border-bottom: 1px solid var(--mp-line);
  backdrop-filter: blur(10px);
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.seal {
  width: 34px;
  height: 34px;
  border-radius: 11px;
  display: grid;
  place-items: center;
  color: #fff;
  font-weight: 700;
  font-size: 17px;
  background: var(--mp-grad);
  box-shadow: 0 6px 16px rgba(107, 120, 224, 0.35);
}

.titles {
  display: flex;
  flex-direction: column;
  line-height: 1.25;
  min-width: 0;
}

.titles b {
  font-size: 15.5px;
  letter-spacing: 0.3px;
}

.tools {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.body {
  flex: 1;
  display: flex;
  min-height: 0;
}

.sider {
  width: 208px;
  flex: 0 0 208px;
  border-right: 1px solid var(--mp-line);
  background: var(--mp-panel);
  padding: 12px 8px 16px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  position: sticky;
  top: 55px;
  height: calc(100vh - 55px);
  overflow-y: auto;
}

.sider-foot {
  margin: 10px 10px 0;
  padding: 12px;
  border-radius: 12px;
  background: var(--mp-panel-soft);
  border: 1px dashed var(--mp-line-strong);
  font-size: 11.5px;
  line-height: 1.7;
}

.sider-foot .due {
  font-size: 14px;
  font-weight: 700;
  color: var(--mp-accent);
}

.foot-split {
  height: 1px;
  margin: 8px 0;
  background: var(--mp-line);
}

.content {
  flex: 1;
  min-width: 0;
  padding: 18px 22px 44px;
  max-width: 1240px;
  margin: 0 auto;
  width: 100%;
}

.mobile-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}

@media (max-width: 900px) {
  .content {
    padding: 14px 12px 36px;
  }
  .topbar {
    padding: 10px 12px;
  }
}
</style>
