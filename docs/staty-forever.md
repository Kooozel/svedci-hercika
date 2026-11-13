# Hodnocení výbavy podle statů ve WoW Forever (podklad)

Stav k 13. 11. 2026. Podklad pro to, aby `upgrade()` v `build.py` neporovnával jen item level.

**Jak číst zdroje.** U každého tvrzení je zdroj a jeho druh:

- **[API]** skutečná odpověď Blizzard API, stažená 13. 11. 2026 (EU, `locale=en_GB`). Ukázky jsou zkrácené, bez tokenu.
- **[API-odvozeno]** výpočet nad čísly z API, tedy z nošených kusů a z `/statistics` postav party.
  Jde o vlastní ověření vzorce, ne o Blizzardí dokumentaci.
- **[Blizzard]** oficiální text Blizzardu: články na worldofwarcraft.blizzard.com nebo příspěvky Kaivaxe
  na fórech. Hlavně jde o vlákno *WoW Forever Beta Development Notes – Updated November 2*,
  <https://us.forums.blizzard.com/en/wow/t/wow-forever-beta-development-notes-%E2%80%93-updated-november-2/2360696>.
  Dál ho cituji jako „Dev notes“ s číslem příspěvku a datem: `/1` 24. 9., `/4` 1. 10., `/5` 8. 10., `/6` 16. 10.,
  `/7` 26. 10., `/9` 3. 11. Celé vlákno jde stáhnout jako JSON přes `…/t/2360696.json`.
- **[Wowhead]** neoficiální tooltip endpoint `https://nether.wowhead.com/forever/tooltip/item/{id}`. Vrací JSON
  s HTML tooltipem a funguje bez JavaScriptu. Stránky `www.wowhead.com/forever/...` obyčejné stažení nenačte
  (vrátí jen kostru), proto jsem je nepoužil.
- **[wiki]** komunitní wiki (warcraft.wiki.gg). **[guide]** komerční nebo fanouškovské guidy.
  Obojí je sekundární a beru to jen tam, kde nic lepšího není.
- **[odhad]** můj návrh. Není doložený.

---

## 1) Co vrací Blizzard API

### 1.1 `/data/wow/item/{id}`, namespace `static-forever-eu`

**Pokrytí je malé.** [API] Ze 143 ID lootu v `bosses.json` vrátilo `static-forever-eu` jen 3 (251485, 251486, 935).
Zbytek skončil na `404 Not Found`. Chybí i obyčejné Classic předměty jako Bandit Boots (9776), Lil Timmy's
Peashooter (13136) a Willow Cape (6542), a stejně tak nové Forever kusy, například Rotmender's Garb (286978)
a Witherbite Bracers (271202). Verzovaný namespace z odkazů v profilu (`static-1.60.1_70399-forever-eu`) dává
stejný výsledek. Předmět, který profil postavy ukáže jako nošený, tedy statické API vůbec nemusí znát.
Vyhledávání `/data/wow/search/item?namespace=static-forever-eu&name.en_GB=…` funguje, ale Bandit Boots
ani Rotmender's v něm nejsou. Najde třeba Feral Shoulder Pads 15313 nebo Libram of Rumination 11732.
Podle README je to známý stav („Forever bossy ani většinu jejich lootu v API zatím nemá“).

