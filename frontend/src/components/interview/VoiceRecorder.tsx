import { useEffect, useRef, useState } from "react";
import { Mic, Send } from "lucide-react";
import { Button } from "@/components/ui/button";

/**
 * 候选人侧语音录音组件(M6 伪实时面试)。
 *
 * 严格的伪实时约束:
 * - 入场后**自动开始录音**(parent 控制 ``recording`` 标志)
 * - **没有暂停按钮** — 一旦开始,直到提交或超时为止
 * - **没有重录按钮** — 提交后不能回头
 * - 倒计时到 ``maxSeconds`` 自动调用 onSubmit(模拟"超时强制交卷")
 * - 实时显示音量波形 + 倒计时,让候选人有"在被听"的反馈
 *
 * 父组件 (``VoiceSession``) 负责:
 * - 控制 ``recording`` 何时为 true(候选人通过权限 + TTS 念完题之后)
 * - 在 ``onSubmit`` 回调里把音频上传 → 后端入队 STT
 * - 上传完成后把 ``recording`` 关掉,触发组件清理
 */

export type VoiceRecorderProps = {
  /** parent 设为 true → 立即 start;false → 立即 stop(若在录音) */
  recording: boolean;
  /** 单题录音上限(秒)。倒计时跑完自动 stop + onSubmit。 */
  maxSeconds: number;
  /** 录音停止(无论手动 / 超时)后回调。blob 是原始音频。 */
  onSubmit: (audio: Blob, durationMs: number) => void;
  /** 录音中显示的文案,默认"正在录音…"。 */
  hint?: string;
  /** 上传中(parent 已经拿走 blob 在 POST)— 禁用提交按钮 */
  uploading?: boolean;
  /** 麦克风权限 / 设备失败时父组件设这个 — 录音组件 disable 自己 */
  errorMessage?: string | null;
};

const MIME_CANDIDATES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/mp4", // Safari
  "audio/ogg;codecs=opus",
];

function pickMime(): string | undefined {
  if (typeof MediaRecorder === "undefined") return undefined;
  for (const m of MIME_CANDIDATES) {
    if (MediaRecorder.isTypeSupported(m)) return m;
  }
  return undefined;
}

