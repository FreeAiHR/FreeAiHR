"""LLM Provider 抽象层。

业务代码(如 :mod:`app.services.interviewer`)只依赖 :func:`chat` 接口;
具体后端由 :func:`app.integrations.llm.registry.resolve_provider` 在请求时选择:

- DB 中 ``is_active=true`` 的 ``LLMProvider`` 行(管理员通过 UI 配的)
- ``.env`` 默认值(没 DB 配置时兜底)
- ``mock`` 兜底(没 key 时 demo 仍可走通)

集成路径用 LiteLLM,客户在 UI 填 base_url + api_key + LiteLLM 标识符(如
``openai/gpt-4o-mini``、``deepseek/deepseek-chat``、``azure/<deployment>``)即可,
后端不再做前缀映射。
"""
from __future__ import annotations

import json
import logging
import re
from typing import TypedDict

from sqlalchemy.orm import Session

from app.integrations.llm.registry import ResolvedProvider, resolve_provider

logger = logging.getLogger(__name__)


class Message(TypedDict):
    role: str  # system / user / assistant
    content: str


class LLMError(RuntimeError):
    pass


def chat(
    messages: list[Message],
    *,
    db: Session | None = None,
    tenant_id: str | None = None,
    provider: ResolvedProvider | None = None,
    temperature: float = 0.7,
    response_json: bool = False,
) -> str:
    """统一对话接口。

    ``db`` 与 ``tenant_id`` 用于查 DB provider;调用方若已经有 ``ResolvedProvider``
    可直接传 ``provider`` 跳过查表(测试连接场景)。
    """
    if provider is None:
        if db is None:
            raise LLMError("chat() 需要传入 db 会话或显式 ResolvedProvider")
        provider = resolve_provider(db, tenant_id)

    if provider.is_mock:
        return _mock_chat(messages, response_json=response_json)
    return _litellm_chat(
        messages,
        provider=provider,
        temperature=temperature,
        response_json=response_json,
    )


def _litellm_chat(
    messages: list[Message],
    *,
    provider: ResolvedProvider,
    temperature: float,
    response_json: bool,
) -> str:
    try:
        import litellm  # 延迟 import,M0 mock 场景免引导加载
    except ImportError as e:
        raise LLMError("litellm 未安装,请检查 backend pyproject.toml 依赖") from e

    if not provider.api_key:
        raise LLMError(
            "未配置 LLM API key。请到「系统 → LLM 配置」添加并激活一个 Provider, "
            "或留空 LLM_API_KEY 走 mock 模式 demo。"
        )

    # model 直接传 LiteLLM 标识符,前缀路由由 LiteLLM 自己处理
    # (如 openai/gpt-4o-mini、deepseek/deepseek-chat、azure/<deployment>)
    kwargs: dict[str, object] = {
        "model": provider.model,
        "messages": messages,
        "temperature": temperature,
        "api_key": provider.api_key,
    }
    if provider.base_url:
        kwargs["api_base"] = provider.base_url
    if response_json:
        kwargs["response_format"] = {"type": "json_object"}

    try:
        resp = litellm.completion(**kwargs)
    except Exception as e:  # noqa: BLE001 — litellm 抛多种异常,统一封装
        logger.exception("LLM 调用失败 model=%s", provider.model)
        raise LLMError(f"LLM 调用失败: {e}") from e
    return _extract_text(resp)


def _extract_text(resp: object) -> str:
    """从 LiteLLM/OpenAI 响应中取出纯文本,容忍多种返回结构。"""
    try:
        return resp.choices[0].message.content  # type: ignore[attr-defined,no-any-return]
    except Exception as e:  # noqa: BLE001
        raise LLMError(f"无法解析 LLM 响应: {e}") from e


def parse_json(text: str) -> dict:
    """宽松地从模型回复中提取 JSON 对象。"""
    cleaned = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
    if fenced:
        cleaned = fenced.group(1)
    if not cleaned.startswith("{"):
        i = cleaned.find("{")
        j = cleaned.rfind("}")
        if i >= 0 and j > i:
            cleaned = cleaned[i : j + 1]
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise LLMError(f"模型未返回合法 JSON: {e}\n原文: {text[:300]}") from e


# ---------------------------------------------------------------------------
# Mock provider:让 demo 无 API key 也能跑完整链路
# ---------------------------------------------------------------------------

