"""督师科普答题：人工题库、私聊提交、确定性判分、截止后结算。"""
import asyncio
import base64
from dataclasses import asdict
from datetime import date
import json
from pathlib import Path
import re
import sqlite3
import time
import unicodedata

import httpx
from nonebot import get_bots, get_driver, on_command, on_message, require
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment, PrivateMessageEvent
from nonebot.adapters.onebot.v11.exception import ActionFailed
from nonebot.params import Command, CommandArg
from nonebot.rule import Rule
from nonebot.log import logger
from nonebot.typing import T_State

from .core import DATA_DIR, _OWNER_QQ, log_command
from .features import guard, is_enabled
from .security import has_sensitive
from src.quiz.store import QuizStore, QuizError
from src.quiz.service import QuizService, question_text, format_time
from src.quiz.importer import normalize_import_row, parse_options
from src.quiz.cloud import CloudSync, TencentDocsSink
from src.quiz.exporter import export_scores

driver = get_driver()
store = QuizStore(DATA_DIR / "quiz" / "state.sqlite3")
service = QuizService(store, lambda gid: is_enabled(int(gid), "quiz"), lambda text: not has_sensitive(text))


def _cloud_sink():
    cfg = driver.config
    if str(getattr(cfg, "quiz_cloud_enabled", "false")).lower() not in ("true", "1", "yes"):
        return None
    return TencentDocsSink(*(str(getattr(cfg, key, "")) for key in (
        "qqdoc_file_id", "qqdoc_summary_sheet", "qqdoc_details_sheet", "qqdoc_client_id", "qqdoc_open_id", "qqdoc_access_token"
    )))


cloud = CloudSync(store, _cloud_sink())
_drafts = {}
_deletions = {}
_alert_retry_at = 0
STEPS = ["title", "image_path", "options", "correct_answer", "explanation", "publish_date"]
PROMPTS = [
    "请发送题干（至多2000字）。",
    "请发送一张题目图片（至多8MB），没有图片发“跳过”。",
    "请发送2~6个选项，每行一个，例如：\nA. 地球\nB. 月球",
    "请发送正确答案字母。",
    "请发送人工编写的解析，没有可发“跳过”。",
    "请发送计划发布日期，格式 YYYY-MM-DD；分值默认1分，可录入后用 /改题 修改。",
]


def _admin(event):
    return event.get_user_id() in _OWNER_QQ


def _plain(text):
    return MessageSegment.text(str(text))


def _key(bot, event):
    return (str(bot.self_id), event.get_user_id())


def _question_message(question, preview=False):
    result = Message(_plain(question_text(question, store.settings().timezone, preview)))
    path = service.image_path(question["image_path"])
    if path:
        # 来源始终是本地归档；base64 避免宿主机与登录端容器路径不同。
        result += MessageSegment.image("base64://" + base64.b64encode(path.read_bytes()).decode("ascii"))
    return result


async def _send(bot, gid, kind, question):
    if kind == "question":
        message = _question_message(question)
    elif kind == "answer":
        stats = store.answer_stats(question["qid"])
        message = _plain(f"【督师 · {question['qid']} 答案公布】\n正确答案：{question['correct_answer']}\n"
                         f"解析：{question['explanation'] or '本题未填写解析'}\n"
                         f"参与 {stats['total']} 人，答对 {stats['correct']} 人。成绩已结算，可查 /排行、/我的成绩。")
    else:
        message = _plain(f"【督师 · 答题提醒】{question['qid']} 将于 {format_time(question['deadline_at'], store.settings().timezone)} 截止，尚未提交的同学请私聊作答。")
    return await bot.send_group_msg(group_id=int(gid), message=message)


def _sender(bot):
    async def send(gid, kind, question):
        return await _send(bot, gid, kind, question)
    return send


async def _member_groups(bot, user_id, groups):
    async def check(gid):
        if not service.group_enabled(gid):
            return None
        try:
            info = await bot.get_group_member_info(group_id=int(gid), user_id=int(user_id), no_cache=True)
            if str((info or {}).get("user_id", "")) == str(user_id):
                return str(gid)
        except Exception:
            pass
        return None
    return [gid for gid in await asyncio.gather(*(check(gid) for gid in groups)) if gid]


async def _candidates(bot, event):
    result = []
    if not store.settings().enabled:
        return result
    for question in store.active_questions():
        groups = [row["group_id"] for row in store.deliveries(question["qid"]) if row["status"] == "sent"]
        eligible = await _member_groups(bot, event.get_user_id(), groups)
        if eligible:
            result.append((question, eligible))
    return result


