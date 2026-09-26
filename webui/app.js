/* ==========================================================================
   MDReader front-end
   ========================================================================== */
'use strict';

const $  = (s, r) => (r || document).querySelector(s);
const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));

/* ==========================================================================
   Application shell state
   ========================================================================== */
const state = {
  version: '',
  engine: '',
  workspace: '',
  mode: 'server',
  projects: [],
  pid: null,
  project: null,
  docs: [],
  dirs: [],
  active: null,            // current id: project doc id, absolute path, or "draft:N"
  doc: null,               // last loaded payload {kind, info, raw, html, meta, title, stats}
  dirty: false,
  openDirs: {},
  recent: [],              // loose documents opened straight from disk
  busy: false,
  roots: [],               // 用户已授权的原文件夹（F01）
  rid: null,
  rootDocs: [],
  rootCounts: {},
  rootStatus: '',
  rootTruncated: '',
  rootSkipped: [],
  foldersOpen: {},
  plugins: [],             // 已安装插件的状态（P01）
  pluginCommands: [],      // 当前可用的插件命令（启用且兼容）
};

/* ------------------------------------------------------------------ utils */
const DOC_EXT = /\.(md|markdown|mdown|mkd|txt)$/i;

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function toast(msg, kind, title) {
  const box = document.createElement('div');
  box.className = 'toast ' + (kind || '');
  box.innerHTML = (title ? '<div class="t-title">' + esc(title) + '</div>' : '') +
                  '<div class="t-body">' + esc(msg) + '</div>';
  $('#toasts').appendChild(box);
  const life = kind === 'err' ? 7000 : 3600;
  setTimeout(() => { box.style.transition = 'opacity .3s'; box.style.opacity = '0';
                     setTimeout(() => box.remove(), 320); }, life);
}

async function api(path, payload) {
  const opt = payload === undefined
    ? { method: 'GET' }
    : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) };
  opt.headers = Object.assign({}, opt.headers, {'X-MDReader-Session': document.querySelector('meta[name="mdreader-session"]')?.content || ''});
  const res = await fetch(path, opt);
  let data;
  try { data = await res.json(); } catch (e) { throw new Error('服务器返回了非 JSON 内容（HTTP ' + res.status + '）'); }
  if (data.conflict && payload) {
    const decision = await modal({title:'磁盘版本冲突',hint:esc(data.error),choices:[{label:'备份后覆盖当前磁盘版本',value:'overwrite',primary:true}]});
    if (decision?.__choice === 'overwrite') return api(path, Object.assign({},payload,{overwrite:data.revision}));
  }
  if (!res.ok || data.ok === false) throw new Error(data.error || ('请求失败 HTTP ' + res.status));
  return data;
}

function busy(on, text) {
  state.busy = on;
  if (on && text) toast(text, '', '请稍候');
}

/* --------------------------------------------------------------- modal */
function modal(opts) {
  return new Promise(resolve => {
    const ov = $('#overlay');
    const fields = (opts.fields || []).map(f => {
      if (f.type === 'textarea') {
        return '<div class="field"><label class="lb">' + esc(f.label) + '</label>' +
               '<textarea id="mf_' + f.name + '" rows="' + (f.rows || 4) + '" placeholder="' +
               esc(f.placeholder || '') + '">' + esc(f.value || '') + '</textarea></div>';
      }
      return '<div class="field"><label class="lb">' + esc(f.label) + '</label>' +
             '<input type="text" id="mf_' + f.name + '" value="' + esc(f.value || '') +
             '" placeholder="' + esc(f.placeholder || '') + '"></div>';
    }).join('');
    const choices = (opts.choices || []).map(c =>
      '<button class="btn ' + (c.primary ? 'primary' : '') + '" data-choice="' + esc(c.value) + '">' +
      esc(c.label) + '</button>').join('');
    ov.innerHTML =
      '<div class="modal" role="dialog">' +
        '<h3>' + esc(opts.title || '') + '</h3>' +
        (opts.hint ? '<p class="hint">' + opts.hint + '</p>' : '') +
        fields +
        '<div class="actions">' +
          (opts.choices ? choices : '') +
          '<button class="btn" data-choice="__cancel">取消</button>' +
          (opts.choices ? '' : '<button class="btn primary" data-choice="__ok">确定</button>') +
        '</div>' +
      '</div>';
    ov.classList.add('on');
    const first = ov.querySelector('input,textarea');
    if (first) { first.focus(); first.select && first.select(); }

    function close(val) {
      ov.classList.remove('on'); ov.innerHTML = '';
      document.removeEventListener('keydown', onKey);
      resolve(val);
    }
    function collect() {
      const out = {};
      (opts.fields || []).forEach(f => { const el = $('#mf_' + f.name); if (el) out[f.name] = el.value.trim(); });
      return out;
    }
    function onKey(e) {
      if (e.key === 'Escape') { e.preventDefault(); close(null); }
      if (e.key === 'Enter' && !e.shiftKey && e.target.tagName !== 'TEXTAREA') {
        e.preventDefault();
        if (opts.choices) return;
        close(collect());
      }
    }
    document.addEventListener('keydown', onKey);
    ov.onclick = e => { if (e.target === ov) close(null); };
    $$('[data-choice]', ov).forEach(b => b.onclick = () => {
      const v = b.dataset.choice;
      if (v === '__cancel') return close(null);
      if (v === '__ok') return close(collect());
      close({ __choice: v, ...collect() });
    });
  });
}

/* ------------------------------------------- loose docs (临时查看本地文件) */
/* 拖进窗口 / 双击打开 / “打开本地文件”进来的 .md 属于这一类：
   软件只记住它的磁盘路径，阅读和编辑直接作用在**原文件**上，
   不复制、不导入。想长期管理时再点“加入项目”。 */

function isLoose() { return !!state.doc && state.doc.kind === 'loose'; }

function renderRecent() {
  const host = $('#recentList');
  if (!host) return;
  const query = ($('#recentSearch').value || '').trim().toLocaleLowerCase();
  const all = state.recent || [];
  const items = all.filter(item => (item.name + ' ' + item.path).toLocaleLowerCase().includes(query));
  $('#recentCount').textContent = query ? items.length + ' / ' + all.length : all.length;
  if (!items.length) {
    host.innerHTML = '<div class="tree-empty" style="padding:10px 6px">' +
      (query ? '没有匹配的记录' : '把 .md 文件拖进窗口<br>就能直接阅读和修改') + '</div>';
    return;
  }
  host.innerHTML = items.map(item => {
    const active = state.active === item.id ? ' active' : '';
    const gone = item.missing ? ' missing' : '';
    return '<div class="tree-item recent' + active + gone + '" data-loose="' + esc(item.id) + '" ' +
             'title="' + esc(item.path) + '">' +
             '<span>' + (item.missing ? '⚠' : '🗎') + '</span>' +
             '<span class="ti-name">' + esc(item.name) + '</span>' +
             '<span class="ti-act">' +
               '<button class="btn" data-loose-act="forget" title="从列表移除（不删文件）">✕</button>' +
             '</span>' +
           '</div>';
  }).join('');

  $$('[data-loose]', host).forEach(el => {
    el.onclick = ev => { if (!ev.target.closest('.ti-act')) openLooseDoc(el.dataset.loose); };
    const forget = $('[data-loose-act=forget]', el);
    if (forget) forget.onclick = ev => { ev.stopPropagation(); forgetLoose(el.dataset.loose); };
  });
}

function rememberRecent(list) {
  if (Array.isArray(list)) state.recent = list;
  renderRecent();
}

/* open one or more files straight from disk (absolute paths) */
async function openLooseFiles(paths) {
  const wanted = (paths || []).filter(Boolean);
  if (!wanted.length) return false;
  if (!await confirmLeaveDocument()) return false;
  try {
    const res = await api('/api/loose/open', { paths: wanted });
    rememberRecent(res.recent);
    const failed = res.failed || [];
    if (failed.length) toast(failed.map(f => f.error).join('；'), 'warn', '部分文件未能打开');
    const doc = res.doc || (res.docs && res.docs[0]);
    if (!doc) return false;
    await showLoose(doc);
    if (wanted.length > 1) toast('已打开 ' + (res.docs || []).length + ' 个文件，可在左侧“最近打开”里切换', 'ok');
    return true;
  } catch (e) {
    toast(e.message, 'err', '打开失败');
    return false;
  }
}

/* fetch + display one loose document (path or draft id) */
async function openLooseDoc(id) {
  if (!id) return false;
  if (state.active === id && state.doc && state.doc.kind === 'loose') return true;
  if (!await confirmLeaveDocument()) return false;
  try {
    const data = await api('/api/loose?doc=' + encodeURIComponent(id));
    return await showLoose(data);
  } catch (e) {
    toast(e.message, 'err', '打开失败');
    return false;
  }
}

async function showLoose(data) {
  state.doc = data;
  state.active = data.info.id;
  state.dirty = false;
  document.body.classList.remove('dirty');
  localStorage.setItem('mdreader.doc', data.info.id);
  localStorage.setItem('mdreader.kind', 'loose');
  renderRecent();
  renderDocument();
  setButtons();
  afterDocumentShown();
  return true;
}

/* a brand new file with no location yet */
async function newLooseDraft() {
  if (!await confirmLeaveDocument()) return false;
  try {
    const res = await api('/api/loose/draft', { name: 'Untitled' });
    await showLoose(res.doc);
    switchMode('source');
    $('#editor').focus();
    toast('新文档还没保存到磁盘，按 Ctrl+S 选择保存位置', 'ok');
    return true;
  } catch (e) { toast(e.message, 'err', '新建失败'); return false; }
}

async function forgetLoose(path) {
  try {
    const res = await api('/api/loose/forget', { path });
    rememberRecent(res.recent);
    toast('已从列表移除（磁盘文件没有删除）', 'ok');
  } catch (e) { toast(e.message, 'err', '移除失败'); }
}

/* save the loose document back to its ORIGINAL file */
async function saveLooseDoc(path) {
  if (!state.doc || state.doc.kind !== 'loose') return false;
  const docId = state.active;
  const content = $('#editor').value;
  const target = path || null;
  try {
    const payload = { doc: docId, content, expected:state.doc.info.revision };
    if (target) payload.path = target;
    const res = await api('/api/loose/save', payload);
    if (res.cancelled) return false;
    if (state.active !== docId || $('#editor').value !== content) {
      toast('已保存提交时的内容；当前编辑内容仍保留。', 'ok');
      return false;
    }
    rememberRecent(res.recent);
    state.doc = res;
    state.active = res.info.id;
    state.dirty = false;
    document.body.classList.remove('dirty');
    discardRecovery(content);
    localStorage.setItem('mdreader.doc', state.active);
    renderRecent();
    renderDocument();
    setButtons();
    toast('已保存到原文件：' + res.info.path, 'ok');
    return true;
  } catch (e) {
    toast(e.message, 'err', '保存失败');
    return false;
  }
}

/* “另存为”：先问用户要路径，再写过去 */
async function saveLooseAs() {
  if (!state.doc || state.doc.kind !== 'loose') return false;
  const content = $('#editor').value;
  const suggested = (state.doc.info.name || '未命名文档') + '.md';
  try {
    const res = await api('/api/loose/saveas', { doc: state.active, content, name: suggested });
    if (res.cancelled) { toast('已取消', 'warn'); return false; }
    rememberRecent(res.recent);
    state.doc = res;
    state.active = res.info.id;
    state.dirty = false;
    document.body.classList.remove('dirty');
    discardRecovery(content);
    localStorage.setItem('mdreader.doc', state.active);
    renderRecent();
    renderDocument();
    setButtons();
    toast('已保存到：' + res.info.path, 'ok');
    return true;
  } catch (e) { toast(e.message, 'err', '另存为失败'); return false; }
}

/* copy the temporary document into the current project */
async function looseToProject() {
  if (!state.doc || state.doc.kind !== 'loose') return false;
  if (!state.pid) {
    toast('请先在左侧选择或新建一个项目', 'warn');
    return false;
  }
  const dirs = ['（项目根目录）'].concat(state.dirs.filter(Boolean));
  const r = await modal({
    title: '加入项目',
    hint: '把这份文档复制进项目，成为受管理的文档。<br>可选目录：<code>' + esc(dirs.join(' · ')) + '</code>',
    fields: [{ name: 'dir', label: '放入子目录（留空=根目录）', placeholder: '例如：docs' }],
    choices: [
      { label: '复制进项目', value: 'copy', primary: true },
      { label: '移动进项目（删除原文件）', value: 'move' },
    ],
  });
  if (!r) return false;
  const move = r.__choice === 'move';
  try {
    const res = await api('/api/loose/to_project', {
      pid: state.pid, doc: state.active, dir: r.dir || '', move,
    });
    rememberRecent(res.recent);
    await refreshProject();
    await openDoc(res.doc.id, { force: true });
    toast((move ? '已移动到项目：' : '已复制到项目：') + res.doc.id, 'ok');
    return true;
  } catch (e) { toast(e.message, 'err', '加入项目失败'); return false; }
}

/* ------------------------------------------------- 我的文件夹（F01，网页端） */
/* 打开用户自己的文件夹：直接读写原件、不导入；外部增删由 1.5 秒轮询带进界面。
   桌面端是同一个核心模块，两边看到的树与新建规则完全一致。 */
function debounce(fn, ms) {
  let job = null;
  const wrapped = (...args) => {
    if (job) clearTimeout(job);
    job = setTimeout(() => { job = null; fn(...args); }, ms);
  };
  wrapped.cancel = () => { if (job) { clearTimeout(job); job = null; } };
  return wrapped;
}

function rootById(id) { return (state.roots || []).find(r => r.id === id) || null; }

function renderRootSelect() {
  const sel = $('#rootSelect');
  if (!sel) return;
  const roots = state.roots || [];
  if (!roots.length) {
    sel.innerHTML = '<option value="">（还没有打开文件夹）</option>';
    sel.disabled = true;
  } else {
    sel.disabled = false;
    sel.innerHTML = roots.map(r =>
      '<option value="' + esc(r.id) + '">' + esc(r.name) + (r.readable ? '' : '（暂时读不到）') + '</option>').join('');
    sel.value = state.rid || roots[0].id;
  }
  ['#btnNewFolderDoc', '#btnRefreshFolder', '#btnRemoveFolder'].forEach(sel2 => {
    const el = $(sel2);
    if (el) el.disabled = !state.rid;
  });
}

