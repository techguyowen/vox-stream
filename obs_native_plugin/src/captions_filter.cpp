#include "captions_filter.h"
#include <util/bmem.h>
#include <obs-module.h>

namespace ObsCaptions {

CaptionsFilterContext::CaptionsFilterContext()
    : ring_buffer(64000)
    , resampler(48000, 2)
{
    stt_client = std::make_unique<SttWorkerClient>(ring_buffer, censor, sink);
    stt_client->start();
}

CaptionsFilterContext::~CaptionsFilterContext()
{
    if (stt_client) {
        stt_client->stop();
    }
}

static const char *captions_filter_get_name(void *unused)
{
    UNUSED_PARAMETER(unused);
    return "Live Speech Captions (AI)";
}

static void *captions_filter_create(obs_data_t *settings, obs_source_t *source)
{
    auto *ctx = new CaptionsFilterContext();
    ctx->source = source;

    // Load initial settings
    obs_source_update(source, settings);

    blog(LOG_INFO, "[Live Captions] Filter attached to audio source: '%s'", obs_source_get_name(source));
    return ctx;
}

static void captions_filter_destroy(void *data)
{
    auto *ctx = static_cast<CaptionsFilterContext *>(data);
    if (ctx) {
        blog(LOG_INFO, "[Live Captions] Filter destroyed on source: '%s'",
            ctx->source ? obs_source_get_name(ctx->source) : "Unknown");
        delete ctx;
    }
}

static struct obs_audio_data *captions_filter_audio(void *data, struct obs_audio_data *audio)
{
    auto *ctx = static_cast<CaptionsFilterContext *>(data);
    if (!ctx || !ctx->enabled || !audio || audio->frames == 0) {
        return audio;
    }

    // Process planar float32 audio and push into 16kHz PCM ring buffer
    ctx->resampler.processObsAudio(
        reinterpret_cast<const float *const *>(audio->data),
        audio->frames,
        ctx->ring_buffer
    );

