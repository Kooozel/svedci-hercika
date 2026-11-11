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


if __name__ == "__main__":
    unittest.main()
