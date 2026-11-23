#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sk_entry.py —— 引用与事实核验（citation-and-fact-verification）。

用法（Harbor 会按此调用；手动复现同一条命令）：

    python /skills/weekly-skill/scripts/sk_entry.py \
        --input /input --work /app/work --out /app/output

输入：/input 下的 case.json、task.md 与稿件/来源文件（md/txt/json/jsonl/xml/csv/pdf）。
输出：<out>/result.json（契约字段齐全）与 <out>/run-review.md（人类可读复核记录）。

设计约束：
  * 仅标准库，不联网，结果确定（相同输入 → 相同输出，无时间戳/随机数）；
  * 每条带引用的论断都给出判定、严重度、证据定位（文件+章节+原文短引）与修改建议；
  * 无法定位来源、来源缺失标识符或 PDF 抽取失败一律记 unverifiable，绝不补造；
  * 入口在异常时仍写出结构合法、契约键齐全的 result.json，并以 0 退出。
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
import unicodedata
import zlib
import xml.etree.ElementTree as ET
from pathlib import Path

SKILL_NAME = "citation-and-fact-verification"
SKILL_VERSION = "2.0.0"
DEFAULT_INPUT = "/input"
DEFAULT_WORK = "/app/work"
DEFAULT_OUT = "/app/output"
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_FILES = 400
MAX_TEXT_CHARS = 4_000_000

# ---------------------------------------------------------------------------
# 默认策略（assets/policy.json 缺失或损坏时使用同一份内置值）
# ---------------------------------------------------------------------------
DEFAULT_POLICY = {
    "version": SKILL_VERSION,
    "thresholds": {
        "supported_min": 0.55,
        "partial_min": 0.28,
        "min_claim_terms": 3,
        "quote_min_chars": 8,
        "excerpt_chars": 240,
        "max_claims": 300,
        "max_findings": 600,
    },
    "severity_levels": ["high", "medium", "low", "info"],
    "verdict_severity": {
        "supported": "info",
        "partially_supported": "medium",
        "unsupported": "high",
        "unverifiable": "medium",
    },
    "stopwords": [
        "the", "and", "for", "with", "from", "that", "this", "these", "those", "were",
        "was", "are", "is", "has", "have", "had", "not", "but", "also", "can", "could",
        "may", "might", "will", "would", "should", "our", "we", "their", "its", "they",
        "which", "than", "more", "less", "into", "over", "under", "about", "between",
        "among", "study", "studies", "paper", "article", "result", "results", "data",
        "analysis", "group", "groups", "using", "used", "use", "based", "however",
        "therefore", "thus", "here", "there", "been", "being", "such", "other", "same",
        "本文", "我们", "研究", "方法", "结果", "数据", "分析", "显示", "表明", "以及",
        "相关", "进行", "通过", "可以", "需要", "提供", "本文", "该", "其", "这", "那",
    ],
    "negation_cues": [
        "not", "no", "never", "none", "cannot", "can't", "without", "fail", "failed",
        "absence", "lack", "lacked", "unable", "不", "无", "未", "没有", "并非", "不能",
    ],
    "overreach_cues": [
        "all", "every", "always", "none", "never", "proves", "proven", "confirms",
        "guarantees", "guarantee", "universally", "safe for", "definitively", "全部",
        "所有", "总是", "从不", "证明", "确保", "完全",
    ],
    "method_cues": [
        "randomized", "randomised", "randomization", "assay", "measured", "we used",
        "we performed", "protocol", "participants were", "samples were", "统计方法",
        "随机", "样本量", "实验设计", "盲法",
    ],
    "method_terms": [
        "randomized", "randomised", "random", "trial", "assay", "measured", "protocol",
        "participants", "samples", "method", "methods", "blind", "placebo", "随机", "方法",
        "样本", "盲法", "对照",
    ],
    "peer_reviewed_markers": [
        "journal", "transactions", "proceedings", "annals", "archives", "review",
        "nature", "science", "lancet", "cell", "bmj", "jama", "plos", "springer",
        "elsevier", "wiley", "press", "学报", "期刊", "杂志",
    ],
    "preprint_markers": [
        "arxiv", "biorxiv", "medrxiv", "ssrn", "preprint", "research square",
        "techrxiv", "osf", "预印本",
    ],
    "retraction_markers": ["retracted", "retraction", "withdrawn", "撤稿", "撤回"],
    "numeric_units": {
        "%": "percent", "percent": "percent", "percentage": "percent",
        "participant": "participants", "participants": "participants",
        "patient": "patients", "patients": "patients",
        "subject": "participants", "subjects": "participants",
        "case": "cases", "cases": "cases",
        "site": "sites", "sites": "sites",
        "trial": "trials", "trials": "trials",
        "year": "years", "years": "years", "month": "months", "months": "months",
        "week": "weeks", "weeks": "weeks", "day": "days", "days": "days",
        "hour": "hours", "hours": "hours", "mg": "mg", "kg": "kg", "ml": "ml",
        "cm": "cm", "mm": "mm",
    },
    "caps": {"max_streams": 300, "max_sources": 500, "max_pdf_chars": 200000},
}

# 来源/文献记录的输入字段别名（归一化后比较）。
FIELD_ALIASES = {
    "id": ["id", "index", "number", "key", "ref_id", "refid", "citation_id", "citationid",
           "source_id", "sourceid", "编号", "序号"],
    "title": ["title", "name", "headline", "label", "标题", "题名", "名称"],
    "authors": ["authors", "author", "creator", "creators", "byline", "作者", "著者"],
    "year": ["year", "publication_year", "publicationyear", "pub_year", "date", "年份", "出版年"],
    "venue": ["venue", "journal", "conference", "publisher", "source_title", "sourcetitle",
              "publication", "期刊", "会议", "出版", "来源"],
    "doi": ["doi", "DOI", "digital_object_identifier", "digitalobjectidentifier"],
    "url": ["url", "link", "href", "permalink", "地址", "链接"],
    "isbn": ["isbn", "issn", "isbn13", "issn_l"],
    "abstract": ["abstract", "summary", "description", "excerpt", "摘要", "提要"],
    "text": ["text", "body", "content", "full_text", "fulltext", "full-text", "paragraphs",
             "sections", "正文", "全文"],
    "retracted": ["retracted", "is_retracted", "isretracted", "withdrawn", "retraction_status",
                  "retractionstatus", "retraction", "status", "撤稿", "状态"],
    "retraction_note": ["retraction_note", "retractionnote", "retraction_reason", "note", "撤稿说明"],
    "source_type": ["source_type", "sourcetype", "type", "kind", "doc_type", "doctype", "文献类型"],
}

CONTAINER_KEYS = (
    "sources", "references", "reference_list", "reflist", "bibliography", "works_cited",
    "records", "items", "results", "data", "documents", "entries", "refs", "list",
    "payload", "response", "cited_sources", "evidence",
)

RECORD_MARKERS = ("title", "authors", "author", "abstract", "doi", "venue", "journal", "year")

EXCLUDE_STEMS = {
    "case", "task", "readme", "skill", "quickstart", "github_url", "policy",
    "sources", "source", "references", "refs", "bibliography", "manifest", "license",
}

HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
LITERAL_HEADING_RE = re.compile(
    r"^\s*(abstract|introduction|background|methods?|materials and methods|results?|"
    r"discussion|conclusions?|limitations|references|摘要|引言|方法|材料与方法|结果|讨论|结论|局限|参考文献)\s*$",
    re.IGNORECASE,
)
NUMERIC_MARKER_RE = re.compile(r"\[([0-9][0-9,\s\-\u2013]*)\]")
AUTHOR_YEAR_RE = re.compile(
    r"\(([A-Z][A-Za-z\u00c0-\u024f\-]+)(?:\s+et\s+al\.?)?(?:\s*(?:&|and)\s*[A-Z][A-Za-z\-]+)?"
    r",?\s*((?:19|20)\d{2})[a-z]?\)"
)
EXPLICIT_REF_RE = re.compile(
    r"(?:见文献|参见文献|文献|see\s+ref(?:erence)?\.?|see\s+document)\s*\[?(\d+)\]?",
    re.IGNORECASE,
)
NUMBER_RE = re.compile(
    r"(?<![\w.])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*"
    r"(%|percent|percentage|participants?|patients?|subjects?|cases?|sites?|trials?|"
    r"years?|months?|weeks?|days?|hours?|mg|kg|ml|cm|mm)?",
    re.IGNORECASE,
)
QUOTE_RE = re.compile(r"[\u201c\"]([^\u201d\"]{8,200})[\u201d\"]")

VERDICTS = ("supported", "partially_supported", "unsupported", "unverifiable")


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def norm_space(value):
    if not isinstance(value, str):
        return ""
    try:
        value = unicodedata.normalize("NFKC", value)
    except (TypeError, ValueError):
        return ""
    return re.sub(r"\s+", " ", value).strip()


def norm_key(value):
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", str(value).lower())


