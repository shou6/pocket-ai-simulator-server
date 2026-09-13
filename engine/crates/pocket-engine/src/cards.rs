//! カードの実装状況の列挙。
//!
//! deckgym は未実装のワザ・特性・トレーナーズを含むデッキを扱えない。
//! 評価できないデッキを事前に判別するため、カードごとの状況を取り出す。
//! deckgym の `src/bin/card_status.rs` と同じ情報を、ライブラリとして返す。

use std::collections::{HashMap, HashSet};

use deckgym::card_ids::CardId;
use deckgym::card_validation::{ImplementationStatus, get_implementation_status};
use deckgym::database::get_card_by_enum;
use strum::IntoEnumIterator;

/// カード 1 枚の実装状況。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Implementation {
    /// 実装済み。
    Complete,
    /// カードが見つからない。
    CardNotFound,
    /// ワザの効果が未実装。
    MissingAttack,
    /// 特性が未実装。
    MissingAbility,
    /// トレーナーズの処理が未実装。
    MissingTrainer,
    /// ポケモンのどうぐが未実装。
    MissingTool,
}

impl Implementation {
    /// 実装済みかどうか。
    #[must_use]
    pub fn is_complete(self) -> bool {
        self == Implementation::Complete
    }

    /// 状況の説明。UI に「なぜ評価できないか」を出すために使う。
    #[must_use]
    pub fn description(self) -> &'static str {
        match self {
            Implementation::Complete => "実装済み",
            Implementation::CardNotFound => "カードが見つからない",
            Implementation::MissingAttack => "ワザの効果が未実装",
            Implementation::MissingAbility => "特性が未実装",
            Implementation::MissingTrainer => "トレーナーズの処理が未実装",
            Implementation::MissingTool => "ポケモンのどうぐが未実装",
        }
    }
}

impl From<ImplementationStatus> for Implementation {
    fn from(status: ImplementationStatus) -> Self {
        match status {
            ImplementationStatus::Complete => Implementation::Complete,
            ImplementationStatus::CardNotFound => Implementation::CardNotFound,
            ImplementationStatus::MissingAttack => Implementation::MissingAttack,
            ImplementationStatus::MissingAbility => Implementation::MissingAbility,
            ImplementationStatus::MissingTrainer => Implementation::MissingTrainer,
            ImplementationStatus::MissingTool => Implementation::MissingTool,
        }
    }
}

/// カード 1 枚の識別情報と実装状況。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CardStatus {
    /// デッキテキストで使う識別子（`A1 001` 形式）。
    pub id: String,
    /// カード名（英語）。
    pub name: String,
    /// 実装状況。
    pub implementation: Implementation,
}

/// すべてのカードの実装状況を返す。
///
/// 順序は deckgym の `CardId` の定義順で、呼び出しごとに変わらない。
#[must_use]
pub fn card_statuses() -> Vec<CardStatus> {
    CardId::iter()
        .map(|card_id| {
            let card = get_card_by_enum(card_id);
            CardStatus {
                id: card.get_id(),
                name: card.get_name(),
                implementation: get_implementation_status(card_id).into(),
            }
        })
        .collect()
}

/// 未実装のカードだけを返す。
#[must_use]
pub fn incomplete_cards() -> Vec<CardStatus> {
    card_statuses()
        .into_iter()
        .filter(|card| !card.implementation.is_complete())
        .collect()
}

/// ワザ 1 つの内容。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AttackInfo {
    /// ワザ名（英語）。
    pub title: String,
    /// 必要なエネルギー（日本語のタイプ名）。
    pub cost: Vec<String>,
    /// 基礎ダメージ。効果による増減は含まない。
    pub damage: u32,
    /// 効果文（英語）。
    pub effect: Option<String>,
}

/// 特性 1 つの内容。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AbilityInfo {
    /// 特性名（英語）。
    pub title: String,
    /// 効果文（英語）。
    pub effect: String,
}

/// カード 1 枚の性能。対戦ログを外部に渡すとき、カードを知らない相手のために添える。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CardDossier {
    /// デッキテキストで使う識別子（`A1 001` 形式）。
    pub id: String,
    /// カード名（英語）。
    pub name: String,
    /// 種類（`ポケモン` / `グッズ` / `サポート` / `ポケモンのどうぐ` / `スタジアム` / `化石`）。
    pub kind: String,
    /// 進化段階（たね 0、1 進化 1、2 進化 2）。トレーナーズなら `None`。
    pub stage: Option<u8>,
    /// 進化元のカード名。
    pub evolves_from: Option<String>,
    /// 最大 HP。
    pub hp: Option<u32>,
    /// タイプ（日本語）。
    pub energy_type: Option<String>,
    /// 弱点のタイプ（日本語）。
    pub weakness: Option<String>,
    /// にげるコスト。
    pub retreat_cost: Option<usize>,
    /// 特性。
    pub ability: Option<AbilityInfo>,
    /// ワザ。
    pub attacks: Vec<AttackInfo>,
    /// トレーナーズの効果文（英語）。
    pub effect: Option<String>,
    /// 古代・未来のポケモンなら `古代` / `未来`。ブーストエナジーやオーリム博士の条件に使う。
    pub lineage: Option<String>,
}

