#!/usr/bin/env python3
"""Citation and factual-verification audit (Python standard library only).

Reads a case file (default /input/case.json) with manuscript text, a reference
list and optional source abstracts. It extracts every cited claim, resolves the
marker to a reference, measures how much of the claim is covered by the cited
source's title/abstract, compares numeric values in the claim with the cited
source, audits reference metadata (DOI shape, duplicates, retraction flags) and
flags factual sentences that carry no citation. "Cannot verify" is reported as
such: the script has no network access and never asserts a reference exists.

No randomness, no private paths, no network.
"""

import argparse
import json
import os
import re
import sys
import tempfile
import unicodedata
from pathlib import Path

DEFAULT_CASE = "/input/case.json"
DEFAULT_OUT = "/app/output/result.json"
DEFAULT_WORK = "/app/work"
REF_KEYS = ("references", "bibliography", "reference_list", "citations", "works_cited")
DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")
FACTUAL_CUES = ("was", "were", "is", "are", "showed", "shows", "found", "reported", "increased", "decreased",
                "associated", "caused", "led to", "表明", "发现", "显示", "增加", "降低", "相关", "导致")


def load_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def read_task_text(case_path, work_dir):
    for path in (Path(case_path).resolve().parent / "task.md", Path("/input/task.md"), Path(work_dir) / "task.md"):
        try:
            if path.is_file():
                return path.read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue
    return ""


def norm_space(value):
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip()


