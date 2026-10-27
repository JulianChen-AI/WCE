#!/usr/bin/env python3
"""Export one WeChat session as JSON + WeChat-like HTML, with decrypted media."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import sqlite3
import struct
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

import zstandard
from Crypto.Cipher import AES

CST = timezone(timedelta(hours=8))
dctx = zstandard.ZstdDecompressor()
V2_SIG = b"\x07\x08V2\x08\x07"


def decode_content(content, ct):
    if content is None:
        return ""
    if ct == 4 and isinstance(content, bytes):
        try:
            content = dctx.decompress(content)
        except Exception:
            return ""
    if isinstance(content, bytes):
        content = content.decode("utf-8", "replace")
    return content or ""


def md5_from_packed(blob: bytes | None) -> str | None:
    if not blob:
        return None
    m = re.search(rb"[0-9a-f]{32}", blob)
    return m.group().decode() if m else None


def load_image_key(path: Path | None) -> bytes | None:
    if not path or not path.exists():
        return None
    raw = path.read_text().strip()
    if len(raw) == 16:
        return raw.encode("ascii")
    if len(raw) == 32 and re.fullmatch(r"[0-9a-fA-F]+", raw):
        return bytes.fromhex(raw)
    return None


def decrypt_v2(data: bytes, key16: bytes, xor_key: int) -> bytes | None:
    if len(data) < 31 or data[:6] != V2_SIG:
        return None
    aes_size, xor_size = struct.unpack_from("<II", data, 6)
    aligned = (aes_size + 15) // 16 * 16
    if 15 + aligned > len(data):
        return None
    pt = AES.new(key16, AES.MODE_ECB).decrypt(data[15 : 15 + aligned])
    pad = pt[-1]
    if 1 <= pad <= 16 and pt.endswith(bytes([pad]) * pad):
        pt = pt[:-pad]
    mid_end = len(data) - xor_size if xor_size else len(data)
    mid = data[15 + aligned : mid_end] if mid_end > 15 + aligned else b""
    xor_part = bytes(b ^ xor_key for b in data[-xor_size:]) if xor_size else b""
    out = pt + mid + xor_part
    if out.startswith(b"\xff\xd8\xff"):
        end = out.rfind(b"\xff\xd9")
        if end != -1:
            out = out[: end + 2]
    return out


def decrypt_emoticon(data: bytes, emo_key: bytes) -> bytes | None:
    if len(data) < 16:
        return None
    buf = data[: len(data) - (len(data) % 16)]
    pt = AES.new(emo_key, AES.MODE_CBC, iv=emo_key).decrypt(buf)
    pad = pt[-1]
    if 1 <= pad <= 16 and pt.endswith(bytes([pad]) * pad):
        pt = pt[:-pad]
    if sniff_ext(pt) in {"jpg", "png", "gif", "webp"}:
        return pt
    return None


def sniff_ext(blob: bytes) -> str:
    if blob.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if blob.startswith(b"\x89PNG"):
        return "png"
    if blob.startswith(b"GIF8"):
        return "gif"
    if blob.startswith(b"RIFF") and b"WEBP" in blob[:16]:
        return "webp"
    if len(blob) > 8 and blob[4:8] == b"ftyp":
        return "mp4"
    return "bin"


def copy_file(src: Path, dest_dir: Path, name: str) -> str | None:
    if not src.exists() or src.stat().st_size <= 0:
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / name
    if not dest.exists():
        shutil.copy2(src, dest)
    return f"media/{dest_dir.name}/{name}"


def index_by_prefix(root: Path) -> dict[str, list[Path]]:
    out: dict[str, list[Path]] = {}
    if not root.exists():
        return out
    for dp, _, fns in os.walk(root):
        for fn in fns:
            if fn.startswith("."):
                continue
            stem = fn.split(".")[0].split("_")[0]
            path = Path(dp) / fn
            out.setdefault(stem, []).append(path)
            out.setdefault(fn, []).append(path)
    return out


def pick_best(files: list[Path], prefer: tuple[str, ...]) -> Path | None:
    if not files:
        return None

    def score(p: Path):
        n = p.name
        for i, s in enumerate(prefer):
            if s == "full" and n.endswith(".dat") and "_t." not in n and "_h." not in n:
                return (0, -p.stat().st_size)
            if s in n:
                return (i, -p.stat().st_size)
        return (50, -p.stat().st_size)

    return sorted(files, key=score)[0]


def xml_text(el, path):
    node = el.find(path) if el is not None else None
    return (node.text or "").strip() if node is not None and node.text else ""


def parse_xml(raw: str):
    raw = raw.strip()
    if not raw.startswith("<"):
        return None
    try:
        return ET.fromstring(raw)
    except ET.ParseError:
        try:
            return ET.fromstring(raw.encode("utf-8"))
        except Exception:
            return None


def load_emoji_cdn(dec: Path) -> dict[str, str]:
    db = dec / "emoticon/emoticon.db"
    if not db.exists():
        return {}
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT md5, cdn_url FROM kNonStoreEmoticonTable WHERE cdn_url IS NOT NULL AND cdn_url!=''"
    ).fetchall()
    conn.close()
    return {md5.lower(): url for md5, url in rows if md5}


def download_emoji(url: str, dest: Path) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read()
        ext = sniff_ext(data)
        if ext not in {"jpg", "png", "gif", "webp"}:
            return False
        dest.write_bytes(data)
        return True
    except Exception:
        return False


def list_contacts(dec: Path) -> None:
    contact_db = dec / "contact/contact.db"
    msg_db = dec / "message/message_0.db"
    if not contact_db.exists() or not msg_db.exists():
        raise SystemExit(f"missing decrypted dbs under {dec}")
    conn = sqlite3.connect(contact_db)
    contacts = {
        row[0]: row
        for row in conn.execute("SELECT username, nick_name, remark, alias FROM contact")
    }
    conn.close()
    conn = sqlite3.connect(msg_db)
    tables = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'"
        )
    ]
    rows = []
    for table in tables:
        n = conn.execute(f"SELECT COUNT(*), MIN(create_time), MAX(create_time) FROM {table}").fetchone()
        rows.append((table, n[0] or 0, n[1], n[2]))
    conn.close()
    rows.sort(key=lambda r: r[1], reverse=True)
    print(f"{'count':>8}  {'username':<32}  remark / nick")
    md5_to_user = {}
    for username in contacts:
        md5_to_user[f"Msg_{hashlib.md5(username.encode()).hexdigest()}"] = username
    for table, count, t0, t1 in rows[:80]:
        username = md5_to_user.get(table, "?")
        info = contacts.get(username)
        label = ""
        if info:
            label = " / ".join(x for x in (info[2], info[1], info[3]) if x)
        start = datetime.fromtimestamp(t0, tz=CST).strftime("%Y-%m-%d") if t0 else ""
        end = datetime.fromtimestamp(t1, tz=CST).strftime("%Y-%m-%d") if t1 else ""
        print(f"{count:8d}  {username:<32}  {label}  {start}..{end}")


def extract_avatars(dec: Path, media: Path, my_wxid: str, username: str) -> dict[str, str]:
    db = dec / "head_image/head_image.db"
    mapping: dict[str, str] = {}
    if not db.exists():
        return mapping
    conn = sqlite3.connect(db)
    ava = media / "avatar"
    ava.mkdir(parents=True, exist_ok=True)
    for wxid, name in ((my_wxid, "me.jpg"), (username, "peer.jpg")):
        row = conn.execute("SELECT image_buffer FROM head_image WHERE username=?", (wxid,)).fetchone()
        if row and row[0] and row[0].startswith(b"\xff\xd8"):
            (ava / name).write_bytes(row[0])
            mapping[wxid] = f"media/avatar/{name}"
    conn.close()
    return mapping


def find_username(dec: Path, query: str) -> tuple[str, str]:
    conn = sqlite3.connect(dec / "contact/contact.db")
    rows = conn.execute(
        """SELECT username, nick_name, remark, alias FROM contact
           WHERE nick_name LIKE ? OR remark LIKE ? OR alias LIKE ? OR username LIKE ?""",
        (f"%{query}%",) * 4,
    ).fetchall()
    conn.close()
    if not rows:
        raise SystemExit(f"no contact matching {query!r}")
    if len(rows) > 1:
        print("multiple contacts:")
        for r in rows:
            print(" ", r)
        raise SystemExit("pass --username with the exact wxid")
    username, nick, remark, _alias = rows[0]
    return username, (remark or nick or username)


def build_messages(cfg) -> tuple[list[dict], dict]:
    dec: Path = cfg["dec"]
    table = cfg["table"]
    my_wxid = cfg["my_wxid"]
    display = cfg["display"]
    img_key = cfg["img_key"]
    xor_key = cfg["xor_key"]
    emo_key = cfg["emo_key"]
    media: Path = cfg["media"]
    since = cfg["since"]

    conn = sqlite3.connect(dec / "message/message_0.db")
    name2id = {rid: uname for rid, uname in conn.execute("SELECT rowid, user_name FROM Name2Id")}
    sql = f"""SELECT local_id, server_id, local_type, create_time, real_sender_id,
                     message_content, WCDB_CT_message_content, packed_info_data
              FROM {table}"""
    params: list = []
    if since:
        sql += " WHERE create_time >= ?"
        params.append(int(since.timestamp()))
    sql += " ORDER BY create_time ASC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()

    img_index = index_by_prefix(cfg["attach"])
    vid_index = index_by_prefix(cfg["video_root"])
    file_index = index_by_prefix(cfg["file_root"])
    emo_index = index_by_prefix(cfg["emo_root"])
    emoji_cdn = load_emoji_cdn(dec)
    emoji_cache: dict[str, str] = {}
    stats = {"image_ok": 0, "image_fail": 0, "video": 0, "file": 0, "emoji": 0}

    def save_emoji_bytes(blob: bytes, md5: str) -> str | None:
        ext = sniff_ext(blob)
        if ext not in {"jpg", "png", "gif", "webp"}:
            return None
        dest = media / "emoji"
        dest.mkdir(parents=True, exist_ok=True)
        name = f"{md5}.{ext}"
        path = dest / name
        if not path.exists():
            path.write_bytes(blob)
        return f"media/emoji/{name}"

    messages = []
    for local_id, _server_id, local_type, ts, sender_id, content, ct, packed in rows:
        low = int(local_type) & 0xFFFF
        sender_wxid = name2id.get(sender_id, "")
        if sender_wxid == my_wxid:
            role, sender = "me", cfg["my_name"]
        elif low in (10000, 10002):
            role, sender = "system", "系统"
        else:
            role, sender = "peer", display
        raw = decode_content(content, ct)
        md5 = md5_from_packed(packed)
        if not md5:
            m = re.search(r'md5="([0-9a-fA-F]{32})"', raw)
            md5 = m.group(1).lower() if m else None
        item = {
            "id": local_id,
            "time": datetime.fromtimestamp(ts, tz=CST).strftime("%Y-%m-%d %H:%M:%S") if ts else "",
            "timestamp": ts,
            "role": role,
            "sender": sender,
            "type": "text",
            "text": "",
            "media": {},
        }
        if low == 1:
            item["text"] = raw.strip()
        elif low == 3:
            item["type"] = "image"
            item["text"] = "[图片]"
            files = img_index.get(md5 or "", [])
            ordered = []
            for label in ("full", "_h", "_t", ".dat"):
                cand = pick_best(files, (label,))
                if cand and cand not in ordered:
                    ordered.append(cand)
            decoded = None
            if img_key:
                for src in ordered:
                    blob = decrypt_v2(src.read_bytes(), img_key, xor_key)
                    if blob and sniff_ext(blob) in {"jpg", "png", "gif", "webp"}:
                        decoded = (blob, sniff_ext(blob))
                        break
            if decoded:
                blob, ext = decoded
                dest = media / "image"
                dest.mkdir(parents=True, exist_ok=True)
                name = f"{md5}.{ext}"
                (dest / name).write_bytes(blob)
                item["media"]["src"] = f"media/image/{name}"
                stats["image_ok"] += 1
            elif ordered:
                rel = copy_file(ordered[0], media / "image_enc", ordered[0].name)
                item["media"]["encrypted"] = rel
                stats["image_fail"] += 1
            else:
                stats["image_fail"] += 1
        elif low == 43:
            item["type"] = "video"
            item["text"] = "[视频]"
            files = vid_index.get(md5 or "", [])
            mp4 = next((p for p in files if p.suffix.lower() == ".mp4"), None)
            thumb = next(
                (p for p in files if p.name.endswith("_thumb.jpg") or p.suffix.lower() in {".jpg", ".jpeg"}),
                None,
            )
            if mp4:
                item["media"]["src"] = copy_file(mp4, media / "video", mp4.name)
                stats["video"] += 1
            if thumb:
                item["media"]["thumb"] = copy_file(thumb, media / "video", thumb.name)
            root = parse_xml(raw)
            if root is not None:
                play = root.find(".//videomsg")
                if play is not None:
                    item["media"]["seconds"] = play.attrib.get("playlength") or xml_text(root, ".//playlength")
        elif low == 47:
            item["type"] = "emoji"
            item["text"] = "[表情]"
            key = (md5 or "").lower()
            if key and key in emoji_cache:
                item["media"]["src"] = emoji_cache[key]
                stats["emoji"] += 1
            else:
                files = emo_index.get(key, []) if key else []
                persist = [p for p in files if "Persist" in str(p) and p.suffix == ""]
                thumbs = [p for p in files if "Thumb" in str(p)]
                decoded = None
                if emo_key:
                    for src in persist + thumbs:
                        blob = decrypt_emoticon(src.read_bytes(), emo_key)
                        if blob:
                            decoded = blob
                            if sniff_ext(blob) in {"gif", "png", "jpg", "webp"} and "Persist" in str(src):
                                break
                if decoded and sniff_ext(decoded) != "wxgf":
                    rel = save_emoji_bytes(decoded, key or f"id{local_id}")
                    if rel:
                        item["media"]["src"] = rel
                        if key:
                            emoji_cache[key] = rel
                        stats["emoji"] += 1
                else:
                    cdn = emoji_cdn.get(key) if key else None
                    if not cdn:
                        murl = re.search(r'cdnurl\s*=\s*"([^"]*)"', raw, re.I)
                        if murl:
                            cdn = html.unescape(murl.group(1))
                    if cdn:
                        dest = media / "emoji"
                        dest.mkdir(parents=True, exist_ok=True)
                        tmp = dest / f"{key or local_id}.bin"
                        if download_emoji(cdn, tmp):
                            ext = sniff_ext(tmp.read_bytes())
                            final = dest / f"{(key or str(local_id))}.{ext}"
                            tmp.rename(final)
                            rel = f"media/emoji/{final.name}"
                            item["media"]["src"] = rel
                            if key:
                                emoji_cache[key] = rel
                            stats["emoji"] += 1
                        elif tmp.exists():
                            tmp.unlink()
        elif low == 49:
            root = parse_xml(raw)
            app = root.find("appmsg") if root is not None else None
            typ = xml_text(app, "type") if app is not None else ""
            title = xml_text(app, "title") if app is not None else ""
            url = xml_text(app, "url") if app is not None else ""
            refer = app.find("refermsg") if app is not None else None
            if typ == "57" or refer is not None:
                item["type"] = "quote"
                item["text"] = title
                item["media"]["quote_from"] = xml_text(refer, "displayname") if refer is not None else ""
                item["media"]["quote_text"] = xml_text(refer, "content") or xml_text(refer, "title")
            elif typ == "6":
                item["type"] = "file"
                item["text"] = title or "[文件]"
                fname = title
                hits = file_index.get(fname, []) if fname else []
                if not hits and fname:
                    hits = [p for k, ps in file_index.items() if fname in k for p in ps]
                if hits:
                    src = max(hits, key=lambda p: p.stat().st_size)
                    item["media"]["src"] = copy_file(src, media / "file", src.name)
                    item["media"]["filename"] = src.name
                    stats["file"] += 1
            elif typ in {"33", "36"}:
                item["type"] = "miniprogram"
                item["text"] = title or "[小程序]"
            else:
                item["type"] = "link"
                item["text"] = title or url or "[链接]"
                item["media"]["url"] = url
                item["media"]["desc"] = xml_text(app, "des") if app is not None else ""
        elif low == 50:
            item["type"] = "call"
            item["text"] = "[语音/视频通话]"
        elif low == 34:
            item["type"] = "voice"
            item["text"] = "[语音]"
        elif low in (10000, 10002):
            item["type"] = "system"
            if "paymsg" in raw:
                cdata = re.search(r"<!\[CDATA\[(.*?)\]\]>", raw, re.S)
                text = re.sub(r"<[^>]+>", "", cdata.group(1) if cdata else "转账")
                item["text"] = f"[转账] {text.strip()[:80]}"
            elif raw.startswith("<"):
                m = re.search(r"<template[^>]*>(.*?)</template>", raw, re.S)
                item["text"] = re.sub(r"<[^>]+>", "", m.group(1) if m else raw)[:120]
            else:
                item["text"] = raw[:200]
        elif low == 48:
            item["type"] = "location"
            item["text"] = "[位置]"
        elif low == 42:
            item["type"] = "card"
            item["text"] = "[名片]"
        else:
            item["text"] = raw[:300] if raw and not raw.startswith("<") else f"[{low}]"
        messages.append(item)
    return messages, stats


def write_html(out: Path, display: str, meta: dict, messages: list[dict]) -> None:
    page = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>__TITLE__</title>
<style>
:root { --bg:#ededed; --me:#95ec69; --peer:#fff; --sys:#b2b2b2; --ink:#111; --muted:#888; }
*{box-sizing:border-box}
html,body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 -apple-system,"PingFang SC","Hiragino Sans GB",sans-serif}
.top{position:sticky;top:0;z-index:2;background:rgba(237,237,237,.92);backdrop-filter:blur(10px);border-bottom:1px solid #ddd;padding:12px 16px}
.top h1{margin:0;font-size:16px;font-weight:600;text-align:center}
.top p{margin:4px 0 0;text-align:center;color:var(--muted);font-size:12px}
.wrap{max-width:720px;margin:0 auto;padding:12px 12px 40px}
.day{text-align:center;color:#999;font-size:12px;margin:18px 0 10px}
.row{display:flex;gap:8px;margin:10px 0;align-items:flex-start}
.row.me{flex-direction:row-reverse}
.av{width:36px;height:36px;border-radius:4px;object-fit:cover;background:#ccc;flex:none}
.col{max-width:min(74%, 520px)}
.name{font-size:12px;color:#888;margin:0 0 3px}
.row.me .name{text-align:right}
.b{position:relative;display:inline-block;padding:8px 10px;border-radius:4px;background:var(--peer);box-shadow:0 1px 1px rgba(0,0,0,.06);word-break:break-word;white-space:pre-wrap}
.row.me .b{background:var(--me)}
.b img,.b video{display:block;max-width:240px;max-height:280px;border-radius:2px}
.ph{min-width:120px;min-height:80px;background:#d7d7d7;color:#666;display:flex;align-items:center;justify-content:center;font-size:13px;border-radius:2px}
.quote{margin-top:6px;padding:6px 8px;background:rgba(0,0,0,.05);border-radius:3px;color:#555;font-size:13px}
.link a{color:#576b95;text-decoration:none}
.file{display:flex;gap:8px;align-items:center;min-width:180px}
.sys{text-align:center;color:var(--sys);font-size:12px;margin:10px 0}
.meta{color:#aaa;font-size:11px;margin-top:3px}
.row.me .meta{text-align:right}
</style>
</head>
<body>
<div class="top">
  <h1>__DISPLAY__</h1>
  <p>__META__</p>
</div>
<div class="wrap" id="chat"></div>
<script id="chat-data" type="application/json">__JSON__</script>
<script>
const data = JSON.parse(document.getElementById('chat-data').textContent);
const avMe = data.meta.avatars.me || '';
const avPeer = data.meta.avatars.peer || '';
const root = document.getElementById('chat');
let lastDay = '';
function esc(s){return String(s||'').replace(/[&<>"]/g, m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[m]))}
function media(m){
  const d=m.media||{};
  if(m.type==='image' && d.src) return `<img src="${esc(d.src)}" alt="图片" loading="lazy"/>`;
  if(m.type==='image') return `<div class="ph">图片未解密</div>`;
  if(m.type==='video' && d.src) return `<video controls preload="metadata" poster="${esc(d.thumb||'')}" src="${esc(d.src)}"></video>`;
  if(m.type==='video' && d.thumb) return `<img src="${esc(d.thumb)}" alt="视频封面"/><div class="meta">视频${d.seconds? ' · '+d.seconds+'s':''}</div>`;
  if(m.type==='video') return `<div class="ph">视频</div>`;
  if(m.type==='emoji' && d.src) return `<img src="${esc(d.src)}" alt="表情" style="max-width:120px;max-height:120px"/>`;
  if(m.type==='emoji') return `<div class="ph" style="min-width:72px;min-height:72px">表情</div>`;
  if(m.type==='file') return `<div class="file">📄 <div><b>${esc(d.filename||m.text)}</b>${d.src?`<div class="meta"><a href="${esc(d.src)}">打开文件</a></div>`:''}</div></div>`;
  if(m.type==='link') return `<div class="link"><b>${esc(m.text)}</b>${d.desc?`<div class="meta">${esc(d.desc)}</div>`:''}${d.url?`<div><a href="${esc(d.url)}" target="_blank">${esc(d.url)}</a></div>`:''}</div>`;
  if(m.type==='quote') return `${esc(m.text||'')}<div class="quote">${esc(d.quote_from? d.quote_from+'：':'')}${esc(d.quote_text||'')}</div>`;
  return esc(m.text||'');
}
for (const m of data.messages){
  const day=(m.time||'').slice(0,10);
  if(day && day!==lastDay){
    root.insertAdjacentHTML('beforeend', `<div class="day">${esc(day)}</div>`);
    lastDay=day;
  }
  if(m.role==='system'){
    root.insertAdjacentHTML('beforeend', `<div class="sys">${esc(m.text)}</div>`);
    continue;
  }
  const mine = m.role==='me';
  const av = mine? avMe : avPeer;
  root.insertAdjacentHTML('beforeend', `
    <div class="row ${mine?'me':''}">
      ${av? `<img class="av" src="${esc(av)}" alt="">` : `<div class="av"></div>`}
      <div class="col">
        <div class="name">${esc(m.sender)}</div>
        <div class="b">${media(m)}</div>
        <div class="meta">${esc((m.time||'').slice(11))}</div>
      </div>
    </div>`);
}
</script>
</body>
</html>
"""
    payload = json.dumps({"meta": meta, "messages": messages}, ensure_ascii=False).replace("</", "<\\/")
    text = (
        page.replace("__TITLE__", f"微信聊天 · {display}")
        .replace("__DISPLAY__", display)
        .replace("__META__", f"{meta['count']} 条 · {meta['start']} 至 {meta['end']}")
        .replace("__JSON__", payload)
    )
    (out / "index.html").write_text(text, encoding="utf-8")


