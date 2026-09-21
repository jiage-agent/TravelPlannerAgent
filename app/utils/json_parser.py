"""
LLM 返回 JSON 的健壮解析工具
"""
import json
import re


def parse_llm_json(content: str) -> dict:
    """解析 LLM 返回的 JSON，处理各种脏数据"""
    if not content:
        raise ValueError("LLM 返回内容为空")

    content = content.strip()

    # 去掉 markdown 代码块包裹
    if content.startswith("```"):
        # 去掉开头的 ``` 或 ```json
        content = re.sub(r"^```(?:json)?\s*", "", content)
        # 去掉结尾的 ```
        content = re.sub(r"\s*```$", "", content)

    content = content.strip()

    # 尝试直接解析
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        # 尝试提取第一个完整的 JSON 对象
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise