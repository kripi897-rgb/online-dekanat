"""
app.py — «Онлайн-деканат», дизайн 2 (UMIHack 2026, кейс «Центр-инвест»).

Логика не меняется: ocr.py, extractor.py, responder.py, rules.py вызываются так же,
формат results.json — по results.schema.json. Меняется только внешний вид:
  * своя палитра (#08090D, градиент #7C3AED -> #06B6D4), Manrope / Inter / JetBrains Mono;
  * sticky-панель с бейджами, GitHub и переключателем светлой темы;
  * hero с анимированным пайплайном, рабочая область из трёх шагов
    (плитки типов, подсказки-чипсы для вопроса), свой прогресс из 4 шагов;
  * результат: статус, ответ с value/sources, сетка полей, подсветка поля на снимке,
    пункты Положения, детали распознавания, «Скачать results.json»;
  * только иконки Lucide (inline SVG), без эмодзи.
"""

import base64
import html as _html
import io
import json
import re
import time
from pathlib import Path
from urllib.parse import quote

import streamlit as st
from streamlit.errors import StreamlitAPIException
from PIL import Image, ImageDraw, ImageOps

import ocr as ocr_engine
from extractor import (DOCUMENT_TYPES, DOCUMENT_TITLES, NOT_FOUND, detect_doc_type, detect_marks,
                       extract_fields, field_regions, field_specs, field_state, load_page_image,
                       mark_kind, pdf_line_boxes, pdf_text_layer)
from responder import build_answer
from rules import load_rules, RulesSearch, rule_text

STANDARD_QUESTION = "Содержит ли документ все обязательные сведения по Положению?"

# =====================================================================
# 1. ИКОНКИ LUCIDE
# ---------------------------------------------------------------------
# Ключ — имя иконки на lucide.dev, значение — внутренности <svg>.
# Новую иконку: открыть lucide.dev, «Copy SVG», вставить содержимое тега сюда.
# =====================================================================
LUCIDE = {
    "graduation-cap": '<path d="M21.42 10.92a1 1 0 0 0-.02-1.84L12.83 5.18a2 2 0 0 0-1.66 0L2.6 9.08a1 1 0 0 0 0 1.83l8.57 3.91a2 2 0 0 0 1.66 0z"/><path d="M22 10v6"/><path d="M6 12.5V16a6 3 0 0 0 12 0v-3.5"/>',
    "file-up": '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M12 12v6"/><path d="m15 15-3-3-3 3"/>',
    "upload": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" x2="12" y1="3" y2="15"/>',
    "folder-open": '<path d="m6 14 1.5-2.9A2 2 0 0 1 9.24 10H20a2 2 0 0 1 1.94 2.5l-1.54 6a2 2 0 0 1-1.95 1.5H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 0 0 1.67.9H18a2 2 0 0 1 2 2v2"/>',
    "message-circle-question": '<path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><path d="M12 17h.01"/>',
    "search": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    "check-circle-2": '<circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/>',
    "alert-circle": '<circle cx="12" cy="12" r="10"/><line x1="12" x2="12" y1="8" y2="12"/><line x1="12" x2="12.01" y1="16" y2="16"/>',
    "alert-triangle": '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
    "info": '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>',
    "x-circle": '<circle cx="12" cy="12" r="10"/><path d="m15 9-6 6"/><path d="m9 9 6 6"/>',
    "link-2": '<path d="M9 17H7A5 5 0 0 1 7 7h2"/><path d="M15 7h2a5 5 0 1 1 0 10h-2"/><line x1="8" x2="16" y1="12" y2="12"/>',
    "list": '<line x1="8" x2="21" y1="6" y2="6"/><line x1="8" x2="21" y1="12" y2="12"/><line x1="8" x2="21" y1="18" y2="18"/><line x1="3" x2="3.01" y1="6" y2="6"/><line x1="3" x2="3.01" y1="12" y2="12"/><line x1="3" x2="3.01" y1="18" y2="18"/>',
    "scale": '<path d="m16 16 3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1Z"/><path d="m2 16 3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1Z"/><path d="M7 21h10"/><path d="M12 3v18"/><path d="M3 7h2c2 0 5-1 7-2 2 1 5 2 7 2h2"/>',
    "file-text": '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M10 9H8"/><path d="M16 13H8"/><path d="M16 17H8"/>',
    "scan": '<path d="M3 7V5a2 2 0 0 1 2-2h2"/><path d="M17 3h2a2 2 0 0 1 2 2v2"/><path d="M21 17v2a2 2 0 0 1-2 2h-2"/><path d="M7 21H5a2 2 0 0 1-2-2v-2"/>',
    "download": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" x2="12" y1="15" y2="3"/>',
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    "arrow-right": '<path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>',
    "cpu": '<rect width="16" height="16" x="4" y="4" rx="2"/><rect width="6" height="6" x="9" y="9" rx="1"/><path d="M15 2v2"/><path d="M15 20v2"/><path d="M2 15h2"/><path d="M2 9h2"/><path d="M20 15h2"/><path d="M20 9h2"/><path d="M9 2v2"/><path d="M9 20v2"/>',
    "shield-check": '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/><path d="m9 12 2 2 4-4"/>',
    "sparkles": '<path d="M9.94 15.5A2 2 0 0 0 8.5 14.06l-6.14-1.58a.5.5 0 0 1 0-.96L8.5 9.94A2 2 0 0 0 9.94 8.5l1.58-6.14a.5.5 0 0 1 .96 0L14.06 8.5A2 2 0 0 0 15.5 9.94l6.14 1.58a.5.5 0 0 1 0 .96L15.5 14.06a2 2 0 0 0-1.44 1.44l-1.58 6.14a.5.5 0 0 1-.96 0z"/><path d="M20 3v4"/><path d="M22 5h-4"/>',
    "book-open": '<path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>',
}


def icon(name: str, size: int = 20, stroke: float = 2) -> str:
    """Inline SVG-иконка Lucide; цвет берётся из CSS (currentColor)."""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" class="ic" width="{size}" height="{size}" '
            f'viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="{stroke}" '
            f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
            f'{LUCIDE[name]}</svg>')


def icon_data_uri(name: str, color: str) -> str:
    """Иконка как data:URI — для CSS (кнопка «Проверить», зона загрузки)."""
    svg = (f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' "
           f"stroke='{color}' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>"
           f"{LUCIDE[name].replace(chr(34), chr(39))}</svg>")
    return "data:image/svg+xml," + quote(svg)


# =====================================================================
# 2. ВСПОМОГАТЕЛЬНОЕ
# =====================================================================

def esc(value) -> str:
    """Экранирование текста из OCR/ответа перед вставкой в HTML."""
    return _html.escape(str(value)).replace("\n", " ")


def md_bold(text: str) -> str:
    """**жирный** из responder.py -> <b>жирный</b> (после экранирования)."""
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", esc(text))


def render(markup: str, target=None):
    """
    Вставляет HTML. Строки склеиваются в одну: пустая строка или отступ
    в 4 пробела внутри st.markdown превратили бы HTML в абзац или код.
    """
    one_line = " ".join(line.strip() for line in markup.splitlines() if line.strip())
    (target or st).markdown(one_line, unsafe_allow_html=True)


def _compat(fn, *args, variants=(), **base):
    """
    Вызывает виджет с самым новым набором параметров, который понимает
    установленная версия Streamlit; при ошибке пробует следующий вариант.
    """
    last_error = None
    for extra in list(variants) + [{}]:
        try:
            return fn(*args, **base, **extra)
        except (TypeError, StreamlitAPIException) as e:
            last_error = e
    raise last_error


def card(key: str):
    """Контейнер-карточка: key даёт CSS-класс .st-key-<key> (Streamlit 1.39+)."""
    return _compat(st.container, variants=[{"key": key}])


def show_image(img):
    _compat(st.image, img, variants=[{"width": "stretch"}, {"use_container_width": True},
                                     {"use_column_width": True}])


def image_to_b64(img: Image.Image, max_side: int = 1400) -> str:
    """Картинка -> base64 JPEG (уменьшенная), чтобы вставить в HTML."""
    img = img.convert("RGB")
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


def make_favicon() -> Image.Image:
    """Иконка вкладки: градиентный квадрат с «шапочкой выпускника»."""
    size = 64
    grad = Image.new("RGB", (size, size))
    px = grad.load()
    for x in range(size):
        for y in range(size):
            t = (x + y) / (2 * size)
            px[x, y] = (int(124 - 118 * t), int(58 + 124 * t), int(237 - 25 * t))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=16, fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(out)
    d.polygon([(32, 14), (54, 25), (32, 36), (10, 25)], fill="white")
    d.rounded_rectangle((19, 30, 45, 46), radius=6, fill="white")
    d.line([(52, 26), (52, 40)], fill="white", width=3)
    return out


# =====================================================================
# 3. НАСТРОЙКА СТРАНИЦЫ
# =====================================================================
st.set_page_config(
    page_title="Онлайн-деканат",
    page_icon=make_favicon(),
    layout="wide",
    initial_sidebar_state="collapsed",
)


# =====================================================================


