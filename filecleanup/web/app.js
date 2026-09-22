/* FileCleanup 前端：横向目录树可视化 + 交互
 *
 * 依赖：D3.js 精简包（vendor/d3.slim.min.js，ISC License，见 THIRD-PARTY-NOTICES.md）
 *
 * 布局规则
 *   - 左 → 右 横向展开，深度越大越靠右
 *   - 同一层级严格按体积从大到小、从上到下排列（服务端排序，客户端再保一次序）
 *   - 支干粗细 ∝ √(子项体积 / 父项体积)；可切换"体积比例布局"让横向长度也表达占比
 *
 * 性能策略
 *   - 视口裁剪：只渲染当前滚动/缩放可见范围内的节点与连线，几万行也不卡
 *   - 事件委托：节点事件挂在容器上，避免每次渲染重复绑定
 *   - rAF 合并：滚动 / 缩放 / 连续操作合并成一帧渲染
 *   - 子节点分页：单个目录下条目过多时按页取，末尾给"加载更多"
 */
'use strict';

const $ = (id) => document.getElementById(id);
const PREF_KEY = 'filecleanup.prefs.v1';

const ROW = 26;            // 行高
const LEVEL = 250;         // 层间基准水平距离
const PAD_X = 40;
const PAD_Y = 30;
const LABEL_MAX = 34;
const CHILD_PAGE = 300;    // 与服务端 CHILD_PAGE_SIZE 保持一致
const CULL_MARGIN = 260;   // 视口裁剪的上下留白（布局坐标）

const CAT_COLOR = {
  dir: '#5b9bd5', archive: '#e8a33d', image: '#6ecf6e', video: '#c86ed6',
  audio: '#4ec9a8', doc: '#e0705f', sheet: '#7fbf7f', slide: '#e0954a',
  pdf: '#d95f6a', code: '#58b6d6', data: '#9b8cf0', db: '#8f7fd6',
  app: '#d95f8a', font: '#7f9bb5', disk: '#b58b4c', system: '#8a94a6',
  log: '#a0aab8', temp: '#6b7686', web: '#4fb0c6', model: '#c2a45e',
  other: '#95a0b3', symlink: '#7f9bb5', error: '#e0604f', more: '#5b9bd5',
};

const state = {
  root: null,
  byId: new Map(),
  extTable: {},
  categories: {},
  cart: new Map(),
  selected: null,
  matches: new Set(),
  matchOrder: [],
  matchIdx: 0,
  scanning: false,
  pollTimer: null,
  total: { files: 0, dirs: 0, bytes: 0, nodes: 0 },
  // 视图开关（持久化）。showExt / showZh 互相独立：可只显示扩展名、
  // 只显示中文释义，也可以两者都显示（或都不显示，只留文件名）。
  showExt: true, showZh: false, showSize: false, thick: true, proportional: false,
  minSize: 0, filter: '', theme: 'dark', sideCollapsed: false,
};

