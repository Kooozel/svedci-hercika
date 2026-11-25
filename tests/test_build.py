"""Testy logiky titulů, výchozího stavu a karty postavy. Bez sítě a bez API klíčů:
python3 -m unittest discover -s tests"""
import html as html_lib
import json
import re
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

    def test_no_same_stat_every_other_day(self):
        # Cilda: healthstony předevčírem, včera vzdané questy, dnes zase healthstony nesmí
        old = member("a", stats={"Healthstones used": 24, "Quests abandoned": 11})
        now = member("a", stats={"Healthstones used": 27, "Quests abandoned": 13})
        b = base([old], titles={"a": "Quests abandoned"})
        b["recent_titles"] = [{"a": "Quests abandoned"}, {"a": "Healthstones used"}]
        titles = build.evening_titles([now], b, TODAY, CATALOG)
        self.assertNotIn(titles["a"]["key"], ("Healthstones used", "Quests abandoned"))

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


class People(unittest.TestCase):
    """Tituly, počítadla a novinky za člověka, ne za postavu."""

    @staticmethod
    def char(slug, player, **kw):
        return {**member(slug, **kw), "player": player, "class": "Mage", "nick": player.title()}

    def test_one_title_per_person_from_all_characters(self):
        olds = [self.char("a", "a", stats={"Total deaths": 1}), self.char("a2", "a", stats={"Total deaths": 1}),
                self.char("b", "b", stats={"Total deaths": 1})]
        now = [self.char("a", "a", stats={"Total deaths": 4}), self.char("a2", "a", stats={"Total deaths": 6}),
               self.char("b", "b", stats={"Total deaths": 6})]
        titles = build.evening_titles(now, base(olds), TODAY, CATALOG)
        self.assertEqual(set(titles), {"a", "b"})
        self.assertEqual(titles["a"]["key"], "Total deaths")  # 3 + 5 přebije 5 u b
        self.assertEqual(titles["a"]["char"], "A2")  # víc přinesl alt

    def test_new_alt_is_not_a_newcomer_and_brings_no_lifetime_stats(self):
        olds = [self.char("a", "a", stats={"Total deaths": 1})]
        now = [self.char("a", "a", stats={"Total deaths": 1}), self.char("a2", "a", stats={"Total deaths": 900})]
        titles = build.evening_titles(now, base(olds), TODAY, CATALOG)
        self.assertFalse(titles["a"]["key"].startswith("newcomers:"))
        self.assertNotEqual(titles["a"]["key"], "Total deaths")
        deaths = next(c for c in build.party_counters(now, base(olds)) if c["label"] == "smrtí")
        self.assertEqual((deaths["total"], deaths["delta"], deaths["crossed"]), (901, 0, None))

    def test_alt_doing_something_main_did_is_no_premiere(self):
        olds = [self.char("a", "a", stats={"Food eaten": 3}), self.char("a2", "a", stats={"Total deaths": 1})]
        now = [self.char("a", "a", stats={"Food eaten": 3}), self.char("a2", "a", stats={"Total deaths": 1, "Food eaten": 1})]
        titles = build.evening_titles(now, base(olds), TODAY, CATALOG)
        self.assertFalse(titles["a"]["key"].startswith("_first:"))

    def test_chronicle_title_for_alt_goes_to_player(self):
        entry = {"date": "2026-11-11", "titles": {"a2": {"title": "Zeppelin"}}}
        got = build.chronicle_titles([entry], base([member("a")]), TODAY, {"a"}, {"a2": "a"})
        self.assertEqual(got["a"]["title"], "Zeppelin")

    def news(self, now, olds):
        build.Q = build.Quips({}, TODAY)
        return build.news_box(now, base(olds), TODAY)

    def test_news_has_one_row_per_person(self):
        olds = [self.char("a", "a", xp=100), self.char("a2", "a", xp=100), self.char("b", "b", xp=100)]
        now = [self.char("a", "a", xp=500), self.char("a2", "a", xp=300), self.char("b", "b", xp=100)]
        html = self.news(now, olds)
        self.assertEqual(html.count('class="who-n"'), 1)
        self.assertEqual(html.count('class="nw-line"'), 2)
        self.assertIn("B: nic nového", html)

    def test_news_announces_new_alt(self):
        olds = [self.char("a", "a")]
        now = [self.char("a", "a"), {**self.char("a2", "a", level=11), "name": "Kozel Bezrohý"}]
        self.assertIn("Nová postava: Kozel Bezrohý, level 11", self.news(now, olds))


