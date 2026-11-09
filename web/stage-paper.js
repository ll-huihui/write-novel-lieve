/* 稿纸渲染：分页、格线、打字机队列、章头、按可视区等比缩放。 */
"use strict";
const LINES_PER_PAGE = 22;
const $st = (id) => document.getElementById(id);
const paper = $st("paper");

/* ---- 稿纸渲染状态（stage-widgets / stage-main 经全局词法作用域共享） ---- */
let pages = [], linesUsed = 0, curLine = null, lineLen = 0;
let curChapter = 0, pendingIndent = false, chapterStartPage = {};
const chapterTitles = {}, chapterHeadingEls = {};  // 章名 → 章节栏按钮 / 稿纸章头
let queue = [], typingTimer = null, speedMs = 100, autoFollow = true;
let lastParaKey = "";  // 当前段落标识（区分段首标记）
let curPageIdx = 1;    // 当前显示的页码（1 起）

function charsPerLine(indent = false) {
  // 稿纸为固定宽度 .page：量「正在打的行」所在页的 padding（px），与缩放/翻页无关
  const page = (curLine && curLine.parentElement) || pages[pages.length - 1];
  if (!page) return 30;
  const cs = getComputedStyle(page);
  const w = page.clientWidth;
  const base = Math.floor((w - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight))
                          / parseFloat(cs.fontSize));
  // 段首缩进占 2 字；再留 1 字余量，防止标点/字体度量差导致溢出折行
  return Math.max(10, base - (indent ? 2 : 0) - 1);
}

function newPage() {
  const page = document.createElement("div");
  page.className = "page";
  const no = document.createElement("span");
  no.className = "page-no";
  page.appendChild(no);
  paper.appendChild(page);
  pages.push(page);
  linesUsed = 0;
  if (autoFollow) showPage(pages.length);  // 直播跟写：自动翻到最新页
  else updatePageLabel();                   // 页数变化也要刷新 x/y
  return page;
}
/* ---- 单页显示与翻页 ---- */
function showPage(idx) {
  curPageIdx = Math.min(Math.max(1, idx), pages.length);
  // 内联 display 必须显式给 block：CSS `.page + .page{display:none}` 会盖掉空串
  pages.forEach((p, i) => { p.style.display = i === curPageIdx - 1 ? "block" : "none"; });
  updatePageLabel();
  // 章节栏高亮当前页所属章节
  let active = 0;
  for (const [ch, start] of Object.entries(chapterStartPage)) {
    if (start <= curPageIdx && +ch > active) active = +ch;
  }
  document.querySelectorAll(".rail-item").forEach(b =>
    b.classList.toggle("active", +b.dataset.ch === active));
}
function updatePageLabel() {
  $st("page-label").textContent = `第 ${curPageIdx} / ${pages.length} 页`;
  // 非跟随时给出显式入口，避免"静默停止自动翻页"
  $st("follow-btn").hidden = autoFollow;
}
$st("follow-btn").addEventListener("click", () => {
  autoFollow = true;
  showPage(pages.length);
});
/* ---- 稿纸自适应：按可视区百分比设定 --sheet-scale，整页真实等比缩放（1.4 封顶） ---- */
function fitSheet() {
  const row = $st("stage-row");
  const rail = $st("chapter-rail");
  const availH = row.clientHeight - $st("book-info").offsetHeight - 30;
  const availW = row.clientWidth - rail.offsetWidth - 24;
  const s = Math.max(0.5, Math.min(availH / 890, availW / 630, 1.4));
  $st("paper-col").style.setProperty("--sheet-scale", s.toFixed(4));
}
window.addEventListener("resize", fitSheet);

function newLine() {
  if (linesUsed >= LINES_PER_PAGE) { newPage(); }
  curLine = document.createElement("div");
  curLine.className = "line" + (pendingIndent ? " indent" : "");
  pendingIndent = false;
  lineLen = 0;
  curPageEl().appendChild(curLine);
  pages[pages.length - 1].querySelector(".page-no").textContent = `— ${pages.length} —`;
  linesUsed++;
}
function curPageEl() { return pages[pages.length - 1] || newPage(); }
function putChar(ch) {
  if (ch === "\n") { newLine(); return; }
  const indent = curLine ? curLine.classList.contains("indent") : false;
  if (!curLine || lineLen >= charsPerLine(indent)) newLine();
  curLine.appendChild(document.createTextNode(ch));
  lineLen++;
}
/* ---- 打字机队列 ---- */
function enqueuePara(text) { queue.push({ para: true }); for (const ch of text) queue.push(ch); queue.push("\n"); }
function beginPara() {  // 段首：必要时换行（空行复用，不产生多余空行），加缩进
  if (!curLine || lineLen > 0) newLine();
  curLine.classList.add("indent");
  pendingIndent = false;
  lineLen = 0;
}
function startTyping() {
  if (typingTimer) return;
  typingTimer = setInterval(() => {
    if (!queue.length) { clearInterval(typingTimer); typingTimer = null; return; }
    const item = queue.shift();
    if (typeof item === "object" && item.heading) { beginChapter(item.heading.idx, item.heading.title); return; }
    if (typeof item === "object" && item.para) { beginPara(); return; }
    putChar(item);
  }, speedMs);
}
function flushQueue() {  // 跳过：瞬间放完
  while (queue.length) {
    const item = queue.shift();
    if (typeof item === "object" && item.heading) { beginChapter(item.heading.idx, item.heading.title); continue; }
    if (typeof item === "object" && item.para) { beginPara(); continue; }
    putChar(item);
  }
  if (autoFollow) showPage(pages.length);
}
/* ---- 章头：另起一页，打出居中章名；标题后到时经 syncRailTitle 回填 ---- */
function beginChapter(idx, title) {
  if (linesUsed > 0) newPage();  // 每章另起一页（sync 首章时稿纸为空，不换页）
  chapterStartPage[idx] = pages.length;  // 章起始页 = 章头所在页（翻页后才记录，跳章才准确）
  curChapter = idx;
  const h = document.createElement("div");
  h.className = "chapter-heading";
  h.textContent = `第${idx}章 ${title}`.trim();
  curPageEl().appendChild(h);
  chapterHeadingEls[idx] = h;
  linesUsed += 2;  // 章头占 2 格行高（与 .chapter-heading min-height 一致）
}
function resetPaper() {
  paper.innerHTML = ""; pages = []; curLine = null; linesUsed = 0;
  curChapter = 0; chapterStartPage = {}; pendingIndent = false; lastParaKey = "";
  for (const k of Object.keys(chapterTitles)) delete chapterTitles[k];
  for (const k of Object.keys(chapterHeadingEls)) delete chapterHeadingEls[k];
  queue = []; $st("rail-list").innerHTML = "";
  newPage();
  showPage(1);
  fitSheet();
}
/* ---- 翻页（操作区按钮；翻回末页时恢复直播跟写） ---- */
$st("prev-page").addEventListener("click", () => {
  showPage(curPageIdx - 1);
  autoFollow = curPageIdx === pages.length;
});
$st("next-page").addEventListener("click", () => {
  showPage(curPageIdx + 1);
  autoFollow = curPageIdx === pages.length;
});
