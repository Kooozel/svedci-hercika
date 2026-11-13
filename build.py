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


# Forever API u předmětu nevypíše spell power caster zbraní, ve hře ale platí (docs/staty-forever.md, kap. 1.1)
WEAPON_SPELL = {272996: {"SPELL_HEALING_DONE": 33, "SPELL_DAMAGE_DONE": 11},  # Trogg Scepter
                251534: {"SPELL_POWER": 26}}  # Gnarled Necromancer's Staff


def item_stats(i):
    """Staty předmětu z API (nošený kus nebo preview_item): {"STRENGTH": 3, "ARMOR": 53, "DPS": 11.8}.
    Náhodný suffix je u nošeného kusu už ve stats."""
    out = {}
    for s in i.get("stats") or []:
        t = s["type"]["type"]
        out[t] = out.get(t, 0) + s["value"]
    if a := (i.get("armor") or {}).get("value"):
        out["ARMOR"] = a
    if b := (i.get("shield_block") or {}).get("value"):
        out["BLOCK"] = b
    if d := ((i.get("weapon") or {}).get("dps") or {}).get("value"):
        out["DPS"] = round(d, 1)
    for k, v in WEAPON_SPELL.get((i.get("item") or {}).get("id"), {}).items():
        out.setdefault(k, v)
    return out


def gear_entry(i):
    """Nošený kus z /equipment pro stránku a porovnání upgradů."""
    g = {"slot": i["slot"]["type"], "name": i["name"], "quality": i["quality"]["type"],
         "level": (i.get("level") or {}).get("value"), "inv": i["inventory_type"]["type"],
         "id": i["item"]["id"], "sub": (i.get("item_subclass") or {}).get("name"),
         "enchanted": any((x.get("enchantment_slot") or {}).get("type") == "PERMANENT"
                          for x in i.get("enchantments") or []),
         "stats": item_stats(i)}
    if i.get("bonus_list"):
        g["bonus"] = i["bonus_list"]
    return g


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
    extra = {"recipes": [{"prof": p["profession"]["name"], "id": r["id"], "name": r["name"]}
                         for g in ("primaries", "secondaries") for p in profs.get(g) or []
                         for t in p.get("tiers") or [] for r in t.get("known_recipes") or []
                         if r["name"] != p["profession"]["name"]]}  # API vrací i samotnou profesi (Fishing)
    assets = {a["key"]: a["value"] for a in get("/character-media").get("assets") or []}
    quests = get("/quests")
    done = get("/quests/completed").get("quests") or []
    login = c.get("last_login_timestamp")
    try:  # výbava a pet jsou bonus, kvůli nim nepadáme na Armory
        extra["gear"] = [gear_entry(i) for i in get("/equipment").get("equipped_items") or []]
    except Exception as exc:
        print(f"{slug}: výbava ({exc})", file=sys.stderr)
    try:  # statistiky jsou pro tituly a počítadla, bez nich stránka jede dál
        extra["stats"] = flat_stats(get("/achievements/statistics"))
    except Exception as exc:
        print(f"{slug}: statistiky ({exc})", file=sys.stderr)
    try:  # staty postavy (stamina, armor, crit…) pro kartu postavy
        extra["cstats"] = char_stats(get("/statistics"))
    except Exception as exc:
        print(f"{slug}: staty postavy ({exc})", file=sys.stderr)
    try:  # průzkum zón pro titul Kartograf a dokončené achievementy do novinek
        extra["achievements"], explore = achievements(get("/achievements"))
        if "stats" in extra:
            extra["stats"].update(explore)
    except Exception as exc:
        print(f"{slug}: achievementy ({exc})", file=sys.stderr)
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
        avatar=assets.get("avatar"), render=assets.get("main-raw"), cls_id=c["character_class"]["id"],
        updated=to_day(login),
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


def char_stats(d):
    """Staty z /statistics zploštěné na čísla; u atributů (base, effective)."""
    def val(k):
        v = d.get(k)
        return v.get("value") if isinstance(v, dict) else v
    def signed(v):  # API občas vrací záporné číslo jako uint32 (base armor 4294967288 = −8)
        return v - 2 ** 32 if isinstance(v, int) and v >= 2 ** 31 else v
    # base drží jen sick(); u armoru je to armor postavy bez výbavy, na kartě se ukazuje rozdíl od včerejška
    out = {k: (signed(d[k]["base"]), signed(d[k]["effective"])) for k in
           ("strength", "agility", "stamina", "intellect", "spirit", "armor", "defense") if isinstance(d.get(k), dict)}
    out.update({k: val(k) for k in ("health", "power", "attack_power", "ranged_attack_power", "main_hand_dps",
                                     "spell_power", "spell_healing", "mana_regen", "melee_crit", "spell_crit",
                                     "ranged_crit", "dodge", "parry", "block") if val(k) is not None})
    out["power_type"] = (d.get("power_type") or {}).get("name")
    return out


def achievements(data):
    """Vrátí (dokončené achievementy, průzkum). Průzkum: _explore = objevená místa celkem,
    _explore:Zóna = objevená místa v zóně (z kritérií achievementů Explore …)."""
    done, explore = [], {"_explore": 0}
    for a in data.get("achievements") or []:
        name = (a.get("achievement") or {}).get("name") or ""
        if a.get("completed_timestamp"):
            done.append(name)
        if name.startswith("Explore "):
            n = sum(1 for c in (a.get("criteria") or {}).get("child_criteria") or [] if c.get("is_completed"))
            explore["_explore:" + name.removeprefix("Explore ")] = n
            explore["_explore"] += n
    return sorted(done), explore


_ICONS = {}


def media_icon(token, region, kind, oid):
    """Ikona z Game Data API (media). Chybějící ikona nevadí, item se ukáže bez ní."""
    if (kind, oid) not in _ICONS:
        try:
            assets = api_get(token, region, f"/data/wow/media/{kind}/{oid}", "static").get("assets") or []
            _ICONS[kind, oid] = next((a["value"] for a in assets if a["key"] == "icon"), None)
        except Exception:
            _ICONS[kind, oid] = None
    return _ICONS[kind, oid]


def add_icons(token, region, members):
    """Ikony výbavy a class."""
    def icon(kind, oid):
        return media_icon(token, region, kind, oid)

    for m in members:
        if not m.get("ok") or m.get("source") != "api":
            continue
        for g in m.get("gear") or []:
            g["icon"] = icon("item", g["id"])
        if m.get("cls_id"):
            m["class_icon"] = icon("playable-class", m["cls_id"])


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
        updated=to_day((c.get("lastUpdatedTimestamp") or {}).get("iso8601")),
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


def to_day(ts):
    """Čas přihlášení z API/Armory zkrátí na místní den. Přesný čas na veřejnou stránku nedáváme."""
    if not ts:
        return None
    if isinstance(ts, (int, float)):
        d = datetime.fromtimestamp(ts / 1000, timezone.utc)
    else:
        d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return d.astimezone(TZ).date().isoformat()


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
    # ruční role z party.json přebijí odhad podle specu (Johnie tankuje, i když má body v Holy)
    for m in members:
        role = (party.get("roles") or {}).get(m["slug"])
        if m["ok"] and role:
            m["roles"] = [role] if isinstance(role, str) else list(role)
        if gen := (party.get("genitive") or {}).get(m["slug"]):
            m["gen"] = gen
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
        # odehraný dungeon zůstává jen v seznamu všech, z plánu, ready checku a lootu zmizí
        near = d["max"] >= lo and d["min"] <= hi + 5 and not d.get("done")
        rows.append({**d, "status": status, "fit": fit, "near": near})
    # Víc lidí v rozsahu > vyšší dungeon > užší rozsah
    best = max((r for r in rows if not r.get("done")), key=lambda r: (r["fit"], r["min"], -r["max"]), default=None)
    return rows, best


def ready_check(token, region, rows, best, members, catalog):
    """Dungeonové questy z dungeonů v dosahu a stav každého člena party."""
    with_quests = [m for m in members if "quests_active" in m]
    if not token or not with_quests:
        return []
    active = {m["slug"]: {q["id"] for q in m["quests_active"]} for m in with_quests}
    done = {m["slug"]: set(m["quests_done"]) for m in with_quests}
    levels = {m["slug"]: m["level"] for m in with_quests}
    who = {m["slug"]: m for m in with_quests}
    details = {}

    def quest_detail(qid):
        """XP a omezení na classu nebo rasu z Game Data API (requirements.classes / races)."""
        if qid not in details:
            try:
                d = api_get(token, region, f"/data/wow/quest/{qid}", "static")
            except Exception:
                d = {}
            req = d.get("requirements") or {}
            details[qid] = {"xp": (d.get("rewards") or {}).get("experience"),
                            "classes": [c["name"] for c in req.get("classes") or []],
                            "races": [r["name"] for r in req.get("races") or []]}
        return details[qid]

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
            det = quest_detail(qid)
            # v quests.json jde omezení dopsat ručně, jinak platí to z API
            classes, races = q.get("classes") or det["classes"], q.get("races") or det["races"]
            can = {s for s in active if (not classes or who[s].get("class") in classes)
                   and (not races or who[s].get("race") in races)}
            if not can:
                continue  # classový quest, který v partě nikdo nevezme
            limit = None
            if classes:
                limit = {"text": "jen " + " / ".join(classes), "cls": classes[0].lower(),
                         "why": f"quest je jen pro classu {', '.join(classes)}"}
            elif len(can) < len(active):
                barred = sorted({who[s].get("race") or "?" for s in active if s not in can})
                limit = {"text": "ne pro " + ", ".join(barred), "cls": None,
                         "why": f"rasa {', '.join(barred)} tenhle quest nedostane"}
            status = {}
            for slug in active:
                if slug not in can:
                    status[slug] = "na"
                elif qid in active[slug]:
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
            if all(st in ("done", "na") for st in status.values()):
                continue
            # kdo quest nemá, dostane ho u vchodu od toho, kdo ho má v logu
            if q.get("sharable") and not pre and "active" in status.values():
                status = {s: "share" if st == "missing" else st for s, st in status.items()}
            quests.append({
                "id": qid, "name": q["name"], "xp": det["xp"], "req": q.get("req"), "limit": limit,
                "sharable": q.get("sharable"), "start": q.get("start"), "note": q.get("note"),
                "prev": pre["name"] if pre else None, "status": status,
                "unclaimed": not any(st in ("active", "done", "pre") for st in status.values()),
            })
        # nejdřív questy, které někdo má, pak volné, nakonec ty, na které zatím nikdo nemůže
        # classové questy až za společnými
        quests.sort(key=lambda q: (q["unclaimed"], all(st in ("level", "chain", "na") for st in q["status"].values()),
                                   q["limit"] is not None))
        # ✋ = musí si někam dojít: pro quest, dokončit rozjetý řetěz nebo udělat předquesty
        take = sum(1 for q in quests for st in q["status"].values() if st in ("missing", "pre", "chain"))
        share = sum(1 for q in quests for st in q["status"].values() if st == "share")
        if quests and (r is best or any(not q["unclaimed"] for q in quests)):
            checks.append({"title": title, "min": r["min"], "max": r["max"], "best": r is best,
                           "quests": quests, "members": list(active), "take": take, "share": share,
                           "source": catalog[title].get("source"), "tavern": catalog[title].get("tavern")})
    return checks


# ---------- profese: recepty, upgrady a dodavatelé ----------

