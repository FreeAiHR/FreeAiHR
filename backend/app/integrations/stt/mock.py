"""Mock STT — 不依赖外部 API,demo / CI / 测试用。

设计目标:
- 不引入网络,不需要 API key
- 根据音频字节长度生成"看上去合理"的文本和 segments,让前端能演示完整链路
- 输出可重复(同样的字节 → 同样的转写),便于断言
- segments 数量与音频长度成正比,让 voice_signals 计算逻辑可被验证

用法:在测试或 demo 模式下,设置 ``settings.stt_backend = "mock"``(默认即此值),
:func:`app.integrations.stt.transcribe` 自动路由到这里。
"""
from __future__ import annotations

import hashlib

from app.integrations.stt.provider import STTResult, STTSegment

# 候选人面试常见的中文回答片段。Mock 用,长度差异让"语速"等指标看着可信。
_MOCK_SENTENCES = [
    "我之前在一家互联网公司工作,主要负责后端服务的开发和维护。",
    "在那个项目里,我用 Python 和 FastAPI 搭建了一套订单处理系统。",
    "遇到的最大挑战是高并发场景下的数据一致性问题。",
    "我用 Redis 做了一层分布式锁来解决这个问题。",
    "上线后系统稳定运行了半年多,日均处理订单超过十万单。",
    "关于这个技术选型,我们当时也考虑过其他方案,比如 Kafka。",
    "最终选 Redis 是因为团队对它更熟悉,而且延迟更低。",
    "我的角色主要是技术 owner,从设计到实现再到上线全程参与。",
]


def _bytes_seed(audio_bytes: bytes) -> int:
    """从音频字节算一个稳定整数,用于挑选确定性句子。"""
    h = hashlib.sha256(audio_bytes).hexdigest()
    return int(h[:8], 16)


def _estimate_duration_ms(audio_bytes: bytes) -> int:
    """很粗的估算:Opus ~32kbps,字节数 / 4 ≈ 毫秒。

    真实厂商返回精确值;mock 只需要量级合理,让 voice_signals 的语速计算不至于
    炸到 0 或 inf。最低 1 秒,最高 5 分钟(避免极端 fixture 把测试搞崩)。
    """
    est = max(1000, len(audio_bytes) // 4)
    return min(est, 5 * 60 * 1000)


def mock_transcribe(audio_bytes: bytes, *, language: str = "zh") -> STTResult:
    """根据音频字节长度返回若干句拼接的中文文本。

    - 1 秒以内 → 1 句
    - 1-5 秒  → 2 句
    - 5+ 秒   → 按每 3 秒一句线性增加,上限 8 句

    生成的 segments 用于 voice_signals 计算:每段 1.5 秒,段间留 200ms 静默。
    """
    if not audio_bytes:
        # 空音频:返回空转写,upstream 应当转 transcript_status='failed'
        return STTResult(
            text="",
            segments=[],
            speakers_count=0,
            duration_ms=0,
            backend="mock",
        )

    duration_ms = _estimate_duration_ms(audio_bytes)
    seconds = duration_ms / 1000

    if seconds < 1:
        n_sentences = 1
    elif seconds < 5:
        n_sentences = 2
    else:
        n_sentences = min(8, 2 + int((seconds - 5) // 3))

    seed = _bytes_seed(audio_bytes)
    chosen = [_MOCK_SENTENCES[(seed + i) % len(_MOCK_SENTENCES)] for i in range(n_sentences)]
    text = "".join(chosen)

    # 构造 segments:每句 1500ms speaking + 200ms 静默
    segments: list[STTSegment] = []
    cursor = 0
    for s in chosen:
        seg_duration = 1500
        segments.append(
            STTSegment(
                text=s,
                start_ms=cursor,
                end_ms=cursor + seg_duration,
                speaker="cand-1",
            )
        )
        cursor += seg_duration + 200
    # mock 总时长以分段累计为准,保持 segments / duration 自洽
    duration_ms = max(duration_ms, cursor)

    return STTResult(
        text=text,
        segments=segments,
        speakers_count=1,
        duration_ms=duration_ms,
        backend="mock",
    )
