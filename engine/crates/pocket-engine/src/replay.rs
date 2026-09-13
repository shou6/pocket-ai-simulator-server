//! 対戦の推移を記録する。
//!
//! 勝敗だけでは方策の良し悪しも、実戦との食い違いの原因も分からない。
//! 1 試合を回しながら、行動ごとに盤面を記録して返す。

use std::panic::{AssertUnwindSafe, catch_unwind};

use deckgym::actions::SimpleAction;
use deckgym::models::{Card, EnergyType, StatusCondition};
use deckgym::state::PlayedCard;
use deckgym::{Deck, State};

use crate::game::{
    GameError, MatchResult, Strategy, correction_rng, new_game, play_tick_with_corrections,
};

/// カード 1 枚の参照（画像の表示に使う）。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CardRef {
    /// デッキテキストで使う識別子（`A1 001` 形式）。カード画像の場所に使う。
    pub id: String,
    /// カード名（英語）。表示するときに和名へ置き換える。
    pub name: String,
}

impl CardRef {
    fn of(card: &Card) -> Self {
        Self {
            id: card.get_id(),
            name: card.get_name(),
        }
    }
}

/// 盤面にいるポケモン 1 匹の状態。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PokemonSnapshot {
    /// デッキテキストで使う識別子（`A1 001` 形式）。
    pub id: String,
    /// カード名。
    pub name: String,
    /// 残り HP。
    pub remaining_hp: u32,
    /// 最大 HP。
    pub max_hp: u32,
    /// 付いているエネルギー。
    pub energy: Vec<String>,
    /// 受けている状態異常。
    pub status: Vec<String>,
    /// 付いているどうぐ。
    /// ポケモンのどうぐ。ブロロローム（B4 115）だけ 2 枚持てる。
    pub tools: Vec<CardRef>,
}

impl PokemonSnapshot {
    fn from_played(pokemon: &PlayedCard) -> Self {
        let max_hp = match &pokemon.card {
            Card::Pokemon(card) => card.hp,
            Card::Trainer(_) => 0,
        };

        let mut status = Vec::new();
        if pokemon.is_asleep() {
            status.push("ねむり".to_string());
        }
        if pokemon.is_paralyzed() {
            status.push("まひ".to_string());
        }
        if pokemon.is_confused() {
            status.push("こんらん".to_string());
        }
        if pokemon.is_poisoned() {
            status.push("どく".to_string());
        }
        if pokemon.is_burned() {
            status.push("やけど".to_string());
        }

        Self {
            id: pokemon.card.get_id(),
            name: pokemon.card.get_name(),
            remaining_hp: pokemon.get_remaining_hp(),
            max_hp,
            energy: pokemon
                .attached_energy
                .iter()
                .map(|energy| energy_name(*energy).to_string())
                .collect(),
            status,
            tools: pokemon.attached_tools.iter().map(CardRef::of).collect(),
        }
    }
}

/// 行動 1 つと、その直後の盤面。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReplayStep {
    /// 何ターン目か。
    pub turn: u8,
    /// 行動したプレイヤー（0 が先攻）。
    pub actor: usize,
    /// 行動の説明（日本語）。
    pub description: String,
    /// 行動後の両者のポイント。
    pub points: [u8; 2],
    /// 行動後の両者のバトルポケモン。
    pub active: [Option<PokemonSnapshot>; 2],
    /// 行動後の両者のベンチ。
    pub bench: [Vec<PokemonSnapshot>; 2],
    /// 行動後の両者の手札枚数。
    pub hand_size: [usize; 2],
    /// 行動後の両者の手札の内容。
    pub hand: [Vec<CardRef>; 2],
    /// 行動後に場に出ているスタジアムと、その持ち主（0 が先攻）。
    pub stadium: Option<CardRef>,
    /// スタジアムの持ち主。
    pub stadium_owner: Option<usize>,
    /// 行動後の両者の山札枚数。
    pub deck_size: [usize; 2],
}

