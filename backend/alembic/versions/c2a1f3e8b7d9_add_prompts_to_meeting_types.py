"""add map/reduce/single_extract prompts to meeting_types, seed builtin types

Revision ID: c2a1f3e8b7d9
Revises: a8f3c2e1b4d6
Create Date: 2026-07-26 11:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c2a1f3e8b7d9'
down_revision = 'a8f3c2e1b4d6'
branch_labels = None
depends_on = None


# ===== 场景模板文本（与 llm.py 中 _build_*_prompt_for_scene 一致）=====

# --- Map prompts ---

MAP_MEETING = """你是一个会议分析助手。请分析以下会议逐字稿片段，提取核心要素。

【片段时间范围】{time_range}

请侧重提取以下要素：{map_focus}

请严格按以下 JSON 格式输出（不要 markdown 代码块）：
{{
  "topics": ["本段讨论的主要议题及其要点"],
  "decisions": ["本段明确达成的决议事项"],
  "action_items": ["明确的待办任务，需包含执行人、任务内容、截止时间（如有）"],
  "summary": "本段的简要概述，2-3句话"
}}

要求：
- 使用中文
- 只提取逐字稿中明确存在的信息，不要编造
- 如果某类要素在本段不存在，返回空数组
- action_items 尽量包含具体执行人

===== 会议逐字稿片段 =====
"""

MAP_LEARNING = """你是一个知识内容分析助手。请分析以下音频转写文本片段，重点提取知识性内容。

【片段时间范围】{time_range}

请侧重提取以下要素：{map_focus}

请严格按以下 JSON 格式输出（不要 markdown 代码块）：
{{
  "topics": ["本段涉及的核心概念/知识点/议题"],
  "decisions": ["本段明确的结论或共识（如有）"],
  "action_items": ["本段提及的后续行动或练习任务（如有）"],
  "summary": "本段的简要概述，2-3句话"
}}

要求：
- 使用中文
- 重点提炼"输入了什么知识"
- 只提取文本中明确存在的信息，不要编造
- 如果某类要素在本段不存在，返回空数组

===== 音频转写文本片段 =====
"""

# --- Reduce prompts ---

REDUCE_MEETING = """你是一个专业的会议纪要生成助手。以下是一个长会议按时间顺序分片提取的结构化摘要清单。

请将这些片段摘要整合为一份完整的会议纪要：
1. 议题合并：把跨多个时间段重复讨论的同一议题合并归类
2. 待办智能覆盖：消除重复待办，后文修改以前文为准时以靠后为准
3. 格式化输出：按模板生成 Markdown 文档

输出目标：{reduce_output}

输出模板：
## 会议速览
（一段话概括会议主题和主要结论）

## 议题详情
### 议题一：xxx
- 讨论要点...

## 决议列表
1. 决议一

## 待办追踪表
| 任务 | 负责人 | 截止时间 | 备注 |
|------|--------|----------|------|
| xxx  | xxx    | xxx      | xxx  |

要求：
- 使用中文，条理清晰
- 合并重复内容，保持逻辑连贯
- 不要编造内容
- 待办追踪表如果没有信息则留空单元格

返回格式要求（严格 JSON，不要 markdown 代码块）：
{{
  "summary": "完整的会议纪要 Markdown 文本（按上述模板）",
  "action_items": ["待办事项1（含负责人和截止时间）", "待办事项2"]
}}

===== 按时间顺序排列的各片段结构化摘要 =====
"""

REDUCE_LEARNING = """你是一个专业的知识内容总结助手。以下是按时间顺序分片提取的结构化摘要清单。

请将这些片段摘要整合为一份完整的知识总结：
1. 知识体系重构：按逻辑递进关系重新组织
2. 去重合并：消除重复内容
3. 格式化输出：生成结构清晰的 Markdown 文档

输出目标：{reduce_output}

返回格式要求（严格 JSON，不要 markdown 代码块）：
{{
  "summary": "完整的知识总结 Markdown 文本",
  "action_items": ["后续学习建议或练习任务（如有）"]
}}

要求：
- 使用中文，条理清晰
- 合并重复内容，保持逻辑连贯
- 不要编造内容

