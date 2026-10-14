"""
extractor.py — извлечение полей документа в формате пакета v1.0.

Публичный интерфейс:
    CONFIG, DOCUMENT_TYPES, NOT_FOUND
    extract_fields(text, doc_type, boxes=None, image=None) -> {ключ: значение}
    detect_doc_type(text) -> ключ типа или None
    field_specs(doc_type) -> список описаний полей из document_types.json
    field_state(value) -> "ok" | "missing"
    to_iso_date(s) -> "YYYY-MM-DD" или None
    load_page_image(file_bytes, filename) -> PIL.Image (первая страница)

Правила (README + rules.md):
  * все текстовые поля — строки; пропуск — "не найдено" (п. 2.2);
  * даты — ISO YYYY-MM-DD; «01.09.2026», «1 сентября 2026 года», «2026-09-01»
    дают одно значение (п. 2.5); периоды делятся на начало и окончание;
  * курс — строка с целым числом;
  * signature_present / seal_present — true/false по наличию ГРАФИЧЕСКОГО
    элемента. Надписи «Подпись», «М.П.» не считаются (п. 2.3). Не уверены — false.

Как ищется поле (по порядку):
  1) по подписи поля в документе («ФИО студента: ...»), варианты подписей —
     labels_in_doc в document_types.json;
  2) по типичным оборотам («Справка выдана ...», «с 01.09.2023 по 31.08.2026»,
     «№ ОБ-1001 от ...», «по состоянию здоровья»);
  3) не нашли — "не найдено". Ничего не додумываем.
"""

import io
import json
import os
import re

NOT_FOUND = "не найдено"
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "document_types.json")


# =====================================================================
# Конфиг
# =====================================================================

def load_config(path: str = CONFIG_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}


CONFIG = load_config()
DOCUMENT_TYPES = {t: [f["key"] for f in cfg["fields"]] for t, cfg in CONFIG.items()}
DOCUMENT_TITLES = {t: cfg.get("title", t) for t, cfg in CONFIG.items()}


def field_specs(doc_type: str) -> list:
    return CONFIG.get(doc_type, {}).get("fields", [])


def mark_kind(spec: dict) -> str:
    """Какую графическую отметку означает boolean-поле: "seal" или "signature".
    Задаётся в конфиге ("mark"); если не задано — по ключу/названию поля."""
    if spec.get("mark") in ("seal", "signature"):
        return spec["mark"]
    text = (spec.get("key", "") + " " + spec.get("label", "")).lower()
    return "seal" if ("seal" in text or "печат" in text or "stamp" in text) else "signature"


def field_state(value) -> str:
    """ok — значение есть; missing — «не найдено» или false у подписи/печати."""
    if value is False or value is None:
        return "missing"
    if isinstance(value, str) and value.strip().lower() in ("", NOT_FOUND):
        return "missing"
    return "ok"


# =====================================================================
# Нормализация текста OCR
# =====================================================================

_LAT2CYR = str.maketrans("AaBCcEeHKMOoPpTXxYy", "АаВСсЕеНКМОоРрТХхУу")


def _fix_token(tok: str) -> str:
    """Смесь кириллицы и латиницы в одном слове -> кириллица («Ивaнов»)."""
    if re.search(r"[А-Яа-яЁё]", tok) and re.search(r"[A-Za-z]", tok):
        return tok.translate(_LAT2CYR)
    return tok


def normalize_text(text: str) -> str:
    text = (text or "").replace("\r", "")
    text = re.sub(r"[‐‑‒–—―−]", "-", text)
    text = text.replace("“", '"').replace("”", '"').replace("„", '"')
    text = re.sub(r"\S+", lambda m: _fix_token(m.group(0)), text)
    text = re.sub(r"[ \t]+", " ", text)
    return "\n".join(line.strip() for line in text.split("\n")).strip()


def _empty(value: str) -> bool:
    """Пусто или только линия для заполнения («______», «-», «—»)."""
    return not re.sub(r"[\s_\-.:;,/\\|]+", "", value or "")


def _clean(value: str) -> str:
    value = re.sub(r"_{2,}", " ", value or "")
    return re.sub(r"\s+", " ", value).strip(" \t:;,-")


# =====================================================================
# Даты
# =====================================================================

# Полные названия в любом падеже и сокращения: янв, фев, мар, апр, мая, июн, июл,
# авг, сен/сент, окт, ноя, дек (с точкой или без).
_MONTHS = [("янв", 1), ("фев", 2), ("мар", 3), ("апр", 4), ("мая", 5), ("май", 5),
           ("июн", 6), ("июл", 7), ("авг", 8), ("сен", 9), ("окт", 10),
           ("ноя", 11), ("дек", 12)]
_MONTH_RE = (r"(январ\w*|феврал\w*|март\w*|апрел\w*|ма[яй]|июн\w*|июл\w*|август\w*|"
             r"сентябр\w*|октябр\w*|ноябр\w*|декабр\w*|"
             r"(?:янв|фев|мар|апр|авг|сент|сен|окт|ноя|дек)\.?)(?![а-яё])")

