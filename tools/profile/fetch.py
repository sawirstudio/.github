"""Fetches fresh profile data from GitHub into data/stats.json, data/recent.json (latest public
pull requests for a user, recently pushed public repos for an org) and
data/calendar.json (last 53 weeks of daily activity, feeds the contribution city).

What to fetch is read from config.json:
  kind   "user"  stats + calendar come from the user's contribution graph
         "org"   stats come from the org's public repos; the calendar counts commits on
                 the default branch of every repo the token can see

Environment variables (all optional):
  PROFILE_TOKEN  personal access token (read-only). For a user it lets streaks and totals
                 include private work; for an org it adds commits in private repos to the
                 city (counts only, private repo names are never written out).
                 Falls back to GITHUB_TOKEN.
  GITHUB_TOKEN   the token GitHub Actions provides automatically (public data only).

If the fetch fails, the previous data files are kept, so a flaky API never blanks the profile.

Adapted from github.com/georgekobaidze/georgekobaidze (MIT, see LICENSE-upstream).
"""
import datetime
import json
import os
import pathlib
import sys
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
DATA = HERE / "data"
CFG = json.loads((HERE / "config.json").read_text())
LOGIN = CFG["login"]
SELF_REPO = CFG.get("profile_repo", f"{LOGIN}/{LOGIN}").lower()
UA = f"{LOGIN}-profile-updater"
SKIP = {SELF_REPO, *(r.lower() for r in CFG.get("recent_exclude", []))}   # repos kept out of the recent feed


