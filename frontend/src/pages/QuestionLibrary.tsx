import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BookOpen,
  Plus,
  Sparkles,
  Trash2,
  Pencil,
  Loader2,
} from "lucide-react";
import { Empty } from "@/components/ui/empty";
import { useConfirm } from "@/components/ui/confirm";
import { SearchInput } from "@/components/ui/search-input";
import { Pagination } from "@/components/ui/pagination";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Drawer } from "@/components/ui/drawer";
import { Modal } from "@/components/ui/modal";
import { fetchJSON } from "@/lib/api";
import { usePagedQuery, PAGE_SIZE } from "@/lib/usePagedQuery";
import { formatRelative } from "@/lib/format";

type LibraryItem = {
  id: string;
  question: string;
  answer_points: string[];
  kind: string;
  difficulty: string;
  category: string;
  skill: string | null;
  follow_up: string | null;
  source: "manual" | "ai_generated";
  use_count: number;
  avg_score: number | null;
  generated_from_job_id: string | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
};

type Job = { id: string; title: string };

const KIND_LABEL: Record<string, string> = {
  tech: "技术深度",
  project: "项目复盘",
  scenario: "场景排查",
  soft: "软技能",
};
const DIFF_LABEL: Record<string, string> = {
  initial: "初级",
  intermediate: "中级",
  advanced: "高级",
  expert: "专家",
};

export function QuestionLibrary() {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const [error, setError] = useState<string | null>(null);
  const [filterKind, setFilterKind] = useState<string>("");
  const [filterDifficulty, setFilterDifficulty] = useState<string>("");
  const [editing, setEditing] = useState<LibraryItem | null>(null);
  const [creating, setCreating] = useState(false);
  const [genOpen, setGenOpen] = useState(false);

  const params: Record<string, string | undefined> = {};
  if (filterKind) params.kind = filterKind;
  if (filterDifficulty) params.difficulty = filterDifficulty;

  const { data, isLoading, page, pageCount, total, goto, q, setQ } =
    usePagedQuery<LibraryItem>({
      key: ["question-library", filterKind, filterDifficulty],
      url: "/api/question-library/",
      params,
    });

  const [inputQ, setInputQ] = useState(q);
  useEffect(() => {
    setInputQ(q);
  }, [q]);

  const items = data?.items ?? [];

  const del = useMutation({
    mutationFn: (id: string) =>
      fetchJSON(`/api/question-library/${id}`, { method: "DELETE" }),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["question-library"] }),
    onError: (e: unknown) =>
      setError(e instanceof Error ? e.message : "删除失败"),
  });

  return (
    <main className="p-8 flex flex-col gap-6 max-w-[1200px] mx-auto w-full">
      <div className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-1.5">
          <h2 className="font-heading font-semibold text-2xl text-[var(--color-text-primary)]">
            题库
          </h2>
          <p className="text-sm text-[var(--color-text-secondary)] font-body">
            可复用的问题池：手动录入 + AI 生成，按题型/难度/分类管理
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="secondary" onClick={() => setGenOpen(true)}>
            <Sparkles className="w-4 h-4" />
            AI 生成
          </Button>
          <Button onClick={() => setCreating(true)}>
            <Plus className="w-4 h-4" />
            手动新增
          </Button>
        </div>
      </div>

      <div className="flex items-center gap-3 flex-wrap">
        <SearchInput
          value={inputQ}
          onChange={(v) => {
            setInputQ(v);
            setQ(v);
          }}
          placeholder="搜索题干 / 分类"
          className="w-[280px]"
        />
        <Select
          value={filterKind}
          onChange={setFilterKind}
          options={[
            { value: "", label: "全部题型" },
            ...Object.entries(KIND_LABEL).map(([k, l]) => ({
              value: k,
              label: l,
            })),
          ]}
          className="w-[160px]"
        />
        <Select
          value={filterDifficulty}
          onChange={setFilterDifficulty}
          options={[
            { value: "", label: "全部难度" },
            ...Object.entries(DIFF_LABEL).map(([k, l]) => ({
              value: k,
              label: l,
            })),
          ]}
          className="w-[160px]"
        />
        {q && (
          <span className="text-[13px] text-[var(--color-text-secondary)] font-body">
            命中{" "}
            <span className="font-mono font-semibold text-[var(--color-text-primary)]">
              {total}
            </span>{" "}
            条
          </span>
        )}
      </div>

      {error && (
        <div className="text-[13px] text-[var(--color-danger)] bg-[var(--color-danger-soft)] px-3 py-2 rounded-md font-body">
          {error}
        </div>
      )}

      <div className="bg-white border border-[var(--color-border-subtle)] rounded-2xl overflow-hidden">
        {isLoading ? (
          <div className="p-12 text-center text-sm text-[var(--color-text-tertiary)] font-body">
            加载中…
          </div>
        ) : items.length === 0 ? (
          <Empty
            icon={BookOpen}
            title={q || filterKind || filterDifficulty ? "没有匹配的题目" : "题库还是空的"}
            description={
              q || filterKind || filterDifficulty
                ? "试试别的筛选条件"
                : "点击「手动新增」创建第一道题，或「AI 生成」批量生成"
            }
          />
        ) : (
          <>
            {items.map((it, i) => (
              <Row
                key={it.id}
                item={it}
                isLast={i === items.length - 1}
                onEdit={() => setEditing(it)}
                onDelete={async () => {
                  if (
                    await confirm({
                      title: "删除题目?",
                      description: "操作不可恢复",
                      tone: "danger",
                      confirmLabel: "删除",
                    })
                  )
                    del.mutate(it.id);
                }}
              />
            ))}
            <Pagination
              page={page}
              pageCount={pageCount}
              total={total}
              pageSize={PAGE_SIZE}
              onChange={goto}
              className="border-t border-[var(--color-border-row)]"
            />
          </>
        )}
      </div>

      {creating && (
        <ItemDrawer
          open={creating}
          onClose={() => setCreating(false)}
          item={null}
        />
      )}
      {editing && (
        <ItemDrawer
          open={true}
          onClose={() => setEditing(null)}
          item={editing}
        />
      )}
      {genOpen && (
        <GenerateDialog open={genOpen} onClose={() => setGenOpen(false)} />
      )}
    </main>
  );
}