class ManyAlts(unittest.TestCase):
    """Stránka musí zůstat přehledná i s pěti alty na hráče."""

    @staticmethod
    def char(slug, player, level=25, active=()):
        return {"ok": True, "slug": slug, "name": slug.title() + " Velký", "nick": player.title(), "player": player, "class": "Mage",
                "class_slug": "mage", "level": level, "quests_active": [{"id": i} for i in active], "quests_done": []}

    def check(self, party):
        catalog = {"D": {"quests": [{"id": 1, "name": "Q", "sharable": True}]}}
        row = {"name": "D", "min": 20, "max": 30, "near": True}
        orig = build.api_get
        build.api_get = lambda *a: {}
        try:
            return build.ready_check("t", "eu", [row], row, party, catalog)[0]
        finally:
            build.api_get = orig

    def test_give_list_offers_all_takers_and_page_keeps_column_ones(self):
        """„Můžeš nasdílet“ nese všechny, komu quest dá, i ty mimo level; stránka nechá jen postavy ze sloupců."""
        party = [self.char("a", "a", active=[1]), self.char("b", "b"), self.char("b2", "b"), self.char("c", "c", level=15)]
        html = build.ready_box(self.check(party), party, 0)
        self.assertIn('<li data-takers="b b2 c">', html)
        self.assertNotIn("potřebuje:", html)  # text doplní stránka podle sloupců

    def test_party_table_header_switch_only_for_player_with_more_chars(self):
        party = [self.char("a", "a", active=[1]), self.char("b", "b"), self.char("b2", "b")]
        head = build.ready_box(self.check(party), party, 0).split("<thead>")[1].split("</thead>")[0]
        self.assertEqual(head.count('class="col-sw"'), 2)  # obě postavy hráče b, hráč a jen jméno

    def test_chain_with_prequest_in_log_is_own_state(self):
        q = {"id": 1, "name": "Q", "prev": "P", "status": {"a": "pre"}, "start": "", "req": 0}
        self.assertIn("⛓ dokončit", build.quest_cell(q, "pre", ""))
        self.assertEqual(build.char_todo({"quests": [q]}, {"slug": "a"}), ([], [], [q]))
        self.assertEqual(build.todo_state([], [], [q]), "⛓ 1× dokončit")
        html = build.mine_card({"quests": [q], "min": 1, "max": 60}, {"slug": "a", "level": 20}, {}, ["a"])
        self.assertIn("⛓ Dokonči řetěz", html)
        self.assertNotIn("Musíš dojít", html)
        self.assertNotIn("Nemusíš nikam chodit", html)

    def test_party_table_has_one_visible_column_per_player(self):
        party = [self.char("a", "a", active=[1]), self.char("b", "b"), self.char("b2", "b", active=[1]), self.char("b3", "b")]
        html = build.ready_box(self.check(party), party, 0)
        head = html.split("<thead>")[1].split("</thead>")[0]
        self.assertEqual(head.count('data-player="b"'), 3)  # všechny postavy hráče, aby šlo přepnout
        self.assertEqual(head.count("data-def"), 2)  # viditelný sloupec jen jeden na hráče
        self.assertIn('<th>Quest</th>', head)

    def test_party_table_marks_rows_and_done_cells_for_mobile_fold(self):
        party = [self.char("a", "a", active=[1]), self.char("b", "b")]
        html = build.ready_box(self.check(party), party, 0)
        self.assertIn('<tr data-q="0"', html)  # pořadí, do kterého stránka řádky vrací
        self.assertIn('class="rc-fold"', html)
        self.assertIn("parta je na tenhle dungeon připravená", html)
        self.assertIn('class="st st-over st-done"', build.quest_cell({}, "done", ""))

    def test_sharing_names_player_once_for_many_characters(self):
        party = [self.char("a", "a"), self.char("b", "b", active=[1]), self.char("b2", "b", active=[1])]
        html = build.ready_box(self.check(party), party, 0)
        self.assertIn("může nasdílet B (2 postavy)", html)

    def test_idle_alt_stays_out_of_history_but_stays_in_baseline(self):
        main, alt = {**member("a"), "player": "a"}, {**member("a2"), "player": "a", "alt": True}
        old = {s: {"name": m["name"], **build.snapshot(m)} for s, m in (("a", main), ("a2", alt))}
        old = json.loads(json.dumps(old))
        self.assertEqual(set(build.history_members([main, alt], {"members": old})), {"a"})
        self.assertEqual(set(build.history_members([main, {**alt, "level": 19}], {"members": old})), {"a", "a2"})
        with tempfile.TemporaryDirectory() as tmp:
            for day, members in (("2026-11-10", {"a": {"attrs": {}}, "a2": {"attrs": {}, "level": 5}}), ("2026-11-11", {"a": {"attrs": {}}})):
                (Path(tmp) / f"{day}.json").write_text(json.dumps({"date": day, "built": day + "T04:20:00Z", "members": members}))
            saved, build.HISTORY = build.HISTORY, Path(tmp)
            try:
                got = build.load_baseline({}, "2026-11-12T20:00:00Z")
            finally:
                build.HISTORY = saved
        self.assertEqual(got["members"]["a2"]["level"], 5)

    def test_alt_chip_is_marked_and_does_not_make_loot_contested(self):
        by_slug = {"k": {**member("k"), "nick": "Kozel"}, "z": {**member("z"), "nick": "Zrza", "alt": True, "name": "Zrza Alt", "player": "zr"},
                   "z1": {**member("z1"), "nick": "Zrza", "name": "Zrza Hlavní", "player": "zr"}}
        chips = build.loot_who({"z": {"gain": "big", "lvl": 0}}, by_slug)
        self.assertIn("lt-altc", chips)
        self.assertIn("<b>Zrza Alt</b>", chips)
        build.Q = build.Quips({}, TODAY)
        line = build.loot_line({"item": {"name": "Meč"}, "fits": {"k": {"gain": "big", "lvl": 0}, "z": {"gain": "big", "lvl": 0}}}, by_slug)
        self.assertNotIn("lt-flag", line)

    def test_loot_chip_names_char_of_player_with_more_chars(self):
        """Hráč s víc postavami má na štítku celé jméno, sám jen přezdívku; level je „až na N“."""
        by_slug = {"k": {**member("k"), "nick": "Kozel", "name": "Kozel Mourningale"},
                   "c": {**member("c"), "nick": "Cilda", "name": "Cildac Velky", "player": "ci", "level": 24},
                   "c2": {**member("c2"), "nick": "Cilda", "name": "Cilda Malý", "player": "ci"}}
        chips = build.loot_who({"k": {"gain": "big", "lvl": 0}, "c": {"gain": "big", "lvl": 2}}, by_slug)
        self.assertIn("<b>Kozel</b>", chips)
        self.assertIn("<b>Cildac Velky</b> velký upgrade <small>až na 26</small>", chips)

    def test_next_box_sums_up_ready_check_per_player(self):
        """Výprava nahoře: za každého hráče stav jeho postavy, stav všech vhodných postav pro „Ty“ v data-todo."""
        party = [self.char("a", "a", active=[1]), self.char("b", "b"), self.char("b2", "b"), self.char("b3", "b", level=12)]
        check = {**self.check(party), "best": True}
        best = {"name": "D", "min": 20, "max": 30, "zone": "Z", "status": {"a": ("fit", 0), "b": ("fit", 0)}, "pick": {"b": "b2"}}
        html = build.next_box([best], best, [party[0], party[1]], [check], party)
        self.assertIn('id="vyprava"', html)
        self.assertIn('data-player="a" data-def="a" style="--cls:#3FC7EB"><b>A</b> ✓ připraveno', html)
        self.assertIn('data-player="b" data-def="b2" style="--cls:#3FC7EB"><b>B</b> ↗ 1× nasdílet', html)
        todo = json.loads(html_lib.unescape(re.search(r'data-todo="([^"]*)"', html).group(1)))
        self.assertEqual(todo, {"a": "✓ připraveno", "b": "↗ 1× nasdílet", "b2": "↗ 1× nasdílet"})  # b3 levelem nesedí
        self.assertIn('href="#ready"', html)

    def test_news_folds_other_characters_of_player(self):
        olds = [self.char("a", "a"), self.char("a2", "a"), self.char("a3", "a")]
        now = [{**self.char("a", "a"), "xp": 500}, {**self.char("a2", "a"), "xp": 900}, {**self.char("a3", "a"), "xp": 100}]
        for m in olds:
            m["xp"] = 100
        build.Q = build.Quips({}, TODAY)
        html = build.news_box(now, base(olds), TODAY)
        head, more = html.split('<details class="nw-more">')
        self.assertIn("A2 Velký", head)  # nejvíc nahrála, je vidět rovnou
        self.assertNotIn("A Velký", head)
        self.assertIn("<summary>+1 další postava</summary>", more)
        self.assertIn("A Velký", more)

    def test_counters_stay_folded_even_with_milestone(self):
        old, now = member("a", stats={"Quests completed": 480}), member("a", stats={"Quests completed": 511})
        build.Q = build.Quips({}, TODAY)
        html = build.evening_box([now], base([old]), {"a": {"emoji": "🎉", "title": "T", "detail": ""}},
                                 build.party_counters([now], base([old])))
        self.assertIn('<details class="counters-box">', html)
        self.assertIn("🎉 questů přes 500", html.split("</summary>")[0])

    def test_same_title_is_one_card_with_all_names(self):
        quiet = {"emoji": "🛠️", "title": "Tichý tahoun", "detail": "Odmakal svoje."}
        chars = [self.char("a", "a"), self.char("b", "b"), self.char("c", "c"), self.char("d", "d")]
        titles = {"a": quiet, "b": dict(quiet), "c": {**quiet, "char": "C Nemrtvý"},
                  "d": {"emoji": "🎉", "title": "Rostoucí legenda", "detail": "+2 levely."}}
        html = build.title_cards(chars, titles)
        self.assertEqual(html.count('<li class="title"'), 2)
        self.assertEqual(html.count("Tichý tahoun"), 1)
        who = re.sub(r"<[^>]+>", "", html.split('class="t-who">')[1].split("</span><b")[0])
        self.assertEqual(who, "A, B, C Nemrtvý")  # jméno postavy začíná přezdívkou: bez „C · “

    def test_title_names_alt_with_player_only_when_names_differ(self):
        m = self.char("a", "kozel")
        self.assertEqual(build.title_who(m, {"char": "Kozel Rohatý"}), "Kozel Rohatý")
        self.assertEqual(build.title_who(m, {"char": "Cildac Velky"}), "Kozel · Cildac Velky")
        self.assertEqual(build.title_who(m, {}), "Kozel")

    def test_profession_overview_on_mobile_has_row_per_player(self):
        chars = [{**self.char("a", "a"), "professions": [{"name": "Mining", "skill": 60, "max": 75, "primary": True},
                                                         {"name": "Cooking", "skill": 30, "max": 75, "primary": False}]},
                 {**self.char("b", "b"), "professions": [{"name": "Herbalism", "skill": 12, "max": 75, "primary": True}]}]
        build.Q = build.Quips({}, TODAY)
        rows = build.professions_box(chars).split('<ul class="prof-list">')[1].split("</ul>")[0].split("<li>")[1:]
        self.assertEqual(len(rows), 2)
        self.assertIn(">A</b>", rows[0])
        self.assertIn("Mining <b>60</b>", rows[0])
        self.assertIn('pg-sec', rows[0])  # vedlejší profese tlumeně
        self.assertNotIn("Herbalism", rows[0])
        self.assertIn("Herbalism <b>12</b>", rows[1])

    def test_profession_overview_has_one_column_per_player(self):
        chars = [{**self.char("a", "a"), "professions": [{"name": "Mining", "skill": 10, "max": 75, "primary": True}]},
                 {**self.char("a2", "a"), "professions": [{"name": "Mining", "skill": 60, "max": 75, "primary": True}]}]
        build.Q = build.Quips({}, TODAY)
        html = build.professions_box(chars)
        self.assertIn('--cols:1', html)
        self.assertIn("<b>60</b>", html)
        self.assertNotIn("<b>10</b>", html)


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

    def test_walk_level_easy_or_max(self):
        self.assertEqual(build.walk_level({"min": 26, "max": 32, "difficulty": [22, 24, 26, 29]}), 29)
        self.assertEqual(build.walk_level({"min": 28, "max": 33, "difficulty": [26, 29, 31, 34]}), 33)
        self.assertEqual(build.walk_level({"min": 28, "max": 38}), 38)

    def test_ahead_rows_drop_walks_and_far_dungeons(self):
        ds = [{"name": "RFC", "zone": "z", "min": 13, "max": 18, "done": True},
              {"name": "WC", "zone": "z", "min": 15, "max": 25, "difficulty": [15, 17, 19, 24], "done": True},
              {"name": "RFK", "zone": "z", "min": 30, "max": 40, "difficulty": [25, 28, 31, 34]},
              {"name": "SM", "zone": "z", "min": 28, "max": 38},
              {"name": "RFD", "zone": "z", "min": 40, "max": 50}]
        team = [member("a", level=29), member("alt", level=24)]
        rows, _ = build.plan_dungeons(team, ds)
        # RFC je procházka pro všechny, RFD začíná až na 40; řazení podle toho, co uteče dřív
        self.assertEqual([r["name"] for r in build.ahead_rows(rows, team)], ["WC", "RFK", "SM"])

    def test_ahead_table_hides_far_rows_on_mobile(self):
        ds = [{"name": f"D{i}", "zone": "z", "min": 20 + 2 * i, "max": 30 + 2 * i} for i in range(8)]
        team = [member("a", level=26)]
        rows, _ = build.plan_dungeons(team, ds)
        shown = build.ahead_rows(rows, team)
        best = shown[4]
        html = build.ahead_table(rows, best, team)
        # dva před příští výpravou a tři po ní zůstanou, zbytek až po „další dungeony“
        self.assertEqual(html.count('ah-far'), len(shown) - 6)
        self.assertIn("další dungeony (2)", html)
        self.assertIn('data-player="a"', html)

    def test_ahead_table_folds_played_rows(self):
        ds = [{"name": f"D{i}", "zone": "z", "min": 20 + 2 * i, "max": 30 + 2 * i, "done": i < 3} for i in range(8)]
        team = [member("a", level=26)]
        rows, _ = build.plan_dungeons(team, ds)
        shown = build.ahead_rows(rows, team)
        best = next(r for r in shown if not r.get("done"))
        html = build.ahead_table(rows, best, team)
        n = sum(1 for r in shown if r.get("done"))
        # odehrané jsou sbalené na desktopu i mobilu a nepočítají se do „dva před příští výpravou“
        self.assertEqual(html.count('ah-done ah-old'), n)
        self.assertEqual(html.count("ah-old-btn"), 1)
        self.assertIn(f"✓ odehrané ({n})", html)
        self.assertNotIn("ah-old ah-far", html)

    def test_order_chars_main_first_then_alts_by_level(self):
        chars = [Alts.char("k", 31), Alts.char("k2", 12, "k"), Alts.char("j", 30, "j"), Alts.char("k3", 24, "k"),
                 Alts.char("j2", 33, "j")]
        for c in chars[1:]:
            c["alt"] = c["slug"] not in ("j",)
        chars[0]["alt"] = False
        self.assertEqual([c["slug"] for c in build.order_chars(chars)], ["k", "k3", "k2", "j", "j2"])

    def test_me_gate_offers_players_with_highest_level_char(self):
        chars = [Alts.char("j", 30, "j"), Alts.char("j2", 12, "j"), Alts.char("k", 31)]
        for c in chars:
            c["name"] = c["slug"].upper() + " Velký"
        chars[1]["updated"] = "2026-11-20"
        html = build.me_gate(chars)
        self.assertEqual(html.count('data-who="'), 2)
        self.assertIn('data-who="j" data-pick="j"', html)  # nejvyšší level, i když alt hrál naposledy
        self.assertIn("jen koukám", html)

    def test_me_menu_groups_chars_by_player(self):
        chars = [Alts.char("j", 30, "j"), Alts.char("j2", 8, "j"), Alts.char("k", 31)]
        for c in chars:
            c["name"] = c["slug"].upper()
        html = build.me_menu(chars)
        self.assertEqual(html.count('class="mm-p"'), 2)
        self.assertLess(html.index('data-me="j2"'), html.index('data-me="k"'))
        self.assertIn('<div class="mm-p" data-player="j" hidden>', html)  # stránka ukáže jen moje postavy
        self.assertIn("změnit hráče", html)

    def test_ahead_cell(self):
        rfk = {"name": "RFK", "min": 30, "max": 40, "difficulty": [25, 28, 31, 34]}
        self.assertEqual(build.ahead_cell(rfk, member("a", level=24))[:2], ("low", "za 6"))
        self.assertEqual(build.ahead_cell(rfk, member("a", level=30))[:2], ("ok", "4"))
        self.assertEqual(build.ahead_cell(rfk, member("a", level=33))[:2], ("soon", "1"))
        self.assertEqual(build.ahead_cell(rfk, member("a", level=34))[:2], ("last", "0"))
        self.assertEqual(build.ahead_cell(rfk, member("a", level=35))[:2], ("late", "pozdě"))

    def test_done_dungeon_past_while_someone_fits(self):
        ds = [{"name": "RFC", "zone": "z", "min": 13, "max": 18, "done": "2026-11-07"}]
        rows, _ = build.plan_dungeons([member("a", level=25), member("alt", level=14)], ds)
        self.assertTrue(rows[0]["past"])  # alt se do něj ještě vejde
        rows, _ = build.plan_dungeons([member("a", level=25)], ds)
        self.assertFalse(rows[0]["past"])

    def test_past_dungeon_in_ready_check_after_upcoming(self):
        party = [{"slug": "a", "class": "Mage", "race": "Orc", "level": 25, "quests_active": [], "quests_done": [1]},
                 {"slug": "alt", "class": "Mage", "race": "Orc", "level": 14, "quests_active": [], "quests_done": []}]
        catalog = {"Old": {"quests": [{"id": 1, "name": "Q1", "sharable": True}]},
                   "New": {"quests": [{"id": 2, "name": "Q2", "sharable": True}]}}
        old = {"name": "Old", "min": 13, "max": 18, "near": False, "past": True, "done": "2026-11-07"}
        new = {"name": "New", "min": 20, "max": 30, "near": True}
        orig = build.api_get
        build.api_get = lambda *a: {}
        try:
            checks = build.ready_check("t", "eu", [old, new], new, party, catalog)
        finally:
            build.api_get = orig
        self.assertEqual([c["title"] for c in checks], ["New", "Old"])
        self.assertEqual(checks[1]["quests"][0]["status"], {"a": "done", "alt": "missing"})  # i když ho nikdo nemá
        html = build.ready_section(checks, [member("a", level=25), member("alt", level=14)])
        self.assertIn('data-past=1', html)
        self.assertIn('data-fit="alt"', html)  # do RFC se vejde jen alt
        self.assertNotIn("odehrál", build.ready_box(checks[1], [member("a"), member("alt")], 1))  # alt o tom nic neví


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
        self.assertEqual(b["recent_titles"], [{"a": "2026-11-11"}, {"a": "2026-11-10"}])