export function VoiceRecorder({
  recording,
  maxSeconds,
  onSubmit,
  hint = "正在录音…",
  uploading = false,
  errorMessage = null,
}: VoiceRecorderProps) {
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const startedAtRef = useRef<number>(0);
  // 录音 stop 时是否已经回调了 onSubmit — 避免 stop event 与 click "提交" 双触发
  const submittedRef = useRef<boolean>(false);
  // 上传中 / 父组件控制 recording=false 期间,需要保留最后一次 duration 数字给 UI
  const [elapsedMs, setElapsedMs] = useState(0);
  // 实时音量(0-100),给波形条用。无需精确 — 只为反馈
  const [volume, setVolume] = useState(0);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const rafRef = useRef<number | null>(null);
  const [bootError, setBootError] = useState<string | null>(null);

  // ---- 录音生命周期 ----

  useEffect(() => {
    if (!recording) {
      // parent 关掉了 → 主动 stop(可能是因为 onSubmit 后 parent 不再要这个组件)
      stopRecording();
      return;
    }
    // recording=true → 启动
    let cancelled = false;
    submittedRef.current = false;
    chunksRef.current = [];
    setElapsedMs(0);
    setBootError(null);

    (async () => {
      try {
        if (typeof MediaRecorder === "undefined") {
          throw new Error("当前浏览器不支持录音 (MediaRecorder)");
        }
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
          // 浏览器把 mediaDevices 砍成 undefined 的常见原因:
          // 1) 页面非 HTTPS 也非 localhost(secure context 限制)
          // 2) 旧版 / 嵌入 iframe 没有 allow="microphone"
          // 3) 在隐私模式下被禁
          const isInsecure =
            typeof window !== "undefined" && !window.isSecureContext;
          throw new Error(
            isInsecure
              ? "当前页面非 HTTPS,浏览器禁止访问麦克风。请用 HTTPS 域名或 localhost 打开。"
              : "当前浏览器不允许访问麦克风。请换一个最新版的 Chrome / Edge / Safari 重试。",
          );
        }
        const stream = await navigator.mediaDevices.getUserMedia({
          audio: {
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
          },
        });
        if (cancelled) {
          stream.getTracks().forEach((t) => t.stop());
          return;
        }
        streamRef.current = stream;

        // 音量分析(只为 UI 反馈)
        const ctx = new AudioContext();
        audioCtxRef.current = ctx;
        const src = ctx.createMediaStreamSource(stream);
        const analyser = ctx.createAnalyser();
        analyser.fftSize = 256;
        src.connect(analyser);
        analyserRef.current = analyser;
        tickVolume();

        const mime = pickMime();
        const rec = mime
          ? new MediaRecorder(stream, { mimeType: mime })
          : new MediaRecorder(stream);
        rec.ondataavailable = (e) => {
          if (e.data && e.data.size > 0) chunksRef.current.push(e.data);
        };
        rec.onstop = () => {
          if (submittedRef.current) return;
          submittedRef.current = true;
          const duration = Date.now() - startedAtRef.current;
          const blob = new Blob(chunksRef.current, {
            type: rec.mimeType || "audio/webm",
          });
          onSubmit(blob, duration);
        };
        recorderRef.current = rec;
        startedAtRef.current = Date.now();
        rec.start(250); // 250ms 采集一次,降低 stop 时丢尾的概率
      } catch (err) {
        const msg =
          err instanceof Error
            ? err.name === "NotAllowedError"
              ? "麦克风权限被拒绝。请在浏览器地址栏的权限图标里允许后刷新页面。"
              : err.message
            : "麦克风启动失败";
        setBootError(msg);
      }
    })();

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recording]);

  // 倒计时驱动:每 100ms 更新一次 elapsedMs;到 maxSeconds 自动 stop
  useEffect(() => {
    if (!recording) return;
    const id = setInterval(() => {
      const elapsed = Date.now() - startedAtRef.current;
      setElapsedMs(elapsed);
      if (elapsed >= maxSeconds * 1000) {
        // 超时 → 触发 stop,onstop 回调里发 onSubmit
        stopRecording();
        clearInterval(id);
      }
    }, 100);
    return () => clearInterval(id);
  }, [recording, maxSeconds]);

  function stopRecording() {
    if (rafRef.current) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    const rec = recorderRef.current;
    if (rec && rec.state !== "inactive") {
      try {
        rec.stop();
      } catch {
        // 已经 stopped — 忽略
      }
    }
    recorderRef.current = null;
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    if (audioCtxRef.current) {
      audioCtxRef.current.close().catch(() => {});
      audioCtxRef.current = null;
    }
    analyserRef.current = null;
  }

  function tickVolume() {
    const analyser = analyserRef.current;
    if (!analyser) return;
    const data = new Uint8Array(analyser.frequencyBinCount);
    const loop = () => {
      if (!analyserRef.current) return;
      analyserRef.current.getByteTimeDomainData(data);
      // RMS 估算,128 是 silence baseline,峰值 0/255 → 偏离 128 越多说话越响
      let sum = 0;
      for (let i = 0; i < data.length; i++) {
        const v = data[i] - 128;
        sum += v * v;
      }
      const rms = Math.sqrt(sum / data.length);
      // 映射到 0-100,经验值
      const v = Math.min(100, Math.round((rms / 64) * 100));
      setVolume(v);
      rafRef.current = requestAnimationFrame(loop);
    };
    rafRef.current = requestAnimationFrame(loop);
  }

  function manualStop() {
    // 候选人主动点"提交答案" — 直接 stop,onstop 触发 onSubmit
    stopRecording();
  }

  // ---- 渲染 ----

  const remainingSec = Math.max(
    0,
    Math.ceil((maxSeconds * 1000 - elapsedMs) / 1000),
  );
  const progress = Math.min(100, (elapsedMs / (maxSeconds * 1000)) * 100);
  const isError = bootError || errorMessage;

  if (isError) {
    return (
      <div className="flex flex-col items-center gap-3 text-center px-6 py-8">
        <div className="w-12 h-12 rounded-full bg-[var(--color-danger-soft)] text-[var(--color-danger)] flex items-center justify-center">
          <Mic className="w-5 h-5" />
        </div>
        <div className="text-sm font-medium text-[var(--color-text-primary)] font-body">
          录音不可用
        </div>
        <div className="text-[12px] text-[var(--color-text-secondary)] font-body max-w-sm">
          {isError}
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col items-center gap-5 px-6 py-8">
      {/* 麦克风脉冲 + 音量波形 */}
      <div className="relative">
        <div
          className="w-20 h-20 rounded-full bg-[var(--color-accent)] text-white flex items-center justify-center shadow-lg"
          style={{
            transform: `scale(${1 + volume / 400})`,
            transition: "transform 80ms",
          }}
        >
          <Mic className="w-7 h-7" />
        </div>
        {/* 波纹环 */}
        <div
          className="absolute inset-0 rounded-full border-2 border-[var(--color-accent)] opacity-40 pointer-events-none"
          style={{
            transform: `scale(${1.1 + volume / 200})`,
            opacity: Math.max(0.1, 0.5 - volume / 200),
            transition: "transform 120ms, opacity 120ms",
          }}
        />
      </div>

      <div className="text-sm font-medium text-[var(--color-text-primary)] font-body">
        {uploading ? "正在上传录音…" : hint}
      </div>

      {/* 倒计时进度条 */}
      <div className="w-full max-w-sm flex flex-col gap-1.5">
        <div className="flex items-center justify-between text-[11px] font-mono text-[var(--color-text-tertiary)]">
          <span>{formatMs(elapsedMs)}</span>
          <span>剩余 {remainingSec}s</span>
        </div>
        <div className="h-1.5 rounded-full bg-[var(--color-bg-subtle)] overflow-hidden">
          <div
            className={`h-full transition-all duration-100 ${
              progress > 80
                ? "bg-[var(--color-danger)]"
                : "bg-[var(--color-accent)]"
            }`}
            style={{ width: `${progress}%` }}
          />
        </div>
      </div>

      <Button
        onClick={manualStop}
        disabled={uploading || !recording}
        fullWidth
        className="max-w-sm"
      >
        <Send className="w-4 h-4" />
        {uploading ? "上传中…" : "提交答案"}
      </Button>

      <div className="text-[11px] text-[var(--color-text-tertiary)] font-body text-center max-w-sm">
        全程录音 · 不可暂停 · 不可重录
        <br />
        {remainingSec <= 10 && remainingSec > 0
          ? `还有 ${remainingSec} 秒,请尽快收尾`
          : "倒计时归零将自动提交"}
      </div>
    </div>
  );
}

function formatMs(ms: number): string {
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}`;
}
