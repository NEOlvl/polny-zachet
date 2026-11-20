"""Картинка-карточка прогресса для кнопки «Поделиться».

Только текст и простые фигуры: никаких логотипов, скриншотов и символики игр и издателей
(правила раздела «Название и товарные знаки» в SPEC.md). Шрифт DejaVu Sans лежит в app/assets/.
"""
import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.progress import CategoryProgress, ProgressSummary

ASSETS = Path(__file__).parent / "assets"
FONT_REGULAR = ASSETS / "DejaVuSans.ttf"
FONT_BOLD = ASSETS / "DejaVuSans-Bold.ttf"

SIZE = 1080
MARGIN = 90
BAR_X0, BAR_X1 = MARGIN, SIZE - MARGIN
BAR_Y0, BAR_Y1 = 600, 660
TOP_N = 3

BG = (18, 24, 38)
FG = (240, 244, 250)
MUTED = (150, 162, 184)
ACCENT = (64, 196, 128)
TRACK = (52, 62, 84)


def _font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size)


def fit_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: float) -> str:
    """Обрезать текст с многоточием, чтобы он влез в max_width пикселей."""
    if draw.textlength(text, font=font) <= max_width:
        return text
    while text and draw.textlength(text + "…", font=font) > max_width:
        text = text[:-1]
    return text.rstrip() + "…"


def top_categories(summary: ProgressSummary, n: int = TOP_N) -> list[CategoryProgress]:
    """n категорий с наибольшим процентом; при равенстве — где больше отмечено, дальше порядок контента."""
    indexed = list(enumerate(summary.categories))
    indexed.sort(key=lambda p: (-p[1].percent, -p[1].done, p[0]))
    return [c for _, c in indexed[:n]]


def _centered(draw, y: int, text: str, font, fill) -> None:
    draw.text((SIZE / 2, y), text, font=font, fill=fill, anchor="mt")


def _bar(draw, x0: int, y0: int, x1: int, y1: int, p: int) -> None:
    radius = (y1 - y0) // 2
    draw.rounded_rectangle((x0, y0, x1, y1), radius=radius, fill=TRACK)
    if p > 0:
        fill_x1 = max(x0 + (y1 - y0), x0 + (x1 - x0) * p // 100)  # минимум — на ширину скругления
        draw.rounded_rectangle((x0, y0, fill_x1, y1), radius=radius, fill=ACCENT)


def render_card(summary: ProgressSummary) -> bytes:
    """PNG 1080x1080: название, общий процент, полоска и 3 лучшие категории."""
    image = Image.new("RGB", (SIZE, SIZE), BG)
    draw = ImageDraw.Draw(image)

    _centered(draw, 80, "Полный зачёт", _font(FONT_BOLD, 76), FG)
    _centered(draw, 175, "Личный чек-лист прохождения", _font(FONT_REGULAR, 36), MUTED)
    _centered(draw, 270, f"{summary.percent}%", _font(FONT_BOLD, 230), ACCENT)
    _centered(draw, 525, f"Отмечено {summary.done} из {summary.total}", _font(FONT_REGULAR, 38), FG)
    _bar(draw, BAR_X0, BAR_Y0, BAR_X1, BAR_Y1, summary.percent)

    top = top_categories(summary)
    if top:
        draw.text((MARGIN, 715), "Лучшие категории", font=_font(FONT_BOLD, 34), fill=MUTED)
        title_font, pct_font = _font(FONT_REGULAR, 36), _font(FONT_BOLD, 36)
        y = 775
        for c in top:
            pct_text = f"{c.percent}%"
            pct_width = draw.textlength(pct_text, font=pct_font)
            title = fit_text(draw, c.title, title_font, BAR_X1 - BAR_X0 - pct_width - 30)
            draw.text((MARGIN, y), title, font=title_font, fill=FG)
            draw.text((BAR_X1, y), pct_text, font=pct_font, fill=ACCENT, anchor="ra")
            _bar(draw, BAR_X0, y + 50, BAR_X1, y + 66, c.percent)
            y += 100

    buf = io.BytesIO()
    image.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
