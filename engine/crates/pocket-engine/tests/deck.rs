//! デッキテキストの解釈と検証のテスト。
//!
//! カード ID は deckgym-core の `example_decks/fire.txt` から取っている。

use deckgym::card_ids::CardId;
use deckgym::card_validation::get_implementation_status;
use deckgym::database::get_card_by_enum;
use pocket_engine::cards::Implementation;
use pocket_engine::deck::{DeckError, infer_energy_line, parse_deck, parse_deck_inferring_energy};
use strum::IntoEnumIterator;

/// 有効な 20 枚デッキ（炎）。
const FIRE_DECK: &str = "\
Energy: Fire
2 A1 042
2 A1 043
2 A1 044
2 A1 049
2 A1 050
2 A1 223
2 A1 225
2 P-A 001
2 P-A 005
2 P-A 007
";

#[test]
fn 有効なデッキを解釈できる() {
    let deck = parse_deck(FIRE_DECK).expect("炎デッキは有効なはず");
    assert_eq!(deck.cards.len(), 20);
}

#[test]
fn カード名は読み飛ばされる() {
    // 行の末尾 2 トークンがセット記号と番号であればよい
    let text = FIRE_DECK.replace("2 A1 042", "2 Charmander A1 042");
    let deck = parse_deck(&text).expect("カード名入りでも解釈できるはず");
    assert_eq!(deck.cards.len(), 20);
}

#[test]
fn 空行とヘッダ行を無視する() {
    let text = format!("Pokémon: 10\n\n{FIRE_DECK}\nTrainer: 6\n");
    let deck = parse_deck(&text).expect("ヘッダ行があっても解釈できるはず");
    assert_eq!(deck.cards.len(), 20);
}

#[test]
fn 不正なエネルギー種別はエラーになる() {
    // deckgym の Deck::from_string は不正なエネルギー種別で panic するため、
    // ラッパー側で先に弾く必要がある
    let text = FIRE_DECK.replace("Energy: Fire", "Energy: Flame");
    assert_eq!(
        parse_deck(&text),
        Err(DeckError::UnknownEnergyType {
            name: "Flame".to_string()
        })
    );
}

#[test]
fn エネルギー行が空ならエラーになる() {
    let text = FIRE_DECK.replace("Energy: Fire", "Energy:");
    assert!(matches!(parse_deck(&text), Err(DeckError::NoEnergyType)));
}

#[test]
fn エネルギーゾーンで生成できない種別はエラーになる() {
    let text = FIRE_DECK.replace("Energy: Fire", "Energy: Dragon");
    assert_eq!(
        parse_deck(&text),
        Err(DeckError::UnselectableEnergyType {
            name: "Dragon".to_string()
        })
    );
}

#[test]
fn 存在しないカード番号はエラーになる() {
    let text = FIRE_DECK.replace("2 A1 042", "2 A1 999");
    assert!(matches!(parse_deck(&text), Err(DeckError::Parse { .. })));
}

#[test]
fn 枚数が足りないデッキはエラーになる() {
    let text = FIRE_DECK.replace("2 A1 042\n", "");
    assert_eq!(parse_deck(&text), Err(DeckError::CardCount { actual: 18 }));
}

#[test]
fn 同名カードが三枚以上ならエラーになる() {
    let text = FIRE_DECK
        .replace("2 A1 042", "3 A1 042")
        .replace("2 A1 043", "1 A1 043");
    assert!(matches!(
        parse_deck(&text),
        Err(DeckError::TooManyCopies { .. })
    ));
}

/// エネルギー行のないデッキ。limitless の登録者が指定していない場合に出てくる。
/// 草（フシギダネ系）に加え、無色のポッポとコラッタを含む。
/// 無色はエネルギーゾーンで生成できないため、そのままでは deckgym が弾く。
const NO_ENERGY_DECK: &str = "\
2 A1 001
2 A1 002
2 A1 003
2 A1 186
2 A1 189
2 A1 223
2 A1 225
2 P-A 001
2 P-A 005
2 P-A 007
";

#[test]
fn エネルギー行がなければ推定できる() {
    // カードのタイプからエネルギーを推定する。無色と竜は生成できないので除く
    let text = infer_energy_line(NO_ENERGY_DECK).expect("推定できるはず");
    assert!(text.starts_with("Energy: "), "推定結果: {text}");
    assert!(!text.contains("Colorless"), "無色が混ざっている: {text}");
    assert!(!text.contains("Dragon"), "竜が混ざっている: {text}");
}

