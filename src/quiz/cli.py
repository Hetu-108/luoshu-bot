"""Local OS administrator tools: python -m src.quiz.cli --help."""
import argparse
import asyncio
from dataclasses import asdict
import json
from pathlib import Path

from .exporter import export_scores
from .importer import load_questions, prepare_images
from .service import QuizService
from .store import QuizStore


async def run(args):
    # Load the same plugin policy/configuration as the bot, without starting its server.
    import nonebot
    nonebot.init()
    nonebot.load_from_toml('pyproject.toml')
    from src.plugins.core import DATA_DIR
    from src.plugins.security import has_sensitive
    store = QuizStore(DATA_DIR / 'quiz' / 'state.sqlite3')
    service = QuizService(store, text_allowed=lambda value: not has_sensitive(value))
    actor = 'cli:' + args.actor
    if args.command == 'import':
        path = Path(args.file).resolve()
        rows = await prepare_images(load_questions(path), service, path.parent)
        result = service.create(rows, actor)
        print(json.dumps([q['qid'] for q in result], ensure_ascii=False))
    elif args.command == 'settings':
        result = store.configure(json.loads(args.json), actor) if args.json else store.settings()
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    elif args.command == 'list':
        print(json.dumps(store.questions(), ensure_ascii=False, indent=2))
    elif args.command == 'export':
        print('\n'.join(str(path) for path in export_scores(store, Path(args.directory))))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--actor', default='os-admin', help='审计操作人标签')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('import').add_argument('file')
    commands.add_parser('list')
    commands.add_parser('settings').add_argument('json', nargs='?')
    commands.add_parser('export').add_argument('directory')
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except (ValueError, OSError) as exc:
        parser.exit(1, f'未完成：{exc}\n')


if __name__ == '__main__':
    main()
