use std::time::Duration;

use anyhow::{anyhow, Context, Result};
use eventsource_stream::Eventsource;
use futures_util::StreamExt;
use reqwest::header::{AUTHORIZATION, CONTENT_TYPE};
use reqwest::Client;
use serde::de::DeserializeOwned;
use serde::Deserialize;
use serde_json::Value;
use tokio::sync::mpsc::UnboundedSender;
use tokio::time::sleep;

use crate::state_aggregator::{GroupContext, LedgerEvent};

/// Connection info needed by the SSE client.
/// Decoupled from DesktopConfig so it can accept a ResolvedConnection.
#[derive(Debug, Clone)]
pub struct ConnectionInfo {
    pub api_base_url: String,
    pub group_id: String,
    pub auth_token: String,
}

impl ConnectionInfo {
    pub fn from_resolved(conn: &crate::bootstrap::ResolvedConnection) -> Self {
        Self {
            api_base_url: conn.api_base_url(),
            group_id: conn.group_id.clone(),
            auth_token: conn.auth_token.clone(),
        }
    }

}

#[derive(Debug, Clone)]
pub struct DesktopApiClient {
    client: Client,
    info: ConnectionInfo,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum StreamSignal {
    Connected,
    ContextChanged,
    ObligationChanged,
    GroupStateChanged,
    GroupStopped,
    Reconnecting { reason: String, delay: Duration },
}

#[derive(Debug, Deserialize)]
struct ApiEnvelope<T> {
    ok: bool,
    result: T,
}

#[derive(Debug, Deserialize)]
struct GroupShowResult {
    group: GroupDoc,
}

#[derive(Debug, Deserialize)]
struct GroupDoc {
    #[serde(default)]
    title: String,
    #[serde(default)]
    state: String,
}

#[derive(Debug, Deserialize)]
struct LedgerTailResult {
    #[serde(default)]
    events: Vec<LedgerEvent>,
}

impl DesktopApiClient {
    pub fn new(info: ConnectionInfo) -> Result<Self> {
        let client = Client::builder()
            .connect_timeout(Duration::from_secs(10))
            .build()
            .context("failed to build reqwest client")?;
        Ok(Self { client, info })
    }

    pub async fn fetch_context(&self) -> Result<GroupContext> {
        let url = format!(
            "{}/api/v1/groups/{}/context",
            self.info.api_base_url, self.info.group_id
        );
        let response: ApiEnvelope<GroupContext> = self.get_json(&url).await?;
        if !response.ok {
            return Err(anyhow!("context endpoint returned ok=false"));
        }
        Ok(response.result)
    }

    pub async fn fetch_group_title(&self) -> Result<Option<String>> {
        let url = format!(
            "{}/api/v1/groups/{}",
            self.info.api_base_url, self.info.group_id
        );
        let response: ApiEnvelope<GroupShowResult> = self.get_json(&url).await?;
        if !response.ok {
            return Ok(None);
        }
        let title = response.result.group.title.trim().to_string();
        if title.is_empty() {
            Ok(None)
        } else {
            Ok(Some(title))
        }
    }

    pub async fn fetch_group_state(&self) -> Result<String> {
        let url = format!(
            "{}/api/v1/groups/{}",
            self.info.api_base_url, self.info.group_id
        );
        let response: ApiEnvelope<GroupShowResult> = self.get_json(&url).await?;
        if !response.ok {
            return Err(anyhow!("group endpoint returned ok=false"));
        }
        Ok(response.result.group.state)
    }

    pub async fn fetch_recent_obligations(&self, lines: usize) -> Result<Vec<LedgerEvent>> {
        let url = format!(
            "{}/api/v1/groups/{}/ledger/tail?lines={}&with_obligation_status=true",
            self.info.api_base_url, self.info.group_id, lines
        );
        let response: ApiEnvelope<LedgerTailResult> = self.get_json(&url).await?;
        if !response.ok {
            return Err(anyhow!("ledger tail endpoint returned ok=false"));
        }
        Ok(response.result.events)
    }

    pub async fn run_stream_loop(self, tx: UnboundedSender<StreamSignal>) {
        let mut delay = Duration::from_secs(1);

        loop {
            let reconnect_reason = match self.stream_once(&tx).await {
                Ok(()) => "SSE stream closed".to_string(),
                Err(error) => error.to_string(),
            };
            let _ = tx.send(StreamSignal::Reconnecting {
                reason: reconnect_reason,
                delay,
            });
            sleep(delay).await;
            delay = std::cmp::min(delay.saturating_mul(2), Duration::from_secs(30));
        }
    }