#[test]
fn 推定したエネルギー行を足すと解釈できる() {
    // 推定なしでは弾かれることを確かめてから、推定を足すと通ることを確かめる
    assert!(parse_deck(NO_ENERGY_DECK).is_err());

    let deck = parse_deck_inferring_energy(NO_ENERGY_DECK).expect("推定すれば解釈できるはず");
    assert_eq!(deck.cards.len(), 20);
}

#[test]
fn エネルギー行があるデッキはそのまま解釈する() {
    // 既に指定があるなら推定しない
    let deck = parse_deck_inferring_energy(FIRE_DECK).expect("炎デッキは有効なはず");
    assert_eq!(deck.cards.len(), 20);
    assert_eq!(infer_energy_line(FIRE_DECK), None);
}

#[test]
fn 未実装カードは残っていない() {
    // deckgym は未実装のワザや特性を持つカードが場に出ると panic するので、
    // `parse_deck` はそういうカードを含むデッキを `UnimplementedCard` で弾く。
    //
    // フォークのカードは全て実装済みになった（docs/deckgym-fork.md）。実在のカードで
    // その分岐を通せなくなったので、代わりに「通す必要がない」ことを確かめる。本家の
    // 新カードを取り込んで未実装が生まれたら、ここが落ちて気づける。
    let unimplemented: Vec<String> = CardId::iter()
        .filter(|card_id| !Implementation::from(get_implementation_status(*card_id)).is_complete())
        .map(|card_id| get_card_by_enum(card_id).get_id())
        .collect();
    assert!(
        unimplemented.is_empty(),
        "未実装のカードがある: {unimplemented:?}"
    );
}

#[test]
fn エネルギータイプが四種以上ならエラーになる() {
    // 選べるエネルギータイプは 1〜3 種（docs/game-rules.md 1 節）。
    // deckgym の Deck::is_valid は上限を見ないのでラッパー側で弾く
    let text = FIRE_DECK.replace("Energy: Fire", "Energy: Fire, Water, Grass, Lightning");
    assert_eq!(
        parse_deck(&text),
        Err(DeckError::TooManyEnergyTypes { actual: 4 })
    );
}

#[test]
fn エネルギータイプが三種までなら通る() {
    let text = FIRE_DECK.replace("Energy: Fire", "Energy: Fire, Water, Grass");
    assert!(parse_deck(&text).is_ok());
}

#[test]
fn 推定したエネルギー行も三種までに収める() {
    // カードから推定すると 4 種以上になりうる。推定側でも上限を守る
    let line = infer_energy_line(NO_ENERGY_DECK).expect("推定できるはず");
    let count = line
        .trim_start_matches("Energy: ")
        .split(',')
        .filter(|s| !s.trim().is_empty())
        .count();
    assert!(count <= 3, "推定したタイプが多すぎる: {line}");
}

/// エネルギー行のないメガジュカイン + ゲッコウガ。limitless の登録者が指定していなかった実例。
/// ゲッコウガは特性専用で攻撃しないため、実際は草単色で回す。
const SCEPTILE_NO_ENERGY: &str = "\
2 Treecko B3 5
1 Grovyle B3 6
2 Mega Sceptile ex B3 8
1 Froakie A1 87
1 Greninja A1 89
1 Pheromosa A3a 7
1 Furfrou B3b 61
2 Professor's Research P-A 7
2 Copycat B1 225
1 Cyrus A2 150
2 Poké Ball P-A 5
2 Rare Candy A3 144
1 X Speed P-A 2
1 Hiking Trail B2b 69
";

#[test]
fn エネルギーの推定はワザのコストに基づく() {
    // ポケモンのタイプで数えるとゲッコウガ（水）が混ざり草・水になるが、
    // ワザのコストで数えると草 6 に対して水 1 で、水は少数派として除かれる
    let line = infer_energy_line(SCEPTILE_NO_ENERGY).expect("推定できるはず");
    assert_eq!(line, "Energy: Grass", "推定結果: {line}");
}

#[test]
fn 主力のタイプが複数あれば複数のエネルギーを推定する() {
    // 炎と水のたねを同数入れ、どちらも攻撃に使う構成
    let text = "\
2 A1 042
2 A1 043
2 A1 053
2 A1 054
2 A1 223
2 A1 225
2 P-A 001
2 P-A 005
2 P-A 007
2 A1 186
";
    let line = infer_energy_line(text).expect("推定できるはず");
    assert!(
        line.contains("Fire") && line.contains("Water"),
        "推定結果: {line}"
    );
}
