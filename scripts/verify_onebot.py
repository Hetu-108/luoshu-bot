"""No QQ connection: exercise the real ASGI app through a local fake OneBot."""
import asyncio
from contextlib import closing
from datetime import datetime
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from zoneinfo import ZoneInfo

import websockets


async def main():
    root = Path(__file__).resolve().parents[1]
    if len(sys.argv) > 1:
        root = Path(sys.argv[1]).resolve()
    report_path = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else root.parent / 'onebot-probe.json'
    report = {'checks': [], 'simulated_onebot': True}
    with tempfile.TemporaryDirectory(prefix='luoshu-onebot-') as temporary:
        data = Path(temporary)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        env = {**os.environ, 'HOST': '127.0.0.1', 'PORT': str(port), 'DATA_DIR': str(data),
               'SUPERUSERS': '["10000000"]', 'FAMILIAR_USERS': '[]', 'GREETING_ENABLED': 'false',
               'ONEBOT_ACCESS_TOKEN': 'local-probe-only', 'QUIZ_CLOUD_ENABLED': 'false',
               'OLLAMA_URL': 'http://127.0.0.1:11435', 'OLLAMA_MODEL': 'qwen3.5:9b',
               'OLLAMA_THINK': 'false', 'AI_DEFAULT_PERSONA': '督师', 'AI_CONTEXT_SIZE': '8192',
               'AI_NUM_PREDICT': '128', 'AI_MAX_CONCURRENT': '1', 'FASTAPI_RELOAD': 'false'}
        # NoneBot reads custom fields only when they also occur in a dotenv file.
        profile = 'probe-' + uuid.uuid4().hex
        env['ENVIRONMENT'] = profile
        dotenv = root / ('.env.' + profile)
        dotenv.write_text('\n'.join(key + '=' + env[key] for key in (
            'DATA_DIR', 'FAMILIAR_USERS', 'GREETING_ENABLED', 'QUIZ_CLOUD_ENABLED',
            'OLLAMA_URL', 'OLLAMA_MODEL', 'OLLAMA_THINK', 'AI_DEFAULT_PERSONA',
            'AI_CONTEXT_SIZE', 'AI_NUM_PREDICT', 'AI_MAX_CONCURRENT', 'ONEBOT_ACCESS_TOKEN')),
            encoding='utf-8')
        dotenv.chmod(0o600)
        with (data / 'server.log').open('w', encoding='utf-8') as log:
            process = subprocess.Popen([sys.executable, 'bot.py'], cwd=root, env=env, stdout=log, stderr=log)
            try:
                for _ in range(100):
                    if process.poll() is not None:
                        raise RuntimeError((data / 'server.log').read_text()[-4000:])
                    try:
                        reader, writer = await asyncio.open_connection('127.0.0.1', port)
                        writer.close()
                        await writer.wait_closed()
                        break
                    except OSError:
                        await asyncio.sleep(.1)
                async with websockets.connect(f'ws://127.0.0.1:{port}/onebot/v11/ws', additional_headers={
                    'X-Self-ID': '90000', 'X-Client-Role': 'Universal', 'Authorization': 'Bearer local-probe-only'}) as ws:
                    received = []
                    mid = 0
                    async def respond():
                        async for raw in ws:
                            request = json.loads(raw)
                            action, params = request['action'], request.get('params', {})
                            if action == 'get_group_member_info':
                                result = {'user_id': params['user_id'], 'nickname': '测试成员'} if params['user_id'] != 77777777 else None
                            elif action == 'get_login_info':
                                result = {'user_id': 90000, 'nickname': '督师'}
                            elif action == 'get_group_list':
                                result = [{'group_id': 123456, 'group_name': '模拟群'}]
                            else:
                                result = {'message_id': 900000 + len(received)}
                            if action in ('send_private_msg', 'send_group_msg', 'send_msg', 'upload_private_file'):
                                received.append({'action': action, 'params': params})
                            await ws.send(json.dumps({'status': 'ok', 'retcode': 0, 'data': result, 'echo': request.get('echo')}))
                    worker = asyncio.create_task(respond())
                    async def send(text, expected, uid=10000000, gid=None, replay=False, timeout=15):
                        nonlocal mid
                        if not replay:
                            mid += 1
                        before = len(received)
                        event = dict(time=int(time.time()), self_id=90000, post_type='message', message_id=mid,
                                     message_type='group' if gid else 'private', sub_type='normal' if gid else 'friend',
                                     user_id=uid, message=[{'type':'text','data':{'text':text}}], raw_message=text,
                                     font=0, sender={'user_id':uid, 'nickname':'测试成员','card':'','role':'member'})
                        if gid:
                            event['group_id'] = gid
                        await ws.send(json.dumps(event))
                        for _ in range(int(timeout * 20)):
                            if any(expected in json.dumps(row, ensure_ascii=False) for row in received[before:]):
                                report['checks'].append({'input':text[:90], 'expected':expected, 'passed':True})
                                return
                            await asyncio.sleep(.05)
                        raise AssertionError(f'{text}: missing {expected}; replies={received[before:]}')
                    await send('/出题', '仅机器人管理员', uid=55555555)
                    await send('/出题', '请私聊', gid=123456)
                    await send('/出题', '请发送题干')
                    await send('太阳属于哪类天体？', '请发送一张')
                    await send('跳过', '每行一个')
                    await send('A. 恒星\nB. 行星', '正确答案字母')
                    await send('A', '人工编写的解析')
                    await send('太阳是一颗恒星。', '计划发布日期')
                    await send(datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat(), '已保存 Q0001')
                    await send('/答题设置 {"enabled":true,"groups":["123456"]}', '已保存答题设置')
                    await send('/发题 Q0001', '本次成功发送 1 个群', gid=123456)
                    await send('ａ', '已记录 Q0001', uid=55555555)
                    await send('ａ', '已记录 Q0001', uid=55555555, replay=True)
                    await send('/答 Q0001 B', '首次提交', uid=55555555)
                    await send('/答 Q0001 A', '需要先加入', uid=77777777)
                    await send('/我的成绩', '待结算：1题', uid=55555555)
                    await send('/排行', '暂无已结算成绩', uid=55555555, gid=123456)
                    await send('/答案 Q0001', '本次成功发送 1 个群')
                    await send('/我的成绩', '已结算总分：1', uid=55555555)
                    await send('/排行', '1 分', uid=55555555, gid=123456)
                    await send('/导出', '已私聊发送汇总')
                    if os.environ.get('PROBE_MODEL') == '1':
                        await send('/ai 请用一句话向新成员问好', 'send_msg', uid=66666666, timeout=180)
                        session = json.loads((data / 'ai_sessions.json').read_text())
                        assert any(value.get('model') == 'qwen3.5:9b' and value.get('history') for value in session.values())
                    with closing(sqlite3.connect(data / 'quiz/state.sqlite3')) as db:
                        assert db.execute('SELECT COUNT(*) FROM answers').fetchone()[0] == 1
                        assert db.execute('SELECT SUM(score) FROM answers').fetchone()[0] == 1
                        assert db.execute('SELECT COUNT(*) FROM cloud_rows WHERE synced_version=0').fetchone()[0] == 2
                    report['answer_count'] = 1
                    report['closed_score'] = 1
                    report['pending_cloud_rows'] = 2
                    report['outgoing_api_calls'] = len(received)
                    worker.cancel()
                    await asyncio.gather(worker, return_exceptions=True)
            finally:
                dotenv.unlink(missing_ok=True)
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                report_path.parent.mkdir(parents=True, exist_ok=True)
                (report_path.parent / 'onebot-probe-server.log').write_text((data / 'server.log').read_text(errors='replace'), encoding='utf-8')
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


asyncio.run(main())
