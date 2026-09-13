//! デッキテキストの解釈と検証。
//!
//! deckgym の [`Deck::from_string`] は不正な入力で panic することがあるため、
//! このモジュールが前段で検証してから渡す（`.claude/rules/engine.md`）。

use std::collections::HashMap;

pub use deckgym::Deck;
use deckgym::models::{Card, EnergyType};

use crate::cards::Implementation;
use thiserror::Error;

/// デッキ 1 つの枚数。
const DECK_SIZE: usize = 20;

/// 同名カードの上限枚数。
const MAX_COPIES: usize = 2;

/// エネルギーゾーンに設定できるタイプ数の上限。
const MAX_ENERGY_TYPES: usize = 3;

/// エネルギーを推定するとき、採用するタイプの最低シェア（1/4 以上）。
///
/// ワザのコスト全体に占める割合がこれ未満のタイプは、攻撃しないポケモンの
/// ワザに由来するとみなして除く。
const MIN_ENERGY_SHARE_DENOMINATOR: usize = 4;

/// デッキテキストを解釈できなかった理由。
#[derive(Debug, Error, PartialEq, Eq)]
pub enum DeckError {
    /// `Energy:` 行に未知の種別が書かれている。
    #[error("エネルギー種別が不正です: {name}")]
    UnknownEnergyType {
        /// 書かれていた文字列。
        name: String,
    },

    /// `Energy:` 行はあるが種別が 1 つも書かれていない。
    #[error("エネルギーの種類が指定されていません")]
    NoEnergyType,

    /// エネルギー種別が上限を超えている。
    #[error("エネルギーは {MAX_ENERGY_TYPES} 種類までです（現在 {actual} 種類）")]
    TooManyEnergyTypes {
        /// 指定されていた種類数。
        actual: usize,
    },

    /// エネルギーゾーンで生成できない種別（Dragon / Colorless）が指定されている。
    #[error("エネルギーゾーンで生成できない種別です: {name}")]
    UnselectableEnergyType {
        /// 指定されていた種別。
        name: String,
    },

    /// カード行を解釈できなかった（書式不正、未知のカード番号など）。
    #[error("デッキテキストを解釈できません: {reason}")]
    Parse {
        /// deckgym が返した理由。
        reason: String,
    },

    /// 枚数が 20 枚ではない。
    #[error("デッキは {DECK_SIZE} 枚である必要があります（現在 {actual} 枚）")]
    CardCount {
        /// 実際の枚数。
        actual: usize,
    },

    /// たねポケモンが入っていない。
    #[error("たねポケモンが 1 枚も入っていません")]
    NoBasicPokemon,

    /// 同名カードが 3 枚以上入っている。
    #[error("同名カードは {MAX_COPIES} 枚までです: {name}")]
    TooManyCopies {
        /// 上限を超えたカード名。
        name: String,
    },

    /// deckgym がまだ実装していないカードを含む。
    ///
    /// このカードが場に出ると deckgym は panic するため、事前に弾く。
    #[error("未実装のカードが含まれています: {id} {name}（{reason}）")]
    UnimplementedCard {
        /// カードの識別子（`B2 092` 形式）。
        id: String,
        /// カード名。
        name: String,
        /// 未実装の理由。
        reason: &'static str,
    },

    /// 上記のいずれにも当てはまらないが deckgym の検証を通らなかった。
    #[error("デッキが不正です")]
    Invalid,
}

/// エネルギー種別の名前を [`EnergyType`] に変換する。
///
/// deckgym の `EnergyType::from_str` は `pub(crate)` で外から呼べないため、
/// 同じ対応表をここに持つ。上流で公開されたら差し替える。
fn energy_type_from_str(name: &str) -> Option<EnergyType> {
    match name {
        "Grass" => Some(EnergyType::Grass),
        "Fire" => Some(EnergyType::Fire),
        "Water" => Some(EnergyType::Water),
        "Lightning" => Some(EnergyType::Lightning),
        "Psychic" => Some(EnergyType::Psychic),
        "Fighting" => Some(EnergyType::Fighting),
        "Darkness" => Some(EnergyType::Darkness),
        "Metal" => Some(EnergyType::Metal),
        "Dragon" => Some(EnergyType::Dragon),
        "Colorless" => Some(EnergyType::Colorless),
        _ => None,
    }
}

