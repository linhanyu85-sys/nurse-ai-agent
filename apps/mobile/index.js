const globalScope =
  typeof globalThis !== "undefined"
    ? globalThis
    : typeof global !== "undefined"
      ? global
      : {};

const noop = () => {};

function createElementStub(tagName = "div") {
  return {
    nodeName: String(tagName).toUpperCase(),
    style: {},
    children: [],
    ownerDocument: null,
    appendChild(child) {
      this.children.push(child);
      return child;
    },
    removeChild(child) {
      this.children = this.children.filter((item) => item !== child);
      return child;
    },
    setAttribute: noop,
    removeAttribute: noop,
    addEventListener: noop,
    removeEventListener: noop,
    dispatchEvent: () => true,
    getContext: () => ({}),
    getBoundingClientRect: () => ({ top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0 }),
    focus: noop,
    blur: noop,
    contains: () => false,
  };
}

if (typeof globalScope.window === "undefined") {
  globalScope.window = globalScope;
}

if (typeof globalScope.self === "undefined") {
  globalScope.self = globalScope;
}

if (typeof globalScope.location === "undefined") {
  globalScope.location = {
    href: "",
    protocol: "exp:",
    host: "",
    hostname: "",
    reload: noop,
  };
}

if (typeof globalScope.navigator === "undefined") {
  globalScope.navigator = {
    product: "ReactNative",
    userAgent: "ReactNative",
  };
}

if (typeof globalScope.document === "undefined") {
  const body = createElementStub("body");
  const documentElement = createElementStub("html");
  const documentStub = {
    body,
    documentElement,
    createElement: (tagName) => createElementStub(tagName),
    createTextNode: (text) => ({ nodeValue: String(text ?? "") }),
    addEventListener: noop,
    removeEventListener: noop,
    dispatchEvent: () => true,
    featurePolicy: {
      allowsFeature: () => false,
    },
  };
  body.ownerDocument = documentStub;
  documentElement.ownerDocument = documentStub;
  globalScope.document = documentStub;
}

if (typeof globalScope.innerWidth === "undefined") {
  globalScope.innerWidth = 0;
}

if (typeof globalScope.innerHeight === "undefined") {
  globalScope.innerHeight = 0;
}

if (typeof globalScope.devicePixelRatio === "undefined") {
  globalScope.devicePixelRatio = 1;
}

if (typeof globalScope.performance === "undefined") {
  globalScope.performance = {
    now: () => Date.now(),
    mark: noop,
    measure: noop,
    clearMarks: noop,
    clearMeasures: noop,
  };
}

if (typeof globalScope.dispatchEvent === "undefined") {
  globalScope.dispatchEvent = () => true;
}

if (typeof globalScope.addEventListener === "undefined") {
  globalScope.addEventListener = noop;
}

if (typeof globalScope.removeEventListener === "undefined") {
  globalScope.removeEventListener = noop;
}

if (typeof globalScope.Event === "undefined") {
  globalScope.Event = function Event(type, init = {}) {
    this.type = type;
    Object.assign(this, init);
  };
}

if (typeof globalScope.ErrorEvent === "undefined") {
  globalScope.ErrorEvent = function ErrorEvent(type, init = {}) {
    this.type = type;
    Object.assign(this, init);
  };
}

if (typeof globalScope.setImmediate === "undefined" && typeof setTimeout !== "undefined") {
  globalScope.setImmediate = (callback, ...args) => setTimeout(() => callback(...args), 0);
}

if (typeof globalScope.clearImmediate === "undefined" && typeof clearTimeout !== "undefined") {
  globalScope.clearImmediate = (handle) => clearTimeout(handle);
}

if (typeof globalScope.requestAnimationFrame === "undefined" && typeof setTimeout !== "undefined") {
  globalScope.requestAnimationFrame = (callback) =>
    setTimeout(() => callback(globalScope.performance.now()), 16);
}

if (typeof globalScope.cancelAnimationFrame === "undefined" && typeof clearTimeout !== "undefined") {
  globalScope.cancelAnimationFrame = (handle) => clearTimeout(handle);
}

if (typeof globalScope.queueMicrotask === "undefined") {
  globalScope.queueMicrotask = (callback) => Promise.resolve().then(callback);
}

// Expo's startup patch touches global FormData very early on Hermes.
if (typeof globalScope.FormData === "undefined") {
  try {
    const formDataModule = require("react-native/Libraries/Network/FormData");
    globalScope.FormData = formDataModule.default || formDataModule;
  } catch (error) {
    console.warn("FormData polyfill bootstrap failed:", error);
  }
}

const { registerRootComponent } = require("expo");
const App = require("./App").default;

registerRootComponent(App);
