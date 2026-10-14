"""
responder.py — предварительная проверка и ответы на вопросы (формат v1.0).

    validate(fields, doc_type) -> {"status", "missing_fields", "sources"}
    answer_question(fields, doc_type, question, question_id, validation) -> answer
    build_answer(fields, doc_type, question=None, questions=None)
        -> {"validation": {...}, "answers": [...]}

Правила (README, rules.md):
  * status: document_ok / missing_fields;
  * missing_fields: ключи текстовых полей со значением «не найдено» и флаги
    подписи/печати со значением false;
  * sources проверки: для полного документа — все пункты, задающие обязательные
    поля и отметки; для неполного — только пункты недостающих элементов;
  * вопрос о комплектности: value = status, sources = sources проверки;
  * вопрос о поле: value = значение, sources = ["field:<ключ>"];
    поле отсутствует -> answerable=false, value=null, sources=[];
  * «Кто принимает окончательное решение?»: value = «сотрудник деканата», rule:1.3;
  * нет информации -> answerable=false, value=null, sources=[] (п. 2.4, 6.3).

Тип вопроса определяется по формулировке. Значение поля берётся только из
карточки — ничего не додумывается.
"""

import re

from extractor import CONFIG, NOT_FOUND, field_specs

NO_INFO_TEXT = ("В документе и в Положении нет информации для ответа на этот вопрос. "
                "Обратитесь в деканат.")
DECISION_VALUE = "сотрудник деканата"


def _norm(s: str) -> str:
    s = (s or "").lower().replace("ё", "е")
    for ch in "«»“”„":
        s = s.replace(ch, '"')
    return re.sub(r"\s+", " ", s).strip()


def _is_missing(spec, value) -> bool:
    if spec.get("type") == "boolean":
        return value is not True
    return not isinstance(value, str) or _norm(value) in ("", NOT_FOUND)


def _rule_order(rule: str):
    return tuple(int(x) for x in rule.split("."))


# =====================================================================
# Предварительная проверка
# =====================================================================

def validate(fields: dict, doc_type: str) -> dict:
    specs = field_specs(doc_type)
    missing = [s for s in specs if _is_missing(s, fields.get(s["key"]))]
    if missing:
        rules = {s["rule"] for s in missing}
        status = "missing_fields"
    else:
        rules = {s["rule"] for s in specs}
        status = "document_ok"
    return {
        "status": status,
        "missing_fields": [s["key"] for s in missing],
        "sources": [f"rule:{r}" for r in sorted(rules, key=_rule_order)],
    }


def _points(sources) -> str:
    return ", ".join(s.split(":", 1)[1] for s in sources)


def validation_text(fields: dict, doc_type: str, validation: dict) -> str:
    """Текст для студента по п. 6.1–6.2."""
    if validation["status"] == "document_ok":
        return (f"Документ содержит все обязательные сведения по пунктам "
                f"{_points(validation['sources'])} Положения. "
                f"Окончательное решение примет сотрудник деканата.")
    specs = {s["key"]: s for s in field_specs(doc_type)}
    names = ", ".join(specs[k]["label"].lower() for k in validation["missing_fields"])
    word = "пункт" if len(validation["sources"]) == 1 else "пункты"
    return (f"Не хватает следующих сведений: {names}. "
            f"Основание: {word} {_points(validation['sources'])} Положения. "
            f"Окончательное решение примет сотрудник деканата.")


# =====================================================================
# Распознавание вопроса
# =====================================================================

DECISION_RE = re.compile(r"кто\s+(?:\S+\s+)?(принима|реша|утвержда|рассматрива)|окончательн\w*\s+решени|"
                         r"кто\s+(?:примет|решит)")
COMPLETENESS_RE = re.compile(
    r"обязательн\w*\s+(сведени|пол|данн|реквизит)|все\s+ли\s+(сведени|пол|данн|на\s+месте|в\s+порядке)|"
    r"комплектн|в\s+порядке|хватает|хватит|достаточн|полност\w*\s+(заполн|оформл)|"
    r"(проверь|провери\w*|проверка)\s+(документ|справк|заявлени|комплект)?|"
    r"(правильно|верно)\s+ли\s+(оформл|заполн)|чего\s+(не\s+хватает|нет)|что\s+(отсутствует|пропущено)|"
    r"корректн\w*\s+ли|соответствует\s+ли")
