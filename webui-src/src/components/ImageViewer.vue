<script setup lang="ts">
// 全屏看图：图库点缩略图 / 抽屉里点大图都走这里。
// 交互：滚轮缩放（1~5x）、放大后可拖动、双击复位、←/→ 切换、Esc 关闭。
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";

const props = defineProps<{
  show: boolean;
  src: string;
  title?: string;
  subtitle?: string;
  index?: number;
  total?: number;
  loading?: boolean;
}>();

const emit = defineEmits<{
  (e: "update:show", v: boolean): void;
  (e: "prev"): void;
  (e: "next"): void;
}>();

const scale = ref(1);
const tx = ref(0);
const ty = ref(0);
const dragging = ref(false);
let startX = 0;
let startY = 0;
let baseX = 0;
let baseY = 0;

const zoomed = computed(() => scale.value > 1.01);
const canPrev = computed(() => (props.index ?? 0) > 0);
const canNext = computed(() => (props.index ?? -1) < (props.total ?? 0) - 1);
const counter = computed(() =>
  props.total && props.total > 1 ? `${(props.index ?? 0) + 1} / ${props.total}` : "",
);

function reset(): void {
  scale.value = 1;
  tx.value = 0;
  ty.value = 0;
}

watch(
  () => [props.show, props.src],
  () => reset(),
);

function close(): void {
  emit("update:show", false);
}

function onWheel(e: WheelEvent): void {
  const step = e.deltaY > 0 ? -0.2 : 0.2;
  const next = Math.min(5, Math.max(1, Math.round((scale.value + step) * 100) / 100));
  scale.value = next;
  if (next === 1) {
    tx.value = 0;
    ty.value = 0;
  }
}

function onPointerDown(e: PointerEvent): void {
  if (!zoomed.value) return;
  dragging.value = true;
  startX = e.clientX;
  startY = e.clientY;
  baseX = tx.value;
  baseY = ty.value;
  (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
}

function onPointerMove(e: PointerEvent): void {
  if (!dragging.value) return;
  tx.value = baseX + (e.clientX - startX);
  ty.value = baseY + (e.clientY - startY);
}

function onPointerUp(): void {
  dragging.value = false;
}

function onKey(e: KeyboardEvent): void {
  if (!props.show) return;
  if (e.key === "Escape") close();
  else if (e.key === "ArrowLeft" && canPrev.value) emit("prev");
  else if (e.key === "ArrowRight" && canNext.value) emit("next");
  else if (e.key === "0") reset();
}

onMounted(() => window.addEventListener("keydown", onKey));
onBeforeUnmount(() => window.removeEventListener("keydown", onKey));
</script>

<template>
  <Teleport to="body">
    <div v-if="show" class="viewer" @click.self="close">
      <div class="vbar">
        <div class="vinfo">
          <b>{{ title || "查看大图" }}</b>
          <span v-if="subtitle" class="vsub">{{ subtitle }}</span>
          <span v-if="counter" class="vcount">{{ counter }}</span>
        </div>
        <div class="vact">
          <button class="vbtn" :disabled="!zoomed" @click="reset">复位</button>
          <button class="vbtn" @click="close">关闭 ✕</button>
        </div>
      </div>

      <div class="vstage" @wheel.prevent="onWheel" @dblclick="reset">
        <div v-if="loading" class="vloading">加载大图…</div>
        <img
          v-else-if="src"
          :src="src"
          alt=""
          class="vimg"
          :class="{ grab: zoomed, grabbing: dragging }"
          :style="{ transform: `translate(${tx}px, ${ty}px) scale(${scale})` }"
          @pointerdown="onPointerDown"
          @pointermove="onPointerMove"
          @pointerup="onPointerUp"
          @pointercancel="onPointerUp"
        />
        <div v-else class="vloading">没有可显示的图片</div>
      </div>

      <button class="vnav left" :disabled="!canPrev" @click.stop="emit('prev')">‹</button>
      <button class="vnav right" :disabled="!canNext" @click.stop="emit('next')">›</button>

      <div class="vhint">
        滚轮缩放 · 放大后拖动 · 双击复位 · ←/→ 切换 · Esc 关闭
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.viewer {
  position: fixed;
  inset: 0;
  z-index: 3200;
  display: flex;
  flex-direction: column;
  background: rgba(12, 10, 20, 0.92);
  backdrop-filter: blur(6px);
  color: #f2f0f8;
}

.vbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 18px;
  background: linear-gradient(180deg, rgba(0, 0, 0, 0.42), transparent);
}

.vinfo {
  display: flex;
  align-items: baseline;
  gap: 10px;
  min-width: 0;
}

.vinfo b {
  font-size: 14px;
}

.vsub {
  font-size: 12px;
  opacity: 0.72;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 52vw;
}

.vcount {
  font-size: 12px;
  opacity: 0.6;
  font-variant-numeric: tabular-nums;
}

.vact {
  display: flex;
  gap: 8px;
}

.vbtn {
  padding: 5px 12px;
  border-radius: 8px;
  border: 1px solid rgba(255, 255, 255, 0.24);
  background: rgba(255, 255, 255, 0.1);
  color: #f2f0f8;
  font-size: 12px;
  cursor: pointer;
}

.vbtn:hover:not(:disabled) {
  background: rgba(255, 255, 255, 0.18);
}

.vbtn:disabled {
  opacity: 0.4;
  cursor: default;
}

.vstage {
  flex: 1;
  min-height: 0;
  /* 用 flex 而不是 grid：单行 grid 的行高是 auto（由内容决定），
     `max-height:100%` 在这里解析成「无约束」—— 大图会按原始像素渲染，
     再被 overflow:hidden 裁掉，表现就是「只显示中间一部分」。
     flex 容器的高度由 flex:1 确定为可用高度，百分比才能生效。 */
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  padding: 8px 56px;
}

.vimg {
  /* 只写 max-*，让浏览器按原比例缩到「宽高都不超过舞台」 */
  width: auto;
  height: auto;
  max-width: 100%;
  max-height: 100%;
  min-width: 0;
  min-height: 0;
  display: block;
  object-fit: contain;
  border-radius: 6px;
  box-shadow: 0 24px 60px rgba(0, 0, 0, 0.55);
  transition: transform 0.08s ease-out;
  cursor: zoom-in;
  user-select: none;
  touch-action: none;
}

.vimg.grab {
  cursor: grab;
}

.vimg.grabbing {
  cursor: grabbing;
  transition: none;
}

.vloading {
  font-size: 13px;
  opacity: 0.75;
}

.vnav {
  position: absolute;
  top: 50%;
  transform: translateY(-50%);
  width: 44px;
  height: 64px;
  border: none;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.1);
  color: #fff;
  font-size: 30px;
  line-height: 1;
  cursor: pointer;
}

.vnav:hover:not(:disabled) {
  background: rgba(255, 255, 255, 0.2);
}

.vnav:disabled {
  opacity: 0.2;
  cursor: default;
}

.vnav.left {
  left: 8px;
}

.vnav.right {
  right: 8px;
}

.vhint {
  padding: 10px 16px 14px;
  text-align: center;
  font-size: 11.5px;
  opacity: 0.55;
}

@media (max-width: 700px) {
  .vstage {
    padding: 6px 10px;
  }
  .vnav {
    width: 34px;
    height: 48px;
    font-size: 22px;
  }
  .vsub {
    max-width: 40vw;
  }
}
</style>