// ------------------------------------------------------------------ 工具
function humanSize(b) {
  if (b == null) return '-';
  const u = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  let i = 0, v = b;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return (i === 0 ? v : v.toFixed(v >= 100 ? 0 : v >= 10 ? 1 : 2)) + ' ' + u[i];
}
function fmtTime(ts) {
  if (!ts) return '-';
  const d = new Date(ts * 1000), p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
const clamp01 = (v) => Math.max(0, Math.min(1, v || 0));
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const api = async (url) => await (await fetch(url)).json();

function catOf(n) {
  if (n.isMore) return 'more';
  if (n.kind === 'error') return 'error';
  if (n.kind === 'symlink') return 'symlink';
  if (n.isDir) return 'dir';
  const it = state.extTable[n.ext];
  return (it && it.cat) || 'other';
}
const colorOf = (n) => CAT_COLOR[catOf(n)] || CAT_COLOR.other;
function zhOf(n) {
  const it = state.extTable[n.ext];
  if (it && it.zh) return it.zh;
  return n.ext ? n.ext.replace(/^\./, '') + ' 文件' : '无扩展名';
}
function baseName(n) {
  return (n.ext && n.name.toLowerCase().endsWith(n.ext)) ? n.name.slice(0, -n.ext.length) : n.name;
}
/** 标签正文：两个开关都关时保留完整文件名，避免信息丢失。 */
function labelMain(n) {
  if (n.isMore) return n.name;
  const plain = !n.isDir && !state.showExt && !state.showZh;
  let s = plain ? n.name : (n.isDir ? n.name : baseName(n));
  if (s.length > LABEL_MAX) s = s.slice(0, LABEL_MAX - 1) + '…';
  return (n._deleted ? '✖ ' : '') + s;
}
/** 标签后缀：扩展名与中文释义各自独立，可只显示其一，也可同时显示。 */
function labelSub(n) {
  if (n.isMore) return '';
  const parts = [];
  if (n.kind === 'error') parts.push('读取失败');
  else if (n.kind === 'symlink') parts.push('链接(未跟随)');
  else if (n.isDir) { if (n.childCount) parts.push(`${n.childCount} 项`); }
  else {
    if (state.showExt && n.ext) parts.push(n.ext);
    if (state.showZh) parts.push(zhOf(n));
  }
  if (state.showSize) parts.push(humanSize(n.size));
  return parts.filter(Boolean).join(' · ');
}

// ------------------------------------------------------------------ 偏好持久化
function loadPrefs() {
  try {
    const p = JSON.parse(localStorage.getItem(PREF_KEY) || '{}');
    Object.assign(state, p, { cart: state.cart });
    // 兼容旧存档：以前只有 showZh 一个开关
    if (p && typeof p.showZh === 'boolean' && p.showExt === undefined) {
      state.showExt = !p.showZh;
    }
    if (typeof state.showExt !== 'boolean') state.showExt = true;
  } catch (e) { /* 首次运行没有存档，忽略 */ }
}
function savePrefs() {
  try {
    localStorage.setItem(PREF_KEY, JSON.stringify({
      showExt: state.showExt, showZh: state.showZh, showSize: state.showSize,
      thick: state.thick, proportional: state.proportional, minSize: state.minSize,
      theme: state.theme, sideCollapsed: state.sideCollapsed,
    }));
  } catch (e) { /* 隐私模式下写不了，忽略 */ }
}
function applyPrefsToUI() {
  $('chkShowExt').checked = state.showExt;
  $('chkExtZh').checked = state.showZh;
  $('chkShowSize').checked = state.showSize;
  $('chkThick').checked = state.thick;
  $('chkProportional').checked = state.proportional;
  $('minSize').value = String(state.minSize);
  document.documentElement.dataset.theme = state.theme;
  $('btnTheme').textContent = state.theme === 'dark' ? '🌙' : '☀️';
  document.body.classList.toggle('side-collapsed', state.sideCollapsed);
  $('btnSideToggle').textContent = state.sideCollapsed ? '⟨' : '⟩';
}

// ------------------------------------------------------------------ 初始化
async function init() {
  $('verText').textContent = 'v1.0.0';
  loadPrefs();
  applyPrefsToUI();
  bindEvents();
  initSvg();

  try {
    const r = await api('/api/extmap');
    state.extTable = r.table || {};
    state.categories = r.categories || {};
    delete state.extTable['README'];
    buildLegend();
  } catch (e) { /* 映射表失败不影响主流程 */ }

  try {
    const r = await api('/api/roots');
    (r.roots || []).forEach((it) => {
      const o = document.createElement('option');
      o.value = it.path; o.textContent = `${it.label}  (${it.path})`;
      $('rootSelect').appendChild(o);
    });
  } catch (e) { /* ignore */ }

  // 刷新页面后接回服务端已有的扫描结果 / 正在进行的扫描
  try {
    const p = await api('/api/progress');
    if (p && p.rootPath) {
      $('pathInput').value = p.rootPath;
      if (p.finished && !p.error) { await loadTree(); onScanDone(p); }
      else if (p.running) { markScanning(true); state.pollTimer = setInterval(poll, 500); }
    }
  } catch (e) { /* ignore */ }
}

function buildLegend() {
  const cats = ['dir', 'archive', 'video', 'image', 'doc', 'code', 'data', 'app', 'other'];
  const label = (c) => (state.categories && state.categories[c]) || c;
  $('legend').innerHTML = cats
    .map((c) => `<span><i style="background:${CAT_COLOR[c]}"></i>${label(c)}</span>`).join('');
}

function bindEvents() {
  $('btnScan').onclick = startScan;
  $('btnCancel').onclick = async () => { await fetch('/api/cancel', { method: 'POST' }); };
  $('rootSelect').onchange = (e) => { if (e.target.value) $('pathInput').value = e.target.value; };

  $('chkShowExt').onchange = (e) => { state.showExt = e.target.checked; savePrefs(); scheduleRender(); refreshDetail(); };
  $('chkExtZh').onchange = (e) => { state.showZh = e.target.checked; savePrefs(); scheduleRender(); refreshDetail(); };
  $('chkShowSize').onchange = (e) => { state.showSize = e.target.checked; savePrefs(); scheduleRender(); };
  $('chkThick').onchange = (e) => { state.thick = e.target.checked; savePrefs(); scheduleRender(); };
  $('chkProportional').onchange = (e) => { state.proportional = e.target.checked; savePrefs(); scheduleRender(); };
  $('minSize').onchange = (e) => { state.minSize = +e.target.value; savePrefs(); scheduleRender(); };

  $('filterInput').oninput = (e) => { state.filter = e.target.value.trim(); applyFilter(); };
  $('btnFind').onclick = findNext;
  $('btnCollapse').onclick = () => { collapseAll(state.root); scheduleRender(); fit(); };
  $('btnFit').onclick = fit; $('btnFit2').onclick = fit;
  $('btnZoomIn').onclick = () => svg.call(zoom.scaleBy, 1.25);
  $('btnZoomOut').onclick = () => svg.call(zoom.scaleBy, 0.8);

  $('btnTheme').onclick = () => {
    state.theme = state.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = state.theme;
    $('btnTheme').textContent = state.theme === 'dark' ? '🌙' : '☀️';
    savePrefs(); scheduleRender();     // 目录连线的颜色由主题决定，需重绘
  };
  $('btnHelp').onclick = showHelp;
  $('btnQuit').onclick = confirmQuit;
  $('btnSideToggle').onclick = () => setSideCollapsed(!state.sideCollapsed);
  $('btnSideOpen').onclick = () => setSideCollapsed(false);

  document.querySelectorAll('.tab').forEach((t) => {
    t.onclick = () => {
      document.querySelectorAll('.tab').forEach((x) => x.classList.remove('active'));
      t.classList.add('active');
      document.querySelectorAll('.tabpane').forEach((p) => p.classList.remove('active'));
      $('pane' + t.dataset.tab.charAt(0).toUpperCase() + t.dataset.tab.slice(1)).classList.add('active');
      if (t.dataset.tab === 'top') loadTop();
    };
  });

  $('btnTopRefresh').onclick = () => { state.topCache = null; loadTop(true); };
  $('btnClearCart').onclick = () => { state.cart.clear(); renderCart(); scheduleRender(); };
  $('btnDelete').onclick = openDeleteModal;
  $('modalCancel').onclick = closeModal;
  $('modalOk').onclick = modalOkClicked;

  // 右键菜单
  document.querySelectorAll('#ctxmenu .ctx-item').forEach((it) => {
    it.addEventListener('click', async () => {
      const n = ctxNode; hideCtx();
      if (!n) return;
      const act = it.dataset.act;
      if (act === 'open') openInExplorer(n.path);
      else if (act === 'cart') addToCart(n);
      else if (act === 'copy') navigator.clipboard && navigator.clipboard.writeText(n.path);
      else if (act === 'expand') { await expandAll(n); scheduleRender(); }
      else if (act === 'collapse') { collapseAll(n); scheduleRender(); }
    });
  });

  // 画布滚动 -> 视口裁剪重绘（rAF 合并）
  $('canvas').addEventListener('scroll', () => scheduleRender(), { passive: true });
  window.addEventListener('resize', () => scheduleRender());

  document.addEventListener('keydown', onKey);
  document.addEventListener('click', () => hideCtx());
}

function onKey(e) {
  const tag = (e.target.tagName || '').toLowerCase();
  const typing = tag === 'input' || tag === 'select' || tag === 'textarea';
  if (e.key === 'Escape') { closeModal(); hideCtx(); return; }
  if (e.key === '/' && !typing) { e.preventDefault(); $('filterInput').focus(); return; }
  if (e.key === 'Enter' && e.target.id === 'pathInput') { startScan(); return; }
  if (typing) return;
  if (e.key === 'f') fit();
  if (e.key === 'c') $('btnCollapse').click();
}

/** 收起 / 展开侧栏。展开按钮在画布上，收起按钮在侧栏里，两边状态始终同步。 */
function setSideCollapsed(collapsed) {
  state.sideCollapsed = collapsed;
  document.body.classList.toggle('side-collapsed', collapsed);
  $('btnSideToggle').textContent = collapsed ? '⟨' : '⟩';
  savePrefs();
  scheduleRender();
}

// ------------------------------------------------------------------ 扫描
async function startScan() {
  const p = $('pathInput').value.trim();
  if (!p) { setProgress('请先填写要扫描的目录路径'); return; }
  state.root = null; state.byId.clear(); state.cart.clear();
  state.selected = null; state.matches.clear(); state.matchOrder = [];
  state.topCache = null;
  renderCart(); refreshDetail(); $('empty').classList.add('hidden');

  const r = await fetch('/api/scan', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path: p }),
  }).then((x) => x.json());
  if (!r.ok) { setProgress('扫描失败：' + (r.error || '未知错误')); return; }

  markScanning(true);
  if (state.pollTimer) clearInterval(state.pollTimer);
  state.pollTimer = setInterval(poll, 500);
  poll();
}

