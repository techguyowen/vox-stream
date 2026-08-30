/**
 * OBS Studio Native Live Captions Plugin Entry Point
 */

#include <obs-module.h>
#include <obs-frontend-api.h>
#include <QAction>
#include <QMainWindow>
#include "captions_filter.h"
#include "captions_dialog.h"

OBS_DECLARE_MODULE()
OBS_MODULE_USE_DEFAULT_LOCALE("obs-live-captions", "en-US")

MODULE_EXPORT const char *obs_module_name(void)
{
    return "OBS Live Speech Captions (AI)";
}

MODULE_EXPORT const char *obs_module_description(void)
{
    return "Ultra-low latency real-time speech captions with church & safety filtering, live translation, and direct OBS rendering.";
}

MODULE_EXPORT const char *obs_module_ver(void)
{
    return "1.0.0";
}

static void on_open_settings_dialog(void *unused)
{
    UNUSED_PARAMETER(unused);
    auto *main_window = static_cast<QMainWindow *>(obs_frontend_get_main_window());
    ObsCaptions::CaptionsSettingsDialog dialog(main_window);
    dialog.exec();
}

bool obs_module_load(void)
{
    // 1. Register OBS Audio Filter Source
    obs_register_source(&ObsCaptions::captions_filter_info);

    // 2. Add menu item under OBS Tools menu: Tools -> Live Speech Captions Settings...
    obs_frontend_add_tools_menu_item(
        "Live Speech Captions Settings...",
        on_open_settings_dialog,
        nullptr
    );

    blog(LOG_INFO, "==================================================");
    blog(LOG_INFO, "   [OBS Live Captions Plugin v1.0.0 Loaded]       ");
    blog(LOG_INFO, "==================================================");

    return true;
}

void obs_module_unload(void)
{
    blog(LOG_INFO, "[OBS Live Captions Plugin] Module unloaded.");
}