===== 按时间顺序排列的各片段结构化摘要 =====
"""

# --- Single extract prompts ---

SINGLE_MEETING = """你是一个会议分析助手。请从以下会议逐字稿中提取信息，以 JSON 格式返回。

输出目标：{reduce_output}

返回格式要求（严格 JSON，不要 markdown 代码块）：
{{
  "summary": "结构化的会议纪要 Markdown 文本，包含：会议速览、议题详情、决议列表、待办追踪表",
  "action_items": ["待办事项1（含负责人和截止时间）", "待办事项2"]
}}

要求：
- 使用中文
- 不要编造逐字稿中不存在的内容

===== 会议逐字稿 =====
"""

SINGLE_LEARNING = """你是一个知识内容分析助手。请从以下音频转写文本中提取信息，以 JSON 格式返回。

输出目标：{reduce_output}

返回格式要求（严格 JSON，不要 markdown 代码块）：
{{
  "summary": "结构化的知识总结 Markdown 文本",
  "action_items": ["后续学习建议或练习任务（如有）"]
}}

要求：
- 使用中文
- 不要编造文本中不存在的内容

===== 音频转写文本 =====
"""

# ===== 10 种内置场景类型 =====

BUILTIN_TYPES = [
    # meeting 类
    {"name": "常规周会/例会", "description": "团队日常沟通、进度同步、问题讨论", "category": "meeting", "sub_type": "regular",
     "map_focus": "议题、决议、待办事项", "reduce_output": "结构化会议纪要（会议速览、议题详情、决议列表、待办追踪表）"},
    {"name": "技术评审/架构会", "description": "技术方案评审、架构设计讨论", "category": "meeting", "sub_type": "tech_review",
     "map_focus": "技术方案、架构决策、风险点、替代方案", "reduce_output": "技术评审纪要（背景、方案对比、决策记录、风险追踪、待办）"},
    {"name": "产品需求会", "description": "需求讨论、用户故事梳理、功能规划", "category": "meeting", "sub_type": "product_req",
     "map_focus": "需求背景、用户场景、功能点、优先级、依赖关系", "reduce_output": "PRD 纪要（需求概述、用户故事、功能清单、优先级、里程碑）"},
    {"name": "高管决策会", "description": "战略议题讨论、资源分配决策", "category": "meeting", "sub_type": "executive",
     "map_focus": "战略议题、决策依据、资源分配、风险判断", "reduce_output": "决策纪要（议题、决策、依据、执行要求）"},
    {"name": "产品介绍会", "description": "产品发布、功能演示、客户介绍", "category": "meeting", "sub_type": "product_intro",
     "map_focus": "产品定位、核心功能、差异化、客户反馈", "reduce_output": "产品介绍纪要（产品概述、核心功能、亮点、市场反馈）"},
    {"name": "技术交流会", "description": "技术分享、实践交流、问题探讨", "category": "meeting", "sub_type": "tech_exchange",
     "map_focus": "技术主题、实践分享、问题讨论、后续探索", "reduce_output": "技术交流纪要（主题概述、核心要点、讨论记录、后续方向）"},
    {"name": "同行调研会", "description": "同行交流、调研访谈、经验学习", "category": "meeting", "sub_type": "research",
     "map_focus": "调研目标、对方情况、对比分析、启示建议", "reduce_output": "调研纪要（调研背景、调研发现、对比分析、启示与建议）"},
    # learning 类
    {"name": "课程/培训/讲座", "description": "知识授课、技能培训、专题讲座", "category": "learning", "sub_type": "course",
     "map_focus": "知识点、概念定义、案例、练习要点", "reduce_output": "学习笔记（知识框架、核心概念详解、案例、复习要点）"},
    {"name": "播客/访谈/演讲", "description": "播客节目、人物访谈、公开演讲", "category": "learning", "sub_type": "podcast",
     "map_focus": "核心观点、论据案例、个人见解、金句", "reduce_output": "内容摘要（主题概述、核心观点、精彩片段、启示）"},
    {"name": "行业研讨/学术报告", "description": "学术报告、行业研讨、研究分享", "category": "learning", "sub_type": "academic",
     "map_focus": "研究背景、方法、数据/证据、结论、局限性", "reduce_output": "学术摘要（背景、方法、关键发现、结论、讨论）"},
]


def _build_map_prompt(category: str, map_focus: str) -> str:
    template = MAP_LEARNING if category == "learning" else MAP_MEETING
    return template.replace("{map_focus}", map_focus)


def _build_reduce_prompt(category: str, reduce_output: str) -> str:
    template = REDUCE_LEARNING if category == "learning" else REDUCE_MEETING
    return template.replace("{reduce_output}", reduce_output)


def _build_single_prompt(category: str, reduce_output: str) -> str:
    template = SINGLE_LEARNING if category == "learning" else SINGLE_MEETING
    return template.replace("{reduce_output}", reduce_output)


def upgrade():
    # 新增字段
    op.add_column('meeting_types', sa.Column('map_prompt', sa.Text, server_default='', nullable=False))
    op.add_column('meeting_types', sa.Column('reduce_prompt', sa.Text, server_default='', nullable=False))
    op.add_column('meeting_types', sa.Column('single_extract_prompt', sa.Text, server_default='', nullable=False))
    op.add_column('meeting_types', sa.Column('is_builtin', sa.Boolean, server_default='0', nullable=False))

    # 为现有记录填充 map_prompt / reduce_prompt / single_extract_prompt
    # 根据 category 和 sub_type 选择对应模板
    conn = op.get_bind()

    # 先获取所有现有 meeting_types
    result = conn.execute(sa.text("SELECT id, category, sub_type FROM meeting_types"))
    existing = result.fetchall()

    # 构建 sub_type -> map_focus / reduce_output 映射
    sub_type_map = {t["sub_type"]: t for t in BUILTIN_TYPES}

    for row in existing:
        mt_id, category, sub_type = row[0], row[1], row[2]
        info = sub_type_map.get(sub_type, sub_type_map["regular"])
        map_p = _build_map_prompt(category, info["map_focus"])
        reduce_p = _build_reduce_prompt(category, info["reduce_output"])
        single_p = _build_single_prompt(category, info["reduce_output"])
        conn.execute(sa.text(
            "UPDATE meeting_types SET map_prompt = :mp, reduce_prompt = :rp, single_extract_prompt = :sp WHERE id = :id"
        ), {"mp": map_p, "rp": reduce_p, "sp": single_p, "id": mt_id})

    # Seed 内置类型（如果不存在）
    for t in BUILTIN_TYPES:
        # 检查是否已存在同 sub_type 的记录
        exists = conn.execute(
            sa.text("SELECT id FROM meeting_types WHERE sub_type = :st AND is_builtin = 1"),
            {"st": t["sub_type"]}
        ).fetchone()

        if not exists:
            # 也检查是否有同名的非内置记录
            exists2 = conn.execute(
                sa.text("SELECT id FROM meeting_types WHERE sub_type = :st"),
                {"st": t["sub_type"]}
            ).fetchone()

            if not exists2:
                map_p = _build_map_prompt(t["category"], t["map_focus"])
                reduce_p = _build_reduce_prompt(t["category"], t["reduce_output"])
                single_p = _build_single_prompt(t["category"], t["reduce_output"])
                conn.execute(sa.text(
                    "INSERT INTO meeting_types (name, description, category, sub_type, "
                    "map_prompt, reduce_prompt, single_extract_prompt, summary_prompt, "
                    "is_default, is_builtin, created_at, updated_at) "
                    "VALUES (:name, :desc, :cat, :st, :mp, :rp, :sp, '', 0, 1, "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ), {
                    "name": t["name"], "desc": t["description"],
                    "cat": t["category"], "st": t["sub_type"],
                    "mp": map_p, "rp": reduce_p, "sp": single_p,
                })

    # 第一个 regular 类型设为默认（如果没有任何默认类型）
    has_default = conn.execute(
        sa.text("SELECT id FROM meeting_types WHERE is_default = 1")
    ).fetchone()
    if not has_default:
        conn.execute(sa.text(
            "UPDATE meeting_types SET is_default = 1 WHERE sub_type = 'regular' AND is_builtin = 1"
        ))


def downgrade():
    op.drop_column('meeting_types', 'is_builtin')
    op.drop_column('meeting_types', 'single_extract_prompt')
    op.drop_column('meeting_types', 'reduce_prompt')
    op.drop_column('meeting_types', 'map_prompt')
