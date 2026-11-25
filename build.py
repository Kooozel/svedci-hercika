#!/usr/bin/env python3
"""Stáhne postavy party z Blizzard API (záloha: Armory) a vygeneruje statickou stránku do site/."""
from collections import Counter
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
        if "type" not in s:  # jen text („+2 to All Resistances“), odolnosti API posílá zvlášť
            continue
        t = s["type"]["type"]
        out[t] = out.get(t, 0) + s["value"]
    if a := (i.get("armor") or {}).get("value"):
        out["ARMOR"] = out.get("ARMOR", 0) + a
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
         "values": item_stats(i)}
    if i.get("bonus_list"):
        g["bonus"] = i["bonus_list"]
    return g


# Wowhead tooltip pro Forever: staty lootu, který Blizzard API nezná (docs/staty-forever.md, kap. 1 a 4)
WOWHEAD_TOOLTIP = "https://nether.wowhead.com/forever/tooltip/item/{id}"
TOOLTIP_V = 2  # zvýšit při změně parseru, cache se pak stáhne znovu
TOOLTIP_RULES = [  # (regex na řádek, klíče skupin); klíče jsou jako enum statů v Blizzard API, kde ho API má
    (r"(\d+) Armor", ["ARMOR"]),
    (r"([+-]\d+) Armor", ["ARMOR"]),  # bonusový armor
    (r"(\d+) Block", ["BLOCK"]),
    (r"\+(\d+) Block Value", ["BLOCK_VALUE"]),
    (r"\(([\d.]+) damage per second\)", ["DPS"]),
    (r"\+(\d+) (Strength|Agility|Stamina|Intellect|Spirit)\.?", [None]),
    (r"(?:Equip: )?\+(\d+) (Fire|Frost|Nature|Shadow|Arcane) Resistance\.?", [None]),
    (r"Equip: \+(\d+) Attack Power\.", ["ATTACK_POWER"]),
    (r"Equip: Increases damage and healing done by magical spells and effects by up to (\d+) ?\.", ["SPELL_POWER"]),
    (r"Equip: Increases healing done by up to (\d+) and damage done by up to (\d+) .*",
     ["SPELL_HEALING_DONE", "SPELL_DAMAGE_DONE"]),
    (r"Equip: Increases damage done by magical spells and effects by up to (\d+) ?\.", ["SPELL_DAMAGE_DONE"]),
    (r"Equip: Increases damage done by (\w+) spells and effects by up to (\d+) ?\.", [None]),
    (r"(?:Equip: )?Restores \+?(\d+) mana per 5 sec\.", ["MANA_REGEN"]),
    (r"(?:Equip: )?Restores \+?(\d+) health per 5 sec\.", ["HEALTH_REGEN"]),
    (r"Equip: Increased Defense \+(\d+)\.", ["DEFENSE_SKILL_RATING"]),
    (r"Equip: Increases your chance to Block attacks with a shield by ([\d.]+)%\.", ["BLOCK_PCT"]),
]


def tooltip_lines(tip):
    """Řádky textu tooltipu bez setových bonusů a bez patičky (cena, kdo dropí)."""
    tip = re.split(r'<a href="/[^"]*item-set=|<div class="whtt-', tip)[0]
    tip = re.sub(r"<!--.*?-->", "", tip, flags=re.S)
    tip = re.sub(r"<br\s*/?>|</?(?:td|th|tr|table)[^>]*>", "\n", tip)
    tip = html.unescape(re.sub(r"<[^>]+>", "", tip))
    return [ln for ln in (re.sub(r"\s+", " ", x).strip() for x in tip.split("\n")) if ln]


def tooltip_values(tip):
    """(staty, náhodný suffix) z HTML Wowhead tooltipu; efekty bez statu (procy, proti zvířatům…) se nepočítají."""
    out, random = {}, False
    for ln in tooltip_lines(tip):
        if ln == "<Random enchantment>":
            random = True
            continue
        for rx, keys in TOOLTIP_RULES:
            mt = re.fullmatch(rx, ln)
            if not mt:
                continue
            g = mt.groups()
            if keys == [None]:  # klíč je v textu: "+3 Strength", "+5 Shadow Resistance", "Shadow spells … 7"
                num, word = (g[0], g[1]) if g[0][0].isdigit() or g[0][0] in "+-" else (g[1], g[0])
                key = word.upper() + ("_RESISTANCE" if "Resistance" in ln else "_SPELL_DAMAGE" if "spells" in ln else "")
                pairs = [(key, num)]
            else:
                pairs = zip(keys, g)
            for k, v in pairs:
                v = float(v) if "." in v else int(v)
                out[k] = round(out.get(k, 0) + v, 1) if k == "DPS" else out.get(k, 0) + v
            break
    return out, random


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
        extra["stats"], extra["stats_at"] = flat_stats(get("/achievements/statistics"))
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
    """Strom kategorií statistik zploští na {anglický název: počet} a {název: kdy se naposledy změnila (unix s)}.
    API vrací jen nenulové. Čas je herní, ale do API se dostane až s odhlášením (uložením profilu)."""
    out, at = {}, {}

    def walk(cat):
        for s in cat.get("statistics") or []:
            if isinstance(s.get("quantity"), (int, float)):
                out[s["name"]] = int(s["quantity"])
                if s.get("last_updated_timestamp"):
                    at[s["name"]] = s["last_updated_timestamp"] // 1000
        for sub in cat.get("sub_categories") or []:
            walk(sub)

    for cat in data.get("categories") or []:
        walk(cat)
    return out, at


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


def roster(party):
    """Postavy po hráčích, první je hlavní. Hráč bez postav se přeskočí. Starý zápis `characters` znamená jednu postavu na hráče."""
    if party.get("players"):
        return [list(p["chars"]) for p in party["players"] if p.get("chars")]
    return [[slug] for slug in party["characters"]]


def add_nicks(party, members):
    """Přezdívka člověka za postavou (`name` v players), bez ní křestní jméno jeho hlavní postavy."""
    names = {p["chars"][0]: p["name"] for p in party.get("players") or [] if p.get("name") and p.get("chars")}
    mains = {m["player"]: m for m in members if m["ok"] and not m.get("alt")}
    for m in members:
        if m["ok"]:
            m["nick"] = names.get(m["player"]) or mains.get(m["player"], m)["name"].split()[0]


def load_party(party, token):
    region = party["region"]
    members = []
    chars = [(slug, player[0], n > 0) for player in roster(party) for n, slug in enumerate(player)]
    for i, (slug, owner, alt) in enumerate(chars):
        try:
            if not token:
                raise RuntimeError("bez API")
            members.append({**fetch_api(token, region, slug), "player": owner, "alt": alt})
            continue
        except Exception as exc:
            if token:
                print(f"{slug}: API selhalo ({exc}), zkouším Armory", file=sys.stderr)
        if i:
            time.sleep(2)  # Armory je neoficiální zdroj, nebombardujeme ho
        try:
            members.append({**fetch_armory(region, slug), "player": owner, "alt": alt})
        except Exception as exc:  # jedna rozbitá postava nesmí shodit celou stránku
            print(f"{slug}: {exc}", file=sys.stderr)
            if alt:  # alt pod levelem 10 API ani Armory neznají, na stránce prostě zatím není
                continue
            url = ARMORY.format(region=region, slug=urllib.parse.quote(slug))
            members.append({"ok": False, "slug": slug, "url": url, "error": str(exc), "player": owner, "alt": False})
    add_nicks(party, members)
    # ruční role z party.json přebijí odhad podle specu (Johnie tankuje, i když má body v Holy)
    for m in members:
        role = (party.get("roles") or {}).get(m["slug"])
        if m["ok"] and role:
            m["roles"] = [role] if isinstance(role, str) else list(role)
        if gen := (party.get("genitive") or {}).get(m["slug"]):
            m["gen"] = gen
    return members


def level_status(level, d):
    if level < d["min"]:
        return ("under", d["min"] - level)
    if level > d["max"]:
        return ("over", level - d["max"])
    return ("fit", 0)


def pick_char(group, d):
    """Postava hráče pro dungeon: z pasujících naposledy hraná (při shodě vyšší level), jinak levelem nejbližší."""
    fit = [c for c in group if level_status(c["level"], d)[0] == "fit"]
    if fit:
        return max(fit, key=lambda c: (c.get("updated") or "", c["level"]))
    return min(group, key=lambda c: (level_status(c["level"], d)[1], -c["level"]))


def plan_dungeons(members, dungeons, chars=None):
    """Místa v partě jsou hlavní postavy (members), za hráče ale v každém dungeonu počítá jeho nejvhodnější
    postava (`pick`, ze všech postav v chars). Status je klíčovaný hlavní postavou, ať tabulky drží jedno místo na hráče."""
    levels = [m["level"] for m in members]
    lo, hi = min(levels), max(levels)
    lowest = min(m["level"] for m in chars or members)
    groups = {m["slug"]: [c for c in chars or [] if c.get("player") == m["slug"]] or [m] for m in members}
    rows = []
    for d in dungeons:
        status, pick = {}, {}
        for m in members:
            c = pick_char(groups[m["slug"]], d)
            status[m["slug"]], pick[m["slug"]] = level_status(c["level"], d), c["slug"]
        fit = sum(1 for s, _ in status.values() if s == "fit")
        # odehraný dungeon zůstává jen v seznamu všech, z plánu, ready checku a lootu zmizí
        near = d["max"] >= lo and d["min"] <= hi + 5 and not d.get("done")
        # odehraný dungeon, do kterého se ještě někdo levelem vejde (alt), ukáže ready check jako minulý
        past = bool(d.get("done")) and d["max"] >= lowest
        rows.append({**d, "status": status, "pick": pick, "fit": fit, "near": near, "past": past})
    # Víc lidí v rozsahu > vyšší dungeon > užší rozsah
    best = max((r for r in rows if not r.get("done")), key=lambda r: (r["fit"], r["min"], -r["max"]), default=None)
    return rows, best


def default_lineup(r, members):
    """Odhad sestavy pro souhrn příští výpravy: od každého hráče postava, která se do dungeonu levelem vejde,
    stejně jako ve výpravě (pick_char). Když se nevejde nikdo, počítají se hlavní postavy."""
    owner = lambda m: m.get("player", m["slug"])
    groups, picked = {}, {}
    for m in members:
        if r["min"] <= m["level"] <= r["max"]:
            groups.setdefault(owner(m), []).append(m)
    for p, g in groups.items():
        picked[p] = pick_char(g, r)["slug"]
    if not picked:
        for m in members:
            picked.setdefault(owner(m), m["slug"])
    return list(picked.values())


