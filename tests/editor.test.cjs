// Exercise the actual application functions with a small DOM/API test double.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../webui/app.js'), 'utf8');

// 编辑工具可能把 app.js 存成 CRLF 或 LF；按实际换行符定位函数末尾，
// 免得测试因为“编辑器换了行尾”而报“找不到函数”。
const EOL = source.includes('\r\n') ? '\r\n' : '\n';

function bodyOf(name) {
  const start = source.indexOf(`async function ${name}(`) >= 0
    ? source.indexOf(`async function ${name}(`)
    : source.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `Missing function ${name}`);
  const end = source.indexOf(`${EOL}}${EOL}`, start);
  assert.ok(end > start, `Unterminated function ${name}`);
  return source.slice(start, end + EOL.length + 1);
}

function setup() {
  const editor = { value: 'unsaved' };
  const state = { pid: 'project', active: 'old.md', dirty: true, doc: { raw: 'old' } };
  const calls = [];
  const context = vm.createContext({
    state, editor, calls, encodeURIComponent,
    $: () => editor,
    document: { body: { classList: { remove() {} } } },
    localStorage: { setItem() {} },
    toast() {}, renderDocument() {}, renderTree() {}, setButtons() {},
    renderProjectSelect() {}, renderProjectMeta() {}, renderEmptyProject() {},
    // helpers the extracted functions call; the snapshot timer is irrelevant here
    discardRecovery() {}, scheduleRecovery() {}, saveReadingPosition() {},
    refreshProject: async () => {},
    modal: async () => ({ __choice: 'save' }),
    api: async (url) => { calls.push(url); throw new Error('disk full'); },
  });
  for (const name of ['saveDoc', 'confirmLeaveDocument', 'openDoc', 'selectProject']) {
    vm.runInContext(bodyOf(name), context);
  }
  return { context, state, editor, calls };
}

test('failed save keeps the current document and unsaved changes', async () => {
  const { context, state, calls } = setup();
  await context.openDoc('next.md');
  assert.equal(state.active, 'old.md');
  assert.equal(state.dirty, true);
  assert.deepEqual(calls, ['/api/doc/save']);
});

test('failed save prevents switching projects', async () => {
  const { context, state, calls } = setup();
  await context.selectProject('other-project');
  assert.equal(state.pid, 'project');
  assert.equal(state.dirty, true);
  assert.deepEqual(calls, ['/api/doc/save']);
});

test('typing while saving is not erased by the response', async () => {
  const { context, state, editor } = setup();
  context.api = async () => { editor.value = 'new typing'; return { raw: 'unsaved' }; };
  assert.equal(await context.saveDoc(), false);
  assert.equal(editor.value, 'new typing');
  assert.equal(state.dirty, true);
  assert.equal(state.doc.raw, 'old');
});

test('successful save clears the dirty state', async () => {
  const { context, state } = setup();
  context.api = async () => ({ raw: 'unsaved' });
  assert.equal(await context.saveDoc(), true);
  assert.equal(state.dirty, false);
  assert.equal(state.doc.raw, 'unsaved');
});

test('cancel leaves both the project and content intact', async () => {
  const { context, state, calls } = setup();
  context.modal = async () => null;
  await context.selectProject('other-project');
  assert.equal(state.pid, 'project');
  assert.equal(state.dirty, true);
  assert.equal(calls.length, 0);
});

// ---- 网页端阅读 / 源码切换（E01：不因原生改造退化） ----
// `#editor` 是 textarea，撤销栈由浏览器持有；只要切换视图不重写它的 value，
// 撤销历史就必然保留。这里锁住“切换视图只改样式与焦点，不碰缓冲、不触发保存”。
function modeSetup() {
  const value = { text: '# 标题\n\n正文', writes: 0, focus: 0 };
  const editor = {
    get value() { return value.text; },
    set value(next) { value.writes += 1; value.text = next; },
    focus() { value.focus += 1; },
  };
  const seen = { classes: [], stored: [], saved: 0, restored: 0, clearedFind: 0, api: 0, buttons: [] };
  const button = mode => ({
    dataset: { mode },
    classList: { toggle: (cls, on) => seen.buttons.push(mode + ':' + cls + '=' + on) },
  });
  const elements = {
    '#editor': editor,
    '#findCount': { textContent: '' },
    '#outlinePanel': { hidden: false },
  };
  const context = vm.createContext({
    seen, value, editor,
    saveReadingPosition: () => { seen.saved += 1; },
    restoreReadingPosition: () => { seen.restored += 1; },
    clearFindHits: () => { seen.clearedFind += 1; },
    localStorage: { setItem: (key, v) => seen.stored.push(key + '=' + v) },
    document: { body: { classList: { toggle: (cls, on) => seen.classes.push(cls + ':' + on) } } },
    $: sel => elements[sel],
    $$: () => [button('render'), button('source')],
    setTimeout: fn => { fn(); return 1; },
    api: async () => { seen.api += 1; return {}; },
  });
  vm.runInContext(bodyOf('switchMode'), context);
  return { context, value, seen };
}

test('switching between reading and source never rewrites the editor buffer', () => {
  const { context, value, seen } = modeSetup();
  const original = value.text;
  context.switchMode('source');
  context.switchMode('render');
  context.switchMode('source');
  assert.equal(value.writes, 0, '切换视图不得写回编辑缓冲（否则会清空浏览器撤销栈）');
  assert.equal(value.text, original);
  assert.deepEqual(seen.classes, ['mode-source:true', 'mode-source:false', 'mode-source:true']);
  assert.deepEqual(seen.buttons,
                   ['render:on=false', 'source:on=true',
                    'render:on=true', 'source:on=false',
                    'render:on=false', 'source:on=true'],
                   '分段按钮的高亮必须跟着切换');
  assert.equal(value.focus, 2, '只有进入源码才把焦点交给编辑框');
});

test('switching views does not save and keeps the reading position round trip', () => {
  const { context, seen, value } = modeSetup();
  context.switchMode('render');
  assert.equal(seen.api, 0, '切换视图不得触发保存或任何接口调用');
  assert.equal(seen.saved, 1, '切走前记录阅读位置');
  assert.equal(seen.restored, 1, '切回后恢复阅读位置');
  assert.equal(seen.clearedFind, 1, '切换视图要清掉查找高亮');
  assert.deepEqual(seen.stored, ['mdreader.mode=render']);
  assert.equal(value.text, '# 标题\n\n正文');
});