def _answer_letter(value):
    return unicodedata.normalize("NFKC", value).strip().upper()


async def _submit(matcher, bot, event, text, candidates=None):
    if not isinstance(event, PrivateMessageEvent):
        await matcher.finish("请私聊我作答，避免在群内公开答案。")
    parts = text.strip().split()
    if len(parts) == 1:
        choices = candidates if candidates is not None else await _candidates(bot, event)
        if not choices:
            await matcher.finish("当前没有你可作答的进行中题目。指定题号可用 /答 Q0001 A。")
        if len(choices) != 1:
            await matcher.finish("有多道题正在进行，请指定题号，例如 /答 Q0001 A。")
        question, groups = choices[0]
        answer = _answer_letter(parts[0])
    elif len(parts) == 2 and re.fullmatch(r"Q\d{4,}", parts[0].upper()):
        question = store.get_question(parts[0].upper())
        groups = await _member_groups(bot, event.get_user_id(), [row["group_id"] for row in store.deliveries(question["qid"]) if row["status"] == "sent"])
        answer = _answer_letter(parts[1])
    else:
        await matcher.finish("格式：/答 A，或 /答 Q0001 A。")
    result = store.submit(question["qid"], event.get_user_id(), event.sender.nickname or "成员", answer,
                          bot.self_id, event.message_id, groups)
    if result["result"] == "duplicate":
        await matcher.finish(f"你已经答过 {question['qid']} 啦，首次提交为准。")
    receipt = f"已记录 {result['qid']} → {result['answer']}"
    if store.settings().immediate_result or question["state"] == "closed":
        receipt += f"；本题得分 {result['score']:g}。"
    else:
        receipt += "；截止后统一公布答案和成绩。"
    log_command("科普答题:提交", event)
    await matcher.finish(receipt)


answer_cmd = on_command("答", aliases={"答题"}, rule=guard("quiz"), priority=5, block=True)


@answer_cmd.handle()
async def _answer(bot: Bot, event: MessageEvent, arg: Message = CommandArg()):
    try:
        await _submit(answer_cmd, bot, event, arg.extract_plain_text())
    except QuizError as exc:
        await answer_cmd.finish(str(exc))
    except sqlite3.Error:
        logger.exception("[quiz] 答题事务失败")
        await answer_cmd.finish("保存答题记录失败，请稍后重试；本次未确认计分。")


def _bare_rule():
    async def check(bot: Bot, event: MessageEvent, state: T_State):
        if not isinstance(event, PrivateMessageEvent):
            return False
        text = _answer_letter(event.get_plaintext())
        if not re.fullmatch(r"[A-F]", text):
            return False
        choices = await _candidates(bot, event)
        if len(choices) != 1:
            return False
        state["quiz_choices"] = choices
        return True
    return Rule(check)


bare_answer = on_message(rule=_bare_rule(), priority=90, block=True)


@bare_answer.handle()
async def _bare(bot: Bot, event: MessageEvent, state: T_State):
    try:
        await _submit(bare_answer, bot, event, event.get_plaintext(), state.get("quiz_choices"))
    except QuizError as exc:
        await bare_answer.finish(str(exc))
    except sqlite3.Error:
        logger.exception("[quiz] 答题事务失败")
        await bare_answer.finish("保存答题记录失败，请稍后重试；本次未确认计分。")


async def _check_view_permission(bot, event):
    if _admin(event):
        return
    groups = store.settings().groups
    gid = getattr(event, "group_id", None)
    if gid:
        if str(gid) not in groups or not service.group_enabled(gid):
            raise QuizError("本群不是已开启答题的活动群")
    elif not await _member_groups(bot, event.get_user_id(), groups):
        raise QuizError("需要加入已开启答题的活动群，并能验证群成员身份")


rank_cmd = on_command("排行", aliases={"答题排行"}, rule=guard("quiz"), priority=5, block=True)


@rank_cmd.handle()
async def _rank(bot: Bot, event: MessageEvent):
    try:
        await _check_view_permission(bot, event)
        if not store.settings().leaderboard_public and not _admin(event):
            raise QuizError("排行榜当前仅管理员可查看")
        rows = store.rankings(getattr(event, "group_id", None))[:10]
        text = "【督师 · 已结算成绩 Top 10】\n"
        text += "\n".join(f"{index}. {row['nickname']}　{row['total']:g} 分（答对{row['correct']}题）" for index, row in enumerate(rows, 1)) if rows else "暂无已结算成绩。"
        text += "\n同分依次按答对数、首次提交时间排序；进行中的题目暂不计入。"
        await rank_cmd.finish(_plain(text))
    except QuizError as exc:
        await rank_cmd.finish(str(exc))


