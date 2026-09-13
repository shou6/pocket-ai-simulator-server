//! 先読み方策 `l`。
//!
//! `p`（`crate::players::PocketPlayer`）は行動 1 つずつを静的な点数で選ぶため、
//! 「エネルギーを付けた直後に逃げて捨てる」「撃てないメガを前に置きに行く」のような
//! 手順の矛盾を見抜けない。ここでは候補手ごとに、その手を打った後の残りの手を `p` で
//! 番の終わりまで進め、できた盤面を評価して最初の手を選ぶ。
//!
//! - 読むのは自分の番の終わりまで。相手の番まで `p` で進める版も試したが、相手の手札を
//!   空にしても実際の手札を使っても 37 組の一致度は下がった（+0.608 → +0.541 / +0.556）
//! - 相手の手札・山札は読まない（評価は盤面と自分の手札だけを見る）
//! - 乱数はシードから決定的に導く（同じ局面なら同じ手）
//! - 途中で deckgym が panic した候補は捨てる

use std::fmt;
use std::panic::{AssertUnwindSafe, catch_unwind};

use deckgym::actions::{Action, SimpleAction};
use deckgym::models::Card;
use deckgym::players::{Player, PlayerCode, create_players};
use deckgym::state::PlayedCard;
use deckgym::{Deck, Game, State};
use rand::rngs::StdRng;
use rand::{Rng, SeedableRng};

use crate::game::{RuleEnforcingPlayer, apply_action_at_turn, apply_action_with_corrections};
use crate::players::{
    PlayerContext, PocketPlayer, TRAINER_USELESS_VALUE, ability_needs_active_spot,
    best_ready_damage, damage_to_defender_now, energy_missing, incoming_damage_to,
    is_bench_role_now, is_draw_or_search_card, knockout_points, max_hp_points,
    opponent_can_lock_active_energy, score_action,
};

/// ポイント 1 点の価値。盤面の他の要素より必ず大きくする。
const POINT_VALUE: f64 = 1000.0;

/// 勝敗が付いたときの価値。
const WIN_VALUE: f64 = 100_000.0;

/// 場のポケモン 1 匹の基礎価値（HP 満タン・たね）。
const POKEMON_VALUE: f64 = 60.0;

/// 進化段階 1 つあたりの加点。
const STAGE_VALUE: f64 = 40.0;

/// 繋ぎに付いているエネルギー 1 個の価値。
/// 繋ぎに付いたエネルギー 1 個の価値。
///
/// エネルギーは 1 ターンに 1 個しか供給されないので、逃げで捨てる 1 個は 1 ターンぶんの損。
/// 25 だと「付ければ撃てる」部分点（+67〜+83）に負け、壁のクイタランに付けてから捨てて
/// E0 の繋ぎへ逃げる手が残った（ユーザーの指摘）。100 で 37 組の一致度が +0.699 → +0.740（500 試合）
const ENERGY_VALUE: f64 = 100.0;

/// 主役（進化済み、または主役の系統の最終形）に付いているエネルギー 1 個の価値。
/// ワザのコストに達するまでの分だけ数える。繋ぎ 1 匹を守るより重い
/// （ユーザーの原則：繋ぎは捨ててベンチの主役を育てる）。
const MAIN_ENERGY_VALUE: f64 = 150.0;

/// 自分のバトル場が次の番に倒されるときの減点。
/// 1 点の繋ぎは「渡す前提」なので軽く、2 点・3 点は渡すポイントに応じて重い。
///
/// 重みは 37 組の一致度で追い込んだ（250 試合で 1 つずつ 3 段階、効いた 2 つを組み合わせて
/// 2000 試合で確認。相関 +0.608 → +0.703、平均絶対誤差 10.0% → 8.1%）。
/// 主役へのエネルギー 150 と倒される主役の減点 400 は動かすと悪化した。
const DYING_STALL_PENALTY: f64 = 75.0;
const DYING_PER_POINT_PENALTY: f64 = 400.0;

/// 相手のバトル場を次の番に倒せるときの加点（得るポイント × この値）。
const KILLABLE_OPPONENT_VALUE: f64 = 150.0;

/// 手札 1 枚の価値（上限つき）。
const HAND_CARD_VALUE: f64 = 10.0;