function markScanning(on) {
  state.scanning = on;
  $('btnScan').disabled = on;
  $('btnCancel').disabled = !on;
}

async function poll() {
  const p = await api('/api/progress');
  if (!p) return;
  if (p.error) { setProgress('扫描出错：' + p.error); stopPolling(); return; }

  const pct = Math.min(96, Math.log10(1 + (p.bytes || 0) / 1048576) * 12 + 6);
  $('barFill').style.width = (p.finished ? 100 : pct) + '%';

  if (p.running) {
    setProgress(`扫描中… 已发现 ${p.files.toLocaleString()} 个文件 / ${p.dirs.toLocaleString()} 个目录 · ${humanSize(p.bytes)}` +
      (p.currentPath ? ` · ${p.currentPath}` : '') + (p.truncated ? ' · 已达节点上限' : ''));
  } else if (p.finished) {
    stopPolling();
    await loadTree();
    onScanDone(p);
  }
}

function onScanDone(p) {
  state.total = { files: p.files, dirs: p.dirs, bytes: p.bytes, nodes: p.nodes };
  setProgress(`扫描完成：${p.files.toLocaleString()} 个文件 / ${p.dirs.toLocaleString()} 个目录 · 合计 ${humanSize(p.bytes)} · 用时 ${p.elapsed}s` +
    (p.truncated ? ' · ⚠ 达到节点上限，结果已截断' : ''));
  updateStatus();
  if (document.querySelector('.tab.active').dataset.tab === 'top') loadTop();
}

function stopPolling() {
  if (state.pollTimer) clearInterval(state.pollTimer);
  state.pollTimer = null;
  markScanning(false);
}

const setProgress = (t) => { $('progText').textContent = t; };
function updateStatus() {
  const t = state.total;
  $('statLeft').textContent = state.root
    ? `${t.files.toLocaleString()} 文件 · ${t.dirs.toLocaleString()} 目录 · ${humanSize(t.bytes)} · ${t.nodes.toLocaleString()} 节点`
    : '就绪';
}

async function loadTree() {
  const r = await api('/api/tree?depth=2');
  if (!r.ok) { setProgress('载入目录树失败：' + (r.error || '')); return; }
  state.root = adapt(r.root);
  register(state.root);
  state.root.expanded = true;
  state.root.children.forEach((c) => { c.expanded = false; });
  if (r.total) state.total = { files: r.total.files, dirs: r.total.dirs, bytes: r.total.size, nodes: r.total.nodes };
  $('empty').classList.add('hidden');
  scheduleRender();
  requestAnimationFrame(() => requestAnimationFrame(fit));
}

