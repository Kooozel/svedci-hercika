"""Testy logiky titulů, výchozího stavu a karty postavy. Bez sítě a bez API klíčů:
python3 -m unittest discover -s tests"""
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import build  # noqa: E402

CATALOG = json.loads((Path(build.ROOT) / "titles.json").read_text(encoding="utf-8"))
TODAY = date(2026, 11, 12)


def member(slug, level=18, xp=1000, stats=None, cls="priest", gear=None, updated="2026-11-11"):
    m = {"ok": True, "slug": slug, "name": slug.title(), "level": level, "xp": xp, "class_slug": cls,
         "stats": stats or {}, "updated": updated, "professions": []}
    if gear is not None:
        m["gear"] = gear
    return m


def base(members, day="2026-11-11", titles=None):
    return {"at": day + "T04:20:00Z", "date": day, "titles": titles or {},
            "members": {m["slug"]: build.snapshot(m) for m in members}}


class Czech(unittest.TestCase):
    def test_plural(self):
        forms = ("{n} smrt", "{n} smrti", "{n} smrtí")
        self.assertEqual(build.pl(1, forms), "1 smrt")
        self.assertEqual(build.pl(3, forms), "3 smrti")
        self.assertEqual(build.pl(12, forms), "12 smrtí")
        self.assertEqual(build.pl(1500, forms), build.num(1500) + " smrtí")

    def test_milestones(self):
        self.assertEqual(build.milestones(77), 100)
        self.assertEqual(build.milestones(100), 250)
        self.assertEqual(build.last_milestone(260), 250)
        self.assertEqual(build.last_milestone(9), 0)

    def test_day_only(self):
        # 23:30 UTC je v Praze už další den; ven jde jen datum
        self.assertEqual(build.to_day("2026-11-10T23:30:00Z"), "2026-11-11")
        self.assertEqual(build.to_day(1794350000000), "2026-11-10")
        self.assertEqual(build.days_ago("2026-11-11", TODAY), "včera")
        self.assertEqual(build.days_ago("2026-11-09", TODAY), "před 3 dny")


