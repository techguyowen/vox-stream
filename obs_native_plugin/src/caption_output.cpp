#include "caption_output.h"
#include <obs-frontend-api.h>
#include <util/bmem.h>

namespace ObsCaptions {

CaptionOutputSink::CaptionOutputSink()
    : last_caption_time_(std::chrono::steady_clock::now())
{
}

CaptionOutputSink::~CaptionOutputSink()
{
}

void CaptionOutputSink::setTextSourceName(const std::string &name)
{
    std::lock_guard<std::mutex> lock(mutex_);
    text_source_name_ = name;
}

std::string CaptionOutputSink::getTextSourceName() const
{
    std::lock_guard<std::mutex> lock(mutex_);
    return text_source_name_;
}

void CaptionOutputSink::setAutoClearSeconds(float seconds)
{
    std::lock_guard<std::mutex> lock(mutex_);
    auto_clear_seconds_ = seconds;
}

float CaptionOutputSink::getAutoClearSeconds() const
{
    std::lock_guard<std::mutex> lock(mutex_);
    return auto_clear_seconds_;
}

void CaptionOutputSink::setSendCea608(bool enable)
{
    std::lock_guard<std::mutex> lock(mutex_);
    send_cea608_ = enable;
}

bool CaptionOutputSink::getSendCea608() const
{
    std::lock_guard<std::mutex> lock(mutex_);
    return send_cea608_;
}

void CaptionOutputSink::dispatchCaption(const std::string &text, bool is_final)
{
    std::lock_guard<std::mutex> lock(mutex_);
    last_caption_time_ = std::chrono::steady_clock::now();
    is_active_ = true;

    // 1. Update In-Memory OBS Text Source directly
    if (!text_source_name_.empty()) {
        obs_source_t *source = obs_get_source_by_name(text_source_name_.c_str());
        if (source) {
            obs_data_t *settings = obs_source_get_settings(source);
            if (settings) {
                obs_data_set_string(settings, "text", text.c_str());
                obs_source_update(source, settings);
                obs_data_release(settings);
            }
            obs_source_release(source);
        }
    }

    // 2. Inject CEA-608 Closed Captions into active RTMP stream output
    if (send_cea608_ && is_final && !text.empty()) {
        obs_output_t *stream_output = obs_frontend_get_streaming_output();
        if (stream_output) {
            // Send caption line to output stream
            obs_output_output_caption_text1(stream_output, text.c_str());
            obs_output_release(stream_output);
        }
    }
}

void CaptionOutputSink::clearCaption()
{
    std::lock_guard<std::mutex> lock(mutex_);
    is_active_ = false;

    if (!text_source_name_.empty()) {
        obs_source_t *source = obs_get_source_by_name(text_source_name_.c_str());
        if (source) {
            obs_data_t *settings = obs_source_get_settings(source);
            if (settings) {
                obs_data_set_string(settings, "text", "");
                obs_source_update(source, settings);
                obs_data_release(settings);
            }
            obs_source_release(source);
        }
    }
}

void CaptionOutputSink::checkAutoClear()
{
    std::lock_guard<std::mutex> lock(mutex_);
    if (!is_active_ || auto_clear_seconds_ <= 0.0f) {
        return;
    }

    auto now = std::chrono::steady_clock::now();
    auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(now - last_caption_time_).count();
    if (elapsed > static_cast<long long>(auto_clear_seconds_ * 1000.0f)) {
        is_active_ = false;
        if (!text_source_name_.empty()) {
            obs_source_t *source = obs_get_source_by_name(text_source_name_.c_str());
            if (source) {
                obs_data_t *settings = obs_source_get_settings(source);
                if (settings) {
                    obs_data_set_string(settings, "text", "");
                    obs_source_update(source, settings);
                    obs_data_release(settings);
                }
                obs_source_release(source);
            }
        }
    }
}

} // namespace ObsCaptions
