// Exercise the web page's find/outline DOM logic with a small DOM double.
// The pure helpers are covered in editor.test.cjs; this file covers the parts
// that touch the document tree (wrapping matches, unwrapping them again).
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../webui/app.js'), 'utf8');

/* --------------------------------------------------------------- DOM double */
class TextNode {
  constructor(value) { this.nodeName = '#text'; this.nodeValue = String(value); this.parentNode = null; }
  get textContent() { return this.nodeValue; }
}

class Element {
  constructor(tag) {
    this.nodeName = String(tag).toUpperCase();
    this.tagName = this.nodeName;
    this.childNodes = [];
    this.parentNode = null;
    this.className = '';
    this.hidden = false;
    this.value = '';
    this.dataset = {};
  }
  get textContent() {
    return this.childNodes.map(child => ('nodeValue' in child ? child.nodeValue : child.textContent)).join('');
  }
  set textContent(value) {
    this.childNodes = [];
    this.appendChild(new TextNode(value));
  }
  get classList() {
    const self = this;
    return {
      add: name => { if (!self.className.split(' ').includes(name)) self.className = (self.className + ' ' + name).trim(); },
      remove: name => { self.className = self.className.split(' ').filter(x => x && x !== name).join(' '); },
      toggle: (name, on) => { if (on) self.classList.add(name); else self.classList.remove(name); },
      contains: name => self.className.split(' ').includes(name),
    };
  }
  appendChild(node) {
    if (node && node.isFragment) {
      for (const child of node.childNodes.slice()) { child.parentNode = this; this.childNodes.push(child); }
      node.childNodes = [];
      return node;
    }
    node.parentNode = this;
    this.childNodes.push(node);
    return node;
  }
  replaceChild(next, old) {
    const index = this.childNodes.indexOf(old);
    assert.ok(index >= 0, 'replaceChild target not found');
    if (next && next.isFragment) {
      const moved = next.childNodes.slice();
      for (const child of moved) child.parentNode = this;
      next.childNodes = [];
      this.childNodes.splice(index, 1, ...moved);
      return old;
    }
    next.parentNode = this;
    this.childNodes.splice(index, 1, next);
    return old;
  }
  normalize() {
    const merged = [];
    for (const child of this.childNodes) {
      const previous = merged[merged.length - 1];
      if (previous && previous.nodeName === '#text' && child.nodeName === '#text') {
        previous.nodeValue += child.nodeValue;
      } else {
        merged.push(child);
      }
    }
    this.childNodes = merged;
  }
  descendants() {
    const out = [];
    for (const child of this.childNodes) {
      out.push(child);
      if (child.descendants) out.push(...child.descendants());
    }
    return out;
  }
  scrollIntoView() { this.scrolled = true; }
}

class Fragment extends Element {
  constructor() { super('#fragment'); this.isFragment = true; }
}

function textNodes(root) {
  const out = [];
  const visit = node => {
    if (!node.childNodes) return;
    for (const child of node.childNodes) {
      if (child.nodeName === '#text') out.push(child);
      else visit(child);
    }
  };
  visit(root);
  return out;
}

function makeDom() {
  const document = {
    body: new Element('body'),
    createElement: tag => new Element(tag),
    createTextNode: value => new TextNode(value),
    createDocumentFragment: () => new Fragment(),
    createTreeWalker(root, _what, options) {
      const all = textNodes(root).filter(node => options.acceptNode(node) === 1);
      let index = -1;
      const walker = {
        currentNode: null,
        nextNode() {
          index += 1;
          walker.currentNode = index < all.length ? all[index] : null;
          return walker.currentNode;
        },
      };
      return walker;
    },
  };
  const registry = new Map();
  const $ = selector => {
    if (registry.has(selector)) return registry.get(selector);
    const created = new Element(selector.includes('input') || selector.includes('Input') ? 'input' : 'div');
    registry.set(selector, created);
    return created;
  };
  const $$ = (selector, root) => {
    const scope = root || document.body;
    if (selector.includes('find-hit')) {
      return scope.descendants().filter(node => node.className && node.className.includes('find-hit'));
    }
    if (selector.includes('h1')) {
      return scope.descendants().filter(node => /^H[1-6]$/.test(node.nodeName));
    }
    return [];
  };
  return { document, $, $$, registry, Fragment, Element, TextNode };
}

