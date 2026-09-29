//! Sage's Windows app (M39).
//!
//! A window onto the same Sage web UI the browser shows -- one code path. The
//! app holds no Sage logic of its own. It starts Sage if it is not running,
//! puts an icon in the tray, and hides instead of closing so Sage keeps
//! listening. The page is the live development build (http://localhost:5173),
//! so code changes appear in the app exactly as they do in the browser.
//!
//! This replaces upstream OpenJarvis's shell (~3,300 lines: its own boot of
//! Ollama and `uv sync`, 29 IPC commands, a native key store, an updater
//! pointed at upstream releases). The Sage page never called any of it once
//! `isTauri()` became false; the upstream code remains in the Git history.

mod startup;
mod window_state;

use std::{
    net::{SocketAddr, TcpStream},
    os::windows::process::CommandExt,
    path::PathBuf,
    process::{Child, Command},
    thread,
    time::Duration,
};

use tauri::{
    menu::{Menu, MenuItem, PredefinedMenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    webview::PageLoadEvent,
    AppHandle, Manager, Url, WebviewUrl, WebviewWindow, WebviewWindowBuilder, WindowEvent,
};
use tauri_plugin_autostart::MacosLauncher;
use tauri_plugin_global_shortcut::ShortcutState;

const SERVER_PORT: u16 = 8000;
/// Passed by the Sage server's reading_focus.py after it read something in Opera.
const RETURN_FOCUS_ARG: &str = "--return-focus";
/// The bundled start-up page (`splash/`): waits for the server and the web UI.
const SPLASH: &str = "http://tauri.localhost/";
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

/// Tauri's own WebView2 defaults, then what the M39 spike measured on
/// 29 September: with these a hidden or minimised window kept the microphone
/// (12.4 frames/s), the wake word, spoken replies and the orb at full speed,
/// and replies play without a click first.
const BROWSER_ARGS: &str = "--disable-features=msWebOOUI,msPdfOOUI,msSmartScreenProtection \
--autoplay-policy=no-user-gesture-required --disable-background-timer-throttling \
--disable-renderer-backgrounding --disable-backgrounding-occluded-windows";

/// Sage's data folder, where its start and stop scripts live.
fn data_dir() -> PathBuf {
    std::env::var_os("OPENJARVIS_HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(r"C:\AI\OpenJarvis-Data"))
}

fn server_up() -> bool {
    let addr = SocketAddr::from(([127, 0, 0, 1], SERVER_PORT));
    TcpStream::connect_timeout(&addr, Duration::from_millis(500)).is_ok()
}

/// Run one of Sage's own launcher scripts, hidden. `SAGE_NO_BROWSER` asks the
/// start script not to open a browser tab: the app is the window.
fn run_script(name: &str) -> std::io::Result<Child> {
    Command::new("powershell.exe")
        .args(["-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File"])
        .arg(data_dir().join("scripts").join(name))
        .env("SAGE_NO_BROWSER", "1")
        .creation_flags(CREATE_NO_WINDOW)
        .spawn()
}

/// Tell the Sage page whether its window is out of sight (hidden in the tray
/// or minimised). BROWSER_ARGS keep the page "visible" to itself on purpose,
/// so the wake word and timers never slow down -- which also means it cannot
/// tell on its own. The orb stops drawing while nobody can see it: measured
/// on 29 September, a hidden app still spent 75% of a core drawing it.
fn tell_page(window: &WebviewWindow) {
    let hidden = !window.is_visible().unwrap_or(true) || window.is_minimized().unwrap_or(false);
    let _ = window.eval(format!(
        "window.__sageWindowHidden = {hidden}; window.dispatchEvent(new Event('sage-window-visibility'));"
    ));
}

fn show(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
        tell_page(&window);
    }
}

fn hide(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.hide();
        tell_page(&window);
    }
}

fn toggle(app: &AppHandle) {
    let visible = app
        .get_webview_window("main")
        .and_then(|w| w.is_visible().ok())
        .unwrap_or(false);
    if visible { hide(app) } else { show(app) }
}

/// Stop Sage, wait for it to let go of its port, start it again. The window
/// goes back to the start-up page, which opens Sage once it answers.
fn restart_sage(app: AppHandle) {
    thread::spawn(move || {
        if let (Some(window), Ok(url)) = (app.get_webview_window("main"), Url::parse(SPLASH)) {
            let _ = window.navigate(url);
        }
        if let Ok(mut stop) = run_script("stop-sage.ps1") {
            let _ = stop.wait();
        }
        for _ in 0..30 {
            if !server_up() {
                break;
            }
            thread::sleep(Duration::from_secs(1));
        }
        let _ = run_script("start-sage.ps1");
    });
}

