# -*- coding: utf-8 -*-
"""
把背景音乐的 base64（bg.txt）转成页面可直接加载的 bg.js。

用法：
    python make_bg_js.py                 # 读 bg.txt → 生成 bg.js（外挂模式）
    python make_bg_js.py -i bg.txt -o bg.js
    python make_bg_js.py -s love.mp3     # 直接由音频文件生成 bg.txt，再写入页面
    python make_bg_js.py --inline        # 把 base64 直接嵌入 index.html（默认，单文件模式）

说明：
    推荐用 --inline / 直接内联：音乐数据写在 index.html 里，
    只需要一个 html 文件就能播放，不用额外带任何 js。
    之所以还保留 "生成 bg.js" 的能力，只是为了想把音乐外挂时的可选之用。
"""
import argparse
import base64
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def read_b64(path):
    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
        s = f.read()
    s = s.strip()
    # 兼容 data:audio/mpeg;base64,XXXX 的写法
    m = re.match(r'^data:[^,]*,\s*(.*)$', s, re.S)
    if m:
        s = m.group(1)
    return ''.join(s.split())          # 去掉所有换行/空白


def decode_id3_text(raw, start, end):
    """解析 ID3v2 帧里的文字内容（返回字符串，失败返回 None）"""
    if end - start < 2:
        return None
    enc = raw[start]
    body = raw[start + 1:end]
    try:
        if enc == 0:                            # ISO-8859-1
            return body.decode('latin-1').strip('\x00 ').strip()
        if enc == 1:                            # UTF-16 with BOM
            return body.decode('utf-16', errors='ignore').strip('\x00 ').strip()
        if enc == 2:                            # UTF-16BE
            return body.decode('utf-16-be', errors='ignore').strip('\x00 ').strip()
        if enc == 3:                            # UTF-8
            return body.decode('utf-8', errors='ignore').strip('\x00 ').strip()
    except Exception:
        return None
    return None


def parse_meta(mp3):
    """从 mp3 二进制里读 ID3v2 的标题 / 艺术家"""
    title = artist = ''
    if mp3[:3] != b'ID3' or len(mp3) < 10:
        return title, artist
    size = 0
    for b in mp3[6:10]:
        size = (size << 7) | (b & 0x7F)
    end = min(10 + size, len(mp3))
    pos = 10
    while pos + 10 <= end:
        fid = mp3[pos:pos + 4]
        if not fid or fid[0:1] < b'A' or fid[0:1] > b'Z':
            break
        fsize = int.from_bytes(mp3[pos + 4:pos + 8], 'big')
        if fsize <= 0 or fsize > 1 << 22:
            break
        body_s, body_e = pos + 10, min(pos + 10 + fsize, end)
        if fid == b'TIT2' and not title:
            title = decode_id3_text(mp3, body_s, body_e) or ''
        elif fid == b'TPE1' and not artist:
            artist = decode_id3_text(mp3, body_s, body_e) or ''
        pos = body_e
        if title and artist:
            break
    return title, artist


def guess_mime(mp3):
    if mp3[:3] == b'ID3' or mp3[:2] in (b'\xff\xfb', b'\xff\xf3', b'\xff\xf2'):
        return 'audio/mpeg'
    if mp3[:4] == b'RIFF' and mp3[8:12] == b'WAVE':
        return 'audio/wav'
    if mp3[:4] == b'OggS':
        return 'audio/ogg'
    if mp3[:4] == b'fLaC':
        return 'audio/flac'
    if mp3[:4] == b'\x1aE\xdf\xa3':
        return 'audio/webm'
    if mp3[4:8] == b'ftyp':
        return 'audio/mp4'
    return 'audio/mpeg'


def write_bg_js(b64, name, mime, out):
    head = (
        "/* 自动生成，请勿手改 —— 由 make_bg_js.py 从 bg.txt 转换而来\n"
        "   内置背景音乐（base64 字符串），通过 Blob 解码后交给 <audio> 播放。 */\n"
    )
    body = [head]
    body.append("window.BG_MUSIC_NAME = %s;\n" % repr(name).lstrip('u'))
    body.append("window.BG_MUSIC_MIME = %s;\n" % repr(mime).lstrip('u'))
    body.append("window.BG_MUSIC_B64  =\n")
    # 每行 4096 字符，避免单行使编辑器/工具卡死
    for i in range(0, len(b64), 4096):
        body.append('"%s" +\n' % b64[i:i + 4096])
    body[-1] = body[-1].rstrip(' +\n') + ';\n'
    with open(out, 'w', encoding='utf-8', newline='\n') as f:
        f.write(''.join(body))


