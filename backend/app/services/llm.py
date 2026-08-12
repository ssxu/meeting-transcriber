"""LLM 服务模块 - 调用 LLM（OpenAI 兼容）生成会议/学习纪要、关键词和标签。

核心架构：Map-Reduce + Tree-Reduce
  1. 数据预处理：清洗口语垃圾词 + 按时间/说话人切块 + 滑动窗口重叠
  2. 内容场景识别与 Prompt 模板路由：区分会议沟通类/学习知识类，加载专属模板
  3. Map 阶段：根据内容场景动态并行提炼（结构化 JSON + 时间锚点）
  4. Reduce 阶段：类型化模板全局汇总与知识重构
  5. Tree-Reduce：极端超长音频时多级递归归纳
  6. 后处理：基于摘要调用一次 LLM 生成关键词+标签
"""
import json
import re
import logging
import asyncio
import httpx
from dataclasses import dataclass
from app.config import settings

logger = logging.getLogger("meeting-transcriber.llm")

# ===== 全局并发控制：保证 TPS=1 =====
_llm_semaphore: asyncio.Semaphore | None = None


def _get_llm_semaphore() -> asyncio.Semaphore:
    global _llm_semaphore
    if _llm_semaphore is None:
        _llm_semaphore = asyncio.Semaphore(3)
    return _llm_semaphore


@dataclass
class ModelConfig:
    base_url: str
    api_key: str
    model: str
    max_context_length: int = 32000
    temperature: float = 0.3

    @property
    def context_length(self) -> int:
        return self.max_context_length


def get_default_model_config() -> ModelConfig:
    return ModelConfig(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        max_context_length=settings.llm_max_context_length,
    )


# ===== Token 估算 =====

def _estimate_tokens(text: str) -> int:
    chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
    other_chars = len(text) - chinese_chars
    return int(chinese_chars * 1.5 + other_chars / 4)


def _compute_token_budget(mc: ModelConfig, system_prompt: str) -> int:
    system_tokens = _estimate_tokens(system_prompt)
    return mc.max_context_length - system_tokens - 4096


def _check_request_tokens(system_prompt: str, user_content: str, mc: ModelConfig):
    total = _estimate_tokens(system_prompt) + _estimate_tokens(user_content)
    if total > mc.max_context_length:
        raise ValueError(
            f"请求 token 数（约{total}）超出模型上下文长度（{mc.max_context_length}）。"
            f"请缩短输入或使用更大上下文的模型。"
        )


# ===== 内容场景模板库 =====

CONTENT_CATEGORIES = {
    "meeting": {"key": "meeting", "label": "会议沟通类", "description": "有明确参与者和互动讨论的沟通场景"},
    "learning": {"key": "learning", "label": "学习知识类", "description": "以知识传递为主的内容，单向输出为主"},
}

SUB_TYPES = {
    "regular": {"key": "regular", "category": "meeting", "label": "常规周会/例会", "map_focus": "议题、决议、待办事项", "reduce_output": "结构化会议纪要（会议速览、议题详情、决议列表、待办追踪表）"},
    "tech_review": {"key": "tech_review", "category": "meeting", "label": "技术评审/架构会", "map_focus": "技术方案、架构决策、风险点、替代方案", "reduce_output": "技术评审纪要（背景、方案对比、决策记录、风险追踪、待办）"},
    "product_req": {"key": "product_req", "category": "meeting", "label": "产品需求会", "map_focus": "需求背景、用户场景、功能点、优先级、依赖关系", "reduce_output": "PRD 纪要（需求概述、用户故事、功能清单、优先级、里程碑）"},
    "executive": {"key": "executive", "category": "meeting", "label": "高管决策会", "map_focus": "战略议题、决策依据、资源分配、风险判断", "reduce_output": "决策纪要（议题、决策、依据、执行要求）"},
    "product_intro": {"key": "product_intro", "category": "meeting", "label": "产品介绍会", "map_focus": "产品定位、核心功能、差异化、客户反馈", "reduce_output": "产品介绍纪要（产品概述、核心功能、亮点、市场反馈）"},
    "tech_exchange": {"key": "tech_exchange", "category": "meeting", "label": "技术交流会", "map_focus": "技术主题、实践分享、问题讨论、后续探索", "reduce_output": "技术交流纪要（主题概述、核心要点、讨论记录、后续方向）"},
    "research": {"key": "research", "category": "meeting", "label": "同行调研会", "map_focus": "调研目标、对方情况、对比分析、启示建议", "reduce_output": "调研纪要（调研背景、调研发现、对比分析、启示与建议）"},
    "course": {"key": "course", "category": "learning", "label": "课程/培训/讲座", "map_focus": "知识点、概念定义、案例、练习要点", "reduce_output": "学习笔记（知识框架、核心概念详解、案例、复习要点）"},
    "podcast": {"key": "podcast", "category": "learning", "label": "播客/访谈/演讲", "map_focus": "核心观点、论据案例、个人见解、金句", "reduce_output": "内容摘要（主题概述、核心观点、精彩片段、启示）"},
    "academic": {"key": "academic", "category": "learning", "label": "行业研讨/学术报告", "map_focus": "研究背景、方法、数据/证据、结论、局限性", "reduce_output": "学术摘要（背景、方法、关键发现、结论、讨论）"},
}