/// まだ撃てない主役をバトル場に置いていることの減点（あと 1 個あたり）。
///
/// 主役は裏で育ててから前に出すのが定石。前で作ると育つ前に削られる。
const UNREADY_MAIN_IN_FRONT_PENALTY: f64 = 60.0;

/// 相手がバトル場へのエネルギー付与を封じられるときに、上の減点へ掛ける倍率。
///
/// 封じられている間は前に置いた主役が永久に完成しない（ユーザーの指摘：
/// メガルカリオ ex を前で作り、Binding Snow を受け続けて E1 のまま削られ、
/// 最後は逃げてエネルギーを捨てていた）。
const ENERGY_LOCK_MULTIPLIER: f64 = 3.0;

/// 1 手番の探索で進める最大の行動数（無限ループの保険）。
const MAX_ROLLOUT_STEPS: usize = 40;

/// 候補 1 つあたりのロールアウト回数。
///
/// 複数回にしてシャッフルの運を平均すると、「引く前に逃げる」手順を直す効果はなく
/// （ロールアウトの半分でフレイムパッチを引くので平均でも逃げが勝つ）、速度だけ 4 分の 1 に
/// なった。手順は `best_draw_action` で直すので、ここは 1 回のまま
const ROLLOUTS_PER_ACTION: u64 = 1;

/// 1 手あたりのロールアウト回数。校正のあいだだけ `POCKET_ROLLOUTS` で上書きできる。
///
/// 再ビルドせずに値を振って相関を測るために置く。既定は `ROLLOUTS_PER_ACTION`。
fn rollouts_per_action() -> u64 {
    std::env::var("POCKET_ROLLOUTS")
        .ok()
        .and_then(|value| value.parse::<u64>().ok())
        .filter(|count| *count > 0)
        .unwrap_or(ROLLOUTS_PER_ACTION)
}

/// 探索に使う deckgym の `Game`。`apply_action` が公開されているのはこの経由だけ。
struct Scratch(Game<'static>);

impl fmt::Debug for Scratch {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("Scratch(Game)")
    }
}

/// 先読み方策。
#[derive(Debug)]
pub struct LookaheadPlayer {
    deck: Deck,
    inner: PocketPlayer,
    context: PlayerContext,
    scratch: Scratch,
}

impl LookaheadPlayer {
    /// デッキから方策を作る。
    #[must_use]
    pub fn new(deck: Deck) -> Self {
        // 探索用の Game。プレイヤーは使わない（状態を差し替えて apply_action だけ呼ぶ）
        let dummies = create_players(
            deck.clone(),
            deck.clone(),
            vec![PlayerCode::ET, PlayerCode::ET],
        );
        let scratch = Scratch(Game::new(dummies, 0));
        Self {
            inner: PocketPlayer::new(deck.clone()),
            context: PlayerContext::from_deck(&deck),
            deck,
            scratch,
        }
    }

    /// 候補手を打った後、番の終わりまで `p` で進めた盤面の評価。
    fn rollout_value(
        &mut self,
        state: &State,
        me: usize,
        first: &Action,
        seed: u64,
    ) -> Option<f64> {
        let mut rng = StdRng::seed_from_u64(seed);
        let turn = state.turn_count;
        let scratch = &mut self.scratch.0;
        let inner = &mut self.inner;
        let context = &self.context;
        scratch.set_state(state.clone());
        // 盤面だけ巻き戻しても乱数は巻き戻らない。再シードしないと候補ごとに
        // 別のシャッフル・別のドローで比べることになり、手の良し悪しではなく
        // 引きの当たり外れを測ってしまう（共通乱数法）
        scratch.set_rng_seed(seed);
        let result = catch_unwind(AssertUnwindSafe(|| {
            apply_action_with_corrections(scratch, state, first, &mut rng);
            // 自分の番の残りを `p` で進める。
            // 状態は `state()` で借りて読む（`get_state_clone` は 1 手ごとに
            // 状態をまるごと複製するので、探索の内側では重い）
            for _ in 0..MAX_ROLLOUT_STEPS {
                {
                    let next = scratch.state();
                    if next.winner.is_some() || next.turn_count != turn || next.current_player != me
                    {
                        break;
                    }
                }
                let (actor, actions) = scratch.state().generate_possible_actions();
                if actor != me || actions.is_empty() {
                    break;
                }
                let action = {
                    let next = scratch.state();
                    let legal = RuleEnforcingPlayer::legal_actions(next, &actions);
                    if legal.len() == 1 {
                        legal[0].clone()
                    } else {
                        inner.decision_fn(&mut rng, next, &legal)
                    }
                };
                apply_action_at_turn(scratch, turn, &action, &mut rng);
            }

            evaluate_state(context, scratch.state(), me)
        }));
        result.ok()
    }
}