class Titles(unittest.TestCase):
    def test_newcomer_gets_welcome_not_lifetime_totals(self):
        old = member("a", stats={"Total deaths": 5})
        now = [member("a", stats={"Total deaths": 7}), member("b", stats={"Total deaths": 300, "Food eaten": 500})]
        titles = build.evening_titles(now, base([old]), TODAY, CATALOG)
        self.assertTrue(titles["b"]["key"].startswith("newcomers:"))
        self.assertEqual(titles["a"]["key"], "Total deaths")

    def test_newcomer_does_not_inflate_counters(self):
        old = member("a", stats={"Total deaths": 5})
        now = [member("a", stats={"Total deaths": 7}), member("b", stats={"Total deaths": 300})]
        deaths = next(c for c in build.party_counters(now, base([old])) if c["label"] == "smrtí")
        self.assertEqual(deaths["total"], 307)
        self.assertEqual(deaths["delta"], 2)
        self.assertIsNone(deaths["crossed"])  # 300 přinesl nováček, ne včerejší večer

    def test_new_synthetic_key_is_not_a_huge_jump(self):
        old = member("a", stats={"Total deaths": 5})
        now = member("a", stats={"Total deaths": 5, "_explore": 40, "_explore:Durotar": 40})
        vals, _, _ = build.evening_values(now, base([old]))
        self.assertNotIn("_explore", vals)

    def test_explore_title_names_zone(self):
        old = member("a", stats={"_explore": 10, "_explore:Durotar": 4, "_explore:Mulgore": 6})
        now = member("a", stats={"_explore": 14, "_explore:Durotar": 4, "_explore:Mulgore": 10})
        titles = build.evening_titles([now], base([old]), TODAY, CATALOG)
        self.assertEqual(titles["a"]["key"], "_explore")
        self.assertIn("Mulgore", titles["a"]["detail"])

    def test_only_one_premiere_per_day(self):
        olds = [member(s, stats={"Total deaths": 1}) for s in "abc"]
        now = [member("a", stats={"Total deaths": 1, "Food eaten": 1}),
               member("b", stats={"Total deaths": 1, "Quests abandoned": 1}),
               member("c", stats={"Total deaths": 1, "Total waves": 1})]
        titles = build.evening_titles(now, base(olds), TODAY, CATALOG)
        firsts = [t for t in titles.values() if t["key"].startswith("_first:")]
        self.assertLessEqual(len(firsts), 1)

    def test_no_same_stat_two_days_in_row(self):
        old = member("a", stats={"Total deaths": 5})
        now = member("a", stats={"Total deaths": 8})
        titles = build.evening_titles([now], base([old], titles={"a": "Total deaths"}), TODAY, CATALOG)
        self.assertNotEqual(titles["a"]["key"], "Total deaths")


    def test_stat_title_only_for_leader(self):
        # Horaes nabral 22 460 XP a dostal level; Kozel s 8 975 XP nesmí být „Rychlík“
        olds = [member("horaes", level=17, xp=14864), member("kozel", level=18, xp=3550)]
        now = [member("horaes", level=19, xp=224), member("kozel", level=18, xp=12525)]
        titles = build.evening_titles(now, base(olds), TODAY, CATALOG)
        self.assertEqual(titles["horaes"]["key"], "_lvl")
        self.assertTrue(titles["kozel"]["key"].startswith("busy:"))


    def test_chronicle_title_only_day_after_run(self):
        entry = {"date": "2026-11-11", "titles": {"a": {"emoji": "🎈", "title": "Špatný zeppelin"},
                                                  "x": {"title": "Není v partě"}}}
        got = build.chronicle_titles([entry], base([member("a")]), TODAY, {"a"})
        self.assertEqual(list(got), ["a"])
        self.assertEqual(got["a"]["title"], "Špatný zeppelin")
        # o den později, nebo bez včerejšího stavu už neplatí
        self.assertEqual(build.chronicle_titles([entry], base([member("a")], day="2026-11-12"),
                                                date(2026, 11, 13), {"a"}), {})
        self.assertEqual(build.chronicle_titles([entry], None, TODAY, {"a"}), {})

    def test_counter_milestone_keeps_delta(self):
        old, now = member("a", stats={"Quests completed": 480}), member("a", stats={"Quests completed": 511})
        html = build.evening_box([now], base([old]), {"a": {"emoji": "🎉", "title": "T", "detail": ""}},
                                 build.party_counters([now], base([old])))
        self.assertIn("přes 500!", html)
        self.assertIn("+31 od včera", html)
        self.assertIn("další milník 1", html)


class Dungeons(unittest.TestCase):
    def test_done_dungeon_only_in_list(self):
        ds = [{"name": "RoL", "zone": "z", "min": 15, "max": 20, "done": "2026-11-12"},
              {"name": "WC", "zone": "z", "min": 15, "max": 25}]
        rows, best = build.plan_dungeons([member("a", level=20)], ds)
        self.assertEqual(best["name"], "WC")
        self.assertFalse(rows[0]["near"])
        self.assertIn("odehráno 12. 11.", build.dungeon_table(rows, best, [member("a", level=20)]))
        rows[0]["done"] = True
        self.assertIn("✓ odehráno<", build.dungeon_table(rows, best, [member("a", level=20)]))


class Baseline(unittest.TestCase):
    def test_uses_last_history_before_today(self):
        with tempfile.TemporaryDirectory() as tmp:
            for day in ("2026-11-10", "2026-11-11", "2026-11-12"):
                (Path(tmp) / f"{day}.json").write_text(json.dumps(
                    {"date": day, "built": day + "T04:20:00Z", "titles": {"a": day}, "members": {}}))
            saved, build.HISTORY = build.HISTORY, Path(tmp)
            try:
                b = build.load_baseline({}, "2026-11-12T20:00:00Z")
            finally:
                build.HISTORY = saved
        self.assertEqual(b["date"], "2026-11-11")
        self.assertEqual(b["titles"], {"a": "2026-11-11"})


