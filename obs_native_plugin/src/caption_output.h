#pragma once

#include <obs.h>
#include <string>
#include <mutex>
#include <chrono>

namespace ObsCaptions {

class CaptionOutputSink {
public:
    CaptionOutputSink();
    ~CaptionOutputSink();

    void setTextSourceName(const std::string &name);
    std::string getTextSourceName() const;

    void setAutoClearSeconds(float seconds);
    float getAutoClearSeconds() const;

    void setSendCea608(bool enable);
    bool getSendCea608() const;

    // Render caption text on-screen and/or inject into RTMP CC stream
    void dispatchCaption(const std::string &text, bool is_final);

    void clearCaption();

    void checkAutoClear();

private:
    mutable std::mutex mutex_;
    std::string text_source_name_ = "Live Captions";
    float auto_clear_seconds_ = 4.0f;
    bool send_cea608_ = true;

    std::chrono::steady_clock::time_point last_caption_time_;
    bool is_active_ = false;
};

} // namespace ObsCaptions