/// 1 試合分の記録。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Replay {
    /// 行動の並び。
    pub steps: Vec<ReplayStep>,
    /// 両者の初手（準備前の 5 枚）。
    pub opening_hands: [Vec<CardRef>; 2],
    /// 試合の結果。
    pub result: MatchResult,
    /// 最終的なポイント。
    pub points: [u8; 2],
    /// 決着までのターン数。
    pub turns: u8,
}

/// エネルギー種別の日本語名。
fn energy_name(energy: EnergyType) -> &'static str {
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

/// 盤面の位置の呼び名。
fn slot_name(in_play_idx: usize) -> String {
    if in_play_idx == 0 {
        "バトル場".to_string()
    } else {
        format!("ベンチ{in_play_idx}")
    }
}

/// 状態異常の日本語名。
fn status_name(condition: StatusCondition) -> &'static str {
    match condition {
        StatusCondition::Poisoned => "どく",
        StatusCondition::Paralyzed => "まひ",
        StatusCondition::Asleep => "ねむり",
        StatusCondition::Burned => "やけど",
        StatusCondition::Confused => "こんらん",
    }
}

/// 場のポケモンの名前。いなければ `?`。
fn pokemon_name(state: &State, player: usize, in_play_idx: usize) -> String {
    state.in_play_pokemon[player]
        .get(in_play_idx)
        .and_then(Option::as_ref)
        .map_or_else(|| "?".to_string(), |p| p.card.get_name())
}

/// 「自分のベンチ2の Caterpie」のように、誰のどこにいる何かを言う。
fn pokemon_at(state: &State, actor: usize, player: usize, in_play_idx: usize) -> String {
    let owner = if player == actor { "自分" } else { "相手" };
    format!(
        "{owner}の{}の {}",
        slot_name(in_play_idx),
        pokemon_name(state, player, in_play_idx)
    )
}

/// カード名を「、」でつなぐ。
fn card_names(cards: &[Card]) -> String {
    cards
        .iter()
        .map(Card::get_name)
        .collect::<Vec<_>>()
        .join("、")
}

