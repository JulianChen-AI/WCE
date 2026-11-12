"""Local voice transcription for WCE archives (faster-whisper; audio never leaves this computer).

Each voice note is transcribed with a short prompt built from the chat text just before it.
The prompt keeps output in Simplified Chinese (the bare model often answers in Traditional)
and gives the model the names, places and plans the voice note is likely to mention.
"""
import re
from pathlib import Path

DEFAULT_MODEL = 'base'
PROMPT_HEAD = '以下是微信语音消息，普通话，简体中文。'
_TAG = re.compile(r'\[[^\[\]]{1,12}\]')
_DROP = re.compile('[^一-鿿A-Za-z0-9 ]+')
# The base model turns full-width punctuation in the prompt into junk like 「４」「Ｇ」「�」 or endless 「。。。」.
_JUNK = re.compile('[�０-９Ａ-Ｚａ-ｚ]+')
_REPEAT = re.compile(r'([。，、！？!?,.\s])\1+')


def clean_text(text):
    """Keep only words: WeChat tags like [捂脸], emoji and punctuation in the prompt come back as garbage."""
    text = _DROP.sub(' ', _TAG.sub(' ', str(text or '')))
    return re.sub(r'\s+', ' ', text).strip()


def voice_prompt(messages, index, limit=160, window=12):
    texts = []
    for m in reversed(messages[:index]):
        if len(texts) >= window:
            break
        done = (m.get('transcription') or {}).get('status') == 'done'
        source = m.get('text') if m.get('type') in ('text', 'quote') else (m['transcription']['text'] if done else '')
        text = clean_text(source)
        if text:
            texts.append(text)
    return PROMPT_HEAD + ' '.join(reversed(texts))[-limit:]


def tidy(text):
    text = _REPEAT.sub(r'\1', _JUNK.sub(' ', text))
    return re.sub(r'\s+', ' ', text).strip()


def load_model(cfg):
    from faster_whisper import WhisperModel
    return WhisperModel(cfg.get('asr_model') or DEFAULT_MODEL, device='cpu', compute_type='int8', cpu_threads=4,
                        local_files_only=bool(cfg.get('legacy')), download_root=cfg['model_dir'])


def disabled_summary(messages):
    available = sum(1 for m in messages if m.get('type') == 'voice' and (m.get('media') or {}).get('src'))
    return dict(enabled=False, available=available, selected=0, success=0, model=None, local_only=True, scope='未开启语音转写')


def transcribe_voices(out, data, cfg, display, save, only_missing=False, cache=None):
    """Transcribe ready voice notes in order, saving after each one so progress survives a crash.

    Pass the same `cache` dict for several conversations to load the model once.
    """
    cache = {} if cache is None else cache
    model = cache.get('model')
    messages = data['messages']
    targets = [i for i, m in enumerate(messages) if m.get('type') == 'voice' and (m.get('media') or {}).get('src')
               and not (only_missing and (m.get('transcription') or {}).get('status') == 'done')]
    name = cfg.get('asr_model') or DEFAULT_MODEL
    info = data['meta']['transcription'] = dict(
        enabled=True, available=sum(1 for m in messages if m.get('type') == 'voice' and (m.get('media') or {}).get('src')),
        selected=len(targets), success=0, model=name, local_only=True, scope='全部可用语音', prompt='前文上下文')
    if targets and model is None:
        try:
            model = cache['model'] = load_model(cfg)
        except Exception as exc:
            info.update(status='model_unavailable', error_type=type(exc).__name__)
            print('本地转写模型不可用，已保留原语音；可安装模型后重试。', flush=True)
            save()
            return info
    for n, i in enumerate(targets, 1):
        item = messages[i]
        try:
            segments, _ = model.transcribe(str(Path(out) / item['media']['src']), language='zh', beam_size=5,
                                           condition_on_previous_text=False, initial_prompt=voice_prompt(messages, i))
            text = tidy(''.join(s.text for s in segments))
            item['transcription'] = dict(text=text, status='done' if text else 'empty', note='机器转写，未人工校对', model=name)
        except Exception as exc:
            item['transcription'] = dict(text='', status='failed', error_type=type(exc).__name__)
        print(f'{display}：语音转写 {n}/{len(targets)} {item["transcription"]["status"]}', flush=True)
        save()
    info.update(status='done', success=sum((m.get('transcription') or {}).get('status') == 'done'
                                           for m in messages if m.get('type') == 'voice'))
    save()
    return info