def js_str(s):
    """安全的 JS 字符串字面量（双引号，中文原样输出）"""
    return json.dumps(s, ensure_ascii=False)


# <script src="bg.js"></script>（内联后不再需要这一行）
BG_SRC_TAG = re.compile(
    r'[ \t]*<script[^>]*\bsrc\s*=\s*["\']bg\.js["\'][^>]*>\s*</script>[ \t]*\r?\n?'
)
INLINE_BLOCK = re.compile(r'/\*__BG_B64_START__\*/[\s\S]*?/\*__BG_B64_END__\*/')


def inject_inline(b64, mime, name, html_path):
    """把 base64 直接嵌入 index.html —— 只需要一个 html 文件即可播放"""
    with open(html_path, 'r', encoding='utf-8') as f:
        html = f.read()

    html = INLINE_BLOCK.sub('', html)     # 清掉上一次嵌入的内容
    html = BG_SRC_TAG.sub('', html)       # 内联后就不需要外挂 bg.js 了

    block = (
        "<!-- 内置背景音乐：base64，由 make_bg_js.py 从 bg.txt 生成，请勿手改 -->\n"
        "<script>\n"
        "/*__BG_B64_START__*/\n"
        "window.BG_MUSIC_INLINE_NAME = %s;\n"
        "window.BG_MUSIC_INLINE = '%s|%s';\n"
        "/*__BG_B64_END__*/\n"
        "</script>\n"
        % (js_str(name), mime, b64)
    )
    if '</body>' in html:
        html = html.replace('</body>', block + '</body>', 1)
    else:
        html = html + '\n' + block

    with open(html_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(html)
    print('已嵌入 %s（单文件模式，%.2f MB）' % (html_path, len(b64) / 1024 / 1024))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('-i', '--input', default=os.path.join(HERE, 'bg.txt'), help='base64 文本文件')
    ap.add_argument('-o', '--output', default=os.path.join(HERE, 'bg.js'),
                    help='外挂模式（--external）下输出的 js 文件')
    ap.add_argument('-s', '--source', help='直接传入音频文件（mp3/wav/…），自动转 base64')
    ap.add_argument('--external', dest='inline', action='store_false',
                    help='生成外挂的 bg.js（默认是把音乐内联进 index.html）')
    ap.add_argument('--html', default=os.path.join(HERE, 'index.html'))
    a = ap.parse_args()

    if a.source:
        with open(a.source, 'rb') as f:
            raw = f.read()
        b64 = base64.b64encode(raw).decode('ascii')
        with open(a.input, 'w', encoding='utf-8', newline='\n') as f:
            f.write(b64)
        print('已生成 %s（原始 %.2f MB）' % (a.input, len(raw) / 1024 / 1024))
    else:
        if not os.path.exists(a.input):
            sys.exit('找不到 %s' % a.input)
        b64 = read_b64(a.input)

    mp3 = base64.b64decode(b64)
    mime = guess_mime(mp3)
    name, artist = parse_meta(mp3)
    label = ' / '.join(x for x in (name, artist) if x) or os.path.basename(a.input)
    print('曲目：%s' % label)
    print('格式：%s' % mime)
    print('大小：%.2f MB（解码后 %.2f MB）' % (len(b64) / 1024 / 1024, len(mp3) / 1024 / 1024))

    if a.inline:
        inject_inline(b64, mime, label, a.html)
        print('现在只需要 %s 一个文件即可播放。' % os.path.basename(a.html))
        if os.path.exists(a.output):
            os.remove(a.output)
            print('已删除不再需要的 %s' % os.path.basename(a.output))
    else:
        write_bg_js(b64, label, mime, a.output)
        print('已生成 %s（需要在页面里用 <script src="bg.js"></script> 引入）' % a.output)


if __name__ == '__main__':
    main()
