"""Delivery policy and scheduling; generation is never involved in grading."""
import asyncio
from datetime import date, datetime, time
import hashlib
import tempfile
import os
from pathlib import Path
import time as clock
from zoneinfo import ZoneInfo

from src.media import download_image, image_extension, MAX_IMAGE_BYTES
from .store import QuizError, QuizStore


class QuizService:
    def __init__(self, store: QuizStore, group_enabled=lambda gid: True, text_allowed=lambda text: True):
        self.store = store
        self.group_enabled = group_enabled
        self.text_allowed = text_allowed
        self.images = store.path.parent / "images"
        self.images.mkdir(exist_ok=True)
        self._tick_lock = asyncio.Lock()

    def check_text(self, payload):
        options = payload.get("options", [])
        if isinstance(options, dict):
            options = options.values()
        elif not isinstance(options, list):
            raise QuizError("选项必须是数组或字母键字典")
        texts = [str(payload.get("title", "")), str(payload.get("explanation", "")), *map(str, options)]
        if not all(self.text_allowed(text) for text in texts):
            raise QuizError("题干、选项或解析命中内容过滤，请检查后重新录入")

    def archive_image(self, data: bytes) -> str:
        if len(data) > MAX_IMAGE_BYTES:
            raise QuizError("图片不能超过 8MB")
        try:
            extension = image_extension(data)
        except ValueError as exc:
            raise QuizError(str(exc)) from exc
        filename = hashlib.sha256(data).hexdigest() + extension
        target = self.images / filename
        if not target.exists():
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=self.images, suffix=".tmp", delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                temporary.replace(target)
            finally:
                if temporary:
                    temporary.unlink(missing_ok=True)
        return "images/" + filename

    async def archive_url(self, url: str) -> str:
        return self.archive_image(await download_image(url))

    def image_path(self, relative: str) -> Path | None:
        if not relative:
            return None
        path = (self.store.path.parent / relative).resolve()
        if path.parent != self.images.resolve() or not path.is_file():
            raise QuizError("题目图片丢失或不在题库图片目录内，请管理员重新上传")
        return path

    def create(self, payloads, actor):
        for payload in payloads:
            self.check_text(payload)
            self.image_path(payload.get("image_path", ""))
        return self.store.create_questions(payloads, actor)

    def edit(self, qid, changes, actor):
        self.check_text({**self.store.get_question(qid), **changes})
        if "image_path" in changes:
            self.image_path(changes["image_path"])
        return self.store.edit_question(qid, changes, actor)

    async def _deliver(self, qid, gid, kind, send, now):
        if not self.group_enabled(gid) or not self.store.claim_delivery(qid, gid, kind):
            return False
        question = self.store.get_question(qid)
        try:
            result = await send(str(gid), kind, question)
            message_id = result.get("message_id") if isinstance(result, dict) else result
        except Exception as exc:
            # 超时也可能已被 QQ 接受；不自动重发结果不明的消息。
            detail = f"{type(exc).__name__}: {str(exc)[:200]}"
            self.store.finish_delivery(qid, gid, kind, error=detail, now=now)
            self.store.alert(f"delivery:{qid}:{gid}:{kind}:{now}", f"{qid} 向群 {gid} 发送{kind}结果不明，请核实后用命令重试。")
            return False
        self.store.finish_delivery(qid, gid, kind, message_id, now=now)
        return True

    async def publish(self, qid, send, now=None, retry=False):
        now = clock.time() if now is None else now
        settings = self.store.settings()
        question = self.store.get_question(qid)
        if not settings.enabled:
            raise QuizError("请先用 /答题设置 开启 活动")
        groups = [gid for gid in settings.groups if self.group_enabled(gid)]
        if not groups:
            raise QuizError("尚未配置目标群，或目标群的答题功能均已关闭")
        if question["state"] not in ("draft", "open") or question["deadline_at"] <= now:
            raise QuizError("题目已结束，不能再发布；未发布的过期题请先修改排期")
        self.image_path(question["image_path"])
        self.store.prepare_delivery(qid, groups, retry=retry)
        result = await asyncio.gather(*(self._deliver(qid, gid, "question", send, now) for gid in groups))
        return sum(result)

    async def reveal(self, qid, send, actor="system", now=None, force=False, retry=False):
        now = clock.time() if now is None else now
        self.store.close_question(qid, actor, now, force)
        groups = [row["group_id"] for row in self.store.deliveries(qid) if row["status"] == "sent"]
        self.store.prepare_delivery(qid, groups, "answer", retry)
        return sum(await asyncio.gather(*(self._deliver(qid, gid, "answer", send, now) for gid in groups)))

    async def tick(self, send, now=None):
        if self._tick_lock.locked():
            return
        async with self._tick_lock:
            now = clock.time() if now is None else now
            settings = self.store.settings()
            if not settings.enabled:
                return
            local = datetime.fromtimestamp(now, ZoneInfo(settings.timezone))
            questions = self.store.questions()
            for question in questions:
                qid = question["qid"]
                try:
                    if question["state"] == "open" and question["deadline_at"] <= now:
                        await self.reveal(qid, send, now=now)
                    elif question["state"] == "closed":
                        await self.reveal(qid, send, now=now)
                    elif question["scheduled_at"] <= now < question["deadline_at"] and settings.active_on(date.fromisoformat(question["publish_date"])):
                        await self.publish(qid, send, now)
                    elif question["state"] == "draft" and question["deadline_at"] <= now:
                        self.store.alert("missed:" + qid, f"{qid} 已过截止时间但从未成功发布，请重新安排题目。")
                    self.store.clear_alert("schedule:" + qid)
                except (QuizError, OSError) as exc:
                    self.store.alert("schedule:" + qid, f"{qid} 排期处理失败：{str(exc)[:150]}")
            publish_at = datetime.combine(local.date(), time.fromisoformat(settings.publish_time), ZoneInfo(settings.timezone)).timestamp()
            if settings.active_on(local.date()) and now >= publish_at and not any(q["publish_date"] == local.date().isoformat() for q in questions):
                self.store.alert("missing:" + local.date().isoformat(), f"{local.date()} 没有排期题目，今日科普答题无法自动发布。")
            if settings.reminder_minutes:
                for question in self.store.active_questions(now):
                    if now >= question["deadline_at"] - settings.reminder_minutes * 60:
                        groups = [row["group_id"] for row in self.store.deliveries(question["qid"]) if row["status"] == "sent"]
                        self.store.prepare_delivery(question["qid"], groups, "reminder")
                        await asyncio.gather(*(self._deliver(question["qid"], gid, "reminder", send, now) for gid in groups))


def format_time(value, timezone="Asia/Shanghai"):
    return datetime.fromtimestamp(value, ZoneInfo(timezone)).strftime("%Y-%m-%d %H:%M")


def question_text(question, timezone="Asia/Shanghai", preview=False):
    options = "\n".join(f"{'ABCDEF'[index]}. {text}" for index, text in enumerate(question["options"]))
    text = (f"【督师 · 天文科普 {question['qid']}】\n{question['title']}\n\n{options}\n\n"
            f"作答：私聊我发 /答 {question['qid']} A（仅一题时可直接发选项字母）\n"
            f"截止：{format_time(question['deadline_at'], timezone)}（{timezone}）\n"
            f"分值：{question['points']:g}；首次提交有效，截止后公布答案。")
    if preview:
        text += f"\n\n【仅管理员预览】答案：{question['correct_answer']}\n解析：{question['explanation'] or '未填写'}"
    return text
