/* 直播控制 + 人工编辑（T9 拆分自旧 console.html）。 */
"use strict";

$("stage-url").textContent = `${location.origin}/stage`;

/* ---- 小说下拉（开播用；书籍列表刷新时同步） ---- */
async function loadNovels() {
  const d = await api("/novels");
  const sel = $("novel-select");
  const prev = sel.value;
  sel.innerHTML = "";
  for (const n of d.novels || []) {
    if (n.status !== "writing") continue;
    const opt = document.createElement("option");
    opt.value = n.id;
    opt.textContent = `《${n.title}》 ${n.total_words}字/${n.chapters}章`;
    sel.appendChild(opt);
  }
  if (!sel.children.length) sel.innerHTML = '<option value="">暂无进行中的小说（去「小说管理」生成）</option>';
  else if ([...sel.options].some(o => o.value === prev)) sel.value = prev;
  loadEditChapters();
}

/* ---- 开播控制 ---- */
$("btn-start").addEventListener("click", async () => {
  const sel = $("novel-select");
  if (!sel.value) return toast("请选择小说", "err");
  const d = await api(`/live/start/${sel.value}`, {});
  d.ok ? toast(`已开写《${sel.options[sel.selectedIndex].text.split("》")[0].slice(1)}》`)
       : toast(d.error || "开写失败", "err");
  refreshState();
});
$("btn-stop").addEventListener("click", async () => {
  const d = await api("/live/stop", {});
  d.ok ? toast("已停止写作循环") : toast(d.error || "停止失败", "err");
  refreshState();
});
$("btn-pause").addEventListener("click", async () => {
  const id = $("novel-select").value; if (!id) return;
  const d = await api(`/novel/${id}/pause`, {});
  d.ok ? toast("已暂停") : toast(d.error || "暂停失败", "err");
  refreshState();
});
$("btn-resume").addEventListener("click", async () => {
  const id = $("novel-select").value; if (!id) return;
  const d = await api(`/novel/${id}/resume`, {});
  d.ok ? toast("已恢复写作") : toast(d.error || "恢复失败", "err");
  refreshState();
});
$("btn-skip").addEventListener("click", async () => {
  const d = await api("/live/skip", {});
  d.ok ? toast("已跳过当前段落（画面瞬间放完）") : toast(d.error || "跳过失败", "err");
});

/* ---- 字速 ---- */
$("speed").addEventListener("input", async (e) => {
  const ms = parseInt(e.target.value, 10);
  $("speed-label").textContent = `${ms}ms/字`;
  await api("/live/speed", { speed_ms: ms });
});
$("speed").addEventListener("change", (e) => toast(`字速已调整为 ${e.target.value}ms/字`));

/* ---- 状态轮询 ---- */
const STATE_TEXT = { writing: "写作中", gap: "互动间隙", paused: "已暂停",
                     ending: "收官中", finished: "已完结", idle: "空闲" };
async function refreshState() {
  const d = await api("/live/state");
  if (!d.ok) return;
  const el = $("live-status");
  el.textContent = STATE_TEXT[d.state] || d.state;
  el.className = "pill " + (["writing", "gap"].includes(d.state) ? "on" : "");
  $("p-words").textContent = d.total_words || 0;
  $("p-target-words").textContent = d.target_words || "-";
  $("p-chapter").textContent = d.chapter_index || "-";
  $("p-target-chapters").textContent = d.target_chapters || "-";
  const pct = d.target_words ? Math.min(100, (d.total_words || 0) / d.target_words * 100) : 0;
  $("p-bar").style.width = `${pct}%`;
  $("btn-pause").disabled = !["writing", "gap", "ending"].includes(d.state);
  $("btn-resume").disabled = d.state !== "paused";
}
setInterval(refreshState, 2000);