class Static:
    """Detaily receptů a předmětů z Game Data API uložené v cache/static.json.
    Ve hře se skoro nemění, takže se stahuje jen to, co v cache ještě není. Smazáním souboru se stáhne znovu."""
    PATH = ROOT / "cache" / "static.json"

    def __init__(self, token, region):
        self.token, self.region, self.dirty = token, region, False
        try:
            self.data = json.loads(self.PATH.read_text(encoding="utf-8"))
        except Exception:
            self.data = {}
        self.data.setdefault("recipes", {})
        self.data.setdefault("items", {})

    def _get(self, kind, oid, fetch):
        store = self.data[kind]
        if str(oid) not in store:
            if not self.token:
                return None
            try:
                store[str(oid)] = fetch(oid)
            except Exception as exc:
                print(f"{kind} {oid}: {exc}", file=sys.stderr)
                return None
            self.dirty = True
        return store[str(oid)]

    def recipe(self, rid):
        def fetch(rid):
            r = api_get(self.token, self.region, f"/data/wow/recipe/{rid}", "static")
            return {"name": r["name"], "item": (r.get("crafted_item") or {}).get("id"),
                    "reagents": [[x["reagent"]["id"], x["quantity"]] for x in r.get("reagents") or []]}
        return self._get("recipes", rid, fetch)

    def item(self, iid):
        def fetch(iid):
            d = api_get(self.token, self.region, f"/data/wow/item/{iid}", "static")
            return {"name": d["name"], "quality": (d.get("quality") or {}).get("type"),
                    "inv": (d.get("inventory_type") or {}).get("type"), "level": d.get("level"),
                    "req": d.get("required_level") or 0, "kind": (d.get("item_class") or {}).get("name"),
                    "sub": (d.get("item_subclass") or {}).get("name"),
                    "icon": media_icon(self.token, self.region, "item", iid)}
        return self._get("items", iid, fetch)

    def save(self):
        if self.dirty:
            self.PATH.parent.mkdir(exist_ok=True)
            self.PATH.write_text(json.dumps(self.data, ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")


# inventory_type předmětu -> sloty, se kterými se porovná
ITEM_SLOTS = {
    "HEAD": ["HEAD"], "NECK": ["NECK"], "SHOULDER": ["SHOULDER"], "CLOAK": ["BACK"], "CHEST": ["CHEST"],
    "ROBE": ["CHEST"], "WRIST": ["WRIST"], "HAND": ["HANDS"], "WAIST": ["WAIST"], "LEGS": ["LEGS"],
    "FEET": ["FEET"], "FINGER": ["FINGER_1", "FINGER_2"], "TRINKET": ["TRINKET_1", "TRINKET_2"],
    "WEAPON": ["MAIN_HAND"], "WEAPONMAINHAND": ["MAIN_HAND"], "TWOHWEAPON": ["MAIN_HAND"],
    "SHIELD": ["OFF_HAND"], "HOLDABLE": ["OFF_HAND"], "WEAPONOFFHAND": ["OFF_HAND"],
    "RANGED": ["RANGED"], "RANGEDRIGHT": ["RANGED"], "THROWN": ["RANGED"],
}
# co který class unese (Classic; mail pro huntera a shamana a plate od levelu 40)
ARMOR_RANK = ["Cloth", "Leather", "Mail", "Plate"]
ARMOR_OK = {"Leather": {"warrior", "paladin", "hunter", "rogue", "shaman", "druid"},
            "Mail": {"warrior", "paladin"}, "Plate": set(), "Shield": {"warrior", "paladin", "shaman"}}
ARMOR_OK_40 = {"Mail": {"hunter", "shaman"}, "Plate": {"warrior", "paladin"}}
WEAPON_OK = {
    "warrior": "dagger sword axe mace polearm staff fist bow gun crossbow thrown",
    "paladin": "sword axe mace polearm", "hunter": "dagger sword axe fist polearm staff bow gun crossbow thrown",
    "rogue": "dagger sword mace fist bow gun crossbow thrown", "priest": "dagger mace staff wand",
    "shaman": "dagger axe mace fist staff", "mage": "dagger sword staff wand", "warlock": "dagger sword staff wand",
    "druid": "dagger mace fist staff polearm",
}


def can_use(item, m):
    cls, sub = m["class_slug"], item.get("sub") or ""
    if item.get("kind") == "Weapon":
        return any(w in sub.lower() for w in WEAPON_OK.get(cls, "").split())
    if sub in ARMOR_OK:
        return cls in ARMOR_OK[sub] or (m["level"] >= 40 and cls in ARMOR_OK_40.get(sub, set()))
    return True  # látka, prsteny, krky, pláště


def best_armor(m):
    return max((a for a in ARMOR_RANK if can_use({"sub": a}, m)), key=ARMOR_RANK.index)


def upgrade(item, m):
    """None = nehodí se, "empty" = má ten slot prázdný, jinak o kolik item levelů je lepší.
    Slabší typ zbroje (látka pro paladina) jen do prázdného slotu."""
    if "gear" not in m or item.get("inv") not in ITEM_SLOTS or not can_use(item, m):
        return None
    if (item.get("req") or 0) > m["level"]:
        return None
    if item["inv"] == "TWOHWEAPON" and m["class_slug"] in CASTERS | {"druid"} and "staff" not in (item.get("sub") or "").lower():
        return None
    worn = {g["slot"]: g for g in m["gear"]}
    slots = ITEM_SLOTS[item["inv"]]
    if slots == ["OFF_HAND"] and (worn.get("MAIN_HAND") or {}).get("inv") == "TWOHWEAPON":
        return None
    if slots == ["RANGED"] and m["class_slug"] in NO_RANGED:
        return None
    if any(s not in worn for s in slots):
        return "empty"
    sub = item.get("sub")
    if sub in ARMOR_RANK and ARMOR_RANK.index(sub) < ARMOR_RANK.index(best_armor(m)):
        return None
    diff = (item.get("level") or 0) - min((worn[s].get("level") or 0) for s in slots)
    return diff if diff > 0 else None


# ---------- bossové a loot (bosses.json) ----------

WEAPON_INV = {"WEAPON", "WEAPONMAINHAND", "WEAPONOFFHAND", "TWOHWEAPON", "RANGED", "RANGEDRIGHT", "THROWN"}
HYBRIDS = {"paladin", "shaman", "druid"}
SCHOOL_FOR = {"Shadow": {"priest", "warlock"}, "Frost": {"mage"}, "Arcane": {"mage"}, "Fire": {"mage", "warlock"},
              "Nature": {"druid", "shaman"}}


def stat_fits(stat, m):
    """Hodí se hlavní stat kusu classe a roli? Bez statu nebo se Staminou ano (Classic: hybridi podle role)."""
    cls, heal = m["class_slug"], "heal" in (m.get("roles") or [])
    if stat == "Junk":  # kus, který nikdo nechce (třeba plášť, co zpomaluje)
        return False
    if stat in SCHOOL_FOR:  # spell damage jen jedné školy (frost pro mága, shadow pro priesta a warlocka)
        return cls in SCHOOL_FOR[stat]
    if stat == "Healing":  # paladin se léčí i jako tank nebo ret, druid a shaman jen v roli healera
        return cls in {"priest", "paladin"} or (cls in HYBRIDS and heal)
    if stat in ("Caster", "Spirit", "Intellect"):
        return cls in CASTERS or (cls in HYBRIDS and heal)
    if stat == "Agility":
        return cls in {"hunter", "rogue"} or (cls in {"druid", "shaman", "warrior"} and not heal)
    if stat == "Defense":
        return cls in {"warrior", "paladin", "druid"} and not heal
    if stat == "Strength":  # hunter a rogue berou agility
        return cls in {"warrior", "paladin", "shaman", "druid"} and not heal
    if stat == "Melee":
        return cls not in CASTERS and not (cls in HYBRIDS and heal)
    return True


def loot_item(raw, static):
    """Kus z bosses.json; jen s id (bez inv) se doplní z API (cache), ručně zapsaná pole mají přednost."""
    # s inv je kus zapsaný ručně (API ho nezná), id je jen pro Wowhead tooltip
    it = dict(static.item(raw["id"]) or {}) if raw.get("id") and not raw.get("inv") else {}
    it.update({k: v for k, v in raw.items() if v is not None})
    it.setdefault("name", "?")
    it.setdefault("kind", "Weapon" if it.get("inv") in WEAPON_INV else "Armor")
    return it


def loot_fit(item, m):
    """Komu se kus hodí. None = ne (classa ho neunese, jiný stat, slabší typ zbroje, caster s 2H bez staffu).
    Jinak gain: "empty" prázdný slot, číslo o kolik ilvl je lepší, None nosí stejné nebo lepší,
    "?" neznámý item level nebo výbava; lvl = kolik levelů chybí na req; second = druhá volba
    (heal nebo spell damage jiné školy, caster z toho využije jen část)."""
    if item.get("inv") not in ITEM_SLOTS or not can_use(item, m):
        return None
    second = False
    if not stat_fits(item.get("stat"), m):
        if (item.get("stat") in SCHOOL_FOR or item.get("stat") == "Healing") and stat_fits("Caster", m):
            second = True
        else:
            return None
    cls, sub = m["class_slug"], item.get("sub") or ""
    if item["inv"] in WEAPON_INV and item.get("stat") in (None, "Stamina") and cls in CASTERS:
        return None  # zbraň bez spell statů caster nepotřebuje, střílí wandem
    if item["inv"] == "TWOHWEAPON" and cls in CASTERS | {"druid"} and "staff" not in sub.lower():
        return None
    if sub in ARMOR_RANK and ARMOR_RANK.index(sub) < ARMOR_RANK.index(best_armor(m)):
        return None
    if ITEM_SLOTS[item["inv"]] == ["RANGED"] and cls in NO_RANGED:
        return None
    if item["inv"] == "HOLDABLE" and "tank" in (m.get("roles") or []):
        return None  # tank si štít za off-hand nevymění
    lvl = max(0, (item.get("req") or 0) - m["level"])
    if "gear" not in m:
        return {"gain": "?", "lvl": lvl, "second": second}
    gain = upgrade(item, {**m, "level": max(m["level"], item.get("req") or 0)})
    if gain is None and not item.get("level"):
        gain = "?"
    return {"gain": gain, "lvl": lvl, "second": second}


def boss_loot(rows, best, members, static, catalog):
    """Bossové, taktika a loot pro dungeony v dosahu (nejdřív příští výprava) a komu se který kus hodí."""
    out, seen = [], set()
    for r in sorted((r for r in rows if r["near"]), key=lambda r: r is not best):
        title = r["name"] if r["name"] in catalog else r["name"].split(":")[0]
        if title not in catalog or title in seen:
            continue
        seen.add(title)
        d = catalog[title]

        def items(raws):
            res = []
            for raw in raws:
                it = loot_item(raw, static)
                fits = {m["slug"]: f for m in members if (f := loot_fit(it, m))}
                res.append({"item": it, "fits": fits})
            return res

        out.append({"title": title, "min": r["min"], "max": r["max"], "best": r is best,
                    "source": d.get("source"), "tactics": d.get("tactics"), "note": d.get("note"),
                    "sets": d.get("sets") or {},
                    "bosses": [{"name": b["name"], "tip": b.get("tip"), "rare": b.get("rare"), "trash": b.get("trash"),
                                "items": items(b.get("loot") or [])} for b in d.get("bosses") or []],
                    "quests": [{"quest": q["quest"], "items": items(q.get("loot") or [])}
                               for q in d.get("quests") or []]})
    return out


# odkud se surovina bere: druh předmětu -> sběrná profese (cloth, maso a vendor věci se neukazují)
GATHER = {"Leather": "Skinning", "Metal & Stone": "Mining", "Herb": "Herbalism", "Enchanting": "Enchanting"}
ENCHANT_SLOT = {"Chest": ["CHEST"], "Bracer": ["WRIST"], "Boots": ["FEET"], "Cloak": ["BACK"], "Gloves": ["HANDS"],
                "Shield": ["OFF_HAND"], "2H Weapon": ["MAIN_HAND"], "Weapon": ["MAIN_HAND"]}
SLOT_CZ = {"CHEST": "hruď", "WRIST": "zápěstí", "FEET": "boty", "BACK": "plášť", "HANDS": "ruce",
           "OFF_HAND": "štít", "MAIN_HAND": "zbraň"}


def crafting(members, static):
    """Co se vyplatí vyrobit: upgrady pro partu (nejlepší kus na slot), suroviny od jiných členů party,
    enchanty pro kusy bez enchantu a profese na stropu skillu."""
    by_slug = {m["slug"]: m for m in members}
    makers, has_prof = {}, {}
    for m in members:
        for p in m.get("professions") or []:
            has_prof.setdefault(p["name"], []).append(m["slug"])
        for r in m.get("recipes") or []:
            rec = static.recipe(r["id"])
            if rec and rec["item"]:
                makers.setdefault(rec["item"], set()).add(m["slug"])

    def supplier(iid, crafter):
        """Kdo z party surovinu dodá; None = neřešit (udělá si sám, vendor, padá z mobů)."""
        if crafter in makers.get(iid, ()):
            return None
        if makers.get(iid):
            return sorted(makers[iid])
        it = static.item(iid)
        prof = GATHER.get((it or {}).get("sub"))
        if not prof or crafter in has_prof.get(prof, []):
            return None
        return sorted(has_prof.get(prof, [])) or [f"nikdo ({prof})"]

    best = {}
    for m in members:
        for r in m.get("recipes") or []:
            rec = static.recipe(r["id"])
            if not rec or not rec["item"]:
                continue
            it = static.item(rec["item"])
            if not it:
                continue
            for t in members:
                d = upgrade(it, t)
                if d is None:
                    continue
                key = (t["slug"], ITEM_SLOTS[it["inv"]][0])
                score = (it.get("level") or 0, t["slug"] == m["slug"])
                if key not in best or score > best[key]["score"]:
                    reagents = []
                    for iid, qty in rec["reagents"]:
                        who = supplier(iid, m["slug"])
                        if who:
                            reagents.append({"name": (static.item(iid) or {}).get("name", "?"), "qty": qty, "from": who})
                    best[key] = {"score": score, "item": {**it, "id": rec["item"]}, "crafter": m["slug"],
                                 "target": t["slug"], "upgrade": d, "reagents": reagents,
                                 "loses": next((g["name"] for g in t.get("gear") or []
                                                if g["slot"] == key[1] and g.get("enchanted")), None)}
    ups = sorted(best.values(), key=lambda u: (u["upgrade"] != "empty", u["target"], -(u["item"].get("level") or 0)))

    enchants = {}
    for m in members:
        for r in m.get("recipes") or []:
            name = r["name"]
            if not name.startswith("Enchant ") or " - " not in name:
                continue
            slot_word, effect = name.removeprefix("Enchant ").split(" - ", 1)
            for slot in ENCHANT_SLOT.get(slot_word, []):
                e_ = enchants.setdefault(slot, {"effects": set(), "by": set(), "targets": []})
                e_["effects"].add(effect)
                e_["by"].add(m["slug"])
    for slot, e_ in enchants.items():
        e_["targets"] = [t["slug"] for t in members
                         if any(g["slot"] == slot and not g.get("enchanted") for g in t.get("gear") or [])]
    enchants = {s: v for s, v in enchants.items() if v["targets"]}

    capped = [(m["slug"], p["name"], p["skill"]) for m in members for p in m.get("professions") or []
              if p["primary"] and p["max"] and p["skill"] >= p["max"] and p["max"] < 300]
    return {"ups": ups, "enchants": enchants, "capped": capped, "names": {s: first(m) for s, m in by_slug.items()}}


# ---------- co se změnilo od včerejška ----------

def snap_attrs(m):
    """Čísla z karty postavy pro rozdíl od včerejška: u atributů effective, jinak hodnota."""
    out = {}
    for k, v in (m.get("cstats") or {}).items():
        v = v[1] if isinstance(v, (tuple, list)) else v
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[k] = v
    return out


def snapshot(m):
    return {"level": m["level"], "xp": m.get("xp"),
            "gear": [g["name"] for g in m["gear"]] if "gear" in m else None,
            "profs": {p["name"]: p["skill"] for p in m.get("professions") or []},
            "stats": m.get("stats"), "achievements": m.get("achievements"), "updated": m.get("updated"),
            "attrs": snap_attrs(m), "sick": sick(m) if m.get("cstats") else None,
            "recipes": sorted({r["name"] for r in m["recipes"]}) if "recipes" in m else None}


def local_day(ts):
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone(TZ).date()


HISTORY = ROOT / "history"


def load_history(today=None):
    """Denní snímky z history/ seřazené podle data; s today jen ty před dneškem."""
    out = []
    for f in sorted(HISTORY.glob("*.json")):
        if today and f.stem >= today.isoformat():
            continue
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception as exc:
            print(f"{f.name}: {exc}", file=sys.stderr)
    return out


def load_baseline(party, built):
    """Stav před včerejším večerem: poslední ranní snímek z history/ před dneškem.
    Je v repu, takže ho večerní push neposune. Bez historie záloha přes party.json z nasazené stránky."""
    past = load_history(local_day(built))
    if past:
        snap = past[-1]
        if any("attrs" not in b for b in snap["members"].values()):
            add_attrs(snap["members"], fetch_deployed(party))
        return {"at": snap["built"], "date": snap["date"], "titles": snap.get("titles") or {},
                "members": snap["members"]}
    prev = fetch_deployed(party)
    if not prev:
        return None
    if local_day(prev["built"]) == local_day(built) and prev.get("baseline"):
        return {"date": local_day(prev["baseline"]["at"]).isoformat(), **prev["baseline"]}
    return {"at": prev["built"], "date": local_day(prev["built"]).isoformat(), "titles": prev.get("titles") or {},
            "members": {m["slug"]: snapshot(m) for m in prev.get("members") or [] if m.get("ok")}}


def fetch_deployed(party):
    """party.json z nasazené stránky, None když není kde ho vzít."""
    url = party.get("site_url")
    if not url:
        return None
    try:
        return http_json(urllib.request.Request(url.rstrip("/") + "/party.json", headers={"User-Agent": USER_AGENT}))
    except Exception as exc:
        print(f"Včerejší stav: {exc}", file=sys.stderr)
        return None


def add_attrs(members, prev):
    """Starší snímky v history/ nemají staty karty, doplní se z nasazeného party.json."""
    for m in (prev or {}).get("members") or []:
        b = members.get(m.get("slug"))
        if b is not None and "attrs" not in b and m.get("cstats"):
            b["attrs"], b["sick"] = snap_attrs(m), sick(m)


def xp_total(level, xp):
    return sum(XP_TO_NEXT[:level - 1]) + (xp or 0)


NEWS_STATS = ("armor", "strength", "agility", "stamina", "intellect", "spirit")


def changes(m, base):
    """Co se u postavy změnilo od výchozího stavu; None = výchozí stav postavu nezná."""
    b = ((base or {}).get("members") or {}).get(m["slug"])
    if not b:
        return None
    gained = (xp_total(m["level"], m["xp"]) - xp_total(b["level"], b["xp"])
              if m.get("xp") is not None and b.get("xp") is not None and m["level"] <= len(XP_TO_NEXT) else 0)
    profs = []
    for p in m.get("professions") or []:
        old = b["profs"].get(p["name"])
        if old is None:
            profs.append((p["name"], None))
        elif p["skill"] > old:
            profs.append((p["name"], p["skill"] - old))
    gear = ([g for g in m["gear"] if g["slot"] not in GEAR_SKIP and g["name"] not in b["gear"]]
            if b.get("gear") is not None and "gear" in m else [])
    ach = ([a for a in m.get("achievements") or [] if a not in b["achievements"]]
           if b.get("achievements") is not None else [])
    learned = (sorted({r["name"] for r in m.get("recipes") or []} - set(b["recipes"]))
               if b.get("recipes") is not None else [])
    old, now = b.get("attrs"), snap_attrs(m)
    stats = ([(k, now[k] - old[k]) for k in NEWS_STATS if k in now and k in old and now[k] != old[k]]
             if gear and old and not b.get("sick") and not (m.get("cstats") and sick(m)) else [])
    return {"from": b["level"], "to": m["level"], "xp": max(0, gained), "profs": profs, "gear": gear, "ach": ach,
            "learned": learned, "stats": stats}


# ---------- tituly a počítadla party ----------

def pl(n, forms):
    """Česká množná čísla: forms = (1, 2–4, 5+), "{n}" se nahradí číslem."""
    form = forms[0] if n == 1 else forms[1] if 2 <= n <= 4 else forms[2]
    return form.replace("{n}", num(n))


# počítadla za celou partu: statistika, emoji, popisek
COUNTERS = [
    ("Total deaths", "🪦", "smrtí"), ("Quests completed", "📜", "questů"),
    ("Creatures killed", "⚔️", "padlých nepřátel"), ("Food eaten", "🍗", "snědených jídel"),
    ("Beverages consumed", "🍺", "vypitých nápojů"), ("Flight paths taken", "🦇", "letů wyvernou"),
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
    info = {"skill": None, "firsts": set(), "zone": None, "new": False}
    if has_daily(base):
        if not b:  # nový člen party: bez včerejška by celoživotní součty vypadaly jako jeden večer
            info["new"] = True
            return {}, True, info
        old = b.get("stats") or {}
        # vlastní klíče (_explore…) jen když je měl i včerejšek, jinak by první den vypadal jako obří skok
        vals = {k: v - old.get(k, 0) for k, v in now.items()
                if v > old.get(k, 0) and (not k.startswith("_") or k in old)}
        zones = {k: v for k, v in vals.items() if k.startswith("_explore:")}
        if zones:
            info["zone"] = max(zones, key=zones.get).removeprefix("_explore:")
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
        vals = {k: v for k, v in vals.items() if not k.startswith("_explore:")}
        # přihlášení se počítá jen na dny, přesný čas na stránku nedáváme
        played = bool(vals) or bool(m.get("updated") and base.get("date") and m["updated"] >= base["date"])
        return vals, played, info
    return dict(now), True, info


def pick_variant(variants, today, slug):
    """Varianta podle dne a postavy: každý den jiná, ale při opakovaném buildu stejná."""
    return variants[(today.toordinal() + zlib.crc32(slug.encode())) % len(variants)]


# kolik z nejlepšího výkonu party stačí na titul za statistiku
LEAD = 0.75


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
            # titul je superlativ („nejvíc ve vzduchu“): jen pro toho, kdo ve statistice vede nebo je těsně za ním
            if v >= rule["min"] and v >= LEAD * top and played[s] and yesterday.get(s) != stat:
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
        # premiéra a objev jen jednou za den, ať nejsou čtyři „Konečně!“
        group = stat.split(":")[0] if stat.startswith("_first:") else "_disc" if rules[stat].get("discovery") else stat
        if s in out or stat in used or group in used:
            continue
        rule = rules[stat]
        t = pick_variant(rule["titles"], today, s)
        if rule.get("discovery"):
            line = t["line"].replace("{stat}", stat).replace("{n}", num(v))
        else:
            line = (t["line"].replace("{v}", pl(v, rule["forms"])).replace("{skill}", info[s]["skill"] or "")
                    .replace("{zone}", info[s]["zone"] or "Azerothu"))
        out[s] = {"key": stat, "emoji": rule["emoji"], "title": t["name"], "detail": line}
        used |= {stat, group}
    for m in with_stats:
        s = m["slug"]
        if s in out:
            continue
        # hrál a něco nahrál, jen v ničem nevedl: „nic se nestalo“ by nebyla pravda
        busy = played[s] and values[s] and catalog.get("busy")
        group = "newcomers" if info[s]["new"] else "busy" if busy else "quiet" if played[s] else "sleepers"
        pool = [(f"{group}:{i}", t) for i, t in enumerate(catalog.get(group) or [])
                if f"{group}:{i}" != yesterday.get(s)] or [(group, {"emoji": "🙂", "name": "Hrdina", "line": ""})]
        key, t = pick_variant(pool, today, s)
        out[s] = {"key": key, "emoji": t["emoji"], "title": t["name"], "detail": t["line"]}
    return out


def chronicle_titles(chronicle, base, today, slugs):
    """Tituly napsané ručně u zápisu v kronice: platí jen první den po výpravě (večer, za který se tituly dávají)."""
    since = (base or {}).get("date")
    if not since:  # bez včerejšího stavu nevíme, který večer to byl
        return {}
    out = {}
    for entry in chronicle:
        if since <= entry["date"] < today.isoformat():
            for s, t in (entry.get("titles") or {}).items():
                if s in slugs and s not in out:
                    out[s] = {"key": "_kronika", "emoji": t.get("emoji", "📖"), "title": t["title"],
                              "detail": t.get("detail", "")}
    return out


def party_counters(members, base):
    olds = base["members"] if has_daily(base) else {}
    out = []
    for stat, emoji, label in COUNTERS:
        total = sum((m.get("stats") or {}).get(stat, 0) for m in members)
        # nový člen bez včerejška se do přírůstku nepočítá, jen do součtu
        before = total - sum((m.get("stats") or {}).get(stat, 0)
                             - ((olds[m["slug"]] or {}).get("stats") or {}).get(stat, 0)
                             for m in members if m["slug"] in olds) if olds else None
        crossed = last_milestone(total) if before is not None and last_milestone(total) > before else None
        out.append({"emoji": emoji, "label": label, "total": total,
                    "delta": total - before if before is not None else None,
                    "next": milestones(total), "crossed": crossed})
    return out


def date_ord(iso):
    return datetime.strptime(iso, "%Y-%m-%d").toordinal()


# ---------- HTML ----------

def e(value):
    return html.escape(str(value), quote=True)


def num(n):
    return f"{n:,}".replace(",", " ")


def first(m):
    return m["name"].split()[0]


def first_gen(m):
    """Druhý pád křestního jména (od Zrzy), z party.json; bez něj první pád."""
    return m.get("gen") or first(m)


def cls_color(m):
    return CLASS_COLOR.get(m.get("class_slug"), "#ccc")


def role_chips(roles):
    if not roles:
        return '<span class="chip chip-none">bez specu</span>'
    return "".join(f'<span class="chip chip-{r}">{ROLE_LABEL[r]}</span>' for r in roles)


def item_link(g, compact=False):
    """Odkaz na Wowhead (s tooltipem). compact = jen ikona, když nějaká je."""
    color = QUALITY_COLOR.get(g["quality"], "#ccc")
    href = f'https://www.wowhead.com/forever/item={g["id"]}'
    tip = " · ".join(str(x) for x in (g["name"], g.get("level") and f'ilvl {g["level"]}', g.get("sub")) if x)
    if compact and g.get("icon"):
        return (f'<a class="item-ic" href="{href}" target="_blank" rel="noopener" title="{e(tip)}">'
                f'<img src="{e(g["icon"])}" alt="{e(g["name"])}" style="border-color:{color}"></a>')
    icon = (f'<img class="ic" src="{e(g["icon"])}" alt="" style="border-color:{color}">'
            if g.get("icon") else "")
    return (f'<a class="item" style="color:{color}" title="{e(tip)}" '
            f'href="{href}" target="_blank" rel="noopener">{icon}{e(g["name"])}</a>')


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


# rozložení jako okno postavy ve hře: levý a pravý sloupec, zbraně dole
DOLL_LEFT = ["HEAD", "NECK", "SHOULDER", "BACK", "CHEST", "WRIST", "HANDS"]
DOLL_RIGHT = ["WAIST", "LEGS", "FEET", "FINGER_1", "FINGER_2", "TRINKET_1", "TRINKET_2"]
DOLL_BOTTOM = ["MAIN_HAND", "OFF_HAND", "RANGED"]


def doll_slot(slot, worn, weakest, m):
    g = worn.get(slot)
    label = SLOT_LABEL.get(slot, slot.lower())
    if g:
        color = QUALITY_COLOR.get(g["quality"], "#ccc")
        # když se ikona nenačte, zůstanou vidět první písmena názvu
        inner = f'<span class="sl-txt">{e(g["name"][:2])}</span>' + (
            f'<img src="{e(g["icon"])}" alt="" loading="lazy" onerror="this.remove()">' if g.get("icon") else "")
        weak = " sl-weak" if g is weakest else ""
        tip = f'{g["name"]} · {label}' + (f' · ilvl {g["level"]}' if g.get("level") else "") + (
            " · nejslabší kus" if weak else "")
        lvl = f'<small>{g["level"]}</small>' if g.get("level") else ""
        return (f'<a class="sl{weak}" style="--q:{color}" href="https://www.wowhead.com/forever/item={g["id"]}" '
                f'target="_blank" rel="noopener" title="{e(tip)}">{inner}{lvl}</a>')
    two_hand = (worn.get("MAIN_HAND") or {}).get("inv") == "TWOHWEAPON"
    if (slot == "OFF_HAND" and two_hand) or (slot == "RANGED" and m["class_slug"] in NO_RANGED):
        return f'<span class="sl sl-na" title="{e(label)}: nepotřebuje"></span>'
    return f'<span class="sl sl-empty" title="{e(label)}: prázdné">{e(label)}</span>'


def prof_bars(m):
    rows = []
    for p in sorted(m.get("professions") or [], key=lambda p: (not p["primary"], -p["skill"])):
        pct = min(100, round(100 * p["skill"] / p["max"])) if p["max"] else 0
        rows.append(f'<li class="{"" if p["primary"] else "pb-sec"}"><span>{e(p["name"])}</span><b>{p["skill"]}</b>'
                    f'<span class="bar"><span style="width:{pct}%"></span></span></li>')
    return f'<ul class="pbars">{"".join(rows)}</ul>' if rows else ""


def doll(m):
    """Výbava kolem renderu postavy jako okno postavy ve hře: ikony v rámečku barvy kvality,
    prázdné sloty čárkovaně, nejslabší kus označený ▼."""
    worn = {g["slot"]: g for g in gear_info(m)["gear"]} if "gear" in m else {}
    weakest = gear_info(m)["weakest"] if "gear" in m else None
    col = lambda slots: "".join(doll_slot(s, worn, weakest, m) for s in slots)  # noqa: E731
    figure = (f'<div class="d-render" style="background-image:url(\'{e(m["render"])}\')"></div>' if m.get("render")
              else f'<div class="d-render d-norender">{avatar(m, "pc-avatar")}</div>')
    if "gear" not in m:
        return f'<div class="doll doll-nogear">{figure}</div>'
    return (f'<div class="doll"><div class="d-col">{col(DOLL_LEFT)}</div>{figure}'
            f'<div class="d-col">{col(DOLL_RIGHT)}</div><div class="d-bottom">{col(DOLL_BOTTOM)}</div></div>')


CASTERS = {"mage", "warlock", "priest"}


def stat_rows(m):
    """Staty jako ve hře, skupiny podle role: tank obrana, healer léčení, jinak útok podle classu."""
    c = m.get("cstats") or {}
    if not c:
        return ""

    prev, since = (m.get("yday") or {}).get("attrs") or {}, (m.get("yday") or {}).get("label", "")

    def plain(key, label, fmt=num):
        v = c.get(key)
        if v is None:
            return ""
        v = v[1] if isinstance(v, (tuple, list)) else v
        show = fmt if callable(fmt) else fmt.format
        bonus = ""
        if key in prev:  # plus/mínus od posledního ranního snímku
            d, zero = v - prev[key], show(0)
            if show(abs(d)) != zero:
                cls_, sign = ("st-bonus", "+") if d > 0 else ("st-malus", "−")
                bonus = f' <small class="{cls_}" title="{e(since)}">{sign}{show(abs(d))}</small>'
        return f'<li><span>{label}</span><b>{show(v)}{bonus}</b></li>'
    attr = plain

    def pct(v):
        return f"{v:.1f} %".replace(".", ",")
    groups = [("Základ", plain("health", "Health", num) + plain("power", c.get("power_type") or "Power", num)),
              ("Atributy", "".join(attr(k, k.capitalize()) for k in ("strength", "agility", "stamina", "intellect", "spirit")))]
    cls, roles = m.get("class_slug"), m.get("roles") or []
    if "tank" in roles:
        groups.append(("Obrana", attr("armor", "Armor") + attr("defense", "Defense") + plain("dodge", "Dodge", pct)
                       + plain("parry", "Parry", pct) + plain("block", "Block", pct)))
    else:
        groups.append(("Obrana", attr("armor", "Armor") + plain("dodge", "Dodge", pct)))
    if "heal" in roles:
        groups.append(("Léčení", plain("spell_healing", "Bonus healing") + plain("spell_crit", "Spell crit", pct)
                       + plain("mana_regen", "Mana regen", "{:.0f}")))
    elif cls == "hunter":
        groups.append(("Útok", plain("ranged_attack_power", "Ranged AP") + plain("ranged_crit", "Ranged crit", pct)
                       + plain("attack_power", "Melee AP")))
    elif cls in CASTERS:
        groups.append(("Kouzla", plain("spell_power", "Spell power") + plain("spell_crit", "Spell crit", pct)
                       + plain("mana_regen", "Mana regen", "{:.0f}")))
    else:
        groups.append(("Útok", plain("attack_power", "Attack power") + plain("main_hand_dps", "DPS zbraně", lambda v: f"{v:.1f}".replace(".", ","))
                       + plain("melee_crit", "Crit", pct)))
    html_groups = "".join(f'<div class="st-group"><h4>{t}</h4><ul>{rows}</ul></div>'
                          for t, rows in groups if rows)
    return f'{sickness(m)}<div class="stats">{html_groups}</div>'


def sick(m):
    """Odhlásil se s Resurrection Sickness? Staty jsou o 75 % dole (i s výbavou), takže většina atributů
    je pod základem a aspoň jeden hluboko pod ním. Normálně je effective vždycky aspoň base."""
    c = m.get("cstats") or {}
    attrs = [c[k] for k in ("strength", "agility", "stamina", "intellect", "spirit") if k in c]
    return (sum(eff < base for base, eff in attrs) >= 3
            and any(eff < base * 0.5 for base, eff in attrs if base > 0))


def sickness(m):
    if not sick(m):
        return ""
    return ('<p class="st-sick">🤒 Odhlásil se s <b>Resurrection Sickness</b>, staty −75 %. '
            'Spirit Healer pozdravuje a staty se vrátí do deseti minut ve hře.</p>')


def sheet_side(m):
    gi = gear_info(m) if "gear" in m else None
    bits = []
    if gi:
        worn = {g["slot"] for g in gi["gear"]}
        qual = "".join(f'<span style="color:{QUALITY_COLOR[q]}">{n}× {QUALITY_NAME[q]}</span>'
                       for q, n in gi["quality"].items() if n)
        two_hand = any(g["slot"] == "MAIN_HAND" and g["inv"] == "TWOHWEAPON" for g in gi["gear"])
        empty = len([s for s in DOLL_LEFT + DOLL_RIGHT + DOLL_BOTTOM if s not in worn]) - (
            ("OFF_HAND" not in worn and two_hand) + ("RANGED" not in worn and m["class_slug"] in NO_RANGED))
        gap = (f'<p class="d-gap">{pl(empty, ("{n} prázdný slot", "{n} prázdné sloty", "{n} prázdných slotů"))}</p>'
               if empty else "")
        bits.append(f'<div class="d-sum"><p class="d-ilvl"><b>{gi["avg"] or "?"}</b><span>ø item level</span></p>'
                    f'<p class="d-qual">{qual}</p>{gap}</div>')
    bits.append(stat_rows(m))
    if m.get("professions"):
        bits.append(f'<div class="st-group"><h4>Profese</h4>{prof_bars(m)}</div>')
    pet = m.get("pet")
    if pet:
        bits.append(f'<p class="d-pet">🐾 <b>{e(pet["name"])}</b> <small>{e(pet["creature"] or "")}, lvl {pet["level"]}</small></p>')
    return f'<aside class="sheet-side">{"".join(bits)}</aside>'


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


def days_ago(day, today):
    if not day:
        return "?"
    n = today.toordinal() - date_ord(day)
    return "dnes" if n <= 0 else "včera" if n == 1 else f"před {pl(n, ('{n} dnem', '{n} dny', '{n} dny'))}"


def sheet(m, today):
    """Karta postavy, která se otevře pod skupinovkou po kliknutí na postavu."""
    spec = f'{e(m["spec"])} <small>{m["spec_points"]} b.</small>' if m["spec"] else ""
    cls_icon = f'<img class="ic ic-cls" src="{e(m["class_icon"])}" alt="">' if m.get("class_icon") else ""
    return f'''<article class="sheet" id="char-{e(m["slug"])}" data-slug="{e(m["slug"])}" style="--cls:{cls_color(m)}" hidden>
  <header class="card-head">
    <div class="who">
      <h3>{e(m["name"])} <a class="sheet-armory" href="{e(m["url"])}" target="_blank" rel="noopener">Armory ↗</a></h3>
      <p class="cls">{e(m["race"])} {cls_icon}<b>{e(m["class"])}</b> · {spec} {role_chips(m["roles"])}</p>
    </div>
    <div class="lvl">{m["level"]}</div>
    <button type="button" class="sheet-close" aria-label="Zavřít">×</button>
  </header>
  {xp_bar(m, big=True)}
  <div class="sheet-body">{doll(m)}{sheet_side(m)}</div>
  <p class="updated">{e(m["updated_label"])}: {days_ago(m.get("updated"), today)}</p>
</article>'''


# ---------- hlavička ----------

def short_status(r, members):
    under = [f"{e(first(m))} −{r['status'][m['slug']][1]}" for m in members if r["status"][m["slug"]][0] == "under"]
    over = [e(first(m)) for m in members if r["status"][m["slug"]][0] == "over"]
    bits = []
    if under:
        bits.append("chybí lvl: " + ", ".join(under))
    if over:
        bits.append("přerostli: " + ", ".join(over))
    return " · ".join(bits) or "sedí všem levelem"


def fit_mark(st):
    kind, n = st
    return ("✓", "fit") if kind == "fit" else (f"−{n}", "under") if kind == "under" else (f"+{n}", "over")


def next_box(rows, best, members, checks):
    """Příští výprava: dungeon, kdo do něj levelem sedí, souhrn ready checku a co přijde pak."""
    if not best:
        return ""
    later = sorted((r for r in rows if r is not best and not r.get("done") and r["min"] >= best["min"] and r["max"] > best["max"]),
                   key=lambda r: (r["min"], r["max"]))[:2]
    party = []
    for m in members:
        mark, kind = fit_mark(best["status"][m["slug"]])
        party.append(f'<li class="nx-m nx-{kind}" style="--cls:{cls_color(m)}">{avatar(m, "nx-av")}'
                     f'<span class="nx-name">{e(first(m))}</span><span class="nx-lvl">{m["level"]}</span>'
                     f'<span class="nx-mark" title="{e(short_status(best, [m]))}">{mark}</span></li>')
    check = next((c for c in checks if c["best"]), None)
    rc = ""
    if check:
        bits = []
        if check["share"]:
            bits.append(f'<span class="nx-rc-share">↗ {check["share"]}× dostane u vchodu</span>')
        if check["take"]:
            bits.append(f'<span class="nx-rc-take">✋ {check["take"]}× musí dojít</span>')
        rc = (f'<a class="nx-rc" href="#ready">{"".join(bits) or "<span class=nx-rc-ok>✓ questy připravené</span>"}'
              f'<span class="nx-go">ready check →</span></a>')
    pak = "".join(
        f'<li><span class="pk-name">{e(r["name"])}</span><span class="pk-range">{r["min"]}–{r["max"]}</span>'
        f'<span class="pk-dots">{"".join(pak_dot(r, m) for m in members)}</span></li>' for r in later)
    note = f' · {e(best["note"])}' if best.get("note") else ""
    return (f'<section class="next"><p class="eyebrow">Příští výprava</p>'
            f'<h2>{e(best["name"])}{"<span class=chip chip-new>nový</span>" if best.get("new") else ""}</h2>'
            f'<p class="next-meta"><span class="nx-range">level {best["min"]}–{best["max"]}</span>'
            f'<span>{e(best["zone"])}{note}</span></p>'
            f'<ul class="nx-party">{"".join(party)}</ul>{rc}'
            f'{f"<p class=eyebrow>Pak</p><ul class=pak>{pak}</ul>" if later else ""}</section>')


def pak_dot(r, m):
    mark, kind = fit_mark(r["status"][m["slug"]])
    return (f'<span class="pk-dot pk-{kind}" style="--cls:{cls_color(m)}" '
            f'title="{e(first(m))}: {"sedí" if kind == "fit" else mark + " lvl"}">{"✓" if kind == "fit" else mark}</span>')


def since_label(base, today):
    """'od včerejšího rána', 'od pondělního rána'… podle dne snímku."""
    day = base.get("date") or local_day(base["at"]).isoformat()
    n = today.toordinal() - date_ord(day)
    if n <= 0:
        return "od dnešního rána"
    if n == 1:
        return "od včerejšího rána"
    return f"od {WEEKDAY_FROM[datetime.strptime(day, '%Y-%m-%d').weekday()]} rána ({cz_short(date_ord(day))})"


WEEKDAY_FROM = ["pondělního", "úterního", "středečního", "čtvrtečního", "pátečního", "sobotního", "nedělního"]


def yday(m, base, today):
    """Staty z posledního ranního snímku pro +/− na kartě; nemocný snímek se nepočítá."""
    b = ((base or {}).get("members") or {}).get(m["slug"]) or {}
    return {"attrs": b["attrs"], "label": since_label(base, today)} if b.get("attrs") and not b.get("sick") else None


def news_box(members, base, today):
    """Novinky po postavách: řádek se jménem, levelem a XP, pod ním řemesla a nové kusy výbavy."""
    rows, quiet = [], []
    for m in members:
        ch = changes(m, base)
        if ch is None:
            continue
        if not (ch["to"] > ch["from"] or ch["xp"] or ch["profs"] or ch["gear"] or ch["ach"] or ch["learned"]):
            quiet.append(m)
            continue
        lvl = f'<span class="nw-lvl">{ch["from"]} → {ch["to"]}</span>' if ch["to"] > ch["from"] else ""
        xp = f'<span class="nw-xp">+{num(ch["xp"])} XP</span>' if ch["xp"] else ""
        profs = " · ".join(f'{e(n)} <b>{"nová" if d is None else f"+{d}"}</b>' for n, d in ch["profs"])
        extra = "".join(f'<p class="nw-ach">🏆 {e(a)}</p>' for a in ch["ach"])
        if ch["learned"]:
            more = f' a {len(ch["learned"]) - 3} další' if len(ch["learned"]) > 3 else ""
            extra += f'<p class="nw-profs">📜 nové recepty: {e(", ".join(ch["learned"][:3]))}{more}</p>'
        gear = "".join(item_link(g, compact=True) for g in ch["gear"])
        if ch["stats"]:
            gear += '<p class="nw-profs">' + " · ".join(
                f'<b class="{"st-bonus" if d > 0 else "st-malus"}">{"+" if d > 0 else "−"}{num(abs(d))}</b> {k.capitalize()}'
                for k, d in ch["stats"]) + '</p>'

        rows.append(f'<li style="--cls:{cls_color(m)}">{avatar(m, "nw-av")}<div class="nw-main">'
                    f'<p class="nw-head"><b class="who-n">{e(first(m))}</b>{lvl}{xp}</p>'
                    f'{f"<p class=nw-profs>{profs}</p>" if profs else ""}{extra}'
                    f'{f"<div class=nw-gear>{gear}</div>" if gear else ""}</div></li>')
    if quiet:
        rows.append(f'<li class="nw-quiet"><span class="nw-avs">{"".join(avatar(m, "nw-av") for m in quiet)}</span>'
                    f'<span>{e(", ".join(first(m) for m in quiet))}: nic nového</span></li>')
    body = f'<ul class="nw">{"".join(rows)}</ul>' if rows else '<p class="muted">Nic nového, nikdo nehrál.</p>'
    return f'<section class="news"><p class="eyebrow">Novinky {since_label(base, today)}</p>{body}</section>'


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
        delta = f'+{num(c["delta"])} od včera · ' if c["delta"] else ""
        foot = f'<small>{delta}další milník {num(c["next"])}</small>'
        if c["crossed"]:
            foot = f'<small class="c-hit">🎉 přes {num(c["crossed"])}!</small>{foot}'
        tiles.append(f'<li class="ctr{" ctr-hit" if c["crossed"] else ""}"><span class="c-emoji">{c["emoji"]}</span>'
                     f'<b>{num(c["total"])}</b><span>{e(c["label"])}</span>{foot}</li>')
    hit = any(c["crossed"] for c in counters)
    summary = " · ".join(f'<b>{num(c["total"])}</b> {e(c["label"])}' for c in counters[:3])
    head = "Tituly za včerejší večer" if daily else "Tituly za celou dobu"
    note = ("Každý má jeden titul, žádný se neopakuje dva dny po sobě. Není to žebříček."
            if daily else "Od zítřka se tituly budou dávat za včerejší večer.")
    return (f'<section id="tituly"><h2 class="sec">{head}</h2><ul class="titles">{cards}</ul>'
            f'<details class="counters-box"{" open" if hit else ""}><summary>Parta dohromady · {summary}</summary>'
            f'<ul class="counters">{"".join(tiles)}</ul></details>'
            f'<p class="note">{note} Počítá se ze statistik Blizzard API, které se aktualizují, když se postava odhlásí.</p></section>')


def squad(members, size, today):
    """Skupinová fotka: celotělové rendery vedle sebe a jmenovky. Klik na postavu otevře pod ní její kartu."""
    out = []
    for m in members:
        if not m["ok"]:
            out.append(f'<figure class="pc pc-err"><div class="pc-img"></div><figcaption>'
                       f'<span class="pc-name">{e(m["slug"])}</span><span class="pc-sub">nenačteno</span></figcaption></figure>')
            continue
        img = (f'<div class="pc-img" style="background-image:url(\'{e(m["render"])}\')"></div>' if m.get("render")
               else f'<div class="pc-img pc-noimg">{avatar(m, "pc-avatar")}</div>')
        role = "/".join(ROLE_LABEL[r] for r in m["roles"]) or "bez specu"
        out.append(f'<figure class="pc" data-slug="{e(m["slug"])}" style="--cls:{cls_color(m)}">'
                   f'<a href="#char-{e(m["slug"])}" aria-label="Karta postavy {e(m["name"])}" aria-expanded="false">'
                   f'{img}</a><figcaption><span class="pc-name">{e(first(m))} <b>{m["level"]}</b></span>{xp_bar(m)}'
                   f'<span class="pc-sub">{e(m["class"])} · {e(role)}</span></figcaption></figure>')
    out += ['<figure class="pc pc-empty"><div class="pc-img"><span>?</span></div><figcaption>'
            '<span class="pc-name">volné místo</span><span class="pc-sub">hledá se parťák</span></figcaption></figure>'] \
        * max(0, size - len(members))
    sheets = "".join(sheet(m, today) for m in members if m["ok"])
    return (f'<section id="postavy"><div class="squad">{"".join(out)}</div>'
            f'<p class="sheet-hint">Klikni na postavu a otevře se její výbava a staty.</p>{sheets}</section>')


# ---------- ready check ----------

def quest_cell(q, st, holders):
    if st == "na":
        return f'<td class="st st-na" title="nemůže vzít: {e(q["limit"]["why"])}">—</td>'
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
        return '<td class="st st-over" title="quest začíná až v dungeonu (předmět nebo postava uvnitř)">uvnitř</td>'
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
    lim = q.get("limit")
    if lim and lim["cls"]:
        tags.append(f'<span class="share share-cls" style="--cls:{CLASS_COLOR.get(lim["cls"], "var(--muted)")}" '
                    f'title="{e(lim["why"])}, ostatní ho nevidí">{e(lim["text"])}</span>')
    elif lim:
        tags.append(f'<span class="share share-race" title="{e(lim["why"])}">{e(lim["text"])}</span>')
    if q["start"] == "dungeon":
        tags.append('<span class="share share-in" title="začíná až v dungeonu: předmětem, který tam padne, nebo od postavy uvnitř">začíná uvnitř</span>')
    if q["prev"]:
        tags.append(f'<span class="share share-no" title="navazuje na {e(q["prev"])}, každý si ho musí vzít sám">✋ každý sám · řetěz</span>')
    elif q["sharable"]:
        whom = f'jen dalším, kdo ho můžou vzít ({lim["text"][4:] if lim["cls"] else lim["why"]})' if lim else "ostatním v partě"
        tags.append(f'<span class="share share-yes" title="kdo ho má, nasdílí ho {e(whom)}">↗ sdílitelný</span>')
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
        cards.append(f'<div class="todo" data-slug="{e(s)}" style="--cls:{cls_color(m)}"><h4>{e(first(m))}</h4>'
                     f'<ul>{"".join(lines)}</ul></div>')
    return f'<div class="todos">{"".join(cards)}</div>'


def ready_box(check, members, idx):
    by_slug = {m["slug"]: m for m in members}
    cols = [by_slug[s] for s in check["members"]]
    head = "".join(f'<th class="who-col" data-slug="{e(m["slug"])}" style="--cls:{cls_color(m)}">{e(first(m))}</th>'
                   for m in cols)
    body = []
    for q in check["quests"]:
        holders = ", ".join(first(m) for m in cols if q["status"][m["slug"]] == "active")
        cells = "".join(quest_cell(q, q["status"][m["slug"]], holders).replace("<td ", f'<td data-slug="{e(m["slug"])}" ', 1)
                        for m in cols)
        classes = (["q-none"] if q["unclaimed"] else []) + (["q-cls"] if q["limit"] and q["limit"]["cls"] else [])
        cls = f' class="{" ".join(classes)}"' if classes else ""
        if q["limit"] and q["limit"]["cls"]:
            cls += f' style="--cls:{CLASS_COLOR.get(q["limit"]["cls"], "var(--muted)")}"'
        body.append(f'<tr{cls}><th scope="row"><div class="q-name"><a href="https://www.wowhead.com/forever/quest={q["id"]}" '
                    f'target="_blank" rel="noopener">{e(q["name"])}</a>{share_label(q)}</div>'
                    f'<small>{quest_meta(q)}</small></th>{cells}</tr>')
    best = '<span class="chip chip-new">příští výprava</span>' if check["best"] else ""
    links = [(check.get("source"), "Wowhead", "questy, levely a sdílení podle Wowheadu"),
             (check.get("tavern"), "Warcraft Tavern", "podrobný postup uvnitř dungeonu")]
    source = "".join(f'<a href="{e(url)}" target="_blank" rel="noopener" title="{tip}">{name} ↗</a>'
                     for url, name, tip in links if url)
    source = f'<span class="rc-source">{source}</span>' if source else ""
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
    me = "".join(f'<button type="button" data-me="{e(m["slug"])}" style="--cls:{cls_color(m)}">{e(first(m))}</button>'
                 for m in members)
    return (f'<section id="ready"><div class="sec-row"><h2 class="sec">Ready check</h2>'
            f'<div class="me-pick" aria-label="Kdo se dívá"><span>Já jsem</span>'
            f'<button type="button" data-me="" aria-pressed="true">všichni</button>{me}</div></div>'
            f'<div class="rc-picker" role="tablist">{tabs}</div>'
            f'{"".join(ready_box(c, members, i) for i, c in enumerate(checks))}'
            f'<p class="note legend"><span class="st-fit">✓</span> má v logu · <span class="st-share">↗</span> dostane nasdílením u vchodu · '
            f'<span class="st-take">dojít</span> musí si pro quest dojít · ⛓ ✓ rozjetý řetěz · ⛓ nejdřív předchozí quest · '
            f'lvl N zatím nízký level · — nemůže vzít (jiná classa nebo rasa) · uvnitř = začne až v dungeonu (předmět nebo postava uvnitř). Šedé questy nemá nikdo z vás. '
            f'Questy podle Wowheadu, postup uvnitř podle Warcraft Tavern, ID a omezení na classu ověřená v Blizzard API. Classové questy vidí jen ta classa, ready check mají jen dungeony z <code>quests.json</code>.</p></section>')


# ---------- profese a dungeony ----------

def craft_box(plan, members):
    """Co se vyplatí vyrobit: upgrady, enchanty a profese na stropu. Prázdné části se neukazují."""
    by_slug = {m["slug"]: m for m in members}

    def who(slug, name=first):
        m = by_slug.get(slug)
        return f'<b style="color:{cls_color(m)}">{e(name(m))}</b>' if m else e(slug)

    rows = []
    for u in plan["ups"]:
        target = "sobě" if u["target"] == u["crafter"] else who(u["target"])
        gain = "prázdný slot" if u["upgrade"] == "empty" else f'+{u["upgrade"]} ilvl'
        slot = SLOT_LABEL.get(ITEM_SLOTS[u["item"]["inv"]][0], "")
        need = " · ".join([f'potřebuje {r["qty"]}× {e(r["name"])} od {", ".join(who(s, first_gen) for s in r["from"])}'
                           for r in u["reagents"]] + ([f'přijde o enchant na {e(u["loses"])}'] if u["loses"] else []))
        rows.append(f'<li>{item_link(u["item"])}<span class="cf-who">{who(u["crafter"])} → {target}</span>'
                    f'<span class="cf-gain">{e(slot)} · {gain}</span>'
                    f'{f"<span class=cf-need>{need}</span>" if need else ""}</li>')
    for slot, en in plan["enchants"].items():
        targets = ", ".join(who(s) for s in en["targets"])
        effects = ", ".join(sorted(en["effects"]))
        rows.append(f'<li class="cf-ench"><span class="cf-title">✨ Enchant na {e(SLOT_CZ.get(slot, slot))}</span>'
                    f'<span class="cf-who">{targets}</span><span class="cf-gain">bez enchantu</span>'
                    f'<span class="cf-need">umí {", ".join(who(s) for s in sorted(en["by"]))}: {e(effects)}</span></li>')
    for slug, prof, skill in plan["capped"]:
        rows.append(f'<li class="cf-cap"><span class="cf-title">⚠️ {e(prof)} {skill}/{skill}</span>'
                    f'<span class="cf-who">{who(slug)}</span><span class="cf-gain">strop</span><span class="cf-need">skill je na stropu, '
                    f'zajdi k trenérovi</span></li>')
    body = (f'<ul class="craft">{"".join(rows)}</ul>' if rows
            else '<p class="muted">Nic, co by se teď vyplatilo vyrobit.</p>')
    return (f'<div class="craft-box"><h3 class="sub-h">Co vyrobit pro partu</h3>{body}'
            f'<p class="note">Jen upgrady (prázdný slot nebo vyšší item level, class je unese, level stačí) a suroviny, '
            f'které musí dodat někdo jiný z party. Staty se neporovnávají.</p></div>')


def loot_who(fits, by_slug):
    """Štítky postav, kterým se kus hodí: upgrade barvou classy, ostatní tlumeně."""
    chips = []
    order = sorted(fits.items(), key=lambda kv: (kv[1].get("second", False), kv[1]["gain"] is None, kv[1]["gain"] == "?",
                                                 list(by_slug).index(kv[0])))
    for slug, f in order:
        m = by_slug[slug]
        g = f["gain"]
        if g is None:
            text, cls, tip = "má lepší", " lt-no", "nosí stejný nebo lepší kus"
        elif g == "?":
            text, cls, tip = "?", " lt-maybe", "neznámý item level nebo výbava, porovnej ve hře"
        elif g == "empty":
            text, cls, tip = "prázdný slot", "", "ten slot má prázdný"
        else:
            text, cls, tip = f"+{g} ilvl", "", f"o {g} item levelů lepší než to, co nosí"
        if f.get("second"):
            cls += " lt-second"
            tip = "druhá volba: heal nebo damage jiné školy nevyužije, " + tip
            text = "2. volba · " + text
        lvl = f' <small>lvl {m["level"] + f["lvl"]}</small>' if f["lvl"] else ""
        if f["lvl"]:
            tip += f', nasadí až na levelu {m["level"] + f["lvl"]}'
        chips.append(f'<span class="lt-who{cls}" data-slug="{e(slug)}" style="--cls:{cls_color(m)}" title="{e(tip)}">'
                     f'<b>{e(first(m))}</b> {e(text)}{lvl}</span>')
    return "".join(chips)


def loot_line(entry, by_slug, contested=True, sets=None):
    it, fits = entry["item"], entry["fits"]
    color = QUALITY_COLOR.get(it.get("quality"), "var(--text)")
    href = (f'https://www.wowhead.com/forever/item={it["id"]}' if it.get("id")
            else "https://www.wowhead.com/forever/search?q=" + urllib.parse.quote(it["name"]))
    icon = f'<img class="ic" src="{e(it["icon"])}" alt="" style="border-color:{color}">' if it.get("icon") else ""
    slot = SLOT_LABEL.get(ITEM_SLOTS.get(it.get("inv"), [""])[0], "")
    if it.get("inv") == "TWOHWEAPON":
        slot = "2H zbraň"
    meta = " · ".join(str(x) for x in (slot, it.get("sub") if it.get("sub") not in (None, "Miscellaneous") else None,
                                       it.get("stats") or it.get("stat"), it.get("level") and f'ilvl {it["level"]}',
                                       it.get("req") and f'od lvl {it["req"]}') if x)
    alt = f' <small class="lt-alt">(nebo {e(it["alt"])})</small>' if it.get("alt") else ""
    ups = [s for s, f in fits.items() if f["gain"] not in (None, "?") and not f.get("second")]
    if it.get("set"):
        bonus = " · ".join((sets or {}).get(it["set"], {}).get("bonus") or [])
        alt += f' <span class="lt-set" title="{e(bonus)}">set {e(it["set"])}</span>'
    flag = ('<span class="lt-flag" title="upgrade pro víc lidí, domluvte se před rollem">🎲 víc zájemců</span>'
            if contested and len(ups) > 1 else "")
    who = loot_who(fits, by_slug) or '<span class="lt-none">nikomu, disenchant nebo prodat</span>'
    cls = " lt-fight" if flag else ""
    return (f'<li class="lt{cls}"><div class="lt-item"><a class="item" style="color:{color}" href="{e(href)}" target="_blank" '
            f'rel="noopener">{icon}{e(it["name"])}</a>{alt}<small>{e(meta)}</small></div>'
            f'<div class="lt-whos">{flag}{who}</div></li>')


def loot_section(lists, members):
    if not lists:
        return ""
    by_slug = {m["slug"]: m for m in members}
    parts = []
    for d in lists:
        bosses = []
        for b in d["bosses"]:
            rare = ('<span class="chip chip-rare">rare</span>' if b.get("rare") else
                    '<span class="chip chip-none">z trashe</span>' if b.get("trash") else "")
            tip = f'<p class="boss-tip">{e(b["tip"])}</p>' if b.get("tip") else ""
            bosses.append(f'<article class="boss{" boss-trash" if b.get("trash") else ""}"><h4>{e(b["name"])}{rare}</h4>{tip}'
                          f'<ul class="loot">{"".join(loot_line(x, by_slug, sets=d["sets"]) for x in b["items"])}</ul></article>')
        set_boxes = []
        for name, st in d["sets"].items():
            pieces = [(x["item"], b) for b in d["bosses"] for x in b["items"] if x["item"].get("set") == name]
            rows_ = "".join(f'<li><a class="item" style="color:{QUALITY_COLOR.get(it.get("quality"), "var(--text)")}" '
                            f'href="https://www.wowhead.com/forever/item={it["id"]}" target="_blank" rel="noopener">{e(it["name"])}</a>'
                            f'<small>{e("trash" if b.get("trash") else b["name"])}</small></li>' for it, b in pieces)
            bonus = "".join(f'<li>{e(x)}</li>' for x in st.get("bonus") or [])
            set_boxes.append(f'<article class="set-box"><h4>Set {e(name)} <small>{len(pieces)} kusů v dungeonu</small></h4>'
                             f'<div class="set-cols"><ul class="set-pieces">{rows_}</ul><ul class="set-bonus">{bonus}</ul></div></article>')
        quests = "".join(f'<li class="lq"><span class="lq-name">{e(q["quest"])}</span>'
                         f'<ul class="loot">{"".join(loot_line(x, by_slug, contested=False) for x in q["items"])}</ul></li>'
                         for q in d["quests"])
        links = [(d.get("source"), "Loot", "seznam lootu"), (d.get("tactics"), "Taktika", "postup a bossové podle Warcraft Tavern")]
        src = "".join(f'<a href="{e(u)}" target="_blank" rel="noopener" title="{t}">{n} ↗</a>' for u, n, t in links if u)
        best = '<span class="chip chip-new">příští výprava</span>' if d["best"] else ""
        note = f'<p class="note">{e(d["note"])}</p>' if d.get("note") else ""
        # spoiler: bossové a loot jsou schované, dokud si je někdo sám nerozklikne
        n = sum(1 for b in d["bosses"] if not b.get("trash"))
        parts.append(f'<details class="loot-dg"><summary><div class="rc-head"><h3>{e(d["title"])}{best}</h3>'
                     f'<span class="range">level {d["min"]}–{d["max"]}</span>'
                     f'<span class="spoil">⚠ spoiler · {pl(n, ("{n} boss", "{n} bossové", "{n} bossů"))}, taktika a loot '
                     f'<span class="spoil-open">ukázat ▾</span><span class="spoil-close">schovat ▴</span></span></div></summary>'
                     f'<p class="rc-source loot-src">{src}</p>'
                     f'<div class="bosses">{"".join(bosses)}</div>{"".join(set_boxes)}'
                     f'{f"<h3 class=sub-h>Odměny z questů (každý si vybírá sám)</h3><ul class=lq-list>{quests}</ul>" if quests else ""}'
                     f'{note}</details>')
    return (f'<section id="loot"><h2 class="sec">Bossové a loot</h2>{"".join(parts)}'
            f'<p class="note legend">U kusu jsou jen ti, kdo ho unesou a komu sedí staty (caster, heal, frost nebo shadow damage, agility, strength). '
            f'<b>+N ilvl</b> = o tolik item levelů lepší než to, co nosí · <b>prázdný slot</b> · <b>má lepší</b> = nosí stejné nebo lepší · '
            f'<b>lvl N</b> = nasadí až na tom levelu · <b>2. volba</b> (tlumeně) = heal nebo damage jiné školy z kusu nevyužije, ale zbytek ano · 🎲 upgrade pro víc lidí, domluvte se před rollem. Staty se neporovnávají, jen item level. '
            f'Bossové a loot jsou schované jako spoiler, ukážou se po kliknutí. Přepisuješ je v <code>bosses.json</code>.</p></section>')


def professions_box(members):
    """Přehled: profese × postavy, skill s ukazatelem; profese, které nikdo nemá, na jednom řádku."""
    names = [p["name"] for m in members for p in m.get("professions") or []]
    prim = [n for n in ALL_PRIMARIES if n in names]
    sec = sorted({p["name"] for m in members for p in m.get("professions") or [] if not p["primary"]})
    nobody = [n for n in ALL_PRIMARIES if n not in names]
    cols = len(members)
    head = "".join(f'<span class="pg-h" style="color:{cls_color(m)}">{e(first(m))}</span>' for m in members)

    def row(name, cls=""):
        cells = ""
        for m in members:
            p = next((p for p in m.get("professions") or [] if p["name"] == name), None)
            if not p:
                cells += '<span class="pg-cell pg-none"><b>–</b><span class="bar"></span></span>'
                continue
            pct = min(100, round(100 * p["skill"] / p["max"])) if p["max"] else 0
            cells += (f'<span class="pg-cell" title="{p["skill"]} / {p["max"]}" style="--cls:{cls_color(m)}">'
                      f'<b>{p["skill"]}</b><span class="bar"><span style="width:{pct}%"></span></span></span>')
        return f'<span class="pg-name{cls}">{e(name)}</span>{cells}'

    grid = "".join(row(n) for n in prim) + "".join(row(n, " pg-sec") for n in sec)
    foot = f'<p class="note">Nikdo nemá: {e(", ".join(nobody))}.</p>' if nobody else ""
    return (f'<div class="prof-grid2" style="--cols:{cols}"><span></span>{head}{grid}</div>{foot}')


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
        if r.get("done"):  # datum, nebo jen true, když si ho nikdo nepamatuje
            when = ""
            if isinstance(r["done"], str):
                _, mo, d = (int(x) for x in r["done"].split("-"))
                when = f" {d}. {mo}."
            chips += f'<span class="chip chip-done">✓ odehráno{when}</span>'
        place = e(r["zone"]) + (f' · {e(r["note"])}' if r.get("note") else "")
        cells = "".join(status_cell(r["status"][m["slug"]]) for m in members)
        body.append(f'<tr{" class=best" if r is best else " class=done" if r.get("done") else ""}><th scope="row">{e(r["name"])}{chips}<small>{place}</small></th>'
                    f'<td class="range">{r["min"]}–{r["max"]}</td>{cells}</tr>')
    return (f'<div class="table-wrap"><table class="dungeons"><thead><tr><th>Dungeon</th><th>Level</th>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')


FAVICON = ("data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'>"
           "<text y='.9em' font-size='90'>⚔️</text></svg>")


def page_head(title, desc="", image="", url=""):
    og = "".join(f'<meta property="og:{k}" content="{e(v)}">\n' for k, v in
                 (("title", title), ("description", desc), ("image", image), ("url", url), ("type", "website")) if v)
    return f'''<!doctype html>
<html lang="cs">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
{f'<meta name="description" content="{e(desc)}">' if desc else ""}
{og}<link rel="icon" href="{FAVICON}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@600;700&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>{CSS}</style>
<script>const whTooltips={{colorLinks:false,iconizeLinks:false,renameLinks:false}};</script>
<script src="https://wow.zamimg.com/js/tooltips.js" defer></script>
</head>
<body>'''


# ---------- kronika ----------

MONTHS = ["", "ledna", "února", "března", "dubna", "května", "června", "července", "srpna", "září", "října",
          "listopadu", "prosince"]


def cz_short(ordinal):
    d = datetime.fromordinal(ordinal)
    return f"{d.day}. {MONTHS[d.month]}"


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
        return ""
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


def render(party, members, rows, best, built, checks, base, titles, counters, chronicle, today, plan=None, loot=None):
    for m in members:
        m["yday"] = yday(m, base, today)
    ok = [m for m in members if m["ok"]]
    has_tank = any("tank" in m["roles"] for m in ok)
    has_heal = any("heal" in m["roles"] for m in ok)
    levels = [m["level"] for m in ok]
    lvl_range = f"{min(levels)}–{max(levels)}" if levels else "?"
    ruleset = next((m["ruleset"] for m in ok if m["ruleset"]), "")
    faction = next((m["faction"] for m in ok if m["faction"]), "")

    def flag(label, good):
        return f'<span class="flag {"flag-ok" if good else "flag-bad"}">{"✓" if good else "✗"} {label}</span>'

    by_slug = {m["slug"]: m for m in ok}
    desc = " · ".join(f'{first(by_slug[s])}: {t["title"]}' for s, t in titles.items() if s in by_slug)
    lead = next(iter(titles), None)
    image = (by_slug.get(lead) or {}).get("render") or next((m.get("render") for m in ok if m.get("render")), "")
    nav = [("postavy", "Postavy", True), ("tituly", "Tituly", bool(titles)), ("vyprava", "Výprava", True), ("ready", "Ready check", bool(checks)), ("loot", "Loot", bool(loot)),
           ("kronika", "Kronika", bool(chronicle)), ("profese", "Profese", True)]
    nav_html = "".join(f'<a href="#{i}">{t}</a>' for i, t, show in nav if show)
    return f'''{page_head(party["title"], desc, image, party.get("site_url", ""))}
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
  {squad(members, party.get("size", len(members)), today)}
  <nav class="nav">{nav_html}</nav>
  {evening_box(ok, base, titles, counters)}
  <div class="hero" id="vyprava">{next_box(rows, best, ok, checks)}{news_box(ok, base, today) if base else ""}</div>
  {ready_section(checks, ok)}
  {loot_section(loot, ok)}
  {chronicle_box(chronicle, ok)}
  <section id="profese"><h2 class="sec">Profese</h2>{craft_box(plan, ok) if plan else ""}
    <h3 class="sub-h">Přehled</h3>{professions_box(ok)}</section>
  <section><details class="all-dungeons"><summary>Všechny dungeony podle levelu ({len(rows)})</summary>
    {dungeon_table(rows, best, ok)}
    <p class="note">✓ sedí · −N chybí N levelů · +N je o N levelů výš. Rozsahy podle
    <a href="https://www.wowhead.com/forever/guide/dungeons-overview-locations-details" target="_blank" rel="noopener">Wowhead pro Forever</a>, upravíš je v <code>dungeons.json</code>.</p></details></section>
  <footer>Data z Blizzard API (záloha: veřejná <a href="https://worldofwarcraft.blizzard.com/en-us/forever/eu/armory" target="_blank" rel="noopener">WoW Forever Armory</a>), obnovuje se každé ráno. Postava se aktualizuje, když se odhlásí ze hry. Tooltipy předmětů z Wowhead.</footer>
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
.squad{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;margin:18px 0 0}
@media(max-width:760px){.squad{grid-template-columns:repeat(2,minmax(0,1fr))}}
.pc{margin:0;display:flex;flex-direction:column;background:var(--panel);border:1px solid var(--line);border-radius:14px;overflow:hidden;position:relative}
.pc>a{display:block;position:relative;color:inherit}.pc>a:hover{text-decoration:none}
.pc>a:hover .pc-img{filter:brightness(1.15)}
.pc-img{height:250px;background-repeat:no-repeat;background-size:auto 155%;background-position:center 60%;
background-color:var(--panel2);background-image:none;
box-shadow:inset 0 -50px 40px -24px var(--panel);position:relative}
.pc-img::before{content:"";position:absolute;inset:0;z-index:-1}
.pc>a .pc-img{background-color:color-mix(in srgb,var(--cls) 14%,var(--panel2))}
@media(max-width:760px){.pc-img{height:170px}.pc-empty{display:none}}
.pc-noimg{display:grid;place-items:center}.pc-avatar{width:96px;height:96px;border-radius:50%;border:3px solid var(--cls)}
.pc figcaption{padding:4px 12px 10px;display:grid;gap:3px;border-top:3px solid var(--cls)}
.pc-name{font-weight:600}.pc-name b{color:var(--gold);font-family:Cinzel,serif;font-size:18px;margin-left:2px}
.pc-sub{font-size:12px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.pc-empty{border-style:dashed;background:transparent;color:var(--muted)}
.pc-empty .pc-img{display:grid;place-items:center;background:transparent;box-shadow:none}
.pc-empty .pc-img span{font:700 64px Cinzel,serif;color:var(--line)}.pc-empty figcaption{border-top:1px dashed var(--line)}
.nav{position:sticky;top:0;z-index:20;display:flex;gap:4px;overflow-x:auto;margin:14px -16px 6px;padding:8px 16px;
background:color-mix(in srgb,var(--bg) 92%,transparent);backdrop-filter:blur(6px);border-bottom:1px solid var(--line);scrollbar-width:none}
.nav a{white-space:nowrap;color:var(--muted);font-size:14px;font-weight:600;padding:4px 10px;border-radius:999px}
.nav a:hover{color:var(--gold);background:var(--panel2);text-decoration:none}
section[id],div[id]{scroll-margin-top:56px}
.sec-row{display:flex;justify-content:space-between;align-items:baseline;gap:8px 16px;flex-wrap:wrap}
.me-pick{display:flex;gap:4px;align-items:center;flex-wrap:wrap;font-size:12px;color:var(--muted)}
.me-pick span{margin-right:4px}
.me-pick button{font:600 12px Inter,system-ui,sans-serif;color:var(--cls,var(--text));background:var(--panel);border:1px solid var(--line);
border-radius:999px;padding:3px 10px;cursor:pointer}
.me-pick button[aria-pressed=true]{border-color:var(--cls,var(--gold));background:var(--panel2);box-shadow:inset 0 0 0 1px var(--cls,var(--gold))}
.todo.dim{display:none}
.rc-table .me-col{background:color-mix(in srgb,var(--gold) 9%,transparent)}
.item-ic img{width:26px;height:26px;border-radius:5px;border:2px solid var(--line);vertical-align:middle}
.hero{display:grid;grid-template-columns:minmax(0,3fr) minmax(0,2fr);gap:14px;margin-bottom:8px}
@media(max-width:760px){.hero{grid-template-columns:minmax(0,1fr)}}
.next{background:linear-gradient(135deg,#3b1612,#22140f);border:1px solid #6b2a24;border-radius:14px;padding:18px 20px}
.next h2{font-size:clamp(22px,4vw,30px)}.next-meta{margin:4px 0 12px;color:var(--text)}
.news{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px 20px}
.news .who-n{color:var(--cls);margin-right:4px}.news small{color:var(--muted)}
.sec{font-size:20px;color:var(--gold);margin:30px 0 12px}
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
.updated{margin:auto 0 0;color:var(--muted);font-size:12px}
.rc{margin-bottom:12px}.rc-head{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:12px}
.rc-head h3{font-size:18px}.rc-source{margin-left:auto;font-size:13px;display:flex;gap:12px}
.rc-table tbody th a{color:var(--text)}.rc-table tr.q-none th a{color:var(--muted)}
.rc-table tbody th small{display:block;color:var(--muted);font-weight:400;font-size:12px}
.q-name{display:flex;flex-wrap:wrap;align-items:center;gap:6px}
.share{font-size:11px;font-weight:600;padding:1px 8px;border-radius:999px;white-space:nowrap}
.share-cls{background:color-mix(in srgb,var(--cls) 18%,transparent);color:var(--cls);border:1px solid color-mix(in srgb,var(--cls) 45%,transparent)}
.share-race{background:#3a2626;color:#e0a0a0}
.rc-table tr.q-cls>th:first-child{box-shadow:inset 3px 0 0 var(--cls)}
.st-na{color:var(--muted);opacity:.45}
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
.chip-done{background:#1f4224;color:#8fe39a;margin-left:8px;vertical-align:middle}.dungeons tr.done td{opacity:.45}
.prof-grid+.all-dungeons{margin-top:30px}.all-dungeons summary{cursor:pointer;color:var(--gold);font:600 16px Cinzel,serif;margin-bottom:10px}
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
.ic{width:16px;height:16px;border-radius:3px;border:1px solid var(--line);vertical-align:-3px;margin-right:5px}
.ic-cls{margin:0 3px 0 2px;border-color:var(--cls)}
.cls .chip{margin-left:4px;vertical-align:1px}.cls small{color:var(--muted)}
.doll{display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:8px 10px;align-self:start}
.d-render{min-height:320px;border-radius:12px;background-repeat:no-repeat;background-size:auto 155%;background-position:center 60%;
background-color:color-mix(in srgb,var(--cls) 12%,var(--panel2));box-shadow:inset 0 -40px 40px -20px var(--panel)}
.d-norender{display:grid;place-items:center}.doll-nogear{display:block}
.sheet[hidden]{display:none}
.sheet{margin:12px 0 0;background:var(--panel);border:1px solid var(--line);border-top:3px solid var(--cls);border-radius:14px;
padding:14px 16px;display:flex;flex-direction:column;gap:10px;scroll-margin-top:56px}
.sheet .who h3{font-size:22px}.sheet-armory{font:600 12px Inter,system-ui,sans-serif;margin-left:6px;vertical-align:3px}
.sheet-close{background:none;border:1px solid var(--line);color:var(--muted);border-radius:50%;width:30px;height:30px;font-size:18px;
cursor:pointer;flex:none;line-height:1}.sheet-close:hover{color:var(--text);border-color:var(--cls)}
.sheet-body{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(0,1fr);gap:18px;border-top:1px solid var(--line);padding-top:14px}
@media(max-width:760px){.sheet-body{grid-template-columns:minmax(0,1fr)}.d-render{min-height:300px}}
.sheet-side{display:flex;flex-direction:column;gap:12px;font-size:13px;min-width:0}
.d-sum{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 14px}.d-sum p{margin:0}
.stats{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px 16px}
.st-group h4{margin:0 0 4px;font:600 11px Inter,system-ui,sans-serif;text-transform:uppercase;letter-spacing:.1em;color:var(--muted)}
.st-group ul{list-style:none;margin:0;padding:0;display:grid;gap:2px}
.st-group li{display:flex;justify-content:space-between;gap:8px;border-bottom:1px dotted var(--line);padding:1px 0}
.st-group li b{font-weight:600}.st-bonus{color:var(--ok);font-weight:600}.st-malus{color:var(--bad);font-weight:600}
.st-sick{margin:0;padding:8px 10px;border:1px solid #6b2a24;background:#3b1612;border-radius:10px;color:#ffb3a8}
.sheet-hint{margin:8px 0 0;font-size:12px;color:var(--muted);text-align:center}
.squad.has-open .pc:not(.open){opacity:.55}.squad.has-open .pc:not(.open):hover{opacity:.85}
.pc.open{border-color:var(--cls);box-shadow:0 0 0 1px var(--cls),0 6px 20px -8px var(--cls)}
.d-col{display:grid;gap:6px;align-content:start}
.d-bottom{grid-column:1/-1;display:flex;justify-content:center;gap:6px}
.sl{position:relative;width:40px;height:40px;border-radius:7px;border:2px solid var(--q,var(--line));background:var(--panel2);
display:grid;place-items:center;overflow:hidden;box-shadow:0 0 8px -3px var(--q,transparent)}
.sl img{position:absolute;inset:0;width:100%;height:100%;display:block}
.sl small{position:absolute;right:1px;bottom:0;font:700 10px Inter,system-ui,sans-serif;color:#fff;text-shadow:0 0 3px #000,0 0 2px #000}
.sl:hover{text-decoration:none;filter:brightness(1.25)}
.sl-txt{font-size:12px;color:var(--q)}
.sl-empty{border:1px dashed var(--line);background:transparent;box-shadow:none;font-size:9px;color:var(--muted);overflow:hidden}
.sl-na{border:1px solid transparent;background:repeating-linear-gradient(45deg,transparent 0 5px,#ffffff08 5px 7px);box-shadow:none}
.sl-weak::after{content:"▼";position:absolute;left:2px;top:0;font-size:9px;color:var(--bad);text-shadow:0 0 3px #000}
.d-ilvl{display:flex;align-items:baseline;gap:8px}.d-ilvl b{font:700 30px/1 Cinzel,serif;color:var(--gold)}.d-ilvl span{color:var(--muted);font-size:12px}
.d-qual{display:flex;gap:10px;flex-wrap:wrap;font-size:12px;font-weight:600}
.d-gap{color:var(--warn);font-size:12px}
.pbars{list-style:none;margin:4px 0 0;padding:0;display:grid;gap:5px}
.pbars li{display:grid;grid-template-columns:1fr auto;gap:0 8px;font-size:12px}.pbars li b{color:var(--gold);font-size:12px}
.pbars .bar{grid-column:1/-1;height:3px}.pbars .bar span{background:linear-gradient(90deg,#b0802f,var(--gold))}
.pbars .pb-sec{color:var(--muted)}.pbars .pb-sec .bar span{background:var(--muted)}
.d-pet{font-size:12px}.d-pet small{color:var(--muted)}
box-shadow:0 0 0 2px var(--panel);z-index:1}
.next{display:flex;flex-direction:column;gap:14px}.next .eyebrow{margin:0}
.next h2 .chip{font-family:Inter,system-ui,sans-serif}
.next-meta{margin:0;display:flex;flex-wrap:wrap;align-items:center;gap:6px 10px;color:var(--muted);font-size:14px}
.nx-range{color:var(--gold);font-weight:600;border:1px solid #6b5420;background:#2c2014;border-radius:999px;padding:1px 10px}
.nx-party{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px}
.nx-m{display:grid;grid-template-columns:30px 1fr auto;grid-template-rows:auto auto;column-gap:8px;align-items:center;
background:#0003;border:1px solid #ffffff10;border-left:3px solid var(--cls);border-radius:10px;padding:6px 10px 6px 8px}
.nx-av{grid-row:1/3;width:30px;height:30px;border-radius:50%;background:var(--panel2)}
.nx-name{font-weight:600;font-size:13px;color:var(--cls)}.nx-lvl{grid-column:2;font-size:12px;color:var(--muted)}
.nx-mark{grid-row:1/3;grid-column:3;font-weight:700;font-size:14px}
.nx-fit .nx-mark{color:var(--ok)}.nx-under .nx-mark{color:var(--warn)}.nx-over .nx-mark{color:var(--muted)}
.nx-under,.nx-over{opacity:.75}
.nx-rc{display:flex;flex-wrap:wrap;align-items:center;gap:6px 14px;font-size:13px;color:var(--text);
background:#0003;border:1px solid #ffffff10;border-radius:10px;padding:8px 12px}
.nx-rc:hover{text-decoration:none;border-color:var(--gold)}
.nx-rc-share{color:var(--share)}.nx-rc-take{color:var(--warn)}.nx-rc-ok{color:var(--ok)}
.nx-go{margin-left:auto;color:var(--gold);font-weight:600}
.pak{list-style:none;margin:-8px 0 0;padding:0;display:grid;gap:2px;font-size:14px}
.pak li{display:grid;grid-template-columns:1fr auto auto;gap:12px;align-items:center;padding:5px 0;border-top:1px solid #ffffff10}
.pk-name{font-weight:600}.pk-range{color:var(--gold);font-size:13px}
.pk-dots{display:flex;gap:4px}
.pk-dot{min-width:22px;height:22px;border-radius:999px;display:grid;place-items:center;font-size:10px;font-weight:700;
background:var(--cls);color:#1a120c;padding:0 4px}
.pk-under,.pk-over{background:transparent;border:1.5px solid var(--cls);color:var(--cls)}
.nw{list-style:none;margin:0;padding:0;display:grid}
.nw>li{display:grid;grid-template-columns:34px 1fr;gap:12px;padding:10px 0;border-top:1px solid #ffffff10}
.nw>li:first-child{border-top:0;padding-top:4px}
.nw-av{width:34px;height:34px;border-radius:50%;border:2px solid var(--cls,var(--line));background:var(--panel2)}
.nw-main{min-width:0;display:grid;gap:4px}.nw-main p{margin:0}
.nw-head{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
.nw .who-n{color:var(--cls);font-size:15px}
.nw-lvl{color:var(--gold);font-weight:700;font-size:13px}
.nw-xp{margin-left:auto;color:#c9b2ff;font-size:13px;font-weight:600}
.nw-profs{font-size:13px;color:var(--muted)}.nw-profs b{color:var(--text);font-weight:600}
.nw-ach{font-size:13px;color:var(--gold)}
.nw-gear{display:flex;flex-wrap:wrap;gap:5px;margin-top:2px}
.nw-gear .item-ic img{width:28px;height:28px}
.nw-quiet{grid-template-columns:auto 1fr!important;align-items:center;color:var(--muted);font-size:13px}
.nw-avs{display:flex}.nw-avs .nw-av{width:26px;height:26px;margin-right:-8px;filter:grayscale(1);opacity:.6;border-color:var(--line)}
.nw-quiet>span:last-child{margin-left:8px}
.sub-h{font:600 13px Inter,system-ui,sans-serif;text-transform:uppercase;letter-spacing:.1em;color:var(--muted);margin:18px 0 8px}
.craft-box .sub-h{margin-top:0}
.loot-dg{margin-bottom:12px}
.loot-dg>summary{list-style:none;cursor:pointer;background:var(--panel);border:1px dashed var(--line);border-radius:12px;padding:12px 14px}
.loot-dg>summary::-webkit-details-marker{display:none}
.loot-dg>summary:hover{border-color:var(--gold)}
.loot-dg>summary .rc-head{margin:0}
.loot-dg[open]>summary{margin-bottom:12px;border-style:solid}
.spoil{margin-left:auto;font-size:13px;color:var(--warn)}
.spoil-open,.spoil-close{margin-left:8px;color:var(--gold);font-weight:600}
.loot-dg[open] .spoil-open,.loot-dg:not([open]) .spoil-close{display:none}
.loot-src{margin:0 0 10px}
.bosses{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,340px),1fr));gap:12px}
.boss{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px 14px;display:flex;flex-direction:column;gap:6px;min-width:0}
.boss h4{margin:0;font:600 16px Cinzel,serif;color:var(--gold)}
.boss-tip{margin:0;font-size:13px;color:var(--muted)}
.chip-rare{background:#16324d;color:var(--share);margin-left:8px;vertical-align:middle}
.loot{list-style:none;margin:0;padding:0;display:grid;gap:0}
.lt{display:grid;gap:4px;padding:7px 0;border-top:1px solid var(--line)}
.lt-item{display:flex;flex-wrap:wrap;gap:2px 8px;align-items:baseline;min-width:0}
.lt-item small{color:var(--muted);font-size:12px;flex-basis:100%}.lt-alt{flex-basis:auto!important}
.lt-whos{display:flex;flex-wrap:wrap;gap:4px}
.lt-who{font-size:12px;padding:1px 8px;border-radius:999px;color:var(--ok);background:color-mix(in srgb,var(--cls) 12%,transparent);
border:1px solid color-mix(in srgb,var(--cls) 40%,transparent)}
.lt-who b{color:var(--cls);font-weight:600}.lt-who small{color:var(--warn)}
.lt-who.lt-no{color:var(--muted);opacity:.6;background:transparent}.lt-who.lt-maybe{color:var(--warn)}
.lt-none{font-size:12px;color:var(--muted)}
.lt-flag{font-size:12px;padding:1px 8px;border-radius:999px;background:#4a3a14;color:var(--warn);font-weight:600}
.lt-who.dim{opacity:.25}
.lt-who.lt-second{opacity:.6;background:transparent;border-style:dashed;color:var(--muted)}
.lt-set{font-size:11px;padding:0 7px;border-radius:999px;background:#2b2440;color:#c9b2ff;border:1px solid #4a3d70;cursor:help}
.boss-trash{border-style:dashed}
.set-box{margin-top:12px;background:var(--panel);border:1px solid #4a3d70;border-radius:12px;padding:12px 14px}
.set-box h4{margin:0 0 8px;font:600 15px Cinzel,serif;color:#c9b2ff}.set-box h4 small{font:400 12px Inter,system-ui,sans-serif;color:var(--muted);margin-left:6px}
.set-cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,260px),1fr));gap:6px 20px}
.set-pieces,.set-bonus{list-style:none;margin:0;padding:0;display:grid;gap:3px;font-size:13px}
.set-pieces small{color:var(--muted);margin-left:8px}.set-bonus li{color:var(--muted)}
.lq-list{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,340px),1fr));gap:12px}
.lq{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:10px 14px}
.lq-name{font-weight:600;font-size:14px}
.craft{list-style:none;margin:0;padding:0;display:grid;gap:0;background:var(--panel);border:1px solid var(--line);border-radius:12px}
.craft li{display:grid;grid-template-columns:minmax(0,1fr) 200px 150px;gap:2px 14px;align-items:baseline;padding:9px 14px;
border-top:1px solid #ffffff10;font-size:14px}
.craft li:first-child{border-top:0}
.cf-who{font-size:13px}.cf-gain{font-size:12px;color:var(--ok);font-weight:600;text-align:right;white-space:nowrap}
.cf-need{grid-column:1/-1;font-size:12px;color:var(--muted)}
.cf-title{font-weight:600}.cf-ench .cf-gain{color:#c9b2ff}.cf-cap .cf-gain{color:var(--warn)}
@media(max-width:600px){.craft li{grid-template-columns:minmax(0,1fr) auto}.cf-who{grid-column:1/-1;grid-row:2}}
.cf-who{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.prof-grid2{display:grid;grid-template-columns:minmax(110px,1.3fr) repeat(var(--cols),minmax(0,1fr));gap:8px 14px;align-items:start;
background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px 16px;font-size:13px}
.pg-h{font-weight:600;font-size:12px;line-height:18px}.pg-name{color:var(--text);line-height:18px}.pg-sec{color:var(--muted)}
.pg-cell{display:grid;gap:3px}.pg-cell b{font-weight:600;font-size:12px;line-height:18px}
.pg-none b{color:var(--line)}.pg-none .bar{visibility:hidden}
.pg-cell .bar{height:3px}.pg-cell .bar span{background:var(--cls)}
"""

JS = """
const tabs=[...document.querySelectorAll('.rc-picker button')];
const meBtns=[...document.querySelectorAll('.me-pick button')];
function me(slug){meBtns.forEach(b=>b.setAttribute('aria-pressed',b.dataset.me===slug));
  document.querySelectorAll('.todo').forEach(t=>t.classList.toggle('dim',!!slug&&t.dataset.slug!==slug));
  document.querySelectorAll('.rc-table [data-slug]').forEach(c=>c.classList.toggle('me-col',!!slug&&c.dataset.slug===slug));
  document.querySelectorAll('.lt-who').forEach(c=>c.classList.toggle('dim',!!slug&&c.dataset.slug!==slug));
  try{localStorage.setItem('me',slug)}catch(e){}}
meBtns.forEach(b=>b.addEventListener('click',()=>me(b.dataset.me)));
try{const m=localStorage.getItem('me');if(m&&meBtns.some(b=>b.dataset.me===m))me(m)}catch(e){}
function pick(i){tabs.forEach(b=>b.setAttribute('aria-selected',b.dataset.rc==i));
  document.querySelectorAll('.rc').forEach(a=>a.hidden=a.id!=='rc-'+i);
  try{localStorage.setItem('rc-pick',tabs[i].dataset.title)}catch(e){}}
tabs.forEach(b=>b.addEventListener('click',()=>pick(+b.dataset.rc)));
try{const saved=localStorage.getItem('rc-pick');const t=tabs.find(b=>b.dataset.title===saved);if(t)pick(+t.dataset.rc)}catch(e){}
const pcs=[...document.querySelectorAll('.pc[data-slug]')];
function openSheet(slug,scroll){
  document.querySelectorAll('.sheet').forEach(x=>x.hidden=x.dataset.slug!==slug);
  pcs.forEach(p=>{const on=p.dataset.slug===slug;p.classList.toggle('open',on);p.querySelector('a').setAttribute('aria-expanded',on)});
  document.querySelector('.squad').classList.toggle('has-open',!!slug);
  const hint=document.querySelector('.sheet-hint'); if(hint) hint.hidden=!!slug;
  history.replaceState(null,'',slug?'#char-'+slug:location.pathname+location.search);
  if(slug&&scroll){const el=document.getElementById('char-'+slug); if(el) el.scrollIntoView({behavior:'smooth',block:'nearest'})}}
pcs.forEach(p=>p.querySelector('a').addEventListener('click',ev=>{ev.preventDefault();
  openSheet(p.classList.contains('open')?'':p.dataset.slug,true)}));
document.querySelectorAll('.sheet-close').forEach(b=>b.addEventListener('click',()=>openSheet('')));
{const h=decodeURIComponent(location.hash);let start='';
 if(h.startsWith('#char-')) start=h.slice(6); else {try{start=localStorage.getItem('me')||''}catch(e){}}
 if(start&&pcs.some(p=>p.dataset.slug===start)) openSheet(start,h.startsWith('#char-'));}
addEventListener('hashchange',()=>{const h=decodeURIComponent(location.hash);
  if(h.startsWith('#char-')&&pcs.some(p=>p.dataset.slug===h.slice(6))) openSheet(h.slice(6),true)});
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
    if token:
        add_icons(token, region, members)
    rows, best = plan_dungeons(ok, dungeons)
    # ručně domluvená příští výprava přebije odhad podle levelů
    best = next((r for r in rows if r["name"] == party.get("next")), best)
    static = Static(token, region)
    try:  # co vyrobit je bonus, stránka musí vzniknout i bez něj
        plan = crafting(ok, static)
    except Exception as exc:
        print(f"Profese: {exc}", file=sys.stderr)
        plan = None
    try:  # bossové a loot jsou bonus, stránka musí vzniknout i bez nich
        bosses = json.loads((ROOT / "bosses.json").read_text(encoding="utf-8"))["dungeons"]
        loot = boss_loot(rows, best, ok, static, bosses)
    except Exception as exc:
        print(f"Loot: {exc}", file=sys.stderr)
        loot = []
    static.save()
    try:
        catalog = json.loads((ROOT / "quests.json").read_text(encoding="utf-8"))["dungeons"]
        checks = ready_check(token, region, rows, best, ok, catalog)
    except Exception as exc:  # ready check je bonus, stránka musí vzniknout i bez něj
        print(f"Ready check: {exc}", file=sys.stderr)
        checks = []
    built = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    today = local_day(built)
    base = load_baseline(party, built)
    catalog_titles = json.loads((ROOT / "titles.json").read_text(encoding="utf-8"))
    kronika = ROOT / "kronika.json"
    chronicle = sorted(json.loads(kronika.read_text(encoding="utf-8")).get("entries") or [] if kronika.exists() else [],
                       key=lambda x: x["date"], reverse=True)
    titles = evening_titles(ok, base, today, catalog_titles)
    titles.update(chronicle_titles(chronicle, base, today, {m["slug"] for m in ok}))
    counters = party_counters(ok, base)

    OUT.mkdir(exist_ok=True)
    (OUT / "kronika.html").write_text(render_chronicle(party, chronicle, ok), encoding="utf-8")
    # denní snímek pro history/, ranní běh na GitHubu ho commitne (podklad pro kroniku)
    (OUT / "snapshot.json").write_text(json.dumps(
        {"date": today.isoformat(), "built": built,
         "titles": {s: t["key"] for s, t in titles.items()},
         "members": {m["slug"]: {"name": m["name"], **snapshot(m)} for m in ok}},
        ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "index.html").write_text(render(party, members, rows, best, built, checks, base, titles, counters,
                                                    chronicle, today, plan, loot), encoding="utf-8")
    public = [{k: v for k, v in m.items() if k not in ("quests_done", "yday")} for m in members]
    (OUT / "party.json").write_text(json.dumps({"built": built, "members": public, "ready_check": checks,
                                                "baseline": base,
                                                "titles": {s: t["key"] for s, t in titles.items()}}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Hotovo: {len(ok)}/{len(members)} postav, nejlepší dungeon: {best['name'] if best else '-'}, "
          f"ready check: {len(checks)} dungeonů")


if __name__ == "__main__":
    main()