function renderFolderMeta() {
  const host = $('#folderMeta');
  if (!host) return;
  const root = rootById(state.rid);
  if (!root) {
    host.innerHTML = '<span>用「打开文件夹」直接管理你自己的目录</span>';
    return;
  }
  const counts = state.rootCounts || {};
  const chips = ['<span title="' + esc(root.path) + '">' + esc(root.path) + '</span>',
                 '<span>' + (counts.docs || 0) + ' 篇 Markdown</span>'];
  if (!root.readable) chips.push('<span>⚠ ' + esc(root.problem || '暂时无法访问') + '</span>');
  if (state.rootTruncated) chips.push('<span>⚠ ' + esc(state.rootTruncated) + '</span>');
  if ((state.rootSkipped || []).length) chips.push('<span>已跳过越界链接 ' + state.rootSkipped.length + ' 个</span>');
  host.innerHTML = chips.join('');
}

function buildRootNodes(docs) {
  const root = { dirs: {}, files: [] };
  (docs || []).forEach(d => {
    const parts = String(d.id || '').split('/').filter(Boolean);
    let node = root;
    parts.slice(0, -1).forEach(part => { node = node.dirs[part] || (node.dirs[part] = { dirs: {}, files: [] }); });
    node.files.push(d);
  });
  return root;
}

function renderRootTree() {
  const host = $('#folderTree');
  if (!host) return;
  state.rootDocs = state.rootDocs || [];
  if (!state.rid) {
    host.innerHTML = '<div class="tree-empty">打开一个文件夹后<br>这里的 Markdown 会持续跟着磁盘更新</div>';
    return;
  }
  if (!state.rootDocs.length) {
    host.innerHTML = '<div class="tree-empty">这个文件夹里还没有 Markdown<br>点上方「＋ 新建 Markdown」在原目录新建</div>';
    return;
  }
  host.innerHTML = renderNode(buildRootNodes(state.rootDocs), '', 'root');
  $$('.tree-dir', host).forEach(el => el.onclick = () => {
    const p = el.dataset.dir;
    state.foldersOpen[p] = !state.foldersOpen[p];
    renderRootTree();
  });
  $$('.tree-item', host).forEach(el => {
    el.onclick = ev => { if (!ev.target.closest('.ti-act')) openRootDoc(el.dataset.id); };
    const del = $('[data-act=del]', el);
    if (del) del.onclick = ev => { ev.stopPropagation(); deleteRootDoc(el.dataset.id); };
  });
  $$('[data-new]', host).forEach(btn => btn.onclick = ev => {
    ev.stopPropagation();
    newUntitledInRoot(btn.dataset.new);
  });
}

/* 删除用户的本地文件：每次都明确提示真实影响，默认移入回收站。
   失败（回收站不可用、文件被占用、确认后文件被替换）一律保留列表与缓冲。 */
async function deleteRootDoc(did) {
  if (!state.rid) return false;
  try {
    const data = await api('/api/folder/doc?root=' + encodeURIComponent(state.rid) +
                           '&doc=' + encodeURIComponent(did));
    const answer = await modal({
      title: '删除本地文件…',
      hint: '将删除本地文件「' + esc(data.info.file) + '」，并移入 Windows 回收站。<br>' +
            '它会从原文件夹消失，不只是从 MDReader 列表中移除。其他文档和附件不会删除。<br><br>' +
            '完整路径：<code>' + esc(data.path) + '</code>',
      choices: [{ label: '取消', value: 'cancel' },
                { label: '移入回收站', value: 'recycle', primary: true }],
    });
    if (!answer || answer.__choice !== 'recycle') return false;
    const res = await api('/api/folder/delete', { root: state.rid, doc: did, recycle: true,
                                                 expected: data.info.revision });
    if (state.doc && state.doc.path === data.path) {
      // 原文件没了：缓冲仍在，但只能另存到新位置
      state.doc.info.deleted = true;
      state.doc.info.missing = true;
      setButtons();
      toast('原文件已删除；未保存内容还在，只能「另存为」到新位置', 'warn');
    }
    state.rootDocs = res.docs || [];
    renderRootTree();
    renderFolderMeta();
    toast('已把「' + data.info.file + '」移入回收站，可从回收站还原', 'ok');
    return true;
  } catch (e) { toast(e.message, 'err', '删除失败，文件已保留'); return false; }
}

async function refreshRoots(wantDoc) {
  let st;
  try { st = await api('/api/state'); } catch (e) { return; }
  state.roots = st.root_folders || [];
  const remembered = localStorage.getItem('mdreader.root');
  const chosen = (state.rid && rootById(state.rid)) ? state.rid
              : (rootById(remembered) ? remembered : (state.roots.length ? state.roots[0].id : ''));
  state.rid = chosen || null;
  renderRootSelect();
  if (state.rid) await refreshFolderTree(true, wantDoc);
  else { state.rootDocs = []; renderFolderMeta(); renderRootTree(); }
}

async function refreshFolderTree(force, wantDoc) {
  if (!state.rid) return;
  try {
    const data = await api('/api/folder?root=' + encodeURIComponent(state.rid) + (force ? '&refresh=1' : ''));
    state.rootDocs = data.docs || [];
    state.rootCounts = data.counts || {};
    state.rootStatus = data.status;
    state.rootTruncated = data.truncated || '';
    state.rootSkipped = data.skipped_links || [];
    if (data.folder) {
      state.roots = (state.roots || []).map(r => r.id === data.folder.id ? Object.assign({}, r, data.folder) : r);
    }
    renderRootSelect();
    renderFolderMeta();
    renderRootTree();
    // 用 JSON 在这里就地构造数组：调用方（桌面壳/网页）传来的值可能来自另一个
    // realm，直接跨域构造数组会变成“类数组对象”，下游的 Array.isArray 会判否
    if (wantDoc) await openLooseFiles(JSON.parse(JSON.stringify([String(wantDoc)])));
  } catch (e) {
    state.rootDocs = [];
    state.foldersOpen = {};
    renderRootTree();
    const meta = $('#folderMeta');
    if (meta) meta.innerHTML = '<span>⚠ ' + esc(e.message) + '</span>';
  }
}

async function openUserFolder() {
  if (!await confirmLeaveDocument()) return;
  let path = '';
  try {
    const picked = await api('/api/dialog/folder', {});
    path = (picked.paths || [])[0] || '';
  } catch (e) { toast(e.message, 'err', '打开文件夹失败'); return; }
  if (!path) return;                      // 用户取消
  try {
    const res = await api('/api/folder/open', { path: path });
    state.roots = res.roots || state.roots;
    state.rid = res.folder.id;
    state.foldersOpen = {};
    localStorage.setItem('mdreader.root', state.rid);
    renderRootSelect();
    await refreshFolderTree(true);
    toast('已打开文件夹「' + res.folder.name + '」，修改直接写回原文件', 'ok');
  } catch (e) { toast(e.message, 'err', '打开文件夹失败'); }
}

async function selectRoot(rid) {
  if (!rid || rid === state.rid) return;
  state.rid = rid;
  state.foldersOpen = {};
  localStorage.setItem('mdreader.root', rid);
  renderRootSelect();
  await refreshFolderTree(true);
}

async function newFolderDoc() {
  if (!state.rid) { toast('请先打开一个文件夹', 'warn'); return; }
  const root = rootById(state.rid) || {};
  const answer = await modal({
    title: '新建 Markdown',
    hint: '会直接建在：' + esc(root.path || '') + '（默认补 .md，不会覆盖同名文件）',
    fields: [{ key: 'name', label: '文件名', value: '未命名文档' }],
    choices: [{ label: '创建并打开', value: 'ok', primary: true }],
  });
  if (!answer || answer.__choice !== 'ok') return;
  const name = (answer.name || '').trim();
  if (!name) { toast('请填写文件名', 'warn'); return; }
  try {
    const res = await api('/api/folder/create', { root: state.rid, name: name });
    await refreshFolderTree(true, res.path);
    toast('已创建 ' + res.doc.file, 'ok');
  } catch (e) { toast(e.message, 'err', '新建失败'); }
}

async function openRootDoc(did) {
  if (!state.rid || !did) return;
  try {
    const data = await api('/api/folder/doc?root=' + encodeURIComponent(state.rid) + '&doc=' + encodeURIComponent(did));
    // 身份仍用绝对路径，与桌面端、最近列表共用一个已打开文档
    await openLooseFiles([data.path]);
  } catch (e) { toast(e.message, 'err', '打开失败'); }
}

async function removeRoot() {
  if (!state.rid) return;
  const root = rootById(state.rid) || {};
  const answer = await modal({
    title: '从列表移除',
    hint: '只从 MDReader 的列表里移除「' + esc(root.name || '') + '」，磁盘上的文件夹和文件都不会被删除。',
    choices: [{ label: '取消', value: '' }, { label: '从列表移除', value: 'ok', primary: true }],
  });
  if (!answer || answer.__choice !== 'ok') return;
  try {
    const res = await api('/api/folder/remove', { root: state.rid });
    state.roots = res.roots || [];
    state.rid = null;
    state.rootDocs = [];
    localStorage.removeItem('mdreader.root');
    renderRootSelect();
    renderFolderMeta();
    renderRootTree();
    toast('已从列表移除，磁盘文件没有改动', 'ok');
  } catch (e) { toast(e.message, 'err', '移除失败'); }
}

function folderTick() {
  if (!state.rid || document.hidden) return;
  refreshFolderTree(false);
}

function mountFolderWatch() {
  if (mountFolderWatch.started) return;
  mountFolderWatch.started = true;
  // 每 1.5 秒问一次核心；核心只做浅层探测，内容没变就不重画树
  setInterval(folderTick, 1500);
}

/* ------------------------------------------------------------ selection */
function projectById(id) { return state.projects.find(p => p.id === id) || null; }

async function loadState() {
  const st = await api('/api/state');
  state.version = st.version; state.engine = st.engine;
  state.workspace = st.workspace; state.projects = st.projects || [];
  try { const m = await api('/api/mode'); state.mode = m.mode || 'server'; } catch (e) { state.mode = 'server'; }
  $('#versionTag').textContent = 'v' + st.version;
  renderProjectSelect();
  state.roots = st.root_folders || [];
  renderRootSelect();
  renderFolderMeta();
  renderRootTree();
  await refreshRoots();
  mountFolderWatch();
  if (state.projects.length) {
    const wanted = localStorage.getItem('mdreader.pid');
    const pid = projectById(wanted) ? wanted : state.projects[0].id;
    await selectProject(pid, localStorage.getItem('mdreader.doc'));
  } else {
    renderWelcome();
  }
}

function renderProjectSelect() {
  const sel = $('#projectSelect');
  sel.innerHTML = state.projects.map(p =>
    '<option value="' + esc(p.id) + '">' + esc(p.name) + ' · ' + p.doc_count + ' 篇</option>').join('');
  if (state.pid) sel.value = state.pid;
}

async function selectProject(pid, wantDoc, opts) {
  if (!await confirmLeaveDocument()) {
    renderProjectSelect();
    return false;
  }
  const data = await api('/api/project?pid=' + encodeURIComponent(pid));
  state.pid = pid;
  localStorage.setItem('mdreader.pid', pid);
  state.project = data.project;
  state.docs = data.docs || [];
  state.dirs = data.dirs || [];
  state.active = null; state.doc = null; state.dirty = false;
  document.body.classList.remove('dirty');
  renderProjectSelect();
  renderProjectMeta();
  renderTree();
  renderRecent();
  setButtons();
  $('#hits').style.display = 'none';

  if (opts && opts.silent) return true;      // caller decides what to open
  const target = (wantDoc && state.docs.some(d => d.id === wantDoc))
    ? wantDoc
    : (state.docs.length ? state.docs[0].id : null);
  if (target) await openDoc(target);
  else renderEmptyProject();
  return true;
}

function renderProjectMeta() {
  const p = state.project || {};
  const chars = state.docs.reduce((a, d) => a + (d.size || 0), 0);
  $('#projectMeta').innerHTML =
    '<span>' + state.docs.length + ' 篇文档</span>' +
    '<span>' + fmtSize(chars) + '</span>' +
    '<span>更新 ' + esc(relTime(p.updated)) + '</span>';
  $('#docCount').textContent = state.docs.length;
}

function fmtSize(n) {
  if (n < 1024) return n + ' B';
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
  return (n / 1048576).toFixed(2) + ' MB';
}

function relTime(iso) {
  if (!iso) return '—';
  const t = Date.parse(iso);
  if (isNaN(t)) return iso;
  const d = (Date.now() - t) / 1000;
  if (d < 60) return '刚刚';
  if (d < 3600) return Math.floor(d / 60) + ' 分钟前';
  if (d < 86400) return Math.floor(d / 3600) + ' 小时前';
  if (d < 86400 * 30) return Math.floor(d / 86400) + ' 天前';
  return new Date(t).toLocaleDateString('zh-CN');
}

/* ----------------------------------------------------------------- tree */
function buildTree() {
  const root = { dirs: {}, files: [] };
  state.docs.forEach(d => {
    const parts = (d.dir || '').split('/').filter(Boolean);
    let node = root;
    parts.forEach(p => { node = (node.dirs[p] = node.dirs[p] || { dirs: {}, files: [] }); });
    node.files.push(d);
  });
  return root;
}

function renderTree() {
  const host = $('#tree');
  const root = buildTree();
  if (!state.docs.length) {
    host.innerHTML = '<div class="tree-empty">这个项目还没有文档<br>点上方 ＋ 新建，或拖入 .md 文件</div>';
    return;
  }
  host.innerHTML = renderNode(root, '');
  $$('.tree-dir', host).forEach(el => el.onclick = () => {
    const p = el.dataset.dir;
    state.openDirs[p] = !state.openDirs[p];
    renderTree();
  });
  $$('[data-new]', host).forEach(btn => btn.onclick = ev => {
    ev.stopPropagation();                          // 不让点击落到目录行的展开/收起上
    newUntitledIn(btn.dataset.new);
  });
  $$('.tree-item', host).forEach(el => {
    el.onclick = ev => {
      if (ev.target.closest('.ti-act')) return;
      openDoc(el.dataset.id);
    };
    const ren = $('[data-act=rename]', el), del = $('[data-act=del]', el);
    if (ren) ren.onclick = ev => { ev.stopPropagation(); renameDoc(el.dataset.id); };
    if (del) del.onclick = ev => { ev.stopPropagation(); deleteDoc(el.dataset.id); };
  });
}