score_cmd = on_command("我的成绩", rule=guard("quiz"), priority=5, block=True)


@score_cmd.handle()
async def _score(bot: Bot, event: MessageEvent):
    if not isinstance(event, PrivateMessageEvent):
        await score_cmd.finish("请私聊我查看个人成绩。")
    try:
        await _check_view_permission(bot, event)
        result = store.personal(event.get_user_id())
        accuracy = result["correct"] / result["answered"] * 100 if result["answered"] else 0
        text = (f"【督师 · 我的成绩】\n已结算总分：{result['total']:g}\n答对：{result['correct']} / {result['answered']}\n"
                f"正确率：{accuracy:.1f}%\n待结算：{result['pending']}题")
        await score_cmd.finish(_plain(text))
    except QuizError as exc:
        await score_cmd.finish(str(exc))


def _wizard_rule():
    async def check(bot: Bot, event: MessageEvent):
        if not isinstance(event, PrivateMessageEvent) or not _admin(event) or _key(bot, event) not in _drafts:
            return False
        text = event.get_plaintext().strip()
        return not text.startswith("/") or text in ("/取消", "/上一步")
    return Rule(check)


wizard = on_message(rule=_wizard_rule(), priority=3, block=True)


@wizard.handle()
async def _wizard(bot: Bot, event: MessageEvent):
    key = _key(bot, event)
    draft = _drafts[key]
    if time.time() - draft["updated_at"] > 1800:
        _drafts.pop(key, None)
        await wizard.finish("录题会话已超过30分钟，请重新发 /出题。")
    text = event.get_plaintext().strip()
    if text in ("取消", "/取消"):
        _drafts.pop(key, None)
        await wizard.finish("已取消录题。")
    if text in ("上一步", "/上一步"):
        draft["step"] = max(0, draft["step"] - 1)
        await wizard.finish(PROMPTS[draft["step"]])
    field = STEPS[draft["step"]]
    try:
        if field == "image_path":
            images = [seg for seg in event.get_message() if seg.type == "image"]
            if text in ("跳过", "无"):
                value = ""
            elif len(images) == 1:
                value = await service.archive_url(str(images[0].data.get("url", "")))
            else:
                raise QuizError("请发送一张图片，或发送“跳过”。")
        elif field == "options":
            value = parse_options(text)
            if not 2 <= len(value) <= 6 or any(not item for item in value):
                raise QuizError("请每行一个选项，发送2~6项。")
        elif field == "correct_answer":
            value = _answer_letter(text)
            if value not in list("ABCDEF"[:len(draft["values"]["options"])]):
                raise QuizError("请输入本题选项中的一个字母。")
        elif field == "publish_date":
            value = date.fromisoformat(text).isoformat()
        else:
            value = "" if field == "explanation" and text in ("跳过", "无") else text
            if field == "title" and (not value or len(value) > 2000):
                raise QuizError("题干不能为空且不能超过2000字。")
        draft["values"][field] = value
        service.check_text(draft["values"])
        draft["updated_at"] = time.time()
        if draft["step"] == len(STEPS) - 1:
            question = service.create([draft["values"]], event.get_user_id())[0]
            _drafts.pop(key, None)
            await bot.send_private_msg(user_id=event.user_id, message=_question_message(question, True))
            await wizard.finish(f"已保存 {question['qid']}。可用 /改题 调整或 /发题 {question['qid']} 发布。")
        draft["step"] += 1
        await wizard.finish(PROMPTS[draft["step"]])
    except (ValueError, httpx.HTTPError) as exc:
        await wizard.finish(f"未完成：{str(exc)[:150]}\n{PROMPTS[draft['step']]}")


ADMIN_COMMANDS = {"题库", "预览", "改题", "删题", "发题", "答案", "查分", "调分", "导出", "同步", "答题设置", "答题总览", "导入题库"}
admin_cmd = on_command("出题", aliases=ADMIN_COMMANDS, rule=guard("quiz"), priority=5, block=True)


def _target(parts, arg):
    at = [str(seg.data.get("qq", "")) for seg in arg if seg.type == "at" and str(seg.data.get("qq")) != "all"]
    user = at[0] if at else (parts.pop(0) if parts else "")
    if not user.isdigit():
        raise QuizError("请 @成员或填写成员 QQ 号")
    return user