class Sheet(unittest.TestCase):
    def test_uint_armor_and_sickness(self):
        c = build.char_stats({"strength": {"base": 22, "effective": 5}, "agility": {"base": 22, "effective": 5},
                              "stamina": {"base": 27, "effective": 8}, "intellect": {"base": 41, "effective": 24},
                              "spirit": {"base": 50, "effective": 28}, "armor": {"base": 4294967288, "effective": 33}})
        self.assertEqual(c["armor"], (-8, 33))
        self.assertTrue(build.sick({"cstats": c}))  # Kozel po oživení: všechno pod základem
        healthy = {k: (20, 30) for k in ("strength", "agility", "stamina", "intellect", "spirit")}
        self.assertFalse(build.sick({"cstats": healthy}))


    def test_bonus_since_yesterday(self):
        m = {"cstats": {"armor": (16, 799), "stamina": (40, 72), "dodge": 4.1}, "roles": [],
             "yday": {"attrs": {"armor": 780, "stamina": 75, "dodge": 4.12}, "label": "od včerejšího rána"}}
        html = build.stat_rows(m)
        self.assertIn("+19</small>", html)
        self.assertIn("−3</small>", html)
        self.assertIn('title="od včerejšího rána"', html)
        self.assertEqual(html.count("<small"), 2)  # dodge −0,02 se zaokrouhlí na nulu
        m["yday"] = None  # bez včerejška žádné plus ani mínus
        self.assertNotIn("<small", build.stat_rows(m))

    def test_sick_snapshot_gives_no_bonus(self):
        sick = {k: (40, 10) for k in ("strength", "agility", "stamina", "intellect", "spirit")}
        snap = build.snapshot({"level": 20, "cstats": sick})
        self.assertTrue(snap["sick"])
        base = {"date": "2026-11-12", "members": {"a": snap}}
        self.assertIsNone(build.yday({"slug": "a"}, base, date(2026, 11, 13)))
        snap["sick"] = False
        self.assertEqual(build.yday({"slug": "a"}, base, date(2026, 11, 13))["label"], "od včerejšího rána")

    def test_old_history_gets_attrs_from_deployed(self):
        members = {"a": {"level": 20}, "b": {"level": 20, "attrs": {"armor": 1}}}
        build.add_attrs(members, {"members": [{"slug": "a", "cstats": {"armor": [16, 500], "dodge": 4.0}},
                                              {"slug": "b", "cstats": {"armor": [16, 900]}}]})
        self.assertEqual(members["a"]["attrs"], {"armor": 500, "dodge": 4.0})
        self.assertFalse(members["a"]["sick"])
        self.assertEqual(members["b"]["attrs"], {"armor": 1})  # co snímek má, se nepřepisuje

    def test_news_stats_with_new_gear(self):
        m = {"slug": "a", "level": 20, "xp": 0, "gear": [{"slot": "CHEST", "name": "Nová vesta"}],
             "cstats": {"armor": (16, 520), "stamina": (40, 60)}}
        base = {"members": {"a": {"level": 20, "xp": 0, "gear": ["Stará vesta"], "profs": {},
                                  "achievements": None, "recipes": None,
                                  "attrs": {"armor": 500, "stamina": 60}, "sick": False}}}
        self.assertEqual(build.changes(m, base)["stats"], [("armor", 20)])
        m["gear"] = [{"slot": "CHEST", "name": "Stará vesta"}]  # bez nové výbavy se staty do novinek nepíšou
        self.assertEqual(build.changes(m, base)["stats"], [])

    def test_genitive_falls_back_to_first_name(self):
        self.assertEqual(build.first_gen({"name": "Zrza Jegod", "gen": "Zrzy"}), "Zrzy")
        self.assertEqual(build.first_gen({"name": "Nový Kluk"}), "Nový")

class FakeStatic:
    def __init__(self, recipes, items):
        self.recipes, self.items = recipes, items

    def recipe(self, rid):
        return self.recipes.get(rid)

    def item(self, iid):
        return self.items.get(iid)

    def tooltip(self, iid):
        return (getattr(self, "tooltips", None) or {}).get(iid)