function renderNode(node, prefix, mode) {
  const root = mode === 'root';
  let html = '';
  const dirNames = Object.keys(node.dirs).sort((a, b) => a.localeCompare(b, 'zh'));
  dirNames.forEach(name => {
    const path = prefix ? prefix + '/' + name : name;
    const open = root
      ? (state.foldersOpen[path] !== undefined ? state.foldersOpen[path] : false)
      : (state.openDirs[path] !== undefined ? state.openDirs[path] : false);
    const count = countDocs(node.dirs[name]);
    html += '<div class="tree-dir' + (open ? ' open' : '') + '" data-dir="' + esc(path) + '">' +
              '<span class="caret">▶</span><span>📁 ' + esc(name) + '</span>' +
              '<span style="margin-left:auto;font-size:10.5px">' + count + '</span>' +
              '<button class="btn ghost sm ti-plus" data-new="' + esc(path) +
                '" title="在这个目录里新建 Untitled.md">＋</button>' +
            '</div>';
    if (open) html += '<div class="tree-children">' + renderNode(node.dirs[name], path, mode) + '</div>';
  });
  node.files.forEach(f => {
    const active = f.id === state.active ? ' active' : '';
    // 原文件夹树：只提供「删除本地文件」；项目树仍保留重命名 + 删除
    const acts = root
      ? '<button class="btn" data-act="del" title="删除本地文件…">🗑</button>'
      : '<button class="btn" data-act="rename" title="重命名">✎</button>' +
        '<button class="btn" data-act="del" title="删除">🗑</button>';
    html += '<div class="tree-item' + active + '" data-id="' + esc(f.id) + '" title="' + esc(f.id) + '">' +
              '<span>📄</span>' +
              '<span class="ti-name">' + esc(f.name) + '</span>' +
              '<span class="ti-meta">' + esc(f.mtime.slice(5, 16)) + '</span>' +
              '<span class="ti-act">' + acts + '</span>' +
            '</div>';
  });
  return html;
}

function countDocs(node) {
  let n = node.files.length;
  Object.values(node.dirs).forEach(d => { n += countDocs(d); });
  return n;
}

/* -------------------------------------------------------------- document */
async function confirmLeaveDocument() {
  if (state.dirty) {
    const ok = await modal({
      title: '有未保存的修改',
      hint: '切换文档会丢失这些修改。',
      choices: [
        { label: '保存并切换', value: 'save' },
        { label: '放弃修改', value: 'drop', primary: false },
      ],
    });
    if (!ok) return false;
    if (ok.__choice === 'save') return await saveDoc();
  }
  return true;
}

async function openDoc(id, opts) {
  if (!id) return false;
  if (!(opts && opts.force) && !await confirmLeaveDocument()) return false;
  const data = await api('/api/doc?pid=' + encodeURIComponent(state.pid) + '&doc=' + encodeURIComponent(id));
  state.doc = data;
  state.active = id;
  state.dirty = false;
  localStorage.setItem('mdreader.doc', id);
  localStorage.setItem('mdreader.kind', 'project');
  document.body.classList.remove('dirty');
  renderTree();
  renderRecent();
  renderDocument();
  setButtons();
  afterDocumentShown();
  return true;
}

/* After a document is shown: back to where the reader stopped, and keep the
   find bar meaningful without stealing focus. */
function afterDocumentShown() {
  restoreReadingPosition();
  try {
    if (!$('#findBar').hidden && $('#findInput').value) runFind(false);
  } catch (e) { /* head-less callers have no find bar */ }
  try { $('#outlinePanel').hidden = true; } catch (e) { /* same */ }
}

function renderDocument() {
  state.findNeedle = '';
  state.findMarks = [];
  state.findIndex = -1;
  state.outline = [];
  const d = state.doc;
  if (!d) return renderWelcome();
  return d.kind === 'loose' ? renderLooseDocument(d) : renderProjectDocument(d);
}

function renderProjectDocument(d) {
  const meta = d.meta || {};
  const chips = Object.keys(meta).slice(0, 8).map(k =>
    '<span class="chip"><b>' + esc(k) + '</b>' + esc(meta[k]) + '</span>').join('');
  const idx = state.docs.findIndex(x => x.id === d.info.id);
  const prev = idx > 0 ? state.docs[idx - 1] : null;
  const next = idx >= 0 && idx < state.docs.length - 1 ? state.docs[idx + 1] : null;
  const s = d.stats || {};

  $('#renderHost').innerHTML =
    '<div class="statline">' +
      '<span>' + esc(d.info.dir || '根目录') + '</span>' +
      '<span>' + s.lines + ' 行</span>' +
      '<span>' + s.chars + ' 字符</span>' +
      '<span>' + s.headings + ' 个标题</span>' +
      '<span>更新 ' + esc(d.info.mtime) + '</span>' +
      (d.engine ? '<span>引擎 ' + esc(d.engine) + '</span>' : '') +
    '</div>' +
    '<article class="markdown-body">' +
      (chips ? '<div class="meta-chips">' + chips + '</div>' : '') +
      '<h1 class="doc-title">' + esc(d.title || d.info.name) + '</h1>' +
      '<div class="doc-sub">' + esc(d.info.id) + '</div>' +
      sanitize(d.html) +
    '</article>' +
    '<footer class="doc-foot" style="max-width:none;margin:0;padding:18px 40px 40px">' +
      '<span>' + (prev ? '<a href="#" data-goto="' + esc(prev.id) + '">← ' + esc(prev.name) + '</a>' : '') + '</span>' +
      '<span>' + (next ? '<a href="#" data-goto="' + esc(next.id) + '">' + esc(next.name) + ' →</a>' : '') + '</span>' +
    '</footer>';

  $('#crumbs').innerHTML =
    '<span>' + esc(state.project ? state.project.name : '') + '</span>' +
    (d.info.dir ? '<span class="sep">/</span><span>' + esc(d.info.dir) + '</span>' : '') +
    '<span class="sep">/</span><span class="cur">' + esc(d.info.name) + '</span>';

  $('#editor').value = d.raw;
  $('#editorInfo').textContent = d.info.id + ' · 已保存 ' + d.info.mtime;
  wireRendered();
}

/* the reading view for a file that lives outside the workspace */
function renderLooseDocument(d) {
  const meta = d.meta || {};
  const chips = Object.keys(meta).slice(0, 8).map(k =>
    '<span class="chip"><b>' + esc(k) + '</b>' + esc(meta[k]) + '</span>').join('');
  const s = d.stats || {};
  const isDraft = !d.info.path;
  const others = (state.recent || []).filter(r => r.id !== d.info.id);

  $('#renderHost').innerHTML =
    '<div class="statline loose">' +
      '<span class="tag">临时查看</span>' +
      (isDraft ? '<span>尚未保存到磁盘</span>'
               : '<span>更新 ' + esc(d.info.mtime) + '</span>') +
      '<span>' + s.lines + ' 行</span>' +
      '<span>' + s.chars + ' 字符</span>' +
      '<span>' + s.headings + ' 个标题</span>' +
      (d.engine ? '<span>引擎 ' + esc(d.engine) + '</span>' : '') +
    '</div>' +
    '<div class="loose-bar">' +
      '<span class="loose-path" title="' + esc(d.info.path || '') + '">' +
        (isDraft ? '新文档（未选择保存位置）' : esc(d.info.path)) + '</span>' +
      '<span class="loose-actions">' +
        '<button class="btn sm" data-loose-action="save">💾 保存回原文件</button>' +
        '<button class="btn sm" data-loose-action="saveas">另存为…</button>' +
        '<button class="btn sm" data-loose-action="toProject">＋ 加入项目</button>' +
        (isDraft ? '' : '<button class="btn sm" data-loose-action="reveal">🗀 定位文件</button>') +
        (isDraft ? '' : '<button class="btn sm" data-loose-action="forget">删除打开记录</button>') +
      '</span>' +
    '</div>' +
    '<article class="markdown-body">' +
      (chips ? '<div class="meta-chips">' + chips + '</div>' : '') +
      '<h1 class="doc-title">' + esc(d.title || d.info.name) + '</h1>' +
      (isDraft ? '' : '<div class="doc-sub">' + esc(d.info.file) + '</div>') +
      sanitize(d.html) +
    '</article>' +
    '<footer class="doc-foot" style="max-width:none;margin:0;padding:18px 40px 40px">' +
      '<span>' + (others.length
        ? '最近打开：' + others.slice(0, 4).map(r =>
            '<a href="#" data-loose-open="' + esc(r.id) + '">' + esc(r.name) + '</a>').join(' · ')
        : '修改后按 Ctrl + S 直接写回原文件') + '</span>' +
    '</footer>';

  $('#crumbs').innerHTML =
    '<span>临时查看</span><span class="sep">/</span>' +
    '<span class="cur">' + esc(d.info.name) + '</span>';

  $('#editor').value = d.raw;
  $('#editorInfo').textContent = (isDraft ? d.info.id : d.info.path) +
    (isDraft ? ' · 未保存' : ' · 已保存 ' + d.info.mtime);

  $$('[data-loose-action]', $('#renderHost')).forEach(btn => btn.onclick = () => {
    const action = btn.dataset.looseAction;
    if (action === 'save') saveDoc();
    else if (action === 'saveas') saveLooseAs();
    else if (action === 'toProject') looseToProject();
    else if (action === 'reveal') {
      api('/api/loose/reveal', { path: d.info.path }).catch(e => toast(e.message, 'err', '定位失败'));
    } else if (action === 'forget') {
      if (!d.info.path) { state.active = null; state.doc = null; renderWelcome(); setButtons(); }
      else forgetLoose(d.info.path);
    }
  });
  $$('[data-loose-open]', $('#renderHost')).forEach(a => a.onclick = ev => {
    ev.preventDefault();
    openLooseDoc(a.dataset.looseOpen);
  });
  wireRendered();
}