def get_sub_type_info(sub_type: str) -> dict | None:
    return SUB_TYPES.get(sub_type)


def get_category_by_sub_type(sub_type: str) -> str:
    info = SUB_TYPES.get(sub_type)
    return info["category"] if info else "meeting"


# ===== 场景识别 =====

SCENE_DETECTION_PROMPT = '请分析以下音频转写文本片段，判断其内容场景类型。\n\n可选类型：\n- meeting_regular: 常规周会/例会（有多人互动讨论）\n- meeting_tech_review: 技术评审/架构会\n- meeting_product_req: 产品需求会\n- meeting_executive: 高管决策会\n- meeting_product_intro: 产品介绍会\n- meeting_tech_exchange: 技术交流会\n- meeting_research: 同行调研会\n- learning_course: 课程/培训/讲座（单向知识传授）\n- learning_podcast: 播客/访谈/演讲\n- learning_academic: 行业研讨/学术报告\n\n判断依据：\n- 如果有多人互动讨论、决议、待办 → meeting 类\n- 如果是单向知识输出、授课、演讲 → learning 类\n- 再根据具体内容细分\n\n请严格按以下 JSON 格式返回（不要 markdown 代码块）：\n{\n  "sub_type": "meeting_regular",\n  "reason": "判断理由（一句话）"\n}\n\n===== 文本片段 =====\n'


async def detect_content_scene(chunks: list[dict], mc: ModelConfig, user_sub_type: str | None = None) -> tuple[str, str]:
    if user_sub_type and user_sub_type in SUB_TYPES:
        category = get_category_by_sub_type(user_sub_type)
        logger.info(f"场景识别: 用户指定 sub_type={user_sub_type}, category={category}")
        return category, user_sub_type
    sample_text = "\n".join(c["text"] for c in chunks[:2])[:4000]
    return "meeting", "regular"


# ===== 场景专属 Prompt 模板生成 =====

def _build_map_prompt_for_scene(category: str, sub_type: str) -> str:
    info = get_sub_type_info(sub_type) or SUB_TYPES["regular"]
    map_focus = info["map_focus"]
    if category == "learning":
        return '你是一个知识内容分析助手。请分析以下音频转写文本片段，重点提取知识性内容。\n\n【片段时间范围】{time_range}\n\n请侧重提取以下要素：' + map_focus + '\n\n请严格按以下 JSON 格式输出（不要 markdown 代码块）：\n{\n  "topics": ["本段涉及的核心概念/知识点/议题"],\n  "decisions": ["本段明确的结论或共识（如有）"],\n  "action_items": ["本段提及的后续行动或练习任务（如有）"],\n  "summary": "本段的简要概述，2-3句话"\n}\n\n要求：\n- 使用中文\n- 重点提炼"输入了什么知识"\n- 只提取文本中明确存在的信息，不要编造\n- 如果某类要素在本段不存在，返回空数组\n\n===== 音频转写文本片段 =====\n'
    else:
        return '你是一个会议分析助手。请分析以下会议逐字稿片段，提取核心要素。\n\n【片段时间范围】{time_range}\n\n请侧重提取以下要素：' + map_focus + '\n\n请严格按以下 JSON 格式输出（不要 markdown 代码块）：\n{\n  "topics": ["本段讨论的主要议题及其要点"],\n  "decisions": ["本段明确达成的决议事项"],\n  "action_items": ["明确的待办任务，需包含执行人、任务内容、截止时间（如有）"],\n  "summary": "本段的简要概述，2-3句话"\n}\n\n要求：\n- 使用中文\n- 只提取逐字稿中明确存在的信息，不要编造\n- 如果某类要素在本段不存在，返回空数组\n- action_items 尽量包含具体执行人\n\n===== 会议逐字稿片段 =====\n'