/// ロールアウトに使うシード。
///
/// **候補の位置（`index`）を混ぜない。** 候補ごとに違う乱数で比べると、
/// 「A のほうが良い」という判定に手の良し悪しではなく引きの当たり外れが混入する。
/// 同じ乱数の下で比べれば、その差が消える（共通乱数法）。
///
/// # 引数
///
/// - `base_seed`：その番の乱数の素
/// - `_index`：候補の位置。**使わない**（同じ番の候補は同じ乱数で比べる）
/// - `k`：その候補について何回目のロールアウトか
#[must_use]
pub fn rollout_seed(base_seed: u64, _index: u64, k: u64) -> u64 {
    base_seed ^ k.wrapping_mul(0x9E37_79B9_7F4A_7C15)
}

/// その特性は ex のワザを無効化するか（オドリドリの Safeguard など）。
///
/// カード名ではなく効果文で判定するので、同じ特性を持つ他のカードにも効く。
#[must_use]
pub fn blocks_ex_attacks(effect: &str) -> bool {
    let lowered = effect.to_lowercase();
    lowered.contains("prevent all damage") && lowered.contains("pokémon ex")
}

/// 劣る進化先でも、進化しないと倒されるなら進化するか。
///
/// ユーザーの補足：「何もしないと相手のワザで気絶すると分かる場合、
/// HP 管理的に進化せざるを得ない場合は進化するしかない」。
/// 進化しても倒されるなら意味がないので、耐えられる場合だけ。
///
/// # 引数
///
/// - `remaining_hp`：いまの残り HP
/// - `incoming_damage`：相手の次のワザの見込み打点
/// - `evolved_hp`：進化後の最大 HP
#[must_use]
pub fn evolution_is_forced(remaining_hp: u32, incoming_damage: u32, evolved_hp: u32) -> bool {
    incoming_damage >= remaining_hp && incoming_damage < evolved_hp
}

/// カードの表記ダメージの最大値。エネルギーが足りるかは見ない。
fn printed_damage(card: &Card) -> u32 {
    let Card::Pokemon(pokemon) = card else {
        return 0;
    };
    pokemon
        .attacks
        .iter()
        .map(|attack| attack.fixed_damage)
        .max()
        .unwrap_or(0)
}

/// 同じ進化元から選べる進化先のうち、劣るものを外す。
///
/// ユーザーの指摘：非 ex のビークインは「ex のワザを無効化するオドリドリ対策」で、
/// それ以外に進化するメリットはない。相手にオドリドリがいなくても 32% で進化していた。
///
/// 打点は ex も非 ex も表記 70 で同じなので、**HP と打点の両方で上回られていれば外す**。
/// 「倒されたときに渡す点数」は判定に入れない（点数を惜しんで選ぶカードではないため）。
///
/// # 引数
///
/// - `candidates`：(進化させる場所, 最大打点, 最大 HP) の並び。進化以外は場所を `usize::MAX` に
///
/// # 戻り値
///
/// 各候補を残すかどうか。
#[must_use]
pub fn keep_evolution_candidates(candidates: &[(usize, u32, u32)]) -> Vec<bool> {
    candidates
        .iter()
        .map(|(target, damage, hp)| {
            if *target == usize::MAX {
                return true;
            }
            // 同じ場所への進化で、打点も HP も自分以上、かつどちらかが上回るものがあるか
            !candidates
                .iter()
                .any(|(other_target, other_damage, other_hp)| {
                    other_target == target
                        && other_damage >= damage
                        && other_hp >= hp
                        && (other_damage > damage || other_hp > hp)
                })
        })
        .collect()
}

