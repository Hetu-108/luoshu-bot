"""Tencent Docs v2 value writes with stable row addresses and a durable local outbox.

No credentials or document IDs are required until cloud sync is enabled.
"""
import asyncio
from urllib.parse import quote
import httpx
from .service import format_time
from .store import QuizError

HEADERS = {
    "summary": ["昵称", "总分", "答对数", "参与天数", "最近提交时间"],
    "details": ["题号", "昵称", "提交答案", "是否正确", "得分", "提交时间"],
}


def safe_cell(value):
    # Nicknames and explanations must never become spreadsheet formulas.
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


class TencentDocsSink:
    def __init__(self, file_id, summary_sheet, details_sheet, client_id, open_id, access_token, transport=None):
        if not all((file_id, summary_sheet, details_sheet, client_id, open_id, access_token)):
            raise QuizError("腾讯文档配置未完成，需要文件/子表 ID 与开放平台凭据")
        self.file_id = str(file_id)
        self.sheets = {"summary": str(summary_sheet), "details": str(details_sheet)}
        self.headers = {"Client-Id": client_id, "Open-Id": open_id, "Access-Token": access_token}
        self.transport = transport

    async def write(self, kind, row_number, values):
        end = "E" if kind == "summary" else "F"
        target_range = f"{self.sheets[kind]}!A{row_number}:{end}{row_number}"
        url = f"https://docs.qq.com/openapi/sheetbook/v2/{quote(self.file_id, safe='')}/values/{quote(target_range, safe='!:')}"
        async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=5), transport=self.transport) as client:
            response = await client.put(url, headers=self.headers, json={"values": [[safe_cell(value) for value in values]]})
            response.raise_for_status()
            result = response.json()
            if (not isinstance(result, dict) or not ({"ret", "code"} & result.keys())
                    or result.get("error") or result.get("ret", 0) != 0 or result.get("code", 0) != 0):
                raise QuizError("腾讯文档未确认写入成功")


class CloudSync:
    def __init__(self, store, sink=None):
        self.store, self.sink = store, sink
        self._lock = asyncio.Lock()

    async def sync(self):
        if self.sink is None:
            raise QuizError("腾讯文档尚未配置；成绩与待同步任务已保存在本地")
        async with self._lock:
            completed = 0
            try:
                for kind, values in HEADERS.items():
                    await self.sink.write(kind, 1, values)
                for row in self.store.pending_cloud():
                    values = list(row["payload"])
                    values[-1] = format_time(values[-1], self.store.settings().timezone)
                    if row["kind"] == "details":
                        values[3] = "是" if values[3] else "否"
                    await self.sink.write(row["kind"], row["row_number"], values)
                    self.store.mark_cloud_synced(row["kind"], row["record_key"], row["version"])
                    completed += 1
            except Exception:
                self.store.alert("cloud-sync", "腾讯文档同步失败，成绩仍保存在本地，将自动补传；请检查账号授权和子表配置。")
                raise
            self.store.clear_alert("cloud-sync")
            return completed
