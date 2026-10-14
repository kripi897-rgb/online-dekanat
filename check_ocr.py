#!/usr/bin/env python3
"""
check_ocr.py — проверка, что EasyOCR работает на этом компьютере.

    python check_ocr.py documents/open-001.png
    python check_ocr.py documents/open-001.png documents/open-013.png documents/open-024.png

Печатает версии Python/библиотек, время, распознанный текст, извлечённые поля
и сравнение с reference_results.json (если документ там есть). Если OCR падает —
печатает ошибку целиком и подсказку, что делать.
"""

import json
import platform
import sys
import time
import traceback
from pathlib import Path


def versions():
    print(f"Python {platform.python_version()} ({platform.system()} {platform.machine()})")
    for mod in ("easyocr", "torch", "torchvision", "numpy", "PIL", "scipy", "pypdf", "pypdfium2", "streamlit"):
        try:
            m = __import__(mod)
            print(f"  {mod:12} {getattr(m, '__version__', 'ok')}")
        except Exception as e:
            print(f"  {mod:12} НЕТ ({type(e).__name__}: {e})")


HINTS = """
Что проверить:
  * Python 3.14 пока не поддерживается PyTorch/EasyOCR. Нужен Python 3.10–3.12:
        py -3.11 -m venv venv
        .\\venv\\Scripts\\Activate.ps1
        pip install -r requirements.txt
  * «No module named easyocr/torch» — пакеты стоят не в том окружении: активируйте venv
    (в начале строки терминала должно быть (venv)) и повторите pip install.
  * Ошибка при скачивании моделей — первый запуск EasyOCR скачивает веса в ~/.EasyOCR;
    нужен интернет один раз, дальше работает офлайн.
  * numpy 2.x и старый torch конфликтуют — pip install "numpy<2".
"""


def main(paths):
    versions()
    ref = {}
    if Path("reference_results.json").exists():
        ref = {d["document_id"]: d for d in json.loads(Path("reference_results.json").read_text(encoding="utf-8"))["documents"]}
    manifest = {}
    if Path("documents.json").exists():
        manifest = {d["document_id"]: d["document_type"] for d in json.loads(Path("documents.json").read_text(encoding="utf-8"))}

    try:
        import ocr
        t = time.time()
        ocr.get_reader()
        print(f"\nEasyOCR загружен за {time.time() - t:.1f} с")
    except Exception:
        print("\n!!! EasyOCR не запускается:\n" + traceback.format_exc() + HINTS)
        return 1

    from extractor import detect_doc_type, extract_fields, load_page_image
    for p in paths:
        path = Path(p)
        did = path.stem
        print("\n" + "=" * 70 + f"\n{path}")
        try:
            t = time.time()
            text, boxes = ocr.recognize_from_file(path.read_bytes(), path.name)
            print(f"OCR: {time.time() - t:.1f} с, фрагментов {len(boxes)}, {ocr.LAST_INFO}")
        except Exception:
            print("!!! OCR упал:\n" + traceback.format_exc() + HINTS)
            return 1
        print("--- текст ---\n" + text + "\n-------------")
        doc_type = manifest.get(did) or detect_doc_type(text)
        print("тип:", doc_type, "| по тексту:", detect_doc_type(text))
        if not doc_type:
            continue
        fields = extract_fields(text, doc_type, boxes, load_page_image(path.read_bytes(), path.name))
        expected = ref.get(did, {}).get("fields", {})
        good = 0
        for k, v in fields.items():
            mark = ""
            if k in expected:
                ok = v == expected[k] or (isinstance(v, str) and isinstance(expected[k], str)
                                          and v.lower().replace("ё", "е") == expected[k].lower().replace("ё", "е"))
                good += ok
                mark = "OK " if ok else f"XX (ожидалось {expected[k]!r})"
            print(f"  {mark:3} {k}: {v!r}")
        if expected:
            print(f"Совпало полей: {good}/{len(expected)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["documents/open-001.png"]))
