# python3
# -*- coding: utf-8 -*-
# @File    : security.py
# @Usage   : 内容安全 —— 敏感词过滤（对应 F-13 输出内容过滤要求）
#
# 用于 AI 对话的输入与输出检查。
# 词库放在 data/sensitive_words.txt，一行一个词，# 开头为注释。
# 文件不存在时会自动生成一份基础词库。
from nonebot.log import logger
from .core import DATA_DIR

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


def _normalize(text: str) -> str:
    """把文本归一化，抵御「空格 / 符号 / 全角」等简单变形绕过。

    处理内容：
      1. 全角字符转半角（含全角字母、数字、标点）
      2. 去掉所有空白（空格、Tab、换行、全角空格）
      3. 去掉常见干扰符号（* · - _ . ~ ^ 、 等）
      4. 统一转小写

    例："傻 逼" / "傻*逼" / "傻　逼" / "Ｓｈａ逼" 归一化后都能命中「傻逼」。
    注意：这只做「去掉干扰字符」的宽松匹配，不做同音字/拼音替换。
    """
    if not text:
        return ""
    out = []
    for ch in text:
        code = ord(ch)
        # 全角 → 半角（ASCII 可见字符区间 0xFF01-0xFF5E；全角空格 0x3000）
        if code == 0x3000:
            continue  # 全角空格当空白丢弃
        if 0xFF01 <= code <= 0xFF5E:
            ch = chr(code - 0xFEE0)
        if ch.isspace():
            continue
        if ch in _NOISE_CHARS:
            continue
        out.append(ch)
    return "".join(out).lower()


# 会被忽略的干扰符号（变形常用）：标点、间隔号、下划线、特殊符号
_NOISE_CHARS = set("*·-_.~^、,，。.！!？?：:；;\"'“”‘’()（）[]【】{}<>《》/\\|+&%$#@`")


def has_sensitive(text: str) -> bool:
    """判断文本是否命中敏感词（含去干扰字符后的宽松匹配）"""
    if not text:
        return False
    low = text.lower()
    norm = _normalize(text)
    for w in _words:
        wl = w.lower()
        if wl in low:
            return True
        # 宽松匹配：词条本身也归一化后再比对，覆盖「傻 逼」这类变形
        wn = _normalize(w)
        if wn and wn in norm:
            return True
    return False


def filter_text(text: str, replacement: str = "***") -> str:
    """检测与替换使用同一归一化规则，并映射回原文的位置。"""
    if not text:
        return text
    import re
    normalized, positions = [], []
    for index, char in enumerate(text):
        part = _normalize(char)
        normalized.extend(part)
        positions.extend([index] * len(part))
    normalized = "".join(normalized)
    spans = []
    for w in _words:
        spans.extend((m.start(), m.end()) for m in re.finditer(re.escape(w), text, flags=re.IGNORECASE))
        word = _normalize(w)
        if not word:
            continue
        start = 0
        while (found := normalized.find(word, start)) >= 0:
            spans.append((positions[found], positions[found + len(word) - 1] + 1))
            start = found + 1
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    result, cursor = [], 0
    for start, end in merged:
        result.extend([text[cursor:start], replacement])
        cursor = end
    result.append(text[cursor:])
    return "".join(result)


def is_safe(text: str) -> bool:
    return not has_sensitive(text)
