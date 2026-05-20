/**
 * fetch 封装。统一处理:
 *  - JWT token(localStorage 持久化)
 *  - 候选人侧 verified session(``X-Candidate-Session`` header,opt-in)
 *  - JSON Content-Type
 *  - 401 自动登出
 *  - 错误归一化为 ApiError
 *
 * FormData 不强制 JSON header,以支持 .lic 文件上传 / 语音录音上传。
 *
 * 候选人侧 session 是 opt-in 的:HR / 内部页面 fetchJSON 不带它,只有候选人侧
 * 接口在 init.candidateSession 显式传入。401 时的处理也分两种:
 *  - 带了 candidateSession 的请求:不清 JWT、不跳 /login,留给调用者处理
 *    (CandidateSession / VoiceSession 会清掉本地 session 并回到 invite 页)
 *  - 普通请求:沿用历史行为,清 JWT + 跳 /login
 */

const TOKEN_KEY = "free-hire-token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public detail?: unknown,
  ) {
    super(message);
  }
}

export interface FetchJSONInit extends RequestInit {
  /** 候选人侧 verified session token —— 进 ``X-Candidate-Session`` header */
  candidateSession?: string;
}

export async function fetchJSON<T = unknown>(
  url: string,
  init: FetchJSONInit = {},
): Promise<T> {
  const { candidateSession, ...rest } = init;
  const token = getToken();
  const headers = new Headers(rest.headers);
  if (
    !headers.has("Content-Type") &&
    rest.body &&
    !(rest.body instanceof FormData)
  ) {
    headers.set("Content-Type", "application/json");
  }
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (candidateSession) headers.set("X-Candidate-Session", candidateSession);

  const res = await fetch(url, { ...rest, headers });
  if (res.status === 401) {
    // 候选人侧 401(session 过期 / 缺失)由调用方自己处理 —— 不能踢到 /login,
    // 候选人没有账号。
    if (!candidateSession) {
      setToken(null);
      if (location.pathname !== "/login") location.href = "/login";
    }
    throw new ApiError(401, "未授权");
  }
  if (!res.ok) {
    let detail: unknown = undefined;
    try {
      detail = await res.json();
    } catch {
      // 忽略解析失败,detail 保持 undefined
    }
    const msg =
      typeof detail === "object" && detail && "detail" in detail
        ? String((detail as { detail: unknown }).detail)
        : `HTTP ${res.status}`;
    throw new ApiError(res.status, msg, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}