/// 古代のポケモン。deckgym の `hooks/core.rs` にある一覧の写し（本家のモジュールは非公開で呼べない）。
/// カード名が変わると `tests/cards.rs` が落ちる。
pub const ANCIENT_POKEMON: [&str; 11] = [
    "Brute Bonnet",
    "Slither Wing",
    "Scream Tail",
    "Flutter Mane ex",
    "Great Tusk",
    "Sandy Shocks",
    "Koraidon ex",
    "Roaring Moon",
    "Walking Wake",
    "Gouging Fire",
    "Raging Bolt",
];

/// 未来のポケモン。`ANCIENT_POKEMON` と同じく deckgym の一覧の写し。
pub const FUTURE_POKEMON: [&str; 11] = [
    "Iron Moth",
    "Iron Bundle ex",
    "Iron Hands",
    "Iron Thorns",
    "Miraidon ex",
    "Iron Valiant",
    "Iron Leaves",
    "Iron Boulder",
    "Iron Crown",
    "Iron Jugulis",
    "Iron Treads",
];

fn lineage_of(name: &str) -> Option<String> {
    if ANCIENT_POKEMON.contains(&name) {
        Some("古代".to_string())
    } else if FUTURE_POKEMON.contains(&name) {
        Some("未来".to_string())
    } else {
        None
    }
}

/// エネルギー種別の日本語名。
fn type_name(energy: deckgym::models::EnergyType) -> &'static str {
    use deckgym::models::EnergyType;
    match energy {
        EnergyType::Grass => "草",
        EnergyType::Fire => "炎",
        EnergyType::Water => "水",
        EnergyType::Lightning => "雷",
        EnergyType::Psychic => "超",
        EnergyType::Fighting => "闘",
        EnergyType::Darkness => "悪",
        EnergyType::Metal => "鋼",
        EnergyType::Dragon => "竜",
        EnergyType::Colorless => "無",
    }
}

fn dossier_of(card: &deckgym::models::Card) -> CardDossier {
    use deckgym::models::{Card, TrainerType};
    match card {
        Card::Pokemon(pokemon) => CardDossier {
            id: pokemon.id.clone(),
            name: pokemon.name.clone(),
            kind: "ポケモン".to_string(),
            stage: Some(pokemon.stage),
            evolves_from: pokemon.evolves_from.clone(),
            hp: Some(pokemon.hp),
            energy_type: Some(type_name(pokemon.energy_type).to_string()),
            weakness: pokemon.weakness.map(|w| type_name(w).to_string()),
            retreat_cost: Some(pokemon.retreat_cost.len()),
            ability: pokemon.ability.as_ref().map(|a| AbilityInfo {
                title: a.title.clone(),
                effect: a.effect.clone(),
            }),
            attacks: pokemon
                .attacks
                .iter()
                .map(|attack| AttackInfo {
                    title: attack.title.clone(),
                    cost: attack
                        .energy_required
                        .iter()
                        .map(|e| type_name(*e).to_string())
                        .collect(),
                    damage: attack.fixed_damage,
                    effect: attack.effect.clone(),
                })
                .collect(),
            effect: None,
            lineage: lineage_of(&pokemon.name),
        },
        Card::Trainer(trainer) => CardDossier {
            id: trainer.id.clone(),
            name: trainer.name.clone(),
            kind: match trainer.trainer_card_type {
                TrainerType::Supporter => "サポート",
                TrainerType::Item => "グッズ",
                TrainerType::Tool => "ポケモンのどうぐ",
                TrainerType::Fossil => "化石",
                TrainerType::Stadium => "スタジアム",
            }
            .to_string(),
            stage: None,
            evolves_from: None,
            hp: None,
            energy_type: None,
            weakness: None,
            retreat_cost: None,
            ability: None,
            attacks: Vec::new(),
            effect: Some(trainer.effect.clone()),
            lineage: None,
        },
    }
}

/// 指定した識別子のカードの性能を返す。見つからない識別子は飛ばす。
///
/// 順序は `ids` の順。同じ識別子を重ねて渡しても 1 件だけ返す。
#[must_use]
pub fn card_dossiers(ids: &[String]) -> Vec<CardDossier> {
    let wanted: HashSet<&str> = ids.iter().map(String::as_str).collect();
    let mut found: HashMap<String, CardDossier> = HashMap::new();
    for card_id in CardId::iter() {
        let card = get_card_by_enum(card_id);
        let id = card.get_id();
        if wanted.contains(id.as_str()) {
            found.entry(id).or_insert_with(|| dossier_of(&card));
        }
    }
    let mut seen = HashSet::new();
    ids.iter()
        .filter(|id| seen.insert(id.as_str()))
        .filter_map(|id| found.get(id).cloned())
        .collect()
}