class Crafting(unittest.TestCase):
    boots = {"name": "Gemmed Copper Boots", "inv": "FEET", "level": 15, "req": 10, "kind": "Armor", "sub": "Mail"}
    cuffs = {"name": "Linen Cuffs", "inv": "WRIST", "level": 12, "req": 5, "kind": "Armor", "sub": "Cloth"}
    leather = {"name": "Light Leather", "sub": "Leather"}
    thread = {"name": "Fine Thread", "sub": "Cloth"}

    def party(self):
        smith = member("j", cls="paladin", gear=[{"slot": "FEET", "level": 10, "inv": "FEET", "name": "Old Boots"},
                                                  {"slot": "WRIST", "level": 5, "inv": "WRIST", "name": "Old Cuffs"}])
        smith["professions"] = [{"name": "Blacksmithing", "primary": True, "skill": 60, "max": 75}]
        smith["recipes"] = [{"id": 1, "name": "Gemmed Copper Boots"}]
        skinner = member("z", cls="hunter", gear=[])
        skinner["professions"] = [{"name": "Skinning", "primary": True, "skill": 75, "max": 75}]
        skinner["recipes"] = []
        tailor = member("k", cls="priest", gear=[{"slot": "WRIST", "level": 5, "inv": "WRIST", "name": "Rag Cuffs", "enchanted": True}])
        tailor["professions"] = [{"name": "Tailoring", "primary": True, "skill": 60, "max": 150}]
        tailor["recipes"] = [{"id": 2, "name": "Linen Cuffs"}, {"id": 3, "name": "Enchant Chest - Minor Health"}]
        return [smith, skinner, tailor]

    def plan(self):
        static = FakeStatic({1: {"item": 10, "reagents": [[20, 2], [21, 1]]}, 2: {"item": 11, "reagents": []},
                             3: {"item": None, "reagents": []}},
                            {10: self.boots, 11: self.cuffs, 20: self.leather, 21: self.thread})
        return build.crafting(self.party(), static)

    def test_upgrade_with_supplier(self):
        up = next(u for u in self.plan()["ups"] if u["item"]["name"] == "Gemmed Copper Boots")
        self.assertEqual((up["crafter"], up["target"], up["upgrade"]), ("j", "j", 5))
        self.assertEqual(up["reagents"], [{"name": "Light Leather", "qty": 2, "from": ["z"]}])  # thread se neřeší

    def test_cloth_not_offered_to_paladin_with_worn_bracers(self):
        targets = {u["target"] for u in self.plan()["ups"] if u["item"]["name"] == "Linen Cuffs"}
        self.assertEqual(targets, {"k", "z"})  # paladin má zápěstí, hunter je má prázdné
        cuffs_k = next(u for u in self.plan()["ups"] if u["target"] == "k")
        self.assertEqual(cuffs_k["loses"], "Rag Cuffs")  # nové zápěstí přepíše enchantnuté

    def test_capped_and_enchants(self):
        plan = self.plan()
        self.assertEqual(plan["capped"], [("z", "Skinning", 75)])
        self.assertNotIn("CHEST", plan["enchants"])  # nikdo nemá hruď, není co enchantovat


class ReadyCheckLimits(unittest.TestCase):
    """Classové a rasové questy: kdo je nemůže vzít, má „na“ a nepočítá se do souhrnů."""

    REQ = {1: {}, 2: {"classes": [{"name": "Paladin"}]}, 3: {"classes": [{"name": "Warlock"}]},
           4: {"races": [{"name": "Orc"}, {"name": "Tauren"}]}}

    def run_check(self):
        party = [{"slug": "j", "class": "Paladin", "race": "Undead", "level": 25, "quests_active": [], "quests_done": []},
                 {"slug": "h", "class": "Mage", "race": "Orc", "level": 25, "quests_active": [], "quests_done": []}]
        catalog = {"D": {"quests": [{"id": i, "name": f"Q{i}", "sharable": True} for i in (1, 2, 3, 4)]}}
        row = {"name": "D", "min": 20, "max": 30, "near": True}
        orig = build.api_get
        build.api_get = lambda token, region, path, kind: {"requirements": self.REQ[int(path.rsplit("/", 1)[1])]}
        try:
            return build.ready_check("t", "eu", [row], row, party, catalog)[0]
        finally:
            build.api_get = orig

    def test_limits(self):
        check = self.run_check()
        by_id = {q["id"]: q for q in check["quests"]}
        self.assertNotIn(3, by_id)  # warlock v partě není, quest se neukáže
        self.assertEqual(by_id[2]["status"], {"j": "missing", "h": "na"})
        self.assertEqual(by_id[2]["limit"]["text"], "jen Paladin")
        self.assertEqual(by_id[4]["status"], {"j": "na", "h": "missing"})
        self.assertEqual(by_id[4]["limit"]["text"], "ne pro Undead")
        self.assertEqual(check["take"], 4)  # Q1 oba, Q2 jen paladin, Q4 jen orc
        self.assertEqual([q["id"] for q in check["quests"]][-2:], [2, 4])  # omezené až za společnými

    def test_render(self):
        check = self.run_check()
        members = [{"slug": "j", "name": "Johnie", "class_slug": "paladin"}, {"slug": "h", "name": "Horaes", "class_slug": "mage"}]
        html = build.ready_box(check, members, 0)
        self.assertIn('class="share share-cls"', html)
        self.assertIn('q-cls" style="--cls:#F48CBA"', html)
        self.assertIn('class="st st-na"', html)


