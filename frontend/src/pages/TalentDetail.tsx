import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Ban,
  CheckCircle2,
  ChevronLeft,
  ClipboardList,
  FileText,
  GitBranch,
  ListChecks,
  MessageCircle,
  Plus,
  Sparkles,
  Tag,
  Users,
  Video,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Empty } from "@/components/ui/empty";
import { Modal } from "@/components/ui/modal";
import { useConfirm } from "@/components/ui/confirm";
import { ApiError, fetchJSON } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { formatRelative } from "@/lib/format";

type TalentDetail = {
  id: string;
  name: string;
  display_email: string | null;
  display_phone: string | null;
  org_unit_id: string | null;
  tags: string[];
  status: string;
  is_blacklisted: boolean;
  blacklist_reason: string | null;
  blacklisted_at: string | null;
  blacklisted_by: string | null;
  last_active_at: string | null;
  created_at: string;
  resumes: ResumeRow[];
  interviews: InterviewRow[];
  matches: MatchRow[];
  groups: { id: string; name: string; description: string | null }[];
  recent_notes: NoteRow[];
};

type ResumeRow = {
  id: string;
  file_name: string;
  source: string;
  parse_status: string;
  created_at: string;
  skills: string[];
};

type InterviewRow = {
  id: string;
  job_id: string;
  job_title: string | null;
  mode: string;
  modality: string;
  status: string;
  recommendation: string | null;
  started_at: string | null;
  finished_at: string | null;
};

type MatchRow = {
  id: string;
  resume_id: string;
  job_id: string;
  job_title: string | null;
  status: string;
  score: number | null;
  created_at: string;
};

type NoteRow = {
  id: string;
  author_id: string;
  author_email: string;
  content: string;
  created_at: string;
};

type Group = {
  id: string;
  name: string;
  description: string | null;
  member_count: number;
};

type TimelineEvent = {
  at: string;
  kind: string;
  title: string;
  detail: Record<string, unknown> | null;
  ref: Record<string, string> | null;
};

const STATUS_LABEL: Record<string, string> = {
  in_progress: "进行中",
  done: "已完成",
  abandoned: "已放弃",
};

const KIND_LABEL: Record<string, { label: string; tone: string }> = {
  candidate_created: { label: "候选人入库", tone: "neutral" },
  resume_upload: { label: "简历上传", tone: "neutral" },
  interview_start: { label: "面试发起", tone: "info" },
  interview_done: { label: "面试完成", tone: "success" },
  note: { label: "备注", tone: "neutral" },
  blacklisted: { label: "加入黑名单", tone: "danger" },
  group_join: { label: "加入分组", tone: "info" },
};

