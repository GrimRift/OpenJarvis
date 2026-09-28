fn main() {
    // The two startup-settings commands get permissions of their own, so the
    // Sage page (a remote URL to Tauri) can be granted exactly these and no more.
    let manifest = tauri_build::AppManifest::new()
        .commands(&["get_startup_settings", "set_startup_settings"]);
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(manifest))
        .expect("failed to run tauri-build");
}
