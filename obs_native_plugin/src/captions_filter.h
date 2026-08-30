#pragma once

#include <obs.h>
#include <memory>
#include "audio_resampler.h"
#include "censor_engine.h"
#include "caption_output.h"
#include "stt_client.h"

namespace ObsCaptions {

struct CaptionsFilterContext {
    obs_source_t *source = nullptr;
    bool enabled = true;

    AudioRingBuffer ring_buffer;
    AudioResampler resampler;
    CensorEngine censor;
    CaptionOutputSink sink;
    std::unique_ptr<SttWorkerClient> stt_client;

    CaptionsFilterContext();
    ~CaptionsFilterContext();
};

extern struct obs_source_info captions_filter_info;

} // namespace ObsCaptions
