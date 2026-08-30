#pragma once

#include "util/c99defs.h"
#include <stdio.h>
#include <stdarg.h>

#ifdef __cplusplus
extern "C" {
#endif

#define LOG_ERROR 100
#define LOG_WARNING 200
#define LOG_INFO 300
#define LOG_DEBUG 400

static inline void blog(int log_level, const char *format, ...)
{
    va_list args;
    va_start(args, format);
    const char *prefix = "[OBS]";
    if (log_level == LOG_ERROR) prefix = "[OBS ERROR]";
    else if (log_level == LOG_WARNING) prefix = "[OBS WARNING]";
    else if (log_level == LOG_INFO) prefix = "[OBS INFO]";
    else if (log_level == LOG_DEBUG) prefix = "[OBS DEBUG]";
    
    printf("%s ", prefix);
    vprintf(format, args);
    printf("\n");
    va_end(args);
}

typedef struct obs_source obs_source_t;
typedef struct obs_data obs_data_t;
typedef struct obs_properties obs_properties_t;
typedef struct obs_property obs_property_t;

enum obs_source_type {
    OBS_SOURCE_TYPE_INPUT,
    OBS_SOURCE_TYPE_FILTER,
    OBS_SOURCE_TYPE_TRANSITION,
    OBS_SOURCE_TYPE_SCENE,
};

#define OBS_SOURCE_AUDIO (1 << 0)
#define OBS_SOURCE_VIDEO (1 << 1)
#define OBS_SOURCE_ASYNC (1 << 2)

struct obs_audio_data {
    uint8_t *data[8];
    uint32_t frames;
    uint64_t timestamp;
};

struct obs_source_info {
    const char *id;
    enum obs_source_type type;
    uint32_t output_flags;
    const char *(*get_name)(void *type_data);
    void *(*create)(obs_data_t *settings, obs_source_t *source);
    void (*destroy)(void *data);
    obs_properties_t *(*get_properties)(void *data);
    void (*get_defaults)(obs_data_t *settings);
    void (*update)(void *data, obs_data_t *settings);
    struct obs_audio_data *(*filter_audio)(void *data, struct obs_audio_data *audio);
};

enum obs_text_type {
    OBS_TEXT_DEFAULT,
    OBS_TEXT_PASSWORD,
    OBS_TEXT_MULTILINE,
};

enum obs_combo_type {
    OBS_COMBO_TYPE_INVALID,
    OBS_COMBO_TYPE_EDITABLE,
    OBS_COMBO_TYPE_LIST,
};

enum obs_combo_format {
    OBS_COMBO_FORMAT_INVALID,
    OBS_COMBO_FORMAT_INT,
    OBS_COMBO_FORMAT_FLOAT,
    OBS_COMBO_FORMAT_STRING,
};

EXPORT_API void obs_register_source_s(const struct obs_source_info *info, size_t size);
#define obs_register_source(info) obs_register_source_s(info, sizeof(struct obs_source_info))

EXPORT_API obs_source_t *obs_get_source_by_name(const char *name);
EXPORT_API void obs_source_release(obs_source_t *source);
EXPORT_API void obs_source_update(obs_source_t *source, obs_data_t *settings);
EXPORT_API bool obs_output_caption_line(void *output, const char *text);

EXPORT_API obs_data_t *obs_data_create(void);
EXPORT_API void obs_data_release(obs_data_t *data);
EXPORT_API void obs_data_set_string(obs_data_t *data, const char *name, const char *val);
EXPORT_API void obs_data_set_bool(obs_data_t *data, const char *name, bool val);
EXPORT_API void obs_data_set_int(obs_data_t *data, const char *name, long long val);
EXPORT_API void obs_data_set_double(obs_data_t *data, const char *name, double val);

EXPORT_API void obs_data_set_default_string(obs_data_t *data, const char *name, const char *val);
EXPORT_API void obs_data_set_default_bool(obs_data_t *data, const char *name, bool val);
EXPORT_API void obs_data_set_default_int(obs_data_t *data, const char *name, long long val);
EXPORT_API void obs_data_set_default_double(obs_data_t *data, const char *name, double val);

EXPORT_API const char *obs_data_get_string(obs_data_t *data, const char *name);
EXPORT_API bool obs_data_get_bool(obs_data_t *data, const char *name);
EXPORT_API long long obs_data_get_int(obs_data_t *data, const char *name);
EXPORT_API double obs_data_get_double(obs_data_t *data, const char *name);

EXPORT_API obs_properties_t *obs_properties_create(void);
EXPORT_API obs_property_t *obs_properties_add_bool(obs_properties_t *props, const char *name, const char *description);
EXPORT_API obs_property_t *obs_properties_add_int(obs_properties_t *props, const char *name, const char *description, int min, int max, int step);
EXPORT_API obs_property_t *obs_properties_add_float(obs_properties_t *props, const char *name, const char *description, double min, double max, double step);
EXPORT_API obs_property_t *obs_properties_add_text(obs_properties_t *props, const char *name, const char *description, enum obs_text_type type);
EXPORT_API obs_property_t *obs_properties_add_list(obs_properties_t *props, const char *name, const char *description, enum obs_combo_type type, enum obs_combo_format format);
EXPORT_API size_t obs_property_list_add_string(obs_property_t *p, const char *name, const char *val);
EXPORT_API size_t obs_property_list_add_int(obs_property_t *p, const char *name, long long val);

#ifdef __cplusplus
}
#endif
