#!/usr/bin/env python3
"""Stáhne postavy party z Blizzard API (záloha: Armory) a vygeneruje statickou stránku do site/."""
import base64
import html
import json
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import zlib
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

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

TZ = ZoneInfo("Europe/Prague")

QUALITY_COLOR = {"POOR": "#9d9d9d", "COMMON": "#e6e6e6", "UNCOMMON": "#1eff00", "RARE": "#4a9bff",
                 "EPIC": "#b866ff", "LEGENDARY": "#ff8000"}
QUALITY_NAME = {"EPIC": "fialové", "RARE": "modré", "UNCOMMON": "zelené"}
# sloty, které se počítají do výbavy, v pořadí jako na Armory
SLOT_LABEL = {"HEAD": "hlava", "NECK": "krk", "SHOULDER": "ramena", "BACK": "plášť", "CHEST": "hruď",
              "WRIST": "zápěstí", "HANDS": "ruce", "WAIST": "pas", "LEGS": "nohy", "FEET": "boty",
              "FINGER_1": "prsten", "FINGER_2": "prsten", "TRINKET_1": "trinket", "TRINKET_2": "trinket",
              "MAIN_HAND": "zbraň", "OFF_HAND": "off-hand", "RANGED": "ranged"}
GEAR_SKIP = {"SHIRT", "TABARD", "AMMO", "BODY"}
NO_RANGED = {"paladin", "druid", "shaman"}  # v Classic mají ve slotu jen relikvii

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
    extra = {}
    try:  # výbava a pet jsou bonus, kvůli nim nepadáme na Armory
        extra["gear"] = [{"slot": i["slot"]["type"], "name": i["name"], "quality": i["quality"]["type"],
                          "level": (i.get("level") or {}).get("value"), "inv": i["inventory_type"]["type"],
                          "id": i["item"]["id"]}
                         for i in get("/equipment").get("equipped_items") or []]
    except Exception as exc:
        print(f"{slug}: výbava ({exc})", file=sys.stderr)
    try:  # statistiky jsou pro tituly a počítadla, bez nich stránka jede dál
        extra["stats"] = flat_stats(get("/achievements/statistics"))
    except Exception as exc:
        print(f"{slug}: statistiky ({exc})", file=sys.stderr)
    if c["character_class"]["name"] == "Hunter":
        try:
            pets = get("/hunter-pets").get("hunter_pets") or []
            pet = next((p for p in pets if p.get("is_active")), pets[0] if pets else None)
            if pet:
                extra["pet"] = {"name": pet["name"], "level": pet.get("level"),
                                "creature": (pet.get("creature") or {}).get("name")}
        except Exception as exc:
            print(f"{slug}: pet ({exc})", file=sys.stderr)
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
        **extra,
    )


def flat_stats(data):
    """Strom kategorií statistik zploští na {anglický název: počet}; API vrací jen nenulové."""
    out = {}

    def walk(cat):
        for s in cat.get("statistics") or []:
            if isinstance(s.get("quantity"), (int, float)):
                out[s["name"]] = int(s["quantity"])
        for sub in cat.get("sub_categories") or []:
            walk(sub)

    for cat in data.get("categories") or []:
        walk(cat)
    return out


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
        quests = []
        for q in catalog[title]["quests"]:
            qid, pre = q["id"], q.get("pre")
            # předquesty se nezobrazují, jen se podle nich pozná, kdo už quest může vzít
            earlier = set(pre["ids"]) if pre else set()
            required = {pre["ids"][-1]} if pre else set()
            status = {}
            for slug in active:
                if qid in active[slug]:
                    status[slug] = "active"
                elif qid in done[slug]:
                    status[slug] = "done"
                elif q.get("req") and levels[slug] < q["req"]:
                    status[slug] = "level"
                elif earlier & active[slug]:
                    status[slug] = "pre"
                elif required and not required <= done[slug]:
                    status[slug] = "chain"
                elif q.get("start") == "dungeon":
                    status[slug] = "inside"
                else:
                    status[slug] = "missing"
            if all(st == "done" for st in status.values()):
                continue
            # kdo quest nemá, dostane ho u vchodu od toho, kdo ho má v logu
            if q.get("sharable") and not pre and "active" in status.values():
                status = {s: "share" if st == "missing" else st for s, st in status.items()}
            quests.append({
                "id": qid, "name": q["name"], "xp": quest_xp(qid), "req": q.get("req"),
                "sharable": q.get("sharable"), "start": q.get("start"), "note": q.get("note"),
                "prev": pre["name"] if pre else None, "status": status,
                "unclaimed": not any(st in ("active", "done", "pre") for st in status.values()),
            })
        # nejdřív questy, které někdo má, pak volné, nakonec ty, na které zatím nikdo nemůže
        quests.sort(key=lambda q: (q["unclaimed"], all(st in ("level", "chain") for st in q["status"].values())))
        # ✋ = musí si někam dojít: pro quest, dokončit rozjetý řetěz nebo udělat předquesty
        take = sum(1 for q in quests for st in q["status"].values() if st in ("missing", "pre", "chain"))
        share = sum(1 for q in quests for st in q["status"].values() if st == "share")
        if quests and (r is best or any(not q["unclaimed"] for q in quests)):
            checks.append({"title": title, "min": r["min"], "max": r["max"], "best": r is best,
                           "quests": quests, "members": list(active), "take": take, "share": share,
                           "source": catalog[title].get("source")})
    return checks


# ---------- co se změnilo od včerejška ----------

def snapshot(m):
    return {"level": m["level"], "xp": m.get("xp"),
            "gear": [g["name"] for g in m["gear"]] if "gear" in m else None,
            "profs": {p["name"]: p["skill"] for p in m.get("professions") or []},
            "stats": m.get("stats"), "updated": m.get("updated")}


def local_day(ts):
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone(TZ).date()


def load_baseline(party, built):
    """Stav z posledního běhu před dneškem, vezme se z party.json na nasazené stránce."""
    url = party.get("site_url")
    if not url:
        return None
    try:
        prev = http_json(urllib.request.Request(url.rstrip("/") + "/party.json", headers={"User-Agent": USER_AGENT}))
    except Exception as exc:
        print(f"Včerejší stav: {exc}", file=sys.stderr)
        return None
    if local_day(prev["built"]) == local_day(built) and prev.get("baseline"):
        return prev["baseline"]
    return {"at": prev["built"], "titles": prev.get("titles") or {},
            "members": {m["slug"]: snapshot(m) for m in prev.get("members") or [] if m.get("ok")}}


def xp_total(level, xp):
    return sum(XP_TO_NEXT[:level - 1]) + (xp or 0)