// ---- 我的文件夹（F01：网页端与服务端同一套规则） ----
function folderSetup() {
  const runs = [];
  const store = {};
  const host = { innerHTML: '', querySelectorAll: () => [] };
  const elements = {
    '#folderTree': host,
    '#folderMeta': { innerHTML: '' },
    '#rootSelect': { innerHTML: '', value: '', disabled: false },
    '#btnNewFolderDoc': { disabled: false },
    '#btnRefreshFolder': { disabled: false },
    '#btnRemoveFolder': { disabled: false },
  };
  const context = vm.createContext({
    runs, host, elements, console, setTimeout, clearTimeout,
    localStorage: {
      getItem: k => (k in store ? store[k] : null),
      setItem: (k, v) => { store[k] = String(v); },
      removeItem: k => { delete store[k]; },
    },
    state: { rid: 'r1', roots: [{ id: 'r1', name: '资料', path: 'D:\\资料', readable: true }],
             rootDocs: [], rootCounts: {}, rootTruncated: '', rootSkipped: [], foldersOpen: {} },
    document: { hidden: false, body: { classList: { add() {}, remove() {}, toggle() {} } } },
    $: sel => elements[sel] || null,
    $$: () => [],
    esc: s => String(s == null ? '' : s).replace(/[&<>"']/g, c =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])),
    toast: (m, k) => runs.push('toast:' + k + ':' + m),
    confirmLeaveDocument: async () => true,
    openLooseFiles: async paths => { runs.push('open:' + paths.join(',')); return true; },
    renderNode: () => '',
    renderFolderMeta: () => {},
    store,
  });
  const names = ['buildRootNodes', 'renderRootTree', 'refreshRoots', 'refreshFolderTree',
                 'selectRoot', 'openUserFolder', 'rootById', 'renderRootSelect',
                 'newUntitledIn', 'newUntitledInRoot', 'deleteRootDoc'];
  for (const name of names) {
    vm.runInContext(bodyOf(name), context);
  }
  context.api = async (url, payload) => {
    runs.push('api:' + url);
    if (url.indexOf('/api/folder/doc') === 0) return { path: 'D:\\资料\\笔记.md' };
    if (url.indexOf('/api/folder?') === 0) {
      return { docs: [{ id: '笔记.md', name: '笔记', mtime: '2026-09-25 10:00' }],
               counts: { docs: 1 }, status: 'ok', folder: { id: 'r1', name: '资料', path: 'D:\\资料', readable: true } };
    }
    if (url === '/api/dialog/folder') return { paths: ['D:\\资料'] };
    if (url === '/api/folder/open') {
      return { folder: { id: 'r1', name: '资料', path: 'D:\\资料' }, roots: [{ id: 'r1', name: '资料' }] };
    }
    return {};
  };
  return { context, runs, host, elements };
}

test('the folder tree is built from the root-relative identities', () => {
  const { context } = folderSetup();
  const node = context.buildRootNodes([
    { id: '笔记.md', name: '笔记' },
    { id: '子目录/深入.md', name: '深入' },
  ]);
  // 值是另一个 vm realm 的对象，比较前先取成普通数组
  assert.deepEqual(Object.keys(node.dirs), ['子目录']);
  assert.deepEqual([...node.files.map(f => f.id)], ['笔记.md']);
  assert.deepEqual([...node.dirs['子目录'].files.map(f => f.id)], ['子目录/深入.md']);
});

test('switching folders asks the server for that root and keeps the identity', async () => {
  const { context, runs } = folderSetup();
  await context.selectRoot('r2');
  assert.deepEqual([...runs], ['api:/api/folder?root=r2&refresh=1']);
});

test('opening a document from the folder tree uses the absolute path identity', async () => {
  const { context, runs } = folderSetup();
  await context.refreshFolderTree(true, 'D:\\资料\\笔记.md');
  assert.deepEqual([...runs], [
    'api:/api/folder?root=r1&refresh=1',
    'open:D:\\资料\\笔记.md',
  ]);
});

test('opening a folder uses the OS picker then registers it server-side', async () => {
  const { context, runs } = folderSetup();
  context.state.rid = null;
  context.state.roots = [];
  await context.openUserFolder();
  assert.deepEqual([...runs].slice(0, 2), ['api:/api/dialog/folder', 'api:/api/folder/open']);
});

// ---- 目录行的「＋」：不弹对话框，直接生成 Untitled.md ----
function plusSetup() {
  const calls = [];
  const context = vm.createContext({
    calls, console,
    state: { pid: 'p1', rid: 'r1' },
    api: async (url, payload) => {
      calls.push('api:' + url + '|' + JSON.stringify(payload));
      if (url === '/api/doc/create') return { doc: { id: payload.dir + '/Untitled.md' } };
      if (url === '/api/folder/create') return { doc: { file: 'Untitled.md' }, path: 'D:\\资料\\Untitled.md' };
      return {};
    },
    toast: (m, k) => calls.push('toast:' + k),
    refreshProject: async () => calls.push('refreshProject'),
    openDoc: async id => calls.push('openDoc:' + id),
    refreshFolderTree: async (force, want) => calls.push('refreshFolderTree:' + String(force) + ':' + String(want)),
  });
  for (const name of ['newUntitledIn', 'newUntitledInRoot']) {
    vm.runInContext(bodyOf(name), context);
  }
  return { context, calls };
}

test('the folder plus creates Untitled in that directory without asking for a name', async () => {
  const { context, calls } = plusSetup();
  assert.equal(await context.newUntitledIn('docs'), true);
  assert.equal(calls[0], 'api:/api/doc/create|{"pid":"p1","name":"Untitled","dir":"docs"}');
  assert.ok(calls.includes('openDoc:docs/Untitled.md'), '新建后要打开它');
  assert.equal(calls.filter(c => c.startsWith('toast:err')).length, 0);
});

test('the folder plus in my-folders uses root plus relative directory', async () => {
  const { context, calls } = plusSetup();
  assert.equal(await context.newUntitledInRoot('子目录'), true);
  assert.equal(calls[0],
               'api:/api/folder/create|{"root":"r1","name":"Untitled","dir":"子目录","unique":true}');
});

test('the plus reports a failure instead of pretending success', async () => {
  const { context, calls } = plusSetup();
  context.api = async () => { throw new Error('同名文件太多'); };
  assert.equal(await context.newUntitledIn('docs'), false);
  assert.deepEqual([...calls], ['toast:err']);
});

// ---- 删除本地文件：必须确认，否则一个字节都不动 ----
function deleteSetup(choice) {
  const calls = [];
  const context = vm.createContext({
    calls, console,
    state: { rid: 'r1', rootDocs: [], doc: null },
    esc: value => String(value == null ? '' : value),
    $: () => null,
    api: async (url) => {
      calls.push('api:' + url);
      if (url.indexOf('/api/folder/doc') === 0) {
        return { path: 'D:\\资料\\笔记.md', info: { file: '笔记.md', revision: 'rev-1' } };
      }
      if (url === '/api/folder/delete') return { docs: [] };
      return {};
    },
    modal: async opts => {
      calls.push('modal:' + opts.title);
      calls.push('hint:' + opts.hint);
      return choice;
    },
    toast: (m, k) => calls.push('toast:' + k),
    renderRootTree: () => calls.push('renderRootTree'),
    renderFolderMeta: () => calls.push('renderFolderMeta'),
    setButtons: () => calls.push('setButtons'),
  });
  vm.runInContext(bodyOf('deleteRootDoc'), context);
  return { context, calls };
}

test('cancelling the delete modal touches neither the disk nor the tree', async () => {
  const { context, calls } = deleteSetup(null);
  assert.equal(await context.deleteRootDoc('笔记.md'), false);
  assert.equal(calls.filter(c => c.indexOf('/api/folder/delete') === 0).length, 0,
               '取消后不得调用删除接口');
  assert.equal(calls.filter(c => c === 'toast:ok').length, 0);
});

test('the delete modal explains the real effect and the full path', async () => {
  const { context, calls } = deleteSetup({ __choice: 'cancel' });
  await context.deleteRootDoc('笔记.md');
  const hint = calls.find(c => c.startsWith('hint:')) || '';
  assert.ok(hint.includes('删除本地文件'), '要说清删的是本地文件');
  assert.ok(hint.includes('回收站'));
  assert.ok(hint.includes('不只是从 MDReader 列表中移除'));
  assert.ok(hint.includes('D:\\资料\\笔记.md'), '要给出完整路径');
  assert.ok(calls.includes('modal:删除本地文件…'));
});

test('confirming deletes with the revision that was shown', async () => {
  const { context, calls } = deleteSetup({ __choice: 'recycle' });
  assert.equal(await context.deleteRootDoc('笔记.md'), true);
  assert.ok(calls.includes('api:/api/folder/delete'), '确认后必须真的删除');
  assert.ok(calls.includes('renderRootTree'));
  assert.ok(calls.includes('toast:ok'));
});

test('a failed delete keeps the tree and says the file was kept', async () => {
  const { context, calls } = deleteSetup({ __choice: 'recycle' });
  context.api = async url => {
    calls.push('api:' + url);
    if (url.indexOf('/api/folder/doc') === 0) {
      return { path: 'D:\\资料\\笔记.md', info: { file: '笔记.md', revision: 'rev-1' } };
    }
    throw new Error('回收站不可用，文件已保留');
  };
  assert.equal(await context.deleteRootDoc('笔记.md'), false);
  assert.ok(calls.includes('toast:err'));
  assert.equal(calls.filter(c => c === 'renderRootTree').length, 0, '失败时不要假装刷新成空树');
});

// ---- 插件（P01）：界面只展示服务端认可的可用命令 ----
function pluginSetup(overrides) {
  const calls = [];
  const dom = {};
  const make = id => (dom[id] = {
    id, innerHTML: '', textContent: '', value: '', disabled: false, style: {}, title: '',
    classList: { add: () => {}, remove: () => {} },
  });
  ['#pluginCount', '#pluginMeta', '#btnInsertImage', '#exportFormat', '#btnExportPlugin',
   '#pluginImageFile', '#editor', '#overlay'].forEach(make);
  const state = Object.assign({
    mode: 'render', dirty: false, pid: null, active: null, doc: null,
    plugins: [], pluginCommands: [],
  }, overrides || {});
  const context = vm.createContext({
    calls, state, dom, console, Event: function (type) { this.type = type; },
    btoa: value => Buffer.from(value, 'binary').toString('base64'),
    esc: value => String(value == null ? '' : value),
    $: sel => dom[sel] || null,
    $$: () => [],
    api: async (url, payload) => { calls.push('api:' + url + '|' + JSON.stringify(payload || null)); return {}; },
    toast: (m, k) => calls.push('toast:' + k + ':' + m),
    switchMode: mode => calls.push('switchMode:' + mode),
    downloadPath: () => calls.push('download'),
    setButtons() {},
  });
  for (const name of ['pluginCommands', 'pluginById', 'pluginTarget', 'renderPlugins',
                      'refreshPlugins', 'pluginStateLabel', 'pluginRowHtml', 'pluginListHtml',
                      'insertMarkdownAtCursor', 'insertImageBlob', 'insertImageWithPlugin',
                      'exportWithPlugin', 'installPluginFile']) {
    vm.runInContext(bodyOf(name), context);
  }
  return { context, state, dom, calls };
}

const IMAGE_COMMAND = {
  command: 'mdreader.sample-image:insert.image', plugin: 'mdreader.sample-image',
  plugin_name: '样例插件：图片插入', capability: 'editor.image_insert', method: 'insert_image',
};
const EXPORT_COMMAND = {
  command: 'mdreader.sample-export-a:export.samplea', plugin: 'mdreader.sample-export-a',
  plugin_name: '样例插件：导出格式 A', capability: 'export.format', method: 'export',
  title: '样例格式 A', format: 'sample-a', extension: 'samplea',
};

test('the plugin target uses the absolute path of a loose document', () => {
  const { context } = pluginSetup({
    doc: { kind: 'loose', info: { path: 'D:\\资料\\笔记.md', revision: 'rev-1' } },
  });
  assert.deepEqual(JSON.parse(JSON.stringify(context.pluginTarget())),
                   { doc: 'D:\\资料\\笔记.md', revision: 'rev-1' });
});

test('the plugin target uses pid plus document id inside a project', () => {
  const { context } = pluginSetup({
    pid: 'p1', active: 'docs/一.md', doc: { kind: 'project', info: { id: 'docs/一.md' } },
  });
  assert.deepEqual(JSON.parse(JSON.stringify(context.pluginTarget())),
                   { pid: 'p1', doc: 'docs/一.md' });
});

test('an unsaved draft has no plugin target', () => {
  const { context } = pluginSetup({
    doc: { kind: 'loose', info: { path: '', draft: true } },
  });
  assert.equal(context.pluginTarget(), null);
});

test('the image button stays disabled without a command or without a document', () => {
  const { context, dom } = pluginSetup({
    doc: { kind: 'loose', info: { path: 'D:\\资料\\笔记.md' } },
  });
  context.renderPlugins();
  assert.equal(dom['#btnInsertImage'].disabled, true, '没有插件时不能点');

  context.state.pluginCommands = [IMAGE_COMMAND];
  context.renderPlugins();
  assert.equal(dom['#btnInsertImage'].disabled, false);
  assert.ok(dom['#btnInsertImage'].title.includes('样例插件'));

  context.state.doc = { kind: 'loose', info: { path: '', draft: true } };
  context.renderPlugins();
  assert.equal(dom['#btnInsertImage'].disabled, true, '未保存的草稿不能插图');
});

test('plugin export formats fill the selector only when available', () => {
  const { context, dom } = pluginSetup({});
  context.renderPlugins();
  assert.equal(dom['#exportFormat'].style.display, 'none');
  assert.equal(dom['#btnExportPlugin'].style.display, 'none');

  context.state.pluginCommands = [EXPORT_COMMAND];
  context.renderPlugins();
  assert.equal(dom['#exportFormat'].style.display, '');
  assert.ok(dom['#exportFormat'].innerHTML.includes('.samplea'));
  assert.ok(dom['#exportFormat'].innerHTML.includes('样例格式 A'));
  assert.equal(dom['#pluginCount'].textContent, '1');
});

test('the manager lists state, reason, dependencies and actions', () => {
  const { context } = pluginSetup({});
  const html = context.pluginListHtml([
    { id: 'mdreader.sample-export-b', name: '样例 B', version: '2.0.0', installed: true,
      enabled: true, state: 'enabled', reason: '', commands: [{ title: '样例格式 B' }],
      dependencies: [] },
    { id: 'mdreader.sample-dependent', name: '依赖 B', version: '1.0.0', installed: true,
      enabled: false, state: 'unavailable', reason: '缺少依赖插件：样例 B', commands: [],
      dependencies: [{ id: 'mdreader.sample-export-b', name: '样例 B', enabled: false }] },
    { id: 'mdreader.export-pdf', name: 'mdreader.export-pdf', version: '', installed: false,
      enabled: false, state: 'not-installed', reason: '尚未发布：F07 交付', commands: [] },
  ]);
  assert.ok(html.includes('已启用'));
  assert.ok(html.includes('不可用') && html.includes('缺少依赖插件'));
  assert.ok(html.includes('依赖：样例 B'));
  assert.ok(html.includes('未安装') && html.includes('尚未发布'));
  assert.ok(html.includes('data-plugin-toggle="mdreader.sample-export-b"'));
  assert.ok(html.includes('data-enabled="1"'), '禁用中的插件按钮应该是“启用”');
  assert.ok(!html.includes('data-plugin-remove="mdreader.export-pdf"'), '未安装的插件没有卸载按钮');
});

test('inserting plugin markdown goes through the editor as one change', () => {
  const inserted = [];
  const { context, dom } = pluginSetup({
    doc: { kind: 'loose', info: { path: 'D:\\资料\\笔记.md' } },
  });
  const editor = {
    value: 'abcdef', selectionStart: 2, selectionEnd: 4,
    dispatchEvent: event => inserted.push(event.type),
    focus() {},
  };
  context.$ = sel => (sel === '#editor' ? editor : (dom[sel] || null));
  context.insertMarkdownAtCursor('![图](assets/x.png)');
  assert.equal(editor.value, 'ab![图](assets/x.png)ef');
  assert.equal(editor.selectionStart, 2 + '![图](assets/x.png)'.length, '光标停在新片段之后');
  assert.equal(inserted.filter(c => c === 'input').length, 1, '要触发 input，让页面标记未保存');
});

test('image insert without a target does not open the picker', async () => {
  const { context, dom, calls } = pluginSetup({ doc: { kind: 'loose', info: { path: '' } } });
  context.state.pluginCommands = [IMAGE_COMMAND];
  let clicked = 0;
  dom['#pluginImageFile'].click = () => { clicked += 1; };
  await context.insertImageWithPlugin();
  assert.equal(clicked, 0);
  assert.ok(calls.some(c => c.startsWith('toast:warn')));
});

test('image insert sends the document identity and revision to the server', async () => {
  const { context, dom, calls } = pluginSetup({
    doc: { kind: 'loose', info: { path: 'D:\\资料\\笔记.md', revision: 'rev-7' } },
  });
  context.state.pluginCommands = [IMAGE_COMMAND];
  const editor = {
    value: '', selectionStart: 0, selectionEnd: 0,
    dispatchEvent: () => {}, focus() {},
  };
  context.$ = sel => (sel === '#editor' ? editor : (dom[sel] || null));
  context.api = async (url, payload) => {
    calls.push('api:' + url + '|' + JSON.stringify(payload || null));
    if (url === '/api/plugins/insert') {
      return { markdown: '![图](assets/图.png)', assets_dir: 'D:\\资料\\assets' };
    }
    return {};
  };
  const input = dom['#pluginImageFile'];
  input.click = () => {};
  context.insertImageWithPlugin();
  input.files = [{ name: '图.png', arrayBuffer: async () => new Uint8Array([1, 2, 3]).buffer }];
  await input.onchange();
  const call = calls.find(c => c.startsWith('api:/api/plugins/insert'));
  assert.ok(call, '必须调用插入接口');
  const payload = JSON.parse(call.split('|')[1]);
  assert.equal(payload.doc, 'D:\\资料\\笔记.md');
  assert.equal(payload.revision, 'rev-7');
  assert.equal(payload.name, '图.png');
  assert.equal(payload.command, IMAGE_COMMAND.command);
  assert.equal(editor.value, '![图](assets/图.png)', '插入的必须是插件给的片段');
});

test('export sends the buffer text only while there are unsaved edits', async () => {
  const { context, dom, calls } = pluginSetup({
    mode: 'server', dirty: true,
    doc: { kind: 'loose', info: { path: 'D:\\资料\\笔记.md', revision: 'rev-2', file: '笔记.md' } },
    pluginCommands: [EXPORT_COMMAND],
  });
  dom['#exportFormat'].value = EXPORT_COMMAND.command;
  dom['#editor'].value = '# 当前编辑内容';
  context.api = async (url, payload) => {
    calls.push('api:' + url + '|' + JSON.stringify(payload || null));
    if (url === '/api/dialog/save') return { path: 'D:\\导出\\笔记.samplea' };
    if (url === '/api/plugins/export') return { path: 'D:\\导出\\笔记.samplea', warnings: [] };
    return {};
  };
  await context.exportWithPlugin();
  const call = calls.find(c => c.startsWith('api:/api/plugins/export'));
  const payload = JSON.parse(call.split('|')[1]);
  assert.equal(payload.dest, 'D:\\导出\\笔记.samplea');
  assert.equal(payload.command, EXPORT_COMMAND.command);
  assert.equal(payload.markdown, '# 当前编辑内容', '有未保存编辑时带上当前编辑内容');
  assert.ok(calls.some(c => c.startsWith('toast:ok')));

  // 没有未保存编辑时走磁盘版本：不带 markdown 字段
  context.state.dirty = false;
  calls.length = 0;
  await context.exportWithPlugin();
  const clean = JSON.parse(calls.find(c => c.startsWith('api:/api/plugins/export')).split('|')[1]);
  assert.equal(clean.markdown, undefined, '没有编辑时让服务端读磁盘版本');
});

test('export without a saved document is refused before choosing a path', async () => {
  const { context, calls } = pluginSetup({
    doc: { kind: 'loose', info: { path: '', draft: true } },
    pluginCommands: [EXPORT_COMMAND],
  });
  await context.exportWithPlugin();
  assert.equal(calls.filter(c => c.startsWith('api:')).length, 0);
  assert.ok(calls.some(c => c.startsWith('toast:warn')));
});

// ---- F06：导出前先问来源、再看预检报告 ----
function exportFlowSetup(plan) {
  const calls = [];
  const editor = { value: '# 改过但没保存\n' };
  const dom = { '#exportFormat': { value: EXPORT_COMMAND.command }, '#editor': editor };
  const state = {
    mode: 'server', dirty: true, pluginCommands: [EXPORT_COMMAND],
    doc: { kind: 'loose', info: { path: 'D:\\资料\\笔记.md', revision: 'rev-1', file: '笔记.md' } },
  };
  const context = vm.createContext({
    calls, state, dom, editor, console,
    $: sel => dom[sel] || null,
    $$: () => [],
    esc: value => String(value == null ? '' : value),
    toast: (message, kind) => calls.push('toast:' + kind + ':' + message),
    downloadPath: () => {},
    api: async (url, payload) => {
      calls.push('api:' + url + '|' + JSON.stringify(payload || null));
      if (url === '/api/export/check') return plan.shift();
      if (url === '/api/dialog/save') return { path: 'D:\\资料\\笔记.pdf' };
      return { path: 'D:\\资料\\笔记.pdf', warnings: [] };
    },
    modal: async opts => {
      calls.push('modal:' + (opts.choices || []).map(c => c.value).join(','));
      return { __choice: 'buffer' };
    },
  });
  for (const name of ['exportWithPlugin', 'pluginTarget']) {
    vm.runInContext(bodyOf(name), context);
  }
  return { context, calls, state };
}

test('an unsaved document is asked which copy to export', async () => {
  const { context, calls } = exportFlowSetup([
    { needs_source: true, source: 'buffer', preflight: { errors: [], warnings: [] } },
    { needs_source: false, source: 'buffer', dest: '', dest_exists: false,
      preflight: { errors: [], warnings: [] } },
  ]);
  await context.exportWithPlugin();
  assert.ok(calls.some(c => c.startsWith('modal:cancel,disk,buffer')),
            '有未保存修改时必须先让用户选来源');
  const check = calls.find(c => c.includes('/api/export/check') && c.includes('"source":"buffer"'));
  assert.ok(check, '选定之后再带着 source 重新问一次核心');
  assert.ok(calls.some(c => c.includes('/api/plugins/export')), '最后仍然走同一个导出接口');
});

test('preflight warnings are shown before the file is written', async () => {
  const { context, calls } = exportFlowSetup([
    { needs_source: false, source: 'buffer', dest: '', dest_exists: false,
      preflight: { errors: [], warnings: [{ kind: 'dead_link', message: '有 1 个本地链接指向的文件不存在' }] } },
  ]);
  context.exportReportModal = async (report, dest, exists) => {
    calls.push('report:' + report.warnings.length + ':' + (dest || '') + ':' + !!exists);
    return true;
  };
  await context.exportWithPlugin();
  const report = calls.find(c => c.startsWith('report:'));
  assert.equal(report, 'report:1::false', '报告要拿到警告条数与目标信息');
  const sent = calls.find(c => c.includes('/api/plugins/export'));
  assert.ok(sent && sent.includes('"confirm":true'), '确认之后才带着 confirm 导出');
});

test('a blocked preflight never reaches the plugin', async () => {
  const { context, calls } = exportFlowSetup([
    { needs_source: false, source: 'buffer', dest: '', dest_exists: false,
      preflight: { errors: [{ kind: 'missing_image', message: '有 1 张图片找不到：assets/没有.png' }],
                   warnings: [] } },
  ]);
  context.exportReportModal = async () => { calls.push('report'); return false; };
  await context.exportWithPlugin();
  assert.ok(calls.includes('report'));
  assert.equal(calls.filter(c => c.includes('/api/plugins/export')).length, 0,
               '有严重问题时不能调用导出');
});


test('a rejected package reports the trust reason instead of pretending success', async () => {
  const { context, calls } = pluginSetup({});
  context.api = async (url, payload) => {
    calls.push('api:' + url + '|' + JSON.stringify(payload || null));
    throw new Error('这个包不在受信清单里，已拒绝安装：mdreader.evil');
  };
  await context.installPluginFile({
    name: 'evil.zip', arrayBuffer: async () => new Uint8Array([1]).buffer,
  });
  const failure = calls.find(c => c.startsWith('toast:err'));
  assert.ok(failure && failure.includes('受信清单'));
  assert.ok(!calls.some(c => c.startsWith('toast:ok')));
});

function helpers() {
  const context = vm.createContext({});
  for (const name of ['findMatches', 'outlineFromMarkdown']) {
    vm.runInContext(bodyOf(name), context);
  }
  return context;
}

test('findMatches lists every hit, including overlapping words', () => {
  const { findMatches } = helpers();
  // Values come from another vm realm, so compare plain strings.
  const hits = (text, needle, limit) =>
    findMatches(text, needle, limit).map(hit => hit.start + '-' + hit.end).join(',');
  assert.equal(hits('中文abc中文', '中文'), '0-2,5-7');
  assert.equal(hits('aAaA', 'a'), '0-1,1-2,2-3,3-4');
  assert.equal(hits('abc', ''), '');
  assert.equal(hits('abc', 'zzz'), '');
  assert.equal(findMatches('x'.repeat(50), 'x', 10).length, 10);
});

// ---- F09：链接检查（只查当前这一篇，点一条跳到源码那一行） ----
function linkCheckSetup(issues) {
  const calls = [];
  const editor = {
    value: '第一行\n第二行\n第三行有问题\n第四行\n',
    selectionStart: 0, selectionEnd: 0, scrollTop: 0,
    focus() {}, setSelectionRange(a, b) { editor.selectionStart = a; editor.selectionEnd = b; },
  };
  const dom = { '#editor': editor };
  const state = {
    mode: 'render', dirty: true, doc: { kind: 'loose', info: { path: 'D:\\资料\\笔记.md', revision: 'r1' } },
  };
  const context = vm.createContext({
    calls, state, editor, dom, console,
    $: sel => dom[sel] || null,
    $$: () => [],
    esc: value => String(value == null ? '' : value),
    toast: (message, kind) => calls.push('toast:' + kind + ':' + message),
    switchMode: mode => calls.push('switchMode:' + mode),
    api: async (url, payload) => {
      calls.push('api:' + url + '|' + JSON.stringify(payload || null));
      return { report: { issues, errors: issues.filter(i => i.level === 'error'),
                         warnings: issues.filter(i => i.level === 'warn'),
                         checked: issues.length, summary: '有 ' + issues.length + ' 条' },
               name: '笔记.md' };
    },
  });
  for (const name of ['checkDocumentLinks', 'pluginTarget', 'jumpToSourceLine']) {
    vm.runInContext(bodyOf(name), context);
  }
  return { context, calls, editor };
}

test('a clean document reports success without opening a modal', async () => {
  const { context, calls } = linkCheckSetup([]);
  assert.equal(await context.checkDocumentLinks(), true);
  assert.ok(calls.some(c => c.startsWith('toast:ok') && c.includes('没有发现问题')));
});

test('link problems jump to the offending source line', () => {
  const { context, editor, calls } = linkCheckSetup([]);
  assert.equal(context.jumpToSourceLine(3), true);
  assert.ok(calls.includes('switchMode:source'), '定位前要先切到源码模式');
  assert.equal(editor.selectionStart, '第一行\n第二行\n'.length);
  assert.equal(editor.selectionEnd, '第一行\n第二行\n第三行有问题'.length);
});

test('the check sends the buffer identity and the current text', async () => {
  const { context, calls, editor } = linkCheckSetup([
    { level: 'error', kind: 'image', line: 3, source: 'assets/没有.png', message: '找不到这个文件' },
  ]);
  context.linkReportModal = async (report, name) => {
    calls.push('report:' + report.issues.length + ':' + name);
    return true;
  };
  await context.checkDocumentLinks();
  const sent = calls.find(c => c.includes('/api/check/links'));
  assert.ok(sent && sent.includes('D:\\\\资料\\\\笔记.md'), '要用文档身份去查');
  assert.ok(sent.includes('第三行有问题'), '未保存时把当前正文一起发过去');
  assert.ok(calls.includes('report:1:笔记.md'));
});

test('switching theme repoints the locally rendered formula images', () => {
  const img = {
    attrs: { src: '/api/formula?tex=x&size=17&theme=light&scale=1&display=0' },
    getAttribute(name) { return this.attrs[name]; },
    setAttribute(name, value) { this.attrs[name] = value; },
  };
  const other = {
    attrs: { src: '/api/file?pid=p&p=图.png' },
    getAttribute(name) { return this.attrs[name]; },
    setAttribute(name, value) { this.attrs[name] = value; },
  };
  const selector = [];
  const context = vm.createContext({
    selector, img, other,
    document: { documentElement: { dataset: {} } },
    localStorage: { setItem() {} },
    api: async () => ({}),
    $: () => null,
    $$: sel => { selector.push(sel); return sel === '#renderHost img.formula-img' ? [img] : []; },
  });
  vm.runInContext(bodyOf('setTheme'), context);
  context.setTheme('dark', false);
  assert.match(img.attrs.src, /theme=dark/);
  assert.match(img.attrs.src, /tex=x/, '只换主题参数，其余查询参数保持原样');
  assert.equal(other.attrs.src, '/api/file?pid=p&p=图.png', '普通图片不受影响');
  assert.equal(context.document.documentElement.dataset.theme, 'dark');
});

test('outlineFromMarkdown keeps levels and skips fenced code', () => {
  const { outlineFromMarkdown } = helpers();
  const rows = outlineFromMarkdown([
    '# 一级',
    '正文',
    '```',
    '## 代码里的井号不是标题',
    '```',
    '### 三级 ###',
    '####### 七个井号不算标题',
  ].join('\n'));
  assert.equal(rows.map(r => r.level + ':' + r.title + ':' + r.line).join('|'),
               '1:一级:1|3:三级:6');
});

// ---- 表格与常用格式（F04）：规则在核心，网页端只负责收发与写回 ----
// 这段源码整块执行：其中还有一个模块级常量（对齐方式），
// 逐个抽函数会漏掉它，所以按区块取。
function f04Block() {
  const start = source.indexOf('/* ------------------------------------- 表格与常用格式（F04） */');
  const end = source.indexOf('/* ---------------------------------------------------------------- export */');
  assert.ok(start >= 0 && end > start, 'Missing F04 section in app.js');
  return source.slice(start, end);
}

function f04Setup(overrides) {
  const calls = [];
  const seen = { input: 0, focus: 0, prevented: 0 };
  const editor = {
    value: '一\n二\n', selectionStart: 0, selectionEnd: 0,
    dispatchEvent: event => { if (event.type === 'input') seen.input += 1; },
    focus() { seen.focus += 1; },
    setSelectionRange(a, b) { editor.selectionStart = a; editor.selectionEnd = b; },
  };
  const overlay = {
    innerHTML: '', classList: { add: () => {}, remove: () => {} },
    querySelector: () => null, querySelectorAll: () => [],
  };
  const state = Object.assign({ doc: { kind: 'loose', info: { path: 'D:\\甲.md' } } }, overrides || {});
  const context = vm.createContext({
    calls, seen, state, editor, overlay, console,
    Event: function (type) { this.type = type; },
    document: { addEventListener: () => {}, removeEventListener: () => {} },
    navigator: { clipboard: { readText: async () => '' } },
    $: sel => (sel === '#editor' ? editor : (sel === '#overlay' ? overlay : null)),
    $$: () => [],
    esc: value => String(value == null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
    toast: (message, kind) => calls.push('toast:' + kind + ':' + message),
    switchMode: mode => calls.push('switchMode:' + mode),
    api: async (url, payload) => {
      calls.push('api:' + url + '|' + JSON.stringify(payload || null));
      return { text: editor.value, start: 0, end: 0, note: '已应用格式' };
    },
  });
  vm.runInContext(f04Block(), context);
  // onEditorPaste 会先问一句“剪贴板里是不是图片”，这两个纯函数必须在同一上下文里
  for (const name of ['isImageName', 'clipboardImageFile']) {
    vm.runInContext(bodyOf(name), context);
  }
  return { context, state, editor, calls, seen };
}

test('a format action sends the buffer and the selection to the shared rules', async () => {
  const { context, editor, calls, seen } = f04Setup();
  editor.value = '重点';
  editor.selectionStart = 0;
  editor.selectionEnd = 2;
  context.api = async (url, payload) => {
    calls.push('api:' + url + '|' + JSON.stringify(payload));
    return { text: '**重点**', start: 2, end: 4, note: '已应用格式' };
  };
  assert.equal(await context.applyFormat('bold'), true);
  assert.ok(calls.some(c => c.startsWith('switchMode:source')), '点工具栏要先进源码模式');
  const request = JSON.parse(calls.find(c => c.startsWith('api:')).split('|')[1]);
  assert.deepEqual(request,
                   { text: '重点', start: 0, end: 2, action: 'bold' });
  assert.equal(editor.value, '**重点**');
  assert.equal(editor.selectionStart, 2);
  assert.equal(editor.selectionEnd, 4);
  assert.equal(seen.input, 1, '写回后要派发一次 input，页面才会显示未保存');
});

test('formula control uses the shared format endpoint and selects the expression', async () => {
  const { context, editor, calls } = f04Setup({ active: '公式.md' });
  editor.value = 'x';
  editor.selectionStart = 0;
  editor.selectionEnd = 1;
  context.api = async (url, payload) => {
    calls.push('api:' + url + '|' + JSON.stringify(payload));
    return { text: '$x$', start: 1, end: 2, note: '已插入行内公式' };
  };
  assert.equal(await context.applyFormat('formula_inline'), true);
  assert.equal(editor.value, '$x$');
  assert.equal(editor.selectionStart, 1);
  assert.equal(editor.selectionEnd, 2);
  assert.equal(JSON.parse(calls.find(c => c.startsWith('api:')).split('|')[1]).action, 'formula_inline');
});

test('a delayed formula response cannot replace subsequent typing', async () => {
  const { context, editor } = f04Setup({ active: '公式.md' });
  editor.value = 'x';
  context.api = async () => {
    editor.value = '用户后来输入的内容';
    return { text: '$x$', start: 1, end: 2 };
  };
  assert.equal(await context.applyFormat('formula_inline'), false);
  assert.equal(editor.value, '用户后来输入的内容');
});

test('a rejected format action leaves the buffer alone', async () => {
  const { context, editor, calls } = f04Setup();
  editor.value = '不该被改';
  context.api = async () => { throw new Error('这个表格不能安全修改：各行的首尾竖线写法不一致'); };
  assert.equal(await context.applyFormat('bullets'), false);
  assert.equal(editor.value, '不该被改');
  assert.ok(calls.some(c => c.startsWith('toast:err') && c.includes('首尾竖线')));
});

test('formatting without an open document does not call the server', async () => {
  const { context, calls } = f04Setup({ doc: null });
  assert.equal(await context.applyFormat('bold'), false);
  assert.equal(calls.filter(c => c.startsWith('api:')).length, 0);
  assert.ok(calls.some(c => c.startsWith('toast:warn')));
});

test('an unchanged buffer is not rewritten (the undo stack is left alone)', () => {
  const { context, editor, seen } = f04Setup();
  editor.value = '原样';
  context.applyEditResult({ text: '原样', start: 1, end: 1 });
  assert.equal(seen.input, 0, '内容没变就不要触发 input');
  assert.equal(editor.selectionStart, 1);
});

test('insert table sends columns, alignment and filled cells', async () => {
  const { context, calls } = f04Setup();
  context.tableModal = async () => ({
    columns: 2, headerOn: true, header: ['名称', '数量'],
    rows: [['甲', '1'], ['乙', '2']], aligns: ['left', 'right'],
  });
  assert.equal(await context.insertTableDialog(), true);
  const request = JSON.parse(calls.find(c => c.startsWith('api:')).split('|')[1]);
  assert.equal(request.op, 'insert_table');
  assert.deepEqual(request.args.fills, [['名称', '数量'], ['甲', '1'], ['乙', '2']]);
  assert.deepEqual(request.args.aligns, ['left', 'right']);
  assert.equal(request.args.header, true);
});

test('a table without a header does not smuggle an extra empty row', async () => {
  const { context, calls } = f04Setup();
  context.tableModal = async () => ({
    columns: 2, headerOn: false, header: ['', ''],
    rows: [['甲', '1']], aligns: ['left', 'left'],
  });
  await context.insertTableDialog();
  const request = JSON.parse(calls.find(c => c.startsWith('api:')).split('|')[1]);
  assert.deepEqual(request.args.fills, [['甲', '1']], '不勾表头时首行就是数据行');
  assert.equal(request.args.header, false);
});

test('edit table reads first and writes the edited grid back', async () => {
  const { context, calls } = f04Setup();
  context.api = async (url, payload) => {
    calls.push('api:' + url + '|' + JSON.stringify(payload));
    if (payload.op === 'read') {
      return { table: { header: ['名称'], rows: [['甲']], aligns: ['center'] } };
    }
    return { text: '| 名称 |\n| :---: |\n| 乙 |\n', start: 0, end: 4 };
  };
  context.tableModal = async (mode, model) => {
    calls.push('modal:' + mode + ':' + JSON.stringify(model));
    return { columns: 1, headerOn: true, header: ['名称'], rows: [['乙']], aligns: ['center'] };
  };
  assert.equal(await context.editTableDialog(), true);
  const requests = calls.filter(c => c.startsWith('api:')).map(c => JSON.parse(c.split('|')[1]));
  assert.equal(requests[0].op, 'read');
  assert.equal(requests[1].op, 'set_table');
  assert.deepEqual(requests[1].args, { header: ['名称'], rows: [['乙']], aligns: ['center'] });
});

test('an oversized table is not opened in the dialog', async () => {
  const { context, calls } = f04Setup();
  context.api = async (url, payload) => {
    calls.push('api:' + url + '|' + JSON.stringify(payload));
    return { table: { header: ['a'], rows: new Array(61).fill(['x']), aligns: ['left'] } };
  };
  let opened = 0;
  context.tableModal = async () => { opened += 1; return null; };
  assert.equal(await context.editTableDialog(), false);
  assert.equal(opened, 0, '超规模的表不进弹层（否则提交时会删掉没显示的行）');
  assert.ok(calls.some(c => c.startsWith('toast:warn')));
});

test('a normal paste is never intercepted', () => {
  const { context, calls } = f04Setup();
  let prevented = 0;
  context.onEditorPaste({
    clipboardData: { getData: () => '普通一行文本' },
    preventDefault: () => { prevented += 1; },
  });
  assert.equal(prevented, 0);
  assert.equal(calls.filter(c => c.startsWith('api:')).length, 0);
});

test('pasting tab separated text previews it and replaces the selection', async () => {
  const { context, editor, calls } = f04Setup();
  editor.value = '开场\n旧内容\n结尾\n';
  editor.selectionStart = 3;
  editor.selectionEnd = 7;
  const tsv = '名称\t数量\n甲\t1\n';
  context.api = async (url, payload) => {
    calls.push('api:' + url + '|' + JSON.stringify(payload));
    if (payload.op === 'parse') {
      return { columns: 2, rows: [['名称', '数量'], ['甲', '1']], warnings: ['第 2 行含引号'] };
    }
    return { text: '开场\n| 名称 | 数量 |\n| --- | --- |\n| 甲 | 1 |\n结尾\n', start: 3, end: 4 };
  };
  context.pasteTableModal = async preview => {
    calls.push('preview:' + JSON.stringify(preview));
    return { header: true };
  };
  let prevented = 0;
  await context.onEditorPaste({
    clipboardData: { getData: () => tsv },
    preventDefault: () => { prevented += 1; },
  });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(prevented, 1, 'TSV 粘贴要被接管');
  const requests = calls.filter(c => c.startsWith('api:')).map(c => JSON.parse(c.split('|')[1]));
  assert.equal(requests[0].op, 'parse');
  assert.equal(requests[1].op, 'paste');
  assert.equal(requests[1].payload, tsv);
  assert.equal(requests[1].start, 3);
  assert.equal(requests[1].end, 7);
  assert.equal(requests[1].header, true);
  assert.ok(calls.some(c => c.startsWith('preview:') && c.includes('含引号')),
            '警告要摆给用户看，不能悄悄补列');
});

/* --------------------------------------------- F03 图片插入（截图 / 拖入） */

const F03_IMAGE_COMMAND = {
  capability: 'editor.image_insert', plugin: 'mdreader.image-insert',
  command: 'mdreader.image-insert.insert',
};

function f03ImageFile(name, bytes) {
  const data = Uint8Array.from(bytes || [137, 80, 78, 71]);
  return { name, arrayBuffer: async () => data.buffer };
}

/* onEditorPaste 现在会先问“剪贴板里是不是图片”，所以这两个 helper 必须在
   同一个上下文里；插件命令与宿主目标由用例自己给。 */
function f03Setup(overrides) {
  const base = f04Setup(overrides);
  const { context, calls } = base;
  context.btoa = value => Buffer.from(value, 'binary').toString('base64');
  context.pluginCommands = capability => (context.state.pluginCommands || [])
    .filter(c => !capability || c.capability === capability);
  context.pluginTarget = () => (context.state.pluginTarget === undefined
    ? { doc: 'D:\\笔记.md', revision: 'r1' } : context.state.pluginTarget);
  context.insertMarkdownAtCursor = text => { calls.push('insert:' + text); };
  vm.runInContext(bodyOf('insertImageBlob'), context);
  return base;
}

test('a pasted screenshot is handed to the image plugin and inserted', async () => {
  const { context, calls } = f03Setup({ pluginCommands: [F03_IMAGE_COMMAND] });
  context.api = async (url, payload) => {
    calls.push('api:' + url + '|' + JSON.stringify(payload));
    return { markdown: '![截图](assets/shot.png)\n', assets: [], assets_dir: 'D:\\笔记\\assets' };
  };
  let prevented = 0;
  context.onEditorPaste({
    clipboardData: {
      items: [{ kind: 'file', type: 'image/png',
                getAsFile: () => f03ImageFile('clipboard.png', [137, 80, 78, 71]) }],
      getData: () => '',
    },
    preventDefault: () => { prevented += 1; },
  });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(prevented, 1, '粘贴截图要被接管，不能当普通文本粘贴放过去');
  const request = JSON.parse(calls.find(c => c.startsWith('api:')).split('|')[1]);
  assert.equal(request.b64, 'iVBORw==', '图片以 base64 交给宿主，不进 Markdown');
  assert.equal(request.name, 'clipboard.png');
  assert.equal(request.doc, 'D:\\笔记.md');
  assert.ok(calls.includes('insert:![截图](assets/shot.png)\n'), calls.join('\n'));
  assert.ok(calls.some(c => c.startsWith('toast:ok:')), '插入成功要有提示');
});

test('a screenshot without the image plugin is refused with a warning', async () => {
  const { context, calls } = f03Setup({ pluginCommands: [] });
  let prevented = 0;
  context.onEditorPaste({
    clipboardData: {
      items: [{ kind: 'file', type: 'image/png', getAsFile: () => f03ImageFile('c.png') }],
      getData: () => '',
    },
    preventDefault: () => { prevented += 1; },
  });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(prevented, 1);
  assert.equal(calls.filter(c => c.startsWith('api:')).length, 0, '插件禁用时不该调用宿主');
  assert.ok(calls.some(c => c.startsWith('toast:warn:')));
});

test('inserting an image without a saved document does not call the host', async () => {
  const { context, calls } = f03Setup({ pluginCommands: [F03_IMAGE_COMMAND], pluginTarget: null });
  assert.equal(await context.insertImageBlob(f03ImageFile('a.png'), '插入成功'), false);
  assert.equal(calls.filter(c => c.startsWith('api:')).length, 0);
  assert.ok(calls.some(c => c.startsWith('toast:warn:')));
});

test('a pending image task inserts nothing', async () => {
  const { context, calls } = f03Setup({ pluginCommands: [F03_IMAGE_COMMAND] });
  context.api = async url => { calls.push('api:' + url); return { pending: true, task: { id: 't1' } }; };
  assert.equal(await context.insertImageBlob(f03ImageFile('a.png'), '插入成功'), false);
  assert.equal(calls.filter(c => c.startsWith('insert:')).length, 0, '还没处理完不能改正文');
  assert.ok(calls.some(c => c.startsWith('toast:warn:')));
});

test('dropped or pasted images are recognised by MIME or extension', () => {
  const { context } = f03Setup();
  const byMime = context.clipboardImageFile({
    items: [{ kind: 'file', type: 'image/jpeg', getAsFile: () => ({ name: 'x.jpg' }) }],
  });
  assert.equal(byMime.name, 'x.jpg');
  assert.equal(context.clipboardImageFile({ files: [{ name: '照片.WEBP', type: '' }] }).name,
               '照片.WEBP', '没有 MIME 时看扩展名，大小写无关');
  assert.equal(context.clipboardImageFile({ files: [{ name: 'note.md', type: 'text/markdown' }] }), null);
  assert.equal(context.clipboardImageFile({ files: [] }), null);
  assert.equal(context.clipboardImageFile(null), null);
});

test('the paste preview keeps warnings and the row count visible', () => {
  const { context } = f04Setup();
  let html = '';
  const overlay = {
    set innerHTML(value) { html = value; },
    get innerHTML() { return html; },
    classList: { add: () => {}, remove: () => {} },
    querySelector: () => null, querySelectorAll: () => [],
  };
  context.$ = sel => (sel === '#overlay' ? overlay : null);
  context.document = { addEventListener: () => {}, removeEventListener: () => {} };
  context.$$ = () => [];
  context.pasteTableModal({ columns: 2, rows: [['甲', '1']], warnings: ['第 2 行的列数与第一行不同，已按空单元格补齐'] });
  assert.ok(html.includes('2 列 × 1 行'), html);
  assert.ok(html.includes('已按空单元格补齐'));
});
