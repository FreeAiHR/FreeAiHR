import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { ApiError, fetchJSON, getToken, setToken } from "./api";
import type { AppPermission, AppRole } from "./roles";

export type User = {
  id: string;
  email: string;
  role: AppRole;
  tenant_id: string;
  org_unit_id: string | null;
  /** 后端 ``/auth/me.permissions`` —— 前端用来隐藏菜单 / 守卫页面。 */
  permissions: AppPermission[];
};

type AuthState = {
  user: User | null;
  loading: boolean;
  login(email: string, password: string): Promise<void>;
  /** SSO 回调写入本地 JWT 后用,重新拉一次 /auth/me。 */
  consumeToken(token: string): Promise<User>;
  logout(): void;
  /** 当前 user 是否拥有给定权限;未登录时恒为 false。 */
  hasPermission(perm: AppPermission): boolean;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const token = getToken();
    if (!token) {
      setLoading(false);
      return;
    }
    fetchJSON<User>("/api/auth/me")
      .then(setUser)
      .catch(() => setToken(null))
      .finally(() => setLoading(false));
  }, []);

  async function login(email: string, password: string) {
    const res = await fetchJSON<{ access_token: string; user: User }>(
      "/api/auth/login",
      {
        method: "POST",
        body: JSON.stringify({ email, password }),
      },
    );
    setToken(res.access_token);
    setUser(res.user);
  }

  async function consumeToken(token: string): Promise<User> {
    setToken(token);
    const fresh = await fetchJSON<User>("/api/auth/me");
    setUser(fresh);
    return fresh;
  }

  function logout() {
    setToken(null);
    setUser(null);
  }

  function hasPermission(perm: AppPermission): boolean {
    return Boolean(user?.permissions?.includes(perm));
  }

  return (
    <AuthContext.Provider
      value={{ user, loading, login, consumeToken, logout, hasPermission }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be inside <AuthProvider>");
  return ctx;
}

export { ApiError };