def _build_reduce_prompt_for_scene(category: str, sub_type: str) -> str:
    info = get_sub_type_info(sub_type) or SUB_TYPES["regular"]
    reduce_output = info["reduce_output"]
    if category == "learning":
        return '你是一个专业的知识内容总结助手。以下是按时间顺序分片提取的结构化摘要清单。\n\n请将这些片段摘要整合为一份完整的知识总结：\n1. 知识体系重构：按逻辑递进关系重新组织\n2. 去重合并：消除重复内容\n3. 格式化输出：生成结构清晰的 Markdown 文档\n\n输出目标：' + reduce_output + '\n\n返回格式要求（严格 JSON，不要 markdown 代码块）：\n{\n  "summary": "完整的知识总结 Markdown 文本",\n  "action_items": ["后续学习建议或练习任务（如有）"]\n}\n\n要求：\n- 使用中文，条理清晰\n- 合并重复内容，保持逻辑连贯\n- 不要编造内容\n\n===== 按时间顺序排列的各片段结构化摘要 =====\n'
    else:
        return '你是一个专业的会议纪要生成助手。以下是一个长会议按时间顺序分片提取的结构化摘要清单。\n\n请将这些片段摘要整合为一份完整的会议纪要：\n1. 议题合并：把跨多个时间段重复讨论的同一议题合并归类\n2. 待办智能覆盖：消除重复待办，后文修改以前文为准时以靠后为准\n3. 格式化输出：按模板生成 Markdown 文档\n\n输出目标：' + reduce_output + '\n\n输出模板：\n## 会议速览\n（一段话概括会议主题和主要结论）\n\n## 议题详情\n### 议题一：xxx\n- 讨论要点...\n\n## 决议列表\n1. 决议一\n\n## 待办追踪表\n| 任务 | 负责人 | 截止时间 | 备注 |\n|------|--------|----------|------|\n| xxx  | xxx    | xxx      | xxx  |\n\n要求：\n- 使用中文，条理清晰\n- 合并重复内容，保持逻辑连贯\n- 不要编造内容\n- 待办追踪表如果没有信息则留空单元格\n\n返回格式要求（严格 JSON，不要 markdown 代码块）：\n{\n  "summary": "完整的会议纪要 Markdown 文本（按上述模板）",\n  "action_items": ["待办事项1（含负责人和截止时间）", "待办事项2"]\n}\n\n===== 按时间顺序排列的各片段结构化摘要 =====\n'


def _build_single_extract_prompt_for_scene(category: str, sub_type: str) -> str:
    info = get_sub_type_info(sub_type) or SUB_TYPES["regular"]
    reduce_output = info["reduce_output"]
    if category == "learning":
        return '你是一个知识内容分析助手。请从以下音频转写文本中提取信息，以 JSON 格式返回。\n\n输出目标：' + reduce_output + '\n\n返回格式要求（严格 JSON，不要 markdown 代码块）：\n{\n  "summary": "结构化的知识总结 Markdown 文本",\n  "action_items": ["后续学习建议或练习任务（如有）"]\n}\n\n要求：\n- 使用中文\n- 不要编造文本中不存在的内容\n\n===== 音频转写文本 =====\n'
    else:
        return '你是一个会议分析助手。请从以下会议逐字稿中提取信息，以 JSON 格式返回。\n\n输出目标：' + reduce_output + '\n\n返回格式要求（严格 JSON，不要 markdown 代码块）：\n{\n  "summary": "结构化的会议纪要 Markdown 文本，包含：会议速览、议题详情、决议列表、待办追踪表",\n  "action_items": ["待办事项1（含负责人和截止时间）", "待办事项2"]\n}\n\n要求：\n- 使用中文\n- 不要编造逐字稿中不存在的内容\n\n===== 会议逐字稿 =====\n'


