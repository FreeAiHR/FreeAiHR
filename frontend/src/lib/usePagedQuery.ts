import { useEffect, useMemo } from "react";
import { useQuery, type QueryKey } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { fetchJSON } from "@/lib/api";

/**
 * 服务端分页响应体 — 与后端 :class:`app.api._pagination.PageOut` 一一对应。
 *
 * ``total`` 是过滤后的总数(即 q/status 等条件命中多少),不是全表数。
 */
export type Page<T> = {
  items: T[];
  total: number;
  limit: number;
  offset: number;
};

/** 固定页大小。UI 用传统分页器(上一页/下一页/跳转),20 行刚好单屏可读。 */
export const PAGE_SIZE = 20;

/** q 输入防抖 ms — URL 不希望被每按一键就污染 */
const Q_DEBOUNCE_MS = 250;

type PagedQueryOpts<T> = {
  /** react-query cache key 前缀,最终 key 会拼上 {q, page, ...params} */
  key: readonly unknown[];
  /** 接口基础路径,例 "/api/jobs/"(结尾斜杠不是必须,会自动拼参数) */
  url: string;
  /** 额外查询参数(状态过滤、resume_id 之类),undefined 不拼 */
  params?: Record<string, string | number | undefined>;
  /**
   * 轮询回调;收到当前页数据后由调用方决定刷新间隔。
   * 返回 false 表示不轮询,返回 ms 数触发定期 refetch。
   */
  refetchInterval?: (data: Page<T> | undefined) => number | false;
  /**
   * URL search param 里承载 q 与 page 的键名。默认 "q" / "page"。
   * 多个分页页面共用一个 URL 时(其实不会,但留个接口),可覆盖。
   */
  qParam?: string;
  pageParam?: string;
};

type PagedQueryResult<T> = {
  data: Page<T> | undefined;
  isLoading: boolean;
  isError: boolean;
  error: unknown;

  /** 1-based 页码 — UI 展示友好 */
  page: number;
  pageCount: number;
  total: number;
  /** 跳到指定 1-based 页码;越界自动夹紧到 [1, pageCount] */
  goto: (p: number) => void;
  /** 回到第 1 页(筛选条件变更时常用) */
  reset: () => void;

  /** URL 同步后的 q(显示到 SearchInput 的受控值仍由调用方用 setQ 输入) */
  q: string;
  /** 写入输入端;hook 内部会 250ms 防抖同步到 URL,并重置 page=1 */
  setQ: (s: string) => void;
};

/**
 * 通用服务端分页 hook。
 *
 * 工作流:
 * 1. URL 里读 ``?q=&page=``,拼 ``?limit&offset&q`` 发请求
 * 2. 用户输入 setQ(s) → 内部 debounce 250ms → 写回 URL ``?q=``,顺便把
 *    page 重置为 1(筛选条件变,翻到第 2 页继续显示会很诡异)
 * 3. goto(n) → 写回 URL ``?page=n``
 * 4. refetchInterval 可选传:若当前页有 inflight 行(解析中/匹配中)
 *    返回 2000,就会轮询只更新当前页;全部完成后返回 false 自动停
 *
 * 为什么 q 放 URL 而不是组件 state:刷新/复制链接/浏览器后退能保留筛选,
 * HR 分享筛选结果给同事也方便。
 */
export function usePagedQuery<T>(opts: PagedQueryOpts<T>): PagedQueryResult<T> {
  const qKey = opts.qParam ?? "q";
  const pageKey = opts.pageParam ?? "page";
  const [searchParams, setSearchParams] = useSearchParams();

  const q = searchParams.get(qKey) ?? "";
  const pageRaw = Number(searchParams.get(pageKey) ?? "1");
  // 容错:URL 被用户手改成负数/非数字时回落到 1
  const page = Number.isFinite(pageRaw) && pageRaw >= 1 ? Math.floor(pageRaw) : 1;
  const offset = (page - 1) * PAGE_SIZE;

  // extraParams 序列化后进 key — 参数变化触发新查询
  const extraEntries = useMemo(
    () =>
      Object.entries(opts.params ?? {}).filter(
        ([, v]) => v !== undefined && v !== "",
      ),
    [opts.params],
  );

  const queryString = useMemo(() => {
    const sp = new URLSearchParams();
    sp.set("limit", String(PAGE_SIZE));
    sp.set("offset", String(offset));
    if (q) sp.set("q", q);
    for (const [k, v] of extraEntries) sp.set(k, String(v));
    return sp.toString();
  }, [offset, q, extraEntries]);

  const queryKey: QueryKey = [...opts.key, { q, page, extra: extraEntries }];

  const query = useQuery<Page<T>>({
    queryKey,
    queryFn: () => fetchJSON<Page<T>>(`${opts.url}?${queryString}`),
    // keepPreviousData 避免翻页时分页器本身闪烁(react-query v5 用 placeholderData)
    placeholderData: (prev) => prev,
    refetchInterval: opts.refetchInterval
      ? (q2) => opts.refetchInterval!(q2.state.data as Page<T> | undefined)
      : false,
  });

  const total = query.data?.total ?? 0;
  const pageCount = total > 0 ? Math.max(1, Math.ceil(total / PAGE_SIZE)) : 1;

  // URL 写入帮手 — replace: true 避免每次翻页都污染浏览器前进/后退栈
  function patchParams(mut: (sp: URLSearchParams) => void, replace = true) {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        mut(next);
        return next;
      },
      { replace },
    );
  }

  function goto(p: number) {
    const clamped = Math.min(Math.max(1, Math.floor(p)), pageCount);
    patchParams((sp) => {
      if (clamped === 1) sp.delete(pageKey);
      else sp.set(pageKey, String(clamped));
    });
  }

  function reset() {
    patchParams((sp) => sp.delete(pageKey));
  }

  // setQ — 外部直接传最新输入字符串,内部做 debounce,期间不触发网络
  function setQ(s: string) {
    // 直接用 setTimeout 简化;重复调用的旧 timer 靠 effect 清;
    // 这里用独立 queue 也行,但 UI 场景一次一个输入框,简单点
    clearTimeout((setQ as unknown as { _t?: number })._t);
    (setQ as unknown as { _t?: number })._t = window.setTimeout(() => {
      patchParams((sp) => {
        if (s) sp.set(qKey, s);
        else sp.delete(qKey);
        // q 变了必须回第 1 页,不然第 5 页被清空会摸不着头脑
        sp.delete(pageKey);
      });
    }, Q_DEBOUNCE_MS);
  }

  // 组件卸载时清 debounce,避免在已卸载组件里 setSearchParams
  useEffect(() => {
    return () => {
      clearTimeout((setQ as unknown as { _t?: number })._t);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return {
    data: query.data,
    isLoading: query.isLoading,
    isError: query.isError,
    error: query.error,
    page,
    pageCount,
    total,
    goto,
    reset,
    q,
    setQ,
  };
}