def changes(m, base):
    b = ((base or {}).get("members") or {}).get(m["slug"])
    if not b:
        return []
    out = []
    lvl = m["level"] - b["level"]
    gained = (xp_total(m["level"], m["xp"]) - xp_total(b["level"], b["xp"])
              if m.get("xp") is not None and b.get("xp") is not None and m["level"] <= len(XP_TO_NEXT) else 0)
    xp = f'{gained:,} XP'.replace(",", " ") if gained > 0 else ""
    if lvl > 0:
        out.append(f'<b>+{lvl} lvl</b>' + (f' <small>({xp})</small>' if xp else ""))
    elif xp:
        out.append(f'+{xp}')
    for p in m.get("professions") or []:
        old = b["profs"].get(p["name"])
        if old is None:
            out.append(f'nová profese {e(p["name"])}')
        elif p["skill"] > old:
            out.append(f'{e(p["name"])} +{p["skill"] - old}')
    if b.get("gear") is not None and "gear" in m:
        out += [item_link(g) for g in m["gear"] if g["slot"] not in GEAR_SKIP and g["name"] not in b["gear"]]
    return out


# ---------- tituly a počítadla party ----------

def pl(n, forms):
    """Česká množná čísla: forms = (1, 2–4, 5+), "{n}" se nahradí číslem."""
    form = forms[0] if n == 1 else forms[1] if 2 <= n <= 4 else forms[2]
    return form.replace("{n}", num(n))


# počítadla za celou partu: statistika, emoji, popisek
COUNTERS = [
    ("Total deaths", "🪦", "smrtí"), ("Quests completed", "📜", "questů"),
    ("Creatures killed", "⚔️", "padlých nepřátel"), ("Food eaten", "🍗", "snědených jídel"),
    ("Beverages consumed", "🍺", "vypitých nápojů"), ("Flight paths taken", "🦅", "letů gryfem"),
    ("Number of times hearthed", "🏠", "hearthstonů"), ("Need rolls made on loot", "🎲", "need rollů"),
    ("Quests abandoned", "🗑️", "vzdaných questů"), ("Critters killed", "🐇", "zabitých zvířátek"),
]