def as_text(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return norm_space(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return norm_space(" ".join(part for part in (as_text(item) for item in value) if part))
    if isinstance(value, dict):
        for key in ("text", "abstract", "value", "content", "title", "name"):
            if key in value and value[key] not in (None, "", [], {}):
                return as_text(value[key])
        try:
            return norm_space(json.dumps(value, ensure_ascii=False, sort_keys=True))[:800]
        except (TypeError, ValueError):
            return ""
    return norm_space(str(value))


def get_any(mapping, names, default=None):
    if not isinstance(mapping, dict):
        return default
    index = {}
    for key in mapping.keys():
        index.setdefault(norm_key(key), key)
    for name in names:
        key = index.get(norm_key(name))
        if key is not None and mapping[key] not in (None, "", [], {}):
            return mapping[key]
    return default


def pick(record, canonical):
    return get_any(record, FIELD_ALIASES.get(canonical, []), None)


def load_json(path):
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return json.load(handle)


def load_policy():
    policy = json.loads(json.dumps(DEFAULT_POLICY))
    path = Path(__file__).resolve().parent.parent / "assets" / "policy.json"
    try:
        if path.is_file():
            overlay = load_json(path)
            if isinstance(overlay, dict):
                for key, value in overlay.items():
                    if isinstance(value, dict) and isinstance(policy.get(key), dict):
                        merged = dict(policy[key])
                        merged.update(value)
                        policy[key] = merged
                    else:
                        policy[key] = value
    except (OSError, ValueError):
        pass
    return policy


def read_text_file(path):
    data = Path(path).read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", "replace")


def rel_path(path, root):
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except (ValueError, OSError):
        return Path(path).name


def iter_files(root):
    base = Path(root)
    if not base.is_dir():
        return []
    files = []
    for path in sorted(base.rglob("*")):
        if len(files) >= MAX_FILES:
            break
        if not path.is_file() or path.is_symlink():
            continue
        if "__pycache__" in path.parts or path.name.startswith("."):
            continue
        files.append(path)
    return files


def write_json(path, payload):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    os.replace(str(tmp), str(target))


def write_text(path, text):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(str(tmp), str(target))


# ---------------------------------------------------------------------------
# PDF 文本抽取（标准库，尽力而为，低置信）
# ---------------------------------------------------------------------------
def _pdf_decode_string(raw):
    out = bytearray()
    index = 0
    mapping = {0x6E: 10, 0x72: 13, 0x74: 9, 0x62: 8, 0x66: 12,
               0x28: 40, 0x29: 41, 0x5C: 92}
    while index < len(raw):
        byte = raw[index]
        if byte == 0x5C and index + 1 < len(raw):
            nxt = raw[index + 1]
            if nxt in mapping:
                out.append(mapping[nxt])
                index += 2
                continue
            if 0x30 <= nxt <= 0x37:
                digits = bytes([nxt])
                index += 2
                for _ in range(2):
                    if index < len(raw) and 0x30 <= raw[index] <= 0x37:
                        digits += bytes([raw[index]])
                        index += 1
                    else:
                        break
                out.append(int(digits, 8) & 0xFF)
                continue
            index += 2
            continue
        out.append(byte)
        index += 1
    return out.decode("latin-1", "replace")


def extract_pdf_text(data, policy):
    """返回 (text, note)。失败时 text 为空，note 说明原因，绝不编造。"""
    if not data.startswith(b"%PDF") and b"%PDF" not in data[:2048]:
        return "", "文件不以 %PDF 开头，按非 PDF 处理。"
    max_streams = int(policy.get("caps", {}).get("max_streams", 300))
    max_chars = int(policy.get("caps", {}).get("max_pdf_chars", 200000))
    pieces = []
    for count, match in enumerate(re.finditer(rb"stream(?:\r\n|\r|\n)(.*?)endstream", data, re.S)):
        if count >= max_streams or sum(len(p) for p in pieces) >= max_chars:
            break
        raw = match.group(1)
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            pass
        for chunk in re.finditer(
            rb"\[(.*?)\]\s*TJ|\((?:\\.|[^\\()])*\)\s*Tj|\((?:\\.|[^\\()])*\)\s*'", raw, re.S
        ):
            for literal in re.finditer(rb"\((?:\\.|[^\\()])*\)|<([0-9A-Fa-f\s]+)>", chunk.group(0), re.S):
                if literal.group(0).startswith(b"("):
                    pieces.append(_pdf_decode_string(literal.group(0)[1:-1]))
                else:
                    hex_value = re.sub(rb"\s+", b"", literal.group(1) or b"")
                    try:
                        decoded = bytes.fromhex(hex_value.decode("ascii"))
                    except (ValueError, UnicodeDecodeError):
                        continue
                    if len(decoded) % 2 == 0:
                        try:
                            pieces.append(decoded.decode("utf-16-be", "ignore"))
                        except (UnicodeDecodeError, ValueError):
                            pieces.append(decoded.decode("latin-1", "replace"))
    text = norm_space(" ".join(piece for piece in pieces if piece))
    if len(text) < 40:
        return "", "PDF 未发现可抽取文本层（可能为扫描件），需 OCR 或人工核对。"
    return text[:max_chars], "PDF 文本按内容流启发式抽取，置信度低；页码与排版不可靠。"


# ---------------------------------------------------------------------------
# 记录解析
# ---------------------------------------------------------------------------
def flatten_records(node):
    if isinstance(node, list):
        out = []
        for item in node:
            out.extend(flatten_records(item))
        return out
    if not isinstance(node, dict):
        return []
    lowered = {norm_key(key): key for key in node.keys()}
    for container in CONTAINER_KEYS:
        key = lowered.get(norm_key(container))
        if key is not None and isinstance(node[key], (list, dict)):
            inner = flatten_records(node[key])
            if inner:
                return inner
    if any(norm_key(key) in {norm_key(m) for m in RECORD_MARKERS} for key in node.keys()):
        return [node]
    out = []
    for value in node.values():
        if isinstance(value, (dict, list)):
            out.extend(flatten_records(value))
    return out


def parse_json_text(text):
    node = json.loads(text)
    return flatten_records(node)


def parse_jsonl_text(text):
    records = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            node = json.loads(line)
        except ValueError:
            continue
        records.extend(flatten_records(node) if isinstance(node, (dict, list)) else [])
    return records


def parse_csv_text(text):
    records = []
    try:
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            if any((value or "").strip() for value in row.values()):
                records.append(dict(row))
    except (csv.Error, ValueError):
        return []
    return records


def parse_xml_text(text):
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []
    records = []
    for child in list(root):
        record = {}
        for key, value in child.attrib.items():
            record[key] = value
        for sub in list(child):
            if len(list(sub)) == 0:
                record[sub.tag] = (sub.text or "").strip()
            for key, value in sub.attrib.items():
                record["%s_%s" % (sub.tag, key)] = value
        if record:
            records.append(record)
    if not records:
        record = {root.tag: (root.text or "").strip()}
        if any(record.values()):
            records.append(record)
    return records


def parse_reference_lines(text):
    """从纯文本里识别 [1] / 1. 起首的参考文献行。"""
    records = []
    current = None
    for line in text.splitlines():
        match = re.match(r"^\s*(?:\[(\d+)\]|(\d+)[.)、])\s+(.+)$", line)
        heading = re.match(r"^\s*#{1,6}\s*\[(\d+)\]\s*(.+)$", line)
        if heading:
            match = heading
        if match and (match.group(1) or match.group(2)):
            if current:
                records.append(current)
            identifier = match.group(1) or match.group(2)
            body = match.group(3) if match.lastindex and match.lastindex >= 3 else match.group(2)
            current = {"id": identifier, "title": norm_space(body)}
        elif current is not None and line.strip():
            current["title"] = norm_space(current["title"] + " " + line.strip())
        else:
            if current:
                records.append(current)
                current = None
    if current:
        records.append(current)
    # 只有一行且没有标题标记时，不视为文献表。
    if len(records) == 1 and not re.search(r"\b(19|20)\d{2}\b|doi", records[0].get("title", ""), re.I):
        return []
    return records


def file_records(path, policy, errors):
    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf":
            text, note = extract_pdf_text(Path(path).read_bytes(), policy)
            if not text:
                errors.append({"stage": "parse", "file": path.name, "type": "pdf_no_text", "message": note})
                return []
            return [{"text": text, "_extraction": "pdf_low_confidence", "_note": note}]
        text = read_text_file(path)
    except OSError as exc:
        errors.append({"stage": "read", "file": path.name, "type": type(exc).__name__, "message": str(exc)[:200]})
        return []
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS]
        errors.append({"stage": "read", "file": path.name, "type": "truncated", "message": "文件超长，仅读取前 4M 字符。"})
    if suffix == ".json":
        try:
            return parse_json_text(text)
        except ValueError as exc:
            errors.append({"stage": "parse", "file": path.name, "type": "json_error", "message": str(exc)[:200]})
            return []
    if suffix in (".jsonl", ".ndjson"):
        return parse_jsonl_text(text)
    if suffix == ".csv":
        return parse_csv_text(text)
    if suffix == ".xml":
        return parse_xml_text(text)
    if suffix in (".md", ".markdown", ".txt", ".text"):
        records = parse_reference_lines(text)
        if records:
            return records
        first_line = next((line.strip("# ").strip() for line in text.splitlines() if line.strip()), "")
        return [{"title": first_line[:200] or path.stem, "text": text}]
    return []


def normalize_source(raw, source_file, index, locator, errors):
    if not isinstance(raw, dict):
        raw = {"text": as_text(raw)}
    identifier = pick(raw, "id")
    if identifier in (None, ""):
        identifier = None
    else:
        text = as_text(identifier)
        identifier = text
    retracted_raw = pick(raw, "retracted")
    retracted = False
    retraction_note = as_text(pick(raw, "retraction_note"))
    if isinstance(retracted_raw, bool):
        retracted = retracted_raw
    elif retracted_raw is not None:
        lowered = as_text(retracted_raw).lower()
        negated = any(token in lowered for token in ("not retracted", "no retraction", "未撤稿", "非撤稿"))
        retracted = (not negated) and any(marker in lowered for marker in ("retract", "withdraw", "撤稿", "撤回"))
    for key, value in raw.items():
        if "retract" in norm_key(key):
            text = as_text(value).lower()
            if isinstance(value, bool) and value:
                retracted = True
            elif any(marker in text for marker in ("true", "yes", "retracted", "withdrawn", "撤稿", "撤回")):
                retracted = True
    extracted_note = as_text(raw.get("_note")) if isinstance(raw, dict) else ""
    record = {
        "source_id": identifier,
        "title": as_text(pick(raw, "title")),
        "authors": as_text(pick(raw, "authors")),
        "year": as_text(pick(raw, "year")),
        "venue": as_text(pick(raw, "venue")),
        "doi": as_text(pick(raw, "doi")),
        "url": as_text(pick(raw, "url")),
        "isbn": as_text(pick(raw, "isbn")),
        "abstract": as_text(pick(raw, "abstract")),
        "text": as_text(pick(raw, "text")),
        "retracted": bool(retracted),
        "retraction_note": retraction_note,
        "source_file": source_file,
        "source_index": index,
        "locator": locator,
        "extraction": as_text(raw.get("_extraction")) if isinstance(raw, dict) else "",
        "extraction_note": extracted_note,
        "origin": "file",
    }
    if not record["title"] and record["text"]:
        record["title"] = record["text"][:120]
    if not any((record["title"], record["abstract"], record["text"], record["doi"])):
        return None
    return record


def normalize_case_reference(raw, index):
    if isinstance(raw, str):
        raw = {"title": raw}
    if not isinstance(raw, dict):
        return None
    identifier = pick(raw, "id")
    identifier = as_text(identifier) if identifier not in (None, "") else None
    retracted_raw = pick(raw, "retracted")
    retracted = False
    if isinstance(retracted_raw, bool):
        retracted = retracted_raw
    elif retracted_raw is not None:
        lowered = as_text(retracted_raw).lower()
        negated = any(token in lowered for token in ("not retracted", "no retraction", "未撤稿"))
        retracted = (not negated) and any(marker in lowered for marker in ("retract", "withdraw", "撤稿", "撤回"))
    record = {
        "source_id": identifier,
        "title": as_text(pick(raw, "title")),
        "authors": as_text(pick(raw, "authors")),
        "year": as_text(pick(raw, "year")),
        "venue": as_text(pick(raw, "venue")),
        "doi": as_text(pick(raw, "doi")),
        "url": as_text(pick(raw, "url")),
        "isbn": as_text(pick(raw, "isbn")),
        "abstract": as_text(pick(raw, "abstract")),
        "text": as_text(pick(raw, "text")),
        "retracted": bool(retracted),
        "retraction_note": as_text(pick(raw, "retraction_note")),
        "source_file": "case.json",
        "source_index": index,
        "locator": "/case.json#references[%d]" % index,
        "extraction": "",
        "extraction_note": "",
        "origin": "case",
    }
    if not any((record["title"], record["abstract"], record["text"], record["doi"])):
        return None
    return record


# ---------------------------------------------------------------------------
# 文本分析
# ---------------------------------------------------------------------------
def tokenize(text, policy, limit=200):
    stop = {norm_key(word) for word in policy.get("stopwords", [])}
    words = []
    for word in re.findall(r"[A-Za-z\u00c0-\u024f][A-Za-z0-9\u00c0-\u024f\-]{2,}", norm_space(text)):
        key = norm_key(word)
        if len(key) < 3 or key in stop:
            continue
        words.append(key)
    for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        for index in range(len(chunk) - 1):
            words.append(chunk[index:index + 2])
    seen, ordered = set(), []
    for word in words:
        if word not in seen:
            seen.add(word)
            ordered.append(word)
    return ordered[:limit]


def strip_markers(text):
    cleaned = NUMERIC_MARKER_RE.sub(" ", text)
    cleaned = AUTHOR_YEAR_RE.sub(" ", cleaned)
    cleaned = EXPLICIT_REF_RE.sub(" ", cleaned)
    return norm_space(cleaned)


def numeric_tokens(text):
    values = []
    for match in NUMBER_RE.finditer(text):
        raw_value = match.group(1).replace(",", "")
        unit_raw = (match.group(2) or "").lower()
        unit = DEFAULT_POLICY["numeric_units"].get(unit_raw, "")
        try:
            number = float(raw_value)
        except ValueError:
            continue
        if not unit and 1900 <= number <= 2100 and number == int(number):
            continue  # 年份不作为数值一致性比较对象
        values.append({"value": raw_value, "number": number, "unit": unit, "raw": match.group(0).strip()})
    return values


def compare_numbers(claim_numbers, source_numbers):
    missing, mismatched = [], []
    source_index = {}
    for item in source_numbers:
        source_index.setdefault(item["unit"], []).append(item)
    for item in claim_numbers:
        same_unit = source_index.get(item["unit"], [])
        if any(abs(other["number"] - item["number"]) < 1e-9 for other in same_unit):
            continue
        if same_unit:
            mismatched.append({
                "claim_value": item["raw"],
                "source_values": [other["raw"] for other in same_unit][:5],
                "unit": item["unit"] or "none",
            })
        else:
            missing.append(item["raw"])
    return missing, mismatched


def neg_count(text):
    lowered = norm_space(text).lower()
    return sum(lowered.count(cue) for cue in DEFAULT_POLICY["negation_cues"])


def has_overreach(text):
    lowered = norm_space(text).lower()
    return any(cue in lowered for cue in DEFAULT_POLICY["overreach_cues"])


def split_sentences(text):
    if not text:
        return []
    boundaries = []
    pattern = re.compile(r"(?<=[.!?])\s+|(?<=[\u3002\uff01\uff1f\uff1b])|\n\s*\n+")
    for match in pattern.finditer(text):
        boundaries.append(match.start())
    spans = []
    start = 0
    for boundary in boundaries:
        spans.append((start, boundary))
        start = boundary
    spans.append((start, len(text)))
    sentences = []
    for begin, end in spans:
        raw = text[begin:end]
        stripped = raw.strip()
        if not stripped:
            continue
        leading = len(raw) - len(raw.lstrip())
        trailing = len(raw) - len(raw.rstrip())
        begin = begin + leading
        end = end - trailing
        sentences.append({"start": begin, "end": end, "text": norm_space(text[begin:end])})
    # 合并被缩写切开的小片段（如 "et al." + "(2020)"）。
    merged = []
    for item in sentences:
        if merged:
            previous = merged[-1]
            if len(item["text"]) < 60 and (
                item["text"][:1].islower() or item["text"].startswith(("(", "et al", "et al."))
            ):
                previous["text"] = norm_space(previous["text"] + " " + item["text"])
                previous["end"] = item["end"]
                continue
        merged.append(dict(item))
    return merged


def section_map(text):
    sections = []
    offset = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        heading = None
        match = HEADING_RE.match(stripped)
        if match:
            heading = match.group(2).strip()
        elif LITERAL_HEADING_RE.match(stripped) and len(stripped) <= 60:
            heading = stripped
        if heading:
            sections.append({"offset": offset, "title": heading})
        offset += len(line)
    return sections


def section_for(sections, position):
    current = "正文"
    for section in sections:
        if section["offset"] <= position:
            current = section["title"]
        else:
            break
    return current


def claim_markers(text):
    markers = []
    masked = text
    for match in NUMERIC_MARKER_RE.finditer(text):
        ids = []
        for part in re.split(r"[,\s]+", match.group(1)):
            part = part.strip()
            if part.isdigit():
                ids.append(part)
            elif re.match(r"^\d+\s*[\-\u2013]\s*\d+$", part):
                low, high = re.split(r"[\-\u2013]", part)
                try:
                    ids.extend(str(value) for value in range(int(low), int(high) + 1))
                except ValueError:
                    continue
        if ids:
            markers.append({"style": "numeric", "ids": sorted(set(ids), key=lambda x: int(x)),
                            "raw": match.group(0), "span": [match.start(), match.end()]})
        masked = masked[:match.start()] + " " * (match.end() - match.start()) + masked[match.end():]
    for match in AUTHOR_YEAR_RE.finditer(masked):
        markers.append({"style": "author_year", "surname": match.group(1), "year": match.group(2),
                        "raw": match.group(0), "span": [match.start(), match.end()]})
    for match in EXPLICIT_REF_RE.finditer(masked):
        markers.append({"style": "explicit", "ids": [match.group(1)], "raw": match.group(0),
                        "span": [match.start(), match.end()]})
    markers.sort(key=lambda item: item["span"][0])
    return markers


def matches_any(value, markers):
    lowered = norm_space(value).lower()
    return any(norm_space(marker).lower() in lowered for marker in markers if marker)


# ---------------------------------------------------------------------------
# 引用解析与可靠性
# ---------------------------------------------------------------------------
def resolve_marker(marker, sources):
    matched = []
    if marker["style"] in ("numeric", "explicit"):
        for identifier in marker.get("ids", []):
            for source in sources:
                if source.get("source_id") is not None and str(source["source_id"]) == str(identifier):
                    matched.append(source)
    else:
        surname = marker.get("surname", "").lower()
        year = str(marker.get("year", ""))
        for source in sources:
            authors = source.get("authors", "").lower()
            source_year = source.get("year", "")
            if surname and surname in authors and source_year.startswith(year):
                matched.append(source)
    unique = []
    seen = set()
    for source in matched:
        key = source_key(source)
        if key not in seen:
            seen.add(key)
            unique.append(source)
    return unique


def source_key(source):
    return "%s|%s|%s" % (source.get("source_file"), source.get("source_index"),
                         source.get("source_id") or source.get("title")[:60])


def source_content(source):
    return norm_space(" ".join(part for part in (
        source.get("title", ""), source.get("abstract", ""), source.get("text", "")) if part))


def source_excerpt(source, terms, limit=240):
    for field in ("abstract", "text", "title"):
        content = source.get(field, "")
        if not content:
            continue
        lowered = content.lower()
        for term in terms:
            position = lowered.find(term.lower())
            if position >= 0:
                begin = max(0, position - 80)
                return field, content[begin:begin + limit], begin
        if field != "title":
            return field, content[:limit], 0
    return "title", source.get("title", "")[:limit], 0


def reliability_signals(source, policy):
    signals = []
    venue = source.get("venue", "")
    title = source.get("title", "")
    joined = "%s %s %s" % (venue, title, source.get("source_type", ""))
    if source.get("retracted"):
        signals.append({
            "kind": "retraction", "severity": "high", "facet": "source_reliability",
            "detail": "来源元数据标记为已撤稿" + ("（%s）" % source["retraction_note"] if source.get("retraction_note") else ""),
        })
    if any(marker in joined.lower() for marker in policy.get("preprint_markers", [])):
        signals.append({
            "kind": "preprint", "severity": "medium", "facet": "source_reliability",
            "detail": "来源呈预印本特征（未经过同行评审或版本未定稿）",
        })
    elif any(marker in joined.lower() for marker in policy.get("peer_reviewed_markers", [])):
        signals.append({
            "kind": "peer_reviewed_signal", "severity": "info", "facet": "source_reliability",
            "detail": "Venue 名称呈同行评审期刊/出版社特征（仅名称信号，未在线核验）",
        })
    if not source.get("doi") and not source.get("isbn") and not source.get("url"):
        signals.append({
            "kind": "missing_identifier", "severity": "low", "facet": "academic_norm",
            "detail": "来源缺少 DOI/ISBN/URL 等可回查标识符",
        })
    if not source.get("venue"):
        signals.append({
            "kind": "missing_venue", "severity": "low", "facet": "academic_norm",
            "detail": "来源缺少期刊/会议/出版方信息",
        })
    if not re.match(r"^(19|20)\d{2}$", source.get("year", "")):
        signals.append({
            "kind": "missing_year", "severity": "low", "facet": "academic_norm",
            "detail": "来源缺少可解析的出版年份",
        })
    return signals


# ---------------------------------------------------------------------------
# 判定
# ---------------------------------------------------------------------------
def assess_claim(claim_text, sources, policy):
    thresholds = policy.get("thresholds", {})
    supported_min = float(thresholds.get("supported_min", 0.55))
    partial_min = float(thresholds.get("partial_min", 0.28))
    min_terms = int(thresholds.get("min_claim_terms", 3))
    core = strip_markers(claim_text)
    claim_terms = tokenize(core, policy, limit=80)
    claim_numbers = numeric_tokens(core)

    if not sources:
        return {
            "verdict": "unverifiable", "overlap": 0.0, "shared_terms": [],
            "issue": "no_resolvable_source", "numeric_missing": [], "numeric_mismatch": [],
            "negation_conflict": False, "quote_conflict": False, "too_short": False,
        }
    source_text = norm_space(" ".join(source_content(source) for source in sources))
    if not source_text:
        return {
            "verdict": "unverifiable", "overlap": 0.0, "shared_terms": [],
            "issue": "source_has_no_text", "numeric_missing": [], "numeric_mismatch": [],
            "negation_conflict": False, "quote_conflict": False, "too_short": False,
        }
    source_terms = set(tokenize(source_text, policy, limit=600))
    shared = [term for term in claim_terms if term in source_terms]
    overlap = len(shared) / float(len(claim_terms) or 1)

    if overlap >= supported_min:
        verdict = "supported"
    elif overlap >= partial_min:
        verdict = "partially_supported"
    else:
        verdict = "unsupported"
    if len(claim_terms) < min_terms:
        verdict = "unverifiable"
    if verdict == "supported" and has_overreach(core):
        # 绝对化措辞 + 未达完整覆盖：视为结论超出引文范围。
        if overlap < 0.85:
            verdict = "unsupported"

    source_numbers = numeric_tokens(source_text)
    numeric_missing, numeric_mismatch = compare_numbers(claim_numbers, source_numbers)
    if numeric_missing and verdict == "supported":
        verdict = "partially_supported"
    if numeric_mismatch:
        verdict = "unsupported"

    negation_conflict = False
    if claim_terms and overlap >= partial_min * 0.5:
        if (neg_count(core) % 2) != (neg_count(source_text) % 2) and overlap >= partial_min:
            negation_conflict = True
            verdict = "unsupported"

    quote_conflict = False
    for quote in QUOTE_RE.findall(core):
        if norm_space(quote) and norm_space(quote) not in source_text:
            quote_conflict = True
            if verdict == "supported":
                verdict = "partially_supported"
            break

    issue = "ok"
    if verdict == "unverifiable":
        issue = "insufficient_lexical_evidence"
    elif negation_conflict:
        issue = "negation_conflict"
    elif numeric_mismatch:
        issue = "numeric_mismatch"
    elif numeric_missing:
        issue = "numeric_absent"
    elif quote_conflict:
        issue = "quote_mismatch"
    elif verdict == "unsupported":
        issue = "low_overlap_or_overreach"
    elif verdict == "partially_supported":
        issue = "partial_overlap"

    return {
        "verdict": verdict, "overlap": round(overlap, 3), "shared_terms": shared[:12],
        "issue": issue, "numeric_missing": numeric_missing, "numeric_mismatch": numeric_mismatch,
        "negation_conflict": negation_conflict, "quote_conflict": quote_conflict,
        "too_short": len(claim_terms) < min_terms,
    }


def suggested_revision(verdict, issue, extra, policy):
    if verdict == "supported":
        return "无需修改；保留该引用，并建议在正文补充分节或页码以提升可定位性。"
    if verdict == "unverifiable":
        return ("先补齐缺失的文献条目或可回查标识符（作者/年份/DOI），再重新核验；"
                "补齐前将该结论标注为待复核，不得补写来源内容。")
    if issue == "numeric_mismatch":
        return ("数值与所引来源不一致（正文 %s；来源 %s）：以来源原文为准更正数值，"
                "或更换能支持该数值的来源；更正前不得保留原数值。" % (extra.get("claim_values", ""), extra.get("source_values", "")))
    if issue == "numeric_absent":
        return ("所引来源未报告该数值（%s）：核对该来源全文或补充能报告该数值的引用；"
                "无法核实时改为定性表述并标注待复核。" % extra.get("missing", ""))
    if issue == "negation_conflict":
        return "正文与来源的否定/肯定方向相反：按来源原文改写结论，或更换支持该方向的来源。"
    if issue == "quote_mismatch":
        return "引号内文字未能在来源中逐字找回：改为来源原文的连续片段，或删除引号改为转述。"
    if verdict == "unsupported":
        return ("删除或替换该引用；若保留结论，须改写为来源实际支持的表述"
                "（当前词项覆盖 %.2f，未达支持阈值），并核对来源范围。" % extra.get("overlap", 0.0))
    return "补充能支持该结论的来源，或将表述收窄到所引来源实际覆盖的范围。"


def method_description_gap(claim_text, sources, policy):
    if not sources:
        return None
    core = strip_markers(claim_text).lower()
    cues = [cue for cue in policy.get("method_cues", []) if cue.lower() in core]
    if not cues:
        return None
    source_text = norm_space(" ".join(source_content(source) for source in sources)).lower()
    if any(term.lower() in source_text for term in policy.get("method_terms", [])):
        return None
    return {"cue": cues[0],
            "detail": "正文包含方法描述（%s），但所引来源文本未出现随机化/测量/样本/方法等词项。" % cues[0]}


# ---------------------------------------------------------------------------
# 证据定位
# ---------------------------------------------------------------------------
def claim_locator(file_name, section, line, paragraph, start, end):
    return {
        "file": file_name,
        "section": section,
        "sentence": "¶%d" % paragraph,
        "line": line,
        "char_start": start,
        "char_end": end,
        "format": "%s §%s ¶%d" % (file_name, section, paragraph),
    }


def source_locator(source, field, position):
    return {
        "file": source.get("source_file"),
        "record": source.get("locator"),
        "field": field,
        "char_start": position,
        "format": "%s → %s" % (source.get("source_file"), field),
    }


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def discover_case(input_dir, errors=None):
    candidates = []
    base = Path(input_dir)
    if base.is_dir():
        for path in sorted(base.glob("*.json")):
            if path.name in ("sources.json", "references.json", "refs.json", "policy.json"):
                continue
            candidates.append(path)
    for path in candidates:
        try:
            node = load_json(path)
        except (OSError, ValueError) as exc:
            if errors is not None:
                errors.append({"stage": "case", "type": "case_json_error", "file": path.name,
                               "message": "case.json 无法解析：%s" % str(exc)[:200]})
            continue
        if isinstance(node, dict) and any(key in node for key in (
                "case_id", "caseId", "output_contract", "manuscript", "paper", "references", "sources")):
            return node, path
    return {}, (candidates[0] if candidates else None)


def find_manuscript(case, input_dir, files, errors):
    explicit = get_any(case, ("manuscript_file", "manuscriptfile", "paper_file", "paperfile",
                              "source_file", "sourcefile", "document_file", "documentfile"))
    base = Path(input_dir)
    if isinstance(explicit, str) and explicit.strip():
        target = base / explicit.strip()
        if target.is_file():
            return target, read_text_file(target)
        errors.append({"stage": "manuscript", "type": "declared_file_missing",
                       "message": "case.json 指定的稿件文件不存在：%s" % explicit})
    # case.json 内嵌正文
    manuscript = get_any(case, ("manuscript", "paper", "article", "document", "content", "full_text"))
    if isinstance(manuscript, str) and len(norm_space(manuscript)) >= 40:
        return None, manuscript
    if isinstance(manuscript, (dict, list)):
        text = as_text(manuscript)
        if len(text) >= 40:
            return None, text
    # 按文件名优先
    ranked = []
    for path in files:
        stem = path.stem.lower()
        if stem in EXCLUDE_STEMS or path.suffix.lower() not in (".md", ".markdown", ".txt", ".text"):
            continue
        priority = 0
        for index, keyword in enumerate(("manuscript", "paper", "article", "draft", "main", "fulltext", "text")):
            if keyword in stem:
                priority = len(keyword) - index
                break
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        ranked.append((-priority, -size, path))
    ranked.sort()
    for _, _, path in ranked:
        text = read_text_file(path)
        if len(norm_space(text)) >= 20:
            return path, text
    return None, ""


def collect_sources(case, input_dir, files, manuscript_path, errors, policy):
    sources = []
    case_refs = get_any(case, ("references", "bibliography", "works_cited", "reference_list"))
    if isinstance(case_refs, dict):
        case_refs = list(case_refs.values())
    if isinstance(case_refs, list):
        for index, raw in enumerate(case_refs):
            record = normalize_case_reference(raw, index)
            if record:
                sources.append(record)
    case_sources = get_any(case, ("sources", "source_documents", "cited_sources"))
    if isinstance(case_sources, dict):
        case_sources = list(case_sources.values())
    if isinstance(case_sources, list):
        for index, raw in enumerate(case_sources):
            record = normalize_case_reference(raw, index)
            if record:
                record["source_file"] = "case.json"
                record["locator"] = "/case.json#sources[%d]" % index
                sources.append(record)

    max_sources = int(policy.get("caps", {}).get("max_sources", 500))
    for path in files:
        if len(sources) >= max_sources:
            errors.append({"stage": "sources", "type": "source_cap",
                           "message": "来源数量达到上限 %d，其余文件未读。" % max_sources})
            break
        if manuscript_path is not None and path.resolve() == Path(manuscript_path).resolve():
            continue
        if path.name.lower() == "case.json" or path.stem.lower() in ("task", "readme", "skill",
                                                                     "quickstart", "github_url", "policy"):
            continue
        if path.suffix.lower() not in (".json", ".jsonl", ".ndjson", ".xml", ".csv", ".txt",
                                       ".text", ".md", ".markdown", ".pdf"):
            continue
        records = file_records(path, policy, errors)
        if not records:
            continue
        relative = rel_path(path, input_dir)
        for index, raw in enumerate(records):
            locator = "/%s#record[%d]" % (relative, index)
            record = normalize_source(raw, relative, index, locator, errors)
            if record:
                sources.append(record)
    # 去重：同文件同 id 同标题只留一条
    unique, seen = [], set()
    for source in sources:
        key = "%s|%s|%s" % (source.get("source_file"), source.get("source_id"), norm_key(source.get("title", "")))
        if key in seen:
            continue
        seen.add(key)
        unique.append(source)
    return unique


def is_heading_sentence(text):
    stripped = text.strip()
    return bool(HEADING_RE.match(stripped) or
                (LITERAL_HEADING_RE.match(stripped) and len(stripped) <= 60))


def build_claim_rows(text, file_name, sections, policy):
    claims = []
    max_claims = int(policy.get("thresholds", {}).get("max_claims", 300))
    paragraph_counter = {}
    previous_section = None
    for sentence in split_sentences(text):
        section = section_for(sections, sentence["start"])
        if section != previous_section:
            paragraph_counter[section] = 0
            previous_section = section
        if is_heading_sentence(sentence["text"]):
            continue
        paragraph_counter[section] = paragraph_counter.get(section, 0) + 1
        markers = claim_markers(sentence["text"])
        if not markers:
            continue
        if len(claims) >= max_claims:
            break
        line = text.count("\n", 0, sentence["start"]) + 1
        claims.append({
            "claim_id": "C%02d" % (len(claims) + 1),
            "text": sentence["text"],
            "quote": sentence["text"][:400],
            "markers": markers,
            "locator": claim_locator(file_name, section, line, paragraph_counter[section],
                                     sentence["start"], sentence["end"]),
        })
    return claims


def build_payload(case, case_path, input_dir, task_text, policy, errors):
    files = iter_files(input_dir)
    manuscript_path, manuscript_text = find_manuscript(case, input_dir, files, errors)
    manuscript_name = rel_path(manuscript_path, input_dir) if manuscript_path else "case.json"
    sections = section_map(manuscript_text)
    sources = collect_sources(case, input_dir, files, manuscript_path, errors, policy)
    claims_raw = build_claim_rows(manuscript_text, manuscript_name, sections, policy)

    findings, citations, claim_rows, conflicts, unverifiable = [], [], [], [], []
    citation_counter = 0
    cited_source_keys = set()

    for claim in claims_raw:
        citation_ids = []
        resolved_sources = []
        unresolved = []
        for marker in claim["markers"]:
            citation_counter += 1
            citation_id = "CIT%02d" % citation_counter
            matched = resolve_marker(marker, sources)
            citation = {
                "citation_id": citation_id,
                "claim_id": claim["claim_id"],
                "marker": marker.get("raw"),
                "style": marker.get("style"),
                "target": marker.get("ids") or [marker.get("surname"), marker.get("year")],
                "resolved": bool(matched),
                "source_keys": [source_key(source) for source in matched],
                "sources": [{
                    "source_file": source.get("source_file"),
                    "source_locator": source.get("locator"),
                    "title": source.get("title"),
                    "authors": source.get("authors"),
                    "year": source.get("year"),
                    "venue": source.get("venue"),
                    "doi": source.get("doi"),
                    "retracted": source.get("retracted"),
                } for source in matched],
                "locator": claim["locator"],
                "quote": claim["quote"],
            }
            citations.append(citation)
            citation_ids.append(citation_id)
            resolved_sources.extend(source for source in matched if source not in resolved_sources)
            if not matched:
                unresolved.append({
                    "citation_id": citation_id,
                    "marker": marker.get("raw"),
                    "style": marker.get("style"),
                    "reason": "在提供的参考文献/来源文件中找不到与该标记对应的条目",
                })

        assessment = assess_claim(claim["text"], resolved_sources, policy)
        primary = resolved_sources[0] if resolved_sources else None
        if primary:
            field, excerpt, position = source_excerpt(primary, assessment["shared_terms"] or
                                                      tokenize(claim["text"], policy, limit=8))
            locator_src = source_locator(primary, field, position)
            source_quote = excerpt
        else:
            locator_src = None
            source_quote = ""

        severity = policy.get("verdict_severity", {}).get(assessment["verdict"], "medium")
        extra = {
            "overlap": assessment["overlap"],
            "missing": ", ".join(assessment["numeric_missing"][:5]),
            "claim_values": ", ".join(item["claim_value"] for item in assessment["numeric_mismatch"][:5]),
            "source_values": "; ".join(", ".join(item["source_values"]) for item in assessment["numeric_mismatch"][:5]),
        }
        revision = suggested_revision(assessment["verdict"], assessment["issue"], extra, policy)
        rationale = build_rationale(assessment, primary, unresolved)

        findings.append({
            "finding_id": "F%02d" % (len(findings) + 1),
            "claim_id": claim["claim_id"],
            "citation_id": citation_ids[0] if citation_ids else None,
            "citation_ids": citation_ids,
            "verdict": assessment["verdict"],
            "severity": severity,
            "facet": facet_for(assessment),
            "locator": claim["locator"],
            "quote": claim["quote"],
            "source_locator": locator_src,
            "source_quote": source_quote,
            "rationale": rationale,
            "suggested_revision": revision,
            "needs_recheck": assessment["verdict"] == "unverifiable",
            "metrics": {
                "lexical_overlap": assessment["overlap"],
                "shared_terms": assessment["shared_terms"],
                "numeric_missing": assessment["numeric_missing"],
                "numeric_mismatch": assessment["numeric_mismatch"],
                "negation_conflict": assessment["negation_conflict"],
                "quote_conflict": assessment["quote_conflict"],
            },
        })

        claim_rows.append({
            "claim_id": claim["claim_id"],
            "claim": claim["text"],
            "quote": claim["quote"],
            "locator": claim["locator"],
            "citation_ids": citation_ids,
            "resolved_source_keys": [source_key(source) for source in resolved_sources],
            "verdict": assessment["verdict"],
            "severity": severity,
            "overlap": assessment["overlap"],
            "shared_terms": assessment["shared_terms"],
            "unresolved": unresolved,
        })

        if assessment["numeric_mismatch"]:
            conflicts.append({
                "type": "numeric_mismatch",
                "claim_id": claim["claim_id"],
                "citation_ids": citation_ids,
                "locator": claim["locator"],
                "quote": claim["quote"],
                "source_locator": locator_src,
                "source_quote": source_quote,
                "detail": assessment["numeric_mismatch"],
            })
        if assessment["negation_conflict"]:
            conflicts.append({
                "type": "negation_conflict",
                "claim_id": claim["claim_id"],
                "citation_ids": citation_ids,
                "locator": claim["locator"],
                "quote": claim["quote"],
                "detail": "正文与来源的否定方向不一致",
            })
        if assessment["verdict"] == "unverifiable":
            unverifiable.append({
                "claim_id": claim["claim_id"],
                "citation_ids": citation_ids,
                "reason": assessment["issue"],
                "locator": claim["locator"],
                "quote": claim["quote"],
                "resolution": "补齐参考文献条目/可回查标识符，或提供可解析的来源全文后重新核验。",
            })

        # 方法描述检查（仅在正文出现方法措辞且来源无方法词项时提示）
        method_gap = method_description_gap(claim["text"], resolved_sources, policy)
        if method_gap:
            field, excerpt, position = source_excerpt(primary, tokenize(primary.get("title", ""), policy, limit=4))
            findings.append({
                "finding_id": "F%02d" % (len(findings) + 1),
                "claim_id": claim["claim_id"],
                "citation_id": citation_ids[0] if citation_ids else None,
                "citation_ids": citation_ids,
                "verdict": "partially_supported",
                "severity": "low",
                "facet": "method_description",
                "locator": claim["locator"],
                "quote": claim["quote"],
                "source_locator": source_locator(primary, field, position),
                "source_quote": excerpt,
                "rationale": method_gap["detail"],
                "suggested_revision": "核对来源全文的方法部分；若来源未描述该方法，删除该细节或改引方法学文献。",
                "needs_recheck": False,
                "metrics": {"method_cue": method_gap["cue"]},
            })

        # 来源可靠性发现（每个来源只记一次）
        for source in resolved_sources:
            key = source_key(source)
            if key in cited_source_keys:
                continue
            cited_source_keys.add(key)
            for signal in reliability_signals(source, policy):
                if signal["kind"] == "peer_reviewed_signal":
                    continue  # 正向信号不单独作为问题
                field, excerpt, position = source_excerpt(source, tokenize(source.get("title", ""), policy, limit=4))
                findings.append({
                    "finding_id": "F%02d" % (len(findings) + 1),
                    "claim_id": claim["claim_id"],
                    "citation_id": citation_ids[0] if citation_ids else None,
                    "citation_ids": citation_ids,
                    "verdict": "unsupported" if signal["kind"] == "retraction" else "partially_supported",
                    "severity": signal["severity"],
                    "facet": signal["facet"],
                    "locator": claim["locator"],
                    "quote": claim["quote"],
                    "source_locator": source_locator(source, field, position),
                    "source_quote": excerpt,
                    "rationale": "%s：%s（来源 %s）。" % (signal["kind"], signal["detail"], source.get("title", "")[:120]),
                    "suggested_revision": revision_for_signal(signal["kind"]),
                    "needs_recheck": signal["kind"] in ("missing_identifier", "missing_venue", "missing_year"),
                    "metrics": {"signal": signal["kind"]},
                })
                if signal["kind"] == "retraction":
                    conflicts.append({
                        "type": "retracted_source",
                        "claim_id": claim["claim_id"],
                        "citation_ids": citation_ids,
                        "locator": claim["locator"],
                        "quote": claim["quote"],
                        "source_locator": source_locator(source, "title", 0),
                        "source_quote": source.get("title", "")[:240],
                        "detail": source.get("retraction_note") or "来源元数据标记为已撤稿",
                    })

    counts = count_findings(findings)
    verdict_counts = {verdict: 0 for verdict in VERDICTS}
    for claim in claim_rows:
        verdict_counts[claim["verdict"]] = verdict_counts.get(claim["verdict"], 0) + 1
    severity_counts = {level: 0 for level in policy.get("severity_levels", ["high", "medium", "low", "info"])}
    for finding in findings:
        severity_counts[finding["severity"]] = severity_counts.get(finding["severity"], 0) + 1

    uncertainty = build_uncertainty(manuscript_text, sources, claims_raw, errors, policy)
    summary_text = build_summary_text(claim_rows, findings, verdict_counts, severity_counts, len(unverifiable))
    evidence = build_evidence(claim_rows, findings, sources)

    status = "success"
    status_reason = "已完成论断抽取、引用映射、支持度检查、来源可靠性检查与证据定位。"
    if not norm_space(manuscript_text):
        status = "insufficient_input"
        status_reason = "未在输入中找到可读稿件正文；未产出审查结论，未补造内容。"
    elif not sources:
        status = "partial"
        status_reason = "有稿件正文，但未找到可解析的参考文献/来源；引用均标为 unverifiable。"
    elif not claims_raw:
        status = "partial"
        status_reason = "有稿件与来源，但未抽取到带引用标记的论断；请核对引用标记格式。"
    elif unverifiable and len(unverifiable) == len(claim_rows):
        status = "partial"
        status_reason = "全部带引用论断均无法定位来源；仅给出待复核清单。"

    payload = {
        "case_id": case_identifier(case),
        "status": status,
        "status_reason": status_reason,
        "claims": claim_rows,
        "citations": citations,
        "findings": findings,
        "unverifiable": unverifiable,
        "conflicts": conflicts,
        "counts": {
            "claims": len(claim_rows),
            "citations": len(citations),
            "findings": len(findings),
            "sources": len(sources),
            "by_verdict": verdict_counts,
            "by_severity": severity_counts,
        },
        "summary_counts": {
            "claims": len(claim_rows),
            "citations": len(citations),
            "findings": len(findings),
            "sources": len(sources),
            "by_verdict": verdict_counts,
            "by_severity": severity_counts,
            "unverifiable": len(unverifiable),
            "conflicts": len(conflicts),
        },
        "summary": summary_text,
        "suggestions": {finding["finding_id"]: finding["suggested_revision"] for finding in findings},
        "evidence": evidence,
        "uncertainty": uncertainty,
        "limitations": limitations(),
        "inputs": [{
            "file": rel_path(path, input_dir),
            "bytes": safe_size(path),
            "kind": path.suffix.lower().lstrip(".") or "unknown",
        } for path in files],
        "run_log": build_run_log(input_dir, files, manuscript_name, sources, claims_raw, findings),
        "errors": errors[:50],
        "stats": {
            "claims": len(claim_rows),
            "citations": len(citations),
            "findings": len(findings),
            "sources": len(sources),
            "supported": verdict_counts.get("supported", 0),
            "partially_supported": verdict_counts.get("partially_supported", 0),
            "unsupported": verdict_counts.get("unsupported", 0),
            "unverifiable": verdict_counts.get("unverifiable", 0),
            "network": "disabled",
        },
        "metadata": {
            "skill": SKILL_NAME,
            "version": SKILL_VERSION,
            "deterministic": True,
            "policy_version": policy.get("version", SKILL_VERSION),
        },
    }
    return payload


def count_findings(findings):
    counts = {}
    for finding in findings:
        counts[finding["verdict"]] = counts.get(finding["verdict"], 0) + 1
    return counts


def facet_for(assessment):
    if assessment["issue"] in ("numeric_mismatch", "numeric_absent"):
        return "numeric_consistency"
    if assessment["issue"] in ("negation_conflict", "quote_mismatch"):
        return "citation_support"
    if assessment["verdict"] == "unverifiable":
        return "citation_integrity"
    return "citation_support"


def build_rationale(assessment, source, unresolved):
    if unresolved and not source:
        return "引用标记无法映射到任何参考文献/来源条目（%s），按待复核处理，未推断来源内容。" % unresolved[0]["reason"]
    if source is None:
        return "未定位到可读来源，无法核验。"
    parts = ["词项覆盖 %.2f（阈值：supported≥0.55，partially_supported≥0.28）" % assessment["overlap"]]
    if assessment["shared_terms"]:
        parts.append("共享词项：%s" % ", ".join(assessment["shared_terms"][:8]))
    if assessment["numeric_mismatch"]:
        parts.append("数值冲突：正文 %s vs 来源 %s" % (
            ", ".join(item["claim_value"] for item in assessment["numeric_mismatch"][:5]),
            "; ".join(", ".join(item["source_values"]) for item in assessment["numeric_mismatch"][:5])))
    if assessment["numeric_missing"]:
        parts.append("来源未报告数值：%s" % ", ".join(assessment["numeric_missing"][:5]))
    if assessment["negation_conflict"]:
        parts.append("否定方向与来源相反")
    if assessment["quote_conflict"]:
        parts.append("引号片段未能在来源中逐字找回")
    if assessment["too_short"]:
        parts.append("论断词项过少，词项覆盖不可靠")
    parts.append("来源：%s（%s）" % (source.get("title", "")[:100], source.get("locator")))
    return "；".join(parts) + "。"


def revision_for_signal(kind):
    mapping = {
        "retraction": "引用已撤稿来源：移除该引用或明确讨论撤稿原因，并改用未撤稿的替代证据。",
        "preprint": "预印本未经同行评审：核对正式版本；如已发表，改引正式版本并注明版本差异。",
        "missing_identifier": "补齐 DOI/ISBN/URL 等可回查标识符后再引用。",
        "missing_venue": "补齐期刊/会议/出版方信息。",
        "missing_year": "补齐可解析的出版年份。",
    }
    return mapping.get(kind, "核对该来源元数据后再引用。")


def build_uncertainty(manuscript_text, sources, claims, errors, policy):
    items = []
    if any(error.get("type") == "case_json_error" for error in errors):
        items.append({"type": "case_json_error",
                      "detail": "case.json 无法解析；case_id 与 output_contract 可能缺失，其余文件仍按实际内容读取。"})
    if not norm_space(manuscript_text):
        items.append({"type": "no_manuscript", "detail": "未找到可读稿件正文；未产出审查结论。"})
    if not sources:
        items.append({"type": "no_sources", "detail": "未找到参考文献/来源；所有引用只能标为 unverifiable。"})
    if sources and any(not source.get("abstract") and not source.get("text") for source in sources):
        count = sum(1 for source in sources if not source.get("abstract") and not source.get("text"))
        items.append({"type": "source_text_missing",
                      "detail": "%d 个来源仅有元数据、无摘要/正文；其支持度判断仅基于标题。" % count})
    if sources and any(source.get("extraction") == "pdf_low_confidence" for source in sources):
        items.append({"type": "pdf_low_confidence",
                      "detail": "存在 PDF 来源，文本按内容流启发式抽取，页码与排版不可靠，关键条目标记待复核。"})
    if any(error.get("type") == "pdf_no_text" for error in errors):
        items.append({"type": "pdf_no_text_layer",
                      "detail": "至少一个 PDF 未发现文本层（可能为扫描件）；相关来源无法核验，未补造内容。"})
    if not claims:
        items.append({"type": "no_cited_claims",
                      "detail": "未抽取到带引用标记的论断；请检查 [n]、(Author, Year) 或“见文献X”格式。"})
    items.append({"type": "no_external_lookup",
                  "detail": "未联网：DOI 存在性、撤稿数据库、出版社元数据与全文均未在线核验；仅使用输入中提供的证据。"})
    items.append({"type": "lexical_support_not_entailment",
                  "detail": "支持度是词项/数值层面的覆盖判断，不等同于语义蕴含；supported 仍建议人工抽读关键段落。"})
    return items


def limitations():
    return [
        "支持度是词项覆盖 + 数值/否定/引号检查，不是形式化语义蕴含。",
        "DOI 仅作形状与存在性字段检查，不在线解析。",
        "PDF 文本为低置信启发式抽取；扫描件无文本层时不做 OCR。",
        "未联网核验撤稿状态、版本差异与出版社元数据；仅依据输入中的标记。",
        "无法定位来源的论断一律标 unverifiable，不推断来源内容。",
    ]


def build_evidence(claim_rows, findings, sources):
    evidence = []
    for claim in claim_rows:
        evidence.append({
            "kind": "claim",
            "id": claim["claim_id"],
            "locator": claim["locator"],
            "quote": claim["quote"],
            "verdict": claim["verdict"],
        })
    for finding in findings:
        if finding.get("source_locator"):
            evidence.append({
                "kind": "source",
                "id": finding["finding_id"],
                "claim_id": finding["claim_id"],
                "locator": finding["source_locator"],
                "quote": finding.get("source_quote", ""),
            })
    seen_files = set()
    for source in sources:
        if source.get("source_file") in seen_files:
            continue
        seen_files.add(source.get("source_file"))
        evidence.append({
            "kind": "source_record",
            "id": source.get("source_id"),
            "locator": {"file": source.get("source_file"), "record": source.get("locator")},
            "quote": source.get("title", "")[:240],
        })
    return evidence


def build_summary_text(claim_rows, findings, verdict_counts, severity_counts, unverifiable_count):
    return ("共审查 %d 条带引用论断：supported %d / partially_supported %d / unsupported %d / "
            "unverifiable %d；产出 %d 条 findings（high %d / medium %d / low %d / info %d）；"
            "待复核 %d 条。所有判定附稿件定位与来源定位；无法定位来源的条目未补造。" % (
                len(claim_rows),
                verdict_counts.get("supported", 0), verdict_counts.get("partially_supported", 0),
                verdict_counts.get("unsupported", 0), verdict_counts.get("unverifiable", 0),
                len(findings),
                severity_counts.get("high", 0), severity_counts.get("medium", 0),
                severity_counts.get("low", 0), severity_counts.get("info", 0),
                unverifiable_count))


def build_run_log(input_dir, files, manuscript_name, sources, claims, findings):
    return [
        {"step": 1, "action": "read_input", "detail": "读取 %d 个文件" % len(files)},
        {"step": 2, "action": "parse_manuscript", "detail": "稿件：%s" % manuscript_name},
        {"step": 3, "action": "parse_sources", "detail": "解析来源记录 %d 条" % len(sources)},
        {"step": 4, "action": "extract_claims", "detail": "抽取带引用论断 %d 条" % len(claims)},
        {"step": 5, "action": "resolve_citations", "detail": "逐标记映射参考文献/来源"},
        {"step": 6, "action": "assess_support", "detail": "词项覆盖 + 数值/否定/引号一致性检查"},
        {"step": 7, "action": "audit_reliability", "detail": "撤稿、预印本、缺失标识符检查"},
        {"step": 8, "action": "locate_evidence", "detail": "生成 文件+章节+原文短引 定位串"},
        {"step": 9, "action": "write_output", "detail": "写出 result.json 与 run-review.md（确定性，无时间戳）"},
    ]


def safe_size(path):
    try:
        return path.stat().st_size
    except OSError:
        return 0


def case_identifier(case):
    if isinstance(case, dict):
        for key in ("case_id", "caseId", "id"):
            value = case.get(key)
            if isinstance(value, str) and value.strip():
                return value
            if isinstance(value, (int, float)):
                return str(value)
    return ""


# ---------------------------------------------------------------------------
# output_contract 映射
# ---------------------------------------------------------------------------
CONTRACT_ALIASES = {
    "issues": "findings", "checks": "findings", "findings": "findings", "problems": "findings",
    "review_findings": "findings", "review_report": "findings", "report": "findings",
    "items": "findings", "审查报告": "findings", "问题": "findings", "审查结果": "findings",
    "results": "claims", "claims": "claims", "claim_table": "claims", "claims_table": "claims",
    "论断": "claims", "结论": "claims",
    "citations": "citations", "references": "citations", "refs": "citations",
    "bibliography": "citations", "引用": "citations", "来源": "citations",
    "severity": "counts", "severity_counts": "counts", "counts": "counts",
    "statistics": "counts", "stats": "stats", "统计": "counts", "严重度": "counts",
    "suggestions": "suggestions", "recommendations": "suggestions", "revisions": "suggestions",
    "revision_advice": "suggestions", "修改建议": "suggestions",
    "notes": "uncertainty", "uncertainty": "uncertainty", "uncertainties": "uncertainty",
    "limitations": "uncertainty", "不确定性": "uncertainty", "局限": "uncertainty",
    "unverifiable": "unverifiable", "cannot_verify": "unverifiable", "recheck": "unverifiable",
    "pending_recheck": "unverifiable", "待复核": "unverifiable",
    "conflicts": "conflicts", "contradictions": "conflicts", "冲突": "conflicts",
    "evidence": "evidence", "locators": "evidence", "sources": "evidence",
    "source_locators": "evidence", "证据": "evidence", "定位": "evidence",
    "run_log": "run_log", "log": "run_log", "运行日志": "run_log",
    "case_id": "case_id", "status": "status", "status_detail": "status_detail",
    "summary": "__summary_text__", "摘要": "__summary_text__", "概述": "__summary_text__",
}


def contract_names(case):
    contract = None
    if isinstance(case, dict):
        contract = case.get("output_contract")
        if contract is None and isinstance(case.get("contract"), dict):
            contract = case["contract"].get("output_contract")
    names = []
    if isinstance(contract, dict):
        names = [str(key) for key in contract.keys()]
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                names.append(item)
            elif isinstance(item, dict):
                for key in ("name", "field", "key", "id"):
                    if isinstance(item.get(key), str):
                        names.append(item[key])
                        break
    return names, contract


def apply_contract(payload, case):
    names, contract = contract_names(case)
    mapping, notes, extras = {}, {}, {}
    for name in names:
        normalized = norm_key(name)
        if name in payload and payload[name] is not None:
            mapping[name] = name
            continue
        target = CONTRACT_ALIASES.get(normalized)
        if target == "__summary_text__":
            payload[name] = payload.get("summary", "")
            mapping[name] = "summary"
            notes[name] = "契约字段 %s 映射为摘要字符串。" % name
            continue
        if target and target in payload:
            payload[name] = payload[target]
            mapping[name] = target
            continue
        # 宽松匹配：契约键与已有键互相包含
        matched = None
        for key in payload.keys():
            key_norm = norm_key(key)
            if normalized and key_norm and (normalized in key_norm or key_norm in normalized):
                matched = key
                break
        if matched and not isinstance(payload.get(matched), (dict, list)):
            payload[name] = payload[matched]
            mapping[name] = matched
            continue
        payload[name] = None
        mapping[name] = None
        notes[name] = "未产出该字段；保留 null 以满足契约完整性，原因见 contract_extras。"
        extras[name] = {
            "value": None,
            "reason": "确定性流程没有对应产出器",
            "declared": contract.get(name) if isinstance(contract, dict) else None,
        }
    present = {name: (name in payload and payload[name] is not None) for name in names}
    payload["contract_notes"] = notes
    payload["contract_map"] = mapping
    payload["contract_extras"] = extras
    payload["output_contract_fields"] = {
        "provided": names,
        "present": present,
        "mapping": mapping,
        "contract_declared": contract if isinstance(contract, (dict, list)) else None,
    }
    return payload


def build_skeleton(case, status="partial", reason="骨架先行写入；内容处理中。"):
    empty_counts = {
        "claims": 0, "citations": 0, "findings": 0, "sources": 0,
        "by_verdict": {verdict: 0 for verdict in VERDICTS},
        "by_severity": {"high": 0, "medium": 0, "low": 0, "info": 0},
    }
    return {
        "case_id": case_identifier(case),
        "status": status,
        "status_reason": reason,
        "claims": [], "citations": [], "findings": [], "unverifiable": [], "conflicts": [],
        "counts": dict(empty_counts), "summary_counts": dict(empty_counts),
        "summary": "骨架已写入，尚未完成审查。",
        "suggestions": {}, "evidence": [], "uncertainty": [{
            "type": "in_progress", "detail": "result.json 在内容产出前先行写入；若运行在此结束，内容缺失。"}],
        "limitations": limitations(), "inputs": [], "run_log": [], "errors": [],
        "stats": {"claims": 0, "citations": 0, "findings": 0, "sources": 0, "network": "disabled"},
        "metadata": {"skill": SKILL_NAME, "version": SKILL_VERSION, "deterministic": True,
                     "policy_version": DEFAULT_POLICY["version"]},
    }


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------
def self_check(payload, case_id):
    problems = []
    if case_id and payload.get("case_id") != case_id:
        problems.append("case_id 与输入不一致：%s != %s" % (payload.get("case_id"), case_id))
    claims = payload.get("claims") or []
    findings = payload.get("findings") or []
    for claim in claims:
        if claim.get("verdict") not in VERDICTS:
            problems.append("%s 的 verdict 非法：%s" % (claim.get("claim_id"), claim.get("verdict")))
        locator = claim.get("locator") or {}
        if not locator.get("file") or not locator.get("section"):
            problems.append("%s 缺少文件/章节定位" % claim.get("claim_id"))
        if not claim.get("quote"):
            problems.append("%s 缺少原文短引" % claim.get("claim_id"))
    for finding in findings:
        for key in ("claim_id", "verdict", "severity", "locator", "quote", "rationale", "suggested_revision"):
            if not finding.get(key):
                problems.append("%s 缺少字段 %s" % (finding.get("finding_id"), key))
        if finding.get("verdict") not in VERDICTS:
            problems.append("%s 的 verdict 非法" % finding.get("finding_id"))
        if finding.get("severity") not in ("high", "medium", "low", "info"):
            problems.append("%s 的 severity 非法" % finding.get("finding_id"))
    counts = payload.get("counts") or {}
    by_verdict = counts.get("by_verdict") or {}
    by_severity = counts.get("by_severity") or {}
    if sum(by_verdict.values()) != len(claims):
        problems.append("by_verdict 计数与 claims 数量不一致")
    if sum(by_severity.values()) != len(findings) or counts.get("findings") != len(findings):
        problems.append("by_severity/findings 计数与 findings 数量不一致")
    contract = payload.get("output_contract_fields") or {}
    extras = payload.get("contract_extras") or {}
    missing = [name for name, ok in (contract.get("present") or {}).items()
               if not ok and name not in extras]
    if missing:
        problems.append("契约字段未产出：%s" % ", ".join(missing))
    return problems


# ---------------------------------------------------------------------------
# run-review.md
# ---------------------------------------------------------------------------
def md_cell(value):
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def build_review(payload, problems):
    counts = payload.get("counts") or {}
    by_verdict = counts.get("by_verdict") or {}
    by_severity = counts.get("by_severity") or {}
    lines = [
        "# 引用与事实核验复核记录（run-review）",
        "",
        "> 本文件由 `scripts/sk_entry.py` 确定性生成。未联网；所有判定仅依据输入中的稿件与来源。",
        "",
        "## 1. 运行标识",
        "",
        "| 字段 | 值 |",
        "|---|---|",
        "| case_id | %s |" % md_cell(payload.get("case_id") or "(未提供)"),
        "| status | %s |" % md_cell(payload.get("status")),
        "| status_reason | %s |" % md_cell(payload.get("status_reason")),
        "| 论断 / 引用 / findings | %s / %s / %s |" % (
            counts.get("claims", 0), counts.get("citations", 0), counts.get("findings", 0)),
        "| 判定计数 | %s |" % md_cell(json.dumps(by_verdict, ensure_ascii=False)),
        "| 严重度计数 | %s |" % md_cell(json.dumps(by_severity, ensure_ascii=False)),
        "",
        "## 2. 输入文件",
        "",
        "| 文件 | 字节 | 类型 |",
        "|---|---|---|",
    ]
    for item in payload.get("inputs", []):
        lines.append("| %s | %s | %s |" % (md_cell(item.get("file")), item.get("bytes"), md_cell(item.get("kind"))))
    lines.extend(["", "## 3. 论断与判定", "",
                  "| claim_id | 定位 | 判定 | 严重度 | 引用 | 原文短引 |",
                  "|---|---|---|---|---|---|"])
    for claim in payload.get("claims", []):
        locator = claim.get("locator") or {}
        lines.append("| %s | %s | %s | %s | %s | %s |" % (
            claim.get("claim_id"), md_cell(locator.get("format")), claim.get("verdict"),
            claim.get("severity"), md_cell(", ".join(claim.get("citation_ids", []))),
            md_cell(claim.get("quote", ""))[:160]))
    lines.extend(["", "## 4. 逐条 findings（位置 + 原文 + 依据 + 修改建议）", ""])
    for finding in payload.get("findings", []):
        locator = finding.get("locator") or {}
        source_locator = finding.get("source_locator") or {}
        lines.extend([
            "### %s · %s · %s（%s）" % (finding.get("finding_id"), finding.get("claim_id"),
                                       finding.get("verdict"), finding.get("severity")),
            "",
            "- 检查项：`%s`" % finding.get("facet"),
            "- 稿件定位：`%s`（行 %s）" % (locator.get("format"), locator.get("line")),
            "- 原文短引：> %s" % finding.get("quote"),
            "- 来源定位：`%s`" % (source_locator.get("format") or "未定位"),
            "- 来源短引：> %s" % (finding.get("source_quote") or "（无可用来源文本）"),
            "- 判定依据：%s" % finding.get("rationale"),
            "- 修改建议：%s" % finding.get("suggested_revision"),
            "- 待复核：%s" % ("是" if finding.get("needs_recheck") else "否"),
            "",
        ])
    lines.extend(["## 5. 待复核（unverifiable）", ""])
    if payload.get("unverifiable"):
        for item in payload["unverifiable"]:
            lines.append("- `%s` %s：%s；处置：%s" % (
                item.get("claim_id"), md_cell((item.get("locator") or {}).get("format")),
                item.get("reason"), item.get("resolution")))
    else:
        lines.append("- 无。")
    lines.extend(["", "## 6. 冲突（conflicts）", ""])
    if payload.get("conflicts"):
        for item in payload["conflicts"]:
            lines.append("- `%s` %s（%s）：%s" % (
                item.get("claim_id"), item.get("type"),
                md_cell((item.get("locator") or {}).get("format")),
                md_cell(json.dumps(item.get("detail"), ensure_ascii=False))))
    else:
        lines.append("- 无。")
    lines.extend(["", "## 7. 不确定性与边界", ""])
    for item in payload.get("uncertainty", []):
        lines.append("- `%s`：%s" % (item.get("type"), item.get("detail")))
    for item in payload.get("limitations", []):
        lines.append("- 限制：%s" % item)
    lines.extend(["", "## 8. 自检", ""])
    present = payload.get("output_contract_fields", {}).get("present") or {}
    extras = payload.get("contract_extras") or {}
    checks = [
        ("契约字段齐全且非空（未知键在 contract_extras 说明）",
         all(ok or name in extras for name, ok in present.items())),
        ("每条论断有 verdict + locator + quote", all(
            claim.get("verdict") in VERDICTS and (claim.get("locator") or {}).get("format") and claim.get("quote")
            for claim in payload.get("claims", []))),
        ("每条 finding 有定位、短引与修改建议", all(
            (finding.get("locator") or {}).get("format") and finding.get("quote")
            and finding.get("suggested_revision") for finding in payload.get("findings", []))),
        ("判定计数与论断数一致", sum(by_verdict.values()) == counts.get("claims", 0)),
        ("严重度计数与 findings 数一致", sum(by_severity.values()) == counts.get("findings", 0)),
        ("自检无阻断问题", not problems),
    ]
    for label, ok in checks:
        lines.append("- [%s] %s" % ("PASS" if ok else "FAIL", label))
    lines.extend(["", "## 9. 运行日志", ""])
    for step in payload.get("run_log", []):
        lines.append("- %s. `%s`：%s" % (step.get("step"), step.get("action"), step.get("detail")))
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def parse_args(argv):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--case", default=None)
    parser.add_argument("--work", default=DEFAULT_WORK)
    parser.add_argument("--out", "--output", dest="out", default=DEFAULT_OUT)
    try:
        args, unknown = parser.parse_known_args(argv)
    except SystemExit:
        args = argparse.Namespace(input=DEFAULT_INPUT, case=None, work=DEFAULT_WORK, out=DEFAULT_OUT)
        unknown = []
    return args, unknown


def resolve_paths(args):
    out_raw = str(args.out or DEFAULT_OUT)
    if out_raw.lower().endswith(".json"):
        out_file = Path(out_raw)
        out_dir = out_file.parent
        result_path = out_file
    else:
        out_dir = Path(out_raw)
        result_path = out_dir / "result.json"
    input_dir = Path(args.input or DEFAULT_INPUT)
    case_path = Path(args.case) if args.case else None
    if case_path is not None and not input_dir.is_dir():
        input_dir = case_path.parent
    work_dir = Path(args.work or DEFAULT_WORK)
    return input_dir, work_dir, out_dir, result_path


def run(input_dir, work_dir, out_dir, result_path):
    errors = []
    case, case_path = discover_case(input_dir, errors)
    if not isinstance(case, dict):
        case = {}
    case_id = case_identifier(case)
    policy = load_policy()

    # 先落骨架：即便后续超时/崩溃，磁盘上仍有契约齐全的 result.json。
    skeleton = apply_contract(build_skeleton(case), case)
    try:
        write_json(result_path, skeleton)
    except OSError as exc:
        sys.stderr.write("cannot write skeleton: %s\n" % exc)

    task_text = ""
    task_path = input_dir / "task.md"
    if task_path.is_file():
        try:
            task_text = read_text_file(task_path)
        except OSError:
            task_text = ""

    try:
        payload = build_payload(case, case_path, input_dir, task_text, policy, errors)
    except Exception as exc:  # noqa: BLE001 - 入口必须诚实降级
        payload = build_skeleton(case, status="partial",
                                 reason="处理过程异常，仅保留骨架；未补造内容。")
        payload["errors"] = [{"stage": "build", "type": type(exc).__name__, "message": str(exc)[:400]}]
        payload["uncertainty"].append({"type": "runtime_error", "detail": str(exc)[:300]})

    payload = apply_contract(payload, case)
    if "in_progress" in json.dumps(payload.get("uncertainty", []), ensure_ascii=False):
        payload["uncertainty"] = [item for item in payload.get("uncertainty", [])
                                  if item.get("type") != "in_progress"]

    problems = self_check(payload, case_id)
    if problems:
        payload["status"] = "partial"
        payload["status_reason"] = (payload.get("status_reason", "") + "；自检：" + "；".join(problems)).strip("；")
        payload.setdefault("uncertainty", []).append({
            "type": "self_check", "detail": "；".join(problems)})

    try:
        write_json(result_path, payload)
    except OSError as exc:
        sys.stderr.write(json.dumps({"status": "failed", "stage": "write_output",
                                     "message": str(exc)[:300]}, ensure_ascii=False) + "\n")
        return 0
    try:
        write_text(out_dir / "run-review.md", build_review(payload, problems))
    except OSError:
        pass
    try:
        write_text(work_dir / "run_log.json",
                   json.dumps({"case_id": payload.get("case_id"), "status": payload.get("status"),
                               "run_log": payload.get("run_log", [])}, ensure_ascii=False, indent=2))
    except OSError:
        pass
    sys.stdout.write(json.dumps({
        "status": payload.get("status"),
        "case_id": payload.get("case_id"),
        "out": str(result_path),
        "claims": (payload.get("counts") or {}).get("claims", 0),
        "findings": (payload.get("counts") or {}).get("findings", 0),
    }, ensure_ascii=False) + "\n")
    return 0


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    out_file_hint = DEFAULT_OUT + "/result.json"
    try:
        args, unknown = parse_args(argv)
        input_dir, work_dir, out_dir, result_path = resolve_paths(args)
        out_file_hint = str(result_path)
        for path in (work_dir, out_dir):
            try:
                path.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
        if unknown:
            sys.stderr.write("ignored arguments: %s\n" % " ".join(unknown))
        return run(input_dir, work_dir, out_dir, result_path)
    except BaseException as exc:  # noqa: BLE001 - 最后一层保护
        try:
            sys.stderr.write("fatal: %s: %s\n" % (type(exc).__name__, exc))
        except Exception:
            pass
        try:
            target = Path(out_file_hint)
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.is_file():
                write_json(target, build_skeleton({}, status="partial",
                                                  reason="入口异常，仅写出骨架。"))
        except Exception:
            pass
        return 0


if __name__ == "__main__":
    sys.exit(main())
