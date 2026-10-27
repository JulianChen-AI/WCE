"""Decode local images, including wxgf's mask followed by its color frame."""
from io import BytesIO
from core.image_dat import decode_dat, detect_ext


def decode_to_displayable(data, aes_key=None, xor_byte=None):
    kind = detect_ext(data)
    result = (data, kind) if kind else decode_dat(data, aes_key, xor_byte)
    if not result or result[1] != 'wxgf':
        return result
    blob = result[0]
    start = blob.find(b'\x00\x00\x00\x01')
    if start < 0:
        return None
    import av
    try:
        with av.open(BytesIO(blob[start:]), format='hevc') as container:
            image = None
            for frame in container.decode(video=0):
                image = frame.to_image().convert('RGB')
            if image is not None:
                out = BytesIO()
                image.save(out, format='JPEG', quality=88)
                return out.getvalue(), 'jpg'
    except (ValueError, av.error.FFmpegError):
        return None
    return None
