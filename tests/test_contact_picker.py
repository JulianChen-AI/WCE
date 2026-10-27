import base64
import http.client
import json
import os
from pathlib import Path
import sys
import tempfile
import threading

SOURCE = Path(sys.argv[1]).resolve() if len(sys.argv)>1 else Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / 'scripts/multimodal'))
import launcher
import portable
import sqlcipher3


def exercise(home):
    os.environ['CHATKIT_USER_DIR'] = str(home / 'user')
    root = home / 'wxid_me_abcd/db_storage'
    key, aux = 'a' * 64, 'b' * 64
    def db(relative, secret):
        p = root / relative; p.parent.mkdir(parents=True, exist_ok=True)
        c = sqlcipher3.connect(str(p)); c.execute('PRAGMA key = "x\'' + secret + '\'"'); c.execute('PRAGMA cipher_compatibility=4')
        return c
    c = db('contact/contact.db', key)
    c.execute('CREATE TABLE contact(username TEXT,nick_name TEXT,remark TEXT,verify_flag INTEGER DEFAULT 0)')
    c.executemany('INSERT INTO contact(username,nick_name,remark) VALUES(?,?,?)', [('wxid_me','自己',''),('wxid_old','旧联系人',''),('wxid_recent','测试小林','项目同事'),('123@chatroom','',''),('wxid_unsafe','<img onerror=alert(1)>','')]);c.commit();c.close()
    c = sqlcipher3.connect(str(root / 'contact/contact.db'));c.execute('PRAGMA key = "x\'' + key + '\'"');c.execute('PRAGMA cipher_compatibility=4')
    c.executemany('INSERT INTO contact(username,nick_name,remark,verify_flag) VALUES(?,?,?,?)', [
        ('gh_subscription','合成订阅号','',8),('legacy_service','合成服务号','',24),
        ('gh_unknown','合成未分类公众号','',0),('gh_enterprise','合成机构号','',8),
        ('verified_business','合成已认证机构','',8)])
    c.execute('CREATE TABLE biz_info(username TEXT,type INTEGER)')
    c.executemany('INSERT INTO biz_info VALUES(?,?)',[('gh_subscription',0),('legacy_service',1),('gh_enterprise',3)]);c.commit();c.close()
    c = db('session/session.db', aux)
    c.execute('CREATE TABLE SessionTable(username TEXT,last_timestamp INTEGER)');c.executemany('INSERT INTO SessionTable VALUES(?,?)',[('wxid_recent',1792000000),('123@chatroom',1791000000)]);c.commit();c.close()
    png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')
    c=db('head_image/head_image.db',aux);c.execute('CREATE TABLE head_image(username TEXT,image_buffer BLOB)');c.executemany('INSERT INTO head_image VALUES(?,?)',[('wxid_recent',png),('wxid_unsafe',b'<svg onload=alert(1)>')]);c.commit();c.close()
    portable.save_settings(dict(db_storage=str(root),platform='windows',own_id='wxid_me',output_dir=str(home/'archives'),transcribe=False),[key,aux])
    server=launcher.ThreadingHTTPServer(('127.0.0.1',0),launcher.Handler);launcher.BASE='http://127.0.0.1:'+str(server.server_port)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    if '--serve' in sys.argv:
        print(launcher.BASE,flush=True)
        threading.Event().wait()
    def get(path, host=None):
        c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=10);c.request('GET',path,headers={'Host':host} if host else {});r=c.getresponse();status=r.status;kind=r.getheader('Content-Type');body=r.read();c.close();return status,kind,body
    try:
        _,_,body=get('/api/contacts');contacts=json.loads(body)['contacts']
        _,_,body=get('/api/groups');groups=json.loads(body)['groups']
        accounts_code,_,body=get('/api/accounts');accounts=json.loads(body).get('accounts',[]) if accounts_code==200 else []
        code,mime,avatar=get('/api/avatar/wxid_recent')
        _,_,body=get('/');html=body.decode()
        return dict(simple_connection_ui='id="connection-card"' in html,advanced_not_forced_open="$('setup-panel').open=!d.configured" not in html,guided_connect="async function connectWechat" in html,private_progress_ui='id="progress-details"' in html,account_types={i['id']:i['kind'] for i in accounts},contact_count=len(contacts),order=[i['id'] for i in contacts],has_history={i['id']:i.get('has_history') for i in contacts},group_name=groups[0]['name'],
                    avatar_http=code,avatar_mime=mime,avatar_bytes_match=avatar==png,missing_avatar_http=get('/api/avatar/unknown')[0],unsafe_avatar_http=get('/api/avatar/wxid_unsafe')[0],foreign_host=get('/api/avatar/wxid_recent','attacker.invalid')[0],
                    avatar_ui="className='avatar'" in html,duplicate_id_ui="name.textContent=g.name+' · '+g.id" in html,idle_rebuild="append(a);}render();" in html)
    finally:server.shutdown();server.server_close()

if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='synthetic-',dir=Path(__file__).parent) as temp:
        observed=exercise(Path(temp))
        print(json.dumps(observed,ensure_ascii=False,sort_keys=True))
        assert observed['simple_connection_ui'] and observed['advanced_not_forced_open'] and observed['guided_connect'] and observed['private_progress_ui'], 'Technical setup is still the primary onboarding'
        assert observed['contact_count']==3, 'Public/service accounts leaked into ordinary contacts'
        assert observed['account_types']=={'wxid_old':'contact','wxid_recent':'contact','wxid_unsafe':'contact','gh_subscription':'official','legacy_service':'service','gh_unknown':'other','gh_enterprise':'other','verified_business':'other'}
        assert observed['avatar_http']==200 and observed['avatar_bytes_match']
        assert observed['avatar_mime']=='image/png'
        assert observed['missing_avatar_http']==404 and observed['unsafe_avatar_http']==404
        assert observed['foreign_host']==403
        assert observed['order'][0]=='wxid_recent'
        assert observed['has_history']=={'wxid_old':False,'wxid_recent':True,'wxid_unsafe':False}
        assert observed['group_name']=='未命名群聊'
        assert observed['avatar_ui'] and not observed['duplicate_id_ui'] and not observed['idle_rebuild']
