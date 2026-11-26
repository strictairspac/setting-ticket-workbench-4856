// 最小 DOM / 浏览器 API 桩：仅实现工作台页面用到的能力，供 Node 集成测试使用。

export class Node {
  constructor() {
    this.children = [];
    this.attributes = new Map();
    this.events = new Map();
    this.parentNode = null;
  }

  append(...items) {
    for (const item of items.flat(Infinity)) {
      if (item === null || item === undefined) continue;
      const child = item instanceof Node ? item : new TextNode(String(item));
      child.parentNode = this;
      this.children.push(child);
    }
  }

  replaceChildren(...items) {
    this.children = [];
    this.append(...items);
  }

  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  getAttribute(name) { return this.attributes.has(name) ? this.attributes.get(name) : null; }
  addEventListener(type, handler) { this.events.set(type, handler); }
  dispatch(type, event = {}) { this.events.get(type)?.({preventDefault() {}, ...event}); }
  click() { this.dispatch("click"); }

  get textContent() {
    return this.children.map(child => child.textContent).join("");
  }

  set textContent(value) {
    this.children = value === "" ? [] : [new TextNode(String(value))];
  }

  walk() {
    const all = [];
    for (const child of this.children) {
      all.push(child, ...child.walk());
    }
    return all;
  }

  match(selector) {
    if (selector.startsWith("#")) return this.attributes.get("id") === selector.slice(1);
    if (selector.startsWith(".")) return (this.attributes.get("class") || "").split(/\s+/).includes(selector.slice(1));
    const attrMatch = selector.match(/^\[([^=\]]+)(?:=["']?([^"'\]]+)["']?)?\]$/);
    if (attrMatch) {
      const [, name, value] = attrMatch;
      return this.attributes.has(name) && (value === undefined || this.attributes.get(name) === value);
    }
    const tagAttr = selector.match(/^(\w+)\[([^=\]]+)=["']?([^"'\]]+)["']?\]$/);
    if (tagAttr) {
      const [, tag, name, value] = tagAttr;
      return this.tag === tag && this.attributes.get(name) === value;
    }
    return this.tag === selector;
  }

  querySelector(selector) {
    return this.walk().find(node => node instanceof Element && node.match(selector)) || null;
  }

  querySelectorAll(selector) {
    return this.walk().filter(node => node instanceof Element && node.match(selector));
  }

  get elements() {
    const named = {};
    for (const node of this.walk()) {
      if (node.attributes?.has("name")) named[node.attributes.get("name")] = node;
    }
    return named;
  }
}

export class Element extends Node {
  constructor(tag) {
    super();
    this.tag = tag;
  }
}

export class TextNode extends Node {
  constructor(text) {
    super();
    this.text = text;
  }

  get textContent() { return this.text; }
  walk() { return []; }
}

export function installDom() {
  globalThis.Node = Node;
  globalThis.Event = class Event {
    constructor(type) { this.type = type; }
  };
  globalThis.document = {
    createElement: tag => new Element(tag),
    querySelector: () => null,
  };
  const listeners = new Map();
  globalThis.window = {
    addEventListener: (type, handler) => listeners.set(type, handler),
    dispatchEvent: event => listeners.get(event.type)?.(event),
  };
  globalThis.location = { hash: "" };
  globalThis.sessionStorage = {
    getItem: () => null,
    setItem() {},
    removeItem() {},
  };
  globalThis.FileReader = class {
    readAsText(file) {
      setTimeout(() => {
        this.result = file.content;
        this.onload?.({target: {result: file.content}});
      }, 0);
    }
  };
  globalThis.URL.createObjectURL = () => "blob:test";
  globalThis.URL.revokeObjectURL = () => {};
}

export function findByTestId(root, testId) {
  return root.walk().find(node => node.attributes?.get("data-testid") === testId) || null;
}

export function findByTag(root, tag) {
  return root.walk().filter(node => node instanceof Element && node.tag === tag);
}

export function installFetch(router) {
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    const path = url.replace(/^\/api/, "");
    const method = options.method || "GET";
    const body = options.body ? JSON.parse(options.body) : undefined;
    calls.push({method, path, body});
    const routed = router({method, path, body});
    if (routed?.error) {
      return {ok: false, status: routed.status, json: async () => ({detail: routed.error})};
    }
    return {
      ok: true,
      status: 200,
      json: async () => routed?.json,
      blob: async () => ({}),
    };
  };
  return calls;
}
