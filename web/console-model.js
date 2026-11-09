/* 模型配置（T9）：三档位模型表单、写作节奏、保存（热应用）、测试连接。 */
"use strict";

let currentConfig = null;   // GET /api/config 返回的（api_key 已脱敏为 bool）
let builtinPresets = [];

const PROFILE_LABELS = { writing: "写作模型", chat: "闲聊模型", review: "审核模型" };

async function loadConfigForm() {
  const d = await api("/config");
  if (!d.ok) return toast(d.error || "配置读取失败", "err");
  currentConfig = d.config;
  builtinPresets = d.builtin_presets || [];
  renderProfiles();
  const w = currentConfig.writing;
  $("w-para-min").value = w.para_min;
  $("w-para-max").value = w.para_max;
  $("w-chapter-max").value = w.chapter_max_words;
  $("w-gap").value = w.gap_seconds;
  $("w-target-words").value = w.target_words;
  $("w-target-chapters").value = w.target_chapters;
}

function renderProfiles() {
  const box = $("model-profiles");
  box.innerHTML = "";
  const presetNames = [...new Set([...builtinPresets, ...Object.keys(currentConfig.llm.presets)])];
  for (const name of ["writing", "chat", "review"]) {
    const p = currentConfig.llm[name];
    const wrap = document.createElement("div");
    wrap.className = "field";
    wrap.style.border = "1px solid var(--border)";
    wrap.style.borderRadius = "8px";
    wrap.style.padding = "12px 14px";
    wrap.innerHTML = `
      <label style="font-family:var(--serif);font-size:14px;color:var(--ink)">${PROFILE_LABELS[name]}</label>
      <div class="row" style="margin:8px 0 0">
        <select data-p="${name}" style="flex:1">
          ${presetNames.map(n => `<option ${n === p.preset ? "selected" : ""}>${escapeHtml(n)}</option>`).join("")}
        </select>
      </div>
      <div class="row" style="margin:8px 0 0">
        <input type="text" data-m="${name}" placeholder="模型名" value="${escapeHtml(p.model)}" style="flex:1">
      </div>
      <div class="row" style="margin:8px 0 0">
        <input type="text" data-k="${name}" placeholder="${p.api_key ? "key 已配置" : "API Key（可空）"}" style="flex:1">
      </div>
      <div class="row" style="margin:8px 0 0">
        <span class="hint">温度</span><input type="number" step="0.1" data-t="${name}" value="${p.temperature}" style="width:70px">
        <span class="hint">max_tokens</span><input type="number" data-x="${name}" value="${p.max_tokens}" style="width:80px">
      </div>
      <p class="hint" style="margin-top:6px">${name === "review" ? "留空预设 = 回退写作模型" : ""}</p>`;
    box.appendChild(wrap);
  }
}

$("btn-save-config").addEventListener("click", async () => {
  if (!currentConfig) return;
  // 档位字段回填；api_key 输入框为空 = 未修改（保持脱敏 bool 原样回传）
  for (const name of ["writing", "chat", "review"]) {
    const p = currentConfig.llm[name];
    p.preset = document.querySelector(`[data-p="${name}"]`).value;
    p.model = document.querySelector(`[data-m="${name}"]`).value.trim();
    const keyInput = document.querySelector(`[data-k="${name}"]`).value.trim();
    if (keyInput) p.api_key = keyInput;
    p.temperature = parseFloat(document.querySelector(`[data-t="${name}"]`).value) || 0.8;
    p.max_tokens = parseInt(document.querySelector(`[data-x="${name}"]`).value, 10) || 2000;
  }
  const w = currentConfig.writing;
  w.para_min = parseInt($("w-para-min").value, 10) || w.para_min;
  w.para_max = parseInt($("w-para-max").value, 10) || w.para_max;
  w.chapter_max_words = parseInt($("w-chapter-max").value, 10) || w.chapter_max_words;
  w.gap_seconds = parseInt($("w-gap").value, 10) || w.gap_seconds;
  w.target_words = parseInt($("w-target-words").value, 10) || w.target_words;
  w.target_chapters = parseInt($("w-target-chapters").value, 10) || w.target_chapters;

  const d = await api("/config", { config: currentConfig });
  if (d.ok) {
    toast("配置已保存并生效" + (d.notes && d.notes.length ? `（${d.notes.join("；")}）` : ""));
    loadConfigForm();
  } else {
    toast(d.error || "保存失败", "err");
  }
});

/* ---- 测试连接 ---- */
$("btn-llm-test").addEventListener("click", async () => {
  const out = $("llm-test-out");
  out.classList.add("show");
  out.textContent = "请求中…（流式）";
  const d = await api("/llm/test", {
    provider: $("llm-test-provider").value,
    prompt: $("llm-test-prompt").value.trim() || "你好",
  });
  out.textContent = d.ok ? d.text : `失败：${d.error}`;
});