/// 行動を日本語の説明にする。
///
/// `state` は行動前の盤面。倒れる直前のポケモンの名前もここから引く。
#[allow(clippy::too_many_lines)]
fn describe(action: &SimpleAction, state: &State, actor: usize) -> String {
    let mine = |in_play_idx: usize| pokemon_at(state, actor, actor, in_play_idx);
    match action {
        SimpleAction::Attack(attack) => {
            let name = pokemon_name(state, actor, 0);
            format!(
                "{name} がワザ「{}」（{} ダメージ）",
                attack.title, attack.fixed_damage
            )
        }
        SimpleAction::Place(card, in_play_idx) => {
            format!("{} を{}に出す", card.get_name(), slot_name(*in_play_idx))
        }
        SimpleAction::Evolve {
            evolution,
            in_play_idx,
            from_deck,
        } => {
            let source = if *from_deck { "山札から" } else { "" };
            format!(
                "{}のポケモンを {source}{} に進化",
                slot_name(*in_play_idx),
                evolution.get_name()
            )
        }
        SimpleAction::Attach { attachments, .. } => {
            let parts: Vec<String> = attachments
                .iter()
                .map(|(amount, energy, in_play_idx)| {
                    format!(
                        "{}エネルギー{amount}個を{}へ",
                        energy_name(*energy),
                        slot_name(*in_play_idx)
                    )
                })
                .collect();
            parts.join("、")
        }
        SimpleAction::Play { trainer_card } => {
            format!("トレーナーズ「{}」を使う", trainer_card.name)
        }
        SimpleAction::UseAbility { in_play_idx } => {
            format!("{} の特性を使う", pokemon_name(state, actor, *in_play_idx))
        }
        SimpleAction::Retreat(to_in_play_idx) => {
            format!("{}のポケモンとにげる", slot_name(*to_in_play_idx))
        }
        SimpleAction::EndTurn => "ターンを終える".to_string(),
        SimpleAction::DrawCard { amount } => format!("{amount} 枚引く"),

        // ここから下はワザや特性・トレーナーズの効果として deckgym が内部で処理する行動
        SimpleAction::ApplyDamage { targets, .. } => {
            let parts: Vec<String> = targets
                .iter()
                .map(|(damage, player, in_play_idx)| {
                    format!(
                        "{} に {damage} ダメージ",
                        pokemon_at(state, actor, *player, *in_play_idx)
                    )
                })
                .collect();
            format!("効果: {}", parts.join("、"))
        }
        SimpleAction::ScheduleDelayedSpotDamage {
            target_player,
            target_in_play_idx,
            amount,
        } => format!(
            "効果: {} に次の番 {amount} ダメージを予約",
            pokemon_at(state, actor, *target_player, *target_in_play_idx)
        ),
        SimpleAction::Heal {
            in_play_idx,
            amount,
            cure_status,
        } => {
            let cure = if *cure_status {
                "、状態異常を回復"
            } else {
                ""
            };
            format!("効果: {} の HP を {amount} 回復{cure}", mine(*in_play_idx))
        }
        SimpleAction::HealAndDiscardEnergy {
            in_play_idx,
            heal_amount,
            discard_energies,
        } => format!(
            "効果: {} の HP を {heal_amount} 回復し、エネルギー {} 個をトラッシュ",
            mine(*in_play_idx),
            discard_energies.len()
        ),
        SimpleAction::MoveAllDamage { from, to } => {
            format!(
                "効果: {} のダメージをすべて {} に移す",
                mine(*from),
                mine(*to)
            )
        }
        SimpleAction::MoveEnergy {
            from_in_play_idx,
            to_in_play_idx,
            energy_type,
            amount,
        } => format!(
            "効果: {}エネルギー{amount}個を{}から{}へ移す",
            energy_name(*energy_type),
            slot_name(*from_in_play_idx),
            slot_name(*to_in_play_idx)
        ),
        SimpleAction::AttachTool {
            in_play_idx,
            tool_card,
        } => format!(
            "{} にどうぐ「{}」を付ける",
            mine(*in_play_idx),
            tool_card.get_name()
        ),
        SimpleAction::DiscardToolFromPokemon {
            player,
            in_play_idx,
        } => format!(
            "効果: {} のどうぐをトラッシュ",
            pokemon_at(state, actor, *player, *in_play_idx)
        ),
        SimpleAction::UseStadium => "スタジアムの効果を使う".to_string(),
        SimpleAction::DiscardActiveStadium => "効果: 場のスタジアムをトラッシュ".to_string(),
        SimpleAction::Activate {
            player,
            in_play_idx,
        } => format!(
            "効果: {} をバトル場に出す",
            pokemon_at(state, actor, *player, *in_play_idx)
        ),
        SimpleAction::ApplyStatusToOpponentActive { condition } => {
            format!(
                "効果: 相手のバトルポケモンを{}にする",
                status_name(*condition)
            )
        }
        SimpleAction::ApplyStatusesToOpponentActive { conditions } => {
            let names: Vec<&str> = conditions.iter().map(|c| status_name(*c)).collect();
            format!("効果: 相手のバトルポケモンを{}にする", names.join("・"))
        }
        SimpleAction::Noop => "何もしない".to_string(),
        SimpleAction::AttachFromDiscard {
            in_play_idx,
            num_random_energies,
        } => format!(
            "効果: トラッシュのエネルギー{num_random_energies}個を{}に付ける",
            slot_name(*in_play_idx)
        ),
        SimpleAction::AttachTypedFromDiscard {
            in_play_idx,
            energy_type,
            count,
        } => format!(
            "効果: トラッシュの{}エネルギー{count}個を{}に付ける",
            energy_name(*energy_type),
            slot_name(*in_play_idx)
        ),
        SimpleAction::SadaAttach { assignments } => {
            let parts: Vec<String> = assignments
                .iter()
                .map(|(energy, in_play_idx)| {
                    format!(
                        "{}エネルギーを{}へ",
                        energy_name(*energy),
                        slot_name(*in_play_idx)
                    )
                })
                .collect();
            format!("効果: {}", parts.join("、"))
        }
        SimpleAction::DiscardRandomOpponentActiveEnergy => {
            "効果: 相手のバトルポケモンのエネルギーをランダムに 1 個トラッシュ".to_string()
        }
        SimpleAction::MoveRandomOpponentEnergyToActive { from_in_play_idx } => format!(
            "効果: {} のエネルギーをランダムに 1 個、相手のバトル場へ移す",
            pokemon_at(state, actor, 1 - actor, *from_in_play_idx)
        ),
        SimpleAction::MoveOpponentActiveEnergyToSelf { to_in_play_idx } => format!(
            "効果: 相手のバトルポケモンのエネルギーを {} に移す",
            mine(*to_in_play_idx)
        ),
        SimpleAction::CommunicatePokemon { hand_pokemon } => format!(
            "効果: 手札の {} を山札のポケモンと入れ替える",
            hand_pokemon.get_name()
        ),
        SimpleAction::ShufflePokemonIntoDeck { hand_pokemon } => {
            format!("効果: 手札の {} を山札に戻す", card_names(hand_pokemon))
        }
        SimpleAction::ShuffleOwnCardsIntoDeck { cards } => {
            format!("効果: 手札の {} を山札に戻して 1 枚引く", card_names(cards))
        }
        SimpleAction::SwitchHandCardForRandomTool { hand_card } => format!(
            "効果: 手札の {} を山札のどうぐと入れ替える",
            hand_card.get_name()
        ),
        SimpleAction::ShuffleOpponentSupporter { supporter_card } => format!(
            "効果: 相手の手札の {} を山札に戻す",
            supporter_card.get_name()
        ),
        SimpleAction::DiscardOpponentSupporter { supporter_card } => {
            format!(
                "効果: 相手の手札の {} をトラッシュ",
                supporter_card.get_name()
            )
        }
        SimpleAction::DiscardOwnCards { cards } => {
            format!("効果: 手札の {} をトラッシュ", card_names(cards))
        }
        SimpleAction::BenchOpponentPokemonFromHand { cards } => {
            format!(
                "効果: 相手の手札の {} をベンチに出させる",
                card_names(cards)
            )
        }
        SimpleAction::ApplyEeveeBagDamageBoost => "効果: イーブイバッグでダメージ追加".to_string(),
        SimpleAction::HealAllEeveeEvolutions => "効果: イーブイの進化系をすべて回復".to_string(),
        SimpleAction::DiscardFossil { in_play_idx } => {
            format!("効果: {} をトラッシュ", mine(*in_play_idx))
        }
        SimpleAction::DiscardOwnBenchedThenDamage {
            in_play_idx,
            damage,
        } => format!(
            "効果: {} をトラッシュして {damage} ダメージ",
            mine(*in_play_idx)
        ),
        SimpleAction::MoveEnergiesFromActive {
            to_in_play_idx,
            energies,
        } => format!(
            "効果: バトル場のエネルギー {} 個を{}へ移す",
            energies.len(),
            mine(*to_in_play_idx)
        ),
        SimpleAction::MoveFixedDamageToOpponentActive {
            in_play_idx,
            amount,
        } => format!(
            "効果: {} のダメージ {amount} を相手のバトルポケモンへ移す",
            mine(*in_play_idx)
        ),
        SimpleAction::ShuffleRandomOwnHandCardIntoDeck => {
            "効果: 自分の手札からランダムに 1 枚を山札へ戻す".to_string()
        }
        SimpleAction::BenchOpponentPokemonFromDiscard { card } => format!(
            "効果: 相手のトラッシュの {} を相手のベンチへ出す",
            card.get_name()
        ),
        SimpleAction::RecoverSupporterFromDiscard => {
            "効果: トラッシュのサポートを 1 枚手札へ".to_string()
        }
        SimpleAction::TakeItemsFromTop { reveal } => {
            format!("効果: 山札の上 {reveal} 枚を見てグッズをすべて手札へ")
        }
        SimpleAction::OpponentRedrawByRemainingPoints => {
            "効果: 相手は手札を山札に戻し、勝利に必要な点数ぶん引き直す".to_string()
        }
        SimpleAction::RecoverToolsFromDiscard { count } => {
            format!("効果: トラッシュのポケモンのどうぐ {count} 枚を手札へ")
        }
        SimpleAction::DiscardOwnBenchedManyThenDamage {
            in_play_idxs,
            damage,
        } => {
            if in_play_idxs.is_empty() {
                format!("効果: ベンチをトラッシュせずに {damage} ダメージ")
            } else {
                let names: Vec<String> = in_play_idxs.iter().map(|idx| mine(*idx)).collect();
                format!(
                    "効果: {} をトラッシュして {damage} ダメージ",
                    names.join("、")
                )
            }
        }
        SimpleAction::DiscardToolsFromHandThenDamage { count, damage } => {
            format!("効果: 手札のポケモンのどうぐ {count} 枚をトラッシュして {damage} ダメージ")
        }
        SimpleAction::ApplyCardEffectToSelf { in_play_idx, .. } => {
            format!("効果: {} に効果を付ける", mine(*in_play_idx))
        }
        SimpleAction::ReturnPokemonToHand { in_play_idx } => {
            format!("効果: {} を手札に戻す", mine(*in_play_idx))
        }
        SimpleAction::ShuffleInPlayPokemonIntoDeck { in_play_idx } => {
            format!("効果: {} を山札に戻す", mine(*in_play_idx))
        }
    }
}