async def _admin_action(bot, event, name, arg):
    text = arg.extract_plain_text().strip()
    parts = text.split()
    actor = event.get_user_id()
    if name == "出题":
        _drafts[_key(bot, event)] = {"step": 0, "values": {}, "updated_at": time.time()}
        return PROMPTS[0] + "\n随时可发“上一步”或“取消”。"
    if name == "题库":
        rows = store.questions()
        return "题库为空。用 /出题 录入，或 /导入题库 导入 JSON。" if not rows else "\n".join(
            f"{q['qid']}　{q['publish_date']}　{q['state']}　{q['title'][:28]}" for q in rows
        )[:6000]
    if name == "预览":
        question = store.get_question(text.upper())
        await bot.send_private_msg(user_id=event.user_id, message=_question_message(question, True))
        return "预览已发送，仅管理员本人可见。"
    if name == "导入题库":
        if not text:
            return "用法：/导入题库 <JSON题目数组>。Excel/JSON文件可用服务器 CLI 批量导入，见 docs/quiz.md。"
        rows = json.loads(text)
        if isinstance(rows, dict):
            rows = rows.get("questions", [])
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise QuizError("题库应为题目对象数组")
        normalized = [normalize_import_row(row) for row in rows]
        for row in normalized:
            image = row.pop("image", "")
            if image:
                row["image_path"] = await service.archive_url(str(image))
        questions = service.create(normalized, actor)
        return "已导入：" + "、".join(q["qid"] for q in questions)
    if name == "改题":
        values = text.split(maxsplit=2)
        if len(values) < 2:
            raise QuizError('用法：/改题 Q0001 {"title":"新题干"}，或 /改题 Q0001 分值 2')
        qid = values[0].upper()
        if values[1].startswith("{"):
            changes = json.loads(text.split(maxsplit=1)[1])
            if not isinstance(changes, dict):
                raise QuizError("修改内容必须是 JSON 对象")
        else:
            mapping = {"题干": "title", "选项": "options", "答案": "correct_answer", "分值": "points", "解析": "explanation", "日期": "publish_date", "截止": "deadline", "图片": "image_path"}
            field = mapping.get(values[1], values[1])
            value = values[2] if len(values) == 3 else ""
            if field == "options":
                value = parse_options(value)
            if field == "image_path":
                images = [seg for seg in arg if seg.type == "image"]
                if value in ("清空", "无", "跳过"):
                    value = ""
                else:
                    value = await service.archive_url(str(images[0].data.get("url", "")) if images else value)
            changes = {field: value}
        service.edit(qid, changes, actor)
        return f"已修改 {qid}。请 /预览 {qid} 确认。"
    if name == "删题":
        if not parts:
            raise QuizError("用法：/删题 Q0001；确认后再发 /删题 Q0001 确认")
        qid = parts[0].upper()
        key = (_key(bot, event), qid)
        if len(parts) == 2 and parts[1] == "确认" and _deletions.get(key, 0) > time.time():
            store.delete_question(qid, actor)
            _deletions.pop(key, None)
            return f"已删除 {qid}，操作已留痕。"
        store.get_question(qid)
        _deletions[key] = time.time() + 60
        return f"请在60秒内发 /删题 {qid} 确认。"
    if name in ("发题", "答案"):
        candidates = store.questions() if name == "发题" else [q for q in store.questions() if q["state"] in ("open", "closed")]
        if not parts:
            if name == "发题":
                now = time.time()
                candidates = [q for q in candidates if q["state"] in ("draft", "open") and q["scheduled_at"] <= now < q["deadline_at"]]
            else:
                candidates = [q for q in candidates if q["state"] == "open"]
            if len(candidates) != 1:
                raise QuizError("请指定题号，例如 /" + name + " Q0001")
            qid = candidates[0]["qid"]
        else:
            qid = parts[0].upper()
        retry = "重试" in parts
        if name == "发题":
            sent = await service.publish(qid, _sender(bot), retry=retry)
        else:
            sent = await service.reveal(qid, _sender(bot), actor, force=True, retry=retry)
        return f"已处理 {qid}，本次成功发送 {sent} 个群；已成功发送的群不会重复发送。"
    if name == "查分":
        user = _target(parts, arg)
        result = store.personal(user)
        lines = [f"QQ {user}；已结算总分 {result['total']:g}；待结算 {result['pending']}题"]
        lines.extend(f"{row['qid']} → {row['answer']}；{row['score']:g}分；{row['state']}" for row in result["details"])
        return "\n".join(lines)[:6000]
    if name == "调分":
        qid = parts.pop(0).upper() if parts and re.fullmatch(r"Q\d{4,}", parts[0].upper()) else None
        user = _target(parts, arg)
        if len(parts) < 2:
            raise QuizError("用法：/调分 [题号] QQ号 分值 原因；有题号时设置单题得分，无题号时增减总分。")
        value, reason = float(parts[0]), " ".join(parts[1:])
        store.adjust(user, value, reason, actor, qid)
        return "调分已保存，原始答案不变，原因和操作人已留痕。"
    if name == "导出":
        paths = export_scores(store, DATA_DIR / "quiz" / "exports")
        export_root = str(getattr(driver.config, "quiz_onebot_export_root", ""))
        for path in paths:
            remote = str(Path(export_root) / path.name) if export_root else str(path.resolve())
            try:
                await bot.upload_private_file(user_id=event.user_id, file=remote, name=path.name)
            except ActionFailed:
                return "CSV 已保存到服务器的 data/quiz/exports；登录端上传失败，请检查 QUIZ_ONEBOT_EXPORT_ROOT 目录映射。"
        return "已私聊发送汇总、明细和操作审计 CSV（管理员文件包含 QQ，请妥善保管）。"
    if name == "同步":
        completed = await cloud.sync()
        return f"本次同步 {completed} 行；剩余待同步 {len(store.pending_cloud())} 行。"
    if name == "答题总览":
        active = store.active_questions()
        lines = [f"活动：{'开启' if store.settings().enabled else '关闭'}；目标群 {len(store.settings().groups)} 个；待同步 {len(store.pending_cloud())} 行"]
        for q in active:
            stats = store.answer_stats(q["qid"])
            deliveries = store.deliveries(q["qid"])
            lines.append(f"{q['qid']}：已发 {sum(d['status']=='sent' for d in deliveries)} 个群，{stats['total']} 人提交，答对 {stats['correct']} 人（仅管理员可见）")
        lines.extend(f"告警：{row['detail']}" for row in store.pending_alerts())
        return "\n".join(lines)[:6000]
    if name == "答题设置":
        if not text:
            return json.dumps(asdict(store.settings()), ensure_ascii=False, indent=2) + "\n用法：/答题设置 <JSON对象>，或 开启 活动、关闭 活动。"
        if text in ("开启", "开启 活动"):
            changes = {"enabled": True}
        elif text in ("关闭", "关闭 活动"):
            changes = {"enabled": False}
        else:
            changes = json.loads(text)
            if not isinstance(changes, dict):
                raise QuizError("设置必须是 JSON 对象")
        result = store.configure(changes, actor)
        return "已保存答题设置：\n" + json.dumps(asdict(result), ensure_ascii=False, indent=2)
    raise QuizError("未知答题管理命令")


