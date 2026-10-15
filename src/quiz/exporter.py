import csv
from datetime import datetime
from pathlib import Path
from .cloud import safe_cell
from .service import format_time


def export_scores(store, destination: Path):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    suffix = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    summary = destination / f"summary-{suffix}.csv"
    details = destination / f"details-{suffix}.csv"
    audit = destination / f"audit-{suffix}.csv"
    with summary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["QQ", "昵称", "总分", "答对数", "已结算题数", "参与天数"])
        for row in store.rankings():
            writer.writerow([row["user_id"], safe_cell(row["nickname"]), row["total"], row["correct"], row["answered"], row["days"]])
    with details.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["题号", "QQ", "昵称", "答案", "是否正确", "得分", "提交时间", "状态"])
        with store.connection() as db:
            rows = db.execute("SELECT a.*,q.state FROM answers a JOIN questions q ON q.qid=a.qid WHERE q.state='closed' ORDER BY a.qid,a.submitted_at").fetchall()
        for row in rows:
            writer.writerow([row["qid"], row["user_id"], safe_cell(row["nickname"]), row["answer"],
                             row["correct"], row["score"], format_time(row["submitted_at"], store.settings().timezone), row["state"]])
    with audit.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["序号", "操作", "管理员QQ", "详情", "时间"])
        for row in store.audit():
            writer.writerow([row["id"], row["action"], row["actor"], safe_cell(row["detail"]), format_time(row["created_at"], store.settings().timezone)])
    return [summary, details, audit]