    async fn stream_once(&self, tx: &UnboundedSender<StreamSignal>) -> Result<()> {
        let url = format!(
            "{}/api/v1/groups/{}/ledger/stream",
            self.info.api_base_url, self.info.group_id
        );
        let response = self
            .authorized(self.client.get(url))
            .header(CONTENT_TYPE, "text/event-stream")
            .send()
            .await
            .context("failed to connect ledger stream")?
            .error_for_status()
            .context("ledger stream returned error status")?;

        let _ = tx.send(StreamSignal::Connected);

        let mut stream = response.bytes_stream().eventsource();
        while let Some(event) = stream.next().await {
            let event = event.context("failed to parse SSE event")?;
            if event.event != "ledger" {
                continue;
            }

            let ledger_event: LedgerEvent =
                serde_json::from_str(&event.data).context("failed to decode ledger event")?;
            if let Some(signal) = classify_ledger_event(&ledger_event) {
                let _ = tx.send(signal);
            }
        }

        Ok(())
    }

    async fn get_json<T>(&self, url: &str) -> Result<T>
    where
        T: DeserializeOwned,
    {
        self.authorized(self.client.get(url))
            .send()
            .await
            .with_context(|| format!("failed GET {}", url))?
            .error_for_status()
            .with_context(|| format!("non-success GET {}", url))?
            .json::<T>()
            .await
            .with_context(|| format!("failed decoding JSON from {}", url))
    }

    fn authorized(&self, builder: reqwest::RequestBuilder) -> reqwest::RequestBuilder {
        let token = self.info.auth_token.trim();
        if token.is_empty() {
            builder
        } else {
            builder.header(AUTHORIZATION, format!("Bearer {}", token))
        }
    }
}

fn classify_ledger_event(event: &LedgerEvent) -> Option<StreamSignal> {
    match event.kind.as_str() {
        "context.sync" => Some(StreamSignal::ContextChanged),
        "chat.ack" | "chat.read" => Some(StreamSignal::ObligationChanged),
        "chat.message" => {
            let reply_required = event
                .data
                .get("reply_required")
                .and_then(Value::as_bool)
                .unwrap_or(false);
            if reply_required || event.by == "user" {
                Some(StreamSignal::ObligationChanged)
            } else {
                None
            }
        }
        "group.set_state" => Some(StreamSignal::GroupStateChanged),
        "group.stop" => Some(StreamSignal::GroupStopped),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::{classify_ledger_event, StreamSignal};
    use crate::state_aggregator::LedgerEvent;

    #[test]
    fn classifies_context_sync() {
        let event = LedgerEvent {
            kind: "context.sync".to_string(),
            ..Default::default()
        };
        assert_eq!(
            classify_ledger_event(&event),
            Some(StreamSignal::ContextChanged)
        );
    }

    #[test]
    fn classifies_reply_required_chat_message() {
        let event = LedgerEvent {
            kind: "chat.message".to_string(),
            data: json!({"reply_required": true}),
            ..Default::default()
        };
        assert_eq!(
            classify_ledger_event(&event),
            Some(StreamSignal::ObligationChanged)
        );
    }

    #[test]
    fn classifies_chat_ack() {
        let event = LedgerEvent {
            kind: "chat.ack".to_string(),
            ..Default::default()
        };
        assert_eq!(
            classify_ledger_event(&event),
            Some(StreamSignal::ObligationChanged)
        );
    }

    #[test]
    fn classifies_chat_read() {
        let event = LedgerEvent {
            kind: "chat.read".to_string(),
            ..Default::default()
        };
        assert_eq!(
            classify_ledger_event(&event),
            Some(StreamSignal::ObligationChanged)
        );
    }

    #[test]
    fn classifies_user_chat_message() {
        let event = LedgerEvent {
            kind: "chat.message".to_string(),
            by: "user".to_string(),
            data: json!({"reply_required": false}),
            ..Default::default()
        };
        assert_eq!(
            classify_ledger_event(&event),
            Some(StreamSignal::ObligationChanged)
        );
    }

    #[test]
    fn ignores_unknown_event_kind() {
        let event = LedgerEvent {
            kind: "file.upload".to_string(),
            ..Default::default()
        };
        assert_eq!(classify_ledger_event(&event), None);
    }

    #[test]
    fn classifies_group_set_state() {
        let event = LedgerEvent {
            kind: "group.set_state".to_string(),
            ..Default::default()
        };
        assert_eq!(
            classify_ledger_event(&event),
            Some(StreamSignal::GroupStateChanged)
        );
    }

    #[test]
    fn classifies_group_stop() {
        let event = LedgerEvent {
            kind: "group.stop".to_string(),
            ..Default::default()
        };
        assert_eq!(
            classify_ledger_event(&event),
            Some(StreamSignal::GroupStopped)
        );
    }

    #[test]
    fn ignores_regular_chat_message() {
        let event = LedgerEvent {
            kind: "chat.message".to_string(),
            by: "peer-impl-1".to_string(),
            data: json!({"reply_required": false}),
            ..Default::default()
        };
        assert_eq!(classify_ledger_event(&event), None);
    }
}