function build({ html }) {
  const dom = makeDom();
  const context = vm.createContext({
    document: dom.document,
    window: { getComputedStyle: () => ({ lineHeight: '20px' }) },
    NodeFilter: { SHOW_TEXT: 4, FILTER_ACCEPT: 1, FILTER_REJECT: 2 },
    state: { findNeedle: '', findMarks: [], findIndex: -1, outline: [] },
    esc: value => String(value),
    console,
    $: dom.$,
    $$: dom.$$,
  });
  // 编辑工具可能把 app.js 存成 CRLF；按实际换行符定位函数末尾
  const EOL = source.includes('\r\n') ? '\r\n' : '\n';
  for (const name of ['inSourceMode', 'findMatches', 'clearFindHits', 'textNodesOf',
                      'highlightInRender', 'runFind', 'outlineRows', 'renderOutline', 'jumpToOutline']) {
    const start = source.indexOf(`function ${name}(`);
    const end = source.indexOf(`${EOL}}${EOL}`, start) + EOL.length + 1;
    assert.ok(start >= 0 && end > start, `Missing function ${name}`);
    vm.runInContext(source.slice(start, end), context);
  }
  const host = new dom.Element('div');
  dom.registry.set('#renderHost', host);
  dom.document.body.appendChild(host);       // document-wide queries must see it
  if (html) {
    // <p>文字<span>更多</span></p> style markup, parsed by hand for the test.
    for (const part of html) {
      if (typeof part === 'string') host.appendChild(new dom.TextNode(part));
      else {
        const node = new dom.Element(part.tag);
        node.appendChild(new dom.TextNode(part.text));
        host.appendChild(node);
      }
    }
  }
  return { context, host, dom };
}

/* -------------------------------------------------------------------- tests */
test('highlightInRender wraps every match and keeps the other text', () => {
  const { context, host } = build({ html: ['前 关键词 中 ', { tag: 'strong', text: '关键词' }, ' 后'] });
  const marks = context.highlightInRender('关键词');
  assert.equal(marks.length, 2);
  assert.equal(host.textContent, '前 关键词 中 关键词 后');
  assert.equal(marks.map(mark => mark.textContent).join('|'), '关键词|关键词');
  assert.equal(marks[0].nodeName, 'MARK');
  assert.ok(marks[0].className.includes('find-hit'));
  // The surrounding structure survives: <strong> still holds the second match.
  const strong = host.descendants().find(node => node.nodeName === 'STRONG');
  assert.ok(strong, 'strong element was lost');
  assert.equal(strong.textContent, '关键词');
});

test('clearFindHits restores the original text exactly', () => {
  const { context, host } = build({ html: ['a 关键词 b ', { tag: 'em', text: 'x 关键词 y' }, ' c'] });
  const before = host.textContent;
  context.highlightInRender('关键词');
  assert.equal(host.descendants().filter(n => n.nodeName === 'MARK').length, 2);
  assert.equal(host.textContent, before, '高亮不得改变可见文字');
  context.clearFindHits();
  assert.equal(host.textContent, before, '取消高亮后文字必须与原来完全一致');
  assert.equal(host.descendants().filter(n => n.nodeName === 'MARK').length, 0);
  assert.equal(context.state.findMarks.length, 0);
});

test('runFind walks the matches and reports the position', () => {
  const { context, host } = build({ html: ['甲 目标 乙 目标 丙 目标'] });
  const input = context.$('#findInput');
  const count = context.$('#findCount');
  input.value = '目标';
  assert.equal(context.runFind(false), true);
  assert.equal(count.textContent, '1/3');
  context.runFind(false);
  assert.equal(count.textContent, '2/3');
  const marks = context.$$('mark.find-hit', host);
  assert.equal(marks.filter(mark => mark.className.includes('current')).length, 1);
  assert.ok(marks[1].scrolled, '当前命中应滚动到可见位置');
  context.runFind(true);                       // 上一处回到第一处
  assert.equal(count.textContent, '1/3');
  context.runFind(true);                       // 再上一处绕到末尾
  assert.equal(count.textContent, '3/3');
});

test('runFind reports misses and empty queries without touching the document', () => {
  const { context, host } = build({ html: ['没有那个词'] });
  const before = host.textContent;
  context.$('#findInput').value = '不存在';
  assert.equal(context.runFind(false), false);
  assert.equal(context.$('#findCount').textContent, '无匹配结果');
  context.$('#findInput').value = '';
  assert.equal(context.runFind(false), false);
  assert.equal(context.$('#findCount').textContent, '');
  assert.equal(host.textContent, before);
});

test('outline lists rendered headings with their level', () => {
  const { context } = build({ html: ['正文', { tag: 'h1', text: '一级' },
                                     { tag: 'h2', text: '二级' }, { tag: 'h3', text: '三级' }] });
  const rows = context.outlineRows();
  assert.equal(rows.map(row => row.level + ':' + row.title).join('|'), '1:一级|2:二级|3:三级');
  context.renderOutline();
  const panel = context.$('#outlinePanel');
  assert.equal(panel.hidden, false);
  assert.ok(panel.innerHTML.includes('data-outline="0"'), panel.innerHTML);
  assert.ok(panel.innerHTML.includes('padding-left:9px'), '一级标题缩进');
  assert.ok(panel.innerHTML.includes('padding-left:35px'), '三级标题缩进');
});

test('outline reports an empty document clearly', () => {
  const { context } = build({ html: ['只有正文'] });
  context.renderOutline();
  const panel = context.$('#outlinePanel');
  assert.ok(panel.innerHTML.includes('没有标题'), panel.innerHTML);
});
