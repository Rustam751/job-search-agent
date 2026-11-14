"""Pull open postings from public ATS job boards and filter them against the target profile.

Usage: python fetch_jobs.py companies.json postings_filtered.json

companies.json: list of {"name", "ats", "slug", "category"} where ats is
greenhouse | lever | ashby | smartrecruiters | workday (workday slug = "tenant.wdN/site").
Output: list of postings with plain-text descriptions and red-flag hints, ready for scoring.
daily.py calls run() directly to also get every live URL per board (used to tell when a posting has closed).
"""
import html
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

HEADERS = {"User-Agent": "Mozilla/5.0 (job-search script; personal use)", "Accept": "application/json"}

TITLE_INCLUDE = re.compile(
    r"machine learning|\bml\b|\bai\b|artificial intelligence|applied scien|\bllm|deep learning|"
    r"computer vision|perception|imaging|mlops|ml ?ops|research engineer|genai|generative|\bnlp\b|"
    r"forward deployed|new grad|university grad|early career|\bgraduate\b|\bagents?\b|\bagentic\b|college grad|"
    r"university hire|\bncg\b|campus hire|entry.level",
    re.I,
)
# Seniority and non-engineering jobs: always excluded.
TITLE_EXCLUDE = re.compile(
    r"\bsenior\b|\bsr\.?\b|\bstaff\b|principal|\blead\b|manager|director|head of|\bvp\b|chief|"
    r"architect|\bintern\b|internship|phd|postdoc|distinguished|fellow|\biii\b|\biv\b|\bv\b|"
    r"clearance|ts/sci|\bsecret\b|recruiter|account exec|counsel|designer|consultant|strategist|advisor|"
    r"research scientist|product manager|program manager|technical writer|go.to.market|\bgtm\b|evangelist|"
    r"trainer|tutor|annotator|data labeler|expert\b",
    re.I,
)
# Team names that usually mean a non-engineering job — unless the title is plainly an ML one
# ("Machine Learning Engineer, Customer Engineering", "Research Engineer, Policy Learning").
TEAM_EXCLUDE = re.compile(r"sales|marketing|strategy|advisory|policy|operations|support|customer success|partnership|"
                          r"legal|finance", re.I)
ML_TITLE = re.compile(r"machine learning|\bml\b|\bai\b|applied scien|research engineer|computer vision|deep learning", re.I)
BAY_AREA = re.compile(
    r"san francisco|\bsf\b|bay area|oakland|berkeley|emeryville|san mateo|palo alto|mountain view|"
    r"sunnyvale|san jose(?!,?\s*costa rica)|menlo park|redwood city|santa clara|foster city|south san francisco|"
    r"burlingame|san carlos|cupertino|los altos|milpitas|fremont|hayward|san bruno|walnut creek|pleasanton|"
    r"los gatos|san ramon|livermore|union city|daly city|san rafael|danville|novato|mill valley|sausalito|"
    r"half moon bay|castro valley|san leandro|millbrae|east palo alto|morgan hill|saratoga",
    re.I,
)
# Names that also exist outside the Bay Area count only with a California marker.
BAY_AREA_AMBIGUOUS = re.compile(r"\b(brisbane|belmont|alameda|campbell|dublin|newark|richmond|concord)\b", re.I)
CALIFORNIA = re.compile(r"\bca\b|california", re.I)
US_MARKERS = re.compile(r"\bus\b|\busa\b|united states|u\.s\.|north america|americas|anywhere", re.I)
NON_US = re.compile(
    r"\b(?:london|toronto|vancouver|montreal|canada|uk|united kingdom|india|bangalore|bengaluru|"
    r"hyderabad|ireland|berlin|germany|paris|france|amsterdam|netherlands|singapore|tokyo|"
    r"japan|sydney|australia|israel|tel aviv|poland|warsaw|brazil|(?<!new )mexico|spain|madrid|barcelona|"
    r"zurich|switzerland|seoul|korea|emea|apac|latam|europe|portugal|lisbon|argentina|colombia|"
    r"china|shanghai|beijing|shenzhen|taiwan|taipei|hong kong|vietnam|philippines|malaysia|thailand|indonesia|"
    r"dubai|uae|saudi|egypt|nigeria|kenya|south africa|romania|czech|prague|hungary|budapest|ukraine|serbia|"
    r"greece|italy|milan|belgium|austria|vienna|denmark|sweden|norway|finland|stockholm|copenhagen|oslo|"
    r"helsinki|chile|peru|costa rica|new zealand|pakistan|turkey|istanbul|armenia|estonia|lithuania|latvia|"
    r"latin america|eu|guadalajara|monterrey|sao paulo|bogota|buenos aires)\b",
    re.I,
)
RED_FLAGS = {
    "citizenship_or_clearance": re.compile(
        r"u\.?s\.? citizen(ship)? (is )?required|must be a u\.?s\.? citizen|security clearance|"
        r"ts/sci|\bitar\b|\bu\.?s\.? persons?\b", re.I),
    "no_sponsorship_stated": re.compile(r"(not|unable to|cannot|won't|will not) (provide |offer )?(visa )?sponsor", re.I),
    "phd_required": re.compile(r"ph\.?d\.? (is )?required|require[sd]? a ph\.?d", re.I),
}
YEARS = re.compile(r"(\d{1,2})\s*\+?\s*(?:-|–|to)?\s*(?:\d{1,2})?\s*\+?\s*years?", re.I)