def _mock_chat(messages: list[Message], *, response_json: bool) -> str:
    """根据 messages 里的指令(尤其是 system prompt)做模式匹配,返回合理回复。

    设计目标:
    - 不引入网络
    - 不试图"假装聪明":硬编码题面 + 基于答案长度/关键词的简单评分
    - 评分有微小随机性,让连续两次面试报告看起来不像复制
    """
    last = messages[-1]["content"] if messages else ""
    sys_prompt = next((m["content"] for m in messages if m["role"] == "system"), "")

    if response_json:
        # 匹配评估场景:必须早于"评分"分支判断,因为 prompt 里
        # 有"匹配度评分"会被下面"评分"分支误中
        if "匹配评估" in sys_prompt or "匹配度评分" in sys_prompt:
            return _mock_match_json(last)
        # 评分场景(面试评分链)
        if "评分" in sys_prompt or "score" in sys_prompt.lower():
            return _mock_score_json(last)
        # 总结场景
        if "总结" in sys_prompt or "summary" in sys_prompt.lower():
            return _mock_summary_json(last)
        # 出题场景:返回 {questions: [...]} 包装
        if "出" in sys_prompt and ("题" in sys_prompt or "questions" in sys_prompt.lower()):
            return _mock_questions_json(sys_prompt)
        return '{"text": "mock"}'

    # 出题场景:返回一个合理的开放问题
    if "面试" in sys_prompt or "interviewer" in sys_prompt.lower():
        return _mock_question(last)
    return "mock response"


def _mock_question(context: str) -> str:
    """根据上下文中"第 X 题"等线索决定返回哪类题目。"""
    n = 1
    m = re.search(r"第\s*(\d+)\s*题", context)
    if m:
        n = int(m.group(1))
    bank = [
        "你好,先简单做个自我介绍 — 最近一段工作经历做了什么、遇到的最大挑战是什么?",
        "我看到你简历里提到的技能,能挑一个你最熟的,讲讲你用它解决过的一个具体问题?",
        "假设线上服务突然出现性能下降,你会从哪些方面入手排查?请按你的优先级讲讲。",
        "讲一个你与团队意见不一致、但你坚持自己方案的案例。后来结果如何?",
        "如果加入我们,你期望在前 3 个月专注做什么?",
    ]
    return bank[(n - 1) % len(bank)]


def _mock_questions_json(sys_prompt: str) -> str:
    """出题场景 mock:返回 {"questions": [...]} 结构。

    试图从 sys_prompt 里抓"产出 N 道"的 N,默认 5 道。
    每题用一个固定模板,answer_points 给 3-4 条,follow_up 给追问。
    """
    n = 5
    m = re.search(r"产出\s*\*?\*?(\d+)\s*\*?\*?\s*道", sys_prompt)
    if m:
        n = int(m.group(1))
    bank: list[dict] = [
        {
            "question": "请做一个 3 分钟自我介绍, 重点讲你最擅长的技术方向。",
            "answer_points": ["背景一句话", "近 1-2 年项目", "擅长技术栈", "未来方向"],
            "dimensions": ["软技能"],
            "difficulty": "初级",
            "follow_up": "你这次想找什么样的岗位?",
        },
        {
            "question": "讲讲你最近做过的最有挑战的项目, 你的角色和最大的难点。",
            "answer_points": ["业务背景", "技术挑战", "你的具体贡献", "最终结果"],
            "dimensions": ["项目复盘"],
            "difficulty": "中级",
            "follow_up": "如果让你重做, 你会改什么?",
        },
        {
            "question": "在简历里提到的某项技术, 讲讲它的核心原理(选最熟的一个)。",
            "answer_points": ["原理概览", "关键数据结构 / 算法", "适用场景", "局限"],
            "dimensions": ["技术深度"],
            "difficulty": "中级",
            "follow_up": "替代方案是什么?优劣对比?",
        },
        {
            "question": "线上服务突然 P99 飙升 10 倍, 你会按什么顺序排查?",
            "answer_points": ["先看监控指标", "定位是流量还是依赖", "对照变更", "回滚 / 限流"],
            "dimensions": ["场景排查"],
            "difficulty": "高级",
            "follow_up": "如果监控也挂了, 你怎么办?",
        },
        {
            "question": "如果让你设计一个高并发的订单系统, 你会怎么拆分?",
            "answer_points": ["读写分离", "分库分表策略", "幂等性 / 一致性", "降级方案"],
            "dimensions": ["系统设计"],
            "difficulty": "高级",
            "follow_up": "并发瓶颈通常在哪?你怎么压测?",
        },
        {
            "question": "讲一次跟团队意见不合,你坚持自己方案的案例,以及后续结果。",
            "answer_points": ["背景", "你的立场", "对方立场", "如何收敛", "事后复盘"],
            "dimensions": ["软技能"],
            "difficulty": "中级",
            "follow_up": "如果再来一次, 你会怎么开局?",
        },
        {
            "question": "未来 1-2 年的成长目标是什么, 你希望我们能给你什么?",
            "answer_points": ["技术成长方向", "管理 / 个人贡献者偏好", "对公司的期望"],
            "dimensions": ["软技能"],
            "difficulty": "初级",
            "follow_up": "如果发现实际跟期待不一样,你会怎么处理?",
        },
    ]
    out = bank[: max(1, n)] if n <= len(bank) else bank + bank[: n - len(bank)]
    return json.dumps({"questions": out}, ensure_ascii=False)