# ===== 切块 =====

def _compute_chunk_target(mc: ModelConfig, system_prompt: str) -> int:
    """根据模型上下文长度动态计算 chunk 目标字符数。"""
    budget = _compute_token_budget(mc, system_prompt)
    # budget 是 token 数，中文 1 token ≈ 0.67 字符，留 40% 余量给 system prompt 和输出
    target_tokens = int(budget * 0.4)
    target_chars = int(target_tokens / 1.5)  # 中文字符到 token 的粗略转换
    return max(2000, min(target_chars, 6000))


def _chunk_transcript(transcript: str, segments: list[dict] | None = None, mc: ModelConfig | None = None, system_prompt: str = "") -> list[dict]:
    target = _compute_chunk_target(mc or get_default_model_config(), system_prompt) if mc else 6000
    if segments and len(segments) > 0:
        return _chunk_by_segments(segments, target)
    return _chunk_by_chars(transcript, target)


def _chunk_by_segments(segments: list[dict], target: int = 6000) -> list[dict]:
    if not segments:
        return []
    TARGET = target
    OVERLAP = max(300, target // 10)
    chunks = []
    lines = []
    chars = 0
    start_time = segments[0].get("start", "")
    for i, seg in enumerate(segments):
        t = seg.get("text", "").strip()
        if not t:
            continue
        sp = seg.get("speaker", "")
        line = f"[{seg.get('start', '')} -> {seg.get('end', '')}] {sp}: {t}" if sp else f"[{seg.get('start', '')} -> {seg.get('end', '')}] {t}"
        lines.append(line)
        chars += len(line)
        if chars >= TARGET:
            chunks.append({"text": "\n".join(lines), "time_start": start_time, "time_end": segments[i].get("end", ""), "chunk_idx": len(chunks)})
            ol = []
            oc = 0
            for j in range(len(lines) - 1, -1, -1):
                if oc + len(lines[j]) > OVERLAP:
                    break
                ol.insert(0, lines[j])
                oc += len(lines[j])
            lines = ol
            chars = oc
            start_time = segments[i].get("end", "") if ol else (segments[i + 1].get("start", "") if i + 1 < len(segments) else "")
    if lines:
        chunks.append({"text": "\n".join(lines), "time_start": start_time, "time_end": segments[-1].get("end", ""), "chunk_idx": len(chunks)})
    return chunks


def _chunk_by_chars(transcript: str, target: int = 6000) -> list[dict]:
    TARGET = target
    OVERLAP = max(300, target // 10)
    chunks = []
    start = 0
    while start < len(transcript):
        end = min(start + TARGET, len(transcript))
        chunks.append({"text": transcript[start:end], "time_start": "", "time_end": "", "chunk_idx": len(chunks)})
        if end >= len(transcript):
            break
        start = end - OVERLAP
    return chunks


# ===== LLM 调用 =====

async def _call_llm(mc: ModelConfig, system_prompt: str, user_content: str, temperature: float = 0.3, json_mode: bool = False) -> str:
    _check_request_tokens(system_prompt, user_content, mc)
    est_tk = _estimate_tokens(system_prompt) + _estimate_tokens(user_content)
    logger.info(f"LLM 调用: model={mc.model}, est_tokens={est_tk}, content_len={len(user_content)}, json_mode={json_mode}")
    sem = _get_llm_semaphore()
    async with sem:
        try:
            return await _call_llm_inner(mc, system_prompt, user_content, temperature, json_mode)
        except httpx.TimeoutException as e:
            logger.error(f"LLM 超时: model={mc.model}, timeout={settings.llm_timeout}s, content_len={len(user_content)}, error={e}")
            raise RuntimeError(f"LLM 请求超时 ({settings.llm_timeout}s)，内容长度 {len(user_content)} 字符") from e
        except httpx.HTTPStatusError as e:
            raw = e.response.text[:500] if e.response.text else "(empty)"
            logger.error(f"LLM HTTP {e.response.status_code}: {raw}")
            raise RuntimeError(f"LLM 返回 HTTP {e.response.status_code}: {raw[:200]}") from e
        except Exception as e:
            logger.error(f"LLM 调用失败: model={mc.model}, type={type(e).__name__}, error={e}", exc_info=True)
            raise


async def _call_llm_inner(mc: ModelConfig, system_prompt: str, user_content: str, temperature: float = 0.3, json_mode: bool = False) -> str:
    headers = {"Content-Type": "application/json"}
    if mc.api_key:
        headers["Authorization"] = f"Bearer {mc.api_key}"
    messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_content}]
    payload = {"model": mc.model, "messages": messages, "temperature": temperature, "stream": False}
    # 显式设置 Ollama 上下文长度，避免使用 Ollama 默认的 4096
    payload["options"] = {"num_ctx": mc.max_context_length}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    timeout = httpx.Timeout(settings.llm_timeout, connect=30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        url = f"{mc.base_url}/chat/completions"
        logger.debug(f"LLM 请求: url={url}, model={mc.model}, json_mode={json_mode}")
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        try:
            data = resp.json()
        except Exception:
            raw = resp.text[:500] if resp.text else "(empty)"
            logger.error(f"LLM 响应非 JSON: status={resp.status_code}, body={raw}")
            raise RuntimeError(f"LLM 返回了非 JSON 响应 (HTTP {resp.status_code}): {raw[:200]}")
        content = data["choices"][0]["message"]["content"]
        if not content or not content.strip():
            logger.error(f"LLM 返回空内容: model={mc.model}, resp={data}")
            raise RuntimeError(f"LLM 返回了空内容 (model={mc.model})")
        # 清洗 markdown 代码块包裹（```json ... ```）
        stripped = content.strip()
        if stripped.startswith("```"):
            lines = stripped.split("\n")
            # 去掉首尾 ``` 行
            if lines[0].strip().startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            content = "\n".join(lines)
        logger.debug(f"LLM 响应: {content[:200]}...")
        return content


# ===== Map 阶段 =====

def _build_map_prompt(custom_prompt: str | None, map_prompt: str | None = None) -> str:
    prompt = map_prompt or _build_map_prompt_for_scene("meeting", "regular")
    if custom_prompt and custom_prompt.strip():
        prompt += f"\n\n【额外要求】请额外遵循以下自定义要求：\n{custom_prompt.strip()}\n"
    return prompt


def _format_map_results_for_reduce(map_results: list[dict]) -> str:
    lines = []
    for r in map_results:
        tr = f"[{r.get('time_start', '')} -> {r.get('time_end', '')}]" if r.get("time_start") else f"[片段 {r.get('chunk_idx', 0) + 1}]"
        lines.append(f"\n{'='*60}")
        lines.append(f"片段摘要 {tr}")
        lines.append(f"{'='*60}")
        if r.get("summary"):
            lines.append(f"\n摘要: {r['summary']}")
        if r.get("topics"):
            lines.append("\n议题:")
            for t in r["topics"]:
                lines.append(f"  - {t}")
        if r.get("decisions"):
            lines.append("\n决议:")
            for d in r["decisions"]:
                lines.append(f"  - {d}")
        if r.get("action_items"):
            lines.append("\n待办:")
            for a in r["action_items"]:
                lines.append(f"  - {a}")
    return "\n".join(lines)


async def _map_stage(mc: ModelConfig, chunks: list[dict], custom_prompt: str | None, map_prompt: str | None = None) -> list[dict]:
    built = _build_map_prompt(custom_prompt, map_prompt)
    budget = _compute_token_budget(mc, built)

    async def process(chunk: dict) -> dict:
        ct = chunk["text"]
        tr = f"{chunk.get('time_start', '')} ~ {chunk.get('time_end', '')}"
        p = built.replace("{time_range}", tr)
        tk = _estimate_tokens(ct)
        if tk > budget:
            logger.info(f"Chunk {chunk['chunk_idx']} 超限 (tk={tk} > budget={budget}), 二次切分")
            # 根据 budget 计算合适的 chunk 大小
            sub_target = max(1000, int(budget * 0.4 / 1.5))  # 留足余量
            subs = _chunk_by_chars(ct, sub_target)
            parts = []
            for sc in subs:
                st = sc["text"]
                if _estimate_tokens(st) > budget:
                    st = st[:int(budget * 2)]
                c = await _call_llm(mc, p, st, temperature=0.3, json_mode=True)
                try:
                    parts.append(json.loads(c))
                except json.JSONDecodeError:
                    parts.append({"summary": c, "topics": [], "decisions": [], "action_items": []})
            merged = {"topics": [], "decisions": [], "action_items": [], "summary": ""}
            for sr in parts:
                merged["topics"].extend(sr.get("topics", []))
                merged["decisions"].extend(sr.get("decisions", []))
                merged["action_items"].extend(sr.get("action_items", []))
                if sr.get("summary"):
                    merged["summary"] += sr["summary"] + " "
            merged["summary"] = merged["summary"].strip()
            merged["time_start"] = chunk.get("time_start", "")
            merged["time_end"] = chunk.get("time_end", "")
            merged["chunk_idx"] = chunk["chunk_idx"]
            return merged
        c = await _call_llm(mc, p, ct, temperature=0.3, json_mode=True)
        try:
            result = json.loads(c)
        except json.JSONDecodeError:
            result = {"summary": c, "topics": [], "decisions": [], "action_items": []}
        result["time_start"] = chunk.get("time_start", "")
        result["time_end"] = chunk.get("time_end", "")
        result["chunk_idx"] = chunk["chunk_idx"]
        return result

    tasks = [process(c) for c in chunks]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    out = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            logger.error(f"Map chunk {i} 失败: {r}")
            out.append({"summary": f"[失败: {r}]", "topics": [], "decisions": [], "action_items": [], "time_start": chunks[i].get("time_start", ""), "time_end": chunks[i].get("time_end", ""), "chunk_idx": i})
        else:
            out.append(r)
    return out


# ===== Reduce 阶段 =====

def _build_reduce_prompt(custom_prompt: str | None, reduce_prompt: str | None = None) -> str:
    prompt = reduce_prompt or _build_reduce_prompt_for_scene("meeting", "regular")
    if custom_prompt and custom_prompt.strip():
        prompt += f"\n\n【额外要求】请额外遵循以下自定义要求：\n{custom_prompt.strip()}\n"
    return prompt


TREE_GROUP_SIZE = 5


async def _tree_reduce(mc: ModelConfig, items: list[dict], budget: int, prompt: str) -> dict:
    level = 0
    cur = items
    while len(cur) > 1:
        level += 1
        groups = [cur[i:i + TREE_GROUP_SIZE] for i in range(0, len(cur), TREE_GROUP_SIZE)]
        logger.info(f"Tree-Reduce L{level}: {len(cur)} -> {len(groups)} 组")
        nxt = []
        for gi, g in enumerate(groups):
            if len(g) == 1:
                nxt.append(g[0])
                continue
            gi_text = _format_map_results_for_reduce(g)
            if _estimate_tokens(gi_text) > budget:
                gi_text = gi_text[:int(budget * 2)]
            try:
                c = await _call_llm(mc, prompt, gi_text, temperature=0.3, json_mode=True)
                try:
                    r = json.loads(c)
                except json.JSONDecodeError:
                    r = {"summary": c, "action_items": []}
            except Exception as e:
                logger.error(f"Tree-Reduce L{level} 组 {gi} 失败: {type(e).__name__}: {e}")
                # 合并各子项的 summary 作为 fallback
                merged_summary = "\n\n".join(item.get("summary", "") for item in g if item.get("summary"))
                merged_ai = []
                for item in g:
                    merged_ai.extend(item.get("action_items", []))
                r = {"summary": merged_summary or f"[L{level}组{gi}合并失败]", "action_items": merged_ai}
            r["time_start"] = g[0].get("time_start", "")
            r["time_end"] = g[-1].get("time_end", "")
            r["chunk_idx"] = gi
            nxt.append(r)
        cur = nxt
    return cur[0] if cur else {"summary": "", "action_items": []}


async def _tree_reduce_with_fallback(mc: ModelConfig, map_results: list[dict], budget: int, prompt: str) -> dict:
    """Tree-Reduce + action_items fallback：如果最终结果缺少 action_items，从 map_results 收集去重。"""
    result = await _tree_reduce(mc, map_results, budget, prompt)
    if not result.get("action_items"):
        all_ai = []
        for r in map_results:
            all_ai.extend(r.get("action_items", []))
        seen = set()
        result["action_items"] = [ai for ai in all_ai if not (ai in seen or seen.add(ai))]
        logger.info(f"Tree-Reduce action_items fallback: 从 map_results 收集 {len(result['action_items'])} 条")
    return result


async def _reduce_stage(mc: ModelConfig, map_results: list[dict], custom_prompt: str | None, reduce_prompt: str | None = None) -> dict:
    built = _build_reduce_prompt(custom_prompt, reduce_prompt)
    inp = _format_map_results_for_reduce(map_results)
    budget = _compute_token_budget(mc, built)
    tk = _estimate_tokens(inp)
    if tk <= budget:
        logger.info(f"Reduce: token={tk} <= {budget}, 直接汇总")
        try:
            c = await _call_llm(mc, built, inp, temperature=0.3, json_mode=True)
            try:
                result = json.loads(c)
            except json.JSONDecodeError:
                result = {"summary": c, "action_items": []}
        except Exception as e:
            logger.error(f"Reduce 直接汇总失败: {type(e).__name__}: {e}, 降级为 Tree-Reduce")
            return await _tree_reduce_with_fallback(mc, map_results, budget, built)
        if not result.get("action_items"):
            all_ai = []
            for r in map_results:
                all_ai.extend(r.get("action_items", []))
            seen = set()
            result["action_items"] = [ai for ai in all_ai if not (ai in seen or seen.add(ai))]
        return result
    logger.info(f"Reduce: token={tk} > {budget}, Tree-Reduce")
    return await _tree_reduce_with_fallback(mc, map_results, budget, built)


# ===== 主入口 =====

async def summarize_with_prompt(
    transcript: str,
    custom_prompt: str | None = None,
    model_config: ModelConfig | None = None,
    segments: list[dict] | None = None,
    prompts: dict[str, str | None] | None = None,
    sub_type: str | None = None,
) -> dict:
    """Map-Reduce 生成纪要。返回 {"summary": str, "action_items": [str], "category": str, "sub_type": str}.
    
    prompts: 来自 MeetingType 的 {map, reduce, single_extract} 模板，值为 None 时回退到代码中的场景默认模板。
    custom_prompt: 额外要求，追加到 map/reduce/single_extract 模板末尾。
    """
    mc = model_config or get_default_model_config()
    # 先确定场景和模板，再根据模型上下文长度动态切块
    category_pre = "meeting"
    resolved_pre = sub_type if sub_type in SUB_TYPES else "regular"
    if resolved_pre != "regular" or sub_type not in SUB_TYPES:
        # 无用户指定 sub_type 时，先用默认值切块，后续 detect_content_scene 再修正
        pass
    map_prompt_pre = (prompts and prompts.get("map")) or _build_map_prompt_for_scene(category_pre, resolved_pre)
    chunks = _chunk_transcript(transcript, segments, mc, map_prompt_pre)
    logger.info(f"切块完成: {len(chunks)} 块")
    category, resolved = await detect_content_scene(chunks, mc, sub_type)
    # 优先用传入的 MeetingType 模板，回退到代码场景模板
    map_prompt = (prompts and prompts.get("map")) or _build_map_prompt_for_scene(category, resolved)
    reduce_prompt = (prompts and prompts.get("reduce")) or _build_reduce_prompt_for_scene(category, resolved)
    single_prompt = (prompts and prompts.get("single_extract")) or _build_single_extract_prompt_for_scene(category, resolved)
    # 追加额外要求
    if custom_prompt and custom_prompt.strip():
        extra = f"\n\n【额外要求】\n{custom_prompt.strip()}\n"
        map_prompt += extra
        reduce_prompt += extra
        single_prompt += extra
    single_budget = _compute_token_budget(mc, single_prompt)
    est = _estimate_tokens(transcript)
    logger.info(f"Map-Reduce: model={mc.model}, tokens={est}, cat={category}, sub={resolved}")
    if est <= single_budget:
        try:
            c = await _call_llm(mc, single_prompt, transcript, temperature=0.3, json_mode=True)
            try:
                result = json.loads(c)
            except json.JSONDecodeError:
                result = {"summary": c, "action_items": []}
        except Exception as e:
            logger.error(f"单次提取失败: {type(e).__name__}: {e}, 降级为 Map-Reduce")
            # 降级到 Map-Reduce
            map_results = await _map_stage(mc, chunks, None, map_prompt)
            logger.info(f"Map 完成 (降级): {len(map_results)} 块")
            result = await _reduce_stage(mc, map_results, None, reduce_prompt)
            result["category"] = category
            result["sub_type"] = resolved
            logger.info("Reduce 完成 (降级)")
            return result
        result["category"] = category
        result["sub_type"] = resolved
        logger.info("单次提取完成")
        return result
    map_results = await _map_stage(mc, chunks, None, map_prompt)
    logger.info(f"Map 完成: {len(map_results)} 块")
    result = await _reduce_stage(mc, map_results, None, reduce_prompt)
    result["category"] = category
    result["sub_type"] = resolved
    logger.info("Reduce 完成")
    return result


# ===== 关键词+标签（一次调用）=====

KEYWORDS_AND_TAGS_PROMPT = '你是一个内容分析助手。请从以下纪要中提取关键词和标签。\n\n返回格式要求（严格 JSON，不要 markdown 代码块）：\n{\n  "keywords": ["关键词1", "关键词2", "关键词3"],\n  "tags": ["标签1", "标签2"]\n}\n\n要求：\n- keywords: 3-8个核心关键词，每个2-8字\n- tags: 2-5个分类标签（如：技术、产品、管理、培训等）\n- 使用中文\n\n===== 纪要内容 =====\n'


async def generate_keywords_and_tags(summary: str, model_config: ModelConfig | None = None) -> dict:
    """基于摘要一次调用同时生成关键词和标签。返回 {"keywords": [str], "tags": [str]}."""
    mc = model_config or get_default_model_config()
    content = await _call_llm(mc, KEYWORDS_AND_TAGS_PROMPT, summary, temperature=0.3, json_mode=True)
    try:
        result = json.loads(content)
        keywords = result.get("keywords", [])
        tags = result.get("tags", [])
    except json.JSONDecodeError:
        keywords = []
        tags = []
    logger.info(f"关键词+标签完成: kw={keywords}, tags={tags}")
    return {"keywords": keywords, "tags": tags}


# ===== 待办事项 =====

ACTION_ITEMS_PROMPT = '你是一个待办事项提取助手。请从以下纪要中提取所有待办事项。\n\n返回格式要求（严格 JSON，不要 markdown 代码块）：\n{\n  "action_items": ["待办事项1（含负责人和截止时间）", "待办事项2"]\n}\n\n要求：\n- 包含具体行动内容和负责人（如有）\n- 使用中文\n- 不要编造不存在的内容\n- 如果没有待办事项，返回空数组\n\n===== 纪要内容 =====\n'


async def extract_action_items_from_summary(summary: str, model_config: ModelConfig | None = None) -> list[str]:
    mc = model_config or get_default_model_config()
    content = await _call_llm(mc, ACTION_ITEMS_PROMPT, summary, temperature=0.3, json_mode=True)
    try:
        result = json.loads(content)
        items = result.get("action_items", [])
    except json.JSONDecodeError:
        items = []
    logger.info(f"待办事项完成: {len(items)} 条")
    return items


# ===== 思维导图 =====

MINDMAP_PROMPT = '你是一个思维导图生成助手。请根据以下纪要内容，生成 Markdown 格式的思维导图。\n\n使用标题层级表示节点关系：\n# 中心主题\n## 一级分支\n### 二级分支\n- 叶子节点\n\n要求：\n- 使用中文\n- 层级不超过4级\n- 提取核心内容\n\n===== 纪要内容 =====\n'


async def generate_mindmap(summary: str, model_config: ModelConfig | None = None) -> str:
    mc = model_config or get_default_model_config()
    content = await _call_llm(mc, MINDMAP_PROMPT, summary, temperature=0.3, json_mode=False)
    logger.info("思维导图完成")
    return content
