#!/usr/bin/env python3
"""Stáhne postavy party z WoW Forever Armory a vygeneruje statickou stránku do site/."""
import html
import json
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

CLASS_COLOR = {
    "warrior": "#C69B6D", "paladin": "#F48CBA", "hunter": "#AAD372", "rogue": "#FFF468",
    "priest": "#F0F0F0", "shaman": "#2E8FFF", "mage": "#3FC7EB", "warlock": "#8788EE",
    "druid": "#FF7C0A",
}


def fetch_profile(region, slug):
    url = ARMORY.format(region=region, slug=urllib.parse.quote(slug))
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        page = resp.read().decode("utf-8")
    match = STATE_RE.search(page)
    if not match:
        raise ValueError("stránka neobsahuje data postavy")
    return json.loads(match.group(1))["character"], url


def summarize(c, url):
    # Armory označí jako active každý strom s body; hlavní spec je ten s nejvíc body.
    specs = [s for s in c.get("specs") or [] if s.get("spentPoints")]
    spec = max(specs, key=lambda s: s["spentPoints"], default=None)
    cls = c["class"]["slug"]
    roles = ROLES.get((cls, spec["id"]), ["dps"]) if spec else []

    professions = []
    for group in ("primaries", "secondaries"):
        for p in (c.get("professions") or {}).get(group, []):
            tier = max(p.get("tiers") or [{}], key=lambda t: t.get("maxSkillPoints", 0))
            professions.append({
                "name": p["name"],
                "primary": group == "primaries",
                "skill": tier.get("skillPoints", 0),
                "max": tier.get("maxSkillPoints", 0),
            })

    return {
        "ok": True,
        "name": f'{c["name"]} {c["surname"]}'.strip(),
        "slug": c["slug"],
        "url": url,
        "level": c["level"],
        "ilvl": c.get("averageItemLevel"),
        "race": c["race"]["name"],
        "class": c["class"]["name"],
        "class_slug": cls,
        "spec": spec["name"] if spec else None,
        "spec_points": spec["spentPoints"] if spec else 0,
        "roles": roles,
        "ruleset": (c.get("ruleset") or {}).get("name"),
        "faction": (c.get("faction") or {}).get("name"),
        "avatar": (c.get("avatar") or {}).get("url"),
        "updated": (c.get("lastUpdatedTimestamp") or {}).get("iso8601"),
        "professions": professions,
    }


def load_party(party):
    members = []
    for i, slug in enumerate(party["characters"]):
        if i:
            time.sleep(2)  # Armory je neoficiální zdroj, nebombardujeme ho
        try:
            members.append(summarize(*fetch_profile(party["region"], slug)))
        except Exception as exc:  # jedna rozbitá postava nesmí shodit celou stránku
            print(f"{slug}: {exc}", file=sys.stderr)
            url = ARMORY.format(region=party["region"], slug=urllib.parse.quote(slug))
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
    return f'''<article class="card" style="--cls:{color}">
  <header class="card-head">
    {avatar}
    <div class="who">
      <h3><a href="{e(m["url"])}" target="_blank" rel="noopener">{e(m["name"])}</a></h3>
      <p class="cls">{e(m["race"])} <b>{e(m["class"])}</b></p>
    </div>
    <div class="lvl"><span>{m["level"]}</span><small>level</small></div>
  </header>
  <div class="meta">
    <span>{spec}</span>{role_chips(m["roles"])}
    <span class="ilvl">ilvl {e(m["ilvl"])}</span>
  </div>
  <ul class="profs">{profs}</ul>
  <p class="updated">Armory aktualizována <time data-ts="{e(m["updated"] or "")}">{e(m["updated"] or "?")}</time></p>
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


def render(party, members, rows, best, built):
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
  <section><h2 class="sec">Postavy</h2><div class="cards">{"".join(card(m) for m in members)}</div></section>
  <section><h2 class="sec">Dungeony</h2>{dungeon_table([r for r in rows if r["near"]], best, ok)}
    <details class="all-dungeons"><summary>Všechny dungeony ({len(rows)})</summary>{dungeon_table(rows, best, ok)}</details>
    <p class="note">✓ sedí · −N chybí N levelů · +N je o N levelů výš. Rozsahy podle
    <a href="https://www.wowhead.com/forever/guide/dungeons-overview-locations-details" target="_blank" rel="noopener">Wowhead pro Forever</a>, upravíš je v <code>dungeons.json</code>.</p></section>
  <section><h2 class="sec">Profese</h2>{professions_box(ok)}</section>
  <footer>Data z veřejné <a href="https://worldofwarcraft.blizzard.com/en-us/forever/eu/armory" target="_blank" rel="noopener">WoW Forever Armory</a>, neoficiálně, obnovuje se každé ráno. Armory se aktualizuje se zpožděním.</footer>
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
    members = load_party(party)
    ok = [m for m in members if m["ok"]]
    if not ok:
        sys.exit("Nepodařilo se načíst žádnou postavu, stránku neměním.")
    rows, best = plan_dungeons(ok, dungeons)
    built = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    OUT.mkdir(exist_ok=True)
    (OUT / "index.html").write_text(render(party, members, rows, best, built), encoding="utf-8")
    (OUT / "party.json").write_text(json.dumps({"built": built, "members": members}, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    print(f"Hotovo: {len(ok)}/{len(members)} postav, nejlepší dungeon: {best['name'] if best else '-'}")


if __name__ == "__main__":
    main()