Oficiální dokumentace (<https://develop.battle.net/documentation/world-of-warcraft-classic/game-data-apis>)
se vykresluje JavaScriptem. Namespace `*-forever-*` jsem v ní ověřit nedokázal. Že funguje, je doložené jen
skutečnými odpověďmi.

**Struktura u předmětu, který API zná.** [API] Klíče nejvyšší úrovně jsou `id, name, quality, level,
required_level, media, item_class, item_subclass, inventory_type, purchase_price, sell_price, max_count,
is_equippable, is_stackable, preview_item, purchase_quantity, appearances`. V `preview_item` jsou podle typu
předmětu `binding, armor, weapon, shield_block, stats, spells, set, requirements, level, durability`.

```json
// /data/wow/item/250515 (Protector's Chain Belt), preview_item, zkráceno
"armor": {"value": 98, "display": {"display_string": "98 Armor"}},
"stats": [
  {"type": {"type": "STAMINA", "name": "Stamina"}, "value": 4,
   "display": {"display_string": "+4 Stamina", "color": {"r":255,"g":255,"b":255}}},
  {"type": {"type": "DEFENSE_SKILL_RATING", "name": "Defense Skill"}, "value": 2, "is_equip_bonus": true,
   "display": {"display_string": "+2 Defense", "color": {"r":0,"g":255,"b":0}}},
  {"type": {"type": "SPELL_POWER", "name": "Spell Power"}, "value": 5, "is_equip_bonus": true,
   "display": {"display_string": "+5 Spell Power"}}
]
```

```json
// /data/wow/item/2848 (Bronze Mace), preview_item.weapon
"weapon": {
  "damage": {"min_value": 20, "max_value": 38, "display_string": "20 - 38 Damage",
             "damage_class": {"type": "PHYSICAL"}},
  "attack_speed": {"value": 2600, "display_string": "Speed 2.60"},
  "dps": {"value": 11.153846, "display_string": "(11.2 damage per second)"}
}
// u wandu (5242 Cinder Wand): "damage_class": {"type": "FIRE"}, speed 1400, dps 11.785713
```

Formát statu: `type.type` je enum, `value` je celé číslo a `display.display_string` je text z tooltipu.
`is_equip_bonus: true` mají zelené „Equip:“ řádky. V Classic jsou to efekty, ale API je ve Forever převádí
na staty. [API] Typy, které se v datech party objevily:

| enum | význam (tooltip) | is_equip_bonus |
|---|---|---|
| `STRENGTH`, `AGILITY`, `STAMINA`, `INTELLECT` | primární atributy | ne |
| `SPIRIT` | Spirit | **ano** (11 z 11 výskytů, Wowhead ho přitom kreslí bíle) |
| `SPELL_POWER` | „Increases damage and healing done by magical spells and effects by up to N“ | ano |
| `SPELL_HEALING_DONE` + `SPELL_DAMAGE_DONE` | „Increases healing done by up to X and damage done by up to Y“ | ano |
| `DEFENSE_SKILL_RATING` | „Increased Defense +N“ (je to skill, ne rating; název enumu klame) | ano |
| `HEALTH_REGEN` | „Restores N health per 5 sec.“ | ano |
| `FIRE_RESISTANCE` apod. | odolnosti | ne |

**Spells.** [API] Pole `spells` (`{spell:{id,name}, description}`) se objevilo jen u efektů, které nejsou statem,
například `"Equip: Increases movement speed by 2% in Silverpine Forest and Hillsbrad Foothills."` (spell
„Forsaken Weapons“ u Gnarled Necromancer's Staff). Equip efekty jako attack power, hit, crit, dodge nebo spell
damage jedné školy jsem v datech party neměl, takže nevím, jestli z nich API dělá `stats` nebo `spells`.
**Neověřeno.** Wowhead je u suffixů ukazuje jako text, třeba „Equip: + 10 Attack Power.“ (of Power),
„Increases your chance to dodge an attack by 1%.“ (of Eluding) nebo „Increases damage done by Shadow spells
… by up to 7.“ (of Shadow Wrath), viz kap. 4.

**Velká díra: spell power u caster zbraní chybí.** [API + Wowhead + API-odvozeno]
- Trogg Scepter (272996): profil vrací jen `SPIRIT 4`. Wowhead tooltip má navíc „Equip: Increases healing
  done by up to 33 and damage done by up to 11“ (<https://nether.wowhead.com/forever/tooltip/item/272996>).
- Gnarled Necromancer's Staff (251534): profil vrací `SPIRIT 10` a spell o rychlosti pohybu. Wowhead má navíc
  „Increases damage and healing done … by up to 26“ (<https://nether.wowhead.com/forever/tooltip/item/251534>).
- Kontrola přes `/statistics`: Kozel (priest) má z výbavy v API spell damage 8+6 = 14 a healing 8+17 = 25.
  API postavy hlásí `spell_power 25` a `spell_healing 58`, rozdíl je přesně 11 a 33. Horaes (mage) má z výbavy
  damage 5 a healing 12, `/statistics` hlásí 31 a 38, rozdíl je přesně 26 a 26. Bonus zbraně tedy ve hře platí,
  jen ho API u předmětu nevypíše.
- Sedí to s Blizzardem: „Spellcaster weapons now grant spell damage and healing“ (Deep Dive recap,
  <https://worldofwarcraft.blizzard.com/news/24303313>). U wandů API `SPELL_POWER 1` vrací.

### 1.2 `/profile/wow/character/{slug}/equipment`, namespace `profile-forever-eu`

Slug je `jmeno-prijmeni` z `party.json`, například `kozel-mourningale`. Forever má příjmení místo realmu.
[API] Každý nošený kus má stejné bloky jako `preview_item` (`stats, armor, weapon, shield_block, spells, set,
requirements, level, durability`) a navíc `slot`, `quantity`, `context`, případně `enchantments` a `bonus_list`.
Hodnoty jsou skutečné hodnoty toho konkrétního kusu, **včetně náhodného suffixu**:

```json
// zrza-jegod, FEET, zkráceno
{"item": {"id": 9776}, "slot": {"type": "FEET"}, "bonus_list": [12725],
 "name": "Bandit Boots of the Boar", "item_subclass": {"name": "Leather"},
 "armor": {"value": 53},
 "stats": [{"type": {"type": "STRENGTH"}, "value": 3},
           {"type": {"type": "SPIRIT"}, "value": 3, "is_equip_bonus": true}],
 "requirements": {"level": {"value": 15}}, "level": {"value": 20}}
```

- Suffix je vidět jen ve třech věcech: v `name` je přípona („of the Boar“), v `bonus_list` je ID bonusu a jeho
  staty jsou už sečtené ve `stats`. Samostatné pole „random suffix“ API nemá. [API] U party jsem našel
  `12720` Owl, `12725` Boar, `12729` of Intellect a `12741` of Fire Protection.
- Kusy bez náhodného suffixu `bonus_list` nemají.
- U setového kusu je `set` s `items[].is_equipped` a `effects[].display_string`, `required_count`, `is_active`.
- Štít má navíc `shield_block.value`.
- `href` v odkazech ukazuje na `static-1.60.1_70399-forever-eu`, takže Forever je podle API verze 1.60.1.

### 1.3 `/profile/wow/character/{slug}/statistics`

[API] Vrací spočítané hodnoty postavy: `strength/agility/intellect/stamina/spirit {base, effective}`,
`attack_power`, `ranged_attack_power`, `spell_power`, `spell_healing`, `melee_crit`, `ranged_crit`,
`spell_crit`, `dodge`, `parry`, `block`, `armor`, `defense`, `mana_regen`, `mana_regen_combat`,
`main_hand_damage_min/max/speed` a `ranged_damage_min/max`. `build.py` už část z nich ukládá do `cstats`.
Hodí se ke kontrole vzorců (kap. 3.1). Ratingy (`*_hit`, `expertise`, `mastery`) jsou ve Forever nulové.

---

## 2) Čím se Forever liší od Classic 1.12 (co mění hodnotu statů)

| Změna | Zdroj |
|---|---|
| Hit a crit pro kouzla, melee a ranged jsou jeden stat („Spell, Melee, and Ranged Hit chance are now combined… The same is true for Critical Strike chance“) | [Blizzard] Deep Dive recap <https://worldofwarcraft.blizzard.com/news/24303313> |
| `/statistics` přesto hlásí tři různé crity: priest 3,44 / 4,52 / 4,53 %, paladin 4,08 / 0,36 / 5,20 %. „Sloučení“ se tedy asi týká bonusů na itemech a talentech, ne zobrazovaného čísla. **Neověřeno.** | [API] |
| Bonus k léčení má i třetinový bonus ke spell damage (předmět „healing X a damage X/3“) | [Blizzard] Deep Dive; [API] Rotmender's Garb 11/4, Trogg Scepter 33/11 |
| Caster zbraně dávají spell damage a healing | [Blizzard] Deep Dive; [API-odvozeno] kap. 1.1 |
| Itemy s weapon skill dávají méně skillu | [Blizzard] Deep Dive |
| Nové staty, například snížení šance na parry a dodge | [Blizzard] Deep Dive (bez čísel) |
| Wandy nedostávají bonus ze spell damage („Wands no longer gain any additional damage from the user’s spell damage“) | [Blizzard] Dev notes /1 (24. 9.) |
| Wand i Auto Shot vyžadují 0,5 s stát na místě | [Blizzard] Dev notes /6 |
| Nižší ranky kouzel mají menší prospěch ze spell damage a healing | [Blizzard] Dev notes /4 |
| Classic itemy mají ve Forever jiné staty: Lil Timmy's Peashooter má ve Forever +4 Agility, v Classic žádné staty (stejné poškození 20–37, 2,60) | [Wowhead] <https://nether.wowhead.com/forever/tooltip/item/13136> vs <https://nether.wowhead.com/classic/tooltip/item/13136> |
| Blizzard zvedl sílu všech class a zdraví vyšších nepřátel („raised damage on classes across the game“) | [Blizzard] Dev notes /9 (3. 11.) |
| Základní staty ras opraveny (Tauren warrior +5 Str oproti člověku) | [Blizzard] Dev notes /9 |
| Talenty, které převádějí staty: Shaman *Mental Dexterity* AP = 50/100/150 % Int; Paladin *Champion of the Light* spell damage 12–60 % Int; Druid *Natural Instinct* spell healing 12/25 % Int; Mage *Arcane Resilience* armor až 75 % Int, *Arcane Blast* +12 % Int jako spell damage na stack, *Arcane Wrath* SP = 5 % max many; Priest *Mental Strength* +2–10 % Int, *Dark Inspiration* +3–15 % Spirit party; Warlock Life Tap škáluje se Spiritem | [Blizzard] Dev notes /9, /5, /6, /1 |
| Human Spirit +10 % (byl 5 %) | [Blizzard] Dev notes /7 |
| Rage: oprava, kdy víc Stamina dávalo méně rage; crit dává o 100 % víc rage | [Blizzard] Dev notes /5, /9 |
| Warrior *Toughness*: nejdřív odstraněn (/4), pak „moved to Row 3“ (/9). Podle Maxroll už nepřidává armor. **Konečný efekt neověřen.** | [Blizzard] Dev notes /4, /9; [guide] <https://maxroll.gg/wow-forever/news/wow-forever-class-deep-dives-whats-changing-for-all-nine-classes> |
| Dvě specializace (dual spec) od 40 | [wiki] <https://warcraft.wiki.gg/wiki/Patch_1.60> |
| **Armor proficiency** (mail pro huntera a shamana, plate pro warriora a paladina od 40): primární zdroj jsem nenašel. Shodně to uvádějí jen guidy. V Classic to tak bylo. Paladin party na 21 nosí mail a ne plate, což tomu odpovídá, ale nic to nedokazuje. **Neověřeno.** | [guide] <https://maxroll.gg/wow-forever/news/wow-forever-beta-update-outlines-major-class-changes-before-launch> |
| **Relikvie** (Libram, Idol, Totem): existují. API zná třeba Libram of Rumination (11732). *Mystic Mushroom* (druid, +5 % Spirit) potvrzují dev notes („Mystic Mushroom now correctly increases Spirit“, /6). Počet nových relikvií se v guidech liší (30 = 12/8/10, jinde 12/12/8) a v API jsem je nenašel. | [API], [Blizzard], [guide] <https://wowhandbook.com/gear/relics/> |

---

## 3) Váhy statů pro levelování

### 3.1 Doložené konstanty

Ověřoval jsem je přepočtem z `/statistics` a nošené výbavy čtyř postav party (L20–23). Všechny sedí
na Classic vzorce:

| Konstanta | Ověření [API-odvozeno] |
|---|---|
| Warrior/Paladin melee AP = 3·lvl + 2·Str − 20 | Johnie (paladin L21, Str 71): 63+142−20 = **185** = API 185 |
| Hunter melee AP = 2·lvl + Str + Agi − 20 | Zrza (L23, Str 44, Agi 69): 46+44+69−20 = **139** = API 139 |
| Hunter ranged AP = 2·lvl + 2·Agi − 20 | 46+138−20 = **164** = API 164 |
| Priest/Mage melee AP = Str − 10 | Kozel 23−10 = **13**, Horaes 26−10 = **16**, obojí sedí na API |
| Poškození zbraně = zbraň + AP/14 · rychlost (14 AP = 1 DPS) | Johnie: Bronze Mace 20–38 / 2,6 + 185/14·2,6 = **54,36–72,36** = API. Priest a mage sedí na tisíciny. Hunter ranged sedí na ±1 (náboje). |
| Armor = armor z výbavy + 2·Agi | Kozel 157+46 = **203**, Horaes 167+48 = **215**, Johnie 1064+60 = **1124**, všechno = API. Hunter má navíc +24 (asi armor kit na hrudi, tunika má `enchantments`). |
| Priest/Mage mana regen (mimo boj, za 5 s) ≈ 2,5·(12,5 + Spi/4) | Kozel Spi 71 → 75,6 (API 75), Horaes Spi 73 → 76,9 (API 76). Vzorec je z [guide] (Icy Veins přes ChaosBoost). Paladin a hunter na `15 + Spi/5` **nesedí** (61 a 57,5 proti API 58 a 50). **Neověřeno.** |
| `mana_regen_combat` = 0 u všech | platí „pravidlo pěti sekund“ a Spirit při castění nepomáhá [API] |

Z Classic se tradují i tyto konstanty, z party ověřit nešly:
- Rogue a shaman mají 1 AP/Str a 2 AP/Agi, hunter 1 AP/Agi [wiki] <https://warcraft.wiki.gg/wiki/Attack_power>.
  Tabulka tam není označená verzí a stránka dál uvádí, že hunter měl před 2.0.1 2 RAP/Agi.
- 10 HP/Stamina (prvních 20 bodů po 1) a 15 many/Int. ChaosBoost to uvádí jako „unchanged from Classic“,
  wiki uvádí 14 HP až od Cata. Nepřímý zdroj.
- Crit z Agi na úrovni 60: rogue 29, warrior 20 a hunter 53 Agi na 1 %, ale wiki tabulka uvádí pro huntera 33.
  Zdroje se rozcházejí, viz <https://warcraft.wiki.gg/wiki/Critical> a výsledky v ChaosBoost.
  Pro levely 20–30 žádný zdroj čísla nemá a crit z Agi roste s levelem rychleji.
- Spell crit z Int: mage 59,5 Int/1 % na úrovni 60 [wiki] <https://warcraft.wiki.gg/wiki/Attributes_(pre-6.0)>.
  Z party: priest L20 Int 64 → 4,53 %, mage L21 Int 83 → 4,94 %. Odpovídá to zhruba 17 Int/1 %, pokud je
  základ kolem 0,2–0,8 %. **Jen odhad**, ze dvou bodů se základ a sklon oddělit nedají.

### 3.2 Odvozené poměry (doložené konstanty, jednoduchá aritmetika)

- **Melee zbraň:** +1 DPS zbraně = 14 AP. Pro warriora a paladina (2 AP/Str) je to 7 Str, pro rogua (1 AP/Str) 14 Str.
  Je to spodní mez, protože schopnosti s bonusem k poškození zbraně (Heroic Strike, Sinister Strike…)
  škálují s poškozením zbraně taky. Na levelu 20–30 je AP 100–200, takže rozdíl 3–5 DPS zbraně
  (≈ 20–35 Str) přebije skoro jakékoli staty na jednom kusu.
- **Hunter ranged:** +1 DPS = 14 RAP = 7 Agi. Peashooter (11,0 DPS + 4 Agi) proti zbrani s 13 DPS bez statů:
  2 DPS = 14 Agi > 4 Agi, takže zbraň s vyšším DPS vyhraje.
- **Wand:** ve Forever škáluje jen svým DPS (Blizzard). U wandu se porovnává jen `weapon.dps`, staty skoro nic.
- **Healer:** `SPELL_POWER` a `SPELL_HEALING_DONE` mají na léčení stejnou váhu. `SPELL_DAMAGE_DONE` je u předmětů
  „healing X / damage X/3“ jen třetina a léčení nezvedá.
- **Tank:** každý bod armoru snižuje fyzické poškození podle `armor/(armor + 400 + 85·lvl)` (level útočníka < 60)
  [wiki] <https://warcraft.wiki.gg/wiki/Armor>. Stránka je ale označená jako zastaralá a vzorec připisuje
  verzi 5.0.4. Na L25 (≈ 2525) se 1124 armoru Johnieho rovná ≈ 31 % redukce, +100 armoru ≈ +1,9 p. b.
  Druid v medvědí formě násobí armor z itemů (Classic), pro Forever **neověřeno**.

### 3.3 Navržené váhy [odhad]

Hlavní stat má váhu 1,0. Weapon DPS je „bodů za +1 DPS“, armor „za bod armoru“. Odhady se opírají o 3.1–3.2
a o pořadí statů z guidů [guide]. Pořadí pro max level podle Icy Veins přes ChaosBoost:
<https://www.chaosboost.com/articles/wow-forever-stats-explained>. Shaman podle Warcraft Tavern:
<https://www.warcrafttavern.com/forever/guides/shaman-leveling/>. Ani jeden guide nemá čísla pro levely 1–60.

| class × role | Str | Agi | Sta | Int | Spi | SP (`SPELL_POWER`) | dmg (`SPELL_DAMAGE_DONE`) | heal (`SPELL_HEALING_DONE`) | Def | armor | DPS zbraně |
|---|---|---|---|---|---|---|---|---|---|---|---|
| warrior dps | 1,0 | 0,6 | 0,4 | 0 | 0,1 | 0 | 0 | 0 | 0,2 | 0,01 | 7 (MH) |
| warrior tank | 0,6 | 0,6 | 1,0 | 0 | 0,1 | 0 | 0 | 0 | 1,0 | 0,05 | 3 |
| paladin tank | 0,6 | 0,4 | 1,0 | 0,4 | 0,1 | 0,6 | 0,6 | 0 | 1,0 | 0,05 | 3 |
| paladin dps | 1,0 | 0,4 | 0,4 | 0,4 | 0,1 | 0,4 | 0,4 | 0 | 0 | 0,01 | 7 |
| paladin / priest / shaman / druid heal | 0 | 0 | 0,3 | 0,7 | 0,5 | 1,0 | 0 | 1,0 | 0 | 0,005 | 0 |
| hunter | 0,1 | 1,0 | 0,4 | 0,3 | 0,1 | 0 | 0 | 0 | 0 | 0,01 | 7 (ranged), 1 (melee) |
| rogue | 0,6 | 1,0 | 0,3 | 0 | 0 | 0 | 0 | 0 | 0 | 0,01 | 10 (MH), 5 (OH) |
| shaman enh | 1,0 | 0,5 | 0,4 | 0,8 | 0,1 | 0,3 | 0,3 | 0 | 0 | 0,01 | 7 |
| mage / warlock / priest shadow / shaman ele / druid balance | 0 | 0 | 0,4 (warlock 0,5) | 0,6 | 0,3 (priest 0,5, warlock 0,4) | 1,0 | 1,0 | 0 | 0 | 0,005 | wand: 2 |
| druid feral (kočka) | 1,0 | 0,8 | 0,4 | 0,1 | 0,1 | 0 | 0 | 0 | 0 | 0,01 | 0 |
| druid tank (medvěd) | 0,5 | 0,6 | 1,0 | 0 | 0 | 0 | 0 | 0 | 0,8 | 0,1 | 0 |

Odůvodnění:
- Str 1,0 a Agi 0,6 u warriora: 1 Str = 2 AP. 1 Agi = 2 armor + crit, crit je na nízkém levelu levnější,
  ale dává rage (+100 % rage za crit, Blizzard).
- Int 0,8 u enh shamana: Mental Dexterity dává až 1,5 AP/Int (Blizzard) proti 2 AP/Str, plus mana.
  Guidy staví Int nad Str.
- Int 0,4 u paladin tanka a dps: Champion of the Light dává až 60 % Int jako spell damage. Consecration
  a Retribution Aura škálují se spell power (Blizzard). Protector's Chain Belt (Sta + Def + SP) ukazuje,
  že Forever dává tankovským kusům spell power.
- Spirit 0,4 u warlocka: Life Tap škáluje se Spiritem (Blizzard).
- Spirit u healerů je jen 0,5, protože v boji regen ze Spiritu nefunguje (`mana_regen_combat` = 0).
- `SPELL_DAMAGE_DONE` je 0 u healerů a `SPELL_HEALING_DONE` 0 u dps casterů. Škola kouzla (Shadow/Frost/…)
  platí jen pro odpovídající classu, `bosses.json` to už řeší přes `SCHOOL_FOR`.
- Resistance, `HEALTH_REGEN` a efekty bez čísla mají váhu ≈ 0. Hit a crit z itemů, až se objeví, odhaduji
  na 10 bodů hlavního statu za 1 %.

---

## 4) Náhodné suffixy

**Statický předmět.** [API]
- V `static-forever-eu` mají náhodné kusy (zkoušel jsem Feral Shoulder Pads 15313, Aboriginal Cape 14116 a Raider's
  Shoulderpads 10407) `preview_item` **bez `stats` a bez `spells`** a jméno bez přípony. Žádný příznak
  „random enchantment“ tam není.
- Pro srovnání `static-classic1x-eu` u Bandit Boots 9776 dává `preview_item.name` s namátkovou příponou
  „Bandit Boots of the Monkey“, ale taky bez statů.
- Většina dungeonového lootu ve Forever static API vůbec není (kap. 1.1).

**Wowhead.** [Wowhead]
- Tooltip `nether.wowhead.com/forever/tooltip/item/9776` obsahuje text `<Random enchantment>`. To je spolehlivý
  příznak náhodného suffixu.
- S parametrem `?bonus=<id>` vrátí konkrétní variantu. `9776?bonus=12725` dá „Bandit Boots of the Boar, +3 Strength
  +3 Spirit“, což přesně odpovídá kusu, který Zrza nosí. ID z `bonus_list` v API jsou tedy stejná jako u Wowheadu.
- Průchodem bonusů 12688–12761 na Bandit Boots (ilvl 20) vyšla tabulka suffixů. Pro ilvl 20:
  - po 3+3: Bear, Monkey, Eagle, Whale, Owl, Gorilla, Falcon, Boar, Wolf, Tiger;
  - po +5: of Agility, Intellect, Spirit, Stamina, Strength;
  - Power (+10 AP), Magic (+6 SP), Healing (healing 11 / damage 4), Defense (+3), Regeneration (4 hp/5),
    Concentration (2 mana/5), Eluding (+2 Agi, +1 % dodge);
  - Protection (+3 Sta, plus odolnost; u Wowheadu odolnost v tooltipu chybí, API u Feral Gloves of Fire Protection
    vrací `STAMINA 3 + FIRE_RESISTANCE 3`);
  - nové Forever (TBC-style) suffixy: Sorcerer, Elder, Channeler, Physician, Prophet, Hierophant, Bandit, Beast,
    Champion, Defender (Sta + Def + SP), Knight;
  - „Wrath“ (spell damage jedné školy +7).
- Stejný název má občas víc ID s různou silou (Owl 12691 = 2/2, 12720 = 3/3; of Intellect 12688 = +3,
  12729 = +5). Hodnoty závisí na item levelu: Owl 12720 dává na ilvl 16 2/2, na ilvl 20 3/3.

**Co ověřit nejde.** Wowhead použije jakýkoli bonus na jakýkoli předmět. Z tooltipu tedy nejde zjistit,
**které suffixy daný předmět opravdu může mít**. Stránka předmětu (záložka „Random Enchantments“) se bez
prohlížeče nenačte a API seznam možných suffixů nemá. Seznam možných suffixů pro konkrétní loot je **neověřený**.

**Jak to zobrazit (návrh).**
1. U nošeného kusu brát `stats` z profilu. Jsou přesné i se suffixem, jméno už příponu obsahuje.
2. U lootu s náhodným suffixem ukázat „náhodný suffix“ / „?“ a hodnotit jen podle armoru, DPS zbraně
   a item levelu. Případně pro classu spočítat skóre „nejlepšího rozumného suffixu“ jako rozsah:
   pro ilvl ~20 je to 3+3 dvou hlavních statů nebo +5 jednoho.
3. Suffixy neslibovat výčtem, protože konkrétní pool pro daný předmět neznáme.

---

## Co z toho plyne pro build.py

- **Do cache ukládat staty, ne jen ilvl.** Z `/equipment` u každého nošeného kusu `stats` (`type` → `value`),
  `armor`, `weapon.dps`, `weapon.attack_speed`, `shield_block`, `bonus_list` a texty `spells` (`fetch_api` dnes
  ukládá jen level, sub a inv). U statických předmětů totéž z `preview_item`, s příznakem `random`, když chybí
  `stats` i `spells` u UNCOMMON+ armoru nebo zbraně.
- **Caster zbraně a skryté equip efekty:** API u nich spell power nevrací. Buď je doplnit z Wowhead tooltipu
  (endpoint je neoficiální, uložit do cache a nestahovat opakovaně), nebo ručně do `bosses.json`. Kontrola:
  rozdíl `spell_power` a `spell_healing` z `/statistics` proti součtu z výbavy.
- **Loot z `bosses.json`:** API ho skoro celý nezná (3 ze 143). Staty se musí psát ručně, nebo brát z Wowhead
  tooltipu (`nether.wowhead.com/forever/tooltip/item/{id}`, bez JavaScriptu).
- **Skóre** = Σ váha(class, role, stat) · hodnota + váha_armor · armor + váha_DPS · DPS zbraně (podle tabulky 3.3).
  Upgrade = skóre(nový) − skóre(nošený) > 0, u prstenů a trinketů proti horšímu z dvojice. Váhy držet
  v jednom slovníku, ať jdou snadno ladit. Jsou to odhady.
- **U item levelu zůstat:** když předmět nemá v datech staty (náhodný suffix bez známé varianty, chybějící statický
  záznam, neznámý equip efekt), když jsou oba kusy bez statů, a jako tiebreaker. Hrubý filtr typu zbroje
  (`ARMOR_RANK`), `req` a `can_use` zůstává. Hranici mail/plate od 40 mít jako konstantu, protože primárně
  ověřená není.
- **Zbraně a wandy:** porovnávat hlavně `weapon.dps` (×7 bodů hlavního statu za 1 DPS u melee a hunter ranged),
  wand jen podle DPS.