function Row({
  item,
  isLast,
  onEdit,
  onDelete,
}: {
  item: LibraryItem;
  isLast: boolean;
  onEdit: () => void;
  onDelete: () => void;
}) {
  return (
    <div
      className={`px-6 py-4 group hover:bg-[var(--color-bg-muted)] transition-colors ${
        isLast ? "" : "border-b border-[var(--color-border-row)]"
      }`}
    >
      <div className="flex items-start justify-between gap-4">
        <div className="flex flex-col flex-1 min-w-0 gap-2">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-[var(--color-bg-subtle)] text-[var(--color-text-secondary)] font-body">
              {KIND_LABEL[item.kind] ?? item.kind}
            </span>
            <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-[var(--color-bg-subtle)] text-[var(--color-text-secondary)] font-body">
              {DIFF_LABEL[item.difficulty] ?? item.difficulty}
            </span>
            {item.category && (
              <span className="text-[11px] text-[var(--color-text-tertiary)] font-body">
                {item.category}
              </span>
            )}
            <span className="text-[10px] text-[var(--color-text-tertiary)] font-body">
              {item.source === "ai_generated" ? "AI 生成" : "手动"}
            </span>
            {item.use_count > 0 && (
              <span className="text-[10px] text-[var(--color-text-tertiary)] font-body">
                · 引用 {item.use_count} 次
              </span>
            )}
          </div>
          <div className="text-sm text-[var(--color-text-primary)] font-body leading-relaxed">
            {item.question}
          </div>
          {item.answer_points.length > 0 && (
            <ul className="text-[12px] text-[var(--color-text-secondary)] font-body list-disc list-inside space-y-0.5">
              {item.answer_points.slice(0, 3).map((p, i) => (
                <li key={i}>{p}</li>
              ))}
              {item.answer_points.length > 3 && (
                <li className="text-[var(--color-text-tertiary)]">
                  …还有 {item.answer_points.length - 3} 条
                </li>
              )}
            </ul>
          )}
          <div className="text-[11px] text-[var(--color-text-tertiary)] font-body">
            {formatRelative(item.created_at)}
          </div>
        </div>
        <div className="opacity-0 group-hover:opacity-100 transition-opacity flex gap-1">
          <button
            onClick={onEdit}
            className="text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)] p-1"
            aria-label="编辑"
          >
            <Pencil className="w-4 h-4" />
          </button>
          <button
            onClick={onDelete}
            className="text-[var(--color-text-tertiary)] hover:text-[var(--color-danger)] p-1"
            aria-label="删除"
          >
            <Trash2 className="w-4 h-4" />
          </button>
        </div>
      </div>
    </div>
  );
}

