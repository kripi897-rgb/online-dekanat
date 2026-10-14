"""
ocr.py — распознавание текста EasyOCR (ru+en) с предобработкой плохих снимков
и постобработкой типичных ошибок OCR.

recognize_from_file(file_bytes, filename) -> (text, boxes)
    text  — строки документа (фрагменты собраны в строки по координатам);
    boxes — [{"bbox": [[x, y] x4], "text": str, "confidence": float}] в координатах
            ИСХОДНОГО изображения (после поворота по EXIF). confidence — уверенность
            OCR по фрагменту (0..1), по ней интерфейс показывает «точно / проверьте / ненадёжно».
LAST_INFO — что было сделано с последним снимком (для интерфейса и отладки).

Конвейер:
  1. поворот по EXIF;
  2. выравнивание наклона (deskew) до ±30°;
  3. увеличение ×2 (мелкий текст читается лучше);
  4. OCR базового варианта; если средняя уверенность < 0.7 — бинаризация Отсу
     (с выравниванием освещения), < 0.5 — ещё исходный масштаб, шумоподавление,
     повороты 90/180/270° и, если установлен, Tesseract; берём лучший результат;
  5. фрагменты с уверенностью < 0.6 перечитываются крупнее по отдельности;
  6. постобработка текста postprocess_text(): кавычки «», цифры вместо букв
     (О -> 0, l -> 1), латинские буквы в русском тексте.
PDF без текстового слоя рендерится через pypdfium2 (poppler не нужен).
"""

import io
import math
import re

import numpy as np
from PIL import Image, ImageFilter, ImageOps

_reader = None
LAST_INFO = {}

UPSCALE = 2.0           # во сколько раз увеличивать страницу перед OCR
MAX_SIDE = 3400         # но не больше этого размера по длинной стороне
CONF_BINARIZE = 0.7     # ниже — пробуем бинаризацию Отсу
CONF_RETRY = 0.5        # ниже — все варианты: исходный масштаб, шумоподавление, повороты, Tesseract
CONF_REREAD = 0.6       # фрагменты ниже — перечитываем крупнее по отдельности
MAX_REREAD = 40         # не больше стольких фрагментов на страницу (время на CPU)
MAX_SKEW = 30           # градусов


def get_reader():
    global _reader
    if _reader is None:
        import easyocr                     # импорт здесь: интерфейс открывается и без EasyOCR
        _reader = easyocr.Reader(["ru", "en"], gpu=False)
    return _reader


# =====================================================================
# Загрузка страницы
# =====================================================================

def load_image(file_bytes: bytes, filename: str) -> Image.Image:
    """PNG/JPG -> RGB с учётом EXIF; PDF -> первая страница (pypdfium2, иначе pdf2image)."""
    if filename.lower().endswith(".pdf"):
        try:
            import pypdfium2 as pdfium
            return pdfium.PdfDocument(file_bytes)[0].render(scale=2).to_pil().convert("RGB")
        except ImportError:
            from pdf2image import convert_from_bytes          # нужен установленный poppler
            return convert_from_bytes(file_bytes, first_page=1, last_page=1)[0].convert("RGB")
    return ImageOps.exif_transpose(Image.open(io.BytesIO(file_bytes))).convert("RGB")


# =====================================================================
# Предобработка
# =====================================================================

def _rescale(img: Image.Image, min_side=1400, max_side=2600):
    w, h = img.size
    scale = 1.0
    if max(w, h) < min_side:
        scale = min_side / max(w, h)           # мелкий текст читается хуже
    elif max(w, h) > max_side:
        scale = max_side / max(w, h)           # огромные фото — долго на CPU
    if scale != 1.0:
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    return img, scale


def otsu_threshold(gray: np.ndarray) -> int:
    """Порог Отсу по гистограмме (без OpenCV)."""
    hist = np.bincount(gray.ravel(), minlength=256).astype(np.float64)
    total = hist.sum()
    if total == 0:
        return 128
    omega = np.cumsum(hist) / total
    mu = np.cumsum(hist * np.arange(256)) / total
    mu_t = mu[-1]
    with np.errstate(divide="ignore", invalid="ignore"):
        sigma_b = (mu_t * omega - mu) ** 2 / (omega * (1 - omega))
    sigma_b = np.nan_to_num(sigma_b)
    return int(np.argmax(sigma_b))