def infer_my_wxid(wx_dir: Path | None, explicit: str | None) -> str:
    if explicit:
        return explicit
    if wx_dir:
        m = re.match(r"^(wxid_.+)_\d+$", wx_dir.name)
        if m:
            return m.group(1)
    raise SystemExit("pass --my-wxid (own wxid). Guessing wrong flips every bubble.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--decrypted-dir", required=True)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--name", help="search remark / nick")
    parser.add_argument("--username", help="exact wxid or chatroom id")
    parser.add_argument("--my-wxid", help="own cleaned wxid")
    parser.add_argument("--display")
    parser.add_argument("--my-name", default="我")
    parser.add_argument("--wx-dir", help="xwechat_files/<account dir>")
    parser.add_argument("--out")
    parser.add_argument("--image-key-file")
    parser.add_argument("--kvcomm-code", type=int)
    parser.add_argument("--since", help="YYYY-MM-DD")
    args = parser.parse_args()

    dec = Path(args.decrypted_dir).expanduser()
    if args.list:
        list_contacts(dec)
        return 0

    username = args.username
    display = args.display
    if not username:
        if not args.name:
            raise SystemExit("pass --username or --name (or --list)")
        username, guessed = find_username(dec, args.name)
        display = display or guessed
    display = display or username
    wx_dir = Path(args.wx_dir).expanduser() if args.wx_dir else None
    my_wxid = infer_my_wxid(wx_dir, args.my_wxid)
    out = Path(args.out).expanduser() if args.out else Path.home() / "Downloads" / f"微信聊天-{display}"
    media = out / "media"
    out.mkdir(parents=True, exist_ok=True)
    for sub in ("image", "image_enc", "video", "file", "emoji", "emoji_enc", "avatar"):
        (media / sub).mkdir(parents=True, exist_ok=True)

    table = f"Msg_{hashlib.md5(username.encode()).hexdigest()}"
    xor_key = (args.kvcomm_code & 0xFF) if args.kvcomm_code else 0
    emo_key = (
        hashlib.md5(f"{args.kvcomm_code}{my_wxid}EMOTICON".encode()).digest()[:16]
        if args.kvcomm_code
        else None
    )
    img_key = load_image_key(Path(args.image_key_file).expanduser() if args.image_key_file else None)
    since = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=CST) if args.since else None
    missing = Path("/var/empty/wechat-export-missing")
    attach = (
        wx_dir / "msg/attach" / hashlib.md5(username.encode()).hexdigest()
        if wx_dir
        else missing
    )
    cfg = {
        "dec": dec,
        "table": table,
        "my_wxid": my_wxid,
        "my_name": args.my_name,
        "display": display,
        "img_key": img_key,
        "xor_key": xor_key,
        "emo_key": emo_key,
        "media": media,
        "since": since,
        "attach": attach,
        "video_root": (wx_dir / "msg/video") if wx_dir else missing,
        "file_root": (wx_dir / "msg/file") if wx_dir else missing,
        "emo_root": (wx_dir / "business/emoticon") if wx_dir else missing,
    }
    avatars = extract_avatars(dec, media, my_wxid, username)
    messages, stats = build_messages(cfg)
    meta = {
        "peer": display,
        "username": username,
        "count": len(messages),
        "start": messages[0]["time"] if messages else "",
        "end": messages[-1]["time"] if messages else "",
        "avatars": {"me": avatars.get(my_wxid, ""), "peer": avatars.get(username, "")},
        "image_key": bool(img_key),
        "stats": stats,
    }
    (out / "chat.json").write_text(
        json.dumps({"meta": meta, "messages": messages}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_html(out, display, meta, messages)
    print("out", out)
    print("messages", len(messages))
    print("me/peer/system",
          sum(1 for m in messages if m["role"] == "me"),
          sum(1 for m in messages if m["role"] == "peer"),
          sum(1 for m in messages if m["role"] == "system"))
    print("stats", stats)
    print("html", out / "index.html")
    print("json", out / "chat.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