function adapt(d) {
  const n = {
    id: d.id, name: d.name, path: d.path, isDir: d.isDir, kind: d.kind,
    size: d.size, ownSize: d.ownSize, mtime: d.mtime, ext: d.ext || '',
    error: d.error, childCount: d.childCount,
    children: d.children ? d.children.map(adapt) : null,
    expanded: false, _parent: null, _deleted: false, _hasMore: false, _nextOffset: 0,
  };
  if (n.children) {
    n.children.forEach((c) => { c._parent = n; });
    finalizeChildren(n);      // 服务端分页过：按 childCount 补出"还有 N 项"
  }
  return n;
}

/** 根据已加载的子项数量决定是否追加"加载更多"节点。 */
function finalizeChildren(n) {
  const real = (n.children || []).filter((c) => !c.isMore);
  n.children = real;
  n._nextOffset = real.length;
  n._hasMore = n.childCount > real.length;
  if (n._hasMore) n.children.push(makeMoreNode(n));
}
function register(n) {
  if (n.isMore) return;
  state.byId.set(n.id, n);
  if (n.children) n.children.forEach(register);
}

async function ensureChildren(n, offset = 0) {
  if (n.children && offset === 0) return n.children;
  const r = await api(`/api/children?id=${n.id}&offset=${offset}&limit=${CHILD_PAGE}`);
  const kids = (r.children || []).map(adapt);
  kids.forEach((c) => { c._parent = n; state.byId.set(c.id, c); });
  n.children = offset === 0 ? kids : (n.children || []).filter((c) => !c.isMore).concat(kids);
  finalizeChildren(n);
  return n.children;
}

function makeMoreNode(parent) {
  const remain = Math.max(0, parent.childCount - (parent._nextOffset || 0));
  return {
    id: 'more:' + parent.id, name: `还有 ${remain.toLocaleString()} 项…`,
    isMore: true, isDir: false, kind: 'more', size: 0, ownSize: 0,
    mtime: 0, ext: '', error: null, childCount: 0, children: [],
    expanded: false, _parent: parent, _deleted: false,
  };
}

function collapseAll(n) {
  if (!n) return;
  n.expanded = false;
  if (n.children) n.children.forEach(collapseAll);
}
async function expandAll(n, depth = 0) {
  if (!n.isDir || !n.childCount || depth > 8) return;
  await ensureChildren(n);
  n.expanded = true;
  for (const c of n.children.slice(0, 40)) if (c.isDir) await expandAll(c, depth + 1);
}

// ------------------------------------------------------------------ 布局
function layout() {
  const nodes = [], links = [];
  let row = 0;
  const rootSize = (state.root && state.root.size) || 1;

  (function walk(n, depth, x, parentSize) {
    n._depth = depth;
    const share = clamp01(parentSize ? n.size / parentSize : 1);
    n._x = state.proportional ? x + Math.max(46, share * LEVEL * 1.7) : x + LEVEL;
    n._r = Math.max(2.5, Math.min(13, 2.5 + 10 * Math.sqrt(clamp01(n.size / rootSize))));
    nodes.push(n);

    const kids = (n.expanded && n.children)
      ? n.children.filter((c) => !c.isMore && c.size >= state.minSize) : [];
    if (kids.length || (n.expanded && n.children && n.children.some((c) => c.isMore))) {
      const all = n.children.filter((c) => c.isMore || c.size >= state.minSize);
      const ys = all.map((c) => {
        links.push({ source: n, target: c, share: clamp01(c.size / (n.size || 1)) });
        return walk(c, depth + 1, n._x, n.size);
      });
      n._y = (ys[0] + ys[ys.length - 1]) / 2;   // 居中于子节点跨度 → 兄弟间纵坐标仍严格递增
    } else {
      n._y = row * ROW; row++;
    }
    return n._y;
  })(state.root, 0, 0, 0);

  return { nodes, links, rows: row };
}

// ------------------------------------------------------------------ 渲染
let svg, viewport, gLinks, gNodes, zoom;
let renderQueued = false;

function initSvg() {
  svg = d3.select('#tree');
  viewport = svg.append('g').attr('class', 'viewport');
  gLinks = viewport.append('g').attr('class', 'links');
  gNodes = viewport.append('g').attr('class', 'nodes');

  zoom = d3.zoom().scaleExtent([0.25, 3]).on('zoom', (e) => {
    viewport.attr('transform', e.transform);
    $('statRight').textContent = '缩放 ' + Math.round(e.transform.k * 100) + '%';
    scheduleRender();
  });
  svg.call(zoom);

  // 事件委托：容器上挂一份监听，节点进出 DOM 不需要反复绑定
  const el = gNodes.node();
  el.addEventListener('mouseover', (e) => {
    const g = e.target.closest('g.node-row'); if (g) showTip(e, g.__data__);
  });
  el.addEventListener('mousemove', moveTip);
  el.addEventListener('mouseout', (e) => { if (!e.relatedTarget || !e.relatedTarget.closest || !e.relatedTarget.closest('g.node-row')) hideTip(); });
  el.addEventListener('click', (e) => {
    const g = e.target.closest('g.node-row'); if (!g) return;
    e.stopPropagation();
    if (clickTimer) clearTimeout(clickTimer);
    clickTimer = setTimeout(() => { clickTimer = null; onNodeClick(g.__data__); }, 230);
  });
  el.addEventListener('dblclick', (e) => {
    const g = e.target.closest('g.node-row'); if (!g) return;
    e.stopPropagation();
    if (clickTimer) { clearTimeout(clickTimer); clickTimer = null; }
    openInExplorer(g.__data__.path);
  });
  el.addEventListener('contextmenu', (e) => {
    const g = e.target.closest('g.node-row'); if (!g) return;
    e.preventDefault(); e.stopPropagation();
    showCtx(e, g.__data__);
  });
  el.addEventListener('mouseleave', hideTip);

  svg.on('click', (e) => {
    if (e.target.tagName === 'svg') { state.selected = null; refreshDetail(); scheduleRender(); }
  });
}

