#pragma once

#include "obs.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct obs_module obs_module_t;

#define OBS_DECLARE_MODULE()                                    \
    EXPORT_API uint32_t obs_module_ver(void) { return 0x020000; }

#define OBS_MODULE_USE_DEFAULT_LOCALE(name, def_locale)

EXPORT_API const char *obs_module_text(const char *val);
#define obs_module_text(val) (val)

#ifdef __cplusplus
}
#endif