def ready_check(token, region, rows, best, members, catalog):
    """Dungeonové questy z dungeonů v dosahu a stav každé postavy včetně altů.
    `holders` říká, kdo může quest nasdílet: postava jiného hráče, která se do dungeonu levelem vejde a má ho v logu."""
    with_quests = [m for m in members if "quests_active" in m]
    if not token or not with_quests:
        return []
    active = {m["slug"]: {q["id"] for q in m["quests_active"]} for m in with_quests}
    done = {m["slug"]: set(m["quests_done"]) for m in with_quests}
    levels = {m["slug"]: m["level"] for m in with_quests}
    who = {m["slug"]: m for m in with_quests}
    owner = {m["slug"]: m.get("player", m["slug"]) for m in with_quests}
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
    # nejdřív příští výprava, pak dungeony v dosahu, nakonec už odehrané (podívat se, co zbylo altům)
    for r in sorted((r for r in rows if r["near"] or r.get("past")), key=lambda r: (r.get("past", False), r is not best)):
        # křídla jako "Scarlet Monastery: Library" sdílí jeden seznam questů
        title = r["name"] if r["name"] in catalog else r["name"].split(":")[0]
        if title not in catalog or title in seen:
            continue
        seen.add(title)
        quests = []
        lineup = default_lineup(r, with_quests)
        fits = {s for s in active if r["min"] <= levels[s] <= r["max"]}
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
                elif qid in done[slug] or set(q.get("after") or []) & done[slug]:
                    status[slug] = "done"  # after: navazující quest je hotový, tenhle už se brát nedá
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
            finished = all(st in ("done", "na") for st in status.values())
            # hotový quest zůstane s fajfkou jen u příští výpravy, ať je vidět celý seznam; jinde jen překáží
            if finished and r is not best:
                continue
            unclaimed = not any(st in ("active", "done", "pre") for st in status.values())
            # nasdílet může jen postava jiného hráče (dvě své postavy naráz online nemá)
            holders = {}
            if q.get("sharable") and not pre:
                holders = {s: [o for o, other in status.items() if other == "active" and o in fits and owner[o] != owner[s]]
                           for s, st in status.items() if st == "missing"}
                holders = {s: h for s, h in holders.items() if h}
            status = {s: "share" if holders.get(s) else st for s, st in status.items()}
            quests.append({
                "id": qid, "name": q["name"], "xp": det["xp"], "req": q.get("req"), "limit": limit,
                "sharable": q.get("sharable"), "start": q.get("start"), "note": q.get("note"),
                "prev": pre["name"] if pre else None, "status": status, "holders": holders,
                "unclaimed": unclaimed,
                "finished": finished,
            })
        # nejdřív questy, které někdo má, pak volné, nakonec ty, na které zatím nikdo nemůže
        # classové questy až za společnými
        quests.sort(key=lambda q: (q["finished"], q["unclaimed"], all(st in ("level", "chain", "na") for st in q["status"].values()),
                                   q["limit"] is not None))
        # ✋ = musí si někam dojít: pro quest, dokončit rozjetý řetěz nebo udělat předquesty
        take = sum(1 for q in quests for s in lineup if q["status"][s] in ("missing", "pre", "chain"))
        share = sum(1 for q in quests for s in lineup if q["status"][s] == "share")
        past = r.get("past", False)
        if quests and (r is best or past or any(not q["unclaimed"] for q in quests)) and not all(q["finished"] for q in quests):
            checks.append({"title": title, "min": r["min"], "max": r["max"], "difficulty": r.get("difficulty"),
                           "best": r is best, "past": past, "done": r.get("done") if past else None,
                           "quests": quests, "members": list(active), "lineup": lineup, "take": take, "share": share,
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

    def tooltip(self, iid):
        """Staty z Wowhead tooltipu ({"v", "values", "random"}); nepotřebuje API klíče."""
        store = self.data.setdefault("tooltips", {})
        hit = store.get(str(iid))
        if hit and hit.get("v") == TOOLTIP_V:
            return hit
        try:
            d = http_json(urllib.request.Request(WOWHEAD_TOOLTIP.format(id=iid), headers={"User-Agent": USER_AGENT}))
        except Exception as exc:
            print(f"tooltip {iid}: {exc}", file=sys.stderr)
            return hit
        values, random = tooltip_values(d.get("tooltip") or "")
        bop = "Binds when picked up" in tooltip_lines(d.get("tooltip") or "")
        store[str(iid)] = hit = {"v": TOOLTIP_V, "values": values, **({"random": True} if random else {}),
                                 **({"bop": True} if bop else {})}
        self.dirty = True
        time.sleep(0.2)  # ohleduplně k Wowheadu, stahuje se jen nové
        return hit

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


BIG_UP, SMALL_UP = (0.2, 4), (0.1, 1)  # o kolik lepší skóre: podíl toho, co nosí, a nejméně bodů


def item_score(values, slot, w):
    """Body kusu podle vah (weights_for): staty × váha a DPS zbraně × body za DPS v tom slotu."""
    stats = w["stats"]
    score = sum(v * stats.get(k, 0) for k, v in values.items() if k != "DPS")
    return round(score + (values.get("DPS") or 0) * w["weapon"].get(slot, 0), 2)


def stat_gain(item, slots, worn, m):
    """"big" / "small" / None podle skóre, False = staty nevíme (kus, nošená výbava nebo váhy)."""
    w = m.get("weights")
    old_items = [worn[s] for s in slots]
    if item["inv"] == "TWOHWEAPON" and "OFF_HAND" in worn:  # 2H nahradí main hand i off hand
        old_items = [worn["MAIN_HAND"], worn["OFF_HAND"]]
    if not w or "values" not in item or any("values" not in g for g in old_items):
        return False
    new = item_score(item["values"], slots[0], w)
    if item["inv"] == "TWOHWEAPON":
        old = sum(item_score(g["values"], g["slot"], w) for g in old_items)
    else:  # prsten a trinket proti slabšímu z obou
        old = min(item_score(g["values"], g["slot"], w) for g in old_items)
    delta = new - old
    if delta >= max(BIG_UP[0] * old, BIG_UP[1]):
        return "big"
    if delta >= max(SMALL_UP[0] * old, SMALL_UP[1]):
        return "small"
    return None


def upgrade(item, m):
    """None = nehodí se nebo nestojí za to, "empty" = má ten slot prázdný, "big" / "small" = upgrade podle statů
    a vah, číslo = o kolik item levelů je lepší (když staty nevíme). Slabší typ zbroje (látka pro paladina)
    jen do prázdného slotu."""
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
    gain = stat_gain(item, slots, worn, m)
    if gain is not False:
        return gain
    diff = (item.get("level") or 0) - min((worn[s].get("level") or 0) for s in slots)
    return diff if diff > 0 else None


# ---------- bossové a loot (bosses.json) ----------

WEAPON_INV = {"WEAPON", "WEAPONMAINHAND", "WEAPONOFFHAND", "TWOHWEAPON", "RANGED", "RANGEDRIGHT", "THROWN"}
HYBRIDS = {"paladin", "shaman", "druid"}
SCHOOL_FOR = {"Shadow": {"priest", "warlock"}, "Frost": {"mage"}, "Arcane": {"mage"}, "Fire": {"mage", "warlock"},
              "Nature": {"druid", "shaman"}}


def weights_for(m, catalog):
    """Váhy statů z weights.json pro classu, roli a spec: {"stats": {STAT: body za jednotku}, "weapon": {slot: body
    za +1 DPS}}. Hlavní role je první z roles, bez specu dps. None = classa bez profilu."""
    cls, role = m.get("class_slug"), ((m.get("roles") or ["dps"])[0])
    spec = (m.get("spec") or "").lower().replace(" ", "-")
    profiles = catalog.get("profiles") or {}
    prof = next((profiles[k] for k in (f"{cls} {role} {spec}", f"{cls} {role}", cls) if k in profiles), None)
    if prof is None:
        return None
    tiers, units = catalog.get("tiers") or {}, catalog.get("units") or {}
    stats = {}
    for tier in ("minor", "main"):  # hlavní přebije vedlejší, kdyby byl stat v obou
        for s in prof.get(tier) or []:
            stats[s] = round(tiers.get(tier, 0) * units.get(s, 1), 4)
    return {"stats": stats, "weapon": dict(prof.get("weapon") or {})}


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
    tip = static.tooltip(raw["id"]) if raw.get("id") else None
    values = {**((tip or {}).get("values") or {}), **(raw.get("values") or {})}  # ručně zapsané má přednost
    if values:
        it["values"] = values
    if (tip or {}).get("random"):
        it["random"] = True
    it.setdefault("name", "?")
    it.setdefault("kind", "Weapon" if it.get("inv") in WEAPON_INV else "Armor")
    return it


def loot_fit(item, m):
    """Komu se kus hodí. None = ne (classa ho neunese, jiný stat, slabší typ zbroje, caster s 2H bez staffu).
    Jinak gain: "empty" prázdný slot, "big" / "small" upgrade podle statů, číslo o kolik ilvl je lepší (staty
    neznáme), None nosí stejně dobré nebo lepší, "?" neznámý item level nebo výbava; lvl = kolik levelů chybí na req; second = druhá volba
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
    for r in sorted((r for r in rows if r["near"] or r.get("past")), key=lambda r: (bool(r.get("past")), r is not best)):
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

        out.append({"title": title, "min": r["min"], "max": r["max"], "best": r is best, "past": bool(r.get("past")),
                    "source": d.get("source"), "tactics": d.get("tactics"), "note": d.get("note"),
                    "sets": d.get("sets") or {},
                    "bosses": [{"name": b["name"], "tip": b.get("tip"), "rare": b.get("rare"), "trash": b.get("trash"),
                                "quip": b.get("quip"), "boe": b.get("boe"),
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
            tip = static.tooltip(rec["item"]) if it.get("inv") in ITEM_SLOTS else None
            if tip and tip.get("values"):
                it = {**it, "values": tip["values"]}  # API staty receptových kusů nemá, bere se Wowhead
            bop = bool(tip and tip.get("bop"))  # Binds when picked up: jen pro výrobce, předat nejde
            for t in members:
                if bop and t["slug"] != m["slug"]:
                    continue
                d = upgrade(it, t)
                if d is None:
                    continue
                key = (t["slug"], ITEM_SLOTS[it["inv"]][0])
                w = t.get("weights")
                points = item_score(it["values"], key[1], w) if w and "values" in it else 0
                score = (points, it.get("level") or 0, t["slug"] == m["slug"])
                if key not in best or score > best[key]["score"]:
                    reagents = []
                    for iid, qty in rec["reagents"]:
                        who = supplier(iid, m["slug"])
                        if who:
                            reagents.append({"name": (static.item(iid) or {}).get("name", "?"), "qty": qty, "from": who})
                    best[key] = {"score": score, "item": {**it, "id": rec["item"]}, "crafter": m["slug"], "prof": r.get("prof"),
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

    capped = [(m["slug"], p["name"], p["skill"]) for m in members if not m.get("alt") for p in m.get("professions") or []
              if p["primary"] and p["max"] and p["skill"] >= p["max"] and p["max"] < 300]
    return {"ups": ups, "enchants": enchants, "capped": capped, "names": {s: nick(m) for s, m in by_slug.items()}}


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
            "stats": m.get("stats"), "stats_at": m.get("stats_at"), "achievements": m.get("achievements"), "updated": m.get("updated"),
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


def history_members(chars, base):
    """Do denní historie jde hlavní postava vždy, alt jen když se od posledního snímku změnil."""
    old = (base or {}).get("members") or {}
    out = {}
    for m in chars:
        row = {"name": m["name"], **snapshot(m)}
        if not m.get("alt") or json.dumps(row, sort_keys=True) != json.dumps(old.get(m["slug"]), sort_keys=True):
            out[m["slug"]] = row
    return out


def load_baseline(party, built):
    """Stav před včerejším večerem: poslední ranní snímek z history/ před dneškem.
    Je v repu, takže ho večerní push neposune. Bez historie záloha přes party.json z nasazené stránky."""
    past = load_history(local_day(built))
    if past:
        snap = past[-1]
        if any("attrs" not in b for b in snap["members"].values()):
            add_attrs(snap["members"], fetch_deployed(party))
        members = {}  # alt, který nehrál, v posledním snímku není, platí jeho poslední zápis
        for day in past:
            members.update(day["members"])
        return {"at": snap["built"], "date": snap["date"], "titles": snap.get("titles") or {},
                "recent_titles": [p.get("titles") or {} for p in reversed(past[-TITLE_PAUSE:])],
                "members": members}
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
    return {"from": b["level"], "to": m["level"], "xp": max(0, gained), "profs": profs, "gear": gear, "ach": ach,
            "learned": learned}


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


def person_values(chars, base):
    """Hodnoty pro tituly za člověka: součet všech jeho postav. Vrací totéž co evening_values,
    v info navíc `top`: která postava ke statistice přispěla nejvíc."""
    rows = [(m, *evening_values(m, base)) for m in chars]
    if len(rows) == 1:
        return rows[0][1:]
    vals = {}
    for _, v, _, _ in rows:
        for k, x in v.items():
            vals[k] = vals.get(k, 0) + x
    top = {k: max(rows, key=lambda r: r[1].get(k, 0))[0] for k in vals}
    pick = lambda stat, field: next((r[3][field] for r in sorted(rows, key=lambda r: -r[1].get(stat, 0)) if r[3][field]), None)
    if "_skill" in vals:  # řemeslo se nesčítá, platí největší skok
        vals["_skill"] = max(r[1].get("_skill", 0) for r in rows)
    olds = [(((base or {}).get("members") or {}).get(m["slug"]) or {}).get("stats") or {} for m in chars]
    firsts = {k for r in rows for k in r[3]["firsts"] if not any(o.get(k) for o in olds)}  # premiéra jen, když to neumí žádná jeho postava
    info = {"skill": pick("_skill", "skill"), "firsts": firsts, "zone": pick("_explore", "zone"),
            "new": all(r[3]["new"] for r in rows), "top": top}  # nový alt z člověka nováčka nedělá
    return vals, any(r[2] for r in rows), info


def pick_variant(variants, today, slug):
    """Varianta podle dne a postavy: každý den jiná, ale při opakovaném buildu stejná."""
    return variants[(today.toordinal() + zlib.crc32(slug.encode())) % len(variants)]


class Quips:
    """Hlášky z hlasky.json. label: štítek, celý den stejný na celé stránce (sedí s legendou).
    line: věta u postavy, jiná podle dne a postavy, na jedné stránce se neopakuje."""

    def __init__(self, data=None, today=None):
        self.data, self.day, self.used = data or {}, today.toordinal() if today else 0, set()

    def variants(self, key):
        v = self.data
        for k in key.split("."):
            v = v.get(k) if isinstance(v, dict) else None
        return v if isinstance(v, list) else [v] if isinstance(v, str) else []

    def label(self, key, default=""):
        vs = self.variants(key)
        return vs[(self.day + zlib.crc32(key.encode())) % len(vs)] if vs else default

    def line(self, key, seed="", default="", **fmt):
        """seed: postava nebo jiný klíč, podle kterého se varianta liší."""
        vs = self.variants(key) or ([default] if default else [])
        if not vs:
            return ""
        start = self.day + zlib.crc32((seed + "|" + key).encode())
        order = [vs[(start + i) % len(vs)] for i in range(len(vs))]
        pick = next((v for v in order if v not in self.used), order[0])
        self.used.add(pick)
        for k, x in fmt.items():
            pick = pick.replace("{" + k + "}", str(x))
        return pick


def read_quips():
    try:
        return json.loads((ROOT / "hlasky.json").read_text(encoding="utf-8"))
    except Exception as exc:  # bez hlášek zůstanou výchozí texty
        print(f"Hlášky: {exc}", file=sys.stderr)
        return {}


Q = Quips(read_quips())  # render() ho přenastaví na dnešek


# kolik z nejlepšího výkonu party stačí na titul za statistiku
LEAD = 0.75
# kolik posledních dní se stejná statistika stejnému člověku nedá znova
# (warlock vede v healthstonech skoro denně, ob den by měl pořád ten samý)
TITLE_PAUSE = 2


def evening_titles(members, base, today, catalog):
    """Každý člověk dostane jeden titul za všechny své postavy (klíč = jeho hlavní postava),
    žádná statistika se nedá dvakrát a nikdo nedostane stejnou jako v posledních TITLE_PAUSE dnech."""
    people = {}
    for m in members:
        if m.get("stats") is not None:
            people.setdefault(m.get("player", m["slug"]), []).append(m)
    yesterday = (base or {}).get("titles") or {}
    recent = (base or {}).get("recent_titles") or [yesterday]
    def lately(s, key):
        return any(t.get(s) == key for t in recent)
    values, played, info = {}, {}, {}
    for s, chars in people.items():
        values[s], played[s], info[s] = person_values(chars, base)
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
            if v >= rule["min"] and v >= LEAD * top and played[s] and not lately(s, stat):
                # podíl na nejlepším + vtipnost + velikost čísla (185 nápojů přebije 2 zvířátka)
                score = v / top + rule["bonus"] + 0.1 * math.log2(v / rule["min"])
                candidates.append((score, v / rule["min"], s, stat, v))
    # premiéra: postava něco udělala úplně poprvé (Johnie snědl první jídlo), klíč _first:<statistika>
    premiere = catalog.get("first")
    if premiere and daily:
        for s in values:
            for stat in info[s]["firsts"]:
                key = "_first:" + stat
                if stat in stats and not stat.startswith("_") and played[s] and not lately(s, key):
                    rules[key] = {**premiere, "forms": stats[stat]["forms"]}
                    # čím víc to ostatní dělají, tím vtipnější premiéra (první jídlo > první vzdaný quest)
                    others = sum(((b or {}).get("stats") or {}).get(stat, 0)
                                 for slug, b in base["members"].items() if slug not in {m["slug"] for m in people[s]})
                    candidates.append((1 + premiere["bonus"] + 0.05 * math.log2(1 + others), 1, s, key, values[s][stat]))
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
        if hero := (info[s].get("top") or {}).get(stat.removeprefix("_first:")):
            out[s]["char"] = hero["name"]
        used |= {stat, group}
    for s in people:
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


def chronicle_titles(chronicle, base, today, slugs, owners=None):
    """Tituly napsané ručně u zápisu v kronice: platí jen první den po výpravě (večer, za který se tituly dávají).
    Titul pro alta (owners: postava → hlavní postava) dostane jeho hráč."""
    since = (base or {}).get("date")
    if not since:  # bez včerejšího stavu nevíme, který večer to byl
        return {}
    out = {}
    for entry in chronicle:
        if since <= entry["date"] < today.isoformat():
            for s, t in (entry.get("titles") or {}).items():
                s = (owners or {}).get(s, s)
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


def nick(m):
    return m.get("nick") or first(m)


def who(m):
    """Jednotné jméno postavy: přezdívka hráče · celé jméno postavy (Kozel · Kozel Mourningale)."""
    return f'{nick(m)} · {m["name"]}'


def char_label(m):
    """Celé jméno postavy, s hráčem, když se jmenuje jinak než on: Cildac Velky (Fanda)."""
    return m["name"] if nick(m) == first(m) else f'{m["name"]} ({nick(m)})'


def shown_char(group):
    """Koho ukázat za hráče: naposledy hranou postavu, při shodě hlavní."""
    return max(group, key=lambda c: c.get("updated") or "")


def top_char(group):
    """Výchozí „moje“ postava hráče: nejvyšší level, při shodě naposledy hraná. Jinou si zvolí v liště."""
    return max(group, key=lambda c: (c.get("level") or 0, c.get("updated") or ""))


def first_gen(m):
    """Druhý pád křestního jména (od Zrzy), z party.json; bez něj první pád."""
    return m.get("gen") or nick(m)


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
        tip = f'{g["name"]} · {label}' + (f' · 🩴 hadr, {Q.label("weak", "tohle by chtělo vyměnit")}' if weak else "")
        return (f'<a class="sl{weak}" style="--q:{color}" href="https://www.wowhead.com/forever/item={g["id"]}" '
                f'target="_blank" rel="noopener" title="{e(tip)}">{inner}</a>')
    two_hand = (worn.get("MAIN_HAND") or {}).get("inv") == "TWOHWEAPON"
    if (slot == "OFF_HAND" and two_hand) or (slot == "RANGED" and m["class_slug"] in NO_RANGED):
        return f'<span class="sl sl-na" title="{e(label)}: nepotřebuje"></span>'
    return f'<span class="sl sl-empty" title="{e(label)}: prázdné">{e(label)}</span>'


CRAFTSMAN = {"Alchemy": "alchymista", "Blacksmithing": "kovář", "Enchanting": "enchanter", "Engineering": "inženýr",
             "Herbalism": "bylinkář", "Leatherworking": "koželuh", "Mining": "horník", "Skinning": "stahovač kůží",
             "Tailoring": "krejčí", "Cooking": "kuchař", "First Aid": "zdravotník", "Fishing": "rybář"}
ACTIVITY = {"Alchemy": "alchymie", "Blacksmithing": "kovařina", "Enchanting": "enchanting", "Engineering": "inženýrství",
            "Herbalism": "bylinkaření", "Leatherworking": "koželužství", "Mining": "hornictví",
            "Skinning": "stahování kůží", "Tailoring": "krejčovina", "Cooking": "vaření", "First Aid": "obvazování",
            "Fishing": "rybaření"}
CRAFT_VERB = {"Tailoring": "ušít", "Blacksmithing": "ukovat", "Leatherworking": "sešít z kůže",
              "Engineering": "smontovat", "Alchemy": "uvařit"}


def prof_num(p):
    """Skill řemesla číslem, se stropem, když ho API dá."""
    return f'{p["skill"]}/{p["max"]}' if p.get("max") else str(p["skill"])


def prof_bars(m):
    rows = []
    for p in sorted(m.get("professions") or [], key=lambda p: (not p["primary"], -p["skill"])):
        rows.append(f'<li class="{"" if p["primary"] else "pb-sec"}">'
                    f'<span>{e(p["name"])}</span><b>{prof_num(p)}</b></li>')
    return f'<ul class="pbars">{"".join(rows)}</ul>' if rows else ""


def doll(m):
    """Výbava kolem renderu postavy jako okno postavy ve hře: ikony v rámečku barvy kvality,
    prázdné sloty čárkovaně, nejslabší kus označený 🩴 (hadr)."""
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


# řádky „Jak na tom je“: (štítek, klíč z cstats); HP mají všichni, zbytek podle role a classy
STAT_ROWS = {"tank": [("Armor", "armor"), ("Attack Power", "attack_power")],
             "heal": [("Healing", "spell_healing"), ("Intellect", "intellect")],
             "hunter": [("Ranged AP", "ranged_attack_power"), ("Agility", "agility")],
             "caster": [("Spell Power", "spell_power"), ("Intellect", "intellect")],
             "melee": [("Attack Power", "attack_power"), ("Strength", "strength")]}


def stat_values(m):
    """[(štítek, hodnota)] pro kartu: HP a dva hlavní staty role, nulové (spell power na nízkém levelu) vynechá."""
    c = m.get("cstats") or {}

    def v(key):
        x = c.get(key) or 0
        return x[1] if isinstance(x, (tuple, list)) else x
    cls, roles = m.get("class_slug"), m.get("roles") or []
    kind = ("tank" if "tank" in roles else "heal" if "heal" in roles else "hunter" if cls == "hunter"
            else "caster" if cls in CASTERS else "melee")
    rows = [("HP", v("health"))] + [(label, v(key)) for label, key in STAT_ROWS[kind]]
    return [(label, round(x)) for label, x in rows if x]


def stat_rows(m):
    """HP a hlavní staty role, víc je na Armory."""
    if not m.get("cstats"):
        return ""
    if sick(m):
        return sickness(m)
    rows = "".join(f'<li><span>{label}</span><b>{num(x)}</b></li>' for label, x in stat_values(m))
    return f'<div class="st-group st-body"><h4>Jak na tom je</h4><ul>{rows}</ul></div>'


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


# co postavě chybí, jak by to řekl hospodský (prsteny a cetky zvlášť, mají dva sloty)
BARE = {"HEAD": "bez čepice", "NECK": "s holým krkem", "SHOULDER": "s holými rameny", "BACK": "bez pláště",
        "CHEST": "s holou hrudí", "WRIST": "s holými zápěstími", "HANDS": "bez rukavic", "WAIST": "bez opasku",
        "LEGS": "bez kalhot", "FEET": "naboso", "MAIN_HAND": "beze zbraně", "OFF_HAND": "s volnou levačkou",
        "RANGED": "bez střelné zbraně"}
BARE_PAIR = {"FINGER": ("jen s jedním prstenem", "bez prstenů"), "TRINKET": ("jen s jednou cetkou", "bez cetek")}


def and_join(words):
    return " a ".join([", ".join(words[:-1]), words[-1]]) if len(words) > 1 else "".join(words)


def bare_line(missing):
    """„Chodí bez čepice, s holým krkem a bez prstenů.“ ze seznamu prázdných slotů."""
    words = []
    for slot in DOLL_LEFT + DOLL_RIGHT + DOLL_BOTTOM:
        if slot not in missing:
            continue
        pair = slot.rsplit("_", 1)[0]
        if pair in BARE_PAIR:
            if slot.endswith("_1") or f"{pair}_1" not in missing:
                words.append(BARE_PAIR[pair][sum(f"{pair}_{i}" in missing for i in (1, 2)) - 1])
        else:
            words.append(BARE[slot])
    return f"Chodí {and_join(words)}." if words else ""


def sheet_side(m):
    gi = gear_info(m) if "gear" in m else None
    bits = []
    if gi:
        worn = {g["slot"] for g in gi["gear"]}
        two_hand = any(g["slot"] == "MAIN_HAND" and g["inv"] == "TWOHWEAPON" for g in gi["gear"])
        missing = [s for s in DOLL_LEFT + DOLL_RIGHT + DOLL_BOTTOM if s not in worn
                   and not (s == "OFF_HAND" and two_hand) and not (s == "RANGED" and m["class_slug"] in NO_RANGED)]
        bare = bare_line(missing)
        gap = f'<p class="d-gap">{e(bare)} {e(Q.line("gap_tail", m["slug"]))}</p>' if bare else ""
        outfit = f'<p class="d-outfit">Průměrný item level <b>{gi["avg"]}</b></p>' if gi["avg"] else ""
        bits.append(f'<div class="d-sum">{outfit}{gap}</div>')
    bits.append(stat_rows(m))
    if m.get("professions"):
        bits.append(f'<div class="st-group"><h4>Řemesla</h4>{prof_bars(m)}</div>')
    pet = m.get("pet")
    if pet:
        bits.append(f'<p class="d-pet">🐾 <b>{e(pet["name"])}</b> <small>{e(pet["creature"] or "")}, lvl {pet["level"]}</small></p>')
    return f'<aside class="sheet-side">{"".join(bits)}</aside>'


def xp_bar(m, big=False):
    if not m.get("xp_next"):
        return ""
    pct = min(100, round(100 * m["xp"] / m["xp_next"]))
    label = f'<span class="xp-num">{pct} % do {m["level"] + 1}</span>' if big else ""
    return (f'<div class="xp" title="{num(m["xp"])} / {num(m["xp_next"])} XP">{label}'
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


def char_tabs(m, group):
    """Přepínač postav hráče v kartě: celé jméno a level každé postavy."""
    if len(group) < 2:
        return ""
    tabs = "".join(f'<button type="button" data-open="{e(c["slug"])}" aria-pressed="{str(c is m).lower()}" '
                   f'style="--cls:{cls_color(c)}" title="{e(c["class"])}"><b>{e(c["name"])}</b> <i>{c["level"]}</i></button>'
                   for c in group)
    return f'<nav class="sheet-alts" aria-label="Postavy hráče {e(nick(m))}">{tabs}</nav>'


def sheet(m, today, group=()):
    """Karta postavy, která se otevře pod skupinovkou po kliknutí na postavu (na mobilu jako panel zespodu).
    group = všechny postavy hráče, mezi nimi se v kartě přepíná."""
    spec = e(m["spec"]) if m["spec"] else ""
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
  {char_tabs(m, group)}
  {xp_bar(m, big=True)}
  <div class="sheet-body">{doll(m)}{sheet_side(m)}</div>
  <p class="updated">{e(m["updated_label"])}: {days_ago(m.get("updated"), today)}</p>
</article>'''


# ---------- hlavička ----------

AHEAD_UNTIL = 40  # Příští výprava ukazuje dungeony, které začínají pod tímhle levelem
AHEAD_BEFORE, AHEAD_AFTER = 2, 3  # na mobilu jen tolik dungeonů před a po příští výpravě


def walk_level(r):
    """Poslední level, kdy dungeon ještě není procházka: konec rozsahu, nebo Wowhead Easy, co přijde dřív."""
    diff = r.get("difficulty")
    return min(r["max"], diff[3]) if diff else r["max"]


def reserve_char(group, r):
    """Postava hráče pro Kam dál: z těch, co se do dungeonu vejdou, ta s největší rezervou do procházky,
    jinak nejbližší pod rozsahem, a když jsou všechny nad ním, ta nejnižší."""
    cap = walk_level(r)
    inside = [c for c in group if r["min"] <= c["level"] <= cap]
    below = [c for c in group if c["level"] < r["min"]]
    if inside:
        return min(inside, key=lambda c: c["level"])
    if below:
        return max(below, key=lambda c: c["level"])
    return min(group, key=lambda c: c["level"])


def player_groups(members, by_slug):
    """Za každou hlavní postavu (místo v partě) všechny postavy jejího hráče."""
    chars = list(by_slug.values()) or members
    return [[c for c in chars if c.get("player", c["slug"]) == m.get("player", m["slug"])] or [m] for m in members]


def ahead_rows(rows, members, by_slug=None):
    """Dungeony začínající pod AHEAD_UNTIL, které aspoň pro jednu postavu ještě nejsou procházka, nejdřív ty, co utečou."""
    groups = player_groups(members, by_slug or {})
    keep = [r for r in rows if r["min"] < AHEAD_UNTIL
            and any(reserve_char(g, r)["level"] <= walk_level(r) for g in groups)]
    return sorted(keep, key=lambda r: (walk_level(r), r["min"]))


def ahead_cell(r, m):
    """(css třída, text, tooltip) pro jednu postavu: kolik levelů zbývá do procházky."""
    lvl, cap, name = m["level"], walk_level(r), f'{who(m)} ({m.get("class") or "?"})'
    if lvl < r["min"]:
        return "low", f"za {r['min'] - lvl}", f"{name}: dungeon od levelu {r['min']}"
    if lvl > cap:
        return "late", "pozdě", f"{name}: od levelu {cap + 1} už procházka"
    left = cap - lvl
    kind = "last" if left == 0 else "soon" if left <= 2 else "ok"
    return kind, str(left), f"{name}: ještě {left} lvl, pak procházka" if left else f"{name}: poslední level, pak procházka"


def ahead_text(t):
    """Na mobilu krátce: 'za 3' -> '3' (čárkovaný okraj zůstane), 'pozdě' -> '–'."""
    if t == "pozdě":
        return '<span class="ah-long">pozdě</span><span class="ah-short">–</span>'
    if t.startswith("za "):
        return f'<span class="ah-long">za </span>{e(t[3:])}'
    return e(t)


def short_dungeon(name):
    return name.replace("Scarlet Monastery:", "SM:").replace("Excavation Site: ", "")


def ahead_table(rows, best, members, by_slug=None):
    """Sloupec za hráče (members = hlavní postavy), v řádku jeho postava s největší rezervou (reserve_char).
    Stránka do sloupce vybraného hráče (a ručně přepnutých v ready checku) dá jeho zvolenou postavu, proto data-min a data-cap."""
    by_slug = by_slug or {}
    groups = player_groups(members, by_slug)
    pl = [e(m.get("player", m["slug"])) for m in members]
    head = "".join(f'<th data-player="{p}" style="--cls:{cls_color(m)}" title="{e(nick(m))}"><span class="ah-long">{e(nick(m))}</span>'
                   f'<span class="ah-short">{e(nick(m)[:3])}</span></th>' for p, m in zip(pl, members))
    body, far = [], 0
    shown = ahead_rows(rows, members, by_slug)
    old = lambda r: bool(r.get("done")) and r is not best
    live = [r for r in shown if not old(r)]
    at = next((i for i, r in enumerate(live) if r is best), None)
    n_old = sum(map(old, shown))
    for r in shown:
        # odehrané sbalené pod „odehrané (N)“ (desktop i mobil); na mobilu navíc jen příští výprava a pár dungeonů kolem
        hide = not old(r) and at is not None and not at - AHEAD_BEFORE <= live.index(r) <= at + AHEAD_AFTER
        far += hide
        if old(r) and not any(old(x) for x in shown[:shown.index(r)]):
            body.append(f'<tr class="ah-old-row"><td colspan="{len(members) + 2}"><button type="button" class="ah-old-btn" data-n="{n_old}">'
                        f'✓ odehrané ({n_old}) ▾</button></td></tr>')
        classes = " ".join(c for c, on in (("ah-best", r is best), ("ah-done ah-old", old(r)), ("ah-far", hide)) if on)
        cls = f' class="{classes}"' if classes else ""
        cells = ""
        for p, m, g in zip(pl, members, groups):
            c = reserve_char(g, r)
            k, t, tip = ahead_cell(r, c)
            # jde jiná postava než hlavní: tečka v barvě jejího povolání
            alt = f' ah-alt" style="--alt:{cls_color(c)}' if c is not m else ""
            cells += f'<td data-player="{p}"><span class="ah ah-{k}{alt}" title="{e(tip)}">{ahead_text(t)}</span></td>'
        mark = "✓ " if r.get("done") else ""
        body.append(f'<tr{cls} data-min="{r["min"]}" data-cap="{walk_level(r)}"><th scope="row" title="{e(r["name"])}, level {r["min"]}–{r["max"]}, {e(r["zone"])}">'
                    f'{mark}{e(short_dungeon(r["name"]))}</th><td class="ah-cap">{walk_level(r)}</td>{cells}</tr>')
    if not body:
        return ""
    return (f'<p class="eyebrow">Kam dál · levely do procházky</p>'
            f'<div class="ah-wrap"><table class="ahead"><thead><tr><th>Dungeon</th>'
            f'<th title="poslední level, kdy to ještě není procházka (konec rozsahu nebo Wowhead Easy)"><span class="ah-long">do lvl</span><span class="ah-short">lvl</span></th>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>'
            f'{f"<button type=button class=ah-more data-n={far}>další dungeony ({far}) ▾</button>" if far else ""}'
            f'<p class="ah-legend"><span class="ah ah-ok">5</span> rezerva <span class="ah ah-soon">2</span> brzy uteče '
            f'<span class="ah ah-last">0</span> poslední level <span class="ah ah-low">za 3</span> ještě nízko '
            f'<span class="ah ah-late">pozdě</span> procházka <span class="ah ah-ok ah-alt" style="--alt:var(--muted)">5</span> jde alt (tečka = barva povolání)</p>'
            f'<p class="ah-note"><b>do lvl</b> = poslední level, kdy dungeon ještě není procházka (konec rozsahu, nebo dřív podle Wowhead Easy)</p>')


def picked(r, m, by_slug):
    """Postava, kterou hráč (jeho hlavní postava m) do dungeonu r pošle."""
    return by_slug.get((r.get("pick") or {}).get(m["slug"]), m)


def next_box(rows, best, members, checks, chars=None):
    """Příští výprava: dungeon, souhrn ready checku a kam dál, než dungeony přelevlíme."""
    if not best:
        return ""
    by_slug = {c["slug"]: c for c in chars or members}
    check = next((c for c in checks if c["best"]), None)
    rc = next_ready(check, best, members, by_slug) if check else ""
    note = f' · {e(best["note"])}' if best.get("note") else ""
    return (f'<section class="next" id="vyprava"><p class="eyebrow">Příští výprava</p>'
            f'<h2>{e(best["name"])}{"<span class=chip chip-new>nový</span>" if best.get("new") else ""}</h2>'
            f'<p class="next-meta"><span class="nx-range">level {best["min"]}–{best["max"]}</span>'
            f'<span>{e(best["zone"])}{note}</span></p>'
            f'{rc}{ahead_table(rows, best, members, by_slug)}</section>')


def next_ready(check, best, members, by_slug):
    """Souhrn ready checku příští výpravy: za každého hráče, co jeho postavě chybí.
    Stav všech postav, které se do dungeonu vejdou, je v data-todo, ať stránka napíše i stav vybrané postavy („Ty“)."""
    fits = [c for c in by_slug.values() if c.get("level") and check["min"] <= c["level"] <= check["max"]
            and all(c["slug"] in q["status"] for q in check["quests"])]
    todo = {c["slug"]: todo_state(*char_todo(check, c)) for c in fits}
    items = []
    for m in members:
        # stránka hráče přepíše postavou z jeho sloupce v ready checku; bez stavu (nevejde se) zůstane skrytý
        c = picked(best, m, by_slug)
        state = todo.get(c["slug"])
        ok = state == "✓ připraveno"
        items.append(f'<span class="nx-p {"nx-p-ok" if ok else "nx-p-todo"}" data-player="{e(m.get("player", m["slug"]))}" '
                     f'data-def="{e(c["slug"])}" style="--cls:{cls_color(c)}"{"" if state else " hidden"}>'
                     f'<b>{e(nick(m))}</b> {e(state or "")}</span>')
    data = json.dumps(todo, ensure_ascii=False).replace("<", "\\u003c")
    return (f'<a class="nx-rc" href="#ready" data-todo="{e(data)}" data-dungeon="{e(check["title"])}">'
            f'<span class="nx-you" hidden></span>{"".join(items)}<span class="nx-p nx-p-ok nx-rest" hidden>✓ ostatní připravení</span><span class="nx-go">celý ready check ↓</span></a>')


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


def level_share(ch):
    """Kolik procent levelu postava nahrála; při level upu se neukazuje."""
    need = XP_TO_NEXT[ch["from"] - 1] if 0 < ch["from"] <= len(XP_TO_NEXT) else 0
    return f"{round(100 * ch['xp'] / need)} %" if need and ch["xp"] and ch["to"] == ch["from"] else ""


def prof_news(m, ch):
    """Profese v novinkách: nové řemeslo a body skillu číslem."""
    out = []
    new = [name for name, d in ch["profs"] if d is None]
    ups = [f"{name} +{d}" for name, d in ch["profs"] if d]
    if new:
        out.append(f"🎒 Nové řemeslo: {', '.join(new)}.")
    if ups:
        out.append(f"🛠️ {' · '.join(ups)}")
    if ch["learned"]:
        n = len(ch["learned"])
        name = ch["learned"][(Q.day + zlib.crc32(m["slug"].encode())) % n]  # každý den jiný příklad
        out.append(f"📜 Nový recept: {name}." if n == 1 else
                   f"📜 {pl(n, ('{n} nový recept', '{n} nové recepty', '{n} nových receptů'))}, třeba {name}.")
    return out


def news_row(m, ch):
    """Řádek postavy ve sloupcích: celé jméno, level nebo podíl levelu, XP."""
    step = (f'<span class="nw-lvl">{ch["from"]} → {ch["to"]}</span>' if ch["to"] > ch["from"]
            else f'<span class="nw-pct" title="z levelu">{level_share(ch)}</span>')
    xp = f'+{num(ch["xp"])} XP' if ch["xp"] else ""
    return (f'<div class="nw-line" style="--cls:{cls_color(m)}"><span class="nw-char">{e(m["name"])}</span>{step}'
            f'<span class="nw-xp">{xp}</span></div>')


def news_extra(m, ch):
    """Achievementy, řemesla, recepty a nové kusy výbavy."""
    extra = "".join(f'<p class="nw-ach">🏆 {e(a)}</p>' for a in ch["ach"])
    extra += "".join(f'<p class="nw-profs">{e(x)}</p>' for x in prof_news(m, ch))
    gear = "".join(item_link(g, compact=True) for g in ch["gear"])
    return extra + (f"<div class=nw-gear>{gear}</div>" if gear else "")


def news_person(chars, log, fresh):
    """Řádek jednoho člověka: jedna aktivní postava rovnou, víc postav pod sebou a podrobnosti k rozbalení."""
    active = sorted(log, key=lambda x: -x[1]["xp"])
    lead = active[0][0] if active else chars[0]
    body = f'<p class="nw-head"><b class="who-n">{e(nick(lead))}</b></p>'
    if active:
        body += f'<div class="nw-lines">{news_row(*active[0])}</div>{news_extra(*active[0])}'
    if len(active) > 1:
        rest = active[1:]
        more = "".join(f'<div class="nw-det" style="--cls:{cls_color(m)}"><div class="nw-lines">{news_row(m, ch)}</div>'
                       f'{news_extra(m, ch)}</div>' for m, ch in rest)
        label = pl(len(rest), ("+{n} další postava", "+{n} další postavy", "+{n} dalších postav"))
        body += f'<details class="nw-more"><summary>{label}</summary>{more}</details>'
    body += "".join(f'<p class="nw-profs">🆕 Nová postava: {e(m["name"])}, level {m["level"]}</p>' for m in fresh)
    return f'<li style="--cls:{cls_color(lead)}">{avatar(lead, "nw-av")}<div class="nw-main">{body}</div></li>'


def news_box(members, base, today):
    """Novinky po lidech: co nahrály všechny jejich postavy, jeden řádek na člověka."""
    people, rows, quiet = {}, [], []
    for m in members:
        people.setdefault(m.get("player", m["slug"]), []).append(m)
    for chars in people.values():
        known = [(m, ch) for m in chars if (ch := changes(m, base)) is not None]
        if not known:  # úplně nový člověk, přivítá ho titul
            continue
        fresh = [m for m in chars if not any(m is x for x, _ in known)]
        log = [(m, ch) for m, ch in known
               if ch["to"] > ch["from"] or ch["xp"] or ch["profs"] or ch["gear"] or ch["ach"] or ch["learned"]]
        if log or fresh:
            rows.append(news_person(chars, log, fresh))
        else:
            quiet.append(shown_char(chars))
    if quiet:
        names = ", ".join(nick(m) for m in quiet)
        rows.append(f'<li class="nw-quiet"><span class="nw-avs">{"".join(avatar(m, "nw-av") for m in quiet)}</span>'
                    f'<span>{e(names)}: nic nového</span></li>')
    body = f'<ul class="nw">{"".join(rows)}</ul>' if rows else '<p class="muted">Nic nového, nikdo nehrál.</p>'
    return f'<section class="news" id="novinky"><p class="eyebrow">Novinky {since_label(base, today)}</p>{body}</section>'


def title_who(m, t):
    """Kdo titul dostal, u hráče s alty i postava, která k němu přispěla nejvíc.
    Když jméno postavy začíná přezdívkou, stačí celé jméno (Johnie Nemrtvý, ne Johnie · Johnie Nemrtvý)."""
    char = t.get("char")
    if not char:
        return nick(m)
    return char if char.split()[0] == nick(m) else f'{nick(m)} · {char}'


def title_cards(members, titles):
    """Stejný titul (emoji, název i věta) u víc lidí je jedna karta se všemi jmény."""
    groups = {}
    for m in members:
        if t := titles.get(m["slug"]):
            groups.setdefault((t["emoji"], t["title"], t["detail"]), []).append((m, t))
    cards = []
    for (emoji, title, detail), got in groups.items():
        who = ", ".join(f'<span style="color:{cls_color(m)}">{e(title_who(m, t))}</span>' for m, t in got)
        cards.append(f'<li class="title" style="--cls:{cls_color(got[0][0])}"><span class="t-emoji">{emoji}</span>'
                     f'<span class="t-who">{who}</span><b class="t-name">{e(title)}</b>'
                     f'<small>{e(detail)}</small></li>')
    return "".join(cards)


def evening_box(members, base, titles, counters):
    if not titles:
        return ""
    daily = has_daily(base)
    cards = title_cards(members, titles)
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
    hit = [c for c in counters if c["crossed"]]
    summary = "".join(f'<span class="c-s"> · <b>{num(c["total"])}</b> {e(c["label"])}</span>' for c in counters[:3])
    if hit:
        text = (f'{hit[0]["label"]} přes {num(hit[0]["crossed"])}' if len(hit) == 1
                else pl(len(hit), ("{n} milník", "{n} milníky", "{n} milníků")))
        summary += f' · <span class="c-hit">🎉 {e(text)}</span>'
    head = "Tituly za včerejší večer" if daily else "Tituly za celou dobu"
    note = (Q.line("titles_note", "note", "Není to žebříček.")
            if daily else "Od zítřka se tituly budou dávat za včerejší večer.")
    # na šířku nejvýš 5 vedle sebe; víc titulů do vyrovnaných řad (6 = 3 + 3), ať jeden neuskočí sám
    n = len(titles)
    cols = 5 if n <= 5 else -(-n // -(-n // 5))
    return (f'<section id="tituly"><h2 class="sec">{head}</h2><ul class="titles" style="--cols:{cols}">{cards}</ul>'
            f'<details class="counters-box"><summary>Parta dohromady{summary} <span class="c-more">ukázat všech {len(tiles)}</span></summary>'
            f'<ul class="counters">{"".join(tiles)}</ul></details>'
            f'<p class="note" title="Počítá se ze statistik Blizzard API, které se aktualizují, když se postava odhlásí.">{e(note)}</p></section>')


def pc_figure(m, group, shown):
    """Místo hráče ve skupinovce: přezdívka, postava a poznámka, kolik má hráč dalších postav.
    Přepínají se v kartě postavy. Na mobilu je z fotky řádek seznamu s hlavou místo renderu."""
    img = (f'<div class="pc-img" style="background-image:url(\'{e(m["render"])}\')"></div>' if m.get("render")
           else f'<div class="pc-img pc-noimg">{avatar(m, "pc-avatar")}</div>')
    role = "/".join(ROLE_LABEL[r] for r in m["roles"]) or "bez specu"
    others = [c for c in group if c is not m]
    more = (f'<small class="pc-alts" title="{e(", ".join(c["name"] for c in others))}">'
            f'+{pl(len(others), ("{n} postava", "{n} postavy", "{n} postav"))}</small>' if others else "")
    hidden = "" if m is shown else " hidden"
    return (f'<figure class="pc" data-slug="{e(m["slug"])}" data-player="{e(m.get("player", m["slug"]))}" style="--cls:{cls_color(m)}"{hidden}>'
            f'<a href="#char-{e(m["slug"])}" aria-label="Karta postavy {e(m["name"])}" aria-expanded="false">'
            f'{avatar(m, "pc-head")}{img}</a><figcaption><span class="pc-nick">{e(nick(m))}{more}</span>'
            f'<span class="pc-name"><span>{e(m["name"])}</span><b>{m["level"]}</b></span>{xp_bar(m)}'
            f'<span class="pc-sub">{e(m["class"])} · {e(role)}</span></figcaption></figure>')


def waiting_players(party):
    """Přezdívky hráčů, kteří v party.json ještě nemají postavu."""
    return [p["name"] for p in party.get("players") or [] if p.get("name") and not p.get("chars")]


def squad(members, size, today, alts=(), waiting=()):
    """Skupinová fotka: celotělové rendery vedle sebe a jmenovky. Klik na postavu otevře pod ní její kartu.
    Alt nemá vlastní místo, přepíná se na místě svého hráče."""
    out, slots, groups = [], 0, {}
    for m in members:
        slots += 1
        if not m["ok"]:
            out.append(f'<figure class="pc pc-err"><div class="pc-img"></div><figcaption>'
                       f'<span class="pc-name">{e(m["slug"])}</span><span class="pc-sub">nenačteno</span></figcaption></figure>')
            continue
        group = [m, *(a for a in alts if a.get("player") == m["slug"])]
        groups.update((c["slug"], group) for c in group)
        out += [pc_figure(c, group, shown_char(group)) for c in group]
    free = max(0, size - slots)
    for i in range(free):
        name, sub = (waiting[i], "bez postavy") if i < len(waiting) else ("volné místo", "hledá se parťák")
        out.append(f'<figure class="pc pc-empty"><div class="pc-img"><span>?</span></div><figcaption>'
                   f'<span class="pc-name">{e(name)}</span><span class="pc-sub">{sub}</span></figcaption></figure>')
    sheets = "".join(sheet(m, today, groups.get(m["slug"], ())) for m in [*members, *alts] if m["ok"])
    return (f'<section id="postavy"><div class="squad" style="--n:{slots + free}">{"".join(out)}</div>'
            f'<p class="sheet-hint">Klikni na postavu a uvidíš, v čem chodí.</p>'
            f'<div class="sheet-dim" hidden></div>{sheets}</section>')


# ---------- ready check ----------

def quest_link(q):
    return f'<a class="ql" href="https://www.wowhead.com/forever/quest={q["id"]}" target="_blank" rel="noopener">{e(q["name"])}</a>'


def or_list(names):
    return names[0] if len(names) < 2 else f'{", ".join(names[:-1])} nebo {names[-1]}'


def people_names(chars):
    """Jména do věty: jedna postava hráče celým jménem, víc postav přezdívkou s počtem."""
    groups = {}
    for m in chars:
        groups.setdefault(m.get("player", m["slug"]), []).append(m)
    return [char_label(g[0]) if len(g) == 1 else f'{nick(g[0])} ({pl(len(g), ("{n} postava", "{n} postavy", "{n} postav"))})'
            for g in groups.values()]


def mine_group(cls, title, items):
    if not items:
        return ""
    return f'<div class="m-g {cls}"><h4>{title} <span class="m-n">({len(items)})</span></h4><ul>{"".join(items)}</ul></div>'


def mine_card(check, m, by_slug, fit):
    """Co má udělat vybraná postava, aby byla na dungeon připravená.
    U „Můžeš nasdílet“ jsou v data-takers všechny postavy, kterým quest může dát; stránka z nich ukáže jen ty,
    které jsou zrovna ve sloupcích tabulky, ať se seznam s tabulkou nerozchází."""
    s = m["slug"]
    take, chain, get, give, inside, low, log = [], [], [], [], [], [], []
    line = lambda q, detail: f'<li><b>{quest_link(q)}</b>{"".join(f"<small>{e(d)}</small>" for d in detail if d)}</li>'
    for q in check["quests"]:
        st = q["status"][s]
        if st == "missing":
            take.append(line(q, [q["start"], q.get("note")]))
        elif st == "share":
            names = or_list(people_names([by_slug[h] for h in q["holders"][s]]))
            get.append(line(q, [f"může nasdílet {names}", f'jinak {q["start"]}' if q["start"] else None]))
        elif st == "pre":
            chain.append(line(q, [f'předquest máš v logu, nejdřív dokonči {q["prev"]}']))
        elif st == "chain":
            take.append(line(q, [f'nejdřív {q["prev"]}']))
        elif st == "active":
            log.append(q)
            takers = [o for o, h in q.get("holders", {}).items() if s in h and o in by_slug]
            if takers:
                give.append(f'<li data-takers="{e(" ".join(takers))}"><b>{quest_link(q)}</b><small class="m-takers"></small></li>')
        elif st == "inside":
            inside.append(q)
        elif st == "level":
            low.append(q)
    off = ""
    if m.get("level") and s not in fit:
        off = f'<p class="m-off">Level {m["level"]}, dungeon je pro {check["min"]}–{check["max"]}.</p>'
    ready = "" if take or chain else '<p class="m-ok">✓ Nemusíš nikam chodit.</p>'
    plain = [("V logu", log, lambda q: q["name"]), ("Začne až uvnitř", inside, lambda q: q["name"]),
             ("Až s levelem", low, lambda q: f'{q["name"]} ({q["req"]})')]
    lines = "".join(f'<p class="m-line"><b>{title}:</b> {e(", ".join(fmt(q) for q in qs))}</p>' for title, qs, fmt in plain if qs)
    return (f'<div class="mine" data-slug="{e(s)}" style="--cls:{cls_color(m)}" hidden>{off}{ready}'
            f'{mine_group("m-take", "✋ Musíš dojít", take)}{mine_group("m-chain", "⛓ Dokonči řetěz", chain)}'
            f'{mine_group("m-get", "↗ Můžou ti nasdílet", get)}'
            f'{mine_group("m-give", "↗ Můžeš nasdílet", give)}{lines}</div>')


def char_todo(check, m):
    """Co postavě k dungeonu chybí: questy, pro které si musí dojít, řetězy s předquestem v logu
    a questy, které může dostat nasdílením."""
    s = m["slug"]
    return ([q for q in check["quests"] if q["status"][s] in ("missing", "chain")],
            [q for q in check["quests"] if q["status"][s] == "share"],
            [q for q in check["quests"] if q["status"][s] == "pre"])


def todo_state(go, get, chain=()):
    bits = [f"✋ {len(go)}× dojít" if go else "", f"⛓ {len(chain)}× dokončit" if chain else "",
            f"↗ {len(get)}× nasdílet" if get else ""]
    return " · ".join(b for b in bits if b) or "✓ připraveno"


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
        return '<td class="st st-over st-done" title="už odevzdal">hotovo</td>'
    if st == "level":
        return f'<td class="st st-over" title="quest jde vzít od levelu {q["req"]}">lvl {q["req"]}</td>'
    if st == "inside":
        return '<td class="st st-over" title="quest začíná až v dungeonu (předmět nebo postava uvnitř)">uvnitř</td>'
    if st == "pre":
        return '<td class="st st-pre" title="má v logu předquest, musí dokončit řetěz">⛓ dokončit</td>'
    return f'<td class="st st-over" title="nejdřív: {e(q["prev"])}">⛓</td>'


def quest_meta(q):
    bits = [f'od lvl {q["req"]}' if q["req"] else None,
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


def party_table(check, members, by_slug, crew):
    """Celá parta: řádek = quest, sloupec = hráč. Ve sloupci je postava, kterou by do dungeonu poslal (pick_char),
    ostatní jeho postavy jsou skryté a stránka do sloupce dá vybranou postavu (moje volba, ruční přepnutí sloupce)."""
    players = {}
    for s in check["members"]:
        m = by_slug[s]
        players.setdefault(m.get("player", s), []).append(m)
    cols = []
    for group in players.values():
        default = pick_char(group, check)
        cols += [(m, m is default) for m in group]
    many = {p for p, g in players.items() if len(g) > 1}

    def attrs(m, default):
        return (f'data-player="{e(m.get("player", m["slug"]))}" data-slug="{e(m["slug"])}"'
                + (" data-def" if default else " hidden"))

    def head(m, default):
        kind, n = level_status(m.get("level", 0), check)
        gap = "" if kind == "fit" else f'<small class="gap">{"−" if kind == "under" else "+"}{n} lvl</small>'
        return (f'<th class="who-col" {attrs(m, default)} style="--cls:{cls_color(m)}">'
                + (f'<button type="button" class="col-sw" aria-haspopup="menu" title="změnit postavu ve sloupci"><b>{e(nick(m))}</b><i>▾</i></button>'
                   if m.get("player", m["slug"]) in many else f'<b>{e(nick(m))}</b>') +
                f'<small><span class="wc-name">{e(m["name"])} · </span>{m.get("level", "")}</small>{gap}</th>')

    def cell(q, m, default):
        st = q["status"][m["slug"]]
        givers = q.get("holders", {}).get(m["slug"], [])
        names = ", ".join(by_slug[h]["name"] for h in givers if h in by_slug)
        return quest_cell(q, st, names).replace("<td ", f"<td {attrs(m, default)} ", 1)

    body = []
    for i, q in enumerate(check["quests"]):
        classes = (["q-none"] if q["unclaimed"] else []) + (["q-cls"] if q["limit"] and q["limit"]["cls"] else [])
        cls = f' class="{" ".join(classes)}"' if classes else ""
        if q["limit"] and q["limit"]["cls"]:
            cls += f' style="--cls:{CLASS_COLOR.get(q["limit"]["cls"], "var(--muted)")}"'
        body.append(f'<tr data-q="{i}"{cls}><th scope="row"><div class="q-name">{quest_link(q)}{share_label(q)}</div>'
                    f'<small>{quest_meta(q)}</small></th>{"".join(cell(q, m, d) for m, d in cols)}</tr>')
    # na mobilu stránka sbalí řádky, kde nikdo z ukázaných postav nic nemusí (hotovo nebo nemůže vzít)
    return (f'<div class="rc-party"><h4>Celá parta</h4>'
            f'<p class="rc-ready">✓ parta je na tenhle dungeon připravená</p>'
            f'<div class="table-wrap rc-wrap"><table class="dungeons rc-table">'
            f'<thead><tr><th>Quest</th>{"".join(head(m, d) for m, d in cols)}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>'
            f'<button type="button" class="rc-fold" aria-expanded="false" hidden></button>'
            f'<p class="note">✓ má v logu · ↗ dostane nasdílet · dojít = musí si ho vzít · ⛓ nejdřív předquest · '
            f'⛓ dokončit = předquest má v logu · uvnitř = začne v dungeonu. U ostatních je odhad: naposledy hraná postava, '
            f'která se do dungeonu vejde. Jméno nahoře sloupec přepne (jen u tebe, do zavření karty).</p></div>')


DIFFICULTY = [("na krev", "Hard"), ("dá se", "Medium"), ("akorát", "At Level"), ("procházka", "Easy")]


def level_scale(check, levels):
    """Stupnice obtížnosti z Wowhead průvodce (difficulty v dungeons.json) místo rozpětí levelů.
    Zvýrazní stupeň, na který stačí nejslabší z party; bez stupnice zůstane rozpětí."""
    steps = check.get("difficulty")
    if not steps:
        return f'<span class="range">level {check["min"]}–{check["max"]}</span>'
    low = min(levels) if levels else 0
    reached = [i for i, lvl in enumerate(steps) if low >= lvl]
    on = reached[-1] if reached else None
    tip = "Podle Wowheadu: " + ", ".join(f"{en} od {lvl}" for (_, en), lvl in zip(DIFFICULTY, steps)) + \
        ". Zvýrazněné podle nejslabšího v partě."
    parts = "".join(f'<span class="dl{" dl-on" if i == on else ""}" data-lvl="{lvl}">{name}</span>'
                    f'<small>{lvl}</small>' for i, ((name, _), lvl) in enumerate(zip(DIFFICULTY, steps)))
    return f'<span class="range diff" title="{e(tip)}">{parts}</span>'


def done_chip(done):
    """Štítek odehraného dungeonu, s datem, když ho víme (v dungeons.json může být jen true)."""
    when = ""
    if isinstance(done, str):
        _, mo, d = (int(x) for x in done.split("-"))
        when = f" {d}. {mo}."
    return f'<span class="chip chip-done">✓ odehráno{when}</span>'


def ready_box(check, members, idx):
    by_slug = {m["slug"]: m for m in members}
    cols = [by_slug[s] for s in check["members"]]
    crew = check.get("lineup") or check["members"]
    best = '<span class="chip chip-new">příští výprava</span>' if check["best"] else ""
    links = [(check.get("source"), "Wowhead", "questy, levely a sdílení podle Wowheadu"),
             (check.get("tavern"), "Warcraft Tavern", "podrobný postup uvnitř dungeonu")]
    source = "".join(f'<a href="{e(url)}" target="_blank" rel="noopener" title="{tip}">{name} ↗</a>'
                     for url, name, tip in links if url)
    source = f'<span class="rc-source">{source}</span>' if source else ""
    hidden = "" if idx == 0 else " hidden"
    fit = [m["slug"] for m in cols if m.get("level") and check["min"] <= m["level"] <= check["max"]]
    levels = [by_slug[s]["level"] for s in crew if by_slug[s].get("level")]
    return f'''<article class="rc" id="rc-{idx}" role="tabpanel" data-fit="{e(" ".join(fit))}"{hidden}>
  <header class="rc-head">{best}{level_scale(check, levels)}{source}</header>
  {"".join(mine_card(check, m, by_slug, fit) for m in cols)}
  {party_table(check, members, by_slug, crew)}
</article>'''


def char_card(m, attr, title=None, line=None, small=None, level=True):
    """Karta ve výběru: portrét, jméno, level a classa. Výběr hráče do ní dá přezdívku a počet postav."""
    art = (f'<span class="ch-art" style="background-image:url(\'{e(m["render"])}\')"></span>' if m.get("render")
           else f'<span class="ch-art ch-noart">{avatar(m, "ch-av")}</span>')
    lvl = f'<span class="ch-lvl">{m.get("level", "")}</span>' if level else ""
    return (f'<button type="button" class="ch" {attr} style="--cls:{cls_color(m)}">{art}{lvl}'
            f'<span class="ch-txt"><b>{e(title or first(m))}</b><span>{e(line if line is not None else m["name"].partition(" ")[2])}</span>'
            f'<small>{e(small or m.get("class", ""))}</small></span></button>')


def me_gate(chars):
    """Volba hráče při první návštěvě: karty hráčů, za každého jeho postava s nejvyšším levelem (alta přepne „Hraju za“)."""
    players = {}
    for m in chars:
        players.setdefault(m.get("player", m["slug"]), []).append(m)
    cards = "".join(char_card(top_char(cs), f'data-who="{e(p)}" data-pick="{e(top_char(cs)["slug"])}"', nick(top_char(cs)),
                              top_char(cs)["name"], pl(len(cs), ("{n} postava", "{n} postavy", "{n} postav")), level=False)
                    for p, cs in players.items())
    return (f'<div class="gate" role="dialog" aria-modal="true" aria-labelledby="gate-h" hidden><div class="gate-box">'
            f'<h2 id="gate-h">Kdo jsi?</h2><p class="gate-sub">Podle toho ti stránka ukáže tvoje questy, loot a dungeony. Změníš to v liště.</p>'
            f'<div class="gate-grid">{cards}</div>'
            f'<button type="button" class="gate-skip">jen koukám</button></div></div>')


def me_menu(chars):
    """Výběr „Hraju za“ přímo v liště: jen postavy vybraného hráče (stránka skryje ostatní) a změna hráče."""
    players = {}
    for m in chars:
        players.setdefault(m.get("player", m["slug"]), []).append(m)
    groups = "".join(
        f'<div class="mm-p" data-player="{e(p)}" hidden><span class="mm-nick" style="--cls:{cls_color(cs[0])}">{e(nick(cs[0]))}</span>'
        + "".join(f'<button type="button" class="mm-c" data-me="{e(m["slug"])}" aria-pressed="false" style="--cls:{cls_color(m)}">'
                  f'{avatar(m, "mm-av")}<span class="mm-name">{e(m["name"])}</span><span class="mm-lvl">{m.get("level", "")}</span></button>'
                  for m in cs) + "</div>"
        for p, cs in players.items())
    return (f'<div class="me-menu" role="dialog" aria-label="Hraju za" hidden>{groups}'
            f'<button type="button" class="mm-c mm-none mm-switch">nejsem to já, změnit hráče</button></div>')


def ready_section(checks, members):
    if not checks:
        return ""
    # jeden výběr dungeonu, skupiny podle vybrané postavy skládá stránka; odehrané až za těmi před námi, podle levelu
    order = sorted(enumerate(checks), key=lambda x: (bool(x[1].get("past")), x[1]["min"] if x[1].get("past") else 0))
    opts = "".join(
        f'<option value="{i}" data-title="{e(c["title"])}"{" data-past=1" if c.get("past") else ""} '
        f'data-fit="{e(" ".join(m["slug"] for m in members if c["min"] <= m.get("level", 0) <= c["max"]))}">'
        f'{e(c["title"])} · {c["min"]}–{c["max"]}</option>' for i, c in order)
    players = {}
    for m in members:
        players.setdefault(m.get("player", m["slug"]), []).append(m)
    strips = "".join(f'<div class="me-chips" data-player="{e(p)}" hidden>'
                     + "".join(f'<button type="button" class="mc" data-me="{e(m["slug"])}" aria-pressed="false" '
                               f'title="{e(m.get("class", ""))}" style="--cls:{cls_color(m)}"><span class="mc-name">{e(m["name"])}</span><span class="mc-lvl">{m.get("level", "")}</span></button>'
                               for m in chars) + "</div>"
                     for p, chars in players.items())
    data = json.dumps({m["slug"]: m.get("player", m["slug"]) for m in members}, ensure_ascii=False).replace("<", "\\u003c")
    names = json.dumps({m["slug"]: [nick(m), m["name"], cls_color(m), m.get("level", 0), m.get("class") or "?", bool(m.get("alt"))]
                        for m in members}, ensure_ascii=False).replace("<", "\\u003c")
    # pamatuje se zvolená postava; bez ní (nebo když zmizela) se za hráče vezme jeho nejvyšší level
    mains = json.dumps({p: top_char(cs)["slug"] for p, cs in players.items()}, ensure_ascii=False).replace("<", "\\u003c")
    return (f'<section id="ready"><h2 class="sec">Ready check</h2>'
            f'<script type="application/json" id="rc-chars">{data}</script>'
            f'<script type="application/json" id="rc-names">{names}</script>'
            f'<script type="application/json" id="rc-mains">{mains}</script>'
            f'<div class="me-box">{strips}<button type="button" class="me-ask" hidden>Vyber, za koho hraješ ▾</button></div>'
            f'<label class="rc-sel"><span>Připravit na dungeon</span><select>{opts}</select></label>'
            f'{"".join(ready_box(c, members, i) for i, c in enumerate(checks))}</section>')


# ---------- profese a dungeony ----------

CRAFT_MAX = 3  # Dílna ukáže nejvýš tolik nabídek, ať to není výrobní plán


def craft_box(plan, members):
    """Dílna: pár nabídek, co kdo komu umí vyrobit (jen kusy, které berou všema deseti, nebo do prázdného slotu),
    kdo co musí přinést, kdo umí třpytky (bez jmen, komu chybí) a kdo je na stropu.
    S vybranou postavou se ukážou nabídky přímo pro ni."""
    by_slug = {m["slug"]: m for m in members}

    def who(slug):
        m = by_slug.get(slug)
        if not m:
            return e(slug)
        return f'<b style="color:{cls_color(m)}">{e(char_label(m) if m.get("alt") else nick(m))}</b>'

    def one_per_player(slugs):
        out = {}  # hlavní postava má přednost před altem
        for s_ in sorted(slugs, key=lambda x: bool(by_slug.get(x, {}).get("alt"))):
            out.setdefault(by_slug.get(s_, {}).get("player", s_), s_)
        return list(out.values())

    def suppliers(slugs):
        names = [who(s_) for s_ in one_per_player(slugs)]
        return ", ".join(names) if len(names) <= 2 else f'{", ".join(names[:2])} a další'

    def offer_rows(ups):
        offers = {}  # stejný kus od stejného řemeslníka pro víc lidí = jedna nabídka
        for u in ups:
            if u["upgrade"] not in ("big", "empty"):
                continue
            o = offers.setdefault((u["item"]["id"], u["crafter"]), {**u, "targets": []})
            o["targets"].append(u["target"])
        rows = []
        for o in list(offers.values())[:CRAFT_MAX]:
            targets = and_join(["sobě" if t == o["crafter"] else who(t) for t in o["targets"]])
            verb = CRAFT_VERB.get(o.get("prof"), "vyrobit")
            need = " · ".join([f'{suppliers(r["from"])} přinese {r["qty"]}× {e(r["name"])}'
                               for r in o["reagents"]] + ([f'přijde o třpytky na {e(o["loses"])}'] if o["loses"] else []))
            rows.append(f'<li><span class="cf-title">{who(o["crafter"])} umí {verb} {item_link(o["item"])} → {targets}</span>'
                        f'<span class="cf-gain">{e(Q.line("craft.offer", o["crafter"] + str(o["item"]["id"])))}</span>'
                        f'{f"<span class=cf-need>{need}</span>" if need else ""}</li>')
        return "".join(rows)

    general = offer_rows([u for u in plan["ups"] if not by_slug.get(u["target"], {}).get("alt")])
    personal = "".join(f'<ul class="craft craft-for" data-for="{e(m["slug"])}" hidden>{rows}</ul>' for m in members
                       if (rows := offer_rows([u for u in plan["ups"] if u["target"] == m["slug"]])))
    rest = []
    if plan["enchants"]:
        by = one_per_player(sorted({s_ for en in plan["enchants"].values() for s_ in en["by"]}))
        slots = and_join([SLOT_CZ.get(slot, slot) for slot in plan["enchants"]])
        rest.append(f'<li class="cf-ench"><span class="cf-title">✨ {and_join([who(s_) for s_ in by])} '
                    f'{"umí" if len(by) == 1 else "umějí"} zatřpytit {e(slots)}.</span>'
                    f'<span class="cf-gain">{e(Q.line("craft.enchant", "ench"))}</span></li>')
    for slug, prof, skill in plan["capped"]:
        rest.append(f'<li class="cf-cap" title="{e(prof)} {skill}/{skill}"><span class="cf-title">⚠️ {who(slug)} je na stropu jako '
                    f'{e(CRAFTSMAN.get(prof, prof))}, {e(Q.line("craft.cap", slug + prof))}</span></li>')
    body = (f'<ul class="craft craft-all">{general}</ul>{personal}'
            f'<p class="muted craft-none" hidden>Pro tuhle postavu teď nikdo nic nevyrobí.</p>'
            f'{f"""<ul class="craft">{"".join(rest)}</ul>""" if rest else ""}' if general or personal or rest
            else f'<p class="muted">{e(Q.line("craft.empty", "craft", "Nic k výrobě."))}</p>')
    return f'<div class="craft-box"><h3 class="sub-h">Dílna</h3>{body}</div>'


def gain_text(g):
    """Text a tooltip štítku upgradu (upgrade() / loot_fit)."""
    if g is None:
        return "nic moc", "nosí stejně dobrý nebo lepší kus"
    if g == "?":
        return "?", "neznámé staty nebo výbava, porovnej ve hře"
    if g == "empty":
        return "prázdný slot", "ten slot má prázdný"
    if g == "big":
        return "velký upgrade", "výrazně lepší než to, co nosí"
    if g == "small":
        return "malý upgrade", "o něco lepší než to, co nosí"
    return "asi lepší", f"staty neznáme, o {g} item levelů lepší než to, co nosí (+{g} ilvl)"


def loot_who(fits, by_slug):
    """Štítky postav, kterým se kus hodí: upgrade barvou classy, ostatní tlumeně.
    Hráč s víc postavami má na štítku celé jméno postavy, s jednou jen přezdívku."""
    chips = []
    count = {}
    for c in by_slug.values():
        count[c.get("player", c["slug"])] = count.get(c.get("player", c["slug"]), 0) + 1
    rank = {"empty": 0, "big": 1, "small": 2, "?": 4, None: 5}
    order = sorted(fits.items(), key=lambda kv: (kv[1].get("second", False), rank.get(kv[1]["gain"], 3),
                                                 list(by_slug).index(kv[0])))
    for slug, f in order:
        m = by_slug[slug]
        g = f["gain"]
        text, tip = gain_text(g)
        cls = {None: " lt-no", "?": " lt-maybe", "small": " lt-small"}.get(g, "")
        cls += " lt-altc" if m.get("alt") else ""  # alta je vidět, jen když je vybraný
        if f.get("second"):
            cls += " lt-second"
            tip = "druhá volba: heal nebo damage jiné školy nevyužije, " + tip
            text = f'2. volba · {text}'
        lvl = f' <small>až na {m["level"] + f["lvl"]}</small>' if f["lvl"] else ""
        if f["lvl"]:
            tip += f', nasadí až na levelu {m["level"] + f["lvl"]}'
        chips.append(f'<span class="lt-who{cls}" data-slug="{e(slug)}" style="--cls:{cls_color(m)}" title="{e(tip)}">'
                     f'<b>{e(m["name"] if count.get(m.get("player", slug), 1) > 1 else nick(m))}</b> {e(text)}{lvl}</span>')
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
                                       it.get("stats") or it.get("stat"), it.get("req") and f'od lvl {it["req"]}') if x)
    alt = f' <small class="lt-alt">(nebo {e(it["alt"])})</small>' if it.get("alt") else ""
    ups = [s for s, f in fits.items() if f["gain"] not in (None, "?") and not f.get("second") and not by_slug[s].get("alt")]
    if it.get("set"):
        bonus = " · ".join((sets or {}).get(it["set"], {}).get("bonus") or [])
        alt += f' <span class="lt-set" title="{e(bonus)}">set {e(it["set"])}</span>'
    flag = (f'<span class="lt-flag" title="hodí se víc lidem, domluvte se před rollem">{e(Q.label("loot.fight", "🎲 víc zájemců"))}</span>'
            if contested and len(ups) > 1 else "")
    who = loot_who(fits, by_slug)
    if not any(not by_slug[s].get("alt") for s in fits):
        who += (f'<span class="lt-none" title="nikomu z party se nehodí">'
                f'{e(Q.label("loot.nobody", "nikomu, disenchant nebo prodat"))}</span>')
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
            quip = Q.line("trash_boe", d["title"]) if b.get("boe") else b.get("quip")
            if isinstance(quip, list):
                quip = quip[(Q.day + zlib.crc32(b["name"].encode())) % len(quip)] if quip else ""
            quip = f'<p class="boss-quip">{e(quip)}</p>' if quip else ""
            tip = f'<p class="boss-tip">{e(b["tip"])}</p>' if b.get("tip") else ""
            # vzácné BoE z trashe: žádný seznam na farmení, jen věta
            items = "" if b.get("boe") else f'<ul class="loot">{"".join(loot_line(x, by_slug, sets=d["sets"]) for x in b["items"])}</ul>'
            bosses.append(f'<article class="boss{" boss-trash" if b.get("trash") else ""}"><h4>{e(b["name"])}{rare}</h4>{quip}{tip}'
                          f'{items}</article>')
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
        if d.get("note"):  # odkud to víme: jen v tooltipu, ne jako odstavec
            src += f'<span class="src-note" title="{e(d["note"])}">ⓘ odkud to víme</span>'
        # spoiler: bossové a loot jsou schované, dokud si je někdo sám nerozklikne
        n = sum(1 for b in d["bosses"] if not b.get("trash"))
        past = " data-past=1" if d.get("past") else ""
        hide = " hidden" if d.get("past") else ""
        parts.append(f'<details class="loot-dg" data-title="{e(d["title"])}"{past}{hide}><summary><div class="rc-head"><h3>{e(d["title"])}{best}</h3>'
                     f'<span class="range">level {d["min"]}–{d["max"]}</span>'
                     f'<span class="spoil">⚠ spoiler · {pl(n, ("{n} boss", "{n} bossové", "{n} bossů"))}, taktika a loot '
                     f'<span class="spoil-open">ukázat ▾</span><span class="spoil-close">schovat ▴</span></span></div></summary>'
                     f'<p class="rc-source loot-src">{src}</p>'
                     f'<div class="bosses">{"".join(bosses)}</div>{"".join(set_boxes)}'
                     f'{f"<h3 class=sub-h>Odměny z questů (každý si vybírá sám)</h3><ul class=lq-list>{quests}</ul>" if quests else ""}'
                     f'</details>')
    return (f'<section id="loot"><div class="sec-row"><h2 class="sec">Bossové a loot</h2>'
            f'<button type="button" class="loot-all" hidden>ukázat i kusy pro ostatní</button></div>{"".join(parts)}'
            f'<p class="muted loot-empty" hidden>K tomuhle dungeonu loot zatím nemáme.</p>'
            f'{loot_legend()}</section>')


def loot_legend():
    """Vysvětlivky štítků lootu jako vzorové štítky, schované za „?“."""
    def chip(cls, text, small=""):
        return (f'<span class="lt-who{cls}" style="--cls:var(--gold)">{e(text)}'
                f'{f" <small>{small}</small>" if small else ""}</span>')
    rows = [(chip("", gain_text("big")[0]), "výrazně lepší než to, co nosí"),
            (chip(" lt-small", gain_text("small")[0]), "o něco lepší"),
            (chip("", gain_text("empty")[0]), "ten slot má prázdný"),
            (chip("", gain_text(3)[0]), "staty neznáme, má vyšší item level"),
            (chip(" lt-maybe", gain_text("?")[0]), "porovnej ve hře"),
            (chip(" lt-no", gain_text(None)[0]), "nosí stejně dobré nebo lepší"),
            (chip("", gain_text("big")[0], "až na N"), "nasadí, až bude mít level N"),
            (chip(" lt-second", "2. volba · " + gain_text("small")[0]), "využije jen část"),
            (f'<span class="lt-flag">{e(Q.label("loot.fight", "🎲 víc zájemců"))}</span>', "hodí se víc lidem")]
    items = "".join(f'<li>{c}<span>{e(t)}</span></li>' for c, t in rows)
    return (f'<details class="note legend"><summary>U kusu jsou ti, kdo ho můžou nosit, „nic moc“ znamená, že mají stejný nebo lepší. Bossové jsou schovaní, ať si nikdo '
            f'nekazí překvapení. <span class="lg-q" title="co znamenají štítky">?</span></summary>'
            f'<ul class="lg">{items}</ul></details>')


def professions_box(members):
    """Přehled: profese × hráči, u každého nejlepší skill ze všech jeho postav (kdo to je, je v tooltipu);
    vedlejší profese u nuly s hláškou, chybějící profese jednou větou."""
    people = {}
    for m in members:
        people.setdefault(m.get("player", m["slug"]), []).append(m)
    names = [p["name"] for m in members for p in m.get("professions") or []]
    prim = [n for n in ALL_PRIMARIES if n in names]
    sec = sorted({p["name"] for m in members for p in m.get("professions") or [] if not p["primary"]})
    nobody = [n for n in ALL_PRIMARIES if n not in names]
    cols = len(people)
    head = "".join(f'<span class="pg-h" style="color:{cls_color(g[0])}">{e(nick(g[0]))}</span>' for g in people.values())

    def best(chars, name):
        have = [(p, m) for m in chars for p in m.get("professions") or [] if p["name"] == name]
        return max(have, key=lambda x: x[0]["skill"]) if have else None

    def row(name, cls=""):
        cells = ""
        for chars in people.values():
            if not (got := best(chars, name)):
                cells += '<span class="pg-cell pg-none"><b>–</b></span>'
                continue
            p, m = got
            cells += (f'<span class="pg-cell" title="{e(m["name"])}: {prof_num(p)}" style="--cls:{cls_color(m)}">'
                      f'<b>{p["skill"]}</b></span>')
        return f'<span class="pg-name{cls}">{e(name)}</span>{cells}'

    grid = "".join(row(n) for n in prim) + "".join(row(n, " pg-sec") for n in sec)
    # na mobilu se šest sloupců nevejde: řádek = hráč a jeho profese
    lines = []
    for chars in people.values():
        bits = "".join(f'<span class="pl-p{" pg-sec" if n in sec else ""}" title="{e(m["name"])}: {prof_num(p)}" '
                       f'style="--cls:{cls_color(m)}">{e(n)} <b>{p["skill"]}</b></span>'
                       for n in prim + sec if (got := best(chars, n)) for p, m in [got])
        lines.append(f'<li><b class="pl-who" style="color:{cls_color(chars[0])}">{e(nick(chars[0]))}</b>'
                     f'<span class="pl-ps">{bits or "<span class=pg-sec>nic</span>"}</span></li>')
    # vedlejší profese skoro na nule: hláška místo čísla (jen u hlavních postav, alt začíná od nuly)
    low = [Q.line(f"prof_low.{p['name']}", m["slug"], who=nick(m))
           for m in members if not m.get("alt") for p in m.get("professions") or [] if not p["primary"] and p["skill"] <= 5]
    foot = "".join(f'<p class="note pg-low">{e(x)}</p>' for x in low if x)
    if nobody:
        bits = [Q.line(f"missing.{n}", "missing", n) for n in nobody]
        line = and_join(bits)
        foot += f'<p class="note" title="nikdo nemá: {e(", ".join(nobody))}">{e(line[:1].upper() + line[1:])}.</p>'
    return (f'<div class="prof-grid2" style="--cols:{cols}"><span></span>{head}{grid}</div>'
            f'<ul class="prof-list">{"".join(lines)}</ul>{foot}')


def status_cell(st):
    kind, n = st
    if kind == "fit":
        return '<td class="st st-fit" title="sedí">✓</td>'
    if kind == "under":
        return f'<td class="st st-take" title="chybí {n} lvl">−{n}</td>'
    return f'<td class="st st-over" title="o {n} lvl výš">+{n}</td>'


def dungeon_table(rows, best, members):
    head = "".join(f'<th class="who-col" style="--cls:{cls_color(m)}">{e(nick(m))}</th>' for m in members)
    body = []
    for r in rows:
        chips = '<span class="chip chip-new">nový</span>' if r.get("new") else ""
        if r.get("done"):  # datum, nebo jen true, když si ho nikdo nepamatuje
            chips += done_chip(r["done"])
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
    who = " ".join(f'<span class="k-who" style="--cls:{cls_color(by_slug[s])}">{e(nick(by_slug[s]))}</span>'
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
  <footer>Píše se po výpravách, u piva.</footer>
</main>
</body>
</html>
'''


def render(party, members, rows, best, built, checks, base, titles, counters, chronicle, today, plan=None, loot=None, chars=None):
    """members = hlavní postavy (místa v partě), chars = všechny načtené postavy včetně altů."""
    global Q
    Q = Quips(Q.data, today)  # hlášky na dnešek, nové počítání opakování
    Q.used |= {t["detail"] for t in titles.values()}  # ať novinky neopakují titul
    ok = [m for m in members if m["ok"]]
    chars = chars or ok
    has_tank = any("tank" in m["roles"] for m in ok)
    has_heal = any("heal" in m["roles"] for m in ok)
    levels = [m["level"] for m in ok]
    lvl_range = f"{min(levels)}–{max(levels)}" if levels else "?"
    ruleset = next((m["ruleset"] for m in ok if m["ruleset"]), "")
    faction = next((m["faction"] for m in ok if m["faction"]), "")

    def flag(label, good):
        return f'<span class="flag {"flag-ok" if good else "flag-bad"}">{"✓" if good else "✗"} {label}</span>'

    by_slug = {m["slug"]: m for m in ok}
    desc = " · ".join(f'{nick(by_slug[s])}: {t["title"]}' for s, t in titles.items() if s in by_slug)
    lead = next(iter(titles), None)
    image = (by_slug.get(lead) or {}).get("render") or next((m.get("render") for m in ok if m.get("render")), "")
    # pořadí lišty kopíruje pořadí sekcí
    nav = [("postavy", "Postavy", True), ("vyprava", "Výprava", bool(best)), ("tituly", "Tituly", bool(titles)),
           ("novinky", "Novinky", bool(base)), ("ready", "Ready check", bool(checks)), ("loot", "Loot", bool(loot)),
           ("kronika", "Kronika", bool(chronicle)), ("profese", "Řemesla", True)]
    nav_html = "".join(f'<a href="#{i}">{t}</a>' for i, t, show in nav if show)
    return f'''{page_head(party["title"], desc, image, party.get("site_url", ""))}
<main>
  <header class="top">
    <div>
      <p class="eyebrow">{e(faction)} · {e(ruleset)} · WoW Forever EU</p>
      <h1>{e(party["title"])}</h1>
    </div>
    <div class="top-right">
      <div class="flags" title="Podle hlavních postav">{flag("Tank", has_tank)}{flag("Healer", has_heal)}<span class="flag">Levely {lvl_range}</span></div>
      <p class="built">Natočeno <time data-ts="{built}">{built}</time></p>
    </div>
  </header>
  <nav class="nav"><div class="nav-links">{nav_html}</div>{'<button type="button" class="nav-me" aria-haspopup="dialog" aria-expanded="false" hidden><span>Hraju za</span><b>Kdo jsi?</b><small></small><i class="nav-caret">▾</i></button>' + me_menu(chars) if checks else ""}</nav>
  {me_gate(chars) if checks else ""}
  {squad(members, party.get("size", len(members)), today, [m for m in chars if m.get("alt")], waiting_players(party))}
  {next_box(rows, best, ok, checks, chars)}
  {evening_box(ok, base, titles, counters)}
  {news_box(chars, base, today) if base else ""}
  {ready_section(checks, chars)}
  {loot_section(loot, chars)}
  {chronicle_box(chronicle, ok)}
  <section id="profese"><h2 class="sec">Řemesla</h2>{craft_box(plan, chars) if plan else ""}
    <h3 class="sub-h">Přehled</h3>{professions_box(chars)}</section>
  <section><details class="all-dungeons"><summary>Kam jednou… (všech {len(rows)} dungeonů)</summary>
    {dungeon_table(rows, best, ok)}
    <p class="note">✓ sedí · −N chybí N levelů · +N je o N levelů výš. Rozsahy podle
    <a href="https://www.wowhead.com/forever/guide/dungeons-overview-locations-details" target="_blank" rel="noopener">Wowheadu</a>.</p></details></section>
  <footer>Čepuje se každé ráno z Blizzard API (a když neteče, z <a href="https://worldofwarcraft.blizzard.com/en-us/forever/eu/armory" target="_blank" rel="noopener">Armory</a>). Postava se aktualizuje, až se odhlásí ze hry. Tooltipy předmětů z Wowheadu.</footer>
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
.squad{display:grid;grid-template-columns:repeat(var(--n,5),minmax(0,1fr));gap:10px;margin:18px 0 0}
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
.nav{position:sticky;top:0;z-index:20;display:flex;align-items:center;gap:8px;margin:14px -16px 6px;padding:8px 16px;
background:color-mix(in srgb,var(--bg) 92%,transparent);backdrop-filter:blur(6px);border-bottom:1px solid var(--line)}
.nav-links{display:flex;gap:4px;overflow-x:auto;min-width:0;flex:1;scrollbar-width:none}
.nav a{white-space:nowrap;color:var(--muted);font-size:14px;font-weight:600;padding:4px 10px;border-radius:999px}
.nav a:hover{color:var(--gold);background:var(--panel2);text-decoration:none}
.nav a.on{color:var(--gold);background:var(--panel2);box-shadow:inset 0 0 0 1px #6b5420}
.more-r{-webkit-mask-image:linear-gradient(to right,#000 calc(100% - 32px),transparent);mask-image:linear-gradient(to right,#000 calc(100% - 32px),transparent)}
.more-l{-webkit-mask-image:linear-gradient(to right,transparent,#000 32px);mask-image:linear-gradient(to right,transparent,#000 32px)}
.more-l.more-r{-webkit-mask-image:linear-gradient(to right,transparent,#000 32px,#000 calc(100% - 32px),transparent);
mask-image:linear-gradient(to right,transparent,#000 32px,#000 calc(100% - 32px),transparent)}
.nav-me{flex:none;display:flex;align-items:baseline;gap:6px;max-width:60vw;
  font:inherit;font-size:13px;color:var(--text);background:var(--panel2);border:1px solid #6b5420;border-radius:999px;
  padding:4px 12px;cursor:pointer}
.nav-me[hidden]{display:none}.nav-me:hover{border-color:var(--gold)}
.nav-me span{color:var(--muted);font-size:12px}.nav-me b{color:var(--gold);font-weight:600}
.nav-me small{color:var(--muted);font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.nav-me small:empty{display:none}
.nav-caret{font-style:normal;color:var(--muted);font-size:11px}
.me-menu{position:absolute;right:16px;top:calc(100% - 2px);z-index:30;width:min(340px,calc(100vw - 32px));max-height:min(70vh,520px);overflow-y:auto;
  background:var(--panel);border:1px solid #6b5420;border-radius:14px;padding:8px;box-shadow:0 12px 32px #000a}
.me-menu[hidden]{display:none}
.col-menu{position:absolute;z-index:30;width:min(260px,calc(100vw - 32px));max-height:min(60vh,420px);overflow-y:auto;
  background:var(--panel);border:1px solid #6b5420;border-radius:14px;padding:6px;box-shadow:0 12px 32px #000a}
.col-menu[hidden]{display:none}.col-menu .mm-c[aria-checked=true]{background:var(--panel2);border-left-color:var(--gold)}
.col-menu .mm-reset{color:var(--muted);font-size:13px}
.col-sw{font:inherit;color:inherit;background:none;border:0;padding:0;cursor:pointer;display:inline-flex;align-items:baseline;gap:3px}
.col-sw i{font-style:normal;font-size:.95em;line-height:1;color:var(--muted)}.col-sw:hover b{text-decoration:underline}
th.col-manual .col-sw b::after{content:"•";color:var(--gold);margin-left:3px}
.mm-p{padding:4px 0}.mm-p[hidden]{display:none}
.mm-nick{display:block;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.08em;color:var(--cls);padding:4px 6px 2px}
.mm-c{display:flex;align-items:center;gap:8px;width:100%;min-height:40px;padding:4px 6px;font:inherit;font-size:14px;color:var(--text);
  background:none;border:0;border-left:3px solid transparent;border-radius:8px;cursor:pointer;text-align:left}
.mm-c:hover,.mm-c:focus-visible{background:var(--panel2);outline:0}
.mm-c[aria-pressed=true]{background:var(--panel2);border-left-color:var(--gold)}.mm-c[aria-pressed=true] .mm-name{color:var(--gold)}
.mm-av{width:26px;height:26px;border-radius:50%;background:var(--panel2);flex:none}
.mm-name{flex:1;color:var(--cls)}.mm-lvl{color:var(--muted);font-size:12px}
.mm-none{margin-top:4px;border-top:1px solid #ffffff10;border-radius:0 0 8px 8px;color:var(--muted);justify-content:center}
@media (max-width:600px){.nav-me span,.nav-me small{display:none}}
section[id],div[id]{scroll-margin-top:56px}
.sec-row{display:flex;justify-content:space-between;align-items:baseline;gap:8px 16px;flex-wrap:wrap}
.me-box{margin:0 0 14px}
.me-chips{display:flex;gap:8px;overflow-x:auto;padding:2px 2px 6px;scrollbar-width:none}.me-chips[hidden]{display:none}
.mc{flex:none;display:inline-flex;align-items:baseline;gap:7px;min-height:36px;padding:6px 14px;font:600 13px Inter,system-ui,sans-serif;
  color:var(--text);background:var(--panel);border:1px solid var(--line);border-radius:999px;cursor:pointer}
.mc:hover{border-color:var(--cls)}
.mc-name{color:var(--cls)}.mc-lvl{color:var(--muted);font-size:12px;font-weight:400}
.mc[aria-pressed=true]{border-color:var(--cls);background:color-mix(in srgb,var(--cls) 14%,var(--panel));box-shadow:0 0 0 1px var(--cls)}
.me-ask{min-height:36px;padding:4px 14px;font:600 13px Inter,system-ui,sans-serif;color:var(--gold);background:var(--panel2);
  border:1px solid #6b5420;border-radius:999px;cursor:pointer}.me-ask[hidden]{display:none}
.gate{position:fixed;inset:0;z-index:60;display:grid;place-items:center;padding:16px;overflow-y:auto;
  background:color-mix(in srgb,var(--bg) 94%,transparent);backdrop-filter:blur(8px)}
.gate[hidden]{display:none}body.gate-on{overflow:hidden}
.gate-box{width:min(720px,100%);text-align:center}
.gate-box h2{font:700 32px Cinzel,serif;color:var(--gold);margin:0 0 6px}
.gate-sub{color:var(--muted);margin:0 0 18px;font-size:14px}
.gate-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:10px;text-align:left}
.gate-grid .ch{flex:none;width:100%}.gate-grid .ch-art{filter:none}
.gate-skip{margin-top:16px;min-height:36px;padding:4px 16px;font:600 13px Inter,system-ui,sans-serif;color:var(--muted);
  background:none;border:0;cursor:pointer;text-decoration:underline}
@media(max-width:600px){.gate{place-items:start center}.gate-box h2{font-size:26px}.gate-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}
.gate-grid .ch-art{height:84px}}
.ch{position:relative;flex:0 0 140px;scroll-snap-align:start;padding:0;text-align:left;color:var(--text);background:var(--panel);
border:1px solid var(--line);border-radius:12px;overflow:hidden;cursor:pointer;font-family:Inter,system-ui,sans-serif;transition:border-color .15s,transform .15s}
.ch:hover{border-color:var(--cls);transform:translateY(-2px)}
.ch-art{display:block;height:104px;background-color:color-mix(in srgb,var(--cls) 16%,var(--panel2));background-repeat:no-repeat;background-size:300%;background-position:50% 33%;
border-bottom:2px solid color-mix(in srgb,var(--cls) 45%,transparent);filter:saturate(.75) brightness(.8);transition:filter .15s}
.ch:hover .ch-art,.ch[aria-pressed=true] .ch-art{filter:none}
.ch-noart{display:flex;align-items:center;justify-content:center}.ch-av{width:64px;height:64px;border-radius:50%}
.ch-lvl{position:absolute;top:6px;right:6px;min-width:30px;text-align:center;font:700 16px Cinzel,serif;color:var(--gold);background:color-mix(in srgb,var(--bg) 82%,transparent);border-radius:8px;padding:1px 6px}
.ch-txt{display:grid;padding:7px 10px 9px;line-height:1.25}
.ch-txt b{font:700 14px Cinzel,serif;color:var(--cls);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ch-txt span{font-size:14px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ch-txt small{font-size:11px;color:var(--muted);margin-top:2px}
.ch[aria-pressed=true]{border-color:var(--cls);box-shadow:0 0 0 1px var(--cls),0 4px 16px color-mix(in srgb,var(--cls) 28%,transparent)}
.ch[aria-pressed=true] .ch-art{border-bottom-color:var(--cls)}
.rc-sel{display:grid;gap:5px;margin:0 0 12px;font-size:13px;color:var(--muted)}
.rc-sel select{width:100%;max-width:460px;font:600 16px Inter,system-ui,sans-serif;color:var(--gold);background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:9px 11px;cursor:pointer}
.rc-sel option,.rc-sel optgroup{color:var(--text);background:var(--panel)}
.item-ic img{width:26px;height:26px;border-radius:5px;border:2px solid var(--line);vertical-align:middle}
.next{background:linear-gradient(135deg,#3b1612,#22140f);border:1px solid #6b2a24;border-radius:14px;padding:18px 20px}
.next h2{font-size:clamp(22px,4vw,30px)}.next-meta{margin:4px 0 12px;color:var(--text)}
.news{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px 20px;margin-top:30px}
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
.mine{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--cls);border-radius:0 10px 10px 0;padding:12px 14px;margin-bottom:12px;display:grid;gap:10px}
.mine[hidden],.mine [hidden]{display:none}
.m-g h4{margin:0 0 4px;font-size:13px}.m-n{font-weight:400}
.m-take h4{color:var(--warn)}.m-get h4{color:var(--share)}.m-give h4{color:#8fe39a}
.m-g ul{list-style:none;margin:0;padding:0;display:grid;gap:6px;font-size:14px}
.m-g b{font-weight:600}.m-g b a{color:var(--text)}.m-g small{display:block;color:var(--muted);font-size:12px}
.m-ok{margin:0;color:var(--ok);font-size:14px}.m-off{margin:0;color:var(--warn);font-size:13px}
.m-line{margin:0;color:var(--muted);font-size:13px}.m-line b{font-weight:600}
.others{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:0 0 12px}
.others h4{margin:0 0 8px;font-size:13px}.others>ul{list-style:none;margin:0;padding:0;display:grid;gap:2px}
.pc[hidden]{display:none}
.pc-nick{font-size:11px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
.pc[data-player] .pc-name{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:6px;align-items:start;min-height:2.7em;line-height:1.35}
.pc-alts{margin-left:6px;font-size:11px;font-weight:500;letter-spacing:0;text-transform:none;color:var(--muted);opacity:.8}
.pc-head{display:none}
@media(max-width:600px){
.squad{grid-template-columns:minmax(0,1fr);gap:6px}
.pc{flex-direction:row;align-items:center;border-radius:12px}
.pc>a{position:static;flex:none;padding:8px 0 8px 10px}.pc>a::after{content:"";position:absolute;inset:0}
.pc-img{display:none}
.pc-head{display:block;width:44px;height:44px;border-radius:10px;border:2px solid var(--cls);background:var(--panel2)}
.pc figcaption{flex:1;min-width:0;border-top:0;padding:8px 12px}
.pc[data-player] .pc-name{min-height:0}
.pc-err{padding:8px 0}.pc-err figcaption{border-left:3px solid var(--bad)}
.squad.has-open .pc:not(.open){opacity:1}
}
.table-wrap{overflow-x:auto;border:1px solid var(--line);border-radius:12px}
table.dungeons{width:100%;border-collapse:collapse;font-size:14px;min-width:520px}
.dungeons th,.dungeons td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left}
.dungeons thead th{background:var(--panel2);font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
.dungeons tbody th{font-weight:600}.dungeons tbody th small{display:block;color:var(--muted);font-weight:400;font-size:12px}
.dungeons tr:last-child>*{border-bottom:0}
.who-col{color:var(--cls)!important;text-align:center!important}
.range{color:var(--gold);white-space:nowrap}
.diff{display:inline-flex;flex-wrap:wrap;gap:2px 8px;align-items:baseline;white-space:normal;color:var(--muted);font-size:13px}
.diff small{margin:0 0 0 -5px;font-size:11px}.diff .dl-on{color:var(--gold);font-weight:700}
.diff .dl-on+small{color:var(--gold)}
.st{text-align:center!important;font-weight:600;white-space:nowrap}
.rc-party{margin:0 0 12px}.rc-party h4{margin:0 0 8px;font-size:13px}.rc-party .note{margin:6px 0 0;color:var(--muted);font-size:12px}
.rc-table th,.rc-table td{padding:6px 8px}
.rc-table thead th.who-col{text-transform:none;letter-spacing:0;font-size:13px;line-height:1.25;min-width:84px}
.rc-table thead th.who-col b{display:block}.rc-table thead th.who-col small{display:block;color:var(--muted);font-weight:400;font-size:11px}
.rc-table thead th.who-col small.gap{color:var(--warn)}
.rc-table tbody>tr>th:first-child,.rc-table thead th:first-child{position:sticky;left:0;z-index:1;background:var(--panel);min-width:150px}
.rc-table .me-col{background:color-mix(in srgb,var(--gold) 9%,transparent)}
.rc-table thead th.me-col{box-shadow:inset 0 -2px 0 var(--gold)}
.rc-table tbody th a{color:var(--text)}.rc-table tr.q-none th a{color:var(--muted)}
.rc-table tbody th small{display:block;color:var(--muted);font-weight:400;font-size:12px}
.q-name{display:flex;flex-wrap:wrap;align-items:center;gap:6px}
.rc-table tr.q-cls>th:first-child{box-shadow:inset 3px 0 0 var(--cls)}
@media (max-width:600px){.rc-table tbody th small{display:none}.rc-table tbody>tr>th:first-child,.rc-table thead th:first-child{min-width:120px;max-width:140px}
  .rc-table thead th.who-col{min-width:68px;font-size:12px}.rc-table th,.rc-table td{padding:6px 5px}.rc-table .share{font-size:10px;padding:0 6px}}
.st-na{color:var(--muted);opacity:.45}
/* posuvná tabulka ready checku: vpravo ztmavení, vlevo stín za připnutým sloupcem questů (ten se ztmavit nesmí) */
.rc-wrap.more-l{-webkit-mask-image:none;mask-image:none}
.rc-wrap.more-l.more-r{-webkit-mask-image:linear-gradient(to right,#000 calc(100% - 32px),transparent);mask-image:linear-gradient(to right,#000 calc(100% - 32px),transparent)}
.rc-wrap.more-l .rc-table tbody>tr>th:first-child,.rc-wrap.more-l .rc-table thead th:first-child{box-shadow:6px 0 8px -4px #000c}
.rc-wrap.more-l .rc-table tr.q-cls>th:first-child{box-shadow:inset 3px 0 0 var(--cls),6px 0 8px -4px #000c}
.rc-ready{display:none;margin:0 0 8px;color:var(--ok);font-weight:600;font-size:14px}
.rc-fold{display:none;width:100%;margin-top:6px;min-height:36px;font:600 13px Inter,system-ui,sans-serif;color:var(--gold);
  background:none;border:1px dashed #6b5420;border-radius:8px;cursor:pointer}
@media(max-width:600px){.rc-fold:not([hidden]){display:block}.rc-party:not(.fold-open) tr.q-fold{display:none}
  .rc-party.all-fold:not(.fold-open) .table-wrap{display:none}.rc-party.all-fold .rc-ready{display:block}
  table.rc-table{min-width:0}.rc-table thead th.who-col{min-width:56px}.rc-table .wc-name{display:none}.rc-table td.st{font-size:12px}}
.share{font-size:11px;font-weight:600;padding:1px 8px;border-radius:999px;white-space:nowrap}
.share-cls{background:color-mix(in srgb,var(--cls) 18%,transparent);color:var(--cls);border:1px solid color-mix(in srgb,var(--cls) 45%,transparent)}
.share-race{background:#3a2626;color:#e0a0a0}
.share-yes{background:#16324d;color:var(--share)}.share-in{background:#3b2a5c;color:#c9b2ff}.share-no{background:#4a3a14;color:var(--warn)}
.st-fit{color:var(--ok)}.st-share{color:var(--share);font-size:17px}.st-take{color:var(--warn)}.st-over{color:var(--muted)}.st-pre{color:#9fd3a3;font-size:12px}
tr.best{background:#2c1611}tr.best th:first-child{box-shadow:inset 3px 0 var(--red)}
.note{color:var(--muted);font-size:13px}
.chip-done{background:#1f4224;color:#8fe39a;margin-left:8px;vertical-align:middle}.dungeons tr.done td{opacity:.45}
.prof-grid+.all-dungeons{margin-top:30px}.all-dungeons summary{cursor:pointer;color:var(--gold);font:600 16px Cinzel,serif;margin-bottom:10px}
footer{margin-top:36px;padding-top:16px;border-top:1px solid var(--line);color:var(--muted);font-size:12px}
code{background:var(--panel2);padding:1px 5px;border-radius:4px}
.sub{font-size:15px;color:var(--muted);margin:18px 0 10px}
.titles,.counters{list-style:none;margin:0;padding:0;display:grid;gap:10px}
.titles{grid-template-columns:repeat(auto-fill,minmax(200px,1fr))}
@media(min-width:1000px){.titles{grid-template-columns:repeat(var(--cols,5),minmax(0,1fr))}}
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
.c-more{color:var(--gold);white-space:nowrap}
@media(max-width:600px){.c-s~.c-s,.c-more{display:none}}.counters-box[open] .c-more{display:none}
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
.st-group li b{font-weight:600}
.st-body li{justify-content:flex-start}.st-body li span{flex:0 0 58px;color:var(--muted)}.st-bonus{color:var(--ok);font-weight:600}.st-malus{color:var(--bad);font-weight:600}
.st-sick{margin:0;padding:8px 10px;border:1px solid #6b2a24;background:#3b1612;border-radius:10px;color:#ffb3a8}
.sheet-alts{display:flex;flex-wrap:wrap;gap:6px}
.sheet-alts button{display:inline-flex;align-items:baseline;gap:6px;padding:5px 10px;border:1px solid var(--line);border-radius:999px;
background:var(--panel2);color:var(--text);font:13px/1.3 Inter,system-ui,sans-serif;cursor:pointer;text-align:left}
.sheet-alts button:hover{border-color:var(--cls)}
.sheet-alts b{color:var(--cls);font-weight:600}.sheet-alts i{font-style:normal;font-weight:600;color:var(--gold)}
.sheet-alts button[aria-pressed=true]{background:color-mix(in srgb,var(--cls) 18%,var(--panel2));border-color:var(--cls)}
.sheet-dim{display:none}
.sheet .cls{display:flex;flex-wrap:wrap;align-items:center;gap:4px}.sheet .cls .chip{margin-left:0;white-space:nowrap}
@media(max-width:600px){
.sheet{position:fixed;left:0;right:0;bottom:0;z-index:60;margin:0;max-height:88vh;overflow-y:auto;overscroll-behavior:contain;
border-radius:16px 16px 0 0;border-bottom:0;box-shadow:0 -12px 40px rgba(0,0,0,.6);padding-top:0}
.sheet .card-head{position:sticky;top:0;z-index:2;background:var(--panel);padding:12px 0 8px;align-items:flex-start}
.sheet .who h3{font-size:19px}.sheet .lvl{font-size:26px}
.sheet-dim:not([hidden]){display:block;position:fixed;inset:0;z-index:59;background:rgba(0,0,0,.6)}
body.sheet-on{overflow:hidden}
}
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
.sl-weak::after{content:"🩴";position:absolute;left:1px;top:0;font-size:10px;line-height:1;text-shadow:0 0 3px #000}
.d-outfit{font:600 15px/1.3 Cinzel,serif;color:var(--gold)}
.d-gap{color:var(--warn);font-size:12px}
.pbars{list-style:none;margin:4px 0 0;padding:0;display:grid;gap:5px}
.pbars li{display:grid;grid-template-columns:1fr auto;gap:0 8px;font-size:12px}.pbars li b{color:var(--gold);font-size:12px;font-weight:600}
.pbars .pb-sec{color:var(--muted)}
.d-pet{font-size:12px}.d-pet small{color:var(--muted)}
box-shadow:0 0 0 2px var(--panel);z-index:1}
.next{display:flex;flex-direction:column;gap:14px}.next .eyebrow{margin:0}
.next h2 .chip{font-family:Inter,system-ui,sans-serif}
.next-meta{margin:0;display:flex;flex-wrap:wrap;align-items:center;gap:6px 10px;color:var(--muted);font-size:14px}
.nx-range{color:var(--gold);font-weight:600;border:1px solid #6b5420;background:#2c2014;border-radius:999px;padding:1px 10px}
.nx-rc{display:flex;flex-wrap:wrap;align-items:center;gap:6px 14px;font-size:13px;color:var(--text);
background:#0003;border:1px solid #ffffff10;border-radius:10px;padding:8px 12px}
.nx-rc:hover{text-decoration:none;border-color:var(--gold)}
.nx-p{white-space:nowrap;color:var(--warn)}.nx-p b{color:var(--cls);font-weight:600}.nx-p-ok{color:var(--ok)}
.nx-you{color:var(--text);font-weight:600}.nx-you[hidden]{display:none}.nx-you.nx-you-ok{color:var(--ok)}
.nx-rc.has-you .nx-p-ok:not(.nx-rest){display:none}.nx-p[hidden]{display:none}
.nx-go{margin-left:auto;color:var(--gold);font-weight:600}
.ah-wrap{overflow-x:auto;margin-top:-6px}
.ahead{width:100%;border-collapse:collapse;font-size:13px;table-layout:fixed}
.ahead th,.ahead td{padding:4px 2px;text-align:center;border-top:1px solid #ffffff10}
.ahead thead th{border-top:0;font-size:12px;font-weight:600;color:var(--cls,var(--muted));white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ahead th:first-child{width:34%;text-align:left}.ahead th:nth-child(2){width:9%}
.ahead tbody th{text-align:left;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ah-cap{color:var(--gold);font-size:12px}
.ah-best td,.ah-best th{background:#e8b85c14}.ah-best th{color:var(--gold)}
.ah-done th{color:var(--muted);font-weight:400}
.ahead .ah-me{background:#e8b85c1a}.ahead thead .ah-me{box-shadow:inset 0 -2px 0 var(--gold)}
.ah-more{display:none;width:100%;margin-top:6px;min-height:36px;font:600 13px Inter,system-ui,sans-serif;color:var(--gold);
  background:none;border:1px dashed #6b5420;border-radius:8px;cursor:pointer}
@media(max-width:600px){.ahead:not(.ah-open) .ah-far{display:none}.ah-more{display:block}}
.ahead:not(.ah-old-open) .ah-old{display:none}
.ahead .ah-old-row td{text-align:left;padding:2px 0}
.ah-old-btn{background:none;border:0;padding:4px 0;min-height:32px;font:600 12px Inter,system-ui,sans-serif;color:var(--muted);cursor:pointer}
.ah-old-btn:hover{color:var(--gold)}
.ah{display:inline-block;min-width:30px;padding:1px 5px;border-radius:999px;font-size:12px;font-weight:600;line-height:1.5;white-space:nowrap}
.ah-short{display:none}
.ah-ok{background:#6fbf7326;color:var(--ok)}.ah-soon{background:#e8a13c26;color:var(--warn)}.ah-last{background:#e0574b2e;color:var(--bad)}
.ah-low{color:var(--muted);border:1px dashed #ffffff30;font-weight:400}
.ah-alt::before{content:"";display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--alt);margin-right:3px;vertical-align:1px}.ah-late{color:var(--muted);font-weight:400}
.ah-note{margin:4px 0 0;font-size:11px;color:var(--muted)}
.ah-legend{margin:8px 0 0;font-size:11px;color:var(--muted);display:flex;flex-wrap:wrap;gap:4px 8px;align-items:center}
@media(max-width:480px){.ahead{font-size:12px}.ahead th:first-child{width:36%}.ahead th:nth-child(2){width:8%}
.ah{min-width:22px;padding:0 3px;font-size:11px}.ah-long{display:none}.ah-short{display:inline}.ah-legend .ah-long{display:inline}
.ah-low{padding:0 2px;font-size:10px}.ah-low .ah-long{display:inline}}
.nw{list-style:none;margin:0;padding:0;display:grid}
.nw>li{display:grid;grid-template-columns:34px 1fr;gap:12px;padding:10px 0;border-top:1px solid #ffffff10}
.nw>li:first-child{border-top:0;padding-top:4px}
@media(min-width:761px){.nw{grid-template-columns:repeat(auto-fill,minmax(300px,1fr));column-gap:32px}.nw>li:first-child{border-top:1px solid #ffffff10;padding-top:10px}}
.nw-av{width:34px;height:34px;border-radius:50%;border:2px solid var(--cls,var(--line));background:var(--panel2)}
.nw-main{min-width:0;display:grid;gap:4px}.nw-main p{margin:0}
.nw-head{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
.nw .who-n{color:var(--cls);font-size:15px}
.nw-lvl{color:var(--gold);font-weight:700;font-size:13px}
.nw-xp{margin-left:auto;color:#c9b2ff;font-size:13px;font-weight:600}
.nw-profs{font-size:13px;color:var(--muted)}.nw-profs b{color:var(--text);font-weight:600}
.nw-ach{font-size:13px;color:var(--gold)}
.nw-lines{display:grid;grid-template-columns:minmax(0,1fr) auto auto;gap:3px 10px;align-items:baseline;font-size:13px}
.nw-line{display:contents}.nw-char{color:var(--cls);font-weight:600}.nw-pct{color:var(--muted);font-size:12px;text-align:right}
.nw-lines .nw-lvl{text-align:right;white-space:nowrap}.nw-lines .nw-xp{margin:0;text-align:right;white-space:nowrap}
.nw-more summary{cursor:pointer;color:var(--muted);font-size:12px}.nw-det{display:grid;gap:3px;margin-top:6px}.nw-det>b{color:var(--cls);font-size:13px;font-weight:600}
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
.lt-who.lt-no{color:var(--muted);background:transparent;border-color:var(--line)}.lt-who.lt-maybe{color:var(--warn)}
.lt-who.lt-small{color:var(--text);background:transparent}
.lt-none{font-size:12px;color:var(--muted)}
.lt-flag{font-size:12px;padding:1px 8px;border-radius:999px;background:#4a3a14;color:var(--warn);font-weight:600}
.lt-who.dim{color:var(--muted);background:transparent;border-color:var(--line)}
.lt-who.dim b{color:color-mix(in srgb,var(--cls) 60%,var(--muted))}.lt-who.dim small{color:var(--muted)}.lt-altc{display:none}.lt-altc.lt-me{display:inline-block}
.craft[hidden],.craft-none[hidden],.loot-empty[hidden]{display:none}.lt-whos:has(.lt-me) .lt-none{display:none}#loot.loot-mine .lt-off{display:none}.loot-dg[hidden]{display:none}
.loot-all{font:600 13px Inter,system-ui,sans-serif;color:var(--gold);background:none;border:0;padding:0;cursor:pointer}
.lt-who.lt-second{background:transparent;border-style:dashed;color:var(--muted)}
.legend summary{cursor:pointer;list-style:none}.legend summary::-webkit-details-marker{display:none}
.lg-q{display:inline-grid;place-items:center;width:22px;height:22px;margin-left:4px;border-radius:50%;border:1px solid var(--gold);
color:var(--gold);font-weight:700;font-size:13px;vertical-align:1px}.legend[open] .lg-q{background:var(--gold);color:var(--bg)}
.lg{list-style:none;margin:8px 0 0;padding:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,300px),1fr));gap:6px 20px}
.lg li{display:grid;grid-template-columns:auto minmax(0,1fr);gap:10px;align-items:center}.lg li>:first-child{justify-self:start}.lg li>span:last-child{color:var(--muted)}
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
.craft li{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:2px 14px;align-items:baseline;padding:9px 14px;
border-top:1px solid #ffffff10;font-size:14px}
.craft li:first-child{border-top:0}
.cf-gain{font-size:12px;color:var(--muted);font-style:italic;text-align:right}
.cf-need{grid-column:1/-1;font-size:12px;color:var(--muted)}
.cf-title{font-weight:500}.cf-title .item{font-weight:600}
@media(max-width:600px){.craft li{grid-template-columns:minmax(0,1fr)}.cf-gain{text-align:left}}
.boss-quip{margin:2px 0 6px;font-style:italic;color:var(--gold);font-size:13px}
.src-note{color:var(--muted);font-size:12px;cursor:help}.pg-low{margin:4px 0 0}
.prof-grid2{display:grid;grid-template-columns:minmax(110px,1.3fr) repeat(var(--cols),minmax(0,1fr));gap:8px 14px;align-items:start;
background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px 16px;font-size:13px}
.pg-h{font-weight:600;font-size:12px;line-height:18px}.pg-name{color:var(--text);line-height:18px}.pg-sec{color:var(--muted)}
.pg-cell{display:grid;gap:3px}.pg-cell b{font-weight:600;font-size:12px;line-height:18px}
.pg-none b{color:var(--line)}.pg-cell b{color:var(--cls)}.pg-none b{color:var(--line)}
.prof-list{display:none;list-style:none;margin:0;padding:4px 14px;background:var(--panel);border:1px solid var(--line);border-radius:12px;font-size:13px}
.prof-list li{display:grid;grid-template-columns:72px minmax(0,1fr);gap:8px;padding:8px 0;border-top:1px solid #ffffff10}
.prof-list li:first-child{border-top:0}.pl-who{font-weight:600;overflow-wrap:anywhere}
.pl-ps{display:flex;flex-wrap:wrap;gap:4px 12px}.pl-p b{color:var(--cls);font-weight:600}
@media(max-width:600px){.prof-grid2{display:none}.prof-list{display:block}}
/* cíle pro prst: na mobilu aspoň 36 px na výšku */
@media(max-width:600px){
.nav a{min-height:36px;display:inline-flex;align-items:center}.nav-me{min-height:36px;align-items:center}
.sheet-armory,.loot-all:not([hidden]),.rc-source a,.k-more a{display:inline-flex;align-items:center;min-height:36px;padding:0 4px}
footer a{display:inline-block;padding:8px 2px}
.lt-who:not(.lt-altc),.lt-altc.lt-me,.lt-flag{display:inline-flex;align-items:center;gap:4px;min-height:36px;padding:2px 10px}
.lt-item a.item,a.ql{display:inline-flex;align-items:center;min-height:36px}
.counters-box>summary,.nw-more>summary,.all-dungeons>summary,.legend>summary{min-height:36px;padding-top:6px;padding-bottom:6px}
.lg-q{width:32px;height:32px}}
"""

JS = """
const OWNER=JSON.parse(document.getElementById('rc-chars')?.textContent||'{}');
const NAMES=JSON.parse(document.getElementById('rc-names')?.textContent||'{}');
const MAINS=JSON.parse(document.getElementById('rc-mains')?.textContent||'{}');
const navMe=document.querySelector('.nav-me');
function syncMe(){if(navMe){const n=NAMES[viewer],same=n&&n[1].split(' ')[0]===n[0];navMe.hidden=!document.querySelector('.me-menu');
  navMe.querySelector('b').textContent=n?(same?n[1]:n[0]):'Kdo jsi?';navMe.querySelector('small').textContent=n&&!same?n[1]:'';navMe.title=n?'Hraju za '+n[1]+': změnit postavu':'Vyber, za koho hraješ'}
  document.querySelectorAll('.ahead [data-player]').forEach(x=>x.classList.toggle('ah-me',!!viewer&&x.dataset.player===OWNER[viewer]));
  document.querySelectorAll('.mm-c[data-me]').forEach(b=>b.setAttribute('aria-pressed',b.dataset.me===viewer));
  document.querySelectorAll('.mm-p').forEach(g=>g.hidden=!viewer||g.dataset.player!==OWNER[viewer]);
  syncNext()}
function syncNext(){const rc=document.querySelector('.nx-rc');if(!rc)return;const you=rc.querySelector('.nx-you'),rest=rc.querySelector('.nx-rest');
  let todo={};try{todo=JSON.parse(rc.dataset.todo||'{}')}catch(e){}
  // za hráče postava z jeho sloupce v ready checku (moje volba, ručně přepnutý sloupec), jinak odhad výpravy
  const others=[];
  rc.querySelectorAll('.nx-p[data-player]').forEach(p=>{const s=colOf(p.dataset.player)||p.dataset.def,st=todo[s],mine=!!viewer&&p.dataset.player===OWNER[viewer];
    p.hidden=!st||mine;if(!st)return;if(!mine)others.push(st);
    p.classList.toggle('nx-p-ok',st==='✓ připraveno');p.classList.toggle('nx-p-todo',st!=='✓ připraveno');
    if(NAMES[s])p.style.setProperty('--cls',NAMES[s][2]);p.replaceChildren(p.querySelector('b'),' '+st)});
  rc.classList.toggle('has-you',!!viewer);you.hidden=!viewer;
  rest.hidden=!viewer||!others.length||others.some(st=>st!=='✓ připraveno');if(!viewer)return;
  const st=todo[viewer];
  you.textContent='Ty ('+NAMES[viewer][1]+'): '+(st||'do '+rc.dataset.dungeon+' se levelem nevejdeš');you.classList.toggle('nx-you-ok',st==='✓ připraveno')}
// sloupce hráčů: můj je vybraná postava, ostatní odhad, nebo co jsem jim ručně přepnul (jen do zavření karty)
let cols={};try{cols=JSON.parse(sessionStorage.getItem('cols')||'{}')}catch(e){}
function colOf(p){if(viewer&&OWNER[viewer]===p)return viewer;const c=cols[p];return c&&OWNER[c]===p?c:''}
function charLabel(s){const n=NAMES[s];return n[1].split(' ')[0]===n[0]?n[1]:n[1]+' ('+n[0]+')'}
function syncCols(){
  panels.forEach(rc=>{const t=rc.querySelector('.rc-table');if(!t)return;
    const byP={},shown=new Set();
    t.querySelectorAll('thead th[data-player]').forEach(th=>(byP[th.dataset.player]??=[]).push(th));
    Object.entries(byP).forEach(([p,ths])=>{const want=colOf(p),has=ths.some(th=>th.dataset.slug===want);
      shown.add(has?want:(ths.find(th=>'def' in th.dataset)||ths[0]).dataset.slug);
      ths.forEach(th=>th.classList.toggle('col-manual',has&&want!==viewer))});
    t.querySelectorAll('[data-player]').forEach(c=>{c.hidden=!shown.has(c.dataset.slug);c.classList.toggle('me-col',!!viewer&&c.dataset.slug===viewer)});
    fold(rc);
    // „Můžeš nasdílet“ jen těm, kdo jsou zrovna ve sloupcích
    rc.querySelectorAll('.mine li[data-takers]').forEach(li=>{const who=words(li.dataset.takers).filter(s=>shown.has(s)&&NAMES[s]).map(charLabel);
      li.hidden=!who.length;li.querySelector('.m-takers').textContent=who.length?'potřebuje: '+who.join(', '):''});
    rc.querySelectorAll('.mine .m-give').forEach(g=>{const n=g.querySelectorAll('li:not([hidden])').length;g.hidden=!n;g.querySelector('.m-n').textContent='('+n+')'})});
  syncAhead();syncNext()}
const AH=new WeakMap();
function aheadCell(r,s){const [nk,nm,,lvl,cl]=NAMES[s],who=nk+' · '+nm+' ('+cl+')',min=+r.dataset.min,cap=+r.dataset.cap;
  if(lvl<min)return['low','za '+(min-lvl),who+': dungeon od levelu '+min];
  if(lvl>cap)return['late','pozdě',who+': od levelu '+(cap+1)+' už procházka'];
  const left=cap-lvl;return[left===0?'last':left<=2?'soon':'ok',String(left),left?who+': ještě '+left+' lvl, pak procházka':who+': poslední level, pak procházka']}
function syncAhead(){document.querySelectorAll('.ahead tbody tr[data-min]').forEach(r=>r.querySelectorAll('td[data-player]').forEach(td=>{
  if(!AH.has(td))AH.set(td,td.innerHTML);const s=colOf(td.dataset.player);
  if(!s||!NAMES[s]){td.innerHTML=AH.get(td);return}
  const [k,t,tip]=aheadCell(r,s),sp=document.createElement('span');sp.className='ah ah-'+k;sp.title=tip;
  if(NAMES[s][5]){sp.classList.add('ah-alt');sp.style.setProperty('--alt',NAMES[s][2])}
  sp.innerHTML=t==='pozdě'?'<span class="ah-long">pozdě</span><span class="ah-short">–</span>':t.startsWith('za ')?'<span class="ah-long">za </span>'+t.slice(3):t;
  td.replaceChildren(sp)}))}
const colMenu=document.createElement('div');colMenu.className='col-menu';colMenu.setAttribute('role','menu');colMenu.hidden=true;document.body.append(colMenu);
function setCol(p,s){colMenu.hidden=true;if(viewer&&OWNER[viewer]===p){if(s)me(s);return}
  if(s)cols[p]=s;else delete cols[p];try{sessionStorage.setItem('cols',JSON.stringify(cols))}catch(e){}syncCols()}
function openCol(th){const p=th.dataset.player,mineP=!!viewer&&OWNER[viewer]===p,item=(text,lvl,cls,on,go)=>{
    const b=document.createElement('button');b.type='button';b.className='mm-c'+(cls?'':' mm-reset');b.setAttribute('role','menuitemradio');
    b.setAttribute('aria-checked',!!on);if(cls)b.style.setProperty('--cls',cls);
    b.innerHTML='<span class="mm-name"></span><span class="mm-lvl"></span>';b.querySelector('.mm-name').textContent=text;
    b.querySelector('.mm-lvl').textContent=lvl;b.addEventListener('click',go);colMenu.append(b)};
  colMenu.textContent='';
  th.closest('table').querySelectorAll(`thead th[data-player="${CSS.escape(p)}"]`).forEach(h=>{const s=h.dataset.slug;
    item(NAMES[s][1]+(!mineP&&'def' in h.dataset?' · odhad':''),NAMES[s][3],NAMES[s][2],s===th.dataset.slug,()=>setCol(p,s))});
  if(!mineP&&colOf(p))item('zpět na odhad','','',false,()=>setCol(p,''));
  colMenu.hidden=false;const r=th.getBoundingClientRect();
  colMenu.style.top=(scrollY+r.bottom+4)+'px';colMenu.style.left=Math.max(8,Math.min(scrollX+r.left,scrollX+innerWidth-colMenu.offsetWidth-8))+'px';
  (colMenu.querySelector('[aria-checked=true]')||colMenu.querySelector('.mm-c')).focus({preventScroll:true})}
document.addEventListener('click',ev=>{const b=ev.target.closest('.col-sw');
  if(b){ev.stopPropagation();const th=b.closest('th');if(!colMenu.hidden&&colMenu.dataset.for===th.dataset.slug){colMenu.hidden=true;return}
    colMenu.dataset.for=th.dataset.slug;openCol(th);return}
  if(!colMenu.hidden&&!colMenu.contains(ev.target))colMenu.hidden=true});
addEventListener('keydown',ev=>{if(ev.key==='Escape'&&!colMenu.hidden)colMenu.hidden=true});
const panels=[...document.querySelectorAll('.rc')];
const sel=document.querySelector('.rc-sel select');
const selOpts=sel?[...sel.options]:[];
const lootBox=document.getElementById('loot'),lootBtn=document.querySelector('.loot-all');
let viewer='',lootAll=false;
const words=s=>(s||'').split(' ').filter(Boolean);
function orderSel(){if(!sel)return;const cur=sel.value;sel.textContent='';
  const grp=(label,list)=>{if(!list.length)return;const g=document.createElement('optgroup');g.label=label;g.append(...list);sel.append(g)};
  if(viewer){const fit=o=>words(o.dataset.fit).includes(viewer);grp('Sedí ti levelem',selOpts.filter(fit));grp('Ostatní',selOpts.filter(o=>!fit(o)))}
  else{grp('Před námi',selOpts.filter(o=>!o.dataset.past));grp('Parta už odehrála',selOpts.filter(o=>o.dataset.past))}
  sel.value=cur}
function syncLoot(){const t=selOpts.find(o=>o.value===sel?.value)?.dataset.title,dgs=[...document.querySelectorAll('.loot-dg')];
  const match=!!viewer&&dgs.some(d=>d.dataset.title===t);
  dgs.forEach(d=>d.hidden=viewer?d.dataset.title!==t:d.dataset.past!==undefined);
  document.querySelector('.loot-empty')?.toggleAttribute('hidden',!viewer||match);
  document.querySelectorAll('.lt-who[data-slug]').forEach(c=>{const on=c.dataset.slug===viewer;c.classList.toggle('lt-me',on);c.classList.toggle('dim',!!viewer&&!on)});
  document.querySelectorAll('.lt').forEach(l=>l.classList.toggle('lt-off',!!viewer&&!l.querySelector('.lt-me')));
  lootBox?.classList.toggle('loot-mine',!!viewer&&!lootAll);
  if(lootBtn){lootBtn.hidden=!viewer;lootBtn.textContent=lootAll?'jen kusy pro mou postavu':'ukázat i kusy pro ostatní'}}
lootBtn?.addEventListener('click',()=>{lootAll=!lootAll;syncLoot()});
function syncCraft(){const mine=[...document.querySelectorAll('.craft-for')];mine.forEach(u=>u.hidden=u.dataset.for!==viewer);
  const has=mine.some(u=>!u.hidden);document.querySelector('.craft-all')?.toggleAttribute('hidden',!!viewer);
  document.querySelector('.craft-none')?.toggleAttribute('hidden',!viewer||has)}
function pick(i){panels.forEach(a=>a.hidden=a.id!=='rc-'+i);if(sel)sel.value=String(i);
  const t=selOpts.find(o=>o.value==i);
  try{if(t)localStorage.setItem('rc-pick',t.dataset.title)}catch(e){}
  syncLoot()}
function fitJump(slug){const rc=panels.find(a=>!a.hidden);if(!slug||(rc&&words(rc.dataset.fit).includes(slug)))return;
  const to=panels.find(a=>words(a.dataset.fit).includes(slug));if(to)pick(+to.id.slice(3))}
function showFig(slug){const f=document.querySelector(`.pc[data-slug="${CSS.escape(slug)}"]`);if(!f||!f.hidden)return;
  document.querySelectorAll(`.pc[data-player="${CSS.escape(f.dataset.player)}"]`).forEach(x=>x.hidden=x!==f)}
function syncChips(){const p=OWNER[viewer];document.querySelectorAll('.me-chips').forEach(d=>d.hidden=!viewer||d.dataset.player!==p);
  const ask=document.querySelector('.me-ask');if(ask)ask.hidden=!!viewer}
// mobil: řádky, kde nikdo z ukázaných postav nic nemusí, na konec tabulky pod přepínač
const thin=matchMedia('(max-width:600px)');
function fold(rc){const box=rc.querySelector('.rc-party'),tb=box?.querySelector('tbody'),btn=box?.querySelector('.rc-fold');if(!tb||!btn)return;
  const rows=[...tb.querySelectorAll('tr[data-q]')].sort((a,b)=>a.dataset.q-b.dataset.q);
  const idle=r=>{const c=[...r.querySelectorAll('td')].filter(td=>!td.hidden);return c.length>0&&c.every(td=>td.matches('.st-done,.st-na'))};
  const done=rows.filter(idle),n=done.length;
  rows.forEach(r=>r.classList.toggle('q-fold',done.includes(r)));
  tb.append(...(thin.matches?rows.filter(r=>!done.includes(r)).concat(done):rows));
  box.classList.toggle('all-fold',n>0&&n===rows.length);btn.hidden=!n;btn.dataset.n=n;foldText(box)}
function foldText(box){const btn=box.querySelector('.rc-fold'),n=+btn.dataset.n,on=box.classList.contains('fold-open');
  btn.setAttribute('aria-expanded',on);
  btn.textContent=on?'skrýt vyřízené ▴':n+' '+(n===1?'quest':n<5?'questy':'questů')+' nikdo nemusí řešit ▾'}
document.querySelectorAll('.rc-fold').forEach(b=>b.addEventListener('click',()=>{const box=b.closest('.rc-party');box.classList.toggle('fold-open');foldText(box)}));
thin.addEventListener('change',()=>panels.forEach(fold));
function me(slug){viewer=slug;
  document.querySelectorAll('.mc[data-me]').forEach(b=>b.setAttribute('aria-pressed',b.dataset.me===slug));
  panels.forEach(rc=>rc.querySelectorAll('.mine').forEach(c=>c.hidden=c.dataset.slug!==slug));
  syncMe();syncCols();
  orderSel();if(slug)showFig(slug);syncChips();
  const on=document.querySelector('.me-chips:not([hidden]) .mc[aria-pressed=true]');
  if(on)on.parentElement.scrollLeft=on.offsetLeft-on.parentElement.offsetLeft-2;
  syncLoot();syncCraft();
  try{localStorage.setItem('player',OWNER[slug]||'');if(slug)localStorage.setItem('me',slug);else localStorage.removeItem('me')}catch(e){}}
document.querySelectorAll('.mc[data-me]').forEach(b=>b.addEventListener('click',()=>{me(b.dataset.me);fitJump(b.dataset.me)}));
const menu=document.querySelector('.me-menu');
function menuOpen(on){if(!menu)return;menu.hidden=!on;navMe.setAttribute('aria-expanded',on);
  if(on)(menu.querySelector('.mm-c[aria-pressed=true]')||menu.querySelector('.mm-c')).focus({preventScroll:true})}
// bez vybraného hráče otevře lišta volbu hráče, jinak menu jeho postav
navMe?.addEventListener('click',ev=>{ev.stopPropagation();if(!viewer){openGate();return}menuOpen(menu.hidden)});
menu?.querySelector('.mm-switch')?.addEventListener('click',()=>{menuOpen(false);openGate()});
menu?.querySelectorAll('.mm-c[data-me]').forEach(b=>b.addEventListener('click',()=>{me(b.dataset.me);fitJump(b.dataset.me);menuOpen(false);navMe.focus()}));
document.addEventListener('click',ev=>{if(menu&&!menu.hidden&&!menu.contains(ev.target))menuOpen(false)});
addEventListener('keydown',ev=>{if(ev.key==='Escape'&&menu&&!menu.hidden){menuOpen(false);navMe.focus()}});
document.querySelector('.me-ask')?.addEventListener('click',ev=>{ev.stopPropagation();openGate()});
// první návštěva: stránka se zeptá, kdo jsi; volba (i „jen koukám“) se pamatuje v prohlížeči
const gate=document.querySelector('.gate');
function openGate(){if(!gate)return;gate.hidden=false;document.body.classList.add('gate-on');
  gate.querySelector('.ch').focus({preventScroll:true})}
function closeGate(){gate.hidden=true;document.body.classList.remove('gate-on')}
addEventListener('keydown',ev=>{if(ev.key==='Escape'&&gate&&!gate.hidden&&viewer)closeGate()});
function gateDone(slug){closeGate();
  try{localStorage.setItem('me-asked','1')}catch(e){}me(slug);fitJump(slug)}
gate?.querySelectorAll('.ch[data-pick]').forEach(b=>b.addEventListener('click',()=>gateDone(b.dataset.pick)));
gate?.querySelector('.gate-skip').addEventListener('click',()=>gateDone(''));
{let asked=false;try{asked=!!localStorage.getItem('me-asked')||!!localStorage.getItem('player')||!!localStorage.getItem('me')}catch(e){asked=true}
 if(!asked&&!location.hash.startsWith('#char-'))openGate()}
document.querySelectorAll('.ah-old-btn').forEach(b=>b.addEventListener('click',()=>{const on=b.closest('.ahead').classList.toggle('ah-old-open');
  b.textContent='✓ odehrané ('+b.dataset.n+') '+(on?'▴':'▾')}));
document.querySelectorAll('.ah-more').forEach(b=>b.addEventListener('click',()=>{const t=b.previousElementSibling.querySelector('.ahead'),
  on=t.classList.toggle('ah-open');b.textContent=on?'méně dungeonů ▴':'další dungeony ('+b.dataset.n+') ▾'}));
if(sel)sel.addEventListener('change',()=>pick(+sel.value));
{let p='',m='',saved=null;try{m=localStorage.getItem('me')||'';p=localStorage.getItem('player')||OWNER[m]||'';saved=localStorage.getItem('rc-pick')}catch(e){}
  me(OWNER[m]&&OWNER[m]===p?m:MAINS[p]||'');const o=selOpts.find(o=>o.dataset.title===saved);if(o)pick(+o.value);fitJump(viewer)}
const pcs=[...document.querySelectorAll('.pc[data-slug]')];
const hasSheet=s=>[...document.querySelectorAll('.sheet')].some(x=>x.dataset.slug===s);
let openNow='';
const narrow=matchMedia('(max-width:600px)'),dim=document.querySelector('.sheet-dim');
function openSheet(slug,scroll){
  document.querySelectorAll('.sheet').forEach(x=>x.hidden=x.dataset.slug!==slug);
  const was=openNow;openNow=slug;if(slug)showFig(slug);
  if(dim)dim.hidden=!slug;document.body.classList.toggle('sheet-on',!!slug);
  pcs.forEach(p=>{const on=p.dataset.slug===slug;p.classList.toggle('open',on);p.querySelector('a').setAttribute('aria-expanded',on)});
  document.querySelector('.squad').classList.toggle('has-open',!!slug);
  const hint=document.querySelector('.sheet-hint'); if(hint) hint.hidden=!!slug;
  history.replaceState(null,'',slug?'#char-'+slug:location.pathname+location.search);
  const el=slug&&document.getElementById('char-'+slug);if(!el)return;
  // na mobilu panel zespodu, na desktopu posun ke kartě pod fotkou
  if(narrow.matches){if(!was)el.querySelector('.sheet-close').focus({preventScroll:true})}
  else if(scroll)el.scrollIntoView({behavior:'smooth',block:'start'})}
pcs.forEach(p=>p.querySelector('a').addEventListener('click',ev=>{ev.preventDefault();
  openSheet(p.classList.contains('open')?'':p.dataset.slug,true)}));
document.querySelectorAll('.sheet-alts button').forEach(b=>b.addEventListener('click',()=>openSheet(b.dataset.open,false)));
document.querySelectorAll('.sheet-close').forEach(b=>b.addEventListener('click',()=>openSheet('')));
dim?.addEventListener('click',()=>openSheet(''));
addEventListener('keydown',ev=>{if(ev.key==='Escape'&&openNow)openSheet('')});
// kartu otevře jen odkaz #char-…; obnovení stránky ji znovu neotevře
{const h=decodeURIComponent(location.hash),nav=performance.getEntriesByType?.('navigation')[0];
 if(h.startsWith('#char-')){if(nav?.type!=='reload'&&hasSheet(h.slice(6)))openSheet(h.slice(6),true);
   else history.replaceState(null,'',location.pathname+location.search)}}
addEventListener('hashchange',()=>{const h=decodeURIComponent(location.hash);
  if(h.startsWith('#char-')&&hasSheet(h.slice(6))) openSheet(h.slice(6),true)});
// posuvné řady (lišta, postavy v ready checku): ztmavení u okraje, za kterým ještě něco je
function edges(el){const l=el.scrollLeft>2,r=el.scrollLeft+el.clientWidth<el.scrollWidth-2;
  el.classList.toggle('more-l',l);el.classList.toggle('more-r',r)}
document.querySelectorAll('.nav-links,.me-chips,.rc-wrap').forEach(el=>{el.addEventListener('scroll',()=>edges(el),{passive:true});
  new ResizeObserver(()=>edges(el)).observe(el);edges(el)});
// odkaz sekce, kterou čtu: nadpis v horní třetině pod lištou (nejvýš položený), jinak sekce, která tu třetinu překrývá;
// po kliknutí v liště drží odkaz, na který jsem klikl, dokud sám nescrolluju
{const links=[...document.querySelectorAll('.nav-links a')],bar=document.querySelector('.nav-links');
 const secs=links.map(a=>document.getElementById(a.hash.slice(1))).filter(Boolean);
 let cur=null,held=null;
 links.forEach(a=>a.addEventListener('click',()=>{held=document.getElementById(a.hash.slice(1));cur=null;mark()}));
 ['wheel','touchstart','keydown'].forEach(t=>addEventListener(t,()=>{held=null},{passive:true}));
 function mark(){const h=document.querySelector('.nav')?.offsetHeight||0,y=h+Math.max(24,(innerHeight-h)*.3);
   const top=s=>s.getBoundingClientRect().top;
   let on=held||secs.find(s=>top(s)>=h-2&&top(s)<=y)||secs.filter(s=>top(s)<=y).pop()||null;
   if(!held&&innerHeight+scrollY>=document.documentElement.scrollHeight-2)on=secs[secs.length-1];
   if(on===cur)return;cur=on;
   links.forEach(a=>{const it=on&&a.hash==='#'+on.id;a.classList.toggle('on',!!it);it?a.setAttribute('aria-current','true'):a.removeAttribute('aria-current')});
   const a=links.find(a=>a.classList.contains('on'));
   if(a&&bar)bar.scrollTo({left:a.offsetLeft-bar.offsetLeft-bar.clientWidth/2+a.offsetWidth/2,behavior:'smooth'})}
 if(secs.length){const io=new IntersectionObserver(mark,{threshold:[0,.25,.5,.75,1]});secs.forEach(s=>io.observe(s));
   addEventListener('scroll',mark,{passive:true});mark()}}
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

    loaded = load_party(party, token)
    members = [m for m in loaded if not m.get("alt")]  # hlavní postavy: místa v partě, plán, tituly, loot, řemesla
    ok = [m for m in members if m["ok"]]
    chars = [m for m in loaded if m["ok"]]  # navíc alti: ready check, karty, novinky
    if not ok:
        sys.exit("Nepodařilo se načíst žádnou postavu, stránku neměním.")
    if token:
        add_icons(token, region, loaded)
    rows, best = plan_dungeons(ok, dungeons, chars)
    # ručně domluvená příští výprava přebije odhad podle levelů
    best = next((r for r in rows if r["name"] == party.get("next")), best)
    try:  # váhy statů pro hodnocení upgradů; bez nich se porovnává item level
        weights = json.loads((ROOT / "weights.json").read_text(encoding="utf-8"))
        for m in chars:
            m["weights"] = weights_for(m, weights)
    except Exception as exc:
        print(f"Váhy: {exc}", file=sys.stderr)
    static = Static(token, region)
    try:  # co vyrobit je bonus, stránka musí vzniknout i bez něj
        plan = crafting(chars, static)
    except Exception as exc:
        print(f"Profese: {exc}", file=sys.stderr)
        plan = None
    try:  # bossové a loot jsou bonus, stránka musí vzniknout i bez nich
        bosses = json.loads((ROOT / "bosses.json").read_text(encoding="utf-8"))["dungeons"]
        loot = boss_loot(rows, best, chars, static, bosses)
    except Exception as exc:
        print(f"Loot: {exc}", file=sys.stderr)
        loot = []
    static.save()
    try:
        catalog = json.loads((ROOT / "quests.json").read_text(encoding="utf-8"))["dungeons"]
        checks = ready_check(token, region, rows, best, chars, catalog)
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
    titles = evening_titles(chars, base, today, catalog_titles)
    titles.update(chronicle_titles(chronicle, base, today, {m["slug"] for m in ok},
                                   {m["slug"]: m["player"] for m in chars}))
    counters = party_counters(chars, base)

    OUT.mkdir(exist_ok=True)
    (OUT / "kronika.html").write_text(render_chronicle(party, chronicle, ok), encoding="utf-8")
    # denní snímek pro history/, ranní běh na GitHubu ho commitne (podklad pro kroniku)
    (OUT / "snapshot.json").write_text(json.dumps(
        {"date": today.isoformat(), "built": built,
         "titles": {s: t["key"] for s, t in titles.items()},
         "members": history_members(chars, base)},
        ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "index.html").write_text(render(party, members, rows, best, built, checks, base, titles, counters,
                                                    chronicle, today, plan, loot, chars), encoding="utf-8")
    public = [{k: v for k, v in m.items() if k not in ("quests_done", "yday", "weights")} for m in loaded]
    (OUT / "party.json").write_text(json.dumps({"built": built, "members": public, "ready_check": checks,
                                                "baseline": base,
                                                "titles": {s: t["key"] for s, t in titles.items()}}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Hotovo: {len(ok)}/{len(members)} postav, nejlepší dungeon: {best['name'] if best else '-'}, "
          f"ready check: {len(checks)} dungeonů")


if __name__ == "__main__":
    main()