/* ---- 弹幕接入 ---- */
const DM_STATUS = { connected: "已连接", connecting: "连接中…", disconnected: "未连接", error: "连接异常" };
async function refreshDanmaku() {
  const d = await api("/live/danmaku/status");
  const el = $("dm-status");
  el.textContent = (DM_STATUS[d.status] || d.status || "")
    + (d.room ? ` · ${d.room}` : "") + (d.mock ? " · 演示" : "")
    + (d.error ? `（${d.error}）` : "");
  el.title = d.error || "";
  el.className = "pill " + (d.status === "connected" ? "on" : (d.status === "error" ? "accent" : ""));
}
async function dmConnect(body, okMsg) {
  const d = await api("/live/danmaku/connect", body);
  if (d.ok) toast(okMsg);
  else toast(d.detail || d.error || "连接失败", "err");
  refreshDanmaku();
  setTimeout(refreshDanmaku, 3000);
}
$("btn-dm-connect").addEventListener("click", () => {
  if (!$("dm-room").value.trim()) return toast("请输入房间号或直播间链接", "err");
  dmConnect({ room: $("dm-room").value.trim() }, "连接请求已发送，正在连接直播间…");
});
$("btn-dm-disconnect").addEventListener("click", async () => {
  const d = await api("/live/danmaku/disconnect", {});
  d.ok ? toast("已断开弹幕连接") : toast(d.detail || d.error || "断开失败", "err");
  refreshDanmaku();
});
$("btn-dm-mock").addEventListener("click", () => dmConnect({ mock: true }, "演示模式已开启，弹幕正在推流…"));
refreshDanmaku();

/* ---- 人工编辑 ---- */
let editNovelId = "";
async function loadEditChapters() {
  const id = $("novel-select").value;
  if (!id) return;
  editNovelId = id;
  const d = await api(`/novel/${id}`);
  const sel = $("edit-chapter");
  sel.innerHTML = '<option value="">选择章节…</option>';
  for (const c of d.novel.chapters || []) {
    const opt = document.createElement("option");
    opt.value = c.index;
    opt.textContent = `第${c.index}章 ${c.title || ""}（${c.paragraphs.length}段）`;
    sel.appendChild(opt);
  }
}
$("edit-chapter").addEventListener("change", async () => {
  const sel = $("edit-para");
  sel.innerHTML = '<option value="">选择段落…</option>';
  if (!editNovelId || !$("edit-chapter").value) return;
  const d = await api(`/novel/${editNovelId}`);
  const ch = (d.novel.chapters || []).find(c => c.index === parseInt($("edit-chapter").value, 10));
  (ch ? ch.paragraphs : []).forEach((p) => {
    const opt = document.createElement("option");
    opt.value = p.index;
    opt.textContent = `第${p.index}段：${p.text.slice(0, 18)}…${p.edited ? "（已编辑）" : ""}`;
    sel.appendChild(opt);
  });
});
$("btn-load-para").addEventListener("click", async () => {
  if (!editNovelId || !$("edit-chapter").value || !$("edit-para").value)
    return toast("请先选择章节和段落", "err");
  const d = await api(`/novel/${editNovelId}`);
  const ch = (d.novel.chapters || []).find(c => c.index === parseInt($("edit-chapter").value, 10));
  const para = ch && ch.paragraphs.find(p => p.index === parseInt($("edit-para").value, 10));
  if (para) { $("edit-text").value = para.text; toast(`已载入第 ${para.index} 段`); }
  else toast("未找到该段落", "err");
});
$("btn-save-edit").addEventListener("click", async () => {
  if (!editNovelId) return toast("请先选择小说", "err");
  if (!$("edit-text").value.trim()) return toast("内容为空", "err");
  $("edit-msg").textContent = "暂停中…";
  await api(`/novel/${editNovelId}/pause`, {});
  const d = await api(`/novel/${editNovelId}/edit`, {
    chapter_index: parseInt($("edit-chapter").value, 10),
    para_index: parseInt($("edit-para").value, 10),
    text: $("edit-text").value,
  });
  $("edit-msg").textContent = d.ok ? "已保存，写作已恢复" : `保存失败：${d.error}`;
  d.ok ? toast("编辑已保存，写作已恢复") : toast(d.error || "保存失败", "err");
  refreshState();
});

/* ---- 启动 ---- */
loadNovels().then(refreshState);
