from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

import httpx
import openpyxl
import pytest
from src.quiz.store import QuizStore, QuizError, Settings
from src.quiz.service import QuizService
from src.quiz.cloud import CloudSync, TencentDocsSink
from src.quiz.importer import load_questions, prepare_images
from src.quiz.exporter import export_scores


@pytest.fixture
def quiz(tmp_path):
    store = QuizStore(tmp_path / "quiz" / "state.sqlite3")
    store.configure({"enabled": True, "groups": ["123456", "654321"]}, "admin")
    return QuizService(store)


def create(quiz, **overrides):
    return quiz.create([dict(title="太阳属于哪类天体？", options=["恒星", "行星"], correct_answer="A",
                             publish_date="2026-10-15", explanation="太阳是一颗恒星。", **overrides)], "admin")[0]


def opened(quiz):
    q = create(quiz)
    quiz.store.prepare_delivery(q["qid"], ["123456", "654321"])
    for gid in ("123456", "654321"):
        assert quiz.store.claim_delivery(q["qid"], gid, "question")
        quiz.store.finish_delivery(q["qid"], gid, "question", 100, now=q["scheduled_at"])
    return quiz.store.get_question(q["qid"])


def submit(quiz, q, uid="55555555", mid="100", answer="A", now=None):
    return quiz.store.submit(q["qid"], uid, "测试成员", answer, "10000", mid,
                             ["123456", "654321"], now=q["scheduled_at"] + 60 if now is None else now)


def test_defaults_and_timezone(quiz):
    settings = Settings()
    assert not settings.enabled and settings.publish_time == "20:00" and settings.late_ratio == 0
    q = create(quiz)
    assert q["deadline_at"] - q["scheduled_at"] == 86400
    from datetime import datetime, timezone
    assert datetime.fromtimestamp(q["scheduled_at"], timezone.utc).hour == 12
    with pytest.raises(QuizError):
        Settings(groups="123456")


def test_no_result_leaks_before_deadline(quiz):
    q = opened(quiz)
    assert submit(quiz, q)["score"] == 1
    assert quiz.store.rankings() == []
    assert quiz.store.personal("55555555")["total"] == 0
    assert quiz.store.personal("55555555")["pending"] == 1
    assert quiz.store.pending_cloud() == []
    quiz.store.close_question(q["qid"], now=q["deadline_at"])
    assert quiz.store.rankings()[0]["total"] == 1
    assert len(quiz.store.pending_cloud()) == 2
    for row in quiz.store.pending_cloud():
        assert "55555555" not in json.dumps(row["payload"])


def test_duplicate_and_replayed_messages(quiz):
    q = opened(quiz)
    assert submit(quiz, q)["result"] == "accepted"
    assert submit(quiz, q)["result"] == "replayed"
    assert submit(quiz, q, mid="101", answer="B")["result"] == "duplicate"
    assert quiz.store.answer_stats(q["qid"])["total"] == 1


def test_transaction_prevents_racing_duplicate_answers(quiz):
    q = opened(quiz)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda mid: submit(quiz, q, mid=str(mid)), range(20)))
    assert sum(result["result"] == "accepted" for result in results) == 1
    assert quiz.store.answer_stats(q["qid"])["total"] == 1


def test_many_users_and_restart_preserve_scores(quiz):
    q = opened(quiz)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda uid: submit(quiz, q, uid=str(uid), mid=str(uid)), range(100, 140)))
    quiz.store.close_question(q["qid"], now=q["deadline_at"])
    reopened = QuizStore(quiz.store.path)
    assert len(reopened.rankings()) == 40
    assert all(row["total"] == 1 for row in reopened.rankings())
    assert len(reopened.rankings("123456")) == 40
    assert len(reopened.rankings("999")) == 0


@pytest.mark.parametrize("problem", ["draft", "late", "option", "membership", "disabled"])
def test_invalid_submission_does_not_count(quiz, problem):
    q = create(quiz) if problem == "draft" else opened(quiz)
    if problem == "disabled":
        quiz.store.configure({"enabled": False}, "admin")
    with pytest.raises(QuizError):
        quiz.store.submit(q["qid"], "123", "成员", "F" if problem == "option" else "A", "10000", "1",
                          [] if problem == "membership" else ["123456"],
                          now=q["deadline_at"] if problem == "late" else q["scheduled_at"] + 1)
    assert quiz.store.answer_stats(q["qid"])["total"] == 0


def test_adjustment_is_audited_and_outbox_versioned(quiz):
    q = opened(quiz)
    submit(quiz, q, answer="B")
    quiz.store.close_question(q["qid"], now=q["deadline_at"])
    old = quiz.store.pending_cloud()
    quiz.store.adjust("55555555", 1, "人工核对", "admin", q["qid"])
    for row in old:
        quiz.store.mark_cloud_synced(row["kind"], row["record_key"], row["version"])
    assert len(quiz.store.pending_cloud()) == 2
    assert quiz.store.rankings()[0]["total"] == 1
    assert quiz.store.audit()[-1]["action"] == "adjust"


