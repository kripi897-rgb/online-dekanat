"""
rules.py — Положение (rules.md) и поиск по его пунктам.

load_rules("rules.md") разбирает пункты вида «3.1. Текст...» и запоминает раздел
(«## 3. Справка об обучении»). RulesSearch(rules).find(query, top_k=3) возвращает
пункты, близкие к вопросу: TF-IDF по символьным n-граммам (устойчив к падежам)
+ совпадения по ключевым словам пунктов. Интерфейс прежний.

Важно: ответ для results.json строит responder.py по чётким правилам
(какой пункт требует какое поле), а этот поиск нужен интерфейсу —
показать студенту пункты Положения, близкие к его вопросу.
"""

import re

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# Основы слов (начала слов). Фраза из нескольких основ срабатывает,
# если все основы есть в вопросе.
RULE_KEYWORDS = {
    "1.1": ["сервис", "что умеет", "для чего"],
    "1.2": ["тип документ", "какие документ", "поддерживает"],
    "1.3": ["кто принима", "кто реша", "окончательн", "решени", "сотрудник деканат", "принят"],
    "1.4": ["кабинет сотрудник", "уведомлен", "статус"],
    "2.2": ["не найдено", "не прочита", "пропуск"],
    "2.3": ["подпис", "печат", "м.п", "подлинн"],
    "2.4": ["нет информации", "нет ответа"],
    "2.5": ["формат дат", "дата"],
    "3.1": ["справк обучени", "период обучени", "форм обучени", "направлени подготовк", "вуз"],
    "3.2": ["подпис сотрудник", "подпис справк"],
    "3.3": ["печат вуз", "печат справк"],
    "3.5": ["перевод", "перевест", "перезач", "задолженн", "курс перевода"],
    "4.1": ["академ", "отпуск", "заявлени", "причин", "групп", "курс"],
    "4.2": ["подпис студент", "подпис заявлени"],
    "4.3": ["печат заявлени", "нужна ли печат"],
    "4.4": ["причин", "длительн", "выплат", "стипенди", "подтвержда"],
    "5.1": ["практик", "организаци", "руководител", "оценк"],
    "5.2": ["подпис руководител"],
    "5.3": ["печат организаци"],
    "5.5": ["оценк", "зачёт практик", "зачет практик", "отчёт", "отчет"],
    "6.3": ["нет информации"],
}

W_TFIDF = 0.5
W_KEYWORDS = 0.5

RULE_LINE = re.compile(r"^\s*(\d+\.\d+)\.\s+(.*)$")
SECTION_LINE = re.compile(r"^\s*##\s*(\d+)\.\s*(.+)$")


def load_rules(path: str = "rules.md"):
    """
    Возвращает список пунктов: [{"number": "3.1", "text": "3.1. ...", "section": "Справка об обучении"}].
    Строки без номера (заголовки, вступление) пропускаются; перенос строки
    внутри пункта склеивается с самим пунктом.
    """
    with open(path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()

    rules, section = [], ""
    for line in lines:
        m_sec = SECTION_LINE.match(line)
        if m_sec:
            section = m_sec.group(2).strip()
            continue
        m = RULE_LINE.match(line)
        if m:
            rules.append({"number": m.group(1), "text": f"{m.group(1)}. {m.group(2).strip()}",
                          "section": section})
        elif rules and line.strip() and not line.lstrip().startswith("#"):
            rules[-1]["text"] += " " + line.strip()       # продолжение пункта
    return rules


def rule_text(rules, number: str) -> str:
    """Текст пункта по номеру (для подсказок в интерфейсе)."""
    for r in rules:
        if r["number"] == number:
            return r["text"]
    return ""


def _norm(text: str) -> str:
    return (text or "").lower().replace("ё", "е")


def _tokens(text: str) -> list:
    return re.findall(r"[а-яa-z0-9.]+", _norm(text))


class RulesSearch:
    def __init__(self, rules, keywords: dict = None):
        self.rules = rules
        self.keywords = RULE_KEYWORDS if keywords is None else keywords
        docs = [_norm(r["text"] + " " + r.get("section", "") + " " +
                      " ".join(self.keywords.get(r["number"], []))) for r in rules]
        self.vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True)
        self.matrix = self.vectorizer.fit_transform(docs) if docs else None

    def _keyword_score(self, q_tokens: list, number: str):
        hits, weight = [], 0
        for phrase in self.keywords.get(number, []):
            stems = _norm(phrase).split()
            if all(any(t.startswith(s) for t in q_tokens) for s in stems):
                hits.append(phrase)
                weight += len(stems)
        return min(1.0, weight / 3), hits

    def find(self, query: str, top_k: int = 3):
        if not self.rules:
            return []
        q_norm, q_tokens = _norm(query), _tokens(query)
        sims = (cosine_similarity(self.vectorizer.transform([q_norm]), self.matrix)[0]
                if q_norm.strip() else [0.0] * len(self.rules))
        results = []
        for i, rule in enumerate(self.rules):
            kw, hits = self._keyword_score(q_tokens, rule["number"])
            results.append({"number": rule["number"], "text": rule["text"],
                            "section": rule.get("section", ""),
                            "score": W_TFIDF * float(sims[i]) + W_KEYWORDS * kw,
                            "tfidf": float(sims[i]), "keywords": hits})
        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:top_k]
