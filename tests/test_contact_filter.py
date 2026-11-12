"""Regression: non-friend group members (local_type 3) must not flood the contact picker."""
import os
from pathlib import Path
import sys
import tempfile

SOURCE = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / 'scripts/multimodal'))
import launcher
import portable
import sqlcipher3


def check(home):
    os.environ['CHATKIT_USER_DIR'] = str(home / 'user')
    root = home / 'wxid_me_abcd/db_storage'
    key, aux = 'a' * 64, 'b' * 64

    def db(relative, secret):
        p = root / relative
        p.parent.mkdir(parents=True, exist_ok=True)
        c = sqlcipher3.connect(str(p))
        c.execute('PRAGMA key = "x\'' + secret + '\'"')
        c.execute('PRAGMA cipher_compatibility=4')
        return c

    c = db('contact/contact.db', key)
    c.execute('CREATE TABLE contact(username TEXT,local_type INTEGER,nick_name TEXT,remark TEXT,verify_flag INTEGER DEFAULT 0)')
    c.executemany('INSERT INTO contact(username,local_type,nick_name,remark) VALUES(?,?,?,?)', [
        ('wxid_me', 1, '自己', ''),
        ('wxid_friend', 1, '好友甲', ''),
        ('custom_friend', 1, '好友乙', '备注乙'),
        ('gh_followed', 1, '合成订阅号', ''),
        ('wxid_member_a', 3, '群成员�\x01', ''),
        ('wxid_member_b', 3, '陌生群成员', ''),
        ('wxid_member_chat', 3, '私聊过的陌生人', ''),
        ('gh_unfollowed', 0, '已取关公众号', ''),
        ('x@openim', 6, '企业微信群成员', ''),
        ('123@chatroom', 2, '项目群', ''),
        ('456@chatroom', 0, '旧群', ''),
    ])
    c.commit()
    c.close()
    c = db('session/session.db', aux)
    c.execute('CREATE TABLE SessionTable(username TEXT,last_timestamp INTEGER)')
    c.executemany('INSERT INTO SessionTable VALUES(?,?)', [('wxid_friend', 1792000000), ('wxid_member_chat', 1791000000)])
    c.commit()
    c.close()
    portable.save_settings(dict(db_storage=str(root), platform='mac', own_id='wxid_me',
                                output_dir=str(home / 'archives'), transcribe=False), [key, aux])

    contacts = {i['id'] for i in launcher.contacts()}
    accounts = {i['id'] for i in launcher.accounts()}
    groups = {i['id'] for i in launcher.groups()}
    print(dict(contacts=sorted(contacts), accounts=sorted(accounts), groups=sorted(groups)))
    assert contacts == {'wxid_friend', 'custom_friend', 'wxid_member_chat'}, 'non-friend group members leaked into contacts'
    assert 'gh_followed' in accounts and 'gh_unfollowed' not in accounts and 'x@openim' not in accounts
    assert groups == {'123@chatroom', '456@chatroom'}, 'group list must not be filtered by local_type'
    print('PASS: only friends, followed accounts and real 1:1 chats are listed')


def test_contact_filter():
    with tempfile.TemporaryDirectory() as temp:
        check(Path(temp))


if __name__ == '__main__':
    test_contact_filter()
