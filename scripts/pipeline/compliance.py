#!/usr/bin/env python3
"""Decide whether we are permitted to collect from an agency's site.

Terms of service are the gate. robots.txt is recorded but does not block.

That ordering was wrong in the first version and is worth stating plainly:
robots.txt is a crawler convention aimed at search indexing, not a contract.
The enforceable question is whether binding terms exist, what they say, and
whether we ever assented to them — after hiQ v. LinkedIn and Meta v. Bright
Data, browsewrap terms are weakly enforceable against a visitor who was shown
no notice. A blanket `Disallow: /` on a vendor domain is a preference worth
knowing about, but it is not the thing that decides the legal question.

So:

  terms       The gate. Prohibitions are classified by what they say AND by
              whether they plainly reach the host we would fetch from. A
              customer contract between the vendor and the district binds
              neither of us; a browsewrap that names "casual browsers" is a
              different matter.

  robots.txt  Collected and stored. Ignoring a stated preference should be a
              deliberate, auditable decision rather than an accident — but it
              no longer blocks on its own.

Every verdict is stored with the sentence it rests on. A decision to skip a
district, or to proceed over an operator's stated preference, needs an answer
better than a boolean a year later.

    python3 scripts/pipeline/compliance.py --agency-id 12
    python3 scripts/pipeline/compliance.py --leaid 0622710 --url https://…
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.robotparser
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db import connect, dict_cursor, t  # noqa: E402

# Identify ourselves honestly. A crawler that hides what it is cannot expect
# operators to make an informed choice about whether to allow it, and an
# unreachable contact address makes robots.txt the only channel they have.
USER_AGENT = (
    "ourschoolboardbot/0.1 (+https://ourschoolboard.org/about; "
    "public meeting records; contact: hello@ourschoolboard.org)"
)
TIMEOUT = 25

# Phrases that, in a terms page, plainly bar what we do. Kept narrow on purpose:
# broad matching would flag ordinary copyright boilerplate on nearly every site.
PROHIBITION_PATTERNS = [
    r"may not (?:use|employ|deploy)[^.]{0,80}(?:robot|spider|scraper|crawler|automated)",
    r"(?:prohibit|forbid|not permit)[^.]{0,80}(?:scrap|crawl|harvest|automated (?:access|collection))",
    r"(?:no|not)[^.]{0,40}(?:artificial intelligence|ai)[^.]{0,60}(?:train|scrap|harvest|ingest)",
    r"automated[^.]{0,40}(?:access|collection|extraction)[^.]{0,40}(?:prohibited|forbidden|not permitted)",
]

TERMS_PATHS = ["/terms", "/terms-of-use", "/terms-of-service", "/terms-and-conditions",
               "/legal", "/site-map", "/privacy", "/disclaimer", "/use-policy"]

# These determinations are reviewed, host-scoped findings from
# docs/compliance.md. A WAF or outage must not erase a known prohibition, but a
# product label is never enough to transfer one origin's terms to another. In
# particular, a district-owned custom hostname does not inherit vendor terms
# merely because the stored platform field says "Simbli" or "BoardDocs".
KNOWN_HOST_PROHIBITIONS = {
    "simbli": {
        "host_suffixes": ("simbli.eboardsolutions.com",),
        "terms_url": "https://simbli.eboardsolutions.com/TERMSOFSERVICE.PDF",
        "evidence": (
            'Reviewed terms bind "casual browsers of the Site" and prohibit '
            '"robot, spider" cataloguing.'
        ),
    },
}


def known_host_terms(base_url: str) -> dict | None:
    """Return a reviewed prohibition only when this exact host family matches."""
    host = (urllib.parse.urlparse(base_url).hostname or "").lower()
    for platform, finding in KNOWN_HOST_PROHIBITIONS.items():
        host_match = any(
            host == suffix or host.endswith(f".{suffix}")
            for suffix in finding["host_suffixes"]
        )
        if host_match:
            return {
                "source": "terms",
                "url": finding["terms_url"],
                "verdict": "prohibited",
                "evidence": finding["evidence"],
                "raw": {
                    "authoritative": "docs/compliance.md",
                    "platform": platform,
                    "matched_host": host,
                    "live_discovery_skipped": True,
                },
            }
    return None


def check_robots(base_url: str) -> dict:
    """Ask robots.txt whether our agent may fetch the site.

    urllib's parser is used rather than a hand-rolled one so that precedence
    rules — most-specific match, wildcard agents, Allow overriding Disallow —
    behave the way operators expect.
    """
    parsed = urllib.parse.urlparse(base_url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        response = requests.get(robots_url, timeout=TIMEOUT,
                                headers={"User-Agent": USER_AGENT})
    except Exception as exc:
        return {"source": "robots", "url": robots_url, "verdict": "unavailable",
                "evidence": f"fetch failed: {exc}", "raw": {}}

    if response.status_code == 404:
        # No robots.txt means no restriction expressed. That is permission by
        # omission, not an error.
        return {"source": "robots", "url": robots_url, "verdict": "allowed",
                "evidence": "no robots.txt published (404)", "raw": {"status": 404}}
    if response.status_code >= 400:
        return {"source": "robots", "url": robots_url, "verdict": "unavailable",
                "evidence": f"HTTP {response.status_code}", "raw": {"status": response.status_code}}

    parser = urllib.robotparser.RobotFileParser()
    parser.parse(response.text.splitlines())

    allowed = parser.can_fetch(USER_AGENT, base_url)
    crawl_delay = parser.crawl_delay(USER_AGENT)

    # Record the directive block that applies to us, so a block is auditable.
    evidence = None
    if not allowed:
        for line in response.text.splitlines():
            if re.match(r"^\s*disallow", line, re.I):
                evidence = line.strip()
                break

    return {
        "source": "robots",
        "url": robots_url,
        "verdict": "allowed" if allowed else "prohibited",
        "evidence": evidence or ("robots.txt permits this path" if allowed else "disallowed"),
        "raw": {"status": response.status_code, "crawl_delay": crawl_delay,
                "excerpt": response.text[:2000]},
    }


def find_terms_url(base_url: str) -> str | None:
    """Look for a terms page, first in the homepage's own links."""
    try:
        response = requests.get(base_url, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
        if response.ok:
            for match in re.finditer(r'href="([^"]+)"[^>]*>([^<]{0,80})</a>', response.text, re.I):
                href, label = match.group(1), match.group(2)
                if re.search(r"terms|legal|acceptable use|conditions of use", label, re.I):
                    return urllib.parse.urljoin(base_url, href)
    except Exception:
        pass

    for path in TERMS_PATHS:
        candidate = urllib.parse.urljoin(base_url, path)
        try:
            head = requests.head(candidate, timeout=10, allow_redirects=True,
                                 headers={"User-Agent": USER_AGENT})
            if head.ok:
                return candidate
        except Exception:
            continue
    return None


def check_terms(base_url: str) -> dict:
    known = known_host_terms(base_url)
    if known:
        return known
    terms_url = find_terms_url(base_url)
    if not terms_url:
        return {"source": "terms", "url": None, "verdict": "unavailable",
                "evidence": "no terms page found", "raw": {}}
    try:
        response = requests.get(terms_url, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
    except Exception as exc:
        return {"source": "terms", "url": terms_url, "verdict": "unavailable",
                "evidence": f"fetch failed: {exc}", "raw": {}}
    if not response.ok:
        return {"source": "terms", "url": terms_url, "verdict": "unavailable",
                "evidence": f"HTTP {response.status_code}", "raw": {}}

    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", response.text,
                  flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)

    for pattern in PROHIBITION_PATTERNS:
        match = re.search(pattern, text, re.I)
        if match:
            start = max(0, match.start() - 120)
            return {"source": "terms", "url": terms_url, "verdict": "prohibited",
                    "evidence": text[start:match.end() + 120].strip(),
                    "raw": {"pattern": pattern}}

    verdict = "allowed" if re.search(r"terms|conditions", text, re.I) else "unclear"
    return {"source": "terms", "url": terms_url, "verdict": verdict,
            "evidence": "no automated-access prohibition matched",
            "raw": {"length": len(text)}}


def record(conn, agency_id: int, result: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            f"""insert into {t('agency_tos_checks')}
                  (agency_id, source, url, verdict, evidence, raw)
                values (%s, %s, %s, %s, %s, %s::jsonb)""",
            (agency_id, result["source"], result.get("url"), result["verdict"],
             (result.get("evidence") or "")[:2000], json.dumps(result.get("raw", {}))),
        )


def canonical_terms_status(result: dict) -> str:
    """Translate a point-in-time check into the agency/platform vocabulary."""
    if result["verdict"] == "prohibited":
        return "prohibits"
    if result["verdict"] == "allowed":
        return "permissive"
    if result["verdict"] == "unavailable" and result.get("url") is None:
        return "none_found"
    return "ambiguous"


def platform_name(url: str, configured: str | None = None) -> str:
    """Use the stored platform when present, otherwise a stable host family."""
    if configured and configured != "self-hosted":
        return configured
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    families = {
        "eboardsolutions.com": "simbli",
        "boarddocs.com": "boarddocs",
        "legistar.com": "legistar",
        "civicclerk.com": "civicclerk",
        "boardbook.org": "boardbook",
    }
    for suffix, name in families.items():
        if host == suffix or host.endswith(f".{suffix}"):
            return name
    return host or "unknown"


def record_platform_terms(conn, url: str, result: dict, configured: str | None = None) -> None:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if not host:
        return
    with conn.cursor() as cur:
        cur.execute(
            f"""insert into {t('platform_terms')}
                  (platform,host,terms_url,status,evidence,scope_note,checked_at)
                values (%s,%s,%s,%s,%s,%s,now())
                on conflict (platform,host) do update set
                  terms_url=excluded.terms_url,status=excluded.status,
                  evidence=excluded.evidence,scope_note=excluded.scope_note,
                  checked_at=now()""",
            (platform_name(url, configured), host, result.get("url"),
             canonical_terms_status(result), (result.get("evidence") or "")[:2000],
             "Automated check of the vendor origin used to retrieve meeting records."),
        )


def evaluate(conn, agency_id: int, base_url: str, extra_hosts: list[str] | None = None,
             configured_platform: str | None = None) -> dict:
    """Check every host we would actually fetch from, not just the district's.

    This originally checked only the district website and cleared districts whose
    meeting documents live on a vendor portal that disallows everything — the
    district's own robots.txt says nothing about eboardsolutions.com. Permission
    has to be evaluated per origin we intend to request, because that is the
    party whose server we would be hitting.
    """
    robots = check_robots(base_url)
    terms = check_terms(base_url)
    record(conn, agency_id, robots)
    record(conn, agency_id, terms)

    vendor_checks = []
    for host in extra_hosts or []:
        if not host:
            continue
        parsed = urllib.parse.urlparse(host)
        if parsed.netloc and parsed.netloc != urllib.parse.urlparse(base_url).netloc:
            vendor_robots = check_robots(host)
            vendor_robots["raw"] = {**vendor_robots.get("raw", {}), "origin": "platform"}
            vendor_terms = check_terms(host)
            vendor_terms["raw"] = {**vendor_terms.get("raw", {}), "origin": "platform"}
            record(conn, agency_id, vendor_robots)
            record(conn, agency_id, vendor_terms)
            record_platform_terms(conn, host, vendor_terms, configured_platform)
            vendor_checks.append((host, vendor_robots, vendor_terms))

    # Terms decide. robots.txt is informational from here on: it is recorded
    # above and surfaced in the summary, but a Disallow alone no longer blocks.
    blocking_terms = [(base_url, terms)] + [
        (host, vendor_terms) for host, _robots, vendor_terms in vendor_checks
    ]
    prohibited = [(host, item) for host, item in blocking_terms
                  if canonical_terms_status(item) == "prohibits"]
    blocked = bool(prohibited)
    effective_terms = prohibited[0][1] if prohibited else terms
    agency_terms_status = canonical_terms_status(effective_terms)
    reason = (f"terms on {urllib.parse.urlparse(prohibited[0][0]).netloc}: "
              f"{prohibited[0][1]['evidence'][:300]}") if blocked else None

    # A vendor robots.txt is still recorded — it is the operator's stated
    # preference for the host the documents actually live on, and proceeding
    # over it should be a visible decision. But it no longer blocks: robots is
    # not the instrument that decides whether collection is permitted, and
    # treating a vendor Disallow as decisive is what wrongly ruled out every
    # BoardDocs district in the first pass.
    advisories = [
        f"{urllib.parse.urlparse(host).netloc} robots.txt: {check['evidence']}"
        for host, check, _terms in vendor_checks if check["verdict"] == "prohibited"
    ]

    with conn.cursor() as cur:
        cur.execute(
            f"""update {t('agencies')}
                   set terms_status=%s,terms_url=%s,terms_note=%s,
                       status=case when status='tos_blocked' then 'not_attempted'
                                   else status end,
                       status_detail=case when status='tos_blocked' then null
                                          else status_detail end,
                       skip_reason=case when %s then 'terms_prohibit_automation'
                                        when skip_reason='terms_prohibit_automation' then null
                                        else skip_reason end,
                       updated_at=now()
                 where id=%s""",
            (agency_terms_status, effective_terms.get("url"),
             (effective_terms.get("evidence") or "")[:2000],
             blocked, agency_id),
        )
    return {"robots": robots, "terms": terms, "platform": vendor_checks,
            "advisories": advisories,
            "blocked": blocked, "reason": reason}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agency-id", type=int)
    ap.add_argument("--leaid")
    ap.add_argument("--url", help="site to check; defaults to the agency's stored website")
    ap.add_argument("--platform-url", help="vendor portal where documents actually live")
    args = ap.parse_args()

    with connect() as conn:
        with dict_cursor(conn) as cur:
            if args.agency_id:
                cur.execute(f"select * from {t('agencies')} where id = %s", (args.agency_id,))
            elif args.leaid:
                cur.execute(f"select * from {t('agencies')} where nces_leaid = %s", (args.leaid,))
            else:
                sys.exit("pass --agency-id or --leaid")
            agency = cur.fetchone()
        if not agency:
            sys.exit("agency not found")

        base_url = args.url or agency.get("website")
        if not base_url:
            sys.exit("no URL known for this agency; pass --url")

        if args.url and args.url != agency.get("website"):
            with conn.cursor() as cur:
                cur.execute(f"update {t('agencies')} set website = %s, updated_at = now() where id = %s",
                            (args.url, agency["id"]))

        print(f"{agency['name']}  ({base_url})\n")
        result = evaluate(conn, agency["id"], base_url,
                          extra_hosts=[agency.get("board_page_url"), args.platform_url],
                          configured_platform=agency.get("platform"))
        for key in ("robots", "terms"):
            item = result[key]
            print(f"  {key:<10} {item['verdict']:<12} {item.get('url') or ''}")
            print(f"             {(item.get('evidence') or '')[:150]}")
        for host, robots_check, terms_check in result["platform"]:
            print(f"  {'platform':<10} robots={robots_check['verdict']:<11} "
                  f"terms={canonical_terms_status(terms_check):<10} {host[:70]}")
            print(f"             {(terms_check.get('evidence') or '')[:150]}")
        print()
        print(f"  => {'BLOCKED — ' + (result['reason'] or '') if result['blocked'] else 'permitted to collect'}")


if __name__ == "__main__":
    main()