class Sheet(unittest.TestCase):
    def test_uint_armor_and_sickness(self):
        c = build.char_stats({"strength": {"base": 22, "effective": 5}, "agility": {"base": 22, "effective": 5},
                              "stamina": {"base": 27, "effective": 8}, "intellect": {"base": 41, "effective": 24},
                              "spirit": {"base": 50, "effective": 28}, "armor": {"base": 4294967288, "effective": 33}})
        self.assertEqual(c["armor"], (-8, 33))
        self.assertTrue(build.sick({"cstats": c}))  # Kozel po oživení: všechno pod základem
        healthy = {k: (20, 30) for k in ("strength", "agility", "stamina", "intellect", "spirit")}
        self.assertFalse(build.sick({"cstats": healthy}))


    def test_flat_stats_keep_times(self):
        data = {"categories": [{"statistics": [{"name": "Total deaths", "quantity": 23.0,
                                                "last_updated_timestamp": 1794827928000}],
                                "sub_categories": [{"statistics": [{"name": "Food eaten", "quantity": 11.0}]}]}]}
        self.assertEqual(build.flat_stats(data), ({"Total deaths": 23, "Food eaten": 11}, {"Total deaths": 1794827928}))
        self.assertEqual(build.snapshot({"level": 1, "stats_at": {"Total deaths": 1}})["stats_at"], {"Total deaths": 1})

    def test_card_stats_by_role(self):
        m = {"slug": "a", "level": 20, "class_slug": "paladin", "roles": ["tank"],
             "cstats": {"health": 1726, "armor": (16, 1124), "attack_power": 185,
                        **{k: (20, 30) for k in ("strength", "agility", "stamina", "intellect", "spirit")}}}
        self.assertEqual(build.stat_values(m), [("HP", 1726), ("Armor", 1124), ("Attack Power", 185)])
        self.assertIn("1\xa0726", build.stat_rows(m))
        lock = {"level": 11, "class_slug": "warlock", "roles": ["dps"],
                "cstats": {"health": 300, "spell_power": 0, "intellect": (20, 25)}}
        self.assertEqual(build.stat_values(lock), [("HP", 300), ("Intellect", 25)])  # nulový spell power pryč

    def test_sick_card_shows_sickness_only(self):
        sick = {k: (40, 10) for k in ("strength", "agility", "stamina", "intellect", "spirit")}
        html = build.stat_rows({"slug": "a", "level": 20, "roles": [], "cstats": sick})
        self.assertIn("Resurrection Sickness", html)
        self.assertNotIn("<li>", html)

    def test_old_history_gets_attrs_from_deployed(self):
        members = {"a": {"level": 20}, "b": {"level": 20, "attrs": {"armor": 1}}}
        build.add_attrs(members, {"members": [{"slug": "a", "cstats": {"armor": [16, 500], "dodge": 4.0}},
                                              {"slug": "b", "cstats": {"armor": [16, 900]}}]})
        self.assertEqual(members["a"]["attrs"], {"armor": 500, "dodge": 4.0})
        self.assertFalse(members["a"]["sick"])
        self.assertEqual(members["b"]["attrs"], {"armor": 1})  # co snímek má, se nepřepisuje

    def test_news_xp_numbers(self):
        ch = {"from": 20, "to": 20, "xp": 11600}
        self.assertEqual(build.level_share(ch), "50 %")  # level 20 chce 23 200
        self.assertEqual(build.level_share({**ch, "to": 21}), "")  # level je vidět zvlášť

    def test_prof_news_numbers(self):
        m = {"slug": "a", "professions": [{"name": "Tailoring", "skill": 80}, {"name": "Fishing", "skill": 1},
                                          {"name": "Mining", "skill": 40}]}
        ch = {"profs": [("Tailoring", 7), ("Fishing", None), ("Mining", 5)], "learned": ["A", "B", "C", "D"]}
        out = build.prof_news(m, ch)
        self.assertEqual(out[:2], ["🎒 Nové řemeslo: Fishing.", "🛠️ Tailoring +7 · Mining +5"])
        self.assertTrue(any("4 nové recepty" in x for x in out))

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

    def test_followup_done_counts_as_done(self):
        """Johnie má hotový A Moon-Kissed Blade, Seeking the Kor Gem mu tedy nechybí, i když ho API mezi splněnými nemá."""
        party = [{"slug": "j", "class": "Paladin", "race": "Undead", "level": 28, "quests_active": [], "quests_done": [95036]}]
        catalog = {"D": {"quests": [{"id": 1, "name": "Kor Gem", "sharable": True, "after": [95036, 95126]}]}}
        row = {"name": "D", "min": 20, "max": 30, "near": True}
        orig = build.api_get
        build.api_get = lambda token, region, path, kind: {}
        try:
            checks = build.ready_check("t", "eu", [row], row, party, catalog)
        finally:
            build.api_get = orig
        self.assertEqual(checks, [])  # všechno hotové, dungeon nemá co ukázat

    def test_finished_quest_stays_with_tick_on_next_dungeon(self):
        party = [{"slug": "j", "class": "Paladin", "race": "Undead", "level": 28, "quests_active": [], "quests_done": [95036]}]
        catalog = {"D": {"quests": [{"id": 1, "name": "Kor Gem", "sharable": True, "after": [95036]},
                                    {"id": 2, "name": "Jiný", "sharable": True}]}}
        row = {"name": "D", "min": 20, "max": 30, "near": True}
        orig = build.api_get
        build.api_get = lambda token, region, path, kind: {}
        try:
            check = build.ready_check("t", "eu", [row], row, party, catalog)[0]
        finally:
            build.api_get = orig
        self.assertEqual([(q["name"], q["status"]["j"]) for q in check["quests"]], [("Jiný", "missing"), ("Kor Gem", "done")])

    def test_difficulty_scale(self):
        """Stupnice z Wowheadu místo rozpětí; zvýrazněný stupeň podle nejslabšího v partě."""
        steps = [22, 24, 26, 29]
        on = lambda html: re.findall(r'class="dl dl-on"[^>]*>([^<]+)<', html)
        self.assertEqual(on(build.level_scale({"min": 24, "max": 29, "difficulty": steps}, [23, 25])), ["na krev"])
        self.assertEqual(on(build.level_scale({"min": 24, "max": 29, "difficulty": steps}, [26, 31])), ["akorát"])
        self.assertEqual(on(build.level_scale({"min": 24, "max": 29, "difficulty": steps}, [30])), ["procházka"])
        self.assertEqual(on(build.level_scale({"min": 24, "max": 29, "difficulty": steps}, [20, 30])), [])
        self.assertIn("level 24–29", build.level_scale({"min": 24, "max": 29}, [25]))

    def test_render(self):
        check = self.run_check()
        members = [{"slug": "j", "name": "Johnie", "class_slug": "paladin", "level": 25},
                   {"slug": "h", "name": "Horaes", "class_slug": "mage", "level": 25}]
        html = build.ready_box(check, members, 0)
        self.assertIn('class="mine" data-slug="j"', html)
        self.assertIn('data-player="h" data-slug="h" data-def', html)
        self.assertIn("✋ Musíš dojít", html)


