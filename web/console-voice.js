/* 语音配置（T9）：OmniVoice 开关/地址、主播音色+试听、角色-音色绑定。 */
"use strict";

async function loadVoiceTab() {
  const [cfgD, vD] = await Promise.all([api("/config"), api("/voices")]);
  if (!cfgD.ok) return toast(cfgD.error || "配置读取失败", "err");
  const tts = cfgD.config.tts;
  $("tts-enabled").value = String(tts.enabled);
  $("tts-url").value = tts.url;
  const sel = $("tts-voice");
  sel.innerHTML = "";
  for (const v of vD.voices || []) {
    const opt = document.createElement("option");
    opt.value = v.name;
    opt.textContent = `${v.label}（${v.name}·${v.kind === "design" ? "设计" : "克隆"}）`;
    sel.appendChild(opt);
  }
  if (vD.voices && vD.voices.some(v => v.name === tts.voice)) sel.value = tts.voice;
  const st = $("tts-state");
  if (vD.scheduler) {
    st.textContent = `运行中 · 待播${vD.scheduler.pending} · 丢${vD.scheduler.dropped}·败${vD.scheduler.failed}`;
    st.className = "pill on";
  } else {
    st.textContent = vD.enabled ? "异常（调度器缺失）" : "未开启";
    st.className = "pill " + (vD.enabled ? "accent" : "warn");
  }
  loadBindNovels();
}

$("btn-voice-preview").addEventListener("click", async () => {
  const voice = $("tts-voice").value;
  if (!voice) return toast("请先选择音色", "err");
  const d = await api("/voice/preview", { voice });
  d.ok ? toast(`试听已入队（${voice}），请听直播页声音`) : toast(d.error || "试听失败", "err");
});

/* 语音基础设置随「保存全部配置」一起提交：此处监听 model tab 的保存按钮之外，
   语音 tab 自带保存动作 —— 复用同一次 /api/config 提交。 */
async function saveVoiceConfig() {
  const cfgD = await api("/config");
  if (!cfgD.ok) return toast(cfgD.error || "配置读取失败", "err");
  const cfg = cfgD.config;
  cfg.tts.enabled = $("tts-enabled").value === "true";
  cfg.tts.url = $("tts-url").value.trim() || "http://127.0.0.1:9877";
  cfg.tts.voice = $("tts-voice").value;
  const d = await api("/config", { config: cfg });
  if (d.ok) {
    toast("语音配置已保存并生效" + (d.notes && d.notes.length ? `（${d.notes.join("；")}）` : ""));
    loadVoiceTab();
  } else {
    toast(d.error || "保存失败", "err");
  }
}
$("tts-enabled").addEventListener("change", saveVoiceConfig);
$("tts-voice").addEventListener("change", saveVoiceConfig);
$("tts-url").addEventListener("change", saveVoiceConfig);

/* ---- 角色-音色绑定 ---- */
async function loadBindNovels() {
  const d = await api("/novels");
  const sel = $("bind-novel");
  const prev = sel.value;
  sel.innerHTML = '<option value="">选择书籍…</option>';
  for (const n of d.novels || []) {
    const opt = document.createElement("option");
    opt.value = n.id;
    opt.textContent = `《${n.title}》`;
    sel.appendChild(opt);
  }
  if ([...sel.options].some(o => o.value === prev)) sel.value = prev;
  else loadBindTable();
}

$("bind-novel").addEventListener("change", loadBindTable);

async function loadBindTable() {
  const tbody = $("bind-table").querySelector("tbody");
  tbody.innerHTML = "";
  const id = $("bind-novel").value;
  if (!id) {
    tbody.innerHTML = '<tr><td colspan="3" style="text-align:center;color:var(--ink-light)">选择书籍后显示角色</td></tr>';
    return;
  }
  const [nD, vD] = await Promise.all([api(`/novel/${id}`), api("/voices")]);
  const voices = vD.voices || [];
  for (const c of (nD.novel && nD.novel.characters) || []) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td style="font-family:var(--serif)">${escapeHtml(c.name)}</td>
      <td class="hint">${escapeHtml((c.persona || "").slice(0, 30))}${(c.persona || "").length > 30 ? "…" : ""}</td>
      <td></td>`;
    const sel = document.createElement("select");
    sel.dataset.name = c.name;
    sel.innerHTML = '<option value="">（未绑定）</option>'
      + voices.map(v => `<option value="${escapeHtml(v.name)}" ${v.name === c.voice ? "selected" : ""}>${escapeHtml(v.label)}</option>`).join("");
    tr.children[2].appendChild(sel);
    tbody.appendChild(tr);
  }
  if (!tbody.children.length) {
    tbody.innerHTML = '<tr><td colspan="3" style="text-align:center;color:var(--ink-light)">该书暂无角色</td></tr>';
  }
}

$("btn-save-bind").addEventListener("click", async () => {
  const id = $("bind-novel").value;
  if (!id) return toast("请先选择书籍", "err");
  const bindings = [...$("bind-table").querySelectorAll("select[data-name]")]
    .map(s => ({ name: s.dataset.name, voice: s.value }));
  const d = await api(`/novel/${id}/voices`, { bindings });
  d.ok ? toast(`已保存 ${d.bound} 个角色的音色绑定`) : toast(d.error || "保存失败", "err");
});