def _mock_score_json(answer: str) -> str:
    """基于答案长度/技能关键词命中,生成 4 维度评分 + 证据片段。"""
    text_len = len(answer.strip())
    has_example = any(
        kw in answer for kw in ("例如", "比如", "我曾", "项目", "实际", "经历", "case")
    )
    has_struct = any(kw in answer for kw in ("第一", "首先", "其次", "另外", "一方面"))

    # 基础分按长度区间
    if text_len < 30:
        base = 50
    elif text_len < 100:
        base = 65
    elif text_len < 300:
        base = 78
    else:
        base = 85

    accuracy = min(95, base + (5 if has_example else 0))
    completeness = min(95, base + (8 if has_struct else -3))
    clarity = min(95, base + (4 if has_struct and has_example else 0))
    # 微随机让多份报告看起来不重复
    import hashlib

    seed = int(hashlib.md5(answer.encode()).hexdigest()[:6], 16) % 7
    accuracy += seed - 3
    completeness += (seed * 2) % 5 - 2

    # 证据片段:取答案中长度合适的一句
    sentences = re.split(r"[。!?\n.!?]", answer)
    sentences = [s.strip() for s in sentences if 12 <= len(s.strip()) <= 80]
    evidence = sentences[0] if sentences else (answer[:80] + ("…" if len(answer) > 80 else ""))

    payload = {
        "scores": {
            "accuracy": max(40, min(100, accuracy)),
            "completeness": max(40, min(100, completeness)),
            "clarity": max(40, min(100, clarity)),
        },
        "evidence": evidence,
    }
    return json.dumps(payload, ensure_ascii=False)


def _mock_match_json(context: str) -> str:
    """简历↔岗位匹配评估 mock。

    user message 里带有岗位 / 关键技能 / JD / 简历摘录,这里粗略字符串匹配:
    - 提取关键技能列表,看简历里命中几个 → 影响 score
    - 命中的进 strengths,缺失的进 gaps
    """
    # 提取关键技能(简单正则:从"关键技能: A、B、C"行抓)
    skills_m = re.search(r"关键技能:\s*([^\n]+)", context)
    skills_raw = skills_m.group(1) if skills_m else ""
    skills = [s.strip() for s in re.split(r"[、,,]", skills_raw) if s.strip()]
    skills = [s for s in skills if s != "(未填)"]

    resume_part_m = re.search(r"简历摘录:\s*\n(.+)", context, re.DOTALL)
    resume_text = (resume_part_m.group(1) if resume_part_m else context).lower()

    if not skills:
        # 岗位没填技能,无从匹配
        payload = {
            "score": 60,
            "strengths": [],
            "gaps": ["岗位未填关键技能"],
            "comment": "岗位关键技能缺失,匹配度无法精确评估,建议先补全 JD。",
        }
        return json.dumps(payload, ensure_ascii=False)

    hits = [s for s in skills if s.lower() in resume_text]
    miss = [s for s in skills if s.lower() not in resume_text]
    overlap = len(hits) / len(skills)

    # 微随机让两次结果不完全一样(根据简历长度的 hash)
    import hashlib

    seed = int(hashlib.md5(context.encode()).hexdigest()[:6], 16) % 7
    score = int(round(40 + overlap * 50)) + seed - 3
    score = max(0, min(100, score))

    if score >= 80:
        comment = f"高度匹配,命中 {len(hits)}/{len(skills)} 项关键技能,建议优先安排面试。"
    elif score >= 65:
        comment = f"基本符合,关键技能命中 {len(hits)}/{len(skills)},可纳入候选池。"
    elif score >= 50:
        comment = f"部分匹配,命中 {len(hits)}/{len(skills)} 项,需结合 JD 重点核对短板。"
    else:
        comment = f"匹配度较低,关键技能仅命中 {len(hits)}/{len(skills)},建议跳过。"

    payload = {
        "score": score,
        "strengths": [f"具备 {s} 经验" for s in hits[:5]],
        "gaps": [f"未见 {s} 相关经验" for s in miss[:5]],
        "comment": comment,
    }
    return json.dumps(payload, ensure_ascii=False)


def _mock_summary_json(context: str) -> str:
    """根据上下文(里面应该带各轮分数),给一个总结判断。"""
    # 简单解析:从上下文里找"平均"或"总体"的数字
    avg_m = re.search(r"avg[=:]?\s*(\d+\.?\d*)", context)
    avg = float(avg_m.group(1)) if avg_m else 75.0
    if avg >= 80:
        rec = "推荐进入下一轮"
        comment = "整体回答完整, 思路清晰, 给出了具体例子。建议进入复试环节。"
    elif avg >= 65:
        rec = "保留"
        comment = "基础能力达标, 但深度与具体案例略显不足。建议补一轮面试再决策。"
    else:
        rec = "不推荐"
        comment = "答题较为简短或缺乏具体场景, 与岗位要求匹配度有限。"
    return json.dumps(
        {"recommendation": rec, "comment": comment}, ensure_ascii=False
    )