export function TalentDetail() {
  const { id = "" } = useParams<{ id: string }>();
  const { user } = useAuth();
  const canWrite =
    user?.role === "admin" || user?.role === "hr";

  const qc = useQueryClient();

  const { data, isLoading, error } = useQuery<TalentDetail>({
    queryKey: ["talent", id],
    queryFn: () => fetchJSON<TalentDetail>(`/api/talents/${id}`),
  });
  const { data: timeline } = useQuery<TimelineEvent[]>({
    queryKey: ["talent-timeline", id],
    queryFn: () => fetchJSON<TimelineEvent[]>(`/api/talents/${id}/timeline`),
  });
  const { data: allGroups } = useQuery<Group[]>({
    queryKey: ["talent-groups"],
    queryFn: () => fetchJSON<Group[]>("/api/talent-groups"),
  });

  if (isLoading) {
    return (
      <main className="p-8 text-sm text-[var(--color-text-tertiary)] font-body">
        加载中…
      </main>
    );
  }
  if (error || !data) {
    return (
      <main className="p-8">
        <Empty
          icon={Users}
          title="候选人不存在或无权访问"
          description={
            error instanceof ApiError ? error.message : "请返回人才库列表"
          }
        />
        <Link
          to="/talents"
          className="text-sm text-[var(--color-text-primary)] hover:underline mt-3 inline-block"
        >
          ← 返回人才库
        </Link>
      </main>
    );
  }

  const c = data;
  const inGroupIds = new Set(c.groups.map((g) => g.id));

  return (
    <main className="p-8 flex flex-col gap-6 max-w-[1200px] mx-auto w-full">
      <Link
        to="/talents"
        className="self-start text-[12px] text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] flex items-center gap-1"
      >
        <ChevronLeft className="w-3.5 h-3.5" />
        返回人才库
      </Link>

      <div className="flex items-start justify-between gap-4">
        <div className="flex items-start gap-4 min-w-0">
          <div className="w-12 h-12 rounded-full bg-[var(--color-bg-subtle)] flex items-center justify-center text-base font-heading font-semibold shrink-0">
            {c.name.slice(0, 2)}
          </div>
          <div className="flex flex-col gap-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <h2 className="font-heading font-semibold text-2xl text-[var(--color-text-primary)]">
                {c.name}
              </h2>
              {c.is_blacklisted ? (
                <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[11px] font-medium bg-[var(--color-danger-soft)] text-[var(--color-danger)] font-body">
                  <Ban className="w-3 h-3" />
                  黑名单
                </span>
              ) : (
                <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[11px] font-medium bg-[var(--color-success-soft)] text-[var(--color-success)] font-body">
                  <CheckCircle2 className="w-3 h-3" />
                  活跃
                </span>
              )}
            </div>
            <span className="text-[13px] text-[var(--color-text-secondary)] font-body">
              {c.display_email ?? "—"}
              {c.display_phone ? ` · ${c.display_phone}` : ""}
            </span>
            <span className="text-[11px] text-[var(--color-text-tertiary)] font-body">
              入库 {formatRelative(c.created_at)}
              {c.last_active_at && ` · 最近活跃 ${formatRelative(c.last_active_at)}`}
            </span>
          </div>
        </div>
      </div>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_420px]">
        <div className="flex flex-col gap-6 min-w-0">
          <TagsCard
            tags={c.tags}
            candidateId={c.id}
            canWrite={canWrite}
            qc={qc}
          />

          <BlacklistCard cand={c} canWrite={canWrite} qc={qc} />

          <GroupsCard
            candidateId={c.id}
            allGroups={allGroups ?? []}
            joined={inGroupIds}
            canWrite={canWrite}
            qc={qc}
          />

          <Section icon={FileText} title="简历版本">
            {c.resumes.length === 0 ? (
              <Empty
                icon={FileText}
                title="还没有简历"
                description="候选人首次上传简历后会沉淀到这里"
              />
            ) : (
              <ul className="flex flex-col">
                {c.resumes.map((r, i) => (
                  <li
                    key={r.id}
                    className={`flex items-start gap-3 py-2.5 ${
                      i === c.resumes.length - 1
                        ? ""
                        : "border-b border-[var(--color-border-row)]"
                    }`}
                  >
                    <div className="flex-1 flex flex-col gap-0.5 min-w-0">
                      <span className="font-medium text-sm text-[var(--color-text-primary)]">
                        {r.file_name}
                      </span>
                      <span className="text-[11px] text-[var(--color-text-tertiary)]">
                        {r.source} · {formatRelative(r.created_at)} · {r.parse_status}
                      </span>
                      {r.skills.length > 0 && (
                        <div className="flex flex-wrap gap-1 mt-1">
                          {r.skills.slice(0, 8).map((s) => (
                            <span
                              key={s}
                              className="px-1.5 py-0.5 rounded text-[10px] bg-[var(--color-bg-subtle)] text-[var(--color-text-secondary)]"
                            >
                              {s}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Section>

          <Section icon={Video} title="面试历史">
            {c.interviews.length === 0 ? (
              <Empty
                icon={Video}
                title="还没有面试记录"
                description="候选人参与面试后会出现在这里"
              />
            ) : (
              <ul className="flex flex-col">
                {c.interviews.map((iv, i) => (
                  <li
                    key={iv.id}
                    className={`flex items-start gap-3 py-2.5 ${
                      i === c.interviews.length - 1
                        ? ""
                        : "border-b border-[var(--color-border-row)]"
                    }`}
                  >
                    <div className="flex-1 flex flex-col gap-0.5 min-w-0">
                      <span className="font-medium text-sm text-[var(--color-text-primary)]">
                        {iv.job_title ?? "未知岗位"}
                      </span>
                      <span className="text-[11px] text-[var(--color-text-tertiary)]">
                        {iv.modality === "voice" ? "语音" : "文本"} ·{" "}
                        {STATUS_LABEL[iv.status] ?? iv.status}
                        {iv.recommendation && ` · ${iv.recommendation}`}
                        {iv.finished_at &&
                          ` · ${formatRelative(iv.finished_at)}`}
                      </span>
                    </div>
                    <Link
                      to={`/interviews/${iv.id}/report`}
                      className="text-[12px] text-[var(--color-text-primary)] hover:underline shrink-0"
                    >
                      查看报告
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Section>

          <Section icon={ListChecks} title="岗位匹配">
            {c.matches.length === 0 ? (
              <Empty
                icon={ListChecks}
                title="还没有岗位匹配"
                description="简历解析完成后会自动评估匹配度"
              />
            ) : (
              <ul className="flex flex-col">
                {c.matches.map((m, i) => (
                  <li
                    key={m.id}
                    className={`flex items-center gap-3 py-2.5 ${
                      i === c.matches.length - 1
                        ? ""
                        : "border-b border-[var(--color-border-row)]"
                    }`}
                  >
                    <div className="flex-1 flex flex-col gap-0.5 min-w-0">
                      <span className="font-medium text-sm text-[var(--color-text-primary)]">
                        {m.job_title ?? "未知岗位"}
                      </span>
                      <span className="text-[11px] text-[var(--color-text-tertiary)]">
                        {m.status === "done" ? "已评估" : m.status} · {formatRelative(m.created_at)}
                      </span>
                    </div>
                    <span className="font-mono text-base text-[var(--color-text-primary)]">
                      {m.score ?? "—"}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Section>

          <NotesCard
            candidateId={c.id}
            notes={c.recent_notes}
            canWrite={canWrite}
            qc={qc}
          />
        </div>

        <Section icon={GitBranch} title="时间线">
          {!timeline || timeline.length === 0 ? (
            <div className="text-[12px] text-[var(--color-text-tertiary)] font-body py-4">
              暂无事件
            </div>
          ) : (
            <ol className="flex flex-col gap-3">
              {timeline.map((ev, i) => {
                const k = KIND_LABEL[ev.kind] ?? { label: ev.kind, tone: "neutral" };
                return (
                  <li key={i} className="flex gap-3">
                    <div className="flex flex-col items-center pt-1.5">
                      <span
                        className={`w-2 h-2 rounded-full ${
                          k.tone === "success"
                            ? "bg-[var(--color-success)]"
                            : k.tone === "danger"
                              ? "bg-[var(--color-danger)]"
                              : k.tone === "info"
                                ? "bg-[var(--color-accent)]"
                                : "bg-[var(--color-text-tertiary)]"
                        }`}
                      />
                      {i < timeline.length - 1 && (
                        <span className="flex-1 w-px bg-[var(--color-border-subtle)] my-1" />
                      )}
                    </div>
                    <div className="flex-1 flex flex-col gap-0.5 pb-2">
                      <span className="text-[12px] text-[var(--color-text-tertiary)] font-body">
                        {formatRelative(ev.at)} · {k.label}
                      </span>
                      <span className="text-sm text-[var(--color-text-primary)] font-body">
                        {ev.title}
                      </span>
                      {ev.detail &&
                        typeof ev.detail.content === "string" && (
                          <span className="text-[12px] text-[var(--color-text-secondary)] font-body">
                            {ev.detail.content as string}
                          </span>
                        )}
                    </div>
                  </li>
                );
              })}
            </ol>
          )}
        </Section>
      </div>
    </main>
  );
}

function Section({
  title,
  icon: Icon,
  children,
}: {
  title: string;
  icon: any;
  children: React.ReactNode;
}) {
  return (
    <section className="bg-white border border-[var(--color-border-subtle)] rounded-2xl p-6 flex flex-col gap-3">
      <div className="flex items-center gap-2 text-[var(--color-text-primary)]">
        <Icon className="w-4 h-4" />
        <h3 className="font-heading font-semibold text-base">{title}</h3>
      </div>
      {children}
    </section>
  );
}

function TagsCard({
  tags,
  candidateId,
  canWrite,
  qc,
}: {
  tags: string[];
  candidateId: string;
  canWrite: boolean;
  qc: any;
}) {
  const [editing, setEditing] = useState<string[] | null>(null);
  const [input, setInput] = useState("");
  const save = useMutation({
    mutationFn: (next: string[]) =>
      fetchJSON<{ tags: string[] }>(`/api/talents/${candidateId}/tags`, {
        method: "PUT",
        body: JSON.stringify({ tags: next }),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["talent", candidateId] });
      qc.invalidateQueries({ queryKey: ["talents"] });
      setEditing(null);
    },
  });

  return (
    <Section icon={Tag} title="标签">
      {editing ? (
        <div className="flex flex-col gap-2">
          <div className="flex flex-wrap gap-1.5">
            {editing.map((t) => (
              <span
                key={t}
                className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-[var(--color-bg-subtle)] text-[12px] text-[var(--color-text-primary)] font-body"
              >
                {t}
                <button
                  type="button"
                  onClick={() => setEditing(editing.filter((x) => x !== t))}
                  className="text-[var(--color-text-tertiary)] hover:text-[var(--color-danger)]"
                  aria-label={`移除 ${t}`}
                >
                  <X className="w-3 h-3" />
                </button>
              </span>
            ))}
            {editing.length === 0 && (
              <span className="text-[12px] text-[var(--color-text-tertiary)]">
                还没有标签
              </span>
            )}
          </div>
          <div className="flex gap-2">
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="输入标签后回车,例如:Java 高潜"
              onKeyDown={(e) => {
                if (e.key === "Enter" && input.trim()) {
                  e.preventDefault();
                  const next = Array.from(new Set([...editing, input.trim()]));
                  setEditing(next);
                  setInput("");
                }
              }}
              className="flex-1 px-3 py-2 rounded-md bg-white border border-[var(--color-border-subtle)] text-sm font-body focus:outline-none focus:border-[var(--color-text-primary)]"
            />
            <Button
              variant="secondary"
              onClick={() => setEditing(null)}
              className="px-3 py-2"
            >
              取消
            </Button>
            <Button
              onClick={() => save.mutate(editing)}
              disabled={save.isPending}
              className="px-3 py-2"
            >
              保存
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap gap-1.5">
          {tags.length === 0 ? (
            <span className="text-[12px] text-[var(--color-text-tertiary)]">
              还没有标签
            </span>
          ) : (
            tags.map((t) => (
              <span
                key={t}
                className="px-2 py-0.5 rounded bg-[var(--color-bg-subtle)] text-[12px] text-[var(--color-text-primary)] font-body"
              >
                {t}
              </span>
            ))
          )}
          {canWrite && (
            <button
              type="button"
              onClick={() => setEditing([...tags])}
              className="px-2 py-0.5 rounded text-[12px] text-[var(--color-text-primary)] border border-dashed border-[var(--color-border-subtle)] hover:bg-[var(--color-bg-muted)] font-body inline-flex items-center gap-1"
            >
              <Plus className="w-3 h-3" />
              编辑标签
            </button>
          )}
        </div>
      )}
    </Section>
  );
}

function BlacklistCard({
  cand,
  canWrite,
  qc,
}: {
  cand: TalentDetail;
  canWrite: boolean;
  qc: any;
}) {
  const confirm = useConfirm();
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");

  const add = useMutation({
    mutationFn: () =>
      fetchJSON(`/api/talents/${cand.id}/blacklist`, {
        method: "POST",
        body: JSON.stringify({ reason }),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["talent", cand.id] });
      qc.invalidateQueries({ queryKey: ["talent-timeline", cand.id] });
      qc.invalidateQueries({ queryKey: ["talents"] });
      setOpen(false);
      setReason("");
    },
  });
  const remove = useMutation({
    mutationFn: () =>
      fetchJSON(`/api/talents/${cand.id}/blacklist`, { method: "DELETE" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["talent", cand.id] });
      qc.invalidateQueries({ queryKey: ["talent-timeline", cand.id] });
      qc.invalidateQueries({ queryKey: ["talents"] });
    },
  });

  return (
    <Section icon={Ban} title="黑名单">
      {cand.is_blacklisted ? (
        <div className="flex flex-col gap-2">
          <div className="text-[13px] text-[var(--color-danger)] bg-[var(--color-danger-soft)] px-3 py-2 rounded-md font-body">
            候选人已在黑名单中
            {cand.blacklist_reason && ` · ${cand.blacklist_reason}`}
          </div>
          {canWrite && (
            <Button
              variant="secondary"
              onClick={async () => {
                if (
                  await confirm({
                    title: "移出黑名单?",
                    description: "移出后该候选人将正常出现在列表与搜索中。",
                    confirmLabel: "移出",
                  })
                ) {
                  remove.mutate();
                }
              }}
            >
              移出黑名单
            </Button>
          )}
        </div>
      ) : canWrite ? (
        <>
          <Button variant="secondary" onClick={() => setOpen(true)}>
            <Ban className="w-4 h-4" />
            加入黑名单
          </Button>
          <Modal
            open={open}
            onClose={() => setOpen(false)}
            title="加入黑名单"
            description="请简要填写原因,审计中心会保留这条变更记录。"
          >
            <div className="flex flex-col gap-3">
              <textarea
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                rows={3}
                placeholder="例如:面试爽约 / 简历造假 / 客户黑名单"
                className="px-3 py-2 rounded-md bg-white border border-[var(--color-border-subtle)] text-sm font-body focus:outline-none focus:border-[var(--color-text-primary)]"
              />
              <div className="flex justify-end gap-2">
                <Button variant="secondary" onClick={() => setOpen(false)}>
                  取消
                </Button>
                <Button onClick={() => add.mutate()} disabled={add.isPending}>
                  {add.isPending ? "提交中…" : "确认加入"}
                </Button>
              </div>
            </div>
          </Modal>
        </>
      ) : (
        <div className="text-[12px] text-[var(--color-text-tertiary)]">
          候选人不在黑名单
        </div>
      )}
    </Section>
  );
}

function GroupsCard({
  candidateId,
  allGroups,
  joined,
  canWrite,
  qc,
}: {
  candidateId: string;
  allGroups: Group[];
  joined: Set<string>;
  canWrite: boolean;
  qc: any;
}) {
  const addMember = useMutation({
    mutationFn: (groupId: string) =>
      fetchJSON(`/api/talent-groups/${groupId}/members`, {
        method: "POST",
        body: JSON.stringify({ candidate_ids: [candidateId] }),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["talent", candidateId] });
      qc.invalidateQueries({ queryKey: ["talent-timeline", candidateId] });
      qc.invalidateQueries({ queryKey: ["talent-groups"] });
    },
  });
  const removeMember = useMutation({
    mutationFn: (groupId: string) =>
      fetchJSON(
        `/api/talent-groups/${groupId}/members/${candidateId}`,
        { method: "DELETE" },
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["talent", candidateId] });
      qc.invalidateQueries({ queryKey: ["talent-timeline", candidateId] });
      qc.invalidateQueries({ queryKey: ["talent-groups"] });
    },
  });

  return (
    <Section icon={Sparkles} title="所在分组">
      {allGroups.length === 0 ? (
        <span className="text-[12px] text-[var(--color-text-tertiary)] font-body">
          租户内还没有分组。先在人才库右上角"人才分组"创建。
        </span>
      ) : (
        <div className="flex flex-wrap gap-1.5">
          {allGroups.map((g) => {
            const isIn = joined.has(g.id);
            return (
              <button
                key={g.id}
                type="button"
                disabled={!canWrite}
                onClick={() =>
                  isIn ? removeMember.mutate(g.id) : addMember.mutate(g.id)
                }
                className={`inline-flex items-center gap-1 px-2 py-1 rounded text-[12px] font-body border transition-colors ${
                  isIn
                    ? "bg-[var(--color-accent)] text-white border-[var(--color-accent)]"
                    : "bg-white text-[var(--color-text-secondary)] border-[var(--color-border-subtle)] hover:bg-[var(--color-bg-muted)]"
                } ${!canWrite ? "cursor-not-allowed opacity-70" : ""}`}
              >
                {isIn ? <CheckCircle2 className="w-3 h-3" /> : <Plus className="w-3 h-3" />}
                {g.name}
              </button>
            );
          })}
        </div>
      )}
    </Section>
  );
}

function NotesCard({
  candidateId,
  notes,
  canWrite,
  qc,
}: {
  candidateId: string;
  notes: NoteRow[];
  canWrite: boolean;
  qc: any;
}) {
  const [content, setContent] = useState("");
  const add = useMutation({
    mutationFn: () =>
      fetchJSON<NoteRow>(`/api/talents/${candidateId}/notes`, {
        method: "POST",
        body: JSON.stringify({ content: content.trim() }),
      }),
    onSuccess: () => {
      setContent("");
      qc.invalidateQueries({ queryKey: ["talent", candidateId] });
      qc.invalidateQueries({ queryKey: ["talent-timeline", candidateId] });
    },
  });

  return (
    <Section icon={MessageCircle} title="备注">
      {canWrite && (
        <div className="flex flex-col gap-2">
          <textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            rows={2}
            placeholder="记录你对这位候选人的判断,例如:'适合做技术面 2-3 轮' '需要 review 项目真实性'"
            className="px-3 py-2 rounded-md bg-white border border-[var(--color-border-subtle)] text-sm font-body focus:outline-none focus:border-[var(--color-text-primary)]"
          />
          <Button
            onClick={() => add.mutate()}
            disabled={!content.trim() || add.isPending}
            className="self-start"
          >
            <Plus className="w-4 h-4" />
            添加备注
          </Button>
        </div>
      )}
      {notes.length === 0 ? (
        <Empty
          icon={ClipboardList}
          title="还没有备注"
          description="备注会自动出现在右侧时间线"
        />
      ) : (
        <ul className="flex flex-col gap-2">
          {notes.map((n) => (
            <li
              key={n.id}
              className="flex flex-col gap-0.5 p-3 rounded-lg bg-[var(--color-bg-canvas)] border border-[var(--color-border-subtle)]"
            >
              <span className="text-[11px] text-[var(--color-text-tertiary)] font-body">
                {n.author_email} · {formatRelative(n.created_at)}
              </span>
              <span className="text-[13px] text-[var(--color-text-primary)] font-body whitespace-pre-wrap break-words">
                {n.content}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}
