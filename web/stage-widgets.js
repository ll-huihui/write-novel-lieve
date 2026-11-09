/* 状态栏、章节栏、弹幕侧栏、闲聊气泡、投票卡片、礼物事件（展示层组件）。 */
"use strict";

/* ---- 状态栏 ---- */
const stateBar = $st("state-bar");

function setState(info) {
  const map = { writing: "写作中", gap: `互动中 剩余${info.gap_remaining ?? "-"}s`,
                paused: "已暂停", ending: "收官中", finished: "已完结", idle: "空闲" };
  stateBar.textContent = map[info.state] || info.state;
  if (info.error) stateBar.textContent += ` ⚠ ${info.error}`;
  else if (info.notice) stateBar.textContent += ` ⚠ ${info.notice}`;
  stateBar.className = info.state;
  $st("book-title").textContent = info.title ? `《${info.title}》` : "";
  if (info.chapter_index) {
    $st("chapter-title").textContent = `第${info.chapter_index}章 ${info.chapter_title || ""}`;
  }
  // 目标是「最低字数」：达标只意味着允许收官，不封顶
  $st("p-words").textContent = info.total_words || 0;
  $st("p-target").textContent = info.target_words ? `≥ ${info.target_words}` : "-";
  // 快捷控制按钮随状态启停
  const active = ["writing", "gap", "ending"].includes(info.state);
  $st("btn-pause").disabled = !active;
  $st("btn-skip").disabled = !active;
  $st("btn-resume").disabled = info.state !== "paused";
  fitSheet();
}
/* ---- 章节栏 ---- */
function ensureChapter(idx, title) {
  if (title) chapterTitles[idx] = title;
  // 章节栏按钮去重按 DOM 判断；chapterStartPage 由 beginChapter 在「实际翻到新页后」记录，
  // 不能在这里记（入队时上一章尚未 flush，页码会早一页）
  if (document.querySelector(`.rail-item[data-ch="${idx}"]`)) {
    syncRailTitle(idx, chapterTitles[idx] || "");
    return;
  }
  const btn = document.createElement("button");
  btn.className = "rail-item";
  btn.dataset.ch = idx;
  btn.textContent = `第${idx}章 ${chapterTitles[idx] || ""}`.trim();
  btn.addEventListener("click", () => {
    autoFollow = false;
    showPage(chapterStartPage[idx]);
  });
  $st("rail-list").appendChild(btn);
}
function syncRailTitle(idx, title) {
  const btn = document.querySelector(`.rail-item[data-ch="${idx}"]`);
  if (btn) btn.textContent = `第${idx}章 ${title}`.trim();
  // 稿纸上的章头同步回填（直播时章名可能晚于正文到达）
  const h = chapterHeadingEls[idx];
  if (h) h.textContent = `第${idx}章 ${title}`.trim();
}
/* ---- 弹幕侧栏 ---- */
const dmList = $st("danmaku-list");
const dmStatus = $st("dm-status");
const DM_KEEP = 50;  // 侧栏最多保留条数

function setDanmakuStatus(msg) {
  const map = { connected: "已连接", connecting: "连接中…", disconnected: "未连接", error: "连接异常" };
  let text = map[msg.status] || msg.status || "";
  if (msg.room) text += ` · ${msg.room}`;
  if (msg.error) text += `（${msg.error}）`;
  dmStatus.textContent = text;
  dmStatus.title = msg.error || "";
  dmStatus.className = msg.status === "connected" ? "connected" : (msg.status === "error" ? "error" : "");
}
function addDanmaku(msg) {
  const div = document.createElement("div");
  div.className = "dm" + (msg.kind === "gift" ? " gift" : "");
  const nick = document.createElement("span");
  nick.className = "nick";
  nick.textContent = msg.nick || "";
  div.appendChild(nick);
  div.appendChild(document.createTextNode(msg.content || ""));
  const stick = dmList.scrollHeight - dmList.scrollTop - dmList.clientHeight < 40;
  dmList.appendChild(div);
  while (dmList.children.length > DM_KEEP) dmList.removeChild(dmList.firstChild);
  if (stick) dmList.scrollTop = dmList.scrollHeight;
}
function addBubble(msg) {  // AI 主播闲聊气泡
  const box = $st("chat-bubbles");
  const div = document.createElement("div");
  div.className = "chat-bubble";
  const who = document.createElement("div");
  who.className = "who";
  who.textContent = `AI 主播 回复 ${msg.nick || "观众"}`;
  div.appendChild(who);
  div.appendChild(document.createTextNode(msg.reply || ""));
  box.appendChild(div);
  while (box.children.length > 30) box.removeChild(box.firstChild);
  box.scrollTop = box.scrollHeight;
}
/* ---- 投票卡片（T8） ---- */
const voteCard = $st("vote-card");
let curVote = null;  // 最近一次投票的完整状态（vote_open/close 全量，update 增量）

function renderVote(v) {
  if (!v || !v.question) { voteCard.hidden = true; curVote = null; return; }
  curVote = v;
  voteCard.hidden = false;
  voteCard.classList.toggle("closed", !v.open);
  $st("vote-question").textContent = v.question;
  const tally = v.tally || [];
  const counts = (v.options || []).map((_, i) => tally[i] || 0);
  const max = Math.max(...counts, 0);
  const box = $st("vote-options");
  box.innerHTML = "";
  (v.options || []).forEach((opt, i) => {
    const row = document.createElement("div");
    row.className = "vote-opt" + (counts[i] === max && max > 0 ? " leading" : "");
    const key = document.createElement("span");
    key.className = "vote-key";
    key.textContent = i + 1;
    const text = document.createElement("span");
    text.className = "vote-text";
    text.textContent = opt;
    const count = document.createElement("span");
    count.className = "vote-count";
    count.textContent = `${counts[i]}票`;
    row.append(key, text, count);
    box.appendChild(row);
  });
  $st("vote-timer").textContent = v.open
    ? `弹幕输入 1~${v.options.length} 参与投票`
    : (v.result || "投票已结束");
}
function updateVoteTally(msg) {  // 增量票数：合并进当前投票状态
  if (!curVote || !curVote.open) return;
  renderVote({ ...curVote, tally: msg.tally || curVote.tally });
}
function addGiftEvent(msg) {  // 礼物剧情事件：弹幕区高亮一条
  const div = document.createElement("div");
  div.className = "dm gift";
  const nick = document.createElement("span");
  nick.className = "nick";
  nick.textContent = `${msg.nick || "观众"} 送出 ${msg.gift || "礼物"}×${msg.num || 1}`;
  div.appendChild(nick);
  div.appendChild(document.createTextNode(` ${msg.event || ""}`));
  dmList.appendChild(div);
  while (dmList.children.length > DM_KEEP) dmList.removeChild(dmList.firstChild);
  dmList.scrollTop = dmList.scrollHeight;
}
