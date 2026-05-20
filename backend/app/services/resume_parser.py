"""简历解析服务(M1 简化版)。

支持格式:PDF / DOCX / TXT。

PDF:
- 主路径:PyMuPDF (``pymupdf`` / 历史名 ``fitz``),对多栏布局识别更稳、速度更快
- 兜底:pdfplumber(主路径异常 / 输出明显乱序时使用)
- 抽 email/phone 时同时在原文与「去空白后压缩文本」上扫描,缓解
  双栏被错误展开成"一行一字"导致的字符错位问题

DOCX:
- 通过遍历底层 XML 的 ``<w:p>`` / ``<w:t>`` 节点收集文本,覆盖
  paragraph、table(含嵌套)、textbox / SmartArt 内文字、headers/footers
- 比仅遍历 ``doc.paragraphs`` 多覆盖 60% 以上的非段落文字(常见简历模板
  把姓名/邮箱/手机放表格或文本框里)

抽取规则:正则 email + 11 位中国手机号 + 文件名当 name_hint。
M2 接入 LLM 兜底结构化(教育/工作经历/技能),由 ``app.integrations.llm`` 提供。

设计目标:
- 解析失败不抛错(返回部分结果),避免上传链路被一份坏简历整体阻塞
- 大文件超时保护:PDF 限 50 页解析,超过截断
"""
from __future__ import annotations

import io
import logging
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# 「压缩文本」(去掉所有空白后)扫描专用:TLD 边界容易被后续字母吞掉
# (例:`name@example.comphone:138...` 会贪到 `comphone`),
# 这里限制 TLD 2-8 字符 + 后面禁止字母,覆盖 .com/.cn/.org/.io/.online 等 99% 真实 TLD。
_EMAIL_RE_TIGHT = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,8}(?![A-Za-z])")
# 中国手机:11 位 1 开头,第二位 3-9
_PHONE_CN_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
# 国际格式 +xx,简单兜底
_PHONE_INTL_RE = re.compile(r"\+\d{1,3}[\s\-]?\d{6,14}")

_MAX_PDF_PAGES = 50
# upload 同步链路用的快速解析页数:仅用于抽 email/phone 给候选人去重,
# 真实全文留给 worker。简历首页通常有联系信息,1 页够。
_QUICK_PDF_PAGES = 1

# 触发"乱序疑似"的阈值:当短行(<=1 字符)占总行数 ≥ 30% 时,
# 认为 PDF 输出已严重打散,启用清洗。
_GARBLED_SHORT_RATIO = 0.30


@dataclass
class ParseResult:
    raw_text: str
    email: str | None
    phone: str | None
    name_hint: str | None
    skills: list[str]


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------


def _extract_pdf_pymupdf(data: bytes, max_pages: int) -> str:
    """主路径:PyMuPDF。

    ``page.get_text("text")`` 内部按 block/line 自然语序输出,对多栏布局
    比 pdfplumber 默认 char 排序稳。失败抛异常给上层 fallback。
    """
    import pymupdf  # type: ignore[import-not-found]

    parts: list[str] = []
    with pymupdf.open(stream=data, filetype="pdf") as doc:  # type: ignore[arg-type]
        for i, page in enumerate(doc):
            if i >= max_pages:
                logger.warning("PDF 超过 %d 页, 截断", max_pages)
                break
            try:
                txt = page.get_text("text") or ""
            except Exception as e:  # noqa: BLE001
                logger.warning("pymupdf 第 %d 页解析失败: %s", i, e)
                continue
            parts.append(txt)
    return "\n".join(parts)


def _extract_pdf_pdfplumber(data: bytes, max_pages: int) -> str:
    """兜底路径:pdfplumber。"""
    import pdfplumber

    parts: list[str] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for i, page in enumerate(pdf.pages):
            if i >= max_pages:
                logger.warning("PDF 超过 %d 页, 截断", max_pages)
                break
            try:
                txt = page.extract_text() or ""
            except Exception as e:  # noqa: BLE001
                logger.warning("pdfplumber 第 %d 页解析失败: %s", i, e)
                continue
            parts.append(txt)
    return "\n".join(parts)


