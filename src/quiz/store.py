"""SQLite transactions keep answers, scores, audit records and sync jobs consistent."""
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields
from datetime import date, datetime, time, timedelta
import json
import math
from pathlib import Path
import re
import sqlite3
import time as clock
from zoneinfo import ZoneInfo


class QuizError(ValueError):
    pass


@dataclass
class Settings:
    enabled: bool = False
    groups: list[str] = None
    publish_time: str = "20:00"
    timezone: str = "Asia/Shanghai"
    start_date: str = ""
    end_date: str = ""
    points: float = 1
    close_mode: str = "next_day"
    late_ratio: float = 0
    immediate_result: bool = False
    leaderboard_public: bool = True
    reminder_minutes: int = 0

    def __post_init__(self):
        if self.groups is not None and not isinstance(self.groups, (list, tuple)):
            raise QuizError("目标群必须是群号数组，例如 [\"123456\"]")
        self.groups = [str(g) for g in (self.groups or [])]
        if any(not g.isdigit() or int(g) <= 0 for g in self.groups):
            raise QuizError("目标群必须是正整数 QQ 群号")
        self.groups = list(dict.fromkeys(self.groups))
        if any(not isinstance(value, str) for value in (self.publish_time, self.timezone, self.start_date, self.end_date)):
            raise QuizError("时间、时区和日期必须是字符串")
        try:
            time.fromisoformat(self.publish_time)
            ZoneInfo(self.timezone)
            start = date.fromisoformat(self.start_date) if self.start_date else None
            end = date.fromisoformat(self.end_date) if self.end_date else None
        except (ValueError, KeyError) as exc:
            raise QuizError("日期、时间或时区格式不正确") from exc
        if start and end and start > end:
            raise QuizError("活动开始日期不能晚于结束日期")
        if not isinstance(self.enabled, bool) or not isinstance(self.immediate_result, bool) or not isinstance(self.leaderboard_public, bool):
            raise QuizError("开关值必须是 true / false")
        if self.close_mode not in ("next_day", "same_day"):
            raise QuizError("截止方式只支持 next_day / same_day")
        try:
            self.points = float(self.points)
            self.late_ratio = float(self.late_ratio)
        except (ValueError, TypeError) as exc:
            raise QuizError("分值和迟交比例必须是数字") from exc
        if not math.isfinite(self.points) or not 0 < self.points <= 1000:
            raise QuizError("分值必须大于 0 且不超过 1000")
        if not math.isfinite(self.late_ratio) or not 0 <= self.late_ratio <= 1:
            raise QuizError("迟交比例必须在 0 到 1 之间")
        if not isinstance(self.reminder_minutes, int) or not 0 <= self.reminder_minutes <= 1440:
            raise QuizError("提醒分钟数必须是 0~1440 的整数（0 为关闭）")

    def active_on(self, day: date) -> bool:
        return self.enabled and (not self.start_date or day.isoformat() >= self.start_date) and (
            not self.end_date or day.isoformat() <= self.end_date
        )


def stamp(value, timezone: str) -> float:
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            raise QuizError("时间必须是有效日期或有限时间戳")
        return float(value)
    try:
        result = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise QuizError("时间应为 YYYY-MM-DD HH:MM 或 ISO8601") from exc
    if result.tzinfo is None:
        result = result.replace(tzinfo=ZoneInfo(timezone))
    return result.timestamp()


