/* 小说管理（T9）：新书生成（设定卡）、书籍列表、选为直播书、生成开头、归档。 */
"use strict";

const STATUS_TEXT = { writing: "进行中", finished: "已完结", archived: "已归档" };

async function refreshBooks() {
  const d = await api("/novels");
  const tbody = $("book-table").querySelector("tbody");
  tbody.innerHTML = "";
  for (const n of d.novels || []) {
    const tr = document.createElement("tr");
    const live = $("novel-select").value === n.id;
    tr.innerHTML = `
      <td style="font-family:var(--serif);letter-spacing:1px">《${escapeHtml(n.title)}》${live ? ' <span class="pill on">直播中</span>' : ""}</td>
      <td>${escapeHtml(n.genre) || "-"}</td>
      <td>${n.total_words}字 / ${n.chapters}章</td>
      <td><span class="pill ${n.status === "writing" ? "on" : n.status === "archived" ? "" : "warn"}">${STATUS_TEXT[n.status] || n.status}</span></td>
      <td class="actions"></td>`;
    const actions = tr.querySelector(".actions");
    if (n.status === "writing") {
      actions.appendChild(actionBtn("开写", "primary", () => startLive(n.id, n.title)));
      if (n.chapters === 0) actions.appendChild(actionBtn("生成开头", "", () => openNovel(n.id, n.title)));
      actions.appendChild(actionBtn("归档", "ghost", () => archiveNovel(n.id, n.title)));
    }
    tbody.appendChild(tr);
  }
  if (!tbody.children.length) {
    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--ink-light)">还没有书籍，先在上方生成新书</td></tr>';
  }
}

function actionBtn(text, cls, fn) {
  const b = document.createElement("button");
  b.className = `act ${cls}`;
  b.textContent = text;
  b.addEventListener("click", fn);
  return b;
}

/* ---- 生成新书 ---- */
$("btn-gen-novel").addEventListener("click", async () => {
  const btn = $("btn-gen-novel");
  btn.disabled = true;
  $("ng-status").textContent = "生成中，约 1-2 分钟…（请勿关闭页面）";
  const d = await api("/novel/generate", {
    genre_hint: $("ng-genre").value.trim(),
    idea: $("ng-idea").value.trim(),
    target_words: parseInt($("ng-words").value, 10) || 0,
    target_chapters: parseInt($("ng-chapters").value, 10) || 0,
  });
  btn.disabled = false;
  $("ng-status").textContent = "生成约需 1-2 分钟（含一次自动重试）";
  if (d.ok) {
    toast(`新书《${d.novel.title}》已生成，可直接开写`);
    $("ng-genre").value = $("ng-idea").value = $("ng-words").value = $("ng-chapters").value = "";
    refreshBooks();
    loadNovels();
  } else {
    toast(d.error || "生成失败", "err");
  }
});

async function startLive(id, title) {
  const d = await api(`/live/start/${id}`, {});
  d.ok ? toast(`已开写《${title}》`) : toast(d.error || "开写失败", "err");
  refreshBooks();
  loadNovels();
  refreshState();
}

async function openNovel(id, title) {
  toast(`正在为《${title}》生成开头…`);
  const d = await api(`/novel/${id}/open`, {});
  d.ok ? toast("开头已生成，去「直播控制」点开写") : toast(d.error || "开头生成失败", "err");
  refreshBooks();
}

async function archiveNovel(id, title) {
  if (!confirm(`确定归档《${title}》？归档后只读，不可再续写。`)) return;
  const d = await api(`/novel/${id}/archive`, {});
  d.ok ? toast(`《${title}》已归档${d.note ? `（${d.note}）` : ""}`) : toast(d.error || "归档失败", "err");
  refreshBooks();
  loadNovels();
}