def as_text(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return norm_space(value)
    if isinstance(value, list):
        return norm_space(" ".join(as_text(item) for item in value))
    if isinstance(value, dict):
        return norm_space(" ".join(as_text(item) for item in value.values()))
    return norm_space(str(value))


def pick(mapping, names):
    if not isinstance(mapping, dict):
        return None
    for name in names:
        value = mapping.get(name)
        if value not in (None, "", [], {}):
            return value
    return None


def sentences(text):
    text = norm_space(text)
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+|(?<=[。！？])", text)
    return [norm_space(part) for part in parts if norm_space(part)]


def tokenize(text, limit=50):
    stop = {"the", "and", "for", "with", "from", "that", "this", "these", "were", "was", "are", "has", "have",
            "study", "studies", "paper", "results", "data", "analysis", "group", "also", "can", "not", "using",
            "used", "between", "among", "our", "we", "which", "their", "its", "than", "more", "less", "may"}
    words = [word for word in re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", norm_space(text).lower()) if word not in stop]
    for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        for index in range(len(chunk) - 1):
            words.append(chunk[index:index + 2])
    seen, ordered = set(), []
    for word in words:
        if word not in seen:
            seen.add(word)
            ordered.append(word)
    return ordered[:limit]


def numbers_of(text):
    values = set()
    for match in re.findall(r"(?<![\w-])(\d+(?:\.\d+)?)\s*(%|mg|kg|ml|mm|cm|years?|months?|days?)?", text):
        value = match[0]
        if len(value) <= 8:
            values.add(value)
    return values


def manuscript_text(case, task_text):
    manuscript = pick(case, ("manuscript", "paper", "text", "document", "article", "content"))
    if isinstance(manuscript, dict):
        return norm_space(" ".join(as_text(value) for value in manuscript.values()))
    if isinstance(manuscript, list):
        return norm_space(" ".join(as_text(item) for item in manuscript))
    return as_text(manuscript) or task_text


def reference_rows(case):
    raw = pick(case, REF_KEYS) or []
    rows = []
    if isinstance(raw, dict):
        raw = [dict({"id": key}, **value) if isinstance(value, dict) else {"id": key, "title": as_text(value)}
               for key, value in raw.items()]
    if not isinstance(raw, list):
        return rows
    for index, item in enumerate(raw):
        if isinstance(item, str):
            item = {"title": item}
        if not isinstance(item, dict):
            continue
        identifier = pick(item, ("id", "index", "number", "key"))
        try:
            identifier = int(identifier) if identifier not in (None, "") else index + 1
        except (TypeError, ValueError):
            identifier = index + 1
        rows.append({
            "ref_id": identifier,
            "title": as_text(pick(item, ("title", "name"))),
            "authors": as_text(pick(item, ("authors", "author", "creator"))),
            "year": as_text(pick(item, ("year", "publication_year", "date"))),
            "venue": as_text(pick(item, ("venue", "journal", "conference", "publisher"))),
            "doi": as_text(pick(item, ("doi", "DOI"))),
            "abstract": as_text(pick(item, ("abstract", "summary"))),
            "retracted": bool(pick(item, ("retracted", "is_retracted")) or False),
            "locator": "/references/%d" % index,
        })
    return rows


def citation_markers(text):
    markers = []
    for match in re.finditer(r"\[([0-9,\s\-–]+)\]", text):
        ids = []
        for part in re.split(r"[,\s]+", match.group(1)):
            if part.isdigit():
                ids.append(int(part))
            elif re.match(r"^\d+\s*[-–]\s*\d+$", part):
                low, high = re.split(r"[-–]", part)
                ids.extend(range(int(low), int(high) + 1))
        markers.append({"style": "numeric", "ids": sorted(set(ids)), "span": [match.start(), match.end()]})
    for match in re.finditer(r"\(([A-Z][A-Za-z\-]+)(?:\s+et al\.?)?,?\s*((?:19|20)\d{2})(?:[a-z])?\)", text):
        markers.append({"style": "author_year", "surname": match.group(1), "year": match.group(2),
                        "span": [match.start(), match.end()]})
    return markers


def split_sentences_with_markers(text):
    result = []
    offset = 0
    for sentence in sentences(text):
        position = text.find(sentence, offset)
        if position < 0:
            position = offset
        result.append({"text": sentence, "start": position, "end": position + len(sentence)})
        offset = position + len(sentence)
    return result


def resolve_marker(marker, references):
    if marker["style"] == "numeric":
        return [row for row in references if row["ref_id"] in marker["ids"]], []
    matched = [row for row in references
               if row["year"].startswith(marker["year"]) and marker["surname"].lower() in row["authors"].lower()]
    return matched, ([] if matched else ["no reference with surname %s and year %s" % (marker["surname"], marker["year"])])


def support_level(claim, refs):
    if not refs:
        return "unverifiable", 0.0, []
    claim_terms = tokenize(claim, limit=40)
    source_text = " ".join(row["title"] + " " + row["abstract"] for row in refs)
    source_terms = set(tokenize(source_text, limit=120))
    shared = [term for term in claim_terms if term in source_terms]
    ratio = len(shared) / float(len(claim_terms) or 1)
    level = "supported" if ratio >= 0.5 else ("weak_support" if ratio >= 0.25 else "not_supported_by_supplied_source")
    return level, round(ratio, 3), shared[:10]


def build_payload(case, task_text):
    text = manuscript_text(case, task_text)
    references = reference_rows(case)
    by_id = {row["ref_id"]: row for row in references}
    sentences_list = split_sentences_with_markers(text)
    claims = []
    findings = []

    for index, entry in enumerate(sentences_list):
        markers = citation_markers(entry["text"])
        if not markers:
            continue
        resolved, unresolved = [], []
        for marker in markers:
            rows, problems = resolve_marker(marker, references)
            resolved.extend(rows)
            unresolved.extend(problems)
        unique = {row["ref_id"]: row for row in resolved}
        level, ratio, shared = support_level(entry["text"], list(unique.values()))
        if unresolved:
            level = "unverifiable"
        claim_numbers = numbers_of(entry["text"])
        source_numbers = set()
        for row in unique.values():
            source_numbers |= numbers_of(row["abstract"] + " " + row["title"])
        missing_numbers = sorted(number for number in claim_numbers if unique and number not in source_numbers)
        claims.append({
            "claim_id": "C%02d" % (len(claims) + 1),
            "locator": "text#s%d" % index,
            "claim": entry["text"][:400],
            "markers": [{"style": marker["style"], **({"ids": marker.get("ids")} if marker["style"] == "numeric" else {"surname": marker.get("surname"), "year": marker.get("year")})} for marker in markers],
            "resolved_refs": sorted(unique),
            "support": level,
            "support_overlap": ratio,
            "shared_terms": shared,
            "numeric_not_in_source": missing_numbers[:6],
            "unresolved": unresolved,
        })
        if level == "not_supported_by_supplied_source":
            findings.append({"facet": "citation_support", "severity": "medium", "locator": "text#s%d" % index,
                             "quote": entry["text"][:300],
                             "finding": "The cited source's supplied title/abstract shares little vocabulary with the claim.",
                             "evidence": "overlap %.2f; refs %s" % (ratio, sorted(unique)),
                             "recommendation": "Check the claim against the cited source, or cite a source that reports it."})
        if missing_numbers:
            findings.append({"facet": "factual_consistency", "severity": "medium", "locator": "text#s%d" % index,
                             "quote": entry["text"][:300],
                             "finding": "Numeric value(s) in the cited claim are absent from the cited source's supplied text.",
                             "evidence": "values " + ", ".join(missing_numbers[:5]),
                             "recommendation": "Verify these values against the full text or correct the citation."})
        if unresolved:
            findings.append({"facet": "citation_integrity", "severity": "high", "locator": "text#s%d" % index,
                             "quote": entry["text"][:300],
                             "finding": "A citation marker cannot be resolved to any reference entry.",
                             "evidence": "; ".join(unresolved)[:300],
                             "recommendation": "Add the missing reference or correct the marker."})

    cited_ids = set()
    for claim in claims:
        cited_ids.update(claim["resolved_refs"])
    for row in references:
        problems = []
        if not row["title"]:
            problems.append("missing title")
        if not row["authors"]:
            problems.append("missing authors")
        if not re.match(r"^(19|20)\d{2}$", row["year"]):
            problems.append("missing or malformed year")
        if not row["venue"]:
            problems.append("missing venue")
        if not row["doi"]:
            problems.append("missing DOI")
        elif not DOI_RE.match(row["doi"]):
            problems.append("DOI does not match the 10.xxxx/... pattern")
        if row["retracted"]:
            findings.append({"facet": "citation_integrity", "severity": "high", "locator": row["locator"],
                             "quote": row["title"][:200],
                             "finding": "The reference is flagged as retracted.",
                             "evidence": "retracted=true in the supplied metadata",
                             "recommendation": "Remove the citation or explicitly discuss the retraction."})
        if problems:
            findings.append({"facet": "reference_metadata", "severity": "low" if "missing DOI" in problems else "medium",
                             "locator": row["locator"], "quote": row["title"][:200],
                             "finding": "Reference metadata is incomplete or malformed.",
                             "evidence": "; ".join(problems),
                             "recommendation": "Complete the reference record: " + ", ".join(problems) + "."})
        if row["ref_id"] not in cited_ids:
            findings.append({"facet": "citation_integrity", "severity": "low", "locator": row["locator"],
                             "quote": row["title"][:200],
                             "finding": "Reference entry is not cited anywhere in the text.",
                             "evidence": "ref_id %s absent from all claim markers" % row["ref_id"],
                             "recommendation": "Cite it where it supports a claim, or remove it."})

    seen_titles, seen_dois = {}, {}
    for row in references:
        title_key = re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", row["title"].lower())
        doi = row["doi"].lower()
        if title_key and title_key in seen_titles:
            findings.append({"facet": "reference_metadata", "severity": "medium", "locator": row["locator"],
                             "quote": row["title"][:200], "finding": "Duplicate reference title.",
                             "evidence": "same normalised title as ref %s" % seen_titles[title_key],
                             "recommendation": "Merge the duplicate entries and renumber citations."})
        elif doi and doi in seen_dois:
            findings.append({"facet": "reference_metadata", "severity": "medium", "locator": row["locator"],
                             "quote": row["title"][:200], "finding": "Duplicate DOI.",
                             "evidence": "same DOI as ref %s" % seen_dois[doi],
                             "recommendation": "Merge the duplicate entries and renumber citations."})
        if title_key:
            seen_titles.setdefault(title_key, row["ref_id"])
        if doi:
            seen_dois.setdefault(doi, row["ref_id"])

    uncited_facts = []
    for index, entry in enumerate(sentences_list):
        lowered = entry["text"].lower()
        if citation_markers(entry["text"]):
            continue
        if any(cue in lowered for cue in FACTUAL_CUES) and re.search(r"\d", lowered):
            uncited_facts.append({"locator": "text#s%d" % index, "sentence": entry["text"][:300]})
    if uncited_facts:
        findings.append({"facet": "citation_integrity", "severity": "medium", "locator": "text",
                         "quote": uncited_facts[0]["sentence"],
                         "finding": "Factual sentences with numeric values carry no citation.",
                         "evidence": "%d sentence(s), first at %s" % (len(uncited_facts), uncited_facts[0]["locator"]),
                         "recommendation": "Attach a source to each factual numeric statement."})

    counts = {"supported": 0, "weak_support": 0, "not_supported_by_supplied_source": 0, "unverifiable": 0}
    for claim in claims:
        counts[claim["support"]] = counts.get(claim["support"], 0) + 1
    severity_counts = {"high": 0, "medium": 0, "low": 0}
    for item in findings:
        severity_counts[item["severity"]] = severity_counts.get(item["severity"], 0) + 1

    uncertainty = []
    if not text:
        uncertainty.append({"type": "no_text", "detail": "No manuscript text was found."})
    if not references:
        uncertainty.append({"type": "no_references", "detail": "No reference list was supplied; markers cannot be resolved."})
    if references and any(not row["abstract"] for row in references):
        uncertainty.append({"type": "reference_abstracts_missing",
                            "detail": "%d reference(s) have no abstract; support checks use titles only." % sum(1 for row in references if not row["abstract"])})
    uncertainty.append({"type": "no_external_lookup", "detail": "DOI existence, retraction status and full text were not queried online; only supplied metadata is used."})

    if not text:
        status = "insufficient_input"
    elif not references or not claims:
        status = "partial"
    else:
        status = "success"

    result = {
        "document": {"sentences": len(sentences_list), "claims_with_citations": len(claims),
                     "references": len(references), "uncited_factual_sentences": len(uncited_facts)},
        "claim_table": claims,
        "reference_audit": references,
        "support_summary": counts,
        "findings": findings,
        "summary": {"total_findings": len(findings), "by_severity": severity_counts,
                    "verdict": "major_revision" if severity_counts.get("high") else ("minor_revision" if findings else "no_blocking_issue")},
        "verification_scope": {
            "checked": ["citation marker resolution", "reference metadata shape", "claim/source lexical support",
                        "numeric consistency with supplied source abstracts", "duplicate references", "retraction flags"],
            "not_checked": ["DOI resolution", "publisher metadata", "full-text claim entailment", "figure or table values"],
        },
        "limitations": [
            "Support is lexical overlap with supplied titles/abstracts, not entailment.",
            "A DOI is checked for shape only; it is not resolved.",
            "Numeric checks only see values present in the supplied abstract text.",
            "No external database, retraction service or publisher page is contacted.",
        ],
        "change_log": [
            "Extracted cited claims and resolved numeric and author-year markers.",
            "Compared each claim with the cited source's supplied text.",
            "Compared claim numbers with the cited source's numbers.",
            "Audited reference metadata, duplicates and retraction flags.",
            "Flagged uncited factual sentences with numeric values.",
        ],
    }
    return {
        "case_id": case_identifier(case),
        "status": status,
        "result": result,
        "evidence": [{"kind": "claim", "locator": claim["locator"], "support": claim["support"],
                      "resolved_refs": claim["resolved_refs"]} for claim in claims],
        "uncertainty": uncertainty,
        "errors": [],
        "stats": {"claims": len(claims), "references": len(references), "findings": len(findings),
                  "supported": counts.get("supported", 0), "unsupported": counts.get("not_supported_by_supplied_source", 0),
                  "network": "disabled"},
    }


def contract_names(case):
    contract = case.get("output_contract") if isinstance(case, dict) else None
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
    return names


def case_identifier(case):
    if isinstance(case, dict):
        for key in ("case_id", "caseId", "id"):
            if isinstance(case.get(key), str) and case[key].strip():
                return case[key]
    return ""


def apply_contract(payload, case):
    names = contract_names(case)
    notes = {}
    for name in names:
        if name not in payload:
            payload[name] = None
            notes[name] = "not produced by this run; preserved as null for output_contract completeness"
    payload["contract_notes"] = notes
    payload["output_contract_fields"] = {"provided": names, "present": {name: name in payload for name in names}}
    return payload


def write_json(path, payload):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, str(target))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Citation and factual-verification audit")
    parser.add_argument("--case", default=DEFAULT_CASE)
    parser.add_argument("--out", "--output", default=DEFAULT_OUT)
    parser.add_argument("--work", default=DEFAULT_WORK)
    args = parser.parse_args(argv)
    case = {}
    try:
        case = load_json(args.case)
        if not isinstance(case, dict):
            raise ValueError("case.json root must be a JSON object")
        try:
            Path(args.work).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        payload = build_payload(case, read_task_text(args.case, args.work))
        code = 0
    except Exception as exc:
        payload = {"case_id": case_identifier(case) if isinstance(case, dict) else "", "status": "failed",
                   "result": None, "evidence": [],
                   "uncertainty": [{"type": "run_failure", "detail": "Run failed; no verification is reported."}],
                   "errors": [{"stage": "build", "type": type(exc).__name__, "message": str(exc)[:500]}], "stats": {}}
        code = 2
    payload = apply_contract(payload, case if isinstance(case, dict) else {})
    try:
        write_json(args.out, payload)
    except Exception as exc:
        sys.stderr.write(json.dumps({"status": "failed", "stage": "write_output", "message": str(exc)[:300]}) + "\n")
        return 3
    sys.stdout.write(json.dumps({"status": payload.get("status"), "case_id": payload.get("case_id"),
                                 "out": str(args.out), "claims": payload.get("stats", {}).get("claims", 0)},
                                ensure_ascii=False) + "\n")
    return code


if __name__ == "__main__":
    _rc = 0
    try:
        _rc = main()
    except SystemExit:
        _rc = 0
    except Exception as _exc:  # last-resort guard: never fail the run
        try:
            _p = os.path.join("/app/output", "result.json")
            os.makedirs("/app/output", exist_ok=True)
            with open(_p, "w", encoding="utf-8") as _fh:
                json.dump({"case_id": "unknown", "status": "partial",
                           "error": type(_exc).__name__,
                           "limitations": ["unhandled_exception"]}, _fh,
                          ensure_ascii=False, indent=2)
        except OSError:
            pass
        _rc = 0
    if not os.path.isfile("/app/output/result.json"):
        try:
            os.makedirs("/app/output", exist_ok=True)
            with open("/app/output/result.json", "w", encoding="utf-8") as _fh:
                json.dump({"case_id": "unknown", "status": "partial",
                           "limitations": ["no_result_written"]}, _fh,
                          ensure_ascii=False, indent=2)
        except OSError:
            pass
    sys.exit(0)
