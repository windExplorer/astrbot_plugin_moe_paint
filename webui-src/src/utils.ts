// 纯格式化 / 计划文案工具（无副作用，页面共用）。

export function fmtTs(ts: number | null | undefined): string {
  if (!ts) return "-";
  const d = new Date(ts * 1000);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

export function fmtTsFull(ts: number | null | undefined): string {
  if (!ts) return "-";
  const d = new Date(ts * 1000);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

export function fmtDur(ms: number | null | undefined): string {
  if (ms == null) return "-";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

export function parseBrief(brief: string): Record<string, string> {
  try {
    const v = JSON.parse(brief || "{}");
    return typeof v === "object" && v ? v : {};
  } catch {
    return {};
  }
}

export function briefText(brief: string): string {
  const b = parseBrief(brief);
  if (!b || !Object.keys(b).length) return brief || "-";
  return [
    `类别=${b.category ?? "-"}`,
    `题材=${b.subject ?? "-"}`,
    `风格=${b.style ?? "-"}`,
    `构图=${b.composition ?? "-"}`,
    `光色=${b.light ?? "-"}`,
    `氛围=${b.mood ?? "-"}`,
  ].join("；");
}

/** 分钟 → 人话（1440 整除按天、60 整除按小时）。 */
export function minutesHuman(minutes: number | null | undefined): string {
  const n = Math.max(1, Math.round(Number(minutes) || 0));
  if (n % 1440 === 0) return `${n / 1440} 天`;
  if (n % 60 === 0) return `${n / 60} 小时`;
  return `${n} 分钟`;
}

/** "01:00-08:00" → ["01:00","08:00"]（非法/空回落默认）。 */
export function splitQuiet(text: string | null | undefined, dflt = "01:00-08:00"): [string, string] {
  const raw = String(text || "").trim() || dflt;
  const parts = raw.replace(/[～—–：]/g, (m) => (m === "：" ? ":" : "-")).split("-");
  const norm = (s: string, fb: string) => {
    const m = String(s || "").trim().match(/^(\d{1,2}):(\d{2})$/);
    if (!m) return fb;
    const h = Number(m[1]);
    const mi = Number(m[2]);
    if (h > 23 || mi > 59) return fb;
    return `${String(h).padStart(2, "0")}:${String(mi).padStart(2, "0")}`;
  };
  return [norm(parts[0] || "", "01:00"), norm(parts[1] || "", "08:00")];
}

export function joinQuiet(start: string, end: string): string {
  return `${start || "01:00"}-${end || "08:00"}`;
}

/** 解析 "YYYY-MM-DD HH:MM:SS"（后端 time.strftime 输出，按本地时间解释）。 */
export function parseDue(text: string | null | undefined): Date | null {
  if (!text) return null;
  const m = String(text).match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?/);
  if (!m) {
    const d = new Date(String(text));
    return Number.isNaN(d.getTime()) ? null : d;
  }
  return new Date(
    Number(m[1]),
    Number(m[2]) - 1,
    Number(m[3]),
    Number(m[4]),
    Number(m[5]),
    Number(m[6] || 0),
  );
}

/** 秒 → 「3 分 12 秒」/「12 秒」/「1 小时 5 分」。 */
export function fmtDurationSec(sec: number | null | undefined): string {
  const s = Math.max(0, Math.round(Number(sec) || 0));
  if (s < 60) return `${s} 秒`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} 分 ${s % 60} 秒`;
  const h = Math.floor(m / 60);
  return `${h} 小时 ${m % 60} 分`;
}

export function untilText(iso: string | null | undefined): string {
  const d = parseDue(iso);
  if (!d) return "未安排";
  const diff = d.getTime() - Date.now();
  if (diff <= 0) return "即将开始";
  const mins = Math.round(diff / 60000);
  if (mins < 60) return `还有 ${mins} 分钟`;
  const hours = Math.floor(mins / 60);
  const rest = mins % 60;
  if (hours < 24) return `还有 ${hours} 小时${rest ? ` ${rest} 分钟` : ""}`;
  return `还有 ${Math.floor(hours / 24)} 天 ${hours % 24} 小时`;
}

export function fmtDue(iso: string | null | undefined): string {
  const d = parseDue(iso);
  if (!d) return "未安排";
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** 计划摘要（给总览/调度页看的一句话）。 */
export function scheduleSummary(s: {
  mode?: string;
  interval_minutes?: number | null;
  jitter_minutes?: number | null;
  daily_times?: string;
  quiet_enabled?: boolean;
  quiet_hours?: string;
} | null): string {
  if (!s) return "-";
  let base: string;
  if (String(s.mode || "interval") === "daily") {
    base = `每天 ${String(s.daily_times || "").trim() || "（未填时刻）"}`;
  } else {
    const jitter = Number(s.jitter_minutes || 0);
    base = `每 ${minutesHuman(s.interval_minutes)}${jitter > 0 ? ` ±${jitter} 分钟` : ""}`;
  }
  const q = String(s.quiet_hours || "").trim();
  if (s.quiet_enabled && q) base += `；安静时段 ${q.replace("-", "–")} 不画`;
  else if (q) base += "；安静时段已关闭";
  return base;
}

/** "a,b，c;d" → ["a","b","c","d"]（去重、去空白）；array 原样清洗。 */
export function splitNames(raw: any): string[] {
  const items = Array.isArray(raw)
    ? raw.map((x) => String(x))
    : String(raw ?? "").split(/[,，;；\n\r]+/);
  const out: string[] = [];
  const seen = new Set<string>();
  for (const it of items) {
    const s = it.trim();
    if (s && !seen.has(s)) {
      seen.add(s);
      out.push(s);
    }
  }
  return out;
}

/** 解析「类别=权重」文本（与后端 prompter.parse_weights 同语义）。 */
export function parseWeights(raw: any): Record<string, number> {
  const out: Record<string, number> = {};
  const text = typeof raw === "string" ? raw : "";
  for (const part of text.split(/[,，;；\n\r]+/)) {
    if (!part.trim()) continue;
    const m = part.match(/^\s*([^=：:]+)\s*[=：:]\s*(.+?)\s*$/);
    if (!m) continue;
    const w = Number(m[2]);
    if (!Number.isFinite(w)) continue;
    out[m[1].trim()] = Math.min(100, Math.max(0, w));
  }
  return out;
}

/** 各类别在当前权重下的占比（缺省 1；全 0 时按均匀）。 */
export function weightDistribution(
  names: string[],
  weights: Record<string, number>,
): { name: string; pct: number }[] {
  const pairs = names.map((n) => [n, weights[n] === undefined ? 1 : Number(weights[n])] as [string, number]);
  const live = pairs.filter(([, w]) => w > 0);
  const list = live.length ? live : pairs;
  const total = list.reduce((s, [, w]) => s + w, 0) || 1;
  return list
    .map(([name, w]) => ({ name, pct: Math.round((w / total) * 1000) / 10 }))
    .sort((a, b) => b.pct - a.pct);
}

export function copyText(text: string, label: string, done?: (msg: string) => void): void {
  if (!text) return;
  const ok = (m: string) => done?.(m);
  try {
    navigator.clipboard
      .writeText(text)
      .then(() => ok(`已复制${label}`))
      .catch(() => fallback(text, label, ok));
  } catch {
    fallback(text, label, ok);
  }
}

function fallback(text: string, label: string, ok: (m: string) => void): void {
  // 沙箱 iframe 里剪贴板可能被禁：退回 textarea + execCommand
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  try {
    const now = document.execCommand("copy");
    ok(now ? `已复制${label}` : `复制失败，请手动选择${label}文本`);
  } catch {
    ok(`复制失败，请手动选择${label}文本`);
  }
  document.body.removeChild(ta);
}