def milestones(n):
    """Kulatá čísla 1, 2,5, 5 × 10^k, nejmenší větší než n."""
    step = 10
    while True:
        for m in (step, step * 5 // 2, step * 5):
            if m > n:
                return m
        step *= 10


def last_milestone(n):
    best, step = 0, 10
    while step <= n:
        for m in (step, step * 5 // 2, step * 5):
            if m <= n:
                best = m
        step *= 10
    return best


def has_daily(base):
    """Má včerejší stav statistiky? První den po zapnutí titulů ještě ne."""
    return any((b or {}).get("stats") is not None for b in ((base or {}).get("members") or {}).values())


def evening_values(m, base):
    """Hodnoty pro tituly: rozdíl proti včerejšku, nebo součty za celou dobu (bez včerejších statistik).
    Vrací (hodnoty, hrál?, info) a v info je nejvíc zlepšené řemeslo a statistiky, které má postava poprvé."""
    b = ((base or {}).get("members") or {}).get(m["slug"]) or {}
    now = m.get("stats") or {}
    info = {"skill": None, "firsts": set()}
    if has_daily(base):
        old = b.get("stats") or {}
        vals = {k: v - old.get(k, 0) for k, v in now.items() if v > old.get(k, 0)}
        if b.get("stats") is not None:
            info["firsts"] = {k for k in vals if not old.get(k)}
        # řemesla: "Highest Mining skill", "First Aid skill"… vyhrává největší skok
        skills = {k: v for k, v in vals.items() if k.endswith(" skill")}
        if skills:
            best = max(skills, key=skills.get)
            vals["_skill"] = skills[best]
            info["skill"] = best.removeprefix("Highest ").removesuffix(" skill")
        if b and m["level"] <= len(XP_TO_NEXT) and m.get("xp") is not None and b.get("xp") is not None:
            gained = xp_total(m["level"], m["xp"]) - xp_total(b["level"], b["xp"])
            if gained > 0:
                vals["_xp"] = gained
            if m["level"] > b["level"]:
                vals["_lvl"] = m["level"] - b["level"]
        played = bool(vals) or bool(m.get("updated") and b.get("updated") and m["updated"] > b["updated"])
        return vals, played, info
    return dict(now), True, info


def pick_variant(variants, today, slug):
    """Varianta podle dne a postavy: každý den jiná, ale při opakovaném buildu stejná."""
    return variants[(today.toordinal() + zlib.crc32(slug.encode())) % len(variants)]


def evening_titles(members, base, today, catalog):
    """Každý dostane jeden titul, žádná statistika se nedá dvakrát a nikdo nedostane stejnou jako včera."""
    with_stats = [m for m in members if m.get("stats") is not None]
    yesterday = (base or {}).get("titles") or {}
    values, played, info = {}, {}, {}
    for m in with_stats:
        values[m["slug"]], played[m["slug"]], info[m["slug"]] = evening_values(m, base)
    stats = catalog.get("stats") or {}
    rules = dict(stats)
    daily = has_daily(base)
    # objev: statistika, kterou ještě nikdo z party neměl a titles.json ji nezná
    disc, skip = catalog.get("discovery"), catalog.get("discovery_skip") or []
    known = set().union(*[set((b or {}).get("stats") or {}) for b in ((base or {}).get("members") or {}).values()])
    if disc and daily:
        for s in values:
            for stat in values[s]:
                if (stat not in rules and stat not in known and not stat.startswith("_")
                        and not any(w in stat for w in skip)):
                    rules[stat] = {**disc, "discovery": True}
    candidates = []
    for stat, rule in rules.items():
        top = max((values[s].get(stat, 0) for s in values), default=0)
        for s in values:
            v = values[s].get(stat, 0)
            if v >= rule["min"] and played[s] and yesterday.get(s) != stat:
                # podíl na nejlepším + vtipnost + velikost čísla (185 nápojů přebije 2 zvířátka)
                score = v / top + rule["bonus"] + 0.1 * math.log2(v / rule["min"])
                candidates.append((score, v / rule["min"], s, stat, v))
    # premiéra: postava něco udělala úplně poprvé (Johnie snědl první jídlo), klíč _first:<statistika>
    first = catalog.get("first")
    if first and daily:
        for s in values:
            for stat in info[s]["firsts"]:
                key = "_first:" + stat
                if stat in stats and not stat.startswith("_") and played[s] and yesterday.get(s) != key:
                    rules[key] = {**first, "forms": stats[stat]["forms"]}
                    # čím víc to ostatní dělají, tím vtipnější premiéra (první jídlo > první vzdaný quest)
                    others = sum(((b or {}).get("stats") or {}).get(stat, 0)
                                 for slug, b in base["members"].items() if slug != s)
                    candidates.append((1 + first["bonus"] + 0.05 * math.log2(1 + others), 1, s, key, values[s][stat]))
    out, used = {}, set()
    for _, _, s, stat, v in sorted(candidates, reverse=True):
        if s in out or stat in used:
            continue
        rule = rules[stat]
        t = pick_variant(rule["titles"], today, s)
        if rule.get("discovery"):
            line = t["line"].replace("{stat}", stat).replace("{n}", num(v))
        else:
            line = t["line"].replace("{v}", pl(v, rule["forms"])).replace("{skill}", info[s]["skill"] or "")
        out[s] = {"key": stat, "emoji": rule["emoji"], "title": t["name"], "detail": line}
        used.add(stat)
    for m in with_stats:
        s = m["slug"]
        if s in out:
            continue
        group = "quiet" if played[s] else "sleepers"
        pool = [(f"{group}:{i}", t) for i, t in enumerate(catalog.get(group) or [])
                if f"{group}:{i}" != yesterday.get(s)] or [(group, {"emoji": "🙂", "name": "Hrdina", "line": ""})]
        key, t = pick_variant(pool, today, s)
        out[s] = {"key": key, "emoji": t["emoji"], "title": t["name"], "detail": t["line"]}
    return out


def party_counters(members, base):
    olds = base["members"] if has_daily(base) else {}
    out = []
    for stat, emoji, label in COUNTERS:
        total = sum((m.get("stats") or {}).get(stat, 0) for m in members)
        before = sum(((olds.get(m["slug"]) or {}).get("stats") or {}).get(stat, 0)
                     for m in members) if olds else None
        crossed = last_milestone(total) if before is not None and last_milestone(total) > before else None
        out.append({"emoji": emoji, "label": label, "total": total,
                    "delta": total - before if before is not None else None,
                    "next": milestones(total), "crossed": crossed})
    return out


# ---------- HTML ----------

def e(value):
    return html.escape(str(value), quote=True)


def num(n):
    return f"{n:,}".replace(",", " ")


def first(m):
    return m["name"].split()[0]


def cls_color(m):
    return CLASS_COLOR.get(m.get("class_slug"), "#ccc")


def role_chips(roles):
    if not roles:
        return '<span class="chip chip-none">bez specu</span>'
    return "".join(f'<span class="chip chip-{r}">{ROLE_LABEL[r]}</span>' for r in roles)


def item_link(g):
    return (f'<a class="item" style="color:{QUALITY_COLOR.get(g["quality"], "#ccc")}" '
            f'href="https://www.wowhead.com/forever/item={g["id"]}" target="_blank" rel="noopener">{e(g["name"])}</a>')


def gear_info(m):
    gear = [g for g in m.get("gear") or [] if g["slot"] not in GEAR_SKIP]
    have = {g["slot"] for g in gear}
    two_hand = any(g["slot"] == "MAIN_HAND" and g["inv"] == "TWOHWEAPON" for g in gear)
    empty = []
    for slot, label in SLOT_LABEL.items():
        if slot in have or (slot == "OFF_HAND" and two_hand) or (slot == "RANGED" and m["class_slug"] in NO_RANGED):
            continue
        empty.append(label)
    merged = []
    for label in dict.fromkeys(empty):
        n = empty.count(label)
        merged.append(f"{n}× {label}" if n > 1 else label)
    levels = [g["level"] for g in gear if g["level"]]
    order = list(SLOT_LABEL)
    return {
        "gear": sorted(gear, key=lambda g: order.index(g["slot"]) if g["slot"] in order else 99),
        "empty": merged,
        "avg": round(sum(levels) / len(levels)) if levels else None,
        "weakest": min(gear, key=lambda g: g["level"] or 0) if gear else None,
        "quality": {q: sum(1 for g in gear if g["quality"] == q) for q in QUALITY_NAME},
    }


def gear_box(m):
    if "gear" not in m:
        return ""
    gi = gear_info(m)
    qual = " · ".join(f'<span style="color:{QUALITY_COLOR[q]}">{QUALITY_NAME[q]} {n}</span>'
                      for q, n in gi["quality"].items() if n)
    lines = [f'<p>ø item level <b>{gi["avg"] or "?"}</b>{" · " + qual if qual else ""}</p>']
    if gi["empty"]:
        lines.append(f'<p class="gear-gap">Prázdné: {e(", ".join(gi["empty"]))}</p>')
    if gi["weakest"]:
        w = gi["weakest"]
        lines.append(f'<p>Nejslabší: {item_link(w)} <small>lvl {w["level"]}</small></p>')
    items = "".join(f'<li><span>{e(SLOT_LABEL.get(g["slot"], g["slot"].lower()))}</span>{item_link(g)}'
                    f'<small>{g["level"] or ""}</small></li>' for g in gi["gear"])
    return (f'<div class="gear">{"".join(lines)}'
            f'<details><summary>Výbava ({len(gi["gear"])})</summary><ul class="gear-list">{items}</ul></details></div>')


def xp_bar(m, big=False):
    if not m.get("xp_next"):
        return ""
    pct = min(100, round(100 * m["xp"] / m["xp_next"]))
    label = (f'<span class="xp-num">{num(m["xp"])} / {num(m["xp_next"])} XP <small>· chybí {num(m["xp_next"] - m["xp"])}</small></span>'
             if big else "")
    return (f'<div class="xp" title="{pct} % do dalšího levelu">{label}'
            f'<span class="bar"><span style="width:{pct}%"></span></span></div>')


def avatar(m, cls="avatar"):
    if m.get("avatar"):
        return f'<img class="{cls}" src="{e(m["avatar"])}" alt="" loading="lazy">'
    return f'<div class="{cls}"></div>'


def card(m):
    if not m["ok"]:
        return (f'<article class="card card-err"><h3>{e(m["slug"])}</h3>'
                f'<p>Postavu se nepodařilo načíst.</p>'
                f'<a href="{e(m["url"])}" target="_blank" rel="noopener">Otevřít Armory</a></article>')
    spec = f'{e(m["spec"])} <small>({m["spec_points"]} b.)</small>' if m["spec"] else ""
    pet = m.get("pet")
    pet = (f'<p class="pet">Pet: <b>{e(pet["name"])}</b> <small>{e(pet["creature"] or "")}, lvl {pet["level"]}</small></p>'
           if pet else "")
    return f'''<article class="card" style="--cls:{cls_color(m)}">
  <header class="card-head">
    {avatar(m)}
    <div class="who">
      <h3><a href="{e(m["url"])}" target="_blank" rel="noopener">{e(m["name"])}</a></h3>
      <p class="cls">{e(m["race"])} <b>{e(m["class"])}</b></p>
    </div>
    <div class="lvl">{m["level"]}</div>
  </header>
  {xp_bar(m, big=True)}
  <div class="meta"><span>{spec}</span>{role_chips(m["roles"])}</div>
  {gear_box(m)}{pet}
  <p class="updated">{e(m["updated_label"])} <time data-ts="{e(m["updated"] or "")}">{e(m["updated"] or "?")}</time></p>
</article>'''


# ---------- hlavička ----------

def party_strip(members, size):
    out = []
    for m in members:
        if not m["ok"]:
            out.append(f'<a class="pm pm-err" href="{e(m["url"])}">{avatar({})}<span class="pm-name">{e(m["slug"])}</span>'
                       f'<span class="pm-sub">nenačteno</span></a>')
            continue
        role = "/".join(ROLE_LABEL[r] for r in m["roles"]) or "bez specu"
        out.append(f'<a class="pm" href="#char-{e(m["slug"])}" style="--cls:{cls_color(m)}">{avatar(m)}'
                   f'<span class="pm-name">{e(first(m))} <b>{m["level"]}</b></span>{xp_bar(m)}'
                   f'<span class="pm-sub">{e(m["class"])} · {e(role)}</span></a>')
    out += ['<div class="pm pm-empty"><div class="avatar"></div><span class="pm-name">volné místo</span></div>'] * max(0, size - len(members))
    return f'<div class="strip">{"".join(out)}</div>'


def short_status(r, members):
    under = [f"{e(first(m))} −{r['status'][m['slug']][1]}" for m in members if r["status"][m["slug"]][0] == "under"]
    over = [e(first(m)) for m in members if r["status"][m["slug"]][0] == "over"]
    bits = []
    if under:
        bits.append("chybí lvl: " + ", ".join(under))
    if over:
        bits.append("přerostli: " + ", ".join(over))
    return " · ".join(bits) or "sedí všem"


def next_box(rows, best, members):
    if not best:
        return ""
    zone = e(best["zone"]) + (f' ({e(best["note"])})' if best.get("note") else "")
    later = sorted((r for r in rows if r is not best and r["min"] >= best["min"] and r["max"] > best["max"]),
                   key=lambda r: (r["min"], r["max"]))[:2]
    nxt = "".join(f'<li><b>{e(r["name"])}</b> <span class="range">{r["min"]}–{r["max"]}</span> '
                  f'<small>{short_status(r, members)}</small></li>' for r in later)
    return (f'<section class="next"><p class="eyebrow">Příští výprava</p>'
            f'<h2>{e(best["name"])}</h2>'
            f'<p class="next-meta"><span class="range">level {best["min"]}–{best["max"]}</span> · {zone} · '
            f'sedí {best["fit"]} z {len(members)} · {short_status(best, members)}</p>'
            f'{f"<p class=eyebrow>Pak</p><ul class=later>{nxt}</ul>" if nxt else ""}</section>')


def news_box(members, base):
    if not base:
        return ""
    rows = []
    for m in members:
        if m["ok"] and (ch := changes(m, base)):
            rows.append(f'<li style="--cls:{cls_color(m)}"><b class="who-n">{e(first(m))}</b> {" · ".join(ch)}</li>')
    body = f'<ul>{"".join(rows)}</ul>' if rows else '<p class="muted">Nic nového, nikdo nehrál.</p>'
    return (f'<section class="news"><p class="eyebrow">Novinky od <time data-ts="{e(base["at"])}">{e(base["at"])}</time></p>'
            f'{body}</section>')


def evening_box(members, base, titles, counters):
    if not titles:
        return ""
    daily = has_daily(base)
    cards = "".join(
        f'<li class="title" style="--cls:{cls_color(m)}"><span class="t-emoji">{t["emoji"]}</span>'
        f'<span class="t-who">{e(first(m))}</span><b class="t-name">{e(t["title"])}</b>'
        f'<small>{e(t["detail"])}</small></li>'
        for m in members if (t := titles.get(m["slug"])))
    tiles = []
    for c in counters:
        if not c["total"]:
            continue
        if c["crossed"]:
            foot = f'<small class="c-hit">🎉 právě {num(c["crossed"])}!</small>'
        else:
            delta = f'+{num(c["delta"])} od včera · ' if c["delta"] else ""
            foot = f'<small>{delta}další milník {num(c["next"])}</small>'
        tiles.append(f'<li class="ctr{" ctr-hit" if c["crossed"] else ""}"><span class="c-emoji">{c["emoji"]}</span>'
                     f'<b>{num(c["total"])}</b><span>{e(c["label"])}</span>{foot}</li>')
    hit = any(c["crossed"] for c in counters)
    summary = " · ".join(f'<b>{num(c["total"])}</b> {e(c["label"])}' for c in counters[:3])
    head = "Tituly za včerejší večer" if daily else "Tituly za celou dobu"
    note = ("Každý má jeden titul, žádný se neopakuje dva dny po sobě. Není to žebříček."
            if daily else "Od zítřka se tituly budou dávat za včerejší večer.")
    return (f'<section id="evening"><h2 class="sec">{head}</h2><ul class="titles">{cards}</ul>'
            f'<details class="counters-box"{" open" if hit else ""}><summary>Parta dohromady · {summary}</summary>'
            f'<ul class="counters">{"".join(tiles)}</ul></details>'
            f'<p class="note">{note} Počítá se ze statistik Blizzard API, které se aktualizují, když se postava odhlásí.</p></section>')


# ---------- ready check ----------

def quest_cell(q, st, holders):
    if st == "active":
        return '<td class="st st-fit" title="má v logu">✓</td>'
    if st == "share":
        return f'<td class="st st-share" title="dostane nasdílením od: {e(holders)}">↗</td>'
    if st == "missing":
        start = f': {q["start"]}' if q["start"] else ""
        return f'<td class="st st-take" title="musí si dojít{e(start)}">dojít</td>'
    if st == "done":
        return '<td class="st st-over" title="už odevzdal">hotovo</td>'
    if st == "level":
        return f'<td class="st st-over" title="quest jde vzít od levelu {q["req"]}">lvl {q["req"]}</td>'
    if st == "inside":
        return '<td class="st st-over" title="quest začíná předmětem v dungeonu">uvnitř</td>'
    if st == "pre":
        return '<td class="st st-pre" title="má v logu předquest, musí dokončit řetěz">⛓ ✓</td>'
    return f'<td class="st st-over" title="nejdřív: {e(q["prev"])}">⛓</td>'


def quest_meta(q):
    bits = [f'{num(q["xp"])} XP' if q["xp"] else None,
            f'od lvl {q["req"]}' if q["req"] else None,
            f'start: {e(q["start"])}' if q["start"] and q["start"] != "dungeon" else None,
            f'nejdřív: {e(q["prev"])}' if q["prev"] else None,
            e(q["note"]) if q.get("note") else None]
    return " · ".join(b for b in bits if b)


def share_label(q):
    tags = []
    if q["start"] == "dungeon":
        tags.append('<span class="share share-in" title="začíná předmětem, který padne v dungeonu">začíná uvnitř</span>')
    if q["prev"]:
        tags.append(f'<span class="share share-no" title="navazuje na {e(q["prev"])}, každý si ho musí vzít sám">✋ každý sám · řetěz</span>')
    elif q["sharable"]:
        tags.append('<span class="share share-yes" title="kdo ho má, nasdílí ho ostatním v partě">↗ sdílitelný</span>')
    elif q["sharable"] is False and q["start"] != "dungeon":
        tags.append('<span class="share share-no" title="nejde nasdílet, každý si ho musí vzít sám">✋ každý sám</span>')
    return "".join(tags)


def counts(check, cls=""):
    if not check["take"] and not check["share"]:
        return f'<span class="cnt cnt-ok{cls}" title="nikdo nemusí nic shánět">✓ připraveno</span>'
    out = ""
    if check["share"]:
        out += f'<span class="cnt cnt-share{cls}" title="questy, které se nasdílí u vchodu">↗ {check["share"]}</span>'
    if check["take"]:
        out += f'<span class="cnt cnt-take{cls}" title="questy, pro které si musí někdo dojít">✋ {check["take"]}</span>'
    return out


def todo_cards(check, cols):
    cards = []
    for m in cols:
        s = m["slug"]
        take, chain, start, get, give, low = [], [], [], [], [], []
        for q in check["quests"]:
            st = q["status"][s]
            if st == "missing":
                take.append(q)
            elif st == "pre":
                chain.append(q)
            elif st == "chain":
                start.append(q)
            elif st == "share":
                get.append(q)
            elif st == "active" and any(q["status"][o["slug"]] == "share" for o in cols):
                give.append(q)
            elif st == "level":
                low.append(q)
        lines = []
        if not take and not chain and not start:
            lines.append('<li class="t-ok">✓ nemusí nikam chodit</li>')
        lines += [f'<li class="t-take">dojde si: <b>{e(q["name"])}</b><small>{e(q["start"] or "")}</small></li>' for q in take]
        lines += [f'<li class="t-take">dokončí řetěz: <b>{e(q["name"])}</b><small>nejdřív {e(q["prev"])}</small></li>' for q in chain]
        lines += [f'<li class="t-take">udělá předquesty: <b>{e(q["name"])}</b><small>nejdřív {e(q["prev"])}</small></li>' for q in start]
        if get:
            lines.append(f'<li class="t-get">u vchodu dostane: {e(", ".join(q["name"] for q in get))}</li>')
        if give:
            lines.append(f'<li class="t-give">nasdílí: {e(", ".join(q["name"] for q in give))}</li>')
        if low:
            names = ", ".join(f'{q["name"]} ({q["req"]})' for q in low)
            lines.append(f'<li class="t-low">až s levelem: {e(names)}</li>')
        cards.append(f'<div class="todo" style="--cls:{cls_color(m)}"><h4>{e(first(m))}</h4><ul>{"".join(lines)}</ul></div>')
    return f'<div class="todos">{"".join(cards)}</div>'


def ready_box(check, members, idx):
    by_slug = {m["slug"]: m for m in members}
    cols = [by_slug[s] for s in check["members"]]
    head = "".join(f'<th class="who-col" style="--cls:{cls_color(m)}">{e(first(m))}</th>' for m in cols)
    body = []
    for q in check["quests"]:
        holders = ", ".join(first(m) for m in cols if q["status"][m["slug"]] == "active")
        cells = "".join(quest_cell(q, q["status"][m["slug"]], holders) for m in cols)
        cls = ' class="q-none"' if q["unclaimed"] else ""
        body.append(f'<tr{cls}><th scope="row"><div class="q-name"><a href="https://www.wowhead.com/forever/quest={q["id"]}" '
                    f'target="_blank" rel="noopener">{e(q["name"])}</a>{share_label(q)}</div>'
                    f'<small>{quest_meta(q)}</small></th>{cells}</tr>')
    best = '<span class="chip chip-new">příští výprava</span>' if check["best"] else ""
    source = (f'<a class="rc-source" href="{e(check["source"])}" target="_blank" rel="noopener">průvodce questů ↗</a>'
              if check.get("source") else "")
    hidden = "" if idx == 0 else " hidden"
    return f'''<article class="rc" id="rc-{idx}" role="tabpanel"{hidden}>
  <header class="rc-head"><h3>{e(check["title"])}{best}</h3><span class="range">level {check["min"]}–{check["max"]}</span>{counts(check, " big")}{source}</header>
  {todo_cards(check, cols)}
  <div class="table-wrap"><table class="dungeons rc-table"><thead><tr><th>Quest</th>{head}</tr></thead>
  <tbody>{"".join(body)}</tbody></table></div>
</article>'''


def ready_section(checks, members):
    if not checks:
        return ""
    tabs = "".join(
        f'<button type="button" role="tab" data-rc="{i}" data-title="{e(c["title"])}" '
        f'aria-selected="{"true" if i == 0 else "false"}">{e(c["title"])}{counts(c)}</button>'
        for i, c in enumerate(checks))
    return (f'<section id="ready"><h2 class="sec">Ready check</h2>'
            f'<div class="rc-picker" role="tablist">{tabs}</div>'
            f'{"".join(ready_box(c, members, i) for i, c in enumerate(checks))}'
            f'<p class="note legend"><span class="st-fit">✓</span> má v logu · <span class="st-share">↗</span> dostane nasdílením u vchodu · '
            f'<span class="st-take">dojít</span> musí si pro quest dojít · ⛓ ✓ rozjetý řetěz · ⛓ nejdřív předchozí quest · '
            f'lvl N zatím nízký level · uvnitř = začne předmětem v dungeonu. Šedé questy nemá nikdo z vás. '
            f'Questy podle průvodců Warcraft Tavern, ready check mají jen dungeony z <code>quests.json</code>.</p></section>')


# ---------- profese a dungeony ----------

def professions_box(members):
    have = {}
    for m in members:
        for p in m["professions"]:
            have.setdefault(p["name"], []).append((m, p["skill"]))
    secondary = sorted({p["name"] for m in members for p in m["professions"] if not p["primary"]})

    def tile(name, extra=""):
        people = have.get(name, [])
        state = "none" if not people else "dup" if len(people) > 1 and not extra else "has"
        who = (", ".join(f'<span style="color:{cls_color(m)}">{e(first(m))}</span> <small>{skill}</small>'
                         for m, skill in people) or "nikdo")
        return f'<li class="pt pt-{state}{extra}"><b>{e(name)}</b><span>{who}</span></li>'

    prim = "".join(tile(n) for n in ALL_PRIMARIES)
    sec = "".join(tile(n, " pt-sec") for n in secondary)
    return (f'<ul class="prof-grid">{prim}</ul>'
            + (f'<ul class="prof-grid prof-sec">{sec}</ul>' if sec else "")
            + '<p class="note">Zelená = má jeden, žlutá = mají dva a víc, šedá = nikdo. Číslo je skill.</p>')


def status_cell(st):
    kind, n = st
    if kind == "fit":
        return '<td class="st st-fit" title="sedí">✓</td>'
    if kind == "under":
        return f'<td class="st st-take" title="chybí {n} lvl">−{n}</td>'
    return f'<td class="st st-over" title="o {n} lvl výš">+{n}</td>'


def dungeon_table(rows, best, members):
    head = "".join(f'<th class="who-col" style="--cls:{cls_color(m)}">{e(first(m))}</th>' for m in members)
    body = []
    for r in rows:
        chips = '<span class="chip chip-new">nový</span>' if r.get("new") else ""
        place = e(r["zone"]) + (f' · {e(r["note"])}' if r.get("note") else "")
        cells = "".join(status_cell(r["status"][m["slug"]]) for m in members)
        body.append(f'<tr{" class=best" if r is best else ""}><th scope="row">{e(r["name"])}{chips}<small>{place}</small></th>'
                    f'<td class="range">{r["min"]}–{r["max"]}</td>{cells}</tr>')
    return (f'<div class="table-wrap"><table class="dungeons"><thead><tr><th>Dungeon</th><th>Level</th>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')


def page_head(title):
    return f'''<!doctype html>
<html lang="cs">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@600;700&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>{CSS}</style>
</head>
<body>'''


# ---------- kronika ----------

MONTHS = ["", "ledna", "února", "března", "dubna", "května", "června", "července", "srpna", "září", "října",
          "listopadu", "prosince"]


def cz_date(iso):
    d = datetime.strptime(iso, "%Y-%m-%d")
    return f"{d.day}. {MONTHS[d.month]} {d.year}"


def chronicle_entry(entry, members, full=True):
    by_slug = {m["slug"]: m for m in members}
    who = " ".join(f'<span class="k-who" style="--cls:{cls_color(by_slug[s])}">{e(first(by_slug[s]))}</span>'
                   for s in entry.get("party") or [] if s in by_slug)
    paras = entry.get("text") or []
    if not full:
        paras = paras[:1]
    body = "".join(f"<p>{e(p)}</p>" for p in paras)
    facts = "".join(f"<li>{e(f)}</li>" for f in entry.get("facts") or [])
    dungeon = f' · {e(entry["dungeon"])}' if entry.get("dungeon") else ""
    return (f'<article class="k-entry" id="k-{e(entry["date"])}"><p class="eyebrow">{cz_date(entry["date"])}{dungeon}</p>'
            f'<h3>{e(entry["title"])}</h3>{f"<p class=k-party>{who}</p>" if who else ""}<div class="k-text">{body}</div>'
            f'{f"<ul class=k-facts>{facts}</ul>" if facts and full else ""}</article>')


def chronicle_box(entries, members):
    if not entries:
        return ('<section id="kronika"><h2 class="sec">Kronika</h2><p class="muted k-empty">Kronika je zatím prázdná. '
                'První zápis přibude po příští výpravě.</p></section>')
    latest = entries[0]
    more = len(latest.get("text") or []) > 1 or latest.get("facts") or len(entries) > 1
    count = pl(len(entries), ("{n} zápis", "{n} zápisy", "{n} zápisů"))
    link = (f'<p class="k-more"><a href="kronika.html#k-{e(latest["date"])}">Číst dál a celá kronika ({count}) →</a></p>'
            if more else "")
    return (f'<section id="kronika"><h2 class="sec">Kronika</h2><div class="k-latest">'
            f'{chronicle_entry(latest, members, full=False)}{link}</div></section>')


def render_chronicle(party, entries, members):
    body = "".join(chronicle_entry(x, members) for x in entries) or '<p class="muted">Zatím žádné zápisy.</p>'
    return f'''{page_head("Kronika · " + party["title"])}
<main>
  <header class="top"><div><p class="eyebrow"><a href="index.html">← {e(party["title"])}</a></p><h1>Kronika</h1></div></header>
  <div class="k-list">{body}</div>
  <footer>Zápisy jsou v <code>kronika.json</code>, píšou se po výpravách.</footer>
</main>
</body>
</html>
'''


def render(party, members, rows, best, built, checks, base, titles, counters, chronicle):
    ok = [m for m in members if m["ok"]]
    has_tank = any("tank" in m["roles"] for m in ok)
    has_heal = any("heal" in m["roles"] for m in ok)
    levels = [m["level"] for m in ok]
    lvl_range = f"{min(levels)}–{max(levels)}" if levels else "?"
    ruleset = next((m["ruleset"] for m in ok if m["ruleset"]), "")
    faction = next((m["faction"] for m in ok if m["faction"]), "")

    def flag(label, good):
        return f'<span class="flag {"flag-ok" if good else "flag-bad"}">{"✓" if good else "✗"} {label}</span>'

    cards = "".join(f'<div id="char-{e(m["slug"])}">{card(m)}</div>' for m in members)
    return f'''{page_head(party["title"])}
<main>
  <header class="top">
    <div>
      <p class="eyebrow">{e(faction)} · {e(ruleset)} · WoW Forever EU</p>
      <h1>{e(party["title"])}</h1>
    </div>
    <div class="top-right">
      <div class="flags">{flag("Tank", has_tank)}{flag("Healer", has_heal)}<span class="flag">Levely {lvl_range}</span></div>
      <p class="built">Staženo <time data-ts="{built}">{built}</time></p>
    </div>
  </header>
  {party_strip(members, party.get("size", len(members)))}
  <div class="hero">{next_box(rows, best, ok)}{news_box(ok, base)}</div>
  {ready_section(checks, ok)}
  {chronicle_box(chronicle, ok)}
  {evening_box(ok, base, titles, counters)}
  <section><h2 class="sec">Postavy</h2><div class="cards">{cards}</div></section>
  <section><h2 class="sec">Profese</h2>{professions_box(ok)}</section>
  <section><details class="all-dungeons"><summary>Všechny dungeony podle levelu ({len(rows)})</summary>
    {dungeon_table(rows, best, ok)}
    <p class="note">✓ sedí · −N chybí N levelů · +N je o N levelů výš. Rozsahy podle
    <a href="https://www.wowhead.com/forever/guide/dungeons-overview-locations-details" target="_blank" rel="noopener">Wowhead pro Forever</a>, upravíš je v <code>dungeons.json</code>.</p></details></section>
  <footer>Data z Blizzard API (záloha: veřejná <a href="https://worldofwarcraft.blizzard.com/en-us/forever/eu/armory" target="_blank" rel="noopener">WoW Forever Armory</a>), obnovuje se každé ráno. Postava se aktualizuje, když se odhlásí ze hry.</footer>
</main>
<script>{JS}</script>
</body>
</html>
'''


CSS = """
:root{--bg:#120e0b;--panel:#1d1712;--panel2:#261e17;--line:#3a2e23;--text:#efe6d8;--muted:#a3937f;
--gold:#e8b85c;--red:#c8372d;--ok:#6fbf73;--bad:#e0574b;--warn:#e8a13c;--share:#6cb6ff}
*{box-sizing:border-box}
body{margin:0;background:radial-gradient(1200px 500px at 50% -200px,#3a1410 0%,transparent 70%),var(--bg);
color:var(--text);font:15px/1.5 Inter,system-ui,sans-serif}
main{max-width:1100px;margin:0 auto;padding:28px 16px 48px}
a{color:var(--gold);text-decoration:none}a:hover{text-decoration:underline}
h1,h2,h3{font-family:Cinzel,Georgia,serif;margin:0;letter-spacing:.02em}
h1{font-size:clamp(28px,6vw,44px);color:var(--gold)}
.eyebrow{margin:0 0 4px;color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.12em}
.muted{color:var(--muted);margin:0}
.top{display:flex;justify-content:space-between;align-items:flex-end;gap:12px 16px;flex-wrap:wrap;border-bottom:1px solid var(--line);padding-bottom:14px}
.top-right{display:flex;flex-direction:column;align-items:flex-end;gap:6px}
.built{margin:0;color:var(--muted);font-size:13px}
.flags{display:flex;gap:8px;flex-wrap:wrap}
.flag{padding:3px 12px;border-radius:999px;background:var(--panel2);border:1px solid var(--line);font-weight:600;font-size:13px}
.flag-ok{color:var(--ok);border-color:#2f5a32}.flag-bad{color:var(--bad);border-color:#6b2a24}
.strip{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:18px 0}
.pm{display:grid;grid-template-columns:44px 1fr;grid-template-rows:auto auto auto;column-gap:10px;align-items:center;
background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:10px;color:var(--text)}
.pm:hover{text-decoration:none;border-color:var(--cls)}
.pm .avatar{grid-row:1/4;width:44px;height:44px;border-radius:50%;border:2px solid var(--cls,var(--line));background:var(--panel2)}
.pm-name{font-weight:600;font-size:14px}.pm-name b{color:var(--gold);font-family:Cinzel,serif}
.pm-sub{font-size:12px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.pm .xp{margin:2px 0}
.pm-empty{border-style:dashed;background:transparent;color:var(--muted)}.pm-empty .avatar{border:1px dashed var(--line);background:transparent}
.pm-empty .pm-name{grid-row:1/4;font-weight:400}
.hero{display:grid;grid-template-columns:minmax(0,3fr) minmax(0,2fr);gap:14px;margin-bottom:8px}
@media(max-width:760px){.hero{grid-template-columns:minmax(0,1fr)}}
.next{background:linear-gradient(135deg,#3b1612,#22140f);border:1px solid #6b2a24;border-radius:14px;padding:18px 20px}
.next h2{font-size:clamp(22px,4vw,30px)}.next-meta{margin:4px 0 12px;color:var(--text)}
.later{list-style:none;margin:0;padding:0;display:grid;gap:4px;font-size:14px}.later small{color:var(--muted)}
.news{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px 20px}
.news ul{list-style:none;margin:0;padding:0;display:grid;gap:6px;font-size:14px}
.news .who-n{color:var(--cls);margin-right:4px}.news small{color:var(--muted)}
.sec{font-size:20px;color:var(--gold);margin:30px 0 12px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:14px}
.cards>div{display:flex}
.card{flex:1;background:var(--panel);border:1px solid var(--line);border-top:3px solid var(--cls);border-radius:12px;padding:14px;display:flex;flex-direction:column;gap:10px}
.card-err{border-top-color:var(--bad)}
.card-head{display:flex;gap:12px;align-items:center}
.avatar{width:48px;height:48px;border-radius:10px;border:2px solid var(--cls);background:var(--panel2);flex:none}
.who{flex:1;min-width:0}.who h3{font-size:16px;overflow-wrap:anywhere}.who h3 a{color:var(--text)}
.cls{margin:0;color:var(--muted);font-size:13px}.cls b{color:var(--cls);font-weight:600}
.lvl{font:700 30px Cinzel,serif;color:var(--gold)}
.meta{display:flex;flex-wrap:wrap;gap:6px;align-items:center;font-size:13px}.meta small{color:var(--muted)}
.chip{padding:1px 8px;border-radius:999px;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.05em}
.chip-tank{background:#1f3550;color:#8cc4ff}.chip-heal{background:#1f4224;color:#8fe39a}.chip-dps{background:#4a1f1b;color:#ff9b8f}
.chip-none{background:var(--panel2);color:var(--muted)}.chip-new{background:#3b2a5c;color:#c9b2ff;margin-left:8px;vertical-align:middle}
.bar{display:block;height:4px;background:var(--panel2);border-radius:2px;overflow:hidden}
.bar span{display:block;height:100%;background:linear-gradient(90deg,#7b4bd1,#b58cff)}
.xp{display:grid;gap:3px;font-size:12px}.xp small{color:var(--muted)}
.gear{font-size:13px;display:grid;gap:3px;border-top:1px solid var(--line);padding-top:8px}.gear p{margin:0}
.gear small{color:var(--muted)}.gear-gap{color:var(--warn)}
.gear summary{cursor:pointer;color:var(--muted);font-size:12px;margin-top:2px}
.gear-list{list-style:none;margin:6px 0 0;padding:0;display:grid;gap:2px}
.gear-list li{display:grid;grid-template-columns:62px 1fr auto;gap:6px}.gear-list li>span{color:var(--muted)}
.pet{margin:0;font-size:13px}.pet small{color:var(--muted)}
.updated{margin:auto 0 0;color:var(--muted);font-size:12px}
.rc{margin-bottom:12px}.rc-head{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:12px}
.rc-head h3{font-size:18px}.rc-source{margin-left:auto;font-size:13px}
.rc-table tbody th a{color:var(--text)}.rc-table tr.q-none th a{color:var(--muted)}
.rc-table tbody th small{display:block;color:var(--muted);font-weight:400;font-size:12px}
.q-name{display:flex;flex-wrap:wrap;align-items:center;gap:6px}
.share{font-size:11px;font-weight:600;padding:1px 8px;border-radius:999px;white-space:nowrap}
.share-yes{background:#16324d;color:var(--share)}.share-in{background:#3b2a5c;color:#c9b2ff}.share-no{background:#4a3a14;color:var(--warn)}
.cnt{font-size:12px;font-weight:600;border-radius:999px;padding:1px 8px;white-space:nowrap}
.cnt.big{font-size:13px;padding:3px 10px}
.cnt-share{background:#16324d;color:var(--share)}.cnt-take{background:#4a3a14;color:var(--warn)}.cnt-ok{background:#1f4224;color:#8fe39a}
.rc-picker{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 16px}
.rc-picker button{font:600 14px Inter,system-ui,sans-serif;color:var(--text);background:var(--panel);border:1px solid var(--line);
border-radius:999px;padding:6px 8px 6px 14px;cursor:pointer;display:flex;align-items:center;gap:6px}
.rc-picker button[aria-selected=true]{border-color:var(--gold);background:#2c2014;color:var(--gold)}
.todos{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px;margin-bottom:14px}
.todo{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--cls);border-radius:0 10px 10px 0;padding:10px 12px}
.todo h4{margin:0 0 6px;color:var(--cls);font-size:14px}
.todo ul{list-style:none;margin:0;padding:0;display:grid;gap:4px;font-size:13px}
.todo small{display:block;color:var(--muted);font-size:12px}
.t-take{color:var(--warn)}.t-take b{color:var(--text)}.t-get{color:var(--share)}.t-give{color:#8fe39a}.t-ok{color:var(--ok)}.t-low{color:var(--muted)}
.table-wrap{overflow-x:auto;border:1px solid var(--line);border-radius:12px}
table.dungeons{width:100%;border-collapse:collapse;font-size:14px;min-width:520px}
.dungeons th,.dungeons td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left}
.dungeons thead th{background:var(--panel2);font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
.dungeons tbody th{font-weight:600}.dungeons tbody th small{display:block;color:var(--muted);font-weight:400;font-size:12px}
.dungeons tr:last-child>*{border-bottom:0}
.who-col{color:var(--cls)!important;text-align:center!important}
.range{color:var(--gold);white-space:nowrap}
.st{text-align:center!important;font-weight:600;white-space:nowrap}
.st-fit{color:var(--ok)}.st-share{color:var(--share);font-size:17px}.st-take{color:var(--warn)}.st-over{color:var(--muted)}.st-pre{color:#9fd3a3;font-size:12px}
.legend .st-share{font-size:inherit}
tr.best{background:#2c1611}tr.best th:first-child{box-shadow:inset 3px 0 var(--red)}
.note{color:var(--muted);font-size:13px}
.prof-grid{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:8px}
.prof-grid+.prof-grid{margin-top:8px}
.pt{border:1px solid var(--line);border-radius:10px;padding:8px 12px;display:flex;flex-direction:column;font-size:13px;background:var(--panel)}
.pt b{font-size:14px}.pt small{color:var(--gold)}
.pt-has{border-color:#2f5a32}.pt-has b{color:#8fe39a}
.pt-dup{border-color:#6b5420}.pt-dup b{color:var(--warn)}
.pt-none{background:transparent;color:var(--muted)}.pt-none b{color:var(--muted);font-weight:500}
.pt-sec{border-style:dashed}.pt-sec b{color:var(--text)}
.all-dungeons{margin-top:30px}.all-dungeons summary{cursor:pointer;color:var(--gold);font:600 16px Cinzel,serif;margin-bottom:10px}
footer{margin-top:36px;padding-top:16px;border-top:1px solid var(--line);color:var(--muted);font-size:12px}
code{background:var(--panel2);padding:1px 5px;border-radius:4px}
.sub{font-size:15px;color:var(--muted);margin:18px 0 10px}
.titles,.counters{list-style:none;margin:0;padding:0;display:grid;gap:10px}
.titles{grid-template-columns:repeat(auto-fill,minmax(200px,1fr))}
.title{background:linear-gradient(160deg,#2c2014,var(--panel));border:1px solid #5a4422;border-radius:12px;padding:12px 14px;
display:grid;grid-template-columns:auto 1fr;column-gap:12px;align-items:center}
.t-emoji{grid-row:1/4;font-size:30px;line-height:1}
.t-who{color:var(--cls);font-weight:600;font-size:13px}
.t-name{font:700 16px Cinzel,Georgia,serif;color:var(--gold)}
.title small{color:var(--muted);font-size:12px}
.counters{grid-template-columns:repeat(auto-fill,minmax(150px,1fr))}
.ctr{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 12px;display:flex;flex-direction:column;font-size:13px}
.ctr b{font:700 22px Cinzel,Georgia,serif;color:var(--text)}
.c-emoji{font-size:18px}.ctr small{color:var(--muted);font-size:11px;margin-top:2px}
.ctr-hit{border-color:var(--gold);background:#2c2014}.c-hit{color:var(--gold)!important;font-weight:600}
.counters-box{margin-top:14px}.counters-box summary{cursor:pointer;color:var(--muted);font-size:14px;margin-bottom:10px}
.counters-box summary b{color:var(--text)}
.k-latest,.k-entry{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--gold);border-radius:0 12px 12px 0;padding:16px 20px}
.k-latest .k-entry{background:none;border:0;padding:0}
.k-list{display:grid;gap:14px;margin-top:20px}
.k-entry h3{font-size:20px;color:var(--gold);margin-bottom:6px}
.k-party{margin:0 0 8px;display:flex;gap:10px;font-size:13px}.k-who{color:var(--cls);font-weight:600}
.k-text p{margin:0 0 10px;font-family:Georgia,serif;font-size:16px;line-height:1.65}
.k-text p:first-child::first-letter{font:700 2.4em/1 Cinzel,serif;float:left;margin:4px 6px 0 0;color:var(--gold)}
.k-facts{margin:8px 0 0;padding:0;list-style:none;display:flex;flex-wrap:wrap;gap:6px}
.k-facts li{font-size:12px;background:var(--panel2);border:1px solid var(--line);border-radius:999px;padding:2px 10px;color:var(--muted)}
.k-more{margin:6px 0 0;font-size:14px}.k-empty{font-style:italic}
"""

JS = """
const tabs=[...document.querySelectorAll('.rc-picker button')];
function pick(i){tabs.forEach(b=>b.setAttribute('aria-selected',b.dataset.rc==i));
  document.querySelectorAll('.rc').forEach(a=>a.hidden=a.id!=='rc-'+i);
  try{localStorage.setItem('rc-pick',tabs[i].dataset.title)}catch(e){}}
tabs.forEach(b=>b.addEventListener('click',()=>pick(+b.dataset.rc)));
try{const saved=localStorage.getItem('rc-pick');const t=tabs.find(b=>b.dataset.title===saved);if(t)pick(+t.dataset.rc)}catch(e){}
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
    base = load_baseline(party, built)
    catalog_titles = json.loads((ROOT / "titles.json").read_text(encoding="utf-8"))
    titles = evening_titles(ok, base, local_day(built), catalog_titles)
    counters = party_counters(ok, base)

    kronika = ROOT / "kronika.json"
    chronicle = sorted(json.loads(kronika.read_text(encoding="utf-8")).get("entries") or [] if kronika.exists() else [],
                       key=lambda x: x["date"], reverse=True)

    OUT.mkdir(exist_ok=True)
    (OUT / "kronika.html").write_text(render_chronicle(party, chronicle, ok), encoding="utf-8")
    # denní snímek pro history/, ranní běh na GitHubu ho commitne (podklad pro kroniku)
    (OUT / "snapshot.json").write_text(json.dumps(
        {"date": local_day(built).isoformat(), "built": built,
         "members": {m["slug"]: {"name": m["name"], **snapshot(m)} for m in ok}},
        ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "index.html").write_text(render(party, members, rows, best, built, checks, base, titles, counters, chronicle), encoding="utf-8")
    public = [{k: v for k, v in m.items() if k != "quests_done"} for m in members]
    (OUT / "party.json").write_text(json.dumps({"built": built, "members": public, "ready_check": checks,
                                                "baseline": base,
                                                "titles": {s: t["key"] for s, t in titles.items()}}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Hotovo: {len(ok)}/{len(members)} postav, nejlepší dungeon: {best['name'] if best else '-'}, "
          f"ready check: {len(checks)} dungeonů")


if __name__ == "__main__":
    main()