let clickTimer = null;

/** 合并成一帧渲染，避免滚动/缩放时重复计算。 */
function scheduleRender() {
  if (renderQueued) return;
  renderQueued = true;
  requestAnimationFrame(() => { renderQueued = false; render(); });
}

/** 当前可见的布局坐标范围（用于视口裁剪）。 */
function visibleBand() {
  const t = d3.zoomTransform(svg.node());
  const el = $('canvas');
  const y0 = (el.scrollTop - t.y) / t.k - CULL_MARGIN;
  const y1 = (el.scrollTop + el.clientHeight - t.y) / t.k + CULL_MARGIN;
  return [Math.max(-1e9, y0), y1];
}

function render() {
  if (!state.root) { gLinks.selectAll('*').remove(); gNodes.selectAll('*').remove(); return; }
  const { nodes, links, rows } = layout();
  const [vy0, vy1] = visibleBand();

  const visNodes = nodes.filter((n) => n._y >= vy0 && n._y <= vy1);  const visLinks = links.filter((l) => {
    const a = Math.min(l.source._y, l.target._y), b = Math.max(l.source._y, l.target._y);
    return b >= vy0 && a <= vy1;
  });

  const maxX = Math.max.apply(null, nodes.map((n) => n._x)) + 340;
  bounds = {
    x0: Math.min.apply(null, nodes.map((n) => n._x)), x1: maxX,
    y0: Math.min.apply(null, nodes.map((n) => n._y)),
    y1: Math.max.apply(null, nodes.map((n) => n._y)),
  };
  // 画布尺寸必须跟着缩放变换走，否则放大后右下角的内容滚不到（SVG 默认裁剪溢出）
  const t = d3.zoomTransform(svg.node());
  const cw = $('canvas').clientWidth || 900, ch = $('canvas').clientHeight || 600;
  const W = Math.max(cw, t.x + t.k * (bounds.x1 + PAD_X + 40));
  const H = Math.max(ch, t.y + t.k * (bounds.y1 + PAD_Y + 40));
  svg.attr('viewBox', `0 0 ${W} ${H}`).attr('width', W).attr('height', H);

  // ---- 支干（目录连线用 CSS 里的主题色，文件连线用分类色；
  //      SVG 表现属性不支持 var()，所以目录这里置空走 CSS）
  const link = gLinks.selectAll('path.link').data(visLinks, (d) => d.target.id);
  link.exit().remove();
  link.enter().append('path').attr('class', 'link').merge(link)
    .attr('d', elbow)
    .attr('stroke', (d) => (d.target.isDir ? null : colorOf(d.target)))
    .attr('stroke-opacity', (d) => (d.target._deleted ? 0.2 : 0.75))
    .attr('stroke-width', (d) => (state.thick ? 1 + 17 * Math.sqrt(d.share) : 1.6));

  // ---- 节点
  const rowSel = gNodes.selectAll('g.node-row').data(visNodes, (d) => d.id);
  rowSel.exit().remove();
  const enter = rowSel.enter().append('g').attr('class', 'node-row');
  enter.append('circle').attr('class', 'node-circle');
  enter.append('text').attr('class', 'node-label');
  enter.append('text').attr('class', 'node-sub');

  const all = enter.merge(rowSel);
  all.attr('transform', (d) => `translate(${d._x + PAD_X},${d._y + PAD_Y})`)
    .attr('opacity', (d) => (d._deleted ? 0.35 : 1))
    .classed('selected', (d) => state.selected && d.id === state.selected.id)
    .classed('match', (d) => state.matches.has(d.id));

  all.select('circle.node-circle')
    .attr('r', (d) => d._r)
    .attr('fill', (d) => colorOf(d))
    .attr('stroke-dasharray', (d) => (d.isDir && d.childCount && !d.expanded ? '2,2' : null));

  all.select('text.node-label').attr('x', (d) => d._r + 7).text(labelMain);
  all.each(function (d) {
    const lab = this.querySelector('text.node-label');
    const w = lab && lab.getComputedTextLength ? lab.getComputedTextLength() : 40;
    const sub = this.querySelector('text.node-sub');
    sub.setAttribute('x', d._r + 13 + w);
    sub.textContent = labelSub(d);
  });

  $('statRight').textContent = `缩放 ${Math.round(d3.zoomTransform(svg.node()).k * 100)}% · 渲染 ${visNodes.length}/${nodes.length}`;
}

function elbow(d) {
  const s = d.source, t = d.target;
  const x0 = s._x + (s._r || 3), y0 = s._y;
  const x1 = t._x - (t._r || 3), y1 = t._y;
  const mx = x0 + (x1 - x0) * 0.55;
  return `M${x0},${y0} H${mx} V${y1} H${x1}`;
}

let bounds = null;

function fit() {
  if (!state.root) return;
  if (!bounds) render();          // 首次先算一次完整边界
  if (!bounds) return;
  const w = $('canvas').clientWidth, h = $('canvas').clientHeight;
  const bw = bounds.x1 - bounds.x0 + 80, bh = bounds.y1 - bounds.y0 + 80;
  const k = Math.max(0.25, Math.min(w / bw, h / bh, 1.15));
  const tx = -bounds.x0 * k + 20, ty = -bounds.y0 * k + 20;
  svg.call(zoom.transform, d3.zoomIdentity.translate(tx, ty).scale(k));
}

