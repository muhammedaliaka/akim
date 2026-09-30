from akim.analysis.matching import (
    TitleProfile,
    clean_title,
    is_generic,
    match_score,
    normalize,
    search_queries,
)


def test_normalize_strips_noise_and_collapses_acronyms():
    assert normalize("[🍎] Schedule Zero") == "schedule zero"
    assert normalize("R.E.P.O.") == "repo"
    assert normalize("Getting Over it[Remastered]🔨") == "getting over it"
    assert normalize("Rocket League®") == "rocket league"
    # tamamen parantez içindeki isim boşa düşmemeli
    assert normalize("[UPDATE]") == "update"


def test_clean_title_removes_editions():
    assert clean_title("Grand Theft Auto V Enhanced") == "Grand Theft Auto V"
    assert clean_title("Dead Island 2 - Deluxe Edition") == "Dead Island 2"
    assert clean_title("EA SPORTS FC™ 27") == "EA SPORTS FC 27"


def test_search_queries_variants():
    assert search_queries("Schedule I") == ["Schedule I", "Schedule"]
    assert search_queries("R.E.P.O.") == ["R.E.P.O.", "REPO"]
    assert search_queries("1666: Amsterdam")[:2] == ["1666: Amsterdam", "1666"]


def test_generic_detection():
    assert is_generic(TitleProfile.from_title("PEAK").tokens)
    assert is_generic(TitleProfile.from_title("Halloween: The Game").tokens)
    assert not is_generic(TitleProfile.from_title("Schedule I").tokens)
    assert not is_generic(TitleProfile.from_title("Lethal Company").tokens)


def test_name_clone_matches():
    p = TitleProfile.from_title("Schedule I")
    assert match_score(p, "Schedule X")[0] >= 0.85
    assert match_score(p, "[🍎] Schedule Zero")[0] >= 0.72
    assert match_score(p, "Chicago Life 😈")[0] < 0.5


def test_joined_words_match():
    p = TitleProfile.from_title("WARDOGS")
    assert match_score(p, "War Dogs [BETA]")[0] >= 0.85


def test_inspired_by_description_matches_even_with_other_name():
    p = TitleProfile.from_title("PEAK")
    score, reason = match_score(
        p, "[⛰️] SUMMIT", "Welcome to SUMMIT, a co-op climbing game, inspired by the viral title PEAK ⛰️"
    )
    assert score >= 0.9
    assert "inspired by" in reason


def test_inspired_by_with_multiple_sources():
    p = TitleProfile.from_title("Lethal Company")
    score, _ = match_score(p, "Backrooms Company", "inspired by the backrooms from kane pixels and lethal company")
    assert score >= 0.9


def test_generic_title_plain_mention_is_weak():
    p = TitleProfile.from_title("PEAK")
    assert match_score(p, "🏔️The Climb", "Climb high mountains with friends to reach the peak.")[0] < 0.72
    # genel isimli oyun için isim benzerliği ancak birebir ise sayılır
    assert match_score(p, "Peak Climbing Simulator")[0] < 0.72
    assert match_score(p, "PEAK [UPDATE]")[0] >= 0.9


def test_derived_single_word_not_matched_by_plain_mention():
    p = TitleProfile.from_title("Schedule I")
    # "schedule" kelimesi sıradan bir İngilizce kelime; düz geçiş eşleşme sayılmamalı
    assert match_score(p, "Rodeo Simulator", "Updates on a weekly schedule!")[0] < 0.72
    assert match_score(p, "Chip Empire", "Heavily inspired by Schedule I")[0] >= 0.9


def test_different_words_with_similar_letters_do_not_match():
    p = TitleProfile.from_title("iRacing")
    assert match_score(p, "Racing")[0] < 0.72
    assert match_score(p, "Racing Simulator [UPDATE]")[0] < 0.72


def test_inspired_by_must_be_in_same_sentence():
    p = TitleProfile.from_title("Lethal Company")
    desc = "Collect scraps to meet your quota! Game inspired by REPO.\nTags: repo, seize, scary, horror, lethal company"
    score, _ = match_score(p, "SEIZE", desc)
    assert score < 0.9  # etiket listesi atıf değildir
    assert match_score(p, "Deadly Company", "Heavily inspired by Lethal Company, a great game!")[0] >= 0.9


def test_inspired_by_with_colon():
    p = TitleProfile.from_title("PEAK")
    assert match_score(p, "Summit", "A co-op climb. Inspired by: PEAK")[0] >= 0.9