/// エネルギー種別を deckgym が受け付ける名前に戻す。
fn energy_type_to_str(energy: EnergyType) -> &'static str {
    match energy {
        EnergyType::Grass => "Grass",
        EnergyType::Fire => "Fire",
        EnergyType::Water => "Water",
        EnergyType::Lightning => "Lightning",
        EnergyType::Psychic => "Psychic",
        EnergyType::Fighting => "Fighting",
        EnergyType::Darkness => "Darkness",
        EnergyType::Metal => "Metal",
        EnergyType::Dragon => "Dragon",
        EnergyType::Colorless => "Colorless",
    }
}

/// `Energy:` 行を検証する。
///
/// deckgym に渡す前に呼ぶこと。未知の種別があると `Deck::from_string` が panic する。
fn validate_energy_lines(text: &str) -> Result<(), DeckError> {
    let mut seen_energy_line = false;
    let mut types: Vec<EnergyType> = Vec::new();

    for line in text.lines() {
        let Some(list) = line.trim().strip_prefix("Energy:") else {
            continue;
        };
        seen_energy_line = true;

        for name in list.split(',').map(str::trim).filter(|s| !s.is_empty()) {
            let energy =
                energy_type_from_str(name).ok_or_else(|| DeckError::UnknownEnergyType {
                    name: name.to_string(),
                })?;
            if !energy.is_selectable() {
                return Err(DeckError::UnselectableEnergyType {
                    name: name.to_string(),
                });
            }
            if !types.contains(&energy) {
                types.push(energy);
            }
        }
    }

    if seen_energy_line && types.is_empty() {
        return Err(DeckError::NoEnergyType);
    }
    if types.len() > MAX_ENERGY_TYPES {
        return Err(DeckError::TooManyEnergyTypes {
            actual: types.len(),
        });
    }
    Ok(())
}

/// 構築済みのデッキが対戦に使えるかを検証し、違反していれば理由を返す。
///
/// deckgym の `Deck::is_valid` は `bool` しか返さないため、同じ条件をここで
/// 個別に確かめて理由付きのエラーにする。取りこぼしは [`DeckError::Invalid`] に落ちる。
fn validate_deck(deck: &Deck) -> Result<(), DeckError> {
    if deck.cards.len() != DECK_SIZE {
        return Err(DeckError::CardCount {
            actual: deck.cards.len(),
        });
    }

    if !deck.cards.iter().any(deckgym::models::Card::is_basic) {
        return Err(DeckError::NoBasicPokemon);
    }

    let mut counts: HashMap<String, usize> = HashMap::new();
    for card in &deck.cards {
        let name = card.get_name();
        let count = counts.entry(name.clone()).or_insert(0);
        *count += 1;
        if *count > MAX_COPIES {
            return Err(DeckError::TooManyCopies { name });
        }
    }

    for card in &deck.cards {
        let implementation = Implementation::from(
            deckgym::card_validation::get_implementation_status(card.get_card_id()),
        );
        if !implementation.is_complete() {
            return Err(DeckError::UnimplementedCard {
                id: card.get_id(),
                name: card.get_name(),
                reason: implementation.description(),
            });
        }
    }

    if deck.is_valid() {
        Ok(())
    } else {
        // `Energy:` 行がないデッキでは、カードから導出したエネルギー種別が
        // 生成不可能だった場合がここに来る
        Err(DeckError::Invalid)
    }
}

/// 生成可能なタイプだけを数える。
fn count_energy(counts: &mut Vec<(EnergyType, usize)>, energy: EnergyType) {
    if !energy.is_selectable() {
        return;
    }
    match counts.iter_mut().find(|(t, _)| *t == energy) {
        Some((_, count)) => *count += 1,
        None => counts.push((energy, 1)),
    }
}

