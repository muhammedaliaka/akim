"""Oynanış / konsept benzerliği (API gerektirmez).

İsim tutmasa bile "aynı oyunu oynatan" Roblox oyunlarını bulmak için:

1. Kavram sözlüğü: eş anlamlı oynanış terimlerini kavramlarda toplar
   (ör. bomb / defuse / plant / 5v5 -> "taktik_fps"; impostor / crewmate / vote -> "sosyal_çıkarım").
   Steam etiketleri de bu kavramlara bağlanır ("Tactical" -> taktik_fps).
2. Kavram örtüşmesi: mağaza oyununun kavram profilinin Roblox oyununda ne kadarının bulunduğu.
   Ağırlıklar aday kümesine göre IDF ile hesaplanır: "Counter-Strike 2" aramasında her sonuç zaten
   bir nişancı oyunudur; ayırt edici olan "fps" değil "bomba kur/çöz"dür.
3. Terim benzerliği: iki açıklamanın TF-IDF kosinüs benzerliği (açıklama kopyalayan klonlar için).

Sonuç 0..1 arası bir "konsept benzerliği"dir; isim ve açıklama-atfı sinyallerine üçüncü sinyal olarak eklenir.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

from .matching import normalize

# ------------------------------------------------------------------ kavram sözlüğü
# Terimler kök haline getirilerek eşleşir; birden çok kelimeli ifadeler desteklenir.
CONCEPTS: dict[str, list[str]] = {
    "climbing": ["climb", "climbing", "climber", "mountain", "summit", "cliff", "ascend", "ascent", "altitude",
                 "hike", "hiking", "peak", "stamina", "rope", "scale the mountain"],
    "tactical_fps": ["bomb", "bombsite", "defuse", "defusal", "defusing", "plant the bomb", "plant a bomb", "5v5",
                     "counter terrorist", "terrorist", "buy weapons", "buy new weapons", "tactical", "hostage",
                     "spike", "attackers", "defenders", "bomb site", "in match money", "earn money to buy",
                     "buy phase", "buy menu", "defuse kit", "utility"],
    "fps": ["fps", "shooter", "first person shooter", "gun", "guns", "gunfight", "firefight", "weapon", "weapons",
            "sniper", "rifle", "headshot", "aim", "loadout"],
    "extraction_quota": ["quota", "scrap", "scraps", "salvage", "valuable", "valuables", "extract", "extraction",
                         "haul", "loot", "meet your quota", "company", "deadline"],
    "horror": ["horror", "scary", "monster", "monsters", "creepy", "spooky", "jumpscare", "entity", "entities",
               "haunted", "terrifying", "nightmare", "lurking", "shadows"],
    "ghost_hunting": ["ghost", "ghosts", "paranormal", "evidence", "spirit", "emf", "investigate", "investigation",
                      "supernatural", "exorcism", "haunting", "ghost hunt"],
    "social_deduction": ["impostor", "imposter", "crewmate", "crewmates", "sabotage", "vote", "voting", "suspect",
                         "traitor", "murder mystery", "deduction", "betrayal", "vent", "emergency meeting", "tasks"],
    "store_sim": ["store", "supermarket", "superstore", "megastore", "shop", "shelf", "shelves", "stock", "restock",
                  "cashier", "customer", "customers", "checkout", "set prices", "set your prices", "retail",
                  "shoplifter", "shoplifters", "konbini", "grocery"],
    "crime_business": ["dealer", "drug", "drugs", "cartel", "crime", "criminal", "gang", "hustle", "kingpin",
                       "underground", "launder", "empire", "distribute", "street"],
    "grow_sell": ["grow", "plant", "harvest", "crop", "crops", "seed", "seeds", "garden", "sell", "profit",
                  "machines", "manufacture"],
    "creature_collect": ["creature", "creatures", "tame", "taming", "capture", "catch", "pal", "pals", "breed",
                         "breeding", "companion", "collect monsters"],
    "survival_craft": ["survival", "survive", "craft", "crafting", "base building", "build a base", "gather",
                       "resources", "hunger", "open world survival"],
    "diving_ocean": ["dive", "diver", "diving", "underwater", "ocean", "sea", "reef", "deep sea", "shark"],
    "fishing": ["fish", "fishing", "rod", "lure", "bait", "angler", "catch fish"],
    "restaurant": ["restaurant", "sushi", "cook", "cooking", "chef", "kitchen", "serve", "recipe", "diner"],
    "coop_physics": ["physics", "ragdoll", "wobbly", "carry", "grab", "throw", "physics based"],
    "driving": ["drive", "driving", "car", "cars", "truck", "trucks", "road", "vehicle", "vehicles", "delivery",
                "cargo", "trailer"],
    "racing": ["race", "racing", "lap", "track", "drift", "racer"],
    "farming_sim": ["farm", "farming", "tractor", "livestock", "field", "fields", "barn"],
    "idle": ["idle", "afk", "clicker", "incremental", "tap"],
    "roguelike": ["roguelike", "roguelite", "permadeath", "procedural", "procedurally"],
    "deckbuilder": ["card", "cards", "deck", "deckbuilder", "deckbuilding"],
    "tower_defense": ["tower defense", "towers", "wave", "waves", "defend", "lane", "lanes"],
    "battle_royale": ["battle royale", "last man standing", "last one standing", "storm", "airdrop", "drop in"],
    "hero_shooter": ["hero", "heroes", "ability", "abilities", "ultimate ability", "hero shooter"],
    "party": ["party", "minigame", "minigames", "mini game", "mini games", "party game"],
    "management_tycoon": ["tycoon", "manage", "management", "business", "employee", "employees", "staff", "hire",
                          "expand", "upgrade your", "empire", "mogul"],
    "mech": ["mech", "mechs", "robot", "robots", "pilot"],
    "space": ["space", "spaceship", "planet", "planets", "galaxy", "astronaut", "alien", "aliens", "moon"],
    "zombie": ["zombie", "zombies", "undead", "infected", "outbreak"],
    "backrooms": ["backrooms", "liminal", "levels", "noclip"],
    "heist_stealth": ["heist", "rob", "robbery", "steal", "vault", "stealth", "bank", "sneak"],
    "soccer": ["soccer", "football", "goal", "goals", "striker", "ball", "kick"],
    "roleplay_city": ["roleplay", "rp", "city", "job", "jobs", "police", "cop", "civilian"],
    "obby_platformer": ["obby", "parkour", "platform", "platforms", "jump", "checkpoint", "checkpoints"],
    "rhythm": ["rhythm", "beat", "beats", "music", "song", "songs", "notes"],
    "fighting": ["fight", "fighting", "combo", "combos", "brawl", "punch", "martial", "fighter"],
    "sandbox_destruction": ["sandbox", "destroy", "destruction", "destructible", "explode", "explosion"],
    "automation": ["automation", "factory", "factories", "conveyor", "machine", "production", "assembly"],
    "cleaning": ["wash", "washing", "clean", "cleaning", "power wash", "dirt"],
    "cozy_life": ["cozy", "life sim", "village", "decorate", "decorating", "friendship", "relaxing"],
    "open_world_exploration": ["explore", "exploration", "biome", "biomes", "island", "wilderness", "open world"],
}

CONCEPT_LABELS = {
    "climbing": "tırmanış", "tactical_fps": "taktik FPS (bomba kur/çöz)", "fps": "FPS/nişancı",
    "extraction_quota": "ganimet/kota toplama", "horror": "korku", "ghost_hunting": "hayalet avı",
    "social_deduction": "sosyal çıkarım (hain kim)", "store_sim": "market/mağaza işletme",
    "crime_business": "suç ekonomisi", "grow_sell": "yetiştir-sat", "creature_collect": "yaratık toplama",
    "survival_craft": "hayatta kalma/üretim", "diving_ocean": "dalış/okyanus", "fishing": "balıkçılık",
    "restaurant": "restoran/yemek", "coop_physics": "fizik tabanlı co-op", "driving": "sürüş/taşımacılık",
    "racing": "yarış", "farming_sim": "çiftlik", "idle": "idle/tıklama", "roguelike": "roguelike",
    "deckbuilder": "kart/deste", "tower_defense": "kule savunma", "battle_royale": "battle royale",
    "hero_shooter": "kahraman nişancı", "party": "parti oyunu", "management_tycoon": "işletme/tycoon",
    "mech": "mech/robot", "space": "uzay", "zombie": "zombi", "backrooms": "backrooms",
    "heist_stealth": "soygun/gizlilik", "soccer": "futbol", "roleplay_city": "şehir/rol yapma",
    "obby_platformer": "obby/parkur", "rhythm": "ritim/müzik", "fighting": "dövüş",
    "sandbox_destruction": "sandbox/yıkım", "automation": "otomasyon/fabrika", "cleaning": "temizlik/yıkama",
    "cozy_life": "rahat yaşam", "open_world_exploration": "keşif",
}

# Geniş türler ayırt edici mekaniklerden daha az kanıt değerindedir:
# "FPS" veya "korku" binlerce Roblox oyununda var; "bomba kur/çöz" veya "kota topla" değil.
BROAD_CONCEPTS = {
    "fps": 0.55, "horror": 0.6, "survival_craft": 0.6, "management_tycoon": 0.65, "party": 0.5,
    "open_world_exploration": 0.5, "roleplay_city": 0.6, "coop_physics": 0.6, "space": 0.7, "fighting": 0.6,
    "sandbox_destruction": 0.6, "cozy_life": 0.6, "driving": 0.8, "racing": 0.8, "grow_sell": 0.8,
}

# Steam etiketi -> kavram
TAG_CONCEPTS: dict[str, list[str]] = {
    "Climbing": ["climbing"], "Tactical": ["tactical_fps"],
    "FPS": ["fps"], "Shooter": ["fps"], "Looter Shooter": ["fps"], "Extraction Shooter": ["fps", "extraction_quota"],
    "Horror": ["horror"], "Survival Horror": ["horror"], "Psychological Horror": ["horror"],
    "Social Deduction": ["social_deduction"], "Management": ["management_tycoon"], "Economy": ["management_tycoon"],
    "Capitalism": ["management_tycoon"], "Crime": ["crime_business"], "Creature Collector": ["creature_collect"],
    "Open World Survival Craft": ["survival_craft"], "Survival": ["survival_craft"], "Crafting": ["survival_craft"],
    "Base Building": ["survival_craft"], "Fishing": ["fishing"], "Underwater": ["diving_ocean"],
    "Cooking": ["restaurant"], "Physics": ["coop_physics"], "Driving": ["driving"], "Racing": ["racing"],
    "Farming Sim": ["farming_sim"], "Farming": ["farming_sim"], "Idler": ["idle"], "Clicker": ["idle"],
    "Incremental": ["idle"], "Roguelike": ["roguelike"], "Roguelite": ["roguelike"], "Action Roguelike": ["roguelike"],
    "Card Game": ["deckbuilder"], "Deckbuilding": ["deckbuilder"], "Tower Defense": ["tower_defense"],
    "Battle Royale": ["battle_royale"], "Hero Shooter": ["hero_shooter"], "Party Game": ["party"],
    "Party": ["party"], "Mechs": ["mech"], "Space": ["space"], "Zombies": ["zombie"], "Heist": ["heist_stealth"],
    "Stealth": ["heist_stealth"], "Soccer": ["soccer"], "Football (Soccer)": ["soccer"], "Rhythm": ["rhythm"],
    "Fighting": ["fighting"], "Sandbox": ["sandbox_destruction"], "Destruction": ["sandbox_destruction"],
    "Automation": ["automation"], "Life Sim": ["cozy_life"], "Cozy": ["cozy_life"], "Exploration": ["open_world_exploration"],
    "Supernatural": ["ghost_hunting"], "Investigation": ["ghost_hunting"], "Trading": ["store_sim"],
}

# Terim benzerliğinde anlam taşımayan kelimeler (İngilizce + Roblox açıklama kalıpları)
STOP = set(
    """a an the and or but if of to in on at for with from by as is are was were be been being it its this that
    these those you your yours we our us they their them he she his her i me my can will would should could may
    might must do does did done have has had not no yes so than then there here what which who whom whose when
    where why how all any each every more most other some such only own same too very just also into out up down
    over under again further once about against between through during before after above below off both few
    new like favorite favourite join group update updates updated game games play playing player players friend
    friends code codes badge badges discord server servers vip pass gamepass reward rewards free bug bugs report
    thank thanks please coming soon welcome enjoy fun best ultimate experience roblox get got make made one two
    time way lot lots use using now still even back around way well much many first make sure want every
    alone together team work""".split()
)

_WORD_RE = re.compile(r"[a-z0-9]+")


def stem(w: str) -> str:
    for suf in ("ings", "ing", "ers", "er", "ies", "ied", "es", "ed", "s"):
        if len(w) > len(suf) + 2 and w.endswith(suf):
            base = w[: -len(suf)]
            return base + "y" if suf in ("ies", "ied") else base
    return w


def stems(text: str) -> list[str]:
    return [stem(w) for w in _WORD_RE.findall(normalize(text, drop_brackets=False))]


def _compile_lexicon() -> dict[str, list[tuple[str, ...]]]:
    out: dict[str, list[tuple[str, ...]]] = {}
    for concept, terms in CONCEPTS.items():
        seqs = {tuple(stem(w) for w in _WORD_RE.findall(t.lower())) for t in terms}
        out[concept] = sorted(seqs, key=len, reverse=True)
    return out


_LEXICON = _compile_lexicon()


def concept_hits(tokens: list[str]) -> Counter:
    """Kök listesinde her kavramın kaç farklı teriminin geçtiğini sayar."""
    joined = " " + " ".join(tokens) + " "
    token_set = set(tokens)
    hits: Counter = Counter()
    for concept, seqs in _LEXICON.items():
        n = 0
        for seq in seqs:
            if len(seq) == 1:
                if seq[0] in token_set:
                    n += 1
            elif " " + " ".join(seq) + " " in joined:
                n += 1
        if n:
            hits[concept] = n
    return hits


@dataclass
class ConceptProfile:
    """Mağaza oyununun oynanış profili."""

    concepts: dict[str, float] = field(default_factory=dict)  # kavram -> ağırlık (0..1)
    terms: Counter = field(default_factory=Counter)  # ayırt edici kökler

    @property
    def empty(self) -> bool:
        return not self.concepts and not self.terms

    @classmethod
    def from_store(cls, title: str, description: str = "", tags: list[str] | None = None) -> ConceptProfile:
        """Açıklamadaki (özellikle ilk cümledeki) kavramlar ana oynanışı anlatır; etiketler ikincildir.

        Örn. PEAK: "PEAK is a co-op climbing game ..." -> tırmanış ana kavram; "Physics" etiketi yan kavram.
        """
        tag_w: dict[str, float] = {}
        for i, tag in enumerate((tags or [])[:12]):
            w = 0.6 if i < 5 else 0.45
            found = list(TAG_CONCEPTS.get(tag, [])) + list(concept_hits(stems(tag)))
            for c in found:
                tag_w[c] = max(tag_w.get(c, 0.0), w)

        first = re.split(r"(?<=[.!?])\s", description.strip(), maxsplit=1)[0] if description else ""
        first_hits = concept_hits(stems(f"{title} {first}"))
        toks = stems(f"{title} {description}")
        desc_hits = concept_hits(toks)

        concepts: dict[str, float] = {}
        for c in set(tag_w) | set(desc_hits):
            w = 0.0
            if c in desc_hits:
                w = min(1.0, 0.5 + 0.2 * desc_hits[c]) + (0.3 if c in first_hits else 0.0)
            if c in tag_w:
                w = w + 0.25 if w else tag_w[c]
            concepts[c] = w
        if concepts:
            top = max(concepts.values())
            concepts = {c: round(w / top, 3) for c, w in concepts.items()}
            # yalnızca en belirgin 5 kavram: yan özellikler benzerliği sulandırmasın
            concepts = dict(sorted(concepts.items(), key=lambda kv: -kv[1])[:5])
        # Terim vektörü yalnızca açıklamadan: isim benzerliği ayrı bir sinyal, burada tekrar sayılmamalı
        terms = Counter(t for t in stems(description) if t not in STOP and len(t) > 2 and not t.isdigit())
        return cls(concepts=concepts, terms=terms)


@dataclass
class CandidateText:
    name: str
    description: str = ""
    genre: str = ""

    def tokens(self, exclude: set[str] = frozenset()) -> list[str]:
        """Kavram tespiti için: isim + açıklama + tür.

        exclude: mağaza oyununun adındaki kökler. Aday isimde de geçiyorsa bu kanıt zaten isim
        sinyalinde sayıldı; oynanış sinyaline ikinci kez girmemeli ("Moss Diver" adlı obby dalış oyunu değildir).
        """
        name = [t for t in stems(self.name) if t not in exclude]
        return name + stems(f"{self.description} {self.genre}")

    def description_tokens(self) -> list[str]:
        """Terim benzerliği için yalnızca açıklama (isim benzerliği ayrı sinyal)."""
        return stems(self.description)


def _idf(doc_sets: list[set[str]], key: str) -> float:
    n = len(doc_sets)
    df = sum(1 for d in doc_sets if key in d)
    return math.log((n + 1) / (df + 1)) + 1.0


def concept_similarity(
    profile: ConceptProfile, candidates: list[CandidateText], title: str = ""
) -> list[tuple[float, list[str]]]:
    """Her aday için (0..1 konsept benzerliği, ortak kavramlar) döner.

    IDF aday kümesi üzerinden hesaplandığından fonksiyon adayları topluca değerlendirir.
    """
    if profile.empty or not candidates:
        return [(0.0, []) for _ in candidates]

    exclude = set(stems(title))
    cand_tokens = [c.tokens(exclude) for c in candidates]
    cand_hits = [concept_hits(t) for t in cand_tokens]
    desc_tokens = [c.description_tokens() for c in candidates]
    term_sets = [set(t) for t in desc_tokens]

    # Kavram önemi: profil ağırlığının karesi (ana kavram baskın) x kavramın özgüllüğü.
    # Aday kümesine göre nadirlik (IDF) burada kullanılmaz: arama zaten ana kavrama göre seçtiği için
    # IDF tam da aranan kavramı (ör. Among Us için "impostor") cezalandırırdı.
    importance = {c: (w * w) * BROAD_CONCEPTS.get(c, 1.0) for c, w in profile.concepts.items()}
    denom = sum(importance.values()) or 1.0
    # Güven sınırı: tek bir geniş kavramdan ibaret zayıf profil ("açık dünya RPG") her oyunla
    # "tam örtüşür"; bu yüzden profilin toplam ayırt edicilik kütlesi düşükse skor sınırlanır.
    cap = min(1.0, 0.5 + 0.5 * denom)

    # Terim vektörü (TF-IDF)
    t_idf_cache: dict[str, float] = {}

    def t_idf(t: str) -> float:
        if t not in t_idf_cache:
            t_idf_cache[t] = _idf(term_sets, t)
        return t_idf_cache[t]

    p_vec = {t: (1 + math.log(n)) * t_idf(t) for t, n in profile.terms.items()}
    p_norm = math.sqrt(sum(v * v for v in p_vec.values())) or 1.0

    out = []
    for toks, hits in zip(desc_tokens, cand_hits):
        shared = []
        num = 0.0
        for c in profile.concepts:
            if c in hits:
                strength = min(1.0, 0.45 + 0.3 * hits[c])  # tek terim zayıf, birden çok terim güçlü kanıt
                num += importance[c] * strength
                shared.append(c)
        concept_cov = num / denom

        tf = Counter(t for t in toks if t not in STOP and len(t) > 2 and not t.isdigit())
        c_vec = {t: (1 + math.log(n)) * t_idf(t) for t, n in tf.items()}
        c_norm = math.sqrt(sum(v * v for v in c_vec.values())) or 1.0
        cos = sum(v * c_vec.get(t, 0.0) for t, v in p_vec.items()) / (p_norm * c_norm)

        # Kavram örtüşmesi ana sinyal; ortak özgül kelimeler ("supermarket", "defuse") onu inceltir.
        # Açıklamayı neredeyse aynen kopyalayan klonlar doğrudan kosinüsle yakalanır.
        term = min(1.0, cos * 3.0)
        score = max(min(cap, concept_cov) * (0.75 + 0.25 * term), min(1.0, cos * 1.6))
        out.append((round(min(1.0, score), 3), sorted(shared, key=lambda c: -importance[c])))
    return out