/// そのカードが「特性を活かす」役割か。
///
/// `attached_energy` は付いているエネルギーの個数。撃てるだけ乗っていれば
/// アタッカーとして扱い、除外しない。
fn card_is_bench_role(card: &Card, attached_energy: usize) -> bool {
    let Card::Pokemon(pokemon) = card else {
        return false;
    };
    let Some(ability) = pokemon.ability.as_ref() else {
        return false;
    };
    let cheapest = pokemon
        .attacks
        .iter()
        .map(|attack| attack.energy_required.len())
        .min()
        .unwrap_or(usize::MAX);
    is_bench_role_now(
        true,
        ability_needs_active_spot(&ability.effect),
        cheapest,
        attached_energy,
    )
}

/// 初期配置の候補から、「特性を活かす」役割のポケモンを外す。
///
/// `l` は静的な点数を見ずロールアウトだけで選ぶので、減点しても挙動が変わらない
/// （静的評価に「特性の働く場所」を足したが初手は変わらなかった）。
/// **候補から外すことだけが効く。**
///
/// 他に選べるたねがない場合は外さない（バトル場は必ず埋めなければならない）。
///
/// # 引数
///
/// - `candidates`：(バトル場に出す手か, 「特性を活かす」役割か) の並び
///
/// # 戻り値
///
/// 各候補を残すかどうか。
#[must_use]
pub fn keep_setup_candidates(candidates: &[(bool, bool)]) -> Vec<bool> {
    let has_alternative = candidates
        .iter()
        .any(|(is_setup_active, bench_role)| *is_setup_active && !*bench_role);
    candidates
        .iter()
        .map(|(is_setup_active, bench_role)| !(has_alternative && *is_setup_active && *bench_role))
        .collect()
}

/// その手を先読みの候補に入れる価値があるか。
///
/// 先読みは 1 手につきロールアウト 1 回で比べるので、評価はノイズだらけになる。
/// 静的な点数は同値のときの決着にしか使っていなかったため、**無駄と分かっている手が
/// ノイズで「番を終える」に勝ってしまう**ことがあった。ユーザーの指摘で見つかった例は
/// 自分で出したスタジアムをフィールドブロアーで壊す手
/// （`docs/analysis/altaria-vs-blaziken/report-s14.html` の 7〜9 手目）。
///
/// 静的な点数が「無駄」の水準に達している手だけを外す。判断が付かない手は残す。
#[must_use]
pub fn is_worth_considering(static_score: f64) -> bool {
    static_score > TRAINER_USELESS_VALUE
}

/// 場を整える手か。手札を使って場を変える手で、手札を戻すドローより先に打つ。
fn is_board_building(action: &SimpleAction) -> bool {
    match action {
        SimpleAction::Evolve { .. } | SimpleAction::Place(..) => true,
        // ふしぎなアメ・どうぐ・フレイムパッチなど。引く手はここに含めない
        SimpleAction::Play { trainer_card } => !is_draw_or_search_card(&trainer_card.name),
        _ => false,
    }
}

/// 引く手（ドロー・サーチ）か。
fn is_drawing(action: &SimpleAction) -> bool {
    match action {
        SimpleAction::Play { trainer_card } => is_draw_or_search_card(&trainer_card.name),
        _ => false,
    }
}

impl LookaheadPlayer {
    /// 候補手の評価。シャッフルの運で決まらないよう、シードを変えて複数回ロールアウトし平均する。
    fn mean_rollout_value(
        &mut self,
        state: &State,
        me: usize,
        action: &Action,
        base_seed: u64,
        index: u64,
    ) -> Option<f64> {
        let values: Vec<f64> = (0..rollouts_per_action())
            .filter_map(|k| {
                let seed = rollout_seed(base_seed, index, k);
                self.rollout_value(state, me, action, seed)
            })
            .collect();
        if values.is_empty() {
            None
        } else {
            Some(values.iter().sum::<f64>() / values.len() as f64)
        }
    }
}

