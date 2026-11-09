/* WS 消息路由与重连、操作区控制、选书/生成新书面板、语音播放、页面启动。 */
"use strict";

function handleMessage(msg) {
  switch (msg.type) {
    case "sync": {
      resetPaper();
      setState(msg);
      for (const ch of msg.chapters || []) {
        ensureChapter(ch.index, ch.title);
        queue.push({ heading: { idx: ch.index, title: ch.title || "" } });
        for (const p of ch.paragraphs) { enqueuePara(p.text); }
        flushQueue();
      }
      for (const b of msg.chat_bubbles || []) addBubble(b);
      renderVote(msg.vote);  // 重连恢复进行中的投票卡片
      autoFollow = true;
      showPage(pages.length);
      break;
    }
    case "vote_open": renderVote({ ...msg, open: true }); break;
    case "vote_update": updateVoteTally(msg); break;
    case "vote_close": renderVote({ ...msg, open: false, result: msg.result }); break;
    case "gift_event": addGiftEvent(msg); break;
    case "chat_bubble": addBubble(msg); break;
    case "voice": pushVoice(msg); break;
    case "novel_delta": {
      if (msg.chapter_index !== curChapter) {
        curChapter = msg.chapter_index;
        ensureChapter(curChapter, "");
        queue.push({ heading: { idx: curChapter, title: chapterTitles[curChapter] || "" } });
      }
      const paraKey = `${msg.chapter_index}-${msg.para_index}`;
      if (msg.text && paraKey !== lastParaKey) {
        lastParaKey = paraKey;
        queue.push({ para: true });  // 段首标记（分段缩进），后续 chunk 只补字符
      }
      if (msg.text) for (const ch of msg.text) queue.push(ch);
      if (msg.done) { queue.push("\n"); startTyping(); }
      break;
    }
    case "state": setState(msg); break;
    case "skip": flushQueue(); break;
    case "danmaku": addDanmaku(msg); break;
    case "danmaku_status": setDanmakuStatus(msg); break;
    case "chapter_end":
      if (msg.new_chapter_index) ensureChapter(msg.new_chapter_index, msg.next_chapter_title || "");
      break;
  }
}
/* ---- WS 连接与断线重连 ---- */
let ws = null;
function connect() {
  ws = new WebSocket(`ws://${location.host}/api/ws/stage`);
  ws.onmessage = (e) => { try { handleMessage(JSON.parse(e.data)); } catch {} };
  ws.onclose = () => setTimeout(connect, 1000);
  ws.onerror = () => ws.close();
}
/* ---- 操作区请求封装与按钮 ---- */
async function post(path, body) {
  try {
    const r = await fetch(path, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    return await r.json();
  } catch { return { ok: false }; }
}
async function getJSON(path) {
  try { const r = await fetch(path); return await r.json(); } catch { return { ok: false }; }
}
$st("dm-connect").addEventListener("click", async () => {
  const room = $st("dm-room").value.trim();
  if (!room) { $st("dm-room").focus(); return; }
  await post("/api/live/danmaku/connect", { room });  // 状态经 WS danmaku_status 回显
});
$st("dm-disconnect").addEventListener("click", () => post("/api/live/danmaku/disconnect"));
$st("dm-mock").addEventListener("click", () => post("/api/live/danmaku/connect", { mock: true }));
$st("btn-pause").addEventListener("click", () => post("/api/live/pause"));
$st("btn-resume").addEventListener("click", () => post("/api/live/resume"));
$st("btn-skip").addEventListener("click", () => post("/api/live/skip"));
/* ---- 选书 / 生成新书面板 ---- */
const bookModal = $st("book-modal");
async function openBookModal() {
  bookModal.hidden = false;
  const d = await getJSON("/api/novels");
  const sel = $st("bm-select");
  sel.innerHTML = "";
  for (const n of d.novels || []) {
    if (n.status !== "writing") continue;
    const opt = document.createElement("option");
    opt.value = n.id;
    opt.textContent = `《${n.title}》 ${n.total_words}字/${n.chapters}章`;
    sel.appendChild(opt);
  }
  if (!sel.children.length) sel.innerHTML = '<option value="">暂无进行中的书，请生成新书</option>';
}
$st("btn-book").addEventListener("click", openBookModal);
$st("bm-close").addEventListener("click", () => { bookModal.hidden = true; });
$st("book-mask").addEventListener("click", () => { bookModal.hidden = true; });
$st("bm-start").addEventListener("click", async () => {
  const id = $st("bm-select").value;
  if (!id) return;
  const d = await post(`/api/live/start/${id}`);
  if (d.ok) { bookModal.hidden = true; $st("bm-status").textContent = ""; }
});
$st("bm-gen").addEventListener("click", async () => {
  const btn = $st("bm-gen");
  btn.disabled = true;
  $st("bm-status").textContent = "生成中，约 1-2 分钟…（请勿关闭页面）";
  const d = await post("/api/novel/generate", {
    genre_hint: $st("bm-genre").value.trim(),
    idea: $st("bm-idea").value.trim(),
    target_words: parseInt($st("bm-words").value, 10) || 0,
    target_chapters: parseInt($st("bm-chapters").value, 10) || 0,
  });
  btn.disabled = false;
  if (!d.ok) {
    $st("bm-status").textContent = `生成失败：${d.error || "未知错误"}`;
    return;
  }
  $st("bm-status").textContent = `《${d.novel.title}》已生成，正在开写…`;
  const s = await post(`/api/live/start/${d.novel.id}`);
  $st("bm-status").textContent = s.ok ? "" : `开写失败：${s.error || "未知错误"}`;
  if (s.ok) bookModal.hidden = true;
  $st("bm-genre").value = $st("bm-idea").value = $st("bm-words").value = $st("bm-chapters").value = "";
});
/* ---- 语音播放（T7）：WS voice 消息 → 串行播放队列 ---- */
let audioCtx = null, voiceQueue = [], playingVoice = false, voiceMuted = false;
$st("voice-mute").addEventListener("click", () => {
  voiceMuted = !voiceMuted;
  $st("voice-mute").textContent = voiceMuted ? "🔇" : "🔊";
  if (voiceMuted) voiceQueue = [];  // 静音即清空待播队列
});
function playVoices() {
  if (playingVoice || voiceMuted || !voiceQueue.length) return;
  playingVoice = true;
  const item = voiceQueue.shift();
  try {
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    const bytes = Uint8Array.from(atob(item.audio), c => c.charCodeAt(0));
    audioCtx.decodeAudioData(bytes.buffer, (buf) => {
      const src = audioCtx.createBufferSource();
      src.buffer = buf;
      src.connect(audioCtx.destination);
      src.onended = () => { playingVoice = false; playVoices(); };
      src.start();
    }, () => { playingVoice = false; playVoices(); });
  } catch { playingVoice = false; playVoices(); }
}
function pushVoice(msg) {
  if (voiceMuted) return;
  voiceQueue.push(msg);
  if (voiceQueue.length > 3) voiceQueue.shift();  // 与服务端队列上限一致
  playVoices();
}
/* ---- 启动 ---- */
newPage();
showPage(1);
fitSheet();
connect();
