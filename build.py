#!/usr/bin/env python3
"""Stáhne postavy party z Blizzard API (záloha: Armory) a vygeneruje statickou stránku do site/."""
import base64
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "site"
ARMORY = "https://worldofwarcraft.blizzard.com/en-us/forever/{region}/armory/character/{slug}"
USER_AGENT = "kozel-party-tracker/1.0"
STATE_RE = re.compile(r"var characterProfileInitialState = (\{.*?\});\s*\n", re.S)

ALL_PRIMARIES = ["Alchemy", "Blacksmithing", "Enchanting", "Engineering", "Herbalism",
                 "Leatherworking", "Mining", "Skinning", "Tailoring"]

# (class, spec) -> role; cokoliv jiného je dps
ROLES = {
    ("warrior", "protection"): ["tank"],
    ("paladin", "protection"): ["tank"],
    ("paladin", "holy"): ["heal"],
    ("priest", "holy"): ["heal"],
    ("priest", "discipline"): ["heal"],
    ("shaman", "restoration"): ["heal"],
    ("druid", "restoration"): ["heal"],
    ("druid", "feral"): ["tank", "dps"],
}
ROLE_LABEL = {"tank": "Tank", "heal": "Healer", "dps": "DPS"}

# XP potřebné na další level (index = level - 1); Forever má stejnou křivku jako Classic, součet 4 084 700
XP_TO_NEXT = [400, 900, 1400, 2100, 2800, 3600, 4500, 5400, 6500, 7600, 8800, 10100, 11400, 12900, 14400, 16000,
              17700, 19400, 21300, 23200, 25200, 27300, 29400, 31700, 34000, 36400, 38900, 41400, 44300, 47400,
              50800, 54500, 58600, 62800, 67100, 71600, 76100, 80800, 85700, 90700, 95800, 101000, 106300, 111800,
              117500, 123200, 129100, 135100, 141200, 147500, 153900, 160400, 167100, 173900, 180800, 187900,
              195000, 202300, 209800]

CLASS_COLOR = {
    "warrior": "#C69B6D", "paladin": "#F48CBA", "hunter": "#AAD372", "rogue": "#FFF468",
    "priest": "#F0F0F0", "shaman": "#2E8FFF", "mage": "#3FC7EB", "warlock": "#8788EE",
    "druid": "#FF7C0A",
}


def http_json(req):
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


# ---------- oficiální Blizzard API (hlavní zdroj) ----------

def api_token():
    cid, secret = os.environ.get("BNET_CLIENT_ID"), os.environ.get("BNET_CLIENT_SECRET")
    if not (cid and secret):
        return None
    auth = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    req = urllib.request.Request("https://oauth.battle.net/token", data=b"grant_type=client_credentials",
                                 headers={"Authorization": f"Basic {auth}"})
    return http_json(req)["access_token"]