class Alts(unittest.TestCase):
    """Víc postav na hráče: kdo komu může nasdílet a kdo patří do výchozí sestavy."""

    @staticmethod
    def char(slug, level, player=None, active=()):
        return {"slug": slug, "class": "Mage", "race": "Orc", "level": level, "player": player or slug,
                "quests_active": [{"id": i} for i in active], "quests_done": []}

    def check(self, party, low=24, high=32):
        catalog = {"D": {"quests": [{"id": 1, "name": "Q", "sharable": True}]}}
        row = {"name": "D", "min": low, "max": high, "near": True}
        orig = build.api_get
        build.api_get = lambda *a: {}
        try:
            return build.ready_check("t", "eu", [row], row, party, catalog)[0]
        finally:
            build.api_get = orig

    def test_roster(self):
        self.assertEqual(build.roster({"characters": ["a", "b"]}), [["a"], ["b"]])
        self.assertEqual(build.roster({"players": [{"chars": ["a", "a2"]}, {"chars": ["b"]}]}), [["a", "a2"], ["b"]])

    def test_nick_from_party_or_main_character(self):
        chars = [{"ok": True, "slug": "a", "name": "Johnie Rotking", "player": "a"},
                 {"ok": True, "slug": "a2", "name": "Cildac Velky", "player": "a", "alt": True},
                 {"ok": True, "slug": "b", "name": "Zrza Jegod", "player": "b"}]
        build.add_nicks({"players": [{"name": "Fanda", "chars": ["a", "a2"]}, {"chars": ["b"]}]}, chars)
        self.assertEqual([m["nick"] for m in chars], ["Fanda", "Fanda", "Zrza"])
        self.assertEqual(build.char_label(chars[1]), "Cildac Velky (Fanda)")
        self.assertEqual(build.char_label(chars[2]), "Zrza Jegod")

    def test_photo_shows_last_played_and_tie_goes_to_main(self):
        main, alt = {"updated": "2026-11-16"}, {"updated": "2026-11-17"}
        self.assertIs(build.shown_char([main, alt]), alt)
        alt["updated"] = "2026-11-16"
        self.assertIs(build.shown_char([main, alt]), main)

    def test_photo_slot_names_player_and_character(self):
        m = {**member("a", level=27), "name": "Cildac Velky", "nick": "Fanda", "class": "Warlock", "roles": ["dps"], "player": "a"}
        other = {**member("b", level=17), "name": "Johnie Rotking", "nick": "Fanda", "class": "Paladin", "roles": ["tank"], "player": "a"}
        html = build.pc_figure(m, [m, other], m)
        self.assertIn('<span class="pc-nick">Fanda<small class="pc-alts" title="Johnie Rotking">+1 postava</small></span>', html)
        self.assertIn('<span class="pc-name"><span>Cildac Velky</span><b>27</b></span>', html)
        self.assertNotIn("<details", html)  # další postavy jen jako poznámka, přepínají se v kartě
        self.assertIn(' hidden>', build.pc_figure(other, [m, other], m))
        self.assertNotIn("pc-alts", build.pc_figure(m, [m], m))

    def test_rows_of_same_named_characters_differ(self):
        chars = [{**member(s), "ok": True, "name": n, "nick": "Kozel", "class": "Mage", "roles": [], "player": "k"}
                 for s, n in (("a", "Kozel Mourningale"), ("b", "Kozel Bezrohý"))]
        html = build.pc_figure(chars[0], chars, chars[0])
        self.assertIn("<span>Kozel Mourningale</span>", html)
        tabs = build.char_tabs(chars[1], chars)
        self.assertIn('data-open="a" aria-pressed="false" style="--cls:#F0F0F0" title="Mage"><b>Kozel Mourningale</b> <i>18</i>', tabs)
        self.assertIn('data-open="b" aria-pressed="true"', tabs)
        self.assertIn("<b>Kozel Bezrohý</b>", tabs)

    def test_sheet_switches_between_players_characters(self):
        chars = [{**member(s, level=lv), "ok": True, "name": n, "nick": "Kozel", "class": "Mage", "roles": ["heal"], "player": "a",
                  "race": "Human", "spec": "", "url": "u", "updated_label": "Naposledy"}
                 for s, n, lv in (("a", "Kozel Mourningale", 25), ("b", "Kozel Bezrohý", 12))]
        chars[1]["alt"] = True
        html = build.squad(chars[:1], 1, TODAY, alts=chars[1:])
        self.assertEqual(html.count('class="sheet-alts"'), 2)  # obě karty mají přepínač
        self.assertIn('<nav class="sheet-alts" aria-label="Postavy hráče Kozel"><button type="button" data-open="a"', html)
        self.assertIn('<div class="sheet-dim" hidden></div>', html)
        self.assertNotIn("sheet-alts", build.sheet(chars[0], TODAY))  # jediná postava přepínač nemá
    def test_player_without_character_waits_on_free_slot(self):
        party = {"players": [{"name": "Kozel", "chars": ["a"]}, {"name": "Susak", "chars": []}]}
        self.assertEqual(build.roster(party), [["a"]])
        self.assertEqual(build.waiting_players(party), ["Susak"])
        html = build.squad([], 2, TODAY, waiting=["Susak"])
        self.assertIn('<span class="pc-name">Susak</span><span class="pc-sub">bez postavy</span>', html)
        self.assertIn('<span class="pc-name">volné místo</span>', html)

    def test_own_alt_never_shares(self):
        check = self.check([self.char("a", 25), self.char("a2", 25, "a", [1])])
        self.assertEqual(check["quests"][0]["status"]["a"], "missing")
        self.assertEqual(check["quests"][0]["holders"], {})

    def test_holder_who_does_not_fit_dungeon_cannot_share(self):
        check = self.check([self.char("a", 25), self.char("b", 26), self.char("b2", 12, "b", [1])])
        q = check["quests"][0]
        self.assertEqual(q["status"]["a"], "missing")
        self.assertEqual(q["holders"], {})
        self.assertEqual((check["take"], check["share"]), (2, 0))

    def test_other_player_in_lineup_shares(self):
        check = self.check([self.char("a", 25), self.char("b", 26, active=[1])])
        self.assertEqual(check["quests"][0]["status"]["a"], "share")
        self.assertEqual((check["take"], check["share"]), (0, 1))

    def test_lineup_takes_fitting_char_of_each_player(self):
        party = [self.char("a", 40), self.char("a2", 25, "a"), self.char("b", 25), self.char("b2", 26, "b"), self.char("c", 14)]
        self.assertEqual(build.default_lineup({"min": 24, "max": 32}, party), ["a2", "b2"])  # z pasujících vyšší level
        self.assertEqual(build.default_lineup({"min": 50, "max": 60}, party), ["a", "b", "c"])  # nikdo: hlavní postavy

    def test_alt_keeps_done_dungeon_on_offer_but_not_the_plan(self):
        ds = [{"name": "RFC", "zone": "z", "min": 13, "max": 18, "done": "2026-11-07"},
              {"name": "BFD", "zone": "z", "min": 24, "max": 32}]
        mains, chars = [member("a", level=25)], [member("a", level=25), member("a2", level=14)]
        rows, best = build.plan_dungeons(mains, ds, chars)
        self.assertTrue(rows[0]["past"])
        self.assertEqual(best["name"], "BFD")
        self.assertEqual(set(rows[1]["status"]), {"a"})

    def test_unavailable_alt_is_skipped_but_main_is_reported(self):
        def fail(*a):
            raise RuntimeError("500")
        saved = build.fetch_armory, build.time.sleep
        build.fetch_armory, build.time.sleep = fail, lambda s: None
        try:
            out = build.load_party({"region": "eu", "players": [{"chars": ["a", "a2"]}]}, None)
        finally:
            build.fetch_armory, build.time.sleep = saved
        self.assertEqual([m["slug"] for m in out], ["a"])
        self.assertFalse(out[0]["ok"])

    def test_render_has_cards_with_full_name_and_personal_tasks(self):
        party = [self.char("a", 25), self.char("a2", 25, "a"), self.char("b", 26, active=[1])]
        check = self.check(party)
        chars = [{**m, "ok": True, "name": m["slug"].title() + " Velký", "class_slug": "mage"} for m in party]
        chars[1]["alt"] = True
        html = build.ready_section([check], chars)
        # v ready checku jen čipy postav hráče, hráče vybírá úvodní volba a lišta
        self.assertIn('<div class="me-chips" data-player="a" hidden>', html)
        self.assertIn('data-me="a2"', html)
        self.assertIn('<span class="mc-name">A2 Velký</span><span class="mc-lvl">25</span>', html)
        self.assertIn('id="rc-mains">{"a": "a"', html)  # pamatuje se hráč, za něj jeho postava
        self.assertNotIn('data-who="', html)
        self.assertIn("může nasdílet B", html)
        self.assertNotIn("Kdo jde", html)


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

    def test_display_only_stat_is_skipped(self):
        # „+2 to All Resistances“ (Rugged Spaulders) je jen text, odolnosti API posílá zvlášť
        spaulders = {"stats": [{"display": {"display_string": "+2 to All Resistances"}},
                               {"type": {"type": "FIRE_RESISTANCE"}, "value": 2, "is_hidden": True},
                               {"type": {"type": "FROST_RESISTANCE"}, "value": 2, "is_hidden": True}]}
        self.assertEqual(build.item_stats(spaulders), {"FIRE_RESISTANCE": 2, "FROST_RESISTANCE": 2})

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


