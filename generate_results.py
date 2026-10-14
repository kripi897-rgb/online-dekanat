#!/usr/bin/env python3
"""
generate_results.py — пакетная обработка набора v1.0 и сохранение results.json.

Запуск (из папки проекта):
    python generate_results.py --kit путь/к/пакету --out results.json
    python generate_results.py --kit путь/к/пакету --out results.json --evaluate

Что делает для каждого документа из documents.json:
  1) получает текст:
       --source auto  (по умолчанию) — текстовый слой PDF, если он есть; иначе OCR картинки;
       --source ocr   — всегда EasyOCR по PNG (как с фото студента);
       --source pdf   — только текстовый слой PDF;
  2) получает изображение страницы (PNG, а если его нет — первая страница PDF),
     по нему ищутся графические подпись и печать;
  3) extract_fields -> поля, build_answer -> validation и ответы на вопросы из questions.json;
  4) складывает всё в results.json по results.schema.json.

Тип документа берётся из манифеста (README: «Типы документов в манифесте известны заранее»).
Внешних API нет: EasyOCR, pypdf и pypdfium2 работают локально.
"""

import argparse
import json
import sys
import time
import traceback
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageOps

from extractor import extract_fields, load_page_image, pdf_text_layer
from responder import build_answer

_OCR = None


def ocr_image(path: Path):
    """EasyOCR по файлу картинки -> (текст, боксы). Модель грузится один раз."""
    global _OCR
    if _OCR is None:
        from ocr import recognize_from_file
        _OCR = recognize_from_file
    return _OCR(path.read_bytes(), path.name)


def read_document(kit: Path, item: dict, source: str):
    """
    -> (text, boxes, image, how, error)
    Изображение читается отдельно от текста: если OCR упал, подпись и печать
    всё равно определяются по картинке, а ошибка OCR возвращается в error.
    """
    png = kit / item["image"] if item.get("image") else None
    pdf = kit / item["pdf"] if item.get("pdf") else None
    png = png if png and png.exists() else None
    pdf = pdf if pdf and pdf.exists() else None
    if not png and not pdf:
        raise FileNotFoundError(f"{item['document_id']}: нет ни PNG, ни PDF ({item.get('image')}, {item.get('pdf')})")

    image = (ImageOps.exif_transpose(Image.open(png)).convert("RGB") if png
             else load_page_image(pdf.read_bytes(), pdf.name))

    text, boxes, how, error = "", [], "", None
    if source in ("auto", "pdf") and pdf:
        text = pdf_text_layer(pdf.read_bytes())
        how = "pdf-text"
    if not text.strip() and source != "pdf":
        src = png or pdf
        how = "ocr-png" if png else "ocr-pdf"
        try:
            text, boxes = ocr_image(src)
        except Exception:
            error = traceback.format_exc()
            text, boxes = "", []
    return text, boxes, image, how, error


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kit", default=".", help="папка пакета: documents.json, questions.json, documents/")
    ap.add_argument("--documents", default=None, help="путь к documents.json (по умолчанию <kit>/documents.json)")
    ap.add_argument("--questions", default=None, help="путь к questions.json (по умолчанию <kit>/questions.json)")
    ap.add_argument("--out", default="results.json")
    ap.add_argument("--source", choices=["auto", "ocr", "pdf"], default="auto")
    ap.add_argument("--save-text", default=None, help="папка, куда сохранить распознанный текст (для отладки)")
    ap.add_argument("--evaluate", action="store_true",
                    help="после генерации сравнить с <kit>/reference_results.json через evaluate.py")
    args = ap.parse_args()

    kit = Path(args.kit)
    documents = json.loads(Path(args.documents or kit / "documents.json").read_text(encoding="utf-8"))
    questions = json.loads(Path(args.questions or kit / "questions.json").read_text(encoding="utf-8"))
    by_doc = defaultdict(list)
    for q in questions:
        by_doc[q["document_id"]].append({"question_id": q["question_id"], "question": q["question"]})

    if args.save_text:
        Path(args.save_text).mkdir(parents=True, exist_ok=True)

    results, errors, t0 = [], [], time.time()
    for n, item in enumerate(documents, 1):
        did, doc_type = item["document_id"], item["document_type"]
        try:
            text, boxes, image, how, error = read_document(kit, item, args.source)
        except Exception as e:                       # файла нет — всё равно сдаём запись
            text, boxes, image, how, error = "", [], None, "error", traceback.format_exc()
        if error:
            errors.append((did, error))
            if len(errors) == 1:                     # первую ошибку показываем целиком
                print(f"\n!!! {did}: ошибка чтения текста:\n{error}", file=sys.stderr)
        elif not text.strip():
            print(f"    {did}: текст пустой — проверьте файл", file=sys.stderr)
        fields = extract_fields(text, doc_type, boxes, image)
        res = build_answer(fields, doc_type, questions=by_doc.get(did, []))
        results.append({
            "document_id": did,
            "document_type": doc_type,
            "fields": fields,
            "validation": res["validation"],
            "answers": res["answers"],
        })
        if args.save_text:
            (Path(args.save_text) / f"{did}.txt").write_text(text, encoding="utf-8")
        print(f"[{n}/{len(documents)}] {did} ({doc_type}, {how}): {res['validation']['status']}"
              f"{' ' + ','.join(res['validation']['missing_fields']) if res['validation']['missing_fields'] else ''}")

    if errors:
        print(f"\n!!! Ошибки чтения текста: {len(errors)} из {len(documents)} документов "
              f"({', '.join(d for d, _ in errors[:8])}{' ...' if len(errors) > 8 else ''}).\n"
              f"    У этих документов поля = «не найдено». Причина — в первой ошибке выше.\n"
              f"    Проверка OCR отдельно: python check_ocr.py documents/open-001.png", file=sys.stderr)

    out = {"version": "1.0", "documents": results}
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Сохранено: {args.out} ({len(results)} документов, {sum(len(r['answers']) for r in results)} "
          f"ответов) за {time.time() - t0:.1f} с")

    if args.evaluate:
        ref = kit / "reference_results.json"
        if not ref.exists():
            print("reference_results.json не найден — пропускаю проверку")
        else:
            sys.path.insert(0, str(kit))
            import evaluate
            report = evaluate.evaluate(json.loads(ref.read_text(encoding="utf-8")), out)
            errors_eval = report.pop("errors")
            print(json.dumps(report, ensure_ascii=False, indent=2))
            for e in errors_eval[:40]:
                print("  ", e)

    if errors and len(errors) == len(documents):
        sys.exit(1)          # текст не прочитан ни в одном документе — это ошибка запуска

if __name__ == "__main__":
    main()