# =====================================================================
# 4. ДИЗАЙН-СИСТЕМА
# ---------------------------------------------------------------------
# Палитра: фон #08090D, поверхности #0F1117 / #161922, акцент — градиент
# фиолетовый #7C3AED -> циан #06B6D4; янтарь #F59E0B, изумруд #10B981, красный #EF4444.
# Шрифты: Manrope (заголовки), Inter (текст), JetBrains Mono (ключи, значения).
# Если Google Fonts недоступен — системные шрифты, вёрстка не ломается.
# Все классы ниже начинаются с понятного имени блока: .topbar, .hero, .pipe,
# .step, .tile, .chip, .prog, .result, .status, .fgrid, .rule, .about, .footer.
# =====================================================================

GITHUB_URL = ""          # ссылка на репозиторий решения (пусто — иконка без перехода)

LUCIDE.update({
    "clipboard-check": '<rect width="8" height="4" x="8" y="2" rx="1" ry="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><path d="m9 14 2 2 4-4"/>',
    "file-json": '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M10 12a1 1 0 0 0-1 1v1a1 1 0 0 1-1 1 1 1 0 0 1 1 1v1a1 1 0 0 0 1 1"/><path d="M14 18a1 1 0 0 0 1-1v-1a1 1 0 0 1 1-1 1 1 0 0 1-1-1v-1a1 1 0 0 0-1-1"/>',
    "pen-line": '<path d="M12 20h9"/><path d="M16.38 3.62a1 1 0 0 1 3 3L7.37 18.64a2 2 0 0 1-.86.5l-2.87.84a.5.5 0 0 1-.62-.62l.84-2.87a2 2 0 0 1 .5-.86z"/>',
    "stamp": '<path d="M5 22h14"/><path d="M19.27 13.73A2.5 2.5 0 0 0 17.5 13h-11A2.5 2.5 0 0 0 4 15.5V17a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-1.5c0-.66-.26-1.3-.73-1.77Z"/><path d="M14 13V8.5C14 7 15 7 15 5a3 3 0 0 0-3-3c-1.66 0-3 1-3 3s1 2 1 3.5V13"/>',
    "file-pen": '<path d="M12.5 22H18a2 2 0 0 0 2-2V7l-5-5H6a2 2 0 0 0-2 2v9.5"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M13.38 15.63a1 1 0 1 0-3-3l-5.01 5.01a2 2 0 0 0-.51.85l-.84 2.87a.5.5 0 0 0 .62.62l2.87-.84a2 2 0 0 0 .85-.51z"/>',
    "briefcase": '<path d="M16 20V4a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/><rect width="20" height="14" x="2" y="6" rx="2"/>',
    "github": '<path d="M15 22v-4a4.8 4.8 0 0 0-1-3.5c3 0 6-2 6-5.5.08-1.25-.27-2.48-1-3.5.28-1.15.28-2.35 0-3.5 0 0-1 0-3 1.5-2.64-.5-5.36-.5-8 0C6 2 5 2 5 2c-.3 1.15-.3 2.35 0 3.5A5.4 5.4 0 0 0 4 9c0 3.5 3 5.5 6 5.5-.39.49-.68 1.05-.85 1.65-.17.6-.22 1.23-.15 1.85v4"/><path d="M9 18c-4.51 2-5-2-7-2"/>',
    "zap": '<path d="M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 13 10h7a1 1 0 0 1 .78 1.63l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z"/>',
    "lock": '<rect width="18" height="11" x="3" y="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "file": '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/>',
})

# Шум-текстура (SVG feTurbulence) — едва заметное «зерно» на фоне
NOISE = ("data:image/svg+xml," + quote(
    "<svg xmlns='http://www.w3.org/2000/svg' width='160' height='160'>"
    "<filter id='n'><feTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='3' stitchTiles='stitch'/>"
    "<feColorMatrix values='0 0 0 0 1  0 0 0 0 1  0 0 0 0 1  0 0 0 .05 0'/></filter>"
    "<rect width='100%' height='100%' filter='url(#n)'/></svg>"))
SEARCH_ICON = icon_data_uri("search", "white")
UPLOAD_ICON = icon_data_uri("upload", "#A78BFA")

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Manrope:wght@600;700;800&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap');