DATE_PATTERNS = [
    # 2026-09-01
    ("iso", re.compile(r"(?<!\d)((?:19|20)\d{2})\s?-\s?(\d{1,2})\s?-\s?(\d{1,2})(?!\d)")),
    # 01.09.2026, 1/9/2026, 01-09-2026 (только 4-значный год: 38.03.01 — код направления)
    ("dmy", re.compile(r"(?<![\d.])(\d{1,2})\s?[./-]\s?(\d{1,2})\s?[./-]\s?((?:19|20)\d{2})(?!\d)")),
    # «1» сентября 2026 г.
    ("text", re.compile(r"[«\"]?(\d{1,2})[»\"]?\s+" + _MONTH_RE + r"\s+((?:19|20)\d{2})", re.IGNORECASE)),
]


def _month_num(word: str) -> int:
    w = word.lower()
    for stem, n in _MONTHS:
        if w.startswith(stem):
            return n
    return 0


def _iso(y: int, m: int, d: int):
    if 1 <= m <= 12 and 1 <= d <= 31 and 1900 <= y <= 2100:
        return f"{y:04d}-{m:02d}-{d:02d}"
    return None


def find_dates(text: str) -> list:
    """Все даты текста по порядку: [{start, end, iso}]."""
    found = []
    for kind, rx in DATE_PATTERNS:
        for m in rx.finditer(text):
            if kind == "iso":
                iso = _iso(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            elif kind == "dmy":
                iso = _iso(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            else:
                iso = _iso(int(m.group(3)), _month_num(m.group(2)), int(m.group(1)))
            if iso:
                found.append({"start": m.start(), "end": m.end(), "iso": iso})
    found.sort(key=lambda d: (d["start"], -d["end"]))
    result, last_end = [], -1
    for d in found:
        if d["start"] >= last_end:
            result.append(d)
            last_end = d["end"]
    return result


def to_iso_date(s: str):
    dates = find_dates(s or "")
    return dates[0]["iso"] if dates else None


# =====================================================================
# ФИО: поиск и приведение к именительному падежу
# =====================================================================

_W = r"[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?"
FIO_RE = re.compile(rf"\b({_W})\s+({_W})\s+({_W}(?:вич|вн|чн|ич)[а-яё]{{0,3}})\b")
FIO_SHORT_RE = re.compile(rf"\b({_W})\s+([А-ЯЁ])\.\s?([А-ЯЁ])\.")

_NAME_STOP = ("справк", "студент", "учебн", "университет", "академи", "институт", "организац",
              "направлен", "форм", "дата", "подпис", "печат", "заявлен", "руковод", "оценк",
              "декан", "ректор", "прош", "группа", "курс", "причин", "период", "практик")

# Особые имена, у которых окончание не снимается простым правилом
_NAME_FIX = {"льва": "Лев", "льву": "Лев", "львом": "Лев", "павла": "Павел", "павлу": "Павел",
             "петра": "Пётр", "петру": "Пётр"}


def _titlecase_caps(text: str) -> str:
    """ИВАНОВ ИВАН -> Иванов Иван (длина строки не меняется)."""
    return re.sub(r"\b[А-ЯЁ]{2,}(?:-[А-ЯЁ]{2,})?\b",
                  lambda m: "-".join(p[0] + p[1:].lower() for p in m.group(0).split("-")), text)


def _nominative(surname: str, name: str, patron: str) -> str:
    """
    Приводит ФИО в родительном/дательном/творительном падеже к именительному.
    Падеж и род определяются по отчеству. Если отчество в именительном — ничего не меняем.
    """
    p = patron.lower()
    masc = re.search(r"ич(а|у|ем|е)?$", p)
    fem = re.search(r"(вн|чн)(а|ы|е|ой|у)$", p)
    if masc and masc.group(1):
        case = {"а": "gen", "у": "dat", "ем": "ins", "е": "dat"}[masc.group(1)]
        patron = patron[: len(patron) - len(masc.group(1))]
        # фамилия
        for old, new in (("ского", "ский"), ("скому", "ский"), ("ским", "ский"),
                         ("ова", "ов"), ("ева", "ев"), ("ёва", "ёв"), ("ина", "ин"), ("ына", "ын"),
                         ("ову", "ов"), ("еву", "ев"), ("ину", "ин"), ("ыну", "ын"),
                         ("овым", "ов"), ("евым", "ев"), ("иным", "ин")):
            if surname.lower().endswith(old):
                surname = surname[: -len(old)] + new
                break
        # имя
        low = name.lower()
        if low in _NAME_FIX:
            name = _NAME_FIX[low]
        elif case == "gen":
            if low.endswith(("ия",)):                      # Юрия -> Юрий
                name = name[:-1] + "й"
            elif low.endswith("ея"):                       # Андрея -> Андрей
                name = name[:-1] + "й"
            elif low.endswith("и") and low[-2:-1] in "ль":  # Ильи -> Илья
                name = name[:-1] + "я"
            elif low.endswith("ы"):                         # Никиты -> Никита
                name = name[:-1] + "а"
            elif low.endswith("а"):                         # Артёма -> Артём
                name = name[:-1]
        elif case == "dat":
            if low.endswith("ию"):
                name = name[:-1] + "й"
            elif low.endswith("ею"):
                name = name[:-1] + "й"
            elif low.endswith("ье"):                        # Илье -> Илья
                name = name[:-1] + "я"
            elif low.endswith("е"):                         # Никите -> Никита
                name = name[:-1] + "а"
            elif low.endswith("у"):                         # Артёму -> Артём
                name = name[:-1]
        elif case == "ins":
            if low.endswith("ем"):
                name = name[:-2] + "й"
            elif low.endswith("ом"):
                name = name[:-2]
    elif fem and fem.group(2) != "а":
        patron = patron[: len(patron) - len(fem.group(2))] + "а"
        for old, new in (("ской", "ская"), ("овой", "ова"), ("евой", "ева"), ("иной", "ина"),
                         ("ову", "ова"), ("еву", "ева"), ("ину", "ина")):
            if surname.lower().endswith(old):
                surname = surname[: -len(old)] + new
                break
        low = name.lower()
        if low.endswith("ии"):                              # Марии -> Мария
            name = name[:-1] + "я"
        elif low.endswith(("ы", "е", "у")) and not low.endswith("ье"):
            name = name[:-1] + "а"                          # Елены/Елене -> Елена
        elif low.endswith("ье"):
            name = name[:-1] + "я"
        elif low.endswith("ой"):
            name = name[:-2] + "а"
    return f"{surname} {name} {patron}"


def find_person(chunk: str):
    """Первое полное ФИО в отрезке текста, в именительном падеже."""
    tc = _titlecase_caps(chunk)
    for m in FIO_RE.finditer(tc):
        if any(w.lower().startswith(_NAME_STOP) for w in m.groups()):
            continue
        return _nominative(*m.groups())
    return None


# =====================================================================
# Документ: текст, строки, подписи полей
# =====================================================================

class Doc:
    def __init__(self, text: str, doc_type: str, boxes=None, image=None):
        self.raw = text or ""
        self.text = normalize_text(text)
        self.low = self.text.lower().replace("ё", "е")
        self.lines = self.text.split("\n")
        self.doc_type = doc_type
        self.cfg = CONFIG.get(doc_type, {})
        self.boxes = boxes or []
        self.image = image
        self.dates = find_dates(self.text)
        self.used_dates = set()          # даты, уже отнесённые к какому-то полю
        self.labeled = self._labeled_values()

    # ---------- «Метка: значение» ----------
    def _all_labels(self):
        """Все подписи полей этого типа (+ подписи периодов), длинные первыми."""
        labels = []
        for spec in self.cfg.get("fields", []):
            for lab in spec.get("labels_in_doc", []):
                labels.append((lab, spec["key"]))
        for lab in self.cfg.get("period_labels", []):
            labels.append((lab, "__period__"))
        labels.sort(key=lambda x: len(x[0]), reverse=True)
        return labels

    def _labeled_values(self) -> dict:
        """
        Находит в строках подписи полей и берёт текст после них.
        В одной строке может быть несколько полей («Группа: ЖР-25-1  Курс: 3»).
        Подпись засчитывается, только если после неё идёт «:» или «-»
        (иначе «дата» внутри обычной фразы приняли бы за поле).
        """
        result = {}
        labels = self._all_labels()
        for li, line in enumerate(self.lines):
            low = line.lower().replace("ё", "е")
            hits, taken = [], []
            for lab, key in labels:
                esc_lab = re.escape(lab.lower().replace("ё", "е"))
                pattern = r"(?<![а-яa-z])" + esc_lab + r"\s*[:;\-]"
                if len(lab.split()) >= 2:                    # OCR мог потерять двоеточие
                    pattern += r"|^" + esc_lab + r"(?=\s)"
                for m in re.finditer(pattern, low):
                    if any(s < m.end() and m.start() < e for s, e in taken):
                        continue        # уже занято более длинной подписью
                    hits.append((m.start(), m.end(), key))
                    taken.append((m.start(), m.end()))
            hits.sort()
            for i, (s, e, key) in enumerate(hits):
                end = hits[i + 1][0] if i + 1 < len(hits) else len(line)
                value = line[e:end].strip()
                # значение на следующей строке (OCR разнёс метку и значение)
                if _empty(value) and i + 1 == len(hits) and li + 1 < len(self.lines):
                    nxt = self.lines[li + 1]
                    nxt_low = nxt.lower()
                    if not any(re.match(re.escape(l.lower()) + r"\s*[:;\-]?", nxt_low) for l, _ in labels):
                        value = nxt.strip()
                offset = sum(len(x) + 1 for x in self.lines[:li]) + e
                result.setdefault(key, {"value": value, "pos": offset})
        return result

    def label_value(self, key: str):
        """Значение после подписи поля; None — подписи нет; "" — подпись есть, поле пустое."""
        item = self.labeled.get(key)
        if item is None:
            return None
        return "" if _empty(item["value"]) else _clean(item["value"])

    def dates_in(self, start: int, end: int):
        return [d for d in self.dates if start <= d["start"] < end]

    def take_date(self, d):
        self.used_dates.add(d["start"])
        return d["iso"]


# =====================================================================
# Извлечение отдельных полей
# =====================================================================

def _label_or_none(doc, key):
    v = doc.label_value(key)
    return v  # None / "" / значение


def ex_student_name(doc, key="student_name"):
    v = _label_or_none(doc, key)
    if v == "":
        return NOT_FOUND
    if v:
        name = find_person(v)
        if name:
            return name
    # обороты: «Справка выдана Иванову Ивану Ивановичу», «студент(ка) ...», «от студента ...»
    for kw in (r"выдан[аоы]?\s", r"студент\w*\s", r"обучающ\w*\s", r"\bот\s", r"заявител\w*\s"):
        for m in re.finditer(kw, doc.text, re.IGNORECASE):
            chunk = doc.text[m.end(): m.end() + 90]
            name = find_person(chunk)
            if name:
                # не путать с руководителем практики
                left = doc.low[max(0, m.start() - 40): m.start()]
                if "руковод" in left:
                    continue
                return name
    return NOT_FOUND


_ORG_KIND = {
    "университет": "Учебный университет", "академи": "Учебная академия",
    "институт": "Учебный институт", "колледж": "Учебный колледж",
    "организаци": "Учебная организация",
}
ORG_RE = re.compile(r"(учебн\w*)\s+(университет\w*|академи\w*|институт\w*|колледж\w*|организаци\w*)"
                    r"\s*(?:[«\"<“'‹]{1,2}\s*([^»\"\n>”'›]{2,60}?)\s*[»\">”'›]{1,2}"
                    r"|(?-i:([А-ЯЁ][А-ЯЁа-яё\-]{1,40})))", re.IGNORECASE)


def _canon_org(m) -> str:
    """«УЧЕБНОГО УНИВЕРСИТЕТА "МЕРИДИАН"» -> «Учебный университет «Меридиан»»."""
    kind = m.group(2).lower()
    head = next(v for k, v in _ORG_KIND.items() if kind.startswith(k))
    name = (m.group(3) or m.group(4) or "").strip()
    if name.isupper():
        name = name[0] + name[1:].lower()
    return f"{head} «{name}»"


def _org(doc, key, kinds):
    v = _label_or_none(doc, key)
    if v == "":
        return NOT_FOUND
    if v:
        m = ORG_RE.search(v)
        return _canon_org(m) if m else _clean(v)
    for m in ORG_RE.finditer(doc.text):
        if any(m.group(2).lower().startswith(k) for k in kinds):
            return _canon_org(m)
    return NOT_FOUND


def ex_university(doc, key="university"):
    return _org(doc, key, ("университет", "академи", "институт", "колледж"))


def ex_organization(doc, key="organization"):
    return _org(doc, key, ("организаци",))


PROGRAM_RE = re.compile(r"(\d{2}\.\d{2}\.\d{2})\s*[«\"]?\s*([А-ЯЁ][А-ЯЁа-яё ,\-]+?)\s*[»\"]?"
                        r"(?=\s*(?:[,;.\n]|$|\s(?:форм\w*|очн\w*|заочн\w*)\b|\(|\sс\s+\d))")


def ex_program(doc, key="program"):
    v = _label_or_none(doc, key)
    if v == "":
        return NOT_FOUND
    if v and re.match(r"\d{2}\.\d{2}\.\d{2}\s", v):
        return re.sub(r"[«»\"]", "", v).strip() if not re.search(r"[«»]", v) else _clean(v)
    src = v if v else doc.text
    m = PROGRAM_RE.search(src)
    if m:
        name = re.sub(r"\s+", " ", m.group(2)).strip(" ,-")
        return f"{m.group(1)} {name}"
    return _clean(v) if v else NOT_FOUND


_FORMS = [("очно-заочн", "очно-заочная"), ("заочн", "заочная"), ("очн", "очная"),
          ("дистанц", "дистанционная")]


def ex_study_form(doc, key="study_form"):
    v = _label_or_none(doc, key)
    if v == "":
        return NOT_FOUND
    src = (v or doc.text).lower()
    m = re.search(r"(очно\s?-\s?заочн|заочн|очн|дистанц)\w*", src)
    if not m:
        return NOT_FOUND
    word = re.sub(r"\s", "", m.group(0))
    return next(full for stem, full in _FORMS if word.startswith(stem))


def _period(doc, start_key, end_key):
    """
    Начало и окончание периода. Порядок:
      1) отдельные подписи («Начало обучения: ...», «Дата окончания практики: ...»);
      2) строка периода («Период обучения: с 01.09.2023 по 31.08.2026»);
      3) оборот «с <дата> по <дата>» рядом со словами о периоде.
    Если одна из дат стерта («с ______ по 31.08.2026»), вторая всё равно извлекается.
    """
    res = {start_key: None, end_key: None}

    for key in (start_key, end_key):
        v = doc.label_value(key)
        if v == "":
            res[key] = NOT_FOUND
        elif v:
            iso = to_iso_date(v)
            res[key] = iso or NOT_FOUND
            for d in doc.dates:          # помечаем дату как использованную
                if iso and d["iso"] == iso and d["start"] >= doc.labeled[key]["pos"] - 1:
                    doc.used_dates.add(d["start"])
                    break

    if res[start_key] is not None and res[end_key] is not None:
        return res

    # фрагменты текста, где может быть период
    chunks = []
    item = doc.labeled.get("__period__")
    if item:
        chunks.append((item["pos"], item["pos"] + max(len(item["value"]), 1) + 60))
    words = {"study_start": r"(обуча\w*|зачислен\w*|(?<![а-я])период\w*|(?<![а-я])срок\w*)",
             "leave_start": r"(отпуск\w*|(?<![а-я])период\w*|(?<![а-я])срок\w*)",
             "internship_start": r"(практик\w*|(?<![а-я])период\w*|(?<![а-я])срок\w*)",
             }.get(start_key, r"((?<![а-я])период\w*|(?<![а-я])срок\w*)")
    for m in re.finditer(words, doc.low):
        chunks.append((m.start(), m.end() + 90))

    for s, e in chunks:
        for d in doc.dates_in(s, e):
            if d["start"] in doc.used_dates:
                continue
            left = doc.low[max(s, d["start"] - 12): d["start"]]
            if res[start_key] is None and re.search(r"(?:^|[^а-я])(с|со|начиная с)\s*$", left):
                res[start_key] = doc.take_date(d)
            elif res[end_key] is None and re.search(r"(?:^|[^а-я])(по|до)\s*$", left):
                res[end_key] = doc.take_date(d)
            elif res[end_key] is None and res[start_key] not in (None, NOT_FOUND) and re.search(r"-\s*$", left):
                res[end_key] = doc.take_date(d)       # «01.09.2023 - 31.08.2026»
        # «01.09.2023 - 31.08.2026» без «с/по»
        ds = [d for d in doc.dates_in(s, e) if d["start"] not in doc.used_dates]
        if res[start_key] is None and res[end_key] is None and len(ds) >= 2:
            between = doc.text[ds[0]["end"]: ds[1]["start"]]
            if re.fullmatch(r"\s*(г\.?|года)?\s*-\s*", between):
                res[start_key] = doc.take_date(ds[0])
                res[end_key] = doc.take_date(ds[1])
        if res[start_key] is not None and res[end_key] is not None:
            break

    for k in res:
        if res[k] is None:
            res[k] = NOT_FOUND
    return res


NUMBER_RE = re.compile(r"(?:№|N[oº°]?\.?|номер\w*)\s*:?\s*([A-ZА-ЯЁa-zа-яё]{1,5}\s?-\s?\d{2,6}|\d{2,8}(?:[/-]\w+)?)",
                       re.IGNORECASE)
NUMBER_FALLBACK = re.compile(r"\b([A-ZА-ЯЁ]{2,3}-\d{4})\b")


def _fix_code(code: str) -> str:
    """Латиница в буквенной части номера -> кириллица (OCR: «OB-1001» -> «ОБ-1001» не угадать, но «OБ» -> «ОБ»)."""
    code = re.sub(r"\s", "", code)
    m = re.match(r"([A-Za-zА-Яа-яЁё]+)(.*)", code)
    if m and re.search(r"[А-Яа-яЁё]", m.group(1)):
        return m.group(1).translate(_LAT2CYR).upper() + m.group(2)
    if m and all(ch in "ABCEHKMOPTXY" for ch in m.group(1).upper()):
        return m.group(1).upper().translate(_LAT2CYR) + m.group(2)
    return code


def ex_document_number(doc, key="document_number"):
    v = _label_or_none(doc, key)
    if v == "":
        return NOT_FOUND
    if v:
        m = re.search(r"[A-ZА-ЯЁa-zа-яё]{0,5}\s?-?\s?\d{2,8}", v)
        return _fix_code(m.group(0)) if m else _clean(v)
    for m in NUMBER_RE.finditer(doc.text):
        left = doc.low[max(0, m.start() - 25): m.start()]
        if re.search(r"(групп|телефон|кабинет|аудитор|приказ)", left):
            continue
        return _fix_code(m.group(1))
    m = NUMBER_FALLBACK.search(doc.text)
    return _fix_code(m.group(1)) if m else NOT_FOUND


def _single_date(doc, key, context_re):
    """Дата из подписи поля или по контексту; иначе — не найдено."""
    v = doc.label_value(key)
    if v == "":
        return NOT_FOUND
    if v:
        iso = to_iso_date(v)
        if iso:
            for d in doc.dates:
                if d["iso"] == iso and d["start"] >= doc.labeled[key]["pos"] - 1:
                    doc.used_dates.add(d["start"])
                    break
        return iso or NOT_FOUND
    for d in doc.dates:
        if d["start"] in doc.used_dates:
            continue
        left = doc.low[max(0, d["start"] - 40): d["start"]]
        if re.search(context_re, left):
            return doc.take_date(d)
    return None    # решит вызывающий


def ex_issue_date(doc, key="issue_date"):
    r = _single_date(doc, key, r"(выдан\w*|выдачи|№\s*\S+\s+от|(?<![а-я])от|(?<![а-я])дата)[^\d]{0,15}$")
    if r is not None:
        return r
    return _last_free_date(doc)


def ex_application_date(doc, key="application_date"):
    r = _single_date(doc, key, r"((?<![а-я])дата|подан\w*|составлен\w*)[^\d]{0,15}$")
    if r is not None:
        return r
    return _last_free_date(doc)


def _last_free_date(doc):
    """Ровно одна неиспользованная дата в нижней части документа — дата документа."""
    free = [d for d in doc.dates if d["start"] not in doc.used_dates]
    if len(free) == 1 and free[0]["start"] >= len(doc.text) * 0.5:
        return doc.take_date(free[0])
    return NOT_FOUND


GROUP_RE = re.compile(r"\b([A-ZА-ЯЁ]{1,5}\s?-\s?\d{2}\s?-\s?\d{1,2})\b")


def ex_group(doc, key="group"):
    v = _label_or_none(doc, key)
    if v == "":
        return NOT_FOUND
    src = v if v else doc.text
    m = GROUP_RE.search(src)
    if m:
        return _fix_code(m.group(1))
    if v:
        return _clean(re.split(r"[,;]|\s(?:курс|\d\s*курс)", v)[0])
    return NOT_FOUND


_COURSE_WORDS = {"перв": "1", "втор": "2", "трет": "3", "четверт": "4", "пят": "5", "шест": "6"}
_ROMAN = {"I": "1", "II": "2", "III": "3", "IV": "4", "V": "5", "VI": "6"}


def ex_course(doc, key="course"):
    v = _label_or_none(doc, key)
    if v == "":
        return NOT_FOUND
    if v:
        m = re.search(r"\b(\d|VI|IV|V|I{1,3})\b", v)
        if m:
            return _ROMAN.get(m.group(1), m.group(1))
        for stem, n in _COURSE_WORDS.items():
            if stem in v.lower():
                return n
        return NOT_FOUND
    m = re.search(r"(?<![\d\w])(\d|VI|IV|V|I{1,3})\s*-?\s*(?:го|й|ого)?\s*курс", doc.text)
    if m:
        return _ROMAN.get(m.group(1), m.group(1))
    m = re.search(r"(перв|втор|трет|четверт|пят|шест)\w*\s+курс", doc.low)
    if m:
        return _COURSE_WORDS[m.group(1)]
    return NOT_FOUND


REASON_RE = re.compile(
    r"((?:по\s+состоянию\s+здоровья|по\s+семейным\s+обстоятельствам|по\s+медицинским\s+показаниям|"
    r"в\s+связи\s+с\s+[^.\n,;]{3,80}?|по\s+уходу\s+за\s+[^.\n,;]{3,60}?|"
    r"по\s+беременности\s+и\s+родам))(?=\s*(?:[.,;\n]|$|с\s+\d|с\s+«|на\s+период|сроком))",
    re.IGNORECASE)


def ex_leave_reason(doc, key="leave_reason"):
    v = _label_or_none(doc, key)
    if v == "":
        return NOT_FOUND
    if v:
        return _clean(re.split(r"\s(?:с|со)\s+\d", v)[0]).rstrip(".")
    m = REASON_RE.search(doc.text)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip().lower()
    return NOT_FOUND


def ex_supervisor(doc, key="supervisor_name"):
    v = _label_or_none(doc, key)
    if v == "":
        return NOT_FOUND
    if v:
        return find_person(v) or NOT_FOUND
    for m in re.finditer(r"руководител\w*(?:\s+практики)?(?:\s+от\s+\w+)?", doc.text, re.IGNORECASE):
        name = find_person(doc.text[m.end(): m.end() + 120])
        if name:
            return name
    return NOT_FOUND


GRADE_RE = re.compile(r"\b(неудовлетворительно|удовлетворительно|отлично|хорошо|не\s*зачтено|зачтено)\b",
                      re.IGNORECASE)


def ex_grade(doc, key="grade"):
    v = _label_or_none(doc, key)
    if v == "":
        return NOT_FOUND
    src = v if v else doc.text
    m = GRADE_RE.search(src)
    if m:
        g = re.sub(r"\s+", " ", m.group(1).lower())
        return "не зачтено" if g.replace(" ", "") == "незачтено" else g
    if v:
        m = re.search(r"\b([2-5])\b", v)
        if m:
            return {"5": "отлично", "4": "хорошо", "3": "удовлетворительно",
                    "2": "неудовлетворительно"}[m.group(1)]
    return NOT_FOUND


def _generic(doc, key):
    """Новое поле из конфига без своей функции: берём текст после подписи."""
    v = doc.label_value(key)
    return _clean(v) if v else NOT_FOUND


EXTRACTORS = {
    "student_name": ex_student_name,
    "university": ex_university,
    "organization": ex_organization,
    "program": ex_program,
    "study_form": ex_study_form,
    "document_number": ex_document_number,
    "group": ex_group,
    "course": ex_course,
    "leave_reason": ex_leave_reason,
    "supervisor_name": ex_supervisor,
    "grade": ex_grade,
}


# =====================================================================
# Подпись и печать: графические элементы на изображении
# =====================================================================
# Подпись и печать в документах пакета — цветные (синие) графические макеты,
# а весь текст — тёмно-серый. Поэтому графику ищем по «цветности» пикселя
# (chroma = max(R,G,B) - min(R,G,B)): у текста она ~15-25, у подписи и печати ~70-80.
# Это не зависит от оттенка: красная или фиолетовая печать тоже найдётся.
# Цветные пиксели склеиваются в пятна, пятна классифицируются по форме:
#   печать  — крупное пятно, близкое к кругу/квадрату (кольцо оттиска);
#   подпись — вытянутый по горизонтали росчерк.
# Надписи «Подпись», «М.П.» серые — они не попадают в маску вообще (п. 2.3).
# Не нашли уверенно — false (строгий режим). Пороги — в MARKS.

MARKS = {
    "work_width": 1000,          # страница приводится к этой ширине
    "chroma": 45,                # пиксель «цветной», если max-min канала >= 45
    "max_brightness": 235,       # почти белые пиксели (сглаживание) не считаем
    "min_blob_px": 150,          # меньше — шум, сжатие JPEG
    "seal_min_side": 0.07,       # печать: обе стороны >= 7% ширины страницы
    "seal_aspect": (0.6, 1.6),   # и почти квадратная рамка
    "sign_min_w": 0.06,          # подпись: ширина >= 6% ширины страницы
    "sign_max_h": 0.10,          # высота <= 10% ширины страницы
    "sign_min_aspect": 1.6,      # и вытянута по горизонтали
}


def load_page_image(file_bytes: bytes, filename: str):
    """Первая страница файла как PIL.Image (PNG/JPG/PDF)."""
    from PIL import Image, ImageOps
    if filename.lower().endswith(".pdf"):
        try:
            import pypdfium2 as pdfium                      # без внешних программ
            page = pdfium.PdfDocument(file_bytes)[0]
            return page.render(scale=2).to_pil().convert("RGB")
        except ImportError:
            from pdf2image import convert_from_bytes          # нужен poppler
            return convert_from_bytes(file_bytes, first_page=1, last_page=1)[0].convert("RGB")
    return ImageOps.exif_transpose(Image.open(io.BytesIO(file_bytes))).convert("RGB")


def pdf_text_layer(file_bytes: bytes) -> str:
    """Текстовый слой PDF (если он есть). Пустая строка — слоя нет, нужен OCR."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(file_bytes))
        return (reader.pages[0].extract_text() or "").strip() if reader.pages else ""
    except Exception:
        return ""


def _blobs(mask):
    """Связные пятна маски: [(x0, y0, x1, y1, pixels)]."""
    import numpy as np
    from scipy import ndimage
    grown = ndimage.binary_dilation(mask, iterations=5)   # склеиваем штрихи и буквы печати
    labels, _ = ndimage.label(grown)
    out = []
    for i, sl in enumerate(ndimage.find_objects(labels), start=1):
        if sl is None:
            continue
        px = int(np.count_nonzero(mask[sl] & (labels[sl] == i)))
        out.append((sl[1].start, sl[0].start, sl[1].stop, sl[0].stop, px))
    return out


def detect_marks(image, boxes=None) -> dict:
    """
    {"signature": bool, "seal": bool, "debug": [...]} по изображению страницы.
    boxes не обязательны (оставлены для совместимости и подсветки).
    """
    result = {"signature": False, "seal": False, "debug": []}
    if image is None:
        return result
    import numpy as np

    img = image.convert("RGB")
    scale = MARKS["work_width"] / img.width
    img = img.resize((MARKS["work_width"], max(1, int(img.height * scale))))
    W = img.width
    rgb = np.asarray(img, dtype=np.int16)
    chroma = rgb.max(axis=2) - rgb.min(axis=2)
    mask = (chroma >= MARKS["chroma"]) & (rgb.max(axis=2) <= MARKS["max_brightness"])

    blobs = [b for b in _blobs(mask) if b[4] >= MARKS["min_blob_px"]]

    def is_seal(b):
        w, h = b[2] - b[0], b[3] - b[1]
        return (min(w, h) >= W * MARKS["seal_min_side"]
                and MARKS["seal_aspect"][0] <= w / max(h, 1) <= MARKS["seal_aspect"][1])

    seals = [b for b in blobs if is_seal(b)]

    def inside_seal(b):
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        return any(s[0] <= cx <= s[2] and s[1] <= cy <= s[3] for s in seals if s is not b)

    for b in blobs:
        x0, y0, x1, y1, px = b
        w, h = x1 - x0, y1 - y0
        aspect = w / max(h, 1)
        kind = None
        if b in seals:
            kind = "seal"
        elif inside_seal(b):
            kind = None                     # текст внутри оттиска печати — не подпись
        elif (w >= W * MARKS["sign_min_w"] and h <= W * MARKS["sign_max_h"]
              and aspect >= MARKS["sign_min_aspect"]):
            kind = "signature"
        result["debug"].append({"box": [round(v / scale) for v in (x0, y0, x1, y1)],
                                "pixels": px, "aspect": round(aspect, 2), "kind": kind})
        if kind:
            result[kind] = True
    return result


# =====================================================================
# Где поле на снимке (бонус «подсветка поля»)
# =====================================================================

def pdf_line_boxes(file_bytes: bytes, scale: float = 2.0) -> list:
    """
    Строки текстового слоя PDF как «боксы OCR» в координатах картинки,
    отрендеренной load_page_image (scale=2). Нет слоя или pypdfium2 — [].
    """
    try:
        import pypdfium2 as pdfium
        page = pdfium.PdfDocument(file_bytes)[0]
        _, height = page.get_size()
        tp = page.get_textpage()
        boxes = []
        for i in range(tp.count_rects()):
            l, b, r, t = tp.get_rect(i)
            text = tp.get_text_bounded(l, b, r, t).strip()
            if not text:
                continue
            x0, y0, x1, y1 = (int(round(v)) for v in (l * scale, (height - t) * scale,
                                                       r * scale, (height - b) * scale))
            boxes.append({"bbox": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]],
                          "text": text, "confidence": 1.0})
        return boxes
    except Exception:
        return []


def _box_rect(b):
    xs = [p[0] for p in b["bbox"]]
    ys = [p[1] for p in b["bbox"]]
    return [min(xs), min(ys), max(xs), max(ys)]


def _group_lines(boxes):
    """Боксы -> строки: [{"rect": [x0,y0,x1,y1], "text": "..."}] сверху вниз."""
    items = sorted((_box_rect(b) + [b.get("text", "")] for b in boxes), key=lambda r: (r[1] + r[3]) / 2)
    lines = []
    for x0, y0, x1, y1, t in items:
        cy, h = (y0 + y1) / 2, y1 - y0
        if lines:
            L = lines[-1]
            lh = L["rect"][3] - L["rect"][1]
            if abs(cy - (L["rect"][1] + L["rect"][3]) / 2) <= max(h, lh) * 0.6:
                L["parts"].append((x0, t))
                L["rect"] = [min(L["rect"][0], x0), min(L["rect"][1], y0),
                             max(L["rect"][2], x1), max(L["rect"][3], y1)]
                continue
        lines.append({"rect": [x0, y0, x1, y1], "parts": [(x0, t)]})
    for L in lines:
        L["text"] = " ".join(t for _, t in sorted(L["parts"]))
    return lines


def field_regions(doc_type: str, boxes: list, marks: list = None, fields: dict = None) -> dict:
    """
    {ключ поля: {"box": [x0, y0, x1, y1], "how": "label" | "value" | "mark" | "place"}}.
    label — строка с подписью поля; value — строка, где встречается значение
    (документы без подписей полей); mark — найденная подпись/печать;
    place — место для подписи/печати, где графики нет.
    """
    specs = field_specs(doc_type)
    lines = _group_lines(boxes or [])
    norm = lambda t: normalize_text(t).lower().replace("ё", "е")
    labels = sorted(((norm(lab), s["key"]) for s in specs for lab in s.get("labels_in_doc", [])),
                    key=lambda x: len(x[0]), reverse=True)
    out = {}

    # 1) строки, которые начинаются с подписи поля (длинные подписи важнее коротких)
    for L in lines:
        low = norm(L["text"])
        for lab, key in labels:
            if key not in out and re.match(re.escape(lab) + r"\s*[:;\-]?", low):
                out[key] = {"box": L["rect"], "how": "label"}
                break

    # 2) поля без подписи — ищем строку с самим значением
    for s in specs:
        v = (fields or {}).get(s["key"])
        if s["key"] in out or s.get("type") in ("boolean", "date") or field_state(v) != "ok":
            continue
        needle = norm(str(v))[:25]
        for L in lines:
            if needle and needle in norm(L["text"]):
                out[s["key"]] = {"box": L["rect"], "how": "value"}
                break

    # 3) подпись и печать — найденные графические элементы или место для них
    for s in specs:
        if s.get("type") != "boolean":
            continue
        kind = mark_kind(s)
        found = [m["box"] for m in (marks or []) if m.get("kind") == kind]
        if found:
            out[s["key"]] = {"box": found[0], "how": "mark"}
            continue
        pat = SEAL_PLACE if kind == "seal" else SIGN_PLACE
        for b in sorted(boxes or [], key=lambda b: -_box_rect(b)[1]):     # снизу вверх
            t = b.get("text", "")
            if pat.search(t) and not re.search(r"вымышлен|макет|учебн", t, re.IGNORECASE):
                out[s["key"]] = {"box": _box_rect(b), "how": "place"}
                break
    return out


SIGN_PLACE = re.compile(r"подпис", re.IGNORECASE)
SEAL_PLACE = re.compile(r"(?<![а-яё])м\.?\s?п\.?(?![а-яё])|печать", re.IGNORECASE)


# =====================================================================
# Сборка
# =====================================================================

def extract_fields(text: str, doc_type: str, boxes=None, image=None) -> dict:
    """
    text  — распознанный текст;
    boxes — боксы EasyOCR [{bbox, text, confidence}] (для подписи/печати);
    image — PIL.Image страницы (для подписи/печати). Без изображения флаги = false.
    Возвращает поля строго в порядке и с ключами из document_types.json.
    """
    if doc_type not in CONFIG:
        raise ValueError(f"Неизвестный тип документа: {doc_type}")
    doc = Doc(text, doc_type, boxes, image)
    specs = field_specs(doc_type)
    values = {}

    # 1) периоды (раньше одиночных дат, чтобы даты периода не ушли в «дату выдачи»)
    keys = doc.cfg.get("period_keys")
    if keys:
        values.update(_period(doc, keys[0], keys[1]))

    # 2) текстовые поля
    for spec in specs:
        key, typ = spec["key"], spec.get("type", "string")
        if key in values or typ == "boolean":
            continue
        try:
            if key == "issue_date":
                values[key] = ex_issue_date(doc)
            elif key == "application_date":
                values[key] = ex_application_date(doc)
            elif key in EXTRACTORS:
                values[key] = EXTRACTORS[key](doc)
            elif typ == "date":
                values[key] = _single_date(doc, key, r"$^") or NOT_FOUND
            else:
                values[key] = _generic(doc, key)
        except Exception:
            values[key] = NOT_FOUND          # одно поле не должно ронять документ

    # 3) подпись и печать — только по графике
    marks = detect_marks(image, boxes) if any(s.get("type") == "boolean" for s in specs) else {}
    for spec in specs:
        if spec.get("type") == "boolean":
            values[spec["key"]] = bool(marks.get(mark_kind(spec), False))

    # порядок и типы — как в конфиге
    out = {}
    for spec in specs:
        v = values.get(spec["key"], NOT_FOUND)
        if spec.get("type") == "boolean":
            out[spec["key"]] = bool(v) if isinstance(v, bool) else False
        else:
            out[spec["key"]] = v if isinstance(v, str) and v.strip() else NOT_FOUND
    return out


def detect_doc_type(text: str, min_score: int = 3):
    """Тип документа по ключевым словам из конфига; None — если не уверены."""
    low = normalize_text(text).lower().replace("ё", "е")
    scores = {t: sum(w for kw, w in cfg.get("detect_keywords", {}).items() if kw in low)
              for t, cfg in CONFIG.items()}
    if not scores:
        return None
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    best, best_score = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0
    return best if best_score >= min_score and best_score > second else None
