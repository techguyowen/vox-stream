#pragma once

#include "obs.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef void (*obs_frontend_cb)(void *private_data);

enum obs_frontend_event {
    OBS_FRONTEND_EVENT_STREAMING_STARTED,
    OBS_FRONTEND_EVENT_STREAMING_STOPPED,
    OBS_FRONTEND_EVENT_RECORDING_STARTED,
    OBS_FRONTEND_EVENT_RECORDING_STOPPED,
    OBS_FRONTEND_EVENT_SCENE_CHANGED,
    OBS_FRONTEND_EVENT_EXIT,
};

typedef void (*obs_frontend_event_cb)(enum obs_frontend_event event, void *private_data);

EXPORT_API void obs_frontend_add_tools_menu_item(const char *name, obs_frontend_cb callback, void *private_data);
EXPORT_API void obs_frontend_add_event_callback(obs_frontend_event_cb callback, void *private_data);
EXPORT_API void obs_frontend_remove_event_callback(obs_frontend_event_cb callback, void *private_data);
EXPORT_API void *obs_frontend_get_main_window(void);

#ifdef __cplusplus
}
#endif
