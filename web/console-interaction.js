/* 互动配置（T8）：闲聊/投票/礼物事件。保存走 /api/config 热应用；投票控制走 /api/vote/*。 */
"use strict";

let interactionConfig = null;  // 本 tab 的配置缓存（保存时回填 interaction 后整体提交）

async function loadInteractionForm() {
  const d = await api("/config");
  if (!d.ok) return toast(d.error || "配置读取失败", "err");
  interactionConfig = d.config;
  const it = interactionConfig.interaction;
  $("i-chat-enabled").value = String(it.chat_enabled);
  $("i-chat-interval").value = it.chat_interval;
  $("i-vote-enabled").value = String(it.vote_enabled);
  $("i-auto-vote").value = String(it.auto_vote);
  $("i-vote-duration").value = it.vote_duration;
  $("i-gift-enabled").value = String(it.gift_events_enabled);
  renderGiftRules();
  refreshVoteState();
}

/* ---- 礼物映射表 ---- */

function renderGiftRules() {
  const tbody = document.querySelector("#gift-table tbody");
  tbody.innerHTML = "";
  const rules = interactionConfig.interaction.gift_rules || {};
  for (const [gift, event] of Object.entries(rules)) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><input type="text" class="g-gift" value="${escapeAttr(gift)}"></td>
      <td><input type="text" class="g-event" value="${escapeAttr(event)}" style="width:95%"></td>
      <td class="actions"><button class="act ghost g-del">删除</button></td>`;
    tr.querySelector(".g-del").addEventListener("click", () => tr.remove());
    tbody.appendChild(tr);
  }
}

function escapeAttr(s) {
  return escapeHtml(s);  // 统一走 console-core 的转义（含 > ，比原实现更严）
}

$("btn-gift-add").addEventListener("click", () => {
  const tbody = document.querySelector("#gift-table tbody");
  const tr = document.createElement("tr");
  tr.innerHTML = `
    <td><input type="text" class="g-gift" placeholder="礼物名，如：火箭"></td>
    <td><input type="text" class="g-event" placeholder="触发的剧情事件文案" style="width:95%"></td>
    <td class="actions"><button class="act ghost g-del">删除</button></td>`;
  tr.querySelector(".g-del").addEventListener("click", () => tr.remove());
  tbody.appendChild(tr);
  tr.querySelector(".g-gift").focus();
});

/* ---- 保存（interaction 段整体回填提交） ---- */

$("btn-save-interaction").addEventListener("click", async () => {
  if (!interactionConfig) return;
  const it = interactionConfig.interaction;
  it.chat_enabled = $("i-chat-enabled").value === "true";
  it.chat_interval = parseInt($("i-chat-interval").value, 10) || it.chat_interval;
  it.vote_enabled = $("i-vote-enabled").value === "true";
  it.auto_vote = $("i-auto-vote").value === "true";
  it.vote_duration = parseInt($("i-vote-duration").value, 10) || it.vote_duration;
  it.gift_events_enabled = $("i-gift-enabled").value === "true";
  it.gift_rules = {};
  for (const tr of document.querySelectorAll("#gift-table tbody tr")) {
    const gift = tr.querySelector(".g-gift").value.trim();
    const event = tr.querySelector(".g-event").value.trim();
    if (gift && event) it.gift_rules[gift] = event;
  }
  const d = await api("/config", { config: interactionConfig });
  d.ok ? toast("互动配置已保存并生效") : toast(d.error || "保存失败", "err");
});

/* ---- 投票控制 ---- */

$("btn-vote-gen").addEventListener("click", async () => {
  const btn = $("btn-vote-gen");
  btn.disabled = true;
  btn.textContent = "出题中…";
  const d = await api("/vote/generate", {});
  btn.disabled = false;
  btn.textContent = "AI 出题";
  if (!d.ok) return toast(d.error || "出题失败", "err");
  $("v-question").value = d.question;
  const opts = d.options || [];
  $("v-opt1").value = opts[0] || "";
  $("v-opt2").value = opts[1] || "";
  $("v-opt3").value = opts[2] || "";
  toast("AI 已按当前剧情出题，可修改后发起");
});

$("btn-vote-start").addEventListener("click", async () => {
  const options = [$("v-opt1").value, $("v-opt2").value, $("v-opt3").value]
    .map(s => s.trim()).filter(Boolean);
  const d = await api("/vote/start", {
    question: $("v-question").value.trim(),
    options,
  });
  if (d.ok) {
    toast("投票已发起，直播页已显示卡片");
    refreshVoteState();
  } else {
    toast(d.error || "发起失败", "err");
  }
});

$("btn-vote-close").addEventListener("click", async () => {
  const d = await api("/vote/close", {});
  d.ok ? toast("投票已结束，结果进入观众素材") : toast(d.error || "结束失败", "err");
  refreshVoteState();
});

async function refreshVoteState() {
  const d = await api("/vote/state");
  const pill = $("vote-status");
  const pool = $("pool-hint");
  if (!d.ok) { pill.textContent = "-"; return; }
  const v = d.vote || {};
  if (v.open) {
    pill.textContent = `投票中：${v.question}`;
  } else if (v.question) {
    pill.textContent = "最近投票已结束";
  } else {
    pill.textContent = "无进行中的投票";
  }
  pool.textContent = d.pool ? `素材池：${d.pool.split("\n").length} 条观众素材待注入` : "素材池为空";
}