// ------------------------------------------------------------------ 节点交互
async function onNodeClick(d) {
  if (d.isMore) {
    await ensureChildren(d._parent, d._parent._nextOffset);
    scheduleRender();
    return;
  }
  state.selected = d;
  refreshDetail();
  if (d.isDir && d.childCount) {
    if (d.expanded) d.expanded = false;
    else { await ensureChildren(d); d.expanded = true; }
  }
  scheduleRender();
}

// ------------------------------------------------------------------ 气泡
const tip = () => $('tooltip');
function showTip(e, d) {
  if (d.isMore) return;
  tip().innerHTML =
    `<div><b>${esc(d.name)}</b></div>` +
    `<div class="muted">${esc(d.path)}</div>` +
    `<div>体积：<b>${humanSize(d.size)}</b>${d.isDir ? `（本级 ${humanSize(d.ownSize)}）` : ''}</div>` +
    `<div>类型：${esc(d.isDir ? '文件夹' : zhOf(d))}${d.kind === 'symlink' ? '（链接，未跟随）' : ''}</div>` +
    `<div>修改：${fmtTime(d.mtime)}</div>` +
    (d.isDir ? `<div>直属子项：${d.childCount}</div>` : '') +
    (d.error ? `<div class="err">${esc(d.error)}</div>` : '') +
    `<div class="muted">单击展开 · 双击定位 · 右键加入清单</div>`;
  tip().classList.remove('hidden');
  moveTip(e);
}
function moveTip(e) {
  const t = tip();
  t.style.left = Math.min(e.clientX + 16, window.innerWidth - 450) + 'px';
  t.style.top = Math.min(e.clientY + 16, window.innerHeight - 170) + 'px';
}
function hideTip() { tip().classList.add('hidden'); }

// ------------------------------------------------------------------ 右键菜单
let ctxNode = null;
function showCtx(e, d) {
  ctxNode = d;
  const m = $('ctxmenu');
  m.style.left = Math.min(e.clientX, window.innerWidth - 210) + 'px';
  m.style.top = Math.min(e.clientY, window.innerHeight - 220) + 'px';
  m.classList.remove('hidden');
}
function hideCtx() { $('ctxmenu').classList.add('hidden'); ctxNode = null; }

// ------------------------------------------------------------------ 详情面板
async function refreshDetail() {
  const box = $('details');
  const n = state.selected;
  if (!n) {
    box.innerHTML = '<div class="muted">单击左侧节点查看详情；双击可在资源管理器中定位；右键加入清理清单。</div>';
    return;
  }
  let extZh = n.isDir ? '文件夹' : zhOf(n);
  let parentPath = n._parent ? n._parent.path : null;
  try {
    const r = await api('/api/node?id=' + n.id);
    if (r.ok) { extZh = r.node.extZh || extZh; parentPath = r.node.parent; }
  } catch (e) { /* 离线也能用本地数据兜底 */ }

  const parentSize = n._parent ? n._parent.size : n.size;
  const pct = parentSize ? Math.min(100, (n.size / parentSize) * 100) : 100;
  box.innerHTML =
    `<div class="kv">
      <div class="k">名称</div><div class="v">${esc(n.name)}</div>
      <div class="k">类型</div><div class="v">${esc(extZh)}${n.kind === 'symlink' ? ' · 符号链接' : ''}${n.kind === 'error' ? ' · 读取失败' : ''}</div>
      <div class="k">体积</div><div class="v"><b>${humanSize(n.size)}</b>${n.isDir ? ` （本级 ${humanSize(n.ownSize)}）` : ''}</div>
      ${n.isDir ? `<div class="k">直属子项</div><div class="v">${n.childCount}</div>` : ''}
      <div class="k">占上级</div><div class="v">${pct.toFixed(1)}%<div class="ratio"><i style="width:${pct}%"></i></div></div>
      <div class="k">修改时间</div><div class="v">${fmtTime(n.mtime)}</div>
      <div class="k">真实路径</div><div class="v path">${esc(n.path)}</div>
      ${parentPath ? `<div class="k">上级目录</div><div class="v path">${esc(parentPath)}</div>` : ''}
      ${n.error ? `<div class="k">异常</div><div class="v err">${esc(n.error)}</div>` : ''}
    </div>
    <div class="detail-actions">
      <button id="dOpen">资源管理器定位</button>
      <button id="dAdd" class="primary">加入清理清单</button>
      <button id="dCopy">复制路径</button>
    </div>`;
  $('dOpen').onclick = () => openInExplorer(n.path);
  $('dCopy').onclick = () => navigator.clipboard && navigator.clipboard.writeText(n.path);
  $('dAdd').onclick = () => addToCart(n);
}

async function openInExplorer(path) {
  await fetch('/api/open', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path }),
  });
}