    // Audio passes through transparently without latency or alteration
    return audio;
}

static bool add_sources_to_list(void *data, obs_source_t *source)
{
    obs_property_t *p = static_cast<obs_property_t *>(data);
    const char *id = obs_source_get_id(source);
    // Find text sources (text_gdiplus_v2 on Windows, text_ft2_source_v2 on macOS/Linux)
    if (strcmp(id, "text_gdiplus_v2") == 0 || strcmp(id, "text_gdiplus") == 0 ||
        strcmp(id, "text_ft2_source_v2") == 0 || strcmp(id, "text_ft2_source") == 0) {
        const char *name = obs_source_get_name(source);
        obs_property_list_add_string(p, name, name);
    }
    return true;
}

static obs_properties_t *captions_filter_properties(void *data)
{
    UNUSED_PARAMETER(data);
    obs_properties_t *props = obs_properties_create();

    obs_properties_add_bool(props, "enabled", "Enable Speech Captioning");

    // Engine selector
    obs_property_t *engines = obs_properties_add_list(
        props, "engine", "Speech Engine", OBS_COMBO_TYPE_LIST, OBS_COMBO_FORMAT_STRING
    );
    obs_property_list_add_string(engines, "Google Speech Recognition (Free / Zero-Setup - No Key)", "google_web");
    obs_property_list_add_string(engines, "Gemini 3.5 Transcribe Live (Google AI Studio)", "gemini_live");
    obs_property_list_add_string(engines, "Google Cloud Speech-to-Text (v1 / Chirp)", "google_stt");
    obs_property_list_add_string(engines, "Local Faster-Whisper (Offline)", "local_whisper");

    // Language selector
    obs_property_t *langs = obs_properties_add_list(
        props, "language", "Spoken Language", OBS_COMBO_TYPE_LIST, OBS_COMBO_FORMAT_STRING
    );
    obs_property_list_add_string(langs, "English (US)", "en-US");
    obs_property_list_add_string(langs, "English (UK)", "en-GB");
    obs_property_list_add_string(langs, "Spanish", "es-ES");
    obs_property_list_add_string(langs, "French", "fr-FR");
    obs_property_list_add_string(langs, "German", "de-DE");
    obs_property_list_add_string(langs, "Japanese", "ja-JP");

    // Target Text Source
    obs_property_t *text_sources = obs_properties_add_list(
        props, "text_source", "Target OBS Text Source", OBS_COMBO_TYPE_LIST, OBS_COMBO_FORMAT_STRING
    );
    obs_property_list_add_string(text_sources, "(None / Web Overlay Only)", "");
    obs_enum_sources(add_sources_to_list, text_sources);

    // Closed captions
    obs_properties_add_bool(props, "send_cea608", "Inject Twitch/YouTube Closed Captions (CEA-608)");

    // Content Filter Mode
    obs_property_t *censor_modes = obs_properties_add_list(
        props, "censor_mode", "Church & Safety Filter", OBS_COMBO_TYPE_LIST, OBS_COMBO_FORMAT_STRING
    );
    obs_property_list_add_string(censor_modes, "Wholesome Word Replacement ('damn' -> 'darn')", "replacement");
    obs_property_list_add_string(censor_modes, "Asterisk Masking ('f***')", "asterisk");
    obs_property_list_add_string(censor_modes, "[CENSORED] Tag", "censored_tag");
    obs_property_list_add_string(censor_modes, "Drop Sentence (Mute line on bad words)", "drop_sentence");
    obs_property_list_add_string(censor_modes, "Disabled", "disabled");

    // Noise gate and silence timeout
    obs_properties_add_float_slider(props, "noise_gate_db", "Noise Gate Threshold (dB)", -80.0, -10.0, 1.0);
    obs_properties_add_float_slider(props, "auto_clear_seconds", "Auto-Clear Silence Timeout (s)", 0.0, 15.0, 0.5);

    return props;
}

static void captions_filter_defaults(obs_data_t *settings)
{
    obs_data_set_default_bool(settings, "enabled", true);
    obs_data_set_default_string(settings, "engine", "google_stt");
    obs_data_set_default_string(settings, "language", "en-US");
    obs_data_set_default_string(settings, "text_source", "Live Captions");
    obs_data_set_default_bool(settings, "send_cea608", true);
    obs_data_set_default_string(settings, "censor_mode", "replacement");
    obs_data_set_default_double(settings, "noise_gate_db", -45.0);
    obs_data_set_default_double(settings, "auto_clear_seconds", 4.0);
}

static void captions_filter_update(void *data, obs_data_t *settings)
{
    auto *ctx = static_cast<CaptionsFilterContext *>(data);
    if (!ctx) return;

    ctx->enabled = obs_data_get_bool(settings, "enabled");

    // Update Output Sink
    const char *text_source = obs_data_get_string(settings, "text_source");
    ctx->sink.setTextSourceName(text_source ? text_source : "");
    ctx->sink.setSendCea608(obs_data_get_bool(settings, "send_cea608"));
    ctx->sink.setAutoClearSeconds(static_cast<float>(obs_data_get_double(settings, "auto_clear_seconds")));

    // Update Censor
    const char *censor_mode_str = obs_data_get_string(settings, "censor_mode");
    CensorConfig censor_cfg = ctx->censor.getConfig();
    if (strcmp(censor_mode_str, "disabled") == 0) {
        censor_cfg.enabled = false;
    } else {
        censor_cfg.enabled = true;
        if (strcmp(censor_mode_str, "asterisk") == 0) {
            censor_cfg.mode = CensorMode::Asterisk;
        } else if (strcmp(censor_mode_str, "censored_tag") == 0) {
            censor_cfg.mode = CensorMode::CensoredTag;
        } else if (strcmp(censor_mode_str, "drop_sentence") == 0) {
            censor_cfg.mode = CensorMode::DropSentence;
        } else {
            censor_cfg.mode = CensorMode::Replacement;
        }
    }
    ctx->censor.setConfig(censor_cfg);

    // Update STT Client
    if (ctx->stt_client) {
        SttClientConfig stt_cfg = ctx->stt_client->getConfig();
        stt_cfg.language_code = obs_data_get_string(settings, "language");
        stt_cfg.noise_gate_db = static_cast<float>(obs_data_get_double(settings, "noise_gate_db"));
        ctx->stt_client->setConfig(stt_cfg);
    }
}

struct obs_source_info captions_filter_info = {
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

} // namespace ObsCaptions
