use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum CatState {
    NeedsYou,
    Busy,
    Working,
    Napping,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct AgentSummary {
    pub id: String,
    pub state: String,
    pub focus: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ActionItem {
    pub id: String,
    pub agent: String,
    pub summary: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ConnectionStatus {
    pub connected: bool,
    pub message: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct PanelDetails {
    pub team_name: String,
    pub agents: Vec<AgentSummary>,
    pub action_items: Vec<ActionItem>,
    pub connection: ConnectionStatus,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct CatStatePayload {
    pub state: CatState,
    pub details: PanelDetails,
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct GroupContext {
    #[serde(default)]
    pub coordination: Coordination,
    #[serde(default)]
    pub agent_states: Vec<AgentState>,
    #[serde(default)]
    pub attention: AttentionProjection,
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct Coordination {
    #[serde(default)]
    pub tasks: Vec<Task>,
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct AttentionProjection {
    #[serde(default)]
    pub waiting_user: Vec<Task>,
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct Task {
    #[serde(default)]
    pub id: String,
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub assignee: String,
    #[serde(default)]
    pub waiting_on: String,
    #[serde(default)]
    pub status: String,
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct AgentState {
    #[serde(default)]
    pub id: String,
    #[serde(default)]
    pub hot: AgentHotState,
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct AgentHotState {
    #[serde(default)]
    pub active_task_id: String,
    #[serde(default)]
    pub focus: String,
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct LedgerEvent {
    #[serde(default)]
    pub id: String,
    #[serde(default)]
    pub kind: String,
    #[serde(default)]
    pub by: String,
    #[serde(default)]
    pub data: Value,
    #[serde(default, rename = "_obligation_status")]
    pub obligation_status: BTreeMap<String, ObligationStatus>,
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct ObligationStatus {
    #[serde(default)]
    pub acked: bool,
    #[serde(default)]
    pub replied: bool,
    #[serde(default)]
    pub reply_required: bool,
}

#[derive(Debug, Clone)]
pub struct StateAggregator {
    team_name: String,
    context: Option<GroupContext>,
    pending_reply_required: BTreeMap<String, ActionItem>,
}

impl StateAggregator {
    pub fn new(team_name: String) -> Self {
        Self {
            team_name,
            context: None,
            pending_reply_required: BTreeMap::new(),
        }
    }

    pub fn set_team_name(&mut self, team_name: String) {
        if !team_name.trim().is_empty() {
            self.team_name = team_name.trim().to_string();
        }
    }

    pub fn replace_context(&mut self, context: GroupContext) {
        self.context = Some(context);
    }

    pub fn reconcile_obligations(&mut self, events: &[LedgerEvent]) {
        self.pending_reply_required.clear();

        for event in events {
            if event.kind != "chat.message" {
                continue;
            }

            let Some(user_status) = event.obligation_status.get("user") else {
                continue;
            };
            if !user_status.reply_required || user_status.acked || user_status.replied {
                continue;
            }

            let text = event
                .data
                .get("text")
                .and_then(Value::as_str)
                .unwrap_or("")
                .trim();
            if text.is_empty() {
                continue;
            }

            self.pending_reply_required.insert(
                event.id.clone(),
                ActionItem {
                    id: event.id.clone(),
                    agent: display_actor(&event.by),
                    summary: truncate_summary(text),
                },
            );
        }
    }

    pub fn payload(&self, connected: bool, message: impl Into<String>) -> CatStatePayload {
        let message = message.into();
        let context = self.context.clone().unwrap_or_default();
        let mut action_items = collect_waiting_user_tasks(&context);
        action_items.extend(self.pending_reply_required.values().cloned());

        let active_agents: Vec<&AgentState> = context
            .agent_states
            .iter()
            .filter(|agent| has_activity(agent))
            .collect();

        let overall_state = if !action_items.is_empty() {
            CatState::NeedsYou
        } else if active_agents.len() >= 2 {
            CatState::Busy
        } else if active_agents.len() == 1 {
            CatState::Working
        } else {
            CatState::Napping
        };

        let mut agents: Vec<AgentSummary> = context
            .agent_states
            .iter()
            .map(|agent| AgentSummary {
                id: agent.id.clone(),
                state: if has_activity(agent) {
                    if active_agents.len() >= 2 {
                        "busy".to_string()
                    } else {
                        "working".to_string()
                    }
                } else {
                    "napping".to_string()
                },
                focus: agent.hot.focus.clone(),
            })
            .collect();

        if overall_state == CatState::NeedsYou
            && !agents.iter().any(|agent| agent.state == "needs_you")
        {
            if let Some(first) = action_items.first() {
                agents.push(AgentSummary {
                    id: "attention".to_string(),
                    state: "needs_you".to_string(),
                    focus: first.summary.clone(),
                });
            }
        }

        CatStatePayload {
            state: overall_state,
            details: PanelDetails {
                team_name: self.team_name.clone(),
                agents,
                action_items,
                connection: ConnectionStatus { connected, message },
            },
        }
    }
}

fn collect_waiting_user_tasks(context: &GroupContext) -> Vec<ActionItem> {
    let mut waiting_user = if !context.attention.waiting_user.is_empty() {
        context.attention.waiting_user.clone()
    } else {
        context
            .coordination
            .tasks
            .iter()
            .filter(|task| {
                task.waiting_on.eq_ignore_ascii_case("user")
                    && !matches!(task.status.as_str(), "done" | "archived")
            })
            .cloned()
            .collect()
    };

    waiting_user
        .drain(..)
        .map(|task| ActionItem {
            id: format!("{}_waiting_on_user", task.id),
            agent: display_actor(&task.assignee),
            summary: truncate_summary(&task.title),
        })
        .collect()
}

fn has_activity(agent: &AgentState) -> bool {
    !agent.hot.active_task_id.trim().is_empty() || !agent.hot.focus.trim().is_empty()
}

fn display_actor(actor_id: &str) -> String {
    let trimmed = actor_id.trim();
    if trimmed.is_empty() {
        "system".to_string()
    } else {
        trimmed.to_string()
    }
}

fn truncate_summary(text: &str) -> String {
    const MAX_CHARS: usize = 96;
    let cleaned = text.trim().replace('\n', " ");
    if cleaned.chars().count() <= MAX_CHARS {
        cleaned
    } else {
        cleaned.chars().take(MAX_CHARS - 1).collect::<String>() + "…"
    }
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::{
        AgentHotState, AgentState, AttentionProjection, CatState, GroupContext, LedgerEvent,
        ObligationStatus, StateAggregator, Task,
    };

    #[test]
    fn derives_needs_you_from_waiting_user_task() {
        let mut aggregator = StateAggregator::new("cccc".to_string());
        aggregator.replace_context(GroupContext {
            attention: AttentionProjection {
                waiting_user: vec![Task {
                    id: "T100".to_string(),
                    title: "Need approval".to_string(),
                    assignee: "peer-impl-1".to_string(),
                    waiting_on: "user".to_string(),
                    status: "active".to_string(),
                }],
            },
            ..Default::default()
        });

        let payload = aggregator.payload(true, "connected");
        assert_eq!(payload.state, CatState::NeedsYou);
        assert_eq!(payload.details.action_items.len(), 1);
        assert_eq!(payload.details.action_items[0].id, "T100_waiting_on_user");
    }

    #[test]
    fn derives_busy_from_multiple_active_agents() {
        let mut aggregator = StateAggregator::new("cccc".to_string());
        aggregator.replace_context(GroupContext {
            agent_states: vec![
                AgentState {
                    id: "peer-impl-1".to_string(),
                    hot: AgentHotState {
                        active_task_id: "T1".to_string(),
                        focus: String::new(),
                    },
                },
                AgentState {
                    id: "peer-impl-2".to_string(),
                    hot: AgentHotState {
                        active_task_id: String::new(),
                        focus: "Review".to_string(),
                    },
                },
            ],
            ..Default::default()
        });

        let payload = aggregator.payload(true, "connected");
        assert_eq!(payload.state, CatState::Busy);
    }

    #[test]
    fn derives_working_from_single_active_agent() {
        let mut aggregator = StateAggregator::new("cccc".to_string());
        aggregator.replace_context(GroupContext {
            agent_states: vec![AgentState {
                id: "peer-impl-1".to_string(),
                hot: AgentHotState {
                    active_task_id: "T1".to_string(),
                    focus: "implementing feature".to_string(),
                },
            }],
            ..Default::default()
        });

        let payload = aggregator.payload(true, "connected");
        assert_eq!(payload.state, CatState::Working);
    }

    #[test]
    fn derives_napping_when_no_activity() {
        let aggregator = StateAggregator::new("cccc".to_string());
        let payload = aggregator.payload(true, "connected");
        assert_eq!(payload.state, CatState::Napping);
        assert!(payload.details.action_items.is_empty());
    }

    #[test]
    fn set_team_name_trims_whitespace() {
        let mut aggregator = StateAggregator::new("old".to_string());
        aggregator.set_team_name("  New Team  ".to_string());
        let payload = aggregator.payload(true, "ok");
        assert_eq!(payload.details.team_name, "New Team");
    }

    #[test]
    fn set_team_name_ignores_empty() {
        let mut aggregator = StateAggregator::new("original".to_string());
        aggregator.set_team_name("   ".to_string());
        let payload = aggregator.payload(true, "ok");
        assert_eq!(payload.details.team_name, "original");
    }

    #[test]
    fn acked_reply_required_is_not_pending() {
        let mut aggregator = StateAggregator::new("cccc".to_string());
        aggregator.reconcile_obligations(&[LedgerEvent {
            id: "evt1".to_string(),
            kind: "chat.message".to_string(),
            by: "peer-reviewer".to_string(),
            data: json!({ "text": "Please confirm", "reply_required": true }),
            obligation_status: [(
                "user".to_string(),
                ObligationStatus {
                    acked: true,
                    replied: false,
                    reply_required: true,
                },
            )]
            .into_iter()
            .collect(),
        }]);

        let payload = aggregator.payload(true, "connected");
        assert_eq!(payload.state, CatState::Napping);
        assert!(payload.details.action_items.is_empty());
    }

    #[test]
    fn connection_status_reflects_payload_args() {
        let aggregator = StateAggregator::new("test".to_string());
        let connected = aggregator.payload(true, "all good");
        assert!(connected.details.connection.connected);
        assert_eq!(connected.details.connection.message, "all good");

        let disconnected = aggregator.payload(false, "reconnecting in 5s");
        assert!(!disconnected.details.connection.connected);
    }

    #[test]
    fn reconciles_reply_required_events() {
        let mut aggregator = StateAggregator::new("cccc".to_string());
        aggregator.reconcile_obligations(&[LedgerEvent {
            id: "evt1".to_string(),
            kind: "chat.message".to_string(),
            by: "peer-reviewer".to_string(),
            data: json!({ "text": "Please confirm this patch", "reply_required": true }),
            obligation_status: [(
                "user".to_string(),
                ObligationStatus {
                    acked: false,
                    replied: false,
                    reply_required: true,
                },
            )]
            .into_iter()
            .collect(),
        }]);

        let payload = aggregator.payload(true, "connected");
        assert_eq!(payload.state, CatState::NeedsYou);
        assert_eq!(payload.details.action_items[0].id, "evt1");
        assert_eq!(payload.details.action_items[0].agent, "peer-reviewer");
    }
}