def flatten_illumination(gray: Image.Image) -> Image.Image:
    """Убирает тени и неравномерный свет: делим на размытый «фон» страницы."""
    radius = max(15, min(gray.size) // 25)
    background = gray.filter(ImageFilter.GaussianBlur(radius))
    g = np.asarray(gray, dtype=np.float32)
    b = np.asarray(background, dtype=np.float32) + 1.0
    flat = np.clip(g / b * 235.0, 0, 255).astype(np.uint8)
    return Image.fromarray(flat)


def binarize(gray: Image.Image) -> Image.Image:
    arr = np.asarray(gray, dtype=np.uint8)
    t = otsu_threshold(arr)
    return Image.fromarray(np.where(arr > t, 255, 0).astype(np.uint8))


def estimate_skew(img: Image.Image, max_angle: float = MAX_SKEW) -> float:
    """
    Угол (в градусах), на который нужно повернуть страницу, чтобы строки стали
    горизонтальными. Перебираем углы: у ровной страницы проекция «чернил» на ось Y
    состоит из резких пиков-строк, и её дисперсия максимальна.
    """
    gray = ImageOps.grayscale(img)
    gray.thumbnail((800, 800))
    arr = np.asarray(gray, dtype=np.uint8)
    ink = Image.fromarray(np.where(arr < otsu_threshold(arr), 255, 0).astype(np.uint8))

    def score(angle):
        rot = np.asarray(ink.rotate(angle, resample=Image.NEAREST, expand=True, fillcolor=0))
        profile = rot.sum(axis=1, dtype=np.float64)
        return float(np.var(profile))

    coarse = max(np.arange(-max_angle, max_angle + 0.01, 1.0), key=score)
    fine = max(np.arange(coarse - 1.0, coarse + 1.01, 0.1), key=score)
    return round(float(fine), 1)


def rotate_page(img: Image.Image, angle: float):
    """Поворот с расширением холста; возвращает (картинка, функция «точка -> исходные координаты»)."""
    if abs(angle) < 1e-6:
        return img, (lambda x, y: (x, y))
    w, h = img.size
    rot = img.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor="white")
    W, H = rot.size
    t = math.radians(angle)
    cos, sin = math.cos(t), math.sin(t)

    def back(x, y):
        dx, dy = x - W / 2, y - H / 2
        return (dx * cos - dy * sin + w / 2, dx * sin + dy * cos + h / 2)

    return rot, back


def _variant(img: Image.Image, name: str) -> Image.Image:
    gray = ImageOps.grayscale(img)
    if name == "base":
        gray = ImageOps.autocontrast(gray, cutoff=1)
        gray = gray.filter(ImageFilter.UnsharpMask(radius=2, percent=120, threshold=3))
    elif name == "flat+otsu":
        gray = binarize(flatten_illumination(gray))
    elif name == "denoise+otsu":
        gray = binarize(ImageOps.autocontrast(gray.filter(ImageFilter.MedianFilter(3)), cutoff=1))
    return gray.convert("RGB")


def preprocess_image(img: Image.Image, min_side=1400, max_side=2600):
    """Совместимость со старым кодом: базовый вариант + масштаб."""
    img = ImageOps.exif_transpose(img).convert("RGB")
    scaled, scale = _rescale(img, min_side, max_side)
    return _variant(scaled, "base"), scale


# =====================================================================
# OCR
# =====================================================================

def _mean_conf(result) -> float:
    return float(np.mean([r[2] for r in result])) if result else 0.0


def _score(result) -> float:
    """Уверенность с учётом объёма текста: пустой результат с высокой уверенностью не выигрывает."""
    chars = sum(len(r[1]) for r in result)
    return _mean_conf(result) * math.sqrt(max(chars, 1))


def _boxes_to_lines(boxes: list) -> str:
    """Группирует боксы в строки по вертикали и сортирует слева направо."""
    if not boxes:
        return ""

    def geom(b):
        ys = [p[1] for p in b["bbox"]]
        xs = [p[0] for p in b["bbox"]]
        return min(xs), (min(ys) + max(ys)) / 2, max(ys) - min(ys)

    items = sorted(boxes, key=lambda b: geom(b)[1])
    lines, current, cur_y, cur_h = [], [], None, None
    for b in items:
        x, y, h = geom(b)
        if current and abs(y - cur_y) <= max(cur_h, h) * 0.5:
            current.append(b)
        else:
            if current:
                lines.append(current)
            current, cur_y, cur_h = [b], y, h
    if current:
        lines.append(current)
    return "\n".join(" ".join(b["text"] for b in sorted(line, key=lambda b: geom(b)[0]))
                     for line in lines)


# =====================================================================
# Постобработка текста: типичные ошибки EasyOCR на документах набора
# =====================================================================
#  * кавычки «» EasyOCR читает как буквы: «Меридиан» -> кМеридианх, ~Орбитах,
#    "Спектру, «ФВекторх». Чиним в названиях вузов и организаций;
#  * в числах буквы вместо цифр: ПР-2О3О -> ПР-2030, О1.06.2026 -> 01.06.2026,
#    «l июня» / «| июня» -> «1 июня»;
#  * одиночная латинская буква среди русских слов: «в связи c уходом» -> «с».