def normalize_question(payload: dict, settings: Settings) -> dict:
    title = str(payload.get("title", "")).strip()
    explanation = str(payload.get("explanation", "")).strip()
    options = payload.get("options", [])
    if isinstance(options, dict):
        if set(options) != set("ABCDEF"[:len(options)]):
            raise QuizError("选项字典必须从 A 开始连续排列")
        options = [options[key] for key in sorted(options)]
    if not isinstance(options, list) or not 2 <= len(options) <= 6:
        raise QuizError("每题必须有 2~6 个选项")
    options = [str(value).strip() for value in options]
    if not title or len(title) > 2000 or any(not value or len(value) > 500 for value in options):
        raise QuizError("题干不能为空且至多 2000 字，选项不能为空且每项至多 500 字")
    if len(explanation) > 4000:
        raise QuizError("解析至多 4000 字")
    answer = str(payload.get("correct_answer", "")).strip().upper()
    if answer not in list("ABCDEF"[:len(options)]):
        raise QuizError("正确答案必须是本题选项中的一个字母")
    try:
        points = float(payload.get("points", settings.points))
    except (TypeError, ValueError) as exc:
        raise QuizError("分值必须是数字") from exc
    if not math.isfinite(points) or not 0 < points <= 1000:
        raise QuizError("分值必须大于 0 且不超过 1000")
    try:
        day = date.fromisoformat(str(payload["publish_date"]))
    except (KeyError, ValueError) as exc:
        raise QuizError("必须提供 YYYY-MM-DD 格式的发布日期") from exc
    scheduled = datetime.combine(day, time.fromisoformat(settings.publish_time), ZoneInfo(settings.timezone))
    deadline = (scheduled + timedelta(days=1)) if settings.close_mode == "next_day" else datetime.combine(
        day, time(23, 59, 59), ZoneInfo(settings.timezone)
    )
    if payload.get("deadline"):
        deadline = datetime.fromtimestamp(stamp(payload["deadline"], settings.timezone), ZoneInfo(settings.timezone))
    if deadline <= scheduled:
        raise QuizError("截止时间必须晚于计划发布时间")
    return dict(title=title, options=options, correct_answer=answer, points=points, explanation=explanation,
                image_path=str(payload.get("image_path", "")), scheduled_at=scheduled.timestamp(),
                deadline_at=deadline.timestamp(), publish_date=day.isoformat())


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS questions (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, qid TEXT UNIQUE,
    title TEXT NOT NULL, options TEXT NOT NULL, correct_answer TEXT NOT NULL, points REAL NOT NULL,
    explanation TEXT NOT NULL, image_path TEXT NOT NULL, scheduled_at REAL NOT NULL, deadline_at REAL NOT NULL,
    publish_date TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'draft', created_by TEXT NOT NULL,
    created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS deliveries (
    qid TEXT NOT NULL REFERENCES questions(qid), group_id TEXT NOT NULL, kind TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending', message_id TEXT, attempted_at REAL, completed_at REAL,
    error TEXT NOT NULL DEFAULT '', PRIMARY KEY(qid,group_id,kind)
);
CREATE TABLE IF NOT EXISTS answers (
    qid TEXT NOT NULL REFERENCES questions(qid), user_id TEXT NOT NULL, nickname TEXT NOT NULL,
    answer TEXT NOT NULL, correct INTEGER NOT NULL, score REAL NOT NULL, submitted_at REAL NOT NULL,
    bot_id TEXT NOT NULL, message_id TEXT NOT NULL,
    PRIMARY KEY(qid,user_id), UNIQUE(bot_id,user_id,message_id)
);
CREATE TABLE IF NOT EXISTS answer_groups (
    qid TEXT NOT NULL, user_id TEXT NOT NULL, group_id TEXT NOT NULL,
    PRIMARY KEY(qid,user_id,group_id), FOREIGN KEY(qid,user_id) REFERENCES answers(qid,user_id)
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT NOT NULL, actor TEXT NOT NULL,
    detail TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS adjustments (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, qid TEXT,
    delta REAL NOT NULL, reason TEXT NOT NULL, actor TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS cloud_rows (
    kind TEXT NOT NULL, record_key TEXT NOT NULL, row_number INTEGER NOT NULL, payload TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1, synced_version INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(kind,record_key), UNIQUE(kind,row_number)
);
CREATE TABLE IF NOT EXISTS alerts (
    key TEXT PRIMARY KEY, detail TEXT NOT NULL, notified_at REAL
);
"""


class QuizStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
            db.execute("INSERT OR IGNORE INTO meta VALUES ('settings', ?)", (json.dumps(asdict(Settings())),))

    @contextmanager
    def connection(self, write=False):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        except Exception:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    def settings(self) -> Settings:
        with self.connection() as db:
            return Settings(**json.loads(db.execute("SELECT value FROM meta WHERE key='settings'").fetchone()[0]))

    def configure(self, changes: dict, actor: str) -> Settings:
        unknown = set(changes) - {item.name for item in fields(Settings)}
        if unknown:
            raise QuizError("未知设置项：" + ", ".join(sorted(unknown)))
        with self.connection(True) as db:
            old = json.loads(db.execute("SELECT value FROM meta WHERE key='settings'").fetchone()[0])
            result = Settings(**{**old, **changes})
            db.execute("UPDATE meta SET value=? WHERE key='settings'", (json.dumps(asdict(result)),))
            self._audit(db, "settings", actor, changes)
        return result

    @staticmethod
    def _audit(db, action, actor, detail):
        db.execute("INSERT INTO audit(action,actor,detail,created_at) VALUES(?,?,?,?)",
                   (action, str(actor), json.dumps(detail, ensure_ascii=False), clock.time()))

    @staticmethod
    def _question(row):
        if row is None:
            raise QuizError("没有这道题")
        result = dict(row)
        result["options"] = json.loads(result["options"])
        return result

    def get_question(self, qid: str) -> dict:
        with self.connection() as db:
            return self._question(db.execute("SELECT * FROM questions WHERE qid=? AND state!='deleted'", (qid.upper(),)).fetchone())

    def questions(self, include_deleted=False) -> list[dict]:
        with self.connection() as db:
            where = "" if include_deleted else " WHERE state!='deleted'"
            return [self._question(row) for row in db.execute("SELECT * FROM questions" + where + " ORDER BY scheduled_at,seq")]

    def create_questions(self, payloads: list[dict], actor: str) -> list[dict]:
        settings = self.settings()
        normalized = [normalize_question(value, settings) for value in payloads]
        if not normalized or len(normalized) > 366:
            raise QuizError("一次导入必须有 1~366 道题")
        result = []
        with self.connection(True) as db:
            for question in normalized:
                row = {**question, "options": json.dumps(question["options"], ensure_ascii=False),
                       "created_by": str(actor), "created_at": clock.time(), "updated_at": clock.time()}
                keys = list(row)
                cur = db.execute(f"INSERT INTO questions({','.join(keys)}) VALUES({','.join('?' for _ in keys)})", list(row.values()))
                qid = f"Q{cur.lastrowid:04d}"
                db.execute("UPDATE questions SET qid=? WHERE seq=?", (qid, cur.lastrowid))
                self._audit(db, "create", actor, {"qid": qid})
                result.append({**question, "qid": qid, "state": "draft"})
        return result

    def edit_question(self, qid: str, changes: dict, actor: str):
        old = self.get_question(qid)
        if old["state"] != "draft":
            raise QuizError("题目发布后锁定，请通过调分留痕更正成绩")
        allowed = {"title", "options", "correct_answer", "points", "explanation", "image_path", "publish_date", "deadline"}
        if set(changes) - allowed:
            raise QuizError("包含不可修改的字段")
        updated = normalize_question({**old, **changes}, self.settings())
        with self.connection(True) as db:
            if db.execute("SELECT 1 FROM deliveries WHERE qid=? AND status!='pending'", (qid,)).fetchone():
                raise QuizError("题目已尝试发送，不能修改；请先核实群内消息")
            updated["options"] = json.dumps(updated["options"], ensure_ascii=False)
            assignments = ",".join(f"{key}=?" for key in updated)
            cur = db.execute(f"UPDATE questions SET {assignments},updated_at=? WHERE qid=? AND state='draft'",
                             [*updated.values(), clock.time(), qid])
            if not cur.rowcount:
                raise QuizError("题目已发布，不能修改")
            self._audit(db, "edit", actor, {"qid": qid, "changes": changes})
        return self.get_question(qid)

    def delete_question(self, qid, actor):
        with self.connection(True) as db:
            if db.execute("SELECT 1 FROM deliveries WHERE qid=? AND status!='pending'", (qid,)).fetchone():
                raise QuizError("题目已尝试发送，不能删除；请先核实群内消息")
            cur = db.execute("UPDATE questions SET state='deleted',updated_at=? WHERE qid=? AND state='draft'", (clock.time(), qid))
            if not cur.rowcount:
                raise QuizError("只能删除尚未发布的题目")
            self._audit(db, "delete", actor, {"qid": qid})

    def recover_deliveries(self):
        with self.connection(True) as db:
            for row in db.execute("SELECT * FROM deliveries WHERE status='sending'").fetchall():
                db.execute("INSERT OR IGNORE INTO alerts(key,detail) VALUES(?,?)", (
                    f"recovery:{row['qid']}:{row['group_id']}:{row['kind']}",
                    f"{row['qid']} 向群 {row['group_id']} 发送{row['kind']}期间进程退出，请核实后重试。"
                ))
            db.execute("UPDATE deliveries SET status='uncertain',error='进程在发送期间退出，请管理员核实后重试' WHERE status='sending'")

    def prepare_delivery(self, qid, groups, kind="question", retry=False):
        with self.connection(True) as db:
            question = self._question(db.execute("SELECT * FROM questions WHERE qid=? AND state!='deleted'", (qid,)).fetchone())
            if kind == "question" and question["state"] not in ("draft", "open"):
                raise QuizError("已结束的题目不能再发布")
            for gid in groups:
                db.execute("INSERT OR IGNORE INTO deliveries(qid,group_id,kind) VALUES(?,?,?)", (qid, str(gid), kind))
                if retry:
                    db.execute("UPDATE deliveries SET status='pending',error='' WHERE qid=? AND group_id=? AND kind=? AND status='uncertain'",
                               (qid, str(gid), kind))

    def claim_delivery(self, qid, gid, kind) -> bool:
        with self.connection(True) as db:
            return bool(db.execute("UPDATE deliveries SET status='sending',attempted_at=? WHERE qid=? AND group_id=? AND kind=? AND status='pending'",
                                   (clock.time(), qid, str(gid), kind)).rowcount)

    def finish_delivery(self, qid, gid, kind, message_id=None, error="", now=None):
        now = clock.time() if now is None else now
        with self.connection(True) as db:
            db.execute("UPDATE deliveries SET status=?,message_id=?,completed_at=?,error=? WHERE qid=? AND group_id=? AND kind=?",
                       ("uncertain" if error else "sent", str(message_id) if message_id is not None else None,
                        now, error, qid, str(gid), kind))
            if not error and kind == "question":
                db.execute("UPDATE questions SET state='open',updated_at=? WHERE qid=? AND state='draft'", (now, qid))

    def deliveries(self, qid, kind="question"):
        with self.connection() as db:
            return [dict(row) for row in db.execute("SELECT * FROM deliveries WHERE qid=? AND kind=? ORDER BY group_id", (qid, kind))]

    def active_questions(self, now=None):
        now = clock.time() if now is None else now
        with self.connection() as db:
            return [self._question(row) for row in db.execute("SELECT * FROM questions WHERE state='open' AND deadline_at>? ORDER BY scheduled_at", (now,))]

    def submit(self, qid, user_id, nickname, answer, bot_id, message_id, eligible_groups, now=None):
        now = clock.time() if now is None else now
        user_id, bot_id, message_id = str(user_id), str(bot_id), str(message_id)
        answer = answer.strip().upper()
        with self.connection(True) as db:
            replay = db.execute("SELECT * FROM answers WHERE bot_id=? AND user_id=? AND message_id=?", (bot_id, user_id, message_id)).fetchone()
            if replay:
                return {**dict(replay), "result": "replayed"}
            question = self._question(db.execute("SELECT * FROM questions WHERE qid=? AND state!='deleted'", (qid,)).fetchone())
            existing = db.execute("SELECT * FROM answers WHERE qid=? AND user_id=?", (qid, user_id)).fetchone()
            if existing:
                return {**dict(existing), "result": "duplicate"}
            settings = Settings(**json.loads(db.execute("SELECT value FROM meta WHERE key='settings'").fetchone()[0]))
            if not settings.enabled:
                raise QuizError("答题活动尚未开启")
            if question["state"] not in ("open", "closed"):
                raise QuizError("这道题还没有发布")
            late = now >= question["deadline_at"] or question["state"] == "closed"
            if late and not settings.late_ratio:
                raise QuizError("这题已经结束啦")
            if answer not in list("ABCDEF"[:len(question["options"])]):
                raise QuizError("答案必须是本题选项中的单个字母")
            delivered_groups = {row[0] for row in db.execute("SELECT group_id FROM deliveries WHERE qid=? AND kind='question' AND status='sent'", (qid,))}
            groups = sorted(delivered_groups & {str(g) for g in eligible_groups})
            if not groups:
                raise QuizError("需要先加入已发布本题且开启答题功能的活动群")
            correct = int(answer == question["correct_answer"])
            score = round(correct * question["points"] * (settings.late_ratio if late else 1), 4)
            nickname = str(nickname).replace("\n", " ").strip()[:80] or "未命名成员"
            row = dict(qid=qid, user_id=user_id, nickname=nickname, answer=answer, correct=correct,
                       score=score, submitted_at=now, bot_id=bot_id, message_id=message_id)
            db.execute("INSERT INTO answers VALUES(?,?,?,?,?,?,?,?,?)", list(row.values()))
            db.executemany("INSERT INTO answer_groups VALUES(?,?,?)", [(qid, user_id, gid) for gid in groups])
            if question["state"] == "closed":
                self._refresh_cloud(db)
            return {**row, "result": "accepted"}

    def close_question(self, qid, actor="system", now=None, force=False):
        now = clock.time() if now is None else now
        with self.connection(True) as db:
            question = self._question(db.execute("SELECT * FROM questions WHERE qid=?", (qid,)).fetchone())
            if question["state"] == "closed":
                return False
            if question["state"] != "open":
                raise QuizError("这道题还没有发布")
            if not force and now < question["deadline_at"]:
                raise QuizError("题目尚未到截止时间")
            db.execute("UPDATE questions SET state='closed',deadline_at=MIN(deadline_at,?),updated_at=? WHERE qid=?", (now, now, qid))
            self._audit(db, "close", actor, {"qid": qid})
            self._refresh_cloud(db)
            return True

    def answer_stats(self, qid):
        with self.connection() as db:
            return dict(db.execute("SELECT COUNT(*) AS total,COALESCE(SUM(correct),0) AS correct FROM answers WHERE qid=?", (qid,)).fetchone())

    @staticmethod
    def _rankings(db, group_id=None):
        scope, args = "", []
        if group_id is not None:
            scope = " AND EXISTS(SELECT 1 FROM answer_groups g WHERE g.qid=a.qid AND g.user_id=a.user_id AND g.group_id=?)"
            args = [str(group_id)]
        rows = db.execute("""SELECT a.user_id, SUM(a.score) AS total, SUM(a.correct) AS correct,
            COUNT(*) AS answered, COUNT(DISTINCT q.publish_date) AS days, MIN(a.submitted_at) AS first_at,
            MAX(a.submitted_at) AS last_at FROM answers a JOIN questions q ON q.qid=a.qid
            WHERE q.state='closed'""" + scope + " GROUP BY a.user_id", args).fetchall()
        result = []
        for row in rows:
            data = dict(row)
            data["nickname"] = db.execute("SELECT nickname FROM answers WHERE user_id=? ORDER BY submitted_at DESC LIMIT 1", (data["user_id"],)).fetchone()[0]
            extra = db.execute("SELECT COALESCE(SUM(delta),0) FROM adjustments WHERE user_id=? AND qid IS NULL", (data["user_id"],)).fetchone()[0]
            data["total"] = round(data["total"] + extra, 4)
            result.append(data)
        return sorted(result, key=lambda value: (-value["total"], -value["correct"], value["first_at"], value["user_id"]))

    def rankings(self, group_id=None):
        with self.connection() as db:
            return self._rankings(db, group_id)

    def personal(self, user_id):
        user_id = str(user_id)
        with self.connection() as db:
            rank = next((row for row in self._rankings(db) if row["user_id"] == user_id),
                        dict(user_id=user_id, total=0, correct=0, answered=0, days=0))
            rank["pending"] = db.execute("SELECT COUNT(*) FROM answers a JOIN questions q ON q.qid=a.qid WHERE user_id=? AND q.state='open'", (user_id,)).fetchone()[0]
            rank["details"] = [dict(row) for row in db.execute("SELECT a.*,q.state FROM answers a JOIN questions q ON q.qid=a.qid WHERE user_id=? ORDER BY a.qid", (user_id,))]
            return rank

    def adjust(self, user_id, value, reason, actor, qid=None):
        if not reason.strip():
            raise QuizError("调分必须填写原因")
        value = float(value)
        if not math.isfinite(value) or abs(value) > 10000:
            raise QuizError("调分数值无效")
        with self.connection(True) as db:
            if qid:
                row = db.execute("SELECT a.score,q.state,q.points FROM answers a JOIN questions q ON q.qid=a.qid WHERE a.qid=? AND user_id=?", (qid, str(user_id))).fetchone()
                if not row or row["state"] != "closed":
                    raise QuizError("只能调整已结算且存在答题记录的题目")
                if not 0 <= value <= row["points"]:
                    raise QuizError("单题得分必须在 0 和该题分值之间")
                delta = value - row["score"]
                db.execute("UPDATE answers SET score=? WHERE qid=? AND user_id=?", (value, qid, str(user_id)))
            else:
                if not any(row["user_id"] == str(user_id) for row in self._rankings(db)):
                    raise QuizError("该成员尚无已结算成绩")
                delta = value
            db.execute("INSERT INTO adjustments(user_id,qid,delta,reason,actor,created_at) VALUES(?,?,?,?,?,?)",
                       (str(user_id), qid, delta, reason.strip(), str(actor), clock.time()))
            self._audit(db, "adjust", actor, {"user_id": str(user_id), "qid": qid, "delta": delta, "reason": reason})
            self._refresh_cloud(db)

    @staticmethod
    def _enqueue(db, kind, key, payload):
        encoded = json.dumps(payload, ensure_ascii=False)
        old = db.execute("SELECT payload FROM cloud_rows WHERE kind=? AND record_key=?", (kind, key)).fetchone()
        if old:
            if old[0] != encoded:
                db.execute("UPDATE cloud_rows SET payload=?,version=version+1 WHERE kind=? AND record_key=?", (encoded, kind, key))
        else:
            row = db.execute("SELECT COALESCE(MAX(row_number),1)+1 FROM cloud_rows WHERE kind=?", (kind,)).fetchone()[0]
            db.execute("INSERT INTO cloud_rows(kind,record_key,row_number,payload) VALUES(?,?,?,?)", (kind, key, row, encoded))

    @classmethod
    def _refresh_cloud(cls, db):
        for row in cls._rankings(db):
            cls._enqueue(db, "summary", row["user_id"], [row["nickname"], row["total"], row["correct"], row["days"], row["last_at"]])
        for row in db.execute("SELECT a.* FROM answers a JOIN questions q ON q.qid=a.qid WHERE q.state='closed'"):
            cls._enqueue(db, "details", f"{row['qid']}:{row['user_id']}",
                         [row["qid"], row["nickname"], row["answer"], bool(row["correct"]), row["score"], row["submitted_at"]])

    def pending_cloud(self, limit=200):
        with self.connection() as db:
            result = [dict(row) for row in db.execute("SELECT * FROM cloud_rows WHERE version>synced_version ORDER BY kind,row_number LIMIT ?", (limit,))]
            for row in result:
                row["payload"] = json.loads(row["payload"])
            return result

    def mark_cloud_synced(self, kind, key, version):
        with self.connection(True) as db:
            db.execute("UPDATE cloud_rows SET synced_version=MAX(synced_version,?) WHERE kind=? AND record_key=? AND version>=?",
                       (version, kind, key, version))

    def alert(self, key, detail):
        with self.connection(True) as db:
            db.execute("INSERT OR IGNORE INTO alerts(key,detail) VALUES(?,?)", (key, detail))

    def pending_alerts(self):
        with self.connection() as db:
            return [dict(row) for row in db.execute("SELECT * FROM alerts WHERE notified_at IS NULL")]

    def mark_alert(self, key):
        with self.connection(True) as db:
            db.execute("UPDATE alerts SET notified_at=? WHERE key=?", (clock.time(), key))

    def clear_alert(self, key):
        with self.connection(True) as db:
            db.execute("DELETE FROM alerts WHERE key=?", (key,))

    def audit(self):
        with self.connection() as db:
            return [dict(row) for row in db.execute("SELECT * FROM audit ORDER BY id")]
