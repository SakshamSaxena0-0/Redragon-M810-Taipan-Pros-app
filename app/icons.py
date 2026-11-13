"""The icon Explorer shows for a file, as a PNG data URL, for the Shortcuts tab.

No PIL: Windows hands over the icon (PrivateExtractIconsW at the size we want, or the shell's 32 px icon), GDI gives
its pixels, and zlib packs them into a PNG.
"""
import base64
import ctypes as C
import ctypes.wintypes as W
import os
import struct
import zlib

user32 = C.WinDLL('user32', use_last_error=True)
gdi32 = C.WinDLL('gdi32', use_last_error=True)
shell32 = C.WinDLL('shell32', use_last_error=True)


class SHFILEINFOW(C.Structure):
    _fields_ = [('hIcon', W.HICON), ('iIcon', C.c_int), ('dwAttributes', W.DWORD), ('szDisplayName', W.WCHAR * 260),
                ('szTypeName', W.WCHAR * 80)]


class ICONINFO(C.Structure):
    _fields_ = [('fIcon', W.BOOL), ('xHotspot', W.DWORD), ('yHotspot', W.DWORD), ('hbmMask', W.HBITMAP),
                ('hbmColor', W.HBITMAP)]


class BITMAP(C.Structure):
    _fields_ = [('bmType', W.LONG), ('bmWidth', W.LONG), ('bmHeight', W.LONG), ('bmWidthBytes', W.LONG),
                ('bmPlanes', W.WORD), ('bmBitsPixel', W.WORD), ('bmBits', C.c_void_p)]


class BITMAPINFOHEADER(C.Structure):
    _fields_ = [('biSize', W.DWORD), ('biWidth', W.LONG), ('biHeight', W.LONG), ('biPlanes', W.WORD),
                ('biBitCount', W.WORD), ('biCompression', W.DWORD), ('biSizeImage', W.DWORD),
                ('biXPelsPerMeter', W.LONG), ('biYPelsPerMeter', W.LONG), ('biClrUsed', W.DWORD),
                ('biClrImportant', W.DWORD)]


user32.PrivateExtractIconsW.argtypes = [W.LPCWSTR, C.c_int, C.c_int, C.c_int, C.POINTER(W.HICON),
                                        C.POINTER(W.UINT), W.UINT, W.UINT]
user32.GetIconInfo.argtypes = [W.HICON, C.POINTER(ICONINFO)]
user32.DestroyIcon.argtypes = [W.HICON]
user32.GetDC.restype = W.HDC
user32.ReleaseDC.argtypes = [W.HWND, W.HDC]
shell32.SHGetFileInfoW.restype = C.c_size_t
shell32.SHGetFileInfoW.argtypes = [W.LPCWSTR, W.DWORD, C.POINTER(SHFILEINFOW), W.UINT, W.UINT]
gdi32.GetObjectW.argtypes = [W.HANDLE, C.c_int, C.c_void_p]
gdi32.GetDIBits.argtypes = [W.HDC, W.HBITMAP, W.UINT, W.UINT, C.c_void_p, C.c_void_p, W.UINT]
gdi32.DeleteObject.argtypes = [W.HANDLE]
SHGFI_ICON, SHGFI_ICONLOCATION, SHGFI_LARGEICON = 0x100, 0x1000, 0x0


def _hicon(path, size):
    """A HICON for path, as close to size px as the file offers. The caller destroys it."""
    info = SHFILEINFOW()
    sources = []
    if os.path.splitext(path)[1].lower() in ('.exe', '.dll', '.ico'):
        sources.append((path, 0))
    if shell32.SHGetFileInfoW(path, 0, C.byref(info), C.sizeof(info), SHGFI_ICONLOCATION) and info.szDisplayName:
        sources.append((os.path.expandvars(info.szDisplayName), info.iIcon))     # shortcuts: their target's icon
    for src, index in sources:
        icon, got = W.HICON(), W.UINT()
        if user32.PrivateExtractIconsW(src, index, size, size, C.byref(icon), C.byref(got), 1, 0) == 1 and icon:
            return icon
    if shell32.SHGetFileInfoW(path, 0, C.byref(info), C.sizeof(info), SHGFI_ICON | SHGFI_LARGEICON) and info.hIcon:
        return W.HICON(info.hIcon)
    return None


def _bits(dc, bitmap, w, h):
    bmi = BITMAPINFOHEADER(C.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0)        # 32-bit BGRA, top row first
    buf = C.create_string_buffer(w * h * 4)
    return buf.raw if gdi32.GetDIBits(dc, bitmap, 0, h, buf, C.byref(bmi), 0) == h else None


def _png(w, h, rgba):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xFFFFFFFF)
    rows = b''.join(b'\0' + rgba[y * w * 4:(y + 1) * w * 4] for y in range(h))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(rows, 9)) + chunk(b'IEND', b''))


def icon_data_url(path, size=64):
    """data:image/png;base64,... for the file's icon, or None if it has none we can read."""
    path = os.path.expandvars(path.strip().strip('"'))
    if not path or not os.path.exists(path):
        return None
    icon = _hicon(path, size)
    if not icon:
        return None
    ii = ICONINFO()
    try:
        if not user32.GetIconInfo(icon, C.byref(ii)) or not ii.hbmColor:
            return None
        bm = BITMAP()
        gdi32.GetObjectW(ii.hbmColor, C.sizeof(bm), C.byref(bm))
        w, h = bm.bmWidth, bm.bmHeight
        dc = user32.GetDC(None)
        try:
            color, mask = _bits(dc, ii.hbmColor, w, h), _bits(dc, ii.hbmMask, w, h)
        finally:
            user32.ReleaseDC(None, dc)
        if not color:
            return None
        px = bytearray(color)
        px[0::4], px[2::4] = px[2::4], px[0::4]                 # BGRA -> RGBA
        if not any(px[3::4]) and mask:                          # an old icon with no alpha: the mask says what shows
            px[3::4] = bytes(0 if m else 255 for m in mask[0::4])
        return 'data:image/png;base64,' + base64.b64encode(_png(w, h, bytes(px))).decode()
    finally:
        for b in (ii.hbmColor, ii.hbmMask):
            if b:
                gdi32.DeleteObject(b)
        user32.DestroyIcon(icon)
