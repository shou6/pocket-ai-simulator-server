//! カードの実装状況を列挙するラッパーのテスト。

use pocket_engine::cards::{CardStatus, Implementation, card_statuses, incomplete_cards};

#[test]
fn すべてのカードの状況を列挙できる() {
    let statuses = card_statuses();
    assert!(
        statuses.len() > 1000,
        "カード数が少なすぎる: {}",
        statuses.len()
    );
}

#[test]
fn カード識別子と名前が入っている() {
    let statuses = card_statuses();
    let bulbasaur = statuses
        .iter()
        .find(|card| card.id == "A1 001")
        .expect("A1 001 があるはず");
    assert_eq!(bulbasaur.name, "Bulbasaur");
    assert_eq!(bulbasaur.implementation, Implementation::Complete);
}

#[test]
fn 未実装カードだけを取り出せる() {
    let incomplete = incomplete_cards();

    // カードは全て実装済みになった（docs/deckgym-fork.md）。本家の新カードを取り込んで
    // 未実装が生まれたらここが落ちて気づける
    assert!(
        incomplete.is_empty(),
        "未実装のカードがある: {:?}",
        incomplete.iter().map(|card| &card.id).collect::<Vec<_>>()
    );
    assert!(
        incomplete
            .iter()
            .all(|card| card.implementation != Implementation::Complete),
        "実装済みカードが混ざっている"
    );
}

#[test]
fn 実装状況に理由が付く() {
    // UI が「なぜ評価できないか」を出せるように、どの状況にも説明がある
    for implementation in [
        Implementation::Complete,
        Implementation::CardNotFound,
        Implementation::MissingAttack,
        Implementation::MissingAbility,
        Implementation::MissingTrainer,
        Implementation::MissingTool,
    ] {
        assert!(
            !implementation.description().is_empty(),
            "理由が空になっている: {implementation:?}"
        );
    }
}

#[test]
fn 同じ呼び出しなら同じ結果になる() {
    let first: Vec<CardStatus> = card_statuses();
    let second: Vec<CardStatus> = card_statuses();
    assert_eq!(first, second);
}

#[test]
fn 古代と未来のポケモンを見分けられる() {
    use pocket_engine::cards::{ANCIENT_POKEMON, FUTURE_POKEMON, card_dossiers};

    let names: std::collections::HashSet<String> =
        card_statuses().into_iter().map(|card| card.name).collect();
    // 一覧は deckgym の hooks/core.rs の写し。名前が変わったらここで気づく
    for name in ANCIENT_POKEMON.iter().chain(FUTURE_POKEMON.iter()) {
        assert!(names.contains(*name), "カードに無い名前: {name}");
    }

    let ids: Vec<String> = card_statuses()
        .into_iter()
        .filter(|card| ["Great Tusk", "Iron Hands", "Bulbasaur"].contains(&card.name.as_str()))
        .map(|card| card.id)
        .collect();
    let dossiers = card_dossiers(&ids);
    let lineage_of = |name: &str| {
        dossiers
            .iter()
            .find(|card| card.name == name)
            .map(|card| card.lineage.clone())
    };
    assert_eq!(lineage_of("Great Tusk"), Some(Some("古代".to_string())));
    assert_eq!(lineage_of("Iron Hands"), Some(Some("未来".to_string())));
    assert_eq!(lineage_of("Bulbasaur"), Some(None));
}