:root {
  --bg: #08090D; --s1: #0F1117; --s2: #161922; --s3: #1E2230;
  --line: rgba(255,255,255,0.07); --line-2: rgba(255,255,255,0.12);
  --text: #F4F4F5; --muted: #A1A1AA; --dim: #71717A;
  --violet: #7C3AED; --cyan: #06B6D4; --amber: #F59E0B; --ok: #10B981; --err: #EF4444;
  --grad: linear-gradient(120deg, #7C3AED 0%%, #06B6D4 100%%);
  --grad-soft: linear-gradient(120deg, rgba(124,58,237,.16), rgba(6,182,212,.12));
  --glass: rgba(15,17,23,0.72);
  --r: 20px;
  --head: 'Manrope', 'Segoe UI', system-ui, sans-serif;
  --body: 'Inter', -apple-system, 'Segoe UI', Roboto, sans-serif;
  --mono: 'JetBrains Mono', ui-monospace, 'Cascadia Code', Consolas, monospace;
}

/* ---------- Основа ---------- */
.stApp {
  background:
    url("%(noise)s"),
    radial-gradient(1100px 600px at 12%% -10%%, rgba(124,58,237,.22), transparent 60%%),
    radial-gradient(900px 520px at 95%% 5%%, rgba(6,182,212,.14), transparent 60%%),
    radial-gradient(700px 500px at 50%% 110%%, rgba(124,58,237,.10), transparent 60%%),
    var(--bg);
  background-attachment: fixed;
  color: var(--text);
}
.stApp, .stApp p, .stApp label, .stApp li, .stApp input, .stApp textarea, .stApp button,
.stMarkdown, .stApp summary { font-family: var(--body); }
.ic { flex-shrink: 0; display: inline-block; vertical-align: middle; }
header[data-testid="stHeader"] { display: none; }
#MainMenu, footer { visibility: hidden; }
[data-testid="stToolbar"], [data-testid="stDecoration"], [data-testid="stStatusWidget"],
[data-testid="stAppDeployButton"], [data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"] {
  display: none !important; }
.block-container, [data-testid="stMainBlockContainer"] {
  padding-top: .75rem !important; padding-bottom: 2rem !important; max-width: 1180px; }

/* кастомный скроллбар */
::-webkit-scrollbar { width: 10px; height: 10px; }
::-webkit-scrollbar-track { background: var(--bg); }
::-webkit-scrollbar-thumb { background: linear-gradient(var(--violet), var(--cyan)); border-radius: 10px;
  border: 2px solid var(--bg); }
::selection { background: rgba(124,58,237,.45); color: #fff; }

/* ---------- Анимации ---------- */
@keyframes rise { from { opacity: 0; transform: translateY(14px); } to { opacity: 1; transform: none; } }
@keyframes shine { to { background-position: 300%% 0; } }
@keyframes flow { to { background-position: 200%% 0; } }
@keyframes pulse { 0%%,100%% { box-shadow: 0 0 0 0 rgba(124,58,237,.6); } 50%% { box-shadow: 0 0 0 10px rgba(124,58,237,0); } }
@keyframes float { 0%%,100%% { transform: translateY(0); } 50%% { transform: translateY(-6px); } }
@keyframes blink { 50%% { opacity: .35; } }
.rise { animation: rise .6s cubic-bezier(.2,.7,.2,1) both; }

/* ---------- Верхняя панель (sticky) ---------- */
.st-key-topbar {
  position: sticky; top: 10px; z-index: 50; padding: 10px 14px !important; margin-bottom: 8px;
  background: rgba(8,9,13,.72); backdrop-filter: blur(16px) saturate(140%%);
  -webkit-backdrop-filter: blur(16px) saturate(140%%);
  border: 1px solid var(--line); border-radius: 16px; box-shadow: 0 10px 30px -18px rgba(0,0,0,.8);
}
.st-key-topbar [data-testid="stHorizontalBlock"] { align-items: center; }
.brand { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.logo { width: 34px; height: 34px; border-radius: 10px; display: grid; place-items: center; color: #fff;
  background: var(--grad); box-shadow: 0 6px 20px -6px rgba(124,58,237,.9); }
.brand-name { font-family: var(--head); font-weight: 800; font-size: 1.05rem; letter-spacing: -.02em;
  background: linear-gradient(90deg, #F4F4F5, #C4B5FD 60%%, #67E8F9); -webkit-background-clip: text;
  background-clip: text; -webkit-text-fill-color: transparent; }
.badge { display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 999px;
  font-size: .72rem; font-weight: 600; color: var(--muted); border: 1px solid var(--line-2);
  background: rgba(255,255,255,.03); }
.badge .dot { width: 6px; height: 6px; border-radius: 50%%; background: var(--ok); box-shadow: 0 0 8px var(--ok);
  animation: blink 2s infinite; }
.top-actions { display: flex; justify-content: flex-end; align-items: center; gap: 8px; }
.icon-btn { width: 34px; height: 34px; border-radius: 10px; display: grid; place-items: center;
  color: var(--muted) !important; border: 1px solid var(--line-2); background: rgba(255,255,255,.02);
  transition: all .25s ease; text-decoration: none !important; }
.icon-btn:hover { color: #fff !important; border-color: rgba(124,58,237,.6); box-shadow: 0 0 18px -4px rgba(124,58,237,.7); }
.st-key-topbar .stCheckbox, .st-key-topbar [data-testid="stCheckbox"] { display: flex; justify-content: flex-end; }

/* ---------- Hero ---------- */
.hero { position: relative; text-align: center; padding: 72px 0 36px; }
.eyebrow { display: inline-flex; align-items: center; gap: 8px; padding: 6px 14px 6px 6px; border-radius: 999px;
  font-size: .8rem; color: #DDD6FE; background: var(--grad-soft); border: 1px solid rgba(124,58,237,.35); }
.eyebrow .new { padding: 2px 9px; border-radius: 999px; background: var(--grad); color: #fff; font-weight: 700;
  font-size: .68rem; letter-spacing: .06em; }
.hero h1 {
  font-family: var(--head); font-weight: 800; letter-spacing: -.045em; line-height: 1.02;
  font-size: clamp(2.6rem, 6.4vw, 5rem); margin: 26px auto 0; padding: 0 0 .08em; max-width: 900px;
  background: linear-gradient(90deg, #F4F4F5 0%%, #C4B5FD 25%%, #67E8F9 50%%, #C4B5FD 75%%, #F4F4F5 100%%);
  background-size: 300%% 100%%; -webkit-background-clip: text; background-clip: text;
  -webkit-text-fill-color: transparent; animation: shine 10s linear infinite;
}
.hero .sub { margin: 18px auto 0; max-width: 640px; font-size: 1.12rem; line-height: 1.6; color: var(--muted); }
.cta-row { display: flex; gap: 12px; justify-content: center; margin-top: 30px; flex-wrap: wrap; }
.cta { display: inline-flex; align-items: center; gap: 10px; padding: 14px 26px; border-radius: 14px;
  font-weight: 600; font-size: 1rem; color: #fff !important; text-decoration: none !important;
  background: var(--grad); box-shadow: 0 12px 40px -12px rgba(124,58,237,.9), inset 0 1px 0 rgba(255,255,255,.25);
  transition: transform .25s ease, box-shadow .25s ease; }
.cta:hover { transform: translateY(-2px); box-shadow: 0 18px 50px -12px rgba(6,182,212,.8), inset 0 1px 0 rgba(255,255,255,.3); }
.cta.ghost { background: rgba(255,255,255,.03); border: 1px solid var(--line-2); box-shadow: none; color: var(--text) !important; }
.cta.ghost:hover { border-color: rgba(6,182,212,.5); }

/* Пайплайн */
.pipe { display: grid; grid-template-columns: 1fr auto 1fr auto 1fr auto 1fr; align-items: center; gap: 10px;
  max-width: 900px; margin: 54px auto 0; }
.pnode { padding: 18px 14px; border-radius: 18px; background: var(--glass); border: 1px solid var(--line);
  backdrop-filter: blur(12px); text-align: center; transition: all .3s ease; animation: rise .6s both; }
.pnode:hover { transform: translateY(-3px); border-color: rgba(124,58,237,.45); }
.pnode .pi { width: 44px; height: 44px; margin: 0 auto 10px; border-radius: 13px; display: grid; place-items: center;
  color: #fff; background: var(--grad); box-shadow: 0 10px 26px -10px rgba(124,58,237,.9); }
.pnode:nth-child(3) .pi { animation-delay: .4s; } .pnode:nth-child(5) .pi { animation-delay: .8s; }
.pnode .pt { font-family: var(--head); font-weight: 700; font-size: .95rem; }
.pnode .ps { font-size: .76rem; color: var(--dim); margin-top: 3px; }
.plink { width: 42px; height: 2px; border-radius: 2px;
  background: linear-gradient(90deg, rgba(124,58,237,.1), #7C3AED, #06B6D4, rgba(6,182,212,.1));
  background-size: 200%% 100%%; animation: flow 2.4s linear infinite; position: relative; }
.plink::after { content: ""; position: absolute; right: -2px; top: -3px; border: 4px solid transparent;
  border-left: 6px solid #06B6D4; }

/* Цифры доверия */
.stats { display: flex; justify-content: center; gap: 36px; margin-top: 40px; flex-wrap: wrap; }
.stat { text-align: center; }
.stat b { display: block; font-family: var(--head); font-size: 1.6rem; font-weight: 800; letter-spacing: -.03em;
  background: var(--grad); -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent; }
.stat span { font-size: .78rem; color: var(--dim); }

/* ---------- Секции ---------- */
.sec-head { display: flex; align-items: end; justify-content: space-between; gap: 16px; margin: 56px 0 18px; flex-wrap: wrap; }
.sec-kicker { font-size: .72rem; font-weight: 700; letter-spacing: .16em; color: #A78BFA; }
.sec-title { font-family: var(--head); font-weight: 800; font-size: 2rem; letter-spacing: -.035em; margin-top: 6px; }
.sec-note { color: var(--dim); font-size: .9rem; max-width: 420px; }

/* Шаги формы: стеклянные карточки с градиентной рамкой при наведении */
.st-key-step_doc, .st-key-step_type, .st-key-step_q {
  background: linear-gradient(var(--s1), var(--s1)) padding-box,
              linear-gradient(140deg, rgba(255,255,255,.09), rgba(255,255,255,.03)) border-box;
  border: 1px solid transparent !important; border-radius: var(--r); padding: 22px 22px 18px !important;
  box-shadow: 0 20px 50px -30px rgba(0,0,0,.9); transition: all .35s ease; animation: rise .6s both;
}
.st-key-step_doc:hover, .st-key-step_type:hover, .st-key-step_q:hover {
  background: linear-gradient(var(--s1), var(--s1)) padding-box, var(--grad) border-box;
  box-shadow: 0 24px 60px -30px rgba(124,58,237,.55);
}
.step-head { display: flex; align-items: center; gap: 12px; margin-bottom: 14px; }
.step-num { width: 30px; height: 30px; border-radius: 9px; display: grid; place-items: center; font-family: var(--mono);
  font-size: .8rem; font-weight: 500; color: #fff; background: var(--grad); }
.step-title { font-family: var(--head); font-weight: 700; font-size: 1.08rem; letter-spacing: -.02em; }
.step-sub { font-size: .8rem; color: var(--dim); margin-left: auto; display: flex; align-items: center; gap: 6px; }

/* Зона загрузки */
[data-testid="stFileUploaderDropzone"] {
  background: radial-gradient(400px 160px at 50%% 0%%, rgba(124,58,237,.12), transparent 70%%), var(--s2) !important;
  border: 1.5px dashed rgba(167,139,250,.45) !important; border-radius: 16px !important;
  padding: 34px 20px !important; min-height: 190px; transition: all .3s ease; }
[data-testid="stFileUploaderDropzone"]:hover { border-color: #22D3EE !important;
  box-shadow: inset 0 0 0 1px rgba(6,182,212,.25), 0 0 40px -14px rgba(6,182,212,.6); }
[data-testid="stFileUploaderDropzoneInstructions"] [data-testid="stIconMaterial"],
[data-testid="stFileUploaderDropzoneInstructions"] svg { display: none !important; }
[data-testid="stFileUploaderDropzoneInstructions"] > div::before {
  content: ""; display: block; width: 30px; height: 30px; margin-bottom: 8px;
  background: url("%(upload)s") no-repeat center / contain; animation: float 3s ease-in-out infinite; }
[data-testid="stFileUploaderDropzone"] button { border-radius: 10px !important; background: rgba(124,58,237,.18) !important;
  border: 1px solid rgba(167,139,250,.4) !important; color: #EDE9FE !important; }
.st-key-step_doc [data-testid="stImage"] img { max-height: 280px; object-fit: contain; border-radius: 12px;
  border: 1px solid var(--line-2); background: #fff; }

/* Плитки типа документа: HTML-плитка + прозрачная кнопка поверх неё */
[class*="st-key-tile_"] { position: relative; }
[class*="st-key-tile_"] .stButton { position: absolute; inset: 0; z-index: 3; }
[class*="st-key-tile_"] .stButton button { width: 100%%; height: 100%%; opacity: 0; cursor: pointer; }
.tile { height: 100%%; min-height: 128px; padding: 16px; border-radius: 16px; background: var(--s2);
  border: 1px solid var(--line); transition: all .25s ease; position: relative; }
[class*="st-key-tile_"]:hover .tile { border-color: rgba(167,139,250,.5); transform: translateY(-2px); }
.tile .ti { width: 38px; height: 38px; border-radius: 11px; display: grid; place-items: center; color: #C4B5FD;
  background: rgba(124,58,237,.14); margin-bottom: 12px; }
.tile .tn { font-family: var(--head); font-weight: 700; font-size: .95rem; line-height: 1.25; }
.tile .tf { font-size: .74rem; color: var(--dim); margin-top: 4px; font-family: var(--mono); }
.tile .tick { position: absolute; top: 12px; right: 12px; width: 22px; height: 22px; border-radius: 50%%;
  display: none; place-items: center; color: #fff; background: var(--grad); }
.tile.on { background: linear-gradient(var(--s2), var(--s2)) padding-box, var(--grad) border-box;
  border: 1.5px solid transparent; box-shadow: 0 0 34px -12px rgba(124,58,237,.8); }
.tile.on .tick { display: grid; } .tile.on .ti { color: #fff; background: var(--grad); }

/* Поля ввода */
.stTextArea textarea { background: var(--s2) !important; border: 1px solid var(--line-2) !important;
  border-radius: 14px !important; color: var(--text) !important; font-size: .98rem !important; line-height: 1.55 !important; }
.stTextArea textarea:focus { border-color: #8B5CF6 !important; box-shadow: 0 0 0 4px rgba(124,58,237,.18) !important; }
.stTextArea [data-baseweb="textarea"], .stTextArea [data-baseweb="base-input"] { background: transparent !important; border: none !important; }
.stCheckbox label p, [data-testid="stCheckbox"] label p { color: var(--muted); font-size: .88rem; }

/* Подсказки-чипсы (кнопки) */
[class*="st-key-chip_"] button {
  border-radius: 999px !important; padding: 6px 14px !important; min-height: 0 !important;
  background: rgba(255,255,255,.03) !important; border: 1px solid var(--line-2) !important;
  color: var(--muted) !important; font-size: .82rem !important; transition: all .2s ease; }
[class*="st-key-chip_"] button p { font-size: .82rem !important; }
[class*="st-key-chip_"] button:hover { color: #fff !important; border-color: rgba(6,182,212,.6) !important;
  background: rgba(6,182,212,.08) !important; }

/* Главная кнопка: иконка search через CSS, свечение, «нажатие» */
.st-key-check_btn button {
  width: 100%%; min-height: 58px; border: none !important; border-radius: 16px !important;
  background: var(--grad) !important; color: #fff !important; font-family: var(--head); font-weight: 700;
  font-size: 1.05rem; letter-spacing: -.01em;
  box-shadow: 0 14px 40px -14px rgba(124,58,237,.95), inset 0 1px 0 rgba(255,255,255,.25);
  transition: transform .2s ease, box-shadow .3s ease, filter .3s ease; }
.st-key-check_btn button p { font-family: var(--head); font-weight: 700; font-size: 1.05rem; }
.st-key-check_btn button::before { content: ""; width: 19px; height: 19px; margin-right: 10px;
  background: url("%(search)s") no-repeat center / contain; }
.st-key-check_btn button:hover { transform: translateY(-2px); filter: brightness(1.08);
  box-shadow: 0 20px 60px -14px rgba(6,182,212,.85), inset 0 1px 0 rgba(255,255,255,.3); }
.st-key-check_btn button:active { transform: scale(.985); }

/* ---------- Прогресс ---------- */
.prog { margin-top: 26px; padding: 22px 24px; border-radius: var(--r); background: var(--glass);
  border: 1px solid var(--line); backdrop-filter: blur(12px); }
.prog-row { display: grid; grid-template-columns: repeat(4, 1fr); position: relative; }
.prog-line { position: absolute; left: 12.5%%; right: 12.5%%; top: 21px; height: 2px; background: var(--line-2); }
.prog-line > div { height: 100%%; background: var(--grad); transition: width .5s ease; box-shadow: 0 0 12px rgba(6,182,212,.7); }
.pstep { text-align: center; position: relative; z-index: 1; color: var(--dim); font-size: .85rem; font-weight: 500; }
.pstep .pc { width: 44px; height: 44px; margin: 0 auto 10px; border-radius: 14px; display: grid; place-items: center;
  background: var(--s2); border: 1px solid var(--line-2); transition: all .3s ease; }
.pstep.done { color: var(--muted); } .pstep.done .pc { color: var(--ok); border-color: rgba(16,185,129,.4); background: rgba(16,185,129,.1); }
.pstep.active { color: var(--text); } .pstep.active .pc { color: #fff; background: var(--grad); border-color: transparent; animation: pulse 1.5s infinite; }
.prog-meta { display: flex; justify-content: space-between; margin-top: 16px; font-size: .84rem; color: var(--muted); }
.prog-meta b { font-family: var(--mono); color: var(--text); }

/* ---------- Результат ---------- */
.st-key-result { margin-top: 30px; padding: 28px !important; border-radius: 24px;
  background: linear-gradient(var(--s1), var(--s1)) padding-box, linear-gradient(160deg, rgba(124,58,237,.6), rgba(6,182,212,.25) 40%%, rgba(255,255,255,.06)) border-box;
  border: 1px solid transparent !important; box-shadow: 0 40px 90px -50px rgba(124,58,237,.6); animation: rise .6s both; }
.res-top { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 18px; }
.res-kicker { font-size: .72rem; font-weight: 700; letter-spacing: .16em; color: var(--dim); }
.meta-row { display: flex; gap: 8px; flex-wrap: wrap; }
.meta { display: inline-flex; align-items: center; gap: 6px; padding: 5px 11px; border-radius: 10px; font-size: .8rem;
  color: var(--muted); background: rgba(255,255,255,.03); border: 1px solid var(--line); }
.meta b { color: var(--text); font-weight: 600; }

.status { display: flex; gap: 18px; padding: 24px; border-radius: 20px; position: relative; overflow: hidden;
  background: linear-gradient(120deg, var(--sbg), rgba(255,255,255,.015) 70%%); border: 1px solid var(--sbr);
  box-shadow: 0 24px 60px -36px var(--sc); animation: rise .5s both; }
.status::after { content: ""; position: absolute; right: -60px; top: -60px; width: 200px; height: 200px; border-radius: 50%%;
  background: radial-gradient(circle, var(--sbg), transparent 70%%); }
.status.ok   { --sc: #10B981; --sbg: rgba(16,185,129,.16); --sbr: rgba(16,185,129,.35); }
.status.warn { --sc: #F59E0B; --sbg: rgba(245,158,11,.15); --sbr: rgba(245,158,11,.35); }
.status.err  { --sc: #EF4444; --sbg: rgba(239,68,68,.14); --sbr: rgba(239,68,68,.35); }
.status .si { width: 52px; height: 52px; border-radius: 16px; display: grid; place-items: center; flex-shrink: 0;
  color: var(--sc); background: rgba(8,9,13,.5); border: 1px solid var(--sbr); }
.status .sk { font-family: var(--mono); font-size: .74rem; color: var(--sc); letter-spacing: .04em; }
.status .st { font-family: var(--head); font-weight: 800; font-size: 1.45rem; letter-spacing: -.03em; margin-top: 2px; }
.status .sd { color: #D4D4D8; margin-top: 6px; font-size: .95rem; line-height: 1.55; }
.status ul { list-style: none; padding: 0; margin: 12px 0 0; }
.status li { display: flex; gap: 8px; align-items: baseline; margin: 6px 0; color: #E4E4E7; font-size: .92rem; }
.status li code, .chip-src, .val code { font-family: var(--mono); }
.status li code { font-size: .78rem; color: var(--muted); background: rgba(255,255,255,.05); padding: 1px 6px; border-radius: 6px; }

.answer { margin-top: 14px; padding: 22px 24px; border-radius: 20px; background: var(--s2); border: 1px solid var(--line); }
.answer .aq { display: flex; align-items: center; gap: 8px; font-size: .82rem; color: var(--dim); }
.answer .at { font-family: var(--head); font-weight: 700; font-size: 1.3rem; line-height: 1.45; letter-spacing: -.02em; margin-top: 8px; }
.val { display: inline-flex; align-items: center; gap: 8px; margin-top: 14px; padding: 7px 12px; border-radius: 10px;
  font-size: .84rem; color: var(--muted); background: #0B0C11; border: 1px solid var(--line-2); }
.val code { color: #67E8F9; background: none; font-size: .86rem; padding: 0; }
.val.null code { color: #FCA5A5; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-top: 12px; }
.chips .lbl { font-size: .76rem; color: var(--dim); margin-right: 4px; }
.chip-src { display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 999px; font-size: .76rem;
  color: #DDD6FE; background: rgba(124,58,237,.14); border: 1px solid rgba(167,139,250,.32); }
.chip-src.field { color: #A5F3FC; background: rgba(6,182,212,.1); border-color: rgba(6,182,212,.32); }
.chip-src.none { font-family: var(--body); color: var(--dim); background: rgba(255,255,255,.03); border-color: var(--line-2); }

.blk { display: flex; align-items: center; gap: 10px; margin: 34px 0 14px; font-family: var(--head); font-weight: 700;
  font-size: 1.12rem; letter-spacing: -.02em; }
.blk .ic { color: #A78BFA; }
.blk .cnt { font-family: var(--mono); font-size: .74rem; font-weight: 500; color: var(--muted); padding: 2px 9px;
  border-radius: 999px; background: rgba(255,255,255,.05); }

/* сетка полей */
.fgrid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.fcard { padding: 16px 16px 14px; border-radius: 16px; background: var(--s2); border: 1px solid var(--line);
  position: relative; overflow: hidden; transition: all .3s cubic-bezier(.4,0,.2,1); animation: rise .5s both; }
.fcard::before { content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 3px; background: var(--fc); }
.fcard:hover { transform: translateY(-2px); border-color: var(--line-2); box-shadow: 0 16px 34px -22px var(--fc); }
.fcard.ok { --fc: #10B981; } .fcard.miss { --fc: #EF4444; }
.fcard .fh { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.fcard .fl { font-size: .72rem; font-weight: 600; letter-spacing: .06em; text-transform: uppercase; color: var(--dim); }
.fcard .fh .ic { color: var(--fc); }
.fcard .fv { margin-top: 8px; font-size: 1.02rem; font-weight: 600; line-height: 1.4; word-break: break-word; }
.fcard.miss .fv { color: #FCA5A5; font-weight: 500; }
.conf { display: inline-flex; align-items: center; gap: 6px; margin-top: 10px; padding: 2px 9px; border-radius: 999px;
  font-size: .72rem; font-weight: 600; border: 1px solid var(--cb); color: var(--cc); background: var(--cg); }
.conf i { width: 7px; height: 7px; border-radius: 50%%; background: var(--cc); }
.conf.hi  { --cc: #34D399; --cb: rgba(16,185,129,.35); --cg: rgba(16,185,129,.08); }
.conf.mid { --cc: #FBBF24; --cb: rgba(245,158,11,.4);  --cg: rgba(245,158,11,.08); }
.conf.lo  { --cc: #F87171; --cb: rgba(239,68,68,.45);  --cg: rgba(239,68,68,.08); }
.conf-note { display: flex; align-items: center; gap: 8px; margin: -4px 0 12px; padding: 10px 14px; border-radius: 12px;
  font-size: .88rem; color: #FDE68A; background: rgba(245,158,11,.08); border: 1px solid rgba(245,158,11,.3); }
.fcard .fk { margin-top: 10px; display: flex; justify-content: space-between; font-family: var(--mono); font-size: .68rem; color: #52525B; }

/* пункты Положения */
.rule { display: flex; gap: 14px; padding: 16px 18px; margin-bottom: 10px; border-radius: 16px; background: var(--s2);
  border: 1px solid var(--line); transition: border-color .3s ease; }
.rule:hover { border-color: rgba(167,139,250,.35); }
.rule.cited { background: linear-gradient(120deg, rgba(124,58,237,.12), rgba(6,182,212,.05)); border-color: rgba(167,139,250,.35); }
.rule .rn { flex-shrink: 0; width: 54px; height: 54px; border-radius: 14px; display: grid; place-items: center;
  font-family: var(--mono); font-weight: 500; color: #EDE9FE; background: rgba(124,58,237,.18); border: 1px solid rgba(167,139,250,.3); }
.rule .rb { flex: 1; min-width: 0; }
.rule .rh { display: flex; justify-content: space-between; font-size: .76rem; color: var(--dim); }
.rule .rh b { font-family: var(--mono); color: var(--text); font-weight: 500; }
.rule .tag { font-family: var(--mono); font-size: .7rem; color: #A5F3FC; }
.rbar { height: 5px; border-radius: 9px; background: rgba(255,255,255,.06); margin: 7px 0 10px; overflow: hidden; }
.rbar > div { height: 100%%; border-radius: 9px; background: var(--grad); }
.rule .rt { font-size: .92rem; line-height: 1.55; color: #D4D4D8; }

/* выбор поля для подсветки */
[data-testid="stPills"] button, .stPills button { border-radius: 999px !important; }
.hl-info { padding: 20px; border-radius: 18px; background: var(--s2); border: 1px solid var(--line); }
.hl-info .hk { font-family: var(--mono); font-size: .74rem; color: var(--dim); }
.hl-info .hv { font-family: var(--head); font-weight: 700; font-size: 1.2rem; margin-top: 6px; }
.hl-info .hw { color: var(--muted); font-size: .86rem; margin-top: 10px; line-height: 1.6; }
.st-key-result [data-testid="stImage"] img { border-radius: 14px; border: 1px solid var(--line-2); }

/* раскрывающиеся блоки */
.disc { margin-top: 10px; border-radius: 16px; background: var(--s2); border: 1px solid var(--line); overflow: hidden; }
.disc summary { list-style: none; cursor: pointer; display: flex; align-items: center; gap: 10px; padding: 14px 16px;
  font-weight: 600; font-size: .94rem; }
.disc summary::-webkit-details-marker { display: none; }
.disc summary .ic { color: #A78BFA; } .disc summary .chev { margin-left: auto; color: var(--dim); transition: transform .3s ease; }
.disc[open] summary .chev { transform: rotate(180deg); }
.disc .sub { color: var(--dim); font-weight: 500; font-size: .8rem; }
.disc .body { padding: 0 16px 16px; }
.mono { font-family: var(--mono); font-size: .83rem; line-height: 1.7; white-space: pre-wrap; color: #D4D4D8;
  background: #07080B; border: 1px solid var(--line); border-radius: 12px; padding: 16px 18px; max-height: 420px; overflow: auto; }
.disc .body img { width: 100%%; display: block; border-radius: 12px; }

/* скачать results.json */
.dl { display: flex; align-items: center; justify-content: space-between; gap: 14px; margin-top: 24px; padding: 18px 20px;
  border-radius: 18px; text-decoration: none !important; color: var(--text) !important;
  background: linear-gradient(var(--s2), var(--s2)) padding-box, var(--grad) border-box; border: 1px solid transparent;
  transition: all .3s ease; }
.dl:hover { transform: translateY(-2px); box-shadow: 0 18px 50px -24px rgba(6,182,212,.8); }
.dl .dli { width: 44px; height: 44px; border-radius: 13px; display: grid; place-items: center; color: #fff; background: var(--grad); }
.dl .dlt { font-family: var(--head); font-weight: 700; } .dl .dls { font-size: .8rem; color: var(--dim); font-family: var(--mono); }
.dl .dla { margin-left: auto; color: var(--muted); }

/* ---------- О сервисе и футер ---------- */
.about { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }
.feat { padding: 22px; border-radius: 20px; background: var(--glass); border: 1px solid var(--line); backdrop-filter: blur(12px);
  transition: all .3s ease; }
.feat:hover { transform: translateY(-3px); border-color: rgba(167,139,250,.4); }
.feat .fi { width: 40px; height: 40px; border-radius: 12px; display: grid; place-items: center; color: #fff; background: var(--grad); }
.feat h4 { font-family: var(--head); font-weight: 700; font-size: 1.02rem; margin: 14px 0 6px; letter-spacing: -.02em; }
.feat p { color: var(--muted); font-size: .88rem; line-height: 1.6; margin: 0; }
.empty { margin-top: 26px; padding: 34px; border-radius: 20px; text-align: center; color: var(--dim);
  border: 1px dashed var(--line-2); background: rgba(255,255,255,.015); }
.empty .ic { color: #A78BFA; margin-bottom: 10px; }
.footer { margin-top: 64px; padding: 22px 0 6px; border-top: 1px solid var(--line); display: flex; justify-content: space-between;
  align-items: center; gap: 10px; flex-wrap: wrap; color: #52525B; font-size: .8rem; }
.footer .fb { display: flex; align-items: center; gap: 8px; color: var(--dim); }

/* ---------- Адаптивность ---------- */
@media (max-width: 1024px) {
  .fgrid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .pipe { grid-template-columns: 1fr 1fr; } .plink { display: none; }
}
@media (max-width: 768px) {
  .hero { padding-top: 40px; } .sec-title { font-size: 1.5rem; }
  .fgrid, .about { grid-template-columns: 1fr; }
  .prog-row { grid-template-columns: repeat(2, 1fr); row-gap: 14px; } .prog-line { display: none; }
  .badge.hide-m { display: none; } .stats { gap: 20px; }
}
</style>
"""

# Светлая тема: переопределяем только переменные и фон
LIGHT_CSS = """
<style>
:root { --bg: #F7F7FA; --s1: #FFFFFF; --s2: #F4F4F8; --s3: #ECECF3; --line: rgba(15,17,23,.08); --line-2: rgba(15,17,23,.14);
  --text: #18181B; --muted: #52525B; --dim: #71717A; --glass: rgba(255,255,255,.75); }
.stApp { background: radial-gradient(1100px 600px at 12% -10%, rgba(124,58,237,.12), transparent 60%),
  radial-gradient(900px 520px at 95% 5%, rgba(6,182,212,.10), transparent 60%), var(--bg) !important; }
.hero h1, .brand-name { background-image: linear-gradient(90deg, #18181B, #7C3AED 35%, #0891B2 65%, #18181B) !important; }
.st-key-topbar { background: rgba(255,255,255,.75) !important; }
.status .sd, .status li, .rule .rt, .mono { color: #3F3F46 !important; }
.mono { background: #FAFAFC !important; } .val { background: #FFFFFF !important; } .val code { color: #0E7490 !important; }
.fcard .fk { color: #A1A1AA; } .stTextArea textarea { color: #18181B !important; }
</style>
"""

st.markdown(CSS % {"noise": NOISE, "upload": UPLOAD_ICON, "search": SEARCH_ICON}, unsafe_allow_html=True)


# =====================================================================
# 5. РЕСУРСЫ И СОСТОЯНИЕ
# =====================================================================
@st.cache_resource
def get_rules_search():
    rules = load_rules("rules.md")
    return RulesSearch(rules), rules


RULES_SEARCH, RULES = get_rules_search()
TYPE_KEYS = list(DOCUMENT_TYPES.keys())

st.session_state.setdefault("doc_type", TYPE_KEYS[0])
st.session_state.setdefault("question", "")

# (короткая подпись чипсы, полный текст вопроса, который подставится в поле)
QUESTION_CHIPS = [
    ("Все ли сведения на месте?", "Содержит ли документ все обязательные сведения?"),
    ("ФИО студента", "Укажи значение поля «ФИО студента»"),
    ("Кто принимает решение?", "Кто принимает окончательное решение?"),
]

STEPS = [("file-up", "Читаю документ"), ("scan", "Разбираю поля"),
         ("scale", "Ищу в Положении"), ("check-circle-2", "Готовлю ответ")]


def type_icon(doc_type: str) -> str:
    """Иконка плитки по названию типа из конфига (новые типы получают «file»)."""
    t = DOCUMENT_TITLES.get(doc_type, "").lower()
    return ("file-text" if "обучени" in t else "file-pen" if "отпуск" in t or "заявлени" in t
            else "briefcase" if "практик" in t else "file")


def set_question(text: str):
    st.session_state["question"] = text


def pick_type(doc_type: str):
    st.session_state["doc_type"] = doc_type


def progress_html(step: int) -> str:
    pct = min(100, (step + 1) * 25)
    nodes = ""
    for i, (ic, label) in enumerate(STEPS):
        state = "done" if i < step else "active" if i == step else ""
        nodes += (f'<div class="pstep {state}"><div class="pc">'
                  f'{icon("check" if state == "done" else ic, 18)}</div>{label}</div>')
    line = max(0, min(100, step / (len(STEPS) - 1) * 100))
    return (f'<div class="prog rise"><div class="prog-row"><div class="prog-line"><div style="width:{line:.0f}%">'
            f'</div></div>{nodes}</div><div class="prog-meta"><span>{STEPS[min(step, 3)][1]}…</span>'
            f'<b>{pct}%</b></div></div>')


def sources_html(sources) -> str:
    if not sources:
        return '<span class="chip-src none">нет источника — ответа в документе и Положении нет</span>'
    return "".join(
        f'<span class="chip-src {"field" if s.startswith("field:") else ""}">'
        f'{icon("list" if s.startswith("field:") else "scale", 12)}{esc(s)}</span>' for s in sources)


# =====================================================================
# 6. ВЕРХНЯЯ ПАНЕЛЬ (sticky): логотип, бейджи, GitHub, тема
# =====================================================================
with card("topbar"):
    c_brand, c_act = _compat(st.columns, [5, 1.3], variants=[{"vertical_alignment": "center"}])
    with c_brand:
        render(f'''
            <div class="brand">
              <div class="logo">{icon("graduation-cap", 18)}</div>
              <span class="brand-name">Онлайн-деканат</span>
              <span class="badge"><span class="dot"></span>UMIHack 2026</span>
              <span class="badge hide-m">Кейс «Центр-инвест»</span>
              <span class="badge hide-m">{icon("lock", 12)}Без API</span>
            </div>''')
    with c_act:
        gh = (f'<a class="icon-btn" href="{esc(GITHUB_URL)}" target="_blank" title="Репозиторий">{icon("github", 17)}</a>'
              if GITHUB_URL else f'<span class="icon-btn" title="Добавьте GITHUB_URL в app.py">{icon("github", 17)}</span>')
        cc1, cc2 = st.columns([1, 1.4])
        with cc1:
            render(f'<div class="top-actions">{gh}</div>')
        with cc2:
            light = _compat(st.toggle, "Светлая", key="light_theme", value=False,
                            variants=[{"label_visibility": "collapsed"}]) if hasattr(st, "toggle") else False

if light:
    st.markdown(LIGHT_CSS, unsafe_allow_html=True)


# =====================================================================
# 7. HERO
# =====================================================================
render(f'''
    <section class="hero">
      <span class="eyebrow rise"><span class="new">v1.0</span>Цифровой секретарь деканата</span>
      <h1 class="rise">Документ проверен<br>за несколько секунд</h1>
      <p class="sub rise">Загрузите справку или заявление — сервис распознает текст, соберёт карточку полей,
        проверит комплектность по Положению и ответит со ссылкой на источник.</p>
      <div class="cta-row rise">
        <a class="cta" href="#work">{icon("zap", 18)}Попробовать</a>
        <a class="cta ghost" href="#about">{icon("info", 18)}Как это работает</a>
      </div>
      <div class="pipe">
        <div class="pnode"><div class="pi">{icon("file-up", 20)}</div><div class="pt">Фото</div><div class="ps">JPG · PNG · PDF</div></div>
        <div class="plink"></div>
        <div class="pnode"><div class="pi">{icon("scan", 20)}</div><div class="pt">OCR</div><div class="ps">EasyOCR локально</div></div>
        <div class="plink"></div>
        <div class="pnode"><div class="pi">{icon("list", 20)}</div><div class="pt">Поля</div><div class="ps">даты в ISO, подпись, печать</div></div>
        <div class="plink"></div>
        <div class="pnode"><div class="pi">{icon("check-circle-2", 20)}</div><div class="pt">Ответ</div><div class="ps">со ссылкой на пункт</div></div>
      </div>
      <div class="stats">
        <div class="stat"><b>3</b><span>типа документов</span></div>
        <div class="stat"><b>1.0</b><span>точность на открытом наборе</span></div>
        <div class="stat"><b>0</b><span>внешних API</span></div>
      </div>
    </section>''')


# =====================================================================
# 8. РАБОЧАЯ ОБЛАСТЬ: три шага
# =====================================================================
render(f'''
    <div class="sec-head" id="work">
      <div><div class="sec-kicker">РАБОЧАЯ ОБЛАСТЬ</div><div class="sec-title">Проверка документа</div></div>
      <div class="sec-note">Три шага: файл, тип, вопрос. Пустой вопрос — самопроверка комплектности (п. 6.5).</div>
    </div>''')

col_l, col_r = st.columns([1, 1.15], gap="large")

with col_l:
    with card("step_doc"):
        render(f'<div class="step-head"><span class="step-num">01</span><span class="step-title">Документ</span>'
               f'<span class="step-sub">{icon("file-up", 14)}JPG, PNG, PDF</span></div>')
        uploaded = st.file_uploader("Перетащите фото сюда или нажмите", type=["jpg", "jpeg", "png", "pdf"],
                                    label_visibility="collapsed", key="uploader")
        if uploaded and uploaded.type.startswith("image"):
            show_image(ImageOps.exif_transpose(Image.open(uploaded)))
        elif uploaded:
            render(f'<div class="step-sub" style="margin:8px 0 0">{icon("info", 14)}PDF: обрабатывается первая страница</div>')

with col_r:
    with card("step_type"):
        render(f'<div class="step-head"><span class="step-num">02</span><span class="step-title">Тип документа</span>'
               f'<span class="step-sub">{icon("folder-open", 14)}{len(TYPE_KEYS)} типа в конфиге</span></div>')
        tcols = st.columns(len(TYPE_KEYS), gap="small")
        for tc, tkey in zip(tcols, TYPE_KEYS):
            with tc:
                with card(f"tile_{tkey}"):
                    on = " on" if st.session_state["doc_type"] == tkey else ""
                    render(f'''<div class="tile{on}"><span class="tick">{icon("check", 13)}</span>
                        <div class="ti">{icon(type_icon(tkey), 19)}</div>
                        <div class="tn">{esc(DOCUMENT_TITLES.get(tkey, tkey))}</div>
                        <div class="tf">{len(DOCUMENT_TYPES[tkey])} полей</div></div>''')
                    st.button("Выбрать", key=f"pick_{tkey}", on_click=pick_type, args=(tkey,))
        auto_type = st.checkbox("Определить тип автоматически по тексту", value=True, key="auto_type")

    with card("step_q"):
        render(f'<div class="step-head"><span class="step-num">03</span><span class="step-title">Ваш вопрос</span>'
               f'<span class="step-sub">{icon("message-circle-question", 14)}или оставьте пустым</span></div>')
        question = st.text_area("Вопрос", height=96, key="question", label_visibility="collapsed",
                                placeholder="Например: Укажи значение поля «Дата выдачи»")
        ch = st.columns([1.25, 0.85, 1.1], gap="small")
        for i, (c, (short, full)) in enumerate(zip(ch, QUESTION_CHIPS)):
            with c:
                st.button(short, key=f"chip_{i}", on_click=set_question, args=(full,), help=full)

st.write("")
doc_type = st.session_state["doc_type"]
check = _compat(st.button, "Проверить документ", type="primary", key="check_btn",
                variants=[{"width": "stretch"}, {"use_container_width": True}])


# =====================================================================
# 9. ОБРАБОТКА (логика та же, что в рабочей версии)
# =====================================================================
if check:
    if not uploaded:
        st.error("Сначала загрузите документ.")
    else:
        q_text = question.strip() or STANDARD_QUESTION
        progress = st.empty()
        render(progress_html(0), progress)

        file_bytes = uploaded.getvalue()
        is_pdf = uploaded.name.lower().endswith(".pdf")
        try:
            page_image = load_page_image(file_bytes, uploaded.name)
            text, boxes, text_source = "", [], ""
            if is_pdf:
                text = pdf_text_layer(file_bytes)
                if text.strip():
                    text_source = "текстовый слой PDF"
                    boxes = pdf_line_boxes(file_bytes)
            if not text.strip():
                text, boxes = ocr_engine.recognize_from_file(file_bytes, uploaded.name)
                info = ocr_engine.LAST_INFO
                text_source = (f"{'Tesseract' if info.get('engine') == 'tesseract' else 'EasyOCR'}"
                               f" · уверенность {info.get('confidence', 0):.2f}"
                               + (f" · ненадёжных фрагментов: {info['low_fragments']}" if info.get("low_fragments") else "")
                               + (f" · наклон {info['skew']:+.1f}°" if info.get("skew") else ""))
        except Exception as e:
            progress.empty()
            st.error(f"Ошибка чтения документа: {e}")
            st.stop()

        render(progress_html(1), progress)
        detected = detect_doc_type(text)
        if auto_type and detected:
            doc_type = detected
            st.session_state["doc_type"] = detected      # плитка типа подсветится правильно
        fields = extract_fields(text, doc_type, boxes, page_image)
        marks = detect_marks(page_image)

        render(progress_html(2), progress)
        result = build_answer(fields, doc_type, question=q_text)
        candidates = RULES_SEARCH.find(q_text, top_k=3)

        render(progress_html(3), progress)
        time.sleep(0.4)
        progress.empty()

        buf = io.BytesIO()
        page_image.save(buf, format="PNG")
        st.session_state["result"] = {
            "question": q_text, "doc_type": doc_type, "detected": detected,
            "auto_used": bool(auto_type and detected), "text": text, "text_source": text_source,
            "boxes": boxes, "marks": marks.get("debug", []),
            "regions": field_regions(doc_type, boxes, marks.get("debug", []), fields),
            "fields": fields, "validation": result["validation"], "answer": result["answers"][0],
            "candidates": candidates, "file_name": uploaded.name, "page_png": buf.getvalue(),
        }
        if hasattr(st, "rerun"):
            st.rerun()          # перерисовать страницу: подсветится найденный тип документа


# =====================================================================
# 10. РЕЗУЛЬТАТ
# =====================================================================
def highlight_image(png_bytes: bytes, boxes: list, marks: list) -> Image.Image:
    """Красные рамки — текст OCR; синие — найденные подпись и печать."""
    base = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    line_w = max(2, round(min(base.size) / 600))
    for b in boxes:
        pts = [tuple(p) for p in b["bbox"]]
        draw.polygon(pts, fill=(239, 68, 68, 38))
        draw.line(pts + [pts[0]], fill=(239, 68, 68, 230), width=line_w)
    for m in marks:
        if m.get("kind"):
            x0, y0, x1, y1 = m["box"]
            pad = 6
            draw.rectangle((x0 - pad, y0 - pad, x1 + pad, y1 + pad), fill=(99, 102, 241, 40),
                           outline=(129, 140, 248, 255), width=line_w + 1)
    return Image.alpha_composite(base, overlay)


def focus_image(png_bytes: bytes, regions: dict, keys: list, states: dict) -> Image.Image:
    """
    Подсветка выбранных полей: страница затемняется, области полей остаются яркими
    и обводятся рамкой. Зелёная — поле найдено, красная — пусто / отметки нет,
    индиго — найденная подпись или печать.
    """
    base = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    W, H = base.size
    shade = Image.new("RGBA", base.size, (10, 14, 26, 150))
    cut = Image.new("L", base.size, 0)
    dc = ImageDraw.Draw(cut)
    frames = []
    pad = max(6, W // 150)
    for k in keys:
        reg = regions.get(k)
        if not reg:
            continue
        x0, y0, x1, y1 = reg["box"]
        box = (max(0, x0 - pad), max(0, y0 - pad), min(W, x1 + pad), min(H, y1 + pad))
        dc.rectangle(box, fill=255)
        color = ((167, 139, 250) if reg["how"] == "mark" else
                 (16, 185, 129) if states.get(k) == "ok" else (239, 68, 68))
        frames.append((box, color))
    shade.putalpha(Image.eval(cut, lambda v: 0 if v else 150))
    out = Image.alpha_composite(base, shade)
    draw = ImageDraw.Draw(out)
    lw = max(3, W // 300)
    for box, color in frames:
        draw.rounded_rectangle(box, radius=lw * 2, outline=color + (255,), width=lw)
    return out.convert("RGB")


def field_confidence(regions: dict, boxes: list) -> dict:
    """
    Уверенность распознавания по каждому полю: минимальная уверенность OCR среди
    фрагментов, попавших в строку поля. У PDF с текстовым слоем — 1.0.
    Подпись и печать определяются по графике, для них уверенности OCR нет.
    """
    out = {}
    for key, reg in (regions or {}).items():
        if reg.get("how") in ("mark", "place"):
            continue
        x0, y0, x1, y1 = reg["box"]
        confs = []
        for b in boxes or []:
            xs, ys = [p[0] for p in b["bbox"]], [p[1] for p in b["bbox"]]
            bx0, by0, bx1, by1 = min(xs), min(ys), max(xs), max(ys)
            overlap_y = min(y1, by1) - max(y0, by0)
            if overlap_y > 0.5 * max(1, by1 - by0) and min(x1, bx1) > max(x0, bx0):
                confs.append(float(b.get("confidence", 1.0)))
        if confs:
            out[key] = min(confs)
    return out


CONF_LEVELS = [(0.9, "hi", "точно"), (0.7, "mid", "проверьте"), (0.0, "lo", "ненадёжно")]


def conf_badge(value) -> str:
    """Плашка уверенности: зелёная — точно, янтарная — проверьте, красная — ненадёжно."""
    if value is None:
        return ""
    cls, label = next((c, l) for t, c, l in CONF_LEVELS if value >= t)
    return f'<span class="conf {cls}" title="Уверенность распознавания {value:.2f}"><i></i>{label} · {value:.2f}</span>'


def field_display(spec, value):
    """Текст значения для карточки."""
    if spec.get("type") == "boolean":
        return "есть" if value is True else "нет (графическая отметка не найдена)"
    return value




def render_result(r: dict):
    fields, validation, answer = r["fields"], r["validation"], r["answer"]
    specs = field_specs(r["doc_type"])
    by_key = {s["key"]: s for s in specs}
    states = {s["key"]: field_state(fields[s["key"]]) for s in specs}
    title = DOCUMENT_TITLES.get(r["doc_type"], r["doc_type"])

    with card("result"):
        # --- шапка результата ---
        render(f'''
            <div class="res-top">
              <span class="res-kicker">РЕЗУЛЬТАТ ПРОВЕРКИ</span>
              <div class="meta-row">
                <span class="meta">{icon(type_icon(r["doc_type"]), 14)}<b>{esc(title)}</b>
                  {"определён автоматически" if r["auto_used"] else "выбран вручную"}</span>
                <span class="meta">{icon("file-text", 14)}{esc(r["text_source"])}</span>
              </div>
            </div>''')

        # --- a) статус проверки ---
        ok = validation["status"] == "document_ok"
        if ok:
            items = ""
            headline = "Все обязательные сведения на месте"
            desc = "Поля и графические отметки, которые требует Положение для этого типа, найдены."
        else:
            items = "".join(f'<li>{icon("x-circle", 14)}<span><b>{esc(by_key[k]["label"])}</b> '
                            f'<code>{esc(k)}</code> — п. {esc(by_key[k]["rule"])}</span></li>'
                            for k in validation["missing_fields"])
            headline = f'Не хватает сведений: {len(validation["missing_fields"])}'
            desc = "Ниже — что отсутствует и какой пункт Положения это требует."
        render(f'''
            <div class="status {"ok" if ok else "warn"}">
              <div class="si">{icon("check-circle-2" if ok else "alert-circle", 26)}</div>
              <div style="position:relative;z-index:1">
                <div class="sk">validation.status = {esc(validation["status"])}</div>
                <div class="st">{esc(headline)}</div>
                <div class="sd">{esc(desc)} Окончательное решение примет сотрудник деканата (п. 1.3).</div>
                <ul>{items}</ul>
                <div class="chips"><span class="lbl">Источники</span>{sources_html(validation["sources"])}</div>
              </div>
            </div>''')

        # --- c) ответ на вопрос ---
        val = (f'<div class="val">{icon("check-circle-2", 14)}value <code>{esc(answer["value"])}</code></div>'
               if answer["answerable"] else
               f'<div class="val null">{icon("x-circle", 14)}answerable: false · value <code>null</code></div>')
        render(f'''
            <div class="answer rise">
              <div class="aq">{icon("message-circle-question", 14)}{esc(r["question"])}</div>
              <div class="at">{esc(answer["answer"])}</div>
              {val}
              <div class="chips"><span class="lbl">Источники ответа</span>{sources_html(answer["sources"])}</div>
            </div>''')

        # --- b) карточка полей ---
        found = sum(v == "ok" for v in states.values())
        fconf = field_confidence(r.get("regions"), r.get("boxes"))
        weak = [by_key[k]["label"] for k, v in fconf.items() if v < 0.7 and k in by_key]
        cards = ""
        for i, s in enumerate(specs):
            good = states[s["key"]] == "ok"
            ic = ("stamp" if mark_kind(s) == "seal" else "pen-line") if (good and s.get("type") == "boolean") \
                else ("check-circle-2" if good else "x-circle")
            cards += (f'<div class="fcard {"ok" if good else "miss"}" style="animation-delay:{i * .035:.2f}s">'
                      f'<div class="fh"><span class="fl">{esc(s["label"])}</span>{icon(ic, 16)}</div>'
                      f'<div class="fv">{esc(field_display(s, fields[s["key"]]))}</div>'
                      f'<div class="fk"><span>{esc(s["key"])}</span><span>п. {esc(s["rule"])}</span></div>'
                      f'{conf_badge(fconf.get(s["key"]))}</div>')
        warn = (f'<div class="conf-note">{icon("alert-circle", 15)}Распознано ненадёжно: {esc(", ".join(weak))}. '
                f'Сверьте эти поля с оригиналом.</div>' if weak else "")
        render(f'<div class="blk">{icon("list", 19)}Карточка полей<span class="cnt">{found}/{len(specs)}</span></div>'
               f'{warn}<div class="fgrid">{cards}</div>')

        # --- подсветка поля на снимке ---
        if r.get("page_png"):
            render(f'<div class="blk">{icon("scan", 19)}Где поле на снимке</div>')
            ALL = "__all__"
            options = [ALL] + [s["key"] for s in specs]
            fmt = lambda k: "Все поля" if k == ALL else by_key[k]["label"]
            if hasattr(st, "pills"):
                picked = _compat(st.pills, "Поле", options, selection_mode="single", default=ALL,
                                 format_func=fmt, key="hl_field", label_visibility="collapsed")
            else:
                picked = st.radio("Поле", options, horizontal=True, format_func=fmt, key="hl_field",
                                  label_visibility="collapsed")
            picked = picked or ALL
            keys = [s["key"] for s in specs] if picked == ALL else [picked]
            c_img, c_info = st.columns([1.4, 1], gap="large")
            with c_img:
                show_image(focus_image(r["page_png"], r["regions"], keys, states))
            with c_info:
                if picked == ALL:
                    render(f'''<div class="hl-info"><div class="hk">все поля</div>
                        <div class="hv">Отмечено {sum(k in r["regions"] for k in keys)} из {len(keys)}</div>
                        <div class="hw"><span style="color:#10B981">Зелёная рамка</span> — поле заполнено,
                        <span style="color:#EF4444">красная</span> — пусто или нет отметки,
                        <span style="color:#A78BFA">фиолетовая</span> — найденная подпись или печать.</div></div>''')
                else:
                    s, reg = by_key[picked], r["regions"].get(picked)
                    where = {"label": "строка с подписью поля", "value": "строка со значением",
                             "mark": "графический элемент", "place": "место для отметки — графики нет"}.get(
                        reg["how"] if reg else "", "на снимке не найдено")
                    render(f'''<div class="hl-info"><div class="hk">{esc(picked)} · п. {esc(s["rule"])}</div>
                        <div class="hv">{esc(field_display(s, fields[picked]))}</div>
                        <div class="hw">{esc(s["label"])} — {esc(where)}.</div>
                        <div class="chips">{sources_html([f"field:{picked}", f"rule:{s['rule']}"])}</div></div>''')

        # --- d) пункты Положения ---
        cited = []
        for src in list(answer["sources"]) + list(validation["sources"]):
            if src.startswith("rule:") and src[5:] not in cited:
                cited.append(src[5:])
        rules_html = ""
        for n in cited:
            body = re.sub(r"^\d+\.\d+\.\s*", "", rule_text(RULES, n))
            rules_html += (f'<div class="rule cited"><div class="rn">{esc(n)}</div><div class="rb">'
                           f'<div class="rh"><span>Источник ответа</span><span class="tag">rule:{esc(n)}</span></div>'
                           f'<div class="rt" style="margin-top:8px">{esc(body)}</div></div></div>')
        for c in [c for c in r["candidates"] if c["number"] not in cited][:2]:
            pct = max(0, min(100, round(float(c["score"]) * 100)))
            body = re.sub(r"^\d+\.\d+\.\s*", "", c["text"])
            rules_html += (f'<div class="rule"><div class="rn">{esc(c["number"])}</div><div class="rb">'
                           f'<div class="rh"><span>Похожий пункт · релевантность</span><b>{float(c["score"]):.2f}</b></div>'
                           f'<div class="rbar"><div style="width:{pct}%"></div></div>'
                           f'<div class="rt">{esc(body)}</div></div></div>')
        render(f'<div class="blk">{icon("scale", 19)}Пункты Положения</div>{rules_html}')

        # --- e) детали распознавания ---
        ocr_text = _html.escape(r["text"] or "Текст не распознан").replace("\n", "<br>")
        img_b64 = image_to_b64(highlight_image(r["page_png"], r["boxes"], r["marks"])) if r.get("page_png") else ""
        render(f'''
            <div class="blk">{icon("file-text", 19)}Детали распознавания</div>
            <details class="disc"><summary>{icon("file-text", 18)}Распознанный текст
              <span class="sub">{esc(r["text_source"])}</span><span class="chev">{icon("chevron-down", 18)}</span></summary>
              <div class="body"><div class="mono">{ocr_text}</div></div></details>
            <details class="disc"><summary>{icon("scan", 18)}Все рамки: текст и графические отметки
              <span class="sub">{len(r["boxes"])} фрагм.</span><span class="chev">{icon("chevron-down", 18)}</span></summary>
              <div class="body"><img src="data:image/jpeg;base64,{img_b64}" alt="Подсветка"></div></details>''')

        # --- f) results.json ---
        doc_id = Path(r["file_name"]).stem
        payload = {"version": "1.0", "documents": [{
            "document_id": doc_id, "document_type": r["doc_type"], "fields": fields,
            "validation": validation, "answers": [dict(answer, question_id=f"{doc_id}-Q1")]}]}
        data = base64.b64encode(json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")).decode()
        render(f'''<a class="dl" download="results.json" href="data:application/json;charset=utf-8;base64,{data}">
            <span class="dli">{icon("download", 20)}</span>
            <span><span class="dlt">Скачать results.json</span><br><span class="dls">version 1.0 · results.schema.json</span></span>
            <span class="dla">{icon("arrow-right", 18)}</span></a>''')


if "result" in st.session_state:
    render_result(st.session_state["result"])
elif not check:
    render(f'<div class="empty rise">{icon("sparkles", 26)}<div>Загрузите документ и нажмите '
           f'<b>«Проверить документ»</b> — здесь появится результат с карточкой полей и ответом.</div></div>')


# =====================================================================
# 11. О СЕРВИСЕ, ПОЛОЖЕНИЕ, ФУТЕР
# =====================================================================
rules_mini = ""
for x in RULES:
    x_body = re.sub(r"^\d+\.\d+\.\s*", "", x["text"])
    rules_mini += (f'<div style="padding:9px 0;border-bottom:1px solid var(--line);font-size:.88rem;color:var(--muted)">'
                   f'<b style="font-family:var(--mono);color:#C4B5FD;margin-right:8px">{esc(x["number"])}</b>'
                   f'{esc(x_body)}</div>')
render(f'''
    <div class="sec-head" id="about">
      <div><div class="sec-kicker">О СЕРВИСЕ</div><div class="sec-title">Как это работает</div></div>
      <div class="sec-note">Всё считается на вашем компьютере. Решение о приёме документа — за сотрудником деканата.</div>
    </div>
    <div class="about">
      <div class="feat"><div class="fi">{icon("scan", 19)}</div><h4>Распознавание</h4>
        <p>EasyOCR с выравниванием наклона, борьбой с тенями и шумом. У PDF читается текстовый слой.</p></div>
      <div class="feat"><div class="fi">{icon("list", 19)}</div><h4>Карточка полей</h4>
        <p>Ключи из конфига, даты в ГГГГ-ММ-ДД, подпись и печать — по графике, а не по надписи «М.П.».</p></div>
      <div class="feat"><div class="fi">{icon("scale", 19)}</div><h4>Проверка по Положению</h4>
        <p>Комплектность, ответы со ссылками field:… и rule:…, честное «нет информации», results.json.</p></div>
    </div>
    <details class="disc" style="margin-top:14px"><summary>{icon("book-open", 18)}Положение о предварительной проверке
      <span class="sub">{len(RULES)} пунктов</span><span class="chev">{icon("chevron-down", 18)}</span></summary>
      <div class="body">{rules_mini}</div></details>
    <div class="footer">
      <span class="fb"><span class="logo" style="width:24px;height:24px;border-radius:7px">{icon("graduation-cap", 13)}</span>
        Онлайн-деканат</span>
      <span>Сделано на хакатоне UMIHack 2026 · Кейс «Центр-инвест»</span>
    </div>''')