/// エネルギー行のないデッキテキストから `Energy:` 行を推定する。
///
/// limitless のデッキリストには登録者がエネルギーを指定していないものがある。
/// その場合、ワザのコストに実際に必要なタイプを数え、多数派を採る。
/// ポケモンのタイプで数えると、特性専用で攻撃しないポケモン（ゲッコウガなど）の
/// タイプまで混ざり、実際は単色のデッキを 2 タイプにしてしまう。
/// 無色と竜はエネルギーゾーンで生成できないため除く。
///
/// 既に `Energy:` 行があるとき、または推定できるタイプがないときは `None` を返す。
///
/// これは公式ルールではなく本プロジェクトの補完である。実際の対戦では
/// プレイヤーがエネルギーを選ぶ。推定した内容が構築意図と食い違う可能性がある。
#[must_use]
pub fn infer_energy_line(text: &str) -> Option<String> {
    if text.lines().any(|line| line.trim().starts_with("Energy:")) {
        return None;
    }

    let deck = Deck::from_string(text).ok()?;

    // ワザのコストに実際に必要なタイプを数える。ポケモンのタイプで数えると、
    // 特性専用で攻撃しないポケモン（ゲッコウガなど）のタイプまで混ざる。
    // 無色はどのタイプでも払えるので数えない
    let mut counts: Vec<(EnergyType, usize)> = Vec::new();
    for card in &deck.cards {
        let Card::Pokemon(pokemon) = card else {
            continue;
        };
        for attack in &pokemon.attacks {
            for energy in &attack.energy_required {
                count_energy(&mut counts, *energy);
            }
        }
    }

    // タイプ指定のワザが 1 つもなければ、ポケモンのタイプで代用する
    if counts.is_empty() {
        for card in &deck.cards {
            if let Card::Pokemon(pokemon) = card {
                count_energy(&mut counts, pokemon.energy_type);
            }
        }
    }
    if counts.is_empty() {
        return None;
    }

    // 少数派のタイプは除く。攻撃しないポケモンのワザが混ざるのを避けるため。
    // 枚数が同じ場合はタイプの定義順で決める。同じ入力なら同じ結果になるように
    let total: usize = counts.iter().map(|(_, count)| *count).sum();
    counts.sort_by(|a, b| b.1.cmp(&a.1).then(a.0.cmp(&b.0)));
    let mut types: Vec<EnergyType> = counts
        .iter()
        .filter(|(_, count)| *count * MIN_ENERGY_SHARE_DENOMINATOR >= total)
        .take(MAX_ENERGY_TYPES)
        .map(|(energy, _)| *energy)
        .collect();
    if types.is_empty() {
        types.push(counts[0].0);
    }
    types.sort();

    let names: Vec<&str> = types.into_iter().map(energy_type_to_str).collect();
    Some(format!("Energy: {}", names.join(", ")))
}

/// デッキテキストを解釈する。`Energy:` 行がなければ推定して補う。
///
/// 推定の内容は [`infer_energy_line`] を参照。取り込んだデッキリストのように
/// エネルギー指定が欠けている可能性がある入力に使う。
///
/// # Errors
///
/// [`parse_deck`] と同じ条件でエラーを返す。
pub fn parse_deck_inferring_energy(text: &str) -> Result<Deck, DeckError> {
    match infer_energy_line(text) {
        Some(line) => parse_deck(&format!("{line}\n{text}")),
        None => parse_deck(text),
    }
}

/// デッキテキストを解釈するが、構築ルールの検証は行わない。
///
/// 未実装カードを含むデッキの挙動を確かめるなど、検証を通さずに
/// [`Deck`] が必要な場合に使う。通常は [`parse_deck`] を使うこと。
///
/// # Errors
///
/// 書式が不正な場合、未知のカードを含む場合に [`DeckError`] を返す。
pub fn parse_deck_unchecked(text: &str) -> Result<Deck, DeckError> {
    validate_energy_lines(text)?;
    Deck::from_string(text).map_err(|reason| DeckError::Parse { reason })
}

/// デッキテキストを解釈し、検証済みの [`Deck`] を返す。
///
/// 受け付ける書式は deckgym と同じ。
///
/// - `Energy: Fire, Water` … エネルギーゾーンが生成する種別（省略時はカードから導出）
/// - `2 A1 042` … 枚数とカード ID。末尾 2 トークンがセット記号と番号であればよく、
///   `2 Charmander A1 042` のようにカード名を挟んでもよい
/// - 空行、`Pokémon:` 行、`Trainer:` 行は無視される
///
/// # Errors
///
/// 書式が不正な場合、未知のカードを含む場合、構築ルールに違反する場合に [`DeckError`] を返す。
pub fn parse_deck(text: &str) -> Result<Deck, DeckError> {
    validate_energy_lines(text)?;
    let deck = Deck::from_string(text).map_err(|reason| DeckError::Parse { reason })?;
    validate_deck(&deck)?;
    Ok(deck)
}
