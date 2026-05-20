import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Ban,
  CheckCircle2,
  Folder,
  Plus,
  Trash2,
  Users,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Drawer } from "@/components/ui/drawer";
import { Empty } from "@/components/ui/empty";
import { Pagination } from "@/components/ui/pagination";
import { SearchInput } from "@/components/ui/search-input";
import { Select } from "@/components/ui/select";
import { useConfirm } from "@/components/ui/confirm";
import { ApiError, fetchJSON } from "@/lib/api";
import { formatRelative } from "@/lib/format";
import { usePagedQuery, PAGE_SIZE } from "@/lib/usePagedQuery";

type TalentItem = {
  id: string;
  name: string;
  display_email: string | null;
  display_phone: string | null;
  tags: string[];
  status: string;
  is_blacklisted: boolean;
  blacklist_reason: string | null;
  last_active_at: string | null;
  created_at: string;
  resume_count: number;
  interview_count: number;
  last_interview_status: string | null;
  last_interview_at: string | null;
  top_match_job_title: string | null;
  top_match_score: number | null;
};

type Group = {
  id: string;
  name: string;
  description: string | null;
  member_count: number;
  created_at: string;
  updated_at: string;
};

const STATUS_LABEL: Record<string, string> = {
  in_progress: "进行中",
  done: "已完成",
  abandoned: "已放弃",
};