/// 両者の手札。
fn hand_cards(state: &State) -> [Vec<CardRef>; 2] {
    [0usize, 1].map(|player| state.hands[player].iter().map(CardRef::of).collect())
}

/// 盤面を切り出す。
fn snapshot(state: &State) -> ([Option<PokemonSnapshot>; 2], [Vec<PokemonSnapshot>; 2]) {
    let active = [0usize, 1].map(|player| {
        state.in_play_pokemon[player][0]
            .as_ref()
            .map(PokemonSnapshot::from_played)
    });
    let bench = [0usize, 1].map(|player| {
        state
            .enumerate_in_play_pokemon(player)
            .filter(|(index, _)| *index != 0)
            .map(|(_, pokemon)| PokemonSnapshot::from_played(pokemon))
            .collect()
    });
    (active, bench)
}

/// 1 試合を回し、行動ごとの記録を返す。
///
/// `deck_a` が先攻、`deck_b` が後攻。同じ引数なら必ず同じ記録になる。
///
/// # Errors
///
/// deckgym が試合中に panic した場合に [`GameError::EnginePanic`] を返す。
pub fn play_one_game_with_log(
    deck_a: &Deck,
    deck_b: &Deck,
    strategy_a: Strategy,
    strategy_b: Strategy,
    seed: u64,
) -> Result<Replay, GameError> {
    // 1 手ずつ進めながら記録する。deckgym には panic する箇所があるので捕まえる
    catch_unwind(AssertUnwindSafe(|| {
        let players = vec![
            strategy_a.build_player(deck_a.clone()),
            strategy_b.build_player(deck_b.clone()),
        ];
        let mut game = new_game(players, seed);
        let mut rng = correction_rng(seed);
        let mut last_turn = 0;

        let opening_hands = hand_cards(&game.get_state_clone());
        let mut steps = Vec::new();
        loop {
            let before = game.get_state_clone();
            if before.winner.is_some() {
                break;
            }

            let action = play_tick_with_corrections(&mut game, &mut last_turn, &mut rng);
            let after = game.get_state_clone();
            let (active, bench) = snapshot(&after);

            steps.push(ReplayStep {
                // 「ターンを終える」もそのターン側に付ける
                turn: before.turn_count,
                actor: action.actor,
                description: describe(&action.action, &before, action.actor),
                points: after.points,
                active,
                bench,
                hand_size: [after.hands[0].len(), after.hands[1].len()],
                hand: hand_cards(&after),
                stadium: after.active_stadium.as_ref().map(CardRef::of),
                stadium_owner: after.active_stadium_owner,
                deck_size: [after.decks[0].cards.len(), after.decks[1].cards.len()],
            });
        }

        let final_state = game.get_state_clone();
        Replay {
            steps,
            opening_hands,
            result: final_state.winner.into(),
            points: final_state.points,
            turns: final_state.turn_count,
        }
    }))
    .map_err(|payload| GameError::EnginePanic {
        message: crate::game::panic_message_of(&payload),
    })
}