// ------------------------------------------------------------------ 空间 TOP
async function loadTop(force) {
  if (!state.root) { $('topList').innerHTML = '<div class="muted small">扫描完成后自动列出。</div>'; return; }
  if (state.topCache && !force) { renderTop(state.topCache); return; }
  const r = await api('/api/top?n=20');
  if (!r.ok) return;
  state.topCache = r;
  renderTop(r);
}
function renderTop(r) {
  const items = (r.files || []).concat([]);
  $('topList').innerHTML =
    (r.dirs || []).map(topRow).join('') +
    `<div class="muted small" style="padding:6px 6px">— 大文件 —</div>` +
    items.map(topRow).join('');

  $('topList').querySelectorAll('[data-add]').forEach((el) => {
    el.onclick = () => {
      const id = +el.dataset.add;
      const node = state.byId.get(id);
      addToCart(node || pickFromTop(id));
    };
  });
  $('topList').querySelectorAll('[data-loc]').forEach((el) => {
    el.onclick = async () => {
      const id = +el.dataset.loc;
      const node = state.byId.get(id);
      if (node) { await reveal(node); state.selected = node; refreshDetail(); scheduleRender(); switchTab('detail'); }
      else openInExplorer(el.dataset.path);
    };
  });
}
function pickFromTop(id) {
  const all = ((state.topCache || {}).files || []).concat(((state.topCache || {}).dirs || []));
  const d = all.find((x) => x.id === id);
  return d ? Object.assign(adapt(d), { _parent: null }) : null;
}
function topRow(d) {
  const n = adapt(d);
  return `<div class="top-item" title="${esc(d.path)}">
    <span class="dotc" style="background:${colorOf(n)}"></span>
    <span class="nm">${esc(d.name)}</span>
    <span class="sz">${humanSize(d.size)}</span>
    <span class="x" data-add="${d.id}" title="加入清单">＋</span>
    <span data-loc="${d.id}" data-path="${esc(d.path)}" style="cursor:pointer" title="定位">⌖</span>
  </div>`;
}
function switchTab(name) {
  const t = document.querySelector(`.tab[data-tab="${name}"]`);
  if (t) t.click();
}

// ------------------------------------------------------------------ 清理清单
function addToCart(n) {
  if (!n || n.isMore || state.cart.has(n.id)) return;
  state.cart.set(n.id, { id: n.id, name: n.name, path: n.path, size: n.size, isDir: n.isDir });
  renderCart(); scheduleRender();
}
function renderCart() {
  const items = [...state.cart.values()].sort((a, b) => b.size - a.size);
  $('cartBadge').textContent = items.length;
  $('btnDelete').disabled = items.length === 0;
  // 侧栏收起时，展开按钮上也要能看到待清理数量
  const sideBadge = $('sideCartBadge');
  sideBadge.textContent = items.length;
  sideBadge.classList.toggle('hidden', items.length === 0);
  let total = 0;
  $('cartList').innerHTML = items.length
    ? items.map((it) => {
      total += it.size;
      return `<div class="cart-item" title="${esc(it.path)}">
        <span>${it.isDir ? '📁' : '📄'}</span>
        <span class="nm">${esc(it.name)}</span>
        <span class="sz">${humanSize(it.size)}</span>
        <span class="x" data-del="${it.id}">✕</span>
      </div>`;
    }).join('')
    : '<div class="muted small">还没有加入任何条目。<br/>在节点上右键，或在详情面板点「加入清理清单」。</div>';
  $('cartSize').textContent = humanSize(total);
  $('cartList').querySelectorAll('[data-del]').forEach((el) => {
    el.onclick = () => { state.cart.delete(+el.dataset.del); renderCart(); scheduleRender(); };
  });
}

// ------------------------------------------------------------------ 删除（二次确认）
let modalStep = 0, useRecycle = true;

function openModal(title, bodyHtml, okText) {
  $('modalTitle').textContent = title;
  $('modalBody').innerHTML = bodyHtml;
  $('modalOk').textContent = okText;
  $('modalOk').disabled = false;
  $('modalOk').onclick = modalOkClicked;
  $('modal').classList.remove('hidden');
}
function closeModal() { $('modal').classList.add('hidden'); modalStep = 0; }

function openDeleteModal() {
  const items = [...state.cart.values()];
  if (!items.length) return;
  const total = items.reduce((s, i) => s + i.size, 0);
  const dirs = items.filter((i) => i.isDir).length;
  modalStep = 1; useRecycle = true;
  const listHtml = items.slice(0, 200).map((i) =>
    `<div>${i.isDir ? '📁' : '📄'} ${esc(i.path)} <span class="muted">(${humanSize(i.size)})</span></div>`).join('')
    + (items.length > 200 ? `<div class="muted">… 其余 ${items.length - 200} 项已省略</div>` : '');

  openModal('第 1 / 2 步 · 核对待删除内容',
    `<div class="warn-box">即将删除 <b>${items.length}</b> 项${dirs ? `（含 ${dirs} 个目录，删除目录会连同其中所有内容）` : ''}，
     合计 <b>${humanSize(total)}</b>。请逐条核对。</div>
     <div class="file-list">${listHtml}</div>
     <div style="margin-top:10px">
       <label class="chk"><input type="checkbox" id="mRecycle" checked /> 移入系统回收站（推荐；失败时不执行删除）</label>
     </div>`,
    '下一步');
  $('modalNote').textContent = '';
  $('mRecycle').onchange = (e) => { useRecycle = e.target.checked; };
}

function modalOkClicked() {
  if (modalStep === 1) {
    const items = [...state.cart.values()];
    const total = items.reduce((s, i) => s + i.size, 0);
    modalStep = 2;
    openModal('第 2 / 2 步 · 最终确认',
      `<div class="warn-box"><b>⚠ 此操作不可撤销，可能导致数据永久丢失。</b><br/>
        将删除 ${items.length} 项，合计 ${humanSize(total)}${useRecycle ? '（移入回收站）' : '（永久删除，不经过回收站）'}。</div>
       <label class="chk"><input type="checkbox" id="mConfirm" /> 我已核对清单，确认删除且已备份重要数据</label>`,
      '确认删除');
    $('modalNote').textContent = '勾选后才可继续';
    $('modalOk').disabled = true;
    $('modalOk').onclick = modalOkClicked;
    $('mConfirm').onchange = (e) => {
      $('modalOk').disabled = !e.target.checked;
      $('modalNote').textContent = e.target.checked ? '' : '勾选后才可继续';
    };
    return;
  }
  if (modalStep === 2) doDelete();
}