def stat(t, v, bonus=False):
    return {"type": {"type": t, "name": t.title()}, "value": v, **({"is_equip_bonus": True} if bonus else {})}


class ItemStats(unittest.TestCase):
    """Staty z /equipment (zkrácené skutečné odpovědi Forever API, viz docs/staty-forever.md)."""

    def test_armor_and_suffix_stats(self):
        boots = {"item": {"id": 9776}, "name": "Bandit Boots of the Boar", "bonus_list": [12725],
                 "armor": {"value": 53}, "stats": [stat("STRENGTH", 3), stat("SPIRIT", 3, True)]}
        self.assertEqual(build.item_stats(boots), {"STRENGTH": 3, "SPIRIT": 3, "ARMOR": 53})

    def test_weapon_dps_and_shield_block(self):
        wand = {"item": {"id": 5242}, "stats": [stat("SPELL_POWER", 1, True)],
                "weapon": {"dps": {"value": 11.785713}, "attack_speed": {"value": 1400}}}
        self.assertEqual(build.item_stats(wand), {"SPELL_POWER": 1, "DPS": 11.8})
        shield = {"item": {"id": 3651}, "armor": {"value": 300}, "shield_block": {"value": 820}}
        self.assertEqual(build.item_stats(shield), {"ARMOR": 300, "BLOCK": 820})

    def test_caster_weapon_spell_power_filled_in(self):
        # API u Trogg Scepter vrací jen Spirit, ve hře má +33 healing a +11 damage
        scepter = {"item": {"id": 272996}, "stats": [stat("SPIRIT", 4, True)], "weapon": {"dps": {"value": 8.125}}}
        self.assertEqual(build.item_stats(scepter),
                         {"SPIRIT": 4, "SPELL_HEALING_DONE": 33, "SPELL_DAMAGE_DONE": 11, "DPS": 8.1})

    def test_empty_item(self):
        self.assertEqual(build.item_stats({"item": {"id": 1}}), {})

    def test_gear_entry_keeps_stats_and_bonus(self):
        raw = {"slot": {"type": "FEET"}, "name": "Bandit Boots of the Boar", "quality": {"type": "UNCOMMON"},
               "level": {"value": 20}, "inventory_type": {"type": "FEET"}, "item": {"id": 9776},
               "item_subclass": {"name": "Leather"}, "bonus_list": [12725], "armor": {"value": 53},
               "stats": [stat("STRENGTH", 3)]}
        g = build.gear_entry(raw)
        self.assertEqual((g["slot"], g["level"], g["values"], g["bonus"]), ("FEET", 20, {"STRENGTH": 3, "ARMOR": 53}, [12725]))
        self.assertNotIn("bonus", build.gear_entry({**raw, "bonus_list": None}))


TOOLTIPS = json.loads((Path(__file__).parent / "fixtures" / "wowhead_tooltips.json").read_text(encoding="utf-8"))


def tip(iid):
    return build.tooltip_values(TOOLTIPS[str(iid)]["tooltip"])


