// Esqueleto Tauri 2. SIN COMPILAR NI PROBAR: el entorno de desarrollo era Linux sin WebView2/GTK.
// Pendiente: lanzar el backend Python como proceso hijo (sidecar) y cerrarlo al salir;
// usar el diálogo nativo para elegir archivos en lugar de escribir rutas.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_shell::init())
        .run(tauri::generate_context!())
        .expect("error al iniciar Leal Dub Studio");
}