# Workday boards have no "list everything" endpoint worth paging through (NVIDIA has thousands of reqs),
# so they are searched by these terms and the union is title/location-screened like every other board.
WORKDAY_TERMS = ["machine learning", "artificial intelligence", "AI engineer", "computer vision", "LLM",
                 "deep learning", "perception", "new grad", "university graduate", "early career"]
WORKDAY_PAGES = 5  # x 20 results per term


def get_json(url, body=None, tries=3):
    """GET (or POST when body is given) with retries on timeouts and 5xx/429."""
    data = json.dumps(body).encode() if body is not None else None
    headers = {**HEADERS, **({"Content-Type": "application/json"} if data else {})}
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code < 500 and e.code != 429 or attempt == tries - 1:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == tries - 1:
                raise
        time.sleep(2 * (attempt + 1))


def strip_html(s):
    s = html.unescape(s or "")
    s = re.sub(r"<(br|/p|/li|/h\d)[^>]*>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", s)).strip()


def title_ok(title):
    hits = {m.group(0).lower() for m in TITLE_INCLUDE.finditer(title)}
    if not hits or TITLE_EXCLUDE.search(title):
        return False
    # "agent" / "graduate" alone are too broad ("Protective Services Agent"): they need an engineering word too
    if hits <= {"agent", "agents", "agentic", "graduate"} and not re.search(r"engineer|developer|scientist|software", title, re.I):
        return False
    return not TEAM_EXCLUDE.search(title) or bool(ML_TITLE.search(title))


def location_ok(loc, remote_flag=False):
    # accent-fold so "San José, CA" matches
    loc = unicodedata.normalize("NFKD", loc or "").encode("ascii", "ignore").decode()
    if BAY_AREA.search(loc) or (BAY_AREA_AMBIGUOUS.search(loc) and CALIFORNIA.search(loc)):
        return "bay_area"
    is_remote = remote_flag or re.search(r"remote|distributed|anywhere", loc, re.I)
    if is_remote and (US_MARKERS.search(loc) or not NON_US.search(loc)):
        return "remote_us"
    if re.fullmatch(r"\s*(united states( of america)?|usa|us|u\.s\.a?\.?)\s*", loc, re.I):
        return "us_unspecified"
    return None


def iso(ts):
    if ts is None:
        return ""
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).date().isoformat()
    return str(ts)[:10]


def fetch_workday(slug):
    host, site = slug.split("/", 1)
    tenant, wd = host.split(".", 1)
    origin = f"https://{tenant}.{wd}.myworkdayjobs.com"
    api = f"{origin}/wday/cxs/{tenant}/{site}"
    found = {}
    for term in WORKDAY_TERMS:
        for page in range(WORKDAY_PAGES):
            data = get_json(f"{api}/jobs", {"appliedFacets": {}, "limit": 20, "offset": page * 20, "searchText": term})
            posts = data.get("jobPostings") or []
            for j in posts:
                if j.get("externalPath"):  # some tenants return grouping rows without a path
                    found.setdefault(j["externalPath"], j)
            if len(posts) < 20:
                break
    out = []
    for path, j in found.items():
        rec = dict(title=j.get("title", ""), location=j.get("locationsText", ""),
                   url=f"{origin}/en-US/{site}{path}", posted="", description="", remote=False, id=path)
        if title_ok(rec["title"]):  # details only for titles that can survive screening
            info = get_json(f"{api}{path}").get("jobPostingInfo", {})
            locs = [info.get("location", "")] + list(info.get("additionalLocations") or [])
            rec.update(location=", ".join(x for x in locs if x) or rec["location"],
                       posted=iso(info.get("startDate")), description=strip_html(info.get("jobDescription", "")),
                       remote=bool(re.search(r"remote", info.get("remoteType") or "", re.I)))
        out.append(rec)
    return out


