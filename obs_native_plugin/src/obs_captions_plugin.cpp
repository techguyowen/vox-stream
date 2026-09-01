/**
 * OBS Studio Native Audio Filter Plugin for Live Captions
 *
 * This native plugin creates an audio filter source inside OBS Studio.
 * Users can right-click any Audio Source -> Filters -> Add "Live Speech Captions".
 */

#include <obs-module.h>
#include <util/platform.h>
#include <string>
#include <vector>
#include <mutex>

OBS_DECLARE_MODULE()
OBS_MODULE_USE_DEFAULT_LOCALE("obs-live-captions", "en-US")

struct captions_filter_data {
    obs_source_t *context;
    bool enabled;
    std::string text_source_name;
    std::string server_url;
    std::string api_key;
    std::mutex audio_mutex;
    std::vector<float> audio_buffer;
};

static const char *captions_filter_get_name(void *unused)
{
    UNUSED_PARAMETER(unused);
    return "Live Speech Captions (AI)";
}

static void *captions_filter_create(obs_data_t *settings, obs_source_t *source)
{
    struct captions_filter_data *filter = (struct captions_filter_data *)bzalloc(sizeof(struct captions_filter_data));
    filter->context = source;
    filter->enabled = obs_data_get_bool(settings, "enabled");
    filter->text_source_name = obs_data_get_string(settings, "text_source_name");
    filter->server_url = obs_data_get_string(settings, "server_url");
    filter->api_key = obs_data_get_string(settings, "api_key");

    blog(LOG_INFO, "[Live Captions Plugin] Filter created on source '%s'", obs_source_get_name(source));
    return filter;
}

static void captions_filter_destroy(void *data)
{
    struct captions_filter_data *filter = (struct captions_filter_data *)data;
    if (filter) {
        bfree(filter);
    }
}

static struct obs_audio_data *captions_filter_audio(void *data, struct obs_audio_data *audio)
{
    struct captions_filter_data *filter = (struct captions_filter_data *)data;
    if (!filter || !filter->enabled || !audio) {
        return audio;
    }

    // Capture mono/interleaved PCM float samples (passed transparently without altering audio)
    const float *samples = (const float *)audio->data[0];
    if (samples && audio->frames > 0) {
        std::lock_guard<std::mutex> lock(filter->audio_mutex);
        filter->audio_buffer.insert(filter->audio_buffer.end(), samples, samples + audio->frames);
        
        // Keep buffer bounded
        if (filter->audio_buffer.size() > 48000) {
            filter->audio_buffer.erase(filter->audio_buffer.begin(), filter->audio_buffer.begin() + 16000);
        }
    }

    return audio;
}

static obs_properties_t *captions_filter_properties(void *data)
{
    UNUSED_PARAMETER(data);
    obs_properties_t *props = obs_properties_create();

    obs_properties_add_bool(props, "enabled", "Enable Live Captioning");
    obs_properties_add_text(props, "text_source_name", "Target Text Source Name", OBS_TEXT_DEFAULT);
    obs_properties_add_text(props, "server_url", "Captioner Backend URL (e.g. http://127.0.0.1:8765)", OBS_TEXT_DEFAULT);
    obs_properties_add_text(props, "api_key", "API Key (if configured)", OBS_TEXT_PASSWORD);

    return props;
}

static void captions_filter_defaults(obs_data_t *settings)
{
    obs_data_set_default_bool(settings, "enabled", true);
    obs_data_set_default_string(settings, "text_source_name", "Live Captions");
    obs_data_set_default_string(settings, "server_url", "http://127.0.0.1:8765");
    obs_data_set_default_string(settings, "api_key", "");
}

static void captions_filter_update(void *data, obs_data_t *settings)
{
    struct captions_filter_data *filter = (struct captions_filter_data *)data;
    if (!filter) return;

    filter->enabled = obs_data_get_bool(settings, "enabled");
    filter->text_source_name = obs_data_get_string(settings, "text_source_name");
    filter->server_url = obs_data_get_string(settings, "server_url");
    filter->api_key = obs_data_get_string(settings, "api_key");
}

static struct obs_source_info captions_filter_info = {
    .id = "obs_live_captions_filter",
    .type = OBS_SOURCE_TYPE_FILTER,
    .output_flags = OBS_SOURCE_AUDIO,
    .get_name = captions_filter_get_name,
    .create = captions_filter_create,
    .destroy = captions_filter_destroy,
    .get_defaults = captions_filter_defaults,
    .get_properties = captions_filter_properties,
    .update = captions_filter_update,
    .filter_audio = captions_filter_audio,
};

bool obs_module_load(void)
{
    obs_register_source(&captions_filter_info);
    blog(LOG_INFO, "[Live Captions Plugin] Native OBS Filter module loaded successfully.");
    return true;
}

void obs_module_unload(void)
{
    blog(LOG_INFO, "[Live Captions Plugin] Native OBS Filter module unloaded.");
}