_ORG_CTX = r"(?i:((?:учебн|образовательн)\w*\s+(?:университет|академи|институт|колледж|организаци)\w*\s*))"
ORG_QUOTED = re.compile(
    _ORG_CTX
    + r"(?P<open>«|<<|<|\"|“|„|'|~|-|к(?=[А-ЯЁ])|)"          # открывающая кавычка или то, во что её превратил OCR
    + r"(?P<junk>[А-ЯЁ](?=[А-ЯЁ][а-яё]))?"                    # лишняя заглавная: «ФВектор»
    + r"(?P<name>[А-ЯЁ][а-яё]{2,}(?:-[А-ЯЁа-яё][а-яё]+)?)"
    + r"(?P<close>»|>>|>|\"|”|'|)")
_CLOSE_AS_LETTER = "хъуэьy"                                    # во что OCR превращает »

_DIGIT_LIKE = str.maketrans({"O": "0", "o": "0", "О": "0", "о": "0", "l": "1", "I": "1", "|": "1",
                             "І": "1", "і": "1", "!": "1", "З": "3", "з": "3", "б": "6"})
NUM_TOKEN = re.compile(r"(?<![A-Za-zА-Яа-яЁё])(?=[0-9OoОоlI|Іі!Ззб]*[0-9])[0-9OoОоlI|Іі!Ззб]{2,}(?![A-Za-zА-Яа-яЁё])")
_MONTHS = r"(?:январ|феврал|март|апрел|ма[яй]|июн|июл|август|сентябр|октябр|ноябр|декабр|янв|фев|мар|апр|авг|сен|окт|ноя|дек)"
DAY_BEFORE_MONTH = re.compile(r"(?<!\S)([0-9lI|Іі!OoОо]{1,2})(?=\s+" + _MONTHS + r")", re.IGNORECASE)
LATIN_SINGLE = re.compile(r"(?<=[А-Яа-яЁё]\s)([aceopxyACEHKMOPTXY])(?=\s[А-Яа-яЁё])")
_LAT2CYR = str.maketrans("aceopxyACEHKMOPTXY", "асеорхуАСЕНКМОРТХУ")


def _fix_org_quotes(m) -> str:
    ctx, open_, junk, name, close = m.group(1), m.group("open"), m.group("junk"), m.group("name"), m.group("close")
    if not open_ and not close and not junk:
        return m.group(0)                          # кавычек нет вовсе — не трогаем
    if open_ == "«" and close == "»" and not junk:
        return m.group(0)                          # прочитано правильно
    # закрывающая кавычка «съедена» и превратилась в последнюю букву имени
    if (not close or junk) and len(name) > 3 and name[-1] in _CLOSE_AS_LETTER:
        name = name[:-1]
    return f"{ctx}«{name}»"