class Tooltip(unittest.TestCase):
    """Staty lootu ze skutečných Wowhead tooltipů pro Forever (tests/fixtures)."""

    def test_set_bonus_not_counted(self):
        self.assertEqual(tip(286978), ({"ARMOR": 41, "INTELLECT": 10, "SPELL_HEALING_DONE": 11, "SPELL_DAMAGE_DONE": 4}, False))

    def test_weapon_dps(self):
        self.assertEqual(tip(271203), ({"DPS": 17.5, "AGILITY": 9, "SPIRIT": 5}, False))

    def test_shield_block_and_regen(self):
        self.assertEqual(tip(6320)[0], {"ARMOR": 585, "BLOCK": 12, "STRENGTH": 5, "STAMINA": 3, "HEALTH_REGEN": 4})
        self.assertEqual(tip(13245)[0], {"ARMOR": 528, "BLOCK": 10, "DEFENSE_SKILL_RATING": 5})
        self.assertEqual(tip(271213)[0], {"ARMOR": 547, "BLOCK": 11, "STRENGTH": 5, "BLOCK_PCT": 1.0})

    def test_block_value_and_resistance(self):
        self.assertEqual(tip(273846)[0], {"ARMOR": 123, "STRENGTH": 8, "BLOCK_VALUE": 6, "SHADOW_RESISTANCE": 5})

    def test_spell_power_variants(self):
        self.assertEqual(tip(271202)[0], {"ARMOR": 37, "AGILITY": 4, "SPELL_POWER": 5})
        self.assertEqual(tip(271206)[0], {"ARMOR": 37, "SPIRIT": 9, "SHADOW_SPELL_DAMAGE": 7})
        self.assertEqual(tip(6461)[0], {"ARMOR": 32, "MANA_REGEN": 3, "HEALTH_REGEN": 10})

    def test_attack_power_without_situational(self):
        self.assertEqual(tip(6314)[0], {"ARMOR": 22, "STAMINA": 3, "ATTACK_POWER": 10})  # proti zvířatům se nepočítá

    def test_bonus_armor_adds_up(self):
        self.assertEqual(tip(3230)[0], {"ARMOR": 63, "STAMINA": 7, "ATTACK_POWER": 4})

    def test_random_enchantment(self):
        self.assertEqual(tip(9776), ({"ARMOR": 53}, True))

    def test_loot_item_gets_values_manual_wins(self):
        static = FakeStatic({}, {})
        static.tooltips = {271201: {"v": build.TOOLTIP_V, "values": {"ARMOR": 104, "STRENGTH": 3}}, 9776: {"values": {"ARMOR": 53}, "random": True}}
        it = build.loot_item({"id": 271201, "name": "Atrophic Girdle", "inv": "WAIST", "stats": "+3 Str",
                              "values": {"STRENGTH": 4}}, static)
        self.assertEqual((it["values"], it["stats"]), ({"ARMOR": 104, "STRENGTH": 4}, "+3 Str"))
        boots = build.loot_item({"id": 9776, "name": "Bandit Boots", "inv": "FEET"}, static)
        self.assertTrue(boots["random"])
        self.assertNotIn("values", build.loot_item({"name": "Bez id", "inv": "FEET"}, static))


WEIGHTS = json.loads((Path(build.ROOT) / "weights.json").read_text(encoding="utf-8"))


def who(cls, roles, spec=None):
    return {"class_slug": cls, "roles": roles, "spec": spec}


