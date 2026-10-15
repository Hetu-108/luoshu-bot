"""JSON and spreadsheet imports; no model invents questions or explanations."""
from datetime import date, datetime
import json
from pathlib import Path
import re
import zipfile
from .store import QuizError

ALIASES = {
    "题干": "title", "题目": "title", "正确答案": "correct_answer", "答案": "correct_answer",
    "分值": "points", "解析": "explanation", "图片": "image", "图片地址": "image",
    "计划发布日期": "publish_date", "发布日期": "publish_date", "日期": "publish_date", "截止时间": "deadline",
}


def parse_options(text: str) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return [re.sub(r"^[A-Fa-f][.、:：)）]\s*", "", line) for line in lines]


def normalize_import_row(row: dict) -> dict:
    result = {ALIASES.get(str(key).strip(), str(key).strip()): value for key, value in row.items() if value is not None}
    if "options" not in result:
        labels = [key for key in "ABCDEF" if key in result and str(result[key]).strip()]
        if labels != list("ABCDEF"[:len(labels)]):
            raise QuizError("选项列必须从 A 开始连续排列")
        result["options"] = [result.pop(key) for key in labels]
    elif isinstance(result["options"], str):
        result["options"] = json.loads(result["options"]) if result["options"].lstrip().startswith("[") else parse_options(result["options"])
    value = result.get("publish_date")
    if isinstance(value, datetime):
        result["publish_date"] = value.date().isoformat()
    elif isinstance(value, date):
        result["publish_date"] = value.isoformat()
    if isinstance(result.get("deadline"), datetime):
        result["deadline"] = result["deadline"].isoformat()
    return result


def load_questions(path: Path) -> list[dict]:
    path = Path(path)
    if path.stat().st_size > 10 * 1024 * 1024:
        raise QuizError("题库文件不能超过 10MB")
    if path.suffix.lower() == ".json":
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(raw, dict):
            raw = raw.get("questions", [])
    elif path.suffix.lower() == ".xlsx":
        import openpyxl
        with zipfile.ZipFile(path) as package:
            if sum(info.file_size for info in package.infolist()) > 40 * 1024 * 1024:
                raise QuizError("Excel 解压后不能超过40MB")
        book = openpyxl.load_workbook(path, read_only=True, data_only=False)
        try:
            rows = iter(book.active.iter_rows(values_only=True))
            headers = next(rows, ())
            raw = []
            for values in rows:
                if any(value is not None for value in values):
                    raw.append(dict(zip(headers, values)))
                if len(raw) > 366:
                    raise QuizError("一次最多导入 366 道题")
        finally:
            book.close()
    else:
        raise QuizError("仅支持 .json 或 .xlsx 题库")
    if not isinstance(raw, list) or not 1 <= len(raw) <= 366 or any(not isinstance(row, dict) for row in raw):
        raise QuizError("题库必须包含 1~366 个题目对象")
    return [normalize_import_row(row) for row in raw]


async def prepare_images(rows, service, base_dir: Path):
    result = []
    for row in rows:
        row = dict(row)
        image = str(row.pop("image", "") or "").strip()
        if image:
            if image.startswith(("http://", "https://")):
                row["image_path"] = await service.archive_url(image)
            else:
                path = (base_dir / image).resolve()
                if not path.is_relative_to(base_dir.resolve()) or not path.is_file():
                    raise QuizError("导入图片必须位于题库文件所在目录内")
                if path.stat().st_size > 8 * 1024 * 1024:
                    raise QuizError("图片不能超过 8MB")
                row["image_path"] = service.archive_image(path.read_bytes())
        result.append(row)
    return result