def armed(cls, roles, gear, level=25, spec=None):
    m = member(cls[0], level=level, cls=cls, gear=gear)
    m["roles"], m["spec"] = roles, spec
    m["weights"] = build.weights_for(m, WEIGHTS)
    return m


def worn(slot, values, inv=None, level=20, name=None):
    return {"slot": slot, "inv": inv or slot, "level": level, "name": name or slot.title(), "values": values}


class Scoring(unittest.TestCase):
    """Upgrade podle statů a vah; bez statů se porovnává item level."""

    def test_item_score_counts_weapon_dps_by_slot(self):
        w = build.weights_for(who("hunter", ["dps"]), WEIGHTS)
        self.assertEqual(build.item_score({"AGILITY": 4, "DPS": 11.0, "SPIRIT": 3}, "RANGED", w), 81.0)
        self.assertEqual(build.item_score({"AGILITY": 4, "DPS": 11.0}, "MAIN_HAND", w), 15.0)

    def test_big_small_and_not_worth_it(self):
        z = armed("hunter", ["dps"], [worn("LEGS", {"AGILITY": 4, "ARMOR": 80})])
        legs = lambda v: {"inv": "LEGS", "sub": "Leather", "level": 22, "values": v}  # noqa: E731
        self.assertEqual(build.upgrade(legs({"AGILITY": 9, "STAMINA": 4}), z), "big")
        self.assertEqual(build.upgrade(legs({"AGILITY": 6}), z), "small")
        self.assertIsNone(build.upgrade(legs({"AGILITY": 4, "SPIRIT": 7}), z))  # Spirit hunterovi nic

    def test_stats_beat_item_level(self):
        k = armed("priest", ["heal"], [worn("CHEST", {"INTELLECT": 10, "SPELL_HEALING_DONE": 11}, inv="ROBE", level=23)])
        bear = {"inv": "CHEST", "sub": "Cloth", "level": 30, "values": {"STRENGTH": 8, "STAMINA": 8}}
        self.assertIsNone(build.upgrade(bear, k))  # vyšší ilvl, ale staty pro warriora

    def test_ring_compares_with_weaker_one(self):
        z = armed("hunter", ["dps"], [worn("FINGER_1", {"AGILITY": 8}, inv="FINGER"), worn("FINGER_2", {"STAMINA": 2}, inv="FINGER")])
        self.assertEqual(build.upgrade({"inv": "FINGER", "level": 20, "values": {"AGILITY": 5}}, z), "big")

    def test_two_hand_against_main_and_off_hand(self):
        j = armed("paladin", ["dps"], [worn("MAIN_HAND", {"DPS": 10.0}, inv="WEAPON"),
                                       worn("OFF_HAND", {"STRENGTH": 10, "ARMOR": 300}, inv="SHIELD")])
        two = {"inv": "TWOHWEAPON", "sub": "Two-Handed Sword", "kind": "Weapon", "level": 22, "values": {"DPS": 11.0}}
        self.assertIsNone(build.upgrade(two, j))  # +1 DPS nevyváží ztrátu štítu se Str
        self.assertEqual(build.upgrade({**two, "values": {"DPS": 14.0}}, j), "big")

    def test_falls_back_to_item_level_without_stats(self):
        k = armed("priest", ["heal"], [{"slot": "CHEST", "inv": "ROBE", "level": 15, "name": "Robe"}])
        self.assertEqual(build.upgrade({"inv": "CHEST", "sub": "Cloth", "level": 20, "values": {"INTELLECT": 5}}, k), 5)
        self.assertEqual(build.upgrade({"inv": "CHEST", "sub": "Cloth", "level": 20}, k), 5)

    def test_loot_chips_say_big_small(self):
        by_slug = {"k": member("k", cls="priest")}
        self.assertIn("velký upgrade", build.loot_who({"k": {"gain": "big", "lvl": 0}}, by_slug))
        self.assertIn("malý upgrade", build.loot_who({"k": {"gain": "small", "lvl": 0}}, by_slug))
        self.assertIn("+3 ilvl", build.loot_who({"k": {"gain": 3, "lvl": 0}}, by_slug))  # v tooltipu
        self.assertIn("nic moc", build.loot_who({"k": {"gain": None, "lvl": 0}}, by_slug))

    def test_crafting_uses_tooltip_stats(self):
        smith = armed("paladin", ["tank"], [worn("FEET", {"ARMOR": 100, "STAMINA": 2}, level=10)])
        static = FakeStatic({1: {"item": 10, "reagents": []}}, {10: {"name": "Boots", "inv": "FEET", "level": 15,
                                                                  "req": 10, "kind": "Armor", "sub": "Mail"}})
        static.tooltips = {10: {"values": {"ARMOR": 180, "STAMINA": 6}}}
        smith["recipes"] = [{"id": 1, "name": "Boots", "prof": "Blacksmithing"}]
        smith["professions"] = [{"name": "Blacksmithing", "primary": True, "skill": 50, "max": 75}]
        up = build.crafting([smith], static)["ups"]
        self.assertEqual([(u["item"]["name"], u["upgrade"]) for u in up], [("Boots", "big")])

    def test_crafting_bop_only_for_crafter(self):
        tailor = armed("mage", ["dps"], [worn("CHEST", {"ARMOR": 10}, level=10)])
        friend = armed("priest", ["healer"], [worn("CHEST", {"ARMOR": 10}, level=10)])
        static = FakeStatic({1: {"item": 10, "reagents": []}}, {10: {"name": "Gown", "inv": "CHEST", "level": 20,
                                                                  "req": 15, "kind": "Armor", "sub": "Cloth"}})
        static.tooltips = {10: {"values": {"ARMOR": 37, "INTELLECT": 5}, "bop": True}}
        tailor["recipes"] = [{"id": 1, "name": "Gown", "prof": "Tailoring"}]
        up = build.crafting([tailor, friend], static)["ups"]
        self.assertEqual([u["target"] for u in up], [tailor["slug"]])


