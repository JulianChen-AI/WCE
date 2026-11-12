"""Regression: voice transcription uses chat context, stays Simplified, reports honestly, can be redone."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

SOURCE = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
MULTI = SOURCE / 'scripts/multimodal'
sys.path[:0] = [str(MULTI), str(SOURCE / 'vendor/wechat-exporter-mac')]
import asr  # noqa: E402


class FakeModel:
    def __init__(self, fail=()):
        self.calls, self.fail = [], set(fail)

    def transcribe(self, path, **kw):
        self.calls.append((Path(path).name, kw))
        if Path(path).name in self.fail:
            raise RuntimeError('broken audio')
        return [SimpleNamespace(text=' 转写' + Path(path).stem)], None


def msg(i, kind, text='', src=None, transcription=None):
    m = dict(id=i, type=kind, text=text, media={'src': src} if src else {})
    if transcription:
        m['transcription'] = transcription
    return m


def sample():
    return dict(meta={}, messages=[
        msg(1, 'text', '周六带你去深圳东部看海[旺柴]🤔'),
        msg(2, 'voice', '[语音]', 'media/voice/a.wav'),
        msg(3, 'text', '欢乐港湾'),
        msg(4, 'voice', '[语音]', 'media/voice/b.wav', dict(text='旧的', status='done')),
        msg(5, 'voice', '[语音]'),  # audio missing: never selected
        msg(6, 'text', '这条在后面，不能进提示'),
    ])


def test_prompt():
    assert asr.clean_text('好的[捂脸]🤩OK！') == '好的 OK', 'tags, emoji and punctuation must be stripped'
    assert asr.tidy('坐地铁去吗４ 慢ＧＧ高铁站�') == '坐地铁去吗 慢 高铁站', 'model junk is removed'
    assert asr.tidy('对啊对啊。。。。。。') == '对啊对啊。' and asr.tidy('我们 Go 2点') == '我们 Go 2点'
    data = sample()
    prompt = asr.voice_prompt(data['messages'], 1)
    assert prompt.startswith(asr.PROMPT_HEAD) and '简体中文' in asr.PROMPT_HEAD
    assert '深圳东部看海' in prompt and '[' not in prompt and '🤔' not in prompt
    later = asr.voice_prompt(data['messages'], 4)
    assert '欢乐港湾' in later and '后面' not in later
    long = [msg(i, 'text', '很长的一句话' * 10) for i in range(30)]
    assert len(asr.voice_prompt(long, 30)) <= len(asr.PROMPT_HEAD) + 160


def test_transcribe():
    data, saves, model = sample(), [], FakeModel()
    info = asr.transcribe_voices('/x', data, {}, 'TA', lambda: saves.append(1), cache={'model': model})
    m = data['messages']
    assert [c[0] for c in model.calls] == ['a.wav', 'b.wav'], 'voices go in order; missing audio is skipped'
    assert all(c[1]['language'] == 'zh' and c[1]['initial_prompt'].startswith(asr.PROMPT_HEAD) for c in model.calls)
    assert '转写a' in model.calls[1][1]['initial_prompt'], 'earlier transcripts feed the next prompt'
    assert m[1]['transcription']['status'] == 'done' and 'transcription' not in m[4]
    assert info['enabled'] and info['selected'] == 2 and info['success'] == 2 and info['available'] == 2 and saves

    data = sample()
    model = FakeModel()
    info = asr.transcribe_voices('/x', data, {}, 'TA', lambda: None, only_missing=True, cache={'model': model})
    assert [c[0] for c in model.calls] == ['a.wav'] and data['messages'][3]['transcription']['text'] == '旧的'
    assert info['selected'] == 1 and info['success'] == 2

    data = sample()
    info = asr.transcribe_voices('/x', data, {}, 'TA', lambda: None, cache={'model': FakeModel(fail={'a.wav'})})
    assert data['messages'][1]['transcription']['status'] == 'failed' and info['success'] == 1

    original = asr.load_model
    asr.load_model = lambda cfg: (_ for _ in ()).throw(OSError('no model'))
    try:
        data = sample()
        info = asr.transcribe_voices('/x', data, {}, 'TA', lambda: None)
    finally:
        asr.load_model = original
    assert info['status'] == 'model_unavailable' and info['success'] == 0

    off = asr.disabled_summary(sample()['messages'])
    assert off['enabled'] is False and off['available'] == 2 and off['selected'] == 0


def test_wiring():
    export = (MULTI / 'export_full.py').read_text(encoding='utf-8')
    launcher = (MULTI / 'launcher.py').read_text(encoding='utf-8')
    page = (MULTI / 'launcher.html').read_text(encoding='utf-8')
    viewer = (MULTI / 'viewer-transcript.html').read_text(encoding='utf-8')
    assert 'BooleanOptionalAction' in export and 'disabled_summary(messages)' in export and 'WhisperModel' not in export
    assert "'--transcribe' if transcribe else '--no-transcribe'" in launcher and '--transcribe-archive' in launcher
    assert 'type(transcribe) is not bool' in launcher and 'asr_model_ready' in launcher
    assert 'id="asr-now"' in page and "transcribe:$('asr-now').checked" in page and 'asr_model_ready' in page
    assert "meta.transcription.enabled===false" in viewer, 'a disabled export must not claim "0 succeeded"'
    build = (MULTI / 'build_desktop.py').read_text(encoding='utf-8')
    package = (MULTI / 'package_source.py').read_text(encoding='utf-8')
    assert "'transcribe_archive'" in build and "'asr'" in build
    assert "'asr.py'" in package and "'transcribe_archive.py'" in package


def test_redo_archive():
    import transcribe_archive
    with tempfile.TemporaryDirectory() as temp:
        out = Path(temp) / 'archive'
        (out / 'media/voice').mkdir(parents=True)
        (out / 'media/voice/a.wav').write_bytes(b'RIFF')
        messages = [dict(id=1, message_key='k1', time='2026-11-11 12:00:00', sender='我', type='text', text='去欢乐港湾',
                         raw_content='去欢乐港湾', media={}),
                    dict(id=2, message_key='k2', time='2026-11-11 12:01:00', sender='我', type='voice', text='[语音]',
                         raw_content='', media={'src': 'media/voice/a.wav'}, availability='ready')]
        meta = dict(peer='TA', limitations=[], unmatched_databases=[])
        (out / 'chat.json').write_text(json.dumps(dict(meta=meta, messages=messages), ensure_ascii=False), encoding='utf-8')
        (out / '完整聊天档案.zip').write_bytes(b'old')  # build_export never overwrites; the tool must clear it
        model = FakeModel()
        orig_settings, orig_load = transcribe_archive.load_settings, asr.load_model
        transcribe_archive.load_settings = lambda: {'model_dir': temp}
        asr.load_model = lambda cfg: model
        try:
            transcribe_archive.main([str(out)])
        finally:
            transcribe_archive.load_settings, asr.load_model = orig_settings, orig_load
        data = json.loads((out / 'chat.json').read_text(encoding='utf-8'))
        assert data['messages'][1]['transcription']['text'] == '转写a'
        assert '去欢乐港湾' in model.calls[0][1]['initial_prompt']
        assert '语音转写：转写a' in (out / '聊天记录.txt').read_text(encoding='utf-8')
        assert (out / '完整聊天档案.zip').read_bytes() != b'old'


if __name__ == '__main__':
    test_prompt()
    test_transcribe()
    test_wiring()
    test_redo_archive()
    print('PASS: context prompt, ordered transcription, honest summary, redo tool')
