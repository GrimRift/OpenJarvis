//! The app's startup options, set from Sage's Settings page: start with
//! Windows, stay in the tray when started that way, and a keyboard shortcut
//! that shows or hides the window from anywhere.
//!
//! "Start with Windows" lives in the registry (the autostart plugin's Run
//! key), so it is read back from there rather than stored twice. The other
//! two are kept in `startup.json` in the app's config folder.

use std::{fs, path::PathBuf};

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Manager, Runtime};
use tauri_plugin_autostart::ManagerExt as _;
use tauri_plugin_global_shortcut::{GlobalShortcutExt, Shortcut};

/// Passed on the command line by the Run key, so a start with Windows can be
/// told apart from the user opening the app.
pub const AUTOSTART_ARG: &str = "--autostart";

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", default)]
pub struct StartupSettings {
    pub start_with_windows: bool,
    /// When started with Windows, stay in the tray instead of opening the window.
    pub start_hidden: bool,
    /// A global shortcut such as `Ctrl+Alt+KeyS`; `None` means off.
    pub shortcut: Option<String>,
}

fn settings_path<R: Runtime>(app: &AppHandle<R>) -> Option<PathBuf> {
    app.path().app_config_dir().ok().map(|dir| dir.join("startup.json"))
}

/// The saved options, with "start with Windows" read from the registry.
pub fn load<R: Runtime>(app: &AppHandle<R>) -> StartupSettings {
    let mut settings: StartupSettings = settings_path(app)
        .and_then(|path| fs::read_to_string(path).ok())
        .and_then(|text| serde_json::from_str(&text).ok())
        .unwrap_or_default();
    settings.start_with_windows = app.autolaunch().is_enabled().unwrap_or(false);
    settings
}

fn save<R: Runtime>(app: &AppHandle<R>, settings: &StartupSettings) -> Result<(), String> {
    let path = settings_path(app).ok_or("no config folder for the app")?;
    if let Some(dir) = path.parent() {
        fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    }
    let text = serde_json::to_string_pretty(settings).map_err(|e| e.to_string())?;
    fs::write(path, text).map_err(|e| e.to_string())
}

fn parse_shortcut(text: &str) -> Result<Shortcut, String> {
    text.parse::<Shortcut>()
        .map_err(|_| format!("\"{text}\" is not a key combination Windows can use"))
}

/// Make `shortcut` the only global shortcut. On failure (most often: another
/// app already owns that combination) the previous one is put back.
pub fn register_shortcut<R: Runtime>(
    app: &AppHandle<R>,
    shortcut: Option<&str>,
    previous: Option<&str>,
) -> Result<(), String> {
    let manager = app.global_shortcut();
    let _ = manager.unregister_all();
    let Some(text) = shortcut else { return Ok(()) };
    let result = parse_shortcut(text).and_then(|parsed| {
        manager
            .register(parsed)
            .map_err(|_| format!("{text} is already taken by Windows or another app; try another"))
    });
    if result.is_err() {
        if let Some(Ok(old)) = previous.map(parse_shortcut) {
            let _ = manager.register(old);
        }
    }
    result
}

#[tauri::command]
pub fn get_startup_settings<R: Runtime>(app: AppHandle<R>) -> StartupSettings {
    load(&app)
}

/// Apply and save new options; returns what is now in effect.
#[tauri::command]
pub fn set_startup_settings<R: Runtime>(
    app: AppHandle<R>,
    settings: StartupSettings,
) -> Result<StartupSettings, String> {
    let current = load(&app);
    let shortcut = settings.shortcut.filter(|s| !s.trim().is_empty());

    if shortcut != current.shortcut {
        register_shortcut(&app, shortcut.as_deref(), current.shortcut.as_deref())?;
    }
    if settings.start_with_windows != current.start_with_windows {
        let autolaunch = app.autolaunch();
        let changed = if settings.start_with_windows { autolaunch.enable() } else { autolaunch.disable() };
        changed.map_err(|e| format!("Windows start-up entry could not be changed ({e})"))?;
    }
    save(
        &app,
        &StartupSettings { start_with_windows: settings.start_with_windows, start_hidden: settings.start_hidden, shortcut },
    )?;
    Ok(load(&app))
}