SCHOOLS = ("SHADOW", "FROST", "FIRE", "ARCANE", "NATURE", "HOLY")
CASTER_KEYS = {"INTELLECT", "SPIRIT", "SPELL_POWER", "SPELL_DAMAGE_DONE", "SPELL_HEALING_DONE", "MANA_REGEN"} | {
    f"{x}_SPELL_DAMAGE" for x in SCHOOLS}
PHYSICAL = {"STRENGTH", "AGILITY", "ATTACK_POWER"}
# ruční stat v bosses.json -> staty, z nichž aspoň jeden musí kus mít (DPS = zbraň bez statů)
STAT_NEEDS = {
    "Agility": {"AGILITY", "ATTACK_POWER", "DPS"}, "Strength": {"STRENGTH", "ATTACK_POWER", "DPS"},
    "Melee": PHYSICAL | {"DPS"}, "Stamina": {"STAMINA"}, "Spirit": {"SPIRIT"},
    "Defense": {"DEFENSE_SKILL_RATING", "BLOCK", "BLOCK_VALUE", "BLOCK_PCT"},
    "Caster": CASTER_KEYS, "Healing": {"SPELL_HEALING_DONE", "SPELL_POWER"},
    **{x.title(): {f"{x}_SPELL_DAMAGE"} for x in SCHOOLS},
}