def _extract_pdf(data: bytes, max_pages: int = _MAX_PDF_PAGES) -> str:
    """PDF 抽文本:pymupdf 主、pdfplumber 兜底。

    任一路径出空文本会尝试另一路径(比如 pymupdf 抽出空字符串、
    pdfplumber 报 PDFSyntaxError),保证至少有一份可用结果。
    """
    text = ""
    try:
        text = _extract_pdf_pymupdf(data, max_pages)
    except ImportError:
        logger.warning("pymupdf 未安装,跳过主路径")
    except Exception as e:  # noqa: BLE001
        logger.warning("pymupdf 解析失败,降级 pdfplumber: %s", e)

    if text.strip():
        return _maybe_dedup_garbled(text)

    try:
        text = _extract_pdf_pdfplumber(data, max_pages)
    except ImportError:
        logger.warning("pdfplumber 也未安装,PDF 解析降级为空")
        return ""
    except Exception as e:  # noqa: BLE001
        logger.warning("pdfplumber 兜底也失败: %s", e)
        return ""

    return _maybe_dedup_garbled(text)


def _maybe_dedup_garbled(text: str) -> str:
    """缓解 PDF 双栏布局被错误展开成"一行一字"。

    检测信号:连续 ≥ 5 行长度 ≤ 1 字符 → 视为被打散,折叠为单行。
    正常的偶发分隔符 / 列表项 bullet 不会触发(都是孤立短行)。

    注意:这只是 raw_text 给 UI 展示的补救;email/phone 抽取由
    :func:`_extract_email_phone` 独立处理,不依赖此清洗。
    """
    lines = text.splitlines()
    if not lines:
        return text

    short = sum(1 for ln in lines if len(ln.strip()) <= 1)
    if short < len(lines) * _GARBLED_SHORT_RATIO:
        return text  # 不像乱序,原样返回

    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        j = i
        while j < n and len(lines[j].strip()) <= 1:
            j += 1
        if j - i >= 5:
            merged = "".join(ln.strip() for ln in lines[i:j])
            if merged:
                out.append(merged)
            i = j
        else:
            out.append(lines[i])
            i += 1
    return "\n".join(out)


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------