impl Player for LookaheadPlayer {
    fn decision_fn(
        &mut self,
        rng: &mut StdRng,
        state: &State,
        possible_actions: &[Action],
    ) -> Action {
        let me = possible_actions[0].actor;
        if possible_actions.len() == 1 {
            return possible_actions[0].clone();
        }
        // 同じ局面で同じ手を選ぶよう、探索の乱数はシードから導く
        let base_seed: u64 = rng.r#gen();

        // 明らかに選ぶべきでない手を候補から外す（ユーザーの指摘）。
        // 点数を下げても `l` は見ないので、候補そのものを外すしかない
        let narrowed = Self::without_bench_role_actives(state, me, possible_actions);
        let narrowed = self.without_dominated_evolutions(state, me, &narrowed);
        let possible_actions: &[Action] = &narrowed;

        // 手は 3 段階に分けて打つ。
        // 1. 場を整える手（進化・展開・引く手以外のトレーナーズ）。手札を戻すドローで
        //    進化パーツを流す前に済ませる（ユーザーの指摘：ベンチにアチャモがいて
        //    ふしぎなアメとメガバシャーモ ex を持ちながらモノマネむすめを打っていた）
        // 2. 引く手（ドロー・サーチ）。エネルギーの付け先や逃げる先を決める前に引く
        //    （ユーザーの指摘：引ける前提で先に逃げてエネルギーを捨てていた）
        // 3. 残り（エネルギーの付け先・逃げる・ワザ）。手札の中身では取り返せない手
        for group in [is_board_building as fn(&SimpleAction) -> bool, is_drawing] {
            if let Some(action) = self.best_in_group(state, me, possible_actions, base_seed, group)
            {
                return action.clone();
            }
        }

        match self.best_action(state, me, possible_actions, base_seed) {
            Some(action) => action.clone(),
            None => self.inner.decision_fn(rng, state, possible_actions),
        }
    }

    fn get_deck(&self) -> Deck {
        self.deck.clone()
    }
}

impl LookaheadPlayer {
    /// バトル場が空のとき、「特性を活かす」役割のポケモンを候補から外す。
    ///
    /// 対象は初期配置（手札からの `Place`）と、気絶後の昇格（ベンチからの `Activate`）。
    /// 他に選べるポケモンがなければ外さない。
    fn without_bench_role_actives(
        state: &State,
        me: usize,
        possible_actions: &[Action],
    ) -> Vec<Action> {
        if state.in_play_pokemon[me][0].is_some() {
            return possible_actions.to_vec();
        }
        let flags: Vec<(bool, bool)> = possible_actions
            .iter()
            .map(|action| match &action.action {
                // 手札から初期配置する手。エネルギーはまだ付いていない
                SimpleAction::Place(card, 0) => (true, card_is_bench_role(card, 0)),
                // 気絶したあと、ベンチから昇格させる手。乗っているエネルギーを見る
                SimpleAction::Activate {
                    player,
                    in_play_idx,
                } if *player == me => {
                    let pokemon = state.in_play_pokemon[me][*in_play_idx].as_ref();
                    let flag = pokemon.is_some_and(|pokemon| {
                        card_is_bench_role(&pokemon.card, pokemon.attached_energy.len())
                    });
                    (true, flag)
                }
                _ => (false, false),
            })
            .collect();
        let keep = keep_setup_candidates(&flags);
        possible_actions
            .iter()
            .zip(keep)
            .filter(|(_, keep)| *keep)
            .map(|(action, _)| action.clone())
            .collect()
    }

