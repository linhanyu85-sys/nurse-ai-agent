import { NativeModules, Platform } from "react-native";
import { decodeEscapedText } from "../utils/text";

type QueryValue =
  | string
  | number
  | boolean
  | null
  | undefined
  | Array<string | number | boolean | null | undefined>;

type QueryParams = Record<string, QueryValue>;
type HttpHeaders = Record<string, string>;

type HttpRequestConfig = {
  headers?: HttpHeaders;
  params?: QueryParams;
  timeout?: number;
};

type HttpResponse<T = any> = {
  data: T;
  status: number;
  headers: HttpHeaders;
};

function trimSlash(s: string): string {
  const v = String(s || "");
  return v.replace(/\/$/, "");
}

function getWebHostName(): string {
  const g = globalThis as { location?: { hostname?: string } } | undefined;
  const host = String(g?.location?.hostname || "").trim();
  return host;
}

function parseHostname(raw: string): string {
  const text = String(raw || "").trim();
  if (!text) {
    return "";
  }
  try {
    return String(new URL(text).hostname || "").trim();
  } catch {
    const match = text.match(/^(?:exp|http|https|ws|wss):\/\/([^/:?#]+)/i);
    return match ? String(match[1] || "").trim() : "";
  }
}

function isIpv4(host: string): boolean {
  return /^\d{1,3}(?:\.\d{1,3}){3}$/.test(host);
}

function isPrivateIpv4(host: string): boolean {
  if (!isIpv4(host)) {
    return false;
  }
  return (
    host.startsWith("10.") ||
    host.startsWith("192.168.") ||
    /^172\.(1[6-9]|2\d|3[0-1])\./.test(host)
  );
}

function isLocalHost(host: string): boolean {
  const value = String(host || "").trim().toLowerCase();
  return value === "localhost" || value === "127.0.0.1" || value === "::1";
}

function resolveRuntimeHost(): string {
  const envProxyHost = parseHostname(process.env.EXPO_PACKAGER_PROXY_URL || "");
  if (isPrivateIpv4(envProxyHost)) {
    return envProxyHost;
  }

  if (Platform.OS === "web") {
    const webHost = getWebHostName();
    if (webHost) {
      return webHost;
    }
  }

  const sourceCodeUrl = String((NativeModules as any)?.SourceCode?.scriptURL || "").trim();
  const sourceHost = parseHostname(sourceCodeUrl);
  if (isPrivateIpv4(sourceHost) || isLocalHost(sourceHost)) {
    return sourceHost;
  }

  return "";
}

function shouldUseRuntimeHost(configHost: string, runtimeHost: string): boolean {
  if (!runtimeHost || runtimeHost === configHost) {
    return false;
  }
  if (isLocalHost(configHost)) {
    return true;
  }
  if (!configHost && isPrivateIpv4(runtimeHost)) {
    return true;
  }
  return false;
}

function resolveBase(raw: string): string {
  const t = trimSlash(raw);
  const os = Platform.OS;

  try {
    const u = new URL(t);
    let resolvedHost = "";

    if (os === "web") {
      let isDev = false;
      if (typeof __DEV__ !== "undefined" && __DEV__) {
        isDev = true;
      }
      if (isDev) {
        resolvedHost = getWebHostName();
      }
    } else {
      const runtimeHost = resolveRuntimeHost();
      if (shouldUseRuntimeHost(u.hostname, runtimeHost)) {
        resolvedHost = runtimeHost;
      }
    }

    if (!resolvedHost) {
      return t;
    }
    u.hostname = resolvedHost;
    return trimSlash(u.toString());
  } catch {
    return t;
  }
}

const cfgUrl = process.env.EXPO_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";
export const apiBaseURL = resolveBase(cfgUrl);

function uniqKeepOrder(values: string[]): string[] {
  const seen = new Set<string>();
  const output: string[] = [];
  values.forEach((item) => {
    const value = trimSlash(item);
    if (!value || seen.has(value)) {
      return;
    }
    seen.add(value);
    output.push(value);
  });
  return output;
}

function deriveRuntimeApiBaseUrl(): string {
  const runtimeHost = resolveRuntimeHost();
  if (!runtimeHost || isLocalHost(runtimeHost)) {
    return "";
  }
  try {
    const base = new URL(apiBaseURL);
    base.hostname = runtimeHost;
    base.port = "8000";
    return trimSlash(base.toString());
  } catch {
    return "";
  }
}

const apiBaseCandidates = uniqKeepOrder([apiBaseURL, deriveRuntimeApiBaseUrl()]);

function setPort(raw: string, p: string): string {
  const t = trimSlash(raw);
  const m = t.match(/^(https?:\/\/[^/:]+)(?::\d+)?(\/.*)?$/i);
  if (!m) {
    return t;
  }
  const host = m[1];
  const path = m[2] || "";
  return `${host}:${p}${path}`;
}

export const asrBaseURL = resolveBase(
  process.env.EXPO_PUBLIC_ASR_BASE_URL || setPort(apiBaseURL, "8013")
);

function norm(v: any): any {
  if (typeof v === "string") {
    return decodeEscapedText(v);
  }
  if (Array.isArray(v)) {
    const arr: any[] = [];
    for (let i = 0; i < v.length; i++) {
      arr.push(norm(v[i]));
    }
    return arr;
  }
  if (v && typeof v === "object") {
    const out: Record<string, any> = {};
    const keys = Object.keys(v);
    for (let j = 0; j < keys.length; j++) {
      const k = keys[j];
      out[k] = norm(v[k]);
    }
    return out;
  }
  return v;
}

function joinUrl(base: string, path: string): string {
  if (/^https?:\/\//i.test(path)) {
    return path;
  }
  const cleanBase = trimSlash(base);
  const cleanPath = path.startsWith("/") ? path : `/${path}`;
  return `${cleanBase}${cleanPath}`;
}

function appendQuery(url: string, params?: QueryParams): string {
  if (!params) {
    return url;
  }

  const parts: string[] = [];
  const keys = Object.keys(params);

  for (let i = 0; i < keys.length; i++) {
    const key = keys[i];
    const rawValue = params[key];
    const pushPair = (value: string | number | boolean) => {
      parts.push(`${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`);
    };

    if (Array.isArray(rawValue)) {
      for (let j = 0; j < rawValue.length; j++) {
        const item = rawValue[j];
        if (item === undefined || item === null) {
          continue;
        }
        pushPair(item);
      }
      continue;
    }

    if (rawValue === undefined || rawValue === null) {
      continue;
    }

    pushPair(rawValue);
  }

  if (parts.length === 0) {
    return url;
  }

  return `${url}${url.includes("?") ? "&" : "?"}${parts.join("&")}`;
}

function headersToObject(headersLike: { forEach?: (cb: (value: string, key: string) => void) => void }): HttpHeaders {
  const out: HttpHeaders = {};
  headersLike?.forEach?.((value, key) => {
    out[key] = value;
  });
  return out;
}

function getFetchImplementation(): typeof fetch {
  const g = globalThis as any;

  if (typeof g.fetch === "function") {
    return g.fetch.bind(g);
  }

  try {
    const fetchModule = require("react-native/Libraries/Network/fetch");
    if (typeof g.fetch !== "function" && typeof fetchModule?.fetch === "function") {
      g.fetch = fetchModule.fetch.bind(g);
    }
    if (typeof g.Headers === "undefined" && fetchModule?.Headers) {
      g.Headers = fetchModule.Headers;
    }
    if (typeof g.Request === "undefined" && fetchModule?.Request) {
      g.Request = fetchModule.Request;
    }
    if (typeof g.Response === "undefined" && fetchModule?.Response) {
      g.Response = fetchModule.Response;
    }
  } catch {
    // ignore and fall through
  }

  if (typeof g.fetch === "function") {
    return g.fetch.bind(g);
  }

  throw new Error("Network API unavailable");
}

function createHttpError(
  message: string,
  status: number,
  data: unknown,
  headers: HttpHeaders,
  url: string,
  method: string,
  code?: string
) {
  const error = new Error(message) as Error & {
    code?: string;
    config?: Record<string, unknown>;
    request?: Record<string, unknown>;
    response?: Record<string, unknown>;
  };

  if (code) {
    error.code = code;
  }

  error.config = {
    method,
    url,
  };
  error.request = {
    method,
    url,
  };
  error.response = {
    status,
    data,
    headers,
    config: error.config,
  };

  return error;
}

async function parseResponseBody(response: Response): Promise<any> {
  const contentType = String(response.headers.get("content-type") || "").toLowerCase();
  const rawText = await response.text();

  if (rawText === "") {
    return null;
  }

  if (contentType.includes("application/json")) {
    try {
      return JSON.parse(rawText);
    } catch {
      return rawText;
    }
  }

  const maybeJson = rawText.trim();
  if (
    (maybeJson.startsWith("{") && maybeJson.endsWith("}")) ||
    (maybeJson.startsWith("[") && maybeJson.endsWith("]"))
  ) {
    try {
      return JSON.parse(maybeJson);
    } catch {
      return rawText;
    }
  }

  return rawText;
}

function buildRequestBody(data: unknown, headers: HttpHeaders): BodyInit | undefined {
  if (data === undefined || data === null) {
    return undefined;
  }

  const FormDataCtor = typeof FormData !== "undefined" ? FormData : undefined;
  if (typeof FormDataCtor !== "undefined" && data instanceof FormDataCtor) {
    return data as BodyInit;
  }

  if (typeof data === "string") {
    return data;
  }

  if (typeof Blob !== "undefined" && data instanceof Blob) {
    return data;
  }

  if (data instanceof ArrayBuffer || ArrayBuffer.isView(data)) {
    return data as BodyInit;
  }

  headers["Content-Type"] = headers["Content-Type"] || "application/json";
  return JSON.stringify(data);
}

async function request<T = any>(
  method: "GET" | "POST" | "DELETE",
  url: string,
  data?: unknown,
  config?: HttpRequestConfig
): Promise<HttpResponse<T>> {
  const fetchImpl = getFetchImplementation();
  const timeout = Number(config?.timeout ?? 20000);
  let lastError: unknown = null;

  for (let index = 0; index < apiBaseCandidates.length; index++) {
    const baseUrl = apiBaseCandidates[index];
    const headers: HttpHeaders = {
      Accept: "application/json, text/plain, */*",
      ...(config?.headers || {}),
    };
    const fullUrl = appendQuery(joinUrl(baseUrl, url), config?.params);
    const body = buildRequestBody(data, headers);
    const controller = typeof AbortController !== "undefined" ? new AbortController() : null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    if (controller && timeout > 0) {
      timer = setTimeout(() => controller.abort(), timeout);
    }

    try {
      const response = await fetchImpl(fullUrl, {
        method,
        headers,
        body,
        signal: controller?.signal,
      });
      const responseHeaders = headersToObject(response.headers);
      const parsed = norm(await parseResponseBody(response));

      if (!response.ok) {
        const message =
          typeof (parsed as any)?.detail === "string"
            ? String((parsed as any).detail)
            : `Request failed with status code ${response.status}`;
        throw createHttpError(message, response.status, parsed, responseHeaders, fullUrl, method);
      }

      return {
        data: parsed as T,
        status: response.status,
        headers: responseHeaders,
      };
    } catch (error) {
      if ((error as any)?.response) {
        throw error;
      }

      if ((error as any)?.name === "AbortError") {
        lastError = createHttpError(
          `timeout of ${timeout}ms exceeded`,
          0,
          null,
          {},
          fullUrl,
          method,
          "ECONNABORTED"
        );
      } else {
        lastError = createHttpError("Network Error", 0, null, {}, fullUrl, method, "ERR_NETWORK");
      }

      if (index === apiBaseCandidates.length - 1) {
        throw lastError;
      }
    } finally {
      if (timer) {
        clearTimeout(timer);
      }
    }
  }

  throw lastError || createHttpError("Network Error", 0, null, {}, joinUrl(apiBaseURL, url), method, "ERR_NETWORK");
}

export const httpClient = {
  get<T = any>(url: string, config?: HttpRequestConfig) {
    return request<T>("GET", url, undefined, config);
  },

  post<T = any>(url: string, data?: unknown, config?: HttpRequestConfig) {
    return request<T>("POST", url, data, config);
  },

  delete<T = any>(url: string, config?: HttpRequestConfig) {
    return request<T>("DELETE", url, undefined, config);
  },
};

const mockEnv = process.env.EXPO_PUBLIC_API_MOCK || "false";
export const isMockMode = mockEnv === "true";

export function getWsBaseUrl() {
  let ws = apiBaseURL;
  if (ws.startsWith("https://")) {
    ws = ws.replace("https://", "wss://");
  } else {
    ws = ws.replace("http://", "ws://");
  }
  return ws;
}