function ItemDrawer({
  open,
  onClose,
  item,
}: {
  open: boolean;
  onClose: () => void;
  item: LibraryItem | null;
}) {
  const qc = useQueryClient();
  const isEdit = !!item;
  const [question, setQuestion] = useState(item?.question ?? "");
  const [answerText, setAnswerText] = useState(
    (item?.answer_points ?? []).join("\n"),
  );
  const [kind, setKind] = useState(item?.kind ?? "tech");
  const [difficulty, setDifficulty] = useState(
    item?.difficulty ?? "intermediate",
  );
  const [category, setCategory] = useState(item?.category ?? "");
  const [skill, setSkill] = useState(item?.skill ?? "");
  const [followUp, setFollowUp] = useState(item?.follow_up ?? "");
  const [err, setErr] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: async () => {
      const payload = {
        question: question.trim(),
        answer_points: answerText
          .split("\n")
          .map((s) => s.trim())
          .filter(Boolean),
        kind,
        difficulty,
        category: category.trim(),
        skill: skill.trim() || null,
        follow_up: followUp.trim() || null,
      };
      if (isEdit) {
        await fetchJSON(`/api/question-library/${item!.id}`, {
          method: "PUT",
          body: JSON.stringify(payload),
        });
      } else {
        await fetchJSON("/api/question-library/", {
          method: "POST",
          body: JSON.stringify(payload),
        });
      }
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["question-library"] });
      onClose();
    },
    onError: (e: unknown) =>
      setErr(e instanceof Error ? e.message : "保存失败"),
  });

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title={isEdit ? "编辑题目" : "新增题目"}
      description="题干 + 答题要点是面试时的核心参考"
    >
      <div className="flex flex-col gap-4">
        <Field label="题干">
          <textarea
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            rows={3}
            className="w-full px-3 py-2 rounded-md border border-[var(--color-border-subtle)] text-sm font-body focus:outline-none focus:border-[var(--color-accent)]"
            placeholder="例如：聊一下你最熟悉的技术栈的某个核心机制"
          />
        </Field>
        <Field label="答题要点（每行一条）">
          <textarea
            value={answerText}
            onChange={(e) => setAnswerText(e.target.value)}
            rows={5}
            className="w-full px-3 py-2 rounded-md border border-[var(--color-border-subtle)] text-sm font-body focus:outline-none focus:border-[var(--color-accent)]"
            placeholder={"原理概览\n关键数据结构\n适用场景"}
          />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="题型">
            <Select
              value={kind}
              onChange={setKind}
              options={Object.entries(KIND_LABEL).map(([k, l]) => ({
                value: k,
                label: l,
              }))}
            />
          </Field>
          <Field label="难度">
            <Select
              value={difficulty}
              onChange={setDifficulty}
              options={Object.entries(DIFF_LABEL).map(([k, l]) => ({
                value: k,
                label: l,
              }))}
            />
          </Field>
        </div>
        <Field label="分类（可选，如 Python 后端）">
          <Input
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            placeholder="后端工程师 / 前端工程师 …"
          />
        </Field>
        <Field label="技能标签（可选）">
          <Input
            value={skill}
            onChange={(e) => setSkill(e.target.value)}
            placeholder="例如 Python / MySQL / React"
          />
        </Field>
        <Field label="追问（可选）">
          <Input
            value={followUp}
            onChange={(e) => setFollowUp(e.target.value)}
            placeholder="若有更深一层追问"
          />
        </Field>

        {err && (
          <div className="text-[13px] text-[var(--color-danger)] bg-[var(--color-danger-soft)] px-3 py-2 rounded-md font-body">
            {err}
          </div>
        )}

        <div className="flex justify-end gap-2 pt-2">
          <Button variant="secondary" onClick={onClose}>
            取消
          </Button>
          <Button
            disabled={save.isPending || question.trim().length < 2}
            onClick={() => save.mutate()}
          >
            {save.isPending && <Loader2 className="w-4 h-4 animate-spin" />}
            保存
          </Button>
        </div>
      </div>
    </Drawer>
  );
}