pub fn run() {
    tauri::Builder::default()
        // A second launch brings the running app forward instead. With
        // `--return-focus` it comes from the Sage server after a read in Opera:
        // forward too, unless the window is hidden in the tray, where the user
        // put it on purpose (a minimised window does come back).
        .plugin(tauri_plugin_single_instance::init(|app, args, _cwd| {
            let from_a_read = args.iter().any(|a| a == RETURN_FOCUS_ARG);
            let in_tray = app
                .get_webview_window("main")
                .map(|w| !w.is_visible().unwrap_or(true))
                .unwrap_or(true);
            if !(from_a_read && in_tray) {
                show(app);
            }
        }))
        .plugin(tauri_plugin_autostart::init(
            MacosLauncher::LaunchAgent,
            Some(vec![startup::AUTOSTART_ARG]),
        ))
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(|app, _shortcut, event| {
                    if event.state() == ShortcutState::Pressed {
                        toggle(app);
                    }
                })
                .build(),
        )
        .invoke_handler(tauri::generate_handler![
            startup::get_startup_settings,
            startup::set_startup_settings
        ])
        .setup(|app| {
            let settings = startup::load(app.handle());
            if let Some(shortcut) = settings.shortcut.as_deref() {
                let _ = startup::register_shortcut(app.handle(), Some(shortcut), None);
            }
            let started_with_windows = std::env::args().any(|a| a == startup::AUTOSTART_ARG);

            if !server_up() {
                let _ = run_script("start-sage.ps1");
            }

            let mut args = BROWSER_ARGS.to_string();
            if let Ok(port) = std::env::var("SAGE_APP_DEBUG_PORT") {
                args.push_str(&format!(" --remote-debugging-port={port}"));
            }
            // Built hidden, sized and placed, then shown: no flash at a default size.
            let window = WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .title("Sage")
                .inner_size(window_state::FIRST_WIDTH, window_state::FIRST_HEIGHT)
                .min_inner_size(900.0, 600.0)
                .additional_browser_args(&args)
                .visible(false)
                .on_page_load(|window, payload| {
                    if let PageLoadEvent::Finished = payload.event() {
                        tell_page(&window);
                    }
                })
                .build()?;
            window_state::restore(&window);
            if !(started_with_windows && settings.start_hidden) {
                let _ = window.show();
            }

            let show_item = MenuItem::with_id(app, "show", "Show Sage", true, None::<&str>)?;
            let hide_item = MenuItem::with_id(app, "hide", "Hide Sage", true, None::<&str>)?;
            let restart_item = MenuItem::with_id(app, "restart", "Restart Sage", true, None::<&str>)?;
            let quit_item = MenuItem::with_id(app, "quit", "Quit Sage app", true, None::<&str>)?;
            let menu = Menu::with_items(
                app,
                &[
                    &show_item,
                    &hide_item,
                    &PredefinedMenuItem::separator(app)?,
                    &restart_item,
                    &PredefinedMenuItem::separator(app)?,
                    &quit_item,
                ],
            )?;
            let mut tray = TrayIconBuilder::with_id("sage")
                .tooltip("Sage")
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => show(app),
                    "hide" => hide(app),
                    "restart" => restart_sage(app.clone()),
                    "quit" => app.exit(0),
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        toggle(tray.app_handle());
                    }
                });
            if let Some(icon) = app.default_window_icon() {
                tray = tray.icon(icon.clone());
            }
            tray.build(app)?;
            Ok(())
        })
        // Closing hides to the tray: Sage keeps listening. Quit is in the tray menu.
        // Moving or resizing is remembered for the next launch.
        .on_window_event(|window, event| {
            let Some(webview) = window.app_handle().get_webview_window(window.label()) else { return };
            match event {
                WindowEvent::CloseRequested { api, .. } => {
                    api.prevent_close();
                    window_state::save(&webview);
                    let _ = window.hide();
                    tell_page(&webview);
                }
                WindowEvent::Moved(_) => window_state::changed(&webview),
                // Minimising and restoring arrive as resizes.
                WindowEvent::Resized(_) => {
                    window_state::changed(&webview);
                    tell_page(&webview);
                }
                _ => {}
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running Sage");
}