    /// 同じ進化元に対して劣る進化先を候補から外す。
    ///
    /// 非 ex のビークインのように、打点が同じで HP だけ低い進化先がある
    /// （ユーザーの説明では「ex のワザを無効化するオドリドリ対策」の札）。
    fn without_dominated_evolutions(
        &self,
        state: &State,
        me: usize,
        possible_actions: &[Action],
    ) -> Vec<Action> {
        let opponent = (me + 1) % 2;
        // 相手の場に ex のワザを無効化する特性がいるなら、劣る進化先にも意味がある
        let ex_blocked =
            state
                .enumerate_in_play_pokemon(opponent)
                .any(|(_, pokemon)| match &pokemon.card {
                    Card::Pokemon(card) => card
                        .ability
                        .as_ref()
                        .is_some_and(|ability| blocks_ex_attacks(&ability.effect)),
                    Card::Trainer(_) => false,
                });
        if ex_blocked {
            return possible_actions.to_vec();
        }

        possible_actions
            .iter()
            .filter(|action| {
                let SimpleAction::Evolve {
                    evolution,
                    in_play_idx,
                    ..
                } = &action.action
                else {
                    return true;
                };
                let Card::Pokemon(evolved) = evolution else {
                    return true;
                };
                let Some(from) = evolved.evolves_from.as_ref() else {
                    return true;
                };
                let damage = printed_damage(evolution);
                let hp = max_hp_points(evolution);
                // デッキに、打点も HP も上回る進化先が入っているか
                let better = self
                    .context
                    .evolution_options
                    .get(from)
                    .is_some_and(|options| {
                        options.iter().any(|(other_damage, other_hp)| {
                            *other_damage >= damage
                                && *other_hp >= hp
                                && (*other_damage > damage || *other_hp > hp)
                        })
                    });
                if !better {
                    return true;
                }
                // 進化しないと倒される場面なら、劣る進化先でも進化する
                state.in_play_pokemon[me][*in_play_idx]
                    .as_ref()
                    .is_some_and(|pokemon| {
                        evolution_is_forced(
                            pokemon.get_remaining_hp(),
                            incoming_damage_to(state, opponent, pokemon),
                            hp,
                        )
                    })
            })
            .cloned()
            .collect()
    }

    /// 候補の中で最も評価の高い手。すべてのロールアウトが失敗したら `None`。
    fn best_action<'a>(
        &mut self,
        state: &State,
        me: usize,
        possible_actions: &'a [Action],
        base_seed: u64,
    ) -> Option<&'a Action> {
        let mut best: Option<(f64, f64, &Action)> = None;
        for (index, action) in possible_actions.iter().enumerate() {
            let Some(value) = self.mean_rollout_value(state, me, action, base_seed, index as u64)
            else {
                continue;
            };
            // 同点なら `p` の静的な点数で決める
            let tiebreak = score_action(&self.context, state, me, &action.action);
            let better = match best {
                None => true,
                Some((v, t, _)) => {
                    value > v + f64::EPSILON || ((value - v).abs() <= f64::EPSILON && tiebreak > t)
                }
            };
            if better {
                best = Some((value, tiebreak, action));
            }
        }
        best.map(|(_, _, action)| action)
    }

    /// その段階の手のうち最も良いもの。段階に手がない、または番を終えるほうが良いなら `None`。
    ///
    /// 候補に「番を終える」を混ぜて先読みするので、無駄なトレーナーズやどうぐを
    /// 打たずに済ませられる
    fn best_in_group<'a>(
        &mut self,
        state: &State,
        me: usize,
        possible_actions: &'a [Action],
        base_seed: u64,
        belongs: fn(&SimpleAction) -> bool,
    ) -> Option<&'a Action> {
        let candidates: Vec<Action> = possible_actions
            .iter()
            .filter(|action| {
                if matches!(action.action, SimpleAction::EndTurn) {
                    return true;
                }
                belongs(&action.action)
                    && is_worth_considering(score_action(&self.context, state, me, &action.action))
            })
            .cloned()
            .collect();
        if !candidates
            .iter()
            .any(|action| !matches!(action.action, SimpleAction::EndTurn))
        {
            return None;
        }
        let chosen = self.best_action(state, me, &candidates, base_seed)?;
        if matches!(chosen.action, SimpleAction::EndTurn) {
            return None;
        }
        possible_actions.iter().find(|action| *action == chosen)
    }
}

