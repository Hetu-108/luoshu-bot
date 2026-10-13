# python3
# -*- coding: utf-8 -*-
# @File    : security.py
# @Usage   : 内容安全 —— 敏感词过滤（对应 F-13 输出内容过滤要求）
#
# 用于 AI 对话的输入与输出检查。
# 词库放在 data/sensitive_words.txt，一行一个词，# 开头为注释。
# 文件不存在时会自动生成一份基础词库。

from pathlib import Path

from nonebot.log import logger

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
WORD_FILE = DATA_DIR / "sensitive_words.txt"

# 基础词库（可在 data/sensitive_words.txt 中自行增删）
DEFAULT_WORDS = """
# 一行一个词，# 开头为注释
# 涉政类
颠覆国家政权
推翻政府
# 暴恐类
制造炸弹
制作炸弹
爆炸物配方
恐怖袭击
# 色情类
色情网站
裸聊
约炮
# 赌博诈骗类
网络赌博
博彩网站
代开发票
刷单返利
# 违禁品类
冰毒
海洛因
枪支弹药
买卖枪支
# 侮辱辱骂类
傻逼
妈的
滚你妈
"""


def _ensure_word_file() -> None:
    if not WORD_FILE.exists():
        try:
            WORD_FILE.write_text(DEFAULT_WORDS.strip() + "\n", encoding="utf-8")
        except Exception as e:
            logger.warning(f"[security] 无法创建词库文件: {e}")


def _load_words() -> list:
    _ensure_word_file()
    words = []
    try:
        for line in WORD_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                words.append(line)
    except Exception as e:
        logger.warning(f"[security] 读取词库失败: {e}")
    return words


_words = _load_words()


def reload_words() -> int:
    """重新加载词库，返回词条数量"""
    global _words
    _words = _load_words()
    return len(_words)


def has_sensitive(text: str) -> bool:
    """判断文本是否命中敏感词"""
    if not text:
        return False
    low = text.lower()
    return any(w.lower() in low for w in _words)


def filter_text(text: str, replacement: str = "***") -> str:
    """把敏感词替换成占位符"""
    if not text:
        return text
    result = text
    for w in _words:
        if w.lower() in result.lower():
            # 大小写不敏感替换
            import re

            result = re.sub(re.escape(w), replacement, result, flags=re.IGNORECASE)
    return result


def is_safe(text: str) -> bool:
    return not has_sensitive(text)