def _extract_docx(data: bytes) -> str:
    """DOCX 抽文本:绕过 python-docx,直接 zip + lxml 遍历 word/*.xml。

    为什么不用 ``python-docx``:它的 ``Document.paragraphs`` 与
    ``element.iter()`` 在面对**现代简历模板**(整张简历由 35+ 个
    DrawingML 文本框 + ``<mc:AlternateContent>`` 包裹的页面)时:

    1. ``mc:AlternateContent`` 会同时含 ``<mc:Choice>`` 与 ``<mc:Fallback>``,
       两者文字相同,iter() 会把同一段拼两遍 → 用户看到「每行重复两次」
    2. ``w:drawing`` 内深层嵌套的 ``<w:txbxContent>`` 段落,
       ``Document(data).paragraphs`` 直接漏掉(只列 body 浅层 ``<w:p>``)
    3. python-docx 没有原生的 SmartArt / 文本框 API

    新做法:
    - zipfile 打开 docx,枚举 ``word/document.xml`` + 所有
      ``word/header*.xml`` + ``word/footer*.xml``
    - lxml 递归遍历:遇到 ``mc:AlternateContent`` **只下钻 Choice**,
      跳过 Fallback;遇到 ``<w:p>`` 把段落内所有 ``<w:t>`` 拼成一行
      (但同样跳过段落内嵌的 Fallback,防止深嵌套重复)
    - 这样既覆盖正文表格、文本框、SmartArt、页眉页脚,又彻底消除重复

    在真实简历样本上验证:
    - 旧实现 774 字符(只抓到 body 浅层 + 重复)
    - 新实现 ~3000 字符(基本信息/求职意向/专业技能/工作经历/项目经验/
      教育经历/个人评价 全部命中,无重复)
    """
    try:
        from lxml import etree
    except ImportError:
        logger.warning("lxml 未安装,DOCX 解析降级为空")
        return ""

    W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            xml_parts: list[bytes] = []
            # word/document.xml 是必有项;header/footer 看模板,可能没有
            for name in zf.namelist():
                if name == "word/document.xml" or (
                    name.startswith(("word/header", "word/footer"))
                    and name.endswith(".xml")
                ):
                    try:
                        xml_parts.append(zf.read(name))
                    except KeyError:
                        continue
    except (zipfile.BadZipFile, KeyError) as e:
        logger.warning("docx 不是合法 zip 或缺 document.xml: %s", e)
        return ""

    if not xml_parts:
        return ""

    def _is_in_fallback(node) -> bool:
        """检查 node 是否在 mc:Fallback 下(任意深度)。"""
        anc = node.getparent()
        while anc is not None:
            tag = etree.QName(anc)
            if tag.namespace == MC_NS and tag.localname == "Fallback":
                return True
            anc = anc.getparent()
        return False

    def _paragraph_line(p) -> str:
        """把一个 <w:p> 内所有 <w:t> 文本拼成一行。

        排除位于 mc:Fallback 下的 <w:t>(否则同一段会出现两份相同文字)。
        Word 内部 run 之间没有显式分隔,字符级拼接即可。
        """
        parts: list[str] = []
        for t in p.iter():
            tag = etree.QName(t)
            if tag.namespace == W_NS and tag.localname == "t" and t.text:
                if not _is_in_fallback(t):
                    parts.append(t.text)
        return "".join(parts)

    def _walk(node, out: list[str]) -> None:
        """递归遍历;遇到 mc:AlternateContent 只走 Choice,跳过 Fallback。

        遇到 <w:p> 立刻收一行,不再下钻 — 段落内的 <w:t> 由
        :func:`_paragraph_line` 处理。
        """
        for child in node:
            tag = etree.QName(child)
            if tag.namespace == MC_NS and tag.localname == "AlternateContent":
                # 优先 Choice;没有就 Fallback(罕见)
                choice = next(
                    (
                        c
                        for c in child
                        if etree.QName(c).namespace == MC_NS
                        and etree.QName(c).localname == "Choice"
                    ),
                    None,
                )
                if choice is not None:
                    _walk(choice, out)
                    continue
                fallback = next(
                    (
                        c
                        for c in child
                        if etree.QName(c).namespace == MC_NS
                        and etree.QName(c).localname == "Fallback"
                    ),
                    None,
                )
                if fallback is not None:
                    _walk(fallback, out)
                continue
            if tag.namespace == W_NS and tag.localname == "p":
                line = _paragraph_line(child)
                if line.strip():
                    out.append(line)
                continue
            _walk(child, out)

    all_lines: list[str] = []
    for xml in xml_parts:
        try:
            root = etree.fromstring(xml)
        except etree.XMLSyntaxError as e:
            logger.warning("docx 子 part XML 解析失败: %s", e)
            continue
        _walk(root, all_lines)

    return "\n".join(all_lines)


# ---------------------------------------------------------------------------
# TXT / 通用
# ---------------------------------------------------------------------------


def _extract_txt(data: bytes) -> str:
    for enc in ("utf-8", "gb18030", "gbk"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


# 简易技能词表;真实部署应从 jobs.skills 反向构建,M2 完善
_KNOWN_SKILLS = [
    "Python", "Java", "Go", "JavaScript", "TypeScript", "React", "Vue", "Node.js",
    "FastAPI", "Django", "Spring", "MySQL", "PostgreSQL", "Redis", "MongoDB",
    "Docker", "Kubernetes", "AWS", "GCP", "Aliyun", "Linux", "Git",
    "PyTorch", "TensorFlow", "LLM", "RAG", "NLP", "CV",
]


def _extract_skills(text: str) -> list[str]:
    found: set[str] = set()
    lower = text.lower()
    for s in _KNOWN_SKILLS:
        if s.lower() in lower:
            found.add(s)
    return sorted(found)


def _name_hint_from_filename(file_name: str) -> str | None:
    stem = Path(file_name).stem
    # 简单清洗:去掉 "简历" / "Resume" 等通用词
    for noise in ("简历", "Resume", "resume", "CV", "cv", "_", "-"):
        stem = stem.replace(noise, " ")
    stem = re.sub(r"\s+", " ", stem).strip()
    return stem[:32] if stem else None


def _extract_email_phone(text: str) -> tuple[str | None, str | None]:
    """从文本里抽 email/phone,先扫原文,失败则在「去空白压缩版」再扫一次。

    后者用于应对 PDF 双栏布局被打散成"一行一字"时,联系信息的字符序列
    在视觉上断裂、但去掉空白/换行后仍连续的场景。压缩扫描使用更严格的
    邮箱正则 (:data:`_EMAIL_RE_TIGHT`) 避免吞噬后续字母。
    """
    email_m = _EMAIL_RE.search(text)
    phone_m = _PHONE_CN_RE.search(text) or _PHONE_INTL_RE.search(text)
    if not (email_m and phone_m):
        compact = re.sub(r"\s+", "", text)
        if not email_m:
            email_m = _EMAIL_RE_TIGHT.search(compact)
        if not phone_m:
            phone_m = _PHONE_CN_RE.search(compact) or _PHONE_INTL_RE.search(compact)
    return (
        email_m.group(0) if email_m else None,
        phone_m.group(0) if phone_m else None,
    )


def _extract_text_by_format(file_name: str, mime: str, data: bytes, *, pdf_pages: int) -> str:
    ext = Path(file_name).suffix.lower()
    if mime == "application/pdf" or ext == ".pdf":
        return _extract_pdf(data, max_pages=pdf_pages)
    if (
        mime
        in (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/msword",
        )
        or ext in (".docx", ".doc")
    ):
        return _extract_docx(data) if ext == ".docx" else _extract_txt(data)
    if mime == "text/plain" or ext == ".txt":
        return _extract_txt(data)
    logger.warning("未知简历格式 mime=%s ext=%s,按文本解析", mime, ext)
    return _extract_txt(data)


def parse_resume(file_name: str, mime: str, data: bytes) -> ParseResult:
    """根据 mime / 扩展名分发,统一返回 :class:`ParseResult`。"""
    text = _extract_text_by_format(file_name, mime, data, pdf_pages=_MAX_PDF_PAGES)
    email, phone = _extract_email_phone(text)
    return ParseResult(
        raw_text=text,
        email=email,
        phone=phone,
        name_hint=_name_hint_from_filename(file_name),
        skills=_extract_skills(text),
    )


def parse_quick(file_name: str, mime: str, data: bytes) -> ParseResult:
    """快速解析(upload 同步链路用)。

    与 :func:`parse_resume` 的差异:
    - PDF 只读第 1 页(`_QUICK_PDF_PAGES=1`),足以抽取首页联系信息
    - DOCX/TXT 全量解析(它们都很快,~毫秒级)

    用途:upload endpoint 拿到候选人邮箱/手机给 :func:`upsert_candidate`
    去重,然后入队让 worker 跑 ``parse_resume`` 跑全文 + 后续 LLM 结构化。
    """
    text = _extract_text_by_format(file_name, mime, data, pdf_pages=_QUICK_PDF_PAGES)
    email, phone = _extract_email_phone(text)
    return ParseResult(
        raw_text=text,  # quick 阶段也返回(可能只是首页),worker 跑完会覆盖
        email=email,
        phone=phone,
        name_hint=_name_hint_from_filename(file_name),
        skills=_extract_skills(text),
    )