def api_get(token, region, path, kind):
    """kind je profile, static nebo dynamic; Forever má namespace {kind}-forever-{region}."""
    url = (f"https://{region}.api.blizzard.com{urllib.parse.quote(path)}"
           f"?namespace={kind}-forever-{region}&locale=en_GB")
    return http_json(urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"}))


def fetch_api(token, region, slug):
    def get(sub=""):
        return api_get(token, region, f"/profile/wow/character/{slug}{sub}", "profile")

    c = get()
    groups = get("/specializations").get("specialization_groups") or []
    active = next((g for g in groups if g.get("is_active")), groups[0] if groups else {})
    specs = [(s["specialization_name"], s["spent_points"])
             for s in active.get("specializations") or [] if s.get("spent_points")]
    profs = get("/professions")
    assets = {a["key"]: a["value"] for a in get("/character-media").get("assets") or []}
    quests = get("/quests")
    done = get("/quests/completed").get("quests") or []
    login = c.get("last_login_timestamp")
    return member(
        slug=slug, region=region, name=c["name"], surname=c.get("surname", ""), level=c["level"],
        ilvl=c.get("average_item_level"), race=c["race"]["name"], cls=c["character_class"]["name"],
        specs=specs, ruleset=(c.get("ruleset") or {}).get("name"), faction=(c.get("faction") or {}).get("name"),
        avatar=assets.get("avatar"),
        updated=datetime.fromtimestamp(login / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if login else None,
        updated_label="Naposledy ve hře",
        professions=[(p["profession"]["name"], group == "primaries", p.get("tiers") or [],
                      "skill_points", "max_skill_points")
                     for group in ("primaries", "secondaries") for p in profs.get(group) or []],
        xp=c.get("experience"),
        quests_active=[{"id": q["id"], "name": q["name"]} for q in quests.get("in_progress") or []],
        quests_done=[q["id"] for q in done],
        source="api",
    )


# ---------- veřejná Armory (záloha) ----------

def fetch_armory(region, slug):
    url = ARMORY.format(region=region, slug=urllib.parse.quote(slug))
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        match = STATE_RE.search(resp.read().decode("utf-8"))
    if not match:
        raise ValueError("stránka neobsahuje data postavy")
    c = json.loads(match.group(1))["character"]
    return member(
        slug=slug, region=region, name=c["name"], surname=c.get("surname", ""), level=c["level"],
        ilvl=c.get("averageItemLevel"), race=c["race"]["name"], cls=c["class"]["name"],
        specs=[(s["name"], s["spentPoints"]) for s in c.get("specs") or [] if s.get("spentPoints")],
        ruleset=(c.get("ruleset") or {}).get("name"), faction=(c.get("faction") or {}).get("name"),
        avatar=(c.get("avatar") or {}).get("url"),
        updated=(c.get("lastUpdatedTimestamp") or {}).get("iso8601"),
        updated_label="Armory aktualizována",
        professions=[(p["name"], group == "primaries", p.get("tiers") or [], "skillPoints", "maxSkillPoints")
                     for group in ("primaries", "secondaries") for p in (c.get("professions") or {}).get(group, [])],
        source="armory",
    )


def member(*, slug, region, name, surname, cls, specs, professions, level, xp=None, **rest):
    # Body můžou být ve víc stromech; hlavní spec je ten s nejvíc body.
    spec_name, spec_points = max(specs, key=lambda s: s[1], default=(None, 0))
    cls_slug = cls.lower()
    spec_slug = spec_name.lower().replace(" ", "-") if spec_name else None
    profs = []
    for prof_name, primary, tiers, skill_key, max_key in professions:
        tier = max(tiers or [{}], key=lambda t: t.get(max_key, 0))
        profs.append({"name": prof_name, "primary": primary,
                      "skill": tier.get(skill_key, 0), "max": tier.get(max_key, 0)})
    return {
        "ok": True,
        "name": f"{name} {surname}".strip(),
        "slug": slug,
        "url": ARMORY.format(region=region, slug=urllib.parse.quote(slug)),
        "class": cls,
        "class_slug": cls_slug,
        "spec": spec_name,
        "spec_points": spec_points,
        "roles": ROLES.get((cls_slug, spec_slug), ["dps"]) if spec_name else [],
        "professions": profs,
        "level": level,
        "xp": xp,
        "xp_next": XP_TO_NEXT[level - 1] if xp is not None and level <= len(XP_TO_NEXT) else None,
        **rest,
    }


def load_party(party, token):
    region = party["region"]
    members = []
    for i, slug in enumerate(party["characters"]):
        try:
            if not token:
                raise RuntimeError("bez API")
            members.append(fetch_api(token, region, slug))
            continue
        except Exception as exc:
            if token:
                print(f"{slug}: API selhalo ({exc}), zkouším Armory", file=sys.stderr)
        if i:
            time.sleep(2)  # Armory je neoficiální zdroj, nebombardujeme ho
        try:
            members.append(fetch_armory(region, slug))
        except Exception as exc:  # jedna rozbitá postava nesmí shodit celou stránku
            print(f"{slug}: {exc}", file=sys.stderr)
            url = ARMORY.format(region=region, slug=urllib.parse.quote(slug))
            members.append({"ok": False, "slug": slug, "url": url, "error": str(exc)})
    return members


def plan_dungeons(members, dungeons):
    levels = [m["level"] for m in members]
    lo, hi = min(levels), max(levels)
    rows = []
    for d in dungeons:
        status = {}
        for m in members:
            if m["level"] < d["min"]:
                status[m["slug"]] = ("under", d["min"] - m["level"])
            elif m["level"] > d["max"]:
                status[m["slug"]] = ("over", m["level"] - d["max"])
            else:
                status[m["slug"]] = ("fit", 0)
        fit = sum(1 for s, _ in status.values() if s == "fit")
        near = d["max"] >= lo and d["min"] <= hi + 5
        rows.append({**d, "status": status, "fit": fit, "near": near})
    # Víc lidí v rozsahu > vyšší dungeon > užší rozsah
    best = max(rows, key=lambda r: (r["fit"], r["min"], -r["max"]), default=None)
    return rows, best


def ready_check(token, region, rows, best, members, catalog):
    """Dungeonové questy z dungeonů v dosahu a stav každého člena party."""
    with_quests = [m for m in members if "quests_active" in m]
    if not token or not with_quests:
        return []
    active = {m["slug"]: {q["id"] for q in m["quests_active"]} for m in with_quests}
    done = {m["slug"]: set(m["quests_done"]) for m in with_quests}
    levels = {m["slug"]: m["level"] for m in with_quests}
    names = {q["id"]: q["name"] for qs in catalog.values() for q in qs}
    xp_cache = {}

    def quest_xp(qid):
        if qid not in xp_cache:
            try:
                detail = api_get(token, region, f"/data/wow/quest/{qid}", "static")
                xp_cache[qid] = (detail.get("rewards") or {}).get("experience")
            except Exception:
                xp_cache[qid] = None
        return xp_cache[qid]

    checks, seen = [], set()
    for r in sorted((r for r in rows if r["near"]), key=lambda r: r is not best):
        # křídla jako "Scarlet Monastery: Library" sdílí jeden seznam questů
        title = r["name"] if r["name"] in catalog else r["name"].split(":")[0]
        if title not in catalog or title in seen:
            continue
        seen.add(title)
        quests, unclaimed_names = [], set()
        for q in catalog[title]:
            qid, series = q["id"], q.get("series") or []
            step = series.index(qid) if qid in series else None
            prev = series[step - 1] if step else None
            status = {}
            for slug in active:
                if qid in active[slug]:
                    status[slug] = "active"
                elif qid in done[slug]:
                    status[slug] = "done"
                elif q.get("req") and levels[slug] < q["req"]:
                    status[slug] = "level"
                elif prev and prev not in done[slug]:
                    status[slug] = "chain"
                else:
                    status[slug] = "missing"
            if all(st == "done" for st in status.values()):
                continue
            unclaimed = not any(st in ("active", "done") for st in status.values())
            if unclaimed:
                # varianty se stejným názvem (opakovatelné kroky řetězu) stačí ukázat jednou
                if q["name"] in unclaimed_names:
                    continue
                unclaimed_names.add(q["name"])
            quests.append({
                "id": qid, "name": q["name"], "xp": quest_xp(qid), "req": q.get("req"),
                "sharable": q.get("sharable"), "status": status, "unclaimed": unclaimed,
                "step": [step + 1, len(series)] if step is not None else None,
                "prev": (names.get(prev) or "předchozí quest") if prev else None,
            })
        # nejdřív questy, které někdo má, pak volné, nakonec ty, na které zatím nikdo nemůže
        quests.sort(key=lambda q: (q["unclaimed"], all(st in ("level", "chain") for st in q["status"].values())))
        if quests and (r is best or any(not q["unclaimed"] for q in quests)):
            checks.append({"title": title, "min": r["min"], "max": r["max"], "best": r is best,
                           "quests": quests, "members": list(active)})
    return checks


# ---------- HTML ----------

def e(value):
    return html.escape(str(value), quote=True)


def role_chips(roles):
    if not roles:
        return '<span class="chip chip-none">bez specu</span>'
    return "".join(f'<span class="chip chip-{r}">{ROLE_LABEL[r]}</span>' for r in roles)


def prof_bar(p):
    pct = round(100 * p["skill"] / p["max"]) if p["max"] else 0
    return (f'<li class="prof{"" if p["primary"] else " prof-sec"}">'
            f'<span class="prof-name">{e(p["name"])}</span>'
            f'<span class="prof-num">{p["skill"]}<small>/{p["max"]}</small></span>'
            f'<span class="bar"><span style="width:{pct}%"></span></span></li>')


def card(m):
    if not m["ok"]:
        return (f'<article class="card card-err"><h3>{e(m["slug"])}</h3>'
                f'<p>Postavu se nepodařilo načíst.</p>'
                f'<a href="{e(m["url"])}" target="_blank" rel="noopener">Otevřít Armory</a></article>')
    color = CLASS_COLOR.get(m["class_slug"], "#ccc")
    spec = f'{e(m["spec"])} <small>({m["spec_points"]} b.)</small>' if m["spec"] else "—"
    avatar = (f'<img class="avatar" src="{e(m["avatar"])}" alt="" loading="lazy">'
              if m["avatar"] else '<div class="avatar"></div>')
    profs = "".join(prof_bar(p) for p in m["professions"]) or '<li class="muted">žádné profese</li>'
    xp = ""
    if m.get("xp_next"):
        pct = min(100, round(100 * m["xp"] / m["xp_next"]))
        xp = (f'<div class="xp"><span class="xp-num">{m["xp"]:,} / {m["xp_next"]:,} XP'
              f' <small>({pct} %, chybí {m["xp_next"] - m["xp"]:,})</small></span>'
              f'<span class="bar xp-bar"><span style="width:{pct}%"></span></span></div>').replace(",", "\u00a0")
    return f'''<article class="card" style="--cls:{color}">
  <header class="card-head">
    {avatar}
    <div class="who">
      <h3><a href="{e(m["url"])}" target="_blank" rel="noopener">{e(m["name"])}</a></h3>
      <p class="cls">{e(m["race"])} <b>{e(m["class"])}</b></p>
    </div>
    <div class="lvl"><span>{m["level"]}</span><small>level</small></div>
  </header>
  {xp}
  <div class="meta">
    <span>{spec}</span>{role_chips(m["roles"])}
    <span class="ilvl">ilvl {e(m["ilvl"])}</span>
  </div>
  <ul class="profs">{profs}</ul>
  <p class="updated">{e(m["updated_label"])} <time data-ts="{e(m["updated"] or "")}">{e(m["updated"] or "?")}</time></p>
</article>'''


def status_cell(st):
    kind, n = st
    if kind == "fit":
        return '<td class="st st-fit" title="sedí">✓</td>'
    if kind == "under":
        return f'<td class="st st-under" title="chybí {n} lvl">−{n}</td>'
    return f'<td class="st st-over" title="o {n} lvl výš">+{n}</td>'


def dungeon_name(r):
    chips = '<span class="chip chip-new">nový</span>' if r.get("new") else ""
    place = e(r["zone"]) + (f' · {e(r["note"])}' if r.get("note") else "")
    return f'{e(r["name"])}{chips}<small>{place}</small>'


def dungeon_table(rows, best, members):
    if not rows:
        return '<p class="muted">Žádný dungeon v dosahu.</p>'
    head = "".join(f'<th class="who-col" style="--cls:{CLASS_COLOR.get(m["class_slug"], "#ccc")}">{e(m["name"].split()[0])}</th>'
                   for m in members)
    body = []
    for r in rows:
        cls = ' class="best"' if r is best else ""
        cells = "".join(status_cell(r["status"][m["slug"]]) for m in members)
        body.append(f'<tr{cls}><th scope="row">{dungeon_name(r)}</th>'
                    f'<td class="range">{r["min"]}–{r["max"]}</td>{cells}</tr>')
    return (f'<div class="table-wrap"><table class="dungeons"><thead><tr><th>Dungeon</th><th>Level</th>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')


def quest_cell(q, st):
    if st == "active":
        return '<td class="st st-fit" title="má v logu">✓</td>'
    if st == "done":
        return '<td class="st st-over" title="už odevzdal">hotovo</td>'
    if st == "level":
        return f'<td class="st st-over" title="quest jde vzít od levelu {q["req"]}">lvl {q["req"]}</td>'
    if st == "chain":
        return f'<td class="st st-over" title="nejdřív: {e(q["prev"])}">⛓</td>'
    return '<td class="st st-under" title="nemá, může si ho vzít">✗</td>'


def quest_meta(q):
    bits = [f'{q["xp"]:,} XP'.replace(",", "\u00a0") if q["xp"] else None,
            f'od lvl {q["req"]}' if q["req"] else None,
            "nesdílitelný" if q["sharable"] is False else None,
            (f'krok {q["step"][0]}/{q["step"][1]}' + (f', po: {e(q["prev"])}' if q["prev"] else ""))
            if q["step"] else None]
    return " · ".join(b for b in bits if b)


def ready_box(check, members):
    by_slug = {m["slug"]: m for m in members}
    cols = [by_slug[s] for s in check["members"]]
    head = "".join(f'<th class="who-col" style="--cls:{CLASS_COLOR.get(m["class_slug"], "#ccc")}">{e(m["name"].split()[0])}</th>'
                   for m in cols)
    body = []
    for q in check["quests"]:
        cells = "".join(quest_cell(q, q["status"][m["slug"]]) for m in cols)
        cls = ' class="q-none"' if q["unclaimed"] else ""
        body.append(f'<tr{cls}><th scope="row"><a href="https://www.wowhead.com/forever/quest={q["id"]}" target="_blank" '
                    f'rel="noopener">{e(q["name"])}</a><small>{quest_meta(q)}</small></th>{cells}</tr>')
    totals = "".join(
        f'<td class="st">{sum(q["xp"] or 0 for q in check["quests"] if q["status"][m["slug"]] == "active"):,}</td>'
        .replace(",", "\u00a0") for m in cols)
    take = sum(1 for q in check["quests"] for st in q["status"].values() if st == "missing")
    flag = ('<span class="flag flag-ok">✓ nikomu nic nechybí</span>' if not take
            else f'<span class="flag flag-bad">✗ k vyzvednutí {take}×</span>')
    best = '<span class="chip chip-new">doporučený</span>' if check["best"] else ""
    return f'''<article class="rc">
  <header class="rc-head"><h3>{e(check["title"])}{best}</h3><span class="range">level {check["min"]}–{check["max"]}</span>{flag}</header>
  <div class="table-wrap"><table class="dungeons rc-table"><thead><tr><th>Quest</th>{head}</tr></thead>
  <tbody>{"".join(body)}</tbody>
  <tfoot><tr><th scope="row">XP z questů v logu</th>{totals}</tr></tfoot></table></div>
</article>'''


def best_box(best, members):
    if not best:
        return ""
    under = [(m["name"], best["status"][m["slug"]][1]) for m in members if best["status"][m["slug"]][0] == "under"]
    over = [m["name"] for m in members if best["status"][m["slug"]][0] == "over"]
    notes = []
    if under:
        notes.append("Chybí: " + ", ".join(f"{e(n)} ({k} lvl)" for n, k in under))
    if over:
        notes.append("Přerostli: " + ", ".join(e(n) for n in over))
    if not notes:
        notes.append("Sedí všem.")
    zone = e(best["zone"]) + (f' ({e(best["note"])})' if best.get("note") else "")
    return (f'<section class="best-box"><p class="eyebrow">Nejlepší společný dungeon</p>'
            f'<h2>{e(best["name"])}</h2><p class="best-meta">{zone} · level {best["min"]}–{best["max"]} · '
            f'sedí {best["fit"]} z {len(members)}</p><p>{" · ".join(notes)}</p></section>')


def professions_box(members):
    have = {}
    for m in members:
        for p in m["professions"]:
            have.setdefault(p["name"], []).append((m["name"].split()[0], p["skill"], p["primary"]))
    rows = []
    for name in sorted(have, key=lambda n: (not have[n][0][2], n)):
        people = ", ".join(f"{e(who)} <small>{skill}</small>" for who, skill, _ in have[name])
        dup = ' <span class="chip chip-dup">2×</span>' if have[name][0][2] and len(have[name]) > 1 else ""
        rows.append(f"<li><div><b>{e(name)}</b>{dup}</div><span>{people}</span></li>")
    missing = [p for p in ALL_PRIMARIES if p not in have]
    miss = (f'<p class="missing">Nikdo nemá: {", ".join(e(p) for p in missing)}</p>' if missing else "")
    return f'<ul class="prof-list">{"".join(rows)}</ul>{miss}'


def ready_section(checks, members):
    if not checks:
        return ""
    return (f'<section><h2 class="sec">Ready check před dungeonem</h2>'
            f'<p class="note legend"><span class="st-fit">✓</span> má v logu · <span class="st-under">✗</span> nemá, může si ho vzít · '
            f'⛓ nejdřív předchozí quest · lvl N zatím nízký level · hotovo = už odevzdal. '
            f'Šedé questy nemá nikdo z vás. Řetězy a sdílení podle Wowheadu, '
            f'jestli quest začíná až v dungeonu, data neříkají.</p>'
            f'{"".join(ready_box(c, members) for c in checks)}</section>')


def render(party, members, rows, best, built, checks):
    ok = [m for m in members if m["ok"]]
    has_tank = any("tank" in m["roles"] for m in ok)
    has_heal = any("heal" in m["roles"] for m in ok)
    levels = [m["level"] for m in ok]
    lvl_range = f"{min(levels)}–{max(levels)}" if levels else "?"
    ruleset = next((m["ruleset"] for m in ok if m["ruleset"]), "")
    faction = next((m["faction"] for m in ok if m["faction"]), "")

    def flag(label, good):
        return f'<span class="flag {"flag-ok" if good else "flag-bad"}">{"✓" if good else "✗"} {label}</span>'

    return f'''<!doctype html>
<html lang="cs">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(party["title"])}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@600;700&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>{CSS}</style>
</head>
<body>
<main>
  <header class="top">
    <div>
      <p class="eyebrow">{e(faction)} · {e(ruleset)} · WoW Forever EU</p>
      <h1>{e(party["title"])}</h1>
    </div>
    <p class="built">Staženo <time data-ts="{built}">{built}</time></p>
  </header>
  <div class="flags">{flag("Tank", has_tank)}{flag("Healer", has_heal)}<span class="flag">Levely {lvl_range}</span></div>
  {best_box(best, ok)}
  {ready_section(checks, ok)}
  <section><h2 class="sec">Postavy</h2><div class="cards">{"".join(card(m) for m in members)}</div></section>
  <section><h2 class="sec">Dungeony</h2>{dungeon_table([r for r in rows if r["near"]], best, ok)}
    <details class="all-dungeons"><summary>Všechny dungeony ({len(rows)})</summary>{dungeon_table(rows, best, ok)}</details>
    <p class="note">✓ sedí · −N chybí N levelů · +N je o N levelů výš. Rozsahy podle
    <a href="https://www.wowhead.com/forever/guide/dungeons-overview-locations-details" target="_blank" rel="noopener">Wowhead pro Forever</a>, upravíš je v <code>dungeons.json</code>.</p></section>
  <section><h2 class="sec">Profese</h2>{professions_box(ok)}</section>
  <footer>Data z Blizzard API (záloha: veřejná <a href="https://worldofwarcraft.blizzard.com/en-us/forever/eu/armory" target="_blank" rel="noopener">WoW Forever Armory</a>), obnovuje se každé ráno. Postava se aktualizuje, když se odhlásí ze hry.</footer>
</main>
<script>{JS}</script>
</body>
</html>
'''


CSS = """
:root{--bg:#120e0b;--panel:#1d1712;--panel2:#261e17;--line:#3a2e23;--text:#efe6d8;--muted:#a3937f;
--gold:#e8b85c;--red:#c8372d;--ok:#6fbf73;--bad:#e0574b;--warn:#e8b85c}
*{box-sizing:border-box}
body{margin:0;background:radial-gradient(1200px 500px at 50% -200px,#3a1410 0%,transparent 70%),var(--bg);
color:var(--text);font:15px/1.5 Inter,system-ui,sans-serif}
main{max-width:1100px;margin:0 auto;padding:28px 16px 48px}
a{color:var(--gold);text-decoration:none}a:hover{text-decoration:underline}
h1,h2,h3{font-family:Cinzel,Georgia,serif;margin:0;letter-spacing:.02em}
h1{font-size:clamp(28px,6vw,44px);color:var(--gold)}
.eyebrow{margin:0 0 4px;color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.12em}
.top{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;flex-wrap:wrap;border-bottom:1px solid var(--line);padding-bottom:16px}
.built{margin:0;color:var(--muted);font-size:13px}
.flags{display:flex;gap:8px;flex-wrap:wrap;margin:16px 0}
.flag{padding:4px 12px;border-radius:999px;background:var(--panel2);border:1px solid var(--line);font-weight:600;font-size:13px}
.flag-ok{color:var(--ok);border-color:#2f5a32}.flag-bad{color:var(--bad);border-color:#6b2a24}
.best-box{background:linear-gradient(135deg,#3b1612,#22140f);border:1px solid #6b2a24;border-radius:14px;padding:20px;margin:8px 0 28px}
.best-box h2{font-size:clamp(22px,4vw,30px)}.best-box p{margin:6px 0 0}.best-meta{color:var(--gold)}
.sec{font-size:20px;color:var(--gold);margin:28px 0 12px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:14px}
.card{background:var(--panel);border:1px solid var(--line);border-top:3px solid var(--cls);border-radius:12px;padding:14px;display:flex;flex-direction:column;gap:10px}
.card-err{border-top-color:var(--bad)}
.card-head{display:flex;gap:12px;align-items:center}
.avatar{width:52px;height:52px;border-radius:10px;border:2px solid var(--cls);background:var(--panel2);flex:none}
.who{flex:1;min-width:0}.who h3{font-size:16px;overflow-wrap:anywhere}.who h3 a{color:var(--text)}
.cls{margin:0;color:var(--muted);font-size:13px}.cls b{color:var(--cls);font-weight:600}
.lvl{text-align:center;line-height:1}.lvl span{display:block;font:700 28px Cinzel,serif;color:var(--gold)}.lvl small{color:var(--muted);font-size:11px}
.meta{display:flex;flex-wrap:wrap;gap:6px;align-items:center;font-size:13px}.meta small{color:var(--muted)}
.ilvl{margin-left:auto;color:var(--muted)}
.chip{padding:1px 8px;border-radius:999px;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.05em}
.chip-tank{background:#1f3550;color:#8cc4ff}.chip-heal{background:#1f4224;color:#8fe39a}.chip-dps{background:#4a1f1b;color:#ff9b8f}
.chip-none{background:var(--panel2);color:var(--muted)}.chip-dup{background:#4a3a14;color:var(--warn);margin-left:6px}
.chip-new{background:#3b2a5c;color:#c9b2ff;margin-left:6px}
.all-dungeons{margin-top:12px}.all-dungeons summary{cursor:pointer;color:var(--gold);font-weight:600;margin-bottom:10px}
.profs{list-style:none;margin:0;padding:0;display:grid;gap:6px}
.prof{display:grid;grid-template-columns:1fr auto;gap:2px 8px;font-size:13px}
.prof-sec{opacity:.7}.prof-num small{color:var(--muted)}
.bar{grid-column:1/-1;height:4px;background:var(--panel2);border-radius:2px;overflow:hidden}
.bar span{display:block;height:100%;background:var(--cls)}
.updated{margin:auto 0 0;color:var(--muted);font-size:12px}
.xp{display:grid;gap:3px;font-size:12px;color:var(--muted)}.xp small{color:var(--muted)}.xp-num{color:var(--text)}
.xp-bar span{background:linear-gradient(90deg,#7b4bd1,#b58cff)!important}
.rc{margin-bottom:18px}.rc-head{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:8px}
.rc-head h3{font-size:18px}.rc-table tfoot th,.rc-table tfoot td{background:var(--panel2);font-weight:600}
.rc-table tbody th a{color:var(--text)}.rc-table tr.q-none th a{color:var(--muted)}
.rc-table tbody th small{display:block;color:var(--muted);font-weight:400;font-size:12px}
.legend .st-fit{color:var(--ok)}.legend .st-under{color:var(--bad)}
.table-wrap{overflow-x:auto;border:1px solid var(--line);border-radius:12px}
table.dungeons{width:100%;border-collapse:collapse;font-size:14px;min-width:520px}
.dungeons th,.dungeons td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left}
.dungeons thead th{background:var(--panel2);font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
.dungeons tbody th{font-weight:600}.dungeons tbody th small{display:block;color:var(--muted);font-weight:400;font-size:12px}
.dungeons tr:last-child>*{border-bottom:0}
.who-col{color:var(--cls)!important;text-align:center!important}
.range{color:var(--gold);white-space:nowrap}
.st{text-align:center!important;font-weight:600}.st-fit{color:var(--ok)}.st-under{color:var(--bad)}.st-over{color:var(--muted)}
tr.best{background:#2c1611}tr.best th:first-child{box-shadow:inset 3px 0 var(--red)}
.note,.missing{color:var(--muted);font-size:13px}.missing{color:var(--warn)}
.prof-list{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:8px}
.prof-list li{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 12px;display:flex;flex-direction:column}
.prof-list li span{color:var(--muted);font-size:13px}.prof-list small{color:var(--gold)}
footer{margin-top:36px;padding-top:16px;border-top:1px solid var(--line);color:var(--muted);font-size:12px}
code{background:var(--panel2);padding:1px 5px;border-radius:4px}
"""

JS = """
const rtf=new Intl.RelativeTimeFormat('cs',{numeric:'auto'});
for(const t of document.querySelectorAll('time[data-ts]')){
  const d=new Date(t.dataset.ts); if(isNaN(d)) continue;
  const s=(d-Date.now())/1000, a=Math.abs(s);
  const [v,u]=a<3600?[s/60,'minute']:a<86400?[s/3600,'hour']:[s/86400,'day'];
  t.textContent=rtf.format(Math.round(v),u); t.title=d.toLocaleString('cs-CZ');
}
"""


def main():
    party = json.loads((ROOT / "party.json").read_text(encoding="utf-8"))
    dungeons = [d for d in json.loads((ROOT / "dungeons.json").read_text(encoding="utf-8"))
                if not d.get("alliance_side")]
    region = party["region"]
    try:
        token = api_token()
    except Exception as exc:
        print(f"API token: {exc}, jedu jen přes Armory", file=sys.stderr)
        token = None
    if not token:
        print("Bez API klíčů, data beru z Armory", file=sys.stderr)

    members = load_party(party, token)
    ok = [m for m in members if m["ok"]]
    if not ok:
        sys.exit("Nepodařilo se načíst žádnou postavu, stránku neměním.")
    rows, best = plan_dungeons(ok, dungeons)
    try:
        catalog = json.loads((ROOT / "quests.json").read_text(encoding="utf-8"))["dungeons"]
        checks = ready_check(token, region, rows, best, ok, catalog)
    except Exception as exc:  # ready check je bonus, stránka musí vzniknout i bez něj
        print(f"Ready check: {exc}", file=sys.stderr)
        checks = []
    built = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    OUT.mkdir(exist_ok=True)
    (OUT / "index.html").write_text(render(party, members, rows, best, built, checks), encoding="utf-8")
    public = [{k: v for k, v in m.items() if k != "quests_done"} for m in members]
    (OUT / "party.json").write_text(json.dumps({"built": built, "members": public, "ready_check": checks},
                                               ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Hotovo: {len(ok)}/{len(members)} postav, nejlepší dungeon: {best['name'] if best else '-'}, "
          f"ready check: {len(checks)} dungeonů")


if __name__ == "__main__":
    main()
