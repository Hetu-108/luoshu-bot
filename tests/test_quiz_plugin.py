import time

import pytest
from nonebot.adapters.onebot.v11 import Message
from src.plugins import quiz as plugin
from src.quiz.service import QuizService
from src.quiz.store import QuizStore, QuizError


class Finished(Exception):
    pass


class Matcher:
    def __init__(self):
        self.messages = []

    async def finish(self, message):
        self.messages.append(str(message))
        raise Finished


@pytest.fixture
def state(tmp_path, monkeypatch):
    store = QuizStore(tmp_path / 'quiz' / 'state.sqlite3')
    service = QuizService(store)
    monkeypatch.setattr(plugin, 'store', store)
    monkeypatch.setattr(plugin, 'service', service)
    plugin._drafts.clear()
    plugin._deletions.clear()
    return store, service


def question(service):
    return service.create([dict(title='太阳是什么天体？', options=['恒星', '行星'], correct_answer='A',
                                publish_date='2099-01-01', explanation='太阳是一颗恒星。')], 'admin')[0]


async def test_permission_and_group_privacy(state, bot, event_factory, monkeypatch):
    matcher = Matcher()
    monkeypatch.setattr(plugin, 'admin_cmd', matcher)
    for event, expected in [(event_factory('/出题', gid=None), '仅机器人管理员'),
                            (event_factory('/出题', uid=10000000), '请私聊')]:
        with pytest.raises(Finished):
            await plugin._admin_handler(bot, event, ('出题',), Message())
        assert expected in matcher.messages[-1]
    assert state[0].questions() == []


async def test_wizard_back_cancel_and_save(state, bot, event_factory, monkeypatch):
    matcher = Matcher()
    monkeypatch.setattr(plugin, 'wizard', matcher)
    sent = []
    async def api(self, name, **data):
        sent.append((name, data))
        return {'message_id': 9}
    monkeypatch.setattr(type(bot), 'call_api', api)
    event = event_factory('', uid=10000000, gid=None)
    await plugin._admin_action(bot, event, '出题', Message())
    for text in ['太阳是什么天体？', '上一步', '太阳属于哪类天体？', '跳过', 'A. 恒星\nB. 行星', 'ａ', '太阳是一颗恒星。', '2099-01-01']:
        with pytest.raises(Finished):
            await plugin._wizard(bot, event_factory(text, uid=10000000, gid=None))
    assert state[0].questions()[0]['correct_answer'] == 'A'
    assert plugin._key(bot, event) not in plugin._drafts
    assert len(sent) == 1 and '仅管理员预览' in str(sent[0][1]['message'])
    await plugin._admin_action(bot, event, '出题', Message())
    with pytest.raises(Finished):
        await plugin._wizard(bot, event_factory('取消', uid=10000000, gid=None))
    assert not plugin._drafts


async def test_delete_requires_same_admin_confirmation(state, bot, event_factory):
    q = question(state[1])
    owner = event_factory('', uid=10000000, gid=None)
    await plugin._admin_action(bot, owner, '删题', Message(q['qid']))
    stranger = event_factory('', uid=20000000, gid=None)
    reply = await plugin._admin_action(bot, stranger, '删题', Message(q['qid'] + ' 确认'))
    assert '60秒' in reply and state[0].get_question(q['qid'])
    await plugin._admin_action(bot, owner, '删题', Message(q['qid'] + ' 确认'))
    with pytest.raises(QuizError):
        state[0].get_question(q['qid'])


async def test_private_answer_rule_and_receipt(state, bot, event_factory, monkeypatch):
    store, service = state
    q = question(service)
    store.configure({'enabled': True, 'groups': ['123456']}, 'admin')
    async def api(self, name, **data):
        return {'user_id': data['user_id']} if name == 'get_group_member_info' else {'message_id': 1}
    monkeypatch.setattr(type(bot), 'call_api', api)
    private = event_factory('ａ', gid=None)
    assert not await plugin._bare_rule()(bot, private, {})
    await service.publish(q['qid'], lambda *args: api(bot, 'send_group_msg'), now=time.time())
    context = {}
    assert await plugin._bare_rule()(bot, private, context)
    assert not await plugin._bare_rule()(bot, event_factory('A'), {})
    assert not await plugin._bare_rule()(bot, event_factory('正常聊天', gid=None), {})
    matcher = Matcher()
    with pytest.raises(Finished):
        await plugin._submit(matcher, bot, private, 'ａ', context['quiz_choices'])
    assert '已记录' in matcher.messages[-1] and '本题得分' not in matcher.messages[-1]
    assert store.personal(private.user_id)['total'] == 0
    with pytest.raises(Finished):
        await plugin._submit(matcher, bot, event_factory('B', gid=None, mid=2), 'B')
    assert '首次提交' in matcher.messages[-1]
    # Ambiguous bare letters must remain available to ordinary chat.
    another = question(service)
    await service.publish(another['qid'], lambda *args: api(bot, 'send_group_msg'), now=time.time())
    assert not await plugin._bare_rule()(bot, private, {})
    monkeypatch.setattr(service, 'group_enabled', lambda gid: False)
    with pytest.raises(QuizError, match='加入'):
        await plugin._submit(Matcher(), bot, event_factory('A', uid=44444444, gid=None), q['qid'] + ' A')


async def test_schedule_isolates_missing_image_and_empty_groups(state):
    store, service = state
    q = question(service)
    store.configure({'enabled': True}, 'admin')
    async def send(*args):
        return {'message_id': 1}
    await service.tick(send, now=q['scheduled_at'])
    assert store.pending_alerts()
    store.configure({'groups': ['123456']}, 'admin')
    service.edit(q['qid'], {'image_path': ''}, 'admin')
    good = question(service)
    with store.connection(True) as db:
        db.execute('UPDATE questions SET image_path=? WHERE qid=?', ('images/missing.png', q['qid']))
    await service.tick(send, now=q['scheduled_at'])
    assert store.get_question(good['qid'])['state'] == 'open'
    assert store.get_question(q['qid'])['state'] == 'draft'


def test_uncertain_delivery_locks_content_and_deleted_cannot_publish(state):
    store, service = state
    q = question(service)
    store.prepare_delivery(q['qid'], ['123456'])
    assert store.claim_delivery(q['qid'], '123456', 'question')
    store.finish_delivery(q['qid'], '123456', 'question', error='timeout')
    with pytest.raises(QuizError):
        service.edit(q['qid'], {'correct_answer': 'B'}, 'admin')
    with pytest.raises(QuizError):
        store.delete_question(q['qid'], 'admin')
    draft = question(service)
    store.delete_question(draft['qid'], 'admin')
    with pytest.raises(QuizError):
        store.prepare_delivery(draft['qid'], ['123456'])