# ─────────────────────────────── helpers ───────────────────────────────
def graphql(token, query, variables=None):
    req = urllib.request.Request("https://api.github.com/graphql",
                                 data=json.dumps({"query": query, "variables": variables or {}}).encode(),
                                 headers={"User-Agent": UA, "Authorization": f"bearer {token}",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        res = json.loads(r.read().decode())
    if res.get("errors"):
        raise RuntimeError("GraphQL error: " + "; ".join(e.get("message", "?") for e in res["errors"]))
    return res["data"]


def save(name, obj):
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / name).write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


def warn(msg):
    print(f"::warning::{msg}" if os.environ.get("GITHUB_ACTIONS") else f"warning: {msg}", file=sys.stderr)


def streaks(days, today):
    """days: {date: count}. Current streak may end yesterday if today has no activity yet."""
    longest = run = 0
    for d in sorted(d for d in days if d <= today):
        run = run + 1 if days[d] > 0 else 0
        longest = max(longest, run)
    current, d = 0, today
    if days.get(d, 0) == 0:
        d -= datetime.timedelta(days=1)
    while days.get(d, 0) > 0:
        current += 1
        d -= datetime.timedelta(days=1)
    return current, longest


def city_window(today):
    """Last 53 weeks, starting on a Sunday like GitHub's own graph."""
    start = today - datetime.timedelta(weeks=52)
    return start - datetime.timedelta(days=(start.weekday() + 1) % 7)


def calendar_of(days, today):
    start = city_window(today)
    return [[d.isoformat(), days.get(d, 0)] for d in
            (start + datetime.timedelta(days=i) for i in range((today - start).days + 1))]


def add_langs(langs, repo):
    for edge in repo["languages"]["edges"]:
        langs[edge["node"]["name"]] = langs.get(edge["node"]["name"], 0) + edge["size"]


def project_stars(token):
    """Stars for the featured projects (they may live outside the account, e.g. OCA)."""
    repos = [p["repo"] for p in CFG.get("projects", [])]
    if not repos:
        return {}
    parts = [f'p{i}: repository(owner: "{r.split("/")[0]}", name: "{r.split("/")[1]}") {{ stargazerCount }}'
             for i, r in enumerate(repos)]
    res = graphql(token, "query {\n" + "\n".join(parts) + "\n}")
    return {r: (res[f"p{i}"] or {}).get("stargazerCount", 0) for i, r in enumerate(repos)}


def recent_entry(r):
    return {"name": r["nameWithOwner"], "url": r["url"], "pushed_at": r["pushedAt"],
            "stars": r["stargazerCount"], "desc": r.get("description") or ""}


REPO_FIELDS = """name nameWithOwner url pushedAt isFork isArchived isPrivate description stargazerCount forkCount
        languages(first: 20) { edges { size node { name } } }"""


# ──────────────────────────────── user ─────────────────────────────────
USER_QUERY = """
query($login: String!, $after: String) {
  user(login: $login) {
    createdAt
    followers { totalCount }
    pullRequests { totalCount }
    merged: pullRequests(states: MERGED) { totalCount }
    contributionsCollection { contributionYears }
    repositories(ownerAffiliations: OWNER, isFork: false, privacy: PUBLIC, first: 100, after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes { %s }
    }
    recentPRs: pullRequests(last: 30, orderBy: {field: CREATED_AT, direction: ASC}) {
      nodes { title url state createdAt repository { nameWithOwner isPrivate } }
    }
  }
}""" % REPO_FIELDS


def years_query(years):
    parts = [f"""
    y{y}: contributionsCollection(from: "{y}-01-01T00:00:00Z", to: "{y}-12-31T23:59:59Z") {{
      totalCommitContributions
      contributionCalendar {{ totalContributions weeks {{ contributionDays {{ date contributionCount }} }} }}
    }}""" for y in years]
    return "query($login: String!) {\n  user(login: $login) {" + "".join(parts) + "\n  }\n}"


def fetch_user(token, today):
    repos, after = [], None
    while True:
        u = graphql(token, USER_QUERY, {"login": LOGIN, "after": after})["user"]
        repos += u["repositories"]["nodes"]
        if not u["repositories"]["pageInfo"]["hasNextPage"]:
            break
        after = u["repositories"]["pageInfo"]["endCursor"]
    years = sorted(u["contributionsCollection"]["contributionYears"])
    ydata = graphql(token, years_query(years), {"login": LOGIN})["user"] if years else {}

    days, contribs_all = {}, 0
    for y in years:
        c = ydata[f"y{y}"]
        contribs_all += c["contributionCalendar"]["totalContributions"]
        for w in c["contributionCalendar"]["weeks"]:
            for day in w["contributionDays"]:
                days[datetime.date.fromisoformat(day["date"])] = day["contributionCount"]
    current, longest = streaks(days, today)
    langs = {}
    for r in repos:
        add_langs(langs, r)
    cur = ydata.get(f"y{today.year}", {})
    # latest pull requests in public repos (private ones are skipped entirely)
    recent = [{"name": pr["repository"]["nameWithOwner"], "url": pr["url"], "pushed_at": pr["createdAt"],
               "desc": pr["title"], "state": pr["state"]}
              for pr in reversed(u["recentPRs"]["nodes"]) if not pr["repository"]["isPrivate"]]
    stats = {
        "kind": "user",
        "created_at": u["createdAt"],
        "followers": u["followers"]["totalCount"],
        "prs": u["pullRequests"]["totalCount"],
        "prs_merged": u["merged"]["totalCount"],
        "stars": sum(r["stargazerCount"] for r in repos),
        "forks": sum(r["forkCount"] for r in repos),
        "repo_count": len(repos),
        "languages": dict(sorted(langs.items(), key=lambda kv: -kv[1])),
        "year": today.year,
        "contributions_year": cur.get("contributionCalendar", {}).get("totalContributions", 0),
        "contributions_all": contribs_all,
        "streak_current": current,
        "streak_longest": longest,
    }
    return stats, recent, calendar_of(days, today)


# ──────────────────────────────── org ──────────────────────────────────
ORG_QUERY = """
query($login: String!, $after: String) {
  organization(login: $login) {
    createdAt
    membersWithRole { totalCount }
    repositories(first: 100, after: $after, orderBy: {field: PUSHED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes { %s }
    }
  }
}""" % REPO_FIELDS

HISTORY_QUERY = """
query($owner: String!, $name: String!, $since: GitTimestamp!, $after: String) {
  repository(owner: $owner, name: $name) {
    defaultBranchRef { target { ... on Commit {
      history(since: $since, first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes { committedDate }
      }
    } } }
  }
}"""


def fetch_org(token, today):
    repos, after = [], None
    while True:
        o = graphql(token, ORG_QUERY, {"login": LOGIN, "after": after})["organization"]
        repos += o["repositories"]["nodes"]
        if not o["repositories"]["pageInfo"]["hasNextPage"]:
            break
        after = o["repositories"]["pageInfo"]["endCursor"]
    start = city_window(today)
    since = f"{start.isoformat()}T00:00:00Z"
    days, active = {}, 0
    for r in repos:
        if r["isFork"] or r["pushedAt"] < since:
            continue
        n0, cursor = sum(days.values()), None
        while True:
            ref = graphql(token, HISTORY_QUERY, {"owner": LOGIN, "name": r["name"], "since": since,
                                                 "after": cursor})["repository"]["defaultBranchRef"]
            if not ref:
                break
            h = ref["target"]["history"]
            for c in h["nodes"]:
                d = datetime.date.fromisoformat(c["committedDate"][:10])
                days[d] = days.get(d, 0) + 1
            if not h["pageInfo"]["hasNextPage"]:
                break
            cursor = h["pageInfo"]["endCursor"]
        active += sum(days.values()) > n0
    current, longest = streaks(days, today)
    public = [r for r in repos if not r["isPrivate"] and not r["isFork"]]
    langs = {}
    for r in public:
        add_langs(langs, r)
    recent = [recent_entry(r) for r in sorted(public, key=lambda r: r["pushedAt"], reverse=True)
              if r["nameWithOwner"].lower() not in SKIP and not r["isArchived"]]
    stats = {
        "kind": "org",
        "created_at": o["createdAt"],
        "members": o["membersWithRole"]["totalCount"],
        "stars": sum(r["stargazerCount"] for r in public),
        "forks": sum(r["forkCount"] for r in public),
        "repo_count": len(public),
        "private_count": sum(r["isPrivate"] for r in repos),
        "active_repos": active,
        "languages": dict(sorted(langs.items(), key=lambda kv: -kv[1])),
        "commits_365": sum(n for d, n in days.items() if d > today - datetime.timedelta(days=365)),
        "streak_current": current,
        "streak_longest": longest,
    }
    return stats, recent, calendar_of(days, today)


def main():
    today = datetime.datetime.now(datetime.timezone.utc).date()
    token = os.environ.get("PROFILE_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        sys.exit("error: set PROFILE_TOKEN or GITHUB_TOKEN")
    try:
        stats, recent, calendar = (fetch_org if CFG["kind"] == "org" else fetch_user)(token, today)
        stats["project_stars"] = project_stars(token)
    except Exception as ex:  # noqa: BLE001
        warn(f"GitHub fetch failed, keeping previous data: {ex}")
        sys.exit(0 if (DATA / "stats.json").exists() else 1)
    stats["updated"] = today.isoformat()
    save("stats.json", stats)
    save("recent.json", recent[:5])
    save("calendar.json", calendar)
    print("github: ok" + (" (with PROFILE_TOKEN)" if os.environ.get("PROFILE_TOKEN") else " (public only)"))


if __name__ == "__main__":
    main()
