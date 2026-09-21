/**
 * 极简 DOM 桩：让 web/index.html 里的脚本能在 Node 里跑起来，
 * 用来验证「导入 + 进度提示」这类界面逻辑。
 */
function makeElement(id) {
  const listeners = {};
  const el = {
    id: id || "",
    tagName: "DIV",
    hidden: false,
    textContent: "",
    innerHTML: "",
    value: "",
    style: {},
    dataset: {},
    children: [],
    classList: {
      _set: new Set(),
      add() {},
      remove() {},
      toggle() {},
      contains() {
        return false;
      },
    },
    addEventListener(type, handler) {
      (listeners[type] = listeners[type] || []).push(handler);
    },
    removeEventListener() {},
    dispatch(type, event) {
      (listeners[type] || []).forEach((handler) => handler(event || {}));
    },
    setAttribute(name, value) {
      this[name] = value;
    },
    getAttribute() {
      return null;
    },
    appendChild(child) {
      this.children.push(child);
      return child;
    },
    removeChild(child) {
      return child;
    },
    querySelector() {
      return null;
    },
    querySelectorAll() {
      return [];
    },
    closest() {
      return null;
    },
    focus() {},
    select() {},
    remove() {},
    insertBefore(child) {
      return child;
    },
    getBoundingClientRect() {
      return { top: 0, left: 0, width: 100, height: 100 };
    },
  };
  return el;
}

function installDom() {
  const elements = new Map();
  const document = {
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, makeElement(id));
      return elements.get(id);
    },
    querySelector() {
      return null;
    },
    querySelectorAll() {
      return [];
    },
    createElement(tag) {
      const el = makeElement("");
      el.tagName = String(tag).toUpperCase();
      return el;
    },
    addEventListener() {},
    removeEventListener() {},
    body: makeElement("body"),
    documentElement: makeElement("html"),
  };

  const timers = [];
  const window = {
    document,
    addEventListener() {},
    removeEventListener() {},
    navigator: {},
    location: { href: "http://127.0.0.1/index.html" },
  };

  globalThis.document = document;
  globalThis.window = window;
  globalThis.navigator = window.navigator;
  globalThis.localStorage = {
    getItem() {
      return null;
    },
    setItem() {},
  };
  globalThis.setTimeout = (fn) => {
    timers.push(fn);
    return timers.length;
  };
  globalThis.clearTimeout = () => {};
  globalThis.timers = timers;

  return { document, window, elements, timers, makeElement };
}

/** 依次执行排队中的定时器回调（界面的进度轮询依赖它）。 */
async function flushTimers(rounds = 5) {
  for (let i = 0; i < rounds; i++) {
    const pending = globalThis.timers.splice(0, globalThis.timers.length);
    for (const fn of pending) {
      await fn();
    }
    await new Promise((resolve) => setImmediate(resolve));
  }
}

/** 只让 Promise 回调跑完，不动定时器（用于观察“正在导入”的中间状态）。 */
async function flushMicrotasks(rounds = 6) {
  for (let i = 0; i < rounds; i++) {
    await new Promise((resolve) => setImmediate(resolve));
  }
}

module.exports = { installDom, flushTimers, flushMicrotasks, makeElement };