async def test_delivery_is_idempotent_and_multigroup(quiz):
    q = create(quiz)
    sent = []
    async def send(gid, kind, question):
        sent.append((gid, kind))
        return {"message_id": len(sent)}
    assert await quiz.publish(q["qid"], send, q["scheduled_at"] + 1) == 2
    assert await quiz.publish(q["qid"], send, q["scheduled_at"] + 1) == 0
    await quiz.reveal(q["qid"], send, now=q["deadline_at"])
    await quiz.reveal(q["qid"], send, now=q["deadline_at"])
    assert sorted(kind for _, kind in sent) == ["answer", "answer", "question", "question"]


async def test_restart_misfire_and_empty_schedule_alert(quiz):
    q = create(quiz)
    sent = []
    async def send(gid, kind, question):
        sent.append((gid, kind))
        return 1
    await quiz.tick(send, q["scheduled_at"] + 3600)
    assert len(sent) == 2
    await quiz.tick(send, q["deadline_at"])
    assert len(sent) == 4
    assert any("没有排期" in row["detail"] for row in quiz.store.pending_alerts())


async def test_uncertain_send_requires_explicit_retry(quiz):
    q = create(quiz)
    async def failing(*args):
        raise TimeoutError("unknown")
    assert await quiz.publish(q["qid"], failing, q["scheduled_at"]) == 0
    calls = []
    async def send(*args):
        calls.append(True)
        return 1
    await quiz.publish(q["qid"], send, q["scheduled_at"] + 1)
    assert calls == []
    await quiz.publish(q["qid"], send, q["scheduled_at"] + 2, retry=True)
    assert len(calls) == 2


def test_interrupted_delivery_recovered_and_alerted(quiz):
    q = create(quiz)
    quiz.store.prepare_delivery(q["qid"], ["123456"])
    quiz.store.claim_delivery(q["qid"], "123456", "question")
    quiz.store.recover_deliveries()
    assert quiz.store.deliveries(q["qid"])[0]["status"] == "uncertain"
    assert quiz.store.pending_alerts()


def test_invalid_batch_is_atomic_and_published_content_locked(quiz):
    good = dict(title="题干", options=["甲", "乙"], correct_answer="A", publish_date="2026-10-15")
    with pytest.raises(QuizError):
        quiz.create([good, {**good, "correct_answer": "Z"}], "admin")
    assert quiz.store.questions() == []
    q = opened(quiz)
    with pytest.raises(QuizError):
        quiz.edit(q["qid"], {"correct_answer": "B"}, "admin")


async def test_import_xlsx_and_archive_local_image(quiz, tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    (source / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"data")
    workbook = openpyxl.Workbook()
    workbook.active.append(["题干", "A", "B", "答案", "日期", "图片"])
    workbook.active.append(["题干", "甲", "乙", "A", "2026-10-15", "image.png"])
    path = source / "questions.xlsx"
    workbook.save(path)
    rows = await prepare_images(load_questions(path), quiz, source)
    questions = quiz.create(rows, "admin")
    assert quiz.image_path(questions[0]["image_path"]).read_bytes().startswith(b"\x89PNG")
    with pytest.raises(QuizError):
        await prepare_images([{**rows[0], "image": "../../private.png"}], quiz, source)


async def test_cloud_failure_keeps_jobs_and_retry_is_stable(quiz):
    q = opened(quiz)
    submit(quiz, q)
    quiz.store.close_question(q["qid"], now=q["deadline_at"])
    writes = []
    class Sink:
        fail = True
        async def write(self, kind, row, values):
            writes.append((kind, row, values))
            if self.fail and row > 1:
                raise OSError("offline")
    sink = Sink()
    worker = CloudSync(quiz.store, sink)
    with pytest.raises(OSError):
        await worker.sync()
    assert len(quiz.store.pending_cloud()) == 2
    sink.fail = False
    assert await worker.sync() == 2
    assert quiz.store.pending_cloud() == []
    assert await worker.sync() == 0
    assert {row for _, row, _ in writes if row > 1} == {2}


async def test_tencent_contract_and_csv_formula_escape(quiz, tmp_path):
    captured = []
    def transport(request):
        captured.append(request)
        return httpx.Response(200, json={"ret": 0})
    sink = TencentDocsSink("book", "summary", "details", "client", "open", "secret", transport=httpx.MockTransport(transport))
    await sink.write("summary", 2, ["=CMD()", 1, 1, 1, "2026-10-15"])
    assert json.loads(captured[0].content)["values"][0][0] == "'=CMD()"
    q = opened(quiz)
    submit(quiz, q)
    quiz.store.close_question(q["qid"], now=q["deadline_at"])
    paths = export_scores(quiz.store, tmp_path / "exports")
    assert len(paths) == 3 and all(path.is_file() for path in paths)


def test_export_does_not_include_pending_correctness(quiz, tmp_path):
    q = opened(quiz)
    submit(quiz, q)
    paths = export_scores(quiz.store, tmp_path / 'exports')
    assert 'Q0001' not in paths[1].read_text(encoding='utf-8-sig')


async def test_cloud_requires_explicit_success(quiz):
    sink = TencentDocsSink('book', 'summary', 'details', 'client', 'open', 'secret',
                          transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})))
    with pytest.raises(QuizError):
        await sink.write('summary', 2, ['昵称', 1, 1, 1, '2026-10-15'])


def test_import_rejects_gapped_options():
    from src.quiz.importer import normalize_import_row
    with pytest.raises(QuizError):
        normalize_import_row({'A': '恒星', 'C': '行星'})
