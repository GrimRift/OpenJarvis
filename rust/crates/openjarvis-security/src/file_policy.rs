//! File sensitivity policy — block access to secrets, credentials, and keys.

use once_cell::sync::Lazy;
use std::path::Path;

/// Sensitive filename patterns. Each is either an exact name, a `*`-prefixed
/// suffix match (e.g. `*.env`), or a `*`-suffixed prefix match (e.g.
/// `credentials.*`). Kept in sync with the Python fallback's
/// `DEFAULT_SENSITIVE_PATTERNS` in `src/openjarvis/security/file_policy.py`.
static SENSITIVE_PATTERNS: Lazy<Vec<&'static str>> = Lazy::new(|| {
    vec![
        ".env",
        ".env.*",
        "*.env",
        ".secret",
        "*.secrets",
        "credentials.*",
        "*.pem",
        "*.key",
        "*.p12",
        "*.pfx",
        "*.jks",
        "id_rsa",
        "id_ed25519",
        ".htpasswd",
        ".pgpass",
        ".netrc",
    ]
});

fn matches_pattern(name: &str, pattern: &str) -> bool {
    if let Some(suffix) = pattern.strip_prefix('*') {
        name.ends_with(suffix)
    } else if let Some(prefix) = pattern.strip_suffix('*') {
        name.starts_with(prefix)
    } else {
        name == pattern
    }
}

/// Return `true` if path matches a sensitive file pattern.
pub fn is_sensitive_file(path: &Path) -> bool {
    let name = match path.file_name().and_then(|n| n.to_str()) {
        Some(n) => n,
        None => return false,
    };

    SENSITIVE_PATTERNS
        .iter()
        .any(|pattern| matches_pattern(name, pattern))
}

/// Return only non-sensitive paths.
pub fn filter_sensitive_paths<'a>(paths: &'a [&'a Path]) -> Vec<&'a Path> {
    paths
        .iter()
        .filter(|p| !is_sensitive_file(p))
        .copied()
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_sensitive_files() {
        assert!(is_sensitive_file(Path::new(".env")));
        assert!(is_sensitive_file(Path::new(".env.local")));
        assert!(is_sensitive_file(Path::new("server.key")));
        assert!(is_sensitive_file(Path::new("cert.pem")));
        assert!(is_sensitive_file(Path::new("id_rsa")));
        assert!(is_sensitive_file(Path::new("credentials.json")));
    }

    #[test]
    fn test_wildcard_env_suffix() {
        // Regression test: *.env must match any file ending in .env, not
        // just the literal ".env" name.
        assert!(is_sensitive_file(Path::new("test_deny.env")));
        assert!(is_sensitive_file(Path::new("prod.env")));
        assert!(is_sensitive_file(Path::new("staging.env")));
    }

    #[test]
    fn test_wildcard_secrets_suffix() {
        assert!(is_sensitive_file(Path::new("app.secrets")));
    }

    #[test]
    fn test_safe_files() {
        assert!(!is_sensitive_file(Path::new("main.py")));
        assert!(!is_sensitive_file(Path::new("README.md")));
        assert!(!is_sensitive_file(Path::new("config.toml")));
        // Must not false-positive on names that merely contain "env".
        assert!(!is_sensitive_file(Path::new("environment.py")));
        assert!(!is_sensitive_file(Path::new("envfile.txt")));
    }
}
