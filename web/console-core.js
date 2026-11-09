/* 控制台基础：DOM 工具、API 封装、toast 反馈、tab 切换（T9 拆分）。 */
"use strict";

const $ = (id) => document.getElementById(id);

function escapeHtml(s) {  // innerHTML 拼接 LLM/观众来源文本前必须转义
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function toast(msg, type = "ok") {  // 所有按钮操作的统一反馈：ok=成功 err=失败
  const box = $("toasts");
  const t = document.createElement("div");
  t.className = `toast ${type}`;
  t.textContent = msg;
  box.appendChild(t);
  setTimeout(() => { t.classList.add("out"); setTimeout(() => t.remove(), 350); }, 2600);
}

async function api(path, body) {
  // 有 body → POST（写操作）；无 body → GET（读操作）。无 body 的写操作需显式传 {}。
  try {
    const r = await fetch(`/api${path}`, {
      method: body ? "POST" : "GET",
      ...(body ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}),
    });
    return await r.json();
  } catch (e) {
    return { ok: false, error: `网络错误：${e.message}` };
  }
}

/* ---- tab 切换 ---- */
const TABS = {
  live:        { title: "直播控制", sub: "开写 / 暂停 / 编辑 / 字速" },
  danmaku:     { title: "弹幕接入", sub: "连接直播间 / 演示模式" },
  novel:       { title: "小说管理", sub: "生成新书 / 书籍列表 / 开头" },
  model:       { title: "模型配置", sub: "三档位模型 / 写作节奏 / 测试连接" },
  voice:       { title: "语音配置", sub: "OmniVoice / 主播音色 / 角色绑定" },
  interaction: { title: "互动配置", sub: "闲聊 / 剧情投票 / 礼物事件" },
};
document.querySelectorAll("nav button").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("nav button").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    const tab = btn.dataset.tab;
    $("tab-title").textContent = TABS[tab].title;
    $("tab-sub").textContent = TABS[tab].sub;
    for (const t of Object.keys(TABS)) $(`tab-${t}`).style.display = tab === t ? "" : "none";
    if (tab === "novel") refreshBooks();
    if (tab === "model") loadConfigForm();
    if (tab === "voice") loadVoiceTab();
    if (tab === "interaction") loadInteractionForm();
    location.hash = tab;  // 深链：#voice 等可直接打开对应页
  });
});
// 启动时按 hash 恢复 tab（须等底部各 tab 脚本加载完，故挂 DOMContentLoaded）
document.addEventListener("DOMContentLoaded", () => {
  const initial = location.hash.slice(1);
  if (TABS[initial]) document.querySelector(`nav button[data-tab="${initial}"]`).click();
});