/* strip anything active out of user markdown before injecting it */
function sanitize(html) {
  const tpl = document.createElement('template');
  tpl.innerHTML = html;
  tpl.content.querySelectorAll('script,style,link,meta,base,object,embed,form,iframe[src^="javascript:"]')
    .forEach(n => n.remove());
  tpl.content.querySelectorAll('*').forEach(el => {
    Array.from(el.attributes).forEach(a => {
      const n = a.name.toLowerCase(), v = (a.value || '').trim().toLowerCase();
      if (n.startsWith('on')) el.removeAttribute(a.name);
      if ((n === 'href' || n === 'src' || n === 'xlink:href') && v.startsWith('javascript:')) el.removeAttribute(a.name);
      if (n === 'style' && /expression|url\s*\(\s*['"]?\s*javascript/i.test(v)) el.removeAttribute(a.name);
    });
    if (el.tagName === 'A' && el.getAttribute('target') === '_blank') el.setAttribute('rel', 'noopener');
  });
  return tpl.innerHTML;
}

function wireRendered() {
  const host = $('#renderHost');
  $$('a[data-md-doc]', host).forEach(a => a.onclick = ev => {
    ev.preventDefault();
    openDoc(a.dataset.mdDoc);
  });
  $$('a[data-goto]', host).forEach(a => a.onclick = ev => {
    ev.preventDefault();
    openDoc(a.dataset.goto);
  });
  $$('a[href^="http"]', host).forEach(a => { a.target = '_blank'; a.rel = 'noopener'; });
  $$('[data-copy]', host).forEach(btn => btn.onclick = () => {
    const code = btn.closest('.code-wrap').querySelector('code');
    const text = code ? code.innerText : '';
    navigator.clipboard.writeText(text).then(
      () => { btn.textContent = '已复制'; setTimeout(() => btn.textContent = '复制', 1400); },
      () => toast('复制失败，请手动选择代码', 'warn')
    );
  });
}

function renderWelcome() {
  state.doc = null;
  $('#renderHost').innerHTML =
    '<div class="doc-shell" style="border:0;background:transparent">' +
    '<div class="welcome">' +
      '<h1>MDReader</h1>' +
      '<p>把机器可读的 Markdown 变成适合人阅读的排版；散落的 .md 可以收进项目统一管理，' +
      '也可以直接拖进来就地读、就地改。</p>' +
      '<div class="cards">' +
        '<div class="card" style="cursor:pointer" id="welcomeOpen"><b>🗎 拖个 .md 进来 / 打开本地文件</b>' +
          '<span>临时查看：不复制、不导入，<code>Ctrl + S</code> 直接写回原来那个文件。</span></div>' +
        '<div class="card"><b>① 新建项目</b><span>在 <code>~/MDReader/projects</code> 下生成独立文件夹，含 <code>docs/</code> 与 <code>assets/</code>。</span></div>' +
        '<div class="card"><b>② 收拢文档</b><span>拖入整个文件夹即导入，目录结构原样保留。</span></div>' +
        '<div class="card"><b>③ 阅读 / 编辑</b><span>阅读视图自动排版；切到源码视图可直接改并保存。</span></div>' +
        '<div class="card"><b>④ 导出分享</b><span>导出单篇或整本为离线单文件 HTML，可打印成 PDF。</span></div>' +
      '</div>' +
      '<p style="margin-top:26px">快捷键：<kbd>Ctrl</kbd>+<kbd>S</kbd> 保存 · <kbd>Ctrl</kbd>+<kbd>B</kbd> 侧栏 · <kbd>Ctrl</kbd>+<kbd>N</kbd> 新建文档 · <kbd>Shift</kbd>+拖入 = 导入项目</p>' +
      (state.projects.length ? '' : '<p style="margin-top:16px"><button class="btn primary" onclick="document.getElementById(\'btnNewProject\').click()">创建第一个项目</button></p>') +
    '</div></div>';
  $('#crumbs').innerHTML = '<span class="cur">未打开项目</span>';
  $('#editor').value = '';
  $('#editorInfo').textContent = '';
  const open = $('#welcomeOpen');
  if (open) open.onclick = () => openLocalFiles();
}

/* 打开本地文件：桌面模式用系统对话框（能拿到真实路径，可原地编辑），
   浏览器模式退回到文件选择框（只能导入到项目）。 */
async function openLocalFiles() {
  if (state.mode === 'server') {
    try {
      const dlg = await api('/api/dialog/open', { multi: true });
      if (!dlg.paths || !dlg.paths.length) return false;
      return await openLooseFiles(dlg.paths);
    } catch (e) {
      toast(e.message, 'err', '打开失败');
      return false;
    }
  }
  pickLooseBrowserFiles();
  return false;
}

/* 浏览器里拿不到路径，只能请用户选择项目后再上传 */
function pickLooseBrowserFiles() {
  const inp = document.createElement('input');
  inp.type = 'file';
  inp.multiple = true;
  inp.accept = '.md,.markdown,.mdown,.mkd,.txt,text/markdown,text/plain';
  inp.onchange = async () => {
    const files = Array.from(inp.files || []);
    if (!files.length) return;
    if (!state.pid) {
      toast('浏览器模式无法就地编辑：请先选择项目，文件会导入到项目里', 'warn');
      return;
    }
    await uploadFiles(files);
  };
  inp.click();
}

function renderEmptyProject() {
  state.doc = null;
  const p = state.project || {};
  $('#renderHost').innerHTML =
    '<div class="doc-shell" style="border:0;background:transparent"><div class="welcome">' +
      '<h1>' + esc(p.name || '') + '</h1>' +
      '<p>' + (p.description ? esc(p.description) : '这个项目还是空的。') + '</p>' +
      '<p>用下面的按钮加入第一份文档：</p>' +
      '<div class="cards">' +
        '<div class="card" style="cursor:pointer" id="emptyNew"><b>＋ 新建文档</b><span>从空白 .md 开始写</span></div>' +
        '<div class="card" style="cursor:pointer" id="emptyImport"><b>⬆ 导入已有 .md</b><span>可多选，也可选整个文件夹</span></div>' +
      '</div>' +
      '<p style="margin-top:22px;font-size:12.5px">项目目录：<code>' + esc(p.id ? state.workspace + '\\projects\\' + p.id : '') + '</code></p>' +
    '</div></div>';
  $('#crumbs').innerHTML = '<span class="cur">' + esc(p.name || '') + '</span>';
  $('#editor').value = '';
  $('#editorInfo').textContent = '';
  const a = $('#emptyNew'); if (a) a.onclick = newDoc;
  const b = $('#emptyImport'); if (b) b.onclick = importFiles;
}

/* ---------------------------------------------------------------- actions */
function setButtons() {
  const has = !!state.active;
  ['btnSave', 'btnExport', 'btnRenameDoc', 'btnDeleteDoc'].forEach(id => {
    $('#' + id).disabled = !has;
  });
  $('#btnExportAll').disabled = !state.pid;
  renderPlugins();          // 插图/导出入口跟着当前文档与插件状态走
}

function newProject() {
  modal({
    title: '新建项目',
    hint: '会在工作区 <code>' + esc(state.workspace) + '\\projects</code> 下创建独立文件夹，并附带一篇起始说明文档。',
    fields: [
      { name: 'name', label: '项目名称', placeholder: '例如：产品需求 / 论文笔记' },
      { name: 'description', label: '一句话描述（可留空）', placeholder: '这个项目打算放什么' },
    ],
  }).then(async r => {
    if (!r || !r.name) return;
    try {
      const res = await api('/api/project/create', { name: r.name, description: r.description || '' });
      toast('项目已创建：' + res.path, 'ok');
      await loadState();
      await selectProject(res.id);
      const first = state.docs[0];
      if (first) openDoc(first.id, { force: true });
    } catch (e) { toast(e.message, 'err', '创建失败'); }
  });
}

function renameProject() {
  if (!state.project) return;
  modal({
    title: '项目改名',
    hint: '只改显示名称，磁盘上的文件夹名保持不变，已有链接不会失效。',
    fields: [{ name: 'name', label: '新名称', value: state.project.name }],
  }).then(async r => {
    if (!r || !r.name) return;
    try {
      await api('/api/project/rename', { pid: state.pid, name: r.name });
      toast('已改名', 'ok');
      await loadState(); await selectProject(state.pid, state.active);
    } catch (e) { toast(e.message, 'err', '改名失败'); }
  });
}

async function deleteProject() {
  if (!state.project) return;
  const r = await modal({
    title: '删除项目“' + state.project.name + '”',
    hint: '默认移到 Windows 回收站，可以还原。',
    choices: [
      { label: '移到回收站', value: 'recycle', primary: true },
      { label: '彻底删除目录', value: 'purge' },
    ],
  });
  if (!r) return;
  try {
    await api('/api/project/delete', { pid: state.pid, recycle: r.__choice !== 'purge' });
    toast('项目已删除', 'ok');
    state.pid = null; state.active = null; state.doc = null;
    localStorage.removeItem('mdreader.pid');
    await loadState();
  } catch (e) { toast(e.message, 'err', '删除失败'); }
}

/* 目录行末尾的「＋」：不弹对话框，直接在该目录生成 Untitled.md 并打开。
   重名由服务端顺延为 Untitled-2.md，绝不覆盖已有文件。 */
async function newUntitledIn(dir) {
  if (!state.pid) { toast('请先创建或选择项目', 'warn'); return false; }
  try {
    const res = await api('/api/doc/create', { pid: state.pid, name: 'Untitled', dir: dir || '' });
    await refreshProject();
    await openDoc(res.doc.id, { force: true });
    toast('已创建 ' + res.doc.id, 'ok');
    return true;
  } catch (e) { toast(e.message, 'err', '新建失败'); return false; }
}

async function newUntitledInRoot(subdir) {
  if (!state.rid) { toast('请先打开一个文件夹', 'warn'); return false; }
  try {
    const res = await api('/api/folder/create', { root: state.rid, name: 'Untitled', dir: subdir || '',
                                                 unique: true });
    await refreshFolderTree(true, res.path);
    toast('已创建 ' + res.doc.file, 'ok');
    return true;
  } catch (e) { toast(e.message, 'err', '新建失败'); return false; }
}

function newDoc() {
  if (!state.pid) return toast('请先创建或选择项目', 'warn');
  const dirs = ['（项目根目录）'].concat(state.dirs.filter(Boolean));
  modal({
    title: '新建文档',
    hint: '可选目录：<code>' + esc(dirs.join(' · ')) + '</code>',
    fields: [
      { name: 'name', label: '文件名', placeholder: '例如：需求说明（不用写 .md）' },
      { name: 'dir', label: '放入子目录（留空=根目录）', placeholder: '例如：docs' },
    ],
  }).then(async r => {
    if (!r || !r.name) return;
    try {
      const res = await api('/api/doc/create', { pid: state.pid, name: r.name, dir: r.dir || '' });
      await refreshProject();
      await openDoc(res.doc.id, { force: true });
      switchMode('source');
      toast('已创建 ' + res.doc.id, 'ok');
    } catch (e) { toast(e.message, 'err', '创建失败'); }
  });
}

async function renameDoc(id) {
  const d = state.docs.find(x => x.id === id);
  if (!d) return;
  const r = await modal({
    title: '重命名文档',
    fields: [{ name: 'name', label: '新文件名', value: d.name }],
  });
  if (!r || !r.name) return;
  try {
    const res = await api('/api/doc/rename', { pid: state.pid, doc: id, name: r.name });
    if (state.active === id) { state.active = res.doc.id; localStorage.setItem('mdreader.doc', res.doc.id); }
    await refreshProject();
    if (state.active) await openDoc(state.active, { force: true });
    toast('已重命名', 'ok');
  } catch (e) { toast(e.message, 'err', '重命名失败'); }
}

async function deleteDoc(id) {
  const d = state.docs.find(x => x.id === id);
  if (!d) return;
  const r = await modal({
    title: '删除“' + d.name + '”',
    hint: '默认移到 Windows 回收站，可以还原。',
    choices: [
      { label: '移到回收站', value: 'recycle', primary: true },
      { label: '彻底删除', value: 'purge' },
    ],
  });
  if (!r) return;
  try {
    await api('/api/doc/delete', { pid: state.pid, doc: id, recycle: r.__choice !== 'purge' });
    if (state.active === id) { state.active = null; state.doc = null; state.dirty = false;
                               document.body.classList.remove('dirty'); }
    await refreshProject();
    toast('已删除', 'ok');
  } catch (e) { toast(e.message, 'err', '删除失败'); }
}

async function saveDoc() {
  if (!state.active) return false;
  // a loose document goes back to its own file; everything else is a project doc
  if (state.doc && state.doc.kind === 'loose') return await saveLooseDoc();
  if (!state.pid) return false;
  const pid = state.pid, docId = state.active;
  const content = $('#editor').value;
  try {
    // Send the revision the page was shown: an external edit must conflict
    // instead of being overwritten.
    await api('/api/doc/save', { pid, doc: docId, content,
                                 expected: (state.doc && state.doc.info && state.doc.info.revision) || undefined });
    const res = await api('/api/doc?pid=' + encodeURIComponent(pid) + '&doc=' + encodeURIComponent(docId));
    if (state.pid !== pid || state.active !== docId || $('#editor').value !== content) {
      toast('已保存提交时的内容；当前编辑内容仍保留。', 'ok');
      return false;
    }
    state.doc = res;
    state.dirty = false;
    document.body.classList.remove('dirty');
    discardRecovery(content);
    renderDocument();
    await refreshProject(true);
    toast('已保存 ' + state.active, 'ok');
    return true;
  } catch (e) { toast(e.message, 'err', '保存失败'); return false; }
}

async function refreshProject(quiet) {
  if (!state.pid) return;
  const data = await api('/api/project?pid=' + encodeURIComponent(state.pid));
  state.project = data.project;
  state.docs = data.docs || [];
  state.dirs = data.dirs || [];
  renderProjectMeta();
  renderTree();
  const sel = $('#projectSelect');
  const opt = Array.from(sel.options).find(o => o.value === state.pid);
  if (opt) {
    const p = projectById(state.pid);
    if (p) { p.doc_count = state.docs.length; opt.textContent = p.name + ' · ' + state.docs.length + ' 篇'; }
  }
}

/* re-read everything from disk (the .md files are editable by other tools) */
async function refreshFromDisk() {
  if (!state.pid) return;
  try {
    await refreshProject();
    const stillThere = state.docs.some(d => d.id === state.active);
    if (!state.active || !stillThere) {
      if (state.docs.length) await openDoc(state.docs[0].id, { force: true });
      else renderEmptyProject();
      toast('磁盘内容已重新读取', 'ok');
      return;
    }
    if (state.dirty) {
      toast('项目已刷新。当前文档有未保存的修改，未从磁盘覆盖。', 'warn');
      return;
    }
    await openDoc(state.active, { force: true });
    toast('已从磁盘重新读取', 'ok');
  } catch (e) { toast(e.message, 'err', '刷新失败'); }
}

/* ---------------------------------------------------------------- import */
async function importFiles() {
  if (!state.pid) return toast('请先创建或选择项目', 'warn');
  if (state.mode === 'server') {
    try {
      const dlg = await api('/api/dialog/files', { multi: true });
      if (!dlg.paths.length) return;
      const res = await api('/api/import/paths', { pid: state.pid, paths: dlg.paths, recursive: false });
      afterImport(res);
    } catch (e) { toast(e.message, 'err', '导入失败'); }
  } else {
    pickBrowserFiles(false);
  }
}

async function importDir() {
  if (!state.pid) return toast('请先创建或选择项目', 'warn');
  if (state.mode === 'server') {
    try {
      const dlg = await api('/api/dialog/folder');
      if (!dlg.paths.length) return;
      busy(true, '正在复制文件夹…');
      const res = await api('/api/import/paths', { pid: state.pid, paths: dlg.paths, recursive: true });
      afterImport(res);
    } catch (e) { toast(e.message, 'err', '导入失败'); }
  } else {
    pickBrowserFiles(true);
  }
}

function pickBrowserFiles(folder) {
  const inp = document.createElement('input');
  inp.type = 'file';
  inp.multiple = true;
  inp.accept = '.md,.markdown,.mdown,.mkd,.txt,text/markdown,text/plain';
  if (folder && inp.webkitdirectory !== undefined) inp.webkitdirectory = true;
  inp.onchange = () => uploadFiles(Array.from(inp.files || []));
  inp.click();
}

async function uploadFiles(files) {
  const md = files.filter(f => DOC_EXT.test(f.name) || DOC_EXT.test(f.webkitRelativePath || ''));
  if (!md.length) return toast('没有找到 Markdown 文件', 'warn');
  busy(true, '正在上传 ' + md.length + ' 个文件…');
  try {
    const payload = [];
    for (const f of md) {
      const buf = await f.arrayBuffer();
      payload.push({ name: stripCommonRoot(f.webkitRelativePath || f.name), b64: b64(buf) });
    }
    const res = await api('/api/import/upload', { pid: state.pid, files: payload });
    afterImport(res);
  } catch (e) { toast(e.message, 'err', '上传失败'); }
}

function stripCommonRoot(path) {
  const parts = String(path).replace(/\\/g, '/').split('/').filter(Boolean);
  if (parts.length > 1 && !DOC_EXT.test(parts[0])) return parts.slice(1).join('/');
  return parts.join('/');
}

function b64(buf) {
  const bytes = new Uint8Array(buf);
  let s = '';
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    s += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
  }
  return btoa(s);
}

async function afterImport(res) {
  const n = res.count || 0;
  const skipped = (res.skipped || []).length;
  toast('导入 ' + n + ' 个文件' + (skipped ? '，跳过 ' + skipped + ' 个' : ''), n ? 'ok' : 'warn');
  await refreshProject();
  if (n && res.added && res.added.length) await openDoc(res.added[0], { force: true });
  else if (!n) renderTree();
}

/* ================================================================ plugins */
/* 插件的可用状态由服务端保存（桌面端与这里读同一份），页面只展示清单里
   校验过的命令；禁用后服务端会直接拒绝调用，不靠界面藏按钮。 */
function pluginCommands(capability) {
  return (state.pluginCommands || []).filter(c => !capability || c.capability === capability);
}

function pluginById(id) { return (state.plugins || []).find(p => p.id === id) || null; }

/* 当前文档的宿主身份：临时查看/文件夹用绝对路径，项目文档用 pid + 文档 id。 */
function pluginTarget() {
  const doc = state.doc || {};
  if (doc.kind === 'folder') {
    const path = doc.path || (doc.identity || '');
    if (!path) return null;
    return { doc: path, revision: (doc.info && doc.info.revision) || '' };
  }
  if (doc.kind === 'loose') {
    const path = (doc.info && doc.info.path) || '';
    if (!path) return null;
    return { doc: path, revision: (doc.info && doc.info.revision) || '' };
  }
  if (state.pid && state.active) return { pid: state.pid, doc: state.active };
  return null;
}

async function refreshPlugins() {
  let data;
  try { data = await api('/api/plugins'); } catch (e) { return; }
  state.plugins = data.plugins || [];
  state.pluginCommands = data.commands || [];
  renderPlugins();
}

function renderPlugins() {
  const rows = state.plugins || [];
  const commands = state.pluginCommands || [];
  const count = $('#pluginCount');
  if (count) count.textContent = String(commands.length);
  const meta = $('#pluginMeta');
  if (meta) {
    const enabled = rows.filter(r => r.enabled);
    const waiting = rows.filter(r => r.state === 'not-installed' || r.state === 'unavailable');
    const chips = [];
    if (enabled.length) chips.push('<span>已启用：' + esc(enabled.map(r => r.name).join('、')) + '</span>');
    if (!enabled.length) chips.push('<span>还没有启用插件；核心功能不依赖插件</span>');
    if (waiting.length) chips.push('<span>' + waiting.length + ' 个插件未安装或不可用</span>');
    meta.innerHTML = chips.join('');
  }
  const images = pluginCommands('editor.image_insert');
  const insertBtn = $('#btnInsertImage');
  if (insertBtn) {
    insertBtn.disabled = !images.length || !pluginTarget();
    insertBtn.title = images.length ? '用 ' + images[0].plugin_name + ' 插入本地图片'
                                    : '需要先安装并启用图片插入插件';
  }
  const exports = pluginCommands('export.format');
  const select = $('#exportFormat');
  const exportBtn = $('#btnExportPlugin');
  if (select && exportBtn) {
    select.innerHTML = exports.map(c =>
      '<option value="' + esc(c.command) + '">' + esc(c.title) + '（.' + esc(c.extension) + '）</option>').join('');
    select.style.display = exports.length ? '' : 'none';
    exportBtn.style.display = exports.length ? '' : 'none';
  }
}

/* 插件管理：安装本地官方插件包、启用/禁用、卸载，并显示失败原因。 */
function pluginStateLabel(state) {
  const labels = {
    enabled: '已启用', disabled: '已禁用', unavailable: '不可用',
    broken: '清单损坏', restart: '重启后生效', 'not-installed': '未安装',
  };
  return labels[state] || state;
}

/* 一行插件：名称、版本、状态、原因、依赖、命令与可用操作。 */
function pluginRowHtml(row) {
  const actions = [];
  if (row.installed) {
    actions.push('<button class="btn sm" data-plugin-toggle="' + esc(row.id) + '" data-enabled="' +
                 (row.enabled ? '0' : '1') + '">' + (row.enabled ? '禁用' : '启用') + '</button>');
    actions.push('<button class="btn sm danger" data-plugin-remove="' + esc(row.id) + '">卸载</button>');
  }
  return '<div class="plugin-row">' +
    '<div class="plugin-head"><b>' + esc(row.name) + '</b>' +
      '<span class="plugin-version">' + esc(row.version || '—') + '</span>' +
      '<span class="plugin-state s-' + esc(row.state) + '">' +
        esc(pluginStateLabel(row.state)) + '</span></div>' +
    '<div class="plugin-note">' + esc(row.reason || row.description || '') + '</div>' +
    (row.dependencies && row.dependencies.length
      ? '<div class="plugin-note">依赖：' + esc(row.dependencies.map(d => d.name).join('、')) + '</div>' : '') +
    (row.commands && row.commands.length
      ? '<div class="plugin-note">命令：' + esc(row.commands.map(c => c.title).join('、')) + '</div>' : '') +
    (actions.length ? '<div class="plugin-actions">' + actions.join('') + '</div>' : '') +
    '</div>';
}

function pluginListHtml(rows) {
  return (rows || []).map(pluginRowHtml).join('') ||
    '<div class="tree-empty">还没有安装任何插件</div>';
}

function pluginManager() {
  const ov = $('#overlay');
  const rows = state.plugins || [];
  ov.innerHTML =
    '<div class="modal wide" role="dialog">' +
      '<h3>插件管理</h3>' +
      '<p class="hint">首版只接受维护者发布、并在受信清单里登记过 SHA256 的本地插件包；' +
      '插件代码在独立进程里执行，产物路径与类型由程序重新校验。</p>' +
      '<div class="plugin-list">' + pluginListHtml(rows) + '</div>' +
      '<div class="actions">' +
        '<button class="btn" data-plugin-install="1">安装本地插件包…</button>' +
        '<button class="btn" data-choice="__cancel">关闭</button>' +
      '</div>' +
    '</div>';
  ov.classList.add('on');
  const close = () => { ov.classList.remove('on'); ov.innerHTML = ''; };
  ov.onclick = e => { if (e.target === ov) close(); };
  $$('[data-choice="__cancel"]', ov).forEach(b => b.onclick = close);
  $$('[data-plugin-toggle]', ov).forEach(btn => btn.onclick = async () => {
    const enabled = btn.dataset.enabled === '1';
    try {
      await api('/api/plugins/toggle', { id: btn.dataset.pluginToggle, enabled });
      toast(enabled ? '已启用插件' : '已禁用插件；正在运行的任务已取消', 'ok');
    } catch (e) { toast(e.message, 'err', '操作失败'); }
    close(); await refreshPlugins();
  });
  $$('[data-plugin-remove]', ov).forEach(btn => btn.onclick = async () => {
    const answer = await modal({
      title: '卸载插件',
      hint: '只会删除插件代码与它自己的缓存；已插入的图片、源 Markdown 和已经导出的文件都不会被删除。',
      choices: [{ label: '取消', value: 'cancel' }, { label: '卸载', value: 'go', primary: true }],
    });
    if (!answer || answer.__choice !== 'go') return;
    try {
      const res = await api('/api/plugins/remove', { id: btn.dataset.pluginRemove });
      toast(res.result.note || '已卸载插件', 'ok');
    } catch (e) { toast(e.message, 'err', '卸载失败'); }
    close(); await refreshPlugins();
  });
  $$('[data-plugin-install]', ov).forEach(btn => btn.onclick = () => {
    close();
    $('#pluginPackageFile').click();
  });
}

async function installPluginFile(file) {
  if (!file) return;
  try {
    const buf = new Uint8Array(await file.arrayBuffer());
    let binary = '';
    for (let i = 0; i < buf.length; i += 1) binary += String.fromCharCode(buf[i]);
    const res = await api('/api/plugins/install', { b64: btoa(binary), name: file.name });
    toast(res.result.note || '已安装插件；启用后命令才会出现', 'ok', '安装完成');
  } catch (e) { toast(e.message, 'err', '安装被拒绝'); }
  await refreshPlugins();
}

/* 图片插入：宿主把图片交给插件暂存，校验通过后写进文档旁边的 assets/。 */
async function insertImageWithPlugin() {
  if (!pluginCommands('editor.image_insert').length) {
    toast('需要先安装并启用图片插入插件', 'warn'); return;
  }
  if (!pluginTarget()) { toast('请先保存当前文档，再用插件插入图片', 'warn'); return; }
  const input = $('#pluginImageFile');
  input.value = '';
  input.onchange = async () => {
    const file = input.files && input.files[0];
    if (!file) return;
    await insertImageBlob(file, '插入成功');
  };
  input.click();
}

/* F03：拿到手的一张图片（选择文件 / 剪贴板截图 / 拖入）走同一条通道。
   base64 交给宿主，宿主落成附件、核对修订后才提交正文；图片数据不进 Markdown。 */
async function insertImageBlob(file, okTitle) {
  const commands = pluginCommands('editor.image_insert');
  if (!commands.length) { toast('需要先安装并启用图片插入插件', 'warn'); return false; }
  const target = pluginTarget();
  if (!target) { toast('请先保存当前文档，再插入图片', 'warn'); return false; }
  try {
    const buf = new Uint8Array(await file.arrayBuffer());
    let binary = '';
    for (let i = 0; i < buf.length; i += 1) binary += String.fromCharCode(buf[i]);
    const payload = Object.assign({
      command: commands[0].command, b64: btoa(binary),
      name: file.name || 'clipboard.png', entry: 'web', wait: 60,
    }, target);
    const res = await api('/api/plugins/insert', payload);
    if (res.pending) { toast('插件还在处理这张图片，请稍后再试', 'warn'); return false; }
    insertMarkdownAtCursor(res.markdown);
    toast('已插入图片，附件保存到：' + res.assets_dir, 'ok', okTitle || '插入成功');
    return true;
  } catch (e) { toast(e.message, 'err', '插入失败'); return false; }
}

/* 剪贴板或拖放内容里的图片：按类型先认 MIME，认不出来再看扩展名。 */
function isImageName(name) {
  return /\.(png|jpe?g|gif|webp|bmp|avif)$/i.test(String(name || ''));
}

function clipboardImageFile(data) {
  if (!data) return null;
  for (const item of Array.from(data.items || [])) {
    if (item.kind === 'file' && /^image\//.test(item.type || '')) {
      const file = item.getAsFile && item.getAsFile();
      if (file) return file;
    }
  }
  for (const file of Array.from(data.files || [])) {
    if (/^image\//.test(file.type || '') || isImageName(file.name)) return file;
  }
  return null;
}

/* 导出：核心负责快照、预检与覆盖确认，插件只产临时文件，最后由核心替换目标。 */
async function exportWithPlugin() {
  const select = $('#exportFormat');
  const command = select && select.value ? select.value : '';
  if (!command) { toast('没有可用的导出插件', 'warn'); return; }
  const target = pluginTarget();
  if (!target) { toast('请先保存当前文档，再用插件导出', 'warn'); return; }
  const picked = (state.pluginCommands || []).find(c => c.command === command) || {};
  const doc = state.doc || {};
  const name = ((doc.info && doc.info.file) || '导出').replace(/\.[^.]+$/, '') + '.' + (picked.extension || '');
  const payload = Object.assign({ command, entry: 'web', wait: 180 }, target);
  if (state.dirty) payload.markdown = $('#editor').value;

  // ① 先让核心算一遍：来源要不要问、预检有没有问题
  let plan;
  try {
    plan = await api('/api/export/check', payload);
  } catch (e) { toast(e.message, 'err', '导出前检查失败'); return; }
  if (plan.needs_source) {
    const answer = await modal({
      title: '这篇文档有未保存的修改',
      hint: '导出用哪一份内容？导出不会替你保存源文件。',
      choices: [{ label: '取消', value: 'cancel' },
                { label: '磁盘已保存版本', value: 'disk' },
                { label: '当前编辑内容（推荐）', value: 'buffer', primary: true }],
    });
    if (!answer || answer.__choice === 'cancel') { toast('已取消导出', 'warn'); return; }
    payload.source = answer.__choice;
    try {
      plan = await api('/api/export/check', payload);
    } catch (e) { toast(e.message, 'err', '导出前检查失败'); return; }
  }
  const report = plan.preflight || {};
  if ((report.errors && report.errors.length) || (report.warnings && report.warnings.length)) {
    const go = await exportReportModal(report, plan.dest, plan.destination_exists);
    if (!go) { toast('已返回修改，导出没有开始', 'warn'); return; }
    payload.confirm = true;
  }

  // ② 目标路径（网页端在服务端模式下询问保存位置；否则下载到浏览器）
  let dest = '';
  if (state.mode === 'server') {
    try {
      const dlg = await api('/api/dialog/save', { name });
      if (!dlg.path) return;
      dest = dlg.path;
    } catch (e) { toast(e.message, 'err', '选择保存位置失败'); return; }
  }
  payload.dest = dest;
  try {
    let res = await api('/api/plugins/export', payload);
    if (res.pending) { toast('插件还在生成文件，请稍后再试', 'warn'); return; }
    if (state.mode !== 'server') downloadPath(res.path, name);
    toast('已导出：' + res.path +
          ((res.warnings || []).length ? '（' + res.warnings.length + ' 条提示）' : ''), 'ok', '导出成功');
  } catch (e) { toast(e.message, 'err', '导出失败, 原目标未改动'); }
}

/* 预检报告：有错误只能返回修改；只有提示时可以确认继续。 */
function exportReportModal(report, dest, exists) {
  return new Promise(resolve => {
    const ov = $('#overlay');
    const errors = report.errors || [];
    const warnings = report.warnings || [];
    const stats = report.stats || {};
    const rows = errors.map(item => '<li class="err">' + esc(item.message) + '</li>')
      .concat(warnings.map(item => '<li>' + esc(item.message) + '</li>')).join('');
    const summary = errors.length ? '有 ' + errors.length + ' 个必须先处理的问题'
      : (warnings.length ? '有 ' + warnings.length + ' 条提示，可以继续' : '没有发现问题');
    ov.innerHTML = '<div class="modal wide" role="dialog">' +
      '<h3>导出前检查</h3>' +
      '<p class="hint">' + esc(summary) + '</p>' +
      '<ul class="export-report">' + rows + '</ul>' +
      '<p class="hint">文档：' + (stats.chars || 0) + ' 字 / ' + (stats.lines || 0) + ' 行 · 图片 ' +
      (stats.images || 0) + ' · 公式 ' + (stats.formulas || 0) + ' · 表格 ' + (stats.tables || 0) +
      ' · 链接 ' + (stats.links || 0) + (dest ? '<br>导出到：' + esc(dest) + (exists ? '（已存在，稍后确认覆盖）' : '') : '') +
      '</p>' +
      '<div class="actions">' +
      '<button class="btn" data-choice="__cancel">返回修改</button>' +
      (errors.length ? '' : '<button class="btn primary" data-choice="__ok">' +
        (warnings.length ? '仍然导出（按提示）' : '开始导出') + '</button>') +
      '</div></div>';
    ov.classList.add('on');
    const close = value => {
      ov.classList.remove('on'); ov.innerHTML = '';
      document.removeEventListener('keydown', onKey);
      resolve(value);
    };
    function onKey(event) { if (event.key === 'Escape') { event.preventDefault(); close(false); } }
    document.addEventListener('keydown', onKey);
    $$('[data-choice]', ov).forEach(btn => {
      btn.onclick = () => close(btn.dataset.choice === '__ok');
    });
    ov.onclick = event => { if (event.target === ov) close(false); };
  });
}

/* 插件产物只回一段标准 Markdown：正文提交仍然走编辑器。 */
function insertMarkdownAtCursor(text) {
  if (!state.doc) { toast('没有打开的文档', 'warn'); return; }
  switchMode('source');
  const ed = $('#editor');
  const start = (typeof ed.selectionStart === 'number') ? ed.selectionStart : ed.value.length;
  const end = (typeof ed.selectionEnd === 'number') ? ed.selectionEnd : start;
  ed.value = ed.value.slice(0, start) + text + ed.value.slice(end);
  ed.selectionStart = ed.selectionEnd = start + text.length;
  ed.dispatchEvent(new Event('input', { bubbles: true }));
}

/* ------------------------------------- 表格与常用格式（F04） */
/* 规则写在核心：mdreader/tables.py 与 mdreader/formatting.py。网页端把缓冲区
   和选区发过去，服务端算出新文本与新选区再发回来——桌面端直接 import 同一批
   函数，所以两端不会出现“一边能识别、一边乱改”的差异。
   写入仍然只发生在编辑器的 value 上，并派发一次 input，让页面标记未保存。 */

const TABLE_ALIGN_CHOICES = [['left', '左对齐'], ['center', '居中'], ['right', '右对齐']];

function editorEl() { return $('#editor'); }

function editorOffsets() {
  const ed = editorEl();
  const start = ed && typeof ed.selectionStart === 'number' ? ed.selectionStart : 0;
  const end = ed && typeof ed.selectionEnd === 'number' ? ed.selectionEnd : start;
  return [start, Math.max(start, end)];
}

function applyEditResult(res) {
  const ed = editorEl();
  if (!ed || !res) return false;
  if (typeof res.text === 'string' && res.text !== ed.value) {
    ed.value = res.text;
    ed.dispatchEvent(new Event('input', { bubbles: true }));
  }
  if (typeof res.start === 'number' && ed.setSelectionRange) {
    ed.setSelectionRange(res.start, typeof res.end === 'number' ? res.end : res.start);
  }
  if (ed.focus) ed.focus();
  return true;
}

async function applyFormat(action, options) {
  if (!state.doc) { toast('没有打开的文档', 'warn'); return false; }
  const ed = editorEl();
  if (!ed) return false;
  switchMode('source');
  const [start, end] = editorOffsets();
  const docId = state.active, original = ed.value;
  try {
    const res = await api('/api/edit/format',
      Object.assign({ text: ed.value, start, end, action }, options || {}));
    if (state.active !== docId || ed.value !== original) {
      toast('编辑期间文档已变化，请重新操作', 'warn');
      return false;
    }
    applyEditResult(res);
    toast(res.note || '已应用格式', 'ok');
    return true;
  } catch (e) { toast(e.message, 'err', '格式未应用'); return false; }
}

async function tableRequest(payload) {
  const ed = editorEl();
  if (!ed) return null;
  try { return await api('/api/edit/table', Object.assign({ text: ed.value }, payload)); }
  catch (e) { toast(e.message, 'err', '表格操作未完成'); return null; }
}

function tableAlignOptions(selected) {
  return TABLE_ALIGN_CHOICES.map(pair =>
    '<option value="' + pair[0] + '"' + (pair[0] === (selected || 'left') ? ' selected' : '') +
    '>' + pair[1] + '</option>').join('');
}

function tableModalHtml(mode, m) {
  let alignRow = '';
  let headRow = '';
  for (let c = 0; c < m.columns; c += 1) {
    alignRow += '<th><select data-align="' + c + '">' + tableAlignOptions(m.aligns[c]) + '</select></th>';
    headRow += '<td><input data-head="' + c + '" value="' + esc(m.header[c] || '') + '"></td>';
  }
  const bodyRows = m.rows.map((row, r) => '<tr>' +
    Array.from({ length: m.columns }, (_skip, c) =>
      '<td><input data-cell="' + r + ':' + c + '" value="' + esc(row[c] || '') + '"></td>').join('') +
    '</tr>').join('');
  return '<div class="modal wide" role="dialog">' +
    '<h3>' + (mode === 'edit' ? '编辑表格' : '插入表格') + '</h3>' +
    '<p class="hint">单元格只支持单行文本，内容里的竖线和反斜杠会自动转义；' +
    '不勾选“首行作表头”时，Markdown 里仍会留一行空表头（表格语法需要分隔行）。</p>' +
    '<div class="table-controls">列数 <input type="number" id="tblCols" min="1" max="24" value="' +
      m.columns + '"> 数据行数 <input type="number" id="tblRows" min="0" max="400" value="' +
      m.rows.length + '"> <label><input type="checkbox" id="tblHeader"' +
      (m.headerOn ? ' checked' : '') + '> 首行作表头</label></div>' +
    '<div class="table-grid"><table><thead><tr>' + alignRow + '</tr></thead><tbody><tr>' +
      headRow + '</tr>' + bodyRows + '</tbody></table></div>' +
    '<div class="actions"><button class="btn" data-choice="__cancel">取消</button>' +
    '<button class="btn primary" data-choice="__ok">' + (mode === 'edit' ? '应用' : '插入') +
    '</button></div></div>';
}

/* 插入/编辑表格的弹层：行列数与对齐在这里定，内容由服务端规则写回缓冲区。 */
function tableModal(mode, model) {
  return new Promise(resolve => {
    const ov = $('#overlay');
    const current = {
      header: model.header.slice(), headerOn: model.headerOn !== false,
      rows: model.rows.map(row => row.slice()), aligns: model.aligns.slice(),
      columns: model.header.length,
    };
    function collect() {
      $$('[data-head]', ov).forEach(el => { current.header[Number(el.dataset.head)] = el.value; });
      $$('[data-cell]', ov).forEach(el => {
        const spot = el.dataset.cell.split(':');
        current.rows[Number(spot[0])][Number(spot[1])] = el.value;
      });
      $$('[data-align]', ov).forEach(el => { current.aligns[Number(el.dataset.align)] = el.value; });
      const head = $('#tblHeader', ov);
      if (head) current.headerOn = head.checked;
    }
    function resize(columns, rows) {
      current.columns = Math.max(1, Math.min(24, columns || 1));
      const count = Math.max(0, Math.min(400, rows || 0));
      current.header = current.header.slice(0, current.columns);
      while (current.header.length < current.columns) current.header.push('');
      current.aligns = current.aligns.slice(0, current.columns);
      while (current.aligns.length < current.columns) current.aligns.push('left');
      const next = [];
      for (let r = 0; r < count; r += 1) {
        const row = (current.rows[r] || []).slice(0, current.columns);
        while (row.length < current.columns) row.push('');
        next.push(row);
      }
      current.rows = next;
      render();
    }
    function close(value) {
      ov.classList.remove('on'); ov.innerHTML = '';
      document.removeEventListener('keydown', onKey);
      resolve(value);
    }
    function onKey(event) { if (event.key === 'Escape') { event.preventDefault(); close(null); } }
    function render() {
      ov.innerHTML = tableModalHtml(mode, current);
      ov.classList.add('on');
      const cols = $('#tblCols', ov);
      const rows = $('#tblRows', ov);
      const head = $('#tblHeader', ov);
      const first = ov.querySelector('input,select');
      if (first) first.focus();
      if (cols) cols.onchange = () => { collect(); resize(Number(cols.value), current.rows.length); };
      if (rows) rows.onchange = () => { collect(); resize(current.columns, Number(rows.value)); };
      if (head) head.onchange = () => { collect(); };
      $$('[data-choice]', ov).forEach(btn => {
        btn.onclick = () => {
          const choice = btn.dataset.choice;
          if (choice === '__cancel') return close(null);
          collect();
          close(current);
        };
      });
      ov.onclick = event => { if (event.target === ov) close(null); };
    }
    document.addEventListener('keydown', onKey);
    render();
  });
}

async function insertTableDialog() {
  if (!state.doc) { toast('没有打开的文档', 'warn'); return false; }
  switchMode('source');
  const data = await tableModal('insert', {
    header: ['', '', ''], rows: [['', '', ''], ['', '', '']],
    aligns: ['left', 'left', 'left'], headerOn: true,
  });
  if (!data) { toast('已取消插入表格', 'warn'); return false; }
  const [offset] = editorOffsets();
  const fills = data.headerOn ? [data.header].concat(data.rows) : data.rows;
  const res = await tableRequest({ op: 'insert_table', offset, args: {
    columns: data.columns, rows: data.rows.length, header: data.headerOn,
    aligns: data.aligns, fills,
  } });
  if (!res) return false;
  applyEditResult(res);
  toast('已插入表格', 'ok');
  return true;
}

async function editTableDialog() {
  if (!state.doc) { toast('没有打开的文档', 'warn'); return false; }
  switchMode('source');
  const [offset] = editorOffsets();
  const info = await tableRequest({ op: 'read', offset });
  if (!info) return false;
  const table = info.table || { header: [], rows: [], aligns: [] };
  if (table.rows.length > 60 || table.header.length > 16) {
    toast('这张表格有 ' + table.header.length + ' 列 ' + table.rows.length +
          ' 行，弹层不便编辑；可以直接在源码里改', 'warn');
    return false;
  }
  const data = await tableModal('edit', {
    header: table.header, rows: table.rows, aligns: table.aligns,
    headerOn: table.header.some(value => value !== ''),
  });
  if (!data) { toast('已取消编辑表格', 'warn'); return false; }
  const res = await tableRequest({ op: 'set_table', offset, args: {
    header: data.header, rows: data.rows, aligns: data.aligns,
  } });
  if (!res) return false;
  applyEditResult(res);
  toast('已更新表格', 'ok');
  return true;
}

/* 粘贴预览：行列与“不能原样处理的地方”先摆出来，确认后才写入缓冲区。 */
function pasteTableModal(preview) {
  return new Promise(resolve => {
    const ov = $('#overlay');
    const rows = preview.rows || [];
    const body = rows.slice(0, 12).map(row =>
      '<tr>' + row.map(cell => '<td>' + esc(cell) + '</td>').join('') + '</tr>').join('');
    const warnings = (preview.warnings || []).map(message => '· ' + esc(message)).join('<br>');
    ov.innerHTML = '<div class="modal wide" role="dialog">' +
      '<h3>粘贴为表格：' + preview.columns + ' 列 × ' + rows.length + ' 行</h3>' +
      (warnings ? '<p class="table-warn">' + warnings + '</p>' : '') +
      '<div class="table-grid"><table><tbody>' + body + '</tbody></table></div>' +
      '<div class="table-controls"><label><input type="checkbox" id="pasteHeader" checked>' +
      ' 第一行作表头</label></div>' +
      '<div class="actions"><button class="btn" data-choice="__cancel">取消</button>' +
      '<button class="btn primary" data-choice="__ok">插入表格</button></div></div>';
    ov.classList.add('on');
    const close = value => {
      ov.classList.remove('on'); ov.innerHTML = '';
      document.removeEventListener('keydown', onKey);
      resolve(value);
    };
    function onKey(event) { if (event.key === 'Escape') { event.preventDefault(); close(null); } }
    document.addEventListener('keydown', onKey);
    $$('[data-choice]', ov).forEach(btn => {
      btn.onclick = () => {
        if (btn.dataset.choice === '__cancel') return close(null);
        const head = $('#pasteHeader', ov);
        close({ header: !head || head.checked });
      };
    });
    ov.onclick = event => { if (event.target === ov) close(null); };
  });
}

async function pasteAsTable(text) {
  if (!state.doc) { toast('没有打开的文档', 'warn'); return false; }
  if (!text || text.indexOf('\t') < 0) return false;
  switchMode('source');
  const preview = await tableRequest({ op: 'parse', payload: text });
  if (!preview) return false;
  if (preview.columns < 2) { toast('这段文本只有一列，用普通粘贴更合适', 'warn'); return false; }
  const choice = await pasteTableModal(preview);
  if (!choice) { toast('已取消粘贴表格', 'warn'); return false; }
  const [start, end] = editorOffsets();
  const res = await tableRequest({ op: 'paste', payload: text, start, end, header: choice.header });
  if (!res) return false;
  applyEditResult(res);
  toast('已粘贴为表格', 'ok');
  return true;
}

/* 编辑器里的粘贴：图片（截图）先落附件再插链接；多行多列纯文本先给预览；
   其余普通粘贴完全不拦。 */
function onEditorPaste(event) {
  const data = event && (event.clipboardData || window.clipboardData);
  const image = clipboardImageFile(data);
  if (image) {
    event.preventDefault();
    insertImageBlob(image, '已插入截图');
    return;
  }
  const text = data && data.getData ? data.getData('text') : '';
  if (!text || text.indexOf('\t') < 0 || text.indexOf('\n') < 0) return;
  event.preventDefault();
  pasteAsTable(text);
}

async function pasteTableFromClipboard() {
  let text = '';
  try {
    text = await navigator.clipboard.readText();
  } catch (e) { text = ''; }
  if (!text || text.indexOf('\t') < 0) {
    toast('读不到剪贴板内容，请在编辑器里直接 Ctrl+V 粘贴', 'warn');
    return false;
  }
  return pasteAsTable(text);
}

/* ------------------------------------------------ 链接检查（F09） */
/* 只查当前这一篇：缺文件、越界引用、绝对路径、指向不存在的标题锚点。
   点一条就跳到源码的对应行；重新选择文件请在桌面端做（网页端只做定位）。 */
async function checkDocumentLinks() {
  if (!state.doc) { toast('没有打开的文档', 'warn'); return false; }
  const target = pluginTarget();
  if (!target) { toast('请先保存当前文档，再检查链接', 'warn'); return false; }
  const payload = Object.assign({ entry: 'web' }, target);
  if (state.dirty) payload.markdown = $('#editor').value;
  let result;
  try {
    result = await api('/api/check/links', payload);
  } catch (e) { toast(e.message, 'err', '检查链接失败'); return false; }
  const report = result.report || {};
  if (!report.issues || !report.issues.length) {
    toast('检查了 ' + (report.checked || 0) + ' 处本地引用，没有发现问题', 'ok');
    return true;
  }
  return linkReportModal(report, result.name);
}

function linkReportModal(report, name) {
  return new Promise(resolve => {
    const ov = $('#overlay');
    const rows = (report.issues || []).map((item, index) =>
      '<li class="' + (item.level === 'error' ? 'err' : '') + '">' +
      '<button class="btn ghost sm" data-goto-line="' + index + '">定位</button> ' +
      '第 ' + item.line + ' 行 · ' + esc(item.source) + ' · ' + esc(item.message) + '</li>').join('');
    ov.innerHTML = '<div class="modal wide" role="dialog">' +
      '<h3>检查当前文档链接</h3>' +
      '<p class="hint">' + esc(report.summary || '') + '（「' + esc(name || '当前文档') + '」共检查 ' +
      (report.checked || 0) + ' 处本地引用；外部网址不检查）' +
      '重新选择文件请在桌面端做。</p>' +
      '<ul class="export-report">' + rows + '</ul>' +
      '<div class="actions"><button class="btn" data-choice="__cancel">关闭</button></div></div>';
    ov.classList.add('on');
    const close = value => {
      ov.classList.remove('on'); ov.innerHTML = '';
      document.removeEventListener('keydown', onKey);
      resolve(value);
    };
    function onKey(event) { if (event.key === 'Escape') { event.preventDefault(); close(false); } }
    document.addEventListener('keydown', onKey);
    $$('[data-goto-line]', ov).forEach(button => {
      button.onclick = () => {
        const issue = (report.issues || [])[Number(button.dataset.gotoLine)] || {};
        close(jumpToSourceLine(issue.line));
      };
    });
    $$('[data-choice]', ov).forEach(btn => { btn.onclick = () => close(false); });
    ov.onclick = event => { if (event.target === ov) close(false); };
  });
}

/* 跳到源码模式的某一行并选中它。 */
function jumpToSourceLine(line) {
  const ed = $('#editor');
  if (!ed) return false;
  switchMode('source');
  const wanted = Math.max(1, Number(line) || 1);
  const lines = ed.value.split('\n');
  let start = 0;
  for (let index = 1; index < wanted && index <= lines.length; index += 1) {
    start += lines[index - 1].length + 1;
  }
  const end = Math.min(ed.value.length, start + (lines[wanted - 1] || '').length);
  ed.focus();
  if (ed.setSelectionRange) ed.setSelectionRange(start, end);
  ed.scrollTop = Math.max(0, (wanted - 4) * 20);
  toast('已跳到第 ' + wanted + ' 行', 'ok');
  return true;
}

/* ---------------------------------------------------------------- export */
async function exportDoc() {
  if (!state.active) return;
  const name = (state.active.split('/').pop() || 'doc').replace(/\.[^.]+$/, '') + '.html';
  try {
    if (state.mode === 'server') {
      const dlg = await api('/api/dialog/save', { name });
      if (!dlg.path) return;
      // The OS save dialog already asked about replacing an existing file.
      const res = await api('/api/doc/export', { pid: state.pid, doc: state.active, dest: dlg.path, overwrite: true, reveal: false });
      toast('已导出：' + res.path, 'ok', '导出成功');
    } else {
      const res = await api('/api/doc/export', { pid: state.pid, doc: state.active, dest: '', reveal: false });
      downloadPath(res.path, name);
    }
  } catch (e) { toast(e.message, 'err', '导出失败'); }
}

async function exportProject() {
  if (!state.pid) return;
  const name = (state.project.name || 'project') + '-合集.html';
  try {
    if (state.mode === 'server') {
      const dlg = await api('/api/dialog/save', { name });
      if (!dlg.path) return;
      const res = await api('/api/project/export', { pid: state.pid, dest: dlg.path, overwrite: true });
      toast('已导出：' + res.path, 'ok', '导出成功');
    } else {
      const res = await api('/api/project/export', { pid: state.pid, dest: '' });
      downloadPath(res.path, name);
    }
  } catch (e) { toast(e.message, 'err', '导出失败'); }
}

function downloadPath(path, fallbackName) {
  const a = document.createElement('a');
  a.href = '/api/file?pid=' + encodeURIComponent(state.pid) + '&p=' + encodeURIComponent(path) + '&dl=1';
  a.download = fallbackName;
  document.body.appendChild(a); a.click(); a.remove();
  toast('已开始下载（浏览器默认下载目录）', 'ok');
}

/* ---------------------------------------------------------------- search */
let searchTimer = null;
function onSearchInput() {
  clearTimeout(searchTimer);
  const q = $('#searchInput').value.trim();
  if (!q) { $('#hits').style.display = 'none'; $('#hits').innerHTML = ''; return; }
  searchTimer = setTimeout(runSearch, 280);
}

async function runSearch() {
  const q = $('#searchInput').value.trim();
  if (!q || !state.pid) return;
  try {
    const res = await api('/api/search?pid=' + encodeURIComponent(state.pid) + '&q=' + encodeURIComponent(q));
    const host = $('#hits');
    host.style.display = 'block';
    if (!res.hits.length) { host.innerHTML = '<div class="hit"><span class="snip">没有匹配</span></div>'; return; }
    host.innerHTML = res.hits.map(h =>
      '<div class="hit" data-id="' + esc(h.id) + '"><b>📄 ' + esc(h.name) + '</b>' +
      '<span class="snip">' + esc(h.dir ? h.dir + ' · ' : '') + esc(h.snippet || '') + '</span></div>').join('');
    $$('.hit', host).forEach(el => el.onclick = () => { if (el.dataset.id) openDoc(el.dataset.id); });
  } catch (e) { toast(e.message, 'err', '搜索失败'); }
}

/* ------------------------------------------------------------------ bind */
function switchMode(mode) {
  saveReadingPosition();
  document.body.classList.toggle('mode-source', mode === 'source');
  $$('#modeSeg button').forEach(b => b.classList.toggle('on', b.dataset.mode === mode));
  localStorage.setItem('mdreader.mode', mode);
  clearFindHits();
  $('#findCount').textContent = '';
  $('#outlinePanel').hidden = true;
  if (mode === 'source') setTimeout(() => $('#editor').focus(), 30);
  restoreReadingPosition();
}

function setTheme(theme, persist = true) {
  if (!['light', 'dark', 'eye'].includes(theme)) theme = 'light';
  document.documentElement.dataset.theme = theme;
  $$('[data-theme-choice]').forEach(button =>
    button.setAttribute('aria-pressed', String(button.dataset.themeChoice === theme)));
  // 公式是**按主题画好的位图**（背景色写进了图片里），CSS 变量换不掉它；
  // 这里只换这一类图片的地址，不重排正文，阅读位置也不会丢。
  $$('#renderHost img.formula-img').forEach(img => {
    const current = img.getAttribute('src') || '';
    if (current.indexOf('theme=') >= 0) {
      img.setAttribute('src', current.replace(/([?&]theme=)[a-z]+/, '$1' + theme));
    }
  });
  if (persist) {
    localStorage.setItem('mdreader.theme', theme);
    // The desktop window and this page share one theme in the workspace file,
    // so changing it here also changes the app background.
    api('/api/settings', { theme }).catch(() => {});
  }
}

/* The workspace setting wins over this browser's own memory. */
async function loadSharedTheme() {
  try {
    const settings = await api('/api/settings');
    if (settings && ['light', 'dark', 'eye'].includes(settings.theme)) {
      setTheme(settings.theme, false);
      localStorage.setItem('mdreader.theme', settings.theme);
      return true;
    }
  } catch (e) { /* offline or older server: keep the local choice */ }
  return false;
}

function bind() {
  $('#projectSelect').onchange = e => selectProject(e.target.value);
  $('#rootSelect').onchange = e => selectRoot(e.target.value);
  $('#btnOpenFolder').onclick = openUserFolder;
  $('#btnNewFolderDoc').onclick = newFolderDoc;
  $('#btnRefreshFolder').onclick = () => refreshFolderTree(true);
  $('#btnRemoveFolder').onclick = removeRoot;
  $('#btnNewProject').onclick = newProject;
  $('#btnRenameProject').onclick = renameProject;
  $('#btnRevealProject').onclick = async () => {
    if (!state.pid) return;
    try { await api('/api/project/reveal', { pid: state.pid }); }
    catch (e) { toast(e.message, 'err', '打开失败'); }
  };
  $('#btnNewDoc').onclick = newDoc;
  $('#btnImportFiles').onclick = importFiles;
  $('#btnImportDir').onclick = importDir;
  $('#btnOpenLocal').onclick = openLocalFiles;
  $('#btnNewLoose').onclick = newLooseDraft;
  $('#recentSearch').addEventListener('input', renderRecent);
  $('#btnRefresh').onclick = refreshFromDisk;
  $('#btnSave').onclick = saveDoc;
  $('#btnExport').onclick = exportDoc;
  $('#btnExportAll').onclick = exportProject;
  $('#btnRenameDoc').onclick = () => state.active && renameDoc(state.active);
  $('#btnDeleteDoc').onclick = () => state.active && deleteDoc(state.active);
  $('#btnCollapse').onclick = () => document.body.classList.toggle('side-collapsed');
  $('#btnPlugins').onclick = pluginManager;
  $('#btnInsertImage').onclick = insertImageWithPlugin;
  $('#btnExportPlugin').onclick = exportWithPlugin;
  $('#pluginPackageFile').addEventListener('change', e => {
    installPluginFile(e.target.files && e.target.files[0]);
  });
  const themeSelect = $('#btnTheme').parentElement;
  let themeCloseTimer;
  $('#btnTheme').onclick = event => {
    event.preventDefault();
    clearTimeout(themeCloseTimer);
    themeSelect.open = true;
  };
  themeSelect.addEventListener('pointerenter', () => {
    clearTimeout(themeCloseTimer);
    themeSelect.open = true;
  });
  themeSelect.addEventListener('pointerleave', () => {
    themeCloseTimer = setTimeout(() => { themeSelect.open = false; }, 200);
  });
  themeSelect.onclick = event => {
    const button = event.target.closest('[data-theme-choice]');
    if (button) {
      setTheme(button.dataset.themeChoice);
      themeSelect.open = false;
    }
  };
  document.addEventListener('click', event => {
    if (!themeSelect.contains(event.target)) themeSelect.open = false;
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') themeSelect.open = false;
  });
  $$('#modeSeg button').forEach(b => b.onclick = () => switchMode(b.dataset.mode));

  // ---- 表格与常用格式（F04）----
  const headingSelect = $('#fmtHeading') ? $('#fmtHeading').parentElement : null;
  $$('#formatBar [data-format]').forEach(b => {
    b.onclick = () => applyFormat(b.dataset.format);
  });
  $$('#fmtFormula [data-formula]').forEach(b => {
    b.onclick = () => {
      applyFormat(b.dataset.formula);
      $('#fmtFormula').open = false;
    };
  });
  $$('#formatBar [data-heading]').forEach(b => {
    b.onclick = () => {
      applyFormat('heading', { level: Number(b.dataset.heading) });
      if (headingSelect) headingSelect.open = false;
    };
  });
  if ($('#fmtTable')) $('#fmtTable').onclick = insertTableDialog;
  if ($('#fmtEditTable')) $('#fmtEditTable').onclick = editTableDialog;
  if ($('#fmtPasteTable')) $('#fmtPasteTable').onclick = pasteTableFromClipboard;
  if ($('#editor')) $('#editor').addEventListener('paste', onEditorPaste);

  $('#searchInput').addEventListener('input', onSearchInput);
  $('#searchInput').addEventListener('keydown', e => { if (e.key === 'Enter') runSearch(); });

  // ---- find / outline / reading position ----
  $('#btnFind').onclick = openFind;
  if ($('#btnLinks')) $('#btnLinks').onclick = checkDocumentLinks;
  $('#btnOutline').onclick = renderOutline;
  $('#findClose').onclick = closeFind;
  $('#findNext').onclick = () => runFind(false);
  $('#findPrev').onclick = () => runFind(true);
  $('#findInput').addEventListener('input', () => runFind(false));
  $('#findInput').addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); runFind(e.shiftKey); }
    if (e.key === 'Escape') { e.preventDefault(); closeFind(); }
  });
  $('#outlinePanel').addEventListener('click', e => {
    const button = e.target.closest('[data-outline]');
    if (button) jumpToOutline(Number(button.dataset.outline));
  });
  document.addEventListener('click', e => {
    const panel = $('#outlinePanel');
    if (panel.hidden) return;
    if (!e.target.closest('#outlinePanel') && !e.target.closest('#btnOutline')) panel.hidden = true;
  });
  let positionJob = null;
  const onScroll = () => {
    if (positionJob) return;
    positionJob = setTimeout(() => { positionJob = null; saveReadingPosition(); }, 400);
  };
  $('#content').addEventListener('scroll', onScroll);
  $('#editor').addEventListener('scroll', onScroll);

  const ed = $('#editor');
  ed.addEventListener('input', () => {
    state.dirty = true;
    document.body.classList.add('dirty');
    $('#editorInfo').textContent = (state.active || '') + ' · ' + ed.value.length + ' 字符（未保存）';
    scheduleRecovery();
  });

  document.addEventListener('keydown', e => {
    const mod = e.ctrlKey || e.metaKey;
    if (!mod) return;
    if (e.key === 's' || e.key === 'S') { e.preventDefault(); saveDoc(); }
    else if (e.shiftKey && (e.key === 'm' || e.key === 'M')) {
      e.preventDefault(); applyFormat('formula_inline');
    }
    else if (e.key === 'b' || e.key === 'B') { e.preventDefault(); document.body.classList.toggle('side-collapsed'); }
    else if (e.key === 'n' || e.key === 'N') { e.preventDefault(); newDoc(); }
    else if (e.key === 'e' || e.key === 'E') { e.preventDefault(); switchMode(document.body.classList.contains('mode-source') ? 'render' : 'source'); }
    else if (e.key === 'f' || e.key === 'F') { e.preventDefault(); openFind(); }
    else if (e.key === 'l' || e.key === 'L') { e.preventDefault(); renderOutline(); }
    else if (e.key === 't' || e.key === 'T') {
      if (!e.shiftKey) return;
      e.preventDefault();
      insertTableDialog();
    }
  });

  window.addEventListener('beforeunload', e => {
    saveReadingPosition();
    if (state.dirty) { e.preventDefault(); e.returnValue = ''; }
  });

  // ---- drag & drop ----
  // 拖进来的 .md 默认**临时查看**（直接读改原文件，不复制）；
  // 拖进来的文件夹、或按住 Shift 拖文件，才导入成项目文档。
  let dragDepth = 0;
  const dz = $('#dropzone');
  window.addEventListener('dragenter', e => {
    if (!e.dataTransfer || !Array.from(e.dataTransfer.types || []).includes('Files')) return;
    dragDepth++; dz.classList.add('on');
  });
  window.addEventListener('dragover', e => { e.preventDefault(); });
  window.addEventListener('dragleave', e => {
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) dz.classList.remove('on');
  });
  window.addEventListener('drop', async e => {
    e.preventDefault(); dragDepth = 0; dz.classList.remove('on');
    const dt = e.dataTransfer;
    if (!dt) return;

    const paths = droppedPaths(e);
    const entries = [];
    if (dt.items && dt.items.length && dt.items[0].webkitGetAsEntry) {
      for (const item of Array.from(dt.items)) {
        const en = item.webkitGetAsEntry && item.webkitGetAsEntry();
        if (en) entries.push(en);
      }
    }
    const wanted = paths.length ? paths : [];
    const hasFolder = entries.some(en => en.isDirectory);
    const asProject = e.shiftKey || hasFolder;

    // F03：整批拖入的都是图片（且没按 Shift）时，插到当前文档的光标处，
    // 不当项目文件导入；按 Shift 或混着别的文件仍旧走下面的打开/导入规则。
    const droppedFiles = Array.from(dt.files || []);
    if (!asProject && droppedFiles.length
        && droppedFiles.every(f => /^image\//.test(f.type || '') || isImageName(f.name))) {
      if (!pluginTarget()) { toast('拖入的是图片：请先打开并保存要插入图片的文档', 'warn'); return; }
      await insertImageBlob(droppedFiles[0], '已插入拖入的图片');
      if (droppedFiles.length > 1) {
        toast('一次只插入一张图片：其余 ' + (droppedFiles.length - 1) + ' 张没有处理', 'warn');
      }
      return;
    }

    // server mode gives us real paths, so a file can be read/edited in place
    if (wanted.length && (!asProject || hasFolder)) {
      const edits = await openLooseFiles(wanted);
      if (edits || wanted.length) return;
    }
    if (asProject) {
      const files = [];
      for (const en of entries) await walkEntry(en, '', files);
      if (!files.length && dt.files) files.push(...Array.from(dt.files));
      if (files.length) await uploadFiles(files);
      return;
    }
    if (wanted.length) { await openLooseFiles(wanted); return; }

    // browser fallback: no real paths exposed, so show what was dropped
    const files = [];
    for (const en of entries) await walkEntry(en, '', files);
    if (!files.length && dt.files) files.push(...Array.from(dt.files));
    if (!files.length) return;
    if (!state.pid) {
      toast('浏览器模式拿不到文件路径：请先选择项目，或用桌面窗口打开', 'warn');
      return;
    }
    await uploadFiles(files);
  });
}