async function doDelete() {
  const ids = [...state.cart.keys()].filter((x) => typeof x === 'number');
  $('modalOk').disabled = true;
  $('modalNote').textContent = '删除中…';
  const r = await fetch('/api/delete', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ids, useRecycle }),
  }).then((x) => x.json());

  const ok = (r.results || []).filter((x) => x.ok);
  const bad = (r.results || []).filter((x) => !x.ok);
  ok.forEach((x) => {
    const item = [...state.cart.values()].find((i) => i.path === x.path);
    if (item) {
      state.cart.delete(item.id);
      const node = state.byId.get(item.id);
      if (node) node._deleted = true;
    }
  });
  renderCart(); scheduleRender();

  modalStep = 3;
  openModal('删除完成',
    `<div>成功 <b>${ok.length}</b> 项，释放 <b>${humanSize(r.freed || 0)}</b></div>
     ${bad.length ? `<div class="err" style="margin-top:8px">失败 ${bad.length} 项：</div>
       <div class="file-list">${bad.map((b) => `<div>${esc(b.path)} — ${esc(b.message)}</div>`).join('')}</div>` : ''}
     <div class="muted" style="margin-top:8px">树状图仍是删除前的快照，建议重新扫描刷新统计。</div>`,
    '关闭');
  $('modalNote').textContent = '';
  $('modalOk').onclick = closeModal;
}

// ------------------------------------------------------------------ 查找
function applyFilter() {
  state.matches.clear(); state.matchOrder = []; state.matchIdx = 0;
  if (!state.filter || !state.root) { scheduleRender(); return; }
  const kw = state.filter.toLowerCase();
  (function walk(n) {
    if (!n.isMore && n.name.toLowerCase().includes(kw)) { state.matches.add(n.id); state.matchOrder.push(n); }
    if (n.children) n.children.forEach(walk);
  })(state.root);
  scheduleRender();
  if (state.matchOrder.length) setProgress(`匹配 ${state.matchOrder.length} 项（未展开的分支需先展开）`);
}
async function findNext() {
  if (!state.matchOrder.length) applyFilter();
  if (!state.matchOrder.length) { setProgress('没有匹配项'); return; }
  const n = state.matchOrder[state.matchIdx % state.matchOrder.length];
  state.matchIdx++;
  await reveal(n);
  state.selected = n; refreshDetail(); scheduleRender();
  switchTab('detail');
}
async function reveal(n) {
  const chain = [];
  let p = n._parent;
  while (p) { chain.unshift(p); p = p._parent; }
  for (const par of chain) { await ensureChildren(par); par.expanded = true; }
}

// ------------------------------------------------------------------ 退出
function confirmQuit() {
  modalStep = 0;
  const warn = state.scanning
    ? '<div class="warn-box">正在扫描中，退出会立即中断本次扫描。</div>' : '';
  openModal('退出 FileCleanup',
    warn + `<div style="line-height:1.9">退出后会：<br/>
      · 关闭本地服务，立即释放占用的端口<br/>
      · 中断尚未完成的扫描<br/>
      · 本页无法继续使用，需重新运行 FileCleanup<br/><br/>
      待清理清单里的内容<b>不会被删除</b>，重新启动后需要重新扫描。</div>`,
    '确认退出');
  $('modalOk').onclick = doQuit;
  $('modalNote').textContent = '';
}

async function doQuit() {
  $('modalOk').disabled = true;
  $('btnQuit').disabled = true;
  $('modalNote').textContent = '正在关闭…';
  stopPolling();                       // 先停掉轮询，避免退出后又打请求
  hideTip();
  try {
    await fetch('/api/shutdown', { method: 'POST' });
  } catch (e) { /* 服务已关也算成功 */ }
  closeModal();
  $('quitOverlay').classList.remove('hidden');
  setProgress('服务已停止，端口已释放');
  $('statLeft').textContent = '服务已停止';
}

// ------------------------------------------------------------------ 帮助
function showHelp() {
  modalStep = 0;
  openModal('使用说明与快捷键',
    `<div style="line-height:1.9">
      <b>基本流程</b>：填目录 → 开始扫描 → 在横向树里按体积从大到小排查 → 右键加入清单 → 删除（两步确认）。<br/><br/>
      <b>鼠标</b><br/>
      · 单击节点：展开 / 折叠<br/>
      · 双击节点：在系统文件管理器中定位<br/>
      · 右键节点：定位 / 加入清单 / 复制路径 / 展开或折叠全部子级<br/>
      · 滚轮缩放、按住拖拽平移<br/><br/>
      <b>快捷键</b><br/>
      · <span class="kbd">Enter</span> 在路径框内直接开始扫描<br/>
      · <span class="kbd">/</span> 聚焦搜索框　· <span class="kbd">Esc</span> 关闭弹窗<br/>
      · <span class="kbd">f</span> 适应窗口　· <span class="kbd">c</span> 全部折叠<br/><br/>
      <b>标签显示</b><br/>
      · 「显示扩展名」「显示中文释义」两个开关相互独立：可只显示扩展名、只显示中文释义、
        也可以两个都显示（如 “.zip · 压缩文件”）；两个都关时只显示文件名<br/>
      · 符号链接 / junction 不跟随，避免重复统计与死循环<br/>
      · 删除只接受本次扫描结果内的条目，且路径必须在扫描根目录之下<br/>
      · 默认送系统回收站，回收站失败时不会自动改为永久删除<br/><br/>
      <b>退出</b><br/>
      · 右上角「退出」会关闭本地服务并立即释放端口；<b>直接关掉浏览器页面不会停服务</b>，
        需要退出请点它（或回到程序窗口按 Ctrl+C）
    </div>`, '知道了');
  $('modalOk').onclick = closeModal;
}

init();