@admin_cmd.handle()
async def _admin_handler(bot: Bot, event: MessageEvent, command: tuple[str, ...] = Command(), arg: Message = CommandArg()):
    if not _admin(event):
        await admin_cmd.finish("此操作仅机器人管理员可用。")
    name = command[-1]
    if not isinstance(event, PrivateMessageEvent) and name not in ("发题", "答案"):
        await admin_cmd.finish("请私聊我使用答题管理命令，避免题库或成绩明细公开。")
    try:
        reply = await _admin_action(bot, event, name, arg)
        log_command("科普答题:" + name, event)
        await admin_cmd.finish(_plain(reply))
    except (ValueError, OSError, sqlite3.Error, httpx.HTTPError) as exc:
        logger.warning(f"[quiz] {name} 失败: {type(exc).__name__}")
        await admin_cmd.finish(_plain(f"未完成：{str(exc)[:180]}"))


async def _notify_alerts(bot):
    global _alert_retry_at
    if not _OWNER_QQ or time.time() < _alert_retry_at:
        return
    _alert_retry_at = time.time() + 300
    for row in store.pending_alerts():
        sent = False
        for owner in _OWNER_QQ:
            try:
                await bot.send_private_msg(user_id=int(owner), message=_plain("【督师告警】" + row["detail"]))
                sent = True
            except Exception:
                pass
        if sent:
            store.mark_alert(row["key"])


@driver.on_startup
async def _recover():
    store.recover_deliveries()


@driver.on_bot_connect
async def _catch_up(bot: Bot):
    await service.tick(_sender(bot))
    await _notify_alerts(bot)


scheduler = require("nonebot_plugin_apscheduler").scheduler


@scheduler.scheduled_job("interval", seconds=30, id="luoshu_quiz_tick", max_instances=1)
async def _tick():
    bots = get_bots()
    if not bots:
        return
    bot = next(iter(bots.values()))
    await service.tick(_sender(bot))
    await _notify_alerts(bot)


@scheduler.scheduled_job("interval", seconds=300, id="luoshu_quiz_cloud", max_instances=1)
async def _sync():
    if cloud.sink:
        try:
            await cloud.sync()
        except Exception:
            logger.warning("[quiz] 云同步失败，待同步数据仍保留")