function GenerateDialog({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [jobId, setJobId] = useState("");
  const [category, setCategory] = useState("");
  const [kind, setKind] = useState("tech");
  const [difficulty, setDifficulty] = useState("intermediate");
  const [count, setCount] = useState(5);
  const [err, setErr] = useState<string | null>(null);

  const jobsQ = useQuery<{ items: Job[]; total: number }>({
    queryKey: ["jobs-for-library"],
    queryFn: () => fetchJSON(`/api/jobs/?limit=100&offset=0`),
  });

  const gen = useMutation({
    mutationFn: () =>
      fetchJSON(`/api/question-library/generate`, {
        method: "POST",
        body: JSON.stringify({
          job_id: jobId || null,
          category: category.trim(),
          kind,
          difficulty,
          count,
        }),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["question-library"] });
      onClose();
    },
    onError: (e: unknown) =>
      setErr(e instanceof Error ? e.message : "AI 生成失败"),
  });

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="AI 批量生成"
      description="按岗位 / 题型 / 难度生成多道题，自动入库"
      footer={
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose}>
            取消
          </Button>
          <Button disabled={gen.isPending} onClick={() => gen.mutate()}>
            {gen.isPending && <Loader2 className="w-4 h-4 animate-spin" />}
            开始生成
          </Button>
        </div>
      }
    >
      <div className="flex flex-col gap-4">
        <Field label="岗位（可选 — 选岗位后按岗位需求出题）">
          <Select
            value={jobId}
            onChange={setJobId}
            options={[
              { value: "", label: "不指定岗位" },
              ...(jobsQ.data?.items ?? []).map((j) => ({
                value: j.id,
                label: j.title,
              })),
            ]}
          />
        </Field>
        <Field label="分类标签（不选岗位时建议填）">
          <Input
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            placeholder="后端工程师 / 数据分析 …"
          />
        </Field>
        <div className="grid grid-cols-3 gap-3">
          <Field label="题型">
            <Select
              value={kind}
              onChange={setKind}
              options={Object.entries(KIND_LABEL).map(([k, l]) => ({
                value: k,
                label: l,
              }))}
            />
          </Field>
          <Field label="难度">
            <Select
              value={difficulty}
              onChange={setDifficulty}
              options={Object.entries(DIFF_LABEL).map(([k, l]) => ({
                value: k,
                label: l,
              }))}
            />
          </Field>
          <Field label="题数">
            <Select
              value={String(count)}
              onChange={(v) => setCount(Number(v))}
              options={[3, 5, 10, 15, 20].map((n) => ({
                value: String(n),
                label: String(n),
              }))}
            />
          </Field>
        </div>
        {err && (
          <div className="text-[13px] text-[var(--color-danger)] bg-[var(--color-danger-soft)] px-3 py-2 rounded-md font-body">
            {err}
          </div>
        )}
      </div>
    </Modal>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-[12px] font-medium text-[var(--color-text-secondary)] font-body">
        {label}
      </span>
      {children}
    </label>
  );
}
