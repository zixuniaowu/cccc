//! Read-only access to the local daemon's `access_tokens.yaml`.
//!
//! Used in local mode to discover a usable access token without
//! requiring the user to configure one manually.

use std::collections::BTreeMap;
use std::env;
use std::fs;
use std::path::PathBuf;

use serde::Deserialize;

const TOKENS_FILE: &str = "access_tokens.yaml";

/// A single access-token entry from the daemon's YAML store.
#[derive(Debug, Clone, Deserialize)]
pub struct TokenEntry {
    #[serde(default)]
    pub user_id: String,
    #[serde(default)]
    pub is_admin: bool,
    #[serde(default)]
    pub allowed_groups: Vec<String>,
}

/// Result of local token discovery.
#[derive(Debug, Clone)]
pub struct DiscoveredToken {
    pub token: String,
    pub entry: TokenEntry,
}

/// Attempt to load a usable token from the daemon's `access_tokens.yaml`.
///
/// Prefers admin tokens (unrestricted access). Falls back to the first
/// non-admin token that has at least one allowed group.
/// Returns `None` if no tokens file exists or no usable token is found.
pub fn discover_local_token() -> Option<DiscoveredToken> {
    let path = tokens_path()?;
    let raw = fs::read_to_string(&path).ok()?;
    let tokens = parse_tokens_yaml(&raw)?;

    // Prefer admin token
    if let Some(found) = tokens
        .iter()
        .find(|(_, entry)| entry.is_admin && !entry.user_id.trim().is_empty())
    {
        return Some(DiscoveredToken {
            token: found.0.clone(),
            entry: found.1.clone(),
        });
    }

    // Fall back to any token with allowed groups
    tokens
        .into_iter()
        .find(|(_, entry)| !entry.user_id.trim().is_empty() && !entry.allowed_groups.is_empty())
        .map(|(token, entry)| DiscoveredToken { token, entry })
}

/// Parse the YAML file content into a token map.
///
/// Supports both formats:
/// - New: `tokens: { acc_xxx: { ... }, ... }`
/// - Legacy: `{ acc_xxx: { ... }, ... }` (flat dict at root)
fn parse_tokens_yaml(raw: &str) -> Option<BTreeMap<String, TokenEntry>> {
    let value: serde_yaml::Value = serde_yaml::from_str(raw).ok()?;
    let mapping = value.as_mapping()?;

    // Try nested format first: { tokens: { ... } }
    let tokens_map = if let Some(inner) = mapping.get(&serde_yaml::Value::String("tokens".into()))
    {
        inner.as_mapping()?
    } else {
        // Legacy flat format
        mapping
    };

    let mut result = BTreeMap::new();
    for (key, val) in tokens_map {
        let token_str = key.as_str().unwrap_or("").trim().to_string();
        if token_str.is_empty() {
            continue;
        }
        // Lenient: skip entries that don't parse
        if let Ok(mut entry) = serde_yaml::from_value::<TokenEntry>(val.clone()) {
            entry.user_id = entry.user_id.trim().to_string();
            entry.allowed_groups = entry
                .allowed_groups
                .into_iter()
                .map(|g| g.trim().to_string())
                .filter(|g| !g.is_empty())
                .collect();
            if entry.is_admin {
                entry.allowed_groups.clear();
            }
            if !entry.user_id.is_empty() {
                result.insert(token_str, entry);
            }
        }
    }

    if result.is_empty() {
        None
    } else {
        Some(result)
    }
}

/// Resolve the path to `access_tokens.yaml`.
///
/// Uses `CCCC_HOME` env var if set, otherwise `~/.cccc/`.
fn tokens_path() -> Option<PathBuf> {
    if let Ok(home) = env::var("CCCC_HOME") {
        let home = home.trim().to_string();
        if !home.is_empty() {
            return Some(PathBuf::from(home).join(TOKENS_FILE));
        }
    }

    dirs::home_dir().map(|home| home.join(".cccc").join(TOKENS_FILE))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_nested_format() {
        let yaml = r#"
tokens:
  acc_abc123:
    user_id: alice
    is_admin: true
    allowed_groups: []
  acc_def456:
    user_id: bob
    is_admin: false
    allowed_groups:
      - g_group1
      - g_group2
"#;
        let tokens = parse_tokens_yaml(yaml).unwrap();
        assert_eq!(tokens.len(), 2);

        let alice = &tokens["acc_abc123"];
        assert_eq!(alice.user_id, "alice");
        assert!(alice.is_admin);
        assert!(alice.allowed_groups.is_empty());

        let bob = &tokens["acc_def456"];
        assert_eq!(bob.user_id, "bob");
        assert!(!bob.is_admin);
        assert_eq!(bob.allowed_groups, vec!["g_group1", "g_group2"]);
    }

    #[test]
    fn parses_legacy_flat_format() {
        let yaml = r#"
acc_flat1:
  user_id: carol
  is_admin: false
  allowed_groups:
    - g_xxx
"#;
        let tokens = parse_tokens_yaml(yaml).unwrap();
        assert_eq!(tokens.len(), 1);
        assert_eq!(tokens["acc_flat1"].user_id, "carol");
    }

    #[test]
    fn prefers_admin_token() {
        let yaml = r#"
tokens:
  acc_normal:
    user_id: bob
    is_admin: false
    allowed_groups:
      - g_1
  acc_admin:
    user_id: alice
    is_admin: true
"#;
        let tokens = parse_tokens_yaml(yaml).unwrap();
        // Admin should be findable
        let admin = tokens
            .iter()
            .find(|(_, e)| e.is_admin && !e.user_id.trim().is_empty());
        assert!(admin.is_some());
        assert_eq!(admin.unwrap().0, "acc_admin");
    }

    #[test]
    fn skips_entries_with_empty_user_id() {
        let yaml = r#"
tokens:
  acc_nouser:
    user_id: ""
    is_admin: true
  acc_valid:
    user_id: dave
    is_admin: false
    allowed_groups:
      - g_1
"#;
        let tokens = parse_tokens_yaml(yaml).unwrap();
        assert_eq!(tokens.len(), 1);
        assert!(tokens.contains_key("acc_valid"));
    }

    #[test]
    fn returns_none_for_empty_yaml() {
        assert!(parse_tokens_yaml("").is_none());
        assert!(parse_tokens_yaml("{}").is_none());
    }

    #[test]
    fn admin_clears_allowed_groups() {
        let yaml = r#"
tokens:
  acc_admin_with_groups:
    user_id: admin
    is_admin: true
    allowed_groups:
      - g_should_be_cleared
"#;
        let tokens = parse_tokens_yaml(yaml).unwrap();
        assert!(tokens["acc_admin_with_groups"].allowed_groups.is_empty());
    }

    #[test]
    fn trims_whitespace_in_groups() {
        let yaml = r#"
tokens:
  acc_trimtest:
    user_id: trimmer
    is_admin: false
    allowed_groups:
      - "  g_spaced  "
      - ""
      - g_clean
"#;
        let tokens = parse_tokens_yaml(yaml).unwrap();
        assert_eq!(
            tokens["acc_trimtest"].allowed_groups,
            vec!["g_spaced", "g_clean"]
        );
    }
}
