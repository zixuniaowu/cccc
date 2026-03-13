//! Connection types and local bootstrap for pet window runtimes.

use anyhow::{anyhow, Context, Result};
use reqwest::header::AUTHORIZATION;
use serde::Deserialize;

use crate::config::DesktopConfig;
use crate::token_store;

/// A fully resolved connection ready to spawn a pet-window runtime.
#[derive(Debug, Clone)]
pub struct ResolvedConnection {
    pub group_id: String,
    pub group_title: String,
    pub daemon_url: String,
    pub auth_token: String,
}

impl ResolvedConnection {
    pub fn api_base_url(&self) -> String {
        self.daemon_url.trim_end_matches('/').to_string()
    }

    pub fn team_label(&self) -> String {
        if self.group_title.is_empty() {
            self.group_id.clone()
        } else {
            self.group_title.clone()
        }
    }
}

// ---------------------------------------------------------------------------
// Local bootstrap: access_tokens.yaml → groups API → desktop_pet_enabled
// ---------------------------------------------------------------------------

#[derive(Debug, Deserialize)]
struct ApiEnvelope<T> {
    ok: bool,
    result: T,
}

#[derive(Debug, Deserialize)]
struct GroupsResult {
    #[serde(default)]
    groups: Vec<GroupEntry>,
}

#[derive(Debug, Deserialize)]
struct GroupEntry {
    group_id: String,
    #[serde(default)]
    title: String,
    #[serde(default)]
    running: bool,
}

#[derive(Debug, Deserialize)]
struct SettingsResult {
    #[serde(default)]
    settings: SettingsDoc,
}

#[derive(Debug, Default, Deserialize)]
struct SettingsDoc {
    #[serde(default)]
    desktop_pet_enabled: Option<bool>,
}

/// Attempt local bootstrap: discover token from `access_tokens.yaml`,
/// query daemon for groups with `desktop_pet_enabled`, return connections.
pub async fn bootstrap_local(config: &DesktopConfig) -> Result<Vec<ResolvedConnection>> {
    let discovered = token_store::discover_local_token()
        .ok_or_else(|| anyhow!("no usable token in access_tokens.yaml"))?;

    let daemon_url = config.daemon_url.trim_end_matches('/').to_string();
    let token = &discovered.token;

    let client = reqwest::Client::builder()
        .connect_timeout(std::time::Duration::from_secs(5))
        .build()
        .context("failed to build HTTP client")?;

    // Fetch all groups
    let groups_url = format!("{}/api/v1/groups", daemon_url);
    let resp: ApiEnvelope<GroupsResult> = client
        .get(&groups_url)
        .header(AUTHORIZATION, format!("Bearer {}", token))
        .send()
        .await
        .context("failed to fetch groups")?
        .json()
        .await
        .context("failed to parse groups response")?;

    if !resp.ok {
        return Err(anyhow!("groups endpoint returned ok=false"));
    }

    let groups = resp.result.groups;

    // Check each group's settings for desktop_pet_enabled
    let mut pet_enabled = Vec::new();
    for group in &groups {
        let settings_url = format!(
            "{}/api/v1/groups/{}/settings",
            daemon_url, group.group_id
        );
        let settings_resp: Result<ApiEnvelope<SettingsResult>, _> = async {
            client
                .get(&settings_url)
                .header(AUTHORIZATION, format!("Bearer {}", token))
                .send()
                .await?
                .json()
                .await
        }
        .await;

        let enabled = match settings_resp {
            Ok(resp) if resp.ok => {
                resp.result.settings.desktop_pet_enabled.unwrap_or(false)
            }
            _ => false,
        };

        if enabled {
            pet_enabled.push(group);
        }
    }

    // If no groups have desktop_pet_enabled:
    // - Dev mode: fallback to all running groups for easier local development
    // - Production: no windows (require explicit opt-in via settings)
    let selected: Vec<&GroupEntry> = if pet_enabled.is_empty() {
        if cfg!(debug_assertions) {
            groups.iter().filter(|g| g.running).collect()
        } else {
            return Err(anyhow!(
                "no groups have desktop_pet_enabled; enable it in group settings"
            ));
        }
    } else {
        pet_enabled
    };

    let connections: Vec<ResolvedConnection> = selected
        .into_iter()
        .map(|group| ResolvedConnection {
            group_id: group.group_id.clone(),
            group_title: if group.title.is_empty() {
                group.group_id.clone()
            } else {
                group.title.clone()
            },
            daemon_url: daemon_url.clone(),
            auth_token: token.clone(),
        })
        .collect();

    if connections.is_empty() {
        return Err(anyhow!("no eligible groups found (none running)"));
    }

    Ok(connections)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn api_base_url_trims_trailing_slash() {
        let conn = ResolvedConnection {
            group_id: "g_1".into(),
            group_title: "".into(),
            daemon_url: "http://localhost:8848/".into(),
            auth_token: "".into(),
        };
        assert_eq!(conn.api_base_url(), "http://localhost:8848");
    }

    #[test]
    fn api_base_url_handles_no_trailing_slash() {
        let conn = ResolvedConnection {
            group_id: "g_1".into(),
            group_title: "".into(),
            daemon_url: "http://localhost:8848".into(),
            auth_token: "".into(),
        };
        assert_eq!(conn.api_base_url(), "http://localhost:8848");
    }

    #[test]
    fn team_label_falls_back_to_group_id() {
        let conn = ResolvedConnection {
            group_id: "g_abc".into(),
            group_title: "".into(),
            daemon_url: "".into(),
            auth_token: "".into(),
        };
        assert_eq!(conn.team_label(), "g_abc");
    }

    #[test]
    fn team_label_uses_title_when_present() {
        let conn = ResolvedConnection {
            group_id: "g_abc".into(),
            group_title: "My Team".into(),
            daemon_url: "".into(),
            auth_token: "".into(),
        };
        assert_eq!(conn.team_label(), "My Team");
    }
}
