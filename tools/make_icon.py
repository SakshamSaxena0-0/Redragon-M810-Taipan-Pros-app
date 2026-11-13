"""Draws app/assets/icon.ico: a glass mouse outline with the app's cyan-violet-pink gradient."""
import os

from PIL import Image, ImageChops, ImageDraw, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
S = 1024


def gradient(size):
    g = Image.new('RGB', (size, size))
    px = g.load()
    stops = [(0.0, (124, 243, 255)), (0.5, (180, 139, 255)), (1.0, (255, 111, 177))]
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * size)
            for (a, ca), (b, cb) in zip(stops, stops[1:]):
                if a <= t <= b:
                    k = (t - a) / (b - a)
                    px[x, y] = tuple(round(ca[i] + (cb[i] - ca[i]) * k) for i in range(3))
                    break
    return g


def main():
    img = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((40, 40, S - 40, S - 40), radius=230, fill=(14, 17, 25, 255))
    # glass sheen on the tile
    sheen = Image.new('L', (S, S), 0)
    ImageDraw.Draw(sheen).ellipse((-300, -700, S + 300, 380), fill=26)
    sheen = sheen.filter(ImageFilter.GaussianBlur(60))
    tile_mask = Image.new('L', (S, S), 0)
    ImageDraw.Draw(tile_mask).rounded_rectangle((40, 40, S - 40, S - 40), radius=230, fill=255)
    img.paste((255, 255, 255, 255), (0, 0), ImageChops.multiply(sheen, tile_mask))
    # mouse outline + wheel in the gradient
    m = Image.new('L', (S, S), 0)
    md = ImageDraw.Draw(m)
    md.rounded_rectangle((300, 190, 724, 850), radius=212, outline=255, width=58)
    md.line((512, 200, 512, 430), fill=255, width=50)
    md.rounded_rectangle((470, 300, 554, 440), radius=42, fill=255)
    glow = m.filter(ImageFilter.GaussianBlur(28))
    grad = gradient(S)
    img.paste(grad, (0, 0), glow.point(lambda v: v * 0.55))
    img.paste(grad, (0, 0), m)
    out = os.path.join(ROOT, 'app', 'assets', 'icon.ico')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    img.resize((256, 256), Image.LANCZOS).save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    img.resize((512, 512), Image.LANCZOS).save(os.path.join(ROOT, 'app', 'assets', 'icon.png'))
    print('wrote', out)


if __name__ == '__main__':
    main()