# štítek je správně, ale ze statů to vidět není
LABEL_OK = {"Deep Fathom Ring": "spell penetrace (jen pro castery), staty jen Stamina",
            "Tortoise Armor": "+40 bonusového armoru, v datech splyne s armorem",
            "Heart of the Primeval": "spell power jen na použití, staty jen frost res",
            "Knucklebone Ring": "+2 weapon dmg (melee), staty jen Stamina"}


class LootLabels(unittest.TestCase):
    """Ruční stat v bosses.json nesmí odporovat statům kusu (z Wowhead tooltipu v cache nebo values)."""

    def test_stat_matches_values(self):
        tips = json.loads((Path(build.ROOT) / "cache" / "static.json").read_text(encoding="utf-8")).get("tooltips") or {}
        dungeons = json.loads((Path(build.ROOT) / "bosses.json").read_text(encoding="utf-8"))["dungeons"]
        bad = []
        for d in dungeons.values():
            groups = [b.get("loot") or [] for b in d.get("bosses") or []] + [q.get("loot") or [] for q in d.get("quests") or []]
            for it in (it for g in groups for it in g):
                values = {**((tips.get(str(it.get("id"))) or {}).get("values") or {}), **(it.get("values") or {})}
                need = STAT_NEEDS.get(it.get("stat"))
                if it.get("sub") == "Wand":  # wand má jen DPS
                    need = (need or set()) | {"DPS"}
                if need and values and not need & set(values) and it["name"] not in LABEL_OK:
                    bad.append(f'{it["name"]}: stat {it["stat"]}, staty {sorted(set(values) - {"ARMOR"})}')
        self.assertEqual(bad, [])


