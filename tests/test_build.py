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


class FakeStatic:
    def __init__(self, recipes, items):
        self.recipes, self.items = recipes, items

    def recipe(self, rid):
        return self.recipes.get(rid)

    def item(self, iid):
        return self.items.get(iid)


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
