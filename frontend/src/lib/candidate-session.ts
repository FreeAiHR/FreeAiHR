/**
 * 候选人侧 verified session token 的本地存储 —— 配合后端 /api/i/{token}/verify 使用。
 *
 * 设计:
 * - 后端在 /verify ok=true 时返回 ``session_token``,前端必须把它写进 sessionStorage,
 *   之后调用 /start /state /answer /audio 时通过 ``X-Candidate-Session`` header 带上;
 *   ``<audio src>`` 没法挂 header,所以 /tts 走 ``?session=`` query。
 * - 用 sessionStorage 而不是 localStorage:候选人关掉 tab,session 自然丢失,
 *   需要重新走 verify —— 与"一次坐下答完"的产品语义吻合。
 * - 按 invite token 分 key —— 同一浏览器同时收到多场邀请的边缘场景也能区分。
 */

const PREFIX = "fh-candidate-session:";

export function getCandidateSession(inviteToken: string): string | null {
  try {
    return sessionStorage.getItem(PREFIX + inviteToken);
  } catch {
    // 浏览器隐私模式可能抛 SecurityError
    return null;
  }
}

export function setCandidateSession(inviteToken: string, sessionToken: string): void {
  try {
    sessionStorage.setItem(PREFIX + inviteToken, sessionToken);
  } catch {
    // 配额满 / 隐私模式 —— 静默失败,后续接口会 401,前端会回到 invite 页重做 verify
  }
}

export function clearCandidateSession(inviteToken: string): void {
  try {
    sessionStorage.removeItem(PREFIX + inviteToken);
  } catch {
    // ignore
  }
}