class Quips(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((Path(build.ROOT) / "hlasky.json").read_text(encoding="utf-8"))

    def test_every_list_has_three_variants(self):
        short = []

        def walk(node, path):
            if isinstance(node, dict):
                for k, v in node.items():
                    if not k.startswith("_"):
                        walk(v, f"{path}.{k}" if path else k)
            elif isinstance(node, list) and len(node) < 3:
                short.append(path)
        walk(self.data, "")
        self.assertEqual(short, [])

    def test_no_repeat_on_page_and_daily_change(self):
        q = build.Quips(self.data, date(2026, 11, 13))
        lines = [q.line("gap_tail", s) for s in ("a", "b", "c")]
        self.assertEqual(len(set(lines)), 3)  # 3 postavy, nikdo nemá stejnou
        again = build.Quips(self.data, date(2026, 11, 13))
        self.assertEqual(again.line("gap_tail", "a"), lines[0])  # stejný den, stejný build
        days = {build.Quips(self.data, date(2026, 11, d)).label("loot.fight") for d in range(1, 8)}
        self.assertGreater(len(days), 1)  # štítek se během týdne mění

    def test_format_and_fallback(self):
        q = build.Quips(self.data, date(2026, 11, 13))
        self.assertIn("Kozel", q.line("prof_low.Cooking", "a", who="Kozel"))
        self.assertEqual(build.Quips({}).label("loot.fight", "🎲 víc zájemců"), "🎲 víc zájemců")

    def test_bare_line(self):
        self.assertEqual(build.bare_line(["NECK", "FINGER_1", "FINGER_2", "TRINKET_2"]),
                         "Chodí s holým krkem, bez prstenů a jen s jednou cetkou.")
        self.assertEqual(build.bare_line([]), "")

    def test_prof_num(self):
        self.assertEqual(build.prof_num({"skill": 75, "max": 150}), "75/150")
        self.assertEqual(build.prof_num({"skill": 12, "max": 0}), "12")



class BossTips(unittest.TestCase):
    def test_tips_name_roles_not_players(self):
        """Taktiky a poznámky bossů píšou role a classy, ne jména: rada musí sedět, ať jde kdokoli."""
        root = Path(__file__).resolve().parent.parent
        party = json.loads((root / "party.json").read_text(encoding="utf-8"))
        names = {p["name"] for p in party["players"]} | {c.split("-")[0].title() for p in party["players"] for c in p["chars"]}
        rx = re.compile(r"\b(" + "|".join(sorted({n[:4] for n in names if len(n) > 3})) + r")\w*", re.I)
        for title, d in json.loads((root / "bosses.json").read_text(encoding="utf-8"))["dungeons"].items():
            texts = [d.get("note") or ""] + [b.get("tip") or "" for b in d.get("bosses", [])]
            for t in texts:
                self.assertIsNone(rx.search(t), f"{title}: {t}")

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
        self.assertIn(build.Q.label("loot.nobody"), html_)  # plate
        self.assertIn("wowhead.com/forever/item=7", html_)
        self.assertIn(build.Q.label("loot.fight"), html_)  # staff je upgrade pro oba castery

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


class OneChoice(unittest.TestCase):
    """Výprava a ready check ukazují za hráče stejnou postavu: tu, která se do dungeonu hodí."""
    ds = [{"name": "BFD", "zone": "z", "min": 24, "max": 32}]

    @staticmethod
    def char(slug, level, player, updated="2026-11-11"):
        return {**member(slug, level=level, updated=updated), "player": player, "name": slug.title() + " Velký",
                "class": "Mage", "class_slug": "mage", "roles": ["dps"], "nick": player.title()}

    def test_plan_takes_fitting_alt(self):
        main, alt = self.char("s", 20, "s"), self.char("s2", 25, "s")
        rows, best = build.plan_dungeons([main], self.ds, [main, alt])
        self.assertEqual(best["status"]["s"], ("fit", 0))
        self.assertEqual(best["pick"]["s"], "s2")
        self.assertEqual(best["fit"], 1)

    def test_plan_takes_closest_when_none_fits(self):
        main, alt = self.char("s", 20, "s"), self.char("s2", 23, "s")
        _, best = build.plan_dungeons([main], self.ds, [main, alt])
        self.assertEqual(best["status"]["s"], ("under", 1))
        self.assertEqual(best["pick"]["s"], "s2")

    def test_plan_prefers_last_played_of_fitting(self):
        main = self.char("k", 25, "k", "2026-11-10")
        alt = self.char("k2", 26, "k", "2026-11-12")
        _, best = build.plan_dungeons([main], self.ds, [main, alt])
        self.assertEqual(best["pick"]["k"], "k2")

    def test_ahead_takes_char_with_most_reserve(self):
        """Kam dál: z postav v rozsahu ta s největší rezervou, ne naposledy hraná."""
        r = {"name": "SFK", "zone": "z", "min": 22, "max": 28}
        low, high = self.char("z1", 22, "z", "2026-11-01"), self.char("z2", 29, "z", "2026-11-20")
        mid = self.char("z3", 26, "z", "2026-11-19")
        self.assertIs(build.reserve_char([low, mid, high], r), low)
        self.assertIs(build.reserve_char([self.char("u", 18, "z"), self.char("u2", 20, "z"), high], r)["level"], 20)
        self.assertEqual(build.reserve_char([high, self.char("h", 31, "z")], r)["level"], 29)
        html = build.ahead_table([r], r, [high], {c["slug"]: c for c in (low, mid, high)})
        self.assertIn('title="Z · Z1 Velký (Mage): ještě 6 lvl, pak procházka">6<', html)
        self.assertIn('data-min="22" data-cap="28"', html)

    def test_top_char_is_highest_level(self):
        a, b = self.char("a", 30, "k", "2026-11-01"), self.char("b", 17, "k", "2026-11-20")
        self.assertIs(build.top_char([b, a]), a)

    def test_next_box_names_picked_character(self):
        main, alt = self.char("s", 20, "s"), self.char("s2", 23, "s")
        rows, best = build.plan_dungeons([main], self.ds, [main, alt])
        html = build.next_box(rows, best, [main], [], [main, alt])
        # sloupec za hráče, buňka za jeho alta, který se do dungeonu vejde nejlíp
        self.assertIn('title="S · S2 Velký (Mage): dungeon od levelu 24"><span class="ah-long">za </span>1</span>', html)
        self.assertIn('class="ah ah-low ah-alt" style="--alt:#3FC7EB"', html)  # jde alt: tečka v barvě povolání

    def test_party_table_shows_closest_char_of_player_out_of_range(self):
        party = [Alts.char("a", 25), Alts.char("c", 20), Alts.char("c2", 23, "c")]
        check = Alts().check(party)
        chars = [{**m, "ok": True, "name": m["slug"].title() + " Velký", "class_slug": "mage"} for m in party]
        html = build.ready_box(check, chars, 0)
        self.assertIn('data-player="c" data-slug="c2" data-def', html)
        self.assertIn('data-player="c" data-slug="c" hidden', html)
        self.assertIn("<small><span class=\"wc-name\">C2 Velký · </span>23</small><small class=\"gap\">−1 lvl</small>", html)