class Weights(unittest.TestCase):
    """Hrubé váhy statů z weights.json podle classy, role a specu."""

    def test_paladin_tank(self):
        w = build.weights_for(who("paladin", ["tank"], "Holy"), WEIGHTS)
        self.assertEqual((w["stats"]["STAMINA"], w["stats"]["ARMOR"], w["stats"]["INTELLECT"]), (1.0, 0.05, 0.4))
        self.assertNotIn("SPELL_HEALING_DONE", w["stats"])
        self.assertEqual(w["weapon"], {"MAIN_HAND": 3})

    def test_healer_ignores_spell_damage(self):
        w = build.weights_for(who("priest", ["heal"], "Holy"), WEIGHTS)
        self.assertEqual((w["stats"]["SPELL_HEALING_DONE"], w["stats"]["SPIRIT"]), (1.0, 0.4))
        self.assertNotIn("SPELL_DAMAGE_DONE", w["stats"])

    def test_mage_school_and_wand(self):
        w = build.weights_for(who("mage", ["dps"], "Frost"), WEIGHTS)
        self.assertEqual(w["stats"]["FROST_SPELL_DAMAGE"], 1.0)
        self.assertNotIn("SHADOW_SPELL_DAMAGE", w["stats"])
        self.assertNotIn("ATTACK_POWER", w["stats"])
        self.assertEqual(w["weapon"], {"RANGED": 2})

    def test_hunter_ranged_weapon_and_ap(self):
        w = build.weights_for(who("hunter", ["dps"], "Beast Mastery"), WEIGHTS)
        self.assertEqual((w["stats"]["AGILITY"], w["stats"]["ATTACK_POWER"], w["weapon"]["RANGED"]), (1.0, 0.5, 7))

    def test_spec_picks_profile(self):
        enh = build.weights_for(who("shaman", ["dps"], "Enhancement"), WEIGHTS)
        ele = build.weights_for(who("shaman", ["dps"], "Elemental"), WEIGHTS)
        self.assertEqual((enh["stats"]["STRENGTH"], "STRENGTH" in ele["stats"]), (1.0, False))
        self.assertEqual(ele["stats"]["SPELL_DAMAGE_DONE"], 1.0)

    def test_no_spec_is_dps_unknown_class_none(self):
        self.assertEqual(build.weights_for(who("rogue", []), WEIGHTS)["weapon"]["MAIN_HAND"], 10)
        self.assertIsNone(build.weights_for(who("monk", ["dps"]), WEIGHTS))

    def test_every_class_role_has_profile(self):
        roles = {"warrior": ["tank", "dps"], "paladin": ["tank", "heal", "dps"], "hunter": ["dps"], "rogue": ["dps"],
                 "priest": ["heal", "dps"], "shaman": ["heal", "dps"], "mage": ["dps"], "warlock": ["dps"],
                 "druid": ["tank", "heal", "dps"]}
        known = set(WEIGHTS["units"]) | {"STRENGTH", "AGILITY", "STAMINA", "INTELLECT", "SPIRIT", "SPELL_POWER",
                                         "SPELL_HEALING_DONE", "SPELL_DAMAGE_DONE", "DEFENSE_SKILL_RATING"}
        known |= {f"{s}_SPELL_DAMAGE" for s in ("SHADOW", "FIRE", "FROST", "ARCANE", "NATURE", "HOLY")}
        for cls, rs in roles.items():
            for r in rs:
                w = build.weights_for(who(cls, [r]), WEIGHTS)
                self.assertIsNotNone(w, f"{cls} {r}")
                self.assertTrue(set(w["stats"]) <= known, f"{cls} {r}: {set(w['stats']) - known}")


if __name__ == "__main__":
    unittest.main()