QUOTED_RE = re.compile(r'"([^"]{2,80})"')


def detect_question(question: str, doc_type: str):
    """
    -> ("decision", None) | ("completeness", None) | ("field", spec) |
       ("foreign_field", label) | ("no_info", None)
    """
    q = _norm(question)
    if DECISION_RE.search(q):
        return "decision", None

    specs = field_specs(doc_type)
    # 1) «Укажи значение поля «X»» — точное совпадение подписи поля
    for quoted in QUOTED_RE.findall(q):
        for s in specs:
            if _norm(s["label"]) == quoted.strip():
                return "field", s
        # поле другого типа документа (например, «Оценка» в справке об обучении)
        for t, cfg in CONFIG.items():
            for s in cfg["fields"]:
                if _norm(s["label"]) == quoted.strip():
                    return "foreign_field", s["label"]

    if COMPLETENESS_RE.search(q):
        return "completeness", None

    # 2) свободный вопрос: подпись поля или синоним целиком входит в вопрос
    best, best_len = None, 0
    for s in specs:
        for phrase in [s["label"]] + s.get("aliases", []):
            p = _norm(phrase)
            if p and re.search(r"(?<![а-яa-z])" + re.escape(p), q) and len(p) > best_len:
                best, best_len = s, len(p)
    if best:
        return "field", best
    return "no_info", None


# =====================================================================
# Ответ на один вопрос
# =====================================================================

def answer_question(fields: dict, doc_type: str, question: str,
                    question_id: str = "Q1", validation: dict = None) -> dict:
    validation = validation or validate(fields, doc_type)
    kind, payload = detect_question(question, doc_type)

    def out(answerable, value, answer, sources):
        return {"question_id": question_id, "answerable": answerable, "value": value,
                "answer": answer, "sources": list(sources)}

    if kind == "decision":
        return out(True, DECISION_VALUE,
                   "Окончательное решение принимает сотрудник деканата, пункт 1.3 Положения.",
                   ["rule:1.3"])

    if kind == "completeness":
        return out(True, validation["status"],
                   validation_text(fields, doc_type, validation), validation["sources"])

    if kind == "field":
        spec = payload
        value = fields.get(spec["key"])
        if spec.get("type") == "boolean":
            # Подпись/печать: отсутствие графической отметки — тоже факт из документа
            if value is True:
                return out(True, "есть", f"{spec['label']}: есть (графическая отметка найдена).",
                           [f"field:{spec['key']}"])
            return out(True, "нет",
                       f"{spec['label']}: графическая отметка не найдена. Без неё документ неполный "
                       f"(пункт {spec['rule']} Положения).",
                       [f"field:{spec['key']}", f"rule:{spec['rule']}"])
        if _is_missing(spec, value):
            return out(False, None,
                       f"Поле «{spec['label']}» не найдено в документе. Обратитесь в деканат.", [])
        return out(True, value, f"{spec['label']}: {value}.", [f"field:{spec['key']}"])

    if kind == "foreign_field":
        return out(False, None,
                   f"Поле «{payload}» не относится к документу этого типа. {NO_INFO_TEXT}", [])

    return out(False, None, NO_INFO_TEXT, [])


# =====================================================================
# Всё вместе
# =====================================================================

def build_answer(fields: dict, doc_type: str, question: str = None, questions: list = None) -> dict:
    """
    question  — один вопрос (интерфейс); получит question_id «Q1»;
    questions — список {"question_id", "question"} (пакетная обработка).
    """
    if doc_type not in CONFIG:
        raise ValueError(f"Неизвестный тип документа: {doc_type}")
    validation = validate(fields, doc_type)
    items = list(questions or [])
    if question:
        items.append({"question_id": "Q1", "question": question})
    answers = [answer_question(fields, doc_type, it["question"], it["question_id"], validation)
               for it in items]
    return {"validation": validation, "answers": answers}