export function TalentPool() {
  const [blacklistOnly, setBlacklistOnly] = useState<string>("");
  const [groupFilter, setGroupFilter] = useState<string>("");
  const [openGroups, setOpenGroups] = useState(false);

  const params = {
    blacklisted: blacklistOnly || undefined,
    group_id: groupFilter || undefined,
  };

  const { data, isLoading, page, pageCount, total, goto, reset, q, setQ } =
    usePagedQuery<TalentItem>({
      key: ["talents"],
      url: "/api/talents",
      params,
    });

  const [inputQ, setInputQ] = useState(q);
  useEffect(() => {
    setInputQ(q);
  }, [q]);

  useEffect(() => {
    reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [blacklistOnly, groupFilter]);

  const { data: groups } = useQuery({
    queryKey: ["talent-groups"],
    queryFn: () => fetchJSON<Group[]>("/api/talent-groups"),
  });

  const items = data?.items ?? [];

  return (
    <main className="p-8 flex flex-col gap-6 max-w-[1200px] mx-auto w-full">
      <div className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-1.5">
          <h2 className="font-heading font-semibold text-2xl text-[var(--color-text-primary)]">
            人才库
          </h2>
          <p className="text-sm text-[var(--color-text-secondary)] font-body">
            以候选人为主视角的人才档案。聚合简历版本、面试历史、岗位匹配,并支持标签、分组、黑名单与备注。
          </p>
        </div>
        <Button variant="secondary" onClick={() => setOpenGroups(true)}>
          <Folder className="w-4 h-4" />
          人才分组
        </Button>
      </div>

      <section className="bg-white border border-[var(--color-border-subtle)] rounded-2xl overflow-hidden">
        <div className="px-6 py-4 border-b border-[var(--color-border-subtle)] flex flex-wrap items-center gap-3">
          <SearchInput
            value={inputQ}
            onChange={(v) => {
              setInputQ(v);
              setQ(v);
            }}
            placeholder="搜索姓名 / 邮箱 / 手机"
            className="w-[280px]"
          />
          <Select
            value={blacklistOnly}
            onChange={setBlacklistOnly}
            placeholder="是否黑名单"
            className="w-[140px]"
            options={[
              { value: "", label: "全部候选人" },
              { value: "true", label: "仅黑名单" },
              { value: "false", label: "排除黑名单" },
            ]}
          />
          <Select
            value={groupFilter}
            onChange={setGroupFilter}
            placeholder="所属分组"
            className="w-[200px]"
            options={[
              { value: "", label: "全部分组" },
              ...(groups ?? []).map((g) => ({
                value: g.id,
                label: g.name,
                hint: `${g.member_count} 人`,
              })),
            ]}
          />
          {(blacklistOnly || groupFilter || q) && (
            <button
              type="button"
              onClick={() => {
                setBlacklistOnly("");
                setGroupFilter("");
                setQ("");
                setInputQ("");
              }}
              className="inline-flex items-center gap-1 text-[12px] text-[var(--color-text-secondary)] font-body hover:text-[var(--color-text-primary)]"
            >
              <X className="w-3.5 h-3.5" />
              清空筛选
            </button>
          )}
          <span className="ml-auto text-[12px] text-[var(--color-text-tertiary)] font-body">
            共 {total} 人
          </span>
        </div>

        {isLoading ? (
          <div className="p-12 text-center text-sm text-[var(--color-text-tertiary)] font-body">
            加载中…
          </div>
        ) : items.length === 0 ? (
          <Empty
            icon={Users}
            title="还没有候选人"
            description="先在简历库上传简历,候选人会自动沉淀到这里。"
          />
        ) : (
          <>
            <table className="w-full text-[13px] font-body">
              <thead className="text-[11px] uppercase tracking-wider text-[var(--color-text-tertiary)] bg-[var(--color-bg-canvas)]">
                <tr>
                  <th className="text-left px-6 py-2.5 font-medium">候选人</th>
                  <th className="text-left px-4 py-2.5 font-medium">标签</th>
                  <th className="text-left px-4 py-2.5 font-medium">最近活动</th>
                  <th className="text-left px-4 py-2.5 font-medium">简历 / 面试</th>
                  <th className="text-left px-4 py-2.5 font-medium">最高匹配</th>
                  <th className="text-left px-4 py-2.5 font-medium">状态</th>
                </tr>
              </thead>
              <tbody>
                {items.map((t) => (
                  <tr
                    key={t.id}
                    className="border-t border-[var(--color-border-row)] hover:bg-[var(--color-bg-muted)]"
                  >
                    <td className="px-6 py-3">
                      <Link
                        to={`/talents/${t.id}`}
                        className="flex flex-col gap-0.5"
                      >
                        <span className="font-medium text-[var(--color-text-primary)]">
                          {t.name}
                        </span>
                        <span className="text-[11px] text-[var(--color-text-tertiary)]">
                          {t.display_email ?? "—"}
                          {t.display_phone ? ` · ${t.display_phone}` : ""}
                        </span>
                      </Link>
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex flex-wrap gap-1">
                        {t.tags.length === 0 && (
                          <span className="text-[11px] text-[var(--color-text-tertiary)]">
                            —
                          </span>
                        )}
                        {t.tags.slice(0, 3).map((tag) => (
                          <span
                            key={tag}
                            className="px-1.5 py-0.5 rounded text-[11px] bg-[var(--color-bg-subtle)] text-[var(--color-text-secondary)]"
                          >
                            {tag}
                          </span>
                        ))}
                        {t.tags.length > 3 && (
                          <span className="text-[11px] text-[var(--color-text-tertiary)]">
                            +{t.tags.length - 3}
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-[var(--color-text-secondary)] whitespace-nowrap">
                      {t.last_active_at ? formatRelative(t.last_active_at) : "—"}
                    </td>
                    <td className="px-4 py-3 text-[var(--color-text-secondary)] whitespace-nowrap">
                      {t.resume_count} 份简历 / {t.interview_count} 次面试
                      {t.last_interview_status && (
                        <span className="ml-1 text-[11px] text-[var(--color-text-tertiary)]">
                          (最近{STATUS_LABEL[t.last_interview_status] ?? t.last_interview_status})
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-[var(--color-text-secondary)]">
                      {t.top_match_job_title ? (
                        <>
                          <span className="font-mono text-[var(--color-text-primary)]">
                            {t.top_match_score}
                          </span>
                          <span className="ml-1 text-[11px] text-[var(--color-text-tertiary)]">
                            · {t.top_match_job_title}
                          </span>
                        </>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="px-4 py-3">
                      {t.is_blacklisted ? (
                        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[11px] font-medium bg-[var(--color-danger-soft)] text-[var(--color-danger)]">
                          <Ban className="w-3 h-3" />
                          黑名单
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[11px] font-medium bg-[var(--color-success-soft)] text-[var(--color-success)]">
                          <CheckCircle2 className="w-3 h-3" />
                          活跃
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="border-t border-[var(--color-border-subtle)]">
              <Pagination
                page={page}
                pageCount={pageCount}
                total={total}
                pageSize={PAGE_SIZE}
                onChange={goto}
              />
            </div>
          </>
        )}
      </section>

      <GroupsDrawer open={openGroups} onClose={() => setOpenGroups(false)} />
    </main>
  );
}

function GroupsDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);

  const { data: groups, isLoading } = useQuery({
    queryKey: ["talent-groups"],
    queryFn: () => fetchJSON<Group[]>("/api/talent-groups"),
  });

  const create = useMutation({
    mutationFn: () =>
      fetchJSON<Group>("/api/talent-groups", {
        method: "POST",
        body: JSON.stringify({ name: name.trim(), description: description || null }),
      }),
    onSuccess: () => {
      setName("");
      setDescription("");
      setError(null);
      qc.invalidateQueries({ queryKey: ["talent-groups"] });
    },
    onError: (e: unknown) =>
      setError(
        e instanceof ApiError
          ? e.status === 409
            ? "同名分组已存在"
            : e.message
          : "创建失败",
      ),
  });

  const del = useMutation({
    mutationFn: (id: string) =>
      fetchJSON(`/api/talent-groups/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["talent-groups"] }),
  });

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title="人才分组"
      description="为常用的人才池建立分组，候选人详情页可批量加入或移出。"
    >
      <div className="flex flex-col gap-4">
        <div className="flex flex-col gap-2 p-3 rounded-lg border border-[var(--color-border-subtle)] bg-[var(--color-bg-canvas)]">
          <span className="text-[12px] font-medium text-[var(--color-text-secondary)]">
            新建分组
          </span>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="例如: 2026 校招储备池"
            className="px-3 py-2 rounded-md bg-white border border-[var(--color-border-subtle)] text-sm font-body focus:outline-none focus:border-[var(--color-text-primary)]"
          />
          <input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="简短描述(可选)"
            className="px-3 py-2 rounded-md bg-white border border-[var(--color-border-subtle)] text-[12px] font-body focus:outline-none focus:border-[var(--color-text-primary)]"
          />
          {error && (
            <span className="text-[12px] text-[var(--color-danger)]">{error}</span>
          )}
          <Button
            onClick={() => create.mutate()}
            disabled={!name.trim() || create.isPending}
          >
            <Plus className="w-4 h-4" />
            创建
          </Button>
        </div>

        {isLoading ? (
          <div className="text-sm text-[var(--color-text-tertiary)] font-body">
            加载中…
          </div>
        ) : !groups || groups.length === 0 ? (
          <div className="text-[12px] text-[var(--color-text-tertiary)] font-body">
            还没有分组,创建第一个吧。
          </div>
        ) : (
          <ul className="flex flex-col gap-2">
            {groups.map((g) => (
              <li
                key={g.id}
                className="flex items-start gap-2 p-3 rounded-lg border border-[var(--color-border-subtle)]"
              >
                <div className="flex-1 flex flex-col gap-0.5 min-w-0">
                  <span className="font-medium text-sm text-[var(--color-text-primary)]">
                    {g.name}
                  </span>
                  <span className="text-[11px] text-[var(--color-text-tertiary)]">
                    {g.member_count} 人 · 创建于 {formatRelative(g.created_at)}
                  </span>
                  {g.description && (
                    <span className="text-[12px] text-[var(--color-text-secondary)]">
                      {g.description}
                    </span>
                  )}
                </div>
                <button
                  type="button"
                  title="删除分组"
                  onClick={async () => {
                    if (
                      await confirm({
                        title: "删除分组?",
                        description: `将删除「${g.name}」及其全部成员关联(候选人本身不受影响)。`,
                        tone: "danger",
                        confirmLabel: "删除",
                      })
                    ) {
                      del.mutate(g.id);
                    }
                  }}
                  className="text-[var(--color-text-tertiary)] hover:text-[var(--color-danger)] p-1.5 rounded"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Drawer>
  );
}