class BossLoot(unittest.TestCase):
    """Kdo kus z bosses.json unese, komu sedí stat a jestli je to upgrade."""

    def party(self):
        k = member("k", level=18, cls="priest", gear=[{"slot": "CHEST", "level": 15, "inv": "ROBE", "name": "Robe"}])
        h = member("h", level=19, cls="mage", gear=[{"slot": "CHEST", "level": 22, "inv": "ROBE", "name": "Better Robe"}])
        j = member("j", level=19, cls="paladin", gear=[])
        j["roles"] = ["tank"]
        z = member("z", level=20, cls="hunter")  # bez výbavy z Armory
        return [k, h, j, z]

    def loot(self):
        catalog = {"D": {"bosses": [{"name": "B", "loot": [
            {"name": "Skin", "inv": "CHEST", "sub": "Cloth", "level": 20, "req": 15},
            {"name": "Scepter", "inv": "WEAPON", "sub": "Mace", "level": 22, "req": 17, "stat": "Caster"},
            {"name": "Staff", "inv": "TWOHWEAPON", "sub": "Staff", "level": 24, "req": 19, "stat": "Caster"},
            {"name": "Leg", "inv": "TWOHWEAPON", "sub": "Staff", "level": 20, "req": 15, "stat": "Agility"},
            {"name": "Mirror", "inv": "SHIELD", "sub": "Shield", "level": 24, "req": 19, "stat": "Strength"},
            {"name": "Plate", "inv": "CHEST", "sub": "Plate", "level": 27, "req": 22}]}],
            "quests": [{"quest": "Q", "loot": [{"id": 7}]}]}}
        static = FakeStatic({}, {7: {"name": "Cuffs", "inv": "WRIST", "kind": "Armor", "sub": "Cloth", "level": 24, "req": 0}})
        row = {"name": "D", "min": 15, "max": 20, "near": True}
        return build.boss_loot([row], row, self.party(), static, catalog)[0]

    def fits(self, name):
        d = self.loot()
        entry = next(x for b in d["bosses"] + d["quests"] for x in b["items"] if x["item"]["name"] == name)
        return entry["fits"]

    def test_cloth_only_for_casters_and_upgrade(self):
        fits = self.fits("Skin")
        self.assertEqual(set(fits), {"k", "h"})  # paladin a hunter cloth nechtějí
        self.assertEqual(fits["k"]["gain"], 5)
        self.assertIsNone(fits["h"]["gain"])  # mág nosí lepší

    def test_weapon_types_and_stats(self):
        self.assertEqual(set(self.fits("Scepter")), {"k"})  # mág mace neunese
        self.assertEqual(set(self.fits("Staff")), {"k", "h"})
        self.assertEqual(self.fits("Staff")["k"]["lvl"], 1)  # req 19, Kozel 18
        self.assertEqual(set(self.fits("Leg")), {"z"})  # agility jen hunter
        self.assertEqual(self.fits("Leg")["z"]["gain"], "?")  # bez výbavy se neporovnává
        self.assertEqual(set(self.fits("Mirror")), {"j"})
        self.assertEqual(self.fits("Mirror")["j"]["gain"], "empty")
        self.assertEqual(self.fits("Plate"), {})  # plate až od 40

    def test_quest_item_from_static_and_render(self):
        self.assertEqual(set(self.fits("Cuffs")), {"k", "h"})
        html_ = build.loot_section([self.loot()], self.party())
        self.assertIn("nikomu, disenchant nebo prodat", html_)  # plate
        self.assertIn("wowhead.com/forever/item=7", html_)
        self.assertIn("🎲 víc zájemců", html_)  # staff je upgrade pro oba castery

    def test_real_file_parses(self):
        data = json.loads((Path(build.ROOT) / "bosses.json").read_text(encoding="utf-8"))["dungeons"]
        for d in data.values():
            for b in d["bosses"]:
                for it in b["loot"]:
                    self.assertIn(it["inv"], build.ITEM_SLOTS, it["name"])

    def test_spell_school_and_healing(self):
        party = self.party()
        party[2]["roles"] = ["heal"]  # paladin healer bere heal kusy
        frost = {"name": "F", "inv": "TWOHWEAPON", "sub": "Staff", "level": 24, "stat": "Frost"}
        shadow = {"name": "S", "inv": "CHEST", "sub": "Cloth", "level": 20, "stat": "Shadow"}
        heal = {"name": "H", "inv": "TWOHWEAPON", "sub": "Sword", "level": 27, "stat": "Healing"}
        def who(it):  # jen hlavní volba, druhá má vlastní test
            return {m["slug"] for m in party
                    if (f := build.loot_fit(build.loot_item(it, FakeStatic({}, {})), m)) and not f["second"]}
        self.assertEqual(who(frost), {"h"})  # frost staff hlavně mág
        self.assertEqual(who(shadow), {"k"})  # shadow damage hlavně priest
        self.assertEqual(who(heal), {"j"})  # 2H meč s healem: priest ho neunese, paladin ano
        party[2]["roles"] = ["tank"]
        self.assertEqual(who(heal), {"j"})  # paladin se léčí i jako tank
        dagger = {"name": "D", "inv": "WEAPON", "sub": "Dagger", "level": 20, "stat": "Stamina"}
        self.assertEqual(who(dagger), {"z"})  # dagger jen se Staminou caster nechce

    def test_second_choice(self):
        party = self.party()
        heal = {"name": "T", "inv": "FEET", "sub": "Cloth", "level": 24, "stat": "Healing"}
        frost = {"name": "F", "inv": "TWOHWEAPON", "sub": "Staff", "level": 24, "stat": "Frost"}
        fit = lambda it, i: build.loot_fit(build.loot_item(it, FakeStatic({}, {})), party[i])
        self.assertFalse(fit(heal, 0)["second"])  # priest: heal je hlavně jeho
        self.assertTrue(fit(heal, 1)["second"])  # mág: využije jen Int a Sta
        self.assertTrue(fit(frost, 0)["second"])  # priest frost damage nepotřebuje
        self.assertIsNone(fit(heal, 3))  # hunter cloth s healem vůbec

    def test_tank_keeps_shield(self):
        party = self.party()  # Johnie je tank
        bucket = {"name": "B", "inv": "HOLDABLE", "level": 22, "stat": "Healing"}
        self.assertIsNone(build.loot_fit(build.loot_item(bucket, FakeStatic({}, {})), party[2]))
        self.assertIsNotNone(build.loot_fit(build.loot_item(bucket, FakeStatic({}, {})), party[0]))