/// 盤面の評価（`me` から見て高いほど良い）。
///
/// ポイント差を最優先に、場のポケモンの HP とエネルギー（主役への投資は重く）、
/// 次の番の脅威（1 点の繋ぎは軽く、2 点・3 点は重く）と好機、手札を見る。
/// 相手の手札・山札は読まない。
#[must_use]
pub fn evaluate_state(ctx: &PlayerContext, state: &State, me: usize) -> f64 {
    let opponent = (me + 1) % 2;
    if let Some(winner) = &state.winner {
        return match winner {
            deckgym::state::GameOutcome::Win(player) if *player == me => WIN_VALUE,
            deckgym::state::GameOutcome::Win(_) => -WIN_VALUE,
            deckgym::state::GameOutcome::Tie => 0.0,
        };
    }

    let mut value = (f64::from(state.points[me]) - f64::from(state.points[opponent])) * POINT_VALUE;
    value += board_value(ctx, state, me) - board_value(ctx, state, opponent);

    // 自分のバトル場：次の番に倒されるか、撃てるか
    if let Some(active) = state.in_play_pokemon[me][0].as_ref() {
        let incoming = incoming_damage_to(state, opponent, active);
        let remaining = active.get_remaining_hp();
        let points = knockout_points(&active.card);
        if incoming >= remaining {
            value -= if points <= 1.0 {
                DYING_STALL_PENALTY
            } else {
                DYING_PER_POINT_PENALTY * points
            };
        } else if remaining > 0 {
            value -= 50.0 * f64::from(incoming) / f64::from(remaining) * points;
        }
        // まだ撃てない主役を前に置いていると、育つ前に削られる。相手がバトル場への
        // エネルギー付与を封じられるなら、次の番も乗らないので更に重く見る
        if ctx.is_main_line(&active.card) && best_ready_damage(active) == 0 {
            let lock = if opponent_can_lock_active_energy(state, opponent) {
                ENERGY_LOCK_MULTIPLIER
            } else {
                1.0
            };
            value -= UNREADY_MAIN_IN_FRONT_PENALTY * energy_to_attack(active) * lock;
        }
        if let Some(defender) = state.in_play_pokemon[opponent][0].as_ref() {
            // 効果文まで読んだ打点。基礎値だけを見ると「手札の枚数ぶん」のような
            // ワザを大幅に過小評価する（ユーザーの指摘：ハンドキネシスを 20 と見ていた）。
            // 弱点と状態異常はこの関数の中で見ている
            let damage = damage_to_defender_now(state, me, true);
            let their_remaining = defender.get_remaining_hp();
            if damage >= their_remaining && damage > 0 {
                value += KILLABLE_OPPONENT_VALUE * knockout_points(&defender.card);
            } else if their_remaining > 0 {
                value += 100.0 * f64::from(damage) / f64::from(their_remaining)
                    * knockout_points(&defender.card);
            }
        }
    } else {
        // バトル場が空（倒された直後で昇格前）
        value -= POINT_VALUE;
    }

    value += HAND_CARD_VALUE * (state.hands[me].len().min(6) as f64);
    value
}

/// 最も安いワザを撃つまでに、あと何個エネルギーが要るか。
fn energy_to_attack(pokemon: &PlayedCard) -> f64 {
    let Card::Pokemon(card) = &pokemon.card else {
        return 0.0;
    };
    let missing = card
        .attacks
        .iter()
        .map(|attack| energy_missing(&attack.energy_required, &pokemon.attached_energy))
        .min()
        .unwrap_or(0);
    f64::from(u32::try_from(missing).unwrap_or(u32::MAX))
}

/// 片方の場の価値。HP 割合と進化段階、ワザに使えるエネルギー（主役は重く）。
fn board_value(ctx: &PlayerContext, state: &State, player: usize) -> f64 {
    state
        .enumerate_in_play_pokemon(player)
        .map(|(_, pokemon)| {
            let max_hp = max_hp_points(&pokemon.card);
            let hp_ratio = if max_hp == 0 {
                0.0
            } else {
                f64::from(pokemon.get_remaining_hp()) / f64::from(max_hp)
            };
            let (evolution_stage, attack_cost) = match &pokemon.card {
                Card::Pokemon(card) => (
                    f64::from(card.stage),
                    card.attacks
                        .iter()
                        .map(|a| a.energy_required.len())
                        .max()
                        .unwrap_or(0),
                ),
                Card::Trainer(_) => (0.0, 0),
            };
            let useful_energy = pokemon.attached_energy.len().min(attack_cost) as f64;
            let is_main = ctx.is_main_line(&pokemon.card) && evolution_stage > 0.0
                || (ctx.is_main_line(&pokemon.card)
                    && attack_cost >= 2
                    && !ctx.is_evolution_basic(&pokemon.card));
            let energy = if is_main {
                MAIN_ENERGY_VALUE * useful_energy
            } else {
                ENERGY_VALUE * useful_energy
            };
            (POKEMON_VALUE + STAGE_VALUE * evolution_stage) * (0.5 + 0.5 * hp_ratio) + energy
        })
        .sum()
}
