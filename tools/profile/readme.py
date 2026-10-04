"""Writes the profile README from config.json and data/*.json.

The README is a stack of SVG slices (rendered by render.py) that line up into one console.
It is fully generated: edit config.json, not the README.

Adapted from github.com/georgekobaidze/georgekobaidze (MIT, see LICENSE-upstream).
"""
import datetime
import html
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
DATA = HERE / "data"
CFG = json.loads((HERE / "config.json").read_text())
README = ROOT / CFG.get("readme_path", "README.md")
UNIT = CFG.get("city_unit", "contributions")


def alt(s):
    return html.escape(s, quote=True)


def main():
    stats = json.loads((DATA / "stats.json").read_text())
    recent = json.loads((DATA / "recent.json").read_text())
    cal_file = DATA / "calendar.json"
    calendar = json.loads(cal_file.read_text()) if cal_file.exists() else None
    # absolute asset URLs get a daily ?v= so GitHub's image proxy picks up the new render
    base = CFG.get("asset_base", "./assets")
    q = f"?v={stats['updated']}" if base.startswith("http") else ""

    def img(path, a, width="100%"):
        return f'<img src="{base}/{path}{q}" width="{width}" align="top" alt="{alt(a)}">'

    def link(url, inner):
        return f'<a href="{alt(url)}">{inner}</a>'

    h = CFG["header"]
    out = [img("header.svg", f'{h["title"]}: {h["desc"]}'), img("links.svg", "Links")]
    n = len(CFG["links"])
    out.append("".join(link(l["url"], img(f'links/{l["slug"]}.svg', f'{l["label"]}: {l["handle"]}', f"{100 / n:g}%"))
                       for l in CFG["links"]))

    since = datetime.date.fromisoformat(stats["created_at"][:10])
    langs = ", ".join(list(stats["languages"])[:5])
    if stats.get("kind") == "org":
        s_alt = (f'Stats: {stats["stars"]} total stars across {stats["repo_count"]} public repos; '
                 f'{stats["commits_365"]} commits in the last 365 days across {stats["active_repos"]} active repos; '
                 f'current streak {stats["streak_current"]} days, longest {stats["streak_longest"]}; '
                 f'{stats["members"]} members; founded {since:%B %Y}. Top languages: {langs}.')
    else:
        s_alt = (f'Stats: {stats["stars"]} total stars; {stats["contributions_year"]} contributions in {stats["year"]}, '
                 f'{stats["contributions_all"]} all time; {stats["prs"]} pull requests ({stats["prs_merged"]} merged); '
                 f'current streak {stats["streak_current"]} days, longest {stats["streak_longest"]}; '
                 f'{stats["followers"]} followers; member since {since:%B %Y}. Top languages: {langs}.')
    out.append(img("stats.svg", s_alt))

    if calendar:
        total = sum(c for _, c in calendar)
        bd, bn = max(calendar, key=lambda t: t[1])
        c_alt = f"Contribution city: an isometric night skyline with one building per day of the last year. {total:,} {UNIT}"
        if bn:
            d = datetime.date.fromisoformat(bd)
            c_alt += f", busiest day {d:%B} {d.day} with {bn}"
        out.append(img("contribution-city.svg", c_alt + "."))

    out.append(img("projects.svg", "Projects"))
    ps = CFG["projects"]
    for i in range(0, len(ps), 2):
        out.append("".join(link(p["url"], img(f'card-{p["slug"]}.svg', f'{p["name"]}: {p["desc"]} {p["stack"]}.', "50%"))
                           for p in ps[i:i + 2]))
    out.append(img("stack.svg", "Tech stack. " + " ".join(f"{c}: {', '.join(i)}." for c, i in CFG["stack"])))

    user = stats.get("kind") != "org"
    out.append(img("recent.svg", "Latest pull requests" if user else "Recently active repositories"))
    out.append("<!-- recent:start -->")
    for i, r in enumerate(recent[:5], 1):
        a = (f'{r["name"]}: pull request "{r["desc"]}", opened {r["pushed_at"][:10]}, {r["state"].lower()}.' if r.get("state")
             else f'{r["name"]}: {r["desc"]} Last pushed {r["pushed_at"][:10]}, {r["stars"]} stars.')
        out.append(link(r["url"], img(f"recent/repo-{i}.svg", a)))
    out.append("<!-- recent:end -->")
    all_url = CFG.get("all_repos_url") or (f'https://github.com/pulls?q=is%3Apr+author%3A{CFG["login"]}+is%3Apublic' if user
                                            else f'https://github.com/{CFG["login"]}?tab=repositories')
    out.append(link(all_url, img("recent/all-repos.svg", "All pull requests" if user else "Browse all repositories")))
    out.append(img("footer.svg", "Connection closed."))

    README.parent.mkdir(parents=True, exist_ok=True)
    README.write_text('<p align="center">\n' + "\n".join(out) + "\n</p>\n")
    print("README updated:", README.relative_to(ROOT))


if __name__ == "__main__":
    main()