/* Chromium/WebView2 expose dropped file paths through the DataTransfer items */
function droppedPaths(event) {
  const out = [];
  const dt = event.dataTransfer;
  if (!dt) return out;
  try {
    if (typeof dt.getData === 'function') {
      const uriList = dt.getData('text/uri-list') || '';
      uriList.split(/\r?\n/).forEach(line => {
        const text = line.trim();
        if (!text || text.startsWith('#')) return;
        try { out.push(decodeURIComponent(new URL(text).pathname.replace(/^\//, ''))); }
        catch (err) { /* not a file url */ }
      });
    }
  } catch (err) { /* getData can throw for protected drags */ }
  if (out.length) return out;
  if (dt.files) {
    Array.from(dt.files).forEach(f => { if (f.path) out.push(f.path); });
  }
  return out;
}

function walkEntry(entry, prefix, out) {
  return new Promise(resolve => {
    if (entry.isFile) {
      entry.file(f => {
        try { Object.defineProperty(f, 'webkitRelativePath', { value: prefix + entry.name }); } catch (e) {}
        out.push(f); resolve();
      }, () => resolve());
      return;
    }
    const reader = entry.createReader();
    const acc = [];
    const read = () => reader.readEntries(async batch => {
      if (!batch.length) {
        for (const e of acc) await walkEntry(e, prefix + entry.name + '/', out);
        return resolve();
      }
      acc.push(...batch);
      read();
    }, () => resolve());
    read();
  });
}

/* ------------------------------------------- find, outline, position */
/* Case-insensitive matches of one needle; pure so the tests can drive it. */
function findMatches(haystack, needle, limit = 2000) {
  const hay = String(haystack == null ? '' : haystack);
  const raw = String(needle == null ? '' : needle);
  if (!raw) return [];
  const lowHay = hay.toLowerCase(), lowNeedle = raw.toLowerCase();
  const out = [];
  let from = 0;
  while (out.length < limit) {
    const at = lowHay.indexOf(lowNeedle, from);
    if (at < 0) break;
    out.push({ start: at, end: at + raw.length });
    from = at + Math.max(1, raw.length);
  }
  return out;
}

/* Headings of a Markdown source, skipping fenced code blocks. */
function outlineFromMarkdown(text) {
  const rows = [];
  let fence = false;
  String(text == null ? '' : text).split('\n').forEach((line, index) => {
    if (/^\s*(```|~~~)/.test(line)) { fence = !fence; return; }
    if (fence) return;
    const match = /^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$/.exec(line);
    if (match) rows.push({ level: match[1].length, title: match[2].trim(), line: index + 1 });
  });
  return rows;
}

function inSourceMode() {
  try { return document.body.classList.contains('mode-source'); }
  catch (e) { return false; }
}

function clearFindHits() {
  const host = $('#renderHost');
  if (host) {
    $$('mark.find-hit', host).forEach(mark => {
      const parent = mark.parentNode;
      if (!parent) return;
      parent.replaceChild(document.createTextNode(mark.textContent), mark);
      parent.normalize();
    });
  }
  state.findNeedle = '';
  state.findMarks = [];
  state.findIndex = -1;
}

function textNodesOf(root) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      if (!node.nodeValue || !node.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
      const parent = node.parentNode;
      if (!parent || /^(SCRIPT|STYLE|MARK|TEXTAREA)$/.test(parent.nodeName)) return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    },
  });
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  return nodes;
}

function highlightInRender(needle) {
  const host = $('#renderHost');
  const marks = [];
  if (!host) return marks;
  for (const node of textNodesOf(host)) {
    const hits = findMatches(node.nodeValue, needle, 200);
    if (!hits.length) continue;
    const text = node.nodeValue;
    const fragment = document.createDocumentFragment();
    let at = 0;
    for (const hit of hits) {
      if (hit.start > at) fragment.appendChild(document.createTextNode(text.slice(at, hit.start)));
      const mark = document.createElement('mark');
      mark.className = 'find-hit';
      mark.textContent = text.slice(hit.start, hit.end);
      fragment.appendChild(mark);
      marks.push(mark);
      at = hit.end;
    }
    if (at < text.length) fragment.appendChild(document.createTextNode(text.slice(at)));
    node.parentNode.replaceChild(fragment, node);
  }
  return marks;
}

function findInEditor(needle, backwards) {
  const editor = $('#editor');
  const hits = findMatches(editor.value, needle, 5000);
  if (!hits.length) { $('#findCount').textContent = '无匹配结果'; return false; }
  const caret = editor.selectionStart;
  let index = backwards ? hits.length - 1 : 0;
  if (backwards) {
    for (let i = hits.length - 1; i >= 0; i -= 1) if (hits[i].start < caret) { index = i; break; }
  } else {
    for (let i = 0; i < hits.length; i += 1) if (hits[i].start >= caret) { index = i; break; }
  }
  const hit = hits[index];
  editor.focus();
  editor.setSelectionRange(hit.start, hit.end);
  // Keep the hit near the middle of the editor, the way the desktop window does.
  let lineHeight = 20;
  try { lineHeight = parseFloat(getComputedStyle(editor).lineHeight) || 20; } catch (e) { /* detached */ }
  const line = editor.value.slice(0, hit.start).split('\n').length - 1;
  editor.scrollTop = Math.max(0, line * lineHeight - editor.clientHeight / 2);
  $('#findCount').textContent = (index + 1) + '/' + hits.length;
  return true;
}

function runFind(backwards = false) {
  const needle = $('#findInput').value;
  if (!needle) { clearFindHits(); $('#findCount').textContent = ''; return false; }
  if (inSourceMode()) return findInEditor(needle, backwards);
  if (needle !== state.findNeedle) {
    clearFindHits();
    state.findNeedle = needle;
    state.findMarks = highlightInRender(needle);
    state.findIndex = -1;
  }
  const marks = state.findMarks || [];
  if (!marks.length) { $('#findCount').textContent = '无匹配结果'; return false; }
  state.findIndex = backwards
    ? (state.findIndex <= 0 ? marks.length - 1 : state.findIndex - 1)
    : (state.findIndex >= marks.length - 1 ? 0 : state.findIndex + 1);
  marks.forEach((mark, i) => mark.classList.toggle('current', i === state.findIndex));
  marks[state.findIndex].scrollIntoView({ block: 'center' });
  $('#findCount').textContent = (state.findIndex + 1) + '/' + marks.length;
  return true;
}

function openFind() {
  const bar = $('#findBar');
  bar.hidden = false;
  $('#findInput').focus();
  $('#findInput').select();
}

function closeFind() {
  $('#findBar').hidden = true;
  clearFindHits();
  $('#findCount').textContent = '';
}

function outlineRows() {
  if (inSourceMode()) {
    return outlineFromMarkdown($('#editor').value).map(row => ({ ...row }));
  }
  return $$('#renderHost h1, #renderHost h2, #renderHost h3, #renderHost h4, #renderHost h5, #renderHost h6')
    .map(el => ({ level: Number(el.tagName.slice(1)), title: el.textContent.trim(), el }));
}

function renderOutline() {
  const panel = $('#outlinePanel');
  const rows = outlineRows();
  state.outline = rows;
  panel.innerHTML = rows.length
    ? rows.map((row, index) =>
        '<button type="button" class="outline-item" data-outline="' + index + '"' +
        ' style="padding-left:' + (9 + (row.level - 1) * 13) + 'px">' + esc(row.title) + '</button>').join('')
    : '<div class="outline-empty">这份文档没有标题</div>';
  panel.hidden = false;
}

function jumpToOutline(index) {
  const row = (state.outline || [])[index];
  if (!row) return;
  if (row.el) {
    row.el.scrollIntoView({ block: 'start' });
  } else {
    const editor = $('#editor');
    const before = editor.value.split('\n').slice(0, Math.max(0, row.line - 1)).join('\n');
    const at = row.line > 1 ? before.length + 1 : 0;
    editor.focus();
    editor.setSelectionRange(at, at);
    editor.scrollTop = Math.max(0, (row.line - 1) * 20 - editor.clientHeight / 2);
  }
  $('#outlinePanel').hidden = true;
}

function readingKey() { return state.active ? 'mdreader.pos.' + state.active : ''; }
function saveReadingPosition() {
  const key = readingKey();
  if (!key) return;
  try {
    const value = inSourceMode() ? $('#editor').scrollTop : $('#content').scrollTop;
    localStorage.setItem(key, String(Math.round(value || 0)));
  } catch (e) { /* private mode: reading position is only a convenience */ }
}

function restoreReadingPosition() {
  const key = readingKey();
  if (!key) return;
  let value = 0;
  try { value = Number(localStorage.getItem(key)) || 0; } catch (e) { value = 0; }
  if (!value) return;
  if (inSourceMode()) $('#editor').scrollTop = value;
  else $('#content').scrollTop = value;
}


/* ------------------------------------------- recovery snapshots (B03) */
/* The desktop window writes the same kind of snapshot into the workspace, so a
   file half-written here can still be recovered there and the other way round. */
function recoveryIdentity() {
  const d = state.doc;
  if (!d || !d.info) return '';
  return d.info.path || d.identity || d.info.id || '';
}

function recoveryBaseline() {
  return (state.doc && state.doc.info && state.doc.info.revision) || 'missing';
}

let recoveryJob = null;

function scheduleRecovery() {
  if (recoveryJob) clearTimeout(recoveryJob);
  // Same default as the desktop window: about 2s after typing stops.
  recoveryJob = setTimeout(pushRecovery, 2000);
}

async function pushRecovery() {
  recoveryJob = null;
  const identity = recoveryIdentity();
  if (!identity || !state.dirty) return;
  try {
    await api('/api/recovery/save', {
      identity,
      text: $('#editor').value,
      path: (state.doc.info && state.doc.info.path) || '',
      baseline: recoveryBaseline(),
    });
  } catch (e) { /* a snapshot is best effort; never interrupt editing for it */ }
}

async function discardRecovery(savedText) {
  const identity = recoveryIdentity();
  if (!identity) return;
  try {
    await api('/api/recovery/discard', { identity, saved_text: savedText });
  } catch (e) { /* keep the snapshot rather than risk losing content */ }
}

async function newDraftFromSnapshot(text, where) {
  const res = await api('/api/loose/draft', { name: '恢复的草稿' });
  await showLoose(res.doc);
  switchMode('source');
  $('#editor').value = text;
  state.dirty = true;
  document.body.classList.add('dirty');
  $('#editorInfo').textContent = (state.active || '') + ' · 来自恢复快照 ' + where;
}

async function offerRecovery() {
  try {
    const res = await api('/api/recovery');
    const records = (res.records || []).filter(record => !record.error);
    if (!records.length) return;
    const lines = records.slice(0, 8).map(record =>
      esc(record.path || record.identity || record.key) + ' · ' +
      esc(new Date((record.time || 0) * 1000).toLocaleString()));
    const decision = await modal({
      title: '发现未保存的恢复快照',
      hint: lines.join('<br>') + '<br><br>恢复会打开成未保存的新草稿，原文件不会被覆盖。',
      choices: [{ label: '稍后再说', value: null }, { label: '恢复最近一份', value: 'restore', primary: true }],
    });
    if (!decision || decision.__choice !== 'restore') return;
    const latest = records[0];
    const one = await api('/api/recovery?key=' + encodeURIComponent(latest.key));
    const text = (one.records && one.records[0] && one.records[0].text) || '';
    if (!text) { toast('快照内容为空，无法恢复', 'err', '恢复'); return; }
    await newDraftFromSnapshot(text, latest.path || latest.identity || latest.key);
    toast('已恢复为未保存的新草稿（原文件未被覆盖）', 'ok', '恢复完成');
  } catch (e) { /* an unreadable snapshot must not block startup */ }
}

/* ------------------------------------------------------------------ boot */
function urlParam(name) {
  try { return new URLSearchParams(location.search).get(name) || ''; }
  catch (e) { return ''; }
}

(async function boot() {
  const theme = localStorage.getItem('mdreader.theme');
  setTheme(theme ||
    (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'), false);
  await loadSharedTheme();
  // Follow a theme switched in the desktop window while this page stays open.
  setInterval(() => { if (!document.hidden) loadSharedTheme(); }, 5000);
  const mode = localStorage.getItem('mdreader.mode') || 'render';
  bind();
  switchMode(mode);
  renderWelcome();
  setButtons();
  try {
    const st = await api('/api/state');
    state.version = st.version; state.engine = st.engine;
    state.workspace = st.workspace; state.projects = st.projects || [];
    rememberRecent(st.recent);
    try { const m = await api('/api/mode'); state.mode = m.mode || 'server'; }
    catch (e) { state.mode = 'server'; }
    $('#versionTag').textContent = 'v' + st.version;
    renderProjectSelect();
    state.roots = st.root_folders || [];
    renderRootSelect();
    renderFolderMeta();
    renderRootTree();
    await refreshRoots();
    mountFolderWatch();
    await refreshPlugins();
    const wantPid = urlParam('pid');
    const wantDoc = urlParam('doc');
    const wantKind = urlParam('docKind');
    if (!state.projects.length) {
      if (wantKind === 'loose' && wantDoc) await openLooseDoc(wantDoc);
      return;
    }
    const remembered = localStorage.getItem('mdreader.pid');
    const pid = projectById(wantPid) ? wantPid
              : (projectById(remembered) ? remembered : state.projects[0].id);
    await selectProject(pid, null, { silent: true });

    // a file handed over by the desktop shell (double-click / drag onto the icon)
    if (wantKind === 'loose' && wantDoc) {
      await openLooseDoc(wantDoc);
      return;
    }
    const rememberDoc = localStorage.getItem('mdreader.kind') === 'loose'
      ? null
      : localStorage.getItem('mdreader.doc');
    const target = (wantDoc && state.docs.some(d => d.id === wantDoc)) ? wantDoc : rememberDoc;
    if (target && state.docs.some(d => d.id === target)) await openDoc(target, { force: true });
    else if (state.docs.length) await openDoc(state.docs[0].id, { force: true });
    else renderEmptyProject();
  } catch (e) {
    toast(e.message, 'err', '启动失败');
    $('#renderHost').innerHTML = '<div class="welcome"><h1>无法连接本地服务</h1><p>' + esc(e.message) + '</p></div>';
  }
  // Snapshots left by an earlier session (either entry) are offered, never applied.
  offerRecovery();
})();