def postprocess_text(text: str) -> str:
    """Исправляет типичные ошибки OCR. Применяется к каждому фрагменту и к итоговому тексту."""
    if not text:
        return text
    t = text.replace("<<", "«").replace(">>", "»").replace("“", "«").replace("”", "»").replace("„", "«")
    t = ORG_QUOTED.sub(_fix_org_quotes, t)
    t = NUM_TOKEN.sub(lambda m: m.group(0).translate(_DIGIT_LIKE), t)
    t = DAY_BEFORE_MONTH.sub(lambda m: m.group(1).translate(_DIGIT_LIKE), t)
    t = LATIN_SINGLE.sub(lambda m: m.group(1).translate(_LAT2CYR), t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    return "\n".join(line.strip() for line in t.split("\n"))


# =====================================================================
# Распознавание
# =====================================================================

def _upscale(img: Image.Image):
    """Увеличение ×UPSCALE, но не больше MAX_SIDE; огромные фото — уменьшаем."""
    w, h = img.size
    scale = min(UPSCALE, MAX_SIDE / max(w, h))
    if abs(scale - 1.0) > 0.01:
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    return img, scale


def _readtext(reader, arr, **kw):
    side = max(arr.shape[0], arr.shape[1])
    return reader.readtext(arr, detail=1, paragraph=False, canvas_size=max(2560, side), **kw)


def _tesseract(img: Image.Image):
    """Запасной OCR: Tesseract (если установлен pytesseract и язык rus). Иначе None."""
    try:
        import pytesseract
        if "rus" not in pytesseract.get_languages(config=""):
            return None
        d = pytesseract.image_to_data(img, lang="rus+eng", output_type=pytesseract.Output.DICT)
    except Exception:
        return None
    result = []
    for i, word in enumerate(d["text"]):
        conf = float(d["conf"][i])
        if word.strip() and conf >= 0:
            x, y, w, h = d["left"][i], d["top"][i], d["width"][i], d["height"][i]
            result.append(([[x, y], [x + w, y], [x + w, y + h], [x, y + h]], word, conf / 100.0))
    return result


def _reread_weak(reader, arr: np.ndarray, result: list) -> tuple:
    """Перечитывает неуверенные фрагменты по одному, увеличив их ещё в 2 раза."""
    improved, out = 0, []
    weak = sorted(range(len(result)), key=lambda i: result[i][2])[:MAX_REREAD]
    weak = {i for i in weak if result[i][2] < CONF_REREAD}
    H, W = arr.shape[:2]
    for i, (bbox, txt, conf) in enumerate(result):
        if i in weak:
            xs, ys = [p[0] for p in bbox], [p[1] for p in bbox]
            pad = int(max(6, (max(ys) - min(ys)) * 0.35))
            x0, y0 = max(0, int(min(xs)) - pad), max(0, int(min(ys)) - pad)
            x1, y1 = min(W, int(max(xs)) + pad), min(H, int(max(ys)) + pad)
            if x1 - x0 > 4 and y1 - y0 > 4:
                crop = Image.fromarray(arr[y0:y1, x0:x1]).resize(((x1 - x0) * 2, (y1 - y0) * 2), Image.LANCZOS)
                sub = reader.readtext(np.array(crop), detail=1, paragraph=False)
                if sub:
                    sub.sort(key=lambda r: min(p[0] for p in r[0]))
                    new_txt = " ".join(r[1] for r in sub)
                    new_conf = float(np.mean([r[2] for r in sub]))
                    if new_conf > conf + 0.05 and new_txt.strip():
                        txt, conf = new_txt, new_conf
                        improved += 1
        out.append((bbox, txt, conf))
    return out, improved


def recognize_image(page: Image.Image, preprocess: bool = True) -> tuple:
    """OCR готового изображения страницы -> (text, boxes в координатах page)."""
    global LAST_INFO
    reader = get_reader()
    info = {"skew": 0.0, "variant": "base", "confidence": 0.0, "rotation": 0,
            "upscale": 1.0, "reread": 0, "engine": "easyocr"}

    work, back = page, (lambda x, y: (x, y))
    if preprocess:
        angle = estimate_skew(page)
        if abs(angle) >= 0.5:
            work, back = rotate_page(page, angle)
            info["skew"] = angle

    if not preprocess:
        scaled, scale = _rescale(work)
        result = _readtext(reader, np.array(scaled.convert("RGB")))
        candidates = [("base", result, np.array(scaled.convert("RGB")), scale)]
    else:
        up, up_scale = _upscale(work)
        arr = np.array(_variant(up, "base"))
        candidates = [("x2", _readtext(reader, arr), arr, up_scale)]
        best_conf = _mean_conf(candidates[0][1])
        # бинаризация, если уверенность ниже 0.7
        if best_conf < CONF_BINARIZE:
            arr = np.array(_variant(up, "flat+otsu"))
            candidates.append(("x2+otsu", _readtext(reader, arr), arr, up_scale))
            best_conf = max(_mean_conf(c[1]) for c in candidates)
        # всё ещё плохо — остальные варианты
        if best_conf < CONF_RETRY:
            small, s_scale = _rescale(work)
            arr = np.array(_variant(small, "base"))
            candidates.append(("original", _readtext(reader, arr), arr, s_scale))
            arr = np.array(_variant(up, "denoise+otsu"))
            candidates.append(("x2+denoise+otsu", _readtext(reader, arr), arr, up_scale))
            arr = candidates[0][2]
            candidates.append(("rotations", _readtext(reader, arr, rotation_info=[90, 180, 270]), arr, up_scale))
            tess = _tesseract(up)
            if tess:
                candidates.append(("tesseract", tess, np.array(up.convert("RGB")), up_scale))

    # лучший вариант — по средней уверенности с учётом объёма текста
    info["variant"], result, arr, scale = max(candidates, key=lambda c: _score(c[1]))
    if info["variant"] == "tesseract":
        info["engine"] = "tesseract"
    elif preprocess:
        result, info["reread"] = _reread_weak(reader, arr, result)
    info["upscale"] = round(scale, 2)

    boxes = []
    for bbox, txt, conf in result:
        pts = [back(x / scale, y / scale) for x, y in bbox]
        boxes.append({"bbox": [[int(round(x)), int(round(y))] for x, y in pts],
                      "text": postprocess_text(txt), "confidence": round(float(conf), 3)})
    info["confidence"] = round(_mean_conf(result), 3)
    info["low_fragments"] = sum(1 for b in boxes if b["confidence"] < 0.7)
    LAST_INFO = info
    return postprocess_text(_boxes_to_lines(boxes)), boxes


def recognize_from_file(file_bytes: bytes, filename: str, preprocess: bool = True) -> tuple:
    """
    Принимает байты файла и имя. Возвращает (полный_текст, список_боксов).
    Координаты боксов — в системе исходного изображения (после EXIF-поворота).
    """
    return recognize_image(load_image(file_bytes, filename), preprocess)
