"""Parse a text-based legal PDF into article-level JSONL using PyMuPDF."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import pymupdf

ARTICLE_RE = re.compile(r"^(第[一二三四五六七八九十百千万零〇0-9]+条)(?:[ \t　]*(.*))?$")
LEVEL_RE = re.compile(r"^(第[一二三四五六七八九十百千万零〇0-9]+)(分编|编|章|节)(?:[ \t　]+(.+))?$")
LEVEL_ORDER = ("编", "分编", "章", "节")
PARSER_VERSION = "0.2.0"


def clean_text(value: str) -> str:
    """Remove layout whitespace beside Chinese characters; keep Latin spaces."""
    value = re.sub(r"\s+", " ", value).strip()
    return re.sub(r"(?<=[\u3400-\u9fff\uff00-\uffef，。；：、！？（）《》“”]) +| +(?=[\u3400-\u9fff\uff00-\uffef，。；：、！？（）《》“”])", "", value)


def normalize(value: str) -> str:
    return re.sub(r"[ \t　]+", " ", value.replace("\u00a0", " ")).strip()


def article_number(article: str) -> int | None:
    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    value = article[1:-1]
    if value.isdigit():
        return int(value)
    total = section = current = 0
    for char in value:
        if char in digits:
            current = digits[char]
        elif char in "十百千":
            section += (current or 1) * {"十": 10, "百": 100, "千": 1000}[char]
            current = 0
        elif char == "万":
            total = (total + section + current) * 10000
            section = current = 0
        else:
            return None
    return total + section + current


def extract_lines(pdf_path: Path) -> list[tuple[int, str]]:
    lines = []
    with pymupdf.open(pdf_path) as document:
        for page_no, page in enumerate(document, 1):
            page_lines = []
            # Keep PDF reading order. Global y/x sorting interleaves two columns.
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    raw = "".join(span["text"] for span in line["spans"])
                    text = normalize(raw)
                    if not text:
                        continue
                    x0, y0, x1, y1 = line["bbox"]
                    # Restrict removal to recognizable furniture at page edges.
                    # Do not discard every short/repeated line or a whole margin.
                    at_edge = y0 < 25 or y0 > page.rect.height - 25
                    if at_edge and ("全国人民代表大会常务委员会公报" in text or re.fullmatch(r"[—－\-0-9０-９ ]+", text)):
                        continue
                    # Letter-spaced titles may be emitted as separate lines at
                    # the same baseline (e.g. 第二章 自 / 然 / 人).
                    if page_lines and abs(page_lines[-1][1][1] - y0) < 2 and 0 <= x0 - page_lines[-1][1][2] < 60:
                        previous, bbox = page_lines[-1]
                        page_lines[-1] = (previous + text, (bbox[0], min(bbox[1], y0), x1, max(bbox[3], y1)))
                    else:
                        page_lines.append((text, (x0, y0, x1, y1)))
            if not page_lines:
                raise ValueError(f"Page {page_no} has no usable text; review the PDF or use OCR")
            lines.extend((page_no, text) for text, _ in page_lines)
    return lines


def parse(pdf_path: Path, metadata: dict) -> list[dict]:
    records, hierarchy, current = [], {}, None
    last_article = None
    lines = extract_lines(pdf_path)
    first_article = next((i for i, (_, line) in enumerate(lines) if ARTICLE_RE.match(line) and article_number(ARTICLE_RE.match(line).group(1)) == 1), None)
    if first_article is None:
        raise ValueError("No first article found; only complete, continuously numbered laws are supported")
    # Exclude publication notes and the table of contents. Only consecutive
    # body headings immediately preceding article 1 establish its initial path.
    body_start = first_article
    while body_start and LEVEL_RE.match(lines[body_start - 1][1]):
        body_start -= 1

    def flush():
        nonlocal current
        if current is None:
            return
        raw = "\n".join(current.pop("_parts")).strip()
        text = clean_text(raw)
        if text:
            current["text_raw"] = raw
            current["text"] = text
            current["content_hash"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
            records.append(current)
        else:
            raise ValueError(f"Empty article: {current['article_no']}")
        current = None

    active_heading = None
    for page_no, line in lines[body_start:]:
        level = LEVEL_RE.match(line)
        if level:
            flush()
            kind, title = level.group(2), f"{level.group(1)}{level.group(2)}"
            if not level.group(3):
                raise ValueError(f"Missing heading title on page {page_no}: {line}")
            title += f" {clean_text(level.group(3))}"
            hierarchy[kind] = title
            active_heading = kind
            for child in LEVEL_ORDER[LEVEL_ORDER.index(kind) + 1:]:
                hierarchy.pop(child, None)
            continue
        if re.fullmatch(r"附\s*则", line):
            flush()
            hierarchy = {"附则": "附则"}
            active_heading = None
            continue
        article = ARTICLE_RE.match(line)
        if article:
            article_no, first_text = article.group(1), article.group(2) or ""
            current_number = article_number(article_no)
            expected = (last_article or 0) + 1
            if current_number != expected:
                # A backward reference attached to preceding unfinished text is
                # body content. Forward jumps/duplicates after sentence endings
                # must fail rather than swallowing the rest of a document.
                previous = current["_parts"][-1] if current and current["_parts"] else ""
                if current and current_number < expected and previous and previous[-1] not in "。！？：":
                    current["_parts"].append(line)
                    current["page_end"] = page_no
                    continue
                raise ValueError(f"Page {page_no}: expected article {expected}, found {article_no}; review extraction")
            flush()
            current = {
                "provision_id": f"{metadata.get('title', pdf_path.stem)}::{metadata.get('publication_date', 'unknown')}::{article_no}",
                "law_id": pdf_path.stem,
                "law_title": metadata.get("title", pdf_path.stem),
                "article_no": article_no,
                "hierarchy_path": list(hierarchy.values()),
                "source_file": pdf_path.name,
                "source_url": metadata.get("source_url"),
                "publication_date": metadata.get("publication_date"),
                "effective_date": metadata.get("effective_date"),
                "expiry_date": metadata.get("expiry_date"),
                "status": metadata.get("status"),
                "retrieved_at": metadata.get("retrieved_at"),
                "parser_version": PARSER_VERSION,
                "page_start": page_no,
                "page_end": page_no,
                "_parts": [first_text] if first_text else [],
            }
            last_article = current_number
            active_heading = None
        elif current is not None:
            current["_parts"].append(line)
            current["page_end"] = page_no
        elif active_heading:
            hierarchy[active_heading] += clean_text(line)
        else:
            raise ValueError(f"Unassigned body text on page {page_no}: {line}")
    flush()
    return records


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--metadata", type=Path, default=Path("data/metadata/legal_sources.json"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    metadata = {x["local_filename"]: x for x in json.loads(args.metadata.read_text(encoding="utf-8-sig"))}[args.pdf.name]
    records = parse(args.pdf, metadata)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in records), encoding="utf-8")
    print(json.dumps({"extractor": "pymupdf", "articles": len(records), "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