def fetch_smartrecruiters(slug):
    out, offset = [], 0
    while True:
        data = get_json(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset={offset}")
        items = data.get("content", [])
        for j in items:
            l = j.get("location") or {}
            loc = ", ".join(x for x in [l.get("city"), l.get("region"), l.get("country")] if x)
            rec = dict(title=j.get("name", ""), location=loc, url=f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}",
                       posted=iso(j.get("releasedDate")), description="", remote=bool(l.get("remote")), id=str(j.get("id")))
            if title_ok(rec["title"]) and location_ok(loc, rec["remote"]):
                ad = get_json(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{j.get('id')}").get("jobAd", {})
                secs = ad.get("sections") or {}
                rec["description"] = "\n".join(strip_html((secs.get(k) or {}).get("text", ""))
                                               for k in ("companyDescription", "jobDescription", "qualifications", "additionalInformation"))
            out.append(rec)
        offset += len(items)
        if not items or offset >= data.get("totalFound", 0):
            return out


def fetch_company(c):
    ats, slug = c["ats"], c["slug"]
    out = []
    try:
        if ats == "greenhouse":
            data = get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
            for j in data.get("jobs", []):
                loc = (j.get("location") or {}).get("name", "")
                offices = ", ".join(o.get("name", "") for o in j.get("offices", []) or [])
                if offices and offices not in loc:  # boards that say only "Hybrid"/"Distributed" put the city here
                    loc = f"{loc}, {offices}" if loc else offices
                out.append(dict(title=j.get("title", ""), location=loc,
                                url=j.get("absolute_url", ""), posted=iso(j.get("first_published") or j.get("updated_at")),
                                description=strip_html(j.get("content", "")), remote=False, id=str(j.get("id"))))
        elif ats == "lever":
            data = get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
            for j in data:
                cats = j.get("categories") or {}
                locs = cats.get("allLocations") or [cats.get("location", "")]
                desc = j.get("descriptionPlain", "") + "\n" + "\n".join(
                    l.get("text", "") + "\n" + strip_html(l.get("content", "")) for l in j.get("lists", []) or [])
                out.append(dict(title=j.get("text", ""), location=", ".join(x for x in locs if x),
                                url=j.get("hostedUrl", ""), posted=iso(j.get("createdAt")),
                                description=desc + "\n" + (j.get("additionalPlain") or ""),
                                remote=(j.get("workplaceType") == "remote"), id=j.get("id", "")))
        elif ats == "ashby":
            data = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true")
            for j in data.get("jobs", []):
                if j.get("isListed") is False:
                    continue
                locs = [j.get("location", "")] + [s.get("location", "") for s in j.get("secondaryLocations", []) or []]
                comp = (j.get("compensation") or {}).get("compensationTierSummary") or ""
                out.append(dict(title=j.get("title", ""), location=", ".join(x for x in locs if x),
                                url=j.get("jobUrl", ""), posted=iso(j.get("publishedAt")),
                                description=(j.get("descriptionPlain") or strip_html(j.get("descriptionHtml", ""))),
                                remote=bool(j.get("isRemote")), id=j.get("id", ""), compensation=comp))
        elif ats == "smartrecruiters":
            out = fetch_smartrecruiters(slug)
        elif ats == "workday":
            out = fetch_workday(slug)
        else:
            return c, [], "skipped"
    except Exception as e:  # noqa: BLE001 - record and continue
        return c, [], f"error: {e.__class__.__name__}: {e}"
    return c, out, f"ok ({len(out)} total)"


def screen(c, p):
    title = p["title"]
    if not title_ok(title):
        return None
    where = location_ok(p["location"], p.get("remote"))
    if not where:
        return None
    desc = p.get("description", "")
    flags = [k for k, rx in RED_FLAGS.items() if rx.search(desc)]
    years = sorted({int(m.group(1)) for m in YEARS.finditer(desc) if int(m.group(1)) <= 20})
    return dict(
        company=c["name"], category=c.get("category", ""), ats=c["ats"], slug=c["slug"], job_id=p.get("id", ""),
        title=title, location=p["location"], where=where, url=p["url"], posted=p["posted"],
        compensation=p.get("compensation", ""), years_mentioned=years, red_flags=flags,
        description=desc[:7000],
    )


def run(companies):
    """Fetch every board. Returns (screened postings, per-board status + live URLs, report lines)."""
    companies = [c for c in companies if c.get("ats") != "other" and c.get("slug")]
    kept, boards, report = [], {}, []
    with ThreadPoolExecutor(max_workers=16) as ex:
        for c, postings, status in ex.map(fetch_company, companies):
            hits = [s for s in (screen(c, p) for p in postings) if s]
            kept.extend(hits)
            boards[f"{c['ats']}:{c['slug']}"] = {"company": c["name"], "ok": status.startswith("ok"),
                                                 "status": status, "urls": [p["url"] for p in postings]}
            report.append(f"{c['name']:<35} {c['ats']:<15} {status:<28} kept {len(hits)}")
    seen, unique = set(), []
    for p in kept:
        if p["url"] not in seen:
            seen.add(p["url"])
            unique.append(p)
    return unique, boards, sorted(report)


def main(src, dst):
    companies = json.load(open(src, encoding="utf-8"))
    unique, boards, report = run(companies)
    json.dump(unique, open(dst, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print("\n".join(report))
    print(f"\n{len(unique)} postings kept from {len(boards)} boards -> {dst}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
