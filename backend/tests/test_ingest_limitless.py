"""limitless の HTML を解釈するパーサのテスト。

フィクスチャは 2026-09-07 に取得した実物を切り詰めたもの。
取得そのものはここでは行わない（ネットワークに触れない）。
"""

from pathlib import Path

import pytest

from pocket_api.ingest.limitless import Archetype, parse_archetypes, parse_decklist

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def decks_html() -> str:
    return (FIXTURES / "limitless_decks.html").read_text(encoding="utf-8")


@pytest.fixture
def decklist_html() -> str:
    return (FIXTURES / "limitless_decklist.html").read_text(encoding="utf-8")


def test_parse_archetypes_returns_rows_in_order(decks_html: str) -> None:
    """メタ表の行を順位順に取り出せる。"""
    archetypes = parse_archetypes(decks_html)
    assert len(archetypes) == 3
    assert [a.rank for a in archetypes] == [1, 2, 3]


def test_parse_archetypes_extracts_all_columns(decks_html: str) -> None:
    """名前・スラッグ・使用数・使用率・勝敗・勝率を取り出せる。"""
    top = parse_archetypes(decks_html)[0]
    assert top == Archetype(
        rank=1,
        name="Mega Lucario ex Lucario",
        slug="mega-lucario-ex-b3-lucario-a2",
        count=352,
        share=pytest.approx(0.08299929261966517),
        wins=1008,
        losses=864,
        ties=71,
        win_rate=pytest.approx(0.5187853834276891),
    )


def test_parse_archetypes_rejects_html_without_table() -> None:
    """表が見つからなければ例外にする。取得元の構造変更を検知するため。"""
    with pytest.raises(ValueError, match="メタ表"):
        parse_archetypes("<html><body>お探しのページはありません</body></html>")


def test_parse_decklist_returns_engine_format(decklist_html: str) -> None:
    """デッキリストをエンジンが読める形式で取り出せる。"""
    text = parse_decklist(decklist_html)
    assert "2 Riolu A2 91" in text
    assert "2 Mega Lucario ex B3 81" in text
    assert text.rstrip().endswith("Energy: Fighting")


def test_parse_decklist_result_is_accepted_by_engine(decklist_html: str) -> None:
    """取り出したデッキリストをエンジンがそのまま検証できる。"""
    import pocket_engine_py as engine

    engine.validate_deck(parse_decklist(decklist_html))


def test_parse_decklist_rejects_html_without_decklist() -> None:
    """デッキリストが見つからなければ例外にする。"""
    with pytest.raises(ValueError, match="デッキリスト"):
        parse_decklist("<html><body>準備中</body></html>")


@pytest.fixture
def matchups_html() -> str:
    return (FIXTURES / "limitless_matchups.html").read_text(encoding="utf-8")


def test_parse_matchups_extracts_opponent_rows(matchups_html: str) -> None:
    """相手アーキタイプごとの試合数・勝敗・勝率を取り出せる。"""
    from pocket_api.ingest.limitless import Matchup, parse_matchups

    matchups = parse_matchups(matchups_html)
    assert len(matchups) == 3
    assert matchups[0] == Matchup(
        opponent="Mega Lucario ex Lucario",
        opponent_slug="mega-lucario-ex-b3-lucario-a2",
        matches=174,
        wins=82,
        losses=82,
        ties=10,
        win_rate=pytest.approx(0.47126436781609193),
    )


def test_parse_matchups_keeps_wide_spread(matchups_html: str) -> None:
    """相性表は総合勝率より差が大きい。校正の基準にできること。"""
    from pocket_api.ingest.limitless import parse_matchups

    rates = [m.win_rate for m in parse_matchups(matchups_html)]
    assert min(rates) < 0.30
    assert max(rates) > 0.60


def test_parse_matchups_rejects_html_without_table() -> None:
    """表が見つからなければ例外にする。"""
    from pocket_api.ingest.limitless import parse_matchups

    with pytest.raises(ValueError, match="相性表"):
        parse_matchups("<html><body>準備中</body></html>")


def test_parse_matchups_skips_unknown_opponents() -> None:
    """相手が特定できない行は読み飛ばす。

    limitless の相性表には、アーキタイプに分類できなかった相手が
    リンクなしの Unknown 行として載る。
    """
    from pocket_api.ingest.limitless import parse_matchups

    page = (
        "<table>"
        '<tr data-name="" data-matches="22" data-winrate="0.4545">'
        "<td></td><td><i>Unknown</i></td><td>22</td>"
        '<td class="nowrap">10 - 11 - 1</td><td>45.45%</td></tr>'
        '<tr data-name="Suicune ex Baxcalibur" data-matches="40" data-winrate="0.6">'
        '<td><a href="/decks/suicune-ex-a4a-baxcalibur-b2a/matchups?game=POCKET">S</a></td>'
        "<td>40</td><td>24 - 16 - 0</td><td>60.00%</td></tr>"
        "</table>"
    )
    matchups = parse_matchups(page)
    assert len(matchups) == 1
    assert matchups[0].opponent == "Suicune ex Baxcalibur"


def test_parse_displayed_set_reads_the_set_of_the_deck_links(decks_html: str) -> None:
    """セットを指定せずに開いた一覧は、limitless がいま既定にしているセットを出す。

    そのセットはアーキタイプへのリンク（`set=B4a`）から分かる。
    """
    from pocket_api.ingest.limitless import parse_displayed_set

    assert parse_displayed_set(decks_html) == "B4a"


def test_parse_displayed_set_rejects_a_page_without_deck_links() -> None:
    from pocket_api.ingest.limitless import parse_displayed_set

    with pytest.raises(ValueError, match="セット"):
        parse_displayed_set("<html></html>")
